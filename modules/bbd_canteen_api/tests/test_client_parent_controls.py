"""`canteen.api` — veli kantin kontrolleri, serbest tutarlı talep, tahsilat, akışlar.

Sözleşme: bbd-akademi `docs/integrations/kantin-veli-kontrolleri.md` §1.3/1.5/1.6.
Kantin `httpx.MockTransport` ile taklit edilir; ağa çıkılmaz.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from modules.bbd_canteen_api.backend.client import CanteenApi, CanteenError

Handler = Callable[[httpx.Request], httpx.Response]


class _Secrets:
    async def get(self, key: str) -> str | None:
        return "cihaz-token" if key == "canteen.device_token" else None

    async def set(self, key: str, value: str) -> None:  # pragma: no cover
        raise AssertionError("token kasada hazır; kayıt yapılmamalı")


class _Log:
    def info(self, *_a: Any, **_k: Any) -> None:
        pass

    def warning(self, *_a: Any, **_k: Any) -> None:
        pass


def _client(handler: Handler, calls: list[httpx.Request] | None = None) -> CanteenApi:
    def record(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        assert request.headers["Authorization"] == "Bearer cihaz-token"
        return handler(request)

    return CanteenApi(base_url="https://kantin.invalid", device_name="km-test",
                      secrets=_Secrets(), log=_Log(), transport=httpx.MockTransport(record))


def _body(request: httpx.Request) -> dict[str, Any]:
    return dict(json.loads(request.content or b"{}"))


STUDENT = {
    "id": "STU-ABCDEFGHIJ",
    "displayName": "Elif Kaya",
    "parentPhone": "05329876543",
    "balance": 4250,
    "spendingLimit": 20000,
    "isBlocked": False,
    "updatedAt": 1760250000000,
    "accessCode": "483920",
    "accessCodeIssuedAt": 1760000000000,
    "dailyLimit": 15000,
    "guardianBlocked": True,
    "restrictedProductIds": [17, 3, 3],
    "controlsUpdatedAt": 1791786600000,
    "spentToday": 2500,
}


# ------------------------------------------------------------- get_student


async def test_get_student_kontrolleri_tasir_kodu_dusurur() -> None:
    calls: list[httpx.Request] = []
    api = _client(lambda r: httpx.Response(200, json={"data": STUDENT}), calls)
    student = await api.get_student("STU-ABCDEFGHIJ")

    assert calls[0].method == "GET"
    assert calls[0].url.path == "/api/students/STU-ABCDEFGHIJ"
    assert "accessCode" not in student and "accessCodeIssuedAt" not in student
    assert student["dailyLimit"] == 15000
    assert student["guardianBlocked"] is True
    assert student["restrictedProductIds"] == [3, 17]
    assert student["spentToday"] == 2500
    assert student["controlsUpdatedAt"] == "2026-10-12T09:30:00+03:00"


async def test_get_student_eski_kantinde_varsayilanlar() -> None:
    eski = {key: STUDENT[key] for key in ("id", "displayName", "balance", "spendingLimit",
                                          "isBlocked")}
    api = _client(lambda r: httpx.Response(200, json={"data": eski}))
    student = await api.get_student("STU-ABCDEFGHIJ")
    assert student["dailyLimit"] is None
    assert student["guardianBlocked"] is False
    assert student["restrictedProductIds"] == []
    assert student["spentToday"] is None          # bilinmiyor ≠ 0
    assert student["controlsUpdatedAt"] is None


async def test_get_student_404_student_not_found() -> None:
    api = _client(lambda r: httpx.Response(404, json={"message": "No query results"}))
    with pytest.raises(CanteenError) as error:
        await api.get_student("STU-YOK")
    assert error.value.reason == "student_not_found"
    assert error.value.status == 404


# --------------------------------------------------------- update_controls


async def test_update_controls_yalniz_gonderilen_alan_gider() -> None:
    calls: list[httpx.Request] = []
    yanit = {**STUDENT, "dailyLimit": None, "restrictedProductIds": [3]}
    api = _client(lambda r: httpx.Response(200, json={"data": yanit}), calls)

    result = await api.update_controls("STU-ABCDEFGHIJ",
                                       {"dailyLimit": None, "restrictedProductIds": [3, 3]})

    assert calls[0].method == "PATCH"
    assert calls[0].url.path == "/api/students/STU-ABCDEFGHIJ/controls"
    assert _body(calls[0]) == {"dailyLimit": None, "restrictedProductIds": [3]}
    assert result["dailyLimit"] is None
    assert "accessCode" not in result


@pytest.mark.parametrize("patch", [
    {},
    {"displayName": "x"},
    {"spendingLimit": -1},
    {"dailyLimit": 100_000_001},
    {"dailyLimit": "100"},
    {"isBlocked": 1},
    {"restrictedProductIds": [0]},
    {"restrictedProductIds": ["3"]},
    {"restrictedProductIds": list(range(1, 202))},
])
async def test_update_controls_gecersiz_yama_aga_cikmaz(patch: dict[str, Any]) -> None:
    calls: list[httpx.Request] = []
    api = _client(lambda r: httpx.Response(200, json={"data": STUDENT}), calls)
    with pytest.raises(CanteenError) as error:
        await api.update_controls("STU-ABCDEFGHIJ", patch)
    assert error.value.reason == "validation_error"
    assert calls == []


async def test_update_controls_kantin_dogrulamasi_validation_error() -> None:
    api = _client(lambda r: httpx.Response(
        422, json={"message": "The selected product is invalid.",
                   "errors": {"restrictedProductIds.0": ["invalid"]}}))
    with pytest.raises(CanteenError) as error:
        await api.update_controls("STU-ABCDEFGHIJ", {"restrictedProductIds": [999]})
    assert error.value.reason == "validation_error"


async def test_update_controls_404() -> None:
    api = _client(lambda r: httpx.Response(404, json={"message": "yok"}))
    with pytest.raises(CanteenError) as error:
        await api.update_controls("STU-YOK", {"isBlocked": True})
    assert error.value.reason == "student_not_found"


# ---------------------------------------------------- create_payment_request


async def test_odeme_talebi_tutarsiz_bugunku_davranis() -> None:
    calls: list[httpx.Request] = []
    api = _client(lambda r: httpx.Response(
        200, json={"queued": True, "ref": "PR-01J", "amount": 4250}), calls)
    result = await api.create_payment_request("STU-ABCDEFGHIJ")
    assert _body(calls[0]) == {"studentId": "STU-ABCDEFGHIJ", "origin": "api"}
    assert result["ref"] == "PR-01J"
    assert result["amount"] == 4250
    assert result["status"] == "PENDING"
    assert result["amountFixed"] is False
    assert result["payToken"] is None and result["linkUrl"] is None


async def test_odeme_talebi_serbest_tutar_paytoken() -> None:
    calls: list[httpx.Request] = []
    api = _client(lambda r: httpx.Response(200, json={
        "queued": True, "ref": "PR-02", "amount": 5000, "status": "PENDING",
        "amountFixed": True, "linkUrl": "https://ode.invalid/p/AB", "payToken": "tok",
    }), calls)
    result = await api.create_payment_request("STU-ABCDEFGHIJ", amount=5000, origin="admin")
    assert _body(calls[0]) == {"studentId": "STU-ABCDEFGHIJ", "origin": "admin", "amount": 5000}
    assert result["amountFixed"] is True
    assert result["payToken"] == "tok"
    assert result["linkUrl"] == "https://ode.invalid/p/AB"


@pytest.mark.parametrize(("response", "reason"), [
    (httpx.Response(200, json={"queued": False, "reason": "no_debt"}), "no_debt"),
    (httpx.Response(200, json={"queued": False, "reason": "no_phone"}), "no_phone"),
    (httpx.Response(200, json={"queued": False, "reason": "student_not_found"}),
     "student_not_found"),
    (httpx.Response(422, json={"reason": "exceeds_debt", "message": "fazla"}), "exceeds_debt"),
    (httpx.Response(422, json={"message": "x", "errors": {"amount": ["min"]}}),
     "validation_error"),
])
async def test_odeme_talebi_redleri_hata_olur(response: httpx.Response, reason: str) -> None:
    api = _client(lambda r: response)
    with pytest.raises(CanteenError) as error:
        await api.create_payment_request("STU-ABCDEFGHIJ", amount=5000)
    assert error.value.reason == reason


@pytest.mark.parametrize("amount", [0, 99, -5, True])
async def test_odeme_talebi_gecersiz_tutar_aga_cikmaz(amount: Any) -> None:
    calls: list[httpx.Request] = []
    api = _client(lambda r: httpx.Response(200, json={}), calls)
    with pytest.raises(CanteenError) as error:
        await api.create_payment_request("STU-ABCDEFGHIJ", amount=amount)
    assert error.value.reason == "invalid_amount"
    assert calls == []


# --------------------------------------------------------- record_collection


async def test_tahsilat_idempotent_anahtarla_gider() -> None:
    calls: list[httpx.Request] = []
    api = _client(lambda r: httpx.Response(200, json={
        "localId": "u-1", "amount": 1000, "balance": 3250, "smsQueued": True}), calls)
    result = await api.record_collection("STU-ABCDEFGHIJ", 1000, "u-1")
    assert calls[0].url.path == "/api/students/STU-ABCDEFGHIJ/collections"
    assert _body(calls[0]) == {"localId": "u-1", "amount": 1000}
    assert result == {"localId": "u-1", "amount": 1000, "balance": 3250, "smsQueued": True}


@pytest.mark.parametrize(("status", "body", "reason"), [
    (422, {"reason": "no_debt", "balance": 0}, "no_debt"),
    (422, {"reason": "exceeds_debt", "balance": 100}, "exceeds_debt"),
    (422, {"reason": "invalid_amount"}, "invalid_amount"),
    (404, {"reason": "student_not_found"}, "student_not_found"),
    (404, {"message": "yok"}, "student_not_found"),
])
async def test_tahsilat_redleri(status: int, body: dict[str, Any], reason: str) -> None:
    api = _client(lambda r: httpx.Response(status, json=body))
    with pytest.raises(CanteenError) as error:
        await api.record_collection("STU-ABCDEFGHIJ", 1000, "u-1")
    assert error.value.reason == reason


async def test_tahsilat_gecersiz_tutar_aga_cikmaz() -> None:
    calls: list[httpx.Request] = []
    api = _client(lambda r: httpx.Response(200, json={}), calls)
    with pytest.raises(CanteenError) as error:
        await api.record_collection("STU-ABCDEFGHIJ", 0, "u-1")
    assert error.value.reason == "invalid_amount"
    assert calls == []


# ------------------------------------------------------------------ akışlar


async def test_odeme_talebi_akisi_parametreleri_ve_iso() -> None:
    calls: list[httpx.Request] = []
    api = _client(lambda r: httpx.Response(200, json={"data": [{
        "ref": "PR-1", "studentId": "STU-1", "studentName": "Elif", "amount": 4250,
        "status": "PAID", "amountFixed": False, "linkUrl": "https://ode.invalid/p/X",
        "payToken": "t", "origin": "bulk", "createdAt": 1791786600000,
        "updatedAt": "2026-10-12T09:31:00+03:00", "paidAt": None,
    }]}), calls)
    rows = await api.payment_requests_feed("2026-10-12T09:00:00+03:00", limit=10_000)
    params = dict(calls[0].url.params)
    assert params == {"limit": "500", "updatedSince": "2026-10-12T09:00:00+03:00"}
    assert rows[0]["origin"] == "bulk" and rows[0]["payToken"] == "t"
    assert rows[0]["createdAt"] == "2026-10-12T09:30:00+03:00"
    assert rows[0]["updatedAt"] == "2026-10-12T09:31:00+03:00"
    assert rows[0]["paidAt"] is None
    assert "studentName" not in rows[0]


async def test_veli_bildirim_akisi_metin_ve_telefon_tasimaz() -> None:
    calls: list[httpx.Request] = []
    api = _client(lambda r: httpx.Response(200, json={"data": [{
        "id": 7, "studentId": "STU-1", "kind": "payment_request", "amount": 4250,
        "status": "SENT", "createdAt": 1791786600000,
        "to": "5329876543", "text": "Sayın veli…", "phone": "5329876543",
    }]}), calls)
    rows = await api.parent_notifications_feed(None, limit=50)
    # Akış kipi yalnız `updatedSince` ile açılır: imleç yoksa başlangıç gider.
    assert dict(calls[0].url.params) == {"limit": "50", "updatedSince": "1970-01-01T00:00:00Z"}
    assert rows == [{"id": 7, "studentId": "STU-1", "kind": "payment_request",
                     "status": "SENT", "amount": 4250,
                     "createdAt": "2026-10-12T09:30:00+03:00"}]


async def test_kantin_ayari_yalniz_on_odeme() -> None:
    api = _client(lambda r: httpx.Response(200, json={"data": {
        "netgsmUsercode": "gizli", "allowPrepayment": True, "maxPrepayment": 20000}}))
    assert await api.canteen_settings_public() == {"allowPrepayment": True,
                                                   "maxPrepayment": 20000}


async def test_kantin_ayari_eski_surumde_kapali() -> None:
    api = _client(lambda r: httpx.Response(200, json={"data": {"netgsmUsercode": "x"}}))
    assert await api.canteen_settings_public() == {"allowPrepayment": False,
                                                   "maxPrepayment": 0}


async def test_urunler_listesi() -> None:
    api = _client(lambda r: httpx.Response(200, json={"data": [
        {"id": 3, "name": "Tost", "price": 2500, "isActive": True}]}))
    assert (await api.products())[0]["name"] == "Tost"


async def test_akis_sayfasi_imlec_tasir() -> None:
    api = _client(lambda r: httpx.Response(200, json={
        "data": [{"id": 9, "studentId": "STU-1", "kind": "collection", "status": "SENT",
                  "amount": 1000, "ref": None, "createdAt": 1791786600000,
                  "updatedAt": 1791786600000}],
        "nextSince": "2026-10-12T06:30:00+00:00", "hasMore": True}))
    page = await api.parent_notifications_feed_page("2026-10-12T00:00:00+03:00", 1)
    assert page["nextSince"] == "2026-10-12T09:30:00+03:00"
    assert page["hasMore"] is True
    assert page["items"][0]["updatedAt"] == "2026-10-12T09:30:00+03:00"


async def test_tekil_talep_taze_token() -> None:
    calls: list[httpx.Request] = []
    api = _client(lambda r: httpx.Response(200, json={"data": {
        "ref": "PR-9", "studentId": "STU-1", "amount": 4250, "status": "PENDING",
        "payToken": "taze", "createdAt": 1791786600000}}), calls)
    row = await api.payment_request("PR-9")
    assert calls[0].url.path == "/api/payment-requests/PR-9"
    assert row["payToken"] == "taze"
    assert row["createdAt"] == "2026-10-12T09:30:00+03:00"

    missing = _client(lambda r: httpx.Response(404, json={"reason": "not_found"}))
    with pytest.raises(CanteenError) as error:
        await missing.payment_request("PR-YOK")
    assert error.value.reason == "not_found"


async def test_borc_asimi_azami_tutari_tasir() -> None:
    api = _client(lambda r: httpx.Response(
        422, json={"queued": False, "reason": "exceeds_debt", "balance": 4250,
                   "maxAmount": 4250}))
    with pytest.raises(CanteenError) as error:
        await api.create_payment_request("STU-1", amount=9000)
    assert error.value.reason == "exceeds_debt"
    assert error.value.payload["maxAmount"] == 4250

    collect = _client(lambda r: httpx.Response(
        422, json={"reason": "exceeds_debt", "balance": 100, "maxAmount": 100}))
    with pytest.raises(CanteenError) as error:
        await collect.record_collection("STU-1", 500, "u-2")
    assert error.value.payload == {"reason": "exceeds_debt", "balance": 100, "maxAmount": 100}


async def test_bilinmeyen_kaynak_aga_cikmaz() -> None:
    calls: list[httpx.Request] = []
    api = _client(lambda r: httpx.Response(200, json={}), calls)
    with pytest.raises(CanteenError) as error:
        await api.create_payment_request("STU-1", origin="kiosk")
    assert error.value.reason == "validation_error" and calls == []
