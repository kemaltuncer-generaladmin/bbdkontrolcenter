"""Tablet-only API. Every route checks a scoped device or student bearer."""

from __future__ import annotations

from typing import Any

from fastapi import Header

from km_sdk import APIRouter, BaseModel, Field, HTTPException, Query

from .routes import current_service

router = APIRouter()


def bearer(authorization: str) -> str:
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value:
        raise HTTPException(status_code=401, detail="Bearer gerekli.")
    return value


async def device_auth(authorization: str) -> dict[str, Any]:
    device = await current_service().device_for_token(bearer(authorization))
    if not device:
        raise HTTPException(status_code=401, detail="Cihaz yetkisi geçersiz.")
    return device


async def session_auth(authorization: str) -> dict[str, Any]:
    session = await current_service().session_for_token(bearer(authorization))
    if not session:
        raise HTTPException(status_code=401, detail="Öğrenci oturumu geçersiz.")
    return session


async def usage_batch_session_auth(authorization: str) -> dict[str, Any]:
    session = await current_service().session_for_usage_batch_token(bearer(authorization))
    if not session:
        raise HTTPException(status_code=401, detail="Öğrenci oturumu geçersiz veya eşitleme süresi dolmuş.")
    return session


class EnrollBody(BaseModel):
    enrollmentCode: str = Field(min_length=8, max_length=64)
    deviceUuid: str = Field(min_length=8, max_length=100)
    deviceName: str = Field(min_length=2, max_length=100)
    manufacturer: str = Field(default="", max_length=100)
    model: str = Field(default="", max_length=100)
    androidVersion: str = Field(default="", max_length=40)
    appVersion: str = Field(default="", max_length=40)


class LoginBody(BaseModel):
    deviceId: str
    studentCode: str = Field(min_length=6, max_length=32)


class LogoutBody(BaseModel):
    deviceId: str
    sessionId: str


class HeartbeatBody(BaseModel):
    appVersion: str = Field(default="", max_length=40)
    profileRevision: int = Field(default=0, ge=0)
    activeStudentId: str | None = None
    sessionId: str | None = None
    lastPolicyAppliedAt: str | None = None
    policyHealth: str = Field(default="", max_length=100)


class InventoryApp(BaseModel):
    packageName: str = Field(min_length=2, max_length=255)
    appLabel: str = Field(default="", max_length=120)
    versionName: str = Field(default="", max_length=100)
    versionCode: str | int = ""
    isSystemApp: bool = False
    isLaunchable: bool = False
    installedAt: str | None = None
    updatedAt: str | None = None


class InventoryBody(BaseModel):
    apps: list[InventoryApp] = Field(default_factory=list, max_length=2000)


class UsageItem(BaseModel):
    packageName: str = Field(min_length=2, max_length=255)
    localDate: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    usedSeconds: int = Field(ge=0, le=86400)


class UsageBatchBody(BaseModel):
    batchId: str = Field(min_length=8, max_length=100)
    deviceId: str
    sessionId: str
    studentId: str
    startedAt: str
    endedAt: str
    usages: list[UsageItem] = Field(min_length=1, max_length=200)


@router.post("/devices/enroll")
async def enroll(body: EnrollBody) -> dict[str, Any]:
    try:
        return await current_service().enroll(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/profiles")
async def profiles(authorization: str = Header(default="")) -> list[dict[str, Any]]:
    await device_auth(authorization)
    rows = await current_service().store.fetch_all(
        f"SELECT id FROM {current_service().profiles} WHERE revision > 0 ORDER BY name"
    )
    return [profile for row in rows if (profile := await current_service().profile(row["id"]))]


@router.get("/profiles/{profile_id}")
async def profile(profile_id: str, authorization: str = Header(default="")) -> dict[str, Any]:
    await device_auth(authorization)
    result = await current_service().profile(profile_id)
    if not result:
        raise HTTPException(status_code=404, detail="Profil bulunamadı.")
    return result


@router.get("/devices/{device_id}/policy")
async def policy(device_id: str, currentRevision: int = Query(default=0, ge=0),
                 sessionId: str | None = None, authorization: str = Header(default="")) -> dict[str, Any]:
    device = await device_auth(authorization)
    if device["id"] != device_id:
        raise HTTPException(status_code=403, detail="Bu tablet için yetki yok.")
    await current_service().expire_stale_sessions()
    session = None
    if sessionId:
        session = await current_service().store.fetch_one(
            f"SELECT * FROM {current_service().sessions} "
            "WHERE id=? AND device_id=? AND state='active'", (sessionId, device_id),
        )
        if not session:
            raise HTTPException(status_code=403, detail="Oturum bu tablete ait değil.")
    else:
        session = await current_service().store.fetch_one(
            f"SELECT * FROM {current_service().sessions} "
            "WHERE device_id=? AND state='active'", (device_id,),
        )
    try:
        return await current_service().policy(
            device=device, revision=currentRevision, session=dict(session) if session else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/devices/{device_id}/heartbeat")
async def heartbeat(device_id: str, body: HeartbeatBody,
                    authorization: str = Header(default="")) -> dict[str, Any]:
    device = await device_auth(authorization)
    if device["id"] != device_id:
        raise HTTPException(status_code=403, detail="Bu tablet için yetki yok.")
    try:
        return await current_service().heartbeat(device=device, data=body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.put("/devices/{device_id}/apps")
async def inventory(device_id: str, body: InventoryBody,
                    authorization: str = Header(default="")) -> dict[str, Any]:
    device = await device_auth(authorization)
    if device["id"] != device_id:
        raise HTTPException(status_code=403, detail="Bu tablet için yetki yok.")
    return await current_service().replace_inventory(
        device=device, apps=[item.model_dump() for item in body.apps],
    )


@router.post("/auth/login")
async def login(body: LoginBody, authorization: str = Header(default="")) -> dict[str, Any]:
    device = await device_auth(authorization)
    if device["id"] != body.deviceId:
        raise HTTPException(status_code=403, detail="Bu tablet için yetki yok.")
    try:
        return await current_service().login(device=device, student_code=body.studentCode)
    except ValueError as exc:
        detail = str(exc)
        status = 429 if "Çok fazla" in detail else 409 if "başka" in detail \
            else 401 if "şifresi geçersiz" in detail else 400
        payload: str | dict[str, str] = (
            {"code": "SESSION_CONFLICT", "message": detail}
            if status == 409 else detail
        )
        raise HTTPException(status_code=status, detail=payload) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/auth/logout")
async def logout(body: LogoutBody, authorization: str = Header(default="")) -> dict[str, Any]:
    session = await session_auth(authorization)
    if session["id"] != body.sessionId or session["device_id"] != body.deviceId:
        raise HTTPException(status_code=403, detail="Oturum eşleşmiyor.")
    return await current_service().logout(session=session)


@router.post("/usage/batch")
async def usage_batch(body: UsageBatchBody, authorization: str = Header(default="")) -> dict[str, Any]:
    session = await usage_batch_session_auth(authorization)
    try:
        return await current_service().usage_batch(session=session, data=body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
