"""`canteen.api` — uygulama içi ödemenin web dönüşü (`ret`) kantine iletilir.

Kantin (ozellik/odeme-donus): `POST /api/payment-requests` gövdesinde ve
`GET /api/payment-requests[/{ref}]?ret=` sorgusunda `ret` kabul eder; onu
`payToken`'a yalnız `PAYMENT_APP_RETURN_URLS` listesindekiyle birebir aynıysa
koyar. KM yalnız biçimi süzer (https, boşluksuz, ≤ 512) ve iletir.
Kantin `httpx.MockTransport` ile taklit edilir; ağa çıkılmaz.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from modules.bbd_canteen_api.backend.client import CanteenApi, return_url

RET = "https://yeni.benimbasaridunyam.com.tr/payment-result"


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


def _client(calls: list[httpx.Request]) -> CanteenApi:
    row = {"ref": "PR-01J", "studentId": "STU-1", "amount": 4250, "status": "PENDING",
           "payToken": "tok", "createdAt": 1760250000000}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"data": {**row, "queued": True}})
        if request.url.path.endswith("/PR-01J"):
            return httpx.Response(200, json={"data": row})
        return httpx.Response(200, json={"data": [row], "nextCursor": None, "hasMore": False})

    return CanteenApi(base_url="https://kantin.invalid", device_name="km-test",
                      secrets=_Secrets(), log=_Log(), transport=httpx.MockTransport(handler))


def test_return_url_bicim_suzgeci() -> None:
    assert return_url(f"  {RET} ") == RET
    for bad in (None, "", "http://yeni.benimbasaridunyam.com.tr/payment-result",
                "javascript:alert(1)", "https://a b", "https://" + "x" * 600):
        assert return_url(bad) is None


async def test_ret_kantin_isteklerine_iletilir() -> None:
    calls: list[httpx.Request] = []
    api = _client(calls)
    await api.create_payment_request("STU-1", 5000, origin="api", ret=RET)
    await api.payment_requests("STU-1", ret=RET)
    await api.payment_request("PR-01J", ret=RET)
    # Biçimi bozuk ret iletilmez; ret'siz çağrı eskisi gibi.
    await api.create_payment_request("STU-1", origin="api", ret="http://kotu.example/x")
    await api.payment_requests("STU-1")

    created, listed, single, bad, plain = calls
    assert json.loads(created.content) == {"studentId": "STU-1", "origin": "api", "amount": 5000, "ret": RET}
    assert listed.url.params["ret"] == RET and listed.url.params["studentId"] == "STU-1"
    assert single.url.params["ret"] == RET
    assert "ret" not in json.loads(bad.content)
    assert "ret" not in plain.url.params
