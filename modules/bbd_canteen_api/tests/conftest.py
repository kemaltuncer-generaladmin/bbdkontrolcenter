"""Test ortamı — kaynak dizini (km_core, km_platform) ve depo kökü sys.path'te.

Kök `tests/conftest.py` yalnız `tests/` toplanırken yüklenir; bu klasör tek
başına çalıştırıldığında (`pytest modules/bbd_canteen_api/tests`) gerekir.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for entry in (ROOT / "backend" / "src", ROOT):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))
