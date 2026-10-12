"""Öğrenci yaşam döngüsü — pasifleştir, yeniden etkinleştir, kalıcı sil.

Sözleşme: bbd-akademi `docs/integrations/km-merkezi-senkron.md` §5.
Durumlar `active → inactive → active` ve `inactive → (silindi)`. Kararı YALNIZ
KM verir; kantin ve Akademi uygular.

PASİFLEŞTİR (§5.2). Tek işlemde: profil `status = inactive` + gerekçe + tarih,
`student.deactivated` olayı (`data` = durum, gerekçe, `effectiveAt` — güvenlik
olayı, Akademi okumadan önce oturumları kapatır), açık grup üyeliklerinin
kapanışı (`group.changed`) ve durum geçmişi. Sonra kantine `PATCH status`
(satış o an kesilir; kantin yoksa uzlaştırma ≤ 1 dk) ve tablet girişi süresiz
kısıtlanır (`bbd_tablet.control`, isteğe bağlı yetenek → `tablet.policy_changed`).
İleri tarihli pasifleştirme o gün 00:00'da uzlaştırma turunda uygulanır; olay
uygulandığı an yazılır. Toplu pasifleştirmede her öğrenci ayrı olaydır, kantin
çağrıları sıralı ve aralıklıdır (`throttle:120,1`).

YENİDEN ETKİNLEŞTİR (§5.3). Durum `active`; grup üyelikleri KENDİLİĞİNDEN
AÇILMAZ (ekran uyarır). YALNIZ pasifleştirmenin koyduğu tablet kısıtı kaldırılır
(başka gerekçeyle konmuş kısıt sürer).

KALICI SİL (§5.3). Yalnız pasif öğrencide, `bbd_students.delete` izniyle, ad
yazılarak; kantin bakiyesi TAM 0 (`409 balance_not_zero`), açık ödeme talebi
yok (`409 open_payment_request`), Akademi kurum cari borcu yok
(`409 institution_debt`, isteğe bağlı yetenek `akademi.student_finance`;
yetenek yoksa ya da Akademi'ye ulaşılamazsa silme KAPALI: `503
akademi_unavailable`). Tek işlemde profil satırı silinir + mezar taşı +
`student.deleted`; sonra kantin `DELETE` (anonimleştirir), olmazsa uzlaştırma.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from .directory import split_name
from .groups import GroupService
from .sync import (
    STATUS_REASONS,
    Event,
    StudentSync,
    SyncError,
    iso,
    local_iso,
)

#: Toplu pasifleştirmede tek istekte en çok öğrenci.
BULK_MAX = 2000
NOTE_MAX = 500

#: Pasifleştirmenin tablet kısıtında kullandığı gerekçe (yeniden etkinleştirme
#: bu kısıtı kaldırır).
TABLET_REASON = "Öğrenci pasifleştirildi (KM)"
REACTIVATE_REASON = "Öğrenci yeniden etkinleştirildi (KM)"


def parse_effective(value: Any, *, today: date) -> date:
    """`effectiveAt`: boş = bugün; ISO gün ya da damga; geçmiş gün reddedilir."""
    if value in (None, ""):
        return today
    text = str(value).strip()
    try:
        day = date.fromisoformat(text[:10])
    except ValueError as failure:
        raise SyncError("Geçerlilik tarihi YYYY-AA-GG olmalı.",
                        details={"field": "effectiveAt"}) from failure
    if day < today:
        raise SyncError("Pasifleştirme geçmiş bir güne yazılamaz.",
                        details={"field": "effectiveAt"})
    if day > today + timedelta(days=400):
        raise SyncError("Geçerlilik tarihi en çok bir yıl ileride olabilir.",
                        details={"field": "effectiveAt"})
    return day


def _reason(value: Any) -> str:
    reason = str(value or "").strip()
    if reason not in STATUS_REASONS:
        raise SyncError("Gerekçe seçin: ayrıldı, mezun, geçici durdurma ya da diğer.",
                        details={"field": "reason"})
    return reason


def _fold(text: str) -> str:
    return " ".join(str(text or "").replace("I", "ı").replace("İ", "i").casefold().split())


class Lifecycle:
    """Yaşam döngüsü işlemleri. `capability(name)` isteğe bağlı yetenekleri ÇAĞRI
    ANINDA çözer (tablet, Akademi finans); yoksa None."""

    def __init__(self, *, sync: StudentSync, groups: GroupService, canteen: Any, log: Any,
                 directory: Any, capability: Callable[[str], Any] | None = None) -> None:
        self._sync = sync
        self._groups = groups
        self._canteen = canteen
        self._log = log
        self._directory = directory
        self._capability = capability or (lambda _name: None)
        self._background: set[asyncio.Task[Any]] = set()
        sync.due_hooks.append(self.apply_due)

    # ============================================================ pasifleştir

    async def deactivate(self, kantin_id: str, *, reason: Any, effective_at: Any = None,
                         note: Any = "", actor: str, push: bool = True) -> dict[str, Any]:
        why = _reason(reason)
        today = self._sync._today()
        day = parse_effective(effective_at, today=today)
        text = str(note or "").strip()[:NOTE_MAX]
        row = await self._require(kantin_id)
        if str(row.get("status") or "active") == "inactive":
            raise SyncError("Öğrenci zaten pasif.", status=409, code="already_inactive")

        if day > today:
            await self._sync.write(
                kantin_id,
                columns={"status_effective_at": day.isoformat(), "status_reason": why,
                         "status_note": text},
                log={"action": "scheduled", "actor": actor, "reason": why,
                     "effective_at": day.isoformat(), "note": text})
            self._log.info("öğrenci pasifleştirme planlandı", student=kantin_id,
                           effective=day.isoformat(), actor=actor)
            return {"studentId": kantin_id, "status": "active", "scheduledFor": day.isoformat(),
                    "canteenSync": str(row.get("canteen_sync") or "ok")}

        return await self._apply_deactivation(kantin_id, reason=why, note=text, actor=actor,
                                              last_day=today, push=push)

    async def _apply_deactivation(self, kantin_id: str, *, reason: str, note: str, actor: str,
                                  last_day: date, push: bool) -> dict[str, Any]:
        moment = self._sync._clock()
        effective = local_iso(moment)
        group_revision: int | None = None
        async with self._sync._store.transaction():
            await self._sync.write(
                kantin_id,
                columns={"status": "inactive", "status_reason": reason,
                         "status_since": iso(moment), "status_note": note,
                         "status_effective_at": None},
                canteen_ops={"status": {"status": "inactive", "reason": reason,
                                        "at": effective}},
                events=[Event("student.deactivated", ["status"],
                              {"status": "inactive", "reason": reason,
                               "effectiveAt": effective})],
                log={"action": "deactivated", "actor": actor, "reason": reason,
                     "effective_at": effective, "note": note})
            group_revision = await self._groups.close_student_memberships(
                kantin_id, last_day=last_day, actor=actor,
                reason="Öğrenci pasifleştirildi")
        if group_revision is not None:
            await self._groups.after_commit(group_revision)
        self._log.info("öğrenci pasifleştirildi", student=kantin_id, reason=reason, actor=actor)
        result: dict[str, Any] = {"studentId": kantin_id, "status": "inactive",
                                  "groupsClosed": group_revision is not None}
        if push:
            outcome = await self._sync.push(kantin_id)
            result["canteenSync"] = "ok" if outcome.state == "ok" else "pending"
            if outcome.state != "ok":
                result["canteenError"] = outcome.error
            result["tablet"] = await self._tablet(kantin_id, block=True, actor=actor)
        else:
            result["canteenSync"] = "pending"
        return result

    async def apply_due(self) -> int:
        """İleri tarihli pasifleştirmeler: günü gelenleri uygular (uzlaştırma turu)."""
        today = self._sync._today()
        rows = await self._sync._store.fetch_all(
            f"SELECT kantin_id, status_reason, status_note, status_effective_at "
            f"FROM {self._sync._profiles} WHERE status = 'active' "
            f"AND status_effective_at IS NOT NULL AND status_effective_at <= ?",
            (today.isoformat(),))
        applied = 0
        for row in rows:
            day = date.fromisoformat(str(row["status_effective_at"])[:10])
            try:
                await self._apply_deactivation(
                    str(row["kantin_id"]), reason=str(row["status_reason"] or "other"),
                    note=str(row["status_note"] or ""), actor="Planlı pasifleştirme",
                    last_day=day - timedelta(days=1), push=True)
                applied += 1
            except Exception as failure:  # noqa: BLE001 — biri patlarsa diğerleri sürsün
                self._log.warning("planlı pasifleştirme uygulanamadı",
                                  student=row["kantin_id"], error=str(failure))
        return applied

    async def bulk_deactivate(self, kantin_ids: list[Any], *, reason: Any,
                              effective_at: Any = None, note: Any = "",
                              actor: str) -> dict[str, Any]:
        """Çoklu seçim (yıl sonu mezunlar). KM tarafı hemen yazılır (her öğrenci ayrı
        olay); kantin ve tablet çağrıları ARKA PLANDA sıralı ve aralıklı gider."""
        ids = list(dict.fromkeys(str(item).strip() for item in kantin_ids if str(item).strip()))
        if not ids:
            raise SyncError("Öğrenci seçilmedi.", details={"field": "students"})
        if len(ids) > BULK_MAX:
            raise SyncError(f"Tek seferde en çok {BULK_MAX} öğrenci.")
        why = _reason(reason)
        done: list[str] = []
        scheduled: list[str] = []
        failed: list[dict[str, str]] = []
        for kantin_id in ids:
            try:
                result = await self.deactivate(kantin_id, reason=why, effective_at=effective_at,
                                               note=note, actor=actor, push=False)
            except SyncError as failure:
                failed.append({"studentId": kantin_id, "error": str(failure),
                               "code": failure.code})
                continue
            (scheduled if result.get("scheduledFor") else done).append(kantin_id)
        if done:
            task = asyncio.get_running_loop().create_task(
                self._push_many(done, actor=actor), name="km-bbd-students-bulk")
            self._background.add(task)
            task.add_done_callback(self._background.discard)
        self._log.info("toplu pasifleştirme", done=len(done), scheduled=len(scheduled),
                       failed=len(failed), actor=actor)
        return {"deactivated": done, "scheduled": scheduled, "failed": failed,
                "canteenPending": len(done)}

    async def _push_many(self, ids: list[str], *, actor: str) -> None:
        for index, kantin_id in enumerate(ids):
            if index:
                await asyncio.sleep(self._sync._delay)
            try:
                await self._sync.push(kantin_id)
                await self._tablet(kantin_id, block=True, actor=actor)
            except Exception as failure:  # noqa: BLE001 — uzlaştırma devralır
                self._log.warning("toplu pasifleştirme kantin adımı", student=kantin_id,
                                  error=str(failure))

    # ===================================================== yeniden etkinleştir

    async def reactivate(self, kantin_id: str, *, actor: str) -> dict[str, Any]:
        row = await self._require(kantin_id)
        if str(row.get("status") or "active") != "inactive":
            if row.get("status_effective_at"):
                # Planlı pasifleştirmeyi iptal etmek de "etkin kalsın" demektir.
                await self._sync.write(
                    kantin_id, columns={"status_effective_at": None, "status_reason": None,
                                        "status_note": ""},
                    log={"action": "schedule_cancelled", "actor": actor})
                return {"studentId": kantin_id, "status": "active", "scheduleCancelled": True}
            raise SyncError("Öğrenci zaten etkin.", status=409, code="already_active")
        moment = self._sync._clock()
        await self._sync.write(
            kantin_id,
            columns={"status": "active", "status_reason": None, "status_since": iso(moment),
                     "status_note": "", "status_effective_at": None},
            canteen_ops={"status": {"status": "active", "reason": None,
                                    "at": local_iso(moment)}},
            events=[Event("student.reactivated", ["status"], {"status": "active"})],
            log={"action": "reactivated", "actor": actor})
        outcome = await self._sync.push(kantin_id)
        tablet = await self._tablet(kantin_id, block=False, actor=actor)
        self._log.info("öğrenci yeniden etkinleştirildi", student=kantin_id, actor=actor)
        return {"studentId": kantin_id, "status": "active",
                "canteenSync": "ok" if outcome.state == "ok" else "pending",
                "tablet": tablet,
                # Grup üyelikleri kendiliğinden açılmaz (§5.3) — ekran uyarır.
                "groupsReopened": False}

    # ============================================================ kalıcı sil

    async def delete(self, kantin_id: str, *, confirm_name: Any, actor: str) -> dict[str, Any]:
        row = await self._require(kantin_id)
        if str(row.get("status") or "active") != "inactive":
            raise SyncError("Yalnız pasif öğrenci kalıcı silinebilir.", status=409,
                            code="not_inactive")
        await self._check_name(kantin_id, row, confirm_name)
        await self._check_canteen(kantin_id)
        await self._check_institution(kantin_id)

        moment = self._sync._clock()
        async with self._sync._store.transaction():
            revision = await self._sync.bump()
            await self._sync._store.execute(
                f"DELETE FROM {self._sync._profiles} WHERE kantin_id = ?", (kantin_id,))
            await self._sync._store.execute(
                f"INSERT OR REPLACE INTO {self._sync._tombstones} (kantin_id, deleted_at, "
                f"canteen_sync, canteen_attempts, canteen_error) VALUES (?, ?, 'pending', 0, NULL)",
                (kantin_id, iso(moment)))
            await self._sync.write_log(kantin_id, action="deleted", actor=actor)
            await self._sync.emit(kantin_id, [Event("student.deleted", [],
                                                    {"erasedAt": local_iso(moment)})],
                                  revision=revision)
        self._sync.changed(canteen=True)
        outcome = await self._sync.push_tombstone(kantin_id)
        self._log.info("öğrenci kalıcı silindi", student=kantin_id, actor=actor)
        return {"studentId": kantin_id, "deleted": True,
                "canteenSync": "ok" if outcome.state == "ok" else "pending",
                "canteenError": outcome.error or None}

    async def _check_name(self, kantin_id: str, row: dict[str, Any], confirm: Any) -> None:
        names = {_fold(f"{row.get('first_name') or ''} {row.get('last_name') or ''}")}
        try:
            record = await self._directory.get(kantin_id)
        except Exception:  # noqa: BLE001 — kantin yoksa profildeki ad yeter
            record = None
        if record:
            names.add(_fold(str(record.get("displayName") or "")))
            names.add(_fold(f"{record.get('firstName') or ''} {record.get('lastName') or ''}"))
        names.discard("")
        if not names or _fold(str(confirm or "")) not in names:
            raise SyncError("Onay için öğrencinin adını aynen yazın.", status=422,
                            code="confirm_name_mismatch", details={"field": "confirmName"})

    async def _check_canteen(self, kantin_id: str) -> None:
        try:
            student = await self._canteen.get_student(kantin_id)
        except Exception as failure:
            if getattr(failure, "reason", None) == "student_not_found":
                return  # kantinde hiç yok (yazılamamış kayıt): bakiye yok
            raise SyncError("Kantine ulaşılamadı; bakiye doğrulanamadan silinmez.",
                            status=503, code="canteen_unavailable") from failure
        balance = int(student.get("balance") or 0)
        if balance != 0:
            raise SyncError(
                "Kantin bakiyesi sıfır değil: önce tahsil edin ya da iade edin.",
                status=409, code="balance_not_zero", details={"balance": balance})
        try:
            requests = await self._canteen.payment_requests(student_id=kantin_id)
        except Exception as failure:
            raise SyncError("Kantine ulaşılamadı; ödeme talepleri doğrulanamadı.",
                            status=503, code="canteen_unavailable") from failure
        open_refs = [str(item.get("ref") or "") for item in requests
                     if str(item.get("status") or "").upper() == "PENDING"]
        if open_refs:
            raise SyncError("Açık ödeme talebi var: önce iptal edin ya da ödenmesini bekleyin.",
                            status=409, code="open_payment_request",
                            details={"refs": open_refs})

    async def _check_institution(self, kantin_id: str) -> None:
        finance = self._capability("akademi.student_finance")
        if finance is None:
            raise SyncError("Akademi kurum cari durumu okunamıyor (yetenek yok); "
                            "silme kapalı.", status=503, code="akademi_unavailable")
        try:
            has_debt = await finance.has_institution_debt(kantin_id)
        except Exception as failure:
            raise SyncError("Akademi'ye ulaşılamadı; kurum cari borcu doğrulanamadan "
                            "silinmez.", status=503, code="akademi_unavailable") from failure
        if has_debt:
            raise SyncError("Akademi'de kurum cari borcu var: önce kapatılmalı.",
                            status=409, code="institution_debt")

    # ============================================================== yardım

    async def _require(self, kantin_id: str) -> dict[str, Any]:
        """Profil satırı; kantinde olup profili olmayan öğrenciye boş satır açılır."""
        if await self._sync.tombstone(kantin_id) is not None:
            raise SyncError("Öğrenci silinmiş.", status=404, code="student_not_found")
        row = await self._sync.profile(kantin_id)
        if row is not None:
            return row
        try:
            record = await self._directory.get(kantin_id)
        except Exception as failure:
            raise SyncError("Kantine ulaşılamadı.", status=503,
                            code="canteen_unavailable") from failure
        if record is None:
            raise SyncError("Öğrenci bulunamadı.", status=404, code="student_not_found")
        first, last = split_name(str(record.get("displayName") or ""))
        await self._sync.write(kantin_id, columns={"first_name": first, "last_name": last})
        created = await self._sync.profile(kantin_id)
        if created is None:  # pragma: no cover - yazma az önce yapıldı
            raise SyncError("Öğrenci bulunamadı.", status=404, code="student_not_found")
        return created

    async def _tablet(self, kantin_id: str, *, block: bool, actor: str) -> str:
        """Tablet girişini kapatır / açar (isteğe bağlı yetenek) + `tablet.policy_changed`."""
        control = self._capability("bbd_tablet.control")
        if control is None:
            return "unavailable"
        try:
            if block:
                await control.block(kantin_id, None, TABLET_REASON, actor)
            else:
                # Yalnız pasifleştirmenin koyduğu kısıt kalkar; başka gerekçeyle
                # (öğretmen, veli talebi) konmuş kısıt yeniden etkinleştirmede sürer.
                try:
                    await control.unblock(kantin_id, REACTIVATE_REASON, actor,
                                          only_reason=TABLET_REASON)
                except TypeError:  # `only_reason` bilmeyen eski tablet modülü
                    await control.unblock(kantin_id, REACTIVATE_REASON, actor)
        except Exception as failure:  # noqa: BLE001 — tablet düşse de durum yazıldı (K7)
            self._log.warning("tablet kısıtı uygulanamadı", student=kantin_id, block=block,
                              error=str(failure))
            return "failed"
        try:
            await self._sync.emit(kantin_id, [Event("tablet.policy_changed", ["blockedUntil"])],
                                  revision=None)
        except Exception as failure:  # noqa: BLE001
            self._log.warning("tablet olayı yazılamadı", error=str(failure))
        return "blocked" if block else "unblocked"


def describe_status(row: dict[str, Any] | None, *, now: datetime | None = None) -> dict[str, Any]:
    """Liste ve dizin için durum alanları (profil satırından)."""
    row = row or {}
    since = row.get("status_since")
    return {
        "status": str(row.get("status") or "active"),
        "statusReason": row.get("status_reason") if row.get("status") == "inactive" else None,
        "statusSince": local_iso(datetime.fromisoformat(str(since))) if since else None,
        "scheduledDeactivation": row.get("status_effective_at"),
    }
