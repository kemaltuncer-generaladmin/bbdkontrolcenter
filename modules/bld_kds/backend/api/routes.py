"""KDS Yönetimi — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde düğmeyi gizlemek yetkilendirme
değildir. `module.yaml` → `http.requires` taban izni verir, uçlar onu DARALTIR.

DÖRT İZİN, ÜÇ DEĞİL:

    bld_kds.view      okuma
    bld_kds.manage    cihaz açma/adlandırma, eşleme kodu, zararsız komutlar
                      (`test_receipt`, `reprint`, `silence_alarm`), revizyon,
                      durum değişikliği
    bld_kds.settings  yönetilen 24 ayarın kasaya yazılması
    bld_kds.devices   YIKICI: cihaz iptali, `restart`, `clear_failed`,
                      `clear_queue`, `update`, `unpair`

`bld_kds.settings` 17.08.2026'da `manage`ten AYRILDI (kullanıcı kararı). Aynı
anahtar iki farklı işi taşıyordu: mutfağın günlük işi (siparişi ilerletmek) ve
kasanın kendisini biçimlendirmek. İkincisi geri alınması merkeze bağlı bir
iştir — yanlış bir `printer_device_path` fiş basımını durdurur, bir kilit
anahtarını `false` yapmak kasadaki düğmeyi öldürür — ve mutfak personelinin
günlük işi değildir. Ayrım YENİ BİR ANAHTARLA yapıldı, `manage` daraltılarak
değil: yeni anahtar hiç kimseye verilmemiş doğar, eski anahtarı daraltmak ise
kurulu sistemde satır silmeyi gerektirirdi.

Sonuncusunun ayrı durmasının nedeni şudur: bu yetkiyi taşıyan kişi mutfağı
sipariş göremez hâle getirebilir. `restart` ekranı kapatır (systemd geri
getirene kadar mutfak kör kalır), `clear_failed` basılamamış fişleri kuyruktan
düşürür ve o fişler BİR DAHA basılmaz, `clear_queue` üstelik bekleyenleri de
düşürür, `update` kurulum boyunca ekranı alır, `unpair` kasanın token'ını siler
ve kasa sahada elle eşleştirilene kadar hiç sipariş göremez, iptal edilen kasa
bir daha bağlanamaz. BLD tarafında da ayrı bir yetki kutusu var
(`Veykemtu.KitchenDevices`); iki tarafın ayrımı aynı olmalı ki bir tarafta
kapalı olan kapı öbür taraftan açılmasın.

KOMUT UCU İKİ İZNİ BİRDEN KABUL EDER (`requires` "en az biri" demektir) ve
hangi komutun hangisine düştüğüne GÖVDEYE bakarak karar verir; kararı servise
`allow_destructive` olarak taşır. Uç noktayı yalnız `manage`e bağlayıp yıkıcı
komutu gövdeden geçirmek, izin ayrımını kâğıt üstünde bırakırdı.

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran mesajı
gösterir. 4xx yalnız izin ve şema kapısından çıkar.
"""

from __future__ import annotations

from typing import Any, ClassVar

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..devices import MAX_REASON, MIN_REASON
from ..service import KdsService

#: Yıkıcı işlemlerin izin anahtarı. Tek yerde durur: uç noktalar ve servis
#: aynı dizgeyi okur, yazım hatası bir kapıyı sessizce açık bırakamaz.
DEVICES = "bld_kds.devices"
MANAGE = "bld_kds.manage"
#: KASA AYARLARI. `manage`ten ayrıdır — bkz. modül başlığı.
SETTINGS = "bld_kds.settings"
VIEW = "bld_kds.view"

router = APIRouter()
_service: KdsService | None = None


