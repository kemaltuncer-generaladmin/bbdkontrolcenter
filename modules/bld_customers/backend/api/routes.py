"""Müşteriler — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde düğmeyi gizlemek yetkilendirme
değildir. `module.yaml` → `http.requires` taban izni verir, uçlar onu DARALTIR.

ÜÇ İZİN, İKİ DEĞİL:

    bld_customers.view      okuma (arama, kart, sipariş, abonelik, adres, SMS,
                            yerel iz, tercih)
    bld_customers.manage    iletişim bilgileri + kurum etiketleri; kapalı bir
                            hesabı yeniden AÇMA
    bld_customers.disable   YIKICI: hesabı KAPATMA

Üçüncüsünün ayrı durmasının nedeni şudur: kapalı bir hesap giriş yapamaz ve
sipariş veremez; sonucu ilk fark eden çoğu zaman müşterinin kendisi olur ve o an
satış kaybedilmiştir. `manage` bir telefon düzeltmesi için günlük bir yetkidir,
hesabı kapatmak bir karardır. Kapı iki kez denetlenir — burada ve serviste
(`allow_destructive`) — çünkü uç noktanın izni bir gün gevşetilse bile ikinci
kapı durur.

HESABI AÇMAK İKİ İZNİ BİRDEN KABUL EDER (`requires` "en az biri" demektir):
kapatmak yıkıcı, açmak onarıcıdır. Açmayı da üçüncü anahtara bağlamak,
yanlışlıkla kapatılmış bir hesabı düzeltebilecek kişi sayısını azaltırdı — yani
hatayı uzatırdı.

`actor` HİÇBİR UÇTA GÖVDEDEN YA DA SORGUDAN ALINMAZ — oturumdan gelir. Sözleşme
`actor`ı sorgu dizesinde taşıyor ama o sınır BLD ile Kontrol Merkezi
ARASINDADIR; panel ile bu modül arasında böyle bir sınır yok. İstemcinin aktör
adını yazabilmesi, silinmeyen bir deftere istediği adı yazabilmek demek olurdu
ve bu ekranda o defter KVKK defteridir.

BU UÇLAR YOKLANMAZ. Panel burada `pollLoop` KURMAZ: her `GET` hem BLD'de hem
yerelde bir denetim satırı yazar ve 15 saniyede bir yoklayan bir ekran, izi
günde binlerce anlamsız satırla doldurup içindeki gerçek erişimi görünmez
kılardı (`00-genel.md` §9).

SİLME UCU YOKTUR VE OLMAYACAKTIR. `DELETE` fiili bu router'da hiç geçmez.
E-POSTA VE PAROLA YAZAN UÇ DA YOKTUR: `PATCH` gövdesi `people.WRITABLE_FIELDS`
dışındaki bir anahtar taşırsa servis isteği reddeder ve geçide hiç göndermez.

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran mesajı
gösterir. 4xx yalnız izin ve şema kapısından çıkar.
"""

from __future__ import annotations

from typing import Any, ClassVar

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..people import MAX_REASON, MIN_REASON, PER_PAGE_MAX
from ..service import CustomersService

#: İzin anahtarları tek yerde durur: uç noktalar ve servis aynı dizgeyi okur,
#: yazım hatası bir kapıyı sessizce açık bırakamaz.
VIEW = "bld_customers.view"
MANAGE = "bld_customers.manage"
DISABLE = "bld_customers.disable"

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
    """Panel açılışı — BLD'YE HİÇ GİTMEZ.

    Süzgeç sözleşmesi, ekran tercihi ve KVKK uyarı metinleri döner; sayaç
    DÖNMEZ. Açılışta sayaç çekmek, her biri bir `customer.read` satırı yazan
    dört istek atmak olurdu — kimsenin sormadığı bir soru için deftere dört
    satır. Sayılar aramanın `meta.total` alanından gelir, yani yöneticinin
    bilinçli bir eyleminden.

    Bu yüzden yanıtta `connected` YOKTUR: bağlantı durumu ancak gerçek bir
    okumada bilinir ve uydurulmuş bir `true` ekranı "bağlı" diye gösterirdi.
    """
    return await service().overview()


