"""Siparişler — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde düğmeyi gizlemek yetkilendirme
değildir. Yıkıcı uçlarda gerekçe `min_length=10` ile ŞEMADA doğrulanır, ayrıca
serviste tekrar denetlenir — istemci şemayı atlatabilir.

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran mesajı
gösterir. 4xx yalnız izin/şema kapısından çıkar.

SÜZGEÇLER TEK GÖVDEDE. On beş süzgeç `Query` parametresi olarak yazılırsa imza
okunmaz hâle gelir ve her yeni süzgeç ucun imzasını değiştirir; liste ve özet
uçları aynı `OrderFilters` modelini paylaşır. GET olduğu için değerler yine
sorgu dizesinden gelir (`Depends` yerine düz ayrıştırma: gövdeli GET yasak).
"""

from __future__ import annotations

from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..service import OrdersService

router = APIRouter()
_service: OrdersService | None = None


def bind(service: OrdersService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> OrdersService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


def _filters(*, q: str, status: str, payment_state: str, payment_method: str, carrier: str,
             channel: str, customer_group: str, date_field: str, date_from: str, date_to: str,
             total_min: int | None, total_max: int | None, city: str, coupon: str, chip: str,
             no_invoice: bool, no_shipment: bool, has_note: bool, risky: bool,
             partial_refund: bool) -> dict[str, Any]:
    """Sorgu dizesini servisin beklediği süzgeç sözlüğüne çevirir."""
    return {
        "q": q, "status": status, "paymentState": payment_state,
        "paymentMethod": payment_method, "carrier": carrier, "channel": channel,
        "customerGroup": customer_group, "dateField": date_field, "dateFrom": date_from,
        "dateTo": date_to, "totalMin": total_min, "totalMax": total_max, "city": city,
        "coupon": coupon, "chip": chip, "noInvoice": no_invoice, "noShipment": no_shipment,
        "hasNote": has_note, "risky": risky, "partialRefund": partial_refund,
    }


# ================================================================== okuma

@router.get("/orders")
async def orders(
    q: str = Query("", max_length=120),
    status: str = Query("", max_length=32),
    paymentState: str = Query("", max_length=16),
    paymentMethod: str = Query("", max_length=64),
    carrier: str = Query("", max_length=64),
    channel: str = Query("", max_length=32),
    customerGroup: str = Query("", max_length=64),
    dateField: str = Query("created", max_length=16),
    dateFrom: str = Query("", max_length=10),
    dateTo: str = Query("", max_length=10),
    totalMin: int | None = Query(None, ge=0),
    totalMax: int | None = Query(None, ge=0),
    city: str = Query("", max_length=64),
    coupon: str = Query("", max_length=64),
    chip: str = Query("", max_length=24),
    noInvoice: bool = Query(False),
    noShipment: bool = Query(False),
    hasNote: bool = Query(False),
    risky: bool = Query(False),
    partialRefund: bool = Query(False),
    page: int = Query(1, ge=1, le=10_000),
    size: int = Query(0, ge=0, le=200),
    user: CurrentUser = requires("store_orders.view"),
) -> dict[str, Any]:
    return await service().orders(
        filters=_filters(q=q, status=status, payment_state=paymentState,
                         payment_method=paymentMethod, carrier=carrier, channel=channel,
                         customer_group=customerGroup, date_field=dateField, date_from=dateFrom,
                         date_to=dateTo, total_min=totalMin, total_max=totalMax, city=city,
                         coupon=coupon, chip=chip, no_invoice=noInvoice, no_shipment=noShipment,
                         has_note=hasNote, risky=risky, partial_refund=partialRefund),
        page=page, size=size)


@router.get("/overview")
async def overview(
    q: str = Query("", max_length=120),
    status: str = Query("", max_length=32),
    paymentState: str = Query("", max_length=16),
    paymentMethod: str = Query("", max_length=64),
    carrier: str = Query("", max_length=64),
    channel: str = Query("", max_length=32),
    customerGroup: str = Query("", max_length=64),
    dateField: str = Query("created", max_length=16),
    dateFrom: str = Query("", max_length=10),
    dateTo: str = Query("", max_length=10),
    totalMin: int | None = Query(None, ge=0),
    totalMax: int | None = Query(None, ge=0),
    city: str = Query("", max_length=64),
    coupon: str = Query("", max_length=64),
    chip: str = Query("", max_length=24),
    noInvoice: bool = Query(False),
    noShipment: bool = Query(False),
    hasNote: bool = Query(False),
    risky: bool = Query(False),
    partialRefund: bool = Query(False),
    user: CurrentUser = requires("store_orders.view"),
) -> dict[str, Any]:
    """Çip sayaçları + süzgecin TAMAMININ mini toplamı (adet · ciro · ortalama)."""
    return await service().overview(
        filters=_filters(q=q, status=status, payment_state=paymentState,
                         payment_method=paymentMethod, carrier=carrier, channel=channel,
                         customer_group=customerGroup, date_field=dateField, date_from=dateFrom,
                         date_to=dateTo, total_min=totalMin, total_max=totalMax, city=city,
                         coupon=coupon, chip=chip, no_invoice=noInvoice, no_shipment=noShipment,
                         has_note=hasNote, risky=risky, partial_refund=partialRefund))


@router.get("/orders/{order_id}")
async def order(
    order_id: int,
    user: CurrentUser = requires("store_orders.view"),
) -> dict[str, Any]:
    return await service().card(order_id)


@router.get("/orders/{order_id}/comments")
async def comments(
    order_id: int,
    user: CurrentUser = requires("store_orders.view"),
) -> dict[str, Any]:
    return await service().comments(order_id)


@router.get("/reference")
async def reference(
    user: CurrentUser = requires("store_orders.view"),
) -> dict[str, Any]:
    return await service().reference()


@router.get("/audit")
async def audit(
    orderId: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    user: CurrentUser = requires("store_orders.view"),
) -> dict[str, Any]:
    """Bu ekrandan yapılan yazmaların YEREL izi. Mağazanın kendi denetim kaydı
    `store.audit.for` yeteneğinden gelir ve o yetenek yoksa panel yalnız bunu
    gösterir."""
    return await service().audit(order_id=orderId, limit=limit)


# ================================================================== yazma

class CommentBody(BaseModel):
    comment: str = Field(min_length=2, max_length=2000)
    #: MÜŞTERİYE E-POSTA GÖNDERİR. Varsayılan kapalı: iç not yazan personelin
    #: kazayla müşteriye posta atması, geri alınamayan bir hatadır.
    notify: bool = False
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/orders/{order_id}/comments")
async def add_comment(
    order_id: int,
    body: CommentBody,
    user: CurrentUser = requires("store_orders.manage"),
) -> dict[str, Any]:
    return await service().add_comment(order_id, comment=body.comment, notify=body.notify,
                                       reason=body.reason, actor=user.full_name,
                                       dry_run=body.dryRun)


class InvoiceBody(BaseModel):
    #: {siparişKalemId: adet}. Boş bırakılırsa siparişin tamamı faturalanır.
    items: dict[str, int] = Field(default_factory=dict)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/orders/{order_id}/invoice")
async def invoice(
    order_id: int,
    body: InvoiceBody,
    user: CurrentUser = requires("store_orders.manage"),
) -> dict[str, Any]:
    return await service().invoice(order_id, items=body.items, reason=body.reason,
                                   actor=user.full_name, dry_run=body.dryRun)


class ShipBody(BaseModel):
    items: dict[str, int] = Field(default_factory=dict)
    carrier: str = Field(default="", max_length=64)
    track: str = Field(default="", max_length=64)
    sourceId: int = Field(default=1, ge=1)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/orders/{order_id}/ship")
async def ship(
    order_id: int,
    body: ShipBody,
    user: CurrentUser = requires("store_orders.manage"),
) -> dict[str, Any]:
    """Bagisto'nun KENDİ gönderi kaydını açar. Etiket SATIN ALINMAZ — o iş
    Kargo Yönetimi ekranınındır ve para harcar."""
    return await service().ship(order_id, items=body.items, carrier=body.carrier,
                                track=body.track, source_id=body.sourceId, reason=body.reason,
                                actor=user.full_name, dry_run=body.dryRun)


class ReasonBody(BaseModel):
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/orders/{order_id}/cancel")
async def cancel(
    order_id: int,
    body: ReasonBody,
    user: CurrentUser = requires("store_orders.cancel"),
) -> dict[str, Any]:
    """Sipariş iptali GERİ ALINAMAZ; ayrı izin, gerekçe ve süre penceresi ister."""
    return await service().cancel(order_id, reason=body.reason, actor=user.full_name,
                                  dry_run=body.dryRun)


# ============================================================ toplu işlem

class BatchPreviewBody(BaseModel):
    kind: str = Field(max_length=16)          # ship | invoice
    orderIds: list[int] = Field(default_factory=list)


@router.post("/batch/preview")
async def batch_preview(
    body: BatchPreviewBody,
    user: CurrentUser = requires("store_orders.manage"),
) -> dict[str, Any]:
    """Hangi siparişin atlanacağını ve NEDEN atlandığını gösterir. Yazmaz —
    gerekçe istemez."""
    return await service().batch_preview(kind=body.kind, order_ids=body.orderIds)


class BatchApplyBody(BaseModel):
    token: str = Field(min_length=8, max_length=64)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/batch/apply")
async def batch_apply(
    body: BatchApplyBody,
    user: CurrentUser = requires("store_orders.manage"),
) -> dict[str, Any]:
    return await service().batch_apply(token=body.token, reason=body.reason,
                                       actor=user.full_name, dry_run=body.dryRun)


class LabelsBody(BaseModel):
    orderIds: list[int] = Field(default_factory=list)


@router.post("/labels")
async def labels(
    body: LabelsBody,
    user: CurrentUser = requires("store_orders.manage"),
) -> dict[str, Any]:
    """Kargo etiketlerini rapor klasörüne indirir. Etiket SATIN ALMAZ; yalnız
    daha önce alınmış etiketin PDF'ini getirir."""
    return await service().labels(body.orderIds)


# =============================================================== ayarlar

@router.get("/settings")
async def settings(
    user: CurrentUser = requires("store_orders.view"),
) -> dict[str, Any]:
    return await service().settings()


class SettingsBody(BaseModel):
    statusNames: dict[str, str] | None = None
    orderNoFormat: str | None = Field(default=None, max_length=40)
    cancelWindowHours: int | None = Field(default=None, ge=0, le=8_760)
    lateDays: int | None = Field(default=None, ge=1, le=90)
    reason: str = Field(min_length=10, max_length=255)


@router.post("/settings")
async def save_settings(
    body: SettingsBody,
    user: CurrentUser = requires("store_orders.manage"),
) -> dict[str, Any]:
    """YALNIZ yerel tercihler yazılır; mağazaya hiçbir şey gönderilmez."""
    return await service().save_settings(status_names=body.statusNames,
                                         order_no_format=body.orderNoFormat,
                                         cancel_window_hours=body.cancelWindowHours,
                                         late_days=body.lateDays, reason=body.reason,
                                         actor=user.full_name)


# ================================================================= rapor

class PreviewBody(BaseModel):
    kind: str = Field(max_length=24)          # list | slip | manifest
    orderId: int = Field(default=0, ge=0)
    orderIds: list[int] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)


@router.post("/preview")
async def preview(
    body: PreviewBody,
    user: CurrentUser = requires("store_orders.view"),
) -> dict[str, Any]:
    return await service().preview(body.kind, {"orderId": body.orderId,
                                               "orderIds": body.orderIds,
                                               "filters": body.filters})


class PrintBody(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    copies: int = Field(default=1, ge=1, le=20)


@router.post("/print")
async def print_report(
    body: PrintBody,
    user: CurrentUser = requires("store_orders.view"),
) -> dict[str, Any]:
    return await service().print_report(body.path, copies=body.copies)


@router.get("/printer")
async def printer(
    user: CurrentUser = requires("store_orders.view"),
) -> dict[str, Any]:
    return await service().printer_status()


class ExportBody(BaseModel):
    filters: dict[str, Any] = Field(default_factory=dict)


@router.post("/export")
async def export(
    body: ExportBody,
    user: CurrentUser = requires("store_orders.view"),
) -> dict[str, Any]:
    """TÜM kayıtların CSV'si — rapor klasörüne yazılır. Görünen sayfanın CSV'sini
    panel kendisi üretir (sunucuya hiç gitmez)."""
    return await service().export_csv(filters=body.filters)
