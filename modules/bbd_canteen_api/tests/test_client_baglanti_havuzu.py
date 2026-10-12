"""`canteen.api` — kalıcı bağlantı (keep-alive) ve zaman aşımları.

Eskiden her istek yeni bir `httpx.AsyncClient` açıp kapatıyordu: her kantin
çağrısı yeni TCP + TLS el sıkışmasıydı. Artık istemci bir kez kurulur ve
bağlantı havuzunda tutulur. Kantin `httpx.MockTransport` ile taklit edilir.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from modules.bbd_canteen_api.backend.client import CONNECT_TIMEOUT, CanteenApi


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


def _api(seen: list[httpx.Request], *, timeout: float = 20.0) -> CanteenApi:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": {"id": "STU-1", "balance": 0}})

    return CanteenApi(base_url="https://kantin.invalid", device_name="km-test",
                      secrets=_Secrets(), log=_Log(), timeout=timeout,
                      transport=httpx.MockTransport(handler))


async def test_istekler_TEK_istemciyi_paylasir() -> None:
    seen: list[httpx.Request] = []
    api = _api(seen)
    first = api._client()
    await api.student("STU-1")
    await api.student("STU-1")
    await api.payment_requests()
    assert api._client() is first and not first.is_closed
    assert len(seen) == 3
    await api.aclose()
    assert first.is_closed


async def test_esZamanli_istekler_de_ayni_istemciden_gecer() -> None:
    seen: list[httpx.Request] = []
    api = _api(seen)
    await asyncio.gather(*(api.student("STU-1") for _ in range(8)))
    assert len(seen) == 8
    assert api._http is not None and not api._http.is_closed
    await api.aclose()


async def test_baglanti_kurma_suresi_kisa_okuma_suresi_ayardan() -> None:
    seen: list[httpx.Request] = []
    api = _api(seen, timeout=20.0)
    await api.student("STU-1")
    timeouts = seen[0].extensions["timeout"]
    assert timeouts["connect"] == CONNECT_TIMEOUT
    assert timeouts["read"] == 20.0
    await api.aclose()


async def test_okuma_suresi_baglanti_suresinden_kisaysa_o_gecerli() -> None:
    seen: list[httpx.Request] = []
    api = _api(seen, timeout=2.0)
    await api.student("STU-1")
    assert seen[0].extensions["timeout"]["connect"] == 2.0
    await api.aclose()


def test_olay_dongusu_degisince_yeni_istemci_kurulur() -> None:
    """Eski döngünün bağlantısı yeni döngüde kullanılamaz; istemci yenilenir."""
    seen: list[httpx.Request] = []
    api = _api(seen)

    async def once() -> httpx.AsyncClient:
        await api.student("STU-1")
        return api._client()

    first = asyncio.run(once())
    second = asyncio.run(once())
    assert first is not second
    assert len(seen) == 2