@router.get("/customers")
async def customers(
    q: str = Query("", max_length=128),
    status: str = Query("", max_length=16),
    has_subscription: bool | None = Query(None),
    sort: str = Query("", max_length=16),
    direction: str = Query("", max_length=8),
    page: int = Query(1, ge=1),
    per_page: int = Query(0, ge=0, le=PER_PAGE_MAX),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Müşteri arama — SUNUCU TARAFINDA sayfalanır. **Denetlenir.**

    `has_subscription` ÜÇ DEĞERLİDİR: `None` süzgeç yok, `true` yalnız
    aboneliği olanlar, `false` yalnız olmayanlar. `bool` varsayılanı `False`
    olsaydı üçüncü hâl kaybolur ve ekran "hepsi" diyemezdi.

    `actor` SORGUDA DEĞİL, oturumdadır — bkz. dosya başlığı.
    """
    return await service().customers(
        actor=user.full_name, q=q, status=status, has_subscription=has_subscription,
        sort=sort, direction=direction, page=page, per_page=per_page,
    )


@router.get("/customers/{customer_id}")
async def customer(
    customer_id: int,
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Tek müşteri + istatistikleri. **Denetlenir.**

    `stats` bu yanıtta gelir, ayrı bir uçta değil: ayrı bir çağrı ikinci bir
    denetim satırı yazardı.
    """
    return await service().customer(customer_id, actor=user.full_name)


@router.get("/customers/{customer_id}/orders")
async def customer_orders(
    customer_id: int,
    status: str = Query("", max_length=128),
    date_from: str = Query("", max_length=10),
    date_to: str = Query("", max_length=10),
    page: int = Query(1, ge=1),
    per_page: int = Query(0, ge=0, le=PER_PAGE_MAX),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Müşterinin sipariş geçmişi (sayfalı). **Denetlenir. SALT OKUNUR.**

    Bu uç siparişi DEĞİŞTİRMEZ ve buradan `bld_orders`a kısayol da yoktur: bir
    iş eylemi tek ekranda durur, yoksa denetim izinde "hangi ekrandan yapıldı"
    sorusu cevapsız kalır.

    `status` VİRGÜLLÜ liste kabul eder (`orders.md`); ayrıştırmayı geçit yapar.
    """
    return await service().orders(customer_id, actor=user.full_name, status=status,
                                  date_from=date_from, date_to=date_to,
                                  page=page, per_page=per_page)


@router.get("/customers/{customer_id}/subscriptions")
async def customer_subscriptions(
    customer_id: int,
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Müşterinin abonelikleri. **Denetlenir.** SAYFALANMAZ.

    Bir müşterinin abonelik sayısı tek hanelidir (sözleşme); yanıt `meta`
    dörtlüsü taşımaz ve panel sayfalayıcı çizmez.
    """
    return await service().subscriptions(customer_id, actor=user.full_name)


@router.get("/customers/{customer_id}/addresses")
async def customer_addresses(
    customer_id: int,
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Adres defteri. **Denetlenir. SALT OKUNUR.**

    Adres yazan bir uç sözleşmede YOKTUR ve burada uydurulmaz: adres siparişe
    kopyalanıyor, bağlanmıyor; defteri panelden düzenlemek geçmiş siparişlerin
    adresini değiştirmez ve yönetici değiştirdiğini sanır.
    """
    return await service().addresses(customer_id, actor=user.full_name)


@router.get("/customers/{customer_id}/sms")
async def customer_sms(
    customer_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(0, ge=0, le=PER_PAGE_MAX),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Müşteriye giden SMS'lerin gönderim kaydı (sayfalı). **Denetlenir.**

    UCUN EVİ BAŞKA ALANDA: `control/sms/log`. Sunucu bu okuma için
    `customer.read` satırı YAZMAZ; yerel iz tek kayıttır ve orada `action`
    alanı `sms.read`'tir. Ayrım raporlanmıştır.

    `bld_comms.*` DEĞİL `bld_customers.view` İSTER: burada okunan şey bir SMS
    şablonu ya da kampanya değil, TEK BİR MÜŞTERİNİN gönderim geçmişidir ve
    kişisel veri kutusuna aittir. SMS altyapısını yöneten ekran ayrı
    (`bld_sms`) ve bu uç ona hiçbir şey açmaz.
    """
    return await service().sms(customer_id, actor=user.full_name, page=page,
                               per_page=per_page)


# ================================================================== yazma

class UpdateBody(ReasonBody):
    """Kısmi güncelleme gövdesi.

    `fields` YUVALIDIR. Kökte olsaydı `reason` ve `dryRun` ile aynı ad alanını
    paylaşırdı; üstelik "gönderilmedi" ile "null yazıldı" ayrımı kaybolurdu —
    `org_name: null` kurum adını boşaltmak, anahtarın hiç bulunmaması ise ona
    dokunmamak demektir.

    ŞEMA BURADA ALAN ADI DENETLEMEZ. Yasak alanların (`email`, `password`,
    `account_type`, `status`) her biri KENDİ GEREKÇESİYLE reddedilmeli ve
    pydantic'in üreteceği "extra fields not permitted" cümlesi o gerekçeyi
    taşıyamaz. Kapı `people.patch_error`'dadır ve tek cümleyle NEDEN olmadığını
    söyler.
    """

    fields: dict[str, Any] = Field(default_factory=dict)


@router.patch("/customers/{customer_id}")
async def update_customer(
    customer_id: int,
    body: UpdateBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """İletişim bilgileri + serbest metin kurum etiketleri. **KISMİ.**

    Yazılabilir alanlar YALNIZ: `first_name`, `last_name`, `telephone`,
    `org_name`, `tax_office`, `tax_no`, `contact_person`, `org_phone`.
    E-POSTA VE PAROLA BU UÇTAN DA YAZILAMAZ.
    """
    return await service().update(customer_id, fields=body.fields, reason=body.reason,
                                  actor=user.full_name, dry_run=body.dryRun)


@router.post("/customers/{customer_id}/disable")
async def disable_customer(
    customer_id: int,
    body: ReasonBody,
    user: CurrentUser = requires(DISABLE),
) -> dict[str, Any]:
    """YIKICI. Hesabı kapatır; kayıt SİLİNMEZ.

    İzin kapısı burada ve serviste iki kez denetlenir (K9 — çift kapı).
    Aktif aboneliği olan bir hesabı kapatmak ENGELLENMEZ ama uyarı üretir:
    abonelik üretimi durmaz (kural hesaba değil aboneliğe bağlıdır) ve yönetici
    bunu bilmelidir.
    """
    return await service().disable(customer_id, reason=body.reason, actor=user.full_name,
                                   dry_run=body.dryRun,
                                   allow_destructive=user.has_permission(DISABLE))


@router.post("/customers/{customer_id}/enable")
async def enable_customer(
    customer_id: int,
    body: ReasonBody,
    #: İKİ İZİN, "en az biri". Açmak onarıcı bir işlemdir; yalnız `disable`
    #: iznine bağlamak, yanlışlıkla kapatılmış bir hesabı düzeltebilecek kişi
    #: sayısını azaltır — yani hatayı uzatırdı.
    user: CurrentUser = requires(MANAGE, DISABLE),
) -> dict[str, Any]:
    """Kapatılmış hesabı yeniden açar. Gerekçe yine zorunlu."""
    return await service().enable(customer_id, reason=body.reason, actor=user.full_name,
                                  dry_run=body.dryRun)


# =========================================================== yerel kayıtlar

@router.get("/access-log")
async def access_log(
    customer_id: int = Query(0, ge=0),
    actor: str = Query("", max_length=120),
    limit: int = Query(0, ge=0, le=1000),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """YEREL KVKK erişim izi — ağa çıkmaz, müşteri verisi taşımaz.

    "Kim, ne zaman, kimin kaydını açtı" sorusunun buradaki cevabı. Satırlar
    yalnız aktör, kapsam, müşteri kimliği ve SÜZGEÇLERİ taşır; dönen kayıtlar
    ASLA yazılmaz (`00-genel.md` §9.4).

    Buradaki `actor` bir SÜZGEÇTİR, kimlik değil: kimliği yine oturum belirler
    ve bu uç yalnız okur. Süzgeç olmasaydı "şu kişi bu ay kimlere baktı"
    sorusunun cevabı, bütün defteri gözle taramaktan geçerdi.

    BU OKUMANIN KENDİSİ İZE YAZILMAZ: defteri açan her kişi deftere bir satır
    eklerse sonsuz bir kuyruk doğar.
    """
    return await service().access_log(customer_id=customer_id, actor=actor, limit=limit)


@router.get("/audit")
async def audit_trail(
    customer_id: int = Query(0, ge=0),
    limit: int = Query(0, ge=0, le=500),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Bu ekrandan yapılan YAZMA DENEMELERİ — yerel tablo, ağa çıkmaz.

    `result` sütunu "denendi"de kalmış bir satır, isteğin gidip gitmediği
    BİLİNMEYEN bir denemedir; sunucunun kendi defteri o satırı hiç bilmez.
    """
    return await service().audit_trail(customer_id=customer_id, limit=limit)


class PrefsBody(BaseModel):
    """Ekran tercihi gövdesi. GEREKÇE İSTEMEZ.

    Tercih BLD'yi etkilemez ve KVKK erişim izine satır düşürmez: yalnız bu
    ekranın açılışta hangi süzgeçle ve kaç satırla geldiğini belirler. Gerekçe
    istemek, hiçbir şeyi denetlemeyen bir kutu göstermek olurdu.
    """

    model_config: ClassVar[dict[str, Any]] = {"extra": "forbid"}

    values: dict[str, Any] = Field(default_factory=dict)


@router.get("/prefs")
async def prefs(
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    return {"ok": True, **await service().prefs()}


@router.put("/prefs")
async def save_prefs(
    body: PrefsBody,
    #: `view` YETER: uzak sistemde hiçbir şey değişmiyor ve müşteri verisine
    #: dokunulmuyor. `manage` istemek, listeyi 50 satır göstermek isteyen bir
    #: okuyucudan yazma yetkisi istemek olurdu.
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    return await service().save_prefs(body.values, actor=user.full_name)
