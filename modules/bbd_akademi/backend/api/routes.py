"""`/api/bbd_akademi` — vekil, panel köprüsü, PIN teyidi ve personel.

ÇİFT KAPI (K9): router tabanı `bbd_akademi.view` (`module.yaml` →
`http.requires`). Vekil uçlarında asıl kapı işlem başınadır
(`permission_map.py`); personel uçları `bbd_akademi.staff` ister.

Yol düzeni:

    /api/bbd_akademi/km/...          modülün kendi uçları (durum, köprü, PIN, personel)
    /api/bbd_akademi/v1/...          ham vekil → Akademi /v1/... (izin listesi)
    /api/bbd_akademi/<başka her şey>  403 operation_not_allowed

Sıra önemlidir: `km/...` uçları vekil yakalayıcısından ÖNCE tanımlanır.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import Request
from fastapi.responses import JSONResponse, Response

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

from ..gateway import request_id
from ..permission_map import ALL_PERMISSIONS, STAFF, VIEW, km_permissions
from ..proxy import AkademiProxy, ProxyError, decode_upload, error_response
from ..staff import ROLE_LABELS, StaffError, StaffService

router = APIRouter()
_proxy: AkademiProxy | None = None
_staff: StaffService | None = None

METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


def bind(proxy: AkademiProxy, staff: StaffService) -> APIRouter:
    global _proxy, _staff
    _proxy, _staff = proxy, staff
    return router


def proxy() -> AkademiProxy:
    if _proxy is None:
        raise HTTPException(status_code=503, detail="Akademi modülü hazır değil.")
    return _proxy


def staff() -> StaffService:
    if _staff is None:
        raise HTTPException(status_code=503, detail="Akademi modülü hazır değil.")
    return _staff


def _staff_error(failure: StaffError) -> JSONResponse:
    return JSONResponse(status_code=failure.status, content={"error": {
        "code": failure.code, "message": str(failure), "status": failure.status,
        "details": [{"field": failure.field, "message": str(failure)}] if failure.field else None,
        "traceId": None}})


# ------------------------------------------------------------------ gövdeler


class UploadBody(BaseModel):
    field: str = Field(default="file", min_length=1, max_length=40)
    filename: str = Field(min_length=1, max_length=200)
    contentType: str = Field(default="application/octet-stream", max_length=120)
    dataBase64: str = Field(min_length=4)
    fields: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class CallBody(BaseModel):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str = Field(min_length=4, max_length=800)
    query: dict[str, Any] = Field(default_factory=dict)
    body: Any = None
    idempotencyKey: str | None = Field(default=None, max_length=128)
    stepUpToken: str | None = Field(default=None, max_length=200)
    ifNoneMatch: str | None = Field(default=None, max_length=200)
    requestId: str | None = Field(default=None, max_length=128)
    upload: UploadBody | None = None


class StepUpBody(BaseModel):
    """PIN gövdede taşınır, sorgu dizesinde DEĞİL."""

    pin: str = Field(min_length=4, max_length=32)
    operationId: str = Field(min_length=3, max_length=120)


class FetchBody(BaseModel):
    url: str = Field(min_length=8, max_length=4000)


class StaffBody(BaseModel):
    displayName: str = Field(min_length=2, max_length=80)
    phone: str = Field(min_length=10, max_length=20)
    roles: list[Literal["admin", "teacher"]] = Field(min_length=1, max_length=2)
    kmUserId: str | None = Field(default=None, max_length=80)
    note: str = Field(default="", max_length=200)
    active: bool = True


class StaffPatchBody(BaseModel):
    expectedRevision: int = Field(ge=1)
    displayName: str | None = Field(default=None, min_length=2, max_length=80)
    phone: str | None = Field(default=None, min_length=10, max_length=20)
    roles: list[Literal["admin", "teacher"]] | None = Field(default=None, min_length=1,
                                                            max_length=2)
    kmUserId: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=200)
    active: bool | None = None


# --------------------------------------------------------------------- durum


@router.get("/km/status")
async def status(user: CurrentUser = requires(VIEW)) -> dict[str, Any]:
    """Panelin açılış bilgisi: bağlantı, sözleşme sürümü, kullanıcının KM izinleri."""
    from ..allowlist import CONTRACT_VERSION

    gateway = proxy().gateway
    return {
        "connection": await gateway.configured(),
        "contractVersion": CONTRACT_VERSION,
        "operations": len(proxy().allowlist),
        "permissions": [key for key in ALL_PERMISSIONS if user.has_permission(key)],
        "user": {"id": user.id, "name": user.full_name},
        "staffSync": await staff().state() if user.has_permission(STAFF) else None,
    }


@router.get("/km/operations")
async def operations(user: CurrentUser = requires(VIEW)) -> dict[str, Any]:
    """İzin listesi + bu kullanıcının her işlemi yapıp yapamayacağı.

    Panel düğmelerini buna göre çizer (`operationId` → izinli mi). Karar yine
    vekilde verilir; bu yalnız arayüzün gerekçeli kapalı düğme çizebilmesi için.
    """
    items = []
    for operation in proxy().allowlist.operations:
        needed = km_permissions(operation)
        items.append({
            "operationId": operation.operation_id,
            "method": operation.method,
            "path": operation.path,
            "surface": operation.surface,
            "kmPermissions": list(needed),
            "akademiPermissions": list(operation.permissions),
            "stepUp": operation.step_up,
            "money": operation.money,
            "multipart": operation.multipart,
            "featureFlag": operation.feature_flag,
            "allowed": any(user.has_permission(key) for key in needed),
        })
    return {"items": items}


# -------------------------------------------------------------- köprü / PIN


@router.post("/km/call")
async def call(body: CallBody, user: CurrentUser = requires(VIEW)) -> dict[str, Any]:
    upload = None
    if body.upload is not None:
        try:
            upload = decode_upload(body.upload.model_dump())
        except ProxyError as failure:
            return {"ok": False, "status": failure.status, "data": None, "headers": {},
                    "error": {"code": failure.code, "message": failure.message,
                              "details": None, "traceId": None, "status": failure.status}}
    return await proxy().call(
        user, method=body.method, path=body.path, query=body.query, body=body.body,
        idempotency_key=body.idempotencyKey, step_up_token=body.stepUpToken,
        if_none_match=body.ifNoneMatch, upload=upload, trace=body.requestId)


@router.post("/km/step-up")
async def step_up(request: Request, body: StepUpBody,
                  user: CurrentUser = requires(VIEW)) -> Any:
    """PIN'i çekirdeğin teyit kapısından geçirir, tek kullanımlık belirteç verir."""
    operation = proxy().allowlist.by_id(body.operationId)
    trace = request_id(request.headers.get("x-request-id"))
    if operation is None or not operation.step_up:
        return error_response(ProxyError(400, "step_up_not_applicable",
                                         "Bu işlem PIN teyidi gerektirmiyor."), trace)
    needed = km_permissions(operation)
    if not any(user.has_permission(key) for key in needed):
        return error_response(ProxyError(403, "forbidden", "Bu işlem için yetkiniz yok.",
                                         {"permission": needed[0]}), trace)
    # Yanlış PIN: 403 + denetim izi (çekirdek yazar). 401 DEĞİL — oturum geçerli.
    await confirm_pin(request, user, body.pin, action=f"bbd_akademi.step_up:{operation.operation_id}")
    return await proxy().step_up.issue(user.id, operation.operation_id)


