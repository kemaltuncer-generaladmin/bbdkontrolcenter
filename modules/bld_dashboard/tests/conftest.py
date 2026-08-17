"""Test ortamı — modül kodunu benzersiz bir paket adıyla yükler.

`modules/*/backend/` klasörlerinin hepsi ad-alanı paketidir; düz `import
backend` yazmak yirmiden çok modülün `backend` klasörünü birbirine karıştırır
(`service.py`, `module.py` adları her modülde var). Bu yüzden yalnız BU modülün
`backend` klasörü `bld_dashboard_backend` adıyla kaydedilir; içerideki göreli
importlar (`.dashboard`) paketin `__path__` değeri üzerinden çözülür.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND = Path(__file__).resolve().parents[1] / "backend"

# `km_sdk` henüz kurulabilir paket değil. Kök `tests/conftest.py` kaynak dizinini
# ekliyor ama YALNIZCA `tests/` toplanırken yükleniyor; bu klasör tek başına
# çalıştırıldığında (`pytest modules/bld_dashboard/tests`) yüklenmez.
# Paketleme yapıldığında bu satır da kök conftest ile birlikte kalkar.
SRC = ROOT / "backend" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

if "bld_dashboard_backend" not in sys.modules:
    package = types.ModuleType("bld_dashboard_backend")
    package.__path__ = [str(BACKEND)]  # type: ignore[attr-defined]
    sys.modules["bld_dashboard_backend"] = package
