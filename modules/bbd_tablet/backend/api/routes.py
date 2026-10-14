"""Control Center's authenticated tablet management endpoints.

ÇİFT KAPI (K9): router tabanı `bbd_tablet.view`; tablet, profil, oturum ve
komut değişiklikleri `bbd_tablet.manage`, ek süre, öğrenci kısıtlaması ve
giriş kilidini kaldırma `bbd_tablet.grant` ister. Eşleme iptali, tablet silme
(tekli ve toplu), profil silme ve öğrenci verisi silme geri alınamaz: izin
yeterli olsa bile PIN teyidi (`confirm_pin`) ister. PIN gövdede taşınır, sorgu
dizesinde değil.

Uygulama dağıtımı: listeler `view`; yükleme, yayın, öne alma `manage`;
dağıtımı bitirmek (`DELETE /apps/{package}`) ayrıca PIN ister. Tanı raporu
listesi `view`, raporun metni `manage` ister.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Any, Literal

from fastapi import Request

from km_sdk import (
    APIRouter,
    BaseModel,
    CurrentUser,
    Field,
    HTTPException,
    Query,
    confirm_pin,
    requires,
)

from ..distribution import CHUNK_BASE64_MAX
from ..errors import StorageError
from ..service import CanteenUnavailableError, ConflictError, NotFoundError, TabletService

router = APIRouter()
_service: TabletService | None = None

VIEW = "bbd_tablet.view"
MANAGE = "bbd_tablet.manage"
GRANT = "bbd_tablet.grant"


#: `events.outbox` çözücüsü (ADR 0029). KM EKRANINDAN yapılan kısıt / ek süre
#: / profil değişikliği BBD Akademi'ye olay olarak gider (km-merkezi-senkron
#: §2.2 `tablet.policy_changed`, `tablet.profile_changed`). Geçidin (Akademi)
#: kendi yazmaları olayı geçitte, `origin: akademi` ile yazar — bu yüzden olay
#: yeteneklerde değil bu uçlardadır.
_outbox: Callable[[], Any] | None = None


def bind(service: TabletService, outbox: Callable[[], Any] | None = None) -> APIRouter:
    global _service, _outbox
    _service = service
    _outbox = outbox
    return router


def current_service() -> TabletService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Tablet modülü hazır değil.")
    return _service


async def announce(kind: str, subject: tuple[str, str | None], fields: list[str]) -> None:
    """Yazma BAŞARILI olduktan sonra outbox olayı. Olay yazılamazsa yazma yine
    tamamdır (K7); Akademi 2 dk yedek yoklamasıyla yetişir."""
    box = _outbox() if _outbox is not None else None
    if box is None:
        return
    try:
        await box.emit(kind, subject, fields=fields)
    except Exception:  # noqa: BLE001 — olay yazmayı geri almaz
        return


async def run[T](call: Awaitable[T]) -> T:
    """Service errors to HTTP: missing 404, wrong state 409, bad input 400."""
    try:
        return await call
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConflictError as exc:
        # The core envelope keeps only a text detail, so the panel gets the
        # message itself; a structured detail would arrive as a generic text.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CanteenUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except StorageError as exc:
        # 507 when the disk is full; the panel does not retry it.
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class AppPolicyBody(BaseModel):
    packageName: str = Field(min_length=2, max_length=255)
    appName: str = Field(default="", max_length=120)
    allowed: bool = False
    unlimited: bool = False
    dailyLimitSeconds: int = Field(default=0, ge=0, le=86400)


class UsageWindowBody(BaseModel):
    days: list[int] = Field(min_length=1, max_length=7)
    start: str = Field(pattern=r"^\d{2}:\d{2}$")
    end: str = Field(pattern=r"^\d{2}:\d{2}$")


class ProfileBody(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    timezone: str = Field(default="Europe/Istanbul", min_length=1, max_length=80)
    apps: list[AppPolicyBody] = Field(default_factory=list, max_length=1000)
    systemControls: dict[str, Any] | None = None
    # Omitted keeps the previous revision's hours; [] means always open.
    usageWindows: list[UsageWindowBody] | None = Field(default=None, max_length=21)


class DuplicateBody(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)


class PinBody(BaseModel):
    """Geri dönüşü olmayan işlemin gövdesi — PIN zorunlu."""

    pin: str = Field(min_length=4, max_length=32)


class ProfileDeleteBody(PinBody):
    """Atanmış tabletler ya `replacementProfileId` profiline taşınır ya da
    `unassign` ile profilsiz kalır; ikisi de yoksa 409 `PROFILE_IN_USE`."""

    replacementProfileId: str | None = Field(default=None, min_length=1)
    unassign: bool = False


class DeviceBody(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    profileId: str | None = Field(default=None, min_length=1)


class DevicePatchBody(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    locked: bool | None = None
    lockMessage: str | None = Field(default=None, max_length=200)
    offlineMode: Literal["local", "allowed", "denied"] | None = None
    announcement: str | None = Field(default=None, max_length=500)


class EnrollmentCodeBody(BaseModel):
    profileId: str | None = Field(default=None, min_length=1)


class AssignBody(BaseModel):
    profileId: str = Field(min_length=1)


class CommandBody(BaseModel):
    type: Literal["message", "refresh_inventory", "flush_usage", "reboot",
                  "self_update", "diagnostics"]
    payload: dict[str, Any] = Field(default_factory=dict)


class BulkBody(BaseModel):
    deviceIds: list[str] = Field(min_length=1, max_length=200)
    action: Literal["lock", "unlock", "announce", "assign_profile", "command",
                    "reset_login_lock", "delete"]
    lockMessage: str = Field(default="", max_length=200)
    announcement: str = Field(default="", max_length=500)
    profileId: str | None = None
    command: CommandBody | None = None
    # Yalnız `delete` için; o işlemde zorunludur.
    pin: str | None = Field(default=None, min_length=4, max_length=32)


class UploadBody(BaseModel):
    fileName: str = Field(min_length=1, max_length=255)
    sizeBytes: int = Field(ge=1)
    # Aynı ad + boyut + parmak izi yarım yüklemeyi sürdürür (panel: lastModified).
    fingerprint: str = Field(default="", max_length=100)


class ChunkBody(BaseModel):
    offset: int = Field(ge=0)
    data: str = Field(min_length=1, max_length=CHUNK_BASE64_MAX)


class PublishBody(BaseModel):
    appName: str = Field(default="", max_length=120)


class AppRemoveBody(PinBody):
    """Dağıtımı bitir; `uninstall` ise tabletlerden de kaldırılır."""

    uninstall: bool = False


class EndSessionBody(BaseModel):
    message: str = Field(default="", max_length=200)


class GrantBody(BaseModel):
    extraSeconds: int = Field(gt=0, le=86400)
    localDate: date
    reason: str = Field(default="", max_length=300)


class BlockBody(BaseModel):
    until: datetime
    reason: str = Field(default="", max_length=300)


# --------------------------------------------------------------- görüntüleme


@router.get("/overview")
async def overview(user: CurrentUser = requires(VIEW)) -> dict[str, Any]:
    return await current_service().overview()


@router.get("/students")
async def students(user: CurrentUser = requires(VIEW)) -> list[dict[str, Any]]:
    return await run(current_service().students())


@router.get("/students/{student_id}/usage")
async def student_usage(student_id: str, localDate: date,
                        user: CurrentUser = requires(VIEW)) -> dict[str, Any]:
    return await current_service().student_usage(student_id, localDate.isoformat())


@router.get("/students/{student_id}/blocks")
async def student_blocks(student_id: str,
                         user: CurrentUser = requires(VIEW)) -> list[dict[str, Any]]:
    return await current_service().list_blocks(student_id=student_id)


@router.get("/reports/daily")
async def daily_report(localDate: date, user: CurrentUser = requires(VIEW)) -> dict[str, Any]:
    return await current_service().daily_report(localDate.isoformat())


@router.get("/sessions")
async def sessions(limit: int = Query(default=100, ge=1, le=500),
                   deviceId: str | None = None, studentId: str | None = None,
                   user: CurrentUser = requires(VIEW)) -> list[dict[str, Any]]:
    return await current_service().list_sessions(limit=limit, device_id=deviceId,
                                                  student_id=studentId)


@router.get("/devices/{device_id}/apps")
async def device_apps(device_id: str,
                      user: CurrentUser = requires(VIEW)) -> list[dict[str, Any]]:
    rows = await current_service().store.fetch_all(
        f"SELECT package_name, app_label, is_launchable, is_system_app "
        f"FROM {current_service().inventory} WHERE device_id=? ORDER BY app_label",
        (device_id,),
    )
    return [{"packageName": row["package_name"], "appName": row["app_label"],
             "isLaunchable": bool(row["is_launchable"]),
             "isSystemApp": bool(row["is_system_app"])} for row in rows]


@router.get("/devices/{device_id}/commands")
async def device_commands(device_id: str, limit: int = Query(default=20, ge=1, le=100),
                          user: CurrentUser = requires(VIEW)) -> list[dict[str, Any]]:
    return await current_service().list_commands(device_id=device_id, limit=limit)


@router.get("/devices/{device_id}/diagnostics")
async def device_diagnostics(device_id: str,
                             user: CurrentUser = requires(VIEW)) -> list[dict[str, Any]]:
    """Tabletin son tanı raporları, metinsiz (sözleşme v2.2)."""
    return await current_service().list_diagnostics(device_id=device_id)


@router.get("/devices/{device_id}/diagnostics/{diagnostics_id}")
async def device_diagnostics_text(device_id: str, diagnostics_id: str,
                                  user: CurrentUser = requires(MANAGE)) -> dict[str, Any]:
    """Raporun metni: kurulum ve politika ayrıntısı taşır, `manage` ister."""
    return await run(current_service().get_diagnostics(
        device_id=device_id, diagnostics_id=diagnostics_id,
    ))


# ------------------------------------------------------------------- profiller


@router.post("/profiles")
async def create_profile(body: ProfileBody,
                         user: CurrentUser = requires(MANAGE)) -> dict[str, Any]:
    result = await run(current_service().save_profile(
        profile_id=None, name=body.name, timezone=body.timezone,
        apps=[item.model_dump() for item in body.apps],
        system_controls=body.systemControls,
        usage_windows=None if body.usageWindows is None
        else [item.model_dump() for item in body.usageWindows],
    ))
    await announce("tablet.profile_changed", ("tablet_profile", _id_of(result)), [])
    return result


@router.put("/profiles/{profile_id}")
async def update_profile(profile_id: str, body: ProfileBody,
                         user: CurrentUser = requires(MANAGE)) -> dict[str, Any]:
    result = await run(current_service().save_profile(
        profile_id=profile_id, name=body.name, timezone=body.timezone,
        apps=[item.model_dump() for item in body.apps],
        system_controls=body.systemControls,
        usage_windows=None if body.usageWindows is None
        else [item.model_dump() for item in body.usageWindows],
    ))
    await announce("tablet.profile_changed", ("tablet_profile", profile_id), [])
    return result


@router.post("/profiles/{profile_id}/duplicate")
async def duplicate_profile(profile_id: str, body: DuplicateBody | None = None,
                            user: CurrentUser = requires(MANAGE)) -> dict[str, Any]:
    result = await run(current_service().duplicate_profile(
        profile_id=profile_id, name=body.name if body else None,
    ))
    await announce("tablet.profile_changed", ("tablet_profile", _id_of(result)), [])
    return result


@router.delete("/profiles/{profile_id}")
async def delete_profile(profile_id: str, body: ProfileDeleteBody, request: Request,
                         user: CurrentUser = requires(MANAGE)) -> dict[str, Any]:
    await confirm_pin(request, user, body.pin, action="bbd_tablet.profile.delete")
    result = await run(current_service().delete_profile(
        profile_id=profile_id, replacement_profile_id=body.replacementProfileId,
        unassign=body.unassign,
    ))
    await announce("tablet.profile_changed", ("tablet_profile", profile_id), [])
    return result


# ------------------------------------------------------------------- tabletler


@router.post("/devices")
async def create_device(body: DeviceBody,
                        user: CurrentUser = requires(MANAGE)) -> dict[str, Any]:
    return await run(current_service().create_device(name=body.name, profile_id=body.profileId))


@router.post("/devices/bulk")
async def bulk_devices(body: BulkBody, request: Request,
                       user: CurrentUser = requires(MANAGE)) -> dict[str, Any]:
    # Like the single reset, lifting login locks needs the grant permission too.
    if body.action == "reset_login_lock" and not user.has_permission(GRANT):
        raise HTTPException(status_code=403, detail="Bu işlem için yetkiniz yok.")
    if body.action == "delete":
        if not body.pin:
            raise HTTPException(status_code=422, detail="Toplu silme için PIN gerekli.")
        await confirm_pin(request, user, body.pin, action="bbd_tablet.device.delete")
    return await run(current_service().bulk(
        device_ids=body.deviceIds, action=body.action, actor=user.full_name,
        lock_message=body.lockMessage, announcement=body.announcement,
        profile_id=body.profileId,
        command=body.command.model_dump() if body.command else None,
    ))


@router.patch("/devices/{device_id}")
async def update_device(device_id: str, body: DevicePatchBody,
                        user: CurrentUser = requires(MANAGE)) -> dict[str, Any]:
    changes = body.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(status_code=400, detail="Değiştirilecek alan yok.")
    return await run(current_service().update_device(device_id=device_id, changes=changes))


@router.post("/devices/{device_id}/assign-profile")
async def assign_profile(device_id: str, body: AssignBody,
                         user: CurrentUser = requires(MANAGE)) -> dict[str, Any]:
    return await run(current_service().assign_profile(
        device_id=device_id, profile_id=body.profileId,
    ))


@router.post("/devices/{device_id}/enrollment-code")
async def enrollment_code(device_id: str, body: EnrollmentCodeBody | None = None,
                          user: CurrentUser = requires(MANAGE)) -> dict[str, Any]:
    return await run(current_service().issue_enrollment_code(
        device_id=device_id, profile_id=body.profileId if body else None,
    ))


@router.post("/devices/{device_id}/revoke")
async def revoke_device(device_id: str, body: PinBody, request: Request,
                        user: CurrentUser = requires(MANAGE)) -> dict[str, Any]:
    await confirm_pin(request, user, body.pin, action="bbd_tablet.device.revoke")
    return await run(current_service().revoke_device(device_id=device_id))


@router.delete("/devices/{device_id}")
async def delete_device(device_id: str, body: PinBody, request: Request,
                        user: CurrentUser = requires(MANAGE)) -> dict[str, Any]:
    await confirm_pin(request, user, body.pin, action="bbd_tablet.device.delete")
    return await run(current_service().delete_device(device_id=device_id))


@router.post("/devices/{device_id}/login-lock/reset")
async def reset_login_lock(device_id: str,
                           user: CurrentUser = requires(GRANT)) -> dict[str, Any]:
    return await run(current_service().reset_login_lock(device_id=device_id))


@router.post("/devices/{device_id}/commands")
async def create_command(device_id: str, body: CommandBody,
                         user: CurrentUser = requires(MANAGE)) -> dict[str, Any]:
    return await run(current_service().create_command(
        device_id=device_id, command_type=body.type, payload=body.payload,
        actor=user.full_name,
    ))


@router.delete("/devices/{device_id}/commands/{command_id}")
async def cancel_command(device_id: str, command_id: str,
                         user: CurrentUser = requires(MANAGE)) -> dict[str, Any]:
    return await run(current_service().cancel_command(
        device_id=device_id, command_id=command_id, actor=user.full_name,
    ))


@router.delete("/students/{student_id}/history")
async def delete_student_history(student_id: str, body: PinBody, request: Request,
                                 user: CurrentUser = requires(MANAGE)) -> dict[str, Any]:
    await confirm_pin(request, user, body.pin, action="bbd_tablet.student.history.delete")
    return await run(current_service().delete_student_history(student_id=student_id))


@router.post("/sessions/{session_id}/end")
async def end_session(session_id: str, body: EndSessionBody | None = None,
                      user: CurrentUser = requires(MANAGE)) -> dict[str, Any]:
    return await run(current_service().end_session(
        session_id=session_id, message=(body.message.strip() if body else ""),
    ))


# ------------------------------------------------- ek süre ve kısıtlama


@router.post("/students/{student_id}/apps/{package_name}/grants")
async def create_grant(student_id: str, package_name: str, body: GrantBody,
                       user: CurrentUser = requires(GRANT)) -> dict[str, Any]:
    result = await run(current_service().grant_time(
        student_id=student_id, package_name=package_name,
        local_date=body.localDate.isoformat(), extra_seconds=body.extraSeconds,
        reason=body.reason, actor=user.full_name,
    ))
    await announce("tablet.policy_changed", ("student", student_id), ["grants"])
    return result


@router.post("/students/{student_id}/grants/{grant_id}/revoke")
async def revoke_grant(student_id: str, grant_id: str,
                       user: CurrentUser = requires(GRANT)) -> dict[str, Any]:
    result = await run(current_service().revoke_grant(
        student_id=student_id, grant_id=grant_id, actor=user.full_name,
    ))
    await announce("tablet.policy_changed", ("student", student_id), ["grants"])
    return result


@router.post("/students/{student_id}/block")
async def block_student(student_id: str, body: BlockBody,
                        user: CurrentUser = requires(GRANT)) -> dict[str, Any]:
    result = await run(current_service().block_student(
        student_id=student_id, until=body.until, reason=body.reason, actor=user.full_name,
    ))
    await announce("tablet.policy_changed", ("student", student_id), ["blockedUntil"])
    return result


@router.post("/students/{student_id}/unblock")
async def unblock_student(student_id: str,
                          user: CurrentUser = requires(GRANT)) -> dict[str, Any]:
    result = await current_service().unblock_student(student_id=student_id)
    if result.get("lifted"):
        await announce("tablet.policy_changed", ("student", student_id), ["blockedUntil"])
    return result



# ------------------------------------------------------- uygulama dağıtımı
# Sabit yollar (`/apps/uploads`, `/apps/releases`) `/apps/{package}` yollarından
# önce tanımlanır.


@router.get("/apps")
async def apps(user: CurrentUser = requires(VIEW)) -> dict[str, Any]:
    return await current_service().distribution.overview()


@router.post("/apps/uploads")
async def start_upload(body: UploadBody,
                       user: CurrentUser = requires(MANAGE)) -> dict[str, Any]:
    return await run(current_service().distribution.start_upload(
        file_name=body.fileName, size_bytes=body.sizeBytes, fingerprint=body.fingerprint,
        actor=user.full_name,
    ))


@router.get("/apps/uploads/{upload_id}")
async def upload_status(upload_id: str,
                        user: CurrentUser = requires(MANAGE)) -> dict[str, Any]:
    return await run(current_service().distribution.upload_status(upload_id))


@router.put("/apps/uploads/{upload_id}/chunk")
async def upload_chunk(upload_id: str, body: ChunkBody,
                       user: CurrentUser = requires(MANAGE)) -> dict[str, Any]:
    try:
        return await current_service().distribution.put_chunk(
            upload_id=upload_id, offset=body.offset, data=body.data,
        )
    except ConflictError as exc:
        # The envelope keeps only text; the right offset also rides in a header.
        received = exc.extra.get("receivedBytes")
        headers = {"X-Received-Bytes": str(received)} if received is not None else None
        raise HTTPException(status_code=409, detail=str(exc), headers=headers) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StorageError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/apps/uploads/{upload_id}/finish")
async def finish_upload(upload_id: str,
                        user: CurrentUser = requires(MANAGE)) -> dict[str, Any]:
    """Starts unpacking/hashing in the background and answers `processing` at once."""
    return await run(current_service().distribution.finish_upload(upload_id=upload_id))


@router.post("/apps/releases/{release_id}/publish")
async def publish_release(release_id: str, body: PublishBody | None = None,
                          user: CurrentUser = requires(MANAGE)) -> dict[str, Any]:
    return await run(current_service().distribution.publish(
        release_id=release_id, app_name=body.appName if body else "",
    ))


@router.delete("/apps/releases/{release_id}")
async def delete_release(release_id: str,
                         user: CurrentUser = requires(MANAGE)) -> dict[str, Any]:
    return await run(current_service().distribution.delete_release(release_id=release_id))


@router.get("/apps/{package_name}/devices")
async def app_devices(package_name: str,
                      user: CurrentUser = requires(VIEW)) -> dict[str, Any]:
    return await run(current_service().distribution.app_devices(package_name=package_name))


@router.post("/apps/{package_name}/devices/{device_id}/prioritize")
async def prioritize(package_name: str, device_id: str,
                     user: CurrentUser = requires(MANAGE)) -> dict[str, Any]:
    return await run(current_service().distribution.prioritize(
        package_name=package_name, device_id=device_id,
    ))


@router.delete("/apps/{package_name}")
async def remove_app(package_name: str, body: AppRemoveBody, request: Request,
                     user: CurrentUser = requires(MANAGE)) -> dict[str, Any]:
    await confirm_pin(request, user, body.pin, action="bbd_tablet.app.remove")
    return await run(current_service().distribution.remove_app(
        package_name=package_name, uninstall=body.uninstall, actor=user.full_name,
    ))


def _id_of(result: Any) -> str | None:
    """Profil yanıtının kimliği (`{"id"}` ya da `{"profile": {"id"}}`)."""
    if not isinstance(result, dict):
        return None
    nested = result.get("profile")
    value = result.get("id") or (nested.get("id") if isinstance(nested, dict) else None)
    return str(value) if value else None
