"""Müşteriler — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde düğmeyi gizlemek yetkilendirme
değildir. Yıkıcı uçlarda gerekçe `min_length=10` ile ŞEMADA doğrulanır, ayrıca
serviste tekrar denetlenir — istemci şemayı atlatabilir.

İZİN AYRIMI (ADR 0012):
  · `store_customers.view`       — okuma, rapor, CSV
  · `store_customers.manage`     — künye, grup, not
  · `store_customers.deactivate` — pasifleştirme (silme yok)
  · `store_customers.reviews`    — yorum moderasyonu ve mağaza yanıtı
  · `store_customers.gdpr`       — KVKK veri paketi
  · `store_customers.anonymize`  — anonimleştirme, GERİ ALINAMAZ

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran
mesajı gösterir. 4xx yalnız izin/şema kapısından çıkar.
"""

from __future__ import annotations

from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..service import CustomersService

router = APIRouter()
_service: CustomersService | None = None


def bind(service: CustomersService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> CustomersService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


# ================================================================== okuma

@router.get("/customers")
async def customers(
    q: str = Query("", max_length=120),
    segment: str = Query("", max_length=16),
    groupId: int = Query(0, ge=0),
    status: str = Query("", max_length=12),
    city: str = Query("", max_length=60),
    newsletter: str = Query("", max_length=1),
    ordersMin: int | None = Query(None, ge=0),
    ordersMax: int | None = Query(None, ge=0),
    spendMin: int | None = Query(None, ge=0),
    spendMax: int | None = Query(None, ge=0),
    registeredFrom: str = Query("", max_length=10),
    registeredTo: str = Query("", max_length=10),
    lastOrderFrom: str = Query("", max_length=10),
    lastOrderTo: str = Query("", max_length=10),
    sort: str = Query("", max_length=32),
    order: str = Query("desc", max_length=4),
    page: int = Query(1, ge=1, le=10_000),
    size: int = Query(0, ge=0, le=200),
    refresh: bool = Query(False),
    user: CurrentUser = requires("store_customers.view"),
) -> dict[str, Any]:
    """Liste. Segment/harcama/tarih süzgeçleri seçilirse TARAMA yolu açılır."""
    return await service().customers(
        q=q, segment=segment, group_id=groupId, status=status, city=city,
        newsletter=newsletter, orders_min=ordersMin, orders_max=ordersMax,
        spend_min=spendMin, spend_max=spendMax, registered_from=registeredFrom,
        registered_to=registeredTo, last_order_from=lastOrderFrom,
        last_order_to=lastOrderTo, sort=sort, order=order, page=page, size=size,
        refresh=refresh)


@router.get("/overview")
async def overview(
    refresh: bool = Query(False),
    user: CurrentUser = requires("store_customers.view"),
) -> dict[str, Any]:
    """Mini KPI + segment sayaçları. Nüfus taraması gerektirir; ayrı uçtur ki
    liste beklemesin."""
    return await service().overview(refresh=refresh)


@router.get("/reference")
async def reference(
    user: CurrentUser = requires("store_customers.view"),
) -> dict[str, Any]:
    return await service().reference()


@router.get("/customers/{customer_id}")
async def customer(
    customer_id: int,
    user: CurrentUser = requires("store_customers.view"),
) -> dict[str, Any]:
    return await service().card(customer_id)


@router.get("/customers/{customer_id}/orders")
async def customer_orders(
    customer_id: int,
    user: CurrentUser = requires("store_customers.view"),
) -> dict[str, Any]:
    return await service().orders(customer_id)


@router.get("/customers/{customer_id}/addresses")
async def customer_addresses(
    customer_id: int,
    user: CurrentUser = requires("store_customers.view"),
) -> dict[str, Any]:
    return await service().addresses(customer_id)


@router.get("/customers/{customer_id}/returns")
async def customer_returns(
    customer_id: int,
    user: CurrentUser = requires("store_customers.view"),
) -> dict[str, Any]:
    return await service().returns(customer_id)


@router.get("/customers/{customer_id}/notes")
async def customer_notes(
    customer_id: int,
    user: CurrentUser = requires("store_customers.view"),
) -> dict[str, Any]:
    return await service().notes(customer_id)


@router.get("/customers/{customer_id}/consents")
async def customer_consents(
    customer_id: int,
    user: CurrentUser = requires("store_customers.view"),
) -> dict[str, Any]:
    return await service().consents(customer_id)


@router.get("/audit")
async def audit(
    customerId: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    user: CurrentUser = requires("store_customers.view"),
) -> dict[str, Any]:
    return await service().audit(customer_id=customerId, limit=limit)


# ================================================================== yazma

class SaveBody(BaseModel):
    patch: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.put("/customers/{customer_id}")
async def save(
    customer_id: int,
    body: SaveBody,
    user: CurrentUser = requires("store_customers.manage"),
) -> dict[str, Any]:
    """Künye ve grup. `status` BURADAN yazılmaz: pasifleştirme ayrı izindir."""
    return await service().save(customer_id, patch=body.patch, reason=body.reason,
                                actor=user.full_name, dry_run=body.dryRun)


class NoteBody(BaseModel):
    note: str = Field(min_length=3, max_length=2000)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/customers/{customer_id}/notes")
async def add_note(
    customer_id: int,
    body: NoteBody,
    user: CurrentUser = requires("store_customers.manage"),
) -> dict[str, Any]:
    return await service().add_note(customer_id, note=body.note, reason=body.reason,
                                    actor=user.full_name, dry_run=body.dryRun)


class StatusBody(BaseModel):
    customerIds: list[int] = Field(default_factory=list)
    active: bool = False
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/customers/status")
async def set_status(
    body: StatusBody,
    user: CurrentUser = requires("store_customers.deactivate"),
) -> dict[str, Any]:
    """Aktif/Pasif. SİLME UCU YOKTUR — silinen müşterinin siparişi ve faturası
    öksüz kalır (ADR 0012)."""
    return await service().set_status(body.customerIds, active=body.active,
                                      reason=body.reason, actor=user.full_name,
                                      dry_run=body.dryRun)


# =================================================================== KVKK

class ReasonBody(BaseModel):
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/customers/{customer_id}/gdpr-export")
async def gdpr_export(
    customer_id: int,
    body: ReasonBody,
    user: CurrentUser = requires("store_customers.gdpr"),
) -> dict[str, Any]:
    """KVKK veri paketi. Paketi MAĞAZA üretir; burada kişisel veri toplanmaz."""
    return await service().gdpr_export(customer_id, reason=body.reason,
                                       actor=user.full_name, dry_run=body.dryRun)


@router.post("/customers/{customer_id}/anonymize")
async def anonymize(
    customer_id: int,
    body: ReasonBody,
    user: CurrentUser = requires("store_customers.anonymize"),
) -> dict[str, Any]:
    """Anonimleştirme — GERİ ALINAMAZ. Ayrı izin, gerekçe ve kuru prova ister."""
    return await service().anonymize(customer_id, reason=body.reason,
                                     actor=user.full_name, dry_run=body.dryRun)


# ================================================================== yorum

@router.get("/reviews")
async def reviews(
    q: str = Query("", max_length=120),
    status: str = Query("", max_length=16),
    ratings: str = Query("", max_length=16),
    customerId: int = Query(0, ge=0),
    productId: int = Query(0, ge=0),
    unanswered: bool = Query(False),
    page: int = Query(1, ge=1, le=10_000),
    size: int = Query(0, ge=0, le=200),
    user: CurrentUser = requires("store_customers.view"),
) -> dict[str, Any]:
    """Yorum listesi. `ratings` virgüllü çoklu puan: `4,5`."""
    return await service().reviews(q=q, status=status, ratings=ratings,
                                   customer_id=customerId, product_id=productId,
                                   unanswered=unanswered, page=page, size=size)


class ModerateBody(BaseModel):
    reviewIds: list[int] = Field(default_factory=list)
    action: str = Field(max_length=12)          # approve | reject | spam | pending
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/reviews/status")
async def moderate(
    body: ModerateBody,
    user: CurrentUser = requires("store_customers.reviews"),
) -> dict[str, Any]:
    """Onayla · Reddet · Spam · Beklemeye al. Yorum SİLİNMEZ."""
    return await service().moderate(body.reviewIds, action=body.action, reason=body.reason,
                                    actor=user.full_name, dry_run=body.dryRun)


class ReplyBody(BaseModel):
    body: str = Field(min_length=5, max_length=2000)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/reviews/{review_id}/reply")
async def reply(
    review_id: int,
    body: ReplyBody,
    user: CurrentUser = requires("store_customers.reviews"),
) -> dict[str, Any]:
    """Mağaza yanıtı — VİTRİNDE yayınlanır."""
    return await service().reply(review_id, body=body.body, reason=body.reason,
                                 actor=user.full_name, dry_run=body.dryRun)


# ================================================================ ayarlar

@router.get("/settings")
async def settings(
    user: CurrentUser = requires("store_customers.view"),
) -> dict[str, Any]:
    return await service().settings()


class SettingsBody(BaseModel):
    emailVerification: bool | None = None
    newsletterDefault: bool | None = None
    #: Grup KODU (`general` · `wholesale`) — mağaza varsayılan grubu kodla
    #: tutuyor, kimlikle değil (canlıda doğrulandı).
    defaultGroup: str | None = Field(default=None, max_length=64)
    gdprEnabled: bool | None = None
    gdprText: str | None = Field(default=None, max_length=20_000)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/settings")
async def save_settings(
    body: SettingsBody,
    user: CurrentUser = requires("store_customers.manage"),
) -> dict[str, Any]:
    values = body.model_dump(exclude={"reason", "dryRun"}, exclude_none=True)
    return await service().save_settings(values=values, reason=body.reason,
                                         actor=user.full_name, dry_run=body.dryRun)


# ================================================================== rapor

class PreviewBody(BaseModel):
    kind: str = Field(max_length=24)            # segments | reviews | card
    customerId: int = Field(default=0, ge=0)


@router.post("/preview")
async def preview(
    body: PreviewBody,
    user: CurrentUser = requires("store_customers.view"),
) -> dict[str, Any]:
    return await service().preview(body.kind, {"customerId": body.customerId})


class PrintBody(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    copies: int = Field(default=1, ge=1, le=20)


@router.post("/print")
async def print_report(
    body: PrintBody,
    user: CurrentUser = requires("store_customers.view"),
) -> dict[str, Any]:
    return await service().print_report(body.path, copies=body.copies)


@router.get("/printer")
async def printer(
    user: CurrentUser = requires("store_customers.view"),
) -> dict[str, Any]:
    return await service().printer_status()


class ExportBody(BaseModel):
    q: str = Field(default="", max_length=120)
    segment: str = Field(default="", max_length=16)
    groupId: int = Field(default=0, ge=0)
    status: str = Field(default="", max_length=12)
    city: str = Field(default="", max_length=60)


@router.post("/export")
async def export(
    body: ExportBody,
    user: CurrentUser = requires("store_customers.view"),
) -> dict[str, Any]:
    """TÜM kayıtların CSV'si — rapor klasörüne 0600 izinle yazılır. Görünen
    sayfanın CSV'sini panel kendisi üretir (sunucuya hiç gitmez)."""
    return await service().export_csv(filters={
        "q": body.q, "segment": body.segment, "group_id": body.groupId,
        "status": body.status, "city": body.city,
    })
