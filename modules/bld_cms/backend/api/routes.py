"""Site İçeriği — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde düğmeyi gizlemek yetkilendirme
değildir. `module.yaml` → `http.requires` taban izni verir, uçlar onu DARALTIR.

ÜÇ İZİN, İKİ DEĞİL:

    bld_cms.view      okuma + yerel düzenleme geçmişi
    bld_cms.manage    içerik anahtarı yazma, hizmet/yazı açma ve güncelleme,
                      yayına alma / yayından çıkarma, siteyi yeniden çizdirme
    bld_cms.delete    YIKICI: hizmet ve yazının GERÇEKTEN silinmesi

Üçüncüsünün ayrı durmasının nedeni şudur: sözleşme yumuşak silme sunmuyor
(cms.md — "Gerçek silme"). Silinen kayıt geri gelmez ve o adrese verilmiş
bütün bağlantılar kırılır. Günlük ihtiyaç — "sitede görünmesin" — zaten
`is_published = false` ile karşılanıyor ve `bld_cms.manage`e düşüyor; iki işi
tek anahtara bağlamak, sayfayı gizlemek isteyen herkese sayfayı yok etme
yetkisi vermek olurdu.

GEREKÇE İKİ KEZ DENETLENİR: burada şema kapısında (`min_length`) ve serviste
(K9 — çift kapı). Şema kapısı erken geri bildirim içindir; asıl kapı serviste,
çünkü istemci gövdeyi elle kurabilir.

`dryRun` camelCase'tir ve TEK KABUL EDİLEN addır. Panel→Kontrol Merkezi
sınırında `store_orders`/`bld_kds` deseni geçerli; tele giden `dry_run` adına
çeviriyi geçit yapar. `dry_run` da kabul edilseydi, bir yazım hatası ("dryrun",
"dry_Run") sessizce düşer ve alan hiç gönderilmemiş sayılırdı. Tek ad +
`extra="forbid"` sayesinde yanlış yazılan alan 422 ile geri döner ve kimse
kuru prova sandığı bir isteğin gerçek yazma yaptığını sonradan öğrenmez.

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran mesajı
gösterir. 4xx yalnız izin ve şema kapısından çıkar.
"""

from __future__ import annotations

from typing import Any, ClassVar

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..content import MAX_REASON, MIN_REASON
from ..service import CmsService

#: İzin anahtarları tek yerde durur: uçlar ve servis aynı dizeyi okur, yazım
#: hatası bir kapıyı sessizce açık bırakamaz.
VIEW = "bld_cms.view"
MANAGE = "bld_cms.manage"
DELETE = "bld_cms.delete"

router = APIRouter()
_service: CmsService | None = None


