"""Ana Ekran Görselleri — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde düğmeyi gizlemek yetkilendirme
değildir. Yazma uçlarında gerekçe `min_length=10` ile ŞEMADA doğrulanır, ayrıca
serviste tekrar denetlenir — istemci şemayı atlatabilir.

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran
mesajı gösterir. 4xx yalnız izin/şema kapısından çıkar.

YÜZEY 18.08.2026'DA DARALDI. Şu uçlar KALDIRILDI: `/slots*` (dört sekmelik
slot düzenleyicisi), `/reorder` (sıra artık liste yazmasının içinde),
`/reference` (kanal/dil/kategori/CMS listeleri — hedef seçici tek adres
kutusuna indi), `/preview` · `/print` · `/printer` · `/export` (yerleşim
raporu ve CSV). Hiçbiri "siteye ilk girişteki görselleri değiştir" işine
hizmet etmiyordu.
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

@router.get("/slides")
async def slides(
    user: CurrentUser = requires("store_home_media.view"),
) -> dict[str, Any]:
    """Ana ekranda dönen görseller — SIRALI. Ekranın tek okuma isteği budur."""
    return await service().slides()


@router.get("/link-search")
async def link_search(
    q: str = Query("", max_length=120),
    user: CurrentUser = requires("store_home_media.manage"),
) -> dict[str, Any]:
    """Hedef seçicisi için ürün arama; bulunan ürünün adresini kutuya yazar."""
    return await service().link_search(q=q)


@router.get("/audit")
async def audit(
    limit: int = Query(50, ge=1, le=500),
    user: CurrentUser = requires("store_home_media.view"),
) -> dict[str, Any]:
    return await service().audit(limit=limit)


# =========================================================== görsel denetim

class ImageBody(BaseModel):
    #: `data:image/png;base64,…` ya da çıplak base64. Tauri'de dosya sistemi
    #: eklentisi yok; görsel tarayıcıda FileReader ile okunup gövdeyle taşınır.
    data: str = Field(min_length=8, max_length=30_000_000)


@router.post("/image/check")
async def image_check(
    body: ImageBody,
    user: CurrentUser = requires("store_home_media.manage"),
) -> dict[str, Any]:
    """Ölçü/oran/tür denetimi. YAZMAZ — gerekçe istemez, mağazaya gitmez."""
    return service().check_image(data=body.data)


class UploadBody(BaseModel):
    data: str = Field(min_length=8, max_length=30_000_000)
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
    """Görseli mağazaya yükler ve saklanan yolu döndürür.

    Yol, `PUT /slides` gövdesinde o slaydın `image` alanına konur. Mağaza
    serbest yol kabul etmiyor: yalnız kendi yüklediği klasördeki dosya yazılır.
    """
    return await service().upload_image(
        data=body.data, filename=body.filename, acknowledged=body.acknowledged,
        reason=body.reason, actor=user.full_name, dry_run=body.dryRun)


# ================================================================== yazma

class SlideBody(BaseModel):
    title: str = Field(default="", max_length=160)
    link: str = Field(default="", max_length=400)
    #: Mağazanın döndürdüğü yol (`storage/theme/{id}/sliders/…`). Panel bunu
    #: uydurmaz: ya listeden gelir ya `POST /image/upload` yanıtından.
    image: str = Field(default="", max_length=500)


class SlidesBody(BaseModel):
    #: TAM LİSTE, YENİ SIRAYLA. Kısmi gövde kabul edilmez — sıra dizinin kendi
    #: sırası olduğu için "yalnız 3. satırı güncelle" diye bir şey yok.
    slides: list[SlideBody] = Field(default_factory=list)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.put("/slides")
async def save_slides(
    body: SlidesBody,
    user: CurrentUser = requires("store_home_media.manage"),
) -> dict[str, Any]:
    """Listeyi (sıra + hedef + ad) tek seferde yazar. SİLME UCU YOKTUR."""
    return await service().save_slides(
        slides=[slide.model_dump() for slide in body.slides],
        reason=body.reason, actor=user.full_name, dry_run=body.dryRun)
