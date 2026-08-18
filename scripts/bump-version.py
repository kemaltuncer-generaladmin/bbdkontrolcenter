#!/usr/bin/env python3
"""Sürümü TEK KOMUTLA yükseltir.

    scripts/bump-version.py 0.1.8
    scripts/bump-version.py --check        # yalnız denetler, yazmaz

## Neden gerekli

Sürüm dört ayrı dosyada tekrar ediyordu ve 18.08.2026'da ÜÇÜ BİRDEN
tutmuyordu: `tauri.conf.json` 0.1.7, `Cargo.toml` ve `package.json` 0.1.0.
Kullanıcının gördüğü sürüm, güncelleyicinin karşılaştırdığı sürüm ve paketin
adındaki sürüm farklı olabiliyordu — "güncelleme yok" diyen bir kurulumun
gerçekten güncel olup olmadığı anlaşılamazdı.

`--check` kapı olarak koşulabilir: dosyalar ayrışmışsa çıkış kodu 1'dir.

## Neden `app.py` listede yok

Çekirdeğin bildirdiği sürüm derleme damgasından (`km_core/_build_stamp.py`)
okunur; o dosyayı `scripts/build-release.sh` üretir ve depoya girmez. Elle
yazılan bir sürüm orada ikinci bir doğruluk kaynağı olurdu.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")

#: Kilit dosyasındaki paket adı — `Cargo.lock` içinde YALNIZ bu bloğun
#: sürümü değişir. Dosyada onlarca `version = "…"` satırı var ve hepsi başka
#: paketlere ait; kör bir arama-değiştirme bağımlılık ağacını bozardı.
CARGO_PACKAGE = "kontrol-merkezi"

#: (dosya, okuma deseni, yazma biçimi). Sıra ekranda görünen sıradır.
TARGETS = (
    ("apps/desktop/src-tauri/tauri.conf.json", "json", "version"),
    ("apps/desktop/src-tauri/Cargo.toml", "toml", "version"),
    # `Cargo.lock` LİSTEDE OLMALI. Yoksa `cargo` ilk derlemede kendisi
    # düzeltiyor ve çalışma ağacında sürüm bumpıyla ilgisiz görünen bir
    # değişiklik bırakıyor; v0.1.9'da tam olarak bu oldu ve commit'e girmedi.
    ("apps/desktop/src-tauri/Cargo.lock", "cargolock", "version"),
    ("apps/desktop/package.json", "json", "version"),
    ("apps/desktop/package-lock.json", "lock", "version"),
)


def _cargo_lock_pattern() -> re.Pattern[str]:
    """`Cargo.lock` içinde YALNIZ kendi paketimizin sürüm satırı.

    Ad satırına çapalanır: dosyadaki diğer `version = "…"` satırları
    bağımlılıklara aittir ve dokunulursa derleme bozulur.
    """
    return re.compile(
        rf'(name\s*=\s*"{re.escape(CARGO_PACKAGE)}"\s*\nversion\s*=\s*)"[^"]+"'
    )


def _read(path: Path, kind: str) -> str | None:
    text = path.read_text(encoding="utf-8")
    if kind in {"json", "lock"}:
        return str(json.loads(text).get("version") or "")
    if kind == "cargolock":
        match = _cargo_lock_pattern().search(text)
        return match.group(0).rsplit('"', 2)[1] if match else None
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else None


def _write(path: Path, kind: str, version: str) -> None:
    text = path.read_text(encoding="utf-8")
    if kind == "json":
        # Ham metin üzerinde değiştirilir, `json.dumps` ile YENİDEN YAZILMAZ:
        # yeniden yazmak yorumları (JSON'da yok ama biçimi) ve anahtar sırasını
        # bozar, diff'i okunmaz hâle getirirdi.
        new = re.sub(r'("version"\s*:\s*)"[^"]*"', rf'\1"{version}"', text, count=1)
    elif kind == "lock":
        # `package-lock.json` sürümü İKİ YERDE taşır: kökte ve `packages[""]`
        # altında. Biri güncellenip diğeri unutulursa `npm` uyarı verir.
        new = re.sub(r'("version"\s*:\s*)"[^"]*"', rf'\1"{version}"', text, count=2)
    elif kind == "cargolock":
        new = _cargo_lock_pattern().sub(rf'\1"{version}"', text, count=1)
    else:
        new = re.sub(r'^(version\s*=\s*)"[^"]+"', rf'\1"{version}"', text,
                     count=1, flags=re.MULTILINE)
    path.write_text(new, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", nargs="?", help="yeni sürüm, örn. 0.1.8")
    parser.add_argument("--check", action="store_true",
                        help="yazma; dosyalar uyuşuyor mu söyle")
    args = parser.parse_args()

    if args.check:
        found: dict[str, str | None] = {}
        for relative, kind, _field in TARGETS:
            found[relative] = _read(ROOT / relative, kind)
        for relative, value in found.items():
            print(f"  {value or '?':<10} {relative}")
        distinct = {value for value in found.values() if value}
        if len(distinct) != 1:
            print(f"\nAYRIŞMA: {len(distinct)} farklı sürüm var — hizalanmalı.")
            return 1
        print(f"\nHepsi aynı: {distinct.pop()}")
        return 0

    if not args.version or not SEMVER.match(args.version):
        print("HATA: sürüm 'X.Y.Z' biçiminde verilmeli (örn. 0.1.8).")
        return 2

    for relative, kind, _field in TARGETS:
        path = ROOT / relative
        before = _read(path, kind)
        _write(path, kind, args.version)
        print(f"  {before or '?'} → {args.version}   {relative}")

    print(f"\nSürüm {args.version} olarak hizalandı.")
    print("Sonraki adım: commit, `git tag v" + args.version + "`, `git push --tags`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
