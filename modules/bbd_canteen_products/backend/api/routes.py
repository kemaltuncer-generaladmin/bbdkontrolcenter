"""Kantin Ürünleri — HTTP yüzeyi.

ÇİFT KAPI (K9): router `view` ister, yazan uçlar `manage`, pasifleştirme
`deactivate`. Arayüzde düğmeyi gizlemek yetkilendirme değildir.
"""

from __future__ import annotations

from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, requires

from ..service import ProductService

router = APIRouter()

_service: ProductService | None = None


def bind(service: ProductService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> ProductService:
    if _service is None:  # pragma: no cover - yükleme sırası garanti eder
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


class ProductBody(BaseModel):
    """Ürün ekleme/düzenleme. Kantin ucu upsert olduğu için `id` yoksa yeni açılır."""

    id: int | None = Field(default=None, ge=1)
    barcode: str | None = Field(default=None, max_length=64)
    name: str | None = Field(default=None, max_length=120)
    price: int | None = Field(default=None, ge=1, le=100_000_000)
    stock: int | None = Field(default=None, ge=0, le=1_000_000)
    isActive: bool | None = None
    note: str = Field(default="", max_length=255)
    # Görsel base64 olarak gelir; modül onu kantine multipart aktarır.
    imageBase64: str | None = Field(default=None, max_length=34_000_000)
    imageName: str | None = Field(default=None, max_length=180)


class ActiveBody(BaseModel):
    isActive: bool
    note: str = Field(default="", max_length=255)


class StockBody(BaseModel):
    delta: int = Field(ge=-1_000_000, le=1_000_000)
    reason: str = Field(min_length=2, max_length=255)


class BulkPriceBody(BaseModel):
    products: list[int] = Field(min_length=1, max_length=2000)
    percent: float | None = Field(default=None, ge=-90, le=500)
    amount: int | None = Field(default=None, ge=-10_000_000, le=10_000_000)
    dryRun: bool = True


@router.get("/products")
async def list_products(
    user: CurrentUser = requires("bbd_canteen_products.view"),
) -> dict[str, Any]:
    """Kantindeki ürünler (pasifler dahil) + özet + sağlık denetimi."""
    return await service().list_products()


@router.get("/audit")
async def audit(
    productId: int | None = None,
    limit: int = 200,
    user: CurrentUser = requires("bbd_canteen_products.view"),
) -> dict[str, Any]:
    """Değişiklik günlüğü — kim, ne zaman, neyi neye çevirdi."""
    return {"entries": await service().audit_log(product_id=productId, limit=min(limit, 1000))}


@router.post("/products")
async def save_product(
    body: ProductBody,
    user: CurrentUser = requires("bbd_canteen_products.manage"),
) -> dict[str, Any]:
    """Ürünü ekler/günceller; görsel varsa kantine multipart olarak aktarır."""
    return await service().save(body.model_dump(exclude_unset=True), actor=user.full_name)


@router.put("/products/{product_id}/active")
async def set_active(
    product_id: int,
    body: ActiveBody,
    user: CurrentUser = requires("bbd_canteen_products.deactivate"),
) -> dict[str, Any]:
    """Pasifleştirme/geri açma. SİLME DEĞİLDİR — geçmiş satışlar korunur."""
    return await service().set_active(product_id, body.isActive,
                                      actor=user.full_name, note=body.note)


@router.post("/products/{product_id}/stock")
async def adjust_stock(
    product_id: int,
    body: StockBody,
    user: CurrentUser = requires("bbd_canteen_products.manage"),
) -> dict[str, Any]:
    return await service().adjust_stock(product_id, body.delta, body.reason,
                                        actor=user.full_name)


@router.post("/bulk-price")
async def bulk_price(
    body: BulkPriceBody,
    user: CurrentUser = requires("bbd_canteen_products.manage"),
) -> dict[str, Any]:
    """Toplu zam/indirim. `dryRun` varsayılan AÇIK — önce ne olacağı gösterilir."""
    return await service().bulk_price(body.products, percent=body.percent,
                                      amount=body.amount, actor=user.full_name,
                                      dry_run=body.dryRun)
