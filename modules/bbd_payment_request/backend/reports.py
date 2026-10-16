"""Ödeme Talebi — PDF raporları (63-finans-pdf).

Kullanıcı (16.10.2026): "finansal raporlarda pdf önizleme ve yazdırma olsun".
Bu ekranın üç finansal listesi tek tip araç çubuğuyla (Önizle · Yazdır · PDF
kaydet) basılır; PDF ortak şablondan çıkar (`km_sdk.build_pdf`):

* **Ödeme talepleri** — ekrandaki durum süzgeci ve arama; duruma göre ara
  toplam, genel toplam.
* **Nakit tahsilatlar** — bu panelden alınan nakit; başarısızlar soluk değil
  AYRI yazılır ve toplama girmez.
* **Borçlu öğrenciler** — kantin açık alacağı; sınıfa göre ara toplam.

KANTİN PARASIDIR (A12): kurum carisi (taksit, kitap) bu raporlarda yoktur ve
hiçbir toplam onunla birleşmez. Veli telefonu yazılmaz (KVKK; şablon ayrıca
maskeler).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
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
SUBCATEGORY = "Ödeme talepleri"
FOOTER = "Kontrol Merkezi · Ödeme Talebi"
CONFIDENTIAL = ("Bu rapor kişisel veri içerir (KVKK). Yalnız yetkili personel içindir; "
                "çoğaltılamaz, üçüncü kişilerle paylaşılamaz.")
A12_NOTE = ("Bu rapor KANTİN hesabına aittir; kurum cari hesabı (taksit, kitap, materyal) "
            "bu raporda yer almaz.")
#: Tek raporda okunacak en çok tahsilat satırı (yerel tablo).
MAX_COLLECTIONS = 20_000

STATUS = {"PENDING": "Bekliyor", "PAID": "Ödendi", "FAILED": "Başarısız", "EXPIRED": "Süresi doldu"}
KINDS = {"requests": "odeme-talepleri", "collections": "nakit-tahsilatlar",
         "debtors": "borclu-ogrenciler"}

_FOLD = str.maketrans("ığüşöçİĞÜŞÖÇ", "igusocigusoc")


def fold(text: Any) -> str:
    """Ekrandaki aramayla (`foldText`) aynı Türkçe katlama."""
    return str(text or "").translate(_FOLD).lower()


def _stamp_ms(value: Any) -> str:
    try:
        moment = datetime.fromtimestamp(int(value) / 1000, TZ)
    except (TypeError, ValueError, OSError):
        return "—"
    return moment.strftime("%d.%m.%Y %H:%M")


def _stamp_iso(value: Any) -> str:
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return str(value or "—")
    if moment.tzinfo is not None:
        moment = moment.astimezone(TZ)
    return moment.strftime("%d.%m.%Y %H:%M")


def requests_sections(items: list[dict[str, Any]], *, status: str,
                      query: str) -> list[dict[str, Any]]:
    """Talepler — ekrandaki süzgeçle; duruma göre grup + ara toplam."""
    needle = fold(query)
    rows = [item for item in items
            if (not status or item.get("status") == status)
            and (not needle or needle in fold(f'{item.get("studentName")} {item.get("ref")}'))]
    body: list[Any] = []
    tiles = []
    for code, label in STATUS.items():
        group = [item for item in rows if item.get("status") == code]
        tiles.append((label, f'{len(group)} · {money(sum(int(i.get("amount") or 0) for i in group))}'))
        if not group:
            continue
        body.append({"cells": [label], "kind": "group"})
        for item in sorted(group, key=lambda entry: -int(entry.get("createdAt") or 0)):
            age = item.get("ageDays")
            body.append([item.get("studentName") or "—", money(item.get("amount")), label,
                         "—" if age is None else f"{age} gün", item.get("ref") or "—",
                         _stamp_ms(item.get("createdAt"))])
        body.append({"cells": [f"{label} ara toplam ({len(group)})",
                               money(sum(int(i.get("amount") or 0) for i in group))],
                     "kind": "subtotal"})
    return [
        {"kind": "tiles", "title": "Duruma göre", "tiles": tiles},
        {"kind": "table", "title": f"Talepler ({len(rows)})",
         "headers": ["Öğrenci", "Tutar", "Durum", "Yaş", "Referans", "Oluşturma"],
         "align": "LRLRLL", "widths": [3, 1.3, 1.2, 0.8, 1.6, 1.5], "rows": body,
         "footer": [f"Genel toplam ({len(rows)} talep)",
                    money(sum(int(item.get("amount") or 0) for item in rows)), "", "", "", ""],
         "empty": "Bu süzgece uyan talep yok."},
        {"kind": "note", "text": "Kantin son 100 talebi verir. Tutar, talebin açıldığı andaki "
                                 "borçtur. " + A12_NOTE},
    ]


def collections_sections(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Nakit tahsilatlar — başarılılar toplanır, başarısızlar ayrı listelenir."""
    ok = [row for row in rows if row.get("status") == "ok"]
    failed = [row for row in rows if row.get("status") != "ok"]

    def line(row: dict[str, Any]) -> list[Any]:
        return [row.get("student_name") or row.get("kantin_id") or "—",
                money(row.get("amount")), money(row.get("balance_after")),
                row.get("note") or (row.get("reason") if row.get("status") != "ok" else ""),
                _stamp_iso(row.get("created_at")), row.get("created_by") or "—"]

    sections: list[dict[str, Any]] = [
        {"kind": "tiles", "title": "Özet", "tiles": [
            ("Tahsilat", str(len(ok))),
            ("Tahsil edilen", money(sum(int(row.get("amount") or 0) for row in ok))),
            ("Başarısız deneme", str(len(failed))),
        ]},
        {"kind": "table", "title": f"Nakit tahsilatlar ({len(ok)})",
         "headers": ["Öğrenci", "Tutar", "Kalan borç", "Not", "Zaman", "Kullanıcı"],
         "align": "LRRLLL", "widths": [2.6, 1.2, 1.2, 2.2, 1.5, 1.4],
         "rows": [line(row) for row in ok],
         "footer": [f"Toplam ({len(ok)} tahsilat)",
                    money(sum(int(row.get("amount") or 0) for row in ok)), "", "", "", ""],
         "empty": "Bu panelden yapılmış nakit tahsilat yok."},
    ]
    if failed:
        sections.append({"kind": "table", "title": f"Başarısız denemeler ({len(failed)}) — toplama girmez",
                         "headers": ["Öğrenci", "Tutar", "Kalan borç", "Sebep", "Zaman", "Kullanıcı"],
                         "align": "LRRLLL", "widths": [2.6, 1.2, 1.2, 2.2, 1.5, 1.4],
                         "rows": [line(row) for row in failed]})
    sections.append({"kind": "note", "text": A12_NOTE})
    return sections


