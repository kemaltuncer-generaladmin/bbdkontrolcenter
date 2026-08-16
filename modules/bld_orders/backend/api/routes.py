"""Sipariş Yönetimi — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde düğmeyi gizlemek yetkilendirme
değildir. `module.yaml` → `http.requires` taban izni verir, uçlar onu DARALTIR.

ÜÇ İZİN, İKİ DEĞİL:

    bld_orders.view     okuma — liste, ayrıntı, revizyon geçmişi, fatura
                        künyesi, yerel iz, CSV dışa aktarım
    bld_orders.manage   revizyon yazma ve durum ilerletme
    bld_orders.cancel   YIKICI: sipariş iptali

Üçüncüsünün ayrı durmasının nedeni PARA. İptal `status` ucundan `iptal`
göndermekle aynı şey değildir ve sözleşmede de ayrı bir uçtur: ödenmiş bir
siparişin İADE KAYDINI üretir, müşteriye SMS gönderir ve porsiyonları gün
toplamı ile ürün tavanından düşürür. Revizyon yazabilen bir personelin, tek
tıkla iade üretebilmesi için hiçbir sebep yok.

PIN DEĞİL GEREKÇE (ADR 0012, `bld_kds` ile aynı ilke): ayrı izin anahtarı +
en az 10 karakter gerekçe (backend'de DE doğrulanır) + iki denetim satırı
("ne denendi" ve "ne oldu"). Bu yüzden hiçbir izin `destructive: true`
taşımaz — o bayrak çekirdekte PIN kapısına bağlanacak ve bu ekran PIN
istemiyor.

İPTAL UCU YALNIZ `bld_orders.cancel` KABUL EDER. `bld_kds`in komut ucundaki
"iki izin, en az biri" deseni burada yanlış olurdu: orada tek uç sekiz farklı
komutu taşıyor ve ayrımı gövde yapıyor; burada ucun TEK işi var ve o iş yıkıcı.
Servis izni AYRICA denetler (`allow_cancel`, çift kapı K9).

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran
mesajı gösterir. 4xx yalnız izin ve şema kapısından çıkar.
"""

from __future__ import annotations

from typing import Any, ClassVar

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..orders import MAX_REASON, MIN_REASON
from ..service import OrdersService

#: İzin anahtarları tek yerde durur: uçlar ve servis aynı dizeyi okur, yazım
#: hatası bir kapıyı sessizce açık bırakamaz.
VIEW = "bld_orders.view"
MANAGE = "bld_orders.manage"
CANCEL = "bld_orders.cancel"

router = APIRouter()
_service: OrdersService | None = None


