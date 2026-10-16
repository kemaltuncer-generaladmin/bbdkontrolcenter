"""Rapor çıktısı — PDF ve Excel uyumlu CSV.

NEDEN ÇEKİRDEKTE. Bu üreteç ilk olarak `modules/bbd_canteen_reports` içinde
yazıldı ve orada tek modüle hizmet ettiği sürece doğru yerdeydi. BBD Store 20
ekran ekliyor ve bunların çoğu PDF/CSV üretecek. Modül modülü import edemez
(K3), dolayısıyla tek alternatif dosyayı 20 kez kopyalamaktı: 250 satır × 20 =
5.000 satır kopya ve tablo stilinde bir düzeltmenin 20 dosyada tekrarlanması.

Bu dosya iş kuralı taşımaz — yalnız biçim. `km_sdk` üzerinden modüllere açılır.
`bbd_canteen_reports` de artık bu üreteci kullanır (kendi kopyası kaldırıldı).

PDF `reportlab` ile üretilir (çekirdeğin `reporting` extra'sı, ADR 0008). Türkçe
karakterler için sistemdeki DejaVuSans kullanılır: reportlab'in yerleşik
Helvetica'sı Latin-1'dir, 'ğ', 'ş', 'İ' karelere döner. Font `fonts-dejavu-core`
paketinden gelir, depoya kopyalanmaz (K11).

reportlab kurulu değilse çekirdek ve modüller YİNE ÇALIŞIR; yalnız PDF üreten
uç anlaşılır bir hata döner (K7). Bu yüzden import fonksiyon içindedir.

TEK RAPOR ŞABLONU (63-finans-pdf, 16.10.2026). Kullanıcı: "finansal raporlarda
pdf önizleme ve yazdırma olsun". Bütün finansal raporlar (kantin satış, ödeme
talepleri, nakit tahsilat, öğle yemeği …) buradan çıkar ve aynı kâğıdı taşır:
kurum adı/logosu, rapor başlığı, EKRANDAKİ SÜZGEÇLERİN ÖZETİ (dönem, sınıf,
durum…), hazırlayan ve hazırlanma anı, her sayfada "Sayfa X / Y", her sayfada
tekrar eden tablo başlığı, ara/genel toplam satırları, `1.234,56 ₺` para
biçimi, gömülü Türkçe yazı tipi ve KVKK maskelemesi (telefon numaraları).
Akademi belgeleri aynı öğeleri `modules/bbd_akademi/backend/document_pdf.py`
ile basar (kurum teması ve belge numarası orada). Eski çağrılar (`build_pdf`
yalnız `title/subtitle/sections/footer`) DEĞİŞMEDEN çalışır — yeni alanların
hepsi isteğe bağlıdır.

Önizleme: `pdf_preview_pages` PDF'in sayfalarını `pdftoppm` ile görüntüye
çevirir (önizleme basılacak PDF'in KENDİSİDİR); `pdf_page_count` toplam sayfa
sayısını verir. İlk sayfalar hızlı gelir, kalanı ekran kaydırıldıkça istenir.

CSV: UTF-8 BOM + noktalı virgül. Excel'in Türkçe yerelinde ondalık ayracı virgül
olduğu için virgüllü CSV sütunları kaydırır.
"""

from __future__ import annotations

