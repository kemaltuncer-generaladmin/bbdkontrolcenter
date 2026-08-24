"""Öğrenci giriş kodu listesi — A4 baskı sayfası.

Kart PDF'inden (`cards.py`) farklı bir düzen: QR yok, satır satır tablo
(Ad Soyad · Sınıf · Kod). İki ayrı kaynaktan beslenir ama aynı biçimi
paylaşır:

  - "mevcut kodları listele" — kantinin hâlâ geçerli saydığı kodlar,
    hiçbir şey değiştirilmez.
  - "sıfırla ve yazdır" — az önce üretilmiş TAZE kodlar.

Hangi kaynaktan geldiği yalnızca başlık altındaki uyarı satırını değiştirir;
tablo çizimi ortaktır.
"""

from __future__ import annotations

import io
from typing import Any


class CodeListError(RuntimeError):
    """Liste üretilemedi."""


def build_code_list_pdf(rows: list[dict[str, Any]], *, title: str, note: str) -> bytes:
    """A4 sayfada Ad Soyad · Sınıf · Kod tablosu.

    `rows`: `{studentName, className, accessCode}` sözlükleri, çağıran
    tarafından zaten istenen sırada (ada göre) verilir.
    """
    if not rows:
        raise CodeListError("Listelenecek kod yok.")

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas as pdfcanvas
    except ImportError as error:  # pragma: no cover
        raise CodeListError("PDF için `reportlab` gerekli (modül bağımlılığında ilan edildi).") from error

    from .fonts import register_fonts

    regular, bold = register_fonts()

    buffer = io.BytesIO()
    page = pdfcanvas.Canvas(buffer, pagesize=A4)
    page.setTitle(title)
    width, height = A4
    margin = 16 * mm
    row_h = 9 * mm
    rows_per_page = int((height - 2 * margin - 22 * mm) / row_h)

    col_name_x = margin
    col_class_x = width - margin - 55 * mm
    col_code_x = width - margin - 30 * mm

    def draw_header(page_no: int, total_pages: int) -> float:
        page.setFillColorRGB(0, 0, 0)
        page.setFont(bold, 14)
        page.drawString(margin, height - margin, title)
        page.setFont(regular, 8.5)
        page.setFillColorRGB(0.4, 0.42, 0.47)
        page.drawString(margin, height - margin - 6 * mm, note)
        if total_pages > 1:
            page.drawRightString(width - margin, height - margin,
                                  f"Sayfa {page_no}/{total_pages}")

        y = height - margin - 14 * mm
        page.setFont(bold, 9)
        page.setFillColorRGB(0.15, 0.16, 0.19)
        page.drawString(col_name_x, y, "AD SOYAD")
        page.drawString(col_class_x, y, "SINIF")
        page.drawString(col_code_x, y, "KOD")
        page.setStrokeColorRGB(0.7, 0.72, 0.76)
        page.setLineWidth(0.6)
        page.line(margin, y - 2 * mm, width - margin, y - 2 * mm)
        return y - row_h

    total_pages = max(1, -(-len(rows) // rows_per_page))
    page_no = 1
    y = draw_header(page_no, total_pages)

    for index, row in enumerate(rows):
        if index and index % rows_per_page == 0:
            page.showPage()
            page_no += 1
            y = draw_header(page_no, total_pages)

        if index % 2 == 1:
            page.setFillColorRGB(0.96, 0.97, 0.98)
            page.rect(margin, y - 2.2 * mm, width - 2 * margin, row_h, stroke=0, fill=1)

        page.setFillColorRGB(0, 0, 0)
        page.setFont(regular, 10)
        page.drawString(col_name_x, y, str(row.get("studentName") or "")[:44])
        page.drawString(col_class_x, y, str(row.get("className") or "")[:12])
        page.setFont(bold, 11)
        page.drawString(col_code_x, y, str(row.get("accessCode") or ""))
        y -= row_h

    page.showPage()
    page.save()
    return buffer.getvalue()
