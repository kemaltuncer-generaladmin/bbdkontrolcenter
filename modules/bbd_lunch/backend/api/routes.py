"""Öğle Yemeği — HTTP yüzeyi.

ÇİFT KAPI (K9): router `bbd_lunch.view` ile korunur, yazan uçlar `manage`,
geri alma `reverse` ister. Arayüzde düğmeyi gizlemek yetkilendirme değildir.
"""

from __future__ import annotations

from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, requires

from ..service import LunchService

router = APIRouter()

_service: LunchService | None = None


def bind(service: LunchService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> LunchService:
    if _service is None:  # pragma: no cover - yükleme sırası garanti eder
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


class SelectionBody(BaseModel):
    """Bir günün toplu yemek seçimi."""

    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    students: list[str] = Field(default_factory=list, max_length=2000)
    portion: int = Field(default=1, ge=1, le=1000)
    unitPrice: int | None = Field(default=None, ge=1, le=100_000_000)
    note: str = Field(default="", max_length=300)
    # Sınıf adları yalnız iz olarak saklanır; kaynağı Öğrenci Yönetimi modülüdür.
    classes: dict[str, str] = Field(default_factory=dict)
    # O gün yemeği zaten işlenmiş öğrenciye İKİNCİ porsiyon girme izni.
    # Varsayılan KAPALI: kazayla iki kez "İşle" demek çift borç yazmasın.
    allowRepeat: bool = False
    dryRun: bool = False


class RangeBody(SelectionBody):
    """Aralık işleme — hafta sonu ve tatil günleri atlanır."""

    endDate: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class ReverseBody(BaseModel):
    batchRef: str | None = Field(default=None, max_length=64)
    localId: str | None = Field(default=None, max_length=64)
    reason: str = Field(min_length=3, max_length=255)


class RosterBody(BaseModel):
    students: list[str] = Field(default_factory=list, max_length=2000)


class HolidayBody(BaseModel):
    day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    label: str = Field(default="", max_length=120)
    remove: bool = False


class StockBody(BaseModel):
    quantity: int = Field(ge=1, le=1_000_000)
    reason: str = Field(default="", max_length=255)


@router.get("/overview")
async def overview(
    month: str,
    user: CurrentUser = requires("bbd_lunch.view"),
) -> dict[str, Any]:
    """Takvim ekranının açılış verisi. `month` = YYYY-MM."""
    if len(month) != 7 or month[4] != "-":
        raise HTTPException(status_code=422, detail="Ay biçimi YYYY-MM olmalı.")
    return await service().overview(month)


@router.get("/days/{service_date}")
async def day(
    service_date: str,
    user: CurrentUser = requires("bbd_lunch.view"),
) -> dict[str, Any]:
    return await service().day(service_date)


@router.post("/preview")
async def preview(
    body: SelectionBody,
    user: CurrentUser = requires("bbd_lunch.view"),
) -> dict[str, Any]:
    """Gönderim yapmadan sonucu gösterir — engel, limit, stok ve çift kayıt kontrolü."""
    return await service().preview(body.model_dump())


@router.post("/commit")
async def commit(
    body: SelectionBody,
    user: CurrentUser = requires("bbd_lunch.manage"),
) -> dict[str, Any]:
    """Yemek kaydını kantine işler. Sonuç kasada elle girilmişten ayırt edilemez."""
    return await service().commit(body.model_dump(), actor=user.full_name)


@router.post("/commit-range")
async def commit_range(
    body: RangeBody,
    user: CurrentUser = requires("bbd_lunch.manage"),
) -> dict[str, Any]:
    """Tarih aralığındaki İŞ GÜNLERİNE aynı listeyi işler (hafta sonu + tatil hariç)."""
    days = await service().working_days(body.date, body.endDate)
    if not days:
        return {"ok": False, "error": "Aralıkta iş günü yok (hafta sonu/tatil)."}

    payload = body.model_dump()
    payload.pop("endDate", None)
    results = []
    for day_iso in days:
        results.append({
            "date": day_iso,
            "result": await service().commit({**payload, "date": day_iso},
                                             actor=user.full_name),
        })
    return {"ok": True, "days": days, "results": results}


@router.post("/reverse")
async def reverse(
    body: ReverseBody,
    user: CurrentUser = requires("bbd_lunch.reverse"),
) -> dict[str, Any]:
    """Geri alma. Kantinde ters cari kayıt + stok iadesi oluşur; satır SİLİNMEZ."""
    return await service().reverse(
        batch_ref=body.batchRef, local_id=body.localId, reason=body.reason
    )


@router.put("/roster")
async def set_roster(
    body: RosterBody,
    user: CurrentUser = requires("bbd_lunch.manage"),
) -> dict[str, Any]:
    """Sabit liste — her gün yemek yiyenler, yeni gün açılışında ön seçili gelir."""
    return await service().set_roster(body.students)


@router.put("/holidays")
async def set_holiday(
    body: HolidayBody,
    user: CurrentUser = requires("bbd_lunch.manage"),
) -> dict[str, Any]:
    return await service().set_holiday(body.day, body.label, remove=body.remove)


@router.post("/stock")
async def top_up_stock(
    body: StockBody,
    user: CurrentUser = requires("bbd_lunch.manage"),
) -> dict[str, Any]:
    """Yemek ürününe stok girişi — seçim stoktan fazlaysa akışı kesmeden çözer."""
    return await service().top_up_stock(body.quantity, body.reason)
