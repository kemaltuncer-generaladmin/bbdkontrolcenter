#!/usr/bin/env python3
"""Yerel SQLite'taki her şeyi merkezdeki PostgreSQL'e taşır — TEK SEFERLİK.

    scripts/migrate-to-central.py --dsn postgresql://... [--source data/kontrol-merkezi.sqlite]
    scripts/migrate-to-central.py --dsn ... --dry-run     # hiçbir şey yazmaz, ne olacağını söyler

ADR 0026 — doğruluk kaynağı merkezdir. Bu betik geçişin veri ayağıdır: 130
tablonun satırları, üretilen dosyalar ve dizi (`BIGSERIAL`) sayaçları taşınır.

## Sırası ve gerekçeleri

  1. **Yedek.** Kaynak dosyanın zaman damgalı kopyası `data/backups/` altına
     alınır. 2 MB'lık ucuz bir sigorta.
  2. **Şema.** Hedefte `build_schema` koşar — kurulumdakiyle BİREBİR aynı
     kurucu. Uygulamayı önce açıp şemayı ona kurdurmak cazip ama YANLIŞ:
     uygulama açılırken kadro boş olduğu için kendi bootstrap yöneticisini
     yaratır ve o satır taşınan gerçek yöneticiyle `pin_lookup` üzerinde
     çakışır. Şema burada kurulur, uygulama SONRA açılır ve kadroyu dolu
     bulup bootstrap'ı atlar.
  3. **Satırlar.** Tablo tablo, sütun kesişimiyle.
  4. **Diziler.** `BIGSERIAL` sayaçları en büyük değere kurulur; kurulmazsa
     ilk yeni kayıt "anahtar zaten var" ile patlar.
  5. **Doğrulama.** Tablo tablo satır sayısı karşılaştırılır. Tek bir
     uyuşmazlık göçü BAŞARISIZ sayar — yarım taşınmış veri, hiç taşınmamış
     veriden kötüdür çünkü fark edilmez.

## Ne taşınmaz

  · `sqlite_sequence` — SQLite'ın kendi iç tablosu; PostgreSQL diziyi kendi
    tutar (4. adım).
  · `schema_migrations` — hedefin kendi göç defteri 2. adımda doldu. Kaynağın
    defterini üstüne yazmak, hedefte HİÇ uygulanmamış göçleri "uygulandı"
    diye işaretlerdi.
  · `sessions` — açık oturumlar makineye özeldir; taşınırsa herkes kendini
    yeni sunucuda giriş yapmış sanır. Kimse kaybetmez, yeniden girilir.
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from km_core.store.bootstrap import build_schema
from km_core.store.postgres import PostgresStore

#: Taşınmayan tablolar ve gerekçeleri modül başlığındadır.
SKIP_TABLES = frozenset({"sqlite_sequence", "schema_migrations", "sessions"})

#: Tek `INSERT` ile gönderilen satır sayısı. Büyük tablo tek seferde
#: gönderilirse asyncpg'nin parametre sınırına (65535) takılır.
BATCH = 500


def _tables(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    return [str(row[0]) for row in rows if str(row[0]) not in SKIP_TABLES]


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')]


def _backup(source: Path) -> Path:
    stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M%S")
    target = ROOT / "data" / "backups" / f"{source.stem}-goc-oncesi-{stamp}.sqlite"
    target.parent.mkdir(parents=True, exist_ok=True)
    # WAL kipindeki veritabanı düz kopyayla eksik alınır: son yazmalar `-wal`
    # dosyasında durur. SQLite'ın kendi yedekleme API'si bunu birleştirir.
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src, \
            sqlite3.connect(target) as dst:
        src.backup(dst)
    return target


async def _sequences(store: PostgresStore, table: str, columns: list[str]) -> None:
    """`BIGSERIAL` sayaçlarını en büyük değere kurar.

    Kurulmazsa dizi 1'den başlar ve ilk yeni kayıt var olan anahtara çarpar.
    """
    for column in columns:
        row = await store.fetch_one(
            "SELECT pg_get_serial_sequence($1, $2) AS seq", (table, column)
        )
        sequence = (row or {}).get("seq")
        if not sequence:
            continue
        await store.execute(
            f'SELECT setval(\'{sequence}\', COALESCE((SELECT MAX("{column}") '
            f'FROM "{table}"), 1), true)'
        )


async def _copy_table(
    store: PostgresStore, connection: sqlite3.Connection, table: str, *, dry_run: bool
) -> tuple[int, str | None]:
    source_columns = _columns(connection, table)
    target_columns = await store.table_columns(table)

    shared = [name for name in source_columns if name in target_columns]
    if not shared:
        return 0, "hedefte karşılık gelen sütun yok"
    missing = [name for name in source_columns if name not in target_columns]

    columns_sql = ", ".join(f'"{name}"' for name in shared)
    rows = connection.execute(f'SELECT {columns_sql} FROM "{table}"').fetchall()
    if not rows:
        return 0, f"atlanan sütun: {', '.join(missing)}" if missing else None

    if not dry_run:
        # HEDEF TABLO ÖNCE BOŞALTILIR. İki gerekçe:
        #
        #  · `build_schema` bazı tabloya satır yazar — `0008`/`0009` çekirdek
        #    göçleri denetim izine kendi izlerini bırakır. Kaynakta da AYNI
        #    satırlar var (orada da o göçler koştu); boşaltılmazsa birincil
        #    anahtar çakışır ve göç yarıda kalır.
        #  · Betik yeniden koşturulabilir olur. Yarıda kalan bir göçün
        #    ardından "kaldığı yerden" devam etmeye çalışmak, hangi tablonun
        #    yarım olduğunu bilmeyi gerektirir; baştan yazmak kesindir.
        #
        # Dolu bir merkeze yanlışlıkla koşmaya karşı koruma `run()` içinde:
        # kadro doluysa betik hiç başlamaz.
        await store.execute(f'DELETE FROM "{table}"')

        placeholders = ", ".join("?" for _ in shared)
        statement = f'INSERT INTO "{table}" ({columns_sql}) VALUES ({placeholders})'
        for start in range(0, len(rows), BATCH):
            await store.execute_many(statement, [tuple(r) for r in rows[start:start + BATCH]])
        await _sequences(store, table, shared)

    note = f"atlanan sütun: {', '.join(missing)}" if missing else None
    return len(rows), note


def _report_secret_key() -> None:
    """Sunucuya girilmesi gereken kasa anahtarını söyler.

    BU ADIM ATLANIRSA KİMSE GİRİŞ YAPAMAZ ve sebebi hiç ele vermez. Taşınan
    18 sır (`core.pin_pepper` dahil) BU anahtarla şifrelenmiştir; sunucu
    anahtarı bulamazsa kendine yenisini üretir, sırların hiçbirini çözemez ve
    hiçbir PIN eşleşmez. Kadro dolu, loglar temiz, giriş yok.

    Değer ekrana yazılır çünkü Coolify'a elle girilmesi gerekir; loga
    DÜŞMEZ ve dosyaya YAZILMAZ.
    """
    key_file = ROOT / "data" / "secret.key"
    print("\n5) sunucuya girilecek kasa anahtarı")
    if not key_file.is_file():
        print(f"   UYARI: {key_file.relative_to(ROOT)} yok — sırlar bu makinede")
        print("   şifrelenmemiş olabilir. Taşımadan önce bakılmalı.")
        return
    print("   Coolify → KM Sunucu → Environment Variables:")
    print(f"\n   KM_SECRET_KEY={key_file.read_text(encoding='ascii').strip()}\n")
    print("   Bu adım ATLANIRSA sunucu kendine yeni bir anahtar üretir, taşınan")
    print("   sırların hiçbirini çözemez ve HİÇ KİMSE GİRİŞ YAPAMAZ. Belirti")
    print("   sebebi ele vermez: kadro dolu görünür, loglar temiz olur.")


async def run(source: Path, dsn: str, *, dry_run: bool, force: bool = False) -> int:
    if not source.is_file():
        print(f"HATA: kaynak bulunamadı: {source}")
        return 2

    print(f"kaynak : {source}")
    print(f"hedef  : {dsn.rsplit('@', 1)[-1]}")      # PAROLA YAZILMAZ
    print(f"kip    : {'DENEME (hiçbir şey yazılmaz)' if dry_run else 'GERÇEK'}\n")

    if not dry_run:
        print(f"1) yedek → {_backup(source).relative_to(ROOT)}")
    else:
        print("1) yedek → atlandı (deneme kipi)")

    store = PostgresStore(dsn)
    await store.open()
    try:
        if dry_run:
            print("2) şema  → atlandı (deneme kipi)")
        else:
            applied = await build_schema(store, ROOT / "modules")
            print(f"2) şema  → {len(applied)} göç uygulandı")

            # DOLU BİR MERKEZE KOŞULMASIN. Betik hedef tabloları boşaltarak
            # yazıyor; çalışan bir merkeze yanlış adresle koşulursa herkesin
            # verisi gider. Kadro bu sorunun en iyi göstergesidir: `build_schema`
            # kullanıcı yaratmaz, dolayısıyla taze bir merkezde `users` BOŞTUR.
            # Dolu bulunması "burada zaten bir kurulum yaşıyor" demektir.
            row = await store.fetch_one("SELECT COUNT(*) AS n FROM users")
            if int((row or {}).get("n") or 0) and not force:
                print("\nHATA: hedefte zaten kullanıcı var — bu boş bir merkez değil.")
                print("Göç hedefteki tabloları BOŞALTARAK yazar; yanlış adrese")
                print("koşulduğunda çalışan bir kurulumu silerdi.")
                print("Gerçekten üzerine yazılacaksa: --force")
                return 2

        connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        try:
            tables = _tables(connection)
            print(f"3) satırlar → {len(tables)} tablo\n")

            taken: dict[str, int] = {}
            notes: list[str] = []
            for table in tables:
                count, note = await _copy_table(store, connection, table, dry_run=dry_run)
                taken[table] = count
                if note:
                    notes.append(f"   {table}: {note}")
                if count:
                    print(f"   {count:>7}  {table}")

            for line in notes:
                print(line)

            # ---------------------------------------------------- doğrulama
            print("\n4) doğrulama")
            if dry_run:
                print("   deneme kipi — karşılaştırma yapılmadı")
                return 0

            mismatched: list[str] = []
            for table, expected in taken.items():
                row = await store.fetch_one(f'SELECT COUNT(*) AS n FROM "{table}"')
                actual = int((row or {}).get("n") or 0)
                if actual != expected:
                    mismatched.append(f"   {table}: kaynak {expected}, hedef {actual}")
            if mismatched:
                print("   UYUŞMAZLIK — göç BAŞARISIZ:")
                print("\n".join(mismatched))
                return 1

            total = sum(taken.values())
            print(f"   {len(taken)} tablo, {total} satır — hepsi uyuştu.")
            _report_secret_key()
            return 0
        finally:
            connection.close()
    finally:
        await store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True, help="PostgreSQL adresi")
    parser.add_argument("--source", default=str(ROOT / "data" / "kontrol-merkezi.sqlite"))
    parser.add_argument("--dry-run", action="store_true",
                        help="hiçbir şey yazma, ne olacağını söyle")
    parser.add_argument("--force", action="store_true",
                        help="hedefte kullanıcı olsa bile ÜZERİNE YAZ")
    args = parser.parse_args()
    return asyncio.run(run(Path(args.source), args.dsn,
                       dry_run=args.dry_run, force=args.force))


if __name__ == "__main__":
    raise SystemExit(main())
