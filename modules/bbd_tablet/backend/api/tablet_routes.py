"""Tablet-only API. Every route checks a scoped device or student bearer.

Machine-readable errors use the contract shape
`{"detail": {"code": "...", "message": "...", ...}}`; the remaining plain-text
details are generic errors on the tablet side.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Header, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.routing import APIRoute

from km_sdk import APIRouter, BaseModel, Field, HTTPException, Query

from ..service import NotFoundError, TabletError
from .routes import current_service


class CodedErrorRoute(APIRoute):
    """Coded tablet errors leave this router in the contract shape.

    The core wraps every HTTPException as `{"error": {"status", "message"}}`
    and replaces a non-text detail with a generic message, which would strip
    the code the tablet acts on (removed pairing, wrong code, rate limit).
    Plain-text errors still go through the core envelope.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def coded_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except HTTPException as exc:
                if isinstance(exc.detail, dict) and "code" in exc.detail:
                    return JSONResponse(status_code=exc.status_code,
                                        content={"detail": exc.detail}, headers=exc.headers)
                raise

        return coded_handler


router = APIRouter(route_class=CodedErrorRoute)


def coded(error: TabletError) -> HTTPException:
    headers = None
    if "retryAfterSeconds" in error.extra:
        headers = {"Retry-After": str(error.extra["retryAfterSeconds"])}
    return HTTPException(status_code=error.status, detail=error.detail(), headers=headers)


def fail(code: str, message: str, **extra: Any) -> HTTPException:
    return coded(TabletError(code, message, **extra))


def token(authorization: str) -> str:
    scheme, _, value = authorization.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


def bearer(authorization: str) -> str:
    value = token(authorization)
    if not value:
        raise HTTPException(status_code=401, detail="Bearer gerekli.")
    return value


async def device_auth(authorization: str, device_id: str | None = None) -> dict[str, Any]:
    """Device bearer: unknown → DEVICE_AUTH_INVALID, revoked → DEVICE_REVOKED."""
    value = token(authorization)
    device = await current_service().device_by_token(value) if value else None
    if device is None:
        raise fail("DEVICE_AUTH_INVALID", "Tablet tanınmadı. Yeniden eşleyin.")
    if device["revoked_at"]:
        raise fail("DEVICE_REVOKED", "Bu tabletin eşlemesi kaldırıldı.")
    if device_id is not None and device["id"] != device_id:
        raise fail("FORBIDDEN_DEVICE", "Bu işlem bu tablete ait değil.")
    return device


async def logout_session_auth(authorization: str) -> dict[str, Any]:
    # Logout is a terminal, write-only operation. It must remain retryable after
    # normal session expiry and after the server accepted a response-lost request.
    session = await current_service().session_for_logout_token(bearer(authorization))
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
    profileId: str | None = Field(default=None, min_length=1, max_length=100)
    activeStudentId: str | None = None
    sessionId: str | None = None
    lastPolicyAppliedAt: str | None = None
    policyHealth: str = Field(default="", max_length=100)


