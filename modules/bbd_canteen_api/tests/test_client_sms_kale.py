"""sms-kale: kantin istemcisinin OTP amacı, teslim raporu ve bakiye çağrıları."""

from __future__ import annotations

from typing import Any

import pytest
from modules.bbd_canteen_api.backend.client import CanteenApi


class _Secrets:
    async def get(self, key: str) -> str | None:
        return None

    async def set(self, key: str, value: str) -> None:
        raise AssertionError("istek yardımcısı taklit edilmeli")


class _Log:
    def info(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def warning(self, *_args: Any, **_kwargs: Any) -> None:
        pass


def _client(monkeypatch: pytest.MonkeyPatch, reply: Any,
            calls: list[tuple[str, str, dict[str, Any]]]) -> CanteenApi:
    client = CanteenApi(base_url="https://canteen.invalid", device_name="km-test",
                        secrets=_Secrets(), log=_Log())

    async def request(method: str, path: str, **kwargs: Any) -> Any:
        calls.append((method, path, kwargs))
        return reply

    monkeypatch.setattr(client, "_request", request)
    return client


@pytest.mark.asyncio
async def test_send_sms_otp_purpose_is_sent_only_when_given(
        monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []
    client = _client(monkeypatch, {"sent": True, "jobId": "J1", "channel": "otp"}, calls)

    result = await client.send_sms(phone="5321112233", message="Kod: 1", purpose="otp")
    await client.send_sms(phone="5321112233", message="Merhaba")

    assert result == {"sent": True, "jobId": "J1", "channel": "otp"}
    assert calls[0][2]["json"]["purpose"] == "otp"
    assert "purpose" not in calls[1][2]["json"]


@pytest.mark.asyncio
async def test_sms_report_quotes_job_id_and_unwraps(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []
    client = _client(monkeypatch, {"data": {"jobId": "J/1", "status": "delivered"}}, calls)

    assert (await client.sms_report("J/1"))["status"] == "delivered"
    assert calls == [("GET", "/api/sms/report/J%2F1", {})]


@pytest.mark.asyncio
async def test_sms_balance_unwraps_and_tolerates_odd_body(
        monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []
    client = _client(monkeypatch, {"data": {"ok": True, "smsCount": 6515}}, calls)
    assert await client.sms_balance() == {"ok": True, "smsCount": 6515}

    odd = _client(monkeypatch, ["?"], calls)
    assert await odd.sms_balance() == {}
