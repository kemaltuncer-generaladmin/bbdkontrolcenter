"""SMS Sistemi — HTTP yüzeyi.

ÇİFT KAPI (K9): router `view` ister; gönderim `send` (yıkıcı — gerçek para,
gerçek veli, geri alınamaz), Netgsm ayarı `manage_settings`.
"""

from __future__ import annotations

from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, requires

from .. import segments as seg
from ..service import SmsService

router = APIRouter()

_service: SmsService | None = None


def bind(service: SmsService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> SmsService:
    if _service is None:  # pragma: no cover - yükleme sırası garanti eder
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


class SendBody(BaseModel):
    """Serbest SMS gönderimi. Alıcı daima velidir."""

    students: list[str] = Field(default_factory=list, max_length=5000)
    body: str = Field(default="", max_length=480)
    title: str = Field(default="", max_length=120)
    includeDebt: bool = False
    includeDaily: bool = False
    # Türkçe karakterleri ASCII'ye çevirir — UCS-2 yerine GSM-7, segment yarı yarıya.
    simplify: bool = False
    # Sınıf adları yer tutucu çözümünde kullanılır; kaynağı Öğrenci Yönetimi modülüdür.
    classes: dict[str, str] = Field(default_factory=dict)
    dryRun: bool = False


class MeasureBody(BaseModel):
    text: str = Field(default="", max_length=2000)
    simplify: bool = False


class PresetBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    body: str = Field(min_length=1, max_length=480)


class NetgsmBody(BaseModel):
    netgsmUsercode: str | None = Field(default=None, max_length=64)
    netgsmPassword: str | None = Field(default=None, max_length=255)
    netgsmHeader: str | None = Field(default=None, max_length=11)
    smsPaymentConfirmedEnabled: bool | None = None


@router.get("/workspace")
async def workspace(user: CurrentUser = requires("bbd_sms.view")) -> dict[str, Any]:
    """Açılış verisi: alıcılar, hazır mesajlar, Netgsm durumu, kuyruk özeti."""
    return await service().workspace()


@router.post("/measure")
async def measure(
    body: MeasureBody,
    user: CurrentUser = requires("bbd_sms.view"),
) -> dict[str, Any]:
    """Karakter seti, segment ve kredi hesabı — yazarken canlı gösterilir."""
    text = seg.simplify(body.text) if body.simplify else body.text
    return {"measure": seg.measure(text), "text": text}


@router.post("/preview")
async def preview(
    body: SendBody,
    user: CurrentUser = requires("bbd_sms.view"),
) -> dict[str, Any]:
    """Kuru prova: kime, hangi veli numarasına, tam metin, kaç kredi."""
    return await service().preview(body.model_dump())


@router.post("/send")
async def send(
    body: SendBody,
    user: CurrentUser = requires("bbd_sms.send"),
) -> dict[str, Any]:
    """SMS'leri gönderir. GERİ ALINAMAZ — gönderim öncesi prova zorunludur."""
    return await service().send(body.model_dump(), actor=user.full_name)


@router.get("/history")
async def history(
    limit: int = 200,
    user: CurrentUser = requires("bbd_sms.view"),
) -> dict[str, Any]:
    """Bu panelden gönderilmiş SMS'ler — kantinde bu kayıt tutulmuyor."""
    return await service().history(limit=min(limit, 1000))


@router.get("/batches/{batch_ref}")
async def batch_detail(
    batch_ref: str,
    user: CurrentUser = requires("bbd_sms.view"),
) -> dict[str, Any]:
    return await service().batch_detail(batch_ref)


@router.get("/queue")
async def queue(
    state: str | None = None,
    user: CurrentUser = requires("bbd_sms.view"),
) -> dict[str, Any]:
    """Kantin SMS kuyruğu — ödeme SMS'lerinin durumu, denemeleri, hataları."""
    return await service().queue(state)


@router.put("/presets")
async def save_preset(
    body: PresetBody,
    user: CurrentUser = requires("bbd_sms.manage_settings"),
) -> dict[str, Any]:
    return await service().save_preset(body.name, body.body, actor=user.full_name)


@router.delete("/presets/{preset_id}")
async def delete_preset(
    preset_id: int,
    user: CurrentUser = requires("bbd_sms.manage_settings"),
) -> dict[str, Any]:
    return await service().delete_preset(preset_id)


@router.put("/netgsm")
async def update_netgsm(
    body: NetgsmBody,
    user: CurrentUser = requires("bbd_sms.manage_settings"),
) -> dict[str, Any]:
    """Netgsm kimlik bilgileri. Yalnız gönderilen alan yazılır; parola boşsa korunur."""
    return await service().update_netgsm(body.model_dump(exclude_unset=True))
