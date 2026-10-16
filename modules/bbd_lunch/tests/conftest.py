"""Test ortamı — öğle yemeği modülünü benzersiz bir paket adıyla yükler.

`modules/*/backend/` klasörlerinin hepsi ad-alanı paketidir; yalnız BU modülün
`backend` klasörü `bbd_lunch_backend` adıyla kaydedilir. DEPO GERÇEKTİR:
geçici dosyada SQLite açılır ve modülün kendi göçleri uygulanır.
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

if "bbd_lunch_backend" not in sys.modules:
    package = types.ModuleType("bbd_lunch_backend")
    package.__path__ = [str(BACKEND)]  # type: ignore[attr-defined]
    sys.modules["bbd_lunch_backend"] = package

from km_core.contracts.module import ScopedStore
from km_core.store.db import Store


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[ScopedStore]:
    database = Store(tmp_path / "km.sqlite")
    await database.open()
    for migration in sorted((BACKEND / "migrations").glob("*.sql")):
        await database.apply_migration("bbd_lunch", migration.name,
                                       migration.read_text(encoding="utf-8"))
    yield ScopedStore(database, "bbd_lunch")
    await database.close()
