"""Toplu Satış — HTTP yüzeyi.

ÇİFT KAPI (K9): router `view` ister, işleme `manage`, geri alma `reverse`.
"""

from __future__ import annotations

from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, requires

from ..service import BulkSaleService

router = APIRouter()

_service: BulkSaleService | None = None


def bind(service: BulkSaleService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> BulkSaleService:
    if _service is None:  # pragma: no cover - yükleme sırası garanti eder
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


class CartLine(BaseModel):
    productId: int = Field(ge=1)
    qty: int = Field(ge=1, le=1000)
    # Boş bırakılırsa kantindeki güncel fiyat kullanılır.
    unitPrice: int | None = Field(default=None, ge=1, le=100_000_000)


class StudentOrder(BaseModel):
    """Öğrenci başına ayrı sepet kipinde tek kuyruk satırı."""

    kantinId: str = Field(max_length=64)
    items: list[CartLine] = Field(default_factory=list, max_length=100)


class SaleBody(BaseModel):
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    mode: str = Field(default="shared", pattern=r"^(shared|per_student)$")
    # shared kipi
    students: list[str] = Field(default_factory=list, max_length=2000)
    cart: list[CartLine] = Field(default_factory=list, max_length=100)
    # per_student kipi
    orders: list[StudentOrder] = Field(default_factory=list, max_length=2000)
    note: str = Field(default="", max_length=300)
    dryRun: bool = False


class ReverseBody(BaseModel):
    batchRef: str | None = Field(default=None, max_length=64)
    localId: str | None = Field(default=None, max_length=64)
    reason: str = Field(min_length=3, max_length=255)


class PresetBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    cart: list[CartLine] = Field(min_length=1, max_length=100)


@router.get("/workspace")
async def workspace(
    user: CurrentUser = requires("bbd_bulk_sale.view"),
) -> dict[str, Any]:
    """Açılış verisi: öğrenciler, aktif ürünler, sepet şablonları, son partiler."""
    return await service().workspace()


@router.get("/batches/{batch_ref}")
async def batch_detail(
    batch_ref: str,
    user: CurrentUser = requires("bbd_bulk_sale.view"),
) -> dict[str, Any]:
    return await service().batch_detail(batch_ref)


@router.post("/preview")
async def preview(
    body: SaleBody,
    user: CurrentUser = requires("bbd_bulk_sale.view"),
) -> dict[str, Any]:
    """Gönderim yapmadan sonucu gösterir — engel, limit, stok ve tutar."""
    return await service().preview(body.model_dump())


@router.post("/commit")
async def commit(
    body: SaleBody,
    user: CurrentUser = requires("bbd_bulk_sale.manage"),
) -> dict[str, Any]:
    """Satışları kantine işler. Kayıt kasada elle girilmişten ayırt edilemez."""
    return await service().commit(body.model_dump(), actor=user.full_name)


@router.post("/reverse")
async def reverse(
    body: ReverseBody,
    user: CurrentUser = requires("bbd_bulk_sale.reverse"),
) -> dict[str, Any]:
    """Geri alma. Kantinde ters cari kayıt + stok iadesi; satır SİLİNMEZ."""
    return await service().reverse(batch_ref=body.batchRef, local_id=body.localId,
                                   reason=body.reason)


@router.put("/presets")
async def save_preset(
    body: PresetBody,
    user: CurrentUser = requires("bbd_bulk_sale.manage"),
) -> dict[str, Any]:
    return await service().save_preset(body.name, [line.model_dump() for line in body.cart],
                                       actor=user.full_name)


@router.delete("/presets/{preset_id}")
async def delete_preset(
    preset_id: int,
    user: CurrentUser = requires("bbd_bulk_sale.manage"),
) -> dict[str, Any]:
    return await service().delete_preset(preset_id)
