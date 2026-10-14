"""Uygulama içi ödemenin web dönüşü (`ret`) — Akademi → geçit → kantin.

Akademi web istemcisinin kökeni + `/payment-result` adresini (`ret`) gönderir;
geçit onu kantinin ödeme talebi isteğine iletir ve kantin `payToken`'a koyar
(yalnız `PAYMENT_APP_RETURN_URLS` listesindekiyle birebir aynıysa; değilse
kantinin varsayılanı). `ret` verilmezse istek eskisi gibidir.
"""

from __future__ import annotations

from typing import Any

import httpx
from mobile_gateway_fakes import ELIF

RET = "https://yeni.benimbasaridunyam.com.tr/payment-result"
World = Any


async def test_talep_acarken_ret_kantine_iletilir(client: httpx.AsyncClient, world: World) -> None:
    created = await client.post(f"/students/{ELIF}/canteen/payment-requests", json={"amount": 5000, "ret": RET})
    assert created.status_code == 201 and created.json()["payToken"] == "tok.new"
    plain = await client.post(f"/students/{ELIF}/payment-requests")
    assert plain.status_code == 201
    assert world.canteen.rets == [("create", RET), ("create", None)]
    too_long = await client.post(f"/students/{ELIF}/payment-requests", json={"ret": "https://" + "x" * 600})
    assert too_long.status_code == 422


async def test_liste_ret_ile_taze_token(client: httpx.AsyncClient, world: World) -> None:
    listed = await client.get(f"/students/{ELIF}/payment-requests", params={"ret": RET})
    assert listed.status_code == 200 and listed.json()["items"]
    await client.get(f"/students/{ELIF}/payment-requests")
    assert world.canteen.rets == [("list", RET), ("list", None)]
