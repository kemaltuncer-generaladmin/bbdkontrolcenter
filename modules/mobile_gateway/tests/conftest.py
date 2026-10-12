"""Test ortamı — gerçek SQLite depo, modülün kendi göçleri, lehçe denetimi ve
çekirdeğin GERÇEK izin kapısı.

`pytest modules/mobile_gateway` tek başına koşabilsin diye kaynak dizini ve
depo kökü sys.path'e eklenir.

- Depo taklit değildir: çekirdeğin `Store`u geçici dosyada açılır, göçler
  `apply_migration` ile uygulanır (K5 ad denetimi her koşuda çalışır) ve her
  SQL ifadesi PostgreSQL çevirmeninden geçirilir.
- Yetenekler gerçek `ModuleContext.try_capability` ile çözülür: manifestte
  `consumes` ile ilan edilmemiş bir yeteneğe uzanan kod testte patlar.
- Router, çekirdeğin `_mount_modules` yaptığı gibi `http.requires` kapısıyla
  (`_permission_guard`) monte edilir ve çekirdeğin hata zarfı işleyicisi
  kurulur; böylece 401/403 gerçek yoldan gelir.
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

MODULE_ROOT = Path(__file__).resolve().parents[1]
REPO = MODULE_ROOT.parents[1]
for entry in (REPO / "backend" / "src", REPO):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from mobile_gateway_fakes import (
    FakeBellAnnounce,
    FakeBellPlan,
    FakeCanteen,
    FakeDirectory,
    FakeGroups,
    FakeTabletControl,
    FakeTabletRead,
    FakeVault,
    RecordingLog,
    bell_week,
)
from modules.mobile_gateway.backend.api import routes
from modules.mobile_gateway.backend.caps import Capabilities
from modules.mobile_gateway.backend.service import GatewayService

from km_core.bus.bus import EventBus
from km_core.contracts.module import ModuleContext, ScopedStore
from km_core.http.app import _permission_guard
from km_core.http.deps import resolve_once
from km_core.registry.registry import Registry
from km_core.security.identity import CurrentUser
from km_core.store.db import Store
from km_core.store.dialect import to_postgres

MIGRATIONS = MODULE_ROOT / "backend" / "migrations"
MANIFEST = yaml.safe_load((MODULE_ROOT / "module.yaml").read_text(encoding="utf-8"))
DEFAULTS = yaml.safe_load((MODULE_ROOT / "config" / "default.yaml").read_text(encoding="utf-8"))
PREFIX = MANIFEST["http"]["prefix"]

SERVICE_USER = CurrentUser(id="svc-1", first_name="Akademi", last_name="Servis",
                           org_scope="org", roles=["mobile_service"],
                           permissions={"mobile_gateway.access"})
STAFF_USER = CurrentUser(id="u-2", first_name="Can", last_name="Demir", org_scope="bbd",
                         roles=["org_staff"], permissions={"bbd_students.view"})


class CheckedStore:
    """`ScopedStore` that proves every statement translates to PostgreSQL."""

    def __init__(self, database: Store) -> None:
        self.database = database
        self.inner = ScopedStore(database, "mobile_gateway")

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


class Clock:
    """2026-10-12 09:30 Europe/Istanbul unless moved."""

    def __init__(self) -> None:
        self.value = datetime(2026, 10, 12, 6, 30, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **delta: float) -> None:
        self.value += timedelta(**delta)


class Identity:
    """Çekirdek kimliğinin bu testlere yeten yüzü: oturum çözümü ve denetim."""

    def __init__(self) -> None:
        self.denied: list[tuple[Any, ...]] = []

    async def resolve_session(self, token: str) -> CurrentUser | None:
        return {"service": SERVICE_USER, "staff": STAFF_USER}.get(token)

    async def audit(self, *args: Any, **kwargs: Any) -> None:
        self.denied.append(args)


class World:
    """Bir testin bütün taklitleri ve registry'si."""

    def __init__(self) -> None:
        self.canteen = FakeCanteen()
        self.directory = FakeDirectory()
        self.tablet_read = FakeTabletRead()
        self.tablet_control = FakeTabletControl()
        self.announce = FakeBellAnnounce()
        self.vault = FakeVault()
        self.groups = FakeGroups()
        self.bell_plan = FakeBellPlan()
        self.registry = Registry()
        for name, instance in (
            ("secrets", self.vault), ("canteen.api", self.canteen),
            ("bbd_students.directory", self.directory),
            ("bbd_tablet.read", self.tablet_read),
            ("bbd_tablet.control", self.tablet_control),
            ("bell.week", bell_week), ("bell.announce", self.announce),
            ("bbd_students.groups", self.groups), ("bell.plan", self.bell_plan),
        ):
            self.registry.register(name, instance, provider="test")

    def remove(self, *names: str) -> None:
        for name in names:
            self.registry._entries.pop(name, None)

    def replace(self, name: str, instance: Any) -> None:
        self.remove(name)
        self.registry.register(name, instance, provider="test")


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[CheckedStore]:
    dsn = os.environ.get("KM_TEST_POSTGRES_DSN")
    database: Any
    if dsn:
        import asyncpg

        from km_core.store.postgres import PostgresStore

        connection = await asyncpg.connect(dsn)
        await connection.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        await connection.close()
        database = PostgresStore(dsn)
    else:
        database = Store(tmp_path / "gateway.sqlite")
    await database.open()
    for migration in sorted(MIGRATIONS.glob("*.sql")):
        await database.apply_migration("mobile_gateway", migration.name,
                                       migration.read_text(encoding="utf-8"))
    yield CheckedStore(database)
    await database.close()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def world() -> World:
    return World()


