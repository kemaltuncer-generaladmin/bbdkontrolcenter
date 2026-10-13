"""Test ortamı — modül kodunu benzersiz bir paket adıyla yükler.

`modules/*/backend/` klasörlerinin hepsi ad-alanı paketidir; düz `import
backend` yazmak modüllerin `backend` klasörlerini birbirine karıştırır. Bu
yüzden yalnız BU modülün `backend` klasörü `bbd_payment_request_backend`
adıyla kaydedilir.

DEPO GERÇEKTİR: geçici dosyada SQLite açılır ve modülün kendi göçleri
uygulanır. Kantin taklittir; ağa çıkılmaz, gerçek SMS gitmez.
"""

from __future__ import annotations

import sys
import types
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
BACKEND = Path(__file__).resolve().parents[1] / "backend"
SRC = ROOT / "backend" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

if "bbd_payment_request_backend" not in sys.modules:
    package = types.ModuleType("bbd_payment_request_backend")
    package.__path__ = [str(BACKEND)]  # type: ignore[attr-defined]
    sys.modules["bbd_payment_request_backend"] = package

from km_core.contracts.module import ScopedStore
from km_core.store.db import Store


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[ScopedStore]:
    database = Store(tmp_path / "km.sqlite")
    await database.open()
    for migration in sorted((BACKEND / "migrations").glob("*.sql")):
        await database.apply_migration("bbd_payment_request", migration.name,
                                       migration.read_text(encoding="utf-8"))
    yield ScopedStore(database, "bbd_payment_request")
    await database.close()
