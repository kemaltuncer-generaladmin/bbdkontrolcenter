"""Kantin Yedekleri — HTTP yüzeyi.

ÇİFT KAPI (K9): router `view`; yedek aldırma ve indirme `manage`.
GERİ YÜKLEME UCU YOKTUR — bilinçli.
"""

from __future__ import annotations

from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, requires

from ..service import BackupService

router = APIRouter()

_service: BackupService | None = None


def bind(service: BackupService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> BackupService:
    if _service is None:  # pragma: no cover - yükleme sırası garanti eder
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


class DownloadBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)


@router.get("/overview")
async def overview(user: CurrentUser = requires("bbd_canteen_backups.view")) -> dict[str, Any]:
    """Sunucudaki yedekler, tazelik durumu ve buradaki doğrulanmış kopyalar."""
    return await service().overview()


@router.post("/create")
async def create(
    user: CurrentUser = requires("bbd_canteen_backups.manage"),
) -> dict[str, Any]:
    """Sunucuda elle yedek aldırır (`db:backup`)."""
    return await service().create()


@router.post("/download")
async def download(
    body: DownloadBody,
    user: CurrentUser = requires("bbd_canteen_backups.manage"),
) -> dict[str, Any]:
    """Yedeği bu makineye indirir ve sha256 ile doğrular."""
    return await service().download(body.name, actor=user.full_name)


@router.post("/verify")
async def verify(
    user: CurrentUser = requires("bbd_canteen_backups.view"),
) -> dict[str, Any]:
    """Yereldeki kopyaları yeniden özetleyip bozulma var mı bakar."""
    return await service().verify_local()
