"""Öğrenci kart QR'ları — A4 baskı sayfası.

Tablette bu var (`QrCardsPdfExporter.kt`, sayfada 3×4 = 12 kart), burada yoktu.
Kartlar kesilip dağıtılıyor; kasa bu QR'ı okuyarak öğrenciyi tanıyor.

QR ÜRETİMİ `segno` İLE. Önce elle bir kodlayıcı yazıldı ve bağımsız bir
kitaplığın çıktısıyla karşılaştırıldı: **matrisler tutmadı** (yanlış sürüm
seçimi ve hatalı yerleşim). Okunmayan bir kart, olmayan karttan kötüdür —
elle yazılan kodlayıcı atıldı. `segno` saf Python'dur, ikili bağımlılığı
yoktur ve modülün kendi `dependencies` bloğunda ilan edilir (K11).

Çıktı reportlab ile A4 PDF; kesim çizgisi, ad, sınıf ve opak kimlik kartta.
"""

from __future__ import annotations

import io
from typing import Any


class QrError(RuntimeError):
    """Kart üretilemedi."""


def qr_matrix(text: str) -> list[list[int]]:
    """Metni QR matrisine çevirir (1 = koyu). Kart QR'ı için L seviyesi yeterli:
    temiz basılır ve yakından okutulur."""
    try:
        import segno
    except ImportError as error:  # pragma: no cover - bağımlılık yoksa
        raise QrError(
            "QR için `segno` gerekli. Kurulum: scripts/install-deps.sh "
            "(modül bağımlılığı olarak ilan edilmiştir)."
        ) from error

    code = segno.make(text, error="L")
    return [[1 if cell else 0 for cell in row] for row in code.matrix]


def build_cards_pdf(cards: list[dict[str, Any]], *, title: str = "Kantin Kartları",
                    columns: int = 3, rows: int = 4) -> bytes:
    """A4 sayfada `columns × rows` kart.

    Her kart: QR + öğrenci adı + sınıf + opak kimlik, kesim çizgisiyle.
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas as pdfcanvas
    except ImportError as error:  # pragma: no cover
        raise QrError("PDF için `reportlab` gerekli (modül bağımlılığında ilan edildi).") from error

    from .fonts import register_fonts

    regular, bold = register_fonts()

    buffer = io.BytesIO()
    page = pdfcanvas.Canvas(buffer, pagesize=A4)
    page.setTitle(title)
    width, height = A4
    margin = 12 * mm
    cell_w = (width - 2 * margin) / columns
    cell_h = (height - 2 * margin) / rows

    drawn = 0
    for index, card in enumerate(cards):
        slot = index % (columns * rows)
        if slot == 0 and index:
            page.showPage()

        col, row = slot % columns, slot // columns
        x = margin + col * cell_w
        y = height - margin - (row + 1) * cell_h

        # Kesim çizgisi — makasla ayrılacak.
        page.setStrokeColorRGB(0.85, 0.87, 0.90)
        page.setLineWidth(0.4)
        page.rect(x + 2, y + 2, cell_w - 4, cell_h - 4)

        text = str(card.get("qrText") or "")
        if not text:
            continue
        matrix = qr_matrix(text)

        modules = len(matrix)
        qr_size = min(cell_w, cell_h) * 0.60
        module = qr_size / modules
        qx = x + (cell_w - qr_size) / 2
        qy = y + cell_h - qr_size - 13 * mm

        page.setFillColorRGB(0, 0, 0)
        for r, line in enumerate(matrix):
            for c, cell in enumerate(line):
                if cell:
                    page.rect(qx + c * module, qy + (modules - 1 - r) * module,
                              module, module, stroke=0, fill=1)

        page.setFont(bold, 10)
        page.drawCentredString(x + cell_w / 2, qy - 6 * mm,
                               str(card.get("name") or "")[:28])

        klass = str(card.get("className") or "")
        if klass:
            page.setFont(regular, 8)
            page.drawCentredString(x + cell_w / 2, qy - 10 * mm, klass)

        page.setFillColorRGB(0.55, 0.58, 0.63)
        page.setFont(regular, 6.5)
        page.drawCentredString(x + cell_w / 2, y + 5 * mm, str(card.get("kantinId") or ""))
        page.setFillColorRGB(0, 0, 0)
        drawn += 1

    if drawn == 0:
        raise QrError("Basılacak kart yok.")

    page.showPage()
    page.save()
    return buffer.getvalue()