def bind(service: CmsService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> CmsService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


class WriteBody(BaseModel):
    """Her yazma gövdesinin ortak üç alanı.

    `actor` GÖVDEDEN ALINMAZ — oturumdan gelir. İstemcinin aktör adını
    yazabilmesi, denetim izini imzalanmamış bir deftere çevirirdi: silinmeyen
    bir satıra istediği adı yazan biri, işi başkasının üstüne bırakabilirdi.

    `revalidate` ÜÇ DEĞERLİDİR (`None` = "modül ayarı ne diyorsa"). Onay kutusu
    üçüncü hâli anlatamaz ama panel bu alanı gönderdiğinde açık bir seçim
    yapmış olur; hiç göndermediğinde ayar geçerlidir ve o AÇIK. Kapalı
    varsayılan, olağan akışı "kaydettim ama sitede yok" ile bitirirdi.
    """

    #: `ClassVar` ile işaretli, çünkü `km_sdk` pydantic'in `ConfigDict` tipini
    #: dışa vurmuyor ve modül pydantic'i doğrudan import etmiyor (K2). Düz
    #: sözlük pydantic için yeterli; işaret yalnız "bu bir alan değil" der.
    model_config: ClassVar[dict[str, Any]] = {"extra": "forbid"}

    reason: str = Field(min_length=MIN_REASON, max_length=MAX_REASON)
    dryRun: bool | None = None
    revalidate: bool | None = None


# ================================================================== okuma

@router.get("/content")
async def content(
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Yedi içerik anahtarının tamamı + ekranın yerel sözleşmesi.

    `GET /content/{key}` YOKTUR (cms.md): tek anahtar için ayrı bir uç, panelin
    yedi istek atması demekti.
    """
    return await service().content()


@router.get("/services")
async def services(
    published: str = Query("", max_length=8),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Hizmet sayfaları. Sayfalanmaz — hizmet sayısı onlarla ifade edilir."""
    return await service().services(published=published)


@router.get("/posts")
async def posts(
    q: str = Query("", max_length=160),
    category: str = Query("", max_length=64),
    published: str = Query("", max_length=8),
    page: int = Query(1, ge=1, le=1_000),
    per_page: int = Query(0, ge=0, le=200),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Bilgi merkezi yazıları — sayfalı. Kategori listesi sunucudan gelir."""
    return await service().posts(q=q, category=category, published=published,
                                 page=page, per_page=per_page)


@router.get("/revisions")
async def revisions(
    target_type: str = Query("", max_length=24),
    target_id: int = Query(0, ge=0),
    target_key: str = Query("", max_length=96),
    limit: int = Query(0, ge=0, le=500),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """YEREL düzenleme geçmişi — ağa çıkmaz, BLD düşse de okunur.

    `bld_cms.view` YETER: bu bir okumadır ve eski metni görmek, yenisini
    yazabilmek demek değildir. Yazma kapısı düzenleme uçlarındadır.
    """
    return await service().revisions(target_type=target_type, target_id=target_id,
                                     target_key=target_key, limit=limit)


@router.get("/revisions/{revision_id}")
async def revision(
    revision_id: int,
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Tek sürümün gövdesi. Kırpılmış sürüm `restorable: false` ile döner."""
    return await service().revision(revision_id)


# ================================================================== içerik

class ContentBody(WriteBody):
    #: TAM DEĞER, birleştirilmez (cms.md). Şemasızdır: sunucu da içeriği
    #: doğrulamıyor, yalnız geçerli JSON olduğunu ve boyutunu denetliyor.
    #: Buraya bir şema koymak, site yeni bir alan eklediğinde Kontrol
    #: Merkezi'nde de değişiklik gerektirirdi.
    value: Any = None


@router.put("/content/{key}")
async def save_content(
    key: str,
    body: ContentBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Tek içerik anahtarını yazar. Anahtar listesi sabittir; uydurulamaz."""
    return await service().save_content(key, value=body.value, reason=body.reason,
                                        actor=user.full_name, dry_run=body.dryRun,
                                        revalidate=body.revalidate)


# ================================================================ hizmetler

class ServiceBody(WriteBody):
    #: Alanlar `fields` altında durur, kökte değil: kökte olsalardı `reason`,
    #: `dryRun` ve `revalidate` ile aynı ad alanını paylaşırlardı ve
    #: sözleşmeye `reason` adında bir alan eklenemezdi.
    fields: dict[str, Any] = Field(default_factory=dict)


@router.post("/services")
async def create_service(
    body: ServiceBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Yeni hizmet sayfası. `slug` tekildir; çakışma ÖNCE burada söylenir."""
    return await service().create_service(fields=body.fields, reason=body.reason,
                                          actor=user.full_name, dry_run=body.dryRun,
                                          revalidate=body.revalidate)


@router.patch("/services/{service_id}")
async def update_service(
    service_id: int,
    body: ServiceBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Hizmeti günceller — KISMİ. `slug` değişimi uyarı üretir."""
    return await service().update_service(service_id, fields=body.fields,
                                          reason=body.reason, actor=user.full_name,
                                          dry_run=body.dryRun,
                                          revalidate=body.revalidate)


@router.delete("/services/{service_id}")
async def delete_service(
    service_id: int,
    body: WriteBody,
    user: CurrentUser = requires(DELETE),
) -> dict[str, Any]:
    """YIKICI. Gerçek silme; kayıt geri gelmez.

    İzin kapısı burada ve serviste iki kez denetlenir (K9 — çift kapı).
    """
    return await service().delete_service(service_id, reason=body.reason,
                                          actor=user.full_name,
                                          allow_delete=user.has_permission(DELETE),
                                          dry_run=body.dryRun,
                                          revalidate=body.revalidate)


# =================================================================== yazılar

class PostBody(WriteBody):
    fields: dict[str, Any] = Field(default_factory=dict)


@router.post("/posts")
async def create_post(
    body: PostBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Yeni yazı. `body_html` zorunludur ve boş olamaz."""
    return await service().create_post(fields=body.fields, reason=body.reason,
                                       actor=user.full_name, dry_run=body.dryRun,
                                       revalidate=body.revalidate)


@router.patch("/posts/{post_id}")
async def update_post(
    post_id: int,
    body: PostBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Yazıyı günceller — KISMİ."""
    return await service().update_post(post_id, fields=body.fields, reason=body.reason,
                                       actor=user.full_name, dry_run=body.dryRun,
                                       revalidate=body.revalidate)


@router.delete("/posts/{post_id}")
async def delete_post(
    post_id: int,
    body: WriteBody,
    user: CurrentUser = requires(DELETE),
) -> dict[str, Any]:
    """YIKICI. Gerçek silme; yazı geri gelmez."""
    return await service().delete_post(post_id, reason=body.reason,
                                       actor=user.full_name,
                                       allow_delete=user.has_permission(DELETE),
                                       dry_run=body.dryRun,
                                       revalidate=body.revalidate)


# ========================================================== yeniden çizdirme

class RevalidateBody(WriteBody):
    #: `None` ya da boş liste = tümü. En çok 20 yol; her biri `/` ile başlar.
    paths: list[str] | None = None


@router.post("/revalidate")
async def revalidate(
    body: RevalidateBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Sitenin ISR önbelleğini boşaltır.

    `bld_cms.manage` YETER, `delete` DEĞİL: çizdirme hiçbir kaydı değiştirmez,
    yalnız yayındaki sayfayı depodaki hâline eşitler. Yıkıcı işlem iznine
    bağlamak, yazan kişiyi yazdığını yayınlayamaz duruma düşürürdü.
    """
    return await service().revalidate(paths=body.paths, reason=body.reason,
                                      actor=user.full_name, dry_run=body.dryRun)


# ======================================================== satır içi görsel

class ImageBody(WriteBody):
    #: `data:` URI ya da düz base64 — geçidin `upload.py` kapısı çözer.
    #: Görsel metnin İÇİNE GÖMÜLMEZ: yüklenir, adresi eklenir.
    content: str = Field(min_length=1, max_length=12_000_000)
    filename: str = Field(min_length=1, max_length=160)


@router.post("/images")
async def upload_image(
    body: ImageBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Satır içi görseli yükler ve adresini döndürür.

    SÖZLEŞMEDE HENÜZ KARŞILIĞI YOK (cms.md görsel yükleme ucu tanımlamıyor).
    Uç burada duruyor ki geçide metot eklendiği gün panelde ve serviste
    değişiklik gerekmesin; eklenene kadar temiz bir `control_endpoint_missing`
    döner ve panel görsel düğmesini HİÇ ÇİZMEZ (K7).
    """
    return await service().upload_image(content=body.content, filename=body.filename,
                                        reason=body.reason, actor=user.full_name,
                                        dry_run=body.dryRun)
