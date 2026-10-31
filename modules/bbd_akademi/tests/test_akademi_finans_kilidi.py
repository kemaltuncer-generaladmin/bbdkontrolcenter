"""Kurum finansı kilidi (iş 83): SMS onay kodu → KM oturumu başına 30 dk kayan izin; kantin etkilenmez."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from modules.bbd_akademi.backend.allowlist import OPERATIONS
from modules.bbd_akademi.backend.api import routes
from modules.bbd_akademi.backend.core.gateway import AkademiGateway
from modules.bbd_akademi.backend.core.operations import Allowlist
from modules.bbd_akademi.backend.core.proxy import AkademiProxy
from modules.bbd_akademi.backend.core.stepup import StepUpStore
from modules.bbd_akademi.backend.finance.access import FinanceAccess, locked
from modules.bbd_akademi.backend.staff.service import StaffService
from modules.bbd_akademi.tests.akademi_fakes import (
    Akademi,
    CheckedStore,
    Identity,
    Log,
    auth,
    core_envelope,
)

CALL = "/api/bbd_akademi/km/call"
SUMMARY = "/v1/admin/finance/summary"
CANTEEN = "/v1/admin/canteen/settings"
CHALLENGE = "ch-0001-abcd"


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
async def locked_client(store: CheckedStore, gateway: AkademiGateway, log: Log, staff_service: StaffService,
                        identity: Identity, clock: Clock) -> AsyncIterator[httpx.AsyncClient]:
    access = FinanceAccess(gateway=gateway, log=log, clock=clock)
    proxy = AkademiProxy(gateway=gateway, allowlist=Allowlist(OPERATIONS),
                         step_up=StepUpStore(store, ttl_seconds=120), log=log, finance=access)
    app = FastAPI()
    core_envelope(app)
    app.state.identity = identity
    app.include_router(routes.bind(proxy, staff_service), prefix="/api/bbd_akademi")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://km.test") as http:
        yield http
    await gateway.close()
    routes._proxy = routes._staff = routes._finance = None


def _akademi_otp(akademi: Akademi, *, code: str = "123456") -> None:
    def reply(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/admin/finance-access/km/request":
            return httpx.Response(201, json={"challengeId": CHALLENGE, "expiresAt": "2027-01-15T08:05:00Z",
                                             "approvers": [{"name": "Finans Yöneticisi", "maskedPhone": "5** *** ** 12"}],
                                             "resendAfterSeconds": 60})
        if path == "/v1/admin/finance-access/km/verify":
            sent = json.loads(request.content)
            if sent["code"] != code:
                return httpx.Response(422, json={"error": {"code": "otp_invalid", "message": "Kod hatalı."}})
            return httpx.Response(200, json={"granted": True, "idleMinutes": 30})
        if path == "/v1/admin/dashboard":
            return httpx.Response(200, json={"students": 3, "institutionFinance": {"overdueMinor": 500}},
                                  headers={"etag": '"d1"'})
        return httpx.Response(200, json={"openMinor": 100})

    akademi.reply(reply)


async def _call(client: httpx.AsyncClient, path: str, who: str = "admin", method: str = "GET") -> dict:
    return (await client.post(CALL, headers=auth(who), json={"method": method, "path": path})).json()


async def _unlock(client: httpx.AsyncClient, who: str = "admin", code: str = "123456") -> httpx.Response:
    asked = await client.post("/api/bbd_akademi/km/finance-access/request", headers=auth(who))
    assert asked.status_code == 200, asked.text
    return await client.post("/api/bbd_akademi/km/finance-access/verify", headers=auth(who),
                             json={"challengeId": asked.json()["challengeId"], "code": code})


def test_kapsam_kurum_finansi_kantin_degil() -> None:
    by_id = {operation.operation_id: operation for operation in OPERATIONS}
    assert locked(by_id["admin_getFinanceSummary"])
    assert locked(by_id["admin_recordPayment"])
    assert locked(by_id["students_getFinanceSummary"])
    for operation in OPERATIONS:
        if "canteen" in operation.path or "/books" in operation.path or "contract" in operation.path:
            assert not locked(operation), operation.operation_id


async def test_izinsiz_finans_403_akademiye_gitmez(locked_client: httpx.AsyncClient, akademi: Akademi) -> None:
    _akademi_otp(akademi)
    data = await _call(locked_client, SUMMARY)
    assert data["ok"] is False
    assert data["error"]["code"] == "finance_access_required"
    assert data["status"] == 403
    raw = await locked_client.get(f"/api/bbd_akademi{SUMMARY}", headers=auth())
    assert raw.status_code == 403
    assert akademi.calls == []


async def test_kantin_ucu_kilitten_etkilenmez(locked_client: httpx.AsyncClient, akademi: Akademi) -> None:
    _akademi_otp(akademi)
    data = await _call(locked_client, CANTEEN)
    assert data["ok"] is True
    assert [call.url.path for call in akademi.calls] == [CANTEEN]


async def test_kod_dogrulaninca_acilir_30_dk_hareketsizlikte_kapanir(
        locked_client: httpx.AsyncClient, akademi: Akademi, clock: Clock) -> None:
    _akademi_otp(akademi)
    verified = await _unlock(locked_client)
    assert verified.status_code == 200, verified.text
    assert verified.json()["active"] is True
    sent = json.loads(akademi.calls[0].content)
    assert sent == {"actorName": "Ayşe Yılmaz", "actorRef": "km:user:u-1"}
    assert akademi.calls[0].headers["idempotency-key"].startswith("km-fin-")
    assert json.loads(akademi.calls[1].content)["actorRef"] == "km:user:u-1"

    data = await _call(locked_client, SUMMARY)
    assert data["ok"] is True
    assert data["headers"]["financeAccessUntil"]

    # Kayan süre: 29. dakikada istek süreyi yeniden 30 dk'ya uzatır.
    clock.now += 29 * 60
    assert (await _call(locked_client, SUMMARY))["ok"] is True
    clock.now += 29 * 60
    assert (await _call(locked_client, SUMMARY))["ok"] is True
    # 30 dk hareketsizlik → kapanır.
    clock.now += 30 * 60 + 1
    assert (await _call(locked_client, SUMMARY))["error"]["code"] == "finance_access_required"
    state = (await locked_client.get("/api/bbd_akademi/km/finance-access", headers=auth())).json()
    assert state["active"] is False


async def test_yanlis_kod_ve_baska_oturum(locked_client: httpx.AsyncClient, akademi: Akademi,
                                          identity: Identity) -> None:
    from modules.bbd_akademi.tests.akademi_fakes import ADMIN

    _akademi_otp(akademi)
    wrong = await _unlock(locked_client, code="000000")
    assert wrong.status_code == 422
    assert wrong.json()["error"]["code"] == "otp_invalid"

    # Aynı kişinin başka oturumu (başka belirteç) bu oturumun kodunu kullanamaz.
    identity.extra["admin-2"] = ADMIN
    asked = await locked_client.post("/api/bbd_akademi/km/finance-access/request", headers=auth())
    stolen = await locked_client.post("/api/bbd_akademi/km/finance-access/verify", headers=auth("admin-2"),
                                      json={"challengeId": asked.json()["challengeId"], "code": "123456"})
    assert stolen.status_code == 410
    assert stolen.json()["error"]["code"] == "otp_expired"

    # Doğru oturumda açılır; öteki oturum hâlâ kilitli.
    ok = await locked_client.post("/api/bbd_akademi/km/finance-access/verify", headers=auth(),
                                  json={"challengeId": asked.json()["challengeId"], "code": "123456"})
    assert ok.status_code == 200
    assert (await _call(locked_client, SUMMARY))["ok"] is True
    assert (await _call(locked_client, SUMMARY, who="admin-2"))["error"]["code"] == "finance_access_required"


async def test_kilitle_hemen_kapatir_ve_ozet_maskelenir(locked_client: httpx.AsyncClient,
                                                         akademi: Akademi) -> None:
    _akademi_otp(akademi)
    locked_board = await _call(locked_client, "/v1/admin/dashboard")
    assert locked_board["ok"] is True
    assert locked_board["data"]["institutionFinance"] is None
    assert locked_board["data"]["institutionFinanceLocked"] is True
    assert "etag" not in locked_board["headers"]

    await _unlock(locked_client)
    open_board = await _call(locked_client, "/v1/admin/dashboard")
    assert open_board["data"]["institutionFinance"] == {"overdueMinor": 500}

    ended = await locked_client.post("/api/bbd_akademi/km/finance-access/end", headers=auth())
    assert ended.status_code == 204
    assert (await _call(locked_client, SUMMARY))["error"]["code"] == "finance_access_required"


async def test_finans_izni_olmayan_kod_isteyemez(locked_client: httpx.AsyncClient, akademi: Akademi) -> None:
    response = await locked_client.post("/api/bbd_akademi/km/finance-access/request", headers=auth("viewer"))
    assert response.status_code == 403
    assert akademi.calls == []


async def test_finans_yoneticisi_yok_409(locked_client: httpx.AsyncClient, akademi: Akademi) -> None:
    akademi.reply(httpx.Response(409, json={"error": {
        "code": "finance_manager_missing", "message": "Finans yöneticisi tanımlı değil."}}))
    response = await locked_client.post("/api/bbd_akademi/km/finance-access/request", headers=auth())
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "finance_manager_missing"
    state = (await locked_client.get("/api/bbd_akademi/km/finance-access", headers=auth())).json()
    assert state["managerMissing"] is True


async def test_finans_yoneticisi_atama_kurali(client: httpx.AsyncClient, identity: Identity) -> None:
    """Hiç finans yöneticisi yokken personel izni yeter; sonra yalnız finans yöneticisi değiştirebilir."""
    from modules.bbd_akademi.backend.permission_map import STAFF, VIEW
    from modules.bbd_akademi.tests.akademi_fakes import user

    identity.extra["staffer"] = user("u-9", "Personel", "Sorumlusu", VIEW, STAFF)
    first = await client.post("/api/bbd_akademi/km/staff", headers=auth("staffer"), json={
        "displayName": "Finans Kişisi", "phone": "5321112233", "roles": ["admin"], "kmUserId": "u-1",
        "financeManager": True})
    assert first.status_code == 201, first.text
    assert first.json()["item"]["financeManager"] is True
    other = await client.post("/api/bbd_akademi/km/staff", headers=auth("staffer"), json={
        "displayName": "Başka Kişi", "phone": "5321112244", "roles": ["admin"]})
    row = other.json()["item"]
    # Artık bir finans yöneticisi var ve "staffer" o değil → reddedilir.
    denied = await client.patch(f"/api/bbd_akademi/km/staff/{row['id']}", headers=auth("staffer"),
                                json={"expectedRevision": row["revision"], "financeManager": True})
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "finance_manager_forbidden"
    # Finans yöneticisinin kendisi (KM kullanıcısı u-1 = admin) değiştirebilir.
    allowed = await client.patch(f"/api/bbd_akademi/km/staff/{row['id']}", headers=auth("admin"),
                                 json={"expectedRevision": row["revision"], "financeManager": True})
    assert allowed.status_code == 200, allowed.text
    listed = (await client.get("/api/bbd_akademi/km/staff", headers=auth("staffer"))).json()
    assert listed["canAssignFinanceManager"] is False


async def test_akademinin_kilit_uclari_vekilde_kapali(locked_client: httpx.AsyncClient, akademi: Akademi) -> None:
    for path in ("/v1/admin/finance-access/km/request", "/v1/admin/finance-access/request"):
        data = (await locked_client.post(CALL, headers=auth(), json={"method": "POST", "path": path})).json()
        assert data["error"]["code"] == "operation_not_allowed"
    assert akademi.calls == []
