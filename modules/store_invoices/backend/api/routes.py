"""Fatura — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde düğmeyi gizlemek yetkilendirme
değildir. Yıkıcı ve para/belge üreten uçlarda gerekçe `min_length=10` ile
ŞEMADA doğrulanır, ayrıca serviste tekrar denetlenir — istemci şemayı
atlatabilir.

İZİN AYRIMI. `store_invoices.legal_no` bilerek `manage`den ayrıldı: yasal
fatura numarasını bilen kişi mali müşavirdir (`accountant`), ama o kişinin
mağazaya fatura kestirmesi ya da müşteriye e-posta göndermesi istenmez.

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran
mesajı gösterir. 4xx yalnız izin/şema kapısından çıkar.
"""

from __future__ import annotations

from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..service import InvoicesService

router = APIRouter()
_service: InvoicesService | None = None


def bind(service: InvoicesService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> InvoicesService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


# ================================================================== okuma

@router.get("/invoices")
async def invoices(
    q: str = Query("", max_length=120),
    state: str = Query("", max_length=24),
    start: str = Query("", max_length=10),
    end: str = Query("", max_length=10),
    minTotal: int | None = Query(None, ge=0),
    maxTotal: int | None = Query(None, ge=0),
    orderId: int = Query(0, ge=0),
    unmatched: bool = Query(False),
    page: int = Query(1, ge=1, le=10_000),
    size: int = Query(0, ge=0, le=200),
    user: CurrentUser = requires("store_invoices.view"),
) -> dict[str, Any]:
    return await service().invoices(q=q, state=state, start=start, end=end, min_total=minTotal,
                                    max_total=maxTotal, order_id=orderId, unmatched=unmatched,
                                    page=page, size=size)


@router.get("/invoices/{invoice_id}")
async def invoice(
    invoice_id: int,
    user: CurrentUser = requires("store_invoices.view"),
) -> dict[str, Any]:
    return await service().card(invoice_id)


@router.get("/by-order/{order_id}")
async def by_order(
    order_id: int,
    user: CurrentUser = requires("store_invoices.view"),
) -> dict[str, Any]:
    """`store.invoice.byOrder` yeteneğinin HTTP karşılığı — sipariş ekranı
    yeteneği kullanır, bu uç ekranın kendi çekmecesi içindir."""
    return await service().by_order(order_id)


@router.get("/shipments")
async def shipments(
    q: str = Query("", max_length=120),
    start: str = Query("", max_length=10),
    end: str = Query("", max_length=10),
    orderId: int = Query(0, ge=0),
    page: int = Query(1, ge=1, le=10_000),
    size: int = Query(0, ge=0, le=200),
    user: CurrentUser = requires("store_invoices.view"),
) -> dict[str, Any]:
    return await service().shipments(q=q, start=start, end=end, order_id=orderId,
                                     page=page, size=size)


@router.get("/uninvoiced")
async def uninvoiced(
    start: str = Query("", max_length=10),
    end: str = Query("", max_length=10),
    page: int = Query(1, ge=1, le=10_000),
    size: int = Query(0, ge=0, le=200),
    user: CurrentUser = requires("store_invoices.view"),
) -> dict[str, Any]:
    return await service().uninvoiced(start=start, end=end, page=page, size=size)


@router.get("/series")
async def series(
    user: CurrentUser = requires("store_invoices.view"),
) -> dict[str, Any]:
    return await service().series()


@router.get("/audit")
async def audit(
    invoiceId: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    user: CurrentUser = requires("store_invoices.view"),
) -> dict[str, Any]:
    return await service().audit(invoice_id=invoiceId, limit=limit)


# ============================================================ yasal numara

class LegalBody(BaseModel):
    series: str = Field(min_length=1, max_length=16)
    number: int = Field(ge=1, le=999_999_999)
    legalNo: str = Field(default="", max_length=32)
    issuedAt: str = Field(default="", max_length=10)
    note: str = Field(default="", max_length=255)
    reason: str = Field(min_length=10, max_length=255)


@router.post("/invoices/{invoice_id}/legal-no")
async def legal_no(
    invoice_id: int,
    body: LegalBody,
    user: CurrentUser = requires("store_invoices.legal_no"),
) -> dict[str, Any]:
    """Dış sistemde kesilen yasal faturanın numarasını eşler. MAĞAZAYA YAZMAZ."""
    return await service().save_legal_no(invoice_id, series=body.series, number=body.number,
                                         legal_no=body.legalNo, issued_at=body.issuedAt,
                                         note=body.note, reason=body.reason,
                                         actor=user.full_name)


class SeriesBody(BaseModel):
    code: str = Field(min_length=1, max_length=16)
    label: str = Field(default="", max_length=64)
    startNo: int = Field(default=1, ge=1, le=999_999_999)
    pad: int = Field(default=9, ge=1, le=16)
    yearReset: bool = True
    isDefault: bool = False
    note: str = Field(default="", max_length=255)
    reason: str = Field(min_length=10, max_length=255)


@router.post("/series")
async def save_series(
    body: SeriesBody,
    user: CurrentUser = requires("store_invoices.manage"),
) -> dict[str, Any]:
    return await service().save_series(code=body.code, label=body.label, start_no=body.startNo,
                                       pad=body.pad, year_reset=body.yearReset,
                                       is_default=body.isDefault, note=body.note,
                                       reason=body.reason, actor=user.full_name)


# ================================================================== yazma

class IssueBody(BaseModel):
    orderIds: list[int] = Field(default_factory=list)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/invoices/issue")
async def issue(
    body: IssueBody,
    user: CurrentUser = requires("store_invoices.issue"),
) -> dict[str, Any]:
    """Toplu fatura kesme. GERİ ALINAMAZ: kesilen fatura silinmez, iptali
    iade faturasıdır. Bu yüzden ayrı izin anahtarı taşır (ADR 0012)."""
    return await service().issue(body.orderIds, reason=body.reason, actor=user.full_name,
                                 dry_run=body.dryRun)


class ReasonBody(BaseModel):
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/invoices/{invoice_id}/send")
async def send_copy(
    invoice_id: int,
    body: ReasonBody,
    user: CurrentUser = requires("store_invoices.manage"),
) -> dict[str, Any]:
    """Fatura kopyasını müşteriye e-postalar — dışarıya çıkan bir iştir."""
    return await service().send_copy(invoice_id, reason=body.reason, actor=user.full_name,
                                     dry_run=body.dryRun)


class StateBody(BaseModel):
    invoiceIds: list[int] = Field(default_factory=list)
    state: str = Field(max_length=24)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/invoices/state")
async def set_state(
    body: StateBody,
    user: CurrentUser = requires("store_invoices.manage"),
) -> dict[str, Any]:
    """Toplu durum değişikliği. SİLME UCU YOKTUR."""
    return await service().set_state(body.invoiceIds, state=body.state, reason=body.reason,
                                     actor=user.full_name, dry_run=body.dryRun)


# ================================================================== rapor

class PreviewBody(BaseModel):
    kind: str = Field(max_length=24)
    start: str = Field(default="", max_length=10)
    end: str = Field(default="", max_length=10)
    state: str = Field(default="", max_length=24)
    invoiceId: int = 0
    invoiceIds: list[int] = Field(default_factory=list)
    shipmentIds: list[int] = Field(default_factory=list)


@router.post("/preview")
async def preview(
    body: PreviewBody,
    user: CurrentUser = requires("store_invoices.view"),
) -> dict[str, Any]:
    return await service().preview(body.kind, {
        "start": body.start, "end": body.end, "state": body.state,
        "invoiceId": body.invoiceId, "invoiceIds": body.invoiceIds,
        "shipmentIds": body.shipmentIds,
    })


class PrintBody(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    copies: int = Field(default=1, ge=1, le=20)


@router.post("/print")
async def print_report(
    body: PrintBody,
    user: CurrentUser = requires("store_invoices.view"),
) -> dict[str, Any]:
    return await service().print_report(body.path, copies=body.copies)


@router.get("/printer")
async def printer(
    user: CurrentUser = requires("store_invoices.view"),
) -> dict[str, Any]:
    return await service().printer_status()


class ExportBody(BaseModel):
    start: str = Field(default="", max_length=10)
    end: str = Field(default="", max_length=10)
    state: str = Field(default="", max_length=24)


@router.post("/export")
async def export(
    body: ExportBody,
    user: CurrentUser = requires("store_invoices.view"),
) -> dict[str, Any]:
    """Muhasebe biçimi CSV — YASAL NUMARA sütunuyla, rapor klasörüne yazılır."""
    return await service().export_csv(start=body.start, end=body.end, state=body.state)
