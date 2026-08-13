"""Deneme Kulübü — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde düğmeyi gizlemek yetkilendirme
değildir. Yıkıcı uçlarda gerekçe `min_length=10` ile ŞEMADA doğrulanır, ayrıca
serviste tekrar denetlenir — istemci şemayı atlatabilir.

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran
mesajı gösterir. 4xx yalnız izin/şema kapısından çıkar.
"""

from __future__ import annotations

from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..service import TrialClubService

router = APIRouter()
_service: TrialClubService | None = None


def bind(service: TrialClubService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> TrialClubService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


# ================================================================== okuma

@router.get("/overview")
async def overview(
    user: CurrentUser = requires("store_trial_club.view"),
) -> dict[str, Any]:
    return await service().overview()


@router.get("/exams")
async def exams(
    q: str = Query("", max_length=120),
    status: str = Query("", max_length=24),
    results: str = Query("", max_length=24),
    user: CurrentUser = requires("store_trial_club.view"),
) -> dict[str, Any]:
    return await service().exams(q=q, status=status, results=results)


@router.get("/exams/{exam_id}")
async def exam(
    exam_id: int,
    user: CurrentUser = requires("store_trial_club.view"),
) -> dict[str, Any]:
    return await service().exam(exam_id)


@router.get("/members")
async def members(
    q: str = Query("", max_length=120),
    examId: int = Query(0, ge=0),
    payment: str = Query("", max_length=16),
    attendance: str = Query("", max_length=16),
    city: str = Query("", max_length=64),
    grade: str = Query("", max_length=32),
    page: int = Query(1, ge=1, le=10_000),
    size: int = Query(0, ge=0, le=200),
    user: CurrentUser = requires("store_trial_club.view"),
) -> dict[str, Any]:
    return await service().members(q=q, exam_id=examId, payment=payment,
                                   attendance=attendance, city=city, grade=grade,
                                   page=page, size=size)


@router.get("/exams/{exam_id}/results")
async def results(
    exam_id: int,
    page: int = Query(1, ge=1, le=10_000),
    size: int = Query(0, ge=0, le=200),
    user: CurrentUser = requires("store_trial_club.view"),
) -> dict[str, Any]:
    return await service().results(exam_id, page=page, size=size)


@router.get("/audit")
async def audit(
    examId: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    user: CurrentUser = requires("store_trial_club.view"),
) -> dict[str, Any]:
    return await service().audit(exam_id=examId, limit=limit)


# ================================================================== deneme

class ExamBody(BaseModel):
    name: str = Field(min_length=2, max_length=180)
    examDate: str = Field(min_length=10, max_length=10)
    examTime: str = Field(default="", max_length=5)
    capacity: int = Field(default=0, ge=0, le=100_000)
    venue: str = Field(default="", max_length=180)
    grade: str = Field(default="", max_length=64)
    registrationStart: str = Field(default="", max_length=10)
    registrationEnd: str = Field(default="", max_length=10)
    feeKurus: int | None = Field(default=None, ge=0)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/exams")
async def create_exam(
    body: ExamBody,
    user: CurrentUser = requires("store_trial_club.manage"),
) -> dict[str, Any]:
    return await service().save_exam(None, payload=body.model_dump(), reason=body.reason,
                                     actor=user.full_name, dry_run=body.dryRun)


@router.put("/exams/{exam_id}")
async def update_exam(
    exam_id: int,
    body: ExamBody,
    user: CurrentUser = requires("store_trial_club.manage"),
) -> dict[str, Any]:
    return await service().save_exam(exam_id, payload=body.model_dump(), reason=body.reason,
                                     actor=user.full_name, dry_run=body.dryRun)


class CapacityBody(BaseModel):
    capacity: int = Field(ge=0, le=100_000)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/exams/{exam_id}/capacity")
async def set_capacity(
    exam_id: int,
    body: CapacityBody,
    user: CurrentUser = requires("store_trial_club.manage"),
) -> dict[str, Any]:
    """Kontenjan kayıtlı sayısının ALTINA çekilemez — kural serviste de var."""
    return await service().set_capacity(exam_id, capacity=body.capacity, reason=body.reason,
                                        actor=user.full_name, dry_run=body.dryRun)


# ============================================================== katılımcı

class MemberBody(BaseModel):
    reason: str = Field(min_length=10, max_length=255)


@router.post("/exams/{exam_id}/members")
async def add_member(
    exam_id: int,
    body: MemberBody,
    user: CurrentUser = requires("store_trial_club.enroll"),
) -> dict[str, Any]:
    """Mağaza ucu HENÜZ YOK; uç `{ok: false, pending: true}` döner ve ekran
    düğmeyi kapalı gösterir. Sessiz 404 yerine anlaşılır cevap."""
    return await service().add_member(exam_id, reason=body.reason, actor=user.full_name)


@router.post("/members/{member_id}/remove")
async def remove_member(
    member_id: int,
    body: MemberBody,
    user: CurrentUser = requires("store_trial_club.enroll"),
) -> dict[str, Any]:
    """Kayıt SİLİNMEZ, iptal edilir (ADR 0012). O uç da henüz yayında değil."""
    return await service().remove_member(member_id, reason=body.reason, actor=user.full_name)


# =========================================================== sonuç yükleme

class UploadBody(BaseModel):
    #: Dosyanın METNİ. Panel `FileReader` ile okur; ikili yükleme yok — sonuç
    #: dosyası CSV'dir ve metin olarak taşımak ara katman gerektirmez.
    content: str = Field(min_length=1, max_length=4_000_000)
    filename: str = Field(default="", max_length=200)


@router.post("/exams/{exam_id}/results/preview")
async def upload_preview(
    exam_id: int,
    body: UploadBody,
    user: CurrentUser = requires("store_trial_club.publish"),
) -> dict[str, Any]:
    """Eşleştirme önizlemesi. YAZMAZ — gerekçe istemez."""
    return await service().upload_preview(exam_id, content=body.content, filename=body.filename)


class ApplyBody(BaseModel):
    token: str = Field(min_length=8, max_length=64)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/results/apply")
async def upload_apply(
    body: ApplyBody,
    user: CurrentUser = requires("store_trial_club.publish"),
) -> dict[str, Any]:
    return await service().upload_apply(token=body.token, reason=body.reason,
                                        actor=user.full_name, dry_run=body.dryRun)


class ReasonBody(BaseModel):
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/exams/{exam_id}/results/publish")
async def publish(
    exam_id: int,
    body: ReasonBody,
    user: CurrentUser = requires("store_trial_club.publish"),
) -> dict[str, Any]:
    """Yayınlanan sonuç katılımcıya görünür olur; ayrı izin ister."""
    return await service().publish(exam_id, reason=body.reason, actor=user.full_name,
                                   dry_run=body.dryRun)


# ================================================================ bildirim

class NotifyPreviewBody(BaseModel):
    audience: str = Field(max_length=16)
    channel: str = Field(default="sms", max_length=8)
    message: str = Field(default="", max_length=1200)


@router.post("/exams/{exam_id}/notify/preview")
async def notify_preview(
    exam_id: int,
    body: NotifyPreviewBody,
    user: CurrentUser = requires("store_trial_club.notify"),
) -> dict[str, Any]:
    """Kaç kişiye, kaç SMS parçası. GÖNDERMEZ."""
    return await service().notify_preview(exam_id, audience=body.audience, channel=body.channel,
                                          message=body.message)


class NotifyBody(BaseModel):
    audience: str = Field(max_length=16)
    channel: str = Field(default="sms", max_length=8)
    template: str = Field(default="", max_length=64)
    message: str = Field(default="", max_length=1200)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/exams/{exam_id}/notify")
async def notify_bulk(
    exam_id: int,
    body: NotifyBody,
    user: CurrentUser = requires("store_trial_club.notify"),
) -> dict[str, Any]:
    """PARA HARCAR (SMS). Ayrı izin + gerekçe + kuru prova varsayılanı."""
    return await service().notify_bulk(exam_id, audience=body.audience, channel=body.channel,
                                       template=body.template, message=body.message,
                                       reason=body.reason, actor=user.full_name,
                                       dry_run=body.dryRun)


class NotifyOneBody(BaseModel):
    template: str = Field(default="", max_length=64)
    channel: str = Field(default="sms", max_length=8)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/members/{member_id}/notify")
async def notify_one(
    member_id: int,
    body: NotifyOneBody,
    user: CurrentUser = requires("store_trial_club.notify"),
) -> dict[str, Any]:
    """Tek kişilik hatırlatma — `store.notify.send` yeteneği yoksa kapalı."""
    return await service().notify_one(member_id, template=body.template, channel=body.channel,
                                      reason=body.reason, actor=user.full_name,
                                      dry_run=body.dryRun)


# ================================================================= ayarlar

@router.get("/settings")
async def settings(
    user: CurrentUser = requires("store_trial_club.view"),
) -> dict[str, Any]:
    return await service().settings()


class SettingsBody(BaseModel):
    nearFullPercent: int | None = Field(default=None, ge=50, le=100)
    attendanceSpareRows: int | None = Field(default=None, ge=0, le=50)
    notifyChannel: str = Field(default="", max_length=8)
    notifyTemplate: str = Field(default="", max_length=64)
    #: Tek kişilik hatırlatmanın şablon KİMLİĞİ (`store:12`) — toplu gönderimin
    #: mağaza şablon anahtarıyla aynı şey değildir, ayrı alan.
    notifyDirectTemplate: str = Field(default="", max_length=64)


@router.post("/settings")
async def save_settings(
    body: SettingsBody,
    user: CurrentUser = requires("store_trial_club.manage"),
) -> dict[str, Any]:
    """Hepsi YEREL tercih; mağazaya yazılmaz, bu yüzden gerekçe istemez."""
    return await service().save_settings(near_full_percent=body.nearFullPercent,
                                         attendance_spare_rows=body.attendanceSpareRows,
                                         notify_channel=body.notifyChannel,
                                         notify_template=body.notifyTemplate,
                                         notify_direct_template=body.notifyDirectTemplate,
                                         actor=user.full_name)


# =================================================================== rapor

class PreviewBody(BaseModel):
    kind: str = Field(max_length=24)
    examId: int = Field(default=0, ge=0)
    memberId: int = Field(default=0, ge=0)


@router.post("/preview")
async def preview(
    body: PreviewBody,
    user: CurrentUser = requires("store_trial_club.view"),
) -> dict[str, Any]:
    return await service().preview(body.kind, {"examId": body.examId,
                                               "memberId": body.memberId})


class PrintBody(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    copies: int = Field(default=1, ge=1, le=20)


@router.post("/print")
async def print_report(
    body: PrintBody,
    user: CurrentUser = requires("store_trial_club.view"),
) -> dict[str, Any]:
    return await service().print_report(body.path, copies=body.copies)


@router.get("/printer")
async def printer(
    user: CurrentUser = requires("store_trial_club.view"),
) -> dict[str, Any]:
    return await service().printer_status()


class ExportBody(BaseModel):
    examId: int = Field(ge=1)
    scope: str = Field(default="members", max_length=16)


@router.post("/export")
async def export(
    body: ExportBody,
    user: CurrentUser = requires("store_trial_club.view"),
) -> dict[str, Any]:
    """Katılımcı ya da sonuç CSV'si — rapor klasörüne yazılır."""
    return await service().export_csv(body.examId, scope=body.scope)
