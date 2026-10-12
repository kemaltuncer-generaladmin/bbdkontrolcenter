"""Mobil Geçit v1 uçları — `/api/mobile_gateway/v1`.

ÇİFT KAPI (K9): router'ın tabanı `module.yaml` → `http.requires`
(`mobile_gateway.access`) ile çekirdekte korunur; her uç aynı izni ayrıca
kendi imzasında `requires(ACCESS)` ile sorar. Rol adı sorulmaz (K10).

Başlıklar:
  `X-KM-On-Behalf-Of` — uygulamadaki asıl kişi; YALNIZ denetim izine gider,
                         yetki vermez.
  `X-Request-Id`       — denetim izine ve log'a geçer.
  `Idempotency-Key`    — her POST/PATCH/PUT'ta; 24 saat saklanır.

Hatalar `GatewayRoute` ile sözleşme zarfına girer (errors.py).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Any, Literal

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..errors import GatewayError, GatewayRoute, validation
from ..service import BELL_PLAN_MAX_BYTES, Caller, GatewayService, Reply, clean_header

ACCESS = "mobile_gateway.access"

router = APIRouter(route_class=GatewayRoute)
_service: GatewayService | None = None


def bind(service: GatewayService) -> APIRouter:
    global _service
    _service = service
    return router


def current_service() -> GatewayService:
    if _service is None:  # pragma: no cover - yükleme sırası garanti eder
        raise HTTPException(status_code=503, detail="Mobil geçit hazır değil.")
    return _service


def caller_of(request: Request, user: CurrentUser) -> Caller:
    # ADR 0028 çekirdeği başlığı çözüp `CurrentUser.on_behalf_of`a koyar; o
    # varsa yeniden çözülmez. Yoksa (kişi oturumu, eski çekirdek) başlık okunur.
    decoded = getattr(user, "on_behalf_of", None)
    on_behalf = clean_header(decoded, decode=False) if decoded \
        else clean_header(request.headers.get("X-KM-On-Behalf-Of"))
    return Caller(
        user_id=str(user.id),
        name=user.full_name or str(user.id),
        on_behalf_of=on_behalf,
        request_id=clean_header(request.headers.get("X-Request-Id"), 100),
    )


def reply(result: Reply) -> Response:
    if result.raw is not None:
        # Önbellekten gelen, önceden kodlanmış JSON (dizin, zil haftası).
        return Response(content=result.raw, status_code=result.status,
                        headers=result.headers, media_type="application/json")
    if result.body is None:
        return Response(status_code=result.status, headers=result.headers)
    return JSONResponse(status_code=result.status, content=result.body, headers=result.headers)


async def idempotent(request: Request, body: BaseModel | dict[str, Any] | None, status: int,
                     produce: Callable[[], Awaitable[dict[str, Any]]]) -> Response:
    payload = body.model_dump(mode="json", exclude_unset=True) \
        if isinstance(body, BaseModel) else body
    route = f"{request.method} {request.url.path}"
    result = await current_service().idempotent(
        request.headers.get("Idempotency-Key"), route, payload, status, produce,
    )
    return reply(result)


# ------------------------------------------------------------------ gövdeler

PHONE = r"^5\d{9}$"

#: `from`/`to` Python'da ayrılmış/çakışan adlar; sorgu takma adıyla okunur.
FROM_QUERY = Query(default=None, alias="from")
TO_QUERY = Query(default=None, alias="to")
DAY_QUERY = Query(default=None, alias="date")
CODE = r"^\d{6}$"


class StudentCodeBody(BaseModel):
    phone: str = Field(pattern=PHONE)
    code: str = Field(pattern=CODE)


class VerifyCodeBody(BaseModel):
    code: str = Field(pattern=CODE)


class PaymentRequestBody(BaseModel):
    amount: int | None = Field(default=None, ge=1, le=100_000_000)


class ControlsBody(BaseModel):
    """Kantin 1.3: gönderilmeyen alan değişmez, `null` limit temizlenir."""

    spendingLimit: int | None = Field(default=None, ge=0, le=100_000_000)
    dailyLimit: int | None = Field(default=None, ge=0, le=100_000_000)
    isBlocked: bool | None = None
    guardianBlocked: bool | None = None
    restrictedProductIds: list[int] | None = Field(default=None, max_length=200)


class CollectionBody(BaseModel):
    amount: int = Field(ge=1, le=100_000_000)
    localId: str = Field(min_length=1, max_length=64)
    note: str | None = Field(default=None, max_length=500)


class ReasonBody(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


class GrantBody(ReasonBody):
    packageName: str | None = Field(default=None, min_length=2, max_length=255)
    extraSeconds: int = Field(ge=1, le=86_400)


class BlockBody(ReasonBody):
    until: datetime | None = None


class CallBody(BaseModel):
    studentId: str = Field(min_length=1, max_length=64)
    template: Literal["page", "custom"] = "page"
    text: str | None = Field(default=None, max_length=160)


class OtpBody(BaseModel):
    phone: str = Field(pattern=PHONE)
    code: str = Field(pattern=CODE)
    purpose: Literal["login"]


def reason_of(body: ReasonBody) -> str:
    reason = " ".join(body.reason.split())
    if len(reason) < 3:
        raise validation("Gerekçe en az 3 karakter olmalı.", "reason")
    return reason


# ================================================================ §1 dizin


@router.get("/directory")
async def directory(request: Request, since: str | None = None,
                    user: CurrentUser = requires(ACCESS)) -> Response:
    return reply(await current_service().directory(
        since, request.headers.get("If-None-Match"),
    ))


# ======================================================== §2 öğrenci şifresi


@router.post("/auth/student-code")
async def auth_student_code(body: StudentCodeBody, request: Request,
                            user: CurrentUser = requires(ACCESS)) -> Response:
    return await idempotent(request, body, 200, lambda: current_service().auth_student_code(
        body.phone, body.code,
    ))


@router.post("/students/{student_id}/verify-code")
async def verify_code(student_id: str, body: VerifyCodeBody, request: Request,
                      user: CurrentUser = requires(ACCESS)) -> Response:
    return await idempotent(request, body, 200, lambda: current_service().verify_code(
        student_id, body.code,
    ))


# ================================================================ §3 kantin


@router.get("/students/{student_id}/canteen")
async def canteen(student_id: str, days: int = Query(default=30, ge=1, le=180),
                  user: CurrentUser = requires(ACCESS)) -> dict[str, Any]:
    return await current_service().canteen_summary(student_id, days)


@router.get("/students/{student_id}/canteen/transactions")
async def canteen_transactions(student_id: str,
                               date_from: date | None = FROM_QUERY,
                               date_to: date | None = TO_QUERY,
                               cursor: str | None = Query(default=None, max_length=512),
                               limit: int = Query(default=20, ge=1, le=100),
                               user: CurrentUser = requires(ACCESS)) -> dict[str, Any]:
    """Öğrencinin satışları, imleçli (§3 eki) — `{items, nextCursor, hasMore}`."""
    return await current_service().canteen_transactions_page(student_id, date_from, date_to,
                                                             cursor, limit)


@router.get("/students/{student_id}/canteen/collections")
async def canteen_collections(student_id: str,
                              date_from: date | None = FROM_QUERY,
                              date_to: date | None = TO_QUERY,
                              cursor: str | None = Query(default=None, max_length=512),
                              limit: int = Query(default=20, ge=1, le=100),
                              user: CurrentUser = requires(ACCESS)) -> dict[str, Any]:
    """Öğrencinin tahsilatları, imleçli (§3 eki) — `{items, nextCursor, hasMore}`."""
    return await current_service().canteen_collections_page(student_id, date_from, date_to,
                                                            cursor, limit)


@router.get("/students/{student_id}/canteen/qr")
async def canteen_qr(student_id: str, user: CurrentUser = requires(ACCESS)) -> dict[str, Any]:
    return await current_service().canteen_qr(student_id)


@router.get("/students/{student_id}/payment-requests")
async def student_payment_requests(student_id: str,
                                   user: CurrentUser = requires(ACCESS)) -> dict[str, Any]:
    return await current_service().student_payment_requests(student_id)


async def _create_payment_request(student_id: str, body: PaymentRequestBody | None,
                                  request: Request, user: CurrentUser) -> Response:
    caller = caller_of(request, user)
    amount = body.amount if body is not None else None
    return await idempotent(request, body, 201, lambda: current_service().create_payment_request(
        student_id, amount, caller,
    ))


@router.post("/students/{student_id}/payment-requests")
async def create_payment_request(student_id: str, request: Request,
                                 body: PaymentRequestBody | None = None,
                                 user: CurrentUser = requires(ACCESS)) -> Response:
    """Sözleşme §3. Gövde isteğe bağlı; `amount` kantin-veli-kontrolleri §2.2'den."""
    return await _create_payment_request(student_id, body, request, user)


