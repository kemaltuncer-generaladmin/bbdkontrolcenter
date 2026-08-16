"""Talepler — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde düğmeyi gizlemek yetkilendirme
değildir. Yıkıcı uçlarda gerekçe `min_length=10` ile ŞEMADA doğrulanır, ayrıca
serviste tekrar denetlenir — istemci şemayı atlatabilir.

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran
mesajı gösterir. 4xx yalnız izin/şema kapısından çıkar.

İKİ AYRI YAZIŞMA UCU. `/reply` müşteriye gider (uzak), `/note` iç nottur
(yerel). Tek uçta bir bayrakla ayırmak, bayrak hatasında personel yazışmasını
müşteriye gösterirdi; ayrı uçlar bu hatayı imkânsız kılar.
"""

from __future__ import annotations

from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..rma import list_filters
from ..service import RequestsService

router = APIRouter()
_service: RequestsService | None = None


def bind(service: RequestsService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> RequestsService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


# ================================================================== okuma

@router.get("/requests")
async def requests(
    q: str = Query("", max_length=160),
    type: str = Query("", max_length=24),
    status: str = Query("", max_length=24),
    priority: str = Query("", max_length=16),
    assignee: str = Query("", max_length=64),
    channel: str = Query("", max_length=16),
    start: str = Query("", max_length=10),
    end: str = Query("", max_length=10),
    dateField: str = Query("created", max_length=16),
    sla: str = Query("", max_length=16),
    product: str = Query("", max_length=64),
    awaiting: bool = Query(False),
    page: int = Query(1, ge=1, le=10_000),
    size: int = Query(0, ge=0, le=200),
    user: CurrentUser = requires("store_requests.view"),
) -> dict[str, Any]:
    return await service().requests(q=q, kind=type, status=status, priority=priority,
                                    assignee=assignee, channel=channel, start=start, end=end,
                                    date_field=dateField, sla=sla, product=product,
                                    awaiting=awaiting, page=page, size=size)


@router.get("/board")
async def board(
    q: str = Query("", max_length=160),
    type: str = Query("", max_length=24),
    priority: str = Query("", max_length=16),
    assignee: str = Query("", max_length=64),
    channel: str = Query("", max_length=16),
    start: str = Query("", max_length=10),
    end: str = Query("", max_length=10),
    dateField: str = Query("created", max_length=16),
    sla: str = Query("", max_length=16),
    product: str = Query("", max_length=64),
    awaiting: bool = Query(False),
    user: CurrentUser = requires("store_requests.view"),
) -> dict[str, Any]:
    return await service().board(q=q, kind=type, priority=priority, assignee=assignee,
                                 channel=channel, start=start, end=end, date_field=dateField,
                                 sla=sla, product=product, awaiting=awaiting)


@router.get("/requests/{request_id}")
async def request_card(
    request_id: int,
    user: CurrentUser = requires("store_requests.view"),
) -> dict[str, Any]:
    return await service().card(request_id)


@router.get("/reference")
async def reference(
    user: CurrentUser = requires("store_requests.view"),
) -> dict[str, Any]:
    return await service().reference()


@router.get("/audit")
async def audit(
    requestId: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    user: CurrentUser = requires("store_requests.view"),
) -> dict[str, Any]:
    return await service().audit(request_id=requestId, limit=limit)


# ================================================================== yazma

class NoteBody(BaseModel):
    #: İç not: YEREL kalır, uzağa gönderilmez, müşteri görmez. Bu yüzden
    #: gerekçe istenmez — notun kendisi zaten metindir.
    body: str = Field(min_length=2, max_length=4_000)


@router.post("/requests/{request_id}/note")
async def add_note(
    request_id: int,
    body: NoteBody,
    user: CurrentUser = requires("store_requests.manage"),
) -> dict[str, Any]:
    return await service().add_note(request_id, body=body.body, actor=user.full_name)


class ReplyBody(BaseModel):
    #: MÜŞTERİYE gider. Geri alınamaz: gerekçe ve kuru prova zorunlu.
    body: str = Field(min_length=2, max_length=4_000)
    status: str = Field(default="", max_length=24)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/requests/{request_id}/reply")
async def reply(  # MAĞAZADA UCU YOK: servis nedenini söyler, istek gönderilmez.
    request_id: int,
    body: ReplyBody,
    user: CurrentUser = requires("store_requests.manage"),
) -> dict[str, Any]:
    return await service().reply(request_id, body=body.body, status=body.status,
                                 reason=body.reason, actor=user.full_name, dry_run=body.dryRun)


class UpdateBody(BaseModel):
    #: MAĞAZANIN durum kimliği. Metin de kabul edilir ve sayıya çevrilir;
    #: ekranın altı anahtarı (`closed` gibi) buraya YAZILMAZ — dokuzu altıya
    #: indiren eşleme tek yönlüdür ve tersi belirsizdir (bkz. `rma.py`).
    status: int | str = ""
    #: ÖNCELİK VE ATAMA MAĞAZADA YOK. Şemadan atılmadılar ki eski bir istemci
    #: 422 yerine NEDENİNİ okusun; servis ikisini de reddediyor ve istek
    #: mağazaya hiç gitmiyor.
    priority: str = Field(default="", max_length=16)
    assignee: str = Field(default="", max_length=64)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/requests/{request_id}/update")
async def update(
    request_id: int,
    body: UpdateBody,
    user: CurrentUser = requires("store_requests.manage"),
) -> dict[str, Any]:
    return await service().update(request_id, status=str(body.status), priority=body.priority,
                                  assignee=body.assignee, reason=body.reason,
                                  actor=user.full_name, dry_run=body.dryRun)


class ItemsBody(BaseModel):
    #: {siparişKalemId: adet}. Adet MUTLAKTIR ve sipariş adedine karşı
    #: doğrulanır; fazlası iki kez para iadesine yol açardı.
    selection: dict[str, int] = Field(default_factory=dict)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/requests/{request_id}/items")
async def set_items(
    request_id: int,
    body: ItemsBody,
    user: CurrentUser = requires("store_requests.manage"),
) -> dict[str, Any]:
    return await service().set_items(request_id, selection=body.selection, reason=body.reason,
                                     actor=user.full_name, dry_run=body.dryRun)


class BulkBody(BaseModel):
    requestIds: list[int] = Field(default_factory=list)
    #: `status` ya da `close`. `assign` KALDIRILDI: mağazada atama alanı yok ve
    #: alan gönderilen istek tümüyle reddediliyordu.
    action: str = Field(max_length=16)
    #: `action="status"` için MAĞAZANIN durum kimliği.
    value: str = Field(default="", max_length=64)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/bulk")
async def bulk(
    body: BulkBody,
    user: CurrentUser = requires("store_requests.manage"),
) -> dict[str, Any]:
    return await service().bulk(body.requestIds, action=body.action, value=body.value,
                                reason=body.reason, actor=user.full_name, dry_run=body.dryRun)


# ---------------------------------------------------------- karar (ayrı izin)

class DecisionBody(BaseModel):
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/requests/{request_id}/approve")
async def approve(
    request_id: int,
    body: DecisionBody,
    user: CurrentUser = requires("store_requests.decide"),
) -> dict[str, Any]:
    """Onay PARA İADE ETMEZ: talebi onaylar ve kalemleri İadeler'e devreder.

    Parayı iade etmek `store_refunds` ekranının, ayrı bir iznin ve ayrı bir
    onayın işidir. Buradan iade başlatmak, iade izni olmayan personele para
    iade ettirirdi (K9).
    """
    return await service().decide(request_id, approve=True, reason=body.reason,
                                  actor=user.full_name, dry_run=body.dryRun)


@router.post("/requests/{request_id}/reject")
async def reject(
    request_id: int,
    body: DecisionBody,
    user: CurrentUser = requires("store_requests.decide"),
) -> dict[str, Any]:
    return await service().decide(request_id, approve=False, reason=body.reason,
                                  actor=user.full_name, dry_run=body.dryRun)


# ==================================================== iade kargosu (RMA)
#
# ÜÇ DÜĞME, ÜÇ AYRI KAPI — ve yalnızca biri para harcar:
#   · "Kargoyu aç" / "Durumu tazele" → `store_requests.manage`, 10 karakter
#     gerekçe. Para harcamaz; mükerrer korumalıdır.
#   · "Etiketi satın al"             → `store_requests.purchase` (AYRI ANAHTAR),
#     20 karakter gerekçe, `dryRun` varsayılanı `true`.
# Kargo gönderisi açabilen herkesin fatura kestirebilmesi gerekmez; anahtarın
# ayrı olması, izni sonradan daraltabilmek içindir.

PURCHASE_REASON = 20


@router.get("/requests/{request_id}/shipment")
async def shipment(
    request_id: int,
    user: CurrentUser = requires("store_requests.view"),
) -> dict[str, Any]:
    """Talebin iade kargosu — SALT OKUMA. Gönderi yoksa hata değil, boş durum."""
    return await service().shipment(request_id)


class ShipmentBody(BaseModel):
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/requests/{request_id}/shipment")
async def open_shipment(
    request_id: int,
    body: ShipmentBody,
    user: CurrentUser = requires("store_requests.manage"),
) -> dict[str, Any]:
    """İade gönderisini açar — ETİKET SATIN ALMAZ (o ayrı izin ister)."""
    return await service().open_shipment(request_id, reason=body.reason,
                                         actor=user.full_name, dry_run=body.dryRun)


@router.post("/requests/{request_id}/shipment/sync")
async def sync_shipment(
    request_id: int,
    body: ShipmentBody,
    user: CurrentUser = requires("store_requests.manage"),
) -> dict[str, Any]:
    """Kargo durumunu taşıyıcıdan tazeler. Yalnız okur; para hareketi yoktur."""
    return await service().sync_shipment(request_id, reason=body.reason,
                                         actor=user.full_name, dry_run=body.dryRun)


class PurchaseLabelBody(BaseModel):
    offerId: str = Field(default="", max_length=120)
    reason: str = Field(min_length=PURCHASE_REASON, max_length=255)
    dryRun: bool = True


@router.post("/requests/{request_id}/shipment/label")
async def purchase_label(
    request_id: int,
    body: PurchaseLabelBody,
    user: CurrentUser = requires("store_requests.purchase"),
) -> dict[str, Any]:
    """İADE ETİKETİNİ SATIN ALIR — PARA HARCAR.

    Kargo ekranındaki etiket satın almayla aynı koruma: ayrı izin anahtarı,
    20 karakter gerekçe ve `dryRun` varsayılanı açık. Onay kutusunda "uygula"
    işaretlenmeden mağazaya gerçek istek gitmez.
    """
    return await service().purchase_label(request_id, offer_id=body.offerId, reason=body.reason,
                                          actor=user.full_name, dry_run=body.dryRun)


@router.get("/requests/{request_id}/shipment/label")
async def label(
    request_id: int,
    user: CurrentUser = requires("store_requests.view"),
) -> dict[str, Any]:
    """Satın alınmış etiketin PDF'ini indirir ve dosya yolunu döner.

    OKUMA İZNİ YETER: etiketi BASMAK satın almakla aynı şey değildir; parayı
    harcayan adım zaten olmuş ve faturalanmıştır.
    """
    return await service().label(request_id)


# ================================================================== rapor

class PreviewBody(BaseModel):
    kind: str = Field(max_length=24)
    requestId: int = 0
    type: str = Field(default="", max_length=24)
    status: str = Field(default="", max_length=24)
    assignee: str = Field(default="", max_length=64)
    start: str = Field(default="", max_length=10)
    end: str = Field(default="", max_length=10)


@router.post("/preview")
async def preview(
    body: PreviewBody,
    user: CurrentUser = requires("store_requests.view"),
) -> dict[str, Any]:
    return await service().preview(body.kind, {
        "requestId": body.requestId, "type": body.type, "status": body.status,
        "assignee": body.assignee, "start": body.start, "end": body.end,
    })


class PrintBody(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    copies: int = Field(default=1, ge=1, le=20)


@router.post("/print")
async def print_report(
    body: PrintBody,
    user: CurrentUser = requires("store_requests.view"),
) -> dict[str, Any]:
    return await service().print_report(body.path, copies=body.copies)


@router.get("/printer")
async def printer(
    user: CurrentUser = requires("store_requests.view"),
) -> dict[str, Any]:
    return await service().printer_status()


class ExportBody(BaseModel):
    q: str = Field(default="", max_length=160)
    type: str = Field(default="", max_length=24)
    status: str = Field(default="", max_length=24)
    priority: str = Field(default="", max_length=16)
    assignee: str = Field(default="", max_length=64)
    channel: str = Field(default="", max_length=16)
    start: str = Field(default="", max_length=10)
    end: str = Field(default="", max_length=10)


@router.post("/export")
async def export(
    body: ExportBody,
    user: CurrentUser = requires("store_requests.view"),
) -> dict[str, Any]:
    """TÜM kayıtların CSV'si — rapor klasörüne yazılır. Görünen sayfanın
    CSV'sini panel kendisi üretir (sunucuya hiç gitmez)."""
    return await service().export_csv(filters=list_filters(
        q=body.q, kind=body.type, status=body.status, priority=body.priority,
        assignee=body.assignee, channel=body.channel, start=body.start, end=body.end))
