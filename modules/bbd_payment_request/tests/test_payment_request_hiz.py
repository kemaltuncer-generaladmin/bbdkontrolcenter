"""Ödeme talebi ekranının hızı: "son gönderim" özeti ve paralel kantin okumaları.

Panel açılışı (`workspace`) ve toplu prova (`preview`) öğrenci başına son
başarılı gönderimi soruyordu; bu bütün `entry` geçmişinin taranmasıydı.
Artık özet tablodan okunur (göç 002). Depo gerçek SQLite, kantin taklittir.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from bbd_payment_request_backend.service import PaymentRequestService

from km_core.contracts.module import ScopedStore
from km_core.store.db import Store

MIGRATIONS = Path(__file__).resolve().parents[1] / "backend" / "migrations"


class Log:
    def info(self, *_a: Any, **_k: Any) -> None:
        pass

    def warning(self, *_a: Any, **_k: Any) -> None:
        pass


class SlowCanteen:
    """Her okuma `delay` sürer; eşzamanlılık ölçülsün."""

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.request_filters: list[str | None] = []
        self.created: list[str] = []

    async def _wait(self) -> None:
        if self.delay:
            await asyncio.sleep(self.delay)

    async def students(self) -> list[dict[str, Any]]:
        await self._wait()
        return [{"id": "STU-1", "displayName": "Elif", "parentPhone": "5329876543",
                 "balance": 4250},
                {"id": "STU-2", "displayName": "Can", "parentPhone": "5051112233",
                 "balance": 1000}]

    async def student(self, opaque_id: str) -> dict[str, Any]:
        await self._wait()
        return {"id": opaque_id, "displayName": "Elif", "parentPhone": "5329876543",
                "balance": 4250}

    async def payment_requests(self, student_id: str | None = None) -> list[dict[str, Any]]:
        await self._wait()
        self.request_filters.append(student_id)
        return [{"ref": "PR-1", "studentId": "STU-1", "status": "PENDING", "amount": 4250}]

    async def settings(self) -> dict[str, Any]:
        await self._wait()
        return {"smsTemplates": {"payment_request": "x"}}

    async def canteen_settings_public(self) -> dict[str, Any]:
        await self._wait()
        return {"allowPrepayment": False, "maxPrepayment": 0}

    async def create_payment_request(self, student_id: str, amount: int | None = None,
                                     origin: str = "admin") -> dict[str, Any]:
        self.created.append(student_id)
        return {"queued": True, "ref": f"PR-{len(self.created)}", "amount": 4250}


async def _store(tmp_path: Path, *, until: str | None = None) -> tuple[Store, ScopedStore]:
    database = Store(tmp_path / "km.sqlite")
    await database.open()
    for migration in sorted(MIGRATIONS.glob("*.sql")):
        if until is not None and migration.name > until:
            break
        await database.apply_migration("bbd_payment_request", migration.name,
                                       migration.read_text(encoding="utf-8"))
    return database, ScopedStore(database, "bbd_payment_request")


async def test_goc_002_son_gonderimi_gecmisten_doldurur(tmp_path: Path) -> None:
    database, store = await _store(tmp_path, until="001_payment.sql")
    rows = [("STU-1", "queued", "2026-10-01T08:00:00+00:00"),
            ("STU-1", "queued", "2026-10-05T08:00:00+00:00"),
            ("STU-1", "failed", "2026-10-09T08:00:00+00:00"),   # başarısız sayılmaz
            ("STU-2", "skipped", "2026-10-09T08:00:00+00:00"),
            ("STU-3", "queued", "2026-09-30T08:00:00+00:00")]
    for kantin_id, status, at in rows:
        await store.execute(
            "INSERT INTO mod_bbd_payment_request_entry (kantin_id, status, created_at) "
            "VALUES (?, ?, ?)", (kantin_id, status, at))
    for migration in sorted(MIGRATIONS.glob("*.sql")):
        if migration.name > "001_payment.sql":
            await database.apply_migration("bbd_payment_request", migration.name,
                                           migration.read_text(encoding="utf-8"))
    summary = {row["kantin_id"]: row["last_at"] for row in await store.fetch_all(
        "SELECT kantin_id, last_at FROM mod_bbd_payment_request_last_sent")}
    assert summary == {"STU-1": "2026-10-05T08:00:00+00:00",
                       "STU-3": "2026-09-30T08:00:00+00:00"}
    await database.close()


async def test_basarili_gonderim_ozeti_gunceller_basarisiz_guncellemez(tmp_path: Path) -> None:
    database, store = await _store(tmp_path)
    service = PaymentRequestService(canteen=SlowCanteen(), store=store, log=Log(), config={})
    await service._record_entry(batch_ref="B", kantin_id="STU-1", name="", phone="",
                                amount=1, ref="", status="failed", reason="no_phone")
    assert await service._last_sent_rows() == []
    await service.request_one("STU-1", amount=None, actor="Ayşe")
    rows = await service._last_sent_rows()
    assert [row["kantin_id"] for row in rows] == ["STU-1"]
    # Daha eski bir damga yenisini EZMEZ.
    await store.execute("UPDATE mod_bbd_payment_request_last_sent SET last_at = ?",
                        ("2099-01-01T00:00:00+00:00",))
    await service.request_one("STU-1", amount=None, actor="Ayşe")
    assert (await service._last_sent_rows())[0]["last_at"] == "2099-01-01T00:00:00+00:00"
    await database.close()


async def test_prova_yakin_gonderimi_ozetten_gorur(tmp_path: Path) -> None:
    database, store = await _store(tmp_path)
    canteen = SlowCanteen()
    service = PaymentRequestService(canteen=canteen, store=store, log=Log(), config={})
    await service.request_one("STU-1", amount=None, actor="Ayşe")
    preview = await service.preview({"students": ["STU-1", "STU-2"]})
    verdicts = {row["kantinId"]: row["verdict"] for row in preview["rows"]}
    assert verdicts == {"STU-1": "recent", "STU-2": "ok"}
    await database.close()


async def test_acilis_kantin_okumalari_ayni_anda(tmp_path: Path) -> None:
    database, store = await _store(tmp_path)
    canteen = SlowCanteen(delay=0.1)
    service = PaymentRequestService(canteen=canteen, store=store, log=Log(), config={})
    started = time.perf_counter()
    result = await service.workspace()
    elapsed = time.perf_counter() - started
    assert result["connected"] is True and result["summary"]["pending"] == 1
    # Dört okuma × 0,1 sn sırayla 0,4 sn olurdu; aynı anda ~0,1 sn.
    assert elapsed < 0.3
    await database.close()


async def test_ogrenci_karti_talepleri_ogrenci_suzgeciyle_ister(tmp_path: Path) -> None:
    database, store = await _store(tmp_path)
    canteen = SlowCanteen(delay=0.1)
    service = PaymentRequestService(canteen=canteen, store=store, log=Log(), config={})
    started = time.perf_counter()
    state = await service.student_state("STU-1")
    assert time.perf_counter() - started < 0.25        # üç okuma aynı anda
    assert state["pendingRequest"]["ref"] == "PR-1"
    assert canteen.request_filters == ["STU-1"]
    await database.close()