def bind(service: KdsService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> KdsService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


class ReasonBody(BaseModel):
    """Her yazma gövdesinin ortak iki alanı.

    `actor` GÖVDEDEN ALINMAZ — oturumdan gelir. İstemcinin aktör adını
    yazabilmesi, denetim izini imzalanmamış bir deftere çevirirdi: silinmeyen
    bir satıra istediği adı yazan biri, işi başkasının üstüne bırakabilirdi.

    `dryRun` camelCase'tir ve TEK KABUL EDİLEN addır. Panel→Kontrol Merkezi
    sınırında `store_orders` deseni geçerli; tele giden `dry_run` adına çeviriyi
    geçit yapar. `dry_run` da kabul edilseydi, bir yazım hatası ("dryrun",
    "dry_Run") sessizce düşer ve alan hiç gönderilmemiş sayılırdı — bu da
    aşağıdaki `None` dalına düşerdi. Tek ad + `extra="forbid"` sayesinde
    yanlış yazılan alan 422 ile geri döner ve kimse kuru prova sandığı bir
    isteğin gerçek yazma yaptığını sonradan öğrenmez.

    ALAN DURUYOR, VARSAYILANI GİTTİ (K-22 §4). Kuru prova arayüzden kaldırıldı
    ve panel `dryRun` göndermiyor; `None` kalan alan için servis modül
    ayarındaki varsayılanı uygular ve o artık KAPALI. Alanın kendisi
    kaldırılmadı çünkü sözleşme §4 additive'dir: `extra="forbid"` yüzünden
    `dryRun` gönderen eski bir çağrı 422 ile kırılırdı.
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
    """Panel açılışı: cihaz sayıları, sipariş sayıları, fiş kaydı sayısı."""
    return await service().overview()


@router.get("/devices")
async def devices(
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Cihaz listesi + ayar formunun sözleşmesi + komut kataloğu.

    Yanıt ayrıca `can.settings` taşır: bu oturum ayar YAZABİLİR mi. Kapı bu
    değil — kapı `PATCH .../settings` ucundadır (K9'un backend yarısı); bu
    alan panelin ayar formunu salt okunur çizebilmesi içindir. Kabuğun panele
    verdiği bağlamda izin listesi YOKTUR (`ui-kernel.js` → `mountPanel`), yani
    panelin izni sorabileceği tek yer sunucunun kendisidir.
    """
    return await service().devices(can_settings=user.has_permission(SETTINGS))


@router.get("/devices/{device_id}/commands")
async def device_commands(
    device_id: int,
    limit: int = Query(0, ge=0, le=50),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    return await service().device_commands(device_id, limit=limit)


@router.get("/print-jobs")
async def print_jobs(
    device_id: int = Query(0, ge=0),
    order_id: int = Query(0, ge=0),
    limit: int = Query(0, ge=0, le=500),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """BASILMIŞ fişlerin denetim kaydı — bekleyen iş listesi DEĞİLDİR."""
    return await service().print_jobs(device_id=device_id, order_id=order_id, limit=limit)


@router.get("/orders")
async def orders(
    include_completed: bool = Query(False),
    since: str = Query("", max_length=32),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    return await service().orders(include_completed=include_completed, since=since)


@router.get("/orders/{order_id}")
async def order(
    order_id: int,
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    return await service().order(order_id)


@router.get("/orders/{order_id}/revisions")
async def order_revisions(
    order_id: int,
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    return await service().order_revisions(order_id)


@router.get("/menu")
async def menu(
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Revizyon ekranındaki "ürün ekle" seçicisinin listesi.

    `bld_kds.view` YETER: bu bir okumadır ve ürün adını görmek, revizyon
    yazabilmek demek değildir. Yazma kapısı `POST /orders/{id}/revisions`
    üzerindedir ve `bld_kds.manage` ister.

    Yanıt `{"data": [...]}` — geçitte karşılığı yoksa `ok: False` ve neden
    döner, ekran ürün kimliğini elle girmeye devam eder (K7).
    """
    return await service().menu()


# ================================================================== cihazlar

class DeviceBody(ReasonBody):
    name: str = Field(min_length=2, max_length=64)


@router.post("/devices")
async def create_device(
    body: DeviceBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Yeni cihaz açar; yanıtta ilk eşleme kodu döner."""
    return await service().create_device(name=body.name, reason=body.reason,
                                         actor=user.full_name, dry_run=body.dryRun)


@router.patch("/devices/{device_id}")
async def rename_device(
    device_id: int,
    body: DeviceBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """YALNIZ ad. Ayarlar ayrı uçtan geçer."""
    return await service().rename_device(device_id, name=body.name, reason=body.reason,
                                         actor=user.full_name, dry_run=body.dryRun)


@router.post("/devices/{device_id}/pairing-code")
async def pairing_code(
    device_id: int,
    body: ReasonBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Yeni eşleme kodu (10 dk). İptal edilmiş cihaza üretilmez — bkz. servis."""
    return await service().pairing_code(device_id, reason=body.reason,
                                        actor=user.full_name, dry_run=body.dryRun)


@router.post("/devices/{device_id}/revoke")
async def revoke_device(
    device_id: int,
    body: ReasonBody,
    user: CurrentUser = requires(DEVICES),
) -> dict[str, Any]:
    """YIKICI. Cihaz bir daha bağlanamaz; kaydı SİLİNMEZ.

    İzin kapısı burada ve serviste iki kez denetlenir (K9 — çift kapı).
    """
    return await service().revoke_device(device_id, reason=body.reason,
                                         actor=user.full_name, dry_run=body.dryRun,
                                         allow_destructive=user.has_permission(DEVICES))


class SettingsBody(ReasonBody):
    #: KISMİ yazma: yalnız gönderilen anahtarlar değişir. Bir anahtarın değerini
    #: `null` yapmak "yönetici dokunmadı" hâline döndürür ve kilidi KALDIRIR.
    settings: dict[str, Any] = Field(default_factory=dict)
    #: Kuru provanın döndürdüğü jeton. İsteğe bağlı; verilirse önizlemeden bu
    #: yana ayarların değişmediği doğrulanır.
    token: str = Field(default="", max_length=64)


@router.patch("/devices/{device_id}/settings")
async def push_settings(
    device_id: int,
    body: SettingsBody,
    #: AYRI İZİN (`bld_kds.settings`), `manage` DEĞİL. Kasa ayarı yazan TEK uç
    #: budur: `PATCH /devices/{id}` yalnız adı değiştirir, komut ucu ayarlara
    #: dokunmaz. Bu yüzden anahtarı ayırmak tek bir satırın değişmesi demek.
    user: CurrentUser = requires(SETTINGS),
) -> dict[str, Any]:
    """Yönetilen 24 ayarı kısmi olarak yazar (23 + `disabled_sound_events`)."""
    return await service().push_settings(device_id, settings=body.settings,
                                         reason=body.reason, actor=user.full_name,
                                         dry_run=body.dryRun, token=body.token)


class CommandBody(ReasonBody):
    command: str = Field(min_length=1, max_length=32)
    payload: dict[str, Any] = Field(default_factory=dict)


@router.post("/devices/{device_id}/commands")
async def send_command(
    device_id: int,
    body: CommandBody,
    #: İKİ İZİN, "en az biri". Hangi komutun hangi izne düştüğüne gövdeye
    #: bakılarak karar verilir; `restart`, `clear_failed`, `clear_queue`,
    #: `update` ve `unpair` için `bld_kds.devices` ŞARTTIR ve servis bunu
    #: ayrıca denetler (çift kapı, K9). Yeni komut eklemek bu uçta bir
    #: değişiklik gerektirmez: küme `devices.DESTRUCTIVE_COMMANDS`ta durur.
    user: CurrentUser = requires(MANAGE, DEVICES),
) -> dict[str, Any]:
    return await service().send_command(device_id, command=body.command,
                                        payload=body.payload, reason=body.reason,
                                        actor=user.full_name, dry_run=body.dryRun,
                                        allow_destructive=user.has_permission(DEVICES))


# ================================================================ siparişler

class RevisionBody(ReasonBody):
    #: TAM LİSTE. Kalem farkı değil, siparişin YENİ hâli gönderilir; boş liste
    #: reddedilir çünkü siparişi boşaltırdı.
    items: list[dict[str, Any]] = Field(default_factory=list)
    note: str = Field(default="", max_length=1000)
    requested_at: str = Field(default="", max_length=32)
    customer_note: str = Field(default="", max_length=1000)


@router.post("/orders/{order_id}/revisions")
async def create_revision(
    order_id: int,
    body: RevisionBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    return await service().create_revision(order_id, items=body.items, reason=body.reason,
                                           actor=user.full_name, note=body.note,
                                           requested_at=body.requested_at,
                                           customer_note=body.customer_note,
                                           dry_run=body.dryRun)


class StatusBody(ReasonBody):
    status: str = Field(min_length=1, max_length=32)


@router.post("/orders/{order_id}/status")
async def set_order_status(
    order_id: int,
    body: StatusBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Durum geçişi. Matris sunucuda uygulanır; buradaki ön denetim yalnız
    imkânsız geçişi anlaşılır bir cümleyle söyler."""
    return await service().set_order_status(order_id, status=body.status,
                                            reason=body.reason, actor=user.full_name,
                                            dry_run=body.dryRun)
