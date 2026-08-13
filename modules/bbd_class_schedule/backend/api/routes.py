"""Ders Takvimi — HTTP yüzeyi."""

from __future__ import annotations

from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, requires

from ..service import ScheduleService

router = APIRouter()

_service: ScheduleService | None = None


def bind(service: ScheduleService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> ScheduleService:
    if _service is None:  # pragma: no cover - yükleme sırası garanti eder
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


class DocumentBody(BaseModel):
    document: dict[str, Any] = Field(default_factory=dict)


@router.get("/groups")
async def read_groups(
    user: CurrentUser = requires("bbd_class_schedule.view"),
) -> dict[str, Any]:
    return await service().read()


@router.put("/groups")
async def write_groups(
    body: DocumentBody,
    user: CurrentUser = requires("bbd_class_schedule.manage"),
) -> dict[str, Any]:
    return await service().write(body.document, actor=user.full_name)


@router.post("/adopt")
async def adopt(
    body: DocumentBody,
    user: CurrentUser = requires("bbd_class_schedule.manage"),
) -> dict[str, Any]:
    """Tarayıcı belleğindeki eski takvimi bir kez içeri alır (yalnız boşsa)."""
    return await service().adopt(body.document, actor=user.full_name)