def debtors_sections(students: list[dict[str, Any]], classes: dict[str, str], *,
                     query: str) -> list[dict[str, Any]]:
    """Borçlu öğrenciler (kantin açık alacağı) — sınıfa göre ara toplam."""
    needle = fold(query)
    debtors = [item for item in students if int(item.get("balance") or 0) > 0
               and (not needle or needle in fold(
                   f'{item.get("displayName")} {classes.get(str(item.get("kantinId")), "")}'))]
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in debtors:
        groups.setdefault(classes.get(str(item.get("kantinId")), "") or "Sınıfsız", []).append(item)
    body: list[Any] = []
    many = len(groups) > 1
    for name in sorted(groups, key=fold):
        group = sorted(groups[name], key=lambda entry: -int(entry.get("balance") or 0))
        if many:
            body.append({"cells": [f"Sınıf: {name}"], "kind": "group"})
        body.extend([item.get("displayName") or "—", name, money(item.get("balance")),
                     "Var" if item.get("hasPhone") else "Yok",
                     "Kapalı" if item.get("isBlocked") else "Açık"] for item in group)
        if many:
            body.append({"cells": [f"{name} ara toplam ({len(group)})", "",
                                   money(sum(int(i.get("balance") or 0) for i in group))],
                         "kind": "subtotal"})
    total = sum(int(item.get("balance") or 0) for item in debtors)
    return [
        {"kind": "tiles", "title": "Özet", "tiles": [
            ("Borçlu öğrenci", str(len(debtors))), ("Açık alacak", money(total)),
            ("Veli telefonu olmayan", str(sum(1 for item in debtors if not item.get("hasPhone")))),
        ]},
        {"kind": "table", "title": f"Borçlu öğrenciler ({len(debtors)})",
         "headers": ["Öğrenci", "Sınıf", "Borç", "Veli telefonu", "Kantin"],
         "align": "LLRCC", "widths": [3.2, 1.2, 1.4, 1.2, 1], "rows": body,
         "footer": [f"Genel toplam ({len(debtors)} öğrenci)", "", money(total), "", ""],
         "empty": "Kantine borçlu öğrenci yok."},
        {"kind": "note", "text": "Veli telefon numaraları bu raporda yer almaz (KVKK). " + A12_NOTE},
    ]


