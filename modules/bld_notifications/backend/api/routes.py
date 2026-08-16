"""Bildirimler — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde düğmeyi gizlemek yetkilendirme
değildir. `module.yaml` → `http.requires` taban izni verir, uçlar onu DARALTIR.

ÜÇ İZİN, İKİ DEĞİL:

    bld_notifications.view      duyuruları, istatistiği ve yerel izi görme
    bld_notifications.manage    taslak oluşturma ve düzenleme
    bld_notifications.publish   YAYINLAMA ve ARŞİVLEME

Üçüncüsünün ayrı durmasının nedeni şudur: bu yetkiyi taşıyan kişi MÜŞTERİYE
GÖRÜNEN metni değiştirir. Yayınlanan duyuru bütün müşterilerin uygulamasında
açılır ve sözleşmede geri alma ucu YOKTUR (`POST /{id}/unpublish` bilerek
tanımlanmadı); arşivleme ise yayında duran bir duyuruyu anında görünmez yapar.
İkisi de dışa dönük ve ikisi de aynı eşiği hak ediyor. Taslak yazmak dışa
dönük değildir ve `manage` ile kalır — aksi hâlde metni hazırlayan kişiye,
hazırladığı metni yayınlama yetkisi de verilmiş olurdu.

YIKICI İŞLEM PIN DEĞİL GEREKÇE İSTER (ADR 0012): ayrı izin anahtarı +
gerekçe (en az 10 karakter, backend'de DE doğrulanır) + çift denetim satırı.
Bu yüzden hiçbir izin `destructive: true` taşımaz — o bayrak çekirdekte PIN
kapısına bağlanacak ve bu ekran PIN istemiyor.

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran
mesajı gösterir. 4xx yalnız izin ve şema kapısından çıkar.
"""

from __future__ import annotations

from typing import Any, ClassVar

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..notices import (
    ACTION_LABEL_MAX,
    ACTION_URL_MAX,
    BODY_MAX,
    MAX_REASON,
    MIN_REASON,
    TITLE_MAX,
    WRITABLE,
)
from ..service import NoticeService

VIEW = "bld_notifications.view"
MANAGE = "bld_notifications.manage"
#: Dışa dönük eşiğin izin anahtarı. Tek yerde durur: uç noktalar ve servis
#: aynı dizgeyi okur, yazım hatası bir kapıyı sessizce açık bırakamaz.
PUBLISH = "bld_notifications.publish"

router = APIRouter()
_service: NoticeService | None = None


