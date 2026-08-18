"""Abonelikler — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde düğmeyi gizlemek yetkilendirme
değildir. `module.yaml` → `http.requires` taban izni verir, uçlar onu DARALTIR.

ÜÇ İZİN — ESKİDEN İKİYDİ (I4):

    bld_subscriptions.view     okuma — talepler, abonelikler, takvim, üretim
                               defteri, sözleşmeler, ödemeler, yerel iz
    bld_subscriptions.manage   yazmalar — İPTAL VE TAHSİLAT HARİÇ
    bld_subscriptions.cancel   YIKICI: abonelik iptali + `mark-paid`

Üçüncüsü kardeş modüllerdeki `bld_orders.cancel`, `bld_customers.disable` ve
`bld_invoices.void` ile aynı çizgide. Eski gerekçe "abonelik iptali para
üretmiyor" diyordu; yarısı doğruydu (iade kaydı çıkmıyor) ama diğer yarısı
atlanmıştı: İPTAL EDİLMİŞ ABONELİK GERİ AÇILAMAZ — aktifleştirilemez,
ödemeyle canlandırılamaz ve yeniden başlatmak yeni abonelik açmaktır.

`mark-paid` de aynı anahtarda ve o daha ağır: GERÇEK PARA HAREKETİ ve geri
alma ucu YOK (sözleşme: "para defterinde silme yoktur").

İKİ KAPI ÜST ÜSTE, BİRİ ÖTEKİNİN YERİNE GEÇMİYOR:
  · GEREKÇE (ADR 0012) — "neden yapıldı" sorusunu cevaplar, denetim izine
    yazılır, en az 10 karakter ve backend'de DE doğrulanır,
  · PIN (`confirm_pin`) — "klavyenin başındaki kişi gerçekten o mu" sorusunu
    cevaplar. Oturum açık bırakılmış bir makinede izin tek başına hiçbir şey
    kanıtlamıyor.
Bu yüzden `bld_subscriptions.cancel` `destructive: true` taşıyor; diğer iki
anahtar TAŞIMIYOR ve taşımamalı.

ROTA SIRASI KIRILGAN — SÖZLEŞMEDEKİ TUZAĞIN AYNISI. Sunucuda sabit parçalı
yollar (`requests`, `contracts`, `payments`, `orders`) `{subscription}`
ÖNÜNDE kaydedilmezse birer kimlik sanılır ve `404` döner. Burada da aynı sıra
uygulanır ve dosyanın bölüm başlıkları o sırayı korur. FastAPI'de `int` tipli
bir yol parametresi "orders" metnini eşleştiremeyip 422 verirdi — yani hata
"uç yok" demezdi, "kimlik sayı değil" derdi ve teşhis edilemezdi.

METOT ADLARI BU DOSYADA SABİTLENİR, servis ona uyar. Rota ile denetleyici metot
adının ayrışması ne açılışta ne `route:list`te hata verir; yalnız uç
çağrılınca patlar (Faz 1'de 13 test bu yüzden düşmüştü).

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran
mesajı gösterir. 4xx yalnız izin ve şema kapısından çıkar.
"""

from __future__ import annotations

from typing import Any, ClassVar

from fastapi import Request

from km_sdk import (
    APIRouter,
    BaseModel,
    CurrentUser,
    Field,
    HTTPException,
    Query,
    confirm_pin,
    requires,
)

from ..service import SubscriptionsService
from ..subs import (
    MAX_CALENDAR_DAYS,
    MAX_EXPIRES_DAYS,
    MAX_REASON,
    MIN_EXPIRES_DAYS,
    MIN_REASON,
)

#: İzin anahtarları tek yerde durur: uçlar ve testler aynı dizeyi okur, yazım
#: hatası bir kapıyı sessizce açık bırakamaz.
VIEW = "bld_subscriptions.view"
MANAGE = "bld_subscriptions.manage"
CANCEL = "bld_subscriptions.cancel"

