"""`canteen.api` — imleçli sayfa, öğrenci süzgeci ve hesap dökümü (`ledger`).

Sözleşme: bbd-akademi `docs/integrations/kantin-veli-kontrolleri.md` §1.7.
Kantin `httpx.MockTransport` ile taklit edilir; ağa çıkılmaz. Her uç hem yeni
(§1.7) hem eski kantin yanıtıyla sınanır: eski kantin `studentId`/`cursor`
parametrelerini yok sayar, `nextCursor` döndürmez, defter ucu 404 verir.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from modules.bbd_canteen_api.backend.client import (
    LEDGER_RETRY_SECONDS,
    CanteenApi,
    CanteenError,
)

Handler = Callable[[httpx.Request], httpx.Response]

#: Saat ve bekleme bu modülde taklit edilir.
CLIENT = "modules.bbd_canteen_api.backend.client"

ELIF = "STU-ABCDEFGHIJ"
CAN = "STU-KLMNOPQRST"


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


def _client(handler: Handler, calls: list[httpx.Request]) -> CanteenApi:
    def record(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    return CanteenApi(base_url="https://kantin.invalid", device_name="km-test",
                      secrets=_Secrets(), log=_Log(), transport=httpx.MockTransport(record))


def _params(request: httpx.Request) -> dict[str, str]:
    return dict(request.url.params)


SALE_ELIF = {"serverId": 11, "studentId": ELIF, "studentName": "Elif", "total": 2500,
             "createdAt": 1791722460000, "reversedAt": None, "items": []}
SALE_CAN = {**SALE_ELIF, "serverId": 12, "studentId": CAN}


# ------------------------------------------------------------- işlemler


async def test_transactions_eski_bicimi_korur_ve_suzer() -> None:
    calls: list[httpx.Request] = []
    # Eski kantin: süzgeci yok sayar, `nextCursor` döndürmez.
    api = _client(lambda r: httpx.Response(200, json={"data": [SALE_ELIF, SALE_CAN]}), calls)
    rows = await api.transactions(from_ms=1, to_ms=2, limit=9000, student_id=ELIF)

    assert rows == [SALE_ELIF]                         # düz liste, başkasınınki düştü
    assert _params(calls[0]) == {"from": "1", "to": "2", "limit": "5000",
                                 "studentId": ELIF}    # limit kantin sınırına kırpıldı
    assert "cursor" not in _params(calls[0])


async def test_transactions_page_imleci_tasir() -> None:
    calls: list[httpx.Request] = []
    api = _client(lambda r: httpx.Response(200, json={
        "data": [SALE_ELIF], "nextCursor": "eyJpZCI6MTF9", "hasMore": True}), calls)
    page = await api.transactions_page(student_id=ELIF, limit=1, cursor="eyJpZCI6OTl9")

    assert page == {"data": [SALE_ELIF], "nextCursor": "eyJpZCI6MTF9", "hasMore": True}
    assert _params(calls[0]) == {"studentId": ELIF, "limit": "1", "cursor": "eyJpZCI6OTl9"}


async def test_transactions_page_son_sayfa() -> None:
    calls: list[httpx.Request] = []
    api = _client(lambda r: httpx.Response(200, json={
        "data": [SALE_ELIF], "nextCursor": None, "hasMore": False}), calls)
    page = await api.transactions_page(student_id=ELIF)
    assert page == {"data": [SALE_ELIF], "nextCursor": None, "hasMore": False}


async def test_transactions_page_eski_kantinde_tek_sayfa_ve_cift_satir_yok() -> None:
    calls: list[httpx.Request] = []
    api = _client(lambda r: httpx.Response(200, json={"data": [SALE_ELIF, SALE_CAN]}), calls)

    first = await api.transactions_page()
    assert first == {"data": [SALE_ELIF, SALE_CAN], "nextCursor": None, "hasMore": False}
    # Eski kantin imleci yok sayıp ilk sayfayı yeniden verirdi: boş sayfa dönülür.
    again = await api.transactions_page(cursor="eyJpZCI6MTF9")
    assert again == {"data": [], "nextCursor": None, "hasMore": False}


async def test_bozuk_imlec_dogrulama_hatasidir() -> None:
    calls: list[httpx.Request] = []
    api = _client(lambda r: httpx.Response(422, json={
        "message": "Geçersiz sayfa imleci.", "errors": {"cursor": ["Geçersiz"]}}), calls)
    with pytest.raises(CanteenError) as caught:
        await api.transactions_page(cursor="bozuk")
    assert caught.value.reason == "validation_error"


# --------------------------------------------------------- ödeme talepleri


PR_ELIF = {"ref": "PR-1", "studentId": ELIF, "studentName": "Elif", "amount": 100,
           "status": "PENDING", "createdAt": 1791784800000}
PR_CAN = {**PR_ELIF, "ref": "PR-2", "studentId": CAN}


async def test_payment_requests_parametresiz_cagri_aynen() -> None:
    calls: list[httpx.Request] = []
    api = _client(lambda r: httpx.Response(200, json={"data": [PR_ELIF, PR_CAN]}), calls)
    rows = await api.payment_requests()
    assert rows == [PR_ELIF, PR_CAN]
    assert calls[0].url.query == b""          # liste kipi: hiçbir parametre gitmez


async def test_payment_requests_ogrenci_suzgeci_eski_kantinde_de() -> None:
    calls: list[httpx.Request] = []
    api = _client(lambda r: httpx.Response(200, json={"data": [PR_ELIF, PR_CAN]}), calls)
    assert await api.payment_requests(student_id=CAN) == [PR_CAN]
    assert _params(calls[0]) == {"studentId": CAN}


async def test_payment_requests_page_imlec_ve_sinir() -> None:
    calls: list[httpx.Request] = []
    api = _client(lambda r: httpx.Response(200, json={
        "data": [PR_ELIF], "nextCursor": "c2", "hasMore": True}), calls)
    page = await api.payment_requests_page(ELIF, limit=900, cursor="c1")
    assert page == {"data": [PR_ELIF], "nextCursor": "c2", "hasMore": True}
    assert _params(calls[0]) == {"studentId": ELIF, "limit": "500", "cursor": "c1"}


async def test_payment_requests_feed_ogrenci_suzgeci() -> None:
    calls: list[httpx.Request] = []
    api = _client(lambda r: httpx.Response(200, json={
        "data": [PR_ELIF, PR_CAN], "nextSince": "2026-10-12T06:00:00+00:00",
        "hasMore": False}), calls)
    page = await api.payment_requests_feed("2026-10-01T00:00:00Z", limit=50, student_id=ELIF)

    assert _params(calls[0]) == {"updatedSince": "2026-10-01T00:00:00Z", "limit": "50",
                                 "studentId": ELIF}
    assert [row["ref"] for row in page["data"]] == ["PR-1"]   # eski kantine karşı süzüldü
    assert page["nextSince"] == "2026-10-12T09:00:00+03:00"

    # Süzgeçsiz çağrı bugünkü gibi: `studentId` gitmez.
    await api.payment_requests_feed()
    assert "studentId" not in _params(calls[1])


# ------------------------------------------------------------- hesap dökümü


LEDGER_ROWS = [
    {"id": 905, "at": 1791784800000, "kind": "sale", "direction": "debit", "amount": 2500,
     "balanceAfter": 4250, "ref": "l-1",
     "sale": {"serverId": 11, "localId": "l-1", "total": 2500, "createdAt": 1791784790000,
              "reversedAt": None, "reversedReason": None,
              "items": [{"productId": 3, "name": "Tost", "qty": 1, "unitPrice": 2500,
                         "lineTotal": 2500}]}},
    {"id": 904, "at": 1791700000000, "kind": "cash", "direction": "credit", "amount": 5000,
     "balanceAfter": 1750, "ref": "col-1", "sale": None},
]
LEDGER_FIRST = {
    "data": LEDGER_ROWS, "nextCursor": "eyJpZCI6OTA0fQ", "hasMore": True,
    "student": {"id": ELIF, "displayName": "Elif Kaya", "balance": 4250,
                "spendingLimit": 20000, "dailyLimit": None, "spentToday": 2500,
                "isBlocked": False, "guardianBlocked": False, "restrictedProductIds": [17, 3],
                "controlsUpdatedAt": "2026-10-12T06:30:00+00:00", "accessCode": "483920"},
    "pendingPaymentRequest": {"ref": "PR-9", "studentId": ELIF, "amount": 4250,
                              "status": "PENDING", "amountFixed": False, "origin": "admin",
                              "linkUrl": "https://ode/p/X", "payToken": "tok",
                              "createdAt": 1791784800000, "updatedAt": 1791784800000,
                              "paidAt": None},
}


async def test_ledger_ilk_sayfa() -> None:
    calls: list[httpx.Request] = []
    api = _client(lambda r: httpx.Response(200, json=LEDGER_FIRST), calls)
    page = await api.ledger(ELIF, limit=500, from_ms=1790000000000)

    assert calls[0].url.path == f"/api/students/{ELIF}/ledger"
    assert _params(calls[0]) == {"limit": "200", "from": "1790000000000"}
    assert page["data"] == LEDGER_ROWS
    assert (page["nextCursor"], page["hasMore"]) == ("eyJpZCI6OTA0fQ", True)
    student = page["student"]
    assert "accessCode" not in student
    assert (student["balance"], student["spentToday"], student["restrictedProductIds"]) == \
        (4250, 2500, [3, 17])
    assert student["controlsUpdatedAt"] == "2026-10-12T09:30:00+03:00"
    pending = page["pendingPaymentRequest"]
    assert pending["payToken"] == "tok" and pending["createdAt"] == "2026-10-12T09:00:00+03:00"


async def test_ledger_sonraki_sayfa_ogrenci_tasimaz() -> None:
    calls: list[httpx.Request] = []
    api = _client(lambda r: httpx.Response(200, json={
        "data": LEDGER_ROWS[1:], "nextCursor": None, "hasMore": False}), calls)
    page = await api.ledger(ELIF, cursor="eyJpZCI6OTA0fQ", to_ms=1791790000000)

    assert _params(calls[0]) == {"limit": "50", "cursor": "eyJpZCI6OTA0fQ",
                                 "to": "1791790000000"}
    assert page == {"data": LEDGER_ROWS[1:], "nextCursor": None, "hasMore": False}
    assert "student" not in page and "pendingPaymentRequest" not in page


async def test_ledger_bekleyen_talep_yoksa_none() -> None:
    calls: list[httpx.Request] = []
    api = _client(lambda r: httpx.Response(200, json={
        **LEDGER_FIRST, "pendingPaymentRequest": None}), calls)
    page = await api.ledger(ELIF)
    assert page["pendingPaymentRequest"] is None


async def test_ledger_ogrenci_yok() -> None:
    calls: list[httpx.Request] = []
    api = _client(lambda r: httpx.Response(404, json={
        "message": "Öğrenci bulunamadı.", "reason": "student_not_found"}), calls)
    for _ in range(2):
        with pytest.raises(CanteenError) as caught:
            await api.ledger("STU-YOK")
        assert (caught.value.status, caught.value.reason) == (404, "student_not_found")
    assert len(calls) == 2          # uç VAR: "uç yok" diye hatırlanmadı


async def test_ledger_kimligi_yol_icin_kodlanir() -> None:
    calls: list[httpx.Request] = []
    api = _client(lambda r: httpx.Response(200, json={"data": []}), calls)
    await api.ledger("STU-a/../b")
    assert calls[0].url.raw_path.startswith(b"/api/students/STU-a%2F..%2Fb/ledger")


async def test_ledger_eski_kantinde_desteklenmiyor_ve_hatirlanir(
        monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[httpx.Request] = []
    # Laravel'in "route bulunamadı" yanıtı: gerekçesiz 404.
    api = _client(lambda r: httpx.Response(404, json={
        "message": "The route api/students/STU-ABCDEFGHIJ/ledger could not be found."}), calls)
    clock = {"now": 1000.0}
    monkeypatch.setattr(f"{CLIENT}.time.monotonic", lambda: clock["now"])

    with pytest.raises(CanteenError) as caught:
        await api.ledger(ELIF)
    assert (caught.value.status, caught.value.reason) == (404, "ledger_unsupported")
    assert len(calls) == 1

    # Hatırlanır: kantine gidilmeden aynı red.
    clock["now"] += LEDGER_RETRY_SECONDS - 1
    with pytest.raises(CanteenError) as again:
        await api.ledger(CAN)
    assert again.value.reason == "ledger_unsupported" and len(calls) == 1

    # Süre dolunca yeniden denenir (kantin güncellenmiş olabilir).
    clock["now"] += 2
    with pytest.raises(CanteenError):
        await api.ledger(ELIF)
    assert len(calls) == 2


async def test_ledger_kantin_guncellenince_yeni_uc_kullanilir(
        monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[httpx.Request] = []
    state = {"new": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["new"]:
            return httpx.Response(200, json=LEDGER_FIRST)
        return httpx.Response(404, json={"message": "not found"})

    api = _client(handler, calls)
    clock = {"now": 50.0}
    monkeypatch.setattr(f"{CLIENT}.time.monotonic", lambda: clock["now"])
    with pytest.raises(CanteenError):
        await api.ledger(ELIF)
    state["new"] = True
    clock["now"] += LEDGER_RETRY_SECONDS + 1
    assert (await api.ledger(ELIF))["student"]["id"] == ELIF


async def test_ledger_gecici_hata_ucu_yok_saymaz(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[httpx.Request] = []
    api = _client(lambda r: httpx.Response(503, json={"message": "bakım"}), calls)

    async def no_wait(_: float) -> None:
        return None

    monkeypatch.setattr(f"{CLIENT}.asyncio.sleep", no_wait)  # GET üç kez denenir
    with pytest.raises(CanteenError) as caught:
        await api.ledger(ELIF)
    assert caught.value.status == 503 and caught.value.reason is None
    assert api._ledger_missing_until == 0.0


async def test_ledger_bozuk_yanit() -> None:
    calls: list[httpx.Request] = []
    api = _client(lambda r: httpx.Response(200, json={"rows": []}), calls)
    with pytest.raises(CanteenError):
        await api.ledger(ELIF)
