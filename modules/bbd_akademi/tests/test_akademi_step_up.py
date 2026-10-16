"""Step-up: tahsilat iptali ancak az önce doğrulanmış PIN ile Akademi'ye gider."""

from __future__ import annotations

import httpx
from modules.bbd_akademi.backend.core.stepup import StepUpStore
from modules.bbd_akademi.tests.akademi_fakes import PIN, Akademi, CheckedStore, Identity, auth

PID = "0192f0aa-1111-7222-8333-944455556666"
VOID = f"/v1/admin/finance/payments/{PID}/void"
STEP_UP = "/api/bbd_akademi/km/step-up"


async def _token(client: httpx.AsyncClient, who: str = "admin", pin: str = PIN,
                 operation: str = "admin_voidPayment") -> httpx.Response:
    return await client.post(STEP_UP, headers=auth(who),
                             json={"pin": pin, "operationId": operation})


async def test_belirtecsiz_iptal_akademiye_gitmez(client: httpx.AsyncClient,
                                                  akademi: Akademi) -> None:
    response = await client.post(f"/api/bbd_akademi{VOID}", headers=auth(),
                                 json={"reason": "yanlış kayıt"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "step_up_required"
    assert akademi.calls == []


async def test_yanlis_pin_403_ve_denetim(client: httpx.AsyncClient, identity: Identity) -> None:
    response = await _token(client, pin="000000")
    assert response.status_code == 403
    assert response.json()["error"]["message"] == "PIN doğrulanamadı."
    assert any("bbd_akademi.step_up:admin_voidPayment" in str(entry)
               for entry in identity.audits)


async def test_tam_akis_ham_vekil(client: httpx.AsyncClient, akademi: Akademi) -> None:
    issued = await _token(client)
    assert issued.status_code == 200
    token = issued.json()["stepUpToken"]
    assert issued.json()["expiresInSeconds"] == 120
    assert PIN not in issued.text

    response = await client.post(f"/api/bbd_akademi{VOID}",
                                 headers=auth(**{"X-KM-Step-Up": token,
                                                 "Idempotency-Key": "void-000000001"}),
                                 json={"reason": "yanlış kayıt"})
    assert response.status_code == 200
    sent = akademi.last
    assert sent.headers["x-akademi-step-up"] == "km-pin"
    assert sent.headers["idempotency-key"] == "void-000000001"
    # Ne PIN ne KM belirteci Akademi'ye gider.
    assert "x-km-step-up" not in sent.headers
    assert PIN.encode() not in akademi.bodies[-1]

    # Tek kullanımlık: ikinci deneme reddedilir.
    again = await client.post(f"/api/bbd_akademi{VOID}",
                              headers=auth(**{"X-KM-Step-Up": token}), json={"reason": "x"})
    assert again.status_code == 403
    assert again.json()["error"]["code"] == "step_up_required"
    assert len(akademi.calls) == 1


async def test_kopru_ile_akis(client: httpx.AsyncClient, akademi: Akademi) -> None:
    token = (await _token(client)).json()["stepUpToken"]
    data = (await client.post("/api/bbd_akademi/km/call", headers=auth(), json={
        "method": "POST", "path": VOID, "body": {"reason": "mükerrer"},
        "idempotencyKey": "void-000000002", "stepUpToken": token})).json()
    assert data["ok"] is True
    assert akademi.last.headers["x-akademi-step-up"] == "km-pin"


async def test_belirtec_kisiye_ve_isleme_bagli(client: httpx.AsyncClient,
                                               akademi: Akademi,
                                               identity: Identity) -> None:
    from modules.bbd_akademi.tests.akademi_fakes import user

    identity.extra["other"] = user("u-7", "Başka", "Yönetici", "bbd_akademi.view",
                                   "bbd_akademi.finance_void")
    token = (await _token(client)).json()["stepUpToken"]
    stolen = await client.post(f"/api/bbd_akademi{VOID}",
                               headers=auth("other", **{"X-KM-Step-Up": token}),
                               json={"reason": "x"})
    assert stolen.status_code == 403
    assert stolen.json()["error"]["code"] == "step_up_required"
    assert akademi.calls == []


async def test_izinsiz_kullanici_belirtec_alamaz(client: httpx.AsyncClient) -> None:
    response = await _token(client, who="cashier")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_step_up_gerektirmeyen_islem(client: httpx.AsyncClient) -> None:
    response = await _token(client, operation="admin_recordPayment")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "step_up_not_applicable"


async def test_suresi_dolan_belirtec(store: CheckedStore) -> None:
    now = [1_000_000.0]
    steps = StepUpStore(store, ttl_seconds=120, clock=lambda: now[0])
    token = (await steps.issue("u-1", "admin_voidPayment"))["stepUpToken"]
    now[0] += 121
    assert await steps.consume(token, user_id="u-1", operation_id="admin_voidPayment") is False
    fresh = (await steps.issue("u-1", "admin_voidPayment"))["stepUpToken"]
    assert await steps.consume(fresh, user_id="u-1", operation_id="admin_recordPayment") is False
    assert await steps.consume(fresh, user_id="u-1", operation_id="admin_voidPayment") is True
    assert await steps.consume(fresh, user_id="u-1", operation_id="admin_voidPayment") is False
    assert await steps.consume(None, user_id="u-1", operation_id="admin_voidPayment") is False
