"""Akademi belgesi → kurum logolu A4 PDF (reportlab). AĞA ÇIKMAZ, DURUM TUTMAZ.

NEDEN SUNUCUDA PDF. Kantin raporlarıyla aynı zincir (`ui-kit/report.js`):
belge sunucuda PDF olarak üretilir, önizleme o PDF'in sayfalarının görüntüsüdür
(`pdftoppm`), baskı cihazdaki yazıcıya PDF'in KENDİSİ gider
(`ui-kit/printing.js` → `/api/outputs/document`). Ekranda görülen ile kâğıttan
çıkan aynıdır; tema (koyu/açık) belgeye hiç karışmaz, kâğıt her zaman beyazdır.

VERİ MODELİ PANELDEN GELİR. Panel ekrandaki veriyi Akademi'den zaten okuyor;
belgenin içeriğini (`parts → sections`) `documents-logic.js` kurar. Bu dosya
yalnız BİÇİMDİR: başlık (logo, kurum adı, belge adı, belge no, tarih), altbilgi
(düzenleyen, gizlilik notu, "Sayfa x / y"), A4 dikey/yatay, tablo başlığının
her sayfada tekrarı, sayfa kırılımı, Türkçe sayı/para biçimi. İş kuralı yoktur.

GÜVENLİK. Panelden gelen her metin reportlab'in mini işaret diline girmeden
KAÇIŞLANIR (`<`, `&`); telefon numarası biçimindeki her dizi maskelenir
(KVKK — panel zaten maskeli gönderir, bu ikinci kapıdır). Boyut sınırları
(`LIMITS`) aşılırsa anlaşılır bir Türkçe hata döner; sunucu boğulmaz (A14).
"""

from __future__ import annotations

import io
import math
import re
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

# ------------------------------------------------------------------ sınırlar

#: Tek istekte kabul edilen en büyük belge. Bir okulun bütün karneleri
#: (≈ 500 öğrenci) tek iş olabilir; daha büyüğü sayfalı hazırlanmalıdır.
LIMITS = {
    "parts": 600,
    "sections_per_part": 120,
    "rows_total": 40_000,
    "columns": 40,
    "text": 6_000,
    "cells_total": 400_000,
}

ORIENTATIONS = ("portrait", "landscape")
TONES = ("good", "bad", "warn", "muted", "info", "strong")

DEFAULT_CONFIDENTIAL = (
    "Bu belge kişisel veri içerir (KVKK). Yalnız ilgili kişi ve yetkili personel "
    "içindir; çoğaltılamaz, üçüncü kişilerle paylaşılamaz.")

#: Türkiye cep/sabit telefonu: 05xx xxx xx xx, +90 5xx…, 0 (212) 555 12 34 …
#: Son iki hane kalır: "05** *** ** 67". Maskeli gelen ("*" içeren) dokunulmaz.
_PHONE = re.compile(
    r"(?<![\d*])(?:\+?90[\s.-]?)?\(?0?\s?[2-5]\d{2}\)?[\s.-]?\d{3}[\s.-]?\d{2}[\s.-]?(\d{2})(?![\d*])")


class DocumentError(ValueError):
    """Belge modeli geçersiz ya da çok büyük — sebebi kullanıcıya gösterilir."""


def mask_phones(text: str) -> str:
    """Metindeki telefon numaralarını maskeler; son iki hane görünür kalır."""
    return _PHONE.sub(lambda match: f"0*** *** ** {match.group(1)}", text)


# ------------------------------------------------------------------ biçim

