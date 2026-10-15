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

from fastapi import Request
from fastapi.responses import JSONResponse

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

from .. import qr as qr_codec
from ..academic_year import YearRollover
from ..classes import ClassLink
from ..classes_merge import ClassMerge
from ..groups import GroupError, GroupService, parse_day
from ..lifecycle import Lifecycle
from ..service import ControlsError, StudentService
from ..sync import StudentSync, SyncError

router = APIRouter()

_service: StudentService | None = None
_groups: GroupService | None = None
_lifecycle: Lifecycle | None = None
_sync: StudentSync | None = None
_merge: ClassMerge | None = None
_link: ClassLink | None = None
_rollover: YearRollover | None = None


def bind(service: StudentService, groups: GroupService | None = None,
         lifecycle: Lifecycle | None = None, sync: StudentSync | None = None, *,
         merge: ClassMerge | None = None, link: ClassLink | None = None) -> APIRouter:
    global _service, _groups, _lifecycle, _sync, _merge, _link, _rollover
    _service = service
    _groups = groups
    _lifecycle = lifecycle
    _sync = sync
    _merge = merge
    _link = link
    _rollover = None
    if groups is not None:
        service.attach_groups(groups)
        # Dönem geçişi (iş 37 / İ4): sınıf servisi + seviye yazması (önce KM) + mezunların
        # pasifleşmesi. Kurallar `academic_year.YearRollover`'da; yeni bağımlılık yok.
        _rollover = YearRollover(store=groups._store, groups=groups, log=groups._log,
                                 sync=sync, lifecycle=lifecycle)
    return router


def rollover() -> YearRollover:
    if _rollover is None:  # pragma: no cover - yükleme sırası garanti eder
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _rollover


def merge() -> ClassMerge:
    if _merge is None:  # pragma: no cover - yükleme sırası garanti eder
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _merge


def lifecycle() -> Lifecycle:
    if _lifecycle is None:  # pragma: no cover - yükleme sırası garanti eder
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _lifecycle


def sync_error(exc: SyncError) -> JSONResponse:
    """Yaşam döngüsü reddi — ortak zarf + makine kodu ve ayrıntı (ekran tutarı gösterir)."""
    return JSONResponse(status_code=exc.status, content={"error": {
        "status": exc.status, "message": str(exc), "code": exc.code, "details": exc.details}})


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
    # Sınıf seviyeyi taşır (göç 008): açıkken öğrencinin kendi alan / hedef sınavı
    # sınıfınkinin yerine geçer (seviye her zaman sınıftan).
    levelOverride: bool | None = None


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


@router.get("/search")
async def search_students(
    q: str = Query(default="", max_length=400),
    limit: int = Query(default=8, ge=1, le=25),
    user: CurrentUser = requires("bbd_students.view"),
) -> dict[str, Any]:
    """Küresel arama (kabuğun üst çubuğu, `/`): ad, okul no, veli/öğrenci
    telefonu (son hanelerle de), kantin kartı (kimlik ya da QR metni). Dizinin
    anlık görüntüsünden — kantine gitmez. Telefon maskeli döner."""
    return await service().search(q, limit)


@router.post("/students")
async def create_student(
    body: StudentCreate,
    user: CurrentUser = requires("bbd_students.manage"),
) -> dict[str, Any]:
    payload = body.model_dump(exclude_unset=True)
    try:
        return await service().create_student(payload, actor=user.full_name)
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
        return await service().update_student(kantin_id, payload, actor=user.full_name)
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


# ------------------------------------------- tek öğrenci, veliler, toplu sınıf


@router.get("/students/{kantin_id}")
async def get_student(
    kantin_id: str,
    user: CurrentUser = requires("bbd_students.view"),
) -> dict[str, Any]:
    """Tek öğrencinin birleşik kaydı — BBD Akademi öğrenci kartındaki "Kayıt" bölümü."""
    try:
        student = await service().get_student(kantin_id)
    except Exception as exc:  # kantin dışarısı
        raise HTTPException(status_code=503, detail=f"Kantine ulaşılamadı: {exc}") from exc
    if student is None:
        raise HTTPException(status_code=404, detail="Öğrenci bulunamadı.")
    return student


