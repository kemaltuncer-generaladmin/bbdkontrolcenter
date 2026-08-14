"""Kargo Yönetimi — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde düğmeyi gizlemek yetkilendirme
değildir. Yıkıcı uçlarda gerekçe `min_length` ile ŞEMADA doğrulanır, ayrıca
serviste tekrar denetlenir — istemci şemayı atlatabilir.

PARA HARCAYAN İKİ UÇ (`/purchase`, `/return`) ayrı izin anahtarı ister
(`store_shipping.purchase`), gerekçesi en az 20 karakterdir ve `dryRun`
varsayılanı `true`'dur: onay kutusunda "uygula" işaretlenmeden mağazaya
gerçek istek gitmez.

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran
mesajı gösterir. 4xx yalnız izin/şema kapısından çıkar.
"""

from __future__ import annotations

from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..service import ShippingService

router = APIRouter()
_service: ShippingService | None = None

#: Para harcayan işlemde gerekçe daha uzun istenir; serviste de doğrulanır.
PURCHASE_REASON = 20


def bind(service: ShippingService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> ShippingService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


# ================================================================== okuma

@router.get("/ready")
async def ready(
    q: str = Query("", max_length=120),
    page: int = Query(1, ge=1, le=10_000),
    size: int = Query(0, ge=0, le=200),
    user: CurrentUser = requires("store_shipping.view"),
) -> dict[str, Any]:
    """Ödemesi alınmış ama kargolanmamış siparişler."""
    return await service().ready(q=q, page=page, size=size)


@router.get("/shipments")
async def shipments(
    q: str = Query("", max_length=120),
    carrier: str = Query("", max_length=32),
    status: str = Query("", max_length=32),
    city: str = Query("", max_length=64),
    dateFrom: str = Query("", max_length=10),
    dateTo: str = Query("", max_length=10),
    dateField: str = Query("created", max_length=16),
    payer: str = Query("", max_length=16),
    desiMin: int = Query(0, ge=0, le=10_000),
    desiMax: int = Query(0, ge=0, le=10_000),
    flag: str = Query("", max_length=16),
    page: int = Query(1, ge=1, le=10_000),
    size: int = Query(0, ge=0, le=200),
    user: CurrentUser = requires("store_shipping.view"),
) -> dict[str, Any]:
    return await service().shipments(q=q, carrier=carrier, status=status, city=city,
                                     date_from=dateFrom, date_to=dateTo, date_field=dateField,
                                     payer=payer, desi_min=desiMin, desi_max=desiMax,
                                     flag=flag, page=page, size=size)


@router.get("/shipments/{shipment_id}")
async def shipment(
    shipment_id: int,
    user: CurrentUser = requires("store_shipping.view"),
) -> dict[str, Any]:
    return await service().shipment(shipment_id)


@router.get("/shipments/{shipment_id}/offers")
async def offers(
    shipment_id: int,
    user: CurrentUser = requires("store_shipping.manage"),
) -> dict[str, Any]:
    """Taşıyıcı teklifleri. Okuma ucudur ama `manage` ister: teklif listesi
    satın alma ekranının kapısıdır ve fiyat pazarlığı operasyon bilgisidir."""
    return await service().offers(shipment_id)


@router.get("/orders/{order_id}/shipments")
async def by_order(
    order_id: int,
    user: CurrentUser = requires("store_shipping.view"),
) -> dict[str, Any]:
    """Siparişin gönderileri. `store.shipment.byOrder` yeteneğinin HTTP karşılığı."""
    return await service().by_order(order_id)


@router.get("/carriers")
async def carriers(
    user: CurrentUser = requires("store_shipping.view"),
) -> dict[str, Any]:
    return await service().carriers()


@router.get("/rates")
async def rates(
    user: CurrentUser = requires("store_shipping.view"),
) -> dict[str, Any]:
    return await service().rates()


@router.get("/zones")
async def zones(
    user: CurrentUser = requires("store_shipping.view"),
) -> dict[str, Any]:
    return await service().zones()


@router.get("/performance")
async def performance(
    days: int = Query(0, ge=0, le=365),
    carrier: str = Query("", max_length=32),
    user: CurrentUser = requires("store_shipping.view"),
) -> dict[str, Any]:
    return await service().performance(days=days, carrier=carrier)


@router.get("/audit")
async def audit(
    shipmentId: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    user: CurrentUser = requires("store_shipping.view"),
) -> dict[str, Any]:
    return await service().audit(shipment_id=shipmentId, limit=limit)


@router.get("/settings")
async def settings(
    user: CurrentUser = requires("store_shipping.view"),
) -> dict[str, Any]:
    return await service().settings()


# ============================================================== sihirbaz

class QuoteBody(BaseModel):
    orderId: int = Field(default=0, ge=0)
    carrier: str = Field(default="", max_length=32)
    desi: float = Field(default=0, ge=0, le=100_000)
    weight: float = Field(default=0, ge=0, le=100_000)
    payer: str = Field(default="sender", max_length=16)
    cod: int = Field(default=0, ge=0)      # kuruş


@router.post("/quote")
async def quote(
    body: QuoteBody,
    user: CurrentUser = requires("store_shipping.view"),
) -> dict[str, Any]:
    """Ücret dökümü ÖNİZLEMESİ. Mağazaya yazmaz, gerekçe istemez."""
    return await service().quote(order_id=body.orderId, carrier=body.carrier,
                                 desi_value=body.desi, weight=body.weight,
                                 payer=body.payer, cod=body.cod)


class CreateBody(BaseModel):
    # TEST YOLUNDA TAŞIYICI SEÇİLMEZ (taşıyıcı yoktur), bu yüzden şema onu
    # zorunlu tutmaz. Gerçek yolun koruması ŞEMADA DEĞİL SERVİSTE: taşıyıcısız
    # bir Geliver gönderisi `wizard_problems` tarafından engellenir. Şema
    # gevşedi, kural gevşemedi (K9 — istemci şemayı atlatabilir).
    carrier: str = Field(default="", max_length=32)
    #: "geliver" (gerçek, para harcar) · "bagisto" (test) · "" (ayardaki yol).
    provider: str = Field(default="", max_length=16)
    packages: int = Field(default=1, ge=1, le=99)
    desi: float = Field(default=0, ge=0, le=100_000)
    weight: float = Field(default=0, ge=0, le=100_000)
    payer: str = Field(default="sender", max_length=16)
    cod: int = Field(default=0, ge=0)      # kuruş
    note: str = Field(default="", max_length=255)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/orders/{order_id}/shipments")
async def create_shipment(
    order_id: int,
    body: CreateBody,
    user: CurrentUser = requires("store_shipping.manage"),
) -> dict[str, Any]:
    """Gönderi TASLAĞI açar; etiket satın alınmaz (o ayrı izin ister)."""
    return await service().create_shipment(order_id, carrier=body.carrier,
                                           packages=body.packages, desi_value=body.desi,
                                           weight=body.weight, payer=body.payer, cod=body.cod,
                                           note=body.note, reason=body.reason,
                                           actor=user.full_name, dry_run=body.dryRun,
                                           provider=body.provider)


# ================================================== PARA HARCAYAN UÇLAR

class PurchaseBody(BaseModel):
    offerId: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=PURCHASE_REASON, max_length=255)
    dryRun: bool = True


