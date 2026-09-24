"""Control Center's authenticated tablet management endpoints."""

from __future__ import annotations

from datetime import date
from typing import Any
from zoneinfo import ZoneInfoNotFoundError

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, requires

from ..service import TabletService

router = APIRouter()
_service: TabletService | None = None


def bind(service: TabletService) -> APIRouter:
    global _service
    _service = service
    return router


def current_service() -> TabletService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Tablet modülü hazır değil.")
    return _service


class AppPolicyBody(BaseModel):
    packageName: str = Field(min_length=2, max_length=255)
    appName: str = Field(default="", max_length=120)
    allowed: bool = False
    unlimited: bool = False
    dailyLimitSeconds: int = Field(default=0, ge=0, le=86400)


class ProfileBody(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    timezone: str = Field(default="Europe/Istanbul", min_length=1, max_length=80)
    apps: list[AppPolicyBody] = Field(default_factory=list, max_length=1000)


class DeviceBody(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    profileId: str = Field(min_length=1)


class AssignBody(BaseModel):
    profileId: str = Field(min_length=1)


class GrantBody(BaseModel):
    extraSeconds: int = Field(gt=0, le=86400)
    localDate: date
    reason: str = Field(default="", max_length=300)


@router.get("/overview")
async def overview(user: CurrentUser = requires("bbd_tablet.view")) -> dict[str, Any]:
    return await current_service().overview()


@router.get("/students")
async def students(user: CurrentUser = requires("bbd_tablet.view")) -> list[dict[str, str]]:
    return await current_service().students()


@router.get("/students/{student_id}/usage")
async def student_usage(student_id: str, localDate: date,
                        user: CurrentUser = requires("bbd_tablet.view")) -> dict[str, Any]:
    return await current_service().student_usage(student_id, localDate.isoformat())


@router.get("/devices/{device_id}/apps")
async def device_apps(device_id: str,
                      user: CurrentUser = requires("bbd_tablet.view")) -> list[dict[str, Any]]:
    rows = await current_service().store.fetch_all(
        f"SELECT package_name, app_label, is_launchable, is_system_app "
        f"FROM {current_service().inventory} WHERE device_id=? ORDER BY app_label",
        (device_id,),
    )
    return [{"packageName": row["package_name"], "appName": row["app_label"],
             "isLaunchable": bool(row["is_launchable"]),
             "isSystemApp": bool(row["is_system_app"])} for row in rows]


@router.post("/profiles")
async def create_profile(body: ProfileBody,
                         user: CurrentUser = requires("bbd_tablet.manage")) -> dict[str, Any]:
    try:
        return await current_service().save_profile(
            profile_id=None, name=body.name, timezone=body.timezone,
            apps=[item.model_dump() for item in body.apps],
        )
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/profiles/{profile_id}")
async def update_profile(profile_id: str, body: ProfileBody,
                         user: CurrentUser = requires("bbd_tablet.manage")) -> dict[str, Any]:
    try:
        return await current_service().save_profile(
            profile_id=profile_id, name=body.name, timezone=body.timezone,
            apps=[item.model_dump() for item in body.apps],
        )
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/devices")
async def create_device(body: DeviceBody,
                        user: CurrentUser = requires("bbd_tablet.manage")) -> dict[str, Any]:
    try:
        return await current_service().create_device(name=body.name, profile_id=body.profileId)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/devices/{device_id}/assign-profile")
async def assign_profile(device_id: str, body: AssignBody,
                         user: CurrentUser = requires("bbd_tablet.manage")) -> dict[str, Any]:
    try:
        return await current_service().assign_profile(device_id=device_id, profile_id=body.profileId)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/students/{student_id}/apps/{package_name}/grants")
async def create_grant(student_id: str, package_name: str, body: GrantBody,
                       user: CurrentUser = requires("bbd_tablet.grant")) -> dict[str, Any]:
    try:
        return await current_service().grant_time(
            student_id=student_id, package_name=package_name,
            local_date=body.localDate.isoformat(), extra_seconds=body.extraSeconds,
            reason=body.reason, actor=user.full_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
