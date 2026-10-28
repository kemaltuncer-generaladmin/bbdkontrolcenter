"""Öğrenci kaydının merkezi senkronu — önce KM, sonra kantin, sonra uzlaştırma.

Sözleşme: bbd-akademi `docs/integrations/km-merkezi-senkron.md` §2 (olaylar),
§5 (yaşam döngüsü), §6.3 (kasadan eklenen öğrenci), §7 (tutarlılık). ADR 0029.

SIRA (§7). Her öğrenci yazması:

  1. doğrulanır (çağıran: `service.py`);
  2. TEK İŞLEMDE profil satırı + `revision` (küresel sayaçtan) +
     `canteen_sync = pending` + `canteen_payload` (kantine gidecek İDEMPOTENT
     gövde) + outbox olayı yazılır — `write()`;
  3. işlemden SONRA kantine yazılır — `push()`; başarılıysa `ok`;
  4. kantin reddederse (4xx) çağıran telafi eder (`compensate_*`); ağ / 5xx /
     zaman aşımında kayıt `pending` kalır ve uzlaştırma görevi (1 dk + kantin
     geri gelince `wake()`) aynı çağrıyı yineler. 24 saati geçen `pending`
     panelde kırmızıdır.

Bekleyen kantin gövdesi `{"upsert": {...}, "status": {...}}` biçimindedir;
art arda yazmalar BİRLEŞİR (son değer kazanır). Dizin, kantine henüz yazılmamış
değerleri bu gövdeden okur: KM ekranı ve Akademi KM'nin kararını hemen görür.

OLAYLAR kişisel veri taşımaz: kimlik + revizyon + değişen MANTIKSAL alan adı
(geçit sözleşmesindeki adlar). `data` yalnız yaşam döngüsü ve veli yuvasında.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

ISTANBUL = ZoneInfo("Europe/Istanbul")

#: Pasifleştirme gerekçeleri (sözleşme `StudentStatus` gerekçesi).
STATUS_REASONS = ("left", "graduated", "suspended", "other")

#: Uzlaştırma aralığı (sn) ve iki kantin çağrısı arası bekleme (kantin
#: `throttle:120,1`; toplu şifre sıfırlamadaki `RESET_DELAY_SECONDS` kalıbı).
RECONCILE_SECONDS = 60.0
CANTEEN_DELAY_SECONDS = 0.6
#: Tek turda en çok kaç bekleyen kayıt denenir.
RECONCILE_BATCH = 100
#: Bu kadar başarısız denemeden sonra kayıt her turda değil 10 dakikada bir denenir.
BACKOFF_AFTER = 10
BACKOFF_SECONDS = 600.0
#: `pending` bu süreyi aşınca panelde kırmızı.
STALE_AFTER = timedelta(hours=24)
#: Mezar taşı saklama süresi (dizin `removed` 90 gün döner).
TOMBSTONE_DAYS = 90

#: Kantin hatalarından "tekrar denemek anlamsız" sayılanlar (kural reddi).
REJECT_STATUSES = (400, 409, 422)

#: Profil sütunu → olaydaki mantıksal alan (geçit sözleşmesi adları).
#: `note` KM'de kalır, olaya girmez. Veli telefonları ayrı veli olaylarıdır.
LOGICAL_FIELDS = {
    "first_name": "firstName",
    "last_name": "lastName",
    "class_name": "className",
    "school_no": "schoolNo",
    "student_phone": "studentPhone",
    "parent_name": "guardians",
    "parent_name2": "guardians",
    # Veli yönetimi (göç 007): yakınlık ve 3.+ veliler de `guardians` alanıdır.
    "parent_relation": "guardians",
    "parent_relation2": "guardians",
    "guardians_extra": "guardians",
    "grade_level": "gradeLevel",
    "track": "track",
    "exam_target": "examTarget",
    # Sözleşme kimliği (göç 014): olay yalnız ALAN ADINI taşır, değeri asla.
    **{column: "identity" for column in (
        "national_id", "national_id_tail", "foreign_id", "foreign_id_tail", "birth_date",
        "gender", "registered_on", "school_name", "home_phone", "address_line",
        "address_district", "address_city", "mother_name", "father_name",
        "guardian_identity")},
}


def now_utc() -> datetime:
    return datetime.now(UTC)


def today_local() -> date:
    return datetime.now(ISTANBUL).date()


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def local_iso(moment: datetime) -> str:
    return moment.astimezone(ISTANBUL).isoformat(timespec="seconds")


class SyncError(RuntimeError):
    """İş kuralı reddi. `status` HTTP karşılığı, `code` makine içindir."""

    def __init__(self, message: str, *, status: int = 400, code: str = "validation_error",
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.details = dict(details or {})


@dataclass(slots=True)
class Event:
    """Outbox'a gidecek olay (konu öğrenci)."""

    type: str
    fields: list[str] = field(default_factory=list)
    data: dict[str, Any] | None = None


