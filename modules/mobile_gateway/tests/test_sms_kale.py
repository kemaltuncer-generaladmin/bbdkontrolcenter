"""sms-kale — OTP SMS görünürlüğü, teslim durumu, günlük sınır, sayaç sıfırlama, izleme.

Sözleşme: bbd-akademi docs/integrations/km-mobile-gateway.md §7 (sms-kale eki).
"""

from __future__ import annotations

from typing import Any

import httpx
from conftest import Clock, World
from modules.mobile_gateway.backend.service import ascii_sms
from test_contract_v1 import MOTHER, expect_error, table

BODY = {"phone": MOTHER, "code": "583012", "purpose": "login"}


async def send(client: httpx.AsyncClient, **extra: Any) -> httpx.Response:
    return await client.post("/sms/otp", json={**BODY, **extra})


def test_otp_template_is_folded_to_ascii() -> None:
    assert ascii_sms("BBD Akademi giriş kodunuz: {code}. Paylaşmayın İĞÜÖÇ") == \
        "BBD Akademi giris kodunuz: {code}. Paylasmayin IGUOC"
    assert ascii_sms("kod 😀  123\n456") == "kod 123 456"


async def test_send_logs_job_and_channel(client: httpx.AsyncClient, world: World,
                                         store: Any) -> None:
    sent = await send(client)
    assert sent.json() == {"sent": True, "jobId": "1791000001", "channel": "otp"}
    row = (await table(store, "sms_log"))[-1]
    assert (row["job_id"], row["channel"], row["delivery"]) == ("1791000001", "otp", "pending")
    assert "583012" not in repr(row) and MOTHER not in repr(row)


async def test_old_canteen_without_job_id_keeps_old_reply(client: httpx.AsyncClient,
                                                          world: World) -> None:
    async def old_send_sms(*, phone: str | None = None, message: str | None = None,
                           **_: Any) -> dict[str, Any]:
        return {"sent": True, "text": message}

    world.canteen.send_sms = old_send_sms  # type: ignore[method-assign]
    sent = await send(client)
    assert sent.status_code == 200 and sent.json() == {"sent": True}


async def test_status_reads_report_once_per_minute_and_keeps_final(
        client: httpx.AsyncClient, world: World, clock: Clock, store: Any) -> None:
    job = (await send(client)).json()["jobId"]

    first = await client.get(f"/sms/otp/{job}")
    assert first.status_code == 200
    assert first.json()["status"] == "pending" and first.json()["channel"] == "otp"
    assert world.canteen.count("sms_report") == 1

    # Netgsm jobId başına dakikada bir: 30 sn sonra kantine yeniden SORULMAZ.
    clock.advance(seconds=30)
    world.canteen.sms_reports[job] = {"status": "delivered",
                                      "deliveredAt": "2026-10-12T06:30:03+00:00"}
    assert (await client.get(f"/sms/otp/{job}")).json()["status"] == "pending"
    assert world.canteen.count("sms_report") == 1

    clock.advance(seconds=30)
    done = (await client.get(f"/sms/otp/{job}")).json()
    assert done["status"] == "delivered" and done["deliverySeconds"] == 3.0
    assert done["deliveredAt"].startswith("2026-10-12T09:30:03")
    assert world.canteen.count("sms_report") == 2

    # Kesin sonuç saklanır: bir daha kantine gidilmez.
    clock.advance(minutes=5)
    assert (await client.get(f"/sms/otp/{job}")).json()["status"] == "delivered"
    assert world.canteen.count("sms_report") == 2
    row = (await table(store, "sms_log"))[-1]
    assert row["delivery"] == "delivered" and row["delivered_at"] is not None


async def test_status_failed_records_netgsm_reason(client: httpx.AsyncClient, world: World,
                                                   store: Any) -> None:
    job = (await send(client)).json()["jobId"]
    world.canteen.sms_reports[job] = {"status": "failed", "netgsmStatus": 13, "errorCode": 0}
    assert (await client.get(f"/sms/otp/{job}")).json()["status"] == "failed"
    assert (await table(store, "sms_log"))[-1]["reason"] == "netgsm_13:0"


async def test_status_survives_canteen_outage(client: httpx.AsyncClient,
                                              world: World) -> None:
    job = (await send(client)).json()["jobId"]
    world.canteen.down = True
    reply = await client.get(f"/sms/otp/{job}")
    assert reply.status_code == 200 and reply.json()["status"] == "pending"


async def test_status_unknown_or_invalid_job(client: httpx.AsyncClient) -> None:
    expect_error(await client.get("/sms/otp/999999"), 404, "sms_not_found")
    expect_error(await client.get("/sms/otp/" + "x" * 65), 422, "validation_error")
    expect_error(await client.get("/sms/otp/a.b"), 422, "validation_error")


async def test_daily_limit_ten_per_phone(client: httpx.AsyncClient, clock: Clock,
                                         store: Any) -> None:
    for index in range(10):
        assert (await send(client)).status_code == 200, index
        clock.advance(minutes=13)  # saatte en çok 5 → 13 dk arayla saat sınırı tutmaz
    limited = await send(client)
    error = expect_error(limited, 429, "rate_limited")
    assert "bugün" in error["message"]
    assert 3600 < error["retryAfterSeconds"] <= 24 * 3600
    assert (await table(store, "sms_log"))[-1]["reason"] == "phone_day"


async def test_admin_reset_clears_phone_counters_with_audit(
        client: httpx.AsyncClient, clock: Clock, store: Any) -> None:
    for _ in range(5):
        assert (await send(client)).status_code == 200
    expect_error(await send(client), 429, "rate_limited")

    reset = await client.post("/sms/limits/reset",
                              json={"phone": MOTHER, "reason": "Veli kod alamadı, destek"},
                              headers={"X-KM-On-Behalf-Of": "akademi:staff:7"})
    assert reset.status_code == 200
    assert reset.json() == {"reset": True, "clearedHour": 5, "clearedDay": 5}
    assert (await send(client)).status_code == 200

    audit = [row for row in await table(store, "audit") if row["action"] == "sms_limits_reset"]
    assert len(audit) == 1 and "532****543" in audit[0]["detail"]
    assert MOTHER not in repr(audit)
    expect_error(await client.post("/sms/limits/reset", json={"phone": MOTHER, "reason": "x"}),
                 422, "validation_error")


async def test_health_summarises_last_hour_and_balance(client: httpx.AsyncClient,
                                                       world: World, clock: Clock) -> None:
    jobs = [(await send(client)).json()["jobId"] for _ in range(3)]
    world.canteen.sms_reports[jobs[0]] = {"status": "delivered",
                                          "deliveredAt": "2026-10-12T06:30:02+00:00"}
    world.canteen.sms_reports[jobs[1]] = {"status": "failed", "netgsmStatus": 2}
    for job in jobs:
        await client.get(f"/sms/otp/{job}")
    health = (await client.get("/sms/health")).json()
    assert health["sent"] == 3 and health["delivered"] == 1
    assert health["deliveryFailed"] == 1 and health["pending"] == 1
    assert health["avgDeliverySeconds"] == 2.0
    assert health["balance"] == {"ok": True, "smsCount": 6515}

    world.canteen.down = True
    assert (await client.get("/sms/health")).json()["balance"] == {"ok": False, "smsCount": None}
