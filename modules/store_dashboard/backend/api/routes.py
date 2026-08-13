"""Kontrol Paneli — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde kartı gizlemek yetkilendirme
değildir. Bakım modu AYRI izin ister (`store_dashboard.maintenance`) ve
gerekçe `min_length=10` ile şemada doğrulanır, ayrıca serviste tekrar
denetlenir — istemci şemayı atlatabilir.

Pano kart kart yüklenir: her kartın kendi ucu var ve biri patlarsa diğerleri
dolar. Tek büyük uç, en yavaş kaynağın hızında bir ekran üretirdi.

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran
mesajı gösterir. 4xx yalnız izin/şema kapısından çıkar.
"""

from __future__ import annotations

from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..service import DashboardService

router = APIRouter()
_service: DashboardService | None = None


def bind(service: DashboardService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> DashboardService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


# ================================================================== okuma

@router.get("/summary")
async def summary(
    start: str = Query("", max_length=10),
    end: str = Query("", max_length=10),
    channel: str = Query("", max_length=32),
    compare: str = Query("", max_length=12),
    user: CurrentUser = requires("store_dashboard.view"),
) -> dict[str, Any]:
    return await service().summary(start=start, end=end, channel=channel, compare=compare)


@router.get("/orders/recent")
async def recent_orders(
    limit: int = Query(0, ge=0, le=50),
    user: CurrentUser = requires("store_dashboard.view"),
) -> dict[str, Any]:
    return await service().recent_orders(limit=limit)


@router.get("/stock/critical")
async def critical_stock(
    limit: int = Query(0, ge=0, le=50),
    user: CurrentUser = requires("store_dashboard.view"),
) -> dict[str, Any]:
    return await service().critical_stock(limit=limit)


@router.get("/pending")
async def pending(
    user: CurrentUser = requires("store_dashboard.view"),
) -> dict[str, Any]:
    return await service().pending_work()


@router.get("/system")
async def system(
    user: CurrentUser = requires("store_dashboard.view"),
) -> dict[str, Any]:
    return await service().system_health()


@router.get("/settings")
async def settings(
    user: CurrentUser = requires("store_dashboard.view"),
) -> dict[str, Any]:
    return await service().settings()


@router.get("/config")
async def config(
    user: CurrentUser = requires("store_dashboard.view"),
) -> dict[str, Any]:
    """Yapılandırma sekmesi: beyaz listedeki ayarlar + kaynak rozeti."""
    return await service().config_view()


@router.get("/audit")
async def audit(
    limit: int = Query(50, ge=1, le=500),
    user: CurrentUser = requires("store_dashboard.view"),
) -> dict[str, Any]:
    return await service().audit(limit=limit)


# ================================================================== yazma

class LocalPrefs(BaseModel):
    channel: str = Field(default="", max_length=32)
    locale: str = Field(default="", max_length=8)
    timezone: str = Field(default="", max_length=64)
    dateFormat: str = Field(default="", max_length=32)
    weekStart: int | None = Field(default=None, ge=0, le=6)
    compare: str = Field(default="", max_length=12)


class SettingsBody(BaseModel):
    local: LocalPrefs = Field(default_factory=LocalPrefs)
    #: Yalnız mağazada BULUNAN anahtarlar yazılır; bulunmayan atlanır ve
    #: yanıtta `skipped` içinde adıyla döner.
    identity: dict[str, str] = Field(default_factory=dict)
    seo: dict[str, str] = Field(default_factory=dict)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/settings")
async def save_settings(
    body: SettingsBody,
    user: CurrentUser = requires("store_dashboard.manage"),
) -> dict[str, Any]:
    local = {key: value for key, value in body.local.model_dump().items()
             if value not in (None, "")}
    return await service().save_settings(local=local, identity=body.identity, seo=body.seo,
                                         reason=body.reason, actor=user.full_name,
                                         dry_run=body.dryRun)


class ConfigBody(BaseModel):
    #: `kod → değer`, ikisi de METİN. Mantıksal alanlar "1"/"0" gelir: JSON
    #: `true` değeri `core_config` içinde "true" METNİ olarak saklanırdı ve
    #: kapatılmak istenen yöntem açık kalırdı.
    changes: dict[str, str] = Field(default_factory=dict)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/config")
async def save_config(
    body: ConfigBody,
    user: CurrentUser = requires("store_dashboard.manage"),
) -> dict[str, Any]:
    """Yalnız mağazada İLAN EDİLMİŞ anahtarlar yazılır; kalanı `skipped` döner."""
    return await service().save_config(changes=body.changes, reason=body.reason,
                                       actor=user.full_name, dry_run=body.dryRun)


class MaintenanceBody(BaseModel):
    enabled: bool
    allowedIps: str = Field(default="", max_length=500)
    message: str = Field(default="", max_length=500)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/settings/maintenance")
async def maintenance(
    body: MaintenanceBody,
    user: CurrentUser = requires("store_dashboard.maintenance"),
) -> dict[str, Any]:
    """Bakım modu vitrini KAPATIR: ayrı izin + gerekçe + kuru prova (ADR 0012)."""
    return await service().set_maintenance(enabled=body.enabled, allowed_ips=body.allowedIps,
                                           message=body.message, reason=body.reason,
                                           actor=user.full_name, dry_run=body.dryRun)


# ================================================================== rapor

class RangeBody(BaseModel):
    start: str = Field(default="", max_length=10)
    end: str = Field(default="", max_length=10)
    channel: str = Field(default="", max_length=32)
    compare: str = Field(default="", max_length=12)


class PreviewBody(RangeBody):
    kind: str = Field(default="daily", max_length=24)


@router.post("/preview")
async def preview(
    body: PreviewBody,
    user: CurrentUser = requires("store_dashboard.view"),
) -> dict[str, Any]:
    return await service().preview(body.kind, {"start": body.start, "end": body.end,
                                               "channel": body.channel,
                                               "compare": body.compare})


class PrintBody(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    copies: int = Field(default=1, ge=1, le=20)


@router.post("/print")
async def print_report(
    body: PrintBody,
    user: CurrentUser = requires("store_dashboard.view"),
) -> dict[str, Any]:
    return await service().print_report(body.path, copies=body.copies)


@router.get("/printer")
async def printer(
    user: CurrentUser = requires("store_dashboard.view"),
) -> dict[str, Any]:
    return await service().printer_status()


@router.post("/export")
async def export(
    body: RangeBody,
    user: CurrentUser = requires("store_dashboard.view"),
) -> dict[str, Any]:
    """KPI + günlük ciro CSV'si — rapor klasörüne yazılır."""
    return await service().export_csv(start=body.start, end=body.end, channel=body.channel,
                                      compare=body.compare)
