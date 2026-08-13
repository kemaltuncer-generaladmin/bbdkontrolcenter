"""Vergilendirme — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde düğmeyi gizlemek yetkilendirme
değildir. Yazan uçlarda gerekçe `min_length=10` ile ŞEMADA doğrulanır, ayrıca
serviste tekrar denetlenir — istemci şemayı atlatabilir.

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran mesajı
gösterir. 4xx yalnız izin/şema kapısından çıkar.

İZİN AYRIMI:
  · `store_tax.view`    — okuma, KDV icmali, rapor ve CSV (accountant burada).
  · `store_tax.manage`  — oran ve vergi kategorisi yazma.
  · `store_tax.assign`  — ürünlerin vergi kategorisini toplu değiştirme. AYRI
    anahtar, çünkü bu uç KATALOĞA yazar: yanlış eşleme yanlış KDV'yle satış
    demektir ve etkisi vergi ekranının dışına taşar.
"""

from __future__ import annotations

from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..capability import TaxRates
from ..service import TaxService

router = APIRouter()
_service: TaxService | None = None
_rates: TaxRates | None = None


def bind(service: TaxService, rates: TaxRates | None = None) -> APIRouter:
    global _service, _rates
    _service = service
    _rates = rates
    return router


def service() -> TaxService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


def _forget() -> None:
    """Yazmadan sonra yetenek önbelleğini düşürür; tüketen ekranlar bayat
    oran göstermesin."""
    if _rates is not None:
        _rates.forget()


# ================================================================== okuma

@router.get("/rates")
async def rates(
    q: str = Query("", max_length=120),
    minPercent: float | None = Query(None, ge=0, le=100),
    maxPercent: float | None = Query(None, ge=0, le=100),
    region: str = Query("", max_length=120),
    flag: str = Query("", max_length=24),
    user: CurrentUser = requires("store_tax.view"),
) -> dict[str, Any]:
    """`flag`: '' · 'unassigned' (hiçbir ürüne atanmamış) · 'conflict' (çakışan kural)."""
    return await service().rates(q=q, min_percent=minPercent, max_percent=maxPercent,
                                 region=region, flag=flag)


@router.get("/rates/{rate_id}")
async def rate(
    rate_id: int,
    user: CurrentUser = requires("store_tax.view"),
) -> dict[str, Any]:
    return await service().rate(rate_id)


@router.get("/categories")
async def categories(
    user: CurrentUser = requires("store_tax.view"),
) -> dict[str, Any]:
    return await service().categories()


@router.get("/regions")
async def regions(
    user: CurrentUser = requires("store_tax.view"),
) -> dict[str, Any]:
    return await service().regions()


@router.get("/mapping")
async def mapping(
    q: str = Query("", max_length=120),
    taxCategoryId: int = Query(0, ge=0),
    page: int = Query(1, ge=1, le=10_000),
    size: int = Query(0, ge=0, le=200),
    user: CurrentUser = requires("store_tax.view"),
) -> dict[str, Any]:
    """Katalog kategorisi süzgeci YOKTUR: mağaza `category_id` parametresini
    sessizce yok sayıyor (canlıda doğrulandı) ve uygulanmayan süzgeç
    kullanıcıya süzülmüş sanılan bir liste gösterir."""
    return await service().mapping(q=q, tax_category_id=taxCategoryId, page=page, size=size)


@router.get("/summary")
async def summary(
    start: str = Query("", max_length=10),
    end: str = Query("", max_length=10),
    channel: str = Query("", max_length=32),
    user: CurrentUser = requires("store_tax.view"),
) -> dict[str, Any]:
    """KDV icmali — `accountant` rolünün ana ekranı."""
    return await service().summary(start=start, end=end, channel=channel)


@router.get("/audit")
async def audit(
    target: str = Query("", max_length=48),
    limit: int = Query(50, ge=1, le=500),
    user: CurrentUser = requires("store_tax.view"),
) -> dict[str, Any]:
    return await service().audit(target=target, limit=limit)


# ================================================================== yazma

class RateBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    percent: float = Field(ge=0, le=100)
    country: str = Field(min_length=2, max_length=2)
    state: str = Field(default="", max_length=64)
    isRange: bool = False
    zipCode: str = Field(default="*", max_length=24)
    zipFrom: str = Field(default="", max_length=24)
    zipTo: str = Field(default="", max_length=24)
    #: YEREL alan — mağazaya gitmez. Bagisto vergi oranında tarih tutmaz.
    effectiveFrom: str = Field(default="", max_length=10)
    effectiveNote: str = Field(default="", max_length=255)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True

    def payload(self) -> dict[str, Any]:
        return {"name": self.name, "percent": self.percent, "country": self.country.upper(),
                "state": self.state, "isRange": self.isRange, "zipCode": self.zipCode,
                "zipFrom": self.zipFrom, "zipTo": self.zipTo}


@router.post("/rates")
async def create_rate(
    body: RateBody,
    user: CurrentUser = requires("store_tax.manage"),
) -> dict[str, Any]:
    result = await service().save_rate(None, payload=body.payload(),
                                       effective_from=body.effectiveFrom,
                                       note=body.effectiveNote, reason=body.reason,
                                       actor=user.full_name, dry_run=body.dryRun)
    _forget()
    return result