class PaymentReports:
    """Talepler, nakit tahsilat ve borçlu listesi PDF'leri — üret, önizle, sayfa ver."""

    def __init__(self, *, service: Any, store: Any, log: Any, fallback_dir: Path,
                 folder: Callable[[], Path] | None = None) -> None:
        """`folder`: testler rapor kökü yerine geçici klasör verir (masaüstüne yazılmaz)."""
        self._service = service
        self._store = store
        self._log = log
        self._fallback_dir = fallback_dir
        self._folder_of = folder
        self._collections = store.table("collection")

    def _folder(self) -> Path:
        if self._folder_of is not None:
            return self._folder_of()
        folder: Path = report_dir(CATEGORY, fallback=self._fallback_dir, subcategory=SUBCATEGORY)
        return folder

    async def _all_collections(self) -> list[dict[str, Any]]:
        rows = await self._store.fetch_all(
            f"SELECT * FROM {self._collections} ORDER BY created_at DESC LIMIT ?",
            (MAX_COLLECTIONS,))
        return [dict(row) for row in rows]

    async def produce(self, kind: str, *, status: str = "", query: str = "",
                      classes: dict[str, str] | None = None, orientation: str = "portrait",
                      author: str = "", preview: bool = True) -> dict[str, Any]:
        if kind not in KINDS:
            return {"ok": False, "error": f"Bilinmeyen rapor türü: {kind[:40]}"}
        filters: list[tuple[str, Any]]
        if kind == "collections":
            rows = await self._all_collections()
            title = "Nakit Tahsilatlar"
            subtitle = "Bu panelden alınan nakit kantin tahsilatları"
            sections = collections_sections(rows)
            filters = [("Kapsam", "Bütün kayıtlar")]
        else:
            workspace = await self._service.workspace()
            if not workspace.get("connected"):
                return {"ok": False, "error": "Kantine ulaşılamadı: "
                        + str(workspace.get("error") or "bilinmeyen hata")}
            if kind == "requests":
                title, subtitle = "Ödeme Talepleri", "Veliye gönderilen kantin ödeme talepleri"
                sections = requests_sections(workspace.get("requests") or [], status=status,
                                             query=query)
                filters = [("Durum", STATUS.get(status, "Tümü")), ("Arama", query or "—")]
            else:
                title, subtitle = "Borçlu Öğrenciler", "Kantin açık alacağı (öğrenci borcu)"
                sections = debtors_sections(workspace.get("students") or [], classes or {},
                                            query=query)
                filters = [("Arama", query or "—")]
        try:
            content = build_pdf(title=title, subtitle=subtitle, sections=sections,
                                footer=FOOTER, filters=filters, author=author,
                                orientation=orientation, confidential=CONFIDENTIAL, mask=True)
        except ExportError as failure:
            return {"ok": False, "error": str(failure)}
        moment = datetime.now(TZ)
        name = f"{KINDS[kind]}-{moment:%Y%m%d-%H%M%S}.pdf"
        path = write_private(self._folder() / name, content)
        self._log.info("ödeme raporu üretildi", kind=kind, bytes=len(content))
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
