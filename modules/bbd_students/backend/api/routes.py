"""Öğrenci Yönetimi — HTTP yüzeyi.

KANTİN OTORİTEDİR. Bu uçlar kantinin öğrenci listesini canlı okur; kopya
tutmaz. Kantinde karşılığı olmayan alanlar (ad/soyad ayrımı, sınıf, okul no,
öğrenci telefonu, ikinci veli, not) modülün kendi tablosunda durur ve
`kantinId` ile eşleşir.

YAZMA İNCELİĞİ: kantin `POST /api/students` ucu yalnızca GÖNDERİLEN alanı
günceller. Bu yüzden yalnızca kullanıcının dokunduğu alanlar gönderilir —
aynı anda kasa tabletinden yapılan değişikliğin üstüne geçilmez.
"""

from __future__ import annotations

from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, requires

from .. import qr as qr_codec
from ..service import StudentService

router = APIRouter()

_service: StudentService | None = None


def bind(service: StudentService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> StudentService:
    if _service is None:  # pragma: no cover - yükleme sırası garanti eder
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


class ProfileFields(BaseModel):
    """Kontrol Merkezi'nin kendi alanları."""

    firstName: str | None = Field(default=None, max_length=60)
    lastName: str | None = Field(default=None, max_length=60)
    className: str | None = Field(default=None, max_length=20)
    schoolNo: str | None = Field(default=None, max_length=20)
    studentPhone: str | None = Field(default=None, max_length=20)
    parentName: str | None = Field(default=None, max_length=80)
    parentName2: str | None = Field(default=None, max_length=80)
    parentPhone2: str | None = Field(default=None, max_length=20)
    note: str | None = Field(default=None, max_length=500)


class StudentUpdate(ProfileFields):
    """Kantin alanları + kendi alanlarımız. Gönderilmeyen alana DOKUNULMAZ."""

    displayName: str | None = Field(default=None, max_length=120)
    parentPhone: str | None = Field(default=None, max_length=32)
    spendingLimit: int | None = Field(default=None, ge=0, le=100_000_000)
    isBlocked: bool | None = None


class StudentCreate(StudentUpdate):
    pass


@router.get("/students")
async def list_students(user: CurrentUser = requires("bbd_students.view")) -> dict[str, Any]:
    return await service().list_students()


@router.post("/students")
async def create_student(
    body: StudentCreate,
    user: CurrentUser = requires("bbd_students.manage"),
) -> dict[str, Any]:
    payload = body.model_dump(exclude_unset=True)
    return await service().create_student(payload)


@router.patch("/students/{kantin_id}")
async def update_student(
    kantin_id: str,
    body: StudentUpdate,
    user: CurrentUser = requires("bbd_students.manage"),
) -> dict[str, Any]:
    # exclude_unset: gönderilmeyen alan "değiştirme" demektir, "boşalt" değil.
    payload = body.model_dump(exclude_unset=True)
    return await service().update_student(kantin_id, payload)


@router.post("/students/{kantin_id}/access-code")
async def reset_access_code(
    kantin_id: str,
    user: CurrentUser = requires("bbd_students.access_code"),
) -> dict[str, Any]:
    """Tek tuşla yeni 6 haneli giriş kodu. Eski kod geçersizleşir.

    AYRI İZİN (`access_code`), `manage` DEĞİL: kodu sıfırlamak öğrencinin
    kasaya girişini o an keser ve elindeki/velisindeki kod ölür. Ad ya da
    telefon düzeltebilen herkesin bunu yapabilmesi gerekmiyor (K9/K10).

    Kod ÜRETİLMEZ, kantinden istenir — "kimsede olmayan" güvencesini yalnız
    kantinin unique indeksi verebilir.
    """
    try:
        return await service().reset_access_code(kantin_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/students/{kantin_id}/qr")
async def student_qr(
    kantin_id: str,
    user: CurrentUser = requires("bbd_students.qr"),
) -> dict[str, str]:
    """Kartın QR metni. Anahtar sunucuda kalır, yanıtta yalnızca sonuç döner."""
    key = await service().qr_key()
    return {"kantinId": kantin_id, "qrText": qr_codec.encode(kantin_id, key)}


class CardsBody(BaseModel):
    """Toplu kart basımı. Boş liste = TÜM öğrenciler."""

    students: list[str] = Field(default_factory=list, max_length=5000)


@router.post("/cards")
async def build_cards(
    body: CardsBody,
    user: CurrentUser = requires("bbd_students.qr"),
) -> dict[str, Any]:
    """A4 kart PDF'i üretir (sayfada 3×4 = 12 kart) ve İndirilenler'e yazar."""
    return await service().build_cards(body.students)


@router.get("/status")
async def canteen_status(user: CurrentUser = requires("bbd_students.view")) -> dict[str, Any]:
    return await service().status()
