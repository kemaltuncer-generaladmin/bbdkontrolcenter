"""Test ortamı.

Proje henüz kurulabilir paket değil (kod yazımı sürüyor), bu yüzden kaynak
dizini sys.path'e eklenir. Paketleme yapıldığında bu dosya kaldırılır ve
yerine ``pip install -e backend`` geçer.
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "backend" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
