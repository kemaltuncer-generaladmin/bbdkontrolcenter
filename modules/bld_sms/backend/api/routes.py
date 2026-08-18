"""SMS Paneli — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde düğmeyi gizlemek yetkilendirme
değildir. `module.yaml` → `http.requires` taban izni verir, uçlar onu DARALTIR.

ÜÇ İZİN, İKİ DEĞİL:

    bld_sms.view      şablonlar, tetikleyici durumları, gönderim kaydı, duyuru
                      taslağı, YEREL ölçüm ve SUNUCU önizlemesi
    bld_sms.manage    şablon metnini yazma, bir bildirimi açma/kapatma, tek
                      numaraya `[DENEME]` SMS'i, duyuru TASLAĞINI yazma
    bld_sms.announce  TOPLU DUYURUYU GÖNDERME

Üçüncüsünün ayrı durmasının nedeni şudur: bu yetkiyi taşıyan kişi tek istekle
yüzlerce müşteriye SMS yollar. Mesaj geri alınamaz, her segment faturalanır ve
yanlış bir metin spam şikâyeti ile numara kaybı demektir. Şablon metnini
düzeltmek ile yüzlerce SMS göndermek aynı yetki olamaz.

`POST /announcement/run` İKİ İZNİ BİRDEN KABUL EDER (`requires` "en az biri"
demektir) ve hangi dalın hangisine düştüğüne GÖVDEYE bakarak karar verir: kuru
prova `manage` ile yapılabilir, gerçek gönderim `announce` ister. Kararı servise
`allow_send` olarak taşır ve servis onu AYRICA denetler (çift kapı, K9). Provayı
ayrı bir uca koymak, "prova yapılmadan gönderilemez" kuralını iki uca dağıtıp
ikisinin arasında kaybederdi.

`POST /measure` YEREL bir hesaptır: ağa çıkmaz, denetim satırı yazmaz, gerekçe
istemez. Bu yüzden `view` yeter ve bu yüzden gövdesinde `reason` alanı YOKTUR —
zorunlu bir gerekçe, her tuş vuruşunda cümle yazdırırdı.

`POST /templates/{key}/preview` ise SUNUCUYA gider ve orada bir denetim satırı
düşer; sözleşme onu `view` izniyle veriyor (hiçbir SMS göndermez) ama yazma
kabuğundan geçtiği için gerekçe ister. İkisi ayrı uçtur ve panel ikisini ayrı
düğme olarak gösterir.

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran mesajı
gösterir. 4xx yalnız izin ve şema kapısından çıkar.
"""

from __future__ import annotations

from typing import Any, ClassVar

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..service import SmsService
from ..text import BODY_MAX, HEADER_MAX, MAX_REASON, MIN_REASON

#: İzin anahtarları tek yerde durur: uç noktalar ve servis aynı dizeyi okur,
#: yazım hatası bir kapıyı sessizce açık bırakamaz.
VIEW = "bld_sms.view"
MANAGE = "bld_sms.manage"
ANNOUNCE = "bld_sms.announce"

router = APIRouter()
_service: SmsService | None = None


