"""Zil Sistemi — HTTP yüzeyi.

ÇİFT KAPI (K9): router `view`; ayar `manage`, elle çalma `ring_now`.
"""

from __future__ import annotations

from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, requires

from ..service import BellService

router = APIRouter()

_service: BellService | None = None


def bind(service: BellService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> BellService:
    if _service is None:  # pragma: no cover - yükleme sırası garanti eder
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


class SettingsBody(BaseModel):
    settings: dict[str, Any] = Field(default_factory=dict)


class RingBody(BaseModel):
    groupId: str = Field(default="", max_length=64)
    sound: str = Field(default="", max_length=120)
    volume: int | None = Field(default=None, ge=0, le=100)


@router.get("/state")
async def state(user: CurrentUser = requires("bell.view")) -> dict[str, Any]:
    """Ayarlar, ders grupları, ses aygıtı, zamanlayıcı durumu ve çalma günlüğü."""
    return await service().state()


@router.put("/settings")
async def save_settings(
    body: SettingsBody,
    user: CurrentUser = requires("bell.manage"),
) -> dict[str, Any]:
    return await service().save(body.settings, actor=user.full_name)


@router.post("/adopt")
async def adopt(
    body: SettingsBody,
    user: CurrentUser = requires("bell.manage"),
) -> dict[str, Any]:
    """Tarayıcı belleğindeki eski ayarı bir kez içeri alır (yalnız boşsa)."""
    return await service().adopt(body.settings, actor=user.full_name)


@router.post("/ring")
async def ring(
    body: RingBody,
    user: CurrentUser = requires("bell.ring_now"),
) -> dict[str, Any]:
    """Zili şimdi çalar. Sonuç (başarısızlık dahil) günlüğe yazılır."""
    return await service().ring(group_id=body.groupId, edge="manual",
                                sound=body.sound, volume=body.volume)


@router.post("/reschedule")
async def reschedule(
    user: CurrentUser = requires("bell.manage"),
) -> dict[str, Any]:
    """Ders saatlerinden tetikleyici tablosunu yeniden kurar."""
    return await service().reschedule()
