"""Test ortamı — Ders Takvimi modülünü benzersiz bir paket adıyla yükler.

`modules/*/backend/` klasörlerinin hepsi ad-alanı paketidir; `import backend`
yazmak yirmi modülün `backend` klasörünü karıştırırdı (`service.py` adı birden
çok modülde var).
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1]
BACKEND = MODULE_ROOT / "backend"

# Proje henüz kurulabilir paket değil; `tests/conftest.py` yalnız `tests/`
# altında geçerli. Bu modül `km_sdk` import ettiği için kaynak dizini burada
# da eklenmeli.
SRC = MODULE_ROOT.parents[1] / "backend" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

if "class_schedule_backend" not in sys.modules:
    package = types.ModuleType("class_schedule_backend")
    package.__path__ = [str(BACKEND)]  # type: ignore[attr-defined]
    sys.modules["class_schedule_backend"] = package
