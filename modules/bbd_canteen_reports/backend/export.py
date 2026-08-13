"""Rapor çıktısı — PDF ve Excel uyumlu CSV.

PDF `reportlab` ile üretilir (modülün kendi bağımlılığı, K11). Türkçe karakterler
için gömülü DejaVuSans kullanılır: reportlab'in yerleşik Helvetica'sı Latin-1'dir,
'ğ', 'ş', 'İ' karelere döner. Font sistemden gelir (`fonts-dejavu-core`), depoya
kopyalanmaz (ADR 0008).

reportlab kurulu değilse modül YİNE ÇALIŞIR; yalnız PDF düğmesi anlaşılır bir
hata döner (K7).

CSV, kantinin kendi dışa aktarma kuralıyla aynıdır: UTF-8 BOM + noktalı virgül.
Excel'in Türkçe yerelinde ondalık ayracı virgül olduğu için virgüllü CSV
sütunları kaydırır.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
]
FONT_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
]

_FONTS_READY = False


class ExportError(RuntimeError):
    """Çıktı üretilemedi — sebebi kullanıcıya gösterilir."""


def money(kurus: Any) -> str:
    value = int(kurus or 0) / 100
    return f"{value:,.2f} ₺".replace(",", " ").replace(".", ",").replace(" ", ".")


# ------------------------------------------------------------------- CSV

def csv_bytes(headers: list[str], rows: list[list[Any]]) -> bytes:
    """UTF-8 BOM + `;` — Excel'in Türkçe yerelinde doğru açılır."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", quoting=csv.QUOTE_MINIMAL,
                        lineterminator="\r\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")


# ------------------------------------------------------------------- PDF

def _ensure_fonts() -> tuple[str, str]:
    """DejaVu'yu bir kez kaydeder. Yoksa Helvetica'ya düşer (Türkçe bozulur)."""
    global _FONTS_READY
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if _FONTS_READY:
        return "KM-Sans", "KM-Sans-Bold"

    regular = next((path for path in FONT_CANDIDATES if Path(path).exists()), None)
    bold = next((path for path in FONT_BOLD_CANDIDATES if Path(path).exists()), None)
    if regular is None:
        # Türkçe karakterler bozulur ama rapor yine üretilir — sessiz kalmayız.
        return "Helvetica", "Helvetica-Bold"

    pdfmetrics.registerFont(TTFont("KM-Sans", regular))
    pdfmetrics.registerFont(TTFont("KM-Sans-Bold", bold or regular))
    _FONTS_READY = True
    return "KM-Sans", "KM-Sans-Bold"


