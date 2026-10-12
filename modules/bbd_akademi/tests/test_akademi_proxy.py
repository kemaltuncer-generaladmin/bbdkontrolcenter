"""Ham vekil: başlıklar, akış (multipart / ikili), hata zarfı, Akademi kapalıyken 503."""

from __future__ import annotations

from urllib.parse import unquote

import httpx
import pytest
from modules.bbd_akademi.backend.gateway import GatewayConfig, on_behalf_of, request_id
from modules.bbd_akademi.tests.akademi_fakes import API_KEY, BASE, Akademi, Secrets, auth

SID = "0192f0aa-1111-7222-8333-944455556666"


async def test_baslik_iletimi(client: httpx.AsyncClient, akademi: Akademi) -> None:
    response = await client.post(
        f"/api/bbd_akademi/v1/admin/finance/accounts/{SID}/payments?dryRun=1",
        headers=auth(**{"Idempotency-Key": "pay-0001-abcdef", "X-Request-Id": "req-12345678",
                        "Cookie": "km=secret", "X-Forwarded-For": "1.2.3.4"}),
        json={"method": "cash", "amountMinor": 150000, "mode": "free_amount"})
    assert response.status_code == 200
    sent = akademi.last
    assert str(sent.url) == f"{BASE}/v1/admin/finance/accounts/{SID}/payments?dryRun=1"
    assert sent.headers["authorization"] == f"Bearer {API_KEY}"
    assert sent.headers["idempotency-key"] == "pay-0001-abcdef"
    assert sent.headers["x-request-id"] == "req-12345678"
    assert response.headers["x-request-id"] == "req-12345678"
    # KM oturum belirteci, çerez ve vekil başlıkları Akademi'ye SIZMAZ.
    assert "bearer admin" not in sent.headers["authorization"].lower()
    assert "cookie" not in sent.headers
    assert "x-forwarded-for" not in sent.headers
    # Ad soyad yüzde kodlu UTF-8; başlık ASCII kalır.
    behalf = sent.headers["x-akademi-on-behalf-of"]
    assert behalf == "km:user:u-1:Ay%C5%9Fe Y%C4%B1lmaz"
    assert behalf.isascii()
    assert unquote(behalf) == "km:user:u-1:Ayşe Yılmaz"
    assert akademi.bodies[-1] == b'{"method":"cash","amountMinor":150000,"mode":"free_amount"}'
    assert "x-akademi-step-up" not in sent.headers


async def test_istek_kimligi_yoksa_uretilir(client: httpx.AsyncClient, akademi: Akademi) -> None:
    response = await client.get("/api/bbd_akademi/v1/admin/dashboard",
                                headers=auth(**{"X-Request-Id": "short"}))
    generated = akademi.last.headers["x-request-id"]
    assert generated.startswith("km-") and len(generated) > 20
    assert response.headers["x-request-id"] == generated


def test_on_behalf_160_karakteri_asmaz_ve_kodu_bolmez() -> None:
    value = on_behalf_of("u-1", "Şükrü Çağlayan Öztürkoğlu " * 12)
    assert len(value) <= 160
    assert value.isascii()
    unquote(value, errors="strict")  # yarım kod dizisi yok
    assert not value.endswith("%") and "%" not in value[-2:]
    assert request_id("geçerli-değil") != "geçerli-değil"


async def test_sorgu_aynen_gecer(client: httpx.AsyncClient, akademi: Akademi) -> None:
    await client.get("/api/bbd_akademi/v1/admin/students?q=Ay%C5%9Fe&classGroupId=a&limit=50",
                     headers=auth())
    assert akademi.last.url.query == b"q=Ay%C5%9Fe&classGroupId=a&limit=50"


async def test_multipart_akis_olarak_gecer(client: httpx.AsyncClient, akademi: Akademi) -> None:
    payload = b"okul_no;ad;mat_d\n101;Ali;12\n" * 50
    response = await client.post(
        f"/api/bbd_akademi/v1/admin/exams/{SID}/imports",
        headers=auth(**{"Idempotency-Key": "imp-0001-abcdef"}),
        files={"file": ("sonuc.csv", payload, "text/csv")},
        data={"templateId": "tpl-1"})
    assert response.status_code == 200
    sent = akademi.last
    assert sent.headers["content-type"].startswith("multipart/form-data; boundary=")
    body = akademi.bodies[-1]
    assert payload in body
    assert b'name="templateId"' in body and b"tpl-1" in body
    assert sent.headers["idempotency-key"] == "imp-0001-abcdef"


