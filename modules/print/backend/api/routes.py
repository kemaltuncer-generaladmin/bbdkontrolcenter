"""Çıktı Merkezi — HTTP yüzeyi.

ÇİFT KAPI (K9): router'ın tabanı `print.view` (manifest), yeniden basma ayrıca
`print.reprint` ister. Görüntüleyebilen kullanıcı basamaz; bu ayrım ADR 0019
§6'nın kendisidir — kayıtlar öğrenci adı ve tutar taşıyan dosyaların adlarını
tutar, basmak ise ayrı bir eylemdir.

Panelin "yeniden bas" düğmesini çizip çizmeyeceği de buradan söylenir
(`canReprint`); ama düğmenin gizlenmesi yetkilendirme DEĞİLDİR — uç nokta izni
bağımsız olarak sorar.
"""

from __future__ import annotations

from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..service import OutputsService

router = APIRouter()

_service: OutputsService | None = None


def bind(service: OutputsService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> OutputsService:
    if _service is None:  # pragma: no cover - yükleme sırası garanti eder
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


class TargetBody(BaseModel):
    id: str = Field(default="", max_length=64)


class ReprintBody(TargetBody):
    copies: int = Field(default=1, ge=1, le=100)


# --------------------------------------------------------------------- okuma


@router.get("/outputs")
async def outputs(
    search: str = Query(default="", max_length=120),
    start: str = Query(default="", max_length=10),
    end: str = Query(default="", max_length=10),
    kind: str = Query(default="", max_length=32),
    source: str = Query(default="", max_length=64),
    user_id: str = Query(default="", max_length=64, alias="user"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    user: CurrentUser = requires("print.view"),
) -> dict[str, Any]:
    """Süzülmüş çıktı listesi, süzgeç seçenekleri ve ekranın durum bilgisi."""
    return await service().outputs(
        search=search, start=start, end=end, kind=kind, source=source,
        user=user_id, page=page, size=size,
        can_reprint=user.has_permission("print.reprint"),
    )


@router.get("/printer")
async def printer(user: CurrentUser = requires("print.view")) -> dict[str, Any]:
    """Yazıcı durumu — hata fırlatmaz, ne olduğunu anlatır."""
    return await service().printer_status()


@router.post("/preview")
async def preview(
    body: TargetBody,
    user: CurrentUser = requires("print.view"),
) -> dict[str, Any]:
    """Kayıtlı dosyanın kendisini önizler; raporu YENİDEN ÜRETMEZ."""
    return await service().preview(body.id)


# -------------------------------------------------------------------- eylem


@router.post("/reprint")
async def reprint(
    body: ReprintBody,
    user: CurrentUser = requires("print.reprint"),
) -> dict[str, Any]:
    """Kayıtlı dosyayı yeniden yazıcıya gönderir. Sayaç DENEME sayar (ADR 0014)."""
    return await service().reprint(body.id, copies=body.copies, actor=user.full_name)


@router.post("/folder")
async def folder(
    body: TargetBody,
    user: CurrentUser = requires("print.view"),
) -> dict[str, Any]:
    """Çıktının klasörünü dosya yöneticisinde açar."""
    return await service().open_folder(body.id)
