"""Kantin → KM olayları ve merkezi senkron istemci uçları (km-merkezi-senkron §6).

İstemci `httpx.MockTransport` ile, alıcı GERÇEK SQLite + gerçek outbox ile
sınanır; öğrenci yeteneği (`bbd_students.sync`) sahtedir. Ağa çıkılmaz.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from modules.bbd_canteen_api.backend.client import CanteenApi, CanteenError
from modules.bbd_canteen_api.backend.events import CanteenEvents

from km_core.contracts.module import ScopedStore
from km_core.store.bootstrap import build_schema
from km_core.store.db import Store
from km_platform.events.outbox import EventOutbox

MIGRATIONS = Path(__file__).resolve().parents[1] / "backend" / "migrations"


class _Secrets:
    async def get(self, key: str) -> str | None:
        return "cihaz-token" if key == "canteen.device_token" else None

    async def set(self, key: str, value: str) -> None:  # pragma: no cover
        raise AssertionError("kayıt yapılmamalı")


class _Log:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, message: str, **_k: Any) -> None:
        self.lines.append(message)

    def warning(self, message: str, **_k: Any) -> None:
        self.lines.append(message)


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> CanteenApi:
    return CanteenApi(base_url="https://kantin.test", device_name="KM", secrets=_Secrets(),
                      log=_Log(), transport=httpx.MockTransport(handler))


# ============================================================ istemci


async def test_durum_patch_govdesi_ve_eski_kantin() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/students/STU-1/status":
            return httpx.Response(200, json={"data": {"id": "STU-1", "status": "inactive"}})
        return httpx.Response(404, json={"message": "The route could not be found."})

    api = _client(handler)
    await api.set_student_status("STU-1", "inactive", "left", "2026-10-12T12:00:00+03:00")
    assert seen[0].method == "PATCH"
    assert json.loads(seen[0].content) == {"status": "inactive", "reason": "left",
                                           "at": "2026-10-12T12:00:00+03:00"}
    assert api.recently_written("STU-1", "status", ["status"]) is True
    with pytest.raises(CanteenError) as error:
        await api.set_student_status("STU-2", "active")
    assert error.value.reason == "not_supported"
    with pytest.raises(CanteenError):
        await api.set_student_status("STU-1", "deleted")


async def test_silme_409_kodu_iletilir() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"reason": "balance_not_zero", "balance": 250,
                                         "message": "bakiye"})

    with pytest.raises(CanteenError) as error:
        await _client(handler).delete_student("STU-1")
    assert error.value.status == 409 and error.value.reason == "balance_not_zero"
    assert error.value.details == {"reason": "balance_not_zero", "balance": 250}


async def test_bakiye_uzlastirmasi_etag_304() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("If-None-Match") == '"991245"':
            return httpx.Response(304)
        assert request.url.params["ids"] == "STU-1,STU-2"
        return httpx.Response(200, headers={"ETag": '"991245"'}, json={
            "ledgerSeq": 991245,
            "items": [{"opaqueId": "STU-1", "balance": 4250, "ledgerSeq": 991200}]})

    api = _client(handler)
    page = await api.balances(["STU-1", "STU-2"])
    assert page == {"ledgerSeq": 991245, "etag": '"991245"',
                    "items": [{"opaqueId": "STU-1", "balance": 4250, "ledgerSeq": 991200}]}
    assert await api.balances(["STU-1", "STU-2"], etag='"991245"') is None


async def test_olay_cekme_sayfasi() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["after"] == "7" and request.url.params["limit"] == "500"
        return httpx.Response(200, json={"items": [{"id": 8, "type": "x"}], "lastId": 8,
                                         "hasMore": False})

    page = await _client(handler).events_after(7, 999)
    assert page == {"items": [{"id": 8, "type": "x"}], "lastId": 8, "hasMore": False}


async def test_yanki_penceresi_alanlari_birlestirir() -> None:
    api = _client(lambda _r: httpx.Response(200, json={}))
    api.remember_write("STU-1", "student", {"displayName": "A"})
    api.remember_write("STU-1", "student", {"parentPhone": "1"})
    assert api.recently_written("STU-1", "student", ["displayName", "parentPhone"])
    assert not api.recently_written("STU-1", "student", ["isBlocked"])
    assert not api.recently_written("STU-1", "student", ["displayName"], window=0.0)
    assert not api.recently_written("STU-2", "student", ["displayName"])


# ============================================================== alıcı


class Students:
    def __init__(self) -> None:
        self.upserts: list[tuple[str, list[str], bool, str | None]] = []
        self.invalidated = 0
        self.woken = 0

    async def student_upserted(self, opaque_id: str, fields: list[str], *, created: bool,
                               causation_id: str | None,
                               within: Callable[[], Awaitable[Any]] | None = None) -> str | None:
        if within is not None and await within() is False:
            return None
        self.upserts.append((opaque_id, fields, created, causation_id))
        return "student.created" if created else "student.updated"

    def invalidate(self) -> None:
        self.invalidated += 1

    def wake(self) -> None:
        self.woken += 1


class Echo:
    def __init__(self) -> None:
        self.recent: set[tuple[str, str]] = set()
        self.pages: list[dict[str, Any]] = []

    def recently_written(self, opaque_id: str, kind: str, fields: Any) -> bool:
        return (opaque_id, kind) in self.recent

    async def events_after(self, after: int, limit: int = 500) -> dict[str, Any]:
        return self.pages.pop(0)


Rig = tuple[CanteenEvents, EventOutbox, Students, Echo]


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[Rig]:
    raw = Store(tmp_path / "km.sqlite")
    await raw.open()
    await build_schema(raw)
    for path in sorted(MIGRATIONS.glob("*.sql")):
        await raw.apply_migration("bbd_canteen_api", path.name,
                                  path.read_text(encoding="utf-8"))
    outbox = EventOutbox(raw)
    await outbox.open(prune_loop=False)
    students, canteen = Students(), Echo()
    events = CanteenEvents(store=ScopedStore(raw, "bbd_canteen_api"), canteen=canteen,
                           log=_Log(), outbox=lambda: outbox, students=lambda: students,
                           coalesce_seconds=0.3)
    yield events, outbox, students, canteen
    await events.close()
    await outbox.close()
    await raw.close()


def item(seq: int, kind: str, opaque: str = "STU-1", **payload: Any) -> dict[str, Any]:
    return {"id": seq, "type": kind, "opaqueId": opaque,
            "payload": {"opaqueId": opaque, **payload},
            "createdAt": "2026-10-12T12:41:02+03:00"}


async def test_esleme_ve_tekillestirme(rig: Rig) -> None:
    events, outbox, students, _ = rig
    batch = [
        item(3, "payment_request.changed", ref="PR-01J", status="paid"),
        item(1, "student.upserted", fields=["displayName"], created=True),
        item(2, "student.controls_changed", fields=["dailyLimit", "isBlocked"]),
        item(4, "kiosk.rebooted"),
    ]
    result = await events.receive(batch)
    assert result == {"received": 4, "duplicates": 0, "lastId": 4}
    assert students.upserts == [("STU-1", ["displayName"], True, "CNT-1")]
    assert students.invalidated == 1 and students.woken == 1
    out = await outbox.read_after(0)
    assert [event["type"] for event in out] == ["canteen.controls_changed",
                                                "canteen.payment_request_changed"]
    assert out[0]["fields"] == ["dailyLimit", "isBlocked"] and out[0]["origin"] == "canteen"
    assert out[0]["causationId"] == "CNT-2"
    assert out[1]["data"] == {"ref": "PR-01J", "status": "PAID"}
    assert out[1]["fields"] == ["paymentRequests"]

    again = await events.receive(batch)
    assert again == {"received": 0, "duplicates": 4, "lastId": 4}
    assert len(students.upserts) == 1 and len(await outbox.read_after(0)) == 2


async def test_bakiye_ogrenci_basina_birlestirilir(rig: Rig) -> None:
    events, outbox, _, _ = rig
    await events.receive([
        item(10, "balance.changed", balance=1000, ledgerSeq=500, reason="sale"),
        item(11, "balance.changed", balance=1500, ledgerSeq=501, reason="sale"),
        item(12, "balance.changed", opaque="STU-2", balance=-200, ledgerSeq=502,
             reason="payment"),
        item(13, "balance.changed", balance=0, ledgerSeq=503, reason="collection"),
    ])
    assert await outbox.read_after(0) == []         # pencere dolmadı
    await asyncio.sleep(0.7)
    out = {event["subject"]["id"]: event for event in await outbox.read_after(0)}
    assert set(out) == {"STU-1", "STU-2"}
    assert out["STU-1"]["data"] == {"balance": 0, "ledgerSeq": 503,
                                    "reasons": ["collection", "sale"],
                                    "at": "2026-10-12T12:41:02+03:00"}
    assert out["STU-1"]["causationId"] == "CNT-13" and out["STU-1"]["fields"] == ["balance"]


async def test_kapanista_tampon_bosalir(rig: Rig) -> None:
    events, outbox, _, _ = rig
    events._coalesce = 60.0
    await events.receive([item(20, "balance.changed", balance=1, ledgerSeq=1, reason="sale")])
    await events.close()
    assert [event["type"] for event in await outbox.read_after(0)] == ["canteen.balance_changed"]


async def test_yanki_yeni_olay_uretmez(rig: Rig) -> None:
    events, outbox, students, canteen = rig
    canteen.recent = {("STU-1", "controls"), ("STU-1", "student")}
    result = await events.receive([
        item(30, "student.controls_changed", fields=["isBlocked"]),
        item(31, "student.upserted", fields=["displayName"], created=False),
    ])
    assert result["received"] == 2
    assert await outbox.read_after(0) == [] and students.upserts == []


async def test_cekme_sayfa_sayfa_ve_imlec(rig: Rig) -> None:
    events, outbox, _, canteen = rig
    canteen.pages = [
        {"items": [item(1, "student.controls_changed", fields=["dailyLimit"])],
         "lastId": 1, "hasMore": True},
        {"items": [item(2, "payment_request.changed", ref="PR-1", status="EXPIRED")],
         "lastId": 2, "hasMore": False},
    ]
    assert await events.pull_once() == 2
    assert await events.last_seq() == 2
    assert len(await outbox.read_after(0)) == 2