async def test_buyuk_yukleme_413(client: httpx.AsyncClient, akademi: Akademi) -> None:
    response = await client.put(
        "/api/bbd_akademi/v1/admin/branding/logo", headers=auth(),
        files={"file": ("logo.png", b"x" * (1024 * 1024 + 10), "image/png")})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert akademi.calls == []


async def test_ikili_yanit_akis_olarak_doner(client: httpx.AsyncClient, akademi: Akademi) -> None:
    pdf = b"%PDF-1.7\n" + bytes(range(256)) * 40
    akademi.reply(httpx.Response(200, content=pdf, headers={
        "content-type": "application/pdf",
        "content-disposition": 'attachment; filename="makbuz.pdf"',
        "etag": '"v1"'}))
    response = await client.get(f"/api/bbd_akademi/v1/admin/finance/payments/{SID}/receipt",
                                headers=auth())
    assert response.status_code == 200
    assert response.content == pdf
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="makbuz.pdf"'
    assert response.headers["etag"] == '"v1"'


@pytest.mark.parametrize("status,code", [(422, "validation_error"), (409, "timetable_conflict"),
                                         (404, "student_not_found"), (503, "canteen_unavailable"),
                                         (429, "rate_limited")])
async def test_hata_zarfi_oldugu_gibi_doner(client: httpx.AsyncClient, akademi: Akademi,
                                           status: int, code: str) -> None:
    body = {"error": {"code": code, "message": "Türkçe açıklama", "traceId": "t-1",
                      "details": [{"field": "amountMinor", "message": "en az 1"}]}}
    akademi.reply(httpx.Response(status, json=body, headers={"Retry-After": "7"}))
    response = await client.get(f"/api/bbd_akademi/v1/admin/students/{SID}", headers=auth())
    assert response.status_code == status
    assert response.json() == body
    assert response.headers["retry-after"] == "7"


async def test_akademi_401_kullanici_oturumu_degildir(client: httpx.AsyncClient,
                                                      akademi: Akademi) -> None:
    body = {"error": {"code": "invalid_integration_key", "message": "Anahtar geçersiz",
                      "details": None, "traceId": "t"}}
    akademi.reply(httpx.Response(401, json=body))
    response = await client.get("/api/bbd_akademi/v1/admin/dashboard", headers=auth())
    assert response.status_code == 502
    assert response.json() == body
    assert response.headers["x-akademi-upstream-status"] == "401"


@pytest.mark.parametrize("failure", [httpx.ConnectError("refused"),
                                     httpx.ReadTimeout("slow"),
                                     httpx.RemoteProtocolError("bozuk")])
async def test_akademi_kapaliyken_503(client: httpx.AsyncClient, akademi: Akademi,
                                      failure: Exception) -> None:
    akademi.down = failure
    response = await client.get("/api/bbd_akademi/v1/admin/dashboard", headers=auth())
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "akademi_unavailable"
    assert error["message"]
    assert error["traceId"] == response.headers["x-request-id"]


async def test_anahtar_yoksa_503_ve_aga_cikilmaz(client: httpx.AsyncClient, akademi: Akademi,
                                                secrets: Secrets) -> None:
    secrets.values.clear()
    response = await client.get("/api/bbd_akademi/v1/admin/dashboard", headers=auth())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "akademi_not_configured"
    assert akademi.calls == []


async def test_salter_kapaliyken_503(client: httpx.AsyncClient, akademi: Akademi,
                                     config: GatewayConfig) -> None:
    config.enabled = False
    response = await client.get("/api/bbd_akademi/v1/admin/dashboard", headers=auth())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "akademi_disabled"
    assert akademi.calls == []


async def test_304_govdesiz_doner(client: httpx.AsyncClient, akademi: Akademi) -> None:
    akademi.reply(httpx.Response(304, headers={"etag": '"x"'}))
    response = await client.get("/api/bbd_akademi/v1/admin/dashboard",
                                headers=auth(**{"If-None-Match": '"x"'}))
    assert response.status_code == 304
    assert akademi.last.headers["if-none-match"] == '"x"'


async def test_durum_ucu_anahtari_gostermez(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/bbd_akademi/km/status", headers=auth("viewer"))
    data = response.json()
    assert response.status_code == 200
    assert data["connection"] == {"enabled": True, "baseUrl": BASE, "hasKey": True}
    assert API_KEY not in response.text
    assert data["permissions"] == ["bbd_akademi.view"]
    assert data["staffSync"] is None
    operations = (await client.get("/api/bbd_akademi/km/operations",
                                   headers=auth("viewer"))).json()["items"]
    by_id = {item["operationId"]: item for item in operations}
    assert by_id["admin_getDashboard"]["allowed"] is True
    assert by_id["admin_voidPayment"]["allowed"] is False
    assert by_id["admin_voidPayment"]["stepUp"] is True
