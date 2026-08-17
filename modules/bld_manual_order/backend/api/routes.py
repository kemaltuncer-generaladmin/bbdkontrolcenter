"""Elle Sipariş — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde düğmeyi gizlemek yetkilendirme
değildir. `module.yaml` → `http.requires` taban izni verir, uçlar onu DARALTIR.

ÜÇ İZİN:

    bld_manual_order.view            okuma — ekranın sözleşmesi, müşteri
                                     araması, ürün kataloğu, servis gününün
                                     kesimi ve stoğu
    bld_manual_order.manage          YAZMA — siparişi açar (ve gerekirse
                                     müşteriyi)
    bld_manual_order.price_override  KATALOG FİYATINI KIRAR — siparişe
                                     müşteriye özel sepet fiyatı yazar

ÜÇÜNCÜ ANAHTARIN ÖLÇÜTÜ `bld_orders`INKİYLE AYNI: PARA. Bu ekran uzun süre tek
bir yazma taşıdı (sipariş açmak) ve o yüzden iki anahtar yetiyordu; anlaşmalı
sepet fiyatıyla birlikte İKİNCİ bir para kararı doğdu. Sipariş AÇMAK rutin bir
kayıt akışıdır, katalog fiyatını KIRMAK ciroyu değiştiren ticari bir karardır.
Tek anahtarda kalsaydı fiyat kırmayı engellemenin tek yolu sipariş girmeyi
engellemek olurdu — yani telefonu açan personelin işini yapamaz hâle getirmek.

Anahtar `manage`ı KAPSAMAZ, ona EKLENİR: yalnız `price_override` taşıyan biri
sipariş açamaz.

GEREKÇE ZORUNLU DEĞİL. `orders.md` → "POST /" bunu açıkça karara bağladı:
telefon siparişi açmak rutin bir KAYIT akışıdır ve `00-genel.md` §3'ün
varsayılanı burada bilerek gevşetildi. Gönderilirse yalnız ÜST sınır (500)
denetlenir; on karakterlik alt sınır UYGULANMAZ — uygulansaydı personel
müşteriyle konuşurken "sipariş", "asdasd" yazardı ve sınırın engellemek için
var olduğu şey üretilirdi. `actor` yine de her yazmada gider.

MÜŞTERİ ARAMASI `GET`TİR AMA MASUM DEĞİLDİR: sözleşme her müşteri okumasını
KVKK gereği denetliyor ve `actor` sorgu dizesine konuyor. Bu yüzden panel bu
uca otomatik yenileme KURMAZ ve arama kısa sorguda hiç gönderilmez.

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran
mesajı gösterir. 4xx yalnız izin ve şema kapısından çıkar.
"""

from __future__ import annotations

from typing import Any, ClassVar

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..draft import (
    MAX_ADDRESS_LINE,
    MAX_ADDRESS_NOTE,
    MAX_ADDRESS_PART,
    MAX_CUSTOMER_NAME,
    MAX_CUSTOMER_NOTE,
    MAX_CUSTOMER_PHONE,
    MAX_ITEMS,
    MAX_REASON,
)
from ..service import ManualOrderService

#: İzin anahtarları tek yerde durur: uçlar ve servis aynı dizeyi okur, yazım
#: hatası bir kapıyı sessizce açık bırakamaz.
VIEW = "bld_manual_order.view"
MANAGE = "bld_manual_order.manage"
PRICE_OVERRIDE = "bld_manual_order.price_override"

router = APIRouter()
_service: ManualOrderService | None = None


