"""Raporlar — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde düğmeyi gizlemek yetkilendirme
değildir. Servis HTTP hatası fırlatmaz; `{"ok": False, "error": …}` döner ve
ekran mesajı gösterir. 4xx yalnız izin/şema kapısından çıkar.

RAPOR ÜÇLÜSÜ. Panel kiti `preview` · `print` · `printer` adlarını bekler;
`/preview` gövdesini `{kind, ...params}` olarak kurar. `kind` bir yaprak
anahtarı ya da `package` olabilir — rapor paketi de aynı zincirden geçer,
böylece önizleme ve yazdırma tek yerde yaşar.
"""

from __future__ import annotations

from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..service import ReportsService

router = APIRouter()
_service: ReportsService | None = None


def bind(service: ReportsService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> ReportsService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


class Params(BaseModel):
    """Parametre şeridi. Yaprak yalnız kendisine anlamlı olanları kullanır."""

    start: str = Field(default="", max_length=10)
    end: str = Field(default="", max_length=10)
    granularity: str = Field(default="day", max_length=8)
    compare: str = Field(default="", max_length=12)
    channel: str = Field(default="", max_length=64)
    customerGroup: str = Field(default="", max_length=64)
    category: str = Field(default="", max_length=120)
    carrier: str = Field(default="", max_length=64)


# ================================================================== okuma

@router.get("/tree")
async def report_tree(
    user: CurrentUser = requires("store_reports.view"),
) -> dict[str, Any]:
    """Ağaç, parametre sözlüğü ve geçit durumu — panel açılışta bunu okur."""
    return await service().catalog()


@router.get("/reference")
async def reference(
    user: CurrentUser = requires("store_reports.view"),
) -> dict[str, Any]:
    return await service().reference()


class RunBody(Params):
    leaf: str = Field(max_length=48)


@router.post("/run")
async def run(
    body: RunBody,
    user: CurrentUser = requires("store_reports.view"),
) -> dict[str, Any]:
    """Tek yaprağı ekranda göster. Hata YAPRAK BAZLIDIR: bu uç patlarsa
    ağaçtaki diğer raporlar etkilenmez."""
    return await service().run(body.leaf, body.model_dump())


@router.get("/runs")
async def runs(
    limit: int = Query(50, ge=1, le=500),
    user: CurrentUser = requires("store_reports.view"),
) -> dict[str, Any]:
    return await service().runs(limit=limit)


# ================================================================== rapor

class PreviewBody(Params):
    #: Yaprak anahtarı ya da `package`.
    kind: str = Field(default="", max_length=48)
    #: `kind == "package"` iken birleştirilecek yapraklar.
    leaves: list[str] = Field(default_factory=list)
    title: str = Field(default="", max_length=120)


@router.post("/preview")
async def preview(
    body: PreviewBody,
    user: CurrentUser = requires("store_reports.view"),
) -> dict[str, Any]:
    """`actor` üretim izine yazılır — "Geçmiş" sekmesi kimin istediğini yazar."""
    return await service().preview(body.kind, body.model_dump(), actor=user.full_name)


class PrintBody(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    copies: int = Field(default=1, ge=1, le=20)


@router.post("/print")
async def print_report(
    body: PrintBody,
    user: CurrentUser = requires("store_reports.view"),
) -> dict[str, Any]:
    """Yol doğrulaması serviste: rapor klasörü dışındaki dosya basılmaz."""
    return await service().print_report(body.path, copies=body.copies)


@router.get("/printer")
async def printer(
    user: CurrentUser = requires("store_reports.view"),
) -> dict[str, Any]:
    return await service().printer_status()


class ExportBody(Params):
    leaf: str = Field(max_length=48)


@router.post("/export")
async def export(
    body: ExportBody,
    user: CurrentUser = requires("store_reports.view"),
) -> dict[str, Any]:
    return await service().export_csv(body.leaf, body.model_dump(), actor=user.full_name)


# ==================================================== kaydedilmiş raporlar

@router.get("/saved")
async def saved(
    user: CurrentUser = requires("store_reports.view"),
) -> dict[str, Any]:
    return await service().saved()


class SaveBody(Params):
    name: str = Field(min_length=3, max_length=80)
    leaves: list[str] = Field(default_factory=list)


@router.post("/saved")
async def save(
    body: SaveBody,
    user: CurrentUser = requires("store_reports.manage"),
) -> dict[str, Any]:
    return await service().save(name=body.name, leaves=body.leaves,
                                params=body.model_dump(), actor=user.full_name)


class ArchiveBody(BaseModel):
    reason: str = Field(min_length=10, max_length=255)


@router.post("/saved/{saved_id}/archive")
async def archive_saved(
    saved_id: int,
    body: ArchiveBody,
    user: CurrentUser = requires("store_reports.manage"),
) -> dict[str, Any]:
    """SİLME UCU YOKTUR — kayıt arşivlenir, satır durur (BBD veri silme yasağı)."""
    return await service().archive_saved(saved_id, reason=body.reason, actor=user.full_name)


# ==================================================== zamanlanmış raporlar

@router.get("/schedules")
async def schedules(
    user: CurrentUser = requires("store_reports.view"),
) -> dict[str, Any]:
    return await service().schedules()


class ScheduleBody(Params):
    name: str = Field(min_length=3, max_length=80)
    leaves: list[str] = Field(default_factory=list)
    period: str = Field(default="weekly", max_length=10)
    weekday: int = Field(default=0, ge=0, le=6)
    dayOfMonth: int = Field(default=1, ge=1, le=28)
    atTime: str = Field(default="07:00", max_length=5)


@router.post("/schedules")
async def create_schedule(
    body: ScheduleBody,
    user: CurrentUser = requires("store_reports.manage"),
) -> dict[str, Any]:
    return await service().schedule(name=body.name, leaves=body.leaves,
                                    params=body.model_dump(), period=body.period,
                                    weekday=body.weekday, day_of_month=body.dayOfMonth,
                                    at_time=body.atTime, actor=user.full_name)


class ToggleBody(BaseModel):
    active: bool = False


@router.post("/schedules/{schedule_id}/toggle")
async def toggle_schedule(
    schedule_id: int,
    body: ToggleBody,
    user: CurrentUser = requires("store_reports.manage"),
) -> dict[str, Any]:
    return await service().toggle_schedule(schedule_id, active=body.active,
                                           actor=user.full_name)