def build_pdf(*, title: str, subtitle: str, sections: list[dict[str, Any]],
              footer: str = "") -> bytes:
    """Çok bölümlü A4 rapor.

    `sections` öğeleri:
      {"kind": "tiles",  "title": …, "tiles": [(etiket, değer), …]}
      {"kind": "table",  "title": …, "headers": [...], "rows": [[...]],
       "align": "LRRR", "widths": [oran, …]}
      {"kind": "bars",   "title": …, "bars": [(etiket, değer, gösterim), …]}
      {"kind": "note",   "text": …}
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            BaseDocTemplate,
            Frame,
            PageTemplate,
            Paragraph,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as error:  # pragma: no cover - bağımlılık yoksa
        raise ExportError(
            "PDF için `reportlab` gerekli. Kurulum: scripts/install-deps.sh "
            "(modül bağımlılığı olarak ilan edilmiştir)."
        ) from error

    regular, bold = _ensure_fonts()
    buffer = io.BytesIO()

    ink = colors.HexColor("#111722")
    soft = colors.HexColor("#667487")
    line = colors.HexColor("#e0e5ee")
    accent = colors.HexColor("#3f6fd8")
    tint = colors.HexColor("#f5f7fb")

    styles = {
        "title": ParagraphStyle("t", fontName=bold, fontSize=17, leading=21, textColor=ink),
        "subtitle": ParagraphStyle("s", fontName=regular, fontSize=9.5, leading=13, textColor=soft),
        "section": ParagraphStyle("h", fontName=bold, fontSize=11.5, leading=15, textColor=ink,
                                  spaceBefore=10, spaceAfter=5),
        "note": ParagraphStyle("n", fontName=regular, fontSize=8.5, leading=12, textColor=soft),
        "cell": ParagraphStyle("c", fontName=regular, fontSize=8.5, leading=11, textColor=ink),
    }

    width, height = A4
    margin = 16 * mm
    stamp = datetime.now(UTC).astimezone().strftime("%d.%m.%Y %H:%M")

    def decorate(canvas, doc):
        canvas.saveState()
        # Başlık şeridi
        canvas.setFillColor(accent)
        canvas.rect(0, height - 8 * mm, width, 8 * mm, stroke=0, fill=1)
        # Alt bilgi
        canvas.setFont(regular, 7.5)
        canvas.setFillColor(soft)
        canvas.drawString(margin, 10 * mm, footer or "Kontrol Merkezi · Kantin Raporları")
        canvas.drawRightString(width - margin, 10 * mm, f"{stamp} · sayfa {doc.page}")
        canvas.setStrokeColor(line)
        canvas.line(margin, 13 * mm, width - margin, 13 * mm)
        canvas.restoreState()

    doc = BaseDocTemplate(
        buffer, pagesize=A4,
        leftMargin=margin, rightMargin=margin,
        topMargin=margin, bottomMargin=18 * mm,
        title=title, author="Kontrol Merkezi",
    )
    frame = Frame(margin, 18 * mm, width - 2 * margin, height - margin - 18 * mm, id="body")
    doc.addPageTemplates([PageTemplate(id="std", frames=[frame], onPage=decorate)])

    story: list[Any] = [
        Paragraph(title, styles["title"]),
        Paragraph(subtitle, styles["subtitle"]),
        Spacer(1, 7),
    ]
    usable = width - 2 * margin

    for section in sections:
        kind = section.get("kind")
        if section.get("title"):
            story.append(Paragraph(section["title"], styles["section"]))

        if kind == "tiles":
            tiles = section["tiles"]
            # Satır başına en çok 4 kutu — daha fazlası A4'te okunmaz olur.
            for start in range(0, len(tiles), 4):
                chunk = tiles[start:start + 4]
                data = [[Paragraph(f'<font size="7.5" color="#667487">{label}</font><br/>'
                                   f'<font size="12">{value}</font>', styles["cell"])
                         for label, value in chunk]]
                table = Table(data, colWidths=[usable / len(chunk)] * len(chunk))
                table.setStyle(TableStyle([
                    ("BOX", (0, 0), (-1, -1), 0.5, line),
                    ("INNERGRID", (0, 0), (-1, -1), 0.5, line),
                    ("BACKGROUND", (0, 0), (-1, -1), tint),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]))
                story.extend([table, Spacer(1, 4)])

        elif kind == "table":
            headers = section["headers"]
            align = section.get("align") or "L" * len(headers)
            ratios = section.get("widths") or [1] * len(headers)
            scale = usable / sum(ratios)
            data = [[Paragraph(f'<font name="{bold}">{item}</font>', styles["cell"])
                     for item in headers]]
            for row in section["rows"]:
                data.append([Paragraph(str(cell), styles["cell"]) for cell in row])

            table = Table(data, colWidths=[ratio * scale for ratio in ratios], repeatRows=1)
            style = [
                ("BOX", (0, 0), (-1, -1), 0.5, line),
                ("LINEBELOW", (0, 0), (-1, 0), 0.8, accent),
                ("INNERGRID", (0, 1), (-1, -1), 0.25, line),
                ("BACKGROUND", (0, 0), (-1, 0), tint),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 3.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
                # Zebra: uzun tabloda satır kaymasını engeller.
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafbfd")]),
            ]
            for index, letter in enumerate(align):
                if letter == "R":
                    style.append(("ALIGN", (index, 0), (index, -1), "RIGHT"))
                elif letter == "C":
                    style.append(("ALIGN", (index, 0), (index, -1), "CENTER"))
            table.setStyle(TableStyle(style))
            story.extend([table, Spacer(1, 5)])

        elif kind == "bars":
            bars = section["bars"]
            peak = max((value for _, value, _ in bars), default=0) or 1
            data = []
            for label, value, display in bars:
                # Çubuk metinle çizilir: reportlab çizim nesnesi eklemeden,
                # her yazıcıda aynı görünen basit ve güvenilir yol.
                filled = round(value * 34 / peak)
                bar = "█" * filled + "░" * (34 - filled)
                data.append([
                    Paragraph(str(label), styles["cell"]),
                    Paragraph(f'<font name="{regular}" color="#3f6fd8">{bar}</font>', styles["cell"]),
                    Paragraph(str(display), styles["cell"]),
                ])
            table = Table(data, colWidths=[usable * 0.30, usable * 0.48, usable * 0.22])
            table.setStyle(TableStyle([
                ("ALIGN", (2, 0), (2, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
            ]))
            story.extend([table, Spacer(1, 5)])

        elif kind == "note":
            story.extend([Paragraph(section["text"], styles["note"]), Spacer(1, 4)])

    doc.build(story)
    return buffer.getvalue()
