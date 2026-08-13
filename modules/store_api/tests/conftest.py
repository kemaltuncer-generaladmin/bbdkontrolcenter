"""Test ortamı — geçit kodunu benzersiz bir paket adıyla yükler.

`modules/*/backend/` klasörlerinin hepsi ad-alanı paketidir; `import backend`
yazmak yirmi modülün `backend` klasörünü birbirine karıştırırdı (`client.py`
adı birden çok modülde var). Bu yüzden yalnız BU modülün `backend` klasörü
`store_api_backend` adıyla kaydedilir; içerideki göreli importlar (`.errors`)
paketin `__path__` değeri üzerinden çözülür.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"

if "store_api_backend" not in sys.modules:
    package = types.ModuleType("store_api_backend")
    package.__path__ = [str(BACKEND)]  # type: ignore[attr-defined]
    sys.modules["store_api_backend"] = package
