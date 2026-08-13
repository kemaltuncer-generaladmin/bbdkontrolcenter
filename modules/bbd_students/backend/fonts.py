"""PDF için Türkçe destekli font kaydı.

reportlab'in yerleşik Helvetica'sı Latin-1'dir: 'ğ', 'ş', 'İ' kareye döner.
Gömülü DejaVuSans sistemden gelir (`fonts-dejavu-core`), depoya kopyalanmaz
(ADR 0008). Bulunamazsa Helvetica'ya düşülür — rapor yine üretilir ama
Türkçe karakterler bozulur; sessiz kalmaktansa bu yeğdir.
"""

from __future__ import annotations

from pathlib import Path

CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
]
BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
]

_READY = False


def register_fonts() -> tuple[str, str]:
    """(normal, kalın) font adlarını döner; bir kez kaydeder."""
    global _READY
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if _READY:
        return "KM-Card", "KM-Card-Bold"

    regular = next((path for path in CANDIDATES if Path(path).exists()), None)
    bold = next((path for path in BOLD_CANDIDATES if Path(path).exists()), None)
    if regular is None:
        return "Helvetica", "Helvetica-Bold"

    pdfmetrics.registerFont(TTFont("KM-Card", regular))
    pdfmetrics.registerFont(TTFont("KM-Card-Bold", bold or regular))
    _READY = True
    return "KM-Card", "KM-Card-Bold"
