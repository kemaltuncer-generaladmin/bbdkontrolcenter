"""Öğle Yemeği — PDF raporları (63-finans-pdf).

Kullanıcı (16.10.2026): "finansal raporlarda pdf önizleme ve yazdırma olsun".
Öğle yemeği öğrencinin KANTİN carisine borç yazar; bu yüzden finansal bir
rapordur. İki rapor ortak şablondan (`km_sdk.build_pdf`) çıkar:

* **Aylık öğle yemeği raporu** — takvimde görüntülenen ay: gün gün porsiyon ve
  tutar, öğrenci başına yemek ve tutar (sınıf ara toplamı), genel toplam.
* **Gün dökümü** — seçili günün öğrencileri, partileri ve tutarları.

Yalnız kantine işlenmiş ve geri alınmamış satırlar (`created`/`duplicate`,
`reversed_at` boş) toplama girer; başarısız ve geri alınan satırlar ayrı
sayılır. Kaynak modülün KENDİ tablolarıdır (K5) — kantine ek istek atılmaz.
Kantin parasıdır; kurum carisiyle birleşmez (A12).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
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
SUBCATEGORY = "Öğle yemeği"
FOOTER = "Kontrol Merkezi · Öğle Yemeği"
CONFIDENTIAL = ("Bu rapor kişisel veri içerir (KVKK). Yalnız yetkili personel içindir; "
                "çoğaltılamaz, üçüncü kişilerle paylaşılamaz.")
A12_NOTE = ("Öğle yemeği öğrencinin KANTİN hesabına borç yazar; kurum cari hesabı (taksit, kitap) "
            "bu raporda yer almaz.")
MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül",
          "Ekim", "Kasım", "Aralık"]
DAYS = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
#: Toplama giren satır: kantine işlenmiş ve geri alınmamış.
LIVE = "status IN ('created','duplicate') AND reversed_at IS NULL"

_FOLD = str.maketrans("ığüşöçİĞÜŞÖÇ", "igusocigusoc")


def fold(text: Any) -> str:
    return str(text or "").translate(_FOLD).lower()


def month_bounds(month: str) -> tuple[str, str]:
    first = date.fromisoformat(f"{month}-01")
    last = (first.replace(day=28) + timedelta(days=4))
    last = last - timedelta(days=last.day)
    return first.isoformat(), last.isoformat()


def tr_day(iso: str) -> str:
    try:
        value = date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{value:%d.%m.%Y} {DAYS[value.weekday()]}"


class LunchReports:
    def __init__(self, *, store: Any, log: Any, fallback_dir: Path,
                 folder: Callable[[], Path] | None = None) -> None:
        """`folder`: testler rapor kökü yerine geçici klasör verir (masaüstüne yazılmaz)."""
        self._store = store
        self._log = log
        self._fallback_dir = fallback_dir
        self._folder_of = folder
        self._entries = store.table("entry")
        self._batches = store.table("batch")

    def _folder(self) -> Path:
        if self._folder_of is not None:
            return self._folder_of()
        folder: Path = report_dir(CATEGORY, fallback=self._fallback_dir, subcategory=SUBCATEGORY)
        return folder

    async def _month_sections(self, month: str, classes: dict[str, str]
                              ) -> tuple[str, list[dict[str, Any]]]:
        first, last = month_bounds(month)
        days = await self._store.fetch_all(
            f"SELECT service_date, "
            f"  SUM(CASE WHEN {LIVE} THEN 1 ELSE 0 END) AS meals, "
            f"  SUM(CASE WHEN {LIVE} THEN amount ELSE 0 END) AS amount, "
            f"  SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed, "
            f"  SUM(CASE WHEN reversed_at IS NOT NULL THEN 1 ELSE 0 END) AS reversed "
            f"FROM {self._entries} WHERE service_date BETWEEN ? AND ? "
            f"GROUP BY service_date ORDER BY service_date", (first, last))
        students = await self._store.fetch_all(
            f"SELECT kantin_id, MAX(student_name) AS name, MAX(class_name) AS class_name, "
            f"  COUNT(*) AS meals, SUM(amount) AS amount "
            f"FROM {self._entries} WHERE service_date BETWEEN ? AND ? AND {LIVE} "
            f"GROUP BY kantin_id", (first, last))
        year, number = (int(part) for part in month.split("-"))
        label = f"{MONTHS[number - 1]} {year}"
        meals = sum(int(row["meals"] or 0) for row in days)
        amount = sum(int(row["amount"] or 0) for row in days)
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in students:
            klass = classes.get(str(row["kantin_id"])) or str(row["class_name"] or "") or "Sınıfsız"
            groups.setdefault(klass, []).append(dict(row))
        body: list[Any] = []
        many = len(groups) > 1
        for klass in sorted(groups, key=fold):
            group = sorted(groups[klass], key=lambda item: fold(item["name"]))
            if many:
                body.append({"cells": [f"Sınıf: {klass}"], "kind": "group"})
            body.extend([row["name"] or row["kantin_id"], klass, int(row["meals"] or 0),
                         money(row["amount"])] for row in group)
            if many:
                body.append({"cells": [f"{klass} ara toplam ({len(group)})", "",
                                       sum(int(row["meals"] or 0) for row in group),
                                       money(sum(int(row["amount"] or 0) for row in group))],
                             "kind": "subtotal"})
        sections: list[dict[str, Any]] = [
            {"kind": "tiles", "title": "Ay özeti", "tiles": [
                ("Yemek verilen gün", str(sum(1 for row in days if int(row["meals"] or 0)))),
                ("Porsiyon", str(meals)), ("Kantine yazılan tutar", money(amount)),
                ("Öğrenci", str(len(students))),
            ]},
            {"kind": "table", "title": "Gün gün",
             "headers": ["Gün", "Porsiyon", "Tutar", "Başarısız", "Geri alınan"],
             "align": "LRRRR", "widths": [2.6, 1, 1.4, 1, 1],
             "rows": [[tr_day(str(row["service_date"])), int(row["meals"] or 0), money(row["amount"]),
                       int(row["failed"] or 0), int(row["reversed"] or 0)] for row in days],
             "footer": ["Toplam", meals, money(amount),
                        sum(int(row["failed"] or 0) for row in days),
                        sum(int(row["reversed"] or 0) for row in days)],
             "empty": "Bu ay işlenmiş öğle yemeği yok."},
            {"kind": "table", "title": f"Öğrenci başına ({len(students)})",
             "headers": ["Öğrenci", "Sınıf", "Yemek", "Tutar"], "align": "LLRR",
             "widths": [3.4, 1.2, 0.9, 1.4], "rows": body,
             "footer": [f"Genel toplam ({len(students)} öğrenci)", "", meals, money(amount)],
             "empty": "Bu ay yemek yiyen öğrenci yok."},
            {"kind": "note", "text": "Toplama yalnız kantine işlenmiş ve geri alınmamış yemekler "
                                     "girer. " + A12_NOTE},
        ]
        return label, sections

    async def _day_sections(self, day: str, classes: dict[str, str]) -> list[dict[str, Any]]:
        entries = await self._store.fetch_all(
            f"SELECT e.*, b.created_by AS batch_by FROM {self._entries} e "
            f"LEFT JOIN {self._batches} b ON b.batch_ref = e.batch_ref "
            f"WHERE e.service_date = ? ORDER BY e.student_name, e.created_at", (day,))
        live = [row for row in entries if row["status"] in ("created", "duplicate")
                and row["reversed_at"] is None]

        def state(row: Any) -> str:
            if row["reversed_at"] is not None:
                return f"Geri alındı: {row['reversed_reason'] or '—'}"
            if row["status"] == "failed":
                return f"Başarısız: {row['reason'] or '—'}"
            return "İşlendi" if row["status"] in ("created", "duplicate") else str(row["status"])

        return [
            {"kind": "tiles", "title": "Gün özeti", "tiles": [
                ("Porsiyon", str(len(live))),
                ("Kantine yazılan tutar", money(sum(int(row["amount"] or 0) for row in live))),
                ("Başarısız / geri alınan", str(len(entries) - len(live))),
            ]},
            {"kind": "table", "title": f"Satırlar ({len(entries)})",
             "headers": ["Öğrenci", "Sınıf", "Tutar", "Durum", "İşleyen"], "align": "LLRLL",
             "widths": [2.8, 1, 1.2, 2.4, 1.4],
             "rows": [[row["student_name"] or row["kantin_id"],
                       classes.get(str(row["kantin_id"])) or row["class_name"] or "—",
                       money(row["amount"]), state(row), row["batch_by"] or "—"] for row in entries],
             "footer": ["Toplam (işlenen)", "", money(sum(int(row["amount"] or 0) for row in live)),
                        f"{len(live)} porsiyon", ""],
             "empty": "Bu gün işlenmiş öğle yemeği yok."},
            {"kind": "note", "text": A12_NOTE},
        ]

    async def produce(self, kind: str, *, month: str = "", day: str = "",
                      classes: dict[str, str] | None = None, orientation: str = "portrait",
                      author: str = "", preview: bool = True) -> dict[str, Any]:
        classes = classes or {}
        if kind == "month":
            label, sections = await self._month_sections(month, classes)
            title, subtitle = "Aylık Öğle Yemeği Raporu", label
            filters: list[tuple[str, Any]] = [("Ay", label)]
            stem = f"ogle-yemegi-{month}"
        elif kind == "day":
            sections = await self._day_sections(day, classes)
            title, subtitle = "Öğle Yemeği Gün Dökümü", tr_day(day)
            filters = [("Gün", tr_day(day))]
            stem = f"ogle-yemegi-gun-{day}"
        else:
            return {"ok": False, "error": f"Bilinmeyen rapor türü: {kind[:40]}"}
        try:
            content = build_pdf(title=title, subtitle=subtitle, sections=sections, footer=FOOTER,
                                filters=filters, author=author, orientation=orientation,
                                confidential=CONFIDENTIAL, mask=True)
        except ExportError as failure:
            return {"ok": False, "error": str(failure)}
        name = f"{stem}-{datetime.now(TZ):%Y%m%d-%H%M%S}.pdf"
        path = write_private(self._folder() / name, content)
        self._log.info("öğle yemeği raporu üretildi", kind=kind, bytes=len(content))
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
