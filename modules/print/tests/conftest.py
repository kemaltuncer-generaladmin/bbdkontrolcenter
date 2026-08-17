"""Test ortamı — Çıktı Merkezi'ni benzersiz bir paket adıyla yükler.

`modules/*/backend/` klasörlerinin hepsi ad-alanı paketidir; `import backend`
yazmak yirmi modülün `backend` klasörünü karıştırırdı. Bu yüzden yalnız BU
modülün `backend` klasörü `print_backend` adıyla kaydedilir.

DEPO GERÇEKTİR, TAKLİT DEĞİL. Servis düz SQL yazıyor ve okuduğu tablo
ÇEKİRDEĞİN tablosudur (`outputs`, ADR 0019 §2); SQL'i ayrıştıran sahte bir
depo, asıl sınamak istediğimiz şeyi kendi varsayımlarıyla değiştirirdi.
Geçici dosyada gerçek SQLite açılır — şema `Store.open()` ile gelir, yani
tablonun çekirdekte durduğu da her koşuda doğrulanmış olur.

YAZICI TAKLİT EDİLİR: testte gerçek yazıcıya iş gönderilmez.
"""

from __future__ import annotations

import sys
import types
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

MODULE_ROOT = Path(__file__).resolve().parents[1]
BACKEND = MODULE_ROOT / "backend"

SRC = MODULE_ROOT.parents[1] / "backend" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

if "print_backend" not in sys.modules:
    package = types.ModuleType("print_backend")
    package.__path__ = [str(BACKEND)]  # type: ignore[attr-defined]
    sys.modules["print_backend"] = package

from km_core.contracts.module import ScopedStore
from km_core.security.migrations import apply_core_migrations
from km_core.store.db import Store


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[ScopedStore]:
    """Çekirdek şeması kurulmuş gerçek depo (kimlik göçleriyle birlikte)."""
    database = Store(tmp_path / "test.sqlite")
    await database.open()
    await apply_core_migrations(database)
    yield ScopedStore(database, "print")
    await database.close()
