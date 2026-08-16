"""Satış Ayarları — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde düğmeyi gizlemek yetkilendirme
değildir. `module.yaml` → `http.requires` taban izni verir, uçlar onu DARALTIR.

ÜÇ İZİN, İKİ DEĞİL:

    bld_sales_settings.view       okuma
    bld_sales_settings.manage     satış KURALLARI: kesim saati, ileri gün
                                  sınırı, abonelik serbest bırakma saati,
                                  minimum sepet, teslimat ücreti, ödeme
                                  yöntemleri, yoğunluk anahtarı ve metni,
                                  süre alanları, günün menüsü rejimi,
                                  otomatik fatura, hızlı stok tavanları,
                                  ekran tercihi
    bld_sales_settings.ordering   SATIŞ KANALININ AÇIK/KAPALI olması: satışı
                                  durdurma ve açma, kapalı gün ekleme ve
                                  kaldırma

Üçüncüsünün ayrı durmasının nedeni şudur: `manage` yetkisi satış AÇIKKEN
geçerli kuralları değiştirir ve yanlış girilen bir kesim saati bir dakikada
düzeltilir. `ordering` yetkisi ise satışın olup olmayacağına karar verir ve
hatası GERİ ALINAMAZ — unutulan bir durdurma o günün bütün siparişlerini
kaybettirir, yanlış eklenen bir kapalı gün abonelik üretimini atlatır
(`SubscriptionGenerateCommand` o günü hiç işlemez) ve kaldırılan bir tatil
mutfağı resmî tatilde üretime sokar. Kaybedilen gün geri gelmez.

`busy` anahtarı BİLEREK `manage` altındadır: satışı KESMEZ, yalnız müşteriye
"hazırlanması uzun sürebilir" uyarısı gösterir. Onu `ordering` altına koymak,
mutfağın gün içinde yaptığı olağan bir işi en dar yetkinin arkasına saklamak
olurdu.

Ekran tercihi yazması `manage` ister çünkü tercih tablosu KULLANICI BAŞINA
DEĞİL, kurulum başınadır: bir kullanıcının seçimi ötekinin ekranını da
değiştirir.

YIKICI İŞLEM PIN DEĞİL GEREKÇE İSTER (ADR 0012): ayrı izin anahtarı + gerekçe
(en az 10 karakter, burada da serviste de doğrulanır) + iki denetim satırı
(`denendi` ve sonuç). Hiçbir izin `destructive: true` taşımaz.

KURU PROVA BAYRAĞI İKİ DEĞERLİDİR: `preview: bool = False`. `bld_kds`'teki
`dryRun: bool|None` + modül ayarı kalıbı burada KULLANILMADI — `None` dalı,
ayarın yanlış olduğu bir kurulumda her yazmayı sessizce provaya çevirirdi ve
bu ekranda o hata başarıdan ayırt edilemez. `extra="forbid"` sayesinde yanlış
yazılmış bir alan adı (`Preview`, `dry_run`) 422 ile geri döner; sessizce
düşüp varsayılana dönmez.

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran
mesajı gösterir. 4xx yalnız izin ve şema kapısından çıkar.
"""

from __future__ import annotations

from typing import Any, ClassVar

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..service import SalesSettingsService
from ..settings import (
    CLOSED_DAY_DESCRIPTION_MAX,
    CUSTOMER_MESSAGE_MAX,
    MAX_REASON,
    MIN_REASON,
)

#: İzin anahtarları tek yerde durur: uç noktalar ve testler aynı dizgeyi okur,
#: yazım hatası bir kapıyı sessizce açık bırakamaz.
VIEW = "bld_sales_settings.view"
MANAGE = "bld_sales_settings.manage"
ORDERING = "bld_sales_settings.ordering"

router = APIRouter()
_service: SalesSettingsService | None = None