def tr_number(value: Any, decimals: int = 0) -> str:
    """1419.5 → '1.419,5' (Türkçe ayraç)."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return str(value)
    text = f"{amount:,.{decimals}f}"
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def tr_money(minor: Any) -> str:
    """Kuruş → '12.345,67 ₺'."""
    try:
        return f"{tr_number(int(minor) / 100, 2)} ₺"
    except (TypeError, ValueError):
        return "—"


# ------------------------------------------------------------------ yazı tipi

FONT_CANDIDATES = (
    # Sunucu imajı (fonts-dejavu-core) — asıl hedef.
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/TTF/DejaVuSans.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
    # Geliştirme makinesi (macOS) — Türkçe karakterleri taşır.
    ("/System/Library/Fonts/Supplemental/Arial.ttf",
     "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
)

_FONTS: tuple[str, str] | None = None
#: Yazı tipi "₺" (U+20BA) taşıyor mu? DejaVu taşır; taşımayanda "TL" yazılır
#: (kâğıtta boş kare çıkmasın).
_LIRA = True


def fonts() -> tuple[str, str]:
    """(normal, kalın) — bir kez kaydedilir. Bulunamazsa Helvetica (Türkçe bozulur)."""
    global _FONTS, _LIRA
    if _FONTS is not None:
        return _FONTS
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for regular, bold in FONT_CANDIDATES:
        if Path(regular).exists():
            font = TTFont("AK-Doc", regular)
            pdfmetrics.registerFont(font)
            pdfmetrics.registerFont(TTFont("AK-Doc-Bold", bold if Path(bold).exists() else regular))
            _FONTS = ("AK-Doc", "AK-Doc-Bold")
            _LIRA = 0x20BA in getattr(font.face, "charToGlyph", {0x20BA: 1})
            return _FONTS
    _FONTS = ("Helvetica", "Helvetica-Bold")
    _LIRA = False
    return _FONTS


def _printable(text: str) -> str:
    return text if _LIRA else text.replace("₺", "TL")


# ------------------------------------------------------------------ model

def is_svg(data: bytes) -> bool:
    """SVG mi: (BOM / XML bildirimi / yorumdan sonra) ilk 2 KB'ta ``<svg`` kökü."""
    head = data[:2048].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    return head.startswith((b"<svg", b"<?xml", b"<!--")) and b"<svg" in head


def logo_drawable(data: bytes | None) -> Any:
    """Başlık logosu → reportlab ``ImageReader`` (PNG/JPEG) ya da ``Drawing`` (SVG); çizilemezse ``None``
    (başlık monogramla çizilir, K7).

    SVG ``svglib`` ile vektör olarak çizilir (module.yaml bağımlılığı; yoksa monogram). Güvenlik: kaynak bellekte
    (``BytesIO``) olduğu için svglib dış ``<image>`` / ``<use>`` yollarını çözmez, XML varlıkları çözülmez
    (``resolve_entities=False``); ``<!DOCTYPE`` taşıyan dosya hiç ayrıştırılmaz."""
    if not data:
        return None
    if is_svg(data):
        if b"<!doctype" in data[:4096].lower():
            return None
        try:
            from svglib.svglib import svg2rlg
        except ImportError:
            return None
        try:
            drawing = svg2rlg(io.BytesIO(data), resolve_entities=False)
        except Exception:  # noqa: BLE001 — bozuk SVG: monogram
            return None
        if drawing is None or not drawing.width or not drawing.height:
            return None
        return drawing
    from reportlab.lib.utils import ImageReader

    try:
        image = ImageReader(io.BytesIO(data))
        image.getSize()
    except Exception:  # noqa: BLE001 — bozuk görsel: başlık monogramla çizilir
        return None
    return image


@dataclass(slots=True)
class Letterhead:
    """Belge başlığının kurum bilgisi (Akademi kurum ayarı + tema)."""

    name: str = "BBD Akademi"
    detail: str = ""               # adres · telefon · e-posta (tek satır)
    logo: bytes | None = None      # PNG/JPEG ya da SVG baytları
    accent: str = "#2F5BD3"        # tema ana rengi


@dataclass(slots=True)
class Stamp:
    """Belgeyi kimin, ne zaman, hangi numarayla ürettiği (sunucu doldurur)."""

    number: str
    issued: str                    # '13.10.2026 20:45'
    author: str                    # düzenleyen (KM oturumu)
    confidential: str = DEFAULT_CONFIDENTIAL


@dataclass(slots=True)
class Part:
    title: str
    subtitle: str = ""
    meta: list[tuple[str, str]] = field(default_factory=list)
    sections: list[dict[str, Any]] = field(default_factory=list)
    watermark: str = ""


@dataclass(slots=True)
class Document:
    title: str
    orientation: str
    parts: list[Part]


def _text(value: Any, *, limit: int | None = None) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Evet" if value else "Hayır"
    if isinstance(value, float):
        text = tr_number(value, 0 if value.is_integer() else 2)
    else:
        text = str(value)
    text = mask_phones(text)
    cap = limit or LIMITS["text"]
    return text if len(text) <= cap else text[: cap - 1] + "…"


class _Budget:
    def __init__(self) -> None:
        self.rows = 0
        self.cells = 0

    def take(self, rows: int, cells: int) -> None:
        self.rows += rows
        self.cells += cells
        if self.rows > LIMITS["rows_total"] or self.cells > LIMITS["cells_total"]:
            raise DocumentError(
                f"Belge çok büyük (en çok {tr_number(LIMITS['rows_total'])} tablo satırı). "
                "Süzgeci daraltın ya da belgeyi parçalara bölün.")


