"""Toplu Satış — parti dökümü PDF'i (63-finans-pdf).

Kullanıcı (16.10.2026): "finansal raporlarda pdf önizleme ve yazdırma olsun".
Toplu satış öğrencilerin KANTİN carisine borç yazar; işlenmiş bir partinin
dökümü (öğrenci, kalemler, tutar, durum) ortak şablondan çıkar
(`km_sdk.build_pdf`) ve tek tip araç çubuğuyla basılır. Yalnız işlenmiş ve
geri alınmamış satırlar toplama girer. Kantin parasıdır (A12).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from km_sdk import (
    ExportError,
    PdfPreviewError,
    build_pdf,
    money,
    pdf_preview,
    pdf_preview_pages,
    report_dir,
    reports_root,
    write_private,
)

TZ = ZoneInfo("Europe/Istanbul")
CATEGORY = "Kantin"
SUBCATEGORY = "Toplu satış"
FOOTER = "Kontrol Merkezi · Toplu Satış"
CONFIDENTIAL = ("Bu rapor kişisel veri içerir (KVKK). Yalnız yetkili personel içindir; "
                "çoğaltılamaz, üçüncü kişilerle paylaşılamaz.")
A12_NOTE = ("Toplu satış öğrencinin KANTİN hesabına borç yazar; kurum cari hesabı (taksit, kitap) "
            "bu belgede yer almaz.")
STATES = {"created": "İşlendi", "duplicate": "İşlendi (zaten vardı)", "pending": "Gönderilmedi"}


def _day(iso: str) -> str:
    try:
        return f"{date.fromisoformat(iso):%d.%m.%Y}"
    except ValueError:
        return iso


def batch_sections(batch: dict[str, Any], entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    live = [row for row in entries if row.get("status") in ("created", "duplicate")
            and not row.get("reversed_at")]

    def state(row: dict[str, Any]) -> str:
        if row.get("reversed_at"):
            return f"Geri alındı: {row.get('reversed_reason') or '—'}"
        return STATES.get(str(row.get("status")), f"Hata: {row.get('reason') or 'bilinmiyor'}")

    return [
        {"kind": "tiles", "title": "Parti özeti", "tiles": [
            ("İşlenen öğrenci", str(len(live))),
            ("Kantine yazılan tutar", money(sum(int(row.get("amount") or 0) for row in live))),
            ("Hata / geri alınan", str(len(entries) - len(live))),
            ("Sepet", "Ayrı sepet" if batch.get("mode") == "per_student" else "Ortak sepet"),
        ]},
        {"kind": "table", "title": f"Öğrenciler ({len(entries)})",
         "headers": ["Öğrenci", "Kalemler", "Tutar", "Durum"], "align": "LLRL",
         "widths": [2.4, 3.6, 1.2, 2],
         "rows": [[row.get("student_name") or row.get("kantin_id") or "—",
                   ", ".join(f"{line.get('qty')}× {line.get('name')}" for line in row.get("items") or [])
                   or "—", money(row.get("amount")), state(row)] for row in entries],
         "footer": [f"Toplam ({len(live)} işlenen)", "",
                    money(sum(int(row.get("amount") or 0) for row in live)), ""],
         "empty": "Bu partide satır yok."},
        *([{"kind": "note", "text": f"Not: {batch.get('note')}"}] if batch.get("note") else []),
        {"kind": "note", "text": "Toplama yalnız kantine işlenmiş ve geri alınmamış satırlar girer. "
                                 + A12_NOTE},
    ]


class BulkSaleReports:
    def __init__(self, *, service: Any, log: Any, fallback_dir: Path,
                 folder: Callable[[], Path] | None = None) -> None:
        """`folder`: testler rapor kökü yerine geçici klasör verir (masaüstüne yazılmaz)."""
        self._service = service
        self._log = log
        self._fallback_dir = fallback_dir
        self._folder_of = folder

    def _folder(self) -> Path:
        if self._folder_of is not None:
            return self._folder_of()
        folder: Path = report_dir(CATEGORY, fallback=self._fallback_dir, subcategory=SUBCATEGORY)
        return folder

    async def produce(self, batch_ref: str, *, orientation: str = "portrait", author: str = "",
                      preview: bool = True) -> dict[str, Any]:
        detail = await self._service.batch_detail(batch_ref)
        if not detail.get("ok"):
            return {"ok": False, "error": detail.get("error") or "Parti bulunamadı."}
        batch, entries = detail["batch"], detail.get("entries") or []
        when = _day(str(batch.get("sale_date") or ""))
        try:
            content = build_pdf(
                title="Toplu Satış Parti Dökümü", subtitle=f"Satış günü {when}",
                sections=batch_sections(batch, entries), footer=FOOTER,
                filters=[("Satış günü", when), ("Parti", batch_ref),
                         ("İşleyen", batch.get("created_by") or "—")],
                author=author, orientation=orientation, confidential=CONFIDENTIAL, mask=True)
        except ExportError as failure:
            return {"ok": False, "error": str(failure)}
        name = f"toplu-satis-{batch.get('sale_date')}-{datetime.now(TZ):%Y%m%d-%H%M%S}.pdf"
        path = write_private(self._folder() / name, content)
        self._log.info("toplu satış dökümü üretildi", batch=batch_ref, bytes=len(content))
        result = {"ok": True, "path": str(path), "name": name, "bytes": len(content)}
        if not preview:
            return {**result, "pages": [], "pageCount": 0, "shown": 0, "previewError": ""}
        return {**result, **await pdf_preview(path)}

    def _owned(self, raw_path: str) -> Path | None:
        try:
            resolved = Path(raw_path).expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        if resolved.suffix.lower() != ".pdf":
            return None
        roots = [reports_root(self._fallback_dir) / CATEGORY / SUBCATEGORY, self._fallback_dir]
        if self._folder_of is not None:
            roots.append(self._folder_of())
        for root in roots:
            try:
                if resolved.is_relative_to(Path(root).expanduser().resolve()):
                    return resolved
            except OSError:
                continue
        return None

    async def pages(self, raw_path: str, first: int, last: int) -> dict[str, Any]:
        path = self._owned(raw_path)
        if path is None:
            return {"ok": False, "error": "Rapor bulunamadı ya da bu modülün çıktısı değil."}
        try:
            images = await pdf_preview_pages(path, first=first, last=max(first, last))
        except PdfPreviewError as failure:
            return {"ok": False, "error": str(failure)}
        return {"ok": True, "first": first, "pages": images}