@router.post("/students/{student_id}/canteen/payment-requests")
async def create_canteen_payment_request(student_id: str, request: Request,
                                         body: PaymentRequestBody | None = None,
                                         user: CurrentUser = requires(ACCESS)) -> Response:
    """kantin-veli-kontrolleri §2.2 yolu — §3 ile aynı uç."""
    return await _create_payment_request(student_id, body, request, user)


@router.get("/students/{student_id}/canteen/controls")
async def canteen_controls(student_id: str,
                           user: CurrentUser = requires(ACCESS)) -> dict[str, Any]:
    return await current_service().controls(student_id)


@router.patch("/students/{student_id}/canteen/controls")
async def update_canteen_controls(student_id: str, body: ControlsBody, request: Request,
                                  user: CurrentUser = requires(ACCESS)) -> Response:
    patch = body.model_dump(exclude_unset=True)
    for flag in ("isBlocked", "guardianBlocked", "restrictedProductIds"):
        if flag in patch and patch[flag] is None:
            raise validation(f"“{flag}” boş (null) olamaz.", flag)
    caller = caller_of(request, user)
    return await idempotent(request, body, 200, lambda: current_service().update_controls(
        student_id, patch, caller,
    ))


@router.post("/students/{student_id}/canteen/collections")
async def canteen_collection(student_id: str, body: CollectionBody, request: Request,
                             user: CurrentUser = requires(ACCESS)) -> Response:
    caller = caller_of(request, user)
    return await idempotent(request, body, 201, lambda: current_service().record_collection(
        student_id, body.amount, body.localId, body.note, caller,
    ))


