#!/usr/bin/env python3
"""`config/local.yaml` içindeki sırları KASAYA taşır.

    scripts/push-local-secrets.py --dsn postgresql://...            # taşı
    scripts/push-local-secrets.py --dsn ... --dry-run               # yalnız listele

## Neden gerekli

K8 kasanın iki kaynağı olduğunu söyler: `config/local.yaml` ve `secrets`
tablosu. Tek makineli dünyada ikisi eşdeğerdi — dosya o makinede duruyordu.

ADR 0026 ile backend SUNUCUDA koşuyor ve `config/local.yaml` git dışıdır,
imaja GİRMEZ. Yani o dosyadaki her sır sunucuda YOKTUR. Belirti sessiz ve
dağınık: zil anonsu üretilemez ("Vertex servis hesabı kasada yok"), köprüye
komut yazılamaz, BBD/BLD geçitleri kimlik doğrulayamaz. Hiçbiri uygulamayı
düşürmez (K7), yalnız o özellikler çalışmaz ve sebebi ekranda görünmez.

Bu betik farkı kapatır: dosyadaki sırlar kasa tablosuna yazılır ve veritabanı
merkezde olduğu için her kurulum onları görür.

## Değer EZİLMEZ

Kasada aynı anahtar zaten varsa ATLANIR ve nedeni yazılır. Üzerine yazmak,
merkezde elle düzeltilmiş bir sırrı eski yerel kopyayla sessizce geri almak
olurdu (`roster_import` ile aynı gerekçe). Bilerek ezmek için `--overwrite`.

## Şifreleme anahtarı

Değerler kurulumun `data/secret.key` dosyasıyla şifrelenir — sunucudaki
`KM_SECRET_KEY` ile AYNI olmak zorundadır, yoksa sunucu yazdığımız satırları
çözemez. Betik ikisinin aynı olduğunu doğrulayamaz (sunucunun ortamını
göremez); bu yüzden sonda hatırlatır.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from km_core.store.postgres import PostgresStore


def _local_secrets(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = data.get("secrets") or {}
    # Değerler metin olmak zorunda: kasa şifreli METİN saklıyor. Sözlük/liste
    # gelirse JSON'a çevirmek yerine REDDEDİLİR — sessiz bir biçim değişikliği,
    # okuyan tarafta anlaşılmaz bir hataya dönüşürdü.
    return {str(key): str(value) for key, value in raw.items() if value is not None}


async def run(dsn: str, *, dry_run: bool, overwrite: bool) -> int:
    local_path = ROOT / "config" / "local.yaml"
    secrets = _local_secrets(local_path)
    if not secrets:
        print(f"{local_path.relative_to(ROOT)} içinde sır yok — yapılacak iş yok.")
        return 0

    key_file = ROOT / "data" / "secret.key"
    if not key_file.is_file():
        print(f"HATA: {key_file.relative_to(ROOT)} yok — değerler şifrelenemez.")
        return 2
    fernet = Fernet(key_file.read_text(encoding="ascii").strip().encode("ascii"))

    print(f"kaynak : {local_path.relative_to(ROOT)}  ({len(secrets)} sır)")
    print(f"hedef  : {dsn.rsplit('@', 1)[-1]}")      # PAROLA YAZILMAZ
    print(f"kip    : {'DENEME' if dry_run else 'GERÇEK'}\n")

    store = PostgresStore(dsn)
    await store.open()
    try:
        rows = await store.fetch_all("SELECT key FROM secrets")
        mevcut = {str(row["key"]) for row in rows}

        yazilan, atlanan = 0, 0
        for name in sorted(secrets):
            if name in mevcut and not overwrite:
                print(f"  atlandı  {name}  (kasada zaten var)")
                atlanan += 1
                continue
            if dry_run:
                print(f"  yazılacak {name}")
                yazilan += 1
                continue
            token = fernet.encrypt(secrets[name].encode("utf-8")).decode("ascii")
            stamp = datetime.now(UTC).isoformat(timespec="seconds")
            await store.execute(
                "INSERT INTO secrets (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
                "updated_at = EXCLUDED.updated_at",
                (name, token, stamp),
            )
            print(f"  yazıldı  {name}")
            yazilan += 1

        print(f"\n{yazilan} sır yazıldı, {atlanan} atlandı.")
        if yazilan and not dry_run:
            print("\nSunucudaki `KM_SECRET_KEY` bu kurulumun `data/secret.key`")
            print("dosyasıyla AYNI olmalı — değilse yazdığımız satırlar çözülemez")
            print("ve ilgili özellikler 'sır kasada yok' demeye devam eder.")
        return 0
    finally:
        await store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--overwrite", action="store_true",
                        help="kasadaki değeri EZ (varsayılan: atla)")
    args = parser.parse_args()
    return asyncio.run(run(args.dsn, dry_run=args.dry_run, overwrite=args.overwrite))


if __name__ == "__main__":
    raise SystemExit(main())