@router.post("/shipments/{shipment_id}/purchase")
async def purchase(
    shipment_id: int,
    body: PurchaseBody,
    user: CurrentUser = requires("store_shipping.purchase"),
) -> dict[str, Any]:
    """ETİKET SATIN ALIR — PARA HARCAR. Ayrı izin + 20 karakter gerekçe + dryRun."""
    return await service().purchase(shipment_id, offer_id=body.offerId, reason=body.reason,
                                    actor=user.full_name, dry_run=body.dryRun)


class LongReasonBody(BaseModel):
    reason: str = Field(min_length=PURCHASE_REASON, max_length=255)
    dryRun: bool = True


@router.post("/shipments/{shipment_id}/return")
async def retour(
    shipment_id: int,
    body: LongReasonBody,
    user: CurrentUser = requires("store_shipping.purchase"),
) -> dict[str, Any]:
    """İade gönderisi de ETİKET ALIR: aynı izin ve aynı uzunlukta gerekçe."""
    return await service().retour(shipment_id, reason=body.reason, actor=user.full_name,
                                  dry_run=body.dryRun)


class ReasonBody(BaseModel):
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/shipments/{shipment_id}/cancel")
async def cancel(
    shipment_id: int,
    body: ReasonBody,
    user: CurrentUser = requires("store_shipping.cancel"),
) -> dict[str, Any]:
    return await service().cancel(shipment_id, reason=body.reason, actor=user.full_name,
                                  dry_run=body.dryRun)