def bind(service: NoticeService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> NoticeService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


class ReasonBody(BaseModel):
    """Her yazma gövdesinin ortak iki alanı.

    `actor` GÖVDEDEN ALINMAZ — oturumdan gelir. İstemcinin aktör adını
    yazabilmesi, denetim izini imzalanmamış bir deftere çevirirdi: silinmeyen
    bir satıra istediği adı yazan biri, yayınladığı duyuruyu başkasının üstüne
    bırakabilirdi.

    `dryRun` camelCase'tir ve TEK KABUL EDİLEN addır (panel→Kontrol Merkezi
    sınırında camelCase; tele giden `dry_run` adına çeviriyi geçit yapar).
    `dry_run` da kabul edilseydi bir yazım hatası ("dryrun", "dry_Run")
    sessizce düşer ve alan hiç gönderilmemiş sayılırdı — istemci kuru prova
    sandığı isteğin GERÇEK YAZMA yaptığını sonradan öğrenirdi. Tek ad +
    `extra="forbid"` sayesinde yanlış yazılan alan 422 ile geri döner.

    Panel bu alanı GÖNDERMEZ; varsayılan modül ayarındadır ve kapalıdır.
    """

    #: `ClassVar` ile işaretli, çünkü `km_sdk` pydantic'in `ConfigDict` tipini
    #: dışa vurmuyor ve modül pydantic'i doğrudan import etmiyor (K2).
    model_config: ClassVar[dict[str, Any]] = {"extra": "forbid"}

    reason: str = Field(min_length=MIN_REASON, max_length=MAX_REASON)
    dryRun: bool | None = None


# ================================================================== okuma

@router.get("/notices")
async def notices(
    status: str = Query("", max_length=32),
    audience: str = Query("", max_length=32),
    level: str = Query("", max_length=32),
    #: ÜÇ DEĞERLİ: `true` · `false` · süzgeç yok. "Şu an görünmeyenler"
    #: (`false`) gerçek bir sorudur — yayında sanılıp görünmeyen duyuruları
    #: bulmanın tek yolu — ve `None` ile aynı şey değildir.
    live: bool | None = Query(None),
    q: str = Query("", max_length=160),
    page: int = Query(1, ge=1),
    per_page: int = Query(0, ge=0, le=100),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Duyuru listesi (sayfalı) + form sözleşmesi + ekran ayarları."""
    return await service().notices(status=status, audience=audience, level=level,
                                   live=live, q=q, page=page, per_page=per_page)


@router.get("/notices/{notification_id}/stats")
async def stats(
    notification_id: int,
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Görülme/kapatılma sayıları. Kitle `all` ise ÖLÇÜLEMEZ ve `null` döner."""
    return await service().stats(notification_id)


@router.get("/audit")
async def audit(
    limit: int = Query(0, ge=0, le=500),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Bu ekrandan yapılan yazma DENEMELERİ (yerel iz). Ağa çıkmaz.

    Sunucunun kendi izinden ayrıdır: oradaki kayıt yalnız SUNUCUYA ULAŞAN
    isteği bilir, buradaki "gönderildi mi" sorusunun cevabıdır.
    """
    return await service().audit(limit=limit)


# ================================================================== yazma

class NoticeCreateBody(ReasonBody):
    """Yeni duyuru. Sunucu her zaman `draft` üretir; durum gövdede YOKTUR.

    `body` DÜZ METİNDİR, HTML değil (sözleşme §Şema). Satır sonu `\\n`
    desteklenir; biçimlendirme yoktur ve bir HTML temizleyicisi eklenmez —
    beyaz liste tutmak, HTML'in kabul edildiğini söylemek olurdu.
    """

    title: str = Field(min_length=1, max_length=TITLE_MAX)
    body: str = Field(min_length=1, max_length=BODY_MAX)
    level: str = Field(default="info", max_length=16)
    audience: str = Field(default="customers", max_length=24)
    #: `None` = pencere yok (`starts_at` boşsa yayınla birlikte, `ends_at`
    #: boşsa süresiz). Boş dize DE kabul edilir ve servis onu `None`'a çevirir:
    #: form alanı boş bırakıldığında tarayıcı boş dize gönderir.
    starts_at: str | None = Field(default=None, max_length=32)
    ends_at: str | None = Field(default=None, max_length=32)
    action_label: str | None = Field(default=None, max_length=ACTION_LABEL_MAX)
    action_url: str | None = Field(default=None, max_length=ACTION_URL_MAX)
    dismissible: bool = True


@router.post("/notices")
async def create(
    body: NoticeCreateBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Yeni duyuru — TASLAK doğar. Yayın ayrı bir eylem ve ayrı bir izindir."""
    return await service().create(
        title=body.title, body=body.body, level=body.level, audience=body.audience,
        starts_at=body.starts_at or "", ends_at=body.ends_at or "",
        action_label=body.action_label or "", action_url=body.action_url or "",
        dismissible=body.dismissible, reason=body.reason, actor=user.full_name,
        dry_run=body.dryRun)


class NoticePatchBody(ReasonBody):
    """Kısmi güncelleme. YALNIZ GÖNDERİLEN alanlar değişir.

    Alanlar tek tek yazılıdır, serbest bir `changes` sözlüğü DEĞİL: sözlük
    olsaydı alan adları ve tipleri şema kapısından hiç geçmez, yanlış yazılmış
    bir ad (`starts` / `startsAt`) sessizce düşer ve "kaydettim ama değişmedi"
    olarak geri dönerdi.

    "Gönderildi mi" ile "boş gönderildi mi" ayrımını `model_fields_set`
    taşır: `None` DEĞERİ GERÇEK BİR DEĞERDİR ve "bu alanı temizle" demektir
    (pencereyi süresiz yapmak, düğmeyi kaldırmak). Varsayılanla ayırt edilemez
    olsaydı, bir kez konmuş bitiş anını kaldırmanın hiçbir yolu kalmazdı.

    `status` alanı BİLEREK YOKTUR: durumun kendi uçları var ve `extra="forbid"`
    sayesinde gönderilirse 422 alır.
    """

    title: str | None = Field(default=None, max_length=TITLE_MAX)
    body: str | None = Field(default=None, max_length=BODY_MAX)
    level: str | None = Field(default=None, max_length=16)
    audience: str | None = Field(default=None, max_length=24)
    starts_at: str | None = Field(default=None, max_length=32)
    ends_at: str | None = Field(default=None, max_length=32)
    action_label: str | None = Field(default=None, max_length=ACTION_LABEL_MAX)
    action_url: str | None = Field(default=None, max_length=ACTION_URL_MAX)
    dismissible: bool | None = None

    def changes(self) -> dict[str, Any]:
        """Yalnız İSTEMCİNİN AÇIKÇA GÖNDERDİĞİ yazılabilir alanlar."""
        return {key: getattr(self, key) for key in self.model_fields_set
                if key in WRITABLE}


@router.patch("/notices/{notification_id}")
async def update(
    notification_id: int,
    body: NoticePatchBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Duyuruyu günceller. Yayınlanmış duyuru da düzenlenebilir (sözleşme §PATCH).

    Kapsam (`audience`) değişirse sunucu `warnings` üretir ve panel onu ayrıca
    gösterir; görülme kayıtları SİLİNMEZ.
    """
    return await service().update(notification_id, changes=body.changes(),
                                  reason=body.reason, actor=user.full_name,
                                  dry_run=body.dryRun)


@router.post("/notices/{notification_id}/publish")
async def publish(
    notification_id: int,
    body: ReasonBody,
    user: CurrentUser = requires(PUBLISH),
) -> dict[str, Any]:
    """DIŞA DÖNÜK. Duyuru bütün hedef kitleye açılır; geri alma ucu yoktur.

    İzin kapısı burada ve serviste iki kez denetlenir (K9 — çift kapı).
    """
    return await service().publish(notification_id, reason=body.reason,
                                   actor=user.full_name, dry_run=body.dryRun,
                                   allow_publish=user.has_permission(PUBLISH))


@router.post("/notices/{notification_id}/archive")
async def archive(
    notification_id: int,
    body: ReasonBody,
    user: CurrentUser = requires(PUBLISH),
) -> dict[str, Any]:
    """YUMUŞAK ARŞİV. Kayıt SİLİNMEZ, `status = archived` olur.

    Uç `DELETE` değil `POST /archive`: yaptığı iş bir silme değil, bir
    pasifleştirmedir (kit kuralı 8) ve gerekçe gövdede taşınır. Duyuru anında
    görünmez olur, görülme kayıtları kalır ve `stats` çalışmaya devam eder.
    """
    return await service().archive(notification_id, reason=body.reason,
                                   actor=user.full_name, dry_run=body.dryRun,
                                   allow_publish=user.has_permission(PUBLISH))
