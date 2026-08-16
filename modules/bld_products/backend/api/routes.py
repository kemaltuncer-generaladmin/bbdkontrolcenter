"""Ürün Yönetimi — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde düğmeyi gizlemek yetkilendirme
değildir. `module.yaml` → `http.requires` taban izni verir, uçlar onu DARALTIR.

ÜÇ İZİN, İKİ DEĞİL:

    bld_products.view      okuma (katalog, kategori ağacı, yerel iz, tercih)
    bld_products.manage    ürün açma ve düzenleme, yeniden satışa açma, görsel
                           yükleme ve kaldırma, tükendi işareti, kategori açma
                           ve düzenleme
    bld_products.retire    YIKICI: ürünü satıştan kaldırma (`menu_status = 0`)

Üçüncüsünün ayrı durmasının nedeni şudur: satıştan kaldırma ürünü siteden ve
sipariş yolundan düşürür ve sonucu ilk fark eden çoğu zaman müşteri olur.
`manage` fiyat ve ad düzeltmesi için günlük bir yetkidir; kaldırma bir karardır.
Kapı iki kez denetlenir — burada ve serviste (`allow_destructive`) — çünkü uç
noktanın izni bir gün gevşetilse bile ikinci kapı durur.

SİLME UCU YOKTUR. Ürün `POST /products/{menu}/retire` ile satıştan kalkar;
kayıt SİLİNMEZ ve geri açmak ayrı bir uç değil, `PATCH` ile `status: true`
yazmaktır. Kategori silen bir uç sözleşmede de yoktur ve burada uydurulmaz:
kategori silmek altındaki ürünleri kategorisiz bırakır ve site menüsünü
sessizce boşaltır.

İKİ `DELETE` VARDIR ve ikisi de KAYIT SİLMEZ: görsel bağını kaldırır
(`/image`) ve bugünkü tükendi işaretini kaldırır (`/sold-out`). Fiil burada
sözleşmedeki fiilin aynısıdır; ikisini "clear" diye adlandırmak, aynı işi iki
farklı adla anlatan bir yüzey üretirdi.

KISMİ GÜNCELLEME GÖVDESİ YUVALIDIR (`fields`). Kökte olsaydı `reason` ve
`dryRun` ile aynı ad alanını paylaşırdı; üstelik "gönderilmedi" ile "null
yazıldı" ayrımı kaybolurdu — `description: null` açıklamayı boşaltmak,
anahtarın hiç bulunmaması ise ona dokunmamak demektir.

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran mesajı
gösterir. 4xx yalnız izin ve şema kapısından çıkar.
"""

from __future__ import annotations

from typing import Any, ClassVar

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..catalog import MAX_REASON, MIN_REASON, NAME_MAX, NAME_MIN, PER_PAGE_MAX
from ..service import ProductsService

#: İzin anahtarları tek yerde durur: uç noktalar ve servis aynı dizgeyi okur,
#: yazım hatası bir kapıyı sessizce açık bırakamaz.
VIEW = "bld_products.view"
MANAGE = "bld_products.manage"
RETIRE = "bld_products.retire"

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


class ReasonBody(BaseModel):
    """Her BLD yazmasının ortak iki alanı.

    `actor` GÖVDEDEN ALINMAZ — oturumdan gelir. İstemcinin aktör adını
    yazabilmesi, denetim izini imzalanmamış bir deftere çevirirdi: silinmeyen
    bir satıra istediği adı yazan biri, işi başkasının üstüne bırakabilirdi.

    `dryRun` camelCase'tir ve TEK KABUL EDİLEN addır. Panel→Kontrol Merkezi
    sınırında `store_orders` deseni geçerli; tele giden `dry_run` adına çeviriyi
    servis yapar. `dry_run` da kabul edilseydi, bir yazım hatası ("dryrun",
    "dry_Run") sessizce düşer ve alan hiç gönderilmemiş sayılırdı. Tek ad +
    `extra="forbid"` sayesinde yanlış yazılan alan 422 ile geri döner ve kimse
    kuru prova sandığı bir isteğin gerçek yazma yaptığını sonradan öğrenmez.
    """

    #: `ClassVar` ile işaretli, çünkü `km_sdk` pydantic'in `ConfigDict` tipini
    #: dışa vurmuyor ve modül pydantic'i doğrudan import etmiyor (K2). Düz
    #: sözlük pydantic için yeterli; işaret yalnız "bu bir alan değil" der.
    model_config: ClassVar[dict[str, Any]] = {"extra": "forbid"}

    reason: str = Field(min_length=MIN_REASON, max_length=MAX_REASON)
    dryRun: bool | None = None


# ================================================================== okuma

