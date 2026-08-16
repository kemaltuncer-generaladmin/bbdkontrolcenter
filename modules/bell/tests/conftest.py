"""Test ortamı — zil modülünü benzersiz bir paket adıyla yükler.

`modules/*/backend/` klasörlerinin hepsi ad-alanı paketidir; `import backend`
yazmak yirmi modülün `backend` klasörünü karıştırırdı (`service.py` adı birden
çok modülde var). Bu yüzden yalnız BU modülün `backend` klasörü `bell_backend`
adıyla kaydedilir.

DEPO GERÇEKTİR, TAKLİT DEĞİL. Zil servisi düz SQL yazıyor; SQL'i ayrıştıran bir
sahte depo, asıl sınamak istediğimiz şeyi (doğru satır doğru anda yazıldı mı)
kendi varsayımlarıyla değiştirirdi. Bunun yerine geçici bir dosyada gerçek
SQLite açılır ve modülün kendi göçleri uygulanır — göçlerin kendisi de böylece
her koşuda sınanmış olur.

AĞA ÇIKILMAZ: Vertex istemcisi her testte taklit edilir.
"""

from __future__ import annotations

import sys
import types
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

MODULE_ROOT = Path(__file__).resolve().parents[1]
BACKEND = MODULE_ROOT / "backend"

# Proje henüz kurulabilir paket değil; `tests/conftest.py` kaynak dizinini
# sys.path'e ekliyor ama o conftest yalnız `tests/` altında geçerli. Zil modülü
# `km_sdk` (dolayısıyla `km_core`) import ettiği için burada da eklenmeli —
# yoksa `pytest modules/bell/tests` tek başına koşturulamaz.
SRC = MODULE_ROOT.parents[1] / "backend" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

if "bell_backend" not in sys.modules:
    package = types.ModuleType("bell_backend")
    package.__path__ = [str(BACKEND)]  # type: ignore[attr-defined]
    sys.modules["bell_backend"] = package

from km_core.contracts.module import ScopedStore
from km_core.store.db import Store


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[ScopedStore]:
    """Modülün kendi göçleri uygulanmış gerçek depo."""
    database = Store(tmp_path / "test.sqlite")
    await database.open()

    for migration in sorted((BACKEND / "migrations").glob("*.sql")):
        await database.apply_migration("bell", migration.name,
                                       migration.read_text(encoding="utf-8"))

    yield ScopedStore(database, "bell")
    await database.close()


@pytest.fixture
def sounds(tmp_path: Path) -> Path:
    """`data/sounds` karşılığı. İçinde tanınabilir bir zil dosyası vardır."""
    folder = tmp_path / "sounds"
    folder.mkdir()
    (folder / "classic_electric.wav").write_bytes(b"RIFF----WAVEfake-bell")
    return folder