def bind(service: ManualOrderService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> ManualOrderService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


# ================================================================== okuma

@router.get("/overview")
async def overview(
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Ekranın sözleşmesi ve alan sınırları. AĞA ÇIKMAZ (K7).

    `can_price_override` DA BURADAN GELİR. Ekran fiyat kutusunu buna göre
    çiziyor; gizlemek YETKİLENDİRME DEĞİLDİR (asıl kapı `POST /orders` ve
    servistedir, K9) ama her seferinde 403 alan bir kutu çizmek de personele
    çalışmayan bir alan göstermek olurdu.
    """
    return await service().overview(
        allow_price_override=user.has_permission(PRICE_OVERRIDE),
    )


@router.get("/customers")
async def customers(
    q: str = Query("", max_length=120),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Müşteri araması. `actor` GÖVDEDEN/SORGUDAN DEĞİL, OTURUMDAN gelir.

    KVKK denetim satırını kimin açtığı istemcinin yazabileceği bir alan
    olsaydı, defter imzasız tutulurdu: silinmeyen bir satıra istediği adı yazan
    biri, okumayı başkasının üstüne bırakabilirdi.
    """
    return await service().customers(q=q, actor=user.full_name)


@router.get("/products")
async def products(
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Seçicinin ürün kataloğu — geçitte önbelleklenmiş referans veri."""
    return await service().products()


@router.get("/service-day")
async def service_day(
    date: str = Query("", max_length=10),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Servis gününün kesim saati, stoğu, ödeme yöntemleri ve teslimat ücreti.

    Süzgeç sorgu dizesindedir (gövde değil): bu bir OKUMADIR ve kopyalanabilir
    bir adresinin olması, "şu günü aç" demenin en kısa yolu.
    """
    return await service().service_day(date)


class StockCheckBody(BaseModel):
    """Sepetin tavan denetimi. OKUMADIR — `view` yeter.

    `POST` seçilmesinin sebebi yetki ya da yan etki DEĞİL, GÖVDE ŞEKLİ: kalem
    listesi bir nesne dizisidir ve sorgu dizesine sığmaz. Yazma değildir, bu
    yüzden `dryRun` alanı da YOKTUR — provası olmayan bir işlemde o alan
    ekranın "acaba yazdı mı" diye sormasına yol açardı.
    """

    model_config: ClassVar[dict[str, Any]] = {"extra": "forbid"}

    service_date: str = Field(min_length=10, max_length=10)
    items: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_ITEMS)


@router.post("/stock-check")
async def stock_check(
    body: StockCheckBody,
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Tavan aşımı uyarısı. HİÇBİR ŞEYİ ENGELLEMEZ.

    `DailyStock::take()` sunucuda `allowOvershoot: true` ile çağrılıyor:
    personel tavanı bilerek aşabilir ("bir porsiyon daha çıkarırız"). Buradan
    dönen liste bir "hayır" değil, bir CÜMLEDİR — aşımın kaza olmamasını
    sağlar.
    """
    return await service().stock_check(service_date=body.service_date, items=body.items)


# ================================================================== yazma

class NewCustomerBody(BaseModel):
    """Kayıtlı olmayan arayan — AYNI EKRANDA açılır.

    Ayrı bir müşteri ekranına gitmek akışı keserdi: personel telefonda,
    müşteri hatta. Kayıt `Admin\\PhoneOrders` kalıbıyla doğar (yer tutucu
    e-posta `tel-<ulusal numara>@bld.invalid`) ve AYNI TELEFONLA İKİNCİ SİPARİŞ
    İKİNCİ MÜŞTERİ YARATMAZ.
    """

    model_config: ClassVar[dict[str, Any]] = {"extra": "forbid"}

    name: str = Field(min_length=2, max_length=MAX_CUSTOMER_NAME)
    phone: str = Field(min_length=1, max_length=MAX_CUSTOMER_PHONE)


class AddressBody(BaseModel):
    """Adres — yalnız `delivery` için. Zorunluluk SERVİSTE denetlenir.

    Alanlar burada `default=""` taşır çünkü `pickup` siparişte gövde hiç
    gönderilmiyor; `required` yapmak gel-al siparişini şema kapısında keserdi.
    Teslimatta boş kalmasını servis reddeder (`draft.address_error`).
    """

    model_config: ClassVar[dict[str, Any]] = {"extra": "forbid"}

    line1: str = Field(default="", max_length=MAX_ADDRESS_LINE)
    district: str = Field(default="", max_length=MAX_ADDRESS_PART)
    city: str = Field(default="", max_length=MAX_ADDRESS_PART)
    note: str = Field(default="", max_length=MAX_ADDRESS_NOTE)


class CreateBody(BaseModel):
    """Sipariş açma gövdesi.

    `actor` GÖVDEDEN ALINMAZ — oturumdan gelir (denetim izi imzasız kalmasın).

    `reason` OPSİYONELDİR ve alt sınırı YOKTUR: sözleşme bu uçta gerekçeyi
    bilerek gevşetti. Üst sınır (500) duruyor.

    `dryRun` camelCase'tir ve TEK KABUL EDİLEN addır. Panel→Kontrol Merkezi
    sınırında camelCase geçerli; tele giden `dry_run` adına çeviriyi geçit
    yapar. `dry_run` da kabul edilseydi bir yazım hatası ("dryrun", "dry_Run")
    sessizce düşer, alan hiç gönderilmemiş sayılır ve varsayılana dönerdi —
    yani kuru prova sanılan bir istek GERÇEK SİPARİŞ açardı. Tek ad +
    `extra="forbid"` sayesinde yanlış yazılan alan 422 ile geri döner.

    `requested_at` ALANI YOKTUR ve eklenmez: saati `OrderFactory` çözüyor
    (servis günü bugünse "şimdi", ileriyse 12:00). Ekrana doldurulması zorunlu
    ama hiçbir yerde okunmayan bir kutu koymak olurdu.
    """

    #: `ClassVar` ile işaretli, çünkü `km_sdk` pydantic'in `ConfigDict` tipini
    #: dışa vurmuyor ve modül pydantic'i doğrudan import etmiyor (K2). Düz
    #: sözlük pydantic için yeterli; işaret yalnız "bu bir alan değil" der.
    model_config: ClassVar[dict[str, Any]] = {"extra": "forbid"}

    service_date: str = Field(min_length=10, max_length=10)
    delivery_type: str = Field(min_length=1, max_length=16)
    payment_method: str = Field(min_length=1, max_length=16)
    #: TAM LİSTE, tek tek eklenmez. Kalemler OLDUĞU GİBİ geçer ve ayıklanmaz:
    #: `option_value_ids` düşseydi "ekstra peynir" silinir, sipariş ucuzlar,
    #: mutfak yanlış yemeği yapardı.
    items: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_ITEMS)

    #: Müşteri: ya kayıtlı kimlik ya yeni kayıt. İKİSİ BİRDEN gönderilirse
    #: servis reddeder — sunucu `customer_id` doluysa `customer` nesnesini
    #: sessizce yok sayar ve sipariş BAŞKA bir hesaba yazılırdı.
    customer_id: int = Field(default=0, ge=0)
    customer: NewCustomerBody | None = None

    address: AddressBody | None = None
    customer_note: str = Field(default="", max_length=MAX_CUSTOMER_NOTE)
    #: Verilmezse etkin vitrin kullanılır (sözleşme).
    location_id: int = Field(default=0, ge=0)
    reason: str = Field(default="", max_length=MAX_REASON)
    dryRun: bool | None = None

    #: ANLAŞMALI SEPET FİYATI — kuruş, SEPETİN TAMAMI için, kalem başına DEĞİL.
    #: `None` = anlaşma yok; sunucu tutarı katalogdan hesaplar (bugünkü
    #: davranış) ve adrese teslimde teslimat ücretini ekler. Doluyken tutar
    #: kalem toplamının yerine geçer, kalemler fiyatsız yazılır ve teslimat
    #: ücreti bu tutara DÂHİL sayılır.
    #:
    #: ŞEMADA `ge`/`le` YOK ve bu bilinçli: sıfır ya da tavan aşımı pydantic'in
    #: İngilizce şema hatasına düşerdi. Aralığı servis denetliyor ve personele
    #: ne yapacağını söyleyen Türkçe bir cümle döndürüyor ("anlaşma yoksa alanı
    #: BOŞ bırakın"). Tip yine de `int`: "400,00" gibi bir metin şema kapısında
    #: kalır ve sessizce 400 kuruşa dönüşemez.
    agreed_total_kurus: int | None = None


@router.post("/orders")
async def create_order(
    body: CreateBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Siparişi açar. Sipariş `onaylandi` doğar ve mutfağa düşer.

    İzin kapısı burada ve serviste iki kez denetlenir (K9 — çift kapı).

    İKİ AYRI ANAHTAR: uca girmek `manage` ister; gövdede `agreed_total_kurus`
    doluysa AYRICA `price_override` istenir. İkincisi uç düzeyinde
    `requires(...)` ile İSTENMEZ, çünkü o alanı hiç kullanmayan personel
    (siparişlerin çoğu) ekrana giremez olurdu. Kapı gövdeye bakıyor ve karar
    serviste veriliyor.

    SİPARİŞ PENCERESİ BURADA DENETLENMEZ: sunucu onu `adminContext: true` ile
    bilerek atlıyor. Kesim saati ve stok tavanı ekranda YAZIYLA söylenir,
    engellenmez — engellemek, personelin telefonda verdiği insan kararını
    sistemin ikinci kez sorgulaması olurdu.
    """
    return await service().create(
        service_date=body.service_date,
        delivery_type=body.delivery_type,
        payment_method=body.payment_method,
        items=body.items,
        actor=user.full_name,
        customer_id=body.customer_id,
        customer_name=body.customer.name if body.customer else "",
        customer_phone=body.customer.phone if body.customer else "",
        address=body.address.model_dump() if body.address else None,
        customer_note=body.customer_note,
        reason=body.reason,
        location_id=body.location_id,
        dry_run=body.dryRun,
        agreed_total_kurus=body.agreed_total_kurus,
        allow_manage=user.has_permission(MANAGE),
        allow_price_override=user.has_permission(PRICE_OVERRIDE),
    )
