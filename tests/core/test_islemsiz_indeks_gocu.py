"""İşlemsiz indeks göçü — canlı PostgreSQL'de `CREATE INDEX CONCURRENTLY`.

`-- km: concurrently` işaretli göç dosyası yalnız indeks açar; merkezde her
indeks işlem dışında, tabloyu yazmaya kapatmadan açılır. Yerel SQLite aynı
dosyayı düz göç gibi uygular. PostgreSQL bölümü `KM_TEST_POSTGRES_DSN`
verilirse gerçek sunucuda koşar (her test boş `public` şemasıyla başlar).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from km_core.store.db import Store
from km_core.store.dialect import UnsupportedDialect, concurrent_index_plan

MIGRATION = """
-- km: concurrently
-- Geri alma: DROP INDEX CONCURRENTLY IF EXISTS mod_ornek_kayit_ogrenci;
CREATE INDEX IF NOT EXISTS mod_ornek_kayit_ogrenci
    ON mod_ornek_kayit (ogrenci, created_at);  -- öğrenci ekranı
CREATE UNIQUE INDEX IF NOT EXISTS mod_ornek_kayit_ref ON mod_ornek_kayit (ref);
"""

TABLE = """
CREATE TABLE IF NOT EXISTS mod_ornek_kayit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ogrenci TEXT NOT NULL,
    ref TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def test_isaretsiz_goc_plan_vermez() -> None:
    assert concurrent_index_plan("CREATE INDEX IF NOT EXISTS a ON t (x);") is None


def test_isaretli_goc_concurrently_ifadelerine_doner() -> None:
    plan = concurrent_index_plan(MIGRATION)
    assert plan == [
        ("mod_ornek_kayit_ogrenci", ("CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                                    "mod_ornek_kayit_ogrenci ON mod_ornek_kayit "
                                    "(ogrenci, created_at)")),
        ("mod_ornek_kayit_ref", ("CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "
                                "mod_ornek_kayit_ref ON mod_ornek_kayit (ref)")),
    ]


@pytest.mark.parametrize("statement", [
    "CREATE TABLE IF NOT EXISTS mod_ornek_x (id TEXT);",
    "INSERT INTO mod_ornek_kayit (ogrenci, ref, created_at) VALUES ('a', 'b', 'c');",
    "CREATE INDEX mod_ornek_eksik ON mod_ornek_kayit (ref);",          # IF NOT EXISTS yok
    "DROP INDEX mod_ornek_kayit_ref;",
])
def test_isaretli_gocte_indeks_disi_ifade_reddedilir(statement: str) -> None:
    with pytest.raises(UnsupportedDialect):
        concurrent_index_plan(f"-- km: concurrently\n{statement}")


def test_bos_isaretli_goc_reddedilir() -> None:
    with pytest.raises(UnsupportedDialect):
        concurrent_index_plan("-- km: concurrently\n-- yalnız yorum\n")


async def test_sqlite_isaretli_gocu_duz_uygular(tmp_path: Path) -> None:
    store = Store(tmp_path / "km.sqlite")
    await store.open()
    await store.apply_migration("ornek", "001_tablo.sql", TABLE)
    await store.apply_migration("ornek", "002_indeks.sql", MIGRATION)
    names = {row["name"] for row in await store.fetch_all(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'mod_ornek_kayit'")}
    assert {"mod_ornek_kayit_ogrenci", "mod_ornek_kayit_ref"} <= names
    assert "002_indeks.sql" in await store.applied_migrations("ornek")
    await store.close()


DSN = os.environ.get("KM_TEST_POSTGRES_DSN")
postgres = pytest.mark.skipif(not DSN, reason="KM_TEST_POSTGRES_DSN verilmedi")


async def _fresh_postgres() -> object:
    import asyncpg

    from km_core.store.postgres import PostgresStore

    connection = await asyncpg.connect(DSN)
    await connection.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    await connection.close()
    store = PostgresStore(str(DSN))
    await store.open()
    return store


@postgres
async def test_postgres_indeksler_islem_disinda_ve_gecerli() -> None:
    store = await _fresh_postgres()
    await store.apply_migration("ornek", "001_tablo.sql", TABLE)  # type: ignore[attr-defined]
    await store.apply_migration("ornek", "002_indeks.sql", MIGRATION)  # type: ignore[attr-defined]
    rows = await store.fetch_all(  # type: ignore[attr-defined]
        "SELECT c.relname AS name, i.indisvalid AS valid FROM pg_index i "
        "JOIN pg_class c ON c.oid = i.indexrelid "
        "WHERE c.relname IN ('mod_ornek_kayit_ogrenci', 'mod_ornek_kayit_ref')")
    assert {row["name"]: row["valid"] for row in rows} == {
        "mod_ornek_kayit_ogrenci": True, "mod_ornek_kayit_ref": True}
    assert "002_indeks.sql" in await store.applied_migrations("ornek")  # type: ignore[attr-defined]
    await store.close()  # type: ignore[attr-defined]


@postgres
async def test_postgres_yarida_kalan_gecersiz_indeks_yeniden_acilir() -> None:
    """Kesilen CONCURRENTLY geçersiz indeks bırakır; IF NOT EXISTS onu atlamamalı."""
    store = await _fresh_postgres()
    await store.apply_migration("ornek", "001_tablo.sql", TABLE)  # type: ignore[attr-defined]
    await store.execute(  # type: ignore[attr-defined]
        "INSERT INTO mod_ornek_kayit (ogrenci, ref, created_at) VALUES "
        "('a', 'R1', '2026'), ('b', 'R1', '2026')")
    import asyncpg

    connection = await asyncpg.connect(DSN)
    with pytest.raises(asyncpg.UniqueViolationError):
        await connection.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY mod_ornek_kayit_ogrenci ON mod_ornek_kayit (ref)")
    assert await connection.fetchval(
        "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
        "WHERE c.relname = 'mod_ornek_kayit_ogrenci'") is False
    await connection.close()

    only_first = "-- km: concurrently\nCREATE INDEX IF NOT EXISTS mod_ornek_kayit_ogrenci " \
                 "ON mod_ornek_kayit (ogrenci, created_at);"
    await store.apply_migration("ornek", "002_indeks.sql", only_first)  # type: ignore[attr-defined]
    row = await store.fetch_one(  # type: ignore[attr-defined]
        "SELECT i.indisvalid AS valid, pg_get_indexdef(i.indexrelid) AS definition "
        "FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
        "WHERE c.relname = 'mod_ornek_kayit_ogrenci'")
    assert row is not None and row["valid"] is True
    assert "(ogrenci, created_at)" in row["definition"]
    await store.close()  # type: ignore[attr-defined]