class GuardianItem(BaseModel):
    name: str = Field(default="", max_length=80)
    phone: str = Field(default="", max_length=32)
    relation: str = Field(default="", max_length=20)


class GuardiansBody(BaseModel):
    """Velilerin TAM listesi; sıra önceliktir, ilk veli birincildir (yuva 1)."""

    items: list[GuardianItem] = Field(default_factory=list, max_length=10)


@router.get("/students/{kantin_id}/guardians")
async def student_guardians(
    kantin_id: str,
    user: CurrentUser = requires("bbd_students.view"),
) -> dict[str, Any]:
    try:
        return await service().guardians(kantin_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/students/{kantin_id}/guardians")
async def set_student_guardians(
    kantin_id: str,
    body: GuardiansBody,
    user: CurrentUser = requires("bbd_students.manage"),
) -> Any:
    """Veli ekle / düzenle / sil / birincil yap — tek uçta, tam liste.

    Telefon `+90 5xx xxx xx xx` (cep) olmalı; aynı öğrencide aynı telefon iki kez
    olamaz; aynı telefon başka öğrencinin velisi olabilir. Yuva 1 kantine yazılır
    (önce KM, sonra kantin), değişiklik olay olarak kantine ve Akademi'ye gider.
    """
    from ..guardians import GuardianError

    try:
        return await service().set_guardians(kantin_id, [item.model_dump() for item in body.items],
                                             actor=user.full_name)
    except GuardianError as exc:
        return JSONResponse(status_code=422, content={"error": {
            "status": 422, "code": "validation_error", "message": str(exc),
            "details": [{"index": exc.index, "field": exc.field, "message": str(exc)}]}})
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class ClassBulkBody(BaseModel):
    """Seçili öğrencilere ANA SINIF. Boş ad sınıfı temizler; boş seçim reddedilir."""

    students: list[str] = Field(min_length=1, max_length=5000)
    className: str = Field(default="", max_length=20)


@router.post("/students/class")
async def set_class(
    body: ClassBulkBody,
    user: CurrentUser = requires("bbd_students.groups"),
) -> dict[str, Any]:
    """Toplu "sınıfa ekle" (çoklu seçim → sınıf). Sınıf = grup (göç 009): üyelik
    yazdığı için `bbd_students.groups` ister; sınıf yoksa açılır."""
    try:
        return await service().set_class(body.students, body.className, actor=user.full_name)
    except GroupError as exc:
        raise _group_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ------------------------------------------------- sınıflar (sınıf seviyeyi taşır)


class ClassLevelBody(BaseModel):
    """Sınıfın seviyesi (zorunlu), alanı (10+ / mezun) ve hedef sınavı. Gönderilmeyen alan
    mevcut değerini korur."""

    gradeLevel: int | str | None = None
    track: str | None = Field(default=None, max_length=8)
    examTarget: str | None = Field(default=None, max_length=8)


@router.get("/classes")
async def list_classes(user: CurrentUser = requires("bbd_students.view")) -> dict[str, Any]:
    """Sınıflar: tanımlı sınıflar ∪ öğrencilerin sınıf adları; seviyesiz olanlar
    `defined: false` + addan öneri ("seviye seçin")."""
    return await service().list_classes()


@router.put("/classes/{class_name}")
async def set_class_level(
    class_name: str,
    body: ClassLevelBody,
    user: CurrentUser = requires("bbd_students.manage"),
) -> dict[str, Any]:
    """Sınıfın seviyesi; sınıftaki öğrencilerin geçerli değeri değişirse öğrenci başına
    `student.updated` (Akademi müfredatı kendiliğinden yeniden çözer)."""
    try:
        return await service().set_class_level(class_name, body.model_dump(exclude_unset=True),
                                               actor=user.full_name)
    except GroupError as exc:
        raise _group_error(exc) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/classes/{class_name}")
async def clear_class_level(
    class_name: str,
    user: CurrentUser = requires("bbd_students.manage"),
) -> dict[str, Any]:
    try:
        return await service().clear_class_level(class_name)
    except GroupError as exc:
        raise _group_error(exc) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ------------------------------------------------- sınıf = grup birleştirmesi (göç 009)


class MergeRun(BaseModel):
    """`force`: tamamlanmış dönüşümde EKSİK sınıf / üyelikleri yeniden tarar."""

    force: bool = False


class MergeRollback(BaseModel):
    """Geri alma yıkıcıdır (dönüşümün açtığı sınıflar ve üyelikleri silinir): "GERİ AL"
    yazılarak + PIN."""

    confirm: str = Field(min_length=1, max_length=16)
    pin: str | None = Field(default=None, max_length=12)


@router.get("/classes/merge")
async def merge_status(user: CurrentUser = requires("bbd_students.view")) -> dict[str, Any]:
    """Sınıf birleştirmesinin durumu: `{state: ''|done|rolled_back, version, summary}`."""
    return await merge().status()


@router.post("/classes/merge")
async def merge_run(
    body: MergeRun,
    user: CurrentUser = requires("bbd_students.groups"),
) -> dict[str, Any]:
    """Birleştirmeyi elle koşturur (açılışta zaten bir kez koşar; idempotent)."""
    return await merge().run(actor=user.full_name, force=body.force)


@router.post("/classes/merge/rollback")
async def merge_rollback(
    request: Request,
    body: MergeRollback,
    user: CurrentUser = requires("bbd_students.delete"),
) -> Any:
    """Birleştirmeyi geri alır (yalnız yayından hemen sonra; admin + PIN)."""
    if body.confirm.strip().upper() != "GERİ AL":
        raise HTTPException(status_code=400, detail="Onay için “GERİ AL” yazın.")
    if not body.pin:
        return pin_required()
    await confirm_pin(request, user, body.pin, action="bbd_students.classes_merge:rollback")
    result = await merge().rollback(actor=user.full_name)
    if _link is not None:
        await _link.reconcile_all(force_day=True)
    return result


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


# ------------------------------------------------------- yaşam döngüsü (§5)


class DeactivateBody(BaseModel):
    """Pasifleştir. `effectiveAt` boş = şimdi; ileri gün = o gün 00:00'da."""

    reason: str = Field(max_length=16)
    effectiveAt: str | None = Field(default=None, max_length=32)
    note: str | None = Field(default=None, max_length=500)


class BulkDeactivateBody(DeactivateBody):
    students: list[str] = Field(min_length=1, max_length=2000)


class ReactivateBody(BaseModel):
    """Aktifleştir. `restoreClasses` (varsayılan açık): pasifleştirmenin kapattığı sınıf
    üyelikleri bugünden geri açılır. Gövdesiz eski çağrı da aynı varsayılanla çalışır."""

    restoreClasses: bool = True


class BulkReactivateBody(ReactivateBody):
    students: list[str] = Field(min_length=1, max_length=2000)


class DeleteBody(BaseModel):
    """Kalıcı silme onayı: öğrencinin adı aynen yazılır + kendi PIN'i (yıkıcı işlem).

    `pin` 0.5.4'te zorunlu oldu: 0.5.3 kabuğu PIN göndermez → `422 pin_required`
    ("masaüstünü güncelleyin"); silme yapılmaz (güvenli taraf)."""

    confirmName: str = Field(min_length=1, max_length=160)
    pin: str | None = Field(default=None, max_length=12)


class BulkDeleteBody(BaseModel):
    """Toplu kalıcı silme: öğrenciler + seçilen SAYI aynen yazılarak onay + PIN."""

    students: list[str] = Field(min_length=1, max_length=50)
    confirmCount: str = Field(min_length=1, max_length=6)
    pin: str | None = Field(default=None, max_length=12)


def pin_required() -> JSONResponse:
    return JSONResponse(status_code=422, content={"error": {
        "status": 422, "code": "pin_required", "details": {"field": "pin"},
        "message": "Kalıcı silme PIN teyidi ister. Masaüstü uygulamasını güncelleyin (0.5.4)."}})


@router.post("/students/deactivate")
async def bulk_deactivate(
    body: BulkDeactivateBody,
    user: CurrentUser = requires("bbd_students.lifecycle"),
) -> Any:
    """Çoklu seçimle pasifleştirme (yıl sonu mezunlar). Her öğrenci ayrı olay;
    kantin çağrıları arka planda sıralı."""
    try:
        return await lifecycle().bulk_deactivate(
            body.students, reason=body.reason, effective_at=body.effectiveAt,
            note=body.note or "", actor=user.full_name)
    except SyncError as exc:
        return sync_error(exc)


@router.post("/students/reactivate")
async def bulk_reactivate(
    body: BulkReactivateBody,
    user: CurrentUser = requires("bbd_students.lifecycle"),
) -> Any:
    """Çoklu seçimle aktifleştirme (pasif listesinden). Her öğrenci ayrı olay ve durum
    geçmişi satırı; kantin ve tablet çağrıları arka planda sıralı."""
    try:
        return await lifecycle().bulk_reactivate(
            body.students, actor=user.full_name, restore_classes=body.restoreClasses)
    except SyncError as exc:
        return sync_error(exc)


@router.post("/students/{kantin_id}/deactivate")
async def deactivate_student(
    kantin_id: str,
    body: DeactivateBody,
    user: CurrentUser = requires("bbd_students.lifecycle"),
) -> Any:
    try:
        return await lifecycle().deactivate(
            kantin_id, reason=body.reason, effective_at=body.effectiveAt,
            note=body.note or "", actor=user.full_name)
    except SyncError as exc:
        return sync_error(exc)


@router.post("/students/{kantin_id}/reactivate")
async def reactivate_student(
    kantin_id: str,
    body: ReactivateBody | None = None,
    user: CurrentUser = requires("bbd_students.lifecycle"),
) -> Any:
    """Aktifleştir (ya da planlı pasifleştirmeyi iptal et). Pasifleştirmenin kapattığı
    sınıf üyelikleri varsayılan olarak bugünden geri açılır (`restoreClasses`)."""
    restore = True if body is None else body.restoreClasses
    try:
        return await lifecycle().reactivate(kantin_id, actor=user.full_name,
                                            restore_classes=restore)
    except SyncError as exc:
        return sync_error(exc)


@router.get("/students/{kantin_id}/delete-check")
async def delete_check(
    kantin_id: str,
    user: CurrentUser = requires("bbd_students.delete"),
) -> Any:
    """Kalıcı silme ön kontrolü (yazma yok): `{deletable, blockers[], notes[],
    willDelete[]}` — engeller tutarıyla, açık Türkçe."""
    try:
        return await lifecycle().delete_check(kantin_id)
    except SyncError as exc:
        return sync_error(exc)


@router.post("/students/delete")
async def bulk_delete_students(
    request: Request,
    body: BulkDeleteBody,
    user: CurrentUser = requires("bbd_students.delete"),
) -> Any:
    """Toplu kalıcı silme (en çok 50): her öğrenci ayrı denetlenir, ayrı sonuç
    (silindi / engel). PIN teyidi + seçilen sayının yazılması."""
    if not body.pin:
        return pin_required()
    await confirm_pin(request, user, body.pin, action="bbd_students.delete:bulk")
    try:
        return await lifecycle().bulk_delete(body.students, confirm_count=body.confirmCount,
                                             actor=user.full_name)
    except SyncError as exc:
        return sync_error(exc)


@router.delete("/students/{kantin_id}")
async def delete_student(
    request: Request,
    kantin_id: str,
    body: DeleteBody,
    user: CurrentUser = requires("bbd_students.delete"),
) -> Any:
    """Kalıcı sil — yalnız pasif öğrenci, bakiye 0, açık talep yok, kurum cari
    borcu yok; ad yazılarak + PIN. 409 `balance_not_zero` / `open_payment_request` /
    `institution_debt`, 503 `akademi_unavailable` / `canteen_unavailable`,
    422 `pin_required`, 403 PIN yanlış."""
    if not body.pin:
        return pin_required()
    await confirm_pin(request, user, body.pin, action="bbd_students.delete")
    try:
        return await lifecycle().delete(kantin_id, confirm_name=body.confirmName,
                                        actor=user.full_name)
    except SyncError as exc:
        return sync_error(exc)


@router.get("/students/{kantin_id}/status-history")
async def status_history(
    kantin_id: str,
    user: CurrentUser = requires("bbd_students.view"),
) -> dict[str, Any]:
    if _sync is None:  # pragma: no cover
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return {"items": await _sync.history(kantin_id)}


@router.get("/sync")
async def sync_status(user: CurrentUser = requires("bbd_students.view")) -> dict[str, Any]:
    """Kantine iletilemeyen kayıtlar (24 saati geçenler kırmızı) ve bekleyen silmeler."""
    if _sync is None:  # pragma: no cover
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return await _sync.sync_status()


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


class LevelBody(BaseModel):
    """Sınıfın seviyesi (şablon): seviye zorunlu, alan 10+ / mezun, hedef sınav."""

    gradeLevel: int | str | None = None
    track: str | None = Field(default=None, max_length=8)
    examTarget: str | None = Field(default=None, max_length=8)


class GroupCreate(BaseModel):
    """Sınıf = ad + öğrenci seçimi. Öğrenciler `validFrom`'dan (vars. bugün) üye olur.
    `level` verilirse `applyLevel` (vars. açık) üyelerin seviyesine yazar."""

    name: str = Field(min_length=1, max_length=80)
    kind: str = Field(default="group", pattern=r"^(group|individual)$")
    callName: str = Field(default="", max_length=120)
    note: str = Field(default="", max_length=500)
    studentIds: list[str] = Field(default_factory=list, max_length=2000)
    validFrom: str | None = Field(default=None, max_length=10)
    reason: str = Field(default="", max_length=200)
    level: LevelBody | None = None
    applyLevel: bool = True
    #: Dönem ("2026-2027"); verilmezse bugünün dönemi (1 Eylül'de döner).
    academicYear: str | None = Field(default=None, pattern=r"^\d{4}-\d{4}$")


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
    #: `null` seviyeyi (şablonu) kaldırır; öğrencilerin kendi seviyesi değişmez.
    level: LevelBody | None = None
    #: Seviye değişince bugünkü üyelere yaz (vars. açık).
    applyLevel: bool = True
    #: Dönem ("2026-2027"); boş bırakılamaz.
    academicYear: str | None = Field(default=None, pattern=r"^\d{4}-\d{4}$")


class GroupsYear(BaseModel):
    """Toplu "dönem ata": seçili sınıflara aynı dönem (tek işlem)."""

    groupIds: list[str] = Field(min_length=1, max_length=1000)
    academicYear: str = Field(pattern=r"^\d{4}-\d{4}$")


class GroupDelete(BaseModel):
    """Arşivdeki sınıfı KALICI silme onayı: sınıfın adı aynen + gerekçe + kendi PIN'i."""

    confirmName: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=200)
    pin: str | None = Field(default=None, max_length=12)


