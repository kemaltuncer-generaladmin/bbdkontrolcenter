#!/usr/bin/env python3
"""Güncelleyicinin okuduğu sürüm listesini (`latest.json`) üretir.

Kurulu uygulama açılışta değil, KULLANICI "denetle" dediğinde bu dosyayı
okur; içinde her platform için ayrı bir paket adresi ve imzası durur. Windows
kurulumu Windows'unkini, macOS `.app` paketini, Linux AppImage'ı çeker —
karar burada verilmiş olur, uygulamada değil.

    {
      "version": "0.2.0",
      "notes":   "...",
      "pub_date": "2026-08-17T09:00:00Z",
      "platforms": {
        "windows-x86_64-nsis": {"url": "https://…-setup.exe", "signature": "dW50…"}
      }
    }

Hedef adı `{sistem}-{mimari}-{kurulum biçimi}` düzenindedir ve uydurulamaz:
`tauri-plugin-updater` önce bu adı, bulamazsa `{sistem}-{mimari}` adını arar.
Adı üreten yer paketi üreten yerdir — `scripts/build-release.{sh,ps1}` her
platformda `dist/updater/<sistem>-<mimari>.json` künyesini yazar; burada
yapılan iş o künyeleri birleştirmek ve dosya adlarını GERÇEK yayın adresine
bağlamaktır.

ADRES TAHMİN EDİLMEZ, SORULUR. GitHub yüklenen varlık adlarındaki boşlukları
noktaya çevirir ("Kontrol Merkezi_0.1.0_amd64.AppImage" →
"Kontrol.Merkezi_0.1.0_amd64.AppImage"). Adresi kalıptan kurmak, ilk bakışta
çalışan ama indirme anında 404 veren bir bağlantı üretirdi. Bu yüzden yayın
oluşturulduktan SONRA varlık listesi okunur ve adres oradan alınır.

Kullanım (iş akışının `release` işinde):

    gh api "repos/$GITHUB_REPOSITORY/releases/tags/$TAG" > release.json
    python3 scripts/make-latest-json.py \
        --dist dist --assets release.json --version "${TAG#v}" --out latest.json

`gh release view "$TAG" --json assets` çıktısı da kabul edilir; ikisi de bir
`assets` listesi taşır.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


#: Varlık adı normalleştirme: GitHub boşluğu noktaya çevirir. Kural burada tek
#: satır olarak durur ki değişirse tek yerde değişsin.
def _candidates(name: str) -> list[str]:
    return [name, name.replace(" ", "."), name.replace(" ", "_")]


def _fail(message: str) -> None:
    print(f"HATA: {message}", file=sys.stderr)
    raise SystemExit(1)


def _read_manifests(updater_dir: Path) -> dict[str, str]:
    """`dist/updater/*.json` künyelerini birleştirir: hedef → dosya adı."""
    merged: dict[str, str] = {}
    for path in sorted(updater_dir.glob("*.json")):
        try:
            # `utf-8-sig`: Windows PowerShell 5.1'in `Set-Content -Encoding UTF8`
            # çıktısı BOM ile başlar ve düz `utf-8` okuması bunu ayrıştıramaz.
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as error:
            _fail(f"{path} okunamadı: {error}")
        for target, filename in data.items():
            if target in merged and merged[target] != filename:
                # İKİ KOŞUCU AYNI HEDEFİ DOLDURDUYSA SESSİZ KALINMAZ: biri
                # ötekini ezerse yayına yanlış paket düşer.
                _fail(
                    f"'{target}' hedefi iki kez bildirildi: "
                    f"{merged[target]} ve {filename}"
                )
            merged[target] = filename
    return merged


def _asset_urls(assets_path: Path) -> dict[str, str]:
    """Yayın gövdesi → {varlık adı: indirme adresi}.

    Hem `gh api .../releases/tags/<etiket>` gövdesini (sözlük, içinde
    `assets`) hem de doğrudan varlık listesini kabul eder.
    """
    try:
        payload = json.loads(assets_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        _fail(f"{assets_path} okunamadı: {error}")

    assets = payload.get("assets") if isinstance(payload, dict) else payload
    if not assets:
        _fail(f"{assets_path}: yayında hiç varlık yok — paketler yüklenmemiş olabilir.")

    urls: dict[str, str] = {}
    for asset in assets:
        name = asset.get("name")
        # `browser_download_url` ÖNCE: API adresi de indirilebilir ama yalnız
        # doğru `Accept` başlığıyla ve yönlendirmeyle. Tarayıcı adresi her
        # istemcide aynı davranır.
        url = asset.get("browser_download_url") or asset.get("url")
        if name and url:
            urls[name] = url
    return urls


def main() -> None:
    parser = argparse.ArgumentParser(description="latest.json üretir")
    parser.add_argument("--dist", default="dist", help="paketlerin ve künyelerin klasörü")
    parser.add_argument("--assets", required=True, help="gh release view --json assets çıktısı")
    parser.add_argument("--version", required=True, help="sürüm (etiketten, baştaki v olmadan)")
    parser.add_argument("--out", default="latest.json", help="yazılacak dosya")
    parser.add_argument("--notes", default="", help="yayın notu (boşsa varsayılan cümle)")
    args = parser.parse_args()

    dist = Path(args.dist)
    manifests = _read_manifests(dist / "updater")
    if not manifests:
        _fail(
            f"{dist}/updater altında künye yok. Paketler imzasız üretilmiş olabilir "
            "(TAURI_SIGNING_PRIVATE_KEY tanımlı mı?); imzasız paketle güncelleme çalışmaz."
        )

    urls = _asset_urls(Path(args.assets))
    platforms: dict[str, dict[str, str]] = {}

    for target, filename in sorted(manifests.items()):
        signature_path = dist / f"{filename}.sig"
        if not signature_path.is_file():
            _fail(f"{signature_path} yok: '{target}' hedefinin imzası bulunamadı.")
        signature = signature_path.read_text(encoding="utf-8").strip()
        if not signature:
            _fail(f"{signature_path} boş.")

        url = next((urls[name] for name in _candidates(filename) if name in urls), None)
        if url is None:
            _fail(
                f"'{filename}' yayına yüklenmemiş görünüyor. Yayındaki varlıklar: "
                + ", ".join(sorted(urls)),
            )

        platforms[target] = {"url": url, "signature": signature}

    payload = {
        "version": args.version,
        "notes": args.notes or (
            f"Kontrol Merkezi {args.version}. Değişiklikler yayın sayfasında."
        ),
        # RFC3339 — eklenti başka biçimi ayrıştırmaz ve hatayı denetleme
        # anında verir.
        "pub_date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platforms": platforms,
    }

    Path(args.out).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(f"{args.out} yazıldı — {len(platforms)} hedef:")
    for target, entry in platforms.items():
        print(f"  {target} → {entry['url']}")


if __name__ == "__main__":
    main()