def bind(service: SmsService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> SmsService:
    if _service is None:  # pragma: no cover — yükleme sırası garanti eder
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


class ReasonBody(BaseModel):
    """Her yazma gövdesinin ortak iki alanı.

    `actor` GÖVDEDEN ALINMAZ — oturumdan gelir. İstemcinin aktör adını
    yazabilmesi, denetim izini imzalanmamış bir deftere çevirirdi: silinmeyen
    bir satıra istediği adı yazan biri, işi başkasının üstüne bırakabilirdi.

    `dryRun` camelCase'tir ve TEK KABUL EDİLEN addır. Panel→Kontrol Merkezi
    sınırında camelCase geçerli; tele giden `dry_run` adına çeviriyi geçit
    yapar. `dry_run` da kabul edilseydi bir yazım hatası ("dryrun", "dry_Run")
    sessizce düşer, alan hiç gönderilmemiş sayılır ve modül ayarındaki
    varsayılana dönerdi — kuru prova sanılan bir istek gerçek yazma yapabilirdi.
    Tek ad + `extra="forbid"` sayesinde yanlış yazılan alan 422 ile geri döner.

    `None` kalan alan için varsayılanı SERVİS uygular ve o KAPALI'dır. Panel
    bayrağı her yazmada açıkça gönderir; alan yine de isteğe bağlı bırakıldı
    çünkü gövdeyi elle kuran bir çağrının sessizce provaya düşmesi değil,
    bilinen bir varsayılana düşmesi gerekir.
    """

    #: `ClassVar` ile işaretli, çünkü `km_sdk` pydantic'in `ConfigDict` tipini
    #: dışa vurmuyor ve modül pydantic'i doğrudan import etmiyor (K2). Düz
    #: sözlük pydantic için yeterli; işaret yalnız "bu bir alan değil" der.
    model_config: ClassVar[dict[str, Any]] = {"extra": "forbid"}

    reason: str = Field(min_length=MIN_REASON, max_length=MAX_REASON)
    dryRun: bool | None = None


# ================================================================== okuma

@router.get("/templates")
async def templates(
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Panel açılışı: şablonlar, öbekler, yerel politika ve sağlayıcı durumu.

    Şablonlar ve Tetikleyiciler sekmelerinin ikisi de bu TEK yanıtla çizilir;
    iki sekme için iki istek atmak paylaşılan 3000/saat bütçesini iki katına
    çıkarırdı.
    """
    return await service().templates()


@router.get("/log")
async def log(
    phone: str = Query("", max_length=32),
    template_key: str = Query("", max_length=48),
    status: str = Query("", max_length=16),
    context: str = Query("", max_length=16),
    customer_id: int = Query(0, ge=0),
    date_from: str = Query("", max_length=10),
    date_to: str = Query("", max_length=10),
    page: int = Query(1, ge=1),
    per_page: int = Query(0, ge=0, le=200),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Gönderim kaydı (sayfalı). Telefon MASKELİ, gövde 120 karakterde kırpık.

    Silme ucu YOKTUR ve olmayacaktır: gönderim kaydı sağlayıcının panelinden
    bağımsız, bizim tarafımızdaki gerçektir.
    """
    return await service().log(phone=phone, template_key=template_key, status=status,
                               context=context, customer_id=customer_id,
                               date_from=date_from, date_to=date_to, page=page,
                               per_page=per_page)


@router.get("/announcement")
async def announcement(
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Duyuru taslağı + ALICI TAHMİNİ + bekleyen kuru prova.

    Tahmin her okumada sunucuda yeniden hesaplanır; ekran onu saklamaz.
    """
    return await service().announcement()


@router.get("/history")
async def history(
    limit: int = Query(50, ge=1, le=500),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Bu ekrandan yapılan yazma DENEMELERİ — yerel iz.

    BLD'nin denetim izini tekrar etmez: burada sunucuya ULAŞMAYAN denemeler de
    var ve "istek gitti mi" sorusunun tek cevabı o satırlardır.
    """
    return await service().history(limit=limit)


@router.get("/netgsm")
async def netgsm(
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Netgsm gönderici ayarı — başlık, kaynağı ve EKSİK ALANLAR.

    Okuma `view` ile açık: "SMS neden gitmiyor" sorusunun cevabı burada ve o
    soruyu soran herkesin ayarı düzeltme yetkisi olmak zorunda değil.

    PAROLA BU YANITTA YOKTUR. BLD onu hiçbir uçtan geri vermiyor; ekranın
    bilmesi gereken tek şey "dolu mu" ve o `missing` listesinde.
    """
    return await service().netgsm()


class NetgsmBody(ReasonBody):
    """Gönderici başlığı. `header` DIŞINDA ALAN YOKTUR ve olmayacak.

    Kullanıcı adı ve parola BLD'nin ortam değişkenlerinde yaşıyor ve oraya
    bir uçtan yazılamıyor (K8 ile aynı gerekçe): sır her veritabanı yedeğine
    girerdi. `extra="forbid"` sayesinde `password` göndermeye çalışan bir
    çağrı sessizce yok sayılmaz, 422 alır.
    """

    #: BOŞ DİZE AYARI SİLER ve BLD ortam değişkenine döner. `min_length`
    #: konulsaydı "geri al" yolu kapanır, panelden bir kez yazılan yanlış
    #: başlık oradaki doğru değeri kalıcı olarak gölgelerdi.
    header: str = Field(default="", max_length=HEADER_MAX)


@router.put("/netgsm")
async def set_netgsm(
    body: NetgsmBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Gönderici başlığını yazar. BLD'DEN GİDEN HER SMS BU ADLA GİDER.

    `announce` DEĞİL `manage` ister: başlığı düzeltmek gönderim yapmaz, para
    harcamaz ve geri alınabilir. Toplu duyurunun ayrı izne konulma gerekçesi
    (geri alınamaz, faturalanır) burada yok.
    """
    return await service().set_netgsm(header=body.header, reason=body.reason,
                                      actor=user.full_name, dry_run=body.dryRun)


class MeasureBody(BaseModel):
    """Yerel ölçüm gövdesi. `reason` YOKTUR — bu bir yazma değil, bir hesap."""

    model_config: ClassVar[dict[str, Any]] = {"extra": "forbid"}

    body: str = Field(default="", max_length=BODY_MAX)
    key: str = Field(default="", max_length=48)
    sample: dict[str, Any] = Field(default_factory=dict)
    #: Şablonun tanıdığı değişkenler (sunucudan gelen liste). Verilmezse
    #: kataloğun örnek anahtarları kullanılır — tanınmayan değişken uyarısı
    #: o zaman yalnızca bir tahmindir ve panel bunu yazar.
    allowed: list[str] | None = None


@router.post("/measure")
async def measure(
    body: MeasureBody,
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Karakter/segment sayacı ve canlı önizleme. AĞA ÇIKMAZ.

    Sunucudaki `preview` her çağrıda denetim satırı yazıyor ve geçidin dakikada
    18 istekle sınırlı TEK kovasından geçiyor; her tuş vuruşunda oraya gitmek
    hem kovayı boşaltır hem denetim izini kullanılamaz hâle getirirdi.
    """
    return service().measure(body=body.body, key=body.key, sample=body.sample,
                             allowed=body.allowed)


# ============================================================== şablonlar

class PreviewBody(ReasonBody):
    #: Verilmezse KAYITLI şablon işlenir; verilirse gönderilen taslak işlenir
    #: (kaydetmeden önce görmek için).
    body: str = Field(default="", max_length=BODY_MAX)
    #: Verilmezse sunucu GERÇEKÇİ örnek değerler üretir. Boş sözlük göndermek
    #: o davranışı kapatır ve önizleme "Sayın , siparişiniz…" diye görünürdü;
    #: bu yüzden `None` ile boş sözlük ayrı tutulur.
    sample: dict[str, Any] | None = None


@router.post("/templates/{key}/preview")
async def preview(
    key: str,
    body: PreviewBody,
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """SUNUCU önizlemesi. Hiçbir şey göndermez; bu uç ağa SMS çıkarmaz.

    Okuma gibi görünür ama POST'tur ve sözleşme de öyle tanımlar: örnek veri
    gövdeyle taşınıyor, gövdesiz bir GET onu sorgu dizesine — yani imzanın
    dışına — koyardı. Yazma kabuğundan geçtiği için gerekçe ister.
    """
    return await service().preview(key, body=body.body, sample=body.sample,
                                   reason=body.reason, actor=user.full_name,
                                   dry_run=body.dryRun)


class TemplateBody(ReasonBody):
    """KISMİ yazma: yalnız verilen alanlar değişir.

    `title` BİLEREK YOKTUR — şablonun adı sistemin kendi sözlüğüdür, yöneticinin
    değiştireceği bir şey değil. `extra="forbid"` sayesinde `title` göndermeye
    çalışan bir çağrı sessizce yok sayılmaz, 422 alır.
    """

    body: str | None = Field(default=None, max_length=BODY_MAX)
    #: `None` = dokunulmadı. `False` o bildirimi tamamen kapatır: gönderim
    #: denenmez ve kayda satır yazılmaz.
    enabled: bool | None = None


@router.patch("/templates/{key}")
async def update_template(
    key: str,
    body: TemplateBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Şablon metnini ve/veya durumunu yazar.

    BİR BİLDİRİMİ AÇMAK BİLİNÇLİ BİR HAREKETTİR: panel `enabled: true` çağrısını
    gerekçeli onaydan geçirir ve servis açılışı ayrıca kaydeder. Açık doğan ya
    da yanlışlıkla açılan bir şablon, tek dağıtımı binlerce SMS'e çevirir.
    """
    return await service().update_template(key, body=body.body, enabled=body.enabled,
                                           reason=body.reason, actor=user.full_name,
                                           dry_run=body.dryRun)


class TestBody(ReasonBody):
    phone: str = Field(min_length=10, max_length=20)
    #: Şablon VEYA serbest metin; ikisi birden `422`. Servis erken keser ki
    #: kullanıcı ham bir sunucu hatası yerine anlaşılır bir cümle görsün.
    template_key: str = Field(default="", max_length=48)
    body: str = Field(default="", max_length=BODY_MAX)
    sample: dict[str, Any] | None = None


@router.post("/send-test")
async def send_test(
    body: TestBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Tek numaraya `[DENEME]` SMS'i. Ön eki sunucu koyar ve kaldırılamaz.

    Sağlayıcı hata verirse yanıt yine `ok: true` olur ve `data.status`
    `"failed"` gelir: gönderim denemesi kayda geçti, isteğin kendisi başarısız
    değil. Ekran ikisini ayırmalı.
    """
    return await service().send_test(phone=body.phone, template_key=body.template_key,
                                     body=body.body, sample=body.sample,
                                     reason=body.reason, actor=user.full_name,
                                     dry_run=body.dryRun)


# ================================================================= duyuru

class AnnouncementBody(ReasonBody):
    body: str = Field(min_length=1, max_length=BODY_MAX)
    #: `active_customers` · `subscribers` · `all_customers`. Servis kümeye
    #: karşı doğrular; burada serbest bırakılması bilinçli, çünkü sözleşmeye
    #: yeni bir kitle eklendiğinde şema kapısı onu 422 ile düşürürdü.
    audience: str = Field(min_length=1, max_length=32)


@router.put("/announcement")
async def set_announcement(
    body: AnnouncementBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Duyuru TASLAĞINI yazar. GÖNDERMEZ.

    Metni yazmakla göndermeyi tek tuşta birleştirmek, yazım hatasını yüzlerce
    kişiye ulaştırırdı. Taslak değişince bekleyen kuru prova da düşer.
    """
    return await service().set_announcement(body=body.body, audience=body.audience,
                                            reason=body.reason, actor=user.full_name,
                                            dry_run=body.dryRun)


class RunBody(ReasonBody):
    #: Sunucunun o andaki hesabıyla BİREBİR eşleşmelidir; aksi hâlde 409.
    #: Kuru prova dalında yer tutucudur, gerçek sayıyı sunucu döndürür.
    confirm_recipients: int = Field(default=0, ge=0)
    #: Kuru provanın döndürdüğü jeton. GERÇEK gönderimde ZORUNLUDUR; provada
    #: hiç bakılmaz.
    token: str = Field(default="", max_length=64)


@router.post("/announcement/run")
async def run_announcement(
    body: RunBody,
    #: İKİ İZİN, "en az biri". Kuru prova `manage` ile yapılabilir; GERÇEK
    #: gönderim `bld_sms.announce` ŞARTTIR ve servis bunu ayrıca denetler
    #: (çift kapı, K9). Ucu yalnız `announce`e bağlamak, metni yazan ama
    #: göndermeyen personelin provayı bile görememesi olurdu — o zaman kuru
    #: prova bir emniyet değil, bir ayrıcalık olurdu.
    user: CurrentUser = requires(MANAGE, ANNOUNCE),
) -> dict[str, Any]:
    """Toplu duyuru. `dryRun: true` → prova (zorunlu ilk adım, jeton üretir).
    `dryRun: false` → gerçek gönderim; jetonsuz YAPILMAZ."""
    return await service().run_announcement(
        confirm_recipients=body.confirm_recipients, reason=body.reason,
        actor=user.full_name, dry_run=body.dryRun, token=body.token,
        allow_send=user.has_permission(ANNOUNCE))