@router.get("/overview")
async def overview(
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Panel açılışı: katalog sayaçları, kategori özeti, süzgeç sözleşmesi."""
    return await service().overview()


@router.get("/products")
async def products(
    q: str = Query("", max_length=128),
    category_id: int = Query(0, ge=0),
    status: str = Query("", max_length=16),
    sold_out: bool | None = Query(None),
    sort: str = Query("", max_length=16),
    direction: str = Query("", max_length=8),
    page: int = Query(1, ge=1),
    per_page: int = Query(0, ge=0, le=PER_PAGE_MAX),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Ürün listesi — SUNUCU TARAFINDA sayfalanır.

    `sold_out` ÜÇ DEĞERLİDİR: `None` süzgeç yok, `true` yalnız bugün
    tükenmişler, `false` yalnız tükenmemişler. `bool` varsayılanı `False`
    olsaydı üçüncü hâl kaybolur ve liste sessizce süzülürdü.
    """
    return await service().products(q=q, category_id=category_id, status=status,
                                    sold_out=sold_out, sort=sort, direction=direction,
                                    page=page, per_page=per_page)


@router.get("/products/{menu_id}")
async def product(
    menu_id: int,
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Tek ürün, seçenekleriyle. Seçenekler SALT OKUNURDUR."""
    return await service().product(menu_id)


@router.get("/categories")
async def categories(
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Kategori ağacı — sayfalanmaz, `depth` ile birlikte döner."""
    return await service().categories()


@router.get("/audit")
async def audit(
    limit: int = Query(0, ge=0, le=500),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Bu ekrandan yapılan yazma DENEMELERİ (yerel tablo, ağa çıkmaz)."""
    return await service().audit_trail(limit=limit)


@router.get("/prefs")
async def prefs(
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Ekran tercihi: sayfa boyutu ve açılıştaki süzgeç."""
    return await service().prefs()


class PrefsBody(BaseModel):
    """Ekran tercihi gövdesi. GEREKÇE İSTEMEZ ve `view` izniyle yazılır.

    Uzak sistemde hiçbir şey değişmiyor: bu tablo yalnız bu ekranın açılışta
    neyi gösterdiğini belirler. Gerekçe zorunluluğu bir denetim kaydıdır ve
    denetlenecek bir eylem yoksa, her sayfa boyutu değişikliğinde gerekçe
    istemek gerekçenin kendisini anlamsızlaştırırdı.
    """

    model_config: ClassVar[dict[str, Any]] = {"extra": "forbid"}

    values: dict[str, Any] = Field(default_factory=dict)


@router.put("/prefs")
async def save_prefs(
    body: PrefsBody,
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    return await service().save_prefs(body.values, actor=user.full_name)


# ================================================================== ürünler

class ProductCreateBody(ReasonBody):
    """Yeni ürün gövdesi.

    `price_kurus` KURUŞTUR (tam sayı) ve SIFIR GEÇERLİDİR: paket bileşeni
    olarak satılan ekmek ve ayran sıfır fiyatlıdır. Ondalıklı TL hiçbir yerde
    telde gitmez (`00-genel.md` §6).
    """

    name: str = Field(min_length=NAME_MIN, max_length=NAME_MAX)
    #: Sözleşme açıklamaya bir uzunluk sınırı KOYMUYOR; burada da uydurulmaz.
    #: Sınır sunucunun sütunudur ve aşılırsa `422` ile geri döner.
    description: str | None = None
    price_kurus: int = Field(ge=0)
    minimum_qty: int = Field(default=1, ge=0)
    priority: int = 0
    status: bool = True
    category_ids: list[int] = Field(default_factory=list)


@router.post("/products")
async def create_product(
    body: ProductCreateBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Yeni ürün. AYNI AD ENGELLENMEZ — panel uyarır, sunucu ve servis
    engellemez; "Tavuk Sote" iki farklı tarifle iki ürün olabilir."""
    return await service().create_product(
        name=body.name, price_kurus=body.price_kurus, description=body.description,
        minimum_qty=body.minimum_qty, priority=body.priority, status=body.status,
        category_ids=body.category_ids, reason=body.reason, actor=user.full_name,
        dry_run=body.dryRun)


class PatchBody(ReasonBody):
    """Kısmi yazma gövdesi: yalnız gönderilen alanlar değişir.

    Alanlar `fields` altında YUVALIDIR. İki sebep var ve ikisi de ölçülmüş:
    kökte olsalardı `reason`/`dryRun` ile ad alanını paylaşırlardı, ve
    pydantic varsayılanı "gönderilmedi" ile "null gönderildi" hâllerini aynı
    gösterirdi — oysa `description: null` açıklamayı boşaltmak demektir.

    Tanınmayan anahtar SERVİSTE reddedilir (sessizce düşürülmez): Laravel
    bilmediği alanı yok sayar ve ekran "kaydedildi" derken hiçbir yere
    yazılmamış bir değer kalırdı.
    """

    fields: dict[str, Any] = Field(default_factory=dict)


@router.patch("/products/{menu_id}")
async def update_product(
    menu_id: int,
    body: PatchBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Ürünü günceller. Ürünü yeniden satışa açmak da budur (`status: true`) —
    ayrı bir "restore" ucu sözleşmede yok."""
    return await service().update_product(menu_id, fields=body.fields, reason=body.reason,
                                          actor=user.full_name, dry_run=body.dryRun)


@router.post("/products/{menu_id}/retire")
async def retire_product(
    menu_id: int,
    body: ReasonBody,
    user: CurrentUser = requires(RETIRE),
) -> dict[str, Any]:
    """YIKICI. Ürün satıştan kalkar (`menu_status = 0`); kaydı SİLİNMEZ.

    İzin kapısı burada ve serviste iki kez denetlenir (K9 — çift kapı).
    """
    return await service().retire_product(menu_id, reason=body.reason,
                                          actor=user.full_name, dry_run=body.dryRun,
                                          allow_destructive=user.has_permission(RETIRE))


class ImageBody(ReasonBody):
    """Görsel gövdesi — BASE64, multipart DEĞİL.

    Multipart gövde sınır dizeleri taşır ve gövdeyi yeniden kodlayan herhangi
    bir vekil imzayı bozar; arıza sahada "sır yanlış" gibi görünür. Gerekçe
    `products.md` → "Neden base64, neden multipart değil".

    `content` `data:` önekli de gelebilir (tarayıcının `FileReader` çıktısı);
    çözme, boyut ve İÇERİKTEN tür okuma geçidin işidir.
    """

    filename: str = Field(min_length=1, max_length=180)
    content: str = Field(min_length=1)


@router.put("/products/{menu_id}/image")
async def set_image(
    menu_id: int,
    body: ImageBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    return await service().set_image(menu_id, content=body.content, filename=body.filename,
                                     reason=body.reason, actor=user.full_name,
                                     dry_run=body.dryRun)


@router.delete("/products/{menu_id}/image")
async def clear_image(
    menu_id: int,
    body: ReasonBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Görseli kaldırır. KAYIT SİLMEZ; görseli olmayan üründe de hata değildir.

    `DELETE` gövde taşır — HTTP açısından alışılmadıktır ama sözleşmede
    bilinçlidir (`00-genel.md` §3): gerekçeyi sorgu dizesine koymak onu
    imzanın dışında bırakırdı.
    """
    return await service().clear_image(menu_id, reason=body.reason, actor=user.full_name,
                                       dry_run=body.dryRun)


class SoldOutBody(ReasonBody):
    """Tükendi işareti gövdesi.

    `reason` `veykemtu_menu_soldout.reason` sütununa DA yazılır ve mutfak
    ekranında görünür; `note` yalnız denetim izine gider. İkisi ayrı alandır
    çünkü biri mutfağa, öteki deftere konuşur.
    """

    note: str = Field(default="", max_length=500)


@router.post("/products/{menu_id}/sold-out")
async def mark_sold_out(
    menu_id: int,
    body: SoldOutBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """BUGÜNE ÖZEL tükendi işareti; ertesi gün kendiliğinden düşer."""
    return await service().mark_sold_out(menu_id, note=body.note, reason=body.reason,
                                         actor=user.full_name, dry_run=body.dryRun)


@router.delete("/products/{menu_id}/sold-out")
async def clear_sold_out(
    menu_id: int,
    body: ReasonBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Tükendi işaretini kaldırır. İşaret yoksa da başarılıdır."""
    return await service().clear_sold_out(menu_id, reason=body.reason,
                                          actor=user.full_name, dry_run=body.dryRun)


# ============================================================== kategoriler

class CategoryCreateBody(ReasonBody):
    """Yeni kategori gövdesi. `slug` YOKTUR — addan üretilir (`HasPermalink`)."""

    name: str = Field(min_length=NAME_MIN, max_length=NAME_MAX)
    description: str | None = None
    parent_id: int | None = None
    priority: int = 0
    status: bool = True


@router.post("/categories")
async def create_category(
    body: CategoryCreateBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    return await service().create_category(
        name=body.name, description=body.description, parent_id=body.parent_id,
        priority=body.priority, status=body.status, reason=body.reason,
        actor=user.full_name, dry_run=body.dryRun)


@router.patch("/categories/{category_id}")
async def update_category(
    category_id: int,
    body: PatchBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Kategoriyi günceller. GİZLEMEK `status: false` yazmaktır — kategori
    silen bir uç sözleşmede yoktur ve burada uydurulmaz."""
    return await service().update_category(category_id, fields=body.fields,
                                           reason=body.reason, actor=user.full_name,
                                           dry_run=body.dryRun)
