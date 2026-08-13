"""Kantin Raporları — HTTP yüzeyi.

ÇİFT KAPI (K9): router `view` ister; dosya üreten uçlar `export`.
Raporlar salt okumadır — kantinde hiçbir şey değiştirmez.
"""

from __future__ import annotations

from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, requires

from ..service import ReportService

router = APIRouter()

_service: ReportService | None = None


def bind(service: ReportService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> ReportService:
    if _service is None:  # pragma: no cover - yükleme sırası garanti eder
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


DATE = r"^\d{4}-\d{2}-\d{2}$"


class ReportBody(BaseModel):
    start: str = Field(pattern=DATE)
    end: str = Field(pattern=DATE)
    # Sınıf bilgisi Öğrenci Yönetimi modülünün verisidir; arayüz yetenekten okur.
    classes: dict[str, str] = Field(default_factory=dict)
    refresh: bool = False
    compare: bool = True


class ExportBody(BaseModel):
    kind: str = Field(pattern=r"^(summary_pdf|products_pdf|student_pdf|students_csv|products_csv)$")
    start: str = Field(pattern=DATE)
    end: str = Field(pattern=DATE)
    studentId: str | None = Field(default=None, max_length=64)
    classes: dict[str, str] = Field(default_factory=dict)


class PrintBody(BaseModel):
    # Serbest yol DEĞİL: servis dosyanın rapor klasöründe olduğunu ayrıca doğrular.
    path: str = Field(min_length=1, max_length=4096)
    copies: int = Field(default=1, ge=1, le=20)


class SavedBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    params: dict[str, Any] = Field(default_factory=dict)


@router.post("/report")
async def report(
    body: ReportBody,
    user: CurrentUser = requires("bbd_canteen_reports.view"),
) -> dict[str, Any]:
    """Tüm kırılımları tek çağrıda üretir: genel, öğrenci, ürün, sınıf, karşılaştırma."""
    return await service().report(body.start, body.end, refresh=body.refresh,
                                  classes=body.classes, compare_previous=body.compare)


@router.get("/students/{kantin_id}")
async def student_detail(
    kantin_id: str,
    start: str,
    end: str,
    user: CurrentUser = requires("bbd_canteen_reports.view"),
) -> dict[str, Any]:
    """Tek öğrencinin dökümü — karne PDF'inin de kaynağı."""
    return await service().student_detail(kantin_id, start, end)


@router.get("/cache")
async def cache_state(
    user: CurrentUser = requires("bbd_canteen_reports.view"),
) -> dict[str, Any]:
    """Önbellekte hangi günler var — kantin erişilemese de ne okunabileceğini söyler."""
    return await service().cache_state()


@router.get("/saved")
async def saved(
    user: CurrentUser = requires("bbd_canteen_reports.view"),
) -> dict[str, Any]:
    return {"reports": await service().saved_reports()}


@router.put("/saved")
async def save_report(
    body: SavedBody,
    user: CurrentUser = requires("bbd_canteen_reports.view"),
) -> dict[str, Any]:
    return await service().save_report(body.name, body.params, actor=user.full_name)


@router.delete("/saved/{report_id}")
async def delete_report(
    report_id: int,
    user: CurrentUser = requires("bbd_canteen_reports.view"),
) -> dict[str, Any]:
    return await service().delete_report(report_id)


@router.post("/export")
async def export(
    body: ExportBody,
    user: CurrentUser = requires("bbd_canteen_reports.export"),
) -> dict[str, Any]:
    """PDF/CSV üretir, masaüstündeki rapor klasörüne yazar ve yolunu döner."""
    return await service().export(body.kind, body.model_dump())


@router.post("/preview")
async def preview(
    body: ExportBody,
    user: CurrentUser = requires("bbd_canteen_reports.export"),
) -> dict[str, Any]:
    """Raporu üretir ve BASILACAK PDF'in sayfalarını görüntü olarak döner."""
    return await service().preview(body.kind, body.model_dump())


@router.get("/printer")
async def printer_status(
    user: CurrentUser = requires("bbd_canteen_reports.view"),
) -> dict[str, Any]:
    """Yazıcı hazır mı, hangi cihaza gidilecek."""
    return await service().printer_status()


@router.post("/print")
async def print_report(
    body: PrintBody,
    user: CurrentUser = requires("bbd_canteen_reports.print"),
) -> dict[str, Any]:
    """Üretilmiş raporu USB'deki yazıcıya gönderir. Onay sorulmaz, doğrudan basar."""
    return await service().print_report(body.path, copies=body.copies)
