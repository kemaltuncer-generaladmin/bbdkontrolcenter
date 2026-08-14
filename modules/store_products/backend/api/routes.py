"""Ürünler — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde düğmeyi gizlemek yetkilendirme
değildir. Yıkıcı uçlarda gerekçe `min_length=10` ile ŞEMADA doğrulanır, ayrıca
serviste tekrar denetlenir — istemci şemayı atlatabilir.

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran
mesajı gösterir. 4xx yalnız izin/şema kapısından çıkar.
"""

from __future__ import annotations

from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..service import ProductsService

router = APIRouter()
_service: ProductsService | None = None


def bind(service: ProductsService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> ProductsService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


# ================================================================== okuma

@router.get("/products")
async def products(
    q: str = Query("", max_length=120),
    chip: str = Query("", max_length=24),
    status: str = Query("", max_length=8),
    kind: str = Query("", max_length=24),
    family: str = Query("", max_length=24),
    categoryId: int = Query(0, ge=0),
    priceMin: int | None = Query(None, ge=0),
    priceMax: int | None = Query(None, ge=0),
    sort: str = Query("", max_length=32),
    order: str = Query("desc", max_length=4),
    page: int = Query(1, ge=1, le=10_000),
    size: int = Query(0, ge=0, le=200),
    user: CurrentUser = requires("store_products.view"),
) -> dict[str, Any]:
    return await service().products(q=q, chip=chip, status=status, kind=kind, family=family,
                                    category_id=categoryId, price_min=priceMin,
                                    price_max=priceMax, sort=sort, order=order,
                                    page=page, size=size)


@router.get("/products/{product_id}")
async def product(
    product_id: int,
    user: CurrentUser = requires("store_products.view"),
) -> dict[str, Any]:
    return await service().card(product_id)


@router.get("/reference")
async def reference(
    user: CurrentUser = requires("store_products.view"),
) -> dict[str, Any]:
    return await service().reference()


@router.get("/health")
async def health(
    user: CurrentUser = requires("store_products.view"),
) -> dict[str, Any]:
    return await service().health()


@router.get("/audit")
async def audit(
    productId: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    user: CurrentUser = requires("store_products.view"),
) -> dict[str, Any]:
    return await service().audit(product_id=productId, limit=limit)


@router.get("/url-key")
async def url_key(
    value: str = Query("", max_length=180),
    productId: int = Query(0, ge=0),
    user: CurrentUser = requires("store_products.manage"),
) -> dict[str, Any]:
    return await service().check_url_key(url_key=value, product_id=productId)


# ================================================================== yazma

class SaveBody(BaseModel):
    patch: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.put("/products/{product_id}")
async def save(
    product_id: int,
    body: SaveBody,
    user: CurrentUser = requires("store_products.manage"),
) -> dict[str, Any]:
    return await service().save(product_id, patch=body.patch, reason=body.reason,
                                actor=user.full_name, dry_run=body.dryRun)


class DraftBody(BaseModel):
    """Ürün açma taslağı. YAZMAZ — gerekçe istemez.

    Alanların hepsi İSTEĞE BAĞLI: kullanıcı ne yazdıysa o gelir, boş kalanı
    servis türetir ve neyi türettiğini `auto`/`notes` ile geri söyler.
    """

    sku: str = Field(default="", max_length=64)
    type: str = Field(default="simple", max_length=24)
    name: str = Field(default="", max_length=255)
    urlKey: str = Field(default="", max_length=180)
    metaTitle: str = Field(default="", max_length=255)
    metaDescription: str = Field(default="", max_length=500)
    shortDescription: str = Field(default="", max_length=2000)
    description: str = Field(default="", max_length=8000)
    categoryIds: list[int] = Field(default_factory=list)
    #: Seçilen kategorinin ÜST kategorileri ağaca göre eklensin mi?
    #: Panel `false` yollar: liste taslakta genişletildi ve kullanıcı gördü;
    #: ikinci kez genişletmek, listeden çıkardığı üstü geri koyardı.
    expandParents: bool = True
    #: Kuruş. `None` = "girilmedi" — 0 ile aynı şey DEĞİL: 0 gerçek bir fiyattır.
    price: int | None = Field(default=None, ge=0)
    #: `None` = "girilmedi" → depoya 0 yazılır, ürün "stokta yok" doğar.
    stock: int | None = Field(default=None, ge=0, le=9_999_999)
    sourceId: int = Field(default=0, ge=0)
    #: 0 = "seçilmedi". Mağazada tek vergi kategorisi varsa ekranda alan YOKTUR
    #: ve servis onu kendisi uygular; ikinci kategori açılırsa alan geri gelir
    #: ve seçilen değer buradan taşınır.
    taxCategoryId: int = Field(default=0, ge=0)
    #: `None` = "seçilmedi" → yeni ürün PASİF doğar.
    status: bool | None = None
    attributeFamilyId: int = Field(default=0, ge=0)

    def draft(self) -> dict[str, Any]:
        return self.model_dump(exclude={"reason", "dryRun"}, exclude_none=False)


@router.post("/products/plan")
async def plan(
    body: DraftBody,
    user: CurrentUser = requires("store_products.manage"),
) -> dict[str, Any]:
    """Ne yazılacağını ÖNCEDEN gösterir: url_key, üst kategoriler, SEO, stok.

    Yazma yapmadığı için gerekçe istemez; yine de `manage` ister — taslak
    mağaza verisini (ağaç, aileler, çakışan url_key'ler) okur.
    """
    return await service().plan(payload=body.draft())


class CreateBody(DraftBody):
    #: SKU ürün açmanın TEK zorunlu alanıdır; gerisi türetilebiliyor.
    sku: str = Field(min_length=1, max_length=64)
    #: Öznitelik ailesi İSTEĞE BAĞLI. Tek satıcılı, tek ürün tipli bir mağazada
    #: her ürün aynı aileye gider; kullanıcıya sormak anlamsız bir seçim üretir.
    #: 0 gelirse servis varsayılan aileyi kendisi çözer (`_default_family`).
    #: Zorunlu olduğu sürece panelden ürün AÇILAMIYORDU: arayüzde bu alanı soran
    #: hiçbir girdi yok, dolayısıyla her istek 422 ile geri dönerdi.
    attributeFamilyId: int = Field(default=0, ge=0)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/products")
async def create(
    body: CreateBody,
    user: CurrentUser = requires("store_products.manage"),
) -> dict[str, Any]:
    """Ürünü açar VE doldurur. Taslak burada yeniden hesaplanır (K9).

    Panel `plan` ucundan gelen değerleri kullanıcıya gösterip onaylatır ama
    kapı burasıdır: istek elle de kurulabilir ve onayla yazma arasında geçen
    sürede url_key kapılmış olabilir.
    """
    return await service().create(payload=body.draft(), reason=body.reason,
                                  actor=user.full_name, dry_run=body.dryRun)


class ReasonBody(BaseModel):
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/products/{product_id}/copy")
async def copy(
    product_id: int,
    body: ReasonBody,
    user: CurrentUser = requires("store_products.manage"),
) -> dict[str, Any]:
    return await service().copy(product_id, reason=body.reason, actor=user.full_name,
                                dry_run=body.dryRun)


class StockBody(BaseModel):
    #: {envanterKaynakId: adet} — MUTLAK değer, fark değil.
    quantities: dict[str, int] = Field(default_factory=dict)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/products/{product_id}/stock")
async def stock(
    product_id: int,
    body: StockBody,
    user: CurrentUser = requires("store_products.manage"),
) -> dict[str, Any]:
    return await service().set_stock(product_id, quantities=body.quantities, reason=body.reason,
                                     actor=user.full_name, dry_run=body.dryRun)


class CategoriesBody(BaseModel):
    #: TAM liste. Bagisto kısmi kabul etmiyor: gönderilmeyen kategori düşer.
    categoryIds: list[int] = Field(default_factory=list)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/products/{product_id}/categories")
async def set_categories(
    product_id: int,
    body: CategoriesBody,
    user: CurrentUser = requires("store_products.manage"),
) -> dict[str, Any]:
    return await service().set_categories(product_id, category_ids=body.categoryIds,
                                          reason=body.reason, actor=user.full_name,
                                          dry_run=body.dryRun)


class GroupPriceBody(BaseModel):
    groupId: int = Field(ge=1)
    qty: int = Field(default=1, ge=1, le=100_000)
    value: int = Field(ge=0)              # kuruş
    priceId: int | None = None
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/products/{product_id}/group-price")
async def group_price(
    product_id: int,
    body: GroupPriceBody,
    user: CurrentUser = requires("store_products.manage"),
) -> dict[str, Any]:
    return await service().save_group_price(product_id, group_id=body.groupId, qty=body.qty,
                                            value=body.value, price_id=body.priceId,
                                            reason=body.reason, actor=user.full_name,
                                            dry_run=body.dryRun)


@router.get("/products/{product_id}/images")
async def images(
    product_id: int,
    user: CurrentUser = requires("store_products.view"),
) -> dict[str, Any]:
    """Görsel listesi + yükleme kuralları. Yükleme sonrası tazeleme içindir."""
    return await service().images(product_id)


class ImageUploadBody(BaseModel):
    """TEK dosya. Çoklu seçimde panel bunu sırayla çağırır.

    `content` base64'tür (Tauri kabuğunda fs eklentisi yok; panel dosyayı
    `FileReader.readAsDataURL` ile okuyor). `data:` öneki kabul edilir.
    Uzunluk sınırı 34 MB: 24 MB'lık ayar tavanının base64 karşılığı ~32 MB;
    gövdeyi bundan büyük kabul etmek belleği boşa doldurur.
    """

    filename: str = Field(min_length=1, max_length=200)
    mime: str = Field(default="", max_length=100)
    content: str = Field(min_length=8, max_length=34_000_000)
    position: int | None = Field(default=None, ge=0, le=999)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/products/{product_id}/images")
async def upload_image(
    product_id: int,
    body: ImageUploadBody,
    user: CurrentUser = requires("store_products.manage"),
) -> dict[str, Any]:
    return await service().upload_image(product_id, filename=body.filename, mime=body.mime,
                                        content=body.content, position=body.position,
                                        reason=body.reason, actor=user.full_name,
                                        dry_run=body.dryRun)


class ImageOrderBody(BaseModel):
    order: list[int] = Field(default_factory=list)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/products/{product_id}/images/reorder")
async def reorder_images(
    product_id: int,
    body: ImageOrderBody,
    user: CurrentUser = requires("store_products.manage"),
) -> dict[str, Any]:
    return await service().reorder_images(product_id, order=body.order, reason=body.reason,
                                          actor=user.full_name, dry_run=body.dryRun)


@router.post("/products/{product_id}/images/{image_id}/remove")
async def remove_image(
    product_id: int,
    image_id: int,
    body: ReasonBody,
    user: CurrentUser = requires("store_products.manage"),
) -> dict[str, Any]:
    return await service().remove_image(product_id, image_id, reason=body.reason,
                                        actor=user.full_name, dry_run=body.dryRun)


# --------------------------------------------------------- yıkıcı uçlar

class StatusBody(BaseModel):
    productIds: list[int] = Field(default_factory=list)
    active: bool = False
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/products/status")
async def set_status(
    body: StatusBody,
    user: CurrentUser = requires("store_products.deactivate"),
) -> dict[str, Any]:
    """Aktif/Pasif — geri alınabilir. Silmek AYRI uç ve AYRI izindir."""
    return await service().set_status(body.productIds, active=body.active, reason=body.reason,
                                      actor=user.full_name, dry_run=body.dryRun)


class DeletePreviewBody(BaseModel):
    """Önizleme YAZMAZ; gerekçe istemez.

    Yine de `store_products.delete` ister: "bu ürün kaç siparişte geçti"
    sorusunun cevabı silme kararının parçasıdır ve o kararı vermeyecek
    kullanıcıya gösterilmesi gerekmiyor.
    """

    productIds: list[int] = Field(default_factory=list, max_length=200)


@router.post("/products/delete/preview")
async def delete_preview(
    body: DeletePreviewBody,
    user: CurrentUser = requires("store_products.delete"),
) -> dict[str, Any]:
    """Ne silineceğini gösterir: künye, stok, satış geçmişi ve uyarılar."""
    return await service().delete_preview(body.productIds)


class DeleteBody(BaseModel):
    productIds: list[int] = Field(default_factory=list, max_length=200)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/products/delete")
async def delete_products(
    body: DeleteBody,
    user: CurrentUser = requires("store_products.delete"),
) -> dict[str, Any]:
    """ÜRÜNÜ GERÇEKTEN SİLER — pasifleştirme değil, geri alınamaz.

    `store_products.deactivate` YETMEZ: pasifleştirme geri alınabilir bir
    işlem, silme değil. İkisini tek anahtarla korumak, vitrinden ürün
    kaldırma yetkisi verilen personele kataloğu silme yetkisi de vermek
    olurdu. Gerekçe hem şemada hem serviste doğrulanır (K9).
    """
    return await service().delete_products(body.productIds, reason=body.reason,
                                           actor=user.full_name, dry_run=body.dryRun)


class OrderItemsBody(BaseModel):
    """Sipariş kalemleri — YALNIZ okunur, hiçbir şey yazılmaz.

    Alanlar serbest sözlüktür: çağıran ekran kalemi mağazadan nasıl aldıysa
    öyle yollar (`product_id`/`productId`, `name`, `sku`). Kural hangi adın
    geldiğini kendisi çözer (`deleted.py`).
    """

    items: list[dict[str, Any]] = Field(default_factory=list, max_length=500)


@router.post("/order-items/mark")
async def mark_order_items(
    body: OrderItemsBody,
    user: CurrentUser = requires("store_products.view"),
) -> dict[str, Any]:
    """Silinmiş üründen gelen kalemleri kırmızı “silinmiş” ile işaretler.

    Kuralın kendisi `backend/deleted.py` içinde SAF fonksiyondur ve
    `store_products.deleted_marker` yeteneğiyle ilan edilir; rapor ve sipariş
    ekranları kuralı registry'den alır, kopyalamaz (K3). Bu uç yalnız kuralın
    ihtiyaç duyduğu katalog çözümünü geçitten yapar.
    """
    return await service().mark_order_items(body.items)


class SkuBody(BaseModel):
    sku: str = Field(min_length=2, max_length=64)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/products/{product_id}/sku")
async def change_sku(
    product_id: int,
    body: SkuBody,
    user: CurrentUser = requires("store_products.rename_sku"),
) -> dict[str, Any]:
    """SKU değişikliği `product_flat`'ı yeniden yazar ve eski URL'leri kırar."""
    return await service().change_sku(product_id, sku=body.sku, reason=body.reason,
                                      actor=user.full_name, dry_run=body.dryRun)


# ============================================================ toplu işlem

class BulkPreviewBody(BaseModel):
    kind: str = Field(max_length=16)
    productIds: list[int] = Field(default_factory=list)
    mode: str = Field(default="", max_length=16)
    amount: int = 0
    rounding: str = Field(default="none", max_length=12)
    categoryId: int = 0
    active: bool = True


@router.post("/bulk/preview")
async def bulk_preview(
    body: BulkPreviewBody,
    user: CurrentUser = requires("store_products.bulk"),
) -> dict[str, Any]:
    """Fark tablosu. Yazmaz — gerekçe istemez."""
    return await service().bulk_preview(kind=body.kind, product_ids=body.productIds,
                                        mode=body.mode, amount=body.amount,
                                        rounding=body.rounding, category_id=body.categoryId,
                                        active=body.active)


class BulkApplyBody(BaseModel):
    token: str = Field(min_length=8, max_length=64)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/bulk/apply")
async def bulk_apply(
    body: BulkApplyBody,
    user: CurrentUser = requires("store_products.bulk"),
) -> dict[str, Any]:
    """Toplu pasifleştirme AYRICA `store_products.deactivate` ister.

    `requires(...)` "en az biri" anlamına geldiği için iki izni burada
    birleştiremeyiz; yıkıcı iznin varlığı servise taşınır ve orada denetlenir.
    Aksi hâlde toplu yol, tek ürün için ayrı tutulan izni atlatan bir arka
    kapı olurdu (K9).
    """
    return await service().bulk_apply(token=body.token, reason=body.reason,
                                      actor=user.full_name, dry_run=body.dryRun,
                                      may_deactivate=user.has_permission(
                                          "store_products.deactivate"))


# ========================================================= nitelik · aile
#
# Nitelik KATALOĞUN ŞEMASIDIR. Ürün düzenlemekten daha ağır bir yetki ister:
# bir niteliği bozmak tek ürünü değil o niteliği taşıyan bütün ürünleri
# etkiler. Bu yüzden yazma uçları `store_products.manage` DEĞİL, ayrı bir
# anahtar (`store_products.attributes`) ister; silme ise ondan da ayrıdır.

@router.get("/attributes")
async def attributes(
    q: str = Query("", max_length=80),
    kind: str = Query("", max_length=24),
    scope: str = Query("", max_length=16),
    user: CurrentUser = requires("store_products.view"),
) -> dict[str, Any]:
    return await service().attributes(q=q, kind=kind, scope=scope)


@router.get("/attributes/{attribute_id}")
async def attribute(
    attribute_id: int,
    user: CurrentUser = requires("store_products.view"),
) -> dict[str, Any]:
    return await service().attribute(attribute_id)


class AttributeCreateBody(BaseModel):
    """Kod ve tip YALNIZ burada seçilir; sonra değiştirilemez."""

    code: str = Field(min_length=2, max_length=50)
    type: str = Field(min_length=2, max_length=24)
    patch: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/attributes")
async def create_attribute(
    body: AttributeCreateBody,
    user: CurrentUser = requires("store_products.attributes"),
) -> dict[str, Any]:
    return await service().create_attribute(code=body.code, kind=body.type, patch=body.patch,
                                            reason=body.reason, actor=user.full_name,
                                            dry_run=body.dryRun)


class AttributePatchBody(BaseModel):
    """`code`/`type` ŞEMADA YOK: gövde onları taşıyamaz.

    Yine de servis `locked_error` ile tekrar bakar — şema tek başına kapı
    değildir ve gövde `patch` içinde de gelebilir (K9).
    """

    patch: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.put("/attributes/{attribute_id}")
async def update_attribute(
    attribute_id: int,
    body: AttributePatchBody,
    user: CurrentUser = requires("store_products.attributes"),
) -> dict[str, Any]:
    return await service().update_attribute(attribute_id, patch=body.patch, reason=body.reason,
                                            actor=user.full_name, dry_run=body.dryRun)


@router.post("/attributes/{attribute_id}/deactivate")
async def deactivate_attribute(
    attribute_id: int,
    body: ReasonBody,
    user: CurrentUser = requires("store_products.attributes"),
) -> dict[str, Any]:
    """Silmenin yerine geçen işlem: değerler kalır, bayraklar iner (ADR 0012)."""
    return await service().deactivate_attribute(attribute_id, reason=body.reason,
                                                actor=user.full_name, dry_run=body.dryRun)


@router.post("/attributes/{attribute_id}/delete")
async def delete_attribute(
    attribute_id: int,
    body: ReasonBody,
    user: CurrentUser = requires("store_products.attributes_delete"),
) -> dict[str, Any]:
    """Nitelik silme — VERİ KAYBIDIR. Servis kullanımdaki niteliği reddeder."""
    return await service().delete_attribute(attribute_id, reason=body.reason,
                                            actor=user.full_name, dry_run=body.dryRun)


class OptionBody(BaseModel):
    optionId: int | None = Field(default=None, ge=1)
    name: str = Field(min_length=1, max_length=180)
    sortOrder: int = Field(default=0, ge=0, le=9_999)
    swatch: str = Field(default="", max_length=64)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/attributes/{attribute_id}/options")
async def save_option(
    attribute_id: int,
    body: OptionBody,
    user: CurrentUser = requires("store_products.attributes"),
) -> dict[str, Any]:
    return await service().save_option(attribute_id, option_id=body.optionId, name=body.name,
                                       sort_order=body.sortOrder, swatch=body.swatch,
                                       reason=body.reason, actor=user.full_name,
                                       dry_run=body.dryRun)


@router.post("/attributes/{attribute_id}/options/{option_id}/delete")
async def delete_option(
    attribute_id: int,
    option_id: int,
    body: ReasonBody,
    user: CurrentUser = requires("store_products.attributes_delete"),
) -> dict[str, Any]:
    """Seçeneği silmek o değeri taşıyan ÜRÜNLERDEN de düşürür."""
    return await service().delete_option(attribute_id, option_id, reason=body.reason,
                                         actor=user.full_name, dry_run=body.dryRun)


@router.get("/families")
async def families(
    user: CurrentUser = requires("store_products.view"),
) -> dict[str, Any]:
    return await service().families()


@router.get("/families/{family_id}")
async def family(
    family_id: int,
    user: CurrentUser = requires("store_products.view"),
) -> dict[str, Any]:
    return await service().family(family_id)


class FamilyGroup(BaseModel):
    code: str = Field(default="", max_length=50)
    name: str = Field(min_length=1, max_length=120)
    column: int = Field(default=1, ge=1, le=4)
    position: int = Field(default=0, ge=0, le=999)
    attributeIds: list[int] = Field(default_factory=list)


class FamilyBody(BaseModel):
    """`groups` YOKSA grup düzenine dokunulmaz.

    TUZAK: boş liste göndermek ailenin bütün gruplarını siler. `None` ile
    `[]` arasındaki fark burada kasıtlıdır; servis de aynı ayrımı korur.
    """

    name: str = Field(min_length=1, max_length=120)
    code: str = Field(default="", max_length=50)
    groups: list[FamilyGroup] | None = None
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/families")
async def create_family(
    body: FamilyBody,
    user: CurrentUser = requires("store_products.attributes"),
) -> dict[str, Any]:
    return await service().save_family(None, name=body.name, code=body.code,
                                       groups=_groups(body), reason=body.reason,
                                       actor=user.full_name, dry_run=body.dryRun)


@router.put("/families/{family_id}")
async def update_family(
    family_id: int,
    body: FamilyBody,
    user: CurrentUser = requires("store_products.attributes"),
) -> dict[str, Any]:
    return await service().save_family(family_id, name=body.name, code=body.code,
                                       groups=_groups(body), reason=body.reason,
                                       actor=user.full_name, dry_run=body.dryRun)


def _groups(body: FamilyBody) -> list[dict[str, Any]] | None:
    """`None` ile `[]` ayrımını Pydantic modelinden sözlüğe TAŞIYARAK korur."""
    if body.groups is None:
        return None
    return [group.model_dump() for group in body.groups]


# =============================================================== ayarlar

@router.get("/settings")
async def settings(
    user: CurrentUser = requires("store_products.view"),
) -> dict[str, Any]:
    return await service().settings()


class SettingsBody(BaseModel):
    lowStockThreshold: int | None = Field(default=None, ge=0, le=9_999)
    backOrder: bool | None = None
    outOfStock: bool | None = None
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/settings")
async def save_settings(
    body: SettingsBody,
    user: CurrentUser = requires("store_products.manage"),
) -> dict[str, Any]:
    return await service().save_settings(low_stock_threshold=body.lowStockThreshold,
                                         back_order=body.backOrder,
                                         out_of_stock=body.outOfStock, reason=body.reason,
                                         actor=user.full_name, dry_run=body.dryRun)


# ================================================================= rapor

class PreviewBody(BaseModel):
    kind: str = Field(max_length=24)
    categoryId: int = 0
    type: str = Field(default="", max_length=24)


@router.post("/preview")
async def preview(
    body: PreviewBody,
    user: CurrentUser = requires("store_products.view"),
) -> dict[str, Any]:
    return await service().preview(body.kind, {"categoryId": body.categoryId,
                                               "type": body.type})


class PrintBody(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    copies: int = Field(default=1, ge=1, le=20)


@router.post("/print")
async def print_report(
    body: PrintBody,
    user: CurrentUser = requires("store_products.view"),
) -> dict[str, Any]:
    return await service().print_report(body.path, copies=body.copies)


@router.get("/printer")
async def printer(
    user: CurrentUser = requires("store_products.view"),
) -> dict[str, Any]:
    return await service().printer_status()


class ExportBody(BaseModel):
    categoryId: int = 0
    type: str = Field(default="", max_length=24)


@router.post("/export")
async def export(
    body: ExportBody,
    user: CurrentUser = requires("store_products.view"),
) -> dict[str, Any]:
    """TÜM kayıtların CSV'si — rapor klasörüne yazılır. Görünen sayfanın
    CSV'sini panel kendisi üretir (sunucuya hiç gitmez)."""
    filters: dict[str, Any] = {}
    if body.categoryId:
        filters["category_id"] = body.categoryId
    if body.type:
        filters["type"] = body.type
    return await service().export_csv(scope="all", filters=filters)
