"""Platform yeteneği `events.outbox` (ADR 0029) — iki lehçe.

PostgreSQL bölümü `KM_TEST_POSTGRES_DSN` verilirse koşar.
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from km_core.store.bootstrap import build_schema
from km_core.store.db import Store
from km_platform.events.outbox import EventOutbox, OutboxError, new_ulid

DSN = os.environ.get("KM_TEST_POSTGRES_DSN")

ENVELOPE_KEYS = {"eventId", "seq", "type", "occurredAt", "subject", "revision", "fields",
                 "data", "origin", "causationId"}

TABLE = """
CREATE TABLE IF NOT EXISTS mod_ornek_kayit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ad TEXT NOT NULL
);
"""


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 12, 9, 30, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture(params=["sqlite", "postgres"])
async def store(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[Any]:
    if request.param == "sqlite":
        sqlite = Store(tmp_path / "km.sqlite")
        await sqlite.open()
        await build_schema(sqlite)
        await sqlite.apply_migration("ornek", "001.sql", TABLE)
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
    await build_schema(postgres)
    await postgres.apply_migration("ornek", "001.sql", TABLE)
    yield postgres
    await postgres.close()


@pytest.fixture
async def outbox(store: Any) -> AsyncIterator[EventOutbox]:
    box = EventOutbox(store, clock=Clock())
    await box.open(prune_loop=False)
    yield box
    await box.close()


def test_ulid_bicimi() -> None:
    value = new_ulid()
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", value)
    assert new_ulid(1000) < new_ulid(2000)


async def test_zarf_sozlesmeyle_birebir(outbox: EventOutbox) -> None:
    event_id = await outbox.emit("student.updated", ("student", "STU-A1B2C3D4E5"),
                                 revision=57, fields=["className", "gradeLevel", "className"],
                                 causation_id="req-1")
    [event] = await outbox.read_after(0)
    assert set(event) == ENVELOPE_KEYS
    assert event["eventId"] == event_id
    assert event["seq"] == 1 and await outbox.last_seq() == 1
    assert event["subject"] == {"kind": "student", "id": "STU-A1B2C3D4E5"}
    assert event["fields"] == ["className", "gradeLevel"]
    assert event["data"] is None and event["revision"] == 57
    assert event["origin"] == "km" and event["causationId"] == "req-1"
    assert event["occurredAt"] == "2026-10-12T12:30:00.000+03:00"

    await outbox.emit("group.changed", {"kind": "groups", "id": None}, revision=3)
    await outbox.emit("canteen.balance_changed", ("student", "STU-X"), fields=["balance"],
                      data={"balance": 4250, "ledgerSeq": 991245, "reasons": ["sale"],
                            "at": "2026-10-12T12:41:02+03:00"}, origin="canteen",
                      causation_id="CNT-552310")
    events = await outbox.read_after(1)
    assert [item["type"] for item in events] == ["group.changed", "canteen.balance_changed"]
    assert events[0]["subject"] == {"kind": "groups", "id": None}
    assert events[1]["data"]["ledgerSeq"] == 991245


@pytest.mark.parametrize("data", [
    {"phone": "5551112233"}, {"displayName": "Ali"}, {"inner": {"parent_phone": "x"}},
    {"accessCode": "123456"}, {"items": [{"name": "Ali"}]},
])
async def test_kisisel_veri_reddedilir(outbox: EventOutbox, data: dict[str, Any]) -> None:
    with pytest.raises(OutboxError, match="A6"):
        await outbox.emit("student.updated", ("student", "STU-1"), data=data)
    assert await outbox.read_after(0) == []


@pytest.mark.parametrize("kwargs", [
    {"type": "Student.Updated", "subject": ("student", "S")},
    {"type": "student.updated", "subject": ("person", "S")},
    {"type": "student.updated", "subject": ("student", "S"), "origin": "web"},
])
async def test_gecersiz_olay_reddedilir(outbox: EventOutbox, kwargs: dict[str, Any]) -> None:
    options = dict(kwargs)
    with pytest.raises(OutboxError):
        await outbox.emit(options.pop("type"), options.pop("subject"), **options)


async def test_olay_is_yazmasiyla_ayni_islemde(store: Any, outbox: EventOutbox) -> None:
    with pytest.raises(RuntimeError):
        async with store.transaction():
            await store.execute("INSERT INTO mod_ornek_kayit (ad) VALUES (?)", ("a",))
            await outbox.emit("student.created", ("student", "STU-1"))
            raise RuntimeError("geri al")
    assert await outbox.read_after(0) == []
    assert await outbox.last_seq() == 0

    async with store.transaction():
        await store.execute("INSERT INTO mod_ornek_kayit (ad) VALUES (?)", ("b",))
        await outbox.emit("student.created", ("student", "STU-2"))
        await outbox.emit("guardian.linked", ("student", "STU-2"), fields=["guardians"],
                          data={"slot": 1})
        # Commit'ten önce görünmez (satır commit anında yazılır).
        assert await outbox.last_seq() == 0
    events = await outbox.read_after(0)
    assert [item["type"] for item in events] == ["student.created", "guardian.linked"]
    rows = await store.fetch_all("SELECT ad FROM mod_ornek_kayit")
    assert [row["ad"] for row in rows] == ["b"]


async def test_seq_commit_sirasidir(store: Any, outbox: EventOutbox) -> None:
    """İlk emit eden SONRA commit ederse daha büyük seq alır: imleç atlamaz."""
    first_emitted = asyncio.Event()
    second_done = asyncio.Event()

    async def slow() -> None:
        async with store.transaction():
            await outbox.emit("student.updated", ("student", "STU-YAVAS"))
            first_emitted.set()
            if isinstance(store, Store):
                return  # SQLite: depo kilidi zaten sıralıyor
            await second_done.wait()

    async def fast() -> None:
        await first_emitted.wait()
        async with store.transaction():
            await outbox.emit("student.updated", ("student", "STU-HIZLI"))
        second_done.set()

    await asyncio.wait_for(asyncio.gather(slow(), fast()), timeout=10)
    events = await outbox.read_after(0)
    if isinstance(store, Store):
        assert [item["subject"]["id"] for item in events] == ["STU-YAVAS", "STU-HIZLI"]
    else:
        assert [item["subject"]["id"] for item in events] == ["STU-HIZLI", "STU-YAVAS"]
    assert [item["seq"] for item in events] == sorted(item["seq"] for item in events)


async def test_wait_after_uyanir_ve_zaman_asar(outbox: EventOutbox) -> None:
    assert await outbox.wait_after(0, 0.05) is False

    async def later() -> None:
        await asyncio.sleep(0.05)
        await outbox.emit("bell.week_changed", ("bell", None))

    task = asyncio.create_task(later())
    started = asyncio.get_running_loop().time()
    assert await outbox.wait_after(0, 5) is True
    assert asyncio.get_running_loop().time() - started < 2
    await task
    assert await outbox.wait_after(0, 0) is True


async def test_tur_suzgeci(outbox: EventOutbox) -> None:
    await outbox.emit("student.updated", ("student", "S1"))
    await outbox.emit("bell.week_changed", ("bell", None))
    await outbox.emit("canteen.controls_changed", ("student", "S1"), fields=["isBlocked"])
    events, scanned = await outbox.scan_after(0, 100, ["student.*", "canteen.controls_changed"])
    assert [item["type"] for item in events] == ["student.updated", "canteen.controls_changed"]
    assert scanned == 3
    events, scanned = await outbox.scan_after(0, 100, ["tablet.*"])
    assert events == [] and scanned == 3


async def test_tuketici_ve_budama(store: Any) -> None:
    clock = Clock()
    box = EventOutbox(store, clock=clock)
    await box.open(prune_loop=False)
    for index in range(4):
        await box.emit("student.updated", ("student", f"S{index}"))
    assert await box.register_consumer("akademi", start=0) == 0
    assert await box.consumer_seq("akademi", 2) == 2
    assert await box.consumer_seq("akademi", 1) == 2  # geri gitmez

    clock.now += timedelta(days=15)
    await box.emit("student.updated", ("student", "S-yeni"))
    # 14 günden eski + tüketicinin geçtiği (seq ≤ 2) silinir; 3 ve 4 kalır.
    assert await box.prune() == 2
    assert [item["seq"] for item in await box.read_after(0)] == [3, 4, 5]
    assert await box.expired(1) is True
    assert await box.expired(2) is False

    # Sert sınır: tüketici takılsa da 30 günü aşan olay gider, en yeni satır kalır.
    clock.now += timedelta(days=31)
    assert await box.prune() == 2
    assert [item["seq"] for item in await box.read_after(0)] == [5]
    assert await box.expired(3) is True
    await box.close()


async def test_kapanis_kancasi_koşar(outbox: EventOutbox) -> None:
    flushed: list[str] = []

    async def flush() -> None:
        await outbox.emit("canteen.balance_changed", ("student", "S1"), fields=["balance"],
                          data={"balance": 1, "ledgerSeq": 1, "reasons": ["sale"], "at": "x"},
                          origin="canteen")
        flushed.append("ok")

    outbox.on_close(flush)
    await outbox.close()
    assert flushed == ["ok"]
    assert [item["type"] for item in await outbox.read_after(0)] == ["canteen.balance_changed"]