router = APIRouter()
_service: SubscriptionsService | None = None


def bind(service: SubscriptionsService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> SubscriptionsService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


# ============================================================ ortak gövdeler

class ReasonBody(BaseModel):
    """Her yazma gövdesinin ortak iki alanı.

    `actor` GÖVDEDEN ALINMAZ — oturumdan gelir. İstemcinin aktör adını
    yazabilmesi, denetim izini imzalanmamış bir deftere çevirirdi: silinmeyen
    bir satıra istediği adı yazan biri, işi başkasının üstüne bırakabilirdi.

    `dryRun` camelCase'tir ve TEK KABUL EDİLEN addır. Panel→Kontrol Merkezi
    sınırında camelCase geçerli; tele giden `dry_run` adına çeviriyi geçit
    yapar. `dry_run` da kabul edilseydi bir yazım hatası ("dryrun", "dry_Run")
    sessizce düşer, alan hiç gönderilmemiş sayılır ve varsayılana dönerdi —
    yani kuru prova sanılan bir istek GERÇEK YAZMA yapabilirdi.

    Gerekçe sınırı 10–500'dür, `bld_orders`taki 160 DEĞİL: o daralma
    `veykemtu_order_revisions.reason` sütunundan geliyor ve abonelik yazmaları
    o sütuna hiç dokunmuyor (`00-genel.md` §3 genel sınırı 500).
    """

    #: `ClassVar` ile işaretli, çünkü `km_sdk` pydantic'in `ConfigDict` tipini
    #: dışa vurmuyor ve modül pydantic'i doğrudan import etmiyor (K2).
    model_config: ClassVar[dict[str, Any]] = {"extra": "forbid"}

    reason: str = Field(min_length=MIN_REASON, max_length=MAX_REASON)
    dryRun: bool | None = None


class SubscriptionBlock(BaseModel):
    """Abonelik kuralının gövdesi — `POST /subscriptions` ve `convert` ORTAK.

    İkisi aynı alanları taşımak ZORUNDA: sözleşme talebi çevirirken bloğu
    `POST /` gövdesiyle aynı doğrulamadan geçiriyor. Ayrı iki gövde, talepten
    açılan aboneliğin elle açılandan farklı kurallara tabi olması demekti.

    `payment_mode` ALANI YOKTUR ve olmayacak: tek geçerli değer
    `prepaid_monthly` (iş kararı 1 — cari hesap kalktı). Seçilemeyen bir alanı
    gövdeye koymak, bir gün `account` yazılabileceği izlenimi verirdi; geçit
    de onu istek çıkmadan keser.
    """

    model_config: ClassVar[dict[str, Any]] = {"extra": "forbid"}

    start_date: str = Field(default="", max_length=10)
    end_date: str = Field(default="", max_length=10)
    delivery_type: str = Field(default="delivery", max_length=16)
    delivery_time_from: str = Field(default="", max_length=5)
    delivery_time_to: str = Field(default="", max_length=5)
    service_days: list[int] = Field(default_factory=list)
    menu_mode: str = Field(default="daily_menu", max_length=16)
    default_quantity: int = Field(default=0, ge=0, le=100000)
    #: `None` = fiyat henüz anlaşılmadı. Sıfır DEĞİL: sıfır fiyatlı bir
    #: abonelik sipariş üretir ve tutarı sıfır olurdu.
    agreed_unit_price_kurus: int | None = Field(default=None, ge=0)
    lines: list[dict[str, Any]] = Field(default_factory=list)
    delivery_points: list[dict[str, Any]] = Field(default_factory=list)
    location_id: int = Field(default=0, ge=0)


# ================================================================== okuma

@router.get("/overview")
async def overview(
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Ekranın sözleşmesi ve kullanıcının tercihleri. AĞA ÇIKMAZ (K7)."""
    return await service().overview()


@router.get("/locations")
async def locations(
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Şube listesi — abonelik formundaki seçicinin kaynağı.

    `view` YETER: liste bir iş verisi değil, formu çizebilmek için gereken
    referans. Yazma yetkisi olmayan personelin de aboneliğin hangi şubeye
    bağlı olduğunu görmesi gerekiyor.
    """
    return await service().locations()


@router.get("/audit")
async def audit(
    target_type: str = Query("", max_length=32),
    target_id: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Bu ekrandan yapılan yazma denemelerinin YEREL izi — sunucununki değil.

    `target_type`/`target_id` ile süzülür: abonelik çekmecesi kendi fiyat
    geçmişini burada okur ("fiyatı kim, ne zaman, neden anlaştı"). Süzgeçsiz
    çağrı bütün ekranın izini verir.
    """
    return await service().audit(target_type=target_type, target_id=target_id,
                                 limit=limit)


# ------------------------------------------------------------------ talepler
#
# SABİT PARÇALI YOLLAR ÖNCE. `/requests` bir abonelik kimliği sanılmamalı.

@router.get("/requests")
async def requests(
    status: str = Query("", max_length=80),
    q: str = Query("", max_length=120),
    date_from: str = Query("", max_length=10),
    date_to: str = Query("", max_length=10),
    page: int = Query(1, ge=1),
    per_page: int = Query(0, ge=0, le=100),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Teklif talepleri — İLETİŞİM BİLGİSİ MASKELİ (maskeyi sunucu uygular)."""
    return await service().requests(status=status, q=q, date_from=date_from,
                                    date_to=date_to, page=page, per_page=per_page)


@router.get("/requests/{request_id}")
async def request_detail(
    request_id: int,
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Tek talep, MASKESİZ. Arayacak kişinin numaraya ihtiyacı var."""
    return await service().request(request_id)


class RequestPatchBody(ReasonBody):
    #: Yazılabilen YALNIZ bu ikisi. Ziyaretçinin yazdığı içerik değiştirilemez:
    #: bir kaydın içeriğini düzeltebilen panel, o kaydın delil değerini yok eder.
    status: str = Field(default="", max_length=16)
    admin_note: str | None = Field(default=None, max_length=2000)


@router.patch("/requests/{request_id}")
async def update_request(
    request_id: int,
    body: RequestPatchBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    return await service().update_request(
        request_id, reason=body.reason, actor=user.full_name, status=body.status,
        admin_note=body.admin_note, dry_run=body.dryRun)


class ConvertBody(ReasonBody):
    #: ZORUNLU ve bu uç müşteri YARATMAZ: hesap açmak parola ve e-posta
    #: doğrulaması ister, ikisi de bu sözleşmenin dışında. Yönetici müşteriyi
    #: önce oluşturur (TastyIgniter admin) ya da mevcut kaydı seçer.
    customer_id: int = Field(ge=1)
    subscription: SubscriptionBlock


@router.post("/requests/{request_id}/convert")
async def convert_request(
    request_id: int,
    body: ConvertBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Talebi aboneliğe çevirir. Talep SİLİNMEZ, abonelik `pending` DOĞAR."""
    return await service().convert_request(
        request_id, customer_id=body.customer_id,
        subscription=body.subscription.model_dump(), reason=body.reason,
        actor=user.full_name, dry_run=body.dryRun)


# --------------------------------------------------------------- sözleşmeler
#
# `/contracts/{id}` de sabit parçalıdır ve `{subscription_id}` önünde durur.

@router.get("/contracts/{contract_id}")
async def contract_detail(
    contract_id: int,
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Tek sözleşme. `token` ve imza bağlantısı DÖNMEZ."""
    return await service().contract(contract_id)


class ResendBody(ReasonBody):
    #: SÜREYİ TAZELE. Varsayılan `False` ve bu, "yeni token üretilmez" sözünün
    #: tek gerçek karşılığı: süre değiştiği anda belirteç yeniden türetiliyor
    #: ve müşterinin elindeki SMS ölüyor. Alan `True` gönderildiğinde ekran
    #: kullanıcıya bunu açıkça sormuş olmalıdır.
    renew: bool = False
    #: 0 = tercihteki varsayılan. YALNIZ `renew=True` iken bakılır; sunucu
    #: sınırı 1–30 gün.
    expires_in_days: int = Field(default=0, ge=0, le=MAX_EXPIRES_DAYS)


@router.post("/contracts/{contract_id}/resend")
async def resend_contract(
    contract_id: int,
    body: ResendBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Aynı bağlantıyı yeniden gönderir.

    VARSAYILAN OLARAK TOKEN KORUNUR (`renew=False`): müşterinin elindeki eski
    SMS çalışmaya devam eder. `renew=True` yeni bir bağlantı üretir ve
    ESKİSİNİ ÖLDÜRÜR — yanıttaki `renews_link` bunu doğrular.

    Süresi dolmuş bağlantıda sunucu süreyi ZORUNLU olarak tazeler; ölü bir
    bağlantıyı yeniden göndermenin anlamı yok.
    """
    return await service().resend_contract(
        contract_id, reason=body.reason, actor=user.full_name, renew=body.renew,
        expires_in_days=body.expires_in_days, dry_run=body.dryRun)


@router.post("/contracts/{contract_id}/cancel")
async def cancel_contract(
    contract_id: int,
    body: ReasonBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Sözleşmeyi iptal eder. İMZALANMIŞ sözleşmede sunucu `409` verir."""
    return await service().cancel_contract(
        contract_id, reason=body.reason, actor=user.full_name, dry_run=body.dryRun)


# ------------------------------------------------------------------ ödemeler

class MarkPaidBody(ReasonBody):
    #: ZORUNLU: `online` ya da `cash` (iş kararı 1).
    method: str = Field(min_length=1, max_length=16)
    #: Boşsa sunucu şimdiyi yazar. Gelecek bir an reddedilir.
    paid_at: str = Field(default="", max_length=32)
    reference: str = Field(default="", max_length=120)
    #: Varsayılan KAPALI: fatura yazdırılabilir bir belgedir ve her tahsilatta
    #: üretmek gereksiz (sözleşme).
    create_invoice: bool = False
    #: Yayınlanan olayın yükü için; ödeme kaydı aboneliğe bağlı ama uç
    #: `payments/{id}` altında ve gövde bunu taşımazsa olay hangi aboneliğin
    #: borcunun kapandığını söyleyemezdi.
    subscription_id: int = Field(default=0, ge=0)
    #: PIN GÖVDEDE TAŞINIR — bu bir para hareketi ve geri alma ucu yok.
    pin: str = Field(default="", min_length=4, max_length=32)


@router.post("/payments/{payment_id}/mark-paid")
async def mark_paid(
    payment_id: int,
    body: MarkPaidBody,
    request: Request,
    user: CurrentUser = requires(CANCEL),
) -> dict[str, Any]:
    """Tahsil edildi işaretler. GERİ ALMA UCU YOKTUR — para defterinde silme yok.

    PIN TEYİDİ ŞART. Bu bir para hareketidir ve yanlış işaretlenen bir tahsilat
    ancak yeni bir dönem kaydıyla düzeltilebilir; oturumu açık bırakılmış bir
    makinede tek tıkla yapılabilmesi kabul edilemezdi.
    """
    await confirm_pin(request, user, body.pin, action="bld_subscriptions.payment.paid")
    return await service().mark_paid(
        payment_id, method=body.method, reason=body.reason, actor=user.full_name,
        paid_at=body.paid_at, reference=body.reference,
        create_invoice=body.create_invoice, subscription_id=body.subscription_id,
        dry_run=body.dryRun)


# ---------------------------------------------------------- erken serbest bırakma

@router.post("/orders/{order_id}/release")
async def release_order(
    order_id: int,
    body: ReasonBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Üretilmiş siparişi 07:00'ı beklemeden mutfağa açar.

    Denetim hedefi SİPARİŞTİR, abonelik değil: soru "bu sipariş neden erken
    düştü" biçiminde sorulur (`subscriptions.md` denetim tablosu).
    """
    return await service().release_order(
        order_id, reason=body.reason, actor=user.full_name, dry_run=body.dryRun)


# ================================================== abonelik — kimlikli yollar
#
# BURADAN SONRASI `{subscription_id}` TAŞIR. Yukarıdaki sabit parçalı yollar
# bilerek önce kaydedildi.

@router.get("/subscriptions")
async def subscriptions(
    status: str = Query("", max_length=80),
    customer_id: int = Query(0, ge=0),
    q: str = Query("", max_length=120),
    service_day: int = Query(0, ge=0, le=7),
    active_on: str = Query("", max_length=10),
    page: int = Query(1, ge=1),
    per_page: int = Query(0, ge=0, le=100),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Sayfalı abonelik listesi.

    Süzgeçler sorgu dizesindedir (gövde değil): liste bir okumadır ve
    kopyalanabilir bir adresinin olması, "şu ekranı aç" demenin en kısa yolu.
    """
    return await service().subscriptions(
        status=status, customer_id=customer_id, q=q, service_day=service_day,
        active_on=active_on, page=page, per_page=per_page)


class CreateBody(ReasonBody):
    customer_id: int = Field(ge=1)
    subscription: SubscriptionBlock


@router.post("/subscriptions")
async def create_subscription(
    body: CreateBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Yeni abonelik — `pending` DOĞAR, aktifleştirme ayrı bir eylemdir."""
    block = body.subscription
    return await service().create(
        customer_id=body.customer_id, start_date=block.start_date,
        service_days=block.service_days, default_quantity=block.default_quantity,
        reason=body.reason, actor=user.full_name, delivery_type=block.delivery_type,
        menu_mode=block.menu_mode, end_date=block.end_date,
        delivery_time_from=block.delivery_time_from,
        delivery_time_to=block.delivery_time_to,
        agreed_unit_price_kurus=block.agreed_unit_price_kurus, lines=block.lines,
        delivery_points=block.delivery_points, location_id=block.location_id,
        dry_run=body.dryRun)


@router.get("/subscriptions/{subscription_id}")
async def subscription_detail(
    subscription_id: int,
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Tek abonelik: alt kayıtları ve akış şeridiyle."""
    return await service().subscription(subscription_id)


class UpdateBody(ReasonBody):
    """KISMİ güncelleme. Gönderilmeyen alan DEĞİŞMEZ.

    `customer_id`, `location_id`, `start_date` ve `status` burada YOKTUR:
    müşteriyi değiştirmek yeni abonelik açmaktır ve durum kendi uçlarındadır.
    `None` = "bu alanı gönderme"; servis onları süzer.
    """

    model_config: ClassVar[dict[str, Any]] = {"extra": "forbid"}

    end_date: str | None = Field(default=None, max_length=10)
    delivery_type: str | None = Field(default=None, max_length=16)
    delivery_time_from: str | None = Field(default=None, max_length=5)
    delivery_time_to: str | None = Field(default=None, max_length=5)
    service_days: list[int] | None = None
    menu_mode: str | None = Field(default=None, max_length=16)
    default_quantity: int | None = Field(default=None, ge=1, le=100000)
    agreed_unit_price_kurus: int | None = Field(default=None, ge=0)
    #: TAM LİSTE: `id` taşıyan satır güncellenir, taşımayan eklenir, listede
    #: olmayan SİLİNİR (sözleşme).
    lines: list[dict[str, Any]] | None = None
    delivery_points: list[dict[str, Any]] | None = None


@router.patch("/subscriptions/{subscription_id}")
async def update_subscription(
    subscription_id: int,
    body: UpdateBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Kural güncelleme. ÜRETİLMİŞ SİPARİŞLERİ ETKİLEMEZ (yanıt `warnings` taşır)."""
    changes = {key: value for key, value in body.model_dump().items()
               if key not in ("reason", "dryRun") and value is not None}
    return await service().update(subscription_id, reason=body.reason,
                                  actor=user.full_name, changes=changes,
                                  dry_run=body.dryRun)


@router.post("/subscriptions/{subscription_id}/activate")
async def activate_subscription(
    subscription_id: int,
    body: ReasonBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Aktifleştirir. Fiyat ve İMZALI SÖZLEŞME şartını SUNUCU denetler."""
    return await service().activate(subscription_id, reason=body.reason,
                                    actor=user.full_name, dry_run=body.dryRun)


class PauseBody(ReasonBody):
    #: İKİSİ DE ZORUNLU. Süresiz duraklatma iptalin adı konmamış hâlidir ve
    #: iptalin kendi ucu var; `end_date` boş geçilemez.
    start_date: str = Field(min_length=10, max_length=10)
    end_date: str = Field(min_length=10, max_length=10)
    #: Duraklama satırına yazılan kısa etiket ("Kurum tatili"). Denetim
    #: gerekçesinden AYRIDIR: biri kaydın kendisinde, öbürü denetim izinde.
    pause_reason: str = Field(default="", max_length=255)


@router.post("/subscriptions/{subscription_id}/pause")
async def pause_subscription(
    subscription_id: int,
    body: PauseBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Aralıklı duraklatma. Aralıktaki siparişler OTOMATİK İPTAL EDİLMEZ."""
    return await service().pause(
        subscription_id, start_date=body.start_date, end_date=body.end_date,
        reason=body.reason, actor=user.full_name, pause_reason=body.pause_reason,
        dry_run=body.dryRun)


@router.post("/subscriptions/{subscription_id}/resume")
async def resume_subscription(
    subscription_id: int,
    body: ReasonBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Duraklamayı bugün itibarıyla kapatır; satırı SİLMEZ."""
    return await service().resume(subscription_id, reason=body.reason,
                                  actor=user.full_name, dry_run=body.dryRun)


class CancelBody(ReasonBody):
    #: Bugünden geriye alınamaz (sunucu denetler). `end_date` bu güne yazılır.
    effective_date: str = Field(min_length=10, max_length=10)
    #: PIN GÖVDEDE TAŞINIR, sorgu dizesinde DEĞİL: sorgu dizesi denetim
    #: kaydına, sunucu günlüğüne ve tarayıcı geçmişine düşer (`bell` deseni).
    pin: str = Field(default="", min_length=4, max_length=32)


@router.post("/subscriptions/{subscription_id}/cancel")
async def cancel_subscription(
    subscription_id: int,
    body: CancelBody,
    request: Request,
    user: CurrentUser = requires(CANCEL),
) -> dict[str, Any]:
    """YIKICI: GERİ DÖNÜŞÜ YOK. Üretilmiş siparişleri DÜŞÜRMEZ.

    AYRI İZİN + PIN. İptal edilmiş abonelik aktifleştirilemez, ödemeyle geri
    açılamaz ve yeniden başlatmak yeni abonelik açmaktır — yani bu, abonelik
    alanındaki tek geri dönüşsüz işlem. Sonraki üretilmiş siparişleri düşürmek
    ayrıca `bld_orders.cancel` iznini ister ve iade orada doğar.
    """
    await confirm_pin(request, user, body.pin, action="bld_subscriptions.cancel")
    return await service().cancel(
        subscription_id, effective_date=body.effective_date, reason=body.reason,
        actor=user.full_name, dry_run=body.dryRun)


@router.get("/subscriptions/{subscription_id}/calendar")
async def calendar(
    subscription_id: int,
    date_from: str = Query("", max_length=10),
    days: int = Query(0, ge=0, le=MAX_CALENDAR_DAYS),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Önümüzdeki servis günleri. YALNIZ üretim yapılacak günler döner."""
    return await service().calendar(subscription_id, date_from=date_from, days=days)


class ExceptionBody(ReasonBody):
    service_date: str = Field(min_length=10, max_length=10)
    skip: bool = False
    #: `skip` ile birlikte GÖNDERİLEMEZ — "atla ama 12 yap" tutarsız.
    quantity_override: int | None = Field(default=None, ge=1, le=100000)
    note: str = Field(default="", max_length=255)


@router.post("/subscriptions/{subscription_id}/exceptions")
async def create_exception(
    subscription_id: int,
    body: ExceptionBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Tek-gün istisnası. Aynı gün için ikincisi ÜZERİNE YAZILIR (`409` yok)."""
    return await service().create_exception(
        subscription_id, service_date=body.service_date, reason=body.reason,
        actor=user.full_name, skip=body.skip, quantity_override=body.quantity_override,
        note=body.note, dry_run=body.dryRun)


@router.post("/subscriptions/{subscription_id}/exceptions/{service_date}/delete")
async def delete_exception(
    subscription_id: int,
    service_date: str,
    body: ReasonBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """İstisnayı kaldırır — GERÇEK SİLME (istisna belge değil, kuraldır).

    Fiil `POST`tur, `DELETE` değil ve bu bilinçli: gerekçe ZORUNLU ve gövdede
    taşınıyor. `DELETE` gövdesi ara katmanlarda (proxy, tarayıcı `fetch`)
    güvenilir biçimde taşınmıyor; gerekçesiz geçen bir silme, denetim izini
    boş bırakırdı. Sunucuya giden istek yine `DELETE`tir — çeviriyi geçit
    yapar ve sözleşme bozulmaz.
    """
    return await service().delete_exception(
        subscription_id, service_date, reason=body.reason, actor=user.full_name,
        dry_run=body.dryRun)


@router.get("/subscriptions/{subscription_id}/runs")
async def runs(
    subscription_id: int,
    date_from: str = Query("", max_length=10),
    date_to: str = Query("", max_length=10),
    page: int = Query(1, ge=1),
    per_page: int = Query(0, ge=0, le=100),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Üretim defteri. `order_id` boş satır = denendi, sipariş oluşmadı."""
    return await service().runs(subscription_id, date_from=date_from, date_to=date_to,
                                page=page, per_page=per_page)


class GenerateBody(ReasonBody):
    service_date: str = Field(min_length=10, max_length=10)
    #: Varsayılan KAPALI: normal serbest bırakma saati (07:00) geçerli kalır.
    #: Açmak, siparişi ANINDA mutfak panosuna düşürür.
    release_now: bool = False


@router.post("/subscriptions/{subscription_id}/generate")
async def generate(
    subscription_id: int,
    body: GenerateBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Gece işini beklemeden elle üretim. Kural gece işiyle AYNIDIR."""
    return await service().generate(
        subscription_id, service_date=body.service_date, reason=body.reason,
        actor=user.full_name, release_now=body.release_now, dry_run=body.dryRun)


@router.get("/subscriptions/{subscription_id}/contracts")
async def contracts(
    subscription_id: int,
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Aboneliğin sözleşmeleri. `terms_snapshot` imzalandığı andaki koşullardır."""
    return await service().contracts(subscription_id)


class ContractBody(ReasonBody):
    #: Boşsa müşterinin kayıtlı telefonu kullanılır; ikisi de yoksa sunucu 422.
    phone: str = Field(default="", max_length=32)
    #: 0 = tercihteki varsayılan (7). Sunucu sınırı 1–30 gün.
    expires_in_days: int = Field(default=0, ge=0, le=MAX_EXPIRES_DAYS)
    #: `False` iken kayıt `pending` kalır ve imza bağlantısı YANITTA döner
    #: (yönetici elden iletir). `True` iken SMS gider ve bağlantı DÖNMEZ.
    send_sms: bool = True


@router.post("/subscriptions/{subscription_id}/contracts")
async def create_contract(
    subscription_id: int,
    body: ContractBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Sözleşme oluşturur ve imza bağlantısını gönderir.

    OTP AKIŞI BURADA YOKTUR (K3): müşteri kodu SUNUCUNUN sayfasında girer,
    Kontrol Merkezi yalnız tetikler. Açık bir sözleşme varken ikincisi
    açılamaz (sunucu `409`) — iki geçerli bağlantı, hangisinin imzalandığını
    belirsiz kılardı.
    """
    return await service().create_contract(
        subscription_id, reason=body.reason, actor=user.full_name, phone=body.phone,
        expires_in_days=body.expires_in_days, send_sms=body.send_sms,
        dry_run=body.dryRun)


@router.get("/subscriptions/{subscription_id}/payments")
async def payments(
    subscription_id: int,
    status: str = Query("", max_length=32),
    date_from: str = Query("", max_length=10),
    date_to: str = Query("", max_length=10),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Dönem ödemeleri — SAYFALANMAZ. `overdue` sunucuda hesaplanır."""
    return await service().payments(subscription_id, status=status, date_from=date_from,
                                    date_to=date_to)


class PaymentBody(ReasonBody):
    period_start: str = Field(min_length=10, max_length=10)
    period_end: str = Field(min_length=10, max_length=10)
    due_date: str = Field(min_length=10, max_length=10)
    #: `None` = SUNUCU HESAPLASIN (dönemdeki üretilmiş, iptal edilmemiş
    #: siparişlerin toplamı). Elle yazmak serbest ama varsayılan hesaplanmış
    #: olmalı — yönetici her ay çarpma yapmamalı.
    amount_kurus: int | None = Field(default=None, ge=0)
    note: str = Field(default="", max_length=255)


@router.post("/subscriptions/{subscription_id}/payments")
async def create_payment(
    subscription_id: int,
    body: PaymentBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Dönem borcu açar. Kuru prova hesabı GERÇEKTEN yapar ve `order_count` döner."""
    return await service().create_payment(
        subscription_id, period_start=body.period_start, period_end=body.period_end,
        due_date=body.due_date, reason=body.reason, actor=user.full_name,
        amount_kurus=body.amount_kurus, note=body.note, dry_run=body.dryRun)


# ============================================================ ekran tercihi

class PrefsBody(BaseModel):
    """Görüntüleme tercihi. BLD'ye HİÇBİR ŞEY GİTMEZ.

    `bld_subscriptions.view` YETER ve gerekçe İSTENMEZ: sayfa boyutunu
    değiştirmek bir iş yazması değildir. `manage` istemek, gerçek
    yazmalardaki izin ayrımını anlamsızlaştırırdı.

    `expires_in_days` bir GÖNDERİM VARSAYILANIDIR: sözleşme gönderilirken
    kutuya önceden yazılır ama sunucuya giden değeri kullanıcı o an onaylar.
    Sınırı (1–30) burada da, serviste de, sunucuda da denetlenir.
    """

    model_config: ClassVar[dict[str, Any]] = {"extra": "forbid"}

    page_size: int | None = Field(default=None, ge=5, le=100)
    calendar_days: int | None = Field(default=None, ge=7, le=MAX_CALENDAR_DAYS)
    expires_in_days: int | None = Field(default=None, ge=MIN_EXPIRES_DAYS,
                                        le=MAX_EXPIRES_DAYS)


@router.put("/prefs")
async def save_prefs(
    body: PrefsBody,
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    values = {key: value for key, value in body.model_dump().items() if value is not None}
    if not values:
        # Boş gövde hiçbir şey değiştirmeden bir satır yazardı.
        return {"ok": False, "error": "Kaydedilecek bir tercih gönderilmedi."}
    return await service().save_prefs(values, actor=user.full_name)
