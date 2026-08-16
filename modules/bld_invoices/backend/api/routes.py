"""Faturalar — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde düğmeyi gizlemek yetkilendirme
değildir. `module.yaml` → `http.requires` taban izni verir, uçlar onu DARALTIR.

ÜÇ İZİN, İKİ DEĞİL:

    bld_invoices.view     okuma, belge üretme/önizleme/yazdırma
    bld_invoices.manage   belge kesme (kuru prova dâhil)
    bld_invoices.void     YIKICI: geçerli belgeyi iptal etme

Üçüncüsünün ayrı durmasının nedeni: iptal, müşterinin elindeki kâğıdı geçersiz
kılar, numarayı seride ölü bırakır (boşluk açılmaz, geri kullanılmaz) ve GERİ
ALINAMAZ. Düzeltme, iptalden sonra kesilen YENİ bir belgedir.

`PATCH` ve `DELETE` UCU YOKTUR ve eklenmeyecektir. Kesilmiş bir belgenin
içeriği değiştirilemez; düzenlenebilen bir belge, elindeki kâğıtla sistemdeki
kayıt farklı olan bir müşteri üretir. Silinen bir belge ise seride "44 nerede"
sorusunu cevapsız bırakır.

PDF üretme ve yazdırma `bld_invoices.view` ile açıktır: belgeyi zaten
görebilen birinin onu kâğıda dökmesi yeni bir yetki değildir. Yazma yolları
(kes, iptal) ayrı ve dar kapılardan geçer.

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran
mesajı gösterir. 4xx yalnız izin ve şema kapısından çıkar.
"""

from __future__ import annotations

from typing import Any, ClassVar

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..documents import MAX_REASON, MIN_REASON
from ..service import InvoicesService

#: İzin anahtarları tek yerde durur: uç noktalar ve servis aynı dizgeyi okur,
#: yazım hatası bir kapıyı sessizce açık bırakamaz.
VIEW = "bld_invoices.view"
MANAGE = "bld_invoices.manage"
VOID = "bld_invoices.void"

router = APIRouter()
_service: InvoicesService | None = None


