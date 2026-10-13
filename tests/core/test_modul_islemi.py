"""Modül işlemi (ADR 0029) — `Store.transaction()` ve `PostgresStore.transaction()`.

Her test iki lehçede koşar: SQLite her zaman, PostgreSQL `KM_TEST_POSTGRES_DSN`
verilirse (her test boş `public` şemasıyla başlar).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from km_core.contracts.module import ScopedStore
from km_core.store.db import Store

DSN = os.environ.get("KM_TEST_POSTGRES_DSN")

TABLE = """
CREATE TABLE IF NOT EXISTS mod_ornek_sayac (
    id INTEGER PRIMARY KEY,
    n INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS mod_ornek_kayit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ad TEXT NOT NULL
);
"""


@pytest.fixture(params=["sqlite", "postgres"])
async def store(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[Any]:
    if request.param == "sqlite":
        sqlite = Store(tmp_path / "km.sqlite")
        await sqlite.open()
        await sqlite.apply_migration("ornek", "001.sql", TABLE)
        await sqlite.execute("INSERT INTO mod_ornek_sayac (id, n) VALUES (1, 0)")
        yield sqlite
        await sqlite.close()
        return
    if not DSN:
        pytest.skip("KM_TEST_POSTGRES_DSN verilmedi")
    import asyncpg

    from km_core.store.postgres import PostgresStore

    connection = await asyncpg.connect(DSN)
    await connection.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    await connection.close()
    postgres = PostgresStore(DSN)
    await postgres.open()
    await postgres.apply_migration("ornek", "001.sql", TABLE)
    await postgres.execute("INSERT INTO mod_ornek_sayac (id, n) VALUES (1, 0)")
    yield postgres
    await postgres.close()


async def _names(store: Any) -> list[str]:
    return [row["ad"] for row in await store.fetch_all(
        "SELECT ad FROM mod_ornek_kayit ORDER BY id")]


async def test_commit_butun_yazmalari_kalici_yapar(store: Any) -> None:
    async with store.transaction():
        await store.execute("INSERT INTO mod_ornek_kayit (ad) VALUES (?)", ("a",))
        await store.execute("INSERT INTO mod_ornek_kayit (ad) VALUES (?)", ("b",))
        # İşlem içindeki okuma kendi yazmasını görür.
        assert await _names(store) == ["a", "b"]
    assert await _names(store) == ["a", "b"]
    assert store.current_transaction() is None


async def test_hata_hepsini_geri_alir(store: Any) -> None:
    with pytest.raises(RuntimeError, match="patla"):
        async with store.transaction():
            await store.execute("INSERT INTO mod_ornek_kayit (ad) VALUES (?)", ("a",))
            raise RuntimeError("patla")
    assert await _names(store) == []
    # Depo kullanılmaya devam eder.
    await store.execute("INSERT INTO mod_ornek_kayit (ad) VALUES (?)", ("b",))
    assert await _names(store) == ["b"]


async def test_ic_ice_cagri_dis_isleme_katilir(store: Any) -> None:
    with pytest.raises(ValueError):
        async with store.transaction() as outer:
            await store.execute("INSERT INTO mod_ornek_kayit (ad) VALUES (?)", ("dis",))
            async with store.transaction() as inner:
                assert inner is outer
                await store.execute("INSERT INTO mod_ornek_kayit (ad) VALUES (?)", ("ic",))
            # İç blok bitti ama commit OLMADI: dış hata ikisini de geri alır.
            raise ValueError("dış")
    assert await _names(store) == []

    async with store.transaction():
        async with store.transaction():
            await store.execute("INSERT INTO mod_ornek_kayit (ad) VALUES (?)", ("ic",))
        await store.execute("INSERT INTO mod_ornek_kayit (ad) VALUES (?)", ("dis",))
    assert await _names(store) == ["ic", "dis"]


async def test_scoped_store_ayni_isleme_katilir(store: Any) -> None:
    scoped = ScopedStore(store, "ornek")
    with pytest.raises(RuntimeError):
        async with scoped.transaction():
            await scoped.execute("INSERT INTO mod_ornek_kayit (ad) VALUES (?)", ("modul",))
            # Çekirdek deposuna doğrudan yazan (ör. platform yeteneği) aynı işlemdedir.
            await store.execute("INSERT INTO mod_ornek_kayit (ad) VALUES (?)", ("platform",))
            raise RuntimeError("geri")
    assert await _names(store) == []


async def test_eszamanli_iki_islem_birbirini_bozmaz(store: Any) -> None:
    """İki işlem aynı sayacı artırır ve kendi kaydını yazar; biri geri alınır.

    Sayaç `n = n + 1` biçiminde artırılır (PostgreSQL'de satır kilidi sıralar,
    SQLite'ta depo kilidi). Geri alınan işlemin yazmaları öbürüne sızmaz.
    """
    started = asyncio.Event()

    async def first() -> None:
        async with store.transaction():
            await store.execute("UPDATE mod_ornek_sayac SET n = n + 1 WHERE id = 1")
            await store.execute("INSERT INTO mod_ornek_kayit (ad) VALUES (?)", ("bir",))
            started.set()
            await asyncio.sleep(0.05)

    async def second() -> None:
        await started.wait()
        async with store.transaction():
            await store.execute("UPDATE mod_ornek_sayac SET n = n + 1 WHERE id = 1")
            await store.execute("INSERT INTO mod_ornek_kayit (ad) VALUES (?)", ("iki",))

    async def failing() -> None:
        await started.wait()
        with pytest.raises(RuntimeError):
            async with store.transaction():
                await store.execute("UPDATE mod_ornek_sayac SET n = n + 1 WHERE id = 1")
                await store.execute("INSERT INTO mod_ornek_kayit (ad) VALUES (?)", ("uc",))
                raise RuntimeError("geri al")

    await asyncio.gather(first(), second(), failing())
    row = await store.fetch_one("SELECT n FROM mod_ornek_sayac WHERE id = 1")
    assert row is not None and int(row["n"]) == 2
    assert sorted(await _names(store)) == ["bir", "iki"]


async def test_islem_disi_okuma_commit_edilmemis_yazmayi_gormez(store: Any) -> None:
    inside = asyncio.Event()
    release = asyncio.Event()
    seen: list[list[str]] = []

    async def writer() -> None:
        async with store.transaction():
            await store.execute("INSERT INTO mod_ornek_kayit (ad) VALUES (?)", ("gizli",))
            inside.set()
            await release.wait()

    async def reader() -> None:
        await inside.wait()
        task = asyncio.create_task(_names(store))
        await asyncio.sleep(0.02)
        release.set()
        seen.append(await task)

    await asyncio.gather(writer(), reader())
    # SQLite: okuma işlem bitene kadar bekler ve commit edilmiş hâli görür.
    # PostgreSQL: okuma beklemez ve (READ COMMITTED) eski hâli görür.
    assert seen[0] in ([], ["gizli"])
    if isinstance(store, Store):
        assert seen[0] == ["gizli"]
    else:
        assert seen[0] == []


async def test_kancalar_sirasi_ve_hata(store: Any) -> None:
    calls: list[str] = []
    async with store.transaction() as transaction:
        transaction.before_commit(lambda: calls.append("before"))
        transaction.after_commit(lambda: calls.append("after"))
        transaction.after_end(lambda: calls.append("end"))
    assert calls == ["before", "after", "end"]

    calls.clear()

    async def boom() -> None:
        await store.execute("INSERT INTO mod_ornek_kayit (ad) VALUES (?)", ("kanca",))
        raise RuntimeError("kanca")

    with pytest.raises(RuntimeError, match="kanca"):
        async with store.transaction() as transaction:
            await store.execute("INSERT INTO mod_ornek_kayit (ad) VALUES (?)", ("govde",))
            transaction.before_commit(boom)
            transaction.after_commit(lambda: calls.append("after"))
            transaction.after_end(lambda: calls.append("end"))
    # before_commit patladı: hem gövde hem kanca yazması geri alındı, after_end koştu.
    assert calls == ["end"]
    assert await _names(store) == []


async def test_execute_script_islem_icinde_reddedilir(store: Any) -> None:
    with pytest.raises(RuntimeError, match="ADR 0029"):
        async with store.transaction():
            await store.execute_script("SELECT 1;")
