"""Durum Monitörü — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde düğmeyi gizlemek yetkilendirme
değildir. `module.yaml` → `http.requires` taban izni verir, uçlar onu DARALTIR.

İKİ İZİN, ÜÇ DEĞİL:

    bld_status_monitor.view     okuma — kutular, kasa sağlığı, sunucudaki hata
                                olayları, yerel araştırma defteri, olay
                                geçmişi, düzeltme defteri, yerel yazma izi ve
                                ekran tercihi
    bld_status_monitor.manage   yazma — olayı çözüldü işaretleme, defter kaydı
                                yazma/pasifleştirme, düzeltme komutu gönderme

`bld_orders` üçüncü bir anahtarı PARA yüzünden açtı (iptal iade üretir).
Burada para hareketi yok: en ağır işlem bir kasayı yeniden başlatmak ve o iş
`bld_kds.devices` altında ZATEN ayrıca korunuyor. Üçüncü bir anahtar, aynı
kapıyı ikinci kez kilitlemek olurdu.

PIN DEĞİL GEREKÇE (ADR 0012): izin anahtarı + en az 10 karakter gerekçe
(backend'de DE doğrulanır) + iki denetim satırı ("ne denendi" ve "ne oldu").
Bu yüzden hiçbir izin `destructive: true` taşımaz — o bayrak çekirdekte PIN
kapısına bağlanacak ve bu ekran PIN istemiyor.

ROTA DOSYASI METOT ADLARINI SABİTLER; SERVİS ONA UYAR. Rota ile servis metot
adının ayrışması ne açılışta ne `route:list`'te hata verir; yalnız uç
çağrılınca patlar. Aşağıdaki her `service().<ad>` çağrısının karşılığı
`service.py` içinde AYNI adla durur.

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran
mesajı gösterir. 4xx yalnız izin ve şema kapısından çıkar.
"""

from __future__ import annotations

from typing import Any, ClassVar

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..monitor import MAX_NOTE, MAX_REASON, MIN_REASON
from ..service import StatusMonitorService

#: İzin anahtarları tek yerde durur: uçlar ve servis aynı dizeyi okur, yazım
#: hatası bir kapıyı sessizce açık bırakamaz.
VIEW = "bld_status_monitor.view"
MANAGE = "bld_status_monitor.manage"

router = APIRouter()
_service: StatusMonitorService | None = None


