"""Test ortamı — gerçek SQLite depo, modülün kendi göçleri, taklit Akademi.

Akademi `httpx.MockTransport` ile taklit edilir: AĞA ÇIKILMAZ. Taklitler
`akademi_fakes.py` içindedir.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
for entry in (REPO / "backend" / "src", REPO):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import httpx
from fastapi import FastAPI
from modules.bbd_akademi.backend.allowlist import OPERATIONS
from modules.bbd_akademi.backend.api import routes
from modules.bbd_akademi.backend.core.gateway import AkademiGateway, GatewayConfig
from modules.bbd_akademi.backend.core.operations import Allowlist
from modules.bbd_akademi.backend.core.proxy import AkademiProxy
from modules.bbd_akademi.backend.core.stepup import StepUpStore
from modules.bbd_akademi.backend.staff.service import StaffService
from modules.bbd_akademi.tests.akademi_fakes import (
    BASE,
    MIGRATIONS,
    Akademi,
    CheckedStore,
    Identity,
    Log,
    Secrets,
    core_envelope,
)

from km_core.store.db import Store


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[CheckedStore]:
    database = Store(tmp_path / "akademi.sqlite")
    await database.open()
    for migration in sorted(MIGRATIONS.glob("*.sql")):
        await database.apply_migration("bbd_akademi", migration.name,
                                       migration.read_text(encoding="utf-8"))
    yield CheckedStore(database)
    await database.close()


@pytest.fixture
def akademi() -> Akademi:
    return Akademi()


@pytest.fixture
def secrets() -> Secrets:
    return Secrets()


@pytest.fixture
def log() -> Log:
    return Log()


@pytest.fixture
def config() -> GatewayConfig:
    # Yeniden deneme (Akademi dağıtım anı) kapalı: kapalı Akademi testleri 20 sn beklemesin;
    # davranışın kendisi test_akademi_guncelleniyor.py'de sahte saatle sınanır.
    return GatewayConfig(base_url=BASE, max_upload_mb=1, staff_sync_minutes=0, read_retry_seconds=0)


@pytest.fixture
def gateway(config: GatewayConfig, secrets: Secrets, log: Log,
            akademi: Akademi) -> AkademiGateway:
    return AkademiGateway(config=config, secrets=secrets, log=log,
                          transport=httpx.MockTransport(akademi.handler))


@pytest.fixture
def staff_service(store: CheckedStore, gateway: AkademiGateway, log: Log) -> StaffService:
    return StaffService(store=store, gateway=gateway, log=log)


@pytest.fixture
def identity() -> Identity:
    return Identity()


@pytest.fixture
async def client(store: CheckedStore, gateway: AkademiGateway, log: Log,
                 staff_service: StaffService,
                 identity: Identity) -> AsyncIterator[httpx.AsyncClient]:
    proxy = AkademiProxy(gateway=gateway, allowlist=Allowlist(OPERATIONS),
                         step_up=StepUpStore(store, ttl_seconds=120), log=log)
    app = FastAPI()
    core_envelope(app)
    app.state.identity = identity
    app.include_router(routes.bind(proxy, staff_service), prefix="/api/bbd_akademi")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://km.test") as http:
        yield http
    await gateway.close()
    routes._proxy = None
    routes._staff = None
