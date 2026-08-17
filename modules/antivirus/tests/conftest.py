"""Test ortamı — antivirüs modülünü benzersiz bir paket adıyla yükler.

`modules/*/backend/` klasörlerinin hepsi ad-alanı paketidir; `import backend`
yazmak elli modülün `backend` klasörünü karıştırırdı (`service.py` adı birden
çok modülde var). Bu yüzden yalnız BU modülün `backend` klasörü
`antivirus_backend` adıyla kaydedilir.

GERÇEK TARAMA YOK. `clamdscan` ve `clamscan` yerine, ClamAV'nin çıktısını
birebir taklit eden küçük kabuk betikleri kurulur. Neden bir sahte "motor
nesnesi" değil de gerçek süreç:

  · Sınamak istediğimiz şeyin yarısı SÜREÇ DAVRANIŞI — çıkış kodu, iki ayrı
    akıştan gelen satırlar, zaman aşımında sürecin öldürülmesi. Bunları
    taklit eden bir nesne, asıl riski kendi varsayımıyla değiştirirdi.
  · Ayrıştırıcı gerçek metin üzerinde çalışır; ClamAV'nin satır biçimi
    (`: OK`, `: … FOUND`, `: … ERROR`) testte olduğu gibi durur.

Betikler ağa çıkmaz, dosya taramaz, saniyeler sürmez.

DEPO GERÇEKTİR: geçici bir dosyada gerçek SQLite açılır ve modülün kendi
göçleri uygulanır — göçlerin kendisi de böylece her koşuda sınanmış olur.
"""

from __future__ import annotations

import os
import sys
import time
import types
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

MODULE_ROOT = Path(__file__).resolve().parents[1]
BACKEND = MODULE_ROOT / "backend"

# Proje henüz kurulabilir paket değil; kökteki `tests/conftest.py` kaynak
# dizinini sys.path'e ekliyor ama o conftest yalnız `tests/` altında geçerli.
# Modül `km_sdk` import ettiği için burada da eklenmeli — yoksa
# `pytest modules/antivirus/tests` tek başına koşturulamaz.
SRC = MODULE_ROOT.parents[1] / "backend" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

if "antivirus_backend" not in sys.modules:
    package = types.ModuleType("antivirus_backend")
    package.__path__ = [str(BACKEND)]  # type: ignore[attr-defined]
    sys.modules["antivirus_backend"] = package

from km_core.contracts.module import ScopedStore
from km_core.store.db import Store

#: Fake betiklerin başı: `--ping` daemon yoklamasıdır, tarama değil. Gerçek
#: clamdscan de bu iki işi aynı ikilide toplar.
PING_GUARD = 'if [ "$1" = "--ping" ]; then exit %d; fi\n'


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[ScopedStore]:
    """Modülün kendi göçleri uygulanmış gerçek depo."""
    database = Store(tmp_path / "test.sqlite")
    await database.open()

    for migration in sorted((BACKEND / "migrations").glob("*.sql")):
        await database.apply_migration("antivirus", migration.name,
                                       migration.read_text(encoding="utf-8"))

    yield ScopedStore(database, "antivirus")
    await database.close()


@pytest.fixture
def make_binary(tmp_path: Path) -> Callable[..., str]:
    """Sahte tarayıcı üretir; dönen değer tam yoldur (`shutil.which` çözer)."""
    folder = tmp_path / "bin"
    folder.mkdir(exist_ok=True)

    def make(name: str, body: str, *, ping: int = 0) -> str:
        path = folder / name
        path.write_text("#!/bin/sh\n" + (PING_GUARD % ping) + body, encoding="utf-8")
        path.chmod(0o755)
        return str(path)

    return make


@pytest.fixture
def signature_dir(tmp_path: Path) -> Callable[..., str]:
    """freshclam imza dizinini taklit eder; yaşı saat cinsinden ayarlanır."""

    def make(*, age_hours: float | None = 1.0, ready: bool = True) -> str:
        folder = tmp_path / "clamav"
        folder.mkdir(exist_ok=True)
        if not ready:
            return str(folder)
        (folder / "main.cvd").write_bytes(b"main")
        daily = folder / "daily.cvd"
        daily.write_bytes(b"daily")
        if age_hours is not None:
            when = time.time() - age_hours * 3600
            os.utime(daily, (when, when))
        return str(folder)

    return make


class FakeLog:
    """Yapısal log yerine geçen sessiz kayıt — çağrılar sınanabilsin diye tutulur."""

    def __init__(self) -> None:
        self.lines: list[tuple[str, str, dict[str, Any]]] = []

    def _record(self, level: str, message: str, **fields: Any) -> None:
        self.lines.append((level, message, fields))

    def info(self, message: str, **fields: Any) -> None:
        self._record("info", message, **fields)

    def warning(self, message: str, **fields: Any) -> None:
        self._record("warning", message, **fields)

    def error(self, message: str, **fields: Any) -> None:
        self._record("error", message, **fields)


@pytest.fixture
def log() -> FakeLog:
    return FakeLog()


class Recorder:
    """Olay veri yolunun testteki karşılığı."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, name: str, payload: dict[str, Any]) -> None:
        self.events.append((name, payload))

    def names(self) -> list[str]:
        return [name for name, _ in self.events]

    def payload(self, name: str) -> dict[str, Any]:
        for event, payload in self.events:
            if event == name:
                return payload
        raise AssertionError(f"'{name}' olayı yayınlanmadı: {self.names()}")


@pytest.fixture
def events() -> Recorder:
    return Recorder()