class RolloverItem(BaseModel):
    """Dönem geçişinde bir sınıfın işlemi; `name` yalnız taşı/kopyala için yeni ad."""

    groupId: str = Field(min_length=1, max_length=64)
    action: str = Field(pattern=r"^(promote|copy|graduate|archive|keep)$")
    name: str | None = Field(default=None, max_length=80)


class RolloverPlan(BaseModel):
    """Dönem geçişi önizlemesi/uygulaması. Gönderilmeyen sınıf varsayılan işlemini alır."""

    fromYear: str | None = Field(default=None, pattern=r"^\d{4}-\d{4}$")
    toYear: str | None = Field(default=None, pattern=r"^\d{4}-\d{4}$")
    effectiveFrom: str | None = Field(default=None, max_length=10)
    items: list[RolloverItem] = Field(default_factory=list, max_length=1000)
    keepStudentIds: list[str] = Field(default_factory=list, max_length=5000)
    bumpOthers: bool = True
    expectedRevision: int | None = None


class RolloverApply(RolloverPlan):
    pin: str | None = Field(default=None, max_length=12)


class RolloverUndo(BaseModel):
    pin: str | None = Field(default=None, max_length=12)


class MembersAdd(BaseModel):
    studentIds: list[str] = Field(min_length=1, max_length=2000)
    validFrom: str | None = Field(default=None, max_length=10)
    reason: str = Field(default="", max_length=200)
    #: Sınıfın seviyesi varsa eklenenlere yaz (vars. açık).
    applyLevel: bool = True


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
    applyLevel: bool = True


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
    year: str = Query(default="", pattern=r"^(|none|\d{4}-\d{4})$", max_length=9),
    user: CurrentUser = requires("bbd_students.view"),
) -> dict[str, Any]:
    """Sınıflar ekranı: liste + o günün (vars. bugün) üye sayısı. `year` dönem süzgeci
    ('' tümü, `none` dönemsiz, `2026-2027`)."""
    return await groups().overview(archived=archived, kind=kind, query=q,
                                   on=_optional_day(on, "date"), year=year)


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


