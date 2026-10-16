"""Merkezi senkron (km-merkezi-senkron §5.3, §8) — panel tarafı.

* `akademi.student_finance` yeteneği: kalıcı silme ön koşulu "kurum cari
  borcu yok". Bilinmeyen durum ASLA "borç yok" sayılmaz.
* Senkron kartının arka ucu: `GET /km/events`, `POST /km/events/retry` —
  geçidin `mobile_gateway.events` yeteneği isteğe bağlı, kapısı geçidin izni.
* Kartın hüküm mantığı (`ui/panel/sync-logic.js`) — Node ile.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from modules.bbd_akademi.backend.api import routes
from modules.bbd_akademi.backend.finance import FinanceCheckFailed, StudentFinance
from modules.bbd_akademi.backend.gateway import AkademiGateway, AkademiUnavailable
from modules.bbd_akademi.tests.akademi_fakes import ADMIN, Akademi, Identity, Log, auth, user

STUDENT = "6f1f2c8e-3c1d-4f7b-9a59-0d3f4b1c2a10"
PANEL = Path(__file__).resolve().parents[1] / "ui" / "panel"


def _page(*items: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"items": list(items), "nextCursor": None})


def _summary(open_minor: int) -> httpx.Response:
    return httpx.Response(200, json={
        "openBalanceMinor": open_minor, "overdueMinor": 0, "dueNext30DaysMinor": 0,
        "creditMinor": 0, "nextDue": None, "lastPayment": None, "plans": [],
        "cardPaymentEnabled": False})


def _error(status: int, code: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message": code,
                                                  "details": None, "traceId": "t"}})


def _router(table: dict[str, Any]) -> Any:
    """Yol (+ status süzgeci) → yanıt. Tanımsız yol testi patlatır."""

    def reply(request: httpx.Request) -> httpx.Response:
        query = parse_qs(request.url.query.decode())
        key = request.url.path
        if key == "/v1/admin/students":
            key = f"{key}?status={query.get('status', [''])[0]}"
        value = table[key]
        return value(request) if callable(value) else value

    return reply


@pytest.fixture
def finance(gateway: AkademiGateway, log: Log) -> StudentFinance:
    return StudentFinance(gateway=gateway, log=log)


# ------------------------------------------------- akademi.student_finance


async def test_borc_varsa_true_yoksa_false(finance: StudentFinance, akademi: Akademi) -> None:
    table: dict[str, Any] = {
        "/v1/admin/students?status=inactive": _page({"id": STUDENT, "externalRef": "STU-42"}),
        f"/v1/students/{STUDENT}/finance": _summary(125000),
    }
    akademi.reply(_router(table))
    assert await finance.has_institution_debt("STU-42") is True

    first = akademi.calls[0]
    query = parse_qs(first.url.query.decode())
    assert query == {"externalRef": ["STU-42"], "status": ["inactive"], "limit": ["5"]}
    assert first.method == "GET"
    assert first.headers["authorization"].startswith("Bearer aksk_")
    assert first.headers["x-akademi-on-behalf-of"].startswith("km:system:bbd_students:")

    table[f"/v1/students/{STUDENT}/finance"] = _summary(0)
    assert await finance.has_institution_debt("STU-42") is False


async def test_pasif_bulunamazsa_suzgecsiz_aranir(finance: StudentFinance,
                                                  akademi: Akademi) -> None:
    # Akademi pasifleştirme olayını henüz uygulamamış: öğrenci "active" görünür.
    akademi.reply(_router({
        "/v1/admin/students?status=inactive": _page(),
        "/v1/admin/students?status=": _page(
            {"id": "baska", "externalRef": "STU-420"},   # tam eşleşme değil — atlanır
            {"id": STUDENT, "externalRef": "STU-42"}),
        f"/v1/students/{STUDENT}/finance": _summary(1),
    }))
    assert await finance.has_institution_debt("STU-42") is True
    assert [call.url.path for call in akademi.calls] == [
        "/v1/admin/students", "/v1/admin/students", f"/v1/students/{STUDENT}/finance"]


async def test_akademide_hic_yoksa_borc_yok(finance: StudentFinance, akademi: Akademi) -> None:
    akademi.reply(_router({"/v1/admin/students?status=inactive": _page(),
                           "/v1/admin/students?status=": _page()}))
    assert await finance.has_institution_debt("STU-9") is False
    assert len(akademi.calls) == 2


async def test_ozet_404_borc_yok(finance: StudentFinance, akademi: Akademi) -> None:
    akademi.reply(_router({
        "/v1/admin/students?status=inactive": _page({"id": STUDENT, "externalRef": "STU-1"}),
        f"/v1/students/{STUDENT}/finance": _error(404, "student_not_found"),
    }))
    assert await finance.has_institution_debt("STU-1") is False


async def test_ozellik_kapaliysa_cari_hesaptan_okunur(finance: StudentFinance,
                                                      akademi: Akademi) -> None:
    table: dict[str, Any] = {
        "/v1/admin/students?status=inactive": _page({"id": STUDENT, "externalRef": "STU-1"}),
        f"/v1/students/{STUDENT}/finance": _error(403, "feature_disabled"),
        f"/v1/admin/finance/accounts/{STUDENT}": httpx.Response(200, json={
            "student": {"id": STUDENT}, "summary": json.loads(_summary(5000).content),
            "plans": [], "openCharges": []}),
    }
    akademi.reply(_router(table))
    assert await finance.has_institution_debt("STU-1") is True
    # Kurum cari modülü de kapalı → kurumda cari yok → borç yok.
    table[f"/v1/admin/finance/accounts/{STUDENT}"] = _error(403, "feature_disabled")
    assert await finance.has_institution_debt("STU-1") is False


@pytest.mark.parametrize("response", [
    _error(500, "internal_error"),
    _error(403, "forbidden"),
    _error(401, "invalid_integration_key"),
    httpx.Response(200, json={"openBalanceMinor": "yok"}),
])
async def test_belirsiz_yanit_borc_yok_demez(finance: StudentFinance, akademi: Akademi,
                                             response: httpx.Response) -> None:
    akademi.reply(_router({
        "/v1/admin/students?status=inactive": _page({"id": STUDENT, "externalRef": "STU-1"}),
        f"/v1/students/{STUDENT}/finance": response,
    }))
    with pytest.raises(FinanceCheckFailed):
        await finance.has_institution_debt("STU-1")


async def test_arama_hatasi_ve_erisilemezlik(finance: StudentFinance, akademi: Akademi) -> None:
    akademi.reply(_error(422, "validation_error"))
    with pytest.raises(FinanceCheckFailed):
        await finance.has_institution_debt("STU-1")
    akademi.down = httpx.ConnectError("yok")
    with pytest.raises(AkademiUnavailable):
        await finance.has_institution_debt("STU-1")
    with pytest.raises(FinanceCheckFailed):
        await finance.has_institution_debt("  ")


# ------------------------------------------------------- Senkron kartı (API)


class FakeEvents:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.retried: list[list[int]] = []

    async def status(self) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError("geçit patladı")
        return {"enabled": True, "lastSeq": 120, "deliveredSeq": 118, "lagSeconds": 0.4,
                "lastSuccessAt": "2026-10-13T08:00:00+00:00", "lastError": None,
                "deadCount": 1, "state": "live", "unreachable": False,
                "deliveryLagMs": 180, "tenant": "bbd"}

    async def dead(self, limit: int = 100) -> list[dict[str, Any]]:
        return [{"seq": 7, "eventId": "01J", "type": "student.updated",
                 "errorCode": "validation_error", "attempts": 3,
                 "lastAttemptAt": "2026-10-13T07:00:00+00:00"}][:limit]

    async def retry_dead(self, ids: list[int]) -> dict[str, int]:
        self.retried.append(list(ids))
        return {"delivered": len(ids), "failed": 0}


VIEWER_EVENTS = user("u-7", "Gül", "Su", "bbd_akademi.view", "mobile_gateway.events.view")


@pytest.fixture
def events_holder(client: httpx.AsyncClient, identity: Identity) -> Iterator[dict[str, Any]]:
    holder: dict[str, Any] = {"cap": None}
    routes._events = lambda: holder["cap"]
    identity.extra["gw-viewer"] = VIEWER_EVENTS
    yield holder
    routes._events = None


async def test_gecit_yoksa_desteklenmiyor(client: httpx.AsyncClient,
                                          events_holder: dict[str, Any]) -> None:
    response = await client.get("/api/bbd_akademi/km/events", headers=auth())
    assert response.status_code == 200
    assert response.json() == {"supported": False, "canView": False, "canManage": False,
                               "status": None, "dead": []}
    retry = await client.post("/api/bbd_akademi/km/events/retry", headers=auth(),
                              json={"seqs": [7]})
    assert retry.status_code == 503
    assert retry.json()["error"]["code"] == "gateway_events_unsupported"
    status = await client.get("/api/bbd_akademi/km/status", headers=auth())
    assert status.json()["gatewayEvents"]["supported"] is False


async def test_izin_gecidin_izni(client: httpx.AsyncClient, identity: Identity,
                                 events_holder: dict[str, Any]) -> None:
    fake = FakeEvents()
    events_holder["cap"] = fake
    # Akademi yöneticisi (bütün bbd_akademi izinleri) geçidin iznini TAŞIMIYOR:
    # durum verisi dönmez.
    assert "mobile_gateway.events.view" not in ADMIN.permissions
    blind = await client.get("/api/bbd_akademi/km/events", headers=auth())
    assert blind.json()["supported"] is True and blind.json()["status"] is None

    seen = await client.get("/api/bbd_akademi/km/events", headers=auth("gw-viewer"))
    body = seen.json()
    assert body["canView"] is True and body["canManage"] is False
    assert body["status"]["lastSeq"] == 120 and body["dead"][0]["seq"] == 7
    assert "phone" not in json.dumps(body) and "displayName" not in json.dumps(body)

    denied = await client.post("/api/bbd_akademi/km/events/retry", headers=auth("gw-viewer"),
                               json={"seqs": [7]})
    assert denied.status_code == 403 and fake.retried == []

    identity.extra["gw-admin"] = user("u-8", "Ece", "Ak", "bbd_akademi.view",
                                      "mobile_gateway.events.view",
                                      "mobile_gateway.events.manage")
    done = await client.post("/api/bbd_akademi/km/events/retry", headers=auth("gw-admin"),
                             json={"seqs": [7, 9]})
    assert done.status_code == 200
    assert done.json()["result"] == {"delivered": 2, "failed": 0}
    assert fake.retried == [[7, 9]]
    empty = await client.post("/api/bbd_akademi/km/events/retry", headers=auth("gw-admin"),
                              json={"seqs": []})
    assert empty.status_code == 422


async def test_gecit_hatasi_paneli_dusurmez(client: httpx.AsyncClient,
                                            events_holder: dict[str, Any]) -> None:
    events_holder["cap"] = FakeEvents(fail=True)
    response = await client.get("/api/bbd_akademi/km/events", headers=auth("gw-viewer"))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "gateway_events_unavailable"


async def test_kayit_yetenegi_ilan_eder(store: Any) -> None:
    """`register` yeteneği sağlar ve geçidi her istekte yeniden çözer."""
    from modules.bbd_akademi.backend import module as module_entry

    provided: dict[str, Any] = {}
    asked: list[str] = []

    class Ctx:
        def __init__(self) -> None:
            self.config = {"base_url": "https://akademi.test", "staff_sync_minutes": 0}
            self.log = Log()
            self.routers: list[Any] = []
            self.store = store

        def capability(self, name: str) -> Any:
            return None

        def try_capability(self, name: str) -> Any:
            asked.append(name)
            return None

        def provide(self, name: str, instance: Any) -> None:
            provided[name] = instance

        def add_router(self, router: Any) -> None:
            self.routers.append(router)

    ctx = Ctx()
    try:
        module_entry.register(ctx)  # type: ignore[arg-type]
        assert isinstance(provided["akademi.student_finance"], StudentFinance)
        assert routes.gateway_events() is None
        assert asked == ["mobile_gateway.events"]
    finally:
        staff = module_entry._LIVE.get("staff")
        if staff is not None:
            await staff.stop()
        module_entry._LIVE.clear()
        routes._proxy = routes._staff = routes._events = None


# -------------------------------------------------------- kart hükmü (Node)

SCRIPT = r"""
import * as s from './sync-logic.mjs';
const live = { supported: true, canView: true, status: { enabled: true, state: 'live', lastSeq: 500,
  deliveredSeq: 500, lagSeconds: 0, deadCount: 0, unreachable: false } };
