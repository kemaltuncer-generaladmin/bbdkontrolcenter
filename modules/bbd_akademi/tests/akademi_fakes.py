"""Akademi testlerinin ortak taklitleri (fikstürler conftest.py içinde).

Akademi `httpx.MockTransport` ile taklit edilir: AĞA ÇIKILMAZ. Taklit her
isteği `akademi.calls` listesine yazar; yanıtı test `akademi.reply(...)` ile
belirler. Her SQL ifadesi PostgreSQL çevirmeninden de geçer — merkezde
reddedilecek bir yapı burada patlar.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

MODULE_ROOT = Path(__file__).resolve().parents[1]
REPO = MODULE_ROOT.parents[1]
for entry in (REPO / "backend" / "src", REPO):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from modules.bbd_akademi.backend.permission_map import ALL_PERMISSIONS

from km_core.contracts.module import ScopedStore
from km_core.store.db import Store
from km_core.store.dialect import to_postgres
from km_sdk import CurrentUser

MIGRATIONS = MODULE_ROOT / "backend" / "migrations"
API_KEY = "aksk_TESTPREF_" + "s" * 40
BASE = "https://akademi.test"
PIN = "482913"


class CheckedStore:
    def __init__(self, database: Store) -> None:
        self.inner = ScopedStore(database, "bbd_akademi")

    def table(self, name: str) -> str:
        return self.inner.table(name)

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        to_postgres(sql)
        return await self.inner.fetch_all(sql, tuple(params))

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        to_postgres(sql)
        return await self.inner.fetch_one(sql, tuple(params))

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        to_postgres(sql)
        await self.inner.execute(sql, tuple(params))


class Secrets:
    def __init__(self) -> None:
        self.values: dict[str, str] = {"bbd_akademi.api_key": API_KEY}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)


class Log:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str, dict[str, Any]]] = []

    def info(self, message: str, **fields: Any) -> None:
        self.lines.append(("info", message, fields))

    def warning(self, message: str, **fields: Any) -> None:
        self.lines.append(("warning", message, fields))

    def error(self, message: str, **fields: Any) -> None:
        self.lines.append(("error", message, fields))


class Akademi:
    """Taklit Akademi. Yanıt varsayılanı `200 {"ok": true}`."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.bodies: list[bytes] = []
        self._reply: Callable[[httpx.Request], httpx.Response] | None = None
        self.down: Exception | None = None

    def reply(self, fn: Callable[[httpx.Request], httpx.Response] | httpx.Response) -> None:
        self._reply = fn if callable(fn) else (lambda _request: fn)

    async def handler(self, request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        self.calls.append(request)
        self.bodies.append(body)
        if self.down is not None:
            raise self.down
        if self._reply is not None:
            return self._reply(request)
        return httpx.Response(200, json={"ok": True})

    @property
    def last(self) -> httpx.Request:
        return self.calls[-1]


def user(uid: str, first: str, last: str, *permissions: str) -> CurrentUser:
    return CurrentUser(id=uid, first_name=first, last_name=last, org_scope="bbd",
                       roles=["test"], permissions=set(permissions))


ADMIN = user("u-1", "Ayşe", "Yılmaz", *ALL_PERMISSIONS)
VIEWER = user("u-2", "Can", "Demir", "bbd_akademi.view")
CASHIER = user("u-3", "Ece", "Kaya", "bbd_akademi.view", "bbd_akademi.finance")

USERS = {"admin": ADMIN, "viewer": VIEWER, "cashier": CASHIER}


class Identity:
    """`requires(...)` ve `confirm_pin` için çekirdek kimliğinin taklidi."""

    def __init__(self) -> None:
        self.store = self
        self.audits: list[tuple[Any, ...]] = []
        self.extra: dict[str, CurrentUser] = {}

    async def resolve_session(self, token: str) -> CurrentUser | None:
        return USERS.get(token) or self.extra.get(token)

    @staticmethod
    def secret_lookup(pin: str) -> str:
        return f"h:{pin}"

    async def fetch_one(self, sql: str, params: tuple[Any, ...]) -> dict[str, str]:
        return {"secret_lookup": f"h:{PIN}"}

    async def audit(self, *args: Any, **kwargs: Any) -> None:
        self.audits.append(args)


def core_envelope(app: FastAPI) -> None:
    """km_core/http/app.py ile aynı işleyici: metin olmayan `detail` kaybolur."""

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "İstek reddedildi."
        return JSONResponse(status_code=exc.status_code,
                            content={"error": {"status": exc.status_code, "message": detail}},
                            headers=exc.headers)


def auth(who: str = "admin", **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {who}", **extra}
