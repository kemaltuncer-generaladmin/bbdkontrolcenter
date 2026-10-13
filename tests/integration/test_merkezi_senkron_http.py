"""Merkezi senkron — gerçek uygulama üzerinden HTTP (km-merkezi-senkron §6.1, §8).

Bütün modüller yüklenir (kantin sahte değil ama ağa çıkılmaz: kasada cihaz
token'ı ve kayıt sırrı yok, kantin istemcisi istek atmadan reddeder).

  · kantin olay itmesi `POST /api/bbd_canteen_api/v1/events` — servis anahtarı,
    rol `canteen_service`; başka rol 403;
  · KM paneli uzun yoklaması `GET /api/events/wait`.
"""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from km_core.config.loader import ROOT, Config, load_config
from km_core.http.app import create_app
from km_core.security.identity import Identity
from km_core.security.service_keys import ServiceKeys

PUSH = "/api/bbd_canteen_api/v1/events"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    data = deepcopy(load_config().as_dict())
    data["core"] = {
        **data.get("core", {}),
        "store_path": str(tmp_path / "km.sqlite"),
        "secret_key_path": str(tmp_path / "secret.key"),
        "log_path": str(tmp_path / "gunluk.log"),
    }
    data["auth"] = {**data.get("auth", {}), "bootstrap_pin": "482913"}
    data["files"] = {"output_path": str(tmp_path / "cikti")}
    modules = dict(data.get("modules") or {})
    modules["bbd_canteen_api"] = {**dict(modules.get("bbd_canteen_api") or {}),
                                  "events_pull": False}
    data["modules"] = modules
    with TestClient(create_app(Config(data, root=ROOT)), raise_server_exceptions=False) as c:
        yield c


def _token(client: TestClient, role: str) -> str:
    identity: Identity = client.app.state.identity  # type: ignore[attr-defined]
    keys: ServiceKeys = client.app.state.service_keys  # type: ignore[attr-defined]

    async def create() -> str:
        user_id = await identity.create_service_user(name=f"Servis {role}", roles=[role])
        return (await keys.create(user_id=user_id, name=role)).token

    return client.portal.call(create)  # type: ignore[union-attr]


def test_moduller_yuklenir(client: TestClient) -> None:
    kernel = client.app.state.kernel  # type: ignore[attr-defined]
    loaded = {record.manifest.id for record in kernel.loaded()}
    assert {"bbd_students", "bbd_canteen_api"} <= loaded
    assert client.app.state.registry.try_resolve("events.outbox") is not None  # type: ignore[attr-defined]


def test_kantin_itmesi_ve_uzun_yoklama(client: TestClient) -> None:
    canteen = {"Authorization": f"Bearer {_token(client, 'canteen_service')}"}
    reader = {"Authorization": f"Bearer {_token(client, 'mobile_service')}"}

    start = client.get("/api/events/wait", headers=reader)
    assert start.status_code == 200, start.text
    assert start.json() == {"items": [], "lastSeq": 0, "reset": False}

    body = {"source": "canteen", "sentAt": "2026-10-12T12:00:00+03:00", "events": [
        {"id": 41, "type": "student.controls_changed", "opaqueId": "STU-A1",
         "payload": {"opaqueId": "STU-A1", "fields": ["isBlocked"]},
         "createdAt": "2026-10-12T12:00:00+03:00"},
        {"id": 42, "type": "payment_request.changed", "opaqueId": "STU-A1",
         "payload": {"opaqueId": "STU-A1", "ref": "PR-9", "status": "PAID"},
         "createdAt": "2026-10-12T12:00:01+03:00"},
    ]}
    pushed = client.post(PUSH, json=body, headers=canteen)
    assert pushed.status_code == 200, pushed.text
    assert pushed.json() == {"received": 2, "duplicates": 0, "lastId": 42}
    again = client.post(PUSH, json=body, headers=canteen)
    assert again.json() == {"received": 0, "duplicates": 2, "lastId": 42}

    waited = client.get("/api/events/wait?after=0&types=canteen.controls_changed&timeout=1",
                        headers=reader)
    assert waited.status_code == 200
    payload = waited.json()
    assert [item["type"] for item in payload["items"]] == ["canteen.controls_changed"]
    assert payload["items"][0]["causationId"] == "CNT-41"
    # Süzgeç dışı olay da tarandı: imleç ikisinin de ötesinde.
    assert payload["lastSeq"] == 2
    tail = client.get("/api/events/wait?after=1&timeout=0", headers=reader).json()
    assert [item["type"] for item in tail["items"]] == ["canteen.payment_request_changed"]
    later = client.get(f"/api/events/wait?after={tail['lastSeq']}&timeout=0.2",
                       headers=reader).json()
    assert later["items"] == [] and later["lastSeq"] == tail["lastSeq"]

    # Başka servis rolü kantin adına olay itemez (K9).
    assert client.post(PUSH, json=body, headers=reader).status_code == 403
    assert client.post(PUSH, json=body).status_code == 401
    assert client.get("/api/events/wait").status_code == 401
