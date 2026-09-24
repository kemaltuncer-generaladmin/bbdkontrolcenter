from __future__ import annotations

from typing import Any

import pytest

from modules.bbd_canteen_api.backend.client import CanteenApi, CanteenError


class _Secrets:
    async def get(self, key: str) -> str | None:
        return None

    async def set(self, key: str, value: str) -> None:
        raise AssertionError("The request helper should be stubbed in this unit test.")


class _Log:
    def info(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def warning(self, *_args: Any, **_kwargs: Any) -> None:
        pass


def _client() -> CanteenApi:
    return CanteenApi(
        base_url="https://canteen.invalid",
        device_name="test-control-center",
        secrets=_Secrets(),
        log=_Log(),
    )


@pytest.mark.asyncio
async def test_lookup_student_by_code_posts_code_and_returns_only_minimal_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    calls: list[tuple[str, str, dict[str, Any]]] = []

    async def request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, path, kwargs))
        return {
            "data": {
                "id": "opaque-student-7",
                "displayName": "Öğrenci",
                "accessCode": "123456",
                "balance": 2500,
                "parentPhone": "5550000000",
            }
        }

    monkeypatch.setattr(client, "_request", request)

    result = await client.lookup_student_by_code("123456")

    assert calls == [
        ("POST", "/api/students/lookup-code", {"json": {"code": "123456"}})
    ]
    assert result == {"id": "opaque-student-7", "displayName": "Öğrenci"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"data": None},
        {"data": {"id": "opaque-student-7"}},
        {"data": {"id": "", "displayName": "Öğrenci"}},
        {"data": {"id": "opaque-student-7", "displayName": " "}},
    ],
)
async def test_lookup_student_by_code_rejects_malformed_response_without_echoing_code(
    monkeypatch: pytest.MonkeyPatch,
    payload: Any,
) -> None:
    client = _client()

    async def request(_method: str, _path: str, **_kwargs: Any) -> Any:
        return payload

    monkeypatch.setattr(client, "_request", request)

    with pytest.raises(CanteenError) as error:
        await client.lookup_student_by_code("123456")

    assert "123456" not in str(error.value)