@router.post("/groups/academic-year")
async def set_groups_year(
    body: GroupsYear,
    user: CurrentUser = requires("bbd_students.groups"),
) -> dict[str, Any]:
    """Toplu "dönem ata": seçili sınıflar (arşivliler dahil) aynı döneme — tek işlem."""
    try:
        return await groups().set_year(body.groupIds, body.academicYear, actor=user.full_name)
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
                                   actor=user.full_name, apply_level=body.applyLevel)
    except GroupError as exc:
        raise _group_error(exc) from exc


# ---------------------------------------------------------------- dönem geçişi
#
# Yıl sonu sihirbazı (iş 37 / İ4). `/groups/{group_id}` uçlarından ÖNCE ilan edilir
# (yol eşleşmesi sırayla). Uygulama ve geri alma yıkıcıdır: PIN teyidi; mezun
# pasifleştiren plan ayrıca `bbd_students.lifecycle` ister (K9).


def _rollover_error(exc: GroupError) -> JSONResponse:
    """Dönem geçişi reddi — ortak zarf + makine kodu (ekran sorun listesini gösterir)."""
    return JSONResponse(status_code=exc.status, content={"error": {
        "status": exc.status, "message": str(exc), "code": exc.code, "details": exc.details}})


def _rollover_pin() -> JSONResponse:
    return JSONResponse(status_code=422, content={"error": {
        "status": 422, "code": "pin_required", "details": {"field": "pin"},
        "message": "Dönem geçişi PIN teyidi ister."}})


