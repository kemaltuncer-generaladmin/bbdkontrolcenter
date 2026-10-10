"""Test ortamı — gerçek SQLite depo, modülün kendi göçleri ve lehçe denetimi.

`pytest modules/bbd_tablet` tek başına koşabilsin diye kaynak dizini ve depo
kökü sys.path'e eklenir (kök `tests/conftest.py` yalnız `tests/` altında
geçerli).

Depo taklit değildir: çekirdeğin `Store`u geçici dosyada açılır ve göçler
`apply_migration` ile uygulanır, böylece K5 ad denetimi de her koşuda çalışır.
Her SQL ifadesi ayrıca PostgreSQL çevirmeninden geçirilir: merkezde
reddedilecek bir yapı burada patlar.
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

MODULE_ROOT = Path(__file__).resolve().parents[1]
REPO = MODULE_ROOT.parents[1]
for entry in (REPO / "backend" / "src", REPO):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from km_core.contracts.module import ScopedStore
from km_core.store.db import Store
from km_core.store.dialect import to_postgres

MIGRATIONS = MODULE_ROOT / "backend" / "migrations"


class CheckedStore:
    """`ScopedStore` that also counts statements and proves PostgreSQL support."""

    def __init__(self, database: Store) -> None:
        self.database = database
        self.inner = ScopedStore(database, "bbd_tablet")
        self.reads = 0
        self.writes = 0

    def table(self, name: str) -> str:
        return self.inner.table(name)

    def reset(self) -> None:
        self.reads = self.writes = 0

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        to_postgres(sql)
        self.reads += 1
        return await self.inner.fetch_all(sql, tuple(params))

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        to_postgres(sql)
        self.reads += 1
        return await self.inner.fetch_one(sql, tuple(params))

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        to_postgres(sql)
        self.writes += 1
        await self.inner.execute(sql, tuple(params))


class Clock:
    """Wednesday 2026-10-14 10:00 Europe/Istanbul unless moved."""

    def __init__(self) -> None:
        self.value = datetime(2026, 10, 14, 7, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **delta: float) -> None:
        self.value += timedelta(**delta)


class LookupError404(Exception):
    status = 404


class Canteen:
    def __init__(self) -> None:
        self.codes = {"123456": ("opaque-A", "Ahmet"), "654321": ("opaque-B", "Büşra")}
        self.down = False
        self.student_calls = 0

    async def lookup_student_by_code(self, code: str) -> dict[str, str]:
        if self.down:
            raise RuntimeError("kantin kapalı")
        if code not in self.codes:
            raise LookupError404
        student_id, name = self.codes[code]
        return {"id": student_id, "displayName": name}

    async def students(self) -> list[dict[str, Any]]:
        self.student_calls += 1
        if self.down:
            raise RuntimeError("kantin kapalı")
        return [{"id": student_id, "displayName": name}
                for student_id, name in self.codes.values()]


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[CheckedStore]:
    # Çevirmen yalnız sözdizimini görür; GROUP BY katılığı, tip karşılaştırması
    # gibi anlam farkları için aynı testler gerçek PostgreSQL'de de koşabilir:
    # KM_TEST_POSTGRES_DSN verilirse her test boş bir `public` şemasıyla başlar.
    dsn = os.environ.get("KM_TEST_POSTGRES_DSN")
    database: Any
    if dsn:
        import asyncpg

        from km_core.store.postgres import PostgresStore

        connection = await asyncpg.connect(dsn)
        await connection.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        await connection.close()
        database = PostgresStore(dsn)
    else:
        database = Store(tmp_path / "tablet.sqlite")
    await database.open()
    for migration in sorted(MIGRATIONS.glob("*.sql")):
        await database.apply_migration("bbd_tablet", migration.name,
                                       migration.read_text(encoding="utf-8"))
    yield CheckedStore(database)
    await database.close()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def canteen() -> Canteen:
    return Canteen()
