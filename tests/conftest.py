"""Test ortamı.

Proje henüz kurulabilir paket değil (kod yazımı sürüyor), bu yüzden kaynak
dizini sys.path'e eklenir. Paketleme yapıldığında bu dosya kaldırılır ve
yerine ``pip install -e backend`` geçer.

Depo kökü de eklenir: `services/` altındaki AYRI uygulamalar (ADR 0021 — merkezî
kimlik servisi) `services.identity.app...` yoluyla import edilir. Kapta bu yolu
`PYTHONPATH` verir; testte karşılığı burasıdır.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "backend" / "src"
for entry in (SRC, ROOT):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))