def _needs_lifecycle(user: CurrentUser) -> JSONResponse | None:
    if user.has_permission("bbd_students.lifecycle"):
        return None
    return JSONResponse(status_code=403, content={"error": {
        "status": 403, "code": "forbidden", "details": {"permission": "bbd_students.lifecycle"},
        "message": "Mezunları pasifleştirmek için öğrenci pasifleştirme yetkisi gerekir; "
                   "o sınıfları “Yalnız arşivle”ye alın."}})


@router.get("/groups/rollover")
async def rollover_overview(
    user: CurrentUser = requires("bbd_students.view"),
) -> dict[str, Any]:
    """Sihirbazın açılışı: kaynak/hedef dönem ve geçiş günü önerisi, pencere, dönemler,
    son geçiş (geri alınabilir mi) ve geçmiş. Akademi "Dönem geçişi kopyası" eşlemeyi
    (`latest.pairs`) buradan okur."""
    return await rollover().overview()


@router.post("/groups/rollover/preview")
async def rollover_preview(
    body: RolloverPlan,
    user: CurrentUser = requires("bbd_students.groups"),
) -> Any:
    """Yazmadan plan: sınıf işlemleri, yeni ad/seviye, taşınan, mezun, seviyesi değişen,
    sorunlar ve uygulamayı engelleyen sebep (`blocked`)."""
    try:
        return await rollover().preview(body.model_dump())
    except GroupError as exc:
        return _rollover_error(exc)