def bind(service: OrdersService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> OrdersService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


# ============================================================ ortak gövdeler

class ReasonBody(BaseModel):
    """Her yazma gövdesinin ortak iki alanı.

    `actor` GÖVDEDEN ALINMAZ — oturumdan gelir. İstemcinin aktör adını
    yazabilmesi, denetim izini imzalanmamış bir deftere çevirirdi: silinmeyen
    bir satıra istediği adı yazan biri, işi başkasının üstüne bırakabilirdi.

    `dryRun` camelCase'tir ve TEK KABUL EDİLEN addır. Panel→Kontrol Merkezi
    sınırında camelCase geçerli; tele giden `dry_run` adına çeviriyi geçit
    yapar. `dry_run` da kabul edilseydi bir yazım hatası ("dryrun", "dry_Run")
    sessizce düşer, alan hiç gönderilmemiş sayılır ve varsayılana dönerdi —
    yani kuru prova sanılan bir istek GERÇEK YAZMA yapabilirdi. Tek ad +
    `extra="forbid"` sayesinde yanlış yazılan alan 422 ile geri döner.
    """

    #: `ClassVar` ile işaretli, çünkü `km_sdk` pydantic'in `ConfigDict` tipini
    #: dışa vurmuyor ve modül pydantic'i doğrudan import etmiyor (K2). Düz
    #: sözlük pydantic için yeterli; işaret yalnız "bu bir alan değil" der.
    model_config: ClassVar[dict[str, Any]] = {"extra": "forbid"}

    reason: str = Field(min_length=MIN_REASON, max_length=MAX_REASON)
    dryRun: bool | None = None


class FilterBody(BaseModel):
    """Liste ve dışa aktarımın ORTAK süzgeç gövdesi.

    İkisi aynı alanları taşımak ZORUNDA: muhasebeciye giden CSV, ekranda
    görünen kümenin ta kendisi olmalı. Ayrı iki gövde, iki alanın sessizce
    ayrışması demekti.
    """

    model_config: ClassVar[dict[str, Any]] = {"extra": "forbid"}

    service_date: str = Field(default="", max_length=10)
    date_from: str = Field(default="", max_length=10)
    date_to: str = Field(default="", max_length=10)
    #: Virgüllü kod listesi ya da dizi; ikisini de geçit `_csv` ile çözer.
    status: list[str] | str | None = None
    delivery_type: str = Field(default="", max_length=16)
    customer_id: int = Field(default=0, ge=0)
    subscription_id: int = Field(default=0, ge=0)
    source: str = Field(default="", max_length=16)
    q: str = Field(default="", max_length=120)


# ================================================================== okuma

@router.get("/overview")
async def overview(
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Ekranın sözleşmesi ve kullanıcının tercihleri. AĞA ÇIKMAZ (K7)."""
    return await service().overview()


@router.get("/orders")
async def orders(
    service_date: str = Query("", max_length=10),
    date_from: str = Query("", max_length=10),
    date_to: str = Query("", max_length=10),
    status: str = Query("", max_length=120),
    delivery_type: str = Query("", max_length=16),
    customer_id: int = Query(0, ge=0),
    subscription_id: int = Query(0, ge=0),
    source: str = Query("", max_length=16),
    q: str = Query("", max_length=120),
    page: int = Query(1, ge=1),
    per_page: int = Query(0, ge=0, le=100),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Sayfalı, süzülen, GEÇMİŞE bakan liste.

    Süzgeçler sorgu dizesindedir (gövde değil): liste bir okumadır ve
    kopyalanabilir bir adresinin olması, "şu ekranı aç" demenin en kısa yolu.
    """
    return await service().orders(
        service_date=service_date, date_from=date_from, date_to=date_to, status=status,
        delivery_type=delivery_type, customer_id=customer_id,
        subscription_id=subscription_id, source=source, q=q, page=page, per_page=per_page)


@router.get("/orders/{order_id}")
async def order(
    order_id: int,
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Düzenlenebilir görünüm + geri alma penceresi (sunucudan okunur)."""
    return await service().order(order_id)


@router.get("/orders/{order_id}/revisions")
async def order_revisions(
    order_id: int,
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    return await service().order_revisions(order_id)


@router.get("/orders/{order_id}/invoice")
async def order_invoice(
    order_id: int,
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Var olan fatura BELGESİNE bakar; ÜRETMEZ.

    `bld_orders.view` YETER. Sözleşme bu ucu `bld_invoices.view` altında
    listeliyor ama o anahtar başka bir modülün kataloğunda; bir modül kendi
    kimliği dışında izin TANIMLAYAMAZ (`module.schema.json`). Burada dönen şey
    zaten siparişin künyesidir — belgenin kendisi (`/invoices/{id}/html`) bu
    ekranda hiç açılmaz. Sapma raporda bildirildi.
    """
    return await service().order_invoice(order_id)


@router.get("/audit")
async def audit(
    limit: int = Query(50, ge=1, le=500),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Bu ekrandan yapılan yazma denemelerinin YEREL izi — sunucununki değil."""
    return await service().audit(limit=limit)


# ================================================================== yazma

class RevisionBody(ReasonBody):
    #: TAM LİSTE. Kalem farkı değil, siparişin YENİ hâli gönderilir; boş liste
    #: reddedilir çünkü siparişi boşaltırdı ve iptalin kendi ucu var.
    items: list[dict[str, Any]] = Field(default_factory=list)
    note: str = Field(default="", max_length=1000)
    requested_at: str = Field(default="", max_length=32)
    customer_note: str = Field(default="", max_length=1000)


@router.post("/orders/{order_id}/revisions")
async def create_revision(
    order_id: int,
    body: RevisionBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    return await service().create_revision(
        order_id, items=body.items, reason=body.reason, actor=user.full_name,
        note=body.note, requested_at=body.requested_at,
        customer_note=body.customer_note, dry_run=body.dryRun)


class StatusBody(ReasonBody):
    status: str = Field(min_length=1, max_length=32)


@router.post("/orders/{order_id}/status")
async def set_order_status(
    order_id: int,
    body: StatusBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    """Durum geçişi. Matris SUNUCUDA uygulanır ve burada kopyası yoktur.

    `status="iptal"` bu uçtan REDDEDİLİR: iptal para hareketi üretir ve ayrı
    izin ister. Uç noktada değil serviste reddedilmesi bilinçli — kural tek
    yerde dursun ve gövdeyi elle kuran bir istemci de aynı cevabı alsın.
    """
    return await service().set_status(order_id, status=body.status, reason=body.reason,
                                      actor=user.full_name, dry_run=body.dryRun)


class CancelBody(ReasonBody):
    #: Ödeme `paid` ise iade kaydı üretilir. `False` göndermek SERBESTTİR ve
    #: yanıt `warnings` taşır: bazen para elden iade edilir ve sistemin ikinci
    #: kez iade üretmemesi gerekir.
    refund: bool = True
    #: İptal SMS'i (`sms.md` → `order_cancelled`).
    notify_customer: bool = True


@router.post("/orders/{order_id}/cancel")
async def cancel_order(
    order_id: int,
    body: CancelBody,
    user: CurrentUser = requires(CANCEL),
) -> dict[str, Any]:
    """YIKICI. İade + SMS + stok iadesi üretir; kayıt SİLİNMEZ.

    İzin kapısı burada ve serviste iki kez denetlenir (K9 — çift kapı).
    """
    return await service().cancel(
        order_id, reason=body.reason, actor=user.full_name, refund=body.refund,
        notify_customer=body.notify_customer, dry_run=body.dryRun,
        allow_cancel=user.has_permission(CANCEL))


# ============================================================ dışa aktarım

class ExportBody(FilterBody):
    #: Sunucu tavanı 20000; varsayılan modül ayarından gelir.
    max_rows: int = Field(default=0, ge=0, le=20000)


@router.post("/export")
async def export_orders(
    body: ExportBody,
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """CSV üretir ve DİSKE yazar. `bld_orders.view` YETER — bu bir OKUMADIR.

    `POST` seçilmesinin sebebi yetki değil YAN ETKİ: uç, kullanıcının
    makinesinde 0600 izinli bir dosya oluşturur. Yan etkisi olan bir isteğin
    `GET` olması, tarayıcının/kabuğun onu yeniden denemesini masum kılardı.
    """
    return await service().export(
        actor=user.full_name, max_rows=body.max_rows, service_date=body.service_date,
        date_from=body.date_from, date_to=body.date_to, status=body.status,
        delivery_type=body.delivery_type, customer_id=body.customer_id,
        subscription_id=body.subscription_id, source=body.source, q=body.q)


# ============================================================ ekran tercihi

class PrefsBody(BaseModel):
    """Görüntüleme tercihi. BLD'ye HİÇBİR ŞEY GİTMEZ.

    `bld_orders.view` YETER ve gerekçe İSTENMEZ: sayfa boyutunu değiştirmek bir
    iş yazması değildir. `manage` istemek, gerçek yazmalardaki izin ayrımını
    anlamsızlaştırırdı — "yazma yetkisi" sayfa boyutunu da kapsayınca ayrımın
    anlattığı şey kalmaz.
    """

    model_config: ClassVar[dict[str, Any]] = {"extra": "forbid"}

    page_size: int | None = Field(default=None, ge=5, le=100)
    range_days: int | None = Field(default=None, ge=1, le=90)
    poll_seconds: int | None = Field(default=None, ge=5, le=300)
    auto_refresh: bool | None = None


@router.put("/prefs")
async def save_prefs(
    body: PrefsBody,
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    values = {key: value for key, value in body.model_dump().items() if value is not None}
    if not values:
        # Boş gövde hiçbir şey değiştirmeden bir satır yazardı.
        return {"ok": False, "error": "Kaydedilecek bir tercih gönderilmedi."}
    return await service().save_prefs(values, actor=user.full_name)
