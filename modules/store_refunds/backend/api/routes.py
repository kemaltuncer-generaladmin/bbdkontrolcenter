"""İadeler — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde düğmeyi gizlemek yetkilendirme
değildir. Para hareketi olan iki uç (`/approve`, `/pos-refund`) AYRI izin
anahtarı ister — `store_refunds.approve`. Gerekçe `min_length=10` ile şemada
doğrulanır, ayrıca serviste tekrar denetlenir; istemci şemayı atlatabilir.

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran mesajı
gösterir. 4xx yalnız izin/şema kapısından çıkar.
"""

from __future__ import annotations

from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..service import RefundsService

router = APIRouter()
_service: RefundsService | None = None


def bind(service: RefundsService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> RefundsService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


# ================================================================== okuma

@router.get("/refunds")
async def refunds(
    dateFrom: str = Query("", max_length=10),
    dateTo: str = Query("", max_length=10),
    user: CurrentUser = requires("store_refunds.view"),
) -> dict[str, Any]:
    """Kredi notları + RMA talepleri, tek liste + KPI. Süzme istemcidedir."""
    return await service().refunds(date_from=dateFrom, date_to=dateTo)


@router.get("/orders/{order_id}")
async def order_card(
    order_id: int,
    user: CurrentUser = requires("store_refunds.view"),
) -> dict[str, Any]:
    """Çekmecenin tek isteği: kalemler, kargo, POS denemeleri, geçmiş iadeler."""
    return await service().order_card(order_id)


@router.get("/audit")
async def audit(
    orderId: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    user: CurrentUser = requires("store_refunds.view"),
) -> dict[str, Any]:
    return await service().audit(order_id=orderId, limit=limit)


# ================================================================== hesap

class CalculateBody(BaseModel):
    orderId: int = Field(ge=1)
    #: {siparişKalemId: adet} — kısmi iade burada belirlenir.
    quantities: dict[str, int] = Field(default_factory=dict)
    refundShipping: bool = False
    adjustmentRefund: int = Field(default=0, ge=0)      # kuruş
    adjustmentFee: int = Field(default=0, ge=0)         # kuruş


@router.post("/calculate")
async def calculate(
    body: CalculateBody,
    user: CurrentUser = requires("store_refunds.view"),
) -> dict[str, Any]:
    """İade tutarını satır satır hesaplar ve onay jetonu üretir.

    YAZMAZ, gerekçe istemez. `view` yeterlidir: muhasebeci de tutarı görebilmeli;
    parayı çıkaran uç ayrıdır ve ayrı izin ister.
    """
    return await service().calculate(body.orderId, quantities=body.quantities,
                                     refund_shipping=body.refundShipping,
                                     adjustment_refund=body.adjustmentRefund,
                                     adjustment_fee=body.adjustmentFee)


# ----------------------------------------------------------- para hareketi

class ApproveBody(BaseModel):
    token: str = Field(min_length=8, max_length=64)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/approve")
async def approve(
    body: ApproveBody,
    user: CurrentUser = requires("store_refunds.approve"),
) -> dict[str, Any]:
    """Onaylanan hesabı uygular: kredi notu oluşur. PARA HAREKETİDİR."""
    return await service().approve(token=body.token, reason=body.reason,
                                   actor=user.full_name, dry_run=body.dryRun)


class PosRefundBody(BaseModel):
    attemptId: int = Field(ge=1)
    amount: int = Field(ge=1)                # kuruş
    orderId: int = Field(default=0, ge=0)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/pos-refund")
async def pos_refund(
    body: PosRefundBody,
    user: CurrentUser = requires("store_refunds.approve"),
) -> dict[str, Any]:
    """Sanal POS iadesi — parayı karta geri verir. Kredi notundan AYRIDIR."""
    return await service().pos_refund(attempt_id=body.attemptId, amount=body.amount,
                                      order_id=body.orderId, reason=body.reason,
                                      actor=user.full_name, dry_run=body.dryRun)


# =================================================================== süreç

class RequestStatusBody(BaseModel):
    status: str = Field(max_length=24)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/requests/{request_id}/status")
async def request_status(
    request_id: int,
    body: RequestStatusBody,
    user: CurrentUser = requires("store_refunds.manage"),
) -> dict[str, Any]:
    """Talebin sürecini ilerletir. 'İade edildi' buradan YAZILAMAZ."""
    return await service().set_request_status(request_id, status=body.status, reason=body.reason,
                                              actor=user.full_name, dry_run=body.dryRun)


class ReturnShipmentBody(BaseModel):
    shipmentId: int = Field(ge=1)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/orders/{order_id}/return-shipment")
async def return_shipment(
    order_id: int,
    body: ReturnShipmentBody,
    user: CurrentUser = requires("store_refunds.ship_return"),
) -> dict[str, Any]:
    """Geri alım etiketi açar. PARA HARCAR (kargo ücreti) — bu yüzden AYRI izin
    anahtarı ister; `manage` yetmez (ADR 0012)."""
    return await service().return_shipment(body.shipmentId, order_id=order_id,
                                           reason=body.reason, actor=user.full_name,
                                           dry_run=body.dryRun)


class NotifyBody(BaseModel):
    message: str = Field(min_length=10, max_length=2000)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/orders/{order_id}/notify")
async def notify(
    order_id: int,
    body: NotifyBody,
    user: CurrentUser = requires("store_refunds.manage"),
) -> dict[str, Any]:
    """Siparişe not düşer ve müşteriye e-posta gider."""
    return await service().notify_customer(order_id, message=body.message, reason=body.reason,
                                           actor=user.full_name, dry_run=body.dryRun)


# =================================================================== rapor

class PreviewBody(BaseModel):
    kind: str = Field(max_length=24)
    dateFrom: str = Field(default="", max_length=10)
    dateTo: str = Field(default="", max_length=10)
    orderId: int = Field(default=0, ge=0)


@router.post("/preview")
async def preview(
    body: PreviewBody,
    user: CurrentUser = requires("store_refunds.view"),
) -> dict[str, Any]:
    return await service().preview(body.kind, {"dateFrom": body.dateFrom,
                                               "dateTo": body.dateTo,
                                               "orderId": body.orderId})


class PrintBody(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    copies: int = Field(default=1, ge=1, le=20)


@router.post("/print")
async def print_report(
    body: PrintBody,
    user: CurrentUser = requires("store_refunds.view"),
) -> dict[str, Any]:
    return await service().print_report(body.path, copies=body.copies)


@router.get("/printer")
async def printer(
    user: CurrentUser = requires("store_refunds.view"),
) -> dict[str, Any]:
    return await service().printer_status()


class ExportBody(BaseModel):
    dateFrom: str = Field(default="", max_length=10)
    dateTo: str = Field(default="", max_length=10)


@router.post("/export")
async def export(
    body: ExportBody,
    user: CurrentUser = requires("store_refunds.view"),
) -> dict[str, Any]:
    """Pencerenin tamamının CSV'si — rapor klasörüne yazılır. Görünen sayfanın
    CSV'sini panel kendisi üretir (sunucuya hiç gitmez)."""
    return await service().export_csv(date_from=body.dateFrom, date_to=body.dateTo)
