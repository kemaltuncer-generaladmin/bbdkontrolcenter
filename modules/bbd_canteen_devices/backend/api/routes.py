"""Kantin Cihazları — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde düğmeyi gizlemek yetkilendirme
değildir. `module.yaml` → `http.requires` taban izni verir, uçlar onu DARALTIR.

ÜÇ İZİN:

    bbd_canteen_devices.view      okuma (liste, özet, yerel iz)
    bbd_canteen_devices.manage    kiosk açma/adlandırma, EŞLEME KODU üretme
    bbd_canteen_devices.devices   YIKICI: eşleme iptali

Kod üretmek ile iptal etmenin ayrılması `bld_kds` ile aynı gerekçeye dayanır:
iptal edilen kiosk kantinde satış yapamaz ve düzeltmesi yalnız merkezden gelir.
Tek anahtarda toplansalardı, kod üretebilen herkes bir kasayı durdurabilirdi.

İPTAL AYRICA PIN İSTER. İzin yeterli olsa bile `confirm_pin` çağrılır
(CLAUDE.md: "Yıkıcı işlemler izin yeterli olsa bile PIN teyidi ister");
`module.yaml` içinde anahtar `destructive: true` taşır. Sebep: izin taşımak, o
an klavyenin başındaki kişinin o kişi olduğunu kanıtlamaz — açık bırakılmış bir
oturum kantindeki bir cihazı satış dışı bırakabilirdi.

KURU PROVA ALANI YOKTUR. `bld_kds` bir `dryRun` taşıyor çünkü BLD geçidinde
karşılığı var; kantinde yok. Olmayan bir kipi taklit eden bir bayrak,
"prova yaptım" diyen ama gerçekten yazan bir çağrı üretirdi.

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran mesajı
gösterir. 4xx yalnız izin, PIN ve şema kapısından çıkar.
"""

from __future__ import annotations

from typing import Any, ClassVar

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

from ..kiosks import MAX_NAME, MAX_REASON, MIN_NAME, MIN_REASON
from ..service import CanteenDeviceService

#: İzin anahtarları tek yerde durur: uç noktalar ve servis aynı dizgeyi okur,
#: yazım hatası bir kapıyı sessizce açık bırakamaz.
VIEW = "bbd_canteen_devices.view"
MANAGE = "bbd_canteen_devices.manage"
DEVICES = "bbd_canteen_devices.devices"

router = APIRouter()
_service: CanteenDeviceService | None = None


def bind(service: CanteenDeviceService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> CanteenDeviceService:
    if _service is None:  # pragma: no cover - yükleme sırası garanti eder
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


class ReasonBody(BaseModel):
    """Her yazma gövdesinin ortak alanı.

    `actor` GÖVDEDEN ALINMAZ — oturumdan gelir. İstemcinin aktör adını
    yazabilmesi, denetim izini imzalanmamış bir deftere çevirirdi: silinmeyen
    bir satıra istediği adı yazan biri, işi başkasının üstüne bırakabilirdi.

    `extra="forbid"`: yanlış yazılmış bir alan (örneğin `reasson`) sessizce
    düşüp gerekçesiz bir istek üretmesin, 422 ile geri dönsün.
    """

    #: `ClassVar` ile işaretli, çünkü `km_sdk` pydantic'in `ConfigDict` tipini
    #: dışa vurmuyor ve modül pydantic'i doğrudan import etmiyor (K2).
    model_config: ClassVar[dict[str, Any]] = {"extra": "forbid"}

    reason: str = Field(min_length=MIN_REASON, max_length=MAX_REASON)


class KioskBody(ReasonBody):
    name: str = Field(min_length=MIN_NAME, max_length=MAX_NAME)


class PairingBody(ReasonBody):
    #: Kod üretildiği anda kâğıda da basılsın mı. Ayrı bir "sonra bas" ucu
    #: YOKTUR: kod hiçbir yere yazılmıyor, basım kodun düz görüldüğü tek anda
    #: yapılabilir.
    print: bool = False


class RevokeBody(ReasonBody):
    """Geri dönüşü olmayan işlemin gövdesi — PIN zorunlu.

    PIN gövdede taşınır, sorgu dizesinde DEĞİL: sorgu dizesi denetim kaydına,
    sunucu günlüğüne ve tarayıcı geçmişine düşer.
    """

    pin: str = Field(min_length=4, max_length=32)


# ================================================================== okuma

@router.get("/kiosks")
async def kiosks(
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Kiosk listesi + özet + panelin okuduğu sözleşme.

    Yanıt `connected` alanı taşır: kantine ulaşılamadıysa `ok` yine `True`
    döner ama `connected` `False`tur. Yalnız `ok`a bakan bir panel, geçit
    düştüğünde "hiç kiosk yok" derdi.
    """
    return await service().overview()


@router.get("/audit")
async def audit(
    kiosk_id: int = Query(0, ge=0),
    limit: int = Query(0, ge=0, le=1000),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Yerel işlem izi — kim, ne zaman, neyi denedi ve ne oldu.

    Kantin yalnız SONUCU tutuyor; yarıda kalan denemeler yalnız burada görünür.
    """
    return await service().audit_log(kiosk_id=kiosk_id, limit=limit)


@router.get("/printer")
async def printer_status(
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Yazıcı durumu — "kodu bas" düğmesinin neden kapalı olduğunu yazabilmek
    için. Yazıcı yoksa ekran çalışmaya devam eder (K7)."""
    return await service().printer_status()


# ================================================================= kiosklar

@router.post("/kiosks")
async def create_kiosk(
    body: KioskBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Yeni kiosk açar; yanıtta İLK eşleme kodu döner (bir kez)."""
    return await service().create_kiosk(name=body.name, reason=body.reason,
                                        actor=user.full_name)


@router.patch("/kiosks/{kiosk_id}")
async def rename_kiosk(
    kiosk_id: int,
    body: KioskBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """YALNIZ ad. Eşleme durumu bu uçtan değişmez."""
    return await service().rename_kiosk(kiosk_id, name=body.name, reason=body.reason,
                                        actor=user.full_name)


@router.post("/kiosks/{kiosk_id}/pairing-code")
async def pairing_code(
    kiosk_id: int,
    body: PairingBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Yeni eşleme kodu. İptal edilmiş kioska üretilmez — bkz. servis."""
    return await service().pairing_code(kiosk_id, reason=body.reason,
                                        actor=user.full_name, print_slip=body.print)


@router.post("/kiosks/{kiosk_id}/revoke")
async def revoke_kiosk(
    kiosk_id: int,
    body: RevokeBody,
    request: Request,
    user: CurrentUser = requires(DEVICES),
) -> dict[str, Any]:
    """YIKICI. Token silinir, cihaz bir daha bağlanamaz; kaydı SİLİNMEZ.

    ÜÇ KAPI: ayrı izin (`.devices`), PIN teyidi ve gerekçe. Üçü ayrı şeylerdir —
    izin "bu kişi yapabilir" der, PIN "klavyenin başındaki o kişidir" der,
    gerekçe ise denetim kaydıdır ve biri ötekinin yerine geçmez.

    POST'tur, DELETE değil: gövde taşıması gerekiyor (PIN) ve DELETE gövdesi
    ara katmanlarda güvenilir biçimde taşınmıyor.

    İzin kapısı burada ve serviste iki kez denetlenir (K9 — çift kapı).
    """
    await confirm_pin(request, user, body.pin, action="bbd_canteen_devices.kiosk.revoke")
    return await service().revoke_kiosk(kiosk_id, reason=body.reason,
                                        actor=user.full_name,
                                        allow_destructive=user.has_permission(DEVICES))