@router.post("/km/fetch")
async def fetch(body: FetchBody, user: CurrentUser = requires(VIEW)) -> dict[str, Any]:
    """İmzalı indirme bağlantısını (PDF/XLSX) panel için base64'e çevirir."""
    return await proxy().fetch_file(body.url)


@router.get("/km/download")
async def download(request: Request, url: str = Query(min_length=8, max_length=4000),
                   user: CurrentUser = requires(VIEW)) -> Response:
    """İmzalı indirme bağlantısını AKIŞ olarak geçirir (tarayıcı/araç istemcileri)."""
    return await proxy().download(url, request_id(request.headers.get("x-request-id")))


# ------------------------------------------------------------------ personel


@router.get("/km/staff")
async def staff_list(user: CurrentUser = requires(STAFF)) -> dict[str, Any]:
    return {"items": await staff().list(), "sync": await staff().state(),
            "roles": [{"key": key, "label": label} for key, label in ROLE_LABELS.items()]}


@router.post("/km/staff", status_code=201)
async def staff_create(body: StaffBody, user: CurrentUser = requires(STAFF)) -> Any:
    try:
        row = await staff().create(user.full_name or user.id, body.model_dump())
    except StaffError as failure:
        return _staff_error(failure)
    sync = await staff().push(source="change", actor=(user.id, user.full_name))
    return {"item": await staff().get(row["id"]), "sync": sync}


@router.patch("/km/staff/{staff_id}")
async def staff_update(staff_id: str, body: StaffPatchBody,
                       user: CurrentUser = requires(STAFF)) -> Any:
    data = body.model_dump(exclude_unset=True)
    expected = int(data.pop("expectedRevision"))
    try:
        await staff().update(user.full_name or user.id, staff_id, data, expected)
    except StaffError as failure:
        return _staff_error(failure)
    sync = await staff().push(source="change", actor=(user.id, user.full_name))
    return {"item": await staff().get(staff_id), "sync": sync}


@router.post("/km/staff/sync")
async def staff_sync(user: CurrentUser = requires(STAFF)) -> dict[str, Any]:
    return {"sync": await staff().push(source="manual", actor=(user.id, user.full_name)),
            "items": await staff().list()}


# ---------------------------------------------------------------- ham vekil


@router.api_route("/{path:path}", methods=METHODS, include_in_schema=False)
async def forward(path: str, request: Request,
                  user: CurrentUser = requires(VIEW)) -> Response:
    return await proxy().forward(request, user, path)
