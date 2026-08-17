"""Antivirüs — HTTP yüzeyi.

ÇİFT KAPI (K9): router'ın tabanı `antivirus.view` (module.yaml → http.requires);
tarama başlatma ve durdurma ayrıca `antivirus.scan` ister. Görüntüleyebilen
kullanıcı tarama başlatamaz — iki izin bilerek ayrı (docs/permissions.md).

YÜZEY BİLEREK DAR. Kullanıcı "çok basit bir ekran" istedi: iki düğme, ilerleme,
son sonuç, imza durumu. Okunacak tek bir uç (`/state`) ve yazılacak iki uç
vardır; geçmiş listesi, karantina ve elle imza güncelleme uçları YOKTUR —
karşılığı olan ekran da yok, ilan edilmemiş uç ise ölü koddur.
"""

from __future__ import annotations

from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, requires

from ..engine import EngineNotReady
from ..service import AntivirusService, ScanBusy

router = APIRouter()

_service: AntivirusService | None = None


def bind(service: AntivirusService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> AntivirusService:
    if _service is None:  # pragma: no cover - yükleme sırası garanti eder
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


class ScanBody(BaseModel):
    #: "quick" (ayarda tanımlı yollar) ya da "full" (tam tarama kökleri).
    kind: str = Field(default="quick", max_length=8)


# --------------------------------------------------------------------- okuma


@router.get("/state")
async def state(user: CurrentUser = requires("antivirus.view")) -> dict[str, Any]:
    """Motor durumu, imza durumu, süren tarama ve son tarama sonucu."""
    return await service().state()


# --------------------------------------------------------------------- işlem


@router.post("/scan")
async def start_scan(
    body: ScanBody,
    user: CurrentUser = requires("antivirus.scan"),
) -> dict[str, Any]:
    """Taramayı başlatır ve hemen döner; ilerleme `/state` ucundan okunur.

    409 = zaten bir tarama sürüyor · 503 = motor hazır değil (kurulu değil ya
    da imzalar hâlâ indiriliyor). İkisi ayrı kod, çünkü kullanıcının yapacağı
    iş ayrı: biri beklemek, öteki kurulum.
    """
    try:
        return await service().start(body.kind, actor=user.full_name)
    except ScanBusy as busy:
        raise HTTPException(status_code=409, detail=str(busy)) from None
    except EngineNotReady as failure:
        raise HTTPException(status_code=503, detail=str(failure)) from None


@router.post("/scan/cancel")
async def cancel_scan(user: CurrentUser = requires("antivirus.scan")) -> dict[str, Any]:
    """Süren taramayı durdurur. Sonuç 'başarısız' olarak kaydedilir."""
    return await service().cancel()
