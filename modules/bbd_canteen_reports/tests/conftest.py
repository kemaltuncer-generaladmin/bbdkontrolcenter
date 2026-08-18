"""Test ortamı — kantin raporları modülünü benzersiz bir paket adıyla yükler.

`modules/*/backend/` klasörlerinin hepsi ad-alanı paketidir; düz `import
backend` yazmak yirmi modülün `backend` klasörünü birbirine karıştırır
(`service.py`, `analytics.py` adları birden çok modülde var). Bu yüzden yalnız
BU modülün `backend` klasörü `bbd_canteen_reports_backend` adıyla kaydedilir;
içerideki göreli importlar (`.analytics`, `.export`) paketin `__path__` değeri
üzerinden çözülür.

AĞA ÇIKILMAZ: kantin istemcisi her testte taklit edilir (`FakeCanteen`).
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND = Path(__file__).resolve().parents[1] / "backend"

# `km_sdk` henüz kurulabilir paket değil. Kök `tests/conftest.py` kaynak
# dizinini ekliyor ama YALNIZCA `tests/` toplanırken yükleniyor; bu klasör tek
# başına çalıştırıldığında (`pytest modules/bbd_canteen_reports`) yüklenmez.
SRC = ROOT / "backend" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

if "bbd_canteen_reports_backend" not in sys.modules:
    package = types.ModuleType("bbd_canteen_reports_backend")
    package.__path__ = [str(BACKEND)]  # type: ignore[attr-defined]
    sys.modules["bbd_canteen_reports_backend"] = package