class SyncBody(BaseModel):
    shipmentIds: list[int] = Field(default_factory=list)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/shipments/sync")
async def sync(
    body: SyncBody,
    user: CurrentUser = requires("store_shipping.manage"),
) -> dict[str, Any]:
    return await service().sync(body.shipmentIds, reason=body.reason, actor=user.full_name,
                                dry_run=body.dryRun)


class NotifyBody(BaseModel):
    template: str = Field(default="shipment_status", max_length=64)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/shipments/{shipment_id}/notify")
async def notify(
    shipment_id: int,
    body: NotifyBody,
    user: CurrentUser = requires("store_shipping.manage"),
) -> dict[str, Any]:
    return await service().notify_customer(shipment_id, template=body.template,
                                           reason=body.reason, actor=user.full_name,
                                           dry_run=body.dryRun)


class CarrierTestBody(BaseModel):
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/carriers/{carrier}/test")
async def test_carrier(
    carrier: str,
    body: CarrierTestBody,
    user: CurrentUser = requires("store_shipping.manage"),
) -> dict[str, Any]:
    return await service().test_carrier(carrier, reason=body.reason, actor=user.full_name,
                                        dry_run=body.dryRun)


# =========================================================== ücretlendirme

class TierBody(BaseModel):
    min: float = Field(default=0, ge=0, le=100_000)
    max: float = Field(default=0, ge=0, le=100_000)   # 0 = açık uçlu (son kademe)
    price: int = Field(default=0, ge=0)               # kuruş
    label: str = Field(default="", max_length=64)


