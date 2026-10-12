"""Tel biçimi: FastAPI üzerinden gerçek JSON gövdeleri, kodlu hata ve PIN kapısı."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from modules.bbd_tablet.backend.api import routes, tablet_routes
from modules.bbd_tablet.backend.service import TabletService

from km_sdk import CurrentUser

ADMIN = CurrentUser(id="u1", first_name="Ayşe", last_name="Yılmaz", org_scope="bbd",
                    roles=["admin"], permissions={"bbd_tablet.view", "bbd_tablet.manage",
                                                  "bbd_tablet.grant"})
VIEWER = CurrentUser(id="u2", first_name="Can", last_name="Demir", org_scope="bbd",
                     roles=["org_staff"], permissions={"bbd_tablet.view"})
MANAGER = CurrentUser(id="u3", first_name="Ece", last_name="Kaya", org_scope="bbd",
                      roles=["bbd_staff"], permissions={"bbd_tablet.view", "bbd_tablet.manage"})


class Identity:
    def __init__(self) -> None:
        self.store = self

    async def resolve_session(self, token: str) -> CurrentUser | None:
        return {"admin": ADMIN, "viewer": VIEWER, "manager": MANAGER}.get(token)

    @staticmethod
    def secret_lookup(pin: str) -> str:
        return f"h:{pin}"

    async def fetch_one(self, sql: str, params: tuple[Any, ...]) -> dict[str, str]:
        return {"secret_lookup": "h:482913"}

    async def audit(self, *args: Any, **kwargs: Any) -> None:
        return None


def core_envelope(app: FastAPI) -> None:
    """Same HTTPException handler as km_core/http/app.py: non-text detail is lost.

    Without it this test app would pass coded errors that production strips.
    """

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "İstek reddedildi."
        return JSONResponse(status_code=exc.status_code,
                            content={"error": {"status": exc.status_code, "message": detail}},
                            headers=exc.headers)


@pytest.fixture
async def client(store: Any, canteen: Any, clock: Any) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    core_envelope(app)
    app.state.identity = Identity()
    app.include_router(routes.bind(TabletService(store=store, canteen=canteen, config={},
                                                 clock=clock)), prefix="/api/bbd_tablet")
    app.include_router(tablet_routes.router, prefix="/api/tablet/v1")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    routes._service = None


ADMIN_AUTH = {"Authorization": "Bearer admin"}


async def test_tablet_wire_format(client: httpx.AsyncClient) -> None:
    profile = (await client.post("/api/bbd_tablet/profiles", headers=ADMIN_AUTH, json={
        "name": "Ortaokul", "apps": [{"packageName": "com.example.video", "appName": "Video",
                                      "allowed": True, "dailyLimitSeconds": 1800}],
        "usageWindows": []})).json()
    created = (await client.post("/api/bbd_tablet/devices", headers=ADMIN_AUTH,
                                 json={"name": "Etüt-01", "profileId": profile["id"]})).json()
    enrolled = await client.post("/api/tablet/v1/devices/enroll", json={
        "enrollmentCode": created["enrollmentCodeDisplay"].lower(),
        "deviceUuid": "uuid-0000-0001", "deviceName": "Etüt-01"})
    assert enrolled.status_code == 200
    device_id = enrolled.json()["deviceId"]
    auth = {"Authorization": f"Bearer {enrolled.json()['deviceToken']}"}

    synced = await client.post(f"/api/tablet/v1/devices/{device_id}/sync", headers=auth,
                               json={"appVersion": "0.6.0", "futureField": True})
    assert synced.status_code == 200
    body = synced.json()
    assert set(body) == {"serverTime", "nextSyncSeconds", "usageFlushSeconds",
                         "assignedProfileId", "profileRevision", "profile", "deviceState",
                         "session", "grants", "commands"}
    assert body["profile"]["usageWindows"] == []

    wrong = await client.post("/api/tablet/v1/auth/login", headers=auth,
                              json={"deviceId": device_id, "studentCode": "000000"})
    assert wrong.status_code == 401
    assert wrong.json() == {"detail": {"code": "INVALID_STUDENT_CODE",
                                       "message": "Şifre hatalı."}}
    for _ in range(4):
        limited = await client.post("/api/tablet/v1/auth/login", headers=auth,
                                    json={"deviceId": device_id, "studentCode": "000000"})
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "300"
    assert limited.json()["detail"]["retryAfterSeconds"] == 300

    unknown = await client.post(f"/api/tablet/v1/devices/{device_id}/sync",
                                headers={"Authorization": "Bearer yok"}, json={})
    assert unknown.status_code == 401
    assert unknown.json()["detail"]["code"] == "DEVICE_AUTH_INVALID"


async def test_management_wire_format_and_pin(client: httpx.AsyncClient) -> None:
    created = (await client.post("/api/bbd_tablet/devices", headers=ADMIN_AUTH,
                                 json={"name": "Etüt-01"})).json()
    path = f"/api/bbd_tablet/devices/{created['id']}"
    denied = await client.patch(path, headers={"Authorization": "Bearer viewer"},
                                json={"locked": True})
    assert denied.status_code == 403
    patched = await client.patch(path, headers=ADMIN_AUTH,
                                 json={"locked": True, "lockMessage": "Sınav"})
    assert patched.json()["stateVersion"] == 2
    assert (await client.patch(path, headers=ADMIN_AUTH, json={})).status_code == 400
    code = await client.post(f"{path}/enrollment-code", headers=ADMIN_AUTH)
    assert code.status_code == 200 and len(code.json()["enrollmentCode"]) == 8

    assert (await client.request("DELETE", path, headers=ADMIN_AUTH,
                                 json={})).status_code == 422
    wrong_pin = await client.request("DELETE", path, headers=ADMIN_AUTH,
                                     json={"pin": "000000"})
    assert wrong_pin.status_code == 403
    deleted = await client.request("DELETE", path, headers=ADMIN_AUTH, json={"pin": "482913"})
    assert deleted.json() == {"deleted": True, "deviceId": created["id"], "revoked": False}
    gone = await client.request("DELETE", path, headers=ADMIN_AUTH, json={"pin": "482913"})
    assert gone.status_code == 404

    overview = (await client.get("/api/bbd_tablet/overview",
                                 headers={"Authorization": "Bearer viewer"})).json()
    assert overview["summary"]["tabletsTotal"] == 0
    history = await client.get("/api/bbd_tablet/sessions?limit=5", headers=ADMIN_AUTH)
    assert history.json() == []


async def test_delete_actions_over_http(client: httpx.AsyncClient) -> None:
    viewer = {"Authorization": "Bearer viewer"}
    base = "/api/bbd_tablet"
    apps = [{"packageName": "com.example.video", "appName": "Video", "allowed": True,
             "dailyLimitSeconds": 1800}]
    profile = (await client.post(f"{base}/profiles", headers=ADMIN_AUTH,
                                 json={"name": "Ortaokul", "apps": apps})).json()
    other = (await client.post(f"{base}/profiles", headers=ADMIN_AUTH,
                               json={"name": "Lise", "apps": apps})).json()
    created = (await client.post(f"{base}/devices", headers=ADMIN_AUTH,
                                 json={"name": "Etüt-01", "profileId": profile["id"]})).json()
    enrolled = (await client.post("/api/tablet/v1/devices/enroll", json={
        "enrollmentCode": created["enrollmentCode"], "deviceUuid": "uuid-0000-0002",
        "deviceName": "Etüt-01"})).json()
    device = f"{base}/devices/{enrolled['deviceId']}"
    auth = {"Authorization": f"Bearer {enrolled['deviceToken']}"}

    in_use = await client.request("DELETE", f"{base}/profiles/{profile['id']}",
                                  headers=ADMIN_AUTH, json={"pin": "482913"})
    assert in_use.status_code == 409
    assert in_use.json()["error"]["message"].startswith("Bu profil 1 tablete atanmış.")
    moved = await client.request("DELETE", f"{base}/profiles/{profile['id']}",
                                 headers=ADMIN_AUTH,
                                 json={"pin": "482913", "replacementProfileId": other["id"]})
    assert moved.json()["movedDevices"] == 1

    command = (await client.post(f"{device}/commands", headers=ADMIN_AUTH,
                                 json={"type": "reboot"})).json()
    path = f"{device}/commands/{command['id']}"
    assert (await client.delete(path, headers=viewer)).status_code == 403
    assert (await client.delete(path, headers=ADMIN_AUTH)).json()["status"] == "cancelled"
    synced = await client.post(f"/api/tablet/v1/devices/{enrolled['deviceId']}/sync",
                               headers=auth, json={})
    assert synced.json()["commands"] == []

    assert (await client.post(f"{device}/login-lock/reset", headers=viewer)).status_code == 403
    reset = await client.post(f"{device}/login-lock/reset", headers=ADMIN_AUTH)
    assert reset.json() == {"deviceId": enrolled["deviceId"], "cleared": False}

    history = f"{base}/students/opaque-A/history"
    assert (await client.request("DELETE", history, headers=ADMIN_AUTH,
                                 json={"pin": "000000"})).status_code == 403
    erased = await client.request("DELETE", history, headers=ADMIN_AUTH, json={"pin": "482913"})
    assert erased.json()["studentId"] == "opaque-A"

    # Bulk login-lock reset needs grant like the single one, not only manage.
    unlock = {"deviceIds": [enrolled["deviceId"]], "action": "reset_login_lock"}
    assert (await client.post(f"{base}/devices/bulk", headers={"Authorization": "Bearer manager"},
                              json=unlock)).status_code == 403
    unlocked = await client.post(f"{base}/devices/bulk", headers=ADMIN_AUTH, json=unlock)
    assert unlocked.json()["okCount"] == 1

    bulk = {"deviceIds": [enrolled["deviceId"]], "action": "delete"}
    assert (await client.post(f"{base}/devices/bulk", headers=ADMIN_AUTH,
                              json=bulk)).status_code == 422
    assert (await client.post(f"{base}/devices/bulk", headers=ADMIN_AUTH,
                              json={**bulk, "pin": "000000"})).status_code == 403
    assert (await client.get(f"{base}/overview", headers=ADMIN_AUTH)).json()["devices"]
    removed = await client.post(f"{base}/devices/bulk", headers=ADMIN_AUTH,
                                json={**bulk, "pin": "482913"})
    assert removed.json()["okCount"] == 1
    dead = await client.post(f"/api/tablet/v1/devices/{enrolled['deviceId']}/unpair",
                             headers=auth)
    assert dead.status_code == 401
    assert dead.json()["detail"]["code"] == "DEVICE_AUTH_INVALID"


class RecordingOutbox:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any, list[str]]] = []

    async def emit(self, kind: str, subject: Any, *, fields: Any = (), **_: Any) -> str:
        self.events.append((kind, subject, list(fields)))
        return "01JA7Q2W3E4R5T6Y7V8W9X0Y1Z"


async def test_km_ekrani_yazmalari_olay_yazar(store: Any, canteen: Any, clock: Any) -> None:
    """KM ekranından kısıt ve profil değişikliği outbox'a gider (km-merkezi-senkron §2.2)."""
    outbox = RecordingOutbox()
    app = FastAPI()
    core_envelope(app)
    app.state.identity = Identity()
    app.include_router(routes.bind(TabletService(store=store, canteen=canteen, config={},
                                                 clock=clock), outbox=lambda: outbox),
                       prefix="/api/bbd_tablet")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        profile = await http.post("/api/bbd_tablet/profiles", headers=ADMIN_AUTH, json={
            "name": "Lise", "apps": [], "usageWindows": []})
        assert profile.status_code == 200, profile.text
        until = (clock() + timedelta(days=1)).isoformat()
        blocked = await http.post("/api/bbd_tablet/students/opaque-A/block",
                                  headers=ADMIN_AUTH, json={"until": until, "reason": "test"})
        assert blocked.status_code == 200, blocked.text
        lifted = await http.post("/api/bbd_tablet/students/opaque-A/unblock", headers=ADMIN_AUTH)
        assert lifted.status_code == 200
        # Kısıt yokken kaldırma olay yazmaz.
        await http.post("/api/bbd_tablet/students/opaque-A/unblock", headers=ADMIN_AUTH)
    routes._service = None
    routes._outbox = None
    assert [(kind, subject[0]) for kind, subject, _ in outbox.events] == [
        ("tablet.profile_changed", "tablet_profile"),
        ("tablet.policy_changed", "student"),
        ("tablet.policy_changed", "student"),
    ]
    assert outbox.events[1][1] == ("student", "opaque-A")
    assert outbox.events[1][2] == ["blockedUntil"]