const ak = (extra) => ({ events: { mode: 'live', lastSeq: 500, pending: 0, lagSecondsP95: 1, ...extra } });
const gw = (extra) => ({ ...live, status: { ...live.status, ...extra } });
const v = (g, a) => { const r = s.syncVerdict(g, a); return { tone: r.tone, n: r.reasons.length }; };
const out = {
  good: v(live, ak({})),
  gap100: v(live, ak({ lastSeq: 400 })),
  gap101: v(live, ak({ lastSeq: 399 })),
  lag: v(gw({ lagSeconds: 61 }), ak({})),
  lag60: v(gw({ lagSeconds: 60 }), ak({})),
  stopped: v(gw({ state: 'stopped', lastError: 'invalid_signature' }), ak({})),
  unreachable: v(gw({ unreachable: true }), ak({})),
  dead: v(gw({ deadCount: 2 }), ak({})),
  off: v(gw({ enabled: false, state: 'stopped' }), ak({ mode: 'polling', lastSeq: 1 })),
  unsupported: v({ supported: false }, ak({ mode: 'polling' })),
  degraded: v(live, ak({ mode: 'degraded' })),
  akP95: v(live, ak({ lagSecondsP95: 90 })),
  noAk: v(live, null),
  stoppedText: s.syncVerdict(gw({ state: 'stopped', lastError: 'invalid_signature' }), null).reasons[0].text,
  gapNone: s.seqGap(live.status, { lastSeq: null }),
  lagTexts: [s.lagText(0.4), s.lagText(42), s.lagText(75), s.lagText(3700), s.lagText(null)],
  err: [s.errorText('events_disabled'), s.errorText('xyz'), s.errorText(null)],
};
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node kurulu değil")
def test_kart_hukmu(tmp_path: Path) -> None:
    (tmp_path / "sync-logic.mjs").write_text((PANEL / "admin/sync-logic.js").read_text(encoding="utf-8"),
                                             encoding="utf-8")
    (tmp_path / "run.mjs").write_text(SCRIPT, encoding="utf-8")
    result = subprocess.run(["node", str(tmp_path / "run.mjs")], capture_output=True,
                            text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["good"] == {"tone": "good", "n": 0}
    assert out["gap100"]["tone"] == "good" and out["gap101"]["tone"] == "warn"
    assert out["lag"]["tone"] == "warn" and out["lag60"]["tone"] == "good"
    assert out["stopped"]["tone"] == "bad" and out["unreachable"]["tone"] == "bad"
    assert out["dead"]["tone"] == "warn"
    # Kapalı iletim hata değildir: gri, yoklama ve fark uyarısı üretmez.
    assert out["off"] == {"tone": "dim", "n": 1}
    assert out["unsupported"] == {"tone": "dim", "n": 1}
    assert out["degraded"]["tone"] == "warn" and out["akP95"]["tone"] == "warn"
    assert out["noAk"]["tone"] == "good"
    assert "imza reddedildi" in out["stoppedText"]
    assert out["gapNone"] is None
    assert out["lagTexts"] == ["1 sn altı", "42 sn", "1 dk 15 sn", "1 sa 1 dk", "—"]
    assert out["err"] == ["iletim ayardan kapalı (events_disabled)", "xyz", "—"]
