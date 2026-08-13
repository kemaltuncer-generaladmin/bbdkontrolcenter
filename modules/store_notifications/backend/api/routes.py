"""Bildirimler — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde düğmeyi gizlemek yetkilendirme
değildir. Yıkıcı uçlarda gerekçe `min_length=10` ile ŞEMADA doğrulanır, ayrıca
serviste tekrar denetlenir — istemci şemayı atlatabilir.

İZİN AYRIMI:
  · `.view`      okuma, önizleme, rapor, CSV
  · `.manage`    şablon, kural, kanal ayarı
  · `.send`      tek gönderim: kendime test ve yeniden gönderme (para harcar)
  · `.broadcast` TOPLU gönderim — canlıda 1.800+ kişi, geri alınamaz

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran
mesajı gösterir. 4xx yalnız izin/şema kapısından çıkar.
"""

from __future__ import annotations

from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..service import NotificationsService

router = APIRouter()
_service: NotificationsService | None = None


def bind(service: NotificationsService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> NotificationsService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


# ================================================================== okuma

@router.get("/catalog")
async def catalog(
    user: CurrentUser = requires("store_notifications.view"),
) -> dict[str, Any]:
    """Olaylar, kanallar, durumlar, değişken paleti. Panel bunları kendi
    içinde tutmaz: kural gövdesindeki olay adı ile sunucununki aynı olmalı."""
    return service().catalog()


@router.get("/history")
async def history(
    q: str = Query("", max_length=160),
    channel: str = Query("", max_length=16),
    template: str = Query("", max_length=120),
    status: str = Query("", max_length=16),
    event: str = Query("", max_length=32),
    dateFrom: str = Query("", max_length=10),
    dateTo: str = Query("", max_length=10),
    costMin: int | None = Query(None, ge=0),
    costMax: int | None = Query(None, ge=0),
    failedOnly: bool = Query(False),
    page: int = Query(1, ge=1, le=10_000),
    size: int = Query(0, ge=0, le=200),
    user: CurrentUser = requires("store_notifications.view"),
) -> dict[str, Any]:
    return await service().history(q=q, channel=channel, template=template, status=status,
                                   event=event, date_from=dateFrom, date_to=dateTo,
                                   cost_min=costMin, cost_max=costMax, failed_only=failedOnly,
                                   page=page, size=size)


@router.get("/templates")
async def templates(
    user: CurrentUser = requires("store_notifications.view"),
) -> dict[str, Any]:
    return await service().templates()


@router.get("/rules")
async def rules(
    user: CurrentUser = requires("store_notifications.view"),
) -> dict[str, Any]:
    return await service().rules()


@router.get("/channels")
async def channels(
    user: CurrentUser = requires("store_notifications.view"),
) -> dict[str, Any]:
    """Kanal ayarları. SIRLAR MASKELİ döner; ham parola yanıtta bulunmaz (K8)."""
    return await service().channels()


@router.get("/subscribers")
async def subscribers(
    q: str = Query("", max_length=160),
    subscribed: str = Query("", max_length=1),
    page: int = Query(1, ge=1, le=10_000),
    size: int = Query(0, ge=0, le=200),
    user: CurrentUser = requires("store_notifications.view"),
) -> dict[str, Any]:
    return await service().subscribers(q=q, subscribed=subscribed, page=page, size=size)


@router.get("/audit")
async def audit(
    limit: int = Query(50, ge=1, le=500),
    user: CurrentUser = requires("store_notifications.view"),
) -> dict[str, Any]:
    return await service().audit(limit=limit)


# ============================================================== şablonlar

class TemplatePreviewBody(BaseModel):
    channel: str = Field(max_length=16)
    subject: str = Field(default="", max_length=255)
    body: str = Field(default="", max_length=20_000)
    recipients: int = Field(default=1, ge=0, le=100_000)


@router.post("/templates/preview")
async def template_preview(
    body: TemplatePreviewBody,
    user: CurrentUser = requires("store_notifications.view"),
) -> dict[str, Any]:
    """Örnek veriyle doldurulmuş önizleme + SMS karakter/parça/kredi sayacı.
    Hiçbir şey yazmaz ve hiçbir yere gitmez; gerekçe istemez."""
    return service().preview_template(channel=body.channel, subject=body.subject,
                                      body=body.body, recipients=body.recipients)


class TemplateBody(BaseModel):
    #: Boşsa yeni şablon. Doluysa `store:12` / `local:3` biçiminde.
    template: str = Field(default="", max_length=32)
    name: str = Field(min_length=1, max_length=120)
    channel: str = Field(max_length=16)
    subject: str = Field(default="", max_length=255)
    body: str = Field(min_length=1, max_length=20_000)
    event: str = Field(default="", max_length=32)
    active: bool = True
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/templates")
async def save_template(
    body: TemplateBody,
    user: CurrentUser = requires("store_notifications.manage"),
) -> dict[str, Any]:
    return await service().save_template(
        template=body.template, name=body.name, channel=body.channel, subject=body.subject,
        body=body.body, event=body.event, active=body.active, reason=body.reason,
        actor=user.full_name, dry_run=body.dryRun)


class TemplateIdBody(BaseModel):
    template: str = Field(min_length=3, max_length=32)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/templates/deactivate")
async def deactivate_template(
    body: TemplateIdBody,
    user: CurrentUser = requires("store_notifications.manage"),
) -> dict[str, Any]:
    """Şablon SİLİNMEZ, pasifleştirilir: geçmiş gönderimler hangi şablonla
    gittiğini göstermeye devam etmeli (ADR 0012)."""
    return await service().deactivate_template(body.template, reason=body.reason,
                                               actor=user.full_name, dry_run=body.dryRun)


class TestSendBody(BaseModel):
    channel: str = Field(max_length=16)
    to: str = Field(min_length=3, max_length=160)
    subject: str = Field(default="", max_length=255)
    body: str = Field(min_length=1, max_length=20_000)


@router.post("/templates/test")
async def test_send(
    body: TestSendBody,
    user: CurrentUser = requires("store_notifications.send"),
) -> dict[str, Any]:
    """Kendime test gönder — TEK alıcı. Gerçek gönderimdir, para harcar."""
    return await service().test_send(channel=body.channel, to=body.to, subject=body.subject,
                                     body=body.body, actor=user.full_name)


# ================================================================ kurallar

class RuleBody(BaseModel):
    ruleId: int | None = Field(default=None, ge=1)
    event: str = Field(max_length=32)
    channel: str = Field(max_length=16)
    template: str = Field(min_length=3, max_length=32)
    condition: str = Field(default="", max_length=500)
    delayMinutes: int = Field(default=0, ge=0, le=10_080)
    active: bool = True
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/rules")
async def save_rule(
    body: RuleBody,
    user: CurrentUser = requires("store_notifications.manage"),
) -> dict[str, Any]:
    return await service().save_rule(
        rule_id=body.ruleId, event=body.event, channel=body.channel, template=body.template,
        condition=body.condition, delay_minutes=body.delayMinutes, active=body.active,
        reason=body.reason, actor=user.full_name, dry_run=body.dryRun)


class RuleToggleBody(BaseModel):
    active: bool = False
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/rules/{rule_id}/toggle")
async def toggle_rule(
    rule_id: int,
    body: RuleToggleBody,
    user: CurrentUser = requires("store_notifications.manage"),
) -> dict[str, Any]:
    return await service().toggle_rule(rule_id, active=body.active, reason=body.reason,
                                       actor=user.full_name, dry_run=body.dryRun)


# ================================================================ kanallar

class MobileBody(BaseModel):
    pushEnabled: bool | None = None
    androidVersion: str = Field(default="", max_length=32)
    iosVersion: str = Field(default="", max_length=32)
    minSupported: str = Field(default="", max_length=32)
    forceUpdate: bool | None = None
    maintenance: bool | None = None
    maintenanceText: str = Field(default="", max_length=500)


class ChannelsBody(BaseModel):
    quietStart: str | None = Field(default=None, max_length=5)
    quietEnd: str | None = Field(default=None, max_length=5)
    quietEnabled: bool | None = None
    dailyLimit: int | None = Field(default=None, ge=0, le=1_000_000)
    mobile: MobileBody | None = None
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/channels")
async def save_channels(
    body: ChannelsBody,
    user: CurrentUser = requires("store_notifications.manage"),
) -> dict[str, Any]:
    """Sessiz saatler, günlük limit ve mobil uygulama ayarları.

    SMTP/SMS SIRLARI BU UÇTAN YAZILMAZ: sır kasada durur (K8). Ekran maskeli
    değeri gösterir ve nereden değiştirileceğini söyler.
    """
    mobile = None
    if body.mobile is not None:
        mobile = {key: value for key, value in body.mobile.model_dump().items()
                  if value is not None and value != ""}
    return await service().save_channels(
        quiet_start=body.quietStart, quiet_end=body.quietEnd, quiet_enabled=body.quietEnabled,
        daily_limit=body.dailyLimit, mobile=mobile or None, reason=body.reason,
        actor=user.full_name, dry_run=body.dryRun)


@router.get("/channels/test")
async def test_connection(
    kind: str = Query("store", max_length=16),
    user: CurrentUser = requires("store_notifications.manage"),
) -> dict[str, Any]:
    """[Bağlantıyı sına]. Gerçek bir mesaj göndermez — bu yüzden GET'tir."""
    return await service().test_connection(kind)


# ========================================================= toplu gönderim

class BroadcastPreviewBody(BaseModel):
    channel: str = Field(max_length=16)
    template: str = Field(default="", max_length=32)
    subject: str = Field(default="", max_length=255)
    body: str = Field(default="", max_length=20_000)
    audience: str = Field(default="subscribers", max_length=32)
    filters: dict[str, Any] = Field(default_factory=dict)


@router.post("/broadcast/preview")
async def broadcast_preview(
    body: BroadcastPreviewBody,
    user: CurrentUser = requires("store_notifications.broadcast"),
) -> dict[str, Any]:
    """KURU PROVA: alıcı sayısı + tahmini maliyet + jeton. Mesaj GİTMEZ."""
    return await service().broadcast_preview(
        channel=body.channel, template=body.template, subject=body.subject, body=body.body,
        audience=body.audience, filters=body.filters, actor=user.full_name)


class BroadcastSendBody(BaseModel):
    token: str = Field(min_length=8, max_length=64)
    reason: str = Field(min_length=10, max_length=255)
    #: VARSAYILAN TRUE. Canlıda 1.800+ kişiye gider ve geri alınamaz.
    dryRun: bool = True


@router.post("/broadcast/send")
async def broadcast_send(
    body: BroadcastSendBody,
    user: CurrentUser = requires("store_notifications.broadcast"),
) -> dict[str, Any]:
    return await service().broadcast_send(token=body.token, reason=body.reason,
                                          actor=user.full_name, dry_run=body.dryRun)


class ResendBody(BaseModel):
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/history/{notification_id}/resend")
async def resend(
    notification_id: int,
    body: ResendBody,
    user: CurrentUser = requires("store_notifications.send"),
) -> dict[str, Any]:
    """Yeniden gönderim YENİ bir gönderimdir: para harcar, müşteriye ikinci
    kez ulaşır. Bu yüzden gerekçe ister ve kuru prova varsayılan açıktır."""
    return await service().resend(notification_id, reason=body.reason, actor=user.full_name,
                                  dry_run=body.dryRun)


# =================================================================== rapor

class PreviewBody(BaseModel):
    kind: str = Field(max_length=24)
    dateFrom: str = Field(default="", max_length=10)
    dateTo: str = Field(default="", max_length=10)
    channel: str = Field(default="", max_length=16)


@router.post("/preview")
async def preview(
    body: PreviewBody,
    user: CurrentUser = requires("store_notifications.view"),
) -> dict[str, Any]:
    return await service().preview(body.kind, {"dateFrom": body.dateFrom,
                                               "dateTo": body.dateTo,
                                               "channel": body.channel})


class PrintBody(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    copies: int = Field(default=1, ge=1, le=20)


@router.post("/print")
async def print_report(
    body: PrintBody,
    user: CurrentUser = requires("store_notifications.view"),
) -> dict[str, Any]:
    return await service().print_report(body.path, copies=body.copies)


@router.get("/printer")
async def printer(
    user: CurrentUser = requires("store_notifications.view"),
) -> dict[str, Any]:
    return await service().printer_status()


class ExportBody(BaseModel):
    dateFrom: str = Field(default="", max_length=10)
    dateTo: str = Field(default="", max_length=10)
    channel: str = Field(default="", max_length=16)
    status: str = Field(default="", max_length=16)


@router.post("/export")
async def export(
    body: ExportBody,
    user: CurrentUser = requires("store_notifications.view"),
) -> dict[str, Any]:
    """TÜM kayıtların CSV'si — rapor klasörüne yazılır. Görünen sayfanın
    CSV'sini panel kendisi üretir (sunucuya hiç gitmez).

    SÜZGEÇ SÖZLÜĞÜNÜ BURADA KURMUYORUZ: servis onu liste ucuyla aynı kapıdan
    (`_history_filters`) geçirir ve tanınmayan kanal/durum değerini atar.
    Burada kurulan ham sözlük, uygulanmayan bir süzgeçle alınmış CSV'yi
    süzülmüş gibi göstermişti.
    """
    return await service().export_csv(channel=body.channel, status=body.status,
                                      date_from=body.dateFrom, date_to=body.dateTo)
