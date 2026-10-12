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

from datetime import date
from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from .. import qr as qr_codec
from ..groups import GroupError, GroupService, parse_day
from ..service import ControlsError, StudentService

router = APIRouter()

_service: StudentService | None = None
_groups: GroupService | None = None


def bind(service: StudentService, groups: GroupService | None = None) -> APIRouter:
    global _service, _groups
    _service = service
    _groups = groups
    return router


def service() -> StudentService:
    if _service is None:  # pragma: no cover - yükleme sırası garanti eder
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


def groups() -> GroupService:
    if _groups is None:  # pragma: no cover - yükleme sırası garanti eder
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _groups


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
    # Seviye / alan / hedef sınav — kural `backend/curriculum.py` içinde.
    # `None` göndermek alanı temizler; göndermemek dokunmaz.
    gradeLevel: int | str | None = None
    track: str | None = Field(default=None, max_length=8)
    examTarget: str | None = Field(default=None, max_length=8)


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
    try:
        return await service().create_student(payload)
    except ValueError as exc:
        # Kural ihlali (ad yok, geçersiz seviye/alan) — 500 değil, cümlesiyle 400.
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/students/{kantin_id}")
async def update_student(
    kantin_id: str,
    body: StudentUpdate,
    user: CurrentUser = requires("bbd_students.manage"),
) -> dict[str, Any]:
    # exclude_unset: gönderilmeyen alan "değiştirme" demektir, "boşalt" değil.
    payload = body.model_dump(exclude_unset=True)
    try:
        return await service().update_student(kantin_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class CurriculumBulkBody(BaseModel):
    """Seçili öğrencilere seviye/alan/hedef sınav. Gönderilmeyen alan değişmez;
    boş seçim reddedilir ("boş = tümü" YOK)."""

    students: list[str] = Field(min_length=1, max_length=5000)
    gradeLevel: int | str | None = None
    track: str | None = Field(default=None, max_length=8)
    examTarget: str | None = Field(default=None, max_length=8)


@router.post("/students/curriculum")
async def set_curriculum(
    body: CurriculumBulkBody,
    user: CurrentUser = requires("bbd_students.manage"),
) -> dict[str, Any]:
    """Toplu seviye/alan ataması. Kural öğrenci başına uygulanır; reddedilen
    öğrenci `failed` içinde gerekçesiyle döner, diğerleri yazılır."""
    patch = body.model_dump(exclude_unset=True)
    students = patch.pop("students")
    try:
        return await service().set_curriculum(students, patch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ------------------------------------------------- veli kantin kontrolleri


class ControlsPatch(BaseModel):
    """Veli kantin kontrolleri — gönderilmeyen alan değişmez, `null` limit temizler.

    Tutarlar KURUŞ. `guardianBlocked` yalnız `false` olabilir (veli engeli
    burada konmaz, yalnız kaldırılır — servis reddeder).
    """

    spendingLimit: int | None = Field(default=None, ge=0, le=100_000_000)
    dailyLimit: int | None = Field(default=None, ge=0, le=100_000_000)
    isBlocked: bool | None = None
    guardianBlocked: bool | None = None
    restrictedProductIds: list[int] | None = Field(default=None, max_length=200)


@router.get("/students/{kantin_id}/canteen")
async def canteen_controls(
    kantin_id: str,
    user: CurrentUser = requires("bbd_students.view"),
) -> dict[str, Any]:
    """Kontroller + yasaklı ürün seçicisinin ürün listesi. Kantin düşse de 200."""
    return await service().canteen_controls(kantin_id)


@router.patch("/students/{kantin_id}/canteen/controls")
async def update_canteen_controls(
    kantin_id: str,
    body: ControlsPatch,
    user: CurrentUser = requires("bbd_students.canteen_controls"),
) -> dict[str, Any]:
    """Kontrolleri kantine yazar. AYRI İZİN: limit ve engel öğrencinin kasada
    alışveriş yapıp yapamayacağını belirler; ad düzeltebilen herkesin işi değil."""
    patch = body.model_dump(exclude_unset=True)
    for key in ("isBlocked", "guardianBlocked", "restrictedProductIds"):
        if key in patch and patch[key] is None:
            raise HTTPException(status_code=422, detail=f"“{key}” boş olamaz.")
    try:
        return await service().update_canteen_controls(kantin_id, patch, actor=user.full_name)
    except ControlsError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


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


class BulkAccessCodeBody(BaseModel):
    """Toplu şifre sıfırlama. Tekli uçtan farklı: boş liste = TÜM okul
    demek olabileceğinden, boş liste burada bilerek REDDEDİLİR."""

    students: list[str] = Field(min_length=1, max_length=5000)


@router.post("/students/access-codes")
async def reset_access_codes(
    body: BulkAccessCodeBody,
    user: CurrentUser = requires("bbd_students.access_code"),
) -> dict[str, Any]:
    """Seçili öğrencilerin HEPSİNE yeni giriş kodu üretir.

    Tekli uçla (`/students/{kantin_id}/access-code`) AYNI kantin kapısına
    sırayla bağlanır — burada yeni bir kod üretim yolu açılmaz. Kısmi hata
    döngüyü durdurmaz; `failed` alanında raporlanır.
    """
    try:
        return await service().reset_access_codes(body.students)
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


class PreviewBody(BaseModel):
    """Kart ya da şifre listesi PDF'ini üretir ve önizler.

    `kind` ∈ cards | access-code-list | access-code-reset. Üçü de aynı
    ortak izni ister (`access_code`) — `/cards` ucu kendi başına hâlâ `qr`
    izniyle açık kalır, yalnızca bu YENİ önizleme kolaylığı `access_code`
    sahibi rollerle sınırlıdır.

    YAZDIRMA BU UCA DAHİL DEĞİL: baskı kullanıcının cihazında yapılıyor
    (ADR 0026), sunucuda CUPS yok. Kabuk `POST /api/outputs/document`
    (çekirdek, modül-bağımsız) ile bu uçtan dönen `path`i indirip yerel
    yazıcıya veriyor — modüle özel bir `/print` ucu gerekmiyor.
    """

    kind: str = Field(pattern=r"^(cards|access-code-list|access-code-reset)$")
    students: list[str] = Field(default_factory=list, max_length=5000)


@router.post("/preview")
async def preview(
    body: PreviewBody,
    user: CurrentUser = requires("bbd_students.access_code"),
) -> dict[str, Any]:
    """PDF'i üretir ve dosyanın sayfalarını görüntü olarak döner."""
    return await service().preview(body.kind, body.model_dump())


# ------------------------------------------------------- öğrenci grupları
#
# Sözleşme: bbd-akademi/docs/integrations/km-ogrenci-gruplari-ve-zil.md §2, §7.A.
# OKUMA `bbd_students.view`, YAZMA `bbd_students.groups` — ayrı anahtar: grup
# ve üyelik Akademi'de yoklama listesini ve zil çağrısını belirler; öğrenci
# adını düzeltebilen herkesin işi değil. Kural servistedir (`backend/groups.py`).


def _group_error(exc: GroupError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


def _optional_day(value: str | None, field: str) -> date | None:
    if not value:
        return None
    try:
        return parse_day(value, field=field)
    except GroupError as exc:
        raise _group_error(exc) from exc


class GroupCreate(BaseModel):
    """Grup = ad + öğrenci seçimi. Öğrenciler `validFrom`'dan (vars. bugün) üye olur."""

    name: str = Field(min_length=1, max_length=80)
    kind: str = Field(default="group", pattern=r"^(group|individual)$")
    callName: str = Field(default="", max_length=120)
    note: str = Field(default="", max_length=500)
    studentIds: list[str] = Field(default_factory=list, max_length=2000)
    validFrom: str | None = Field(default=None, max_length=10)
    reason: str = Field(default="", max_length=200)


class GroupFromClass(BaseModel):
    """"Sınıftan grup oluştur": sınıfın şu anki öğrencileri kopyalanır."""

    className: str = Field(min_length=1, max_length=20)
    name: str | None = Field(default=None, max_length=80)
    callName: str = Field(default="", max_length=120)
    validFrom: str | None = Field(default=None, max_length=10)
    reason: str = Field(default="", max_length=200)


class GroupUpdate(BaseModel):
    """Gönderilmeyen alan değişmez."""

    name: str | None = Field(default=None, max_length=80)
    kind: str | None = Field(default=None, pattern=r"^(group|individual)$")
    callName: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=500)


class MembersAdd(BaseModel):
    studentIds: list[str] = Field(min_length=1, max_length=2000)
    validFrom: str | None = Field(default=None, max_length=10)
    reason: str = Field(default="", max_length=200)


class MembersEnd(BaseModel):
    studentIds: list[str] = Field(min_length=1, max_length=2000)
    validTo: str | None = Field(default=None, max_length=10)
    reason: str = Field(default="", max_length=200)


class MembersMove(BaseModel):
    studentIds: list[str] = Field(min_length=1, max_length=2000)
    fromGroupId: str = Field(min_length=1, max_length=64)
    toGroupId: str = Field(min_length=1, max_length=64)
    effectiveFrom: str | None = Field(default=None, max_length=10)
    reason: str = Field(default="", max_length=200)


class MemberFix(BaseModel):
    """Geçmiş tarihli düzeltme — gerekçe servis tarafında zorunlu tutulur."""

    validFrom: str | None = Field(default=None, max_length=10)
    validTo: str | None = Field(default=None, max_length=10)
    reason: str = Field(default="", max_length=200)


@router.get("/groups")
async def list_groups(
    archived: str = Query(default="live", pattern=r"^(live|archived|all)$"),
    kind: str = Query(default="", pattern=r"^(|group|individual)$"),
    q: str = Query(default="", max_length=80),
    on: str | None = Query(default=None, alias="date", max_length=10),
    user: CurrentUser = requires("bbd_students.view"),
) -> dict[str, Any]:
    """Gruplar sekmesi: liste + o günün (vars. bugün) üye sayısı."""
    return await groups().overview(archived=archived, kind=kind, query=q,
                                   on=_optional_day(on, "date"))


@router.post("/groups")
async def create_group(
    body: GroupCreate,
    user: CurrentUser = requires("bbd_students.groups"),
) -> dict[str, Any]:
    try:
        return await groups().create(body.model_dump(), actor=user.full_name)
    except GroupError as exc:
        raise _group_error(exc) from exc


@router.post("/groups/from-class")
async def create_group_from_class(
    body: GroupFromClass,
    user: CurrentUser = requires("bbd_students.groups"),
) -> dict[str, Any]:
    payload = body.model_dump(exclude_none=True)
    class_name = payload.pop("className")
    try:
        return await groups().from_class(class_name, payload, actor=user.full_name)
    except GroupError as exc:
        raise _group_error(exc) from exc


@router.post("/groups/move")
async def move_members(
    body: MembersMove,
    user: CurrentUser = requires("bbd_students.groups"),
) -> dict[str, Any]:
    """A'da son gün = d-1, B'de başlangıç = d; ikisi birlikte doğrulanıp yazılır."""
    try:
        return await groups().move(body.studentIds, from_group=body.fromGroupId,
                                   to_group=body.toGroupId,
                                   effective_from=body.effectiveFrom, reason=body.reason,
                                   actor=user.full_name)
    except GroupError as exc:
        raise _group_error(exc) from exc


@router.get("/groups/memberships")
async def group_memberships(
    on: str | None = Query(default=None, alias="date", max_length=10),
    user: CurrentUser = requires("bbd_students.view"),
) -> dict[str, Any]:
    """O gün geçerli bütün üyelikler — öğrenci seçicileri grup süzgeci için."""
    return await groups().memberships(on=_optional_day(on, "date"))


@router.get("/groups/{group_id}")
async def group_detail(
    group_id: str,
    on: str | None = Query(default=None, alias="date", max_length=10),
    user: CurrentUser = requires("bbd_students.view"),
) -> dict[str, Any]:
    try:
        return await groups().detail(group_id, on=_optional_day(on, "date"))
    except GroupError as exc:
        raise _group_error(exc) from exc


@router.get("/groups/{group_id}/members")
async def group_members(
    group_id: str,
    on: str | None = Query(default=None, alias="date", max_length=10),
    user: CurrentUser = requires("bbd_students.view"),
) -> dict[str, Any]:
    """`date` verilirse o gün geçerli üyeler (`current`); `members` hep tam geçmiş."""
    try:
        record = await groups().detail(group_id, on=_optional_day(on, "date"))
    except GroupError as exc:
        raise _group_error(exc) from exc
    return {"groupId": record["id"], "date": record["date"], "current": record["current"],
            "members": record["members"]}


@router.get("/groups/{group_id}/history")
async def group_history(
    group_id: str,
    user: CurrentUser = requires("bbd_students.view"),
) -> dict[str, Any]:
    """Denetim izi: kim, ne zaman, ne değiştirdi (gerekçesiyle)."""
    try:
        return {"items": await groups().history(group_id)}
    except GroupError as exc:
        raise _group_error(exc) from exc


@router.put("/groups/{group_id}")
async def update_group(
    group_id: str,
    body: GroupUpdate,
    user: CurrentUser = requires("bbd_students.groups"),
) -> dict[str, Any]:
    try:
        return await groups().update(group_id, body.model_dump(exclude_unset=True),
                                     actor=user.full_name)
    except GroupError as exc:
        raise _group_error(exc) from exc


@router.post("/groups/{group_id}/archive")
async def archive_group(
    group_id: str,
    user: CurrentUser = requires("bbd_students.groups"),
) -> dict[str, Any]:
    """Silme yoktur, arşiv vardır: açık üyelikler bugün kapanır, geçmiş korunur."""
    try:
        return await groups().archive(group_id, actor=user.full_name)
    except GroupError as exc:
        raise _group_error(exc) from exc


@router.post("/groups/{group_id}/restore")
async def restore_group(
    group_id: str,
    user: CurrentUser = requires("bbd_students.groups"),
) -> dict[str, Any]:
    try:
        return await groups().restore(group_id, actor=user.full_name)
    except GroupError as exc:
        raise _group_error(exc) from exc


@router.post("/groups/{group_id}/members")
async def add_members(
    group_id: str,
    body: MembersAdd,
    user: CurrentUser = requires("bbd_students.groups"),
) -> dict[str, Any]:
    try:
        return await groups().add_members(group_id, body.studentIds,
                                          valid_from=body.validFrom, reason=body.reason,
                                          actor=user.full_name)
    except GroupError as exc:
        raise _group_error(exc) from exc


@router.post("/groups/{group_id}/members/end")
async def end_members(
    group_id: str,
    body: MembersEnd,
    user: CurrentUser = requires("bbd_students.groups"),
) -> dict[str, Any]:
    """Çıkarma: `validTo` (vars. bugün) son gündür ve dahildir."""
    try:
        return await groups().end_members(group_id, body.studentIds, valid_to=body.validTo,
                                          reason=body.reason, actor=user.full_name)
    except GroupError as exc:
        raise _group_error(exc) from exc


@router.patch("/groups/{group_id}/members/{member_id}")
async def fix_member(
    group_id: str,
    member_id: str,
    body: MemberFix,
    user: CurrentUser = requires("bbd_students.groups"),
) -> dict[str, Any]:
    """Yanlış girilmiş başlangıç/son gün. `validTo: ""` açık uca çevirir."""
    try:
        return await groups().patch_member(group_id, member_id,
                                           body.model_dump(exclude_unset=True),
                                           actor=user.full_name)
    except GroupError as exc:
        raise _group_error(exc) from exc


@router.get("/students/{kantin_id}/groups")
async def student_groups(
    kantin_id: str,
    user: CurrentUser = requires("bbd_students.view"),
) -> dict[str, Any]:
    """Öğrenci kartındaki "Gruplar" bölümü — geçmiş dahil bütün üyelikler."""
    return {"studentId": kantin_id, "items": await groups().for_student(kantin_id)}