@router.get("/canteen/payment-requests")
async def payment_requests_feed(since: str | None = None,
                                limit: int = Query(default=100, ge=1, le=500),
                                user: CurrentUser = requires(ACCESS)) -> dict[str, Any]:
    return await current_service().payment_requests_feed(since, limit)


@router.get("/canteen/parent-notifications")
async def parent_notifications_feed(since: str | None = None,
                                    limit: int = Query(default=100, ge=1, le=500),
                                    user: CurrentUser = requires(ACCESS)) -> dict[str, Any]:
    return await current_service().parent_notifications_feed(since, limit)


@router.get("/canteen/settings/public")
async def canteen_settings_public(user: CurrentUser = requires(ACCESS)) -> dict[str, Any]:
    return await current_service().canteen_settings_public()


@router.get("/canteen/products")
async def canteen_products(user: CurrentUser = requires(ACCESS)) -> dict[str, Any]:
    return await current_service().products()


# ================================================================ §4 tablet


@router.get("/tablets")
async def tablets(user: CurrentUser = requires(ACCESS)) -> dict[str, Any]:
    return await current_service().tablets()


@router.get("/tablets/usage")
async def tablets_daily_usage(day: date | None = DAY_QUERY,
                              user: CurrentUser = requires(ACCESS)) -> dict[str, Any]:
    return await current_service().tablets_daily_usage(day)


@router.get("/students/{student_id}/tablet-usage")
async def tablet_usage(student_id: str,
                       date_from: date | None = FROM_QUERY,
                       date_to: date | None = TO_QUERY,
                       user: CurrentUser = requires(ACCESS)) -> dict[str, Any]:
    return await current_service().tablet_usage(student_id, date_from, date_to)


@router.post("/students/{student_id}/tablet/grant")
async def tablet_grant(student_id: str, body: GrantBody, request: Request,
                       user: CurrentUser = requires(ACCESS)) -> Response:
    caller, reason = caller_of(request, user), reason_of(body)
    return await idempotent(request, body, 201, lambda: current_service().tablet_grant(
        student_id, package_name=body.packageName, extra_seconds=body.extraSeconds,
        reason=reason, caller=caller,
    ))


@router.post("/students/{student_id}/tablet/block")
async def tablet_block(student_id: str, body: BlockBody, request: Request,
                       user: CurrentUser = requires(ACCESS)) -> Response:
    caller, reason = caller_of(request, user), reason_of(body)
    until = body.until
    if until is not None and until.tzinfo is None:
        raise validation("`until` saat dilimi içermeli (ör. +03:00).", "until")
    return await idempotent(request, body, 200, lambda: current_service().tablet_block(
        student_id, until=until, reason=reason, caller=caller,
    ))


