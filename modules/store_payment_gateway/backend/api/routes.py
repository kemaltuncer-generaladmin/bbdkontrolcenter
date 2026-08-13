"""Link ile Ödeme — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde düğmeyi gizlemek yetkilendirme
değildir. Para hareketi başlatan uçlarda gerekçe `min_length=10` ile ŞEMADA
doğrulanır ve serviste TEKRAR denetlenir — istemci şemayı atlatabilir.

İzin ayrımı bilinçlidir:
  · `view`    — ekran, liste, rapor
  · `manage`  — talep açma, SMS şablonu
  · `collect` — PARA HAREKETİ BAŞLATIR: bağlantı üretimi ve SMS
  · `settle`  — havale/nakit beyanı (mağazaya ödeme kaydı yazar)
  · `cancel`  — bağlantı iptali

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran
mesajı gösterir. 4xx yalnız izin/şema kapısından çıkar.
"""

from __future__ import annotations

from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..service import PaymentGatewayService

router = APIRouter()
_service: PaymentGatewayService | None = None


def bind(service: PaymentGatewayService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> PaymentGatewayService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


# ================================================================== okuma

@router.get("/state")
async def state(
    user: CurrentUser = requires("store_payment_gateway.view"),
) -> dict[str, Any]:
    """Mağaza, SMS ve frenlerin durumu. Ekran açılışta bunu okur."""
    return await service().state()


@router.get("/reference")
async def reference(
    user: CurrentUser = requires("store_payment_gateway.view"),
) -> dict[str, Any]:
    return await service().reference()


@router.get("/requests")
async def requests(
    q: str = Query("", max_length=120),
    status: str = Query("", max_length=24),
    start: str = Query("", max_length=10),
    end: str = Query("", max_length=10),
    minAmount: int | None = Query(None, ge=0),
    maxAmount: int | None = Query(None, ge=0),
    page: int = Query(1, ge=1, le=10_000),
    size: int = Query(0, ge=0, le=200),
    user: CurrentUser = requires("store_payment_gateway.view"),
) -> dict[str, Any]:
    return await service().requests(q=q, status=status, start=start, end=end,
                                    min_amount=minAmount, max_amount=maxAmount,
                                    page=page, size=size)


@router.get("/requests/{request_id}")
async def request_card(
    request_id: int,
    user: CurrentUser = requires("store_payment_gateway.view"),
) -> dict[str, Any]:
    return await service().card(request_id)


@router.get("/products")
async def products(
    q: str = Query("", max_length=120),
    page: int = Query(1, ge=1, le=1_000),
    size: int = Query(0, ge=0, le=100),
    user: CurrentUser = requires("store_payment_gateway.view"),
) -> dict[str, Any]:
    """Formdaki ürün seçicisi. Ürün YAZILMAZ, yalnız okunur."""
    return await service().search_products(q=q, page=page, size=size)


# =============================================================== önizleme

class LineBody(BaseModel):
    #: 'free' (serbest tutar — KDV'SİZ) | 'product' (ürünün kendi vergisi)
    kind: str = Field(default="free", max_length=16)
    label: str = Field(default="", max_length=160)
    quantity: int = Field(default=1, ge=1, le=1_000)
    amount: int = Field(default=0, ge=0, le=100_000_000)   # kuruş
    productId: int = Field(default=0, ge=0)
    sku: str = Field(default="", max_length=64)


class PreviewBody(BaseModel):
    fullName: str = Field(default="", max_length=120)
    phone: str = Field(default="", max_length=24)
    email: str = Field(default="", max_length=160)
    city: str = Field(default="", max_length=60)
    district: str = Field(default="", max_length=60)
    address: str = Field(default="", max_length=400)
    note: str = Field(default="", max_length=400)
    orderId: int = Field(default=0, ge=0)
    lines: list[LineBody] = Field(default_factory=list)
    template: str = Field(default="", max_length=1000)


@router.post("/quote")
async def quote(
    body: PreviewBody,
    user: CurrentUser = requires("store_payment_gateway.view"),
) -> dict[str, Any]:
    """Canlı ön izleme: tutar kırılımı + SMS metni + parça sayacı.

    ADI `preview` DEĞİL: panel kitinin rapor zinciri `POST <prefix>/preview`
    adını rapor üretimi için kullanıyor. İkisini aynı yola koymak, form
    önizlemesini rapor önizlemesiyle çakıştırırdı.

    HİÇBİR ŞEY YAZMAZ; bu yüzden gerekçe istemez ve `view` yeter.
    """
    return await service().preview(body.model_dump())


@router.post("/requests")
async def create(
    body: PreviewBody,
    user: CurrentUser = requires("store_payment_gateway.manage"),
) -> dict[str, Any]:
    """Talebi TASLAK olarak kaydeder. Mağazaya hiçbir şey yazılmaz."""
    return await service().create(payload=body.model_dump(), actor=user.full_name)


# ============================================================ para hareketi

class StartBody(BaseModel):
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True
    sendSms: bool = True


@router.post("/requests/{request_id}/start")
async def start(
    request_id: int,
    body: StartBody,
    user: CurrentUser = requires("store_payment_gateway.collect"),
) -> dict[str, Any]:
    """Gerekçeli onay → ödeme bağlantısı → (istenirse) SMS.

    Parası çekilmiş OLABİLECEK bir talep için yeni bağlantı üretilmez; servis
    o satırı kilitler ve nedenini yazar.
    """
    return await service().start(request_id, reason=body.reason, actor=user.full_name,
                                 dry_run=body.dryRun, send_sms=body.sendSms)


class ReasonBody(BaseModel):
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/requests/{request_id}/sms")
async def send_sms(
    request_id: int,
    body: ReasonBody,
    user: CurrentUser = requires("store_payment_gateway.collect"),
) -> dict[str, Any]:
    """SMS'i (yeniden) gönderir. Üç katmanlı fren serviste denetlenir."""
    return await service().send_sms(request_id, reason=body.reason, actor=user.full_name,
                                    dry_run=body.dryRun)


@router.post("/requests/{request_id}/poll")
async def poll(
    request_id: int,
    user: CurrentUser = requires("store_payment_gateway.view"),
) -> dict[str, Any]:
    """Mağazadaki durumu okur. Yazma değildir; gerekçe istemez."""
    return await service().poll(request_id)


@router.post("/requests/{request_id}/cancel")
async def cancel(
    request_id: int,
    body: ReasonBody,
    user: CurrentUser = requires("store_payment_gateway.cancel"),
) -> dict[str, Any]:
    """Bağlantıyı öldürür. KAYIT SİLİNMEZ (BBD veri silme yasağı)."""
    return await service().cancel(request_id, reason=body.reason, actor=user.full_name,
                                  dry_run=body.dryRun)


class SettleBody(BaseModel):
    method: str = Field(max_length=12)          # havale | nakit
    reference: str = Field(default="", max_length=120)
    amount: int = Field(default=0, ge=0, le=100_000_000)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/requests/{request_id}/settle")
async def settle(
    request_id: int,
    body: SettleBody,
    user: CurrentUser = requires("store_payment_gateway.settle"),
) -> dict[str, Any]:
    """Elden kapatma: havale/nakit beyanı. Mağazaya ödeme kaydı yazılır."""
    return await service().settle(request_id, method=body.method, reference=body.reference,
                                  amount=body.amount, reason=body.reason,
                                  actor=user.full_name, dry_run=body.dryRun)


# ================================================================ şablon

@router.get("/template")
async def template(
    user: CurrentUser = requires("store_payment_gateway.view"),
) -> dict[str, Any]:
    return await service().template()


class TemplateBody(BaseModel):
    body: str = Field(min_length=10, max_length=1000)
    reason: str = Field(min_length=10, max_length=255)


@router.post("/template")
async def save_template(
    body: TemplateBody,
    user: CurrentUser = requires("store_payment_gateway.manage"),
) -> dict[str, Any]:
    return await service().save_template(body=body.body, reason=body.reason,
                                         actor=user.full_name)


# ================================================================= rapor

class ReportBody(BaseModel):
    kind: str = Field(max_length=24)
    start: str = Field(default="", max_length=10)
    end: str = Field(default="", max_length=10)


@router.post("/preview")
async def report_preview(
    body: ReportBody,
    user: CurrentUser = requires("store_payment_gateway.view"),
) -> dict[str, Any]:
    return await service().preview_report(body.kind, {"start": body.start, "end": body.end})


class PrintBody(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    copies: int = Field(default=1, ge=1, le=20)


@router.post("/print")
async def print_report(
    body: PrintBody,
    user: CurrentUser = requires("store_payment_gateway.view"),
) -> dict[str, Any]:
    """Yol doğrulaması serviste ZORUNLUDUR: rapor klasörü dışındaki dosya basılmaz."""
    return await service().print_report(body.path, copies=body.copies)


@router.get("/printer")
async def printer(
    user: CurrentUser = requires("store_payment_gateway.view"),
) -> dict[str, Any]:
    return await service().printer_status()


class ExportBody(BaseModel):
    q: str = Field(default="", max_length=120)
    status: str = Field(default="", max_length=24)
    start: str = Field(default="", max_length=10)
    end: str = Field(default="", max_length=10)


@router.post("/export")
async def export(
    body: ExportBody,
    user: CurrentUser = requires("store_payment_gateway.view"),
) -> dict[str, Any]:
    """TÜM kayıtların CSV'si — rapor klasörüne yazılır. Görünen sayfanın
    CSV'sini panel kendisi üretir (sunucuya hiç gitmez)."""
    return await service().export_csv(filters={"q": body.q, "status": body.status,
                                               "start": body.start, "end": body.end})