import asyncio
import base64
import csv
import io
import re
import shutil
import tempfile
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

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
#: Geliştirme makinesi (macOS) yedekleri — Türkçe karakterleri ve ₺'yi taşır.
#: Sunucu imajında DejaVu vardır (`fonts-dejavu-core`); bu satırlar yalnız
#: yerel geliştirmede Helvetica'ya (Latin-1, 'ğ' kare) düşmeyi önler.
FONT_FALLBACKS = [
    ("/System/Library/Fonts/Supplemental/Arial.ttf",
     "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
]

DEFAULT_FOOTER = "Kontrol Merkezi"

_FONTS_READY = False
#: Yazı tipi "₺" (U+20BA) taşıyor mu? Taşımıyorsa kâğıda "TL" yazılır.
_LIRA = True

#: Önizleme: ilk istekte gösterilen sayfa sayısı ve çözünürlük. Büyük raporda
#: (yüzlerce sayfa) hepsini görüntüye çevirmek önizlemeyi dakikalarca
#: geciktirirdi; kalan sayfalar kullanıcı kaydırdıkça parça parça istenir.
PREVIEW_FIRST_PAGES = 4
PREVIEW_DPI = 110
#: Tek parça isteğinde en çok bu kadar sayfa görüntüye çevrilir.
PREVIEW_CHUNK_MAX = 12

#: Türkiye cep/sabit telefonu: 05xx xxx xx xx, +90 5xx…, 0 (212) 555 12 34 …
#: Son iki hane görünür kalır. Maskeli gelen ("*" içeren) dokunulmaz.
_PHONE = re.compile(
    r"(?<![\d*])(?:\+?90[\s.-]?)?\(?0?\s?[2-5]\d{2}\)?[\s.-]?\d{3}[\s.-]?\d{2}[\s.-]?(\d{2})(?![\d*])")
#: Çağıranın bilerek koyduğu biçim etiketleri (`<b>`, `<font …>`). Bunları
#: taşıyan metin olduğu gibi geçer; taşımayan metin KAÇIŞLANIR — öğrenci
#: adındaki "&" ya da notlardaki "<" reportlab'in işaret dilini bozuyordu.
_MARKUP = re.compile(r"</?(?:b|i|u|br|font|super|sub|strike)\b", re.IGNORECASE)


class ExportError(RuntimeError):
    """Çıktı üretilemedi — sebebi kullanıcıya gösterilir."""


#: Ayraç takasında kullanılan nöbetçi.
#:
#: `1,234.56` → `1.234,56` dönüşümü üç adımlı bir takastır ve ara adımda
#: kullanılan karakter metinde BAŞKA HİÇBİR YERDE bulunmamalıdır. Sıradan
#: boşluk kullanılırsa para biriminden önceki boşluk da noktaya döner ve
#: `12.345,67.₺` çıkar. Kantin sürümü bu tuzağı U+00A0 (kırılmaz boşluk) ile
#: aşmış; doğru ama GÖRÜNMEZ — dosyadan dosyaya kopyalanırken sıradan boşluğa
#: normalleşirse hata sessizce geri gelir (bu dosya yazılırken tam olarak bu
#: oldu, test yakaladı). Bu yüzden nöbetçi burada metinde asla bulunamayacak
#: ve gözle ayırt edilebilir bir kaçış dizisidir.
_SWAP = "\x00"


def _tr_separators(text: str) -> str:
    """`1,234.56` (İngiliz) → `1.234,56` (Türk). Tek geçişte, güvenli takas."""
    return text.replace(",", _SWAP).replace(".", ",").replace(_SWAP, ".")


def money(kurus: Any) -> str:
    """Kuruş tam sayısı → `12.345,67 ₺`.

    Para her yerde kuruş (int) taşınır; float yuvarlama hatası muhasebeye
    sızmasın diye biçimlendirme yalnız burada, gösterim anında yapılır.

    Para birimi ayraç takasından SONRA eklenir — takasın içine girerse
    öncesindeki boşluk noktaya döner.
    """
    value = int(kurus or 0) / 100
    return f"{_tr_separators(f'{value:,.2f}')} ₺"


def number(value: Any, decimals: int = 0) -> str:
    """Türkçe binlik/ondalık ayracıyla sayı: 1419 → `1.419`."""
    amount = float(value or 0)
    return _tr_separators(f"{amount:,.{decimals}f}")


def percent(value: Any, decimals: int = 1) -> str:
    """12.5 → `%12,5`. Yüzde işareti Türkçede önde durur."""
    return f"%{number(value, decimals)}"


# ------------------------------------------------------------------- CSV

def csv_bytes(headers: list[str], rows: list[list[Any]]) -> bytes:
    """UTF-8 BOM + `;` — Excel'in Türkçe yerelinde doğru açılır."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", quoting=csv.QUOTE_MINIMAL,
                        lineterminator="\r\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")




# ------------------------------------------------------------------- KVKK

def mask_phones(text: str) -> str:
    """Metindeki telefon numaralarını maskeler; son iki hane görünür kalır."""
    return _PHONE.sub(lambda match: f"0*** *** ** {match.group(1)}", text)


def filters_line(filters: Iterable[tuple[str, Any]] | None) -> str:
    """`[("Dönem", "1–16 Ekim"), ("Sınıf", "8-A")]` → `Dönem: 1–16 Ekim · Sınıf: 8-A`.

    Boş değerli süzgeç yazılmaz; ekranda "Tümü" seçiliyse çağıran "Tümü" yazar —
    kâğıdı eline alan kişi süzgecin UNUTULMADIĞINI görmelidir.
    """
    parts = []
    for label, value in filters or ():
        text = str(value if value is not None else "").strip()
        if text:
            parts.append(f"{label}: {text}")
    return " · ".join(parts)


# ------------------------------------------------------------------- PDF

def _ensure_fonts() -> tuple[str, str]:
    """DejaVu'yu bir kez kaydeder. Yoksa macOS Arial, o da yoksa Helvetica."""
    global _FONTS_READY, _LIRA
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if _FONTS_READY:
        return "KM-Sans", "KM-Sans-Bold"

    regular = next((path for path in FONT_CANDIDATES if Path(path).exists()), None)
    bold = next((path for path in FONT_BOLD_CANDIDATES if Path(path).exists()), None)
    if regular is None:
        for candidate, candidate_bold in FONT_FALLBACKS:
            if Path(candidate).exists():
                regular = candidate
                bold = candidate_bold if Path(candidate_bold).exists() else None
                break
    if regular is None:
        # Türkçe karakterler bozulur ama rapor yine üretilir — sessiz kalmayız.
        _LIRA = False
        return "Helvetica", "Helvetica-Bold"

    font = TTFont("KM-Sans", regular)
    pdfmetrics.registerFont(font)
    pdfmetrics.registerFont(TTFont("KM-Sans-Bold", bold or regular))
    _LIRA = 0x20BA in getattr(font.face, "charToGlyph", {0x20BA: 1})
    _FONTS_READY = True
    return "KM-Sans", "KM-Sans-Bold"


def _markup(value: Any, *, mask: bool) -> str:
    """Hücre metni → reportlab işaret dili. Bilinçli etiket yoksa kaçışlanır."""
    text = "" if value is None else str(value)
    if mask:
        text = mask_phones(text)
    if not _LIRA:
        text = text.replace("₺", "TL")
    if _MARKUP.search(text):
        return text
    return escape(text).replace("\n", "<br/>")


def _row_parts(row: Any) -> tuple[list[Any], str]:
    """Tablo satırı: düz liste ya da `{"cells": [...], "kind": "subtotal"|"total"|"group"}`."""
    if isinstance(row, dict):
        cells = row.get("cells")
        kind = str(row.get("kind") or "")
        return (list(cells) if isinstance(cells, list | tuple) else []), kind
    return list(row), ""


def build_pdf(*, title: str, subtitle: str, sections: list[dict[str, Any]],
              footer: str = "", filters: Sequence[tuple[str, Any]] | None = None,
              author: str = "", organization: str = "", logo: bytes | None = None,
              orientation: str = "portrait", confidential: str = "",
              mask: bool = False) -> bytes:
    """Çok bölümlü A4 rapor — finansal raporların ortak şablonu.

    `sections` öğeleri:
      {"kind": "tiles",  "title": …, "tiles": [(etiket, değer), …]}
      {"kind": "table",  "title": …, "headers": [...], "rows": [[...]],
       "align": "LRRR", "widths": [oran, …],
       "footer": [...] | [[...], …],          ← genel toplam satır(lar)ı
       "empty": "Kayıt yok."}
         satır `{"cells": [...], "kind": "subtotal"}` ise ARA TOPLAM (kalın,
         açık zemin), `"group"` ise grup başlığı (tek hücre, bütün satır).
      {"kind": "bars",   "title": …, "bars": [(etiket, değer, gösterim), …]}
      {"kind": "note",   "text": …}
      {"kind": "break"}                       ← yeni sayfa

    Yeni alanlar (hepsi isteğe bağlı, eski çağrılar aynen çalışır):
      filters      — ekrandaki süzgeçler [(etiket, değer)]; başlıkta yazar.
      author       — hazırlayan (KM oturumundaki kişi).
      organization — kurum adı (her sayfanın üst satırında).
      logo         — PNG/JPEG baytları (bozuksa yok sayılır).
      orientation  — "portrait" (dikey) | "landscape" (yatay).
      confidential — altbilgideki gizlilik notu (KVKK).
      mask         — metindeki telefon numaralarını maskele.
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfgen import canvas as rl_canvas
        from reportlab.platypus import (
            BaseDocTemplate,
            CondPageBreak,
            Frame,
            PageBreak,
            PageTemplate,
            Paragraph,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as error:  # pragma: no cover - bağımlılık yoksa
        raise ExportError(
            "PDF için `reportlab` gerekli. Kurulum: scripts/install-deps.sh"
        ) from error

    regular, bold = _ensure_fonts()
    buffer = io.BytesIO()

    ink = colors.HexColor("#111722")
    soft = colors.HexColor("#5D6878")
    line = colors.HexColor("#e0e5ee")
    accent = colors.HexColor("#3f6fd8")
    tint = colors.HexColor("#f5f7fb")
    strong_tint = colors.HexColor("#e8eefb")

    styles = {
        "title": ParagraphStyle("t", fontName=bold, fontSize=17, leading=21, textColor=ink),
        "subtitle": ParagraphStyle("s", fontName=regular, fontSize=9.5, leading=13,
                                   textColor=soft),
        "meta": ParagraphStyle("m", fontName=regular, fontSize=8.4, leading=11.5,
                               textColor=ink),
        "section": ParagraphStyle("h", fontName=bold, fontSize=11.5, leading=15, textColor=ink,
                                  spaceBefore=10, spaceAfter=5),
        "note": ParagraphStyle("n", fontName=regular, fontSize=8.5, leading=12, textColor=soft),
        "cell": ParagraphStyle("c", fontName=regular, fontSize=8.5, leading=11, textColor=ink),
        "cell-r": ParagraphStyle("cr", fontName=regular, fontSize=8.5, leading=11,
                                 textColor=ink, alignment=2),
        "cell-c": ParagraphStyle("cc", fontName=regular, fontSize=8.5, leading=11,
                                 textColor=ink, alignment=1),
    }

    def cell_style(letter: str) -> Any:
        return styles["cell-r"] if letter == "R" else styles["cell-c"] if letter == "C" \
            else styles["cell"]

    def text(value: Any) -> str:
        return _markup(value, mask=mask)

    landscape_mode = orientation == "landscape"
    size = landscape(A4) if landscape_mode else A4
    width, height = size
    margin = 16 * mm
    stamp = datetime.now(UTC).astimezone().strftime("%d.%m.%Y %H:%M")
    foot = footer or DEFAULT_FOOTER
    running = " · ".join(item for item in (organization.strip(), title.strip()) if item)

    image = None
    if logo:
        try:
            image = ImageReader(io.BytesIO(logo))
            image.getSize()
        except Exception:  # noqa: BLE001 — bozuk logo raporu durdurmaz (K7)
            image = None

    def decorate(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        # Başlık şeridi
        canvas.setFillColor(accent)
        canvas.rect(0, height - 8 * mm, width, 8 * mm, stroke=0, fill=1)
        if doc.page > 1 and running:
            # Devam sayfalarında hangi raporun hangi kurumdan olduğu yazar:
            # sayfalar karışsa bile kâğıt kendini tanıtır.
            canvas.setFont(regular, 7.5)
            canvas.setFillColor(colors.white)
            canvas.drawString(margin, height - 5.4 * mm, running[:140])
        # Alt bilgi
        canvas.setFont(regular, 7.5)
        canvas.setFillColor(soft)
        left = foot if not author else f"{foot} · Hazırlayan: {author}"
        canvas.drawString(margin, 10 * mm, left[:160])
        if confidential:
            canvas.setFont(regular, 6.4)
            canvas.drawString(margin, 6.6 * mm, confidential[:220])
        canvas.setStrokeColor(line)
        canvas.line(margin, 13 * mm, width - margin, 13 * mm)
        canvas.restoreState()

    class NumberedCanvas(rl_canvas.Canvas):  # type: ignore[misc]
        """"Sayfa X / Y" — Y yalnız belge bitince bilinir; sayılar en sonda yazılır."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._km_pages: list[dict[str, Any]] = []

        def showPage(self) -> None:  # reportlab'in adı
            self._km_pages.append(dict(self.__dict__))
            self._startPage()

        def save(self) -> None:
            total = len(self._km_pages)
            for index, state in enumerate(self._km_pages, start=1):
                self.__dict__.update(state)
                self.saveState()
                self.setFont(regular, 7.5)
                self.setFillColor(soft)
                self.drawRightString(width - margin, 10 * mm,
                                     f"{stamp} · Sayfa {index} / {total}")
                self.restoreState()
                super().showPage()
            super().save()

    doc = BaseDocTemplate(
        buffer, pagesize=size,
        leftMargin=margin, rightMargin=margin,
        topMargin=margin, bottomMargin=18 * mm,
        title=title, author=author or "Kontrol Merkezi",
        subject=title, creator="Kontrol Merkezi",
    )
    frame = Frame(margin, 18 * mm, width - 2 * margin, height - margin - 18 * mm, id="body")
    doc.addPageTemplates([PageTemplate(id="std", frames=[frame], onPage=decorate)])

    usable = width - 2 * margin
    story: list[Any] = []

    # --- başlık bloğu: logo + kurum + rapor adı + süzgeçler + hazırlayan ---
    heading: list[Any] = []
    if organization:
        heading.append(Paragraph(text(organization), styles["subtitle"]))
    heading += [Paragraph(text(title), styles["title"])]
    if subtitle:
        heading.append(Paragraph(text(subtitle), styles["subtitle"]))
    if image is not None:
        raw_w, raw_h = image.getSize()
        scale = min(34 * mm / raw_w, 15 * mm / raw_h)
        from reportlab.platypus import Image as RLImage

        logo_flow = RLImage(io.BytesIO(logo or b""), width=raw_w * scale, height=raw_h * scale)
        head = Table([[logo_flow, heading]], colWidths=[38 * mm, usable - 38 * mm])
        head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                  ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        story.append(head)
    else:
        story.extend(heading)

    meta_rows = []
    summary = filters_line(filters)
    if summary:
        meta_rows.append(["Süzgeç", summary])
    prepared = f"{stamp}" + (f" · {author}" if author else "")
    if filters is not None or author:
        meta_rows.append(["Hazırlanma", prepared])
    if meta_rows:
        meta = Table([[Paragraph(f'<font name="{bold}">{label}</font>', styles["meta"]),
                       Paragraph(text(value), styles["meta"])] for label, value in meta_rows],
                     colWidths=[24 * mm, usable - 24 * mm])
        meta.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.5, line),
            ("BACKGROUND", (0, 0), (-1, -1), tint),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.extend([Spacer(1, 5), meta])
    story.append(Spacer(1, 7))

    for section in sections:
        kind = section.get("kind")
        title_flow = [Paragraph(text(section["title"]), styles["section"])] \
            if section.get("title") else []

        if kind == "tiles":
            story.extend(title_flow)
            tiles = section["tiles"]
            # Satır başına en çok 4 kutu (yatayda 5) — daha fazlası okunmaz olur.
            per_row = 5 if landscape_mode else 4
            for start in range(0, len(tiles), per_row):
                chunk = tiles[start:start + per_row]
                data = [[Paragraph(f'<font size="7.5" color="#5D6878">{text(label)}</font><br/>'
                                   f'<font size="12">{text(value)}</font>', styles["cell"])
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
            columns = len(headers)
            align = (section.get("align") or "").upper()[:columns].ljust(columns, "L")
            ratios = list(section.get("widths") or [1] * columns)[:columns]
            ratios += [1] * (columns - len(ratios))
            scale = usable / sum(ratios)
            data = [[Paragraph(f'<font name="{bold}">{text(item)}</font>',
                               cell_style(align[index]))
                     for index, item in enumerate(headers)]]
            style: list[Any] = [
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
            rows = section.get("rows") or []
            if not rows:
                data.append([Paragraph(text(section.get("empty") or "Kayıt yok."),
                                       styles["note"])] + [""] * (columns - 1))
                style.append(("SPAN", (0, 1), (-1, 1)))
            for raw in rows:
                cells, row_kind = _row_parts(raw)
                index = len(data)
                if row_kind == "group":
                    data.append([Paragraph(f'<font name="{bold}">{text(cells[0] if cells else "")}'
                                           f'</font>', styles["cell"])] + [""] * (columns - 1))
                    style += [("SPAN", (0, index), (-1, index)),
                              ("BACKGROUND", (0, index), (-1, index), strong_tint)]
                    continue
                cells = (cells + [""] * columns)[:columns]
                if row_kind in ("subtotal", "total"):
                    data.append([Paragraph(f'<font name="{bold}">{text(cell)}</font>',
                                           cell_style(align[column]))
                                 for column, cell in enumerate(cells)])
                    style += [("BACKGROUND", (0, index), (-1, index), strong_tint),
                              ("LINEABOVE", (0, index), (-1, index), 0.6, accent)]
                else:
                    data.append([Paragraph(text(cell), cell_style(align[column]))
                                 for column, cell in enumerate(cells)])
            footer_rows = section.get("footer")
            if footer_rows:
                if footer_rows and not isinstance(footer_rows[0], list | tuple | dict):
                    footer_rows = [footer_rows]
                for raw in footer_rows:
                    cells, _ = _row_parts(raw)
                    cells = (cells + [""] * columns)[:columns]
                    index = len(data)
                    data.append([Paragraph(f'<font name="{bold}">{text(cell)}</font>',
                                           cell_style(align[column]))
                                 for column, cell in enumerate(cells)])
                    style += [("BACKGROUND", (0, index), (-1, index), strong_tint),
                              ("LINEABOVE", (0, index), (-1, index), 0.9, accent)]
            for index, letter in enumerate(align):
                if letter == "R":
                    style.append(("ALIGN", (index, 0), (index, -1), "RIGHT"))
                elif letter == "C":
                    style.append(("ALIGN", (index, 0), (index, -1), "CENTER"))
            table = Table(data, colWidths=[ratio * scale for ratio in ratios], repeatRows=1,
                          splitByRow=1)
            table.setStyle(TableStyle(style))
            # Başlık tablosuz sayfa sonunda yalnız kalmasın.
            story.append(CondPageBreak(24 * mm))
            story.extend(title_flow + [table, Spacer(1, 5)])

        elif kind == "bars":
            story.extend(title_flow)
            bars = section["bars"]
            peak = max((value for _, value, _ in bars), default=0) or 1
            data = []
            for label, value, display in bars:
                # Çubuk metinle çizilir: reportlab çizim nesnesi eklemeden,
                # her yazıcıda aynı görünen basit ve güvenilir yol.
                filled = round(value * 34 / peak)
                bar = "█" * filled + "░" * (34 - filled)
                data.append([
                    Paragraph(text(label), styles["cell"]),
                    Paragraph(f'<font name="{regular}" color="#3f6fd8">{bar}</font>',
                              styles["cell"]),
                    Paragraph(text(display), styles["cell"]),
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
            story.extend(title_flow + [Paragraph(text(section["text"]), styles["note"]),
                                       Spacer(1, 4)])

        elif kind == "break":
            # Rapor paketi (birden çok raporun tek PDF'i) bölümleri ayırır.
            story.append(PageBreak())

    doc.build(story, canvasmaker=NumberedCanvas)
    return buffer.getvalue()


# ------------------------------------------------------------------- önizleme

class PdfPreviewError(RuntimeError):
    """Önizleme görüntüsü üretilemedi. Belge yine de kaydedilmiştir."""


async def pdf_page_count(path: Path) -> int:
    """PDF'in gerçek sayfa sayısı (`pdfinfo`); bilinemezse 0."""
    binary = shutil.which("pdfinfo")
    if not binary:
        return 0
    try:
        process = await asyncio.create_subprocess_exec(
            binary, str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(process.communicate(), timeout=15)
    except (TimeoutError, OSError):
        return 0
    match = re.search(r"^Pages:\s+(\d+)", out.decode(errors="replace"), re.MULTILINE)
    return int(match.group(1)) if match else 0


async def pdf_preview_pages(path: Path, *, first: int = 1, last: int | None = None,
                            dpi: int = PREVIEW_DPI) -> list[str]:
    """PDF'in `first..last` sayfalarını PNG data-URI listesine çevirir.

    Önizleme basılacak PDF'in BİREBİR kendisidir. Tek istekte en çok
    `PREVIEW_CHUNK_MAX` sayfa çevrilir: yüzlerce sayfalık raporu tek seferde
    görüntüye çevirmek hem süreyi hem yanıt boyutunu patlatırdı.
    """
    binary = shutil.which("pdftoppm")
    if not binary:
        raise PdfPreviewError(
            "Önizleme üretilemedi: `pdftoppm` bulunamadı (poppler-utils). "
            "Belge yine de kaydedildi; yazdırabilir ya da PDF olarak kaydedebilirsiniz.")
    start = max(1, int(first))
    stop = start + PREVIEW_FIRST_PAGES - 1 if last is None else int(last)
    stop = max(start, min(stop, start + PREVIEW_CHUNK_MAX - 1))
    resolution = max(60, min(int(dpi), 200))
    with tempfile.TemporaryDirectory(prefix="km-onizleme-") as folder:
        target = Path(folder) / "sayfa"
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                binary, "-png", "-r", str(resolution), "-f", str(start), "-l", str(stop),
                str(path), str(target),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            _, err = await asyncio.wait_for(process.communicate(), timeout=60)
        except TimeoutError:
            if process is not None:
                process.kill()
                await process.wait()
            raise PdfPreviewError("Önizleme üretilemedi: süre aşıldı. Belge yazdırılabilir.") from None
        except OSError as failure:
            raise PdfPreviewError(f"Önizleme üretilemedi: {failure}") from failure
        if process.returncode != 0:
            message = err.decode(errors="replace").strip()[:200] or "bilinmeyen hata"
            raise PdfPreviewError(f"Önizleme üretilemedi: {message}")
        files = sorted(Path(folder).glob("sayfa*.png"),
                       key=lambda item: int(re.sub(r"\D", "", item.stem) or 0))
        return ["data:image/png;base64," + base64.b64encode(item.read_bytes()).decode("ascii")
                for item in files]


async def pdf_preview(path: Path, *, pages: int = PREVIEW_FIRST_PAGES,
                      dpi: int = PREVIEW_DPI) -> dict[str, Any]:
    """Önizleme yanıtının ortak gövdesi: `{pages, pageCount, shown, first, previewError}`.

    Hata FIRLATMAZ: önizleme üretilemese de belge vardır ve basılabilir.
    """
    try:
        images = await pdf_preview_pages(path, first=1, last=pages, dpi=dpi)
    except PdfPreviewError as failure:
        return {"pages": [], "pageCount": await pdf_page_count(path), "shown": 0, "first": 1,
                "previewError": str(failure)}
    total = await pdf_page_count(path) or len(images)
    return {"pages": images, "pageCount": total, "shown": len(images), "first": 1,
            "previewError": ""}