def _cell(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        tone = raw.get("tone")
        return {"text": _text(raw.get("text")),
                "tone": tone if tone in TONES else "",
                "bold": bool(raw.get("bold"))}
    return {"text": _text(raw), "tone": "", "bold": False}


def _rows(raw: Any, width: int, budget: _Budget) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise DocumentError("Tablo satırları liste olmalı.")
    rows = []
    for item in raw:
        if isinstance(item, Mapping):
            cells, tone = item.get("cells"), item.get("tone")
            bold = bool(item.get("bold"))
        else:
            cells, tone, bold = item, None, False
        if not isinstance(cells, list):
            raise DocumentError("Tablo satırı hücre listesi olmalı.")
        normalized = [_cell(value) for value in cells[:width]]
        normalized += [_cell("")] * (width - len(normalized))
        rows.append({"cells": normalized, "tone": tone if tone in TONES else "", "bold": bold})
    budget.take(len(rows), len(rows) * max(width, 1))
    return rows


def _section(raw: Any, budget: _Budget) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise DocumentError("Belge bölümü nesne olmalı.")
    kind = raw.get("kind")
    title = _text(raw.get("title"), limit=200)
    if kind == "table":
        headers = [_text(item, limit=120) for item in (raw.get("headers") or [])]
        if not headers or len(headers) > LIMITS["columns"]:
            raise DocumentError(f"Tablo 1–{LIMITS['columns']} sütun olmalı.")
        align = str(raw.get("align") or "").upper()[: len(headers)].ljust(len(headers), "L")
        widths = raw.get("widths") or [1] * len(headers)
        try:
            ratios = [max(0.2, float(value)) for value in widths][: len(headers)]
        except (TypeError, ValueError) as failure:
            raise DocumentError("Sütun genişlikleri sayı olmalı.") from failure
        ratios += [1.0] * (len(headers) - len(ratios))
        footer = raw.get("footer")
        return {"kind": "table", "title": title, "headers": headers, "align": align,
                "widths": ratios, "rows": _rows(raw.get("rows") or [], len(headers), budget),
                "footer": _rows([footer], len(headers), budget)[0] if footer else None,
                "dense": bool(raw.get("dense")), "empty": _text(raw.get("empty") or
                                                                 "Kayıt yok.", limit=200),
                "keep": bool(raw.get("keep"))}
    if kind in ("fields", "tiles"):
        items = raw.get("items") or []
        if not isinstance(items, list):
            raise DocumentError("Alan listesi geçersiz.")
        pairs = []
        for item in items[:60]:
            if isinstance(item, Mapping):
                pairs.append((_text(item.get("label"), limit=120), _text(item.get("value")),
                              item.get("tone") if item.get("tone") in TONES else ""))
            elif isinstance(item, list | tuple) and len(item) >= 2:
                pairs.append((_text(item[0], limit=120), _text(item[1]), ""))
        budget.take(1, len(pairs))
        columns = int(raw.get("columns") or (4 if kind == "tiles" else 2))
        return {"kind": kind, "title": title, "items": pairs, "columns": max(1, min(columns, 6))}
    if kind == "bars":
        bars = []
        for item in (raw.get("items") or [])[:80]:
            if not isinstance(item, Mapping):
                continue
            try:
                value = float(item.get("value") or 0)
            except (TypeError, ValueError):
                value = 0.0
            bars.append((_text(item.get("label"), limit=120), value if math.isfinite(value) else 0.0,
                         _text(item.get("display") if item.get("display") is not None else
                               tr_number(value, 1))))
        try:
            ceiling = float(raw.get("max") or 0)
        except (TypeError, ValueError):
            ceiling = 0.0
        budget.take(len(bars), len(bars) * 3)
        return {"kind": "bars", "title": title, "items": bars, "max": ceiling}
    if kind in ("text", "note"):
        return {"kind": kind, "title": title, "text": _text(raw.get("text"))}
    if kind == "list":
        entries = [_text(item, limit=600) for item in (raw.get("items") or [])[:200]]
        budget.take(len(entries), len(entries))
        return {"kind": "list", "title": title, "items": entries}
    if kind == "signatures":
        labels = [_text(item, limit=60) for item in (raw.get("items") or [])[:4]]
        return {"kind": "signatures", "title": title, "items": labels}
    if kind == "heading":
        return {"kind": "heading", "title": title}
    if kind == "break":
        return {"kind": "break", "title": ""}
    raise DocumentError(f"Bilinmeyen belge bölümü: {str(kind)[:40]}")


def parse(raw: Mapping[str, Any]) -> Document:
    """Panelin gönderdiği modeli doğrular ve normalleştirir."""
    title = _text(raw.get("title"), limit=160).strip()
    if not title:
        raise DocumentError("Belge adı boş olamaz.")
    orientation = raw.get("orientation") or "portrait"
    if orientation not in ORIENTATIONS:
        raise DocumentError("Sayfa yönü 'portrait' ya da 'landscape' olmalı.")
    parts_raw = raw.get("parts")
    if not isinstance(parts_raw, list) or not parts_raw:
        raise DocumentError("Belgede en az bir bölüm (öğrenci/sayfa) olmalı.")
    if len(parts_raw) > LIMITS["parts"]:
        raise DocumentError(f"Tek seferde en çok {LIMITS['parts']} belge hazırlanabilir; "
                            "seçimi bölün.")
    budget = _Budget()
    parts = []
    for item in parts_raw:
        if not isinstance(item, Mapping):
            raise DocumentError("Belge parçası nesne olmalı.")
        sections = item.get("sections") or []
        if not isinstance(sections, list) or len(sections) > LIMITS["sections_per_part"]:
            raise DocumentError("Belge parçasının bölüm listesi geçersiz ya da çok uzun.")
        meta = []
        for entry in (item.get("meta") or [])[:16]:
            if isinstance(entry, list | tuple) and len(entry) >= 2:
                meta.append((_text(entry[0], limit=80), _text(entry[1], limit=300)))
            elif isinstance(entry, Mapping):
                meta.append((_text(entry.get("label"), limit=80), _text(entry.get("value"), limit=300)))
        parts.append(Part(
            title=_text(item.get("title") or title, limit=160),
            subtitle=_text(item.get("subtitle"), limit=300),
            meta=meta,
            sections=[_section(section, budget) for section in sections],
            watermark=_text(item.get("watermark"), limit=24),
        ))
    return Document(title=title, orientation=orientation, parts=parts)


# ------------------------------------------------------------------ çizim

def _hex(value: str, fallback: str = "#2F5BD3") -> str:
    text = str(value or "").strip()
    return text if re.fullmatch(r"#[0-9A-Fa-f]{6}", text) else fallback


def _mix(color: str, toward: str, amount: float) -> str:
    """İki rengi karıştırır (açık ton üretmek için)."""
    a = [int(color[index:index + 2], 16) for index in (1, 3, 5)]
    b = [int(toward[index:index + 2], 16) for index in (1, 3, 5)]
    mixed = [round(x + (y - x) * amount) for x, y in zip(a, b, strict=True)]
    return "#" + "".join(f"{value:02X}" for value in mixed)


def _readable_accent(color: str) -> str:
    """Çok açık tema rengi beyaz kâğıtta okunmaz: koyulaştırılır."""
    r, g, b = (int(color[index:index + 2], 16) / 255 for index in (1, 3, 5))
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return _mix(color, "#000000", 0.45) if luminance > 0.6 else color


def initials(name: str) -> str:
    words = [word for word in re.split(r"\s+", name.strip()) if word[:1].isalnum()]
    return "".join(word[0] for word in words[:2]).upper() or "A"


def render(document: Document, letterhead: Letterhead, stamp: Stamp) -> bytes:
    """Belgeyi PDF baytlarına çevirir."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.platypus import (
        BaseDocTemplate,
        CondPageBreak,
        Flowable,
        Frame,
        KeepTogether,
        PageBreak,
        PageTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )

    regular, bold = fonts()
    accent_hex = _readable_accent(_hex(letterhead.accent))
    accent = colors.HexColor(accent_hex)
    tint = colors.HexColor(_mix(accent_hex, "#FFFFFF", 0.92))
    tint_strong = colors.HexColor(_mix(accent_hex, "#FFFFFF", 0.82))
    ink = colors.HexColor("#141A24")
    soft = colors.HexColor("#5D6878")
    line = colors.HexColor("#D9DEE7")
    zebra = colors.HexColor("#F7F9FC")
    tone_fill = {
        "good": colors.HexColor("#E7F5EC"), "bad": colors.HexColor("#FCE9E9"),
        "warn": colors.HexColor("#FFF4DE"), "muted": colors.HexColor("#F1F3F6"),
        "info": tint, "strong": tint_strong,
    }
    tone_ink = {
        "good": colors.HexColor("#17663A"), "bad": colors.HexColor("#A3201D"),
        "warn": colors.HexColor("#8A5A00"), "muted": soft, "info": accent, "strong": ink,
    }

    size = landscape(A4) if document.orientation == "landscape" else A4
    width, height = size
    margin = 13 * mm
    header_h = 21 * mm
    footer_h = 13 * mm
    usable = width - 2 * margin

    styles = {
        "title": ParagraphStyle("t", fontName=bold, fontSize=16, leading=20, textColor=ink),
        "subtitle": ParagraphStyle("s", fontName=regular, fontSize=9.5, leading=13,
                                   textColor=soft, spaceAfter=2),
        "section": ParagraphStyle("h", fontName=bold, fontSize=11, leading=14, textColor=accent,
                                  spaceBefore=8, spaceAfter=4),
        "body": ParagraphStyle("b", fontName=regular, fontSize=9.2, leading=13, textColor=ink),
        "note": ParagraphStyle("n", fontName=regular, fontSize=8, leading=11, textColor=soft),
        "cell": ParagraphStyle("c", fontName=regular, fontSize=8.4, leading=10.6, textColor=ink),
        "dense": ParagraphStyle("d", fontName=regular, fontSize=7, leading=8.4, textColor=ink),
        "label": ParagraphStyle("l", fontName=regular, fontSize=7.2, leading=9, textColor=soft),
        "value": ParagraphStyle("v", fontName=bold, fontSize=9.4, leading=12, textColor=ink),
        "tile": ParagraphStyle("k", fontName=bold, fontSize=13, leading=16, textColor=ink),
    }

    def para(text: str, style: str, *, color: Any = None, strong: bool = False,
             align: str = "") -> Any:
        markup = escape(_printable(text)).replace("\n", "<br/>")
        if strong:
            markup = f'<font name="{bold}">{markup}</font>'
        if color is not None:
            markup = f'<font color="{color.hexval()}">{markup}</font>'
        base = styles[style]
        if align:
            base = ParagraphStyle(f"{style}-{align}", parent=base,
                                  alignment={"R": 2, "C": 1}.get(align, 0))
        return Paragraph(markup or "&nbsp;", base)

    logo = logo_drawable(letterhead.logo)

    parts = document.parts

    # --- sayfa süslemesi (her sayfa sonunda) ---
    def decorate(canvas: Any, doc: Any) -> None:
        index = getattr(canvas, "_ak_part", 0)
        part = parts[index]
        canvas.saveState()
        top = height - margin
        # Logo / monogram
        box_h = 14 * mm
        text_x = margin
        if logo is not None:
            vector = not isinstance(logo, ImageReader)
            raw_w, raw_h = (float(logo.width), float(logo.height)) if vector else logo.getSize()
            scale = min(36 * mm / raw_w, box_h / raw_h)
            draw_w, draw_h = raw_w * scale, raw_h * scale
            y = top - box_h + (box_h - draw_h) / 2
            if vector:
                from reportlab.graphics import renderPDF

                canvas.saveState()
                canvas.translate(margin, y)
                canvas.scale(scale, scale)
                renderPDF.draw(logo, canvas, 0, 0)
                canvas.restoreState()
            else:
                canvas.drawImage(logo, margin, y, draw_w, draw_h, mask="auto", preserveAspectRatio=True)
            text_x = margin + draw_w + 4 * mm
        else:
            canvas.setFillColor(accent)
            canvas.roundRect(margin, top - box_h, box_h, box_h, 3 * mm, stroke=0, fill=1)
            canvas.setFillColor(colors.white)
            canvas.setFont(bold, 13)
            canvas.drawCentredString(margin + box_h / 2, top - box_h / 2 - 4.5,
                                     initials(letterhead.name))
            text_x = margin + box_h + 4 * mm
        right_x = width - margin
        right_room = 82 * mm
        name_room = right_x - right_room - text_x
        canvas.setFillColor(ink)
        canvas.setFont(bold, 12)
        canvas.drawString(text_x, top - 5 * mm, _fit(letterhead.name, bold, 12, name_room))
        if letterhead.detail:
            # Adres · telefon · e-posta: sığmazsa " · " yerinden iki satıra bölünür.
            canvas.setFillColor(soft)
            canvas.setFont(regular, 7.4)
            for row, text in enumerate(_wrap_detail(letterhead.detail, regular, 7.4, name_room)):
                canvas.drawString(text_x, top - (9.5 + row * 3.4) * mm, text)
        # Belge adı + numara + tarih
        canvas.setFillColor(accent)
        canvas.setFont(bold, 10.5)
        canvas.drawRightString(right_x, top - 4.6 * mm, _fit(part.title, bold, 10.5, right_room))
        canvas.setFillColor(soft)
        canvas.setFont(regular, 7.4)
        number = stamp.number if len(parts) == 1 else f"{stamp.number}-{index + 1:03d}"
        canvas.drawRightString(right_x, top - 9.2 * mm, f"Belge no: {number}")
        canvas.drawRightString(right_x, top - 12.6 * mm, f"Düzenlenme: {stamp.issued}")
        # Başlık çizgisi
        canvas.setStrokeColor(accent)
        canvas.setLineWidth(1.4)
        canvas.line(margin, top - 16.5 * mm, right_x, top - 16.5 * mm)
        canvas.setStrokeColor(line)
        canvas.setLineWidth(0.5)
        canvas.line(margin, top - 17.4 * mm, right_x, top - 17.4 * mm)
        # Filigran (taslak)
        if part.watermark:
            # Süsleme sayfa SONUNDA çizilir (parça işareti o zaman bilinir); yazı
            # okunsun diye filigran yarı saydamdır.
            canvas.saveState()
            canvas.setFillColor(colors.HexColor("#8A94A6"))
            canvas.setFillAlpha(0.13)
            canvas.setFont(bold, 64)
            canvas.translate(width / 2, height / 2)
            canvas.rotate(35)
            canvas.drawCentredString(0, 0, part.watermark.upper())
            canvas.restoreState()
        # Altbilgi
        base = margin
        canvas.setStrokeColor(line)
        canvas.setLineWidth(0.5)
        canvas.line(margin, base + 8 * mm, right_x, base + 8 * mm)
        canvas.setFillColor(soft)
        canvas.setFont(regular, 7.2)
        canvas.drawString(margin, base + 4.6 * mm,
                          _fit(f"Düzenleyen: {stamp.author} · Kontrol Merkezi · BBD Akademi",
                               regular, 7.2, usable - 40 * mm))
        canvas.setFont(regular, 6.3)
        canvas.drawString(margin, base + 1.2 * mm, _fit(stamp.confidential, regular, 6.3, usable))
        canvas.restoreState()

    page_font = (regular, 7.6)

    class _Canvas(rl_canvas.Canvas):
        """"Sayfa x / y" — y yalnız belge bitince bilinir; sayılar en sonda yazılır.

        Toplu baskıda (her öğrencinin karnesi bir parça) sayfa sayısı PARÇA
        içindedir: "Sayfa 2 / 3" o öğrencinin üç sayfasının ikincisidir."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._ak_part = 0
            self._ak_pages: list[dict[str, Any]] = []

        def showPage(self) -> None:  # reportlab'in adı
            self._ak_pages.append(dict(self.__dict__))
            self._startPage()

        def save(self) -> None:
            totals = Counter(state.get("_ak_part", 0) for state in self._ak_pages)
            seen: defaultdict[int, int] = defaultdict(int)
            for state in self._ak_pages:
                self.__dict__.update(state)
                part = state.get("_ak_part", 0)
                seen[part] += 1
                self.saveState()
                self.setFont(*page_font)
                self.setFillColor(soft)
                self.drawRightString(width - margin, margin + 4.6 * mm,
                                     f"Sayfa {seen[part]} / {totals[part]}")
                self.restoreState()
                super().showPage()
            super().save()

    class _PartStart(Flowable):
        """Parçanın ilk sayfasını işaretler (başlık ve sayfa sayısı parçaya göre)."""

        def __init__(self, index: int) -> None:
            super().__init__()
            self.index = index

        def wrap(self, available_width: float, available_height: float) -> tuple[float, float]:
            return 0, 0

        def draw(self) -> None:
            self.canv._ak_part = self.index

    class _Bar(Flowable):
        def __init__(self, ratio: float, bar_width: float) -> None:
            super().__init__()
            self.ratio = max(0.0, min(1.0, ratio))
            self.bar_width = bar_width

        def wrap(self, available_width: float, available_height: float) -> tuple[float, float]:
            return self.bar_width, 7

        def draw(self) -> None:
            self.canv.setFillColor(tint)
            self.canv.roundRect(0, 0.5, self.bar_width, 6, 2, stroke=0, fill=1)
            if self.ratio > 0:
                self.canv.setFillColor(accent)
                self.canv.roundRect(0, 0.5, max(3, self.bar_width * self.ratio), 6, 2,
                                    stroke=0, fill=1)

    # --- bölüm çizicileri ---
    def fields_block(items: list[tuple[str, str, str]], columns: int, *, tiles: bool) -> Any:
        columns = max(1, min(columns, len(items) or 1))
        rows, current = [], []
        for label, value, tone in items:
            value_style = "tile" if tiles else "value"
            current.append([para(label, "label"),
                            para(value or "—", value_style,
                                 color=tone_ink.get(tone) if tone else None)])
            if len(current) == columns:
                rows.append(current)
                current = []
        if current:
            current += [[para("", "label")]] * (columns - len(current))
            rows.append(current)
        table = Table(rows, colWidths=[usable / columns] * columns)
        style: list[Any] = [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]
        if tiles:
            style += [("BACKGROUND", (0, 0), (-1, -1), tint),
                      ("INNERGRID", (0, 0), (-1, -1), 1.5, colors.white),
                      ("BOX", (0, 0), (-1, -1), 0.5, tint_strong)]
        else:
            style += [("BOX", (0, 0), (-1, -1), 0.5, line),
                      ("LINEBELOW", (0, 0), (-1, -2), 0.3, line),
                      ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FBFCFE"))]
        table.setStyle(TableStyle(style))
        return table

    def table_block(section: dict[str, Any]) -> list[Any]:
        headers = section["headers"]
        dense = section["dense"] or len(headers) > 12
        cell_style = "dense" if dense else "cell"
        scale = usable / sum(section["widths"])
        align = section["align"]
        data = [[para(text, cell_style, strong=True, align=align[index])
                 for index, text in enumerate(headers)]]
        style: list[Any] = [
            ("BOX", (0, 0), (-1, -1), 0.5, line),
            ("LINEBELOW", (0, 0), (-1, 0), 0.9, accent),
            ("INNERGRID", (0, 1), (-1, -1), 0.25, line),
            ("BACKGROUND", (0, 0), (-1, 0), tint),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 2.5 if dense else 4.5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 2.5 if dense else 4.5),
            ("TOPPADDING", (0, 0), (-1, -1), 1.8 if dense else 3.2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1.8 if dense else 3.2),
        ]
        body_rows = section["rows"]
        if not body_rows:
            data.append([para(section["empty"], cell_style, color=soft)]
                        + [para("", cell_style)] * (len(headers) - 1))
            style.append(("SPAN", (0, 1), (-1, 1)))
        for row_index, row in enumerate(body_rows, start=1):
            cells = []
            for column, cell in enumerate(row["cells"]):
                tone = cell["tone"] or row["tone"]
                cells.append(para(cell["text"], cell_style,
                                  strong=cell["bold"] or row["bold"],
                                  color=tone_ink.get(tone) if tone in ("good", "bad", "warn",
                                                                       "muted", "info") else None,
                                  align=align[column]))
                if cell["tone"]:
                    style.append(("BACKGROUND", (column, row_index), (column, row_index),
                                  tone_fill[cell["tone"]]))
            data.append(cells)
            if row["tone"]:
                style.append(("BACKGROUND", (0, row_index), (-1, row_index),
                              tone_fill[row["tone"]]))
            elif row_index % 2 == 0:
                style.append(("BACKGROUND", (0, row_index), (-1, row_index), zebra))
        if section["footer"] is not None:
            footer = section["footer"]
            data.append([para(cell["text"], cell_style, strong=True, align=align[column])
                         for column, cell in enumerate(footer["cells"])])
            style += [("BACKGROUND", (0, -1), (-1, -1), tint),
                      ("LINEABOVE", (0, -1), (-1, -1), 0.9, accent)]
        table = Table(data, colWidths=[ratio * scale for ratio in section["widths"]],
                      repeatRows=1, splitByRow=1)
        table.setStyle(TableStyle(style))
        flow: list[Any] = [table, Spacer(1, 6)]
        if section["keep"]:
            return [KeepTogether(flow)]
        return flow

    def bars_block(items: list[tuple[str, float, str]], ceiling: float) -> Any:
        peak = ceiling or max((value for _, value, _ in items), default=0) or 1
        bar_width = usable * 0.46
        data = [[para(label, "cell"), _Bar(value / peak, bar_width), para(display, "cell", align="R")]
                for label, value, display in items]
        table = Table(data, colWidths=[usable * 0.34, usable * 0.5, usable * 0.16])
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 1.6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1.6),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ]))
        return table

    def signatures_block(labels: list[str]) -> Any:
        count = max(1, len(labels))
        row_lines = [[para("", "cell") for _ in labels]]
        row_labels = [[para(label, "label", align="C") for label in labels]]
        table = Table([row_lines[0], row_labels[0]], colWidths=[usable / count] * count,
                      rowHeights=[16 * mm, None])
        style: list[Any] = [("ALIGN", (0, 0), (-1, -1), "CENTER")]
        for column in range(count):
            style.append(("LINEBELOW", (column, 0), (column, 0), 0.6, soft))
        style += [("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10)]
        table.setStyle(TableStyle(style))
        return KeepTogether([Spacer(1, 6), table])

    story: list[Any] = []
    for index, part in enumerate(parts):
        if index:
            story.append(PageBreak())
        story.append(_PartStart(index))
        story.append(para(part.title, "title"))
        if part.subtitle:
            story.append(para(part.subtitle, "subtitle"))
        story.append(Spacer(1, 4))
        if part.meta:
            story.append(fields_block([(label, value, "") for label, value in part.meta],
                                      min(4, len(part.meta)), tiles=False))
            story.append(Spacer(1, 6))
        for section in part.sections:
            kind = section["kind"]
            heading = [para(section["title"], "section")] if section.get("title") else []
            if kind == "break":
                story.append(PageBreak())
                continue
            if kind == "heading":
                story.append(CondPageBreak(30 * mm))
                story.extend(heading)
                continue
            if kind == "table":
                flow = table_block(section)
                # Başlık tablosuz sayfa sonunda yalnız kalmasın.
                story.append(CondPageBreak(24 * mm))
                story.extend(heading + flow)
            elif kind in ("fields", "tiles"):
                block = fields_block(section["items"], section["columns"], tiles=kind == "tiles")
                story.append(KeepTogether(heading + [block, Spacer(1, 6)]))
            elif kind == "bars":
                story.append(CondPageBreak(24 * mm))
                story.extend(heading + [bars_block(section["items"], section["max"]), Spacer(1, 6)])
            elif kind == "text":
                story.extend(heading + [para(section["text"], "body"), Spacer(1, 5)])
            elif kind == "note":
                story.extend(heading + [para(section["text"], "note"), Spacer(1, 4)])
            elif kind == "list":
                story.extend(heading)
                for entry in section["items"]:
                    story.append(Paragraph(f"•&nbsp;&nbsp;{escape(entry)}", styles["body"]))
                story.append(Spacer(1, 5))
            elif kind == "signatures":
                story.extend(heading + [signatures_block(section["items"])])

    buffer = io.BytesIO()
    doc = BaseDocTemplate(
        buffer, pagesize=size,
        leftMargin=margin, rightMargin=margin,
        topMargin=margin + header_h, bottomMargin=margin + footer_h,
        title=document.title, author=f"{letterhead.name} · Kontrol Merkezi",
        subject=document.title, creator="Kontrol Merkezi · BBD Akademi",
    )
    frame = Frame(margin, margin + footer_h, usable, height - 2 * margin - header_h - footer_h,
                  id="body", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id="ak", frames=[frame], onPageEnd=decorate)])
    doc.build(story, canvasmaker=_Canvas)
    return buffer.getvalue()


def _wrap_detail(text: str, font: str, size: float, room: float) -> list[str]:
    """Başlık alt satırı en çok iki satır; parçalar " · " ile ayrılır."""
    from reportlab.pdfbase.pdfmetrics import stringWidth

    if stringWidth(text, font, size) <= room:
        return [text]
    pieces = [piece.strip() for piece in text.split("·") if piece.strip()]
    first: list[str] = []
    while pieces and stringWidth(" · ".join([*first, pieces[0]]), font, size) <= room:
        first.append(pieces.pop(0))
    if not first:
        return [_fit(text, font, size, room)]
    rows = [" · ".join(first)]
    if pieces:
        rows.append(_fit(" · ".join(pieces), font, size, room))
    return rows


def _fit(text: str, font: str, size: float, room: float) -> str:
    """Metni verilen genişliğe sığdırır (sonuna … koyarak)."""
    from reportlab.pdfbase.pdfmetrics import stringWidth

    if room <= 0:
        return ""
    if stringWidth(text, font, size) <= room:
        return text
    while text and stringWidth(text + "…", font, size) > room:
        text = text[:-1]
    return text.rstrip() + "…"