def bind(service: InvoicesService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> InvoicesService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


class ReasonBody(BaseModel):
    """Her yazma gövdesinin ortak iki alanı.

    `actor` GÖVDEDEN ALINMAZ — oturumdan gelir. İstemcinin aktör adını
    yazabilmesi, denetim izini imzalanmamış bir deftere çevirirdi.

    `dryRun` camelCase'tir ve TEK KABUL EDİLEN addır (`bld_kds` deseni).
    `dry_run` da kabul edilseydi, yanlış yazılan bir ad sessizce düşer ve alan
    "hiç gönderilmemiş" sayılıp varsayılana dönerdi — kuru prova sanılan bir
    isteğin gerçek belge kesmesi böyle olurdu. `extra="forbid"` sayesinde
    yanlış yazılan alan 422 ile geri döner.
    """

    #: `ClassVar`: `km_sdk` pydantic'in `ConfigDict` tipini dışa vurmuyor ve
    #: modül pydantic'i doğrudan import etmiyor (K2). Düz sözlük yeterli.
    model_config: ClassVar[dict[str, Any]] = {"extra": "forbid"}

    reason: str = Field(min_length=MIN_REASON, max_length=MAX_REASON)
    dryRun: bool | None = None


# ================================================================== okuma

@router.get("/invoices")
async def invoices(
    q: str = Query("", max_length=120),
    status: str = Query("", max_length=16),
    customer_id: int = Query(0, ge=0),
    order_id: int = Query(0, ge=0),
    subscription_id: int = Query(0, ge=0),
    date_from: str = Query("", max_length=10),
    date_to: str = Query("", max_length=10),
    page: int = Query(1, ge=1, le=10_000),
    per_page: int = Query(0, ge=0, le=200),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Belge listesi. Sayfalama SUNUCUDADIR; `meta.issued_total_kurus`
    süzgeçlenmiş kümenin toplamıdır, sayfanın değil."""
    return await service().invoices(q=q, status=status, customer_id=customer_id,
                                    order_id=order_id, subscription_id=subscription_id,
                                    date_from=date_from, date_to=date_to,
                                    page=page, per_page=per_page)


@router.get("/invoices/{invoice_id}")
async def invoice(
    invoice_id: int,
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Tek belge — donmuş içeriğiyle (`snapshot_json`)."""
    return await service().invoice(invoice_id)


@router.get("/archive")
async def archive(
    invoice_id: int = Query(0, ge=0),
    limit: int = Query(0, ge=0, le=500),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Bu makinede ÜRETİLMİŞ dosyaların künyesi — belge verisi değil, DOSYA."""
    return await service().archive(invoice_id=invoice_id, limit=limit)


@router.get("/audit")
async def audit(
    invoice_id: int = Query(0, ge=0),
    limit: int = Query(0, ge=0, le=500),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Yerel denetim izi: kim neyi denedi. SATIR SİLİNMEZ."""
    return await service().audit(invoice_id=invoice_id, limit=limit)


# ================================================================== yazma

class CreateBody(ReasonBody):
    """Belge kesme gövdesi. İKİ KİP, biri seçilir.

    Alan adları sözleşmenin snake_case sözlüğünü korur; tek istisna `dryRun`
    (panel→Kontrol Merkezi sınırı). Kip denetimi hem burada (aşağıdaki servis
    çağrısından önce) hem de geçitte yapılır: ikisi birden gönderilmiş bir
    istek, sunucuya hiç çıkmadan anlaşılır bir cümleyle geri döner.
    """

    order_id: int = Field(default=0, ge=0)
    subscription_id: int = Field(default=0, ge=0)
    period_start: str = Field(default="", max_length=10)
    period_end: str = Field(default="", max_length=10)
    subscription_payment_id: int = Field(default=0, ge=0)


@router.post("/invoices")
async def create_invoice(
    body: CreateBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Belge keser. Kuru prova NUMARA ÜRETMEZ ama toplamı hesaplar.

    Aynı sipariş/dönem için geçerli bir belge varsa sunucu 409 verir; ikinci
    bir belge kesilmez, önce eskisi iptal edilir.
    """
    return await service().create(order_id=body.order_id,
                                  subscription_id=body.subscription_id,
                                  period_start=body.period_start,
                                  period_end=body.period_end,
                                  subscription_payment_id=body.subscription_payment_id,
                                  reason=body.reason, actor=user.full_name,
                                  dry_run=body.dryRun)


@router.post("/invoices/{invoice_id}/void")
async def void_invoice(
    invoice_id: int,
    body: ReasonBody,
    user: CurrentUser = requires(VOID),
) -> dict[str, Any]:
    """YIKICI. Belge geçersiz olur, numara seride ölü kalır, geri alınamaz.

    İzin kapısı burada ve serviste iki kez denetlenir (K9 — çift kapı).
    `void_reason` alanına ORTAK `reason` metni yazılır; ayrı bir alan
    istenmez, ikisinin çelişmesine yol açardı.
    """
    return await service().void(invoice_id, reason=body.reason, actor=user.full_name,
                                dry_run=body.dryRun,
                                allow_void=user.has_permission(VOID))


@router.post("/invoices/{invoice_id}/html")
async def save_html(
    invoice_id: int,
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Sunucunun yazdırılabilir HTML'ini rapor klasörüne yazar.

    BLD'de hiçbir şey değişmez — bu bir OKUMADIR ve yazma yalnız yerel diske
    yapılır; bu yüzden gerekçe istemez ve `view` yeter. `POST` olmasının
    nedeni yan etkisinin (dosya üretimi) olmasıdır.
    """
    return await service().save_html(invoice_id, actor=user.full_name)


# ================================================================== rapor

class PreviewBody(BaseModel):
    """`report.js` → `reportChain` gövdesi: üret → önizle → bas.

    `kind` iki değer alır: `invoice` (tek belgenin A4'ü) ve `list`
    (süzgeçlenmiş dökümün özeti). Süzgeç alanları listede kullanılır;
    tek belgede yalnız `invoice_id` okunur.
    """

    model_config: ClassVar[dict[str, Any]] = {"extra": "forbid"}

    kind: str = Field(max_length=24)
    invoice_id: int = Field(default=0, ge=0)
    q: str = Field(default="", max_length=120)
    status: str = Field(default="", max_length=16)
    customer_id: int = Field(default=0, ge=0)
    order_id: int = Field(default=0, ge=0)
    subscription_id: int = Field(default=0, ge=0)
    date_from: str = Field(default="", max_length=10)
    date_to: str = Field(default="", max_length=10)


@router.post("/preview")
async def preview(
    body: PreviewBody,
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    return await service().preview(body.kind, {
        "invoice_id": body.invoice_id, "q": body.q, "status": body.status,
        "customer_id": body.customer_id, "order_id": body.order_id,
        "subscription_id": body.subscription_id,
        "date_from": body.date_from, "date_to": body.date_to,
    }, actor=user.full_name)


class PrintBody(BaseModel):
    model_config: ClassVar[dict[str, Any]] = {"extra": "forbid"}

    path: str = Field(min_length=1, max_length=1000)
    copies: int = Field(default=1, ge=1, le=20)


@router.post("/print")
async def print_report(
    body: PrintBody,
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Üretilmiş belgeyi CUPS'a gönderir. YALNIZ rapor klasöründeki dosya."""
    return await service().print_report(body.path, copies=body.copies)


@router.get("/printer")
async def printer(
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Yazıcı durumu. Yetenek yoksa `ready: False` + neden döner (K7)."""
    return await service().printer_status()