def bind(service: StatusMonitorService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> StatusMonitorService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


# ============================================================ ortak gövdeler

class ReasonBody(BaseModel):
    """Her yazma gövdesinin ortak iki alanı.

    `actor` GÖVDEDEN ALINMAZ — oturumdan gelir. İstemcinin aktör adını
    yazabilmesi, denetim izini imzalanmamış bir deftere çevirirdi.

    `dryRun` camelCase'tir ve TEK KABUL EDİLEN addır. Panel→Kontrol Merkezi
    sınırında camelCase geçerli; tele giden `dry_run` adına çeviriyi geçit
    yapar. `dry_run` da kabul edilseydi bir yazım hatası ("dryrun", "dry_Run")
    sessizce düşer, alan hiç gönderilmemiş sayılır ve varsayılana dönerdi —
    yani kuru prova sanılan bir istek GERÇEK KOMUT gönderirdi. Tek ad +
    `extra="forbid"` sayesinde yanlış yazılan alan 422 ile geri döner.
    """

    #: `ClassVar` ile işaretli, çünkü `km_sdk` pydantic'in `ConfigDict` tipini
    #: dışa vurmuyor ve modül pydantic'i doğrudan import etmiyor (K2).
    model_config: ClassVar[dict[str, Any]] = {"extra": "forbid"}

    reason: str = Field(min_length=MIN_REASON, max_length=MAX_REASON)
    dryRun: bool | None = None


# ================================================================== okuma

@router.get("/overview")
async def overview(
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Ekranın sözleşmesi ve kullanıcının tercihleri. AĞA ÇIKMAZ (K7)."""
    return await service().overview()


@router.get("/summary")
async def summary(
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Dört kutu + sunucunun tek cümlelik hükmü. Panel bunu yoklar.

    YAN ETKİSİ VAR ve `GET` olması bilinçli: her çağrı yerel araştırma
    defterine işlenir. Yazılan şey bir İŞ KAYDI değil, okumanın kendisinin
    gözlemidir — "şu an sordum, şunu gördüm". Bunu `POST` yapmak, tarayıcı ve
    kabuk için sıradan bir yoklamayı yazma gibi göstermek olurdu.
    """
    return await service().summary()


@router.get("/devices")
async def devices(
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Kasa sağlık özeti. Kasa YÖNETİMİ `bld_kds` modülünün işi."""
    return await service().devices()


@router.get("/events")
async def events(
    source: str = Query("", max_length=120),
    level: str = Query("", max_length=64),
    code: str = Query("", max_length=64),
    device_id: int = Query(0, ge=0),
    since: str = Query("", max_length=32),
    resolved: str = Query("", max_length=8),
    q: str = Query("", max_length=120),
    page: int = Query(1, ge=1),
    per_page: int = Query(0, ge=0, le=100),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Sunucudaki hata olayları — sayfalı.

    Süzgeçler sorgu dizesindedir (gövde değil): liste bir okumadır ve
    kopyalanabilir bir adresinin olması, "şu ekranı aç" demenin en kısa yolu.
    """
    return await service().events(
        source=source, level=level, code=code, device_id=device_id, since=since,
        resolved=resolved, q=q, page=page, per_page=per_page)


@router.get("/events/{event_id}")
async def event(
    event_id: int,
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Tek olay + `context` + cihazın ŞU ANKİ sağlığı (`related`)."""
    return await service().event(event_id)


@router.get("/log")
async def local_log(
    source: str = Query("", max_length=120),
    result: str = Query("", max_length=16),
    kind: str = Query("", max_length=16),
    q: str = Query("", max_length=120),
    limit: int = Query(0, ge=0, le=500),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Bu modülün KENDİ araştırma defteri — sunucununki değil.

    Buradaki satırlar sunucuya HİÇ ULAŞMAMIŞ gözlemleri de içerir (geçit
    koptu, imza reddedildi, uç yayında değil); tam olarak bu yüzden var.
    """
    return await service().local_log(source=source, result=result, kind=kind,
                                     q=q, limit=limit)


@router.get("/history")
async def history(
    limit: int = Query(0, ge=0, le=500),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Olay geçmişi — gözlemler ve yazmalar TEK akışta, eskiden yeniye."""
    return await service().history(limit=limit)


@router.get("/audit")
async def audit(
    limit: int = Query(50, ge=1, le=500),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Bu ekrandan yapılan yazma denemelerinin YEREL izi — sunucununki değil."""
    return await service().audit(limit=limit)


@router.get("/runbook")
async def runbook(
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Düzeltme defteri. Pasifleştirilmiş kayıtlar da döner ve işaretlenir."""
    return await service().runbook()


# ================================================================== yazma

class ResolveBody(ReasonBody):
    #: İsteğe bağlı, en çok 500 karakter. Sunucu `reason` metnini
    #: `resolve_note` alanına yazıyor; `note` verilirse ikisi birleştiriliyor
    #: (sözleşme). Birleştirmeyi SUNUCU yapar, burada tekrarlanmaz.
    note: str = Field(default="", max_length=MAX_NOTE)


@router.post("/events/{event_id}/resolve")
async def resolve_event(
    event_id: int,
    body: ResolveBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Olayı çözüldü işaretler. SİLME YOKTUR (sözleşme).

    İzin kapısı burada ve serviste iki kez denetlenir (K9 — çift kapı).
    """
    return await service().resolve_event(
        event_id, reason=body.reason, actor=user.full_name, note=body.note,
        dry_run=body.dryRun, allow_manage=user.has_permission(MANAGE))


class RunbookBody(ReasonBody):
    """Defter kaydı. BLD'YE HİÇBİR ŞEY GİTMEZ ama gerekçe YİNE DE istenir.

    Ayrım şurada: bu tablo neyin ÇALIŞTIRILABİLECEĞİNİ tanımlıyor. Ekran
    tercihinden (sayfa boyutu) farkı budur ve gerekçeyi burada da istemek,
    "kim bu satırı ekledi" sorusunun bir cevabı olsun diye.
    """

    title: str = Field(min_length=3, max_length=120)
    description: str = Field(default="", max_length=1000)
    #: `bld.api` | `manual` — servis kapalı listeye karşı doğrular.
    channel: str = Field(default="bld.api", max_length=16)
    #: `monitor.RUNBOOK_ACTIONS` anahtarı ya da `manual.note`. SERBEST METİN
    #: DEĞİLDİR: kapalı listede olmayan bir ad çalıştırılmaz.
    action: str = Field(default="manual.note", max_length=64)
    device_id: int = Field(default=0, ge=0)
    enabled: bool = True


@router.put("/runbook/{key}")
async def save_runbook(
    key: str,
    body: RunbookBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Defter kaydı yazar ya da günceller. SİLME YOK, PASİFLEŞTİRME VAR."""
    return await service().save_runbook(
        key, title=body.title, description=body.description, channel=body.channel,
        action=body.action, device_id=body.device_id, enabled=body.enabled,
        reason=body.reason, actor=user.full_name)


@router.post("/runbook/{key}/run")
async def run_runbook(
    key: str,
    body: ReasonBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Defterdeki düzeltme komutunu kasaya gönderir — `bld.api` geçidinden.

    Ham `subprocess` ya da doğrudan SSH YOKTUR (K4). Eylem adı kapalı listeden
    çözülür; defter satırına yazılmış rastgele bir ad çalıştırılmaz.

    İzin kapısı burada ve serviste iki kez denetlenir (K9 — çift kapı).
    """
    return await service().run_runbook(
        key, reason=body.reason, actor=user.full_name, dry_run=body.dryRun,
        allow_manage=user.has_permission(MANAGE))


# ============================================================ ekran tercihi

class PrefsBody(BaseModel):
    """Görüntüleme tercihi. BLD'ye HİÇBİR ŞEY GİTMEZ.

    `bld_status_monitor.view` YETER ve gerekçe İSTENMEZ: yoklama aralığını
    değiştirmek bir iş yazması değildir. `manage` istemek, gerçek
    yazmalardaki izin ayrımını anlamsızlaştırırdı.
    """

    model_config: ClassVar[dict[str, Any]] = {"extra": "forbid"}

    #: Alt sınır 15 saniye. Daha kısası paylaşılan `bld-control-panel` kovasını
    #: (3000/saat/IP) boşuna yakar ve ikinci bir yöneticinin ekranını 429'a
    #: düşürür; `00-genel.md` §2 bu ekran için 60 saniye varsayıyor.
    poll_seconds: int | None = Field(default=None, ge=15, le=600)
    page_size: int | None = Field(default=None, ge=5, le=100)
    auto_refresh: bool | None = None


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
