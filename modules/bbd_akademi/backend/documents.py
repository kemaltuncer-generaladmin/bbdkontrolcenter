"""Akademi belgeleri — üret → önizle → yazdır (kantin raporlarıyla aynı zincir).

AKIŞ. Panel ekrandaki veriden belge modelini kurar (`ui/panel/documents-logic.js`)
ve `POST /api/bbd_akademi/km/documents/preview` ile gönderir. Burada:

1. **Kapı (K9).** Belge türü (`KINDS`) hangi Akademi işlemlerinin verisini
   taşıyorsa kullanıcı o işlemlerin KM iznini taşımalıdır — panelde düğmeyi
   gizlemek yetki değildir. Kurum carisi belgesi `bbd_akademi.finance` ister;
   kantin belgesi ayrı türdür ve kurum carisine karışmaz (A12).
2. **Başlık.** Kurum adı/adresi Akademi kurum ayarından, logo ve ana renk
   yayımlanmış temadan okunur (`Letterheads`, 10 dk önbellek, uçuştaki istek
   paylaşılır — A14). Akademi'ye ulaşılamazsa belge YİNE çıkar: son bilinen
   başlık ya da monogramlı yedek başlık kullanılır.
3. **PDF.** `document_pdf.render` iş parçacığında koşar (olay döngüsü
   bloklanmaz). Dosya rapor köküne 0600 ile yazılır (`Raporlar/Akademi/<tür>/…`),
   böylece `/api/outputs/document` onu cihazın yazıcısına verebilir.
4. **Önizleme.** Sayfalar `pdftoppm` ile PNG'ye çevrilir — önizleme basılacak
   PDF'in BİREBİR kendisidir. `preview=false` ise atlanır ("Yazdır" düğmesi
   önizlemesiz basar).

Akademi'nin KENDİ ürettiği PDF'ler (makbuz, ekstre, kitap sipariş belgesi)
`POST /km/documents/akademi` ile aynı pencereye girer: imzalı bağlantı
yalnız izinli kökenden indirilir, PDF olduğu doğrulanır, rapor köküne yazılır.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import secrets
import shutil
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from km_sdk import CurrentUser, report_dir, write_private

from . import document_pdf
from .document_pdf import DocumentError, Letterhead, Stamp
from .gateway import AkademiError, AkademiGateway, on_behalf_of, request_id
from .operations import Allowlist
from .permission_map import km_permissions

TZ = ZoneInfo("Europe/Istanbul")
ACTOR = ("bbd_akademi", "Belge başlığı")
LETTERHEAD_TTL = 600.0
LETTERHEAD_WAIT = 4.0
MAX_LOGO_BYTES = 3 * 1024 * 1024
MAX_AKADEMI_PDF_BYTES = 30 * 1024 * 1024
PREVIEW_PAGES = 12
PREVIEW_DPI = 110


@dataclass(frozen=True, slots=True)
class DocumentKind:
    code: str                      # belge numarasının türü (AK-KRN-…)
    folder: str                    # Raporlar/Akademi/<folder>/
    label: str                     # dosya adının başı
    operations: tuple[str, ...]    # belgenin taşıdığı veri — kapı bunların iznini ister
    #: Akademi işlemi olmayan (KM verisi) belgede kapı doğrudan bu KM iznini ister.
    permissions: tuple[str, ...] = ()


#: Belge türleri. Bir tür yalnız buradaysa üretilebilir.
KINDS: dict[str, DocumentKind] = {
    "report_card": DocumentKind("KRN", "Karne", "karne", ("admin_getEvaluation",)),
    "exam_result": DocumentKind("DNS", "Deneme", "deneme-sonuc", ("students_getExamResult",)),
    "exam_analysis": DocumentKind("DNA", "Deneme", "deneme-analiz",
                                  ("admin_getExamAnalysis",)),
    "exam_results": DocumentKind("DNL", "Deneme", "deneme-liste", ("admin_listExamResults",)),
    "attendance_day": DocumentKind("YKG", "Yoklama", "yoklama-gunluk",
                                   ("admin_listAttendanceSessions",)),
    "attendance_sheet": DocumentKind("YKO", "Yoklama", "yoklama-cizelge",
                                     ("admin_getAttendanceSheet",)),
    "attendance_matrix": DocumentKind("YKA", "Yoklama", "yoklama-aylik",
                                      ("admin_getAttendanceMatrix",)),
    "absentee_list": DocumentKind("YKD", "Yoklama", "devamsizlik",
                                  ("admin_listChronicAbsentees",)),
    "timetable_class": DocumentKind("DPS", "Ders programı", "ders-programi",
                                    ("admin_getTimetable",)),
    "timetable_teacher": DocumentKind("DPO", "Ders programı", "ogretmen-programi",
                                      ("admin_getTimetableGrid",)),
    "homework_list": DocumentKind("ODV", "Ödev", "odev-listesi", ("admin_listHomework",)),
    "homework_student": DocumentKind("ODO", "Ödev", "ogrenci-odevleri",
                                     ("students_listHomework",)),
    "study_plan": DocumentKind("CPL", "Çalışma planı", "calisma-plani",
                               ("students_getPlanWeek",)),
    "finance_statement": DocumentKind("CRE", "Kurum cari", "hesap-ekstresi",
                                      ("admin_getFinanceStatement",)),
    "finance_receipt": DocumentKind("MKB", "Kurum cari", "makbuz", ("admin_getReceipt",)),
    # Akademi README §14 madde 87 (ozellik/finans-belge): iade makbuzu ve ödeme planı (kayıt sözleşmesi eki).
    "finance_refund_receipt": DocumentKind("IAD", "Kurum cari", "iade-makbuzu", ("admin_getRefundReceipt",)),
    "finance_plan": DocumentKind("OPL", "Kurum cari", "odeme-plani", ("admin_getPlanAgreement",)),
    "book_request": DocumentKind("KTP", "Kitap", "kitap-talebi", ("admin_getBookRequest",)),
    # 0.5.4 (km-toplu-kitap): kitap satışı (satış + öğrenci satırları, teslim imzası).
    "book_sale": DocumentKind("KSA", "Kitap", "kitap-satisi", ("admin_getBookSale",)),
    # 0.5.4: toplu kalıcı silmenin öğrenci başına sonucu — KM verisidir (Akademi işlemi
    # yok); yalnız silme yetkisi olan basar (`bbd_students.delete`, varsayılan admin).
    "student_deletion": DocumentKind("OGS", "Öğrenci", "kalici-silme-sonucu", (),
                                     permissions=("bbd_students.delete",)),
    "student_card": DocumentKind("OGK", "Öğrenci", "ogrenci-karti", ("admin_getStudent",)),
    "guardian_list": DocumentKind("VLL", "Öğrenci", "veli-listesi",
                                  ("admin_listStudents", "admin_getStudent")),
    "canteen_statement": DocumentKind("KNT", "Kantin", "kantin-dokumu",
                                      ("students_getCanteen", "students_listCanteenTransactions")),
}

#: Akademi'nin kendi PDF'i → hangi işlemin bağlantısı (kapı onun iznini ister).
AKADEMI_PDFS: dict[str, DocumentKind] = {
    "finance_receipt": DocumentKind("MKB", "Kurum cari", "makbuz", ("admin_getReceipt",)),
    "finance_statement": DocumentKind("CRE", "Kurum cari", "hesap-ekstresi",
                                      ("admin_getFinanceStatement",)),
    "finance_refund_receipt": DocumentKind("IAD", "Kurum cari", "iade-makbuzu", ("admin_getRefundReceipt",)),
    "finance_plan": DocumentKind("OPL", "Kurum cari", "odeme-plani", ("admin_getPlanAgreement",)),
    "book_order": DocumentKind("KTS", "Kitap", "kitap-siparis", ("admin_getBookOrderPdf",)),
    "book_sale_order": DocumentKind("KTS", "Kitap", "kitap-satis-siparis",
                                    ("admin_getBookSaleOrderPdf",)),
    "evaluation_reports": DocumentKind("KRN", "Karne", "gelisim-karnesi",
                                       ("admin_exportEvaluationReports",)),
    # Veli toplantısı günün randevu listesi (Akademi README §14 madde 90).
    "parent_meeting_list": DocumentKind("VTL", "Veli toplantısı", "veli-toplantisi-listesi",
                                        ("admin_getParentMeetingListPdf",)),
}


class DocumentDenied(Exception):
    def __init__(self, permission: str, operation: str) -> None:
        super().__init__("Bu belge için yetkiniz yok.")
        self.permission = permission
        self.operation = operation


class PreviewError(RuntimeError):
    pass


def slug(text: str, fallback: str = "belge") -> str:
    """Dosya adına uygun ASCII parça ('Ayşe Yılmaz 8-A' → 'ayse-yilmaz-8-a')."""
    table = str.maketrans("çğıöşüÇĞİÖŞÜâîû", "cgiosuCGIOSUaiu")
    ascii_text = str(text or "").translate(table).lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    return cleaned[:60].strip("-") or fallback


# ------------------------------------------------------------------ başlık

class Letterheads:
    """Kurum adı, adresi, logosu ve ana rengi — Akademi'den, önbellekli."""

    def __init__(self, *, gateway: AkademiGateway, log: Any, fallback_name: str = "",
                 ttl_seconds: float = LETTERHEAD_TTL,
                 wait_seconds: float = LETTERHEAD_WAIT) -> None:
        self._gateway = gateway
        self._log = log
        self._fallback = Letterhead(name=fallback_name.strip() or "BBD Akademi")
        self._ttl = ttl_seconds
        self._wait = wait_seconds
        self._value: Letterhead | None = None
        self._at = 0.0
        self._inflight: asyncio.Task[Letterhead | None] | None = None

    def invalidate(self) -> None:
        self._at = 0.0

    async def get(self) -> Letterhead:
        if self._value is not None and time.monotonic() - self._at < self._ttl:
            return self._value
        if self._inflight is None or self._inflight.done():
            self._inflight = asyncio.ensure_future(self._load())
        try:
            loaded = await asyncio.wait_for(asyncio.shield(self._inflight), self._wait)
        except TimeoutError:
            self._log.warning("belge başlığı zamanında okunamadı")
            loaded = None
        return loaded or self._value or self._fallback

    def _headers(self) -> dict[str, str]:
        return {"accept": "application/json",
                "X-Akademi-On-Behalf-Of": on_behalf_of(*ACTOR, kind="system"),
                "X-Request-Id": request_id(None)}

    async def _json(self, path: str) -> Any:
        response = await self._gateway.send("GET", path, headers=self._headers())
        try:
            raw = await response.aread()
        finally:
            await response.aclose()
        if response.status_code != 200:
            raise AkademiError(f"{path} okunamadı ({response.status_code}).")
        return json.loads(raw) if raw else None

    async def _logo(self, branding: Mapping[str, Any]) -> bytes | None:
        logo = branding.get("logo") if isinstance(branding.get("logo"), Mapping) else {}
        for key in ("url", "markUrl"):
            url = logo.get(key) if isinstance(logo, Mapping) else None
            if not isinstance(url, str) or not url:
                continue
            try:
                response = await self._gateway.fetch(url)
                try:
                    raw = await _read_capped(response, MAX_LOGO_BYTES)
                finally:
                    await response.aclose()
            except (AkademiError, ValueError):
                continue
            # PNG/JPEG ya da SVG (svglib ile vektör çizilir; çizilemezse başlık monogramla çıkar);
            # başka biçimse sonraki aday (kare amblem).
            if response.status_code == 200 and (raw[:8] == b"\x89PNG\r\n\x1a\n" or raw[:3] == b"\xff\xd8\xff"
                                                or document_pdf.is_svg(raw)):
                return raw
        return None

    async def _load(self) -> Letterhead | None:
        try:
            settings, state = await asyncio.gather(self._json("/v1/admin/settings"),
                                                   self._json("/v1/admin/branding"))
        except AkademiError:
            return None
        except Exception as failure:  # noqa: BLE001 — başlık okunamazsa belge yine çıkar (K7)
            self._log.warning("belge başlığı okunamadı", error=type(failure).__name__)
            return None
        tenant: Mapping[str, Any] = settings if isinstance(settings, Mapping) else {}
        theme: Mapping[str, Any] = state if isinstance(state, Mapping) else {}
        published = theme.get("published")
        branding: Mapping[str, Any] = published if isinstance(published, Mapping) else {}
        name = str(tenant.get("name") or "").strip() or self._fallback.name
        raw_support = tenant.get("support")
        support: Mapping[str, Any] = raw_support if isinstance(raw_support, Mapping) else {}
        detail = " · ".join(str(item).strip() for item in (
            tenant.get("address"), support.get("phone"), support.get("email"))
            if isinstance(item, str) and item.strip())
        raw_light = branding.get("light")
        light: Mapping[str, Any] = raw_light if isinstance(raw_light, Mapping) else {}
        accent = str(light.get("primary") or branding.get("seedColor") or "#2F5BD3")
        try:
            logo = await self._logo(branding)
        except Exception:  # noqa: BLE001 — logo yoksa monogram
            logo = None
        value = Letterhead(name=name, detail=detail, logo=logo, accent=accent)
        self._value, self._at = value, time.monotonic()
        return value