@dataclass(slots=True)
class PushResult:
    """`push()` sonucu: `ok` | `pending` (ağ / 5xx) | `rejected` (kural reddi)."""

    state: str
    error: str = ""
    status: int | None = None
    reason: str | None = None


def guardian_event(slot: int, before: str | None, after: str | None) -> Event | None:
    """Veli telefonu karşılaştırması → `guardian.linked|unlinked|phone_changed`."""
    old, new = (before or "").strip(), (after or "").strip()
    if old == new:
        return None
    if not old:
        kind = "guardian.linked"
    elif not new:
        kind = "guardian.unlinked"
    else:
        kind = "guardian.phone_changed"
    return Event(kind, ["guardians"], {"slot": slot})


def merge_payload(current: str | None, ops: dict[str, Any] | None) -> str | None:
    """Bekleyen kantin gövdesine yeni işlemleri ekler (son değer kazanır)."""
    if not ops:
        return current
    merged: dict[str, Any] = {}
    if current:
        try:
            loaded = json.loads(current)
            merged = loaded if isinstance(loaded, dict) else {}
        except ValueError:
            merged = {}
    for key, value in ops.items():
        if key == "upsert":
            merged["upsert"] = {**(merged.get("upsert") or {}), **value}
        else:
            merged[key] = value
    return json.dumps(merged, ensure_ascii=False, sort_keys=True)


def pending_upsert(row: dict[str, Any] | None) -> dict[str, Any]:
    """Profil satırındaki, kantine henüz yazılmamış `upsert` alanları."""
    if not row or row.get("canteen_sync") != "pending" or not row.get("canteen_payload"):
        return {}
    try:
        payload = json.loads(str(row["canteen_payload"]))
    except ValueError:
        return {}
    upsert = payload.get("upsert") if isinstance(payload, dict) else None
    return dict(upsert) if isinstance(upsert, dict) else {}


