"""Duyuru SMS'i — POST /sms/send (bbd-akademi README §14 madde 81, uçtan uca denetim U23 / P14).

Akademi duyurusunda "SMS olarak da gönder": veli telefonuna serbest metin, olağan hat, ASCII; KM'nin
duyuru SMS bütçesi (son 24 saat), telefon başına günlük sınır, dakika sınırı; OTP sağlık özeti bunları saymaz.
"""

from __future__ import annotations

from typing import Any

import httpx
from conftest import World
from modules.mobile_gateway.backend.service import GatewayService, sms_segments
from test_contract_v1 import FATHER, MOTHER, expect_error, table

TEXT = "Yarın deneme sınavı 09:30'da başlıyor; öğrenciler 09:15'te sınıfta olsun."


async def send(client: httpx.AsyncClient, phone: str = MOTHER, text: str = TEXT,
               **headers: str) -> httpx.Response:
    return await client.post("/sms/send", json={"phone": phone, "text": text,
                                                "purpose": "announcement"}, headers=headers)


def test_segments() -> None:
    assert sms_segments("") == 0 and sms_segments("a" * 160) == 1
    assert sms_segments("a" * 161) == 2 and sms_segments("a" * 307) == 3
    assert sms_segments("[" * 80) == 1 and sms_segments("[" * 81) == 2


async def test_sends_ascii_on_normal_line_and_logs(client: httpx.AsyncClient, world: World,
                                                   store: Any) -> None:
    sent = await send(client)
    assert sent.status_code == 200, sent.text
    assert sent.json() == {"sent": True, "segments": 1, "jobId": "1791000001", "channel": "otp"}
    call = world.canteen.calls[-1]
    assert call[1]["message"] == "Yarin deneme sinavi 09:30'da basliyor; ogrenciler 09:15'te sinifta olsun."
    assert world.canteen.sms_purposes[-1] is None  # olağan hat (OTP hattı değil)
    row = (await table(store, "sms_log"))[-1]
    assert row["purpose"] == "announcement" and row["status"] == "sent"
    assert MOTHER not in repr(row) and "deneme" not in repr(row)
    # OTP sağlık özeti duyuru SMS'ini saymaz
    health = (await client.get("/sms/health")).json()
    assert health["sent"] == 0


async def test_idempotent_replay_sends_once(client: httpx.AsyncClient, world: World) -> None:
    first = await send(client, **{"Idempotency-Key": "ann-sms-1"})
    again = await send(client, **{"Idempotency-Key": "ann-sms-1"})
    assert first.json() == again.json() and world.canteen.count("send_sms") == 1


async def test_validation_and_disabled(client: httpx.AsyncClient, service: GatewayService) -> None:
    for bad in ({"phone": "05321234567", "text": "x", "purpose": "announcement"},
                {"phone": MOTHER, "text": "", "purpose": "announcement"},
                {"phone": MOTHER, "text": "x", "purpose": "otp"}):
        assert (await client.post("/sms/send", json=bad)).status_code == 422
    expect_error(await send(client, text="😀"), 422, "validation_error")
    expect_error(await send(client, text="ş" * 460), 422, "validation_error")
    service.text_sms_enabled = False
    expect_error(await send(client), 403, "sms_disabled")


async def test_budget_phone_and_minute_limits(client: httpx.AsyncClient, service: GatewayService,
                                              world: World) -> None:
    service.text_sms_per_phone_per_day = 2
    assert (await send(client)).status_code == 200
    assert (await send(client)).status_code == 200
    expect_error(await send(client), 429, "sms_phone_limit")
    # bütçe parça (SMS) sayar: 2 SMS'lik metin kalan 1'e sığmaz
    service.text_sms_daily_budget = 3
    expect_error(await send(client, phone=FATHER, text="a" * 200), 429, "sms_budget_exceeded")
    assert (await send(client, phone=FATHER, text="kisa")).status_code == 200
    service.text_sms_daily_budget = 100
    service.text_sms_per_minute = 3
    limited = expect_error(await send(client, phone=FATHER), 429, "rate_limited")
    assert limited["retryAfterSeconds"] >= 1
    assert world.canteen.count("send_sms") == 3


async def test_canteen_failure_is_502(client: httpx.AsyncClient, world: World) -> None:
    world.canteen.sms_sent = False
    expect_error(await send(client), 502, "sms_failed")