async def _read_capped(response: Any, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > limit:
            raise ValueError("dosya çok büyük")
        chunks.append(chunk)
    return b"".join(chunks)


# ------------------------------------------------------------------ servis

class DocumentService:
    def __init__(self, *, gateway: AkademiGateway, allowlist: Allowlist, log: Any,
                 fallback_dir: Path, letterheads: Letterheads | None = None,
                 folder: Callable[[DocumentKind], Path] | None = None) -> None:
        """`folder`: testler rapor kökü yerine geçici klasör verir (masaüstüne yazılmaz)."""
        self._gateway = gateway
        self._allowlist = allowlist
        self._log = log
        self._fallback_dir = fallback_dir
        self.letterheads = letterheads or Letterheads(gateway=gateway, log=log)
        self._folder_of = folder

    # --- kapı ---
    def authorize(self, user: CurrentUser, kind: DocumentKind) -> None:
        for permission in kind.permissions:
            if not user.has_permission(permission):
                raise DocumentDenied(permission, kind.label)
        for operation_id in kind.operations:
            operation = self._allowlist.by_id(operation_id)
            if operation is None:
                raise DocumentDenied("", operation_id)
            needed = km_permissions(operation)
            if not any(user.has_permission(key) for key in needed):
                raise DocumentDenied(needed[0], operation_id)

    def _folder(self, kind: DocumentKind) -> Path:
        if self._folder_of is not None:
            return self._folder_of(kind)
        folder: Path = report_dir("Akademi", fallback=self._fallback_dir,
                                  subcategory=kind.folder)
        return folder

    @staticmethod
    def _now() -> datetime:
        return datetime.now(TZ)

    def _number(self, kind: DocumentKind, moment: datetime) -> str:
        return f"AK-{kind.code}-{moment:%y%m%d-%H%M}-{secrets.token_hex(2).upper()}"

    # --- üretim ---
    async def produce(self, user: CurrentUser, kind_key: str, model: Mapping[str, Any], *,
                      preview: bool = True) -> dict[str, Any]:
        kind = KINDS.get(kind_key)
        if kind is None:
            return {"ok": False, "error": f"Bilinmeyen belge türü: {kind_key[:40]}"}
        self.authorize(user, kind)
        try:
            document = document_pdf.parse(model)
        except DocumentError as failure:
            return {"ok": False, "error": str(failure)}

        letterhead = await self.letterheads.get()
        moment = self._now()
        number = self._number(kind, moment)
        stamp = Stamp(number=number, issued=f"{moment:%d.%m.%Y %H:%M}",
                      author=(user.full_name or user.id or "Kontrol Merkezi").strip())
        try:
            content = await asyncio.to_thread(document_pdf.render, document, letterhead, stamp)
        except ImportError:
            return {"ok": False, "error": "PDF için `reportlab` gerekli (scripts/install-deps.sh)."}
        except Exception as failure:  # noqa: BLE001 — çizim hatası paneli düşürmez (K7)
            self._log.warning("belge çizilemedi", kind=kind_key, error=type(failure).__name__)
            return {"ok": False, "error": "Belge çizilemedi. Lütfen tekrar deneyin."}

        name_hint = model.get("filename") if isinstance(model.get("filename"), str) else ""
        name = f"{kind.label}-{slug(name_hint or document.title)}-{moment:%Y%m%d-%H%M%S}.pdf"
        path = write_private(self._folder(kind) / name, content)
        self._log.info("akademi belgesi üretildi", kind=kind_key, number=number,
                       parts=len(document.parts), bytes=len(content), user=user.id)
        return await self._result(path, name, content, number=number, preview=preview,
                                  parts=len(document.parts))

    async def import_pdf(self, user: CurrentUser, kind_key: str, url: str, name_hint: str, *,
                         preview: bool = True) -> dict[str, Any]:
        kind = AKADEMI_PDFS.get(kind_key)
        if kind is None:
            return {"ok": False, "error": f"Bilinmeyen Akademi belgesi: {kind_key[:40]}"}
        self.authorize(user, kind)
        try:
            response = await self._gateway.fetch(url)
            try:
                raw = await _read_capped(response, MAX_AKADEMI_PDF_BYTES)
            finally:
                await response.aclose()
        except AkademiError as failure:
            return {"ok": False, "error": str(failure)}
        except ValueError:
            return {"ok": False, "error": "Akademi belgesi çok büyük."}
        if response.status_code != 200:
            return {"ok": False, "error": f"Akademi belgesi indirilemedi ({response.status_code})."}
        if not raw.startswith(b"%PDF"):
            return {"ok": False, "error": "Akademi'den gelen dosya PDF değil."}
        moment = self._now()
        name = f"{kind.label}-{slug(name_hint)}-{moment:%Y%m%d-%H%M%S}.pdf"
        path = write_private(self._folder(kind) / name, raw)
        self._log.info("akademi belgesi alındı", kind=kind_key, bytes=len(raw), user=user.id)
        return await self._result(path, name, raw, number="", preview=preview, parts=1)

    async def _result(self, path: Path, name: str, content: bytes, *, number: str,
                      preview: bool, parts: int) -> dict[str, Any]:
        base = {"ok": True, "path": str(path), "name": name, "bytes": len(content),
                "number": number, "parts": parts}
        if not preview:
            return {**base, "pages": [], "pageCount": 0, "shown": 0, "previewError": ""}
        try:
            pages, total = await render_pages(path)
        except PreviewError as failure:
            return {**base, "pages": [], "pageCount": 0, "shown": 0,
                    "previewError": str(failure)}
        return {**base, "pages": pages, "pageCount": total, "shown": len(pages),
                "previewError": ""}


# ------------------------------------------------------------------ önizleme

async def render_pages(path: Path, *, max_pages: int = PREVIEW_PAGES,
                       dpi: int = PREVIEW_DPI) -> tuple[list[str], int]:
    """PDF'in ilk sayfalarını PNG data-URI listesine çevirir (+ toplam sayfa)."""
    binary = shutil.which("pdftoppm")
    if not binary:
        raise PreviewError("Önizleme üretilemedi: `pdftoppm` bulunamadı (poppler-utils). "
                           "Belge yine de kaydedildi ve yazdırılabilir.")
    with tempfile.TemporaryDirectory(prefix="km-akademi-belge-") as folder:
        target = Path(folder) / "sayfa"
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                binary, "-png", "-r", str(int(dpi)), "-f", "1", "-l", str(int(max_pages)),
                str(path), str(target),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            _, err = await asyncio.wait_for(process.communicate(), timeout=60)
        except TimeoutError:
            if process is not None:
                process.kill()
                await process.wait()
            raise PreviewError("Önizleme üretilemedi: süre aşıldı. Belge yazdırılabilir.") from None
        except OSError as failure:
            raise PreviewError(f"Önizleme üretilemedi: {failure}") from failure
        if process.returncode != 0:
            message = err.decode(errors="replace").strip()[:200] or "bilinmeyen hata"
            raise PreviewError(f"Önizleme üretilemedi: {message}")
        files = sorted(Path(folder).glob("sayfa*.png"),
                       key=lambda item: int(re.sub(r"\D", "", item.stem) or 0))
        pages = ["data:image/png;base64," + base64.b64encode(item.read_bytes()).decode("ascii")
                 for item in files]
    return pages, await page_count(path) or len(pages)


async def page_count(path: Path) -> int:
    binary = shutil.which("pdfinfo")
    if not binary:
        return 0
    try:
        process = await asyncio.create_subprocess_exec(
            binary, str(path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(process.communicate(), timeout=15)
    except (TimeoutError, OSError):
        return 0
    match = re.search(r"^Pages:\s+(\d+)", out.decode(errors="replace"), re.MULTILINE)
    return int(match.group(1)) if match else 0
