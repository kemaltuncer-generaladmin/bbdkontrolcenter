"""Setler — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde düğmeyi gizlemek yetkilendirme
değildir. Yıkıcı uçlarda gerekçe `min_length=10` ile ŞEMADA doğrulanır, ayrıca
serviste tekrar denetlenir — istemci şemayı atlatabilir.

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran mesajı
gösterir. 4xx yalnız izin/şema kapısından çıkar.
"""

from __future__ import annotations

from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..bundles import from_kurus
from ..service import BundlesService

router = APIRouter()
_service: BundlesService | None = None


def bind(service: BundlesService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> BundlesService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


# ================================================================== okuma

@router.get("/bundles")
async def bundles(
    user: CurrentUser = requires("store_bundles.view"),
) -> dict[str, Any]:
    """Tüm setler tek yanıtta. Küme küçüktür (kategori 42); süzme ekranda yapılır."""
    return await service().bundles()


@router.get("/bundles/{bundle_id}")
async def bundle(
    bundle_id: int,
    user: CurrentUser = requires("store_bundles.view"),
) -> dict[str, Any]:
    return await service().bundle(bundle_id)


@router.get("/lookup")
async def lookup(
    q: str = Query("", max_length=120),
    limit: int = Query(40, ge=5, le=50),
    user: CurrentUser = requires("store_bundles.view"),
) -> dict[str, Any]:
    """Bileşen seçicisinin ürün araması."""
    return await service().lookup(q=q, limit=limit)


@router.get("/audit")
async def audit(
    bundleId: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    user: CurrentUser = requires("store_bundles.view"),
) -> dict[str, Any]:
    return await service().audit(bundle_id=bundleId, limit=limit)


# ================================================================= hesap

class ComponentBody(BaseModel):
    productId: int = Field(ge=1)
    qty: int = Field(default=1, ge=1, le=999)
    discount: int = Field(default=0, ge=0, le=100)
    required: bool = True


class CalcBody(BaseModel):
    #: YALNIZ TANIM gelir. Birim fiyat ve maliyet sunucuda mağaza verisinden
    #: çözülür; istemciden tutar kabul etmek, ekranda kârlı görünen bir setin
    #: gerçekte zarar etmesine kapı açardı.
    components: list[ComponentBody] = Field(default_factory=list)
    pricingMode: str = Field(default="fixed", max_length=12)
    discountPercent: int = Field(default=0, ge=0, le=100)
    setPrice: int | None = Field(default=None, ge=0)      # kuruş
    taxRate: float | None = Field(default=None, ge=0, le=100)


@router.post("/calc")
async def calc(
    body: CalcBody,
    user: CurrentUser = requires("store_bundles.view"),
) -> dict[str, Any]:
    """Canlı hesap kutusu. MAĞAZAYA YAZMAZ — gerekçe istemez."""
    return await service().calc(
        components=[item.model_dump() for item in body.components],
        pricing_mode=body.pricingMode, discount_percent=body.discountPercent,
        set_price=body.setPrice, tax_rate=body.taxRate)


# ================================================================== yazma

class SaveBody(BaseModel):
    name: str = Field(default="", max_length=180)
    sku: str = Field(default="", max_length=64)
    components: list[ComponentBody] = Field(default_factory=list)
    pricingMode: str = Field(default="fixed", max_length=12)
    discountPercent: int = Field(default=0, ge=0, le=100)
    setPrice: int | None = Field(default=None, ge=0)      # kuruş; boşsa mağaza fiyatı geçerli
    taxRate: float | None = Field(default=None, ge=0, le=100)
    validFrom: str = Field(default="", max_length=10)
    validTo: str = Field(default="", max_length=10)
    note: str = Field(default="", max_length=500)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.put("/bundles/{bundle_id}")
async def save(
    bundle_id: int,
    body: SaveBody,
    user: CurrentUser = requires("store_bundles.manage"),
) -> dict[str, Any]:
    payload = body.model_dump()
    payload["components"] = [item.model_dump() for item in body.components]
    # Telde kuruş (int) gelir; künye çözücüsü ondalık metin okur — çevrim tek
    # yerde ve `float` kullanmadan yapılır.
    payload["setPrice"] = None if body.setPrice is None else from_kurus(body.setPrice)
    return await service().save(bundle_id, payload=payload, reason=body.reason,
                                actor=user.full_name, dry_run=body.dryRun)


class StatusBody(BaseModel):
    active: bool = False
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/bundles/{bundle_id}/status")
async def set_status(
    bundle_id: int,
    body: StatusBody,
    user: CurrentUser = requires("store_bundles.deactivate"),
) -> dict[str, Any]:
    """Vitrinden kaldırma/geri alma. SİLME UCU YOKTUR (ADR 0012)."""
    return await service().set_status(bundle_id, active=body.active, reason=body.reason,
                                      actor=user.full_name, dry_run=body.dryRun)


# ================================================================= rapor

class PreviewBody(BaseModel):
    kind: str = Field(max_length=24)


@router.post("/preview")
async def preview(
    body: PreviewBody,
    user: CurrentUser = requires("store_bundles.view"),
) -> dict[str, Any]:
    return await service().preview(body.kind, {})


class PrintBody(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    copies: int = Field(default=1, ge=1, le=20)


@router.post("/print")
async def print_report(
    body: PrintBody,
    user: CurrentUser = requires("store_bundles.view"),
) -> dict[str, Any]:
    return await service().print_report(body.path, copies=body.copies)


@router.get("/printer")
async def printer(
    user: CurrentUser = requires("store_bundles.view"),
) -> dict[str, Any]:
    return await service().printer_status()


@router.post("/export")
async def export(
    user: CurrentUser = requires("store_bundles.view"),
) -> dict[str, Any]:
    """Set listesi CSV'si — rapor klasörüne yazılır. Görünen listenin CSV'sini
    panel kendisi üretir (sunucuya hiç gitmez)."""
    return await service().export_csv()