@router.put("/rates/{rate_id}")
async def update_rate(
    rate_id: int,
    body: RateBody,
    user: CurrentUser = requires("store_tax.manage"),
) -> dict[str, Any]:
    """Oran değişikliği GEÇMİŞ faturaları etkilemez; yanıttaki `notice` bunu söyler."""
    result = await service().save_rate(rate_id, payload=body.payload(),
                                       effective_from=body.effectiveFrom,
                                       note=body.effectiveNote, reason=body.reason,
                                       actor=user.full_name, dry_run=body.dryRun)
    _forget()
    return result


class EffectiveBody(BaseModel):
    effectiveFrom: str = Field(default="", max_length=10)
    note: str = Field(default="", max_length=255)


@router.post("/rates/{rate_id}/effective")
async def set_effective(
    rate_id: int,
    body: EffectiveBody,
    user: CurrentUser = requires("store_tax.manage"),
) -> dict[str, Any]:
    """Yalnız YEREL geçerlilik notunu yazar; mağazaya hiç istek gitmez.

    Gerekçe istemez: mağazada hiçbir şey değişmiyor, bu bir defter notudur.
    Yine de denetim izine kim yazdı bilgisiyle düşer.
    """
    return await service().save_effective(rate_id, effective_from=body.effectiveFrom,
                                          note=body.note, actor=user.full_name)


class CategoryBody(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    #: TAM liste. Bagisto kısmi kabul etmiyor: gönderilmeyen oran kategoriden düşer.
    rateIds: list[int] = Field(default_factory=list)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True

    def payload(self) -> dict[str, Any]:
        return {"code": self.code, "name": self.name, "description": self.description,
                "rateIds": self.rateIds}


@router.post("/categories")
async def create_category(
    body: CategoryBody,
    user: CurrentUser = requires("store_tax.manage"),
) -> dict[str, Any]:
    result = await service().save_category(None, payload=body.payload(), reason=body.reason,
                                           actor=user.full_name, dry_run=body.dryRun)
    _forget()
    return result


@router.put("/categories/{category_id}")
async def update_category(
    category_id: int,
    body: CategoryBody,
    user: CurrentUser = requires("store_tax.manage"),
) -> dict[str, Any]:
    result = await service().save_category(category_id, payload=body.payload(),
                                           reason=body.reason, actor=user.full_name,
                                           dry_run=body.dryRun)
    _forget()
    return result


# ========================================================== ürün eşlemesi

@router.post("/mapping/scan")
async def scan_usage(
    user: CurrentUser = requires("store_tax.view"),
) -> dict[str, Any]:
    """Katalog taraması — vergi kategorisi başına ürün sayısı.

    Yazma DEĞİLDİR (mağazaya yalnız GET gider), bu yüzden gerekçe istemez;
    ama pahalıdır ve elle tetiklenir.
    """
    return await service().scan_usage(actor=user.full_name)


class AssignPreviewBody(BaseModel):
    productIds: list[int] = Field(default_factory=list)
    taxCategoryId: int = Field(ge=1)


@router.post("/mapping/preview")
async def assign_preview(
    body: AssignPreviewBody,
    user: CurrentUser = requires("store_tax.assign"),
) -> dict[str, Any]:
    """Fark tablosu. Mağazaya yazmaz — gerekçe istemez."""
    return await service().assign_preview(product_ids=body.productIds,
                                          tax_category_id=body.taxCategoryId)


class AssignApplyBody(BaseModel):
    token: str = Field(min_length=8, max_length=64)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/mapping/apply")
async def assign_apply(
    body: AssignApplyBody,
    user: CurrentUser = requires("store_tax.assign"),
) -> dict[str, Any]:
    """Önizlenen eşlemeyi uygular. Jeton olmadan uygulama YOK."""
    result = await service().assign_apply(token=body.token, reason=body.reason,
                                          actor=user.full_name, dry_run=body.dryRun)
    _forget()
    return result


# ================================================================= rapor

class PreviewBody(BaseModel):
    kind: str = Field(max_length=24)
    start: str = Field(default="", max_length=10)
    end: str = Field(default="", max_length=10)
    channel: str = Field(default="", max_length=32)


@router.post("/preview")
async def preview(
    body: PreviewBody,
    user: CurrentUser = requires("store_tax.view"),
) -> dict[str, Any]:
    """`kind`: 'vat' (KDV icmali) · 'rates' (oran listesi)."""
    return await service().preview(body.kind, {"start": body.start, "end": body.end,
                                               "channel": body.channel})


class PrintBody(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    copies: int = Field(default=1, ge=1, le=20)


@router.post("/print")
async def print_report(
    body: PrintBody,
    user: CurrentUser = requires("store_tax.view"),
) -> dict[str, Any]:
    return await service().print_report(body.path, copies=body.copies)


@router.get("/printer")
async def printer(
    user: CurrentUser = requires("store_tax.view"),
) -> dict[str, Any]:
    return await service().printer_status()


class ExportBody(BaseModel):
    kind: str = Field(default="rates", max_length=24)
    start: str = Field(default="", max_length=10)
    end: str = Field(default="", max_length=10)
    channel: str = Field(default="", max_length=32)


@router.post("/export")
async def export(
    body: ExportBody,
    user: CurrentUser = requires("store_tax.view"),
) -> dict[str, Any]:
    """TÜM kayıtların CSV'si — rapor klasörüne yazılır. Görünen sayfanın
    CSV'sini panel kendisi üretir (sunucuya hiç gitmez)."""
    return await service().export_csv(body.kind, {"start": body.start, "end": body.end,
                                                  "channel": body.channel})
