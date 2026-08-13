"""Ödeme Talebi — HTTP yüzeyi.

ÇİFT KAPI (K9): router `view`; talep gönderimi `send`, nakit tahsilat `collect`
(ikisi de yıkıcı), şablon düzenleme `manage_templates`.
"""

from __future__ import annotations

from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, requires

from ..service import PaymentRequestService

router = APIRouter()

_service: PaymentRequestService | None = None


def bind(service: PaymentRequestService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> PaymentRequestService:
    if _service is None:  # pragma: no cover - yükleme sırası garanti eder
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


class SendBody(BaseModel):
    students: list[str] = Field(default_factory=list, max_length=5000)
    note: str = Field(default="", max_length=300)
    # Yakın zamanda talep gönderilmiş öğrenciye TEKRAR gönderme izni.
    # Varsayılan KAPALI: kantinde çift gönderim koruması yok, her istek yeni SMS demek.
    allowRepeat: bool = False
    dryRun: bool = False


class CollectBody(BaseModel):
    kantinId: str = Field(max_length=64)
    amount: int = Field(ge=1, le=100_000_000)
    localId: str | None = Field(default=None, max_length=64)
    note: str = Field(default="", max_length=255)


class TemplateBody(BaseModel):
    templates: dict[str, str] = Field(default_factory=dict)


@router.get("/workspace")
async def workspace(user: CurrentUser = requires("bbd_payment_request.view")) -> dict[str, Any]:
    """Talepler, öğrenciler, şablonlar, son gönderimler, tahsilat geçmişi."""
    return await service().workspace()


@router.post("/preview")
async def preview(
    body: SendBody,
    user: CurrentUser = requires("bbd_payment_request.view"),
) -> dict[str, Any]:
    """Kuru prova: kime, hangi veli numarasına, hangi tutarla gidecek."""
    return await service().preview(body.model_dump())


@router.post("/send")
async def send(
    body: SendBody,
    user: CurrentUser = requires("bbd_payment_request.send"),
) -> dict[str, Any]:
    """Talepleri açar ve velilere ödeme linki SMS'ini kuyruğa alır. GERİ ALINAMAZ."""
    return await service().send(body.model_dump(), actor=user.full_name)


@router.post("/requests/{ref}/resend")
async def resend(
    ref: str,
    user: CurrentUser = requires("bbd_payment_request.send"),
) -> dict[str, Any]:
    """Bekleyen talebi güncel borca göre yeniden gönderir; aynı ref korunur."""
    return await service().resend(ref)


@router.get("/requests/{ref}")
async def request_detail(
    ref: str,
    user: CurrentUser = requires("bbd_payment_request.view"),
) -> dict[str, Any]:
    """Talebin bütün hikâyesi: kantin kaydı, SMS akıbeti, bu panelden gönderimler."""
    return await service().request_detail(ref)


@router.post("/requests/{ref}/reopen")
async def reopen(
    ref: str,
    user: CurrentUser = requires("bbd_payment_request.send"),
) -> dict[str, Any]:
    """Kapanmış talebin öğrencisine YENİ talep açar; tutar güncel borçtur."""
    return await service().reopen(ref, actor=user.full_name)


@router.post("/requests/{ref}/cancel")
async def cancel(
    ref: str,
    user: CurrentUser = requires("bbd_payment_request.send"),
) -> dict[str, Any]:
    """Bekleyen talebi iptal eder. Satır SİLİNMEZ; ödeme linki ölür."""
    return await service().cancel(ref)


@router.post("/collections")
async def collect(
    body: CollectBody,
    user: CurrentUser = requires("bbd_payment_request.collect"),
) -> dict[str, Any]:
    """Elden alınan nakit tahsilat. Tutar borçtan fazla olamaz; localId ile idempotent."""
    return await service().collect(body.model_dump(), actor=user.full_name)


@router.get("/batches")
async def batches(
    limit: int = 100,
    user: CurrentUser = requires("bbd_payment_request.view"),
) -> dict[str, Any]:
    return await service().batches(limit=min(limit, 500))


@router.get("/batches/{batch_ref}")
async def batch_detail(
    batch_ref: str,
    user: CurrentUser = requires("bbd_payment_request.view"),
) -> dict[str, Any]:
    return await service().batch_detail(batch_ref)


@router.put("/templates")
async def update_templates(
    body: TemplateBody,
    user: CurrentUser = requires("bbd_payment_request.manage_templates"),
) -> dict[str, Any]:
    """Ödeme SMS şablonları. Boş şablon reddedilir — kantin onu varsayılana düşürür."""
    return await service().update_templates(body.templates)
