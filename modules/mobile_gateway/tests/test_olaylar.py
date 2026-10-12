"""Merkezi senkron — geçit §9 uçları, yazmalarda olay ve KM → Akademi webhook iletimi.

Sözleşme: bbd-akademi `docs/integrations/km-merkezi-senkron.md` §3.2, §3.3, §8;
`km-mobile-gateway.md` §9–10; API `webhooks_km` (KmEventBatch, kmSignature).
Outbox GERÇEKTİR (aynı depo, çekirdek şeması); Akademi sahte bir alıcıdır
(`httpx.MockTransport`) ve imzayı sözleşmedeki gibi doğrular. Ağa çıkılmaz.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import time
from collections.abc import AsyncIterator
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from conftest import World
from mobile_gateway_fakes import CAN, ELIF, MOTHER, FakeDirectory
from modules.mobile_gateway.backend.events import EventSender, GatewayEvents, coalesce
from modules.mobile_gateway.backend.service import GatewayService

from km_core.store.bootstrap import build_schema
from km_platform.events.outbox import EventOutbox

SECRET = "wh-sır-0123456789"
TENANT = "bbd"
BASE = "https://akademi.test"
ENVELOPE = {"eventId", "seq", "type", "occurredAt", "subject", "revision", "fields", "data",
            "origin", "causationId"}


@pytest.fixture
async def outbox(store: Any, world: World) -> AsyncIterator[EventOutbox]:
    await build_schema(store.database)
    box = EventOutbox(store.database)
    await box.open(prune_loop=False)
    world.replace("events.outbox", box)
    yield box
    await box.close()


class Akademi:
    """Sahte alıcı: imzayı doğrular, paketleri kaydeder, yanıtı sıradan verir."""

    def __init__(self, secret: str = SECRET) -> None:
        self.secret = secret
        self.batches: list[dict[str, Any]] = []
        self.raw: list[bytes] = []
        self.times: list[float] = []
        self.responses: list[Any] = []
        self.reject_types: set[str] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url == f"{BASE}/v1/webhooks/km"
        assert request.headers["X-KM-Tenant"] == TENANT
        match = re.fullmatch(r"t=(\d+),v1=([0-9a-f]{64})", request.headers["X-KM-Signature"])
        assert match, request.headers["X-KM-Signature"]
        body = request.content
        expected = hmac.new(self.secret.encode(), f"{match.group(1)}.".encode() + body,
                            hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, match.group(2)):
            return httpx.Response(401, json={"error": {"code": "invalid_signature"}})
        batch = json.loads(body)
        self.batches.append(batch)
        self.raw.append(body)
        self.times.append(time.perf_counter())
        if self.responses:
            answer = self.responses.pop(0)
            if isinstance(answer, httpx.Response):
                return answer
            return httpx.Response(int(answer), json={"error": {"code": f"e{answer}"}})
        if len(batch["events"]) > 1 and any(item["type"] in self.reject_types
                                            for item in batch["events"]):
            return httpx.Response(422, json={"error": {"code": "validation_error"}})
        if any(item["type"] in self.reject_types for item in batch["events"]):
            return httpx.Response(422, json={"error": {"code": "validation_error"}})
        return httpx.Response(200, json={"received": len(batch["events"]), "duplicates": 0,
                                         "lastSeq": batch["events"][-1]["seq"]})


class Log:
    def __init__(self) -> None:
        self.lines: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **fields: Any) -> None:
        self.lines.append((event, fields))

    warning = error = info


def sender(store: Any, world: World, akademi: Akademi, *, enabled: bool = True,
           window: float = 0.0, sleeps: list[float] | None = None) -> EventSender:
    record = sleeps if sleeps is not None else []

    async def fake_sleep(seconds: float) -> None:
        record.append(seconds)

    return EventSender(
        store=store, outbox=lambda: world.registry.try_resolve("events.outbox"),
        secrets=lambda: world.vault,
        config={"events_enabled": enabled, "akademi_base_url": BASE,
                "akademi_tenant": TENANT, "events_instance_id": "km-test"},
        log=Log(), settings=lambda: {"institutionName": "BBD", "features": {"canteen": True}},
        transport=httpx.MockTransport(akademi), sleep=fake_sleep, rand=lambda: 0.5,
        window=window)


async def emit_student(box: EventOutbox, kind: str, student: str = ELIF, **kw: Any) -> None:
    await box.emit(kind, ("student", student), **kw)


# ============================================================ birleştirme


def test_birlestirme_kurallari() -> None:
    def event(seq: int, kind: str, sid: str = ELIF, fields: list[str] | None = None,
              data: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"seq": seq, "type": kind, "subject": {"kind": "student", "id": sid},
                "fields": fields or [], "data": data}

    out = coalesce([
        event(1, "student.updated", fields=["className"]),
        event(2, "student.deactivated", fields=["status"]),
        event(3, "student.updated", fields=["gradeLevel"]),
        event(4, "canteen.balance_changed", data={"balance": 1, "ledgerSeq": 10,
                                                   "reasons": ["sale"]}),
        event(5, "canteen.balance_changed", data={"balance": 2, "ledgerSeq": 9,
                                                   "reasons": ["payment"]}),
        event(6, "student.deactivated", fields=["status"]),
        event(7, "student.updated", sid=CAN, fields=[]),
        event(8, "student.updated", sid=CAN, fields=["className"]),
    ])
    assert [item["seq"] for item in out] == [2, 3, 5, 6, 8]
    assert out[1]["fields"] == ["className", "gradeLevel"]
    assert out[2]["data"] == {"balance": 1, "ledgerSeq": 10, "reasons": ["payment", "sale"]}
    assert out[4]["fields"] == []   # biri "hepsini yenile" dediyse birleşim de öyle


# ================================================================ iletim


async def test_kapaliyken_hic_istek_yok(store: Any, world: World,
                                        outbox: EventOutbox) -> None:
    akademi = Akademi()
    world.vault.values["mobile_gateway.webhook_secret"] = SECRET
    events = sender(store, world, akademi, enabled=False)
    await outbox.register_consumer("akademi", start=0)
    await events.start(loop=False)
    await emit_student(outbox, "student.updated", fields=["className"])
    await asyncio.sleep(0.05)
    assert akademi.batches == []
    assert "akademi" not in await outbox.consumers()   # budamayı bekletmez
    status = await GatewayEvents(events).status()
    assert status["state"] == "stopped" and status["enabled"] is False
    assert status["lastError"] == "events_disabled"


async def test_paket_imzali_birlesik_ve_sirali(store: Any, world: World,
                                               outbox: EventOutbox) -> None:
    akademi = Akademi()
    world.vault.values["mobile_gateway.webhook_secret"] = SECRET
    events = sender(store, world, akademi)
    await events.start(loop=False)
    await emit_student(outbox, "student.updated", fields=["className"], revision=5)
    await emit_student(outbox, "student.updated", fields=["gradeLevel"], revision=6)
    await emit_student(outbox, "student.deactivated", fields=["status"],
                       data={"status": "inactive", "reason": "left",
                             "effectiveAt": "2026-10-12T12:00:00+03:00"})
    await emit_student(outbox, "canteen.balance_changed", student=CAN, fields=["balance"],
                       data={"balance": 100, "ledgerSeq": 7, "reasons": ["sale"], "at": "x"},
                       origin="canteen", causation_id="CNT-1")
    assert await events.run_once(wait=0) == 0.0
    [batch] = akademi.batches
    assert set(batch) == {"source", "sentAt", "events"}
    assert batch["source"] == {"system": "km", "instanceId": "km-test"}
    assert [item["type"] for item in batch["events"]] == [
        "student.updated", "student.deactivated", "canteen.balance_changed"]
    assert all(set(item) == ENVELOPE for item in batch["events"])
    assert batch["events"][0]["fields"] == ["className", "gradeLevel"]
    assert batch["events"][0]["revision"] == 6
    seqs = [item["seq"] for item in batch["events"]]
    assert seqs == sorted(seqs)
    status = await GatewayEvents(events).status()
    assert status["deliveredSeq"] == 4 and status["lastSeq"] == 4
    assert status["state"] == "live" and status["lagSeconds"] == 0
    assert (await outbox.consumers())["akademi"] == 4
    # Kişisel veri yok (A6).
    text = akademi.raw[0].decode()
    for needle in ("Elif", "Kaya", MOTHER, "483920"):
        assert needle not in text
    # Yeni olay yokken istek atılmaz.
    assert await events.run_once(wait=0) == 0.0
    assert len(akademi.batches) == 1
    await events.stop()


async def test_401_durur_ve_imlec_ilerlemez(store: Any, world: World,
                                            outbox: EventOutbox) -> None:
    akademi = Akademi(secret="başka-sır")
    world.vault.values["mobile_gateway.webhook_secret"] = SECRET
    events = sender(store, world, akademi)
    await events.start(loop=False)
    await emit_student(outbox, "student.updated", fields=["className"])
    assert await events.run_once(wait=0) == 300.0
    status = await GatewayEvents(events).status()
    assert status["state"] == "stopped" and status["deliveredSeq"] == 0
    assert "401" in status["lastError"]
    # Durmuşken yoklama zamanı gelene kadar istek yok.
    assert await events.run_once(wait=0) > 0
    assert len(akademi.batches) == 0
    await events.stop()


async def test_sir_yoksa_durur(store: Any, world: World, outbox: EventOutbox) -> None:
    akademi = Akademi()
    events = sender(store, world, akademi)
    await events.start(loop=False)
    await emit_student(outbox, "student.updated", fields=["className"])
    assert await events.run_once(wait=0) == 300.0
    assert (await events.status())["lastError"] == "webhook_secret_missing"
    assert akademi.batches == []
    await events.stop()


async def test_422_tek_tek_ve_olu_mektup(store: Any, world: World,
                                         outbox: EventOutbox) -> None:
    akademi = Akademi()
    akademi.reject_types = {"guardian.linked"}
    world.vault.values["mobile_gateway.webhook_secret"] = SECRET
    events = sender(store, world, akademi)
    await events.start(loop=False)
    await emit_student(outbox, "student.updated", fields=["className"])
    await emit_student(outbox, "guardian.linked", fields=["guardians"], data={"slot": 1})
    await emit_student(outbox, "student.created", student=CAN)
    assert await events.run_once(wait=0) == 0.0
    assert [len(batch["events"]) for batch in akademi.batches] == [3, 1, 1, 1]
    capability = GatewayEvents(events)
    status = await capability.status()
    assert status["deliveredSeq"] == 3 and status["deadCount"] == 1
    [dead] = await capability.dead()
    assert dead["type"] == "guardian.linked" and dead["seq"] == 2
    assert dead["errorCode"] == "validation_error"
    akademi.reject_types = set()
    assert await capability.retry_dead([2]) == {"delivered": 1, "failed": 0}
    assert (await capability.status())["deadCount"] == 0
    await events.stop()


async def test_gecici_hatada_ustel_geri_cekilme(store: Any, world: World,
                                                outbox: EventOutbox) -> None:
    akademi = Akademi()
    akademi.responses = [503, 503, 503,
                         httpx.Response(429, headers={"Retry-After": "7"}), 200]
    world.vault.values["mobile_gateway.webhook_secret"] = SECRET
    events = sender(store, world, akademi)
    await events.start(loop=False)
    await emit_student(outbox, "student.updated", fields=["className"])
    delays = [await events.run_once(wait=0) for _ in range(4)]
    # rand = 0,5 → sarsıntı sıfır: 1, 2, 4 sn; Retry-After 7 sn'ye uyulur.
    assert delays == [1.0, 2.0, 4.0, 7.0]
    status = await events.status()
    assert status["state"] == "retrying" and status["deliveredSeq"] == 0
    assert await events.run_once(wait=0) == 0.0
    assert (await events.status())["state"] == "live"
    assert len({batch["events"][0]["eventId"] for batch in akademi.batches}) == 1
    await events.stop()


async def test_hiz_siniri_saniyede_en_cok_on(store: Any, world: World,
                                             outbox: EventOutbox) -> None:
    sleeps: list[float] = []
    akademi = Akademi()
    world.vault.values["mobile_gateway.webhook_secret"] = SECRET
    events = sender(store, world, akademi, sleeps=sleeps)
    await events.start(loop=False)
    for index in range(3):
        await emit_student(outbox, "student.created", student=f"STU-{index}")
        await events.run_once(wait=0)
    # İkinci ve üçüncü istekten önce 100 ms'ye tamamlayan bekleme.
    assert len([value for value in sleeps if 0 < value <= 0.1]) == 2
    await events.stop()


async def test_eski_imlec_tam_uzlastirma_ister(store: Any, world: World,
                                               outbox: EventOutbox) -> None:
    akademi = Akademi()
    world.vault.values["mobile_gateway.webhook_secret"] = SECRET
    events = sender(store, world, akademi)
    await events.start(loop=False)
    await emit_student(outbox, "student.updated", fields=["className"])
    # Budama imlecin önüne geçti (Akademi uzun süre kapalıydı).
    await outbox.unregister_consumer("akademi")
    outbox._clock = lambda: datetime(2026, 12, 1, tzinfo=UTC)
    await outbox.emit("student.updated", ("student", CAN))
    await outbox.prune()
    await events.run_once(wait=0)
    await events.run_once(wait=0)
    sent = [item["type"] for batch in akademi.batches for item in batch["events"]]
    assert sent == ["directory.resync_required"]
    assert akademi.batches[0]["events"][0]["data"] == {"scope": "all"}
    await events.stop()


async def test_yeniden_oynatma(store: Any, world: World, outbox: EventOutbox) -> None:
    akademi = Akademi()
    world.vault.values["mobile_gateway.webhook_secret"] = SECRET
    events = sender(store, world, akademi)
    await events.start(loop=False)
    await emit_student(outbox, "student.updated", fields=["className"])
    await events.run_once(wait=0)
    assert await GatewayEvents(events).replay_from(1) == {"replayed": True, "deliveredSeq": 0}
    await events.run_once(wait=0)
    assert [batch["events"][0]["seq"] for batch in akademi.batches] == [1, 1]
    await events.stop()


async def test_kurum_ayari_degisince_olay(store: Any, world: World,
                                          outbox: EventOutbox) -> None:
    settings = {"institutionName": "BBD", "features": {"canteen": True}}
    events = EventSender(store=store, outbox=lambda: outbox, secrets=lambda: None,
                         config={}, log=Log(), settings=lambda: dict(settings))
    assert await events.announce_settings() == []          # ilk açılış: yalnız kayıt
    assert await events.announce_settings() == []
    settings["features"] = {"canteen": False}
    assert await events.announce_settings() == ["features"]
    [event] = await outbox.read_after(0)
    assert event["type"] == "institution.settings_changed"
    assert event["subject"] == {"kind": "institution", "id": None}
    assert event["fields"] == ["features"]


async def test_commit_ile_istek_arasi_300_ms_alti(store: Any, world: World,
                                                 outbox: EventOutbox) -> None:
    """Bütçe (§9): commit → webhook isteği ≤ 300 ms (200 ms pencere dahil)."""
    akademi = Akademi()
    world.vault.values["mobile_gateway.webhook_secret"] = SECRET
    events = EventSender(
        store=store, outbox=lambda: outbox, secrets=lambda: world.vault,
        config={"events_enabled": True, "akademi_base_url": BASE, "akademi_tenant": TENANT},
        log=Log(), transport=httpx.MockTransport(akademi))
    await events.start()
    await asyncio.sleep(0.05)
    lags = []
    for index in range(5):
        started = time.perf_counter()
        await outbox.emit("student.updated", ("student", f"STU-{index}"))
        for _ in range(100):
            if len(akademi.times) > index:
                break
            await asyncio.sleep(0.01)
        lags.append((akademi.times[index] - started) * 1000)
        await asyncio.sleep(0.15)
    print(f"\n[olay iletimi] commit → istek: {[round(value) for value in lags]} ms")
    assert max(lags) <= 300
    await events.stop()


# ======================================================= §9 uçları


async def test_tekil_ogrenci_ve_404(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/students/{ELIF}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == ELIF and body["canteen"]["balance"] == 4250
    assert {"status", "statusReason", "statusSince", "revision", "canteenSync"} <= set(body)
    no_balance = (await client.get(f"/students/{ELIF}?balance=false")).json()
    assert "balance" not in no_balance["canteen"]
    error = (await client.get("/students/STU-YOK")).json()["error"]
    assert error["code"] == "student_not_found"


async def test_dizin_ids_balance_false_ve_since_siniri(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/directory?ids={ELIF},STU-YOK,{ELIF}")
    assert response.status_code == 200
    body = response.json()
    assert body["full"] is False and body["classes"] == []
    assert [item["id"] for item in body["students"]] == [ELIF]
    assert body["missingIds"] == ["STU-YOK"] and "etag" not in response.headers
    many = ",".join(f"STU-{index}" for index in range(101))
    assert (await client.get(f"/directory?ids={many}")).status_code == 422

    full = await client.get("/directory")
    lean = await client.get("/directory?balance=false")
    assert full.headers["etag"] != lean.headers["etag"]
    assert all("balance" not in (item["canteen"] or {}) for item in lean.json()["students"])
    again = await client.get("/directory?balance=false",
                             headers={"If-None-Match": lean.headers["etag"]})
    assert again.status_code == 304

    old = await client.get("/directory?since=2026-06-01T00:00:00%2B03:00")
    assert old.status_code == 422 and old.json()["error"]["fields"] == ["since"]
    recent = (await client.get("/directory?since=2026-10-11T00:00:00%2B03:00")).json()
    assert recent["removed"] == [] and recent["full"] is False


async def test_dizin_since_silinenleri_doner(client: httpx.AsyncClient, world: World) -> None:
    class Removing(FakeDirectory):
        async def list(self, since: Any = None) -> dict[str, Any]:
            result = await super().list(since)
            if since is not None:
                result["removed"] = [{"id": "STU-SILINDI", "at": "2026-10-12T06:00:00+00:00"}]
            return result

    world.replace("bbd_students.directory", Removing())
    body = (await client.get("/directory?since=2026-10-11T00:00:00%2B03:00")).json()
    assert body["removed"] == [{"id": "STU-SILINDI", "at": "2026-10-12T09:00:00+03:00"}]


async def test_bakiye_uzlastirmasi(client: httpx.AsyncClient, world: World) -> None:
    calls: list[Any] = []

    async def balances(ids: Any = None, *, etag: str | None = None) -> Any:
        calls.append((ids, etag))
        if etag == '"991245"':
            return None
        return {"ledgerSeq": 991245, "etag": '"991245"',
                "items": [{"opaqueId": ELIF, "balance": 4250, "ledgerSeq": 991200}]}

    world.canteen.balances = balances  # type: ignore[attr-defined]
    response = await client.get(f"/canteen/balances?ids={ELIF}")
    assert response.status_code == 200 and response.headers["etag"] == '"991245"'
    assert response.json() == {"ledgerSeq": 991245, "items": [
        {"studentId": ELIF, "balance": 4250, "ledgerSeq": 991200}]}
    cached = await client.get(f"/canteen/balances?ids={ELIF}",
                              headers={"If-None-Match": '"991245"'})
    assert cached.status_code == 304
    assert calls == [([ELIF], None), ([ELIF], '"991245"')]

    async def unsupported(ids: Any = None, *, etag: str | None = None) -> Any:
        error = RuntimeError("404")
        error.status = 404  # type: ignore[attr-defined]
        error.reason = "not_supported"  # type: ignore[attr-defined]
        raise error

    world.canteen.balances = unsupported  # type: ignore[attr-defined]
    error = (await client.get("/canteen/balances")).json()["error"]
    assert error["code"] == "capability_missing"


async def test_olay_cekme_ve_410(client: httpx.AsyncClient, world: World,
                                 outbox: EventOutbox) -> None:
    for index in range(3):
        await outbox.emit("student.updated", ("student", f"STU-{index}"))
    body = (await client.get("/events?after=0&limit=2")).json()
    assert [item["seq"] for item in body["items"]] == [1, 2]
    assert body["lastSeq"] == 2 and body["hasMore"] is True
    assert set(body["items"][0]) == ENVELOPE
    tail = (await client.get("/events?after=2")).json()
    assert tail["lastSeq"] == 3 and tail["hasMore"] is False
    await outbox._store.execute(  # budama izi: 2'ye kadar silindi
        "INSERT OR REPLACE INTO km_event_consumer (name, last_seq, updated_at) "
        "VALUES ('#pruned', 2, 'x')")
    error = (await client.get("/events?after=1")).json()["error"]
    assert error["code"] == "events_expired"
    world.remove("events.outbox")
    assert (await client.get("/events?after=0")).json()["error"]["code"] == "capability_missing"


async def test_yazmalar_akademi_kokenli_olay_yazar(client: httpx.AsyncClient, world: World,
                                                   outbox: EventOutbox) -> None:
    remembered: list[tuple[Any, ...]] = []
    world.canteen.remember_write = lambda *args: remembered.append(args)  # type: ignore
    key = {"Idempotency-Key": "idem-77"}
    assert (await client.patch(f"/students/{ELIF}/canteen/controls", json={"dailyLimit": 5000,
                               "isBlocked": True}, headers=key)).status_code == 200
    assert (await client.post(f"/students/{ELIF}/payment-requests", json={},
                              headers={"Idempotency-Key": "idem-78"})).status_code == 201
    assert (await client.post(f"/students/{ELIF}/tablet/block", json={"reason": "ödev"},
                              headers={"X-Request-Id": "req-9"})).status_code == 200
    assert (await client.post("/tablet-sessions/s-1/end", json={"reason": "bitti"},
                              headers={"Idempotency-Key": "idem-79"})).status_code == 200
    events = await outbox.read_after(0)
    assert [(item["type"], item["origin"], item["causationId"]) for item in events] == [
        ("canteen.controls_changed", "akademi", "idem-77"),
        ("canteen.payment_request_changed", "akademi", "idem-78"),
        ("tablet.policy_changed", "akademi", "req-9"),
        ("tablet.policy_changed", "akademi", "idem-79"),
    ]
    assert events[0]["fields"] == ["dailyLimit", "isBlocked"]
    assert events[1]["data"] == {"ref": "PR-01JNEW", "status": "PENDING"}
    assert events[3]["subject"] == {"kind": "student", "id": None}   # eski tablet yeteneği
    assert remembered == [(ELIF, "payment_request", ["PR-01JNEW"])]

    # Yanıt biçimi değişmedi; olay yazılamasa da yazma başarılıdır (K7).
    world.remove("events.outbox")
    again = await client.patch(f"/students/{CAN}/canteen/controls", json={"dailyLimit": 100})
    assert again.status_code == 200 and set(again.json()) >= {"dailyLimit", "balance"}


async def test_dizin_kaydi_kopyalanmaz(service: GatewayService) -> None:
    record = deepcopy(next(iter((await FakeDirectory().list())["students"])))
    record.update({"status": "inactive", "statusReason": "graduated",
                   "statusSince": "2026-10-12T06:00:00+00:00", "revision": 57,
                   "canteenSync": "pending"})
    out = service.student(record)
    assert (out["status"], out["statusReason"], out["revision"], out["canteenSync"]) == \
        ("inactive", "graduated", 57, "pending")
    assert out["statusSince"] == "2026-10-12T09:00:00+03:00"
    record["status"] = "weird"
    assert service.student(record)["status"] == "active"