class CarrierRatesBody(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    tiers: list[TierBody] = Field(default_factory=list)


class RatesBody(BaseModel):
    carriers: list[CarrierRatesBody] = Field(default_factory=list)
    freeThreshold: int = Field(default=0, ge=0)       # kuruş
    codFee: int = Field(default=0, ge=0)              # kuruş
    promise: str = Field(default="", max_length=255)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/rates")
async def save_rates(
    body: RatesBody,
    user: CurrentUser = requires("store_shipping.rates"),
) -> dict[str, Any]:
    """Desi→ücret matrisi VİTRİNİ etkiler; ayrı izin anahtarı ister."""
    return await service().save_rates(
        carriers=[{"code": item.code, "tiers": [tier.model_dump() for tier in item.tiers]}
                  for item in body.carriers],
        free_threshold=body.freeThreshold, cod_fee=body.codFee, promise=body.promise,
        reason=body.reason, actor=user.full_name, dry_run=body.dryRun)


@router.post("/rates/refresh")
async def refresh_price_list(
    body: ReasonBody,
    user: CurrentUser = requires("store_shipping.rates"),
) -> dict[str, Any]:
    return await service().refresh_price_list(reason=body.reason, actor=user.full_name,
                                              dry_run=body.dryRun)


class ZoneBody(BaseModel):
    city: str = Field(min_length=1, max_length=64)
    district: str = Field(default="", max_length=64)
    zone: str = Field(default="", max_length=64)
    surcharge: int = Field(default=0, ge=0)           # kuruş
    delivers: bool = True
    note: str = Field(default="", max_length=255)
    reason: str = Field(min_length=10, max_length=255)


@router.post("/zones")
async def save_zone(
    body: ZoneBody,
    user: CurrentUser = requires("store_shipping.rates"),
) -> dict[str, Any]:
    """Bölge satırı YEREL tabloya yazılır; silme yoktur (`delivers = false`)."""
    return await service().save_zone(city=body.city, district=body.district, zone=body.zone,
                                     surcharge=body.surcharge, delivers=body.delivers,
                                     note=body.note, reason=body.reason,
                                     actor=user.full_name)


class SettingsBody(BaseModel):
    labelFormat: str = Field(default="", max_length=32)
    defaultCarrier: str = Field(default="", max_length=32)
    idleDays: int | None = Field(default=None, ge=1, le=30)
    reason: str = Field(min_length=10, max_length=255)


@router.post("/settings")
async def save_settings(
    body: SettingsBody,
    user: CurrentUser = requires("store_shipping.manage"),
) -> dict[str, Any]:
    return await service().save_settings(label_format=body.labelFormat,
                                         default_carrier=body.defaultCarrier,
                                         idle_days=body.idleDays, reason=body.reason,
                                         actor=user.full_name)


# ================================================================= belge

class PreviewBody(BaseModel):
    # labels | manifest | performance | receipt | handover
    kind: str = Field(max_length=24)
    shipmentIds: list[int] = Field(default_factory=list)
    format: str = Field(default="", max_length=32)
    carrier: str = Field(default="", max_length=32)
    driver: str = Field(default="", max_length=120)
    days: int = Field(default=0, ge=0, le=365)
    #: `receipt` ve `handover` gönderi değil SİPARİŞ üzerinden çalışır: teslim
    #: fişi kargo kaydı açılmadan da basılabilmeli (paket hazırlanırken).
    orderId: int = Field(default=0, ge=0)
    trackNumber: str = Field(default="", max_length=64)


@router.post("/preview")
async def preview(
    body: PreviewBody,
    user: CurrentUser = requires("store_shipping.view"),
) -> dict[str, Any]:
    """Etiket sayfası · manifesto · performans · kargo fişi · teslim fişi üretir."""
    return await service().preview(body.kind, {
        "shipmentIds": body.shipmentIds, "format": body.format, "carrier": body.carrier,
        "driver": body.driver, "days": body.days,
        "orderId": body.orderId, "trackNumber": body.trackNumber,
    })


class PrintBody(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    copies: int = Field(default=1, ge=1, le=20)


@router.post("/print")
async def print_report(
    body: PrintBody,
    user: CurrentUser = requires("store_shipping.view"),
) -> dict[str, Any]:
    return await service().print_report(body.path, copies=body.copies)


@router.get("/printer")
async def printer(
    user: CurrentUser = requires("store_shipping.view"),
) -> dict[str, Any]:
    return await service().printer_status()


class ExportBody(BaseModel):
    carrier: str = Field(default="", max_length=32)
    status: str = Field(default="", max_length=32)
    dateFrom: str = Field(default="", max_length=10)
    dateTo: str = Field(default="", max_length=10)


@router.post("/export")
async def export(
    body: ExportBody,
    user: CurrentUser = requires("store_shipping.view"),
) -> dict[str, Any]:
    """TÜM gönderilerin CSV'si — rapor klasörüne yazılır. Görünen sayfanın
    CSV'sini panel kendisi üretir (sunucuya hiç gitmez)."""
    filters: dict[str, Any] = {}
    if body.carrier:
        filters["carrier"] = body.carrier
    if body.status:
        filters["status"] = body.status
    if body.dateFrom:
        filters["date_from"] = body.dateFrom
    if body.dateTo:
        filters["date_to"] = body.dateTo
    return await service().export_csv(filters=filters)
