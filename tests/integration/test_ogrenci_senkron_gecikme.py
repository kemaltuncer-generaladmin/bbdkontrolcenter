"""Senkron doğrulama: KM'de öğrenci güncelle → Akademi'ye ≤ 3 sn (0.5.3).

Kullanıcı: "İki çekirdek tek yere baksın, senkron olsun." Öğrenci kartında
satır içi kayıt (`PATCH /students/{id}` → `StudentService.update_student`)
profil + outbox olayını TEK işlemde yazar; mobil geçidin gönderici döngüsü
(`EventSender`, commit kancasıyla uyanır) olayı imzalı tek isteğle Akademi'nin
`/v1/webhooks/km` ucuna iletir. Bu test zinciri gerçek parçalarla kurar —
gerçek SQLite depo + göçler, gerçek outbox, gerçek öğrenci servisi, gerçek
gönderici — yalnız Akademi `httpx.MockTransport`tur (ağa çıkılmaz).

Ölçülen: kayıt çağrısının başı → Akademi'nin isteği alması (≤ 3 sn hedef,
pratikte ~0,2 sn pencere) ve kartın "Akademi'ye ulaştı ✓" göstergesinin
okuduğu durum (`status()` → `deliveredSeq ≥ lastSeq`, `deliveryLagMs`).
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
for entry in (ROOT / "backend" / "src", ROOT):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from modules.bbd_students.backend.directory import StudentDirectory  # noqa: E402
from modules.bbd_students.backend.service import StudentService  # noqa: E402
from modules.bbd_students.backend.sync import StudentSync  # noqa: E402
from modules.mobile_gateway.backend.events import EventSender  # noqa: E402

from km_core.contracts.module import ScopedStore  # noqa: E402
from km_core.store.bootstrap import build_schema  # noqa: E402
from km_core.store.db import Store  # noqa: E402
from km_platform.events.outbox import EventOutbox  # noqa: E402

BASE = "https://akademi.test"
TARGET_SECONDS = 3.0


class Log:
    def info(self, *args: Any, **kwargs: Any) -> None:
        pass

    warning = error = debug = info


class Canteen:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def add(self, opaque_id: str, name: str) -> None:
        self.rows[opaque_id] = {"id": opaque_id, "displayName": name, "parentPhone": "", "balance": 0,
                                "spendingLimit": None, "isBlocked": False, "updatedAt": 1791786600000}

    async def students(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.rows.values()]

    async def student(self, opaque_id: str) -> dict[str, Any]:
        return dict(self.rows[opaque_id])

    get_student = student

    async def upsert_student(self, opaque_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        row = self.rows.setdefault(opaque_id, {"id": opaque_id, "balance": 0})
        row.update(changes)
        return dict(row)

    async def dashboard(self) -> dict[str, Any]:
        return {}


class Vault:
    async def get(self, key: str) -> str | None:
        return "wh-sır-0123456789" if key == "mobile_gateway.webhook_secret" else None


class Akademi:
    def __init__(self) -> None:
        self.received: list[tuple[float, dict[str, Any]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        batch = json.loads(request.content)
        self.received.append((time.perf_counter(), batch))
        return httpx.Response(200, json={"received": len(batch["events"]), "duplicates": 0,
                                         "lastSeq": batch["events"][-1]["seq"]})


@pytest.fixture
async def chain(tmp_path: Path) -> AsyncIterator[dict[str, Any]]:
    raw = Store(tmp_path / "km.sqlite")
    await raw.open()
    await build_schema(raw)
    for module in ("bbd_students", "mobile_gateway"):
        for path in sorted((ROOT / "modules" / module / "backend" / "migrations").glob("*.sql")):
            await raw.apply_migration(module, path.name, path.read_text(encoding="utf-8"))
    outbox = EventOutbox(raw)
    await outbox.open(prune_loop=False)
    students = ScopedStore(raw, "bbd_students")
    canteen = Canteen()
    directory = StudentDirectory(canteen=canteen, store=students, log=Log())
    sync = StudentSync(store=students, canteen=canteen, log=Log(), outbox=lambda: outbox,
                       on_change=directory.invalidate, delay=0)
    service = StudentService(canteen=canteen, store=students, log=Log(), on_change=directory.invalidate,
                             sync=sync, directory=directory)
    akademi = Akademi()
    sender = EventSender(store=ScopedStore(raw, "mobile_gateway"), outbox=lambda: outbox, secrets=lambda: Vault(),
                         config={"events_enabled": True, "akademi_base_url": BASE, "akademi_tenant": "bbd",
                                 "events_instance_id": "km-test"},
                         log=Log(), transport=httpx.MockTransport(akademi))
    await sender.start()
    await asyncio.sleep(0.05)
    yield {"service": service, "canteen": canteen, "akademi": akademi, "sender": sender, "outbox": outbox}
    await sender.stop()
    await outbox.close()
    await raw.close()


async def test_km_guncellemesi_akademiye_uc_saniyede_ulasir(chain: dict[str, Any]) -> None:
    service: StudentService = chain["service"]
    akademi: Akademi = chain["akademi"]
    sender: EventSender = chain["sender"]
    chain["canteen"].add("STU-1", "Elif Kaya")

    lags: list[float] = []
    for index, class_name in enumerate(("9-A", "10-B", "11-C")):
        started = time.perf_counter()
        await service.update_student("STU-1", {"className": class_name}, actor="test")
        for _ in range(int(TARGET_SECONDS * 100)):
            if len(akademi.received) > index:
                break
            await asyncio.sleep(0.01)
        assert len(akademi.received) > index, "Akademi'ye ulaşmadı"
        arrived, batch = akademi.received[index]
        lags.append(arrived - started)
        event = batch["events"][-1]
        assert event["type"] == "student.updated"
        assert event["subject"] == {"kind": "student", "id": "STU-1"}
        assert "className" in (event.get("fields") or [])
        # Kişisel veri olaya girmez (yalnız kimlik + alan adı).
        assert class_name not in json.dumps(batch, ensure_ascii=False)
        await asyncio.sleep(0.05)

    print(f"\n[senkron] KM kaydı → Akademi isteği: {[round(value * 1000) for value in lags]} ms")
    assert max(lags) <= TARGET_SECONDS

    # Kartın "Akademi'ye ulaştı ✓" göstergesinin okuduğu durum.
    status = await sender.status()
    last = await chain["outbox"].last_seq()
    assert status["enabled"] is True
    assert status["deliveredSeq"] >= last
    assert status["deliveryLagMs"] is not None and status["deliveryLagMs"] <= TARGET_SECONDS * 1000
