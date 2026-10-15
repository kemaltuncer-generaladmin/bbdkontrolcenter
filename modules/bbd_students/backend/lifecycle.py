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

AKTİFLEŞTİR / YENİDEN ETKİNLEŞTİR (§5.3; 16.10.2026 kullanıcı: "KM de pasife
alınan öğrenci aktifleştirilebilsin"). Durum `active` + `student.reactivated`;
`restore_classes` (ekran ve uç varsayılanı AÇIK) ile pasifleştirmenin kapattığı
sınıf üyelikleri AYNI İŞLEMDE bugünden geri açılır (`group.changed`; arşivlenmiş
sınıf atlanır) — Akademi öğrenci, veli girişi, sınıf (öğretmen kapsamı, müfredat)
olaylarla 1–3 sn'de döner. Dönem geçişi geri alması sınıfları kendisi döndürdüğü
için açmaz (`restore_classes=False`). YALNIZ pasifleştirmenin koyduğu tablet
kısıtı kaldırılır (başka gerekçeyle konmuş kısıt sürer). Toplu aktifleştirmede
kantin ve tablet çağrıları arka planda sıralıdır (pasifleştirmeyle aynı).

KALICI SİL (§5.3). Yalnız pasif öğrencide, `bbd_students.delete` izniyle, ad
yazılarak; kantin bakiyesi TAM 0 (`409 balance_not_zero`), açık ödeme talebi
yok (`409 open_payment_request`), Akademi kurum cari borcu yok
(`409 institution_debt`, isteğe bağlı yetenek `akademi.student_finance`;
yetenek yoksa ya da Akademi'ye ulaşılamazsa silme KAPALI: `503
akademi_unavailable`). Tek işlemde profil satırı silinir + mezar taşı +
`student.deleted`; sonra kantin `DELETE` (anonimleştirir), olmazsa uzlaştırma.
Kurum carisindeki ALACAK (öğrenci lehine) silmeyi ENGELLEMEZ (kullanıcı kararı).

ÖN KONTROL (0.5.4): `delete_check` bütün engelleri birlikte, tutarıyla ve açık
Türkçe döner (yazma yok) — ekran silme penceresini açmadan gösterir. TOPLU
SİLME: her öğrenci ayrı denetlenir ve ayrı sonuç döner (silindi / engel); onay
seçilen sayının yazılmasıdır; kantin çağrıları arka planda sıralı ve aralıklı.
Rota katmanı her iki silmede de PIN teyidi ister (yıkıcı işlem, K-PIN).
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
#: Toplu kalıcı silmede tek istekte en çok öğrenci (her biri canlı kantin +
#: Akademi denetimi; kantin `throttle:120,1`).
BULK_DELETE_MAX = 50

#: Kalıcı silmede neyin silindiği — ekran onay penceresinde aynen gösterir.
WILL_DELETE = [
    "KM öğrenci kaydı (kimlik, sınıf, gruplar, veliler, kantin ayarları) silinir; yalnız silme kaydı kalır.",
    ("Kantinde öğrenci anonimleşir: kart ve giriş kapanır; satış ve defter kayıtları muhasebe için "
     "kimliksiz kalır."),
    ("BBD Akademi kişisel veriyi KVKK planına göre siler / anonimleştirir (student.deleted olayı); "
     "öğrencinin uygulama hesabı ve yalnız ona bağlı velinin girişi kapanır."),
    "Geri alınamaz.",
]
NOTE_MAX = 500

#: Pasifleştirmenin tablet kısıtında kullandığı gerekçe (yeniden etkinleştirme
#: bu kısıtı kaldırır).
TABLET_REASON = "Öğrenci pasifleştirildi (KM)"
REACTIVATE_REASON = "Öğrenci yeniden etkinleştirildi (KM)"
#: Pasifleştirmenin kapattığı sınıf üyeliğine yazılan gerekçe; aktifleştirme bu
#: satırları bulup geri açar.
GROUPS_CLOSED_REASON = "Öğrenci pasifleştirildi"
GROUPS_REOPENED_REASON = "Öğrenci aktifleştirildi"


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


def _lira(kurus: int) -> str:
    """42 50 → '42,50 ₺' (işaretsiz; yön metinde söylenir)."""
    value = abs(int(kurus))
    whole = f"{value // 100:,}".replace(",", ".")
    return f"{whole},{value % 100:02d} ₺"


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
                reason=GROUPS_CLOSED_REASON)
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

    async def reactivate(self, kantin_id: str, *, actor: str, restore_classes: bool = False,
                         push: bool = True) -> dict[str, Any]:
        """Pasif öğrenciyi aktifleştirir (ya da planlı pasifleştirmeyi iptal eder).

        `restore_classes`: pasifleştirmenin kapattığı sınıf üyelikleri bugünden geri açılır
        (aynı işlem). `push=False` (toplu): kantin / tablet çağrısını çağıran yapar."""
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
        group_revision: int | None = None
        classes: list[str] = []
        async with self._sync._store.transaction():
            await self._sync.write(
                kantin_id,
                columns={"status": "active", "status_reason": None, "status_since": iso(moment),
                         "status_note": "", "status_effective_at": None},
                canteen_ops={"status": {"status": "active", "reason": None,
                                        "at": local_iso(moment)}},
                events=[Event("student.reactivated", ["status"], {"status": "active"})],
                log={"action": "reactivated", "actor": actor})
            if restore_classes:
                group_revision, classes = await self._groups.reopen_student_memberships(
                    kantin_id, start=self._sync._today(), actor=actor,
                    closed_reason=GROUPS_CLOSED_REASON, reason=GROUPS_REOPENED_REASON)
                if classes:
                    # Durum geçmişinde (denetim) hangi sınıflara dönüldüğü görünsün.
                    await self._sync.write_log(
                        kantin_id, action="classes_restored", actor=actor,
                        note="Sınıflara geri eklendi: " + ", ".join(classes))
        if group_revision is not None:
            await self._groups.after_commit(group_revision)
        result: dict[str, Any] = {"studentId": kantin_id, "status": "active",
                                  "groupsReopened": bool(classes), "classesRestored": classes}
        if push:
            outcome = await self._sync.push(kantin_id)
            result["canteenSync"] = "ok" if outcome.state == "ok" else "pending"
            result["tablet"] = await self._tablet(kantin_id, block=False, actor=actor)
        else:
            result["canteenSync"] = "pending"
        self._log.info("öğrenci aktifleştirildi", student=kantin_id, actor=actor,
                       classes=len(classes))
        return result

    async def bulk_reactivate(self, kantin_ids: list[Any], *, actor: str,
                              restore_classes: bool = True) -> dict[str, Any]:
        """Çoklu seçim: KM tarafı hemen (her öğrenci ayrı olay), kantin ve tablet arka
        planda sıralı (pasifleştirmeyle aynı). Etkin öğrenci `failed` içinde döner."""
        ids = list(dict.fromkeys(str(item).strip() for item in kantin_ids if str(item).strip()))
        if not ids:
            raise SyncError("Öğrenci seçilmedi.", details={"field": "students"})
        if len(ids) > BULK_MAX:
            raise SyncError(f"Tek seferde en çok {BULK_MAX} öğrenci.")
        done: list[str] = []
        failed: list[dict[str, str]] = []
        restored = 0
        for kantin_id in ids:
            try:
                result = await self.reactivate(kantin_id, actor=actor,
                                               restore_classes=restore_classes, push=False)
            except SyncError as failure:
                failed.append({"studentId": kantin_id, "error": str(failure),
                               "code": failure.code})
                continue
            if result.get("scheduleCancelled"):
                continue
            done.append(kantin_id)
            restored += len(result.get("classesRestored") or [])
        if done:
            task = asyncio.get_running_loop().create_task(
                self._push_many_reactivated(done, actor=actor), name="km-bbd-students-bulk-on")
            self._background.add(task)
            task.add_done_callback(self._background.discard)
        self._log.info("toplu aktifleştirme", done=len(done), failed=len(failed), actor=actor)
        return {"reactivated": done, "failed": failed, "classesRestored": restored,
                "canteenPending": len(done)}

    async def _push_many_reactivated(self, ids: list[str], *, actor: str) -> None:
        for index, kantin_id in enumerate(ids):
            if index:
                await asyncio.sleep(self._sync._delay)
            try:
                await self._sync.push(kantin_id)
                await self._tablet(kantin_id, block=False, actor=actor)
            except Exception as failure:  # noqa: BLE001 — uzlaştırma devralır
                self._log.warning("toplu aktifleştirme kantin adımı", student=kantin_id,
                                  error=str(failure))

    # ============================================================ kalıcı sil

    async def delete(self, kantin_id: str, *, confirm_name: Any, actor: str) -> dict[str, Any]:
        row = await self._require(kantin_id)
        if str(row.get("status") or "active") != "inactive":
            raise SyncError("Yalnız pasif öğrenci kalıcı silinebilir.", status=409,
                            code="not_inactive")
        await self._check_name(kantin_id, row, confirm_name)
        blockers, _notes = await self._blockers(kantin_id)
        if blockers:
            raise blockers[0]
        return await self._erase(kantin_id, actor=actor)

    async def _erase(self, kantin_id: str, *, actor: str, push: bool = True) -> dict[str, Any]:
        """Profil satırı silinir + mezar taşı + `student.deleted` (TEK işlem); sonra kantin."""
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
        self._log.info("öğrenci kalıcı silindi", student=kantin_id, actor=actor)
        if not push:
            return {"studentId": kantin_id, "deleted": True, "canteenSync": "pending",
                    "canteenError": None}
        outcome = await self._sync.push_tombstone(kantin_id)
        return {"studentId": kantin_id, "deleted": True,
                "canteenSync": "ok" if outcome.state == "ok" else "pending",
                "canteenError": outcome.error or None}

    async def delete_check(self, kantin_id: str) -> dict[str, Any]:
        """Silmeden ÖNCE ön kontrol: bütün engeller birlikte, açık Türkçe (yazma yok).

        Ekran bunları silme penceresini açmadan gösterir ("Kantin bakiyesi
        42,50 ₺ — önce sıfırlayın" + ilgili ekrana bağlantı). Kurum carisindeki
        ALACAK (öğrenci lehine) silmeyi ENGELLEMEZ (kullanıcı kararı); bilgi
        notu olarak döner.
        """
        row = await self._require(kantin_id)
        name = f"{row.get('first_name') or ''} {row.get('last_name') or ''}".strip()
        try:
            record = await self._directory.get(kantin_id)
        except Exception:  # noqa: BLE001 — ad profilden
            record = None
        if record and record.get("displayName"):
            name = str(record["displayName"])
        status = str(row.get("status") or "active")
        found: list[SyncError] = []
        if status != "inactive":
            found.append(SyncError("Öğrenci etkin: kalıcı silmeden önce pasifleştirin (önerilen yol "
                                   "budur; veriler korunur, yeniden etkinleştirilebilir).",
                                   status=409, code="not_inactive"))
        blockers, notes = await self._blockers(kantin_id)
        found.extend(blockers)
        return {
            "studentId": kantin_id,
            "displayName": name,
            "status": status,
            "deletable": not found,
            "blockers": [{"code": item.code, "message": str(item), "details": item.details or {}}
                         for item in found],
            "notes": notes,
            "willDelete": WILL_DELETE,
        }

    async def bulk_delete(self, kantin_ids: list[Any], *, confirm_count: Any,
                          actor: str) -> dict[str, Any]:
        """Toplu kalıcı silme: her öğrenci AYRI denetlenir ve ayrı sonuç döner
        (silindi / engel). Onay: seçilen öğrenci sayısı aynen yazılır (ad tek
        tek yazdırılamaz). Kantin silme çağrıları arka planda sıralı ve aralıklı
        gider (`throttle:120,1`; uzlaştırma ≤ 1 dk devralır)."""
        ids = list(dict.fromkeys(str(item).strip() for item in kantin_ids if str(item).strip()))
        if not ids:
            raise SyncError("Öğrenci seçilmedi.", details={"field": "students"})
        if len(ids) > BULK_DELETE_MAX:
            raise SyncError(f"Tek seferde en çok {BULK_DELETE_MAX} öğrenci kalıcı silinebilir.")
        if str(confirm_count or "").strip() != str(len(ids)):
            raise SyncError("Onay için silinecek öğrenci sayısını aynen yazın.", status=422,
                            code="confirm_count_mismatch", details={"field": "confirmCount"})
        results: list[dict[str, Any]] = []
        erased: list[str] = []
        for index, kantin_id in enumerate(ids):
            if index:
                await asyncio.sleep(self._sync._delay)
            try:
                check = await self.delete_check(kantin_id)
            except SyncError as failure:
                results.append({"studentId": kantin_id, "displayName": "", "deleted": False,
                                "code": failure.code, "message": str(failure)})
                continue
            if not check["deletable"]:
                first = check["blockers"][0]
                results.append({"studentId": kantin_id, "displayName": check["displayName"],
                                "deleted": False, "code": first["code"], "message": first["message"],
                                "blockers": check["blockers"]})
                continue
            await self._erase(kantin_id, actor=actor, push=False)
            erased.append(kantin_id)
            results.append({"studentId": kantin_id, "displayName": check["displayName"],
                            "deleted": True, "code": None, "message": "Kalıcı silindi."})
        if erased:
            task = asyncio.get_running_loop().create_task(
                self._push_tombstones(erased), name="km-bbd-students-bulk-delete")
            self._background.add(task)
            task.add_done_callback(self._background.discard)
        self._log.info("toplu kalıcı silme", deleted=len(erased), blocked=len(ids) - len(erased),
                       actor=actor)
        return {"results": results, "deleted": len(erased), "blocked": len(ids) - len(erased),
                "canteenPending": len(erased)}

    async def _push_tombstones(self, ids: list[str]) -> None:
        for index, kantin_id in enumerate(ids):
            if index:
                await asyncio.sleep(self._sync._delay)
            try:
                await self._sync.push_tombstone(kantin_id)
            except Exception as failure:  # noqa: BLE001 — uzlaştırma devralır
                self._log.warning("toplu silme kantin adımı", student=kantin_id, error=str(failure))

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

    async def _blockers(self, kantin_id: str) -> tuple[list[SyncError], list[dict[str, Any]]]:
        """Silmeyi engelleyen durumlar (sırayla: kantin, ödeme talebi, kurum cari) ve
        engellemeyen bilgi notları. Hepsi toplanır; `delete` ilkini fırlatır."""
        blockers: list[SyncError] = []
        notes: list[dict[str, Any]] = []
        canteen_ok = True
        try:
            student = await self._canteen.get_student(kantin_id)
        except Exception as failure:  # noqa: BLE001 — kantin dışarısı; engel olarak döner
            student = None
            if getattr(failure, "reason", None) != "student_not_found":
                canteen_ok = False
                blockers.append(SyncError("Kantine ulaşılamadı; bakiye doğrulanamadan silinmez. "
                                          "Biraz sonra tekrar deneyin.", status=503,
                                          code="canteen_unavailable"))
        if student is not None:
            balance = int(student.get("balance") or 0)
            if balance > 0:
                blockers.append(SyncError(
                    f"Kantin bakiyesi {_lira(balance)} borç — önce tahsil edin (kasada nakit ya da "
                    "ödeme talebi) ve sıfırlayın.", status=409, code="balance_not_zero",
                    details={"balance": balance}))
            elif balance < 0:
                blockers.append(SyncError(
                    f"Kantin bakiyesi {_lira(balance)} öğrenci lehine — önce veliye iade edin ve "
                    "sıfırlayın.", status=409, code="balance_not_zero", details={"balance": balance}))
            try:
                requests = await self._canteen.payment_requests(student_id=kantin_id)
            except Exception:  # noqa: BLE001 — kantin dışarısı
                requests = None
                if canteen_ok:
                    blockers.append(SyncError("Kantine ulaşılamadı; ödeme talepleri doğrulanamadı.",
                                              status=503, code="canteen_unavailable"))
            open_refs = [str(item.get("ref") or "") for item in requests or []
                         if str(item.get("status") or "").upper() == "PENDING"]
            if open_refs:
                blockers.append(SyncError(
                    f"Açık ödeme talebi var ({', '.join(open_refs[:3])}) — önce iptal edin ya da "
                    "ödenmesini bekleyin.", status=409, code="open_payment_request",
                    details={"refs": open_refs}))
        finance = self._capability("akademi.student_finance")
        if finance is None:
            blockers.append(SyncError("Akademi kurum cari durumu okunamıyor (BBD Akademi bağlantısı "
                                      "yok); silme kapalı.", status=503, code="akademi_unavailable"))
            return blockers, notes
        try:
            # Tutarlı yetenek (0.5.4) tek okumada borç + alacak verir; eskisi yalnız evet/hayır.
            summary = None
            if hasattr(finance, "institution_balance"):
                summary = await finance.institution_balance(kantin_id)
                has_debt = int(summary.get("debtMinor") or 0) > 0
            else:
                has_debt = await finance.has_institution_debt(kantin_id)
        except Exception:  # noqa: BLE001 — Akademi dışarısı
            blockers.append(SyncError("Akademi'ye ulaşılamadı; kurum cari borcu doğrulanamadan "
                                      "silinmez. Biraz sonra tekrar deneyin.", status=503,
                                      code="akademi_unavailable"))
            return blockers, notes
        debt = int((summary or {}).get("debtMinor") or 0)
        credit = int((summary or {}).get("creditMinor") or 0)
        if has_debt:
            blockers.append(SyncError(
                f"Kurum cari borcu var{f' ({_lira(debt)})' if debt > 0 else ''} — önce Kurum "
                "finansı'ndan tahsil edin ya da kapatın.", status=409, code="institution_debt",
                details={"debtMinor": debt} if debt > 0 else {}))
        if credit > 0:
            notes.append({"code": "institution_credit", "details": {"creditMinor": credit},
                          "message": f"Kurum carisinde öğrenci lehine {_lira(credit)} alacak var. "
                                     "Silmeyi engellemez; iade gerekiyorsa silmeden önce yapın."})
        return blockers, notes

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
