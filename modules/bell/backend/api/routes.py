"""Zil Sistemi — HTTP yüzeyi.

ÇİFT KAPI (K9): router'ın tabanı `bell.view`; düzenleme `bell.manage`, çalma
`bell.ring_now` ister. Görüntüleyebilen kullanıcı zili çalamaz, çalabilen
kullanıcı saatleri değiştiremez — üç izin bilerek ayrı.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, requires

from ..service import BellService

router = APIRouter()

_service: BellService | None = None

#: Yüklenen zil sesinin üst sınırı. Zil birkaç saniyedir; bundan büyüğü ya
#: yanlış dosyadır ya da bellekte taşınmaması gereken bir kayıttır.
MAX_SOUND_BYTES = 8 * 1024 * 1024


def bind(service: BellService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> BellService:
    if _service is None:  # pragma: no cover - yükleme sırası garanti eder
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


# ------------------------------------------------------------------ gövdeler


class SettingsBody(BaseModel):
    settings: dict[str, Any] = Field(default_factory=dict)


class TimesBody(BaseModel):
    times: dict[str, Any] = Field(default_factory=dict)


class GroupBody(BaseModel):
    name: str = Field(default="", max_length=80)
    #: "grup" (toplu ders) ya da "ozel" (tek öğrenciyle özel ders).
    #: Cümle buna göre kurulur: "dersiniz başlıyor" ↔ "özel dersin başlıyor".
    #: Ad değiştirirken boş bırakılırsa mevcut tür korunur.
    kind: str = Field(default="", max_length=8)


class CallBody(BaseModel):
    groupId: str = Field(default="", max_length=64)


class PreviewBody(BaseModel):
    """Yalnız bu bilgisayardan dinleme — okulun hoparlörüne gitmez."""

    sound: str = Field(default="", max_length=160)
    volume: int | None = Field(default=None, ge=0, le=100)


class SoundBody(BaseModel):
    name: str = Field(default="", max_length=160)
    data: str = Field(default="", description="base64")


# --------------------------------------------------------------------- okuma


@router.get("/state")
async def state(user: CurrentUser = requires("bell.view")) -> dict[str, Any]:
    """Ayarlar, haftalık saatler, gruplar, ses durumları, ajan ve günlük."""
    return await service().state()


# ------------------------------------------------------------------ düzenleme


@router.put("/settings")
async def save_settings(
    body: SettingsBody,
    user: CurrentUser = requires("bell.manage"),
) -> dict[str, Any]:
    return await service().save_settings(body.settings, actor=user.full_name)


@router.put("/times")
async def save_times(
    body: TimesBody,
    user: CurrentUser = requires("bell.manage"),
) -> dict[str, Any]:
    """Haftalık zil saatlerini tümüyle değiştirir."""
    return await service().save_times(body.times, actor=user.full_name)


@router.post("/groups")
async def add_group(
    body: GroupBody,
    user: CurrentUser = requires("bell.manage"),
) -> dict[str, Any]:
    """Grup ekler ve o grubun anons sesini üretim sırasına koyar."""
    return await service().add_group(body.name, kind=body.kind or "grup",
                                     actor=user.full_name)


@router.put("/groups/{group_id}")
async def rename_group(
    group_id: str,
    body: GroupBody,
    user: CurrentUser = requires("bell.manage"),
) -> dict[str, Any]:
    return await service().rename_group(group_id, body.name, kind=body.kind,
                                        actor=user.full_name)


@router.delete("/groups/{group_id}")
async def remove_group(
    group_id: str,
    user: CurrentUser = requires("bell.manage"),
) -> dict[str, Any]:
    """Grubu listeden kaldırır. Satır silinmez; sesi ve günlüğü yerinde kalır."""
    return await service().remove_group(group_id, actor=user.full_name)


@router.post("/voices/rebuild")
async def rebuild_voices(
    user: CurrentUser = requires("bell.manage"),
) -> dict[str, Any]:
    """Eksik ya da hata almış sesleri yeniden üretim sırasına alır."""
    queued = await service().rebuild_voices()
    return {"ok": True, "queued": queued}


@router.post("/sound")
async def upload_sound(
    body: SoundBody,
    user: CurrentUser = requires("bell.manage"),
) -> dict[str, Any]:
    """Teneffüs zili dosyasını yükler.

    Dosya base64 gelir: Tauri kabuğunda dosya sistemi eklentisi yok, panel
    `FileReader` ile okuyup gönderiyor (mağaza geçidiyle aynı desen).
    """
    try:
        data = base64.b64decode(body.data, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="Dosya çözülemedi.") from None
    if len(data) > MAX_SOUND_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Dosya {MAX_SOUND_BYTES // (1024 * 1024)} MB sınırını aşıyor.",
        )
    return await service().upload_sound(body.name, data, actor=user.full_name)


# --------------------------------------------------------------------- çalma


@router.post("/ring")
async def ring(user: CurrentUser = requires("bell.ring_now")) -> dict[str, Any]:
    """Zili şimdi çalar (anons yok). Sonuç, başarısızlık dahil günlüğe yazılır."""
    return await service().ring_now(actor=user.full_name)


@router.post("/call")
async def call(
    body: CallBody,
    user: CurrentUser = requires("bell.ring_now"),
) -> dict[str, Any]:
    """Grubu çağırır: yalnız anons çalar, zil çalmaz."""
    return await service().call_group(body.groupId, actor=user.full_name)


@router.post("/preview")
async def preview(
    body: PreviewBody,
    user: CurrentUser = requires("bell.view"),
) -> dict[str, Any]:
    """Sesi YALNIZ bu bilgisayarda dinletir — okulun hoparlörüne gitmez.

    `bell.view` yeterlidir: bu, zil çalmak değil, ekranda ses denemektir.
    """
    return await service().preview(body.sound, volume=body.volume)


# ------------------------------------------------------------------- bakım


@router.post("/reschedule")
async def reschedule(user: CurrentUser = requires("bell.manage")) -> dict[str, Any]:
    """Tetikleyici tablosunu haftalık saatlerden yeniden kurar."""
    return await service().reschedule()


@router.post("/sync")
async def sync(user: CurrentUser = requires("bell.manage")) -> dict[str, Any]:
    """Ajanın ses kitaplığını hemen eşitler (arka plan döngüsünü beklemeden)."""
    return await service().sync_sounds()