class CommandResult(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    ok: bool = False
    message: str = Field(default="", max_length=500)


class ActiveInstall(BaseModel):
    releaseId: str = Field(min_length=1, max_length=100)
    receivedBytes: int = Field(default=0, ge=0)


class InstallResult(BaseModel):
    releaseId: str = Field(min_length=1, max_length=100)
    ok: bool = False
    message: str = Field(default="", max_length=500)
    versionCode: int | None = Field(default=None, ge=0)


ReleaseId = Annotated[str, Field(min_length=1, max_length=100)]


class InstallsBody(BaseModel):
    """Sözleşme v2.1 `installs`; the service reads at most 50 results."""

    wanted: list[ReleaseId] = Field(default_factory=list, max_length=500)
    priority: str | None = Field(default=None, max_length=100)
    active: ActiveInstall | None = None
    results: list[InstallResult] = Field(default_factory=list, max_length=500)


class SyncBody(BaseModel):
    """Every field is optional; unknown fields are ignored for forward compatibility."""

    appVersion: str | None = Field(default=None, max_length=40)
    profileId: str | None = Field(default=None, max_length=100)
    profileRevision: int | None = Field(default=None, ge=0)
    sessionId: str | None = Field(default=None, max_length=100)
    activeStudentId: str | None = Field(default=None, max_length=100)
    grantsVersion: str | None = Field(default=None, max_length=200)
    deviceStateVersion: int | None = Field(default=None, ge=0)
    policyHealth: str | None = Field(default=None, max_length=100)
    lastPolicyAppliedAt: str | None = Field(default=None, max_length=64)
    batteryPercent: int | None = Field(default=None, ge=0, le=100)
    charging: bool | None = None
    foregroundPackage: str | None = Field(default=None, max_length=255)
    pendingUsageBatches: int | None = Field(default=None, ge=0, le=1_000_000)
    # Generous on purpose: a backlog must never turn every sync into a 422. The
    # service reads the first 100 and the tablet reports the rest on later syncs.
    commandResults: list[CommandResult] = Field(default_factory=list, max_length=1000)
    appsVersion: str | None = Field(default=None, max_length=200)
    installs: InstallsBody | None = None


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


class InitialProfileBody(BaseModel):
    profileId: str | None = Field(default=None, min_length=1)
    name: str = Field(default="Öğrenci profili", min_length=2, max_length=100)
    timezone: str = Field(default="Europe/Istanbul", min_length=1, max_length=80)
    apps: list[dict[str, Any]] = Field(default_factory=list, max_length=1000)


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
async def profiles(authorization: str = Header(default="")) -> dict[str, Any]:
    await device_auth(authorization)
    return {"profiles": await current_service().all_profiles()}


@router.post("/devices/{device_id}/initial-profile")
async def initial_profile(device_id: str, body: InitialProfileBody,
                          authorization: str = Header(default="")) -> dict[str, Any]:
    await device_auth(authorization, device_id)
    try:
        return await current_service().configure_initial_profile(
            device_id=device_id, profile_id=body.profileId,
            name=body.name, timezone=body.timezone, apps=body.apps,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/profiles/{profile_id}")
async def profile(profile_id: str, authorization: str = Header(default="")) -> dict[str, Any]:
    await device_auth(authorization)
    result = await current_service().profile(profile_id)
    if not result:
        raise HTTPException(status_code=404, detail="Profil bulunamadı.")
    return result


def public_origin(request: Request) -> str:
    """Scheme + host the tablet reached us on, for absolute download URLs.

    The `public_base_url` setting wins. Otherwise the request tells: behind
    Cloudflare `CF-Visitor` carries the visitor's scheme (Traefik may speak
    plain HTTP to the container and uvicorn does not trust forwarded headers
    from the proxy network), then `X-Forwarded-Proto`/`-Host`, then `Host`.
    The answer goes only to the authenticated tablet that asked, so a forged
    header misleads no one else.
    """
    configured = current_service().public_base_url
    if configured:
        return configured
    headers = request.headers
    scheme = ""
    try:
        scheme = str(json.loads(headers.get("cf-visitor") or "{}").get("scheme") or "")
    except (ValueError, AttributeError):
        scheme = ""
    if scheme not in ("http", "https"):
        scheme = headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    if scheme not in ("http", "https"):
        scheme = request.url.scheme
    host = headers.get("x-forwarded-host", "").split(",")[0].strip() \
        or headers.get("host", "").strip() or request.url.netloc
    return f"{scheme}://{host}"


def api_root(request: Request) -> str:
    """`https://host/api/tablet/v1` — the router's mount prefix included."""
    prefix = request.url.path.rsplit("/devices/", 1)[0]
    return f"{public_origin(request)}{prefix}"


@router.post("/devices/{device_id}/sync")
async def sync(device_id: str, body: SyncBody, request: Request,
               authorization: str = Header(default="")) -> dict[str, Any]:
    device = await device_auth(authorization, device_id)
    data = body.model_dump()
    if "appsVersion" not in body.model_fields_set:
        # An APK without app distribution does not send the key; it must not
        # get the whole catalog on every sync. Key present with null → catalog.
        del data["appsVersion"]
    return await current_service().sync(
        device=device, data=data,
        base_url=api_root(request) if data.get("installs") is not None else "",
    )


@router.get("/devices/{device_id}/releases/{release_id}/files/{index}")
async def release_file(device_id: str, release_id: str, index: int,
                       authorization: str = Header(default="")) -> FileResponse:
    """A distributed package file; only the tablet holding the lease may read it.

    `Range: bytes=N-` answers 206. `application/zip` (an APK is a zip) keeps
    the core's GZip middleware away from full answers too — compressing
    gigabytes would burn CPU and break `Content-Length` and resuming.
    `no-store` and the extension-less URL keep Cloudflare from caching.
    """
    await device_auth(authorization, device_id)
    try:
        path = await current_service().distribution.download_path(
            device_id=device_id, release_id=release_id, index=index,
        )
    except TabletError as exc:
        raise coded(exc) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type="application/zip",
                        headers={"Cache-Control": "no-store"})


@router.get("/devices/{device_id}/policy")
async def policy(device_id: str, currentRevision: int = Query(default=0, ge=0),
                 sessionId: str | None = None, authorization: str = Header(default="")) -> dict[str, Any]:
    """Eski APK'lar için; yeni sürüm `sync` kullanır."""
    device = await device_auth(authorization, device_id)
    service = current_service()
    await service.expire_stale_sessions()
    session = None
    if sessionId:
        session = await service.store.fetch_one(
            f"SELECT * FROM {service.sessions} "
            "WHERE id=? AND device_id=? AND state='active'", (sessionId, device_id),
        )
        if not session:
            raise fail("FORBIDDEN_DEVICE", "Oturum bu tablete ait değil.")
    else:
        session = await service.store.fetch_one(
            f"SELECT * FROM {service.sessions} "
            "WHERE device_id=? AND state='active'", (device_id,),
        )
    try:
        return await service.policy(
            device=device, revision=currentRevision, session=dict(session) if session else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/devices/{device_id}/heartbeat")
async def heartbeat(device_id: str, body: HeartbeatBody,
                    authorization: str = Header(default="")) -> dict[str, Any]:
    """Eski APK'lar için; yeni sürüm `sync` kullanır."""
    device = await device_auth(authorization, device_id)
    try:
        return await current_service().heartbeat(device=device, data=body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/devices/{device_id}/unpair")
async def unpair(device_id: str, authorization: str = Header(default="")) -> dict[str, Any]:
    service = current_service()
    value = token(authorization)
    # An already-revoked token has one remaining capability: retry its own
    # unpair and retrieve the stable acknowledgement after a lost response.
    device = await service.device_by_token(value) if value else None
    if device is None:
        raise fail("DEVICE_AUTH_INVALID", "Tablet tanınmadı. Yeniden eşleyin.")
    if device["id"] != device_id:
        raise fail("FORBIDDEN_DEVICE", "Bu işlem bu tablete ait değil.")
    try:
        return await service.revoke_device(device_id=device_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/devices/{device_id}/apps")
async def inventory(device_id: str, body: InventoryBody,
                    authorization: str = Header(default="")) -> dict[str, Any]:
    device = await device_auth(authorization, device_id)
    return await current_service().replace_inventory(
        device=device, apps=[item.model_dump() for item in body.apps],
    )


@router.post("/auth/login")
async def login(body: LoginBody, authorization: str = Header(default="")) -> dict[str, Any]:
    device = await device_auth(authorization, body.deviceId)
    try:
        return await current_service().login(device=device, student_code=body.studentCode)
    except TabletError as exc:
        raise coded(exc) from exc


@router.post("/auth/logout")
async def logout(body: LogoutBody, authorization: str = Header(default="")) -> dict[str, Any]:
    session = await logout_session_auth(authorization)
    if session["id"] != body.sessionId or session["device_id"] != body.deviceId:
        raise fail("FORBIDDEN_DEVICE", "Oturum bu tablete ait değil.")
    return await current_service().logout(session=session)


@router.post("/usage/batch")
async def usage_batch(body: UsageBatchBody, authorization: str = Header(default="")) -> dict[str, Any]:
    session = await usage_batch_session_auth(authorization)
    try:
        return await current_service().usage_batch(session=session, data=body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