def bind(service: SalesSettingsService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> SalesSettingsService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


class ReasonBody(BaseModel):
    """Her yazma gövdesinin ortak iki alanı.

    `actor` GÖVDEDEN ALINMAZ — oturumdan gelir. İstemcinin aktör adını
    yazabilmesi, denetim izini imzalanmamış bir deftere çevirirdi: silinmeyen
    bir satıra istediği adı yazan biri, işi başkasının üstüne bırakabilirdi.

    `preview` KURU PROVADIR ve İKİ DEĞERLİDİR. Verilmezse `False` — yani
    gerçek yazma. Üçüncü bir hâl (`None` + ayardan varsayılan) yok, çünkü o
    varsayılanın yanlış olduğu bir kurulumda ekran "kaydedildi" der ve
    sunucuda hiçbir şey değişmez. Önizleme AÇIK bir eylemdir: kullanıcı
    "Önizle" düğmesine basar, ekran değişiklik tablosunu gösterir ve hiçbir
    şeyin yazılmadığını YAZAR.
    """

    #: `ClassVar` ile işaretli, çünkü `km_sdk` pydantic'in `ConfigDict` tipini
    #: dışa vurmuyor ve modül pydantic'i doğrudan import etmiyor (K2).
    model_config: ClassVar[dict[str, Any]] = {"extra": "forbid"}

    reason: str = Field(min_length=MIN_REASON, max_length=MAX_REASON)
    preview: bool = False


# ================================================================== okuma


@router.get("/sales")
async def sales(
    baseline: bool = Query(True),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Satış ayarlarının tamamı + sunucunun bildirdiği varsayılanlar.

    Yanıt bir TABAN ÇİZGİSİ JETONU taşır (`baseline_token`); panel onu yazma
    isteğinde geri gönderir ve servis, yazılan alanların aradan değişip
    değişmediğini o jetona bakarak anlar.

    `baseline=false` YOKLAMA okumasıdır (panelin arka plan tazelemesi): jeton
    ÜRETİLMEZ. Her yoklamada yeni jeton üretmek, yarım saattir açık duran
    formun tabanını sessizce "şu an" hâline çeker ve yarış denetimini işlevsiz
    bırakırdı.
    """
    return await service().sales(baseline=baseline)


@router.get("/closed-days")
async def closed_days(
    date_from: str = Query("", max_length=10),
    date_to: str = Query("", max_length=10),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Kapalı gün listesi. Aralık verilmezse sunucu bugünden 365 gün döner."""
    return await service().closed_days(date_from=date_from, date_to=date_to)


@router.get("/stock")
async def stock(
    dates: str = Query("", max_length=64),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Hızlı stok şeridi. `dates` virgüllü; boşsa bugün ve yarın."""
    wanted = [piece.strip() for piece in dates.split(",") if piece.strip()]
    return await service().stock(dates=wanted)


@router.get("/audit")
async def audit(
    limit: int = Query(100, ge=1, le=500),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Bu ekrandan yapılan yazma DENEMELERİNİN yerel izi.

    Sunucudaki iz ayrı bir sorudur (`control/audit` alanı); buradaki satırlar
    sunucuya hiç ULAŞMAYAN denemeleri de taşır ve tek kanıt onlardır.
    """
    return await service().audit(limit=limit)


@router.get("/prefs")
async def prefs(
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Ekran tercihleri. BLD'yi ETKİLEMEZ."""
    return await service().prefs()


# ================================================================ satış kuralı


class SalesBody(ReasonBody):
    """Kısmi ayar yazması.

    Ayarlar `settings` altında YUVALIDIR, kökte değil: kökte olsalardı
    `reason` ve `preview` ile aynı ad alanını paylaşırlardı ve `reason` adında
    bir ayar eklenemezdi. `GET /sales` yanıtındaki `data` ile de simetrik.

    `token` kuru provanın değil, OKUMANIN jetonudur: formun açıldığı andaki
    ayar görüntüsünü işaret eder. Verilmezse yarış denetimi yapılamaz ve
    servis bunu sessizce geçer — eski bir istemciyi kırmak yerine, yarışı
    yakalayamadığını bilmek yeterlidir.
    """

    settings: dict[str, Any] = Field(default_factory=dict)
    token: str = Field(default="", max_length=64)


@router.put("/sales")
async def update_sales(
    body: SalesBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """13 satış ayarını KISMİ olarak yazar.

    `ordering_enabled` burada YAZILAMAZ (kendi uçları var), `is_open` ve
    `daily_package_menu_id` hiç yazılamaz. Üçünün de reddi serviste yapılır ve
    hangi alanın neden reddedildiği yazılır.
    """
    return await service().update_sales(settings=body.settings, reason=body.reason,
                                        actor=user.full_name, preview=body.preview,
                                        token=body.token)


class StockBody(ReasonBody):
    """Bir günün stok tavanları. **TAM LİSTE**, fark değil.

    `capacity_total` ve her kalemin `capacity` alanı `null` olabilir ve `null`
    SINIRSIZ demektir — sıfırdan farklıdır: sıfır "hiç satılmasın", `null`
    "tavan yok".
    """

    capacity_total: int | None = Field(default=None, ge=0)
    items: list[dict[str, Any]] = Field(default_factory=list)


@router.put("/stock/{date}")
async def set_stock(
    date: str,
    body: StockBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Günün ve kalemlerin tavanlarını yazar.

    Servis, yazmadan önce günün TAZE kalem listesini okur ve ekrandaki liste
    eksikse yazmayı reddeder: eksik gönderilen bir kalemin tavanı sunucuda
    sessizce kalkardı.
    """
    return await service().set_stock(date=date, capacity_total=body.capacity_total,
                                     items=body.items, reason=body.reason,
                                     actor=user.full_name, preview=body.preview)


class PrefBody(BaseModel):
    """Ekran tercihi. Gerekçe İSTEMEZ: BLD'ye hiç gitmez ve satışı etkilemez."""

    model_config: ClassVar[dict[str, Any]] = {"extra": "forbid"}

    key: str = Field(min_length=1, max_length=32)
    value: str = Field(default="", max_length=64)


@router.post("/prefs")
async def set_pref(
    body: PrefBody,
    #: `manage` — tercih tablosu KULLANICI BAŞINA DEĞİL kurulum başınadır ve
    #: bir kullanıcının seçimi ötekinin ekranını da değiştirir.
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    return await service().set_pref(key=body.key, value=body.value, actor=user.full_name)


# ================================================================ satış şalteri


class PauseBody(ReasonBody):
    """Satışı durdurma.

    `customer_message` MÜŞTERİYE gösterilir, `reason` GÖSTERİLMEZ — o denetim
    izi içindir. İkisinin ayrı alanlar olması bilinçlidir: "buzdolabı arızası"
    cümlesi müşteriye söylenecek şey değildir.

    `until` `null` ise durdurma SÜRESİZDİR (elle açılana kadar). Süre
    dolduğunda satış kendiliğinden açılır; arka planda bir iş yoktur, sunucu
    okuma anında karşılaştırır.
    """

    until: str | None = Field(default=None, max_length=32)
    customer_message: str | None = Field(default=None, max_length=CUSTOMER_MESSAGE_MAX)


@router.post("/ordering/pause")
async def pause_ordering(
    body: PauseBody,
    user: CurrentUser = requires(ORDERING),
) -> dict[str, Any]:
    """SATIŞI KESER. `busy` ile karıştırılmamalı — o yalnız uyarır."""
    return await service().pause(until=body.until, customer_message=body.customer_message,
                                 reason=body.reason, actor=user.full_name,
                                 preview=body.preview)


@router.post("/ordering/resume")
async def resume_ordering(
    body: ReasonBody,
    user: CurrentUser = requires(ORDERING),
) -> dict[str, Any]:
    """Satışı açar ve durdurma izlerini temizler. Zaten açıksa da `ok` döner."""
    return await service().resume(reason=body.reason, actor=user.full_name,
                                  preview=body.preview)


# ================================================================ kapalı günler


class ClosedDayBody(ReasonBody):
    date: str = Field(min_length=10, max_length=10)
    description: str | None = Field(default=None, max_length=CLOSED_DAY_DESCRIPTION_MAX)


@router.post("/closed-days")
async def create_closed_day(
    body: ClosedDayBody,
    user: CurrentUser = requires(ORDERING),
) -> dict[str, Any]:
    """Kapalı gün ekler. **Global**: bütün vitrinler o gün sipariş almaz."""
    return await service().add_closed_day(date=body.date, description=body.description,
                                          reason=body.reason, actor=user.full_name,
                                          preview=body.preview)


@router.delete("/closed-days/{date}")
async def delete_closed_day(
    date: str,
    body: ReasonBody,
    user: CurrentUser = requires(ORDERING),
) -> dict[str, Any]:
    """Kapalı günü kaldırır — YOL PARÇASI TARİHTİR, kimlik değil.

    Gerekçe GÖVDEDEDİR: sorgu dizesine konsaydı imzanın dışında kalırdı
    (`00-genel.md` §3). Gövdeli `DELETE` HTTP açısından alışılmadıktır ama
    sözleşmede bilinçlidir.
    """
    return await service().remove_closed_day(date=date, reason=body.reason,
                                             actor=user.full_name, preview=body.preview)