class StudentSync:
    """Revizyon, KM-önce yazma, kantine itme ve uzlaştırma — tek yerde."""

    def __init__(self, *, store: Any, canteen: Any, log: Any,
                 outbox: Callable[[], Any] | None = None,
                 on_change: Callable[..., None] | None = None,
                 clock: Callable[[], datetime] = now_utc,
                 today: Callable[[], date] = today_local,
                 delay: float = CANTEEN_DELAY_SECONDS) -> None:
        self._store = store
        self._canteen = canteen
        self._log = log
        self._outbox = outbox or (lambda: None)
        self._on_change = on_change
        self._clock = clock
        self._today = today
        self._delay = delay
        self._profiles = store.table("profile")
        self._rev = store.table("student_rev")
        self._tombstones = store.table("tombstone")
        self._status_log = store.table("status_log")
        #: Profil satırı başına itme kilidi: aynı öğrencinin iki itmesi
        #: (istek + uzlaştırma) birbirinin CAS'ını boşa çıkarmasın.
        self._push_locks: dict[str, asyncio.Lock] = {}
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        #: Uzlaştırma turunda çağrılacak ek işler (ör. planlı pasifleştirme).
        self.due_hooks: list[Callable[[], Any]] = []

    # ================================================================ okuma

    def changed(self, *, canteen: bool) -> None:
        if self._on_change is not None:
            self._on_change(canteen=canteen)

    async def profile(self, kantin_id: str) -> dict[str, Any] | None:
        row = await self._store.fetch_one(
            f"SELECT * FROM {self._profiles} WHERE kantin_id = ?", (str(kantin_id),))
        return dict(row) if row is not None else None

    async def tombstone(self, kantin_id: str) -> dict[str, Any] | None:
        row = await self._store.fetch_one(
            f"SELECT * FROM {self._tombstones} WHERE kantin_id = ?", (str(kantin_id),))
        return dict(row) if row is not None else None

    async def revision(self) -> int:
        row = await self._store.fetch_one(f"SELECT revision FROM {self._rev} WHERE id = 1")
        return int(row["revision"]) if row else 0

    # ================================================================ yazma

    async def bump(self) -> int:
        """Küresel öğrenci revizyonu +1 — İŞLEM İÇİNDE (satır kilidi sıralar)."""
        await self._store.execute(
            f"INSERT OR IGNORE INTO {self._rev} (id, revision) VALUES (1, 0)")
        await self._store.execute(
            f"UPDATE {self._rev} SET revision = revision + 1 WHERE id = 1")
        return await self.revision()

    async def emit(self, kantin_id: str, events: Iterable[Event], *, revision: int | None,
                   origin: str = "km", causation_id: str | None = None) -> None:
        """Olayları outbox'a yazar — açık işlem varsa ona katılır."""
        outbox = self._outbox()
        if outbox is None:
            return
        for event in events:
            await outbox.emit(event.type, ("student", kantin_id), revision=revision,
                              fields=event.fields, data=event.data, origin=origin,
                              causation_id=causation_id)

    async def write(self, kantin_id: str, *, columns: dict[str, Any] | None = None,
                    canteen_ops: dict[str, Any] | None = None,
                    events: Iterable[Event] = (), origin: str = "km",
                    causation_id: str | None = None,
                    log: dict[str, Any] | None = None) -> int:
        """§7 adım 2: profil + revizyon + bekleyen kantin gövdesi + olay, TEK İŞLEM.

        Profil satırı yoksa açılır (kantinde olup KM'de alanı olmayan öğrenci).
        Çağıranın açık işlemine katılır. Yeni revizyonu döner.
        """
        stamp = iso(self._clock())
        async with self._store.transaction():
            revision = await self.bump()
            current = await self.profile(kantin_id)
            values = dict(columns or {})
            values["revision"] = revision
            values["updated_at"] = stamp
            if canteen_ops:
                values["canteen_payload"] = merge_payload(
                    (current or {}).get("canteen_payload")
                    if (current or {}).get("canteen_sync") == "pending" else None,
                    canteen_ops)
                values["canteen_sync"] = "pending"
                if not (current or {}).get("canteen_since") \
                        or (current or {}).get("canteen_sync") != "pending":
                    values["canteen_since"] = stamp
            if current is None:
                names = ["kantin_id", *values]
                await self._store.execute(
                    f"INSERT INTO {self._profiles} ({', '.join(names)}) "
                    f"VALUES ({', '.join('?' for _ in names)})",
                    (str(kantin_id), *values.values()))
            else:
                await self._store.execute(
                    f"UPDATE {self._profiles} SET "
                    + ", ".join(f"{name} = ?" for name in values)
                    + " WHERE kantin_id = ?",
                    (*values.values(), str(kantin_id)))
            if log is not None:
                await self.write_log(kantin_id, **log)
            await self.emit(kantin_id, events, revision=revision, origin=origin,
                            causation_id=causation_id)
        self.changed(canteen=False)
        return revision

    async def write_log(self, kantin_id: str, *, action: str, actor: str = "",
                        reason: str | None = None, effective_at: str | None = None,
                        note: str = "") -> None:
        await self._store.execute(
            f"INSERT INTO {self._status_log} (kantin_id, at, actor, action, reason, "
            f"effective_at, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(kantin_id), iso(self._clock()), actor[:160], action, reason, effective_at,
             note[:500]))

    async def history(self, kantin_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self._store.fetch_all(
            f"SELECT * FROM {self._status_log} WHERE kantin_id = ? ORDER BY id DESC LIMIT ?",
            (str(kantin_id), int(limit)))
        return [{
            "at": local_iso(datetime.fromisoformat(str(row["at"]))),
            "actor": str(row["actor"] or ""),
            "action": str(row["action"]),
            "reason": row["reason"],
            "effectiveAt": row["effective_at"],
            "note": str(row["note"] or ""),
        } for row in rows]

    # ======================================================= kantine itme

    def _lock_for(self, kantin_id: str) -> asyncio.Lock:
        lock = self._push_locks.get(kantin_id)
        if lock is None:
            if len(self._push_locks) > 5000:
                self._push_locks = {key: value for key, value in self._push_locks.items()
                                    if value.locked()}
            lock = self._push_locks[kantin_id] = asyncio.Lock()
        return lock

    async def push(self, kantin_id: str, *, announce: bool = False) -> PushResult:
        """§7 adım 3: bekleyen gövdeyi kantine yazar (idempotent).

        `upsert` önce, `status` sonra. Hepsi başarılıysa satır `ok` olur — ama
        yalnız gövde bu arada DEĞİŞMEDİYSE (CAS): araya giren yeni yazma kendi
        itmesini yapar. `announce`: bekleme sonrası (uzlaştırma) başarıda
        `student.updated` (`canteenSync`, köken `system`) yazılır; Akademi kaydı
        kantinle birlikte yeniden okusun.
        """
        async with self._lock_for(str(kantin_id)):
            row = await self.profile(kantin_id)
            if row is None or row.get("canteen_sync") != "pending":
                return PushResult("ok")
            text = str(row.get("canteen_payload") or "")
            try:
                payload = json.loads(text) if text else {}
            except ValueError:
                payload = {}
            result = await self._apply(kantin_id, payload if isinstance(payload, dict) else {})
            if result.state == "ok":
                await self._settle(kantin_id, text, announce=announce
                                   or int(row.get("canteen_attempts") or 0) > 0)
                self.changed(canteen=True)
            else:
                await self._store.execute(
                    f"UPDATE {self._profiles} SET canteen_attempts = canteen_attempts + 1, "
                    f"canteen_error = ? WHERE kantin_id = ?",
                    (result.error[:300], str(kantin_id)))
                if result.state == "pending":
                    self._log.warning("kantine iletilemedi — uzlaştırma yineleyecek",
                                      student=kantin_id, error=result.error[:200])
            return result

    async def _apply(self, kantin_id: str, payload: dict[str, Any]) -> PushResult:
        upsert = payload.get("upsert")
        status = payload.get("status")
        try:
            if isinstance(upsert, dict) and upsert:
                await self._canteen.upsert_student(kantin_id, dict(upsert))
            if isinstance(status, dict) and status.get("status"):
                try:
                    await self._canteen.set_student_status(
                        kantin_id, str(status["status"]), status.get("reason"),
                        status.get("at"))
                except Exception as failure:
                    # Eski kantin: uç yok. KM kararı geçerli kalır, satış engeli
                    # ancak kantin güncellenince çalışır — kayıt düşülür.
                    if getattr(failure, "reason", None) != "not_supported":
                        raise
                    self._log.warning("kantin durum ucunu desteklemiyor", student=kantin_id)
        except Exception as failure:  # noqa: BLE001 — kantin dışarısı; sınıflandırılır
            return classify(failure)
        return PushResult("ok")

    async def _settle(self, kantin_id: str, payload_text: str, *, announce: bool) -> None:
        async with self._store.transaction():
            row = await self.profile(kantin_id)
            if row is None or str(row.get("canteen_payload") or "") != payload_text:
                return  # araya yeni yazma girdi; onun itmesi bitirir
            values: dict[str, Any] = {"canteen_sync": "ok", "canteen_payload": None,
                                      "canteen_attempts": 0, "canteen_since": None,
                                      "canteen_error": None}
            if announce:
                revision = await self.bump()
                values["revision"] = revision
                values["updated_at"] = iso(self._clock())
            await self._store.execute(
                f"UPDATE {self._profiles} SET "
                + ", ".join(f"{name} = ?" for name in values) + " WHERE kantin_id = ?",
                (*values.values(), str(kantin_id)))
            if announce:
                await self.emit(kantin_id, [Event("student.updated", ["canteenSync"])],
                                revision=values["revision"], origin="system")

    async def clear_pending(self, kantin_id: str) -> None:
        """Kantinin reddettiği gövdeyi bırakır (telafi yazıldıktan sonra)."""
        await self._store.execute(
            f"UPDATE {self._profiles} SET canteen_sync = 'ok', canteen_payload = NULL, "
            f"canteen_attempts = 0, canteen_since = NULL, canteen_error = NULL "
            f"WHERE kantin_id = ?", (str(kantin_id),))

    # ========================================================== mezar taşı

    async def push_tombstone(self, kantin_id: str) -> PushResult:
        row = await self.tombstone(kantin_id)
        if row is None or row.get("canteen_sync") != "pending":
            return PushResult("ok")
        try:
            await self._canteen.delete_student(str(kantin_id))
            result = PushResult("ok")
        except Exception as failure:  # noqa: BLE001 — kantin dışarısı
            result = classify(failure)
            if result.reason == "student_not_found":
                result = PushResult("ok")
        if result.state == "ok":
            await self._store.execute(
                f"UPDATE {self._tombstones} SET canteen_sync = 'ok', canteen_error = NULL "
                f"WHERE kantin_id = ?", (str(kantin_id),))
            self.changed(canteen=True)
        else:
            await self._store.execute(
                f"UPDATE {self._tombstones} SET canteen_attempts = canteen_attempts + 1, "
                f"canteen_error = ? WHERE kantin_id = ?",
                (result.error[:300], str(kantin_id)))
        return result

    # ========================================================= uzlaştırma

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(
                self._loop(), name="km-bbd-students-reconcile")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    def wake(self) -> None:
        """Kantin geri geldi (ör. kantinden olay ulaştı): turu beklemeden koş."""
        self._wake.set()

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=RECONCILE_SECONDS)
            except TimeoutError:
                pass
            self._wake.clear()
            try:
                await self.reconcile_once()
            except asyncio.CancelledError:
                raise
            except Exception as failure:  # noqa: BLE001 — döngü ölmez (K7)
                self._log.warning("öğrenci uzlaştırması patladı", error=str(failure))

    async def reconcile_once(self) -> dict[str, int]:
        """Bir tur: planlı işler, bekleyen profiller, bekleyen silmeler, budama."""
        for hook in list(self.due_hooks):
            result = hook()
            if asyncio.iscoroutine(result):
                await result
        pushed = failed = 0
        now = self._clock()
        rows = await self._store.fetch_all(
            f"SELECT kantin_id, canteen_attempts, canteen_since FROM {self._profiles} "
            f"WHERE canteen_sync = 'pending' ORDER BY canteen_since LIMIT ?",
            (RECONCILE_BATCH,))
        first = True
        for row in rows:
            if not self._due(row, now):
                continue
            if not first:
                await asyncio.sleep(self._delay)
            first = False
            result = await self.push(str(row["kantin_id"]), announce=True)
            if result.state == "ok":
                pushed += 1
            else:
                failed += 1
                if result.state == "pending":
                    break  # kantin yok: sırayı zorlamaya gerek yok
        tombs = await self._store.fetch_all(
            f"SELECT kantin_id, canteen_attempts, deleted_at AS canteen_since "
            f"FROM {self._tombstones} WHERE canteen_sync = 'pending' LIMIT ?",
            (RECONCILE_BATCH,))
        for row in tombs:
            if not self._due(row, now):
                continue
            await asyncio.sleep(self._delay if not first else 0)
            first = False
            result = await self.push_tombstone(str(row["kantin_id"]))
            if result.state == "pending":
                break
        cutoff = iso(now - timedelta(days=TOMBSTONE_DAYS))
        await self._store.execute(
            f"DELETE FROM {self._tombstones} WHERE deleted_at < ? AND canteen_sync = 'ok'",
            (cutoff,))
        return {"pushed": pushed, "failed": failed}

    def _due(self, row: dict[str, Any], now: datetime) -> bool:
        attempts = int(row.get("canteen_attempts") or 0)
        if attempts < BACKOFF_AFTER:
            return True
        # Çok kez reddedilmiş kayıt her turda değil, 10 dakikada bir denenir.
        return int(now.timestamp()) % int(BACKOFF_SECONDS) < int(RECONCILE_SECONDS)

    async def sync_status(self) -> dict[str, Any]:
        """Panel özeti: bekleyen, 24 saati geçen, bekleyen silmeler."""
        now = self._clock()
        rows = await self._store.fetch_all(
            f"SELECT kantin_id, canteen_since, canteen_attempts, canteen_error "
            f"FROM {self._profiles} WHERE canteen_sync = 'pending' ORDER BY canteen_since")
        stale_cut = iso(now - STALE_AFTER)
        tombs = await self._store.fetch_all(
            f"SELECT kantin_id, deleted_at, canteen_attempts, canteen_error "
            f"FROM {self._tombstones} WHERE canteen_sync = 'pending' ORDER BY deleted_at")
        return {
            "pending": len(rows),
            "stale": [{"studentId": str(row["kantin_id"]),
                       "since": row["canteen_since"],
                       "attempts": int(row["canteen_attempts"] or 0),
                       "error": row["canteen_error"]}
                      for row in rows if str(row["canteen_since"] or "") < stale_cut],
            "items": [{"studentId": str(row["kantin_id"]), "since": row["canteen_since"],
                       "attempts": int(row["canteen_attempts"] or 0),
                       "error": row["canteen_error"]} for row in rows[:200]],
            "deletesPending": [{"studentId": str(row["kantin_id"]),
                                "deletedAt": row["deleted_at"],
                                "attempts": int(row["canteen_attempts"] or 0),
                                "error": row["canteen_error"]} for row in tombs],
        }


def classify(failure: BaseException) -> PushResult:
    """Kantin hatası → `pending` (yeniden denenir) ya da `rejected` (kural reddi)."""
    status = getattr(failure, "status", None)
    reason = getattr(failure, "reason", None)
    text = f"{reason or 'canteen_error'}: {str(failure)[:200]}"
    if isinstance(status, int) and status in REJECT_STATUSES:
        return PushResult("rejected", text, status, reason)
    if reason == "student_not_found":
        return PushResult("rejected", text, status, reason)
    return PushResult("pending", text, status if isinstance(status, int) else None, reason)
