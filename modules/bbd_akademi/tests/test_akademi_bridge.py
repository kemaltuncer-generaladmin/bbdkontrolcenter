"""Panel köprüsü (`km/call`): aynı kapılar, JSON zarf, base64 dosya, imzalı indirme."""

from __future__ import annotations

import base64
import json

import httpx
from modules.bbd_akademi.backend.core.gateway import GatewayConfig
from modules.bbd_akademi.tests.akademi_fakes import API_KEY, BASE, Akademi, auth

SID = "0192f0aa-1111-7222-8333-944455556666"
CALL = "/api/bbd_akademi/km/call"


async def test_basarili_cagri_zarfi(client: httpx.AsyncClient, akademi: Akademi) -> None:
    akademi.reply(httpx.Response(201, json={"id": "p1", "receiptNo": "M-1"},
                                 headers={"Idempotency-Replayed": "true", "X-Request-Id": "r-1"}))
    response = await client.post(CALL, headers=auth(), json={
        "method": "POST", "path": f"/v1/admin/finance/accounts/{SID}/payments",
        "body": {"method": "cash", "amountMinor": 5000, "mode": "free_amount"},
        "idempotencyKey": "pay-key-000001"})
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True and data["status"] == 201
    assert data["data"] == {"id": "p1", "receiptNo": "M-1"}
    assert data["headers"]["idempotencyReplayed"] is True
    sent = akademi.last
    assert sent.headers["authorization"] == f"Bearer {API_KEY}"
    assert sent.headers["idempotency-key"] == "pay-key-000001"
    assert sent.headers["content-type"] == "application/json"
    assert json.loads(akademi.bodies[-1]) == {"method": "cash", "amountMinor": 5000,
                                              "mode": "free_amount"}
    assert sent.headers["x-akademi-on-behalf-of"].startswith("km:user:u-1:")


async def test_hata_kodu_ve_ayrintisi_panele_ulasir(client: httpx.AsyncClient,
                                                   akademi: Akademi) -> None:
    conflict = {"error": {"code": "timetable_conflict", "message": "Öğretmen dolu",
                          "details": [{"kind": "teacher", "day": 1}], "traceId": "t-9"}}
    akademi.reply(httpx.Response(409, json=conflict))
    data = (await client.post(CALL, headers=auth(), json={
        "method": "PUT", "path": f"/v1/admin/class-groups/{SID}/timetable",
        "body": {"entries": []}})).json()
    assert data["ok"] is False and data["status"] == 409
    assert data["error"]["code"] == "timetable_conflict"
    assert data["error"]["details"] == [{"kind": "teacher", "day": 1}]


async def test_sorgu_nesnesi_kodlanir(client: httpx.AsyncClient, akademi: Akademi) -> None:
    await client.post(CALL, headers=auth(), json={
        "method": "GET", "path": "/v1/admin/students",
        "query": {"q": "Ayşe", "gradeLevel": [11, 12], "active": True, "cursor": None}})
    params = akademi.last.url.params
    assert params["q"] == "Ayşe"
    assert params.get_list("gradeLevel") == ["11", "12"]
    assert params["active"] == "true"
    assert "cursor" not in params


async def test_kopru_ayni_izin_kapisindan_gecer(client: httpx.AsyncClient,
                                                akademi: Akademi) -> None:
    denied = (await client.post(CALL, headers=auth("viewer"), json={
        "method": "POST", "path": f"/v1/admin/finance/accounts/{SID}/charges",
        "body": {}})).json()
    assert denied["ok"] is False and denied["status"] == 403
    assert denied["error"]["code"] == "forbidden"
    assert denied["error"]["details"]["permission"] == "bbd_akademi.finance"
    outside = (await client.post(CALL, headers=auth(), json={
        "method": "POST", "path": "/v1/auth/otp/request", "body": {}})).json()
    assert outside["error"]["code"] == "operation_not_allowed"
    assert akademi.calls == []


async def test_gecersiz_islem_anahtari(client: httpx.AsyncClient, akademi: Akademi) -> None:
    data = (await client.post(CALL, headers=auth(), json={
        "method": "POST", "path": "/v1/admin/announcements", "body": {},
        "idempotencyKey": "boş anahtar!"})).json()
    assert data["error"]["code"] == "idempotency_key_invalid"
    assert akademi.calls == []