@pytest.fixture
def log() -> RecordingLog:
    return RecordingLog()


@pytest.fixture
def context(store: CheckedStore, world: World, log: RecordingLog) -> ModuleContext:
    return ModuleContext(
        module_id="mobile_gateway", module_path=MODULE_ROOT, config=dict(DEFAULTS),
        store=store.inner, log=log, _registry=world.registry, _bus=EventBus(),
        _declared_provides=set(),
        _declared_consumes={item["capability"] for item in MANIFEST["consumes"]},
    )


@pytest.fixture
def service(store: CheckedStore, context: ModuleContext, log: RecordingLog,
            clock: Clock) -> GatewayService:
    return GatewayService(store=store, caps=Capabilities(context.try_capability),
                          config=dict(DEFAULTS), log=log, clock=clock)


def core_app(service: GatewayService) -> FastAPI:
    """Çekirdeğin yaptığı gibi: zarf işleyicisi + `http.requires` kapısı."""
    app = FastAPI()
    identity = Identity()
    app.state.identity = identity

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "İstek reddedildi."
        return JSONResponse(status_code=exc.status_code,
                            content={"error": {"status": exc.status_code, "message": detail}},
                            headers=exc.headers)

    async def current_user(request: Request,
                           authorization: str | None = Header(default=None)
                           ) -> AsyncIterator[CurrentUser]:
        token = authorization[7:].strip() if authorization and \
            authorization.lower().startswith("bearer ") else ""
        user = await resolve_once(request, identity, token)
        if user is None:
            raise HTTPException(status_code=401, detail="Oturum yok veya süresi doldu.")
        yield user

    guard = _permission_guard(MANIFEST["http"]["requires"], current_user, identity,
                              "mobile_gateway")
    app.include_router(routes.bind(service), prefix=PREFIX, dependencies=[Depends(guard)])
    return app


@pytest.fixture
async def client(service: GatewayService) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=core_app(service))
    async with httpx.AsyncClient(transport=transport,
                                 base_url=f"http://test{PREFIX}",
                                 headers={"Authorization": "Bearer service"}) as http:
        yield http
    routes._service = None


@pytest.fixture(autouse=True)
def _reset_router() -> Iterator[None]:
    yield
    routes._service = None