@router.post("/students/{student_id}/tablet/unblock")
async def tablet_unblock(student_id: str, body: ReasonBody, request: Request,
                         user: CurrentUser = requires(ACCESS)) -> Response:
    caller, reason = caller_of(request, user), reason_of(body)
    return await idempotent(request, body, 200, lambda: current_service().tablet_unblock(
        student_id, reason=reason, caller=caller,
    ))


@router.post("/tablet-sessions/{session_id}/end")
async def tablet_session_end(session_id: str, body: ReasonBody, request: Request,
                             user: CurrentUser = requires(ACCESS)) -> Response:
    caller, reason = caller_of(request, user), reason_of(body)
    return await idempotent(request, body, 200, lambda: current_service().tablet_end_session(
        session_id, reason=reason, caller=caller,
    ))


# ================================================================ §5 anons


@router.post("/announcements/call")
async def announcement_call(body: CallBody, request: Request,
                            user: CurrentUser = requires(ACCESS)) -> Response:
    caller = caller_of(request, user)
    return await idempotent(request, body, 202, lambda: current_service().call_student(
        body.studentId, body.template, body.text, caller,
    ))


# ======================================================== §6 zil saatleri


@router.get("/schedule/bell-week")
async def bell_week(request: Request, user: CurrentUser = requires(ACCESS)) -> Response:
    return reply(await current_service().bell_week_reply(request.headers.get("If-None-Match")))


# ================================================== §9 öğrenci grupları


@router.get("/groups")
async def groups(request: Request, since: str | None = Query(default=None, max_length=32),
                 user: CurrentUser = requires(ACCESS)) -> Response:
    """km-ogrenci-gruplari-ve-zil.md §2.4 — `since` grup revizyonu, ETag/304."""
    return reply(await current_service().groups(since, request.headers.get("If-None-Match")))


# ======================================================== §10 zil planı


async def json_object(request: Request, limit: int) -> dict[str, Any]:
    """Ham gövde → JSON nesnesi. Sınır OKUMADAN önce başlıktan, sonra bayttan denetlenir."""
    declared = request.headers.get("Content-Length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise GatewayError(413, "payload_too_large",
                           f"Gövde en çok {limit // 1024} KB olabilir.")
    raw = await request.body()
    if len(raw) > limit:
        raise GatewayError(413, "payload_too_large",
                           f"Gövde en çok {limit // 1024} KB olabilir.")
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise validation("Gövde geçerli JSON olmalı.", "gövde") from None
    if not isinstance(body, dict):
        raise validation("Gövde JSON nesnesi olmalı.", "gövde")
    return body


@router.put("/bell/plan")
async def put_bell_plan(request: Request, user: CurrentUser = requires(ACCESS)) -> Response:
    """§3.3 — Akademi'nin zil planı (en çok 15 gün). Gün bazında sonuç, 200."""
    body = await json_object(request, BELL_PLAN_MAX_BYTES)
    caller = caller_of(request, user)
    return await idempotent(request, body, 200, lambda: current_service().put_bell_plan(
        body, caller,
    ))


@router.get("/bell/plan/status")
async def bell_plan_status(request: Request,
                           date_from: date | None = FROM_QUERY,
                           date_to: date | None = TO_QUERY,
                           user: CurrentUser = requires(ACCESS)) -> Response:
    """§3.3 — gün başına kip, kaynak, revizyon, ses durumu (en çok 31 gün). ETag/304."""
    return reply(await current_service().bell_plan_status(
        date_from, date_to, request.headers.get("If-None-Match"),
    ))


# ========================================================== §7 OTP SMS


@router.post("/sms/otp")
async def sms_otp(body: OtpBody, request: Request,
                  user: CurrentUser = requires(ACCESS)) -> Response:
    caller = caller_of(request, user)
    return await idempotent(request, body, 200, lambda: current_service().send_otp(
        body.phone, body.code, body.purpose, caller,
    ))


# ========================================================= §8 genel ayarlar


@router.get("/settings/public")
async def public_settings(user: CurrentUser = requires(ACCESS)) -> dict[str, Any]:
    return current_service().public_settings()