async def test_dosya_yukleme_base64_multipart_olur(client: httpx.AsyncClient,
                                                   akademi: Akademi) -> None:
    xlsx = b"PK\x03\x04" + bytes(range(200))
    data = (await client.post(CALL, headers=auth(), json={
        "method": "POST", "path": f"/v1/admin/exams/{SID}/imports",
        "idempotencyKey": "import-0000001",
        "upload": {"field": "file", "filename": "deneme.xlsx",
                   "contentType": "application/vnd.ms-excel",
                   "dataBase64": base64.b64encode(xlsx).decode(),
                   "fields": {"templateId": "t1", "historical": True}}})).json()
    assert data["ok"] is True
    sent = akademi.last
    assert sent.headers["content-type"].startswith("multipart/form-data")
    assert xlsx in akademi.bodies[-1]
    assert b'filename="deneme.xlsx"' in akademi.bodies[-1]
    assert b"historical" in akademi.bodies[-1]
    assert sent.headers["idempotency-key"] == "import-0000001"


async def test_bozuk_base64(client: httpx.AsyncClient, akademi: Akademi) -> None:
    data = (await client.post(CALL, headers=auth(), json={
        "method": "PUT", "path": "/v1/admin/branding/logo",
        "upload": {"filename": "logo.png", "dataBase64": "@@@@"}})).json()
    assert data["error"]["code"] == "upload_invalid"
    assert akademi.calls == []


async def test_ikili_yanit_base64_doner(client: httpx.AsyncClient, akademi: Akademi) -> None:
    pdf = b"%PDF-1.4 binary \x00\xff"
    akademi.reply(httpx.Response(200, content=pdf, headers={
        "content-type": "application/pdf",
        "content-disposition": "attachment; filename*=UTF-8''karne-%C3%B6%C4%9Frenci.pdf"}))
    data = (await client.post(CALL, headers=auth(), json={
        "method": "GET", "path": f"/v1/admin/finance/payments/{SID}/receipt"})).json()
    assert data["ok"] is True
    assert base64.b64decode(data["file"]["dataBase64"]) == pdf
    assert data["file"]["contentType"] == "application/pdf"
    assert data["file"]["filename"] == "karne-öğrenci.pdf"


async def test_akademi_kapali_zarfta_503(client: httpx.AsyncClient, akademi: Akademi) -> None:
    akademi.down = httpx.ConnectError("refused")
    data = (await client.post(CALL, headers=auth(), json={
        "method": "GET", "path": "/v1/admin/dashboard"})).json()
    assert data["ok"] is False and data["status"] == 503
    assert data["error"]["code"] == "akademi_unavailable"


async def test_imzali_indirme_yalniz_izinli_alandan(client: httpx.AsyncClient,
                                                    akademi: Akademi,
                                                    config: GatewayConfig) -> None:
    akademi.reply(httpx.Response(200, content=b"XLSX", headers={
        "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}))
    ok = (await client.post("/api/bbd_akademi/km/fetch", headers=auth("viewer"),
                            json={"url": f"{BASE}/exports/abc.xlsx?sig=1"})).json()
    assert ok["ok"] is True and base64.b64decode(ok["file"]["dataBase64"]) == b"XLSX"
    # İmzalı bağlantıya entegrasyon anahtarı gönderilmez.
    assert "authorization" not in akademi.last.headers

    evil = (await client.post("/api/bbd_akademi/km/fetch", headers=auth(),
                              json={"url": "https://169.254.169.254/latest/meta-data"})).json()
    assert evil["ok"] is False and evil["error"]["code"] == "download_not_allowed"
    plain = (await client.post("/api/bbd_akademi/km/fetch", headers=auth(),
                               json={"url": "http://akademi.test/x.pdf"})).json()
    assert plain["error"]["code"] == "download_not_allowed"

    config.download_hosts.append("cdn.akademi.test")
    calls = len(akademi.calls)
    streamed = await client.get("/api/bbd_akademi/km/download", headers=auth(),
                                params={"url": "https://cdn.akademi.test/f.pdf"})
    assert streamed.status_code == 200 and streamed.content == b"XLSX"
    assert len(akademi.calls) == calls + 1