@router.post("/groups/rollover")
async def rollover_apply(
    request: Request,
    body: RolloverApply,
    user: CurrentUser = requires("bbd_students.groups"),
) -> Any:
    """Planı uygular (PIN). 409 `rollover_blocked` (sorun/pencere), `rollover_stale`
    (önizlemeden sonra sınıflar değişti), 403 mezun var ama pasifleştirme yetkisi yok."""
    if not body.pin:
        return _rollover_pin()
    payload = body.model_dump(exclude={"pin"})
    try:
        plan = await rollover().preview(payload)
    except GroupError as exc:
        return _rollover_error(exc)
    if plan["graduates"] and (denied := _needs_lifecycle(user)) is not None:
        return denied
    await confirm_pin(request, user, body.pin, action="bbd_students.year_rollover")
    try:
        return await rollover().apply(payload, actor=user.full_name)
    except GroupError as exc:
        return _rollover_error(exc)


@router.get("/groups/rollover/{rollover_id}")
async def rollover_detail(
    rollover_id: str,
    user: CurrentUser = requires("bbd_students.view"),
) -> Any:
    try:
        return await rollover().get(rollover_id)
    except GroupError as exc:
        return _rollover_error(exc)


@router.post("/groups/rollover/{rollover_id}/undo")
async def rollover_undo(
    request: Request,
    rollover_id: str,
    body: RolloverUndo,
    user: CurrentUser = requires("bbd_students.groups"),
) -> Any:
    """SON geçişi geri alır (PIN). Geçişin açtığı sınıflar kalıcı silinir, eski sınıflar ve
    üyelikler döner, seviyeler geri yazılır, geçişin pasifleştirdiği mezunlar etkinleşir."""
    if not body.pin:
        return _rollover_pin()
    try:
        record = await rollover().get(rollover_id)
    except GroupError as exc:
        return _rollover_error(exc)
    graduation = record.get("graduation") or {}
    if (graduation.get("deactivated") or graduation.get("scheduled")) \
            and (denied := _needs_lifecycle(user)) is not None:
        return denied
    await confirm_pin(request, user, body.pin, action="bbd_students.year_rollover_undo")
    try:
        return await rollover().undo(rollover_id, actor=user.full_name)
    except GroupError as exc:
        return _rollover_error(exc)


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


