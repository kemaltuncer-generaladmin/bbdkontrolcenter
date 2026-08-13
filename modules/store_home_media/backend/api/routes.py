"""Ana Ekran Görselleri — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde düğmeyi gizlemek yetkilendirme
değildir. Yıkıcı uçlarda gerekçe `min_length=10` ile ŞEMADA doğrulanır, ayrıca
serviste tekrar denetlenir — istemci şemayı atlatabilir.

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran
mesajı gösterir. 4xx yalnız izin/şema kapısından çıkar.
"""

from __future__ import annotations

from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..service import HomeMediaService

router = APIRouter()
_service: HomeMediaService | None = None


def bind(service: HomeMediaService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> HomeMediaService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


# ================================================================== okuma

@router.get("/slots")
async def slots(
    q: str = Query("", max_length=120),
    area: str = Query("", max_length=24),
    status: str = Query("", max_length=16),
    device: str = Query("", max_length=16),
    channel: str = Query("", max_length=32),
    placement: str = Query("", max_length=16),
    start: str = Query("", max_length=10),
    end: str = Query("", max_length=10),
    user: CurrentUser = requires("store_home_media.view"),
) -> dict[str, Any]:
    """Slot listesi + ana sayfa önizlemesi tek yanıtta.

    Önizleme SÜZGEÇTEN ETKİLENMEZ: ana sayfanın temsilidir, filtre koyunca
    vitrin boşalmış gibi görünmemeli.
    """
    return await service().overview(q=q, area=area, status=status, device=device,
                                    channel=channel, placement=placement, start=start, end=end)


@router.get("/reference")
async def reference(
    user: CurrentUser = requires("store_home_media.view"),
) -> dict[str, Any]:
    return await service().reference()


@router.get("/link-search")
async def link_search(
    q: str = Query("", max_length=120),
    user: CurrentUser = requires("store_home_media.manage"),
) -> dict[str, Any]:
    """Hedef bağlantı seçicisi için ürün arama."""
    return await service().link_search(q=q)


@router.get("/audit")
async def audit(
    slotId: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    user: CurrentUser = requires("store_home_media.view"),
) -> dict[str, Any]:
    return await service().audit(slot_id=slotId, limit=limit)


# =========================================================== görsel denetim

class ImageBody(BaseModel):
    #: `data:image/png;base64,…` ya da çıplak base64. Tauri'de dosya sistemi
    #: eklentisi yok; görsel tarayıcıda FileReader ile okunup gövdeyle taşınır.
    data: str = Field(min_length=8, max_length=30_000_000)
    area: str = Field(default="banner", max_length=24)


@router.post("/image/check")
async def image_check(
    body: ImageBody,
    user: CurrentUser = requires("store_home_media.manage"),
) -> dict[str, Any]:
    """Ölçü/oran/tür denetimi. YAZMAZ — gerekçe istemez, mağazaya gitmez."""
    return service().check_image(area=body.area, data=body.data)


class UploadBody(BaseModel):
    data: str = Field(min_length=8, max_length=30_000_000)
    area: str = Field(default="banner", max_length=24)
    #: Görselin bağlanacağı slot. 0 = henüz kaydedilmemiş yeni slot; yükleme
    #: yine denetim izine geçer, yalnız kimliği boş kalır.
    slotId: int = Field(default=0, ge=0)
    filename: str = Field(default="", max_length=180)
    #: Ölçü/oran uyarısı GÖRÜLDÜ ve kabul edildi. Bayrak yoksa servis yüklemeyi
    #: reddeder ve uyarıyı geri gönderir — panelde onay göstermek yetkilendirme
    #: değildir (K9).
    acknowledged: bool = False
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/image/upload")
async def image_upload(
    body: UploadBody,
    user: CurrentUser = requires("store_home_media.manage"),
) -> dict[str, Any]:
    """Görseli mağazanın yükleme ucuna gönderir (geçit üzerinden, multipart).

    Uç henüz yayında değilse yanıt `{"ok": False, "pending": True}` olur; bu
    bir HATA DEĞİLDİR ve ekran onu "uç hazır olunca açılacak" diye anlatır (K7).
    """
    return await service().upload_image(
        data=body.data, area=body.area, slot_id=body.slotId, filename=body.filename,
        acknowledged=body.acknowledged, reason=body.reason, actor=user.full_name,
        dry_run=body.dryRun)


# ================================================================== yazma

class SaveBody(BaseModel):
    patch: dict[str, Any] = Field(default_factory=dict)
    image: str = Field(default="", max_length=30_000_000)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/slots")
async def create(
    body: SaveBody,
    user: CurrentUser = requires("store_home_media.manage"),
) -> dict[str, Any]:
    return await service().save(None, patch=body.patch, image=body.image, reason=body.reason,
                                actor=user.full_name, dry_run=body.dryRun)


@router.put("/slots/{slot_id}")
async def save(
    slot_id: int,
    body: SaveBody,
    user: CurrentUser = requires("store_home_media.manage"),
) -> dict[str, Any]:
    return await service().save(slot_id, patch=body.patch, image=body.image, reason=body.reason,
                                actor=user.full_name, dry_run=body.dryRun)


class StatusBody(BaseModel):
    published: bool = False
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/slots/{slot_id}/status")
async def set_status(
    slot_id: int,
    body: StatusBody,
    user: CurrentUser = requires("store_home_media.publish"),
) -> dict[str, Any]:
    """Yayına al / yayından kaldır. SİLME UCU YOKTUR (ADR 0012)."""
    return await service().set_status(slot_id, published=body.published, reason=body.reason,
                                      actor=user.full_name, dry_run=body.dryRun)


class ReorderBody(BaseModel):
    area: str = Field(max_length=24)
    #: Yalnız o şeridin kimlikleri, YENİ sırayla. Servis bunu global sıraya
    #: oturtur; eksik/fazla kimlik reddedilir.
    order: list[int] = Field(default_factory=list)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/reorder")
async def reorder(
    body: ReorderBody,
    user: CurrentUser = requires("store_home_media.manage"),
) -> dict[str, Any]:
    return await service().reorder(area=body.area, order=body.order, reason=body.reason,
                                   actor=user.full_name, dry_run=body.dryRun)


# ================================================================= rapor

class PreviewBody(BaseModel):
    kind: str = Field(default="layout", max_length=24)
    area: str = Field(default="", max_length=24)


@router.post("/preview")
async def preview(
    body: PreviewBody,
    user: CurrentUser = requires("store_home_media.view"),
) -> dict[str, Any]:
    return await service().preview(body.kind, {"area": body.area})


class PrintBody(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    copies: int = Field(default=1, ge=1, le=20)


@router.post("/print")
async def print_report(
    body: PrintBody,
    user: CurrentUser = requires("store_home_media.view"),
) -> dict[str, Any]:
    return await service().print_report(body.path, copies=body.copies)


@router.get("/printer")
async def printer(
    user: CurrentUser = requires("store_home_media.view"),
) -> dict[str, Any]:
    return await service().printer_status()


@router.post("/export")
async def export(
    user: CurrentUser = requires("store_home_media.view"),
) -> dict[str, Any]:
    """TÜM slotların CSV'si — rapor klasörüne yazılır."""
    return await service().export_csv()
