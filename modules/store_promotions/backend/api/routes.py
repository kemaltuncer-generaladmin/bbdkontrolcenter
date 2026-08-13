"""Promosyonlar — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde düğmeyi gizlemek yetkilendirme
değildir. Yıkıcı ve parayı etkileyen uçlarda gerekçe `min_length=10` ile
ŞEMADA doğrulanır, ayrıca serviste tekrar denetlenir — istemci şemayı
atlatabilir.

İKİ YAZMA İZNİ AYRIDIR:
  · `store_promotions.manage`   → kuralın içeriğini düzenler (taslak kalır).
  · `store_promotions.activate` → kuralı YAYINA ALIR / DURDURUR. Yayındaki
    kural her siparişte para indirir; düzenleme izniyle aynı kapıdan
    geçirilseydi taslak yazma yetkisi kampanya başlatma yetkisi olurdu.
  · `store_promotions.coupons`  → kupon üretir/kaldırır.

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran
mesajı gösterir. 4xx yalnız izin/şema kapısından çıkar.
"""

from __future__ import annotations

from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..service import PromotionsService

router = APIRouter()
_service: PromotionsService | None = None


def bind(service: PromotionsService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> PromotionsService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


# ================================================================== okuma

@router.get("/rules")
async def rules(
    q: str = Query("", max_length=120),
    status: str = Query("", max_length=16),
    kind: str = Query("", max_length=24),
    chip: str = Query("", max_length=24),
    channelId: int = Query(0, ge=0),
    groupId: int = Query(0, ge=0),
    page: int = Query(1, ge=1, le=10_000),
    size: int = Query(0, ge=0, le=500),
    user: CurrentUser = requires("store_promotions.view"),
) -> dict[str, Any]:
    return await service().rules(q=q, status=status, kind=kind, chip=chip,
                                 channel_id=channelId, group_id=groupId, page=page, size=size)


@router.get("/rules/{rule_id}")
async def rule(
    rule_id: int,
    user: CurrentUser = requires("store_promotions.view"),
) -> dict[str, Any]:
    return await service().rule(rule_id)


@router.get("/catalog-rules")
async def catalog_rules(
    q: str = Query("", max_length=120),
    status: str = Query("", max_length=16),
    page: int = Query(1, ge=1, le=10_000),
    size: int = Query(0, ge=0, le=200),
    user: CurrentUser = requires("store_promotions.view"),
) -> dict[str, Any]:
    return await service().catalog_rules(q=q, status=status, page=page, size=size)


@router.get("/catalog-rules/{rule_id}")
async def catalog_rule(
    rule_id: int,
    user: CurrentUser = requires("store_promotions.view"),
) -> dict[str, Any]:
    return await service().catalog_rule(rule_id)


@router.get("/rules/{rule_id}/coupons")
async def coupons(
    rule_id: int,
    page: int = Query(1, ge=1, le=10_000),
    size: int = Query(0, ge=0, le=200),
    user: CurrentUser = requires("store_promotions.view"),
) -> dict[str, Any]:
    return await service().coupons(rule_id, page=page, size=size)


@router.get("/reference")
async def reference(
    user: CurrentUser = requires("store_promotions.view"),
) -> dict[str, Any]:
    return await service().reference()


@router.get("/products")
async def products(
    q: str = Query("", max_length=120),
    user: CurrentUser = requires("store_promotions.view"),
) -> dict[str, Any]:
    """Ürün koşulu seçicisi için hafif arama."""
    return await service().products(q)


@router.get("/performance")
async def performance(
    start: str = Query("", max_length=10),
    end: str = Query("", max_length=10),
    user: CurrentUser = requires("store_promotions.view"),
) -> dict[str, Any]:
    return await service().performance(start=start, end=end)


@router.get("/audit")
async def audit(
    ruleId: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    user: CurrentUser = requires("store_promotions.view"),
) -> dict[str, Any]:
    return await service().audit(rule_id=ruleId, limit=limit)


@router.get("/batches")
async def batches(
    ruleId: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    user: CurrentUser = requires("store_promotions.view"),
) -> dict[str, Any]:
    return await service().batches(rule_id=ruleId, limit=limit)


@router.get("/batches/{token}")
async def batch_codes(
    token: str,
    user: CurrentUser = requires("store_promotions.view"),
) -> dict[str, Any]:
    return await service().batch_codes(token)


@router.get("/coupon-preview")
async def coupon_preview(
    prefix: str = Query("", max_length=16),
    length: int = Query(8, ge=1, le=64),
    fmt: str = Query("alphanumeric", max_length=16),
    user: CurrentUser = requires("store_promotions.view"),
) -> dict[str, Any]:
    """Kodun neye benzeyeceği. Mağazaya HİÇ gitmez, kupon üretmez."""
    return service().code_preview(prefix=prefix, length=length, fmt=fmt)


# ============================================================= simülasyon

class CartItem(BaseModel):
    name: str = Field(default="", max_length=120)
    price: int = Field(ge=0, le=100_000_000)          # kuruş
    qty: int = Field(default=1, ge=1, le=1000)
    productId: int = Field(default=0, ge=0)
    categoryIds: list[int] = Field(default_factory=list)


class SimulateBody(BaseModel):
    items: list[CartItem] = Field(default_factory=list)
    shipping: int = Field(default=0, ge=0, le=10_000_000)
    paymentMethod: str = Field(default="", max_length=64)
    customerGroupId: int = Field(default=0, ge=0)
    channelId: int = Field(default=0, ge=0)
    firstOrder: bool = False
    coupon: str = Field(default="", max_length=64)
    #: Kaydedilmemiş kuralın ekrandaki hâli. Verilirse aynı kimlikli kaydın
    #: yerine geçer: "kaydetmeden önce ne olurdu" sorusunun cevabı.
    draft: dict[str, Any] | None = None


@router.post("/simulate")
async def simulate(
    body: SimulateBody,
    user: CurrentUser = requires("store_promotions.view"),
) -> dict[str, Any]:
    """Örnek sepete kuralları uygular. MAĞAZAYA YAZMAZ, sipariş oluşturmaz."""
    cart = {
        "items": [item.model_dump() for item in body.items],
        "shipping": body.shipping,
        "paymentMethod": body.paymentMethod,
        "customerGroupId": body.customerGroupId,
        "channelId": body.channelId,
        "firstOrder": body.firstOrder,
    }
    return await service().simulate(cart=cart, draft=body.draft, coupon=body.coupon)


# ================================================================== yazma

class RuleBody(BaseModel):
    patch: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/rules")
async def create_rule(
    body: RuleBody,
    user: CurrentUser = requires("store_promotions.manage"),
) -> dict[str, Any]:
    """Yeni kural TASLAK açılır; yayına almak `activate` izni ister."""
    return await service().create_rule(patch=body.patch, reason=body.reason,
                                       actor=user.full_name, dry_run=body.dryRun)


@router.put("/rules/{rule_id}")
async def save_rule(
    rule_id: int,
    body: RuleBody,
    user: CurrentUser = requires("store_promotions.manage"),
) -> dict[str, Any]:
    """Kural içeriği. `status` bu uçtan DEĞİŞMEZ — servis onu düşürür."""
    return await service().save_rule(rule_id, patch=body.patch, reason=body.reason,
                                     actor=user.full_name, dry_run=body.dryRun)


class ReasonBody(BaseModel):
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/rules/{rule_id}/copy")
async def copy_rule(
    rule_id: int,
    body: ReasonBody,
    user: CurrentUser = requires("store_promotions.manage"),
) -> dict[str, Any]:
    return await service().copy_rule(rule_id, reason=body.reason, actor=user.full_name,
                                     dry_run=body.dryRun)


class StatusBody(BaseModel):
    active: bool = False
    scope: str = Field(default="cart", max_length=8)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/rules/{rule_id}/status")
async def set_status(
    rule_id: int,
    body: StatusBody,
    user: CurrentUser = requires("store_promotions.activate"),
) -> dict[str, Any]:
    """Yayına alma / durdurma. SİLME UCU YOKTUR: kural durdurulur, silinmez —
    geçmiş siparişler hangi kampanyayla indirildiğini kaybetmesin (ADR 0012)."""
    return await service().set_rule_status(rule_id, active=body.active, scope=body.scope,
                                           reason=body.reason, actor=user.full_name,
                                           dry_run=body.dryRun)


@router.post("/catalog-rules")
async def create_catalog_rule(
    body: RuleBody,
    user: CurrentUser = requires("store_promotions.manage"),
) -> dict[str, Any]:
    return await service().save_catalog_rule(0, patch=body.patch, reason=body.reason,
                                             actor=user.full_name, dry_run=body.dryRun)


@router.put("/catalog-rules/{rule_id}")
async def save_catalog_rule(
    rule_id: int,
    body: RuleBody,
    user: CurrentUser = requires("store_promotions.manage"),
) -> dict[str, Any]:
    return await service().save_catalog_rule(rule_id, patch=body.patch, reason=body.reason,
                                             actor=user.full_name, dry_run=body.dryRun)


# ================================================================== kupon

class GenerateBody(BaseModel):
    prefix: str = Field(default="", max_length=16)
    count: int = Field(ge=1, le=50_000)
    length: int = Field(default=8, ge=4, le=64)
    fmt: str = Field(default="alphanumeric", max_length=16)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/rules/{rule_id}/coupons/generate")
async def generate_coupons(
    rule_id: int,
    body: GenerateBody,
    user: CurrentUser = requires("store_promotions.coupons"),
) -> dict[str, Any]:
    return await service().generate_coupons(rule_id, prefix=body.prefix, count=body.count,
                                            length=body.length, fmt=body.fmt,
                                            reason=body.reason, actor=user.full_name,
                                            dry_run=body.dryRun)


class CouponBody(BaseModel):
    code: str = Field(min_length=3, max_length=64)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/rules/{rule_id}/coupons")
async def add_coupon(
    rule_id: int,
    body: CouponBody,
    user: CurrentUser = requires("store_promotions.coupons"),
) -> dict[str, Any]:
    return await service().add_coupon(rule_id, code=body.code, reason=body.reason,
                                      actor=user.full_name, dry_run=body.dryRun)


class RemoveCouponsBody(BaseModel):
    couponIds: list[int] = Field(default_factory=list)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/rules/{rule_id}/coupons/remove")
async def remove_coupons(
    rule_id: int,
    body: RemoveCouponsBody,
    user: CurrentUser = requires("store_promotions.coupons"),
) -> dict[str, Any]:
    """Yalnız KULLANILMAMIŞ kod kaldırılır; servis kullanılmışı reddeder."""
    return await service().remove_coupons(rule_id, coupon_ids=body.couponIds,
                                          reason=body.reason, actor=user.full_name,
                                          dry_run=body.dryRun)


# ================================================================= rapor

class PreviewBody(BaseModel):
    kind: str = Field(max_length=24)
    start: str = Field(default="", max_length=10)
    end: str = Field(default="", max_length=10)


@router.post("/preview")
async def preview(
    body: PreviewBody,
    user: CurrentUser = requires("store_promotions.view"),
) -> dict[str, Any]:
    return await service().preview(body.kind, {"start": body.start, "end": body.end})


class PrintBody(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    copies: int = Field(default=1, ge=1, le=20)


@router.post("/print")
async def print_report(
    body: PrintBody,
    user: CurrentUser = requires("store_promotions.view"),
) -> dict[str, Any]:
    return await service().print_report(body.path, copies=body.copies)


@router.get("/printer")
async def printer(
    user: CurrentUser = requires("store_promotions.view"),
) -> dict[str, Any]:
    return await service().printer_status()


class ExportBody(BaseModel):
    kind: str = Field(default="rules", max_length=16)
    ruleId: int = Field(default=0, ge=0)


@router.post("/export")
async def export(
    body: ExportBody,
    user: CurrentUser = requires("store_promotions.view"),
) -> dict[str, Any]:
    """Kural ya da kupon listesi CSV'si — rapor klasörüne yazılır. Ekrandaki
    görünen listenin CSV'sini panel kendisi üretir (sunucuya hiç gitmez)."""
    return await service().export_csv(kind=body.kind, rule_id=body.ruleId)