@router.post("/groups/{group_id}/apply-level")
async def apply_group_level(
    group_id: str,
    user: CurrentUser = requires("bbd_students.groups"),
) -> dict[str, Any]:
    """"Seviyeyi üyelere uygula": sınıfın seviyesini bugünkü üyelerin kendi seviyesine
    yazar (değişen öğrenci başına `student.updated`)."""
    try:
        return await groups().apply_level_now(group_id, actor=user.full_name)
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


@router.delete("/groups/{group_id}")
async def delete_group(
    request: Request,
    group_id: str,
    body: GroupDelete,
    user: CurrentUser = requires("bbd_students.groups_delete"),
) -> Any:
    """ARŞİVDEKİ sınıfı KALICI siler — geri alınamaz. Sınıfın adı aynen + gerekçe +
    PIN (yıkıcı işlem). Üyelikler silinir, kimlik mezar taşında kalır (Akademi'ye
    `deleted: true`), denetim izi kalır. 409 `group_not_archived`, 422
    `confirm_mismatch` / `reason_required` / `pin_required`, 403 PIN yanlış."""
    if not body.pin:
        return pin_required()
    await confirm_pin(request, user, body.pin, action="bbd_students.groups_delete")
    try:
        return await groups().delete(group_id, confirm_name=body.confirmName,
                                     reason=body.reason, actor=user.full_name)
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
                                          actor=user.full_name, apply_level=body.applyLevel)
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
