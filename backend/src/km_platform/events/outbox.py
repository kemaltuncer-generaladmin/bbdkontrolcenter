"""Platform yeteneği `events.outbox` — kalıcı olay outbox'u (ADR 0029).

Sözleşme: bbd-akademi `docs/integrations/km-merkezi-senkron.md` §2 (zarf ve
katalog) ve §3.1 (outbox). Zarf alanları API sözleşmesindeki `KmEvent` ile
BİREBİRDİR (camelCase):

    {"eventId", "seq", "type", "occurredAt", "subject": {"kind", "id"},
     "revision", "fields", "data", "origin", "causationId"}

YÜZEY (modüller registry'den `events.outbox` olarak çözer, K3):

    await emit(type, subject, *, revision=None, fields=(), data=None,
               origin="km", causation_id=None) -> event_id
    await read_after(seq, limit=100, types=None) -> [KmEvent]
    await last_seq() -> int
    await wait_after(seq, timeout) -> bool      # süreç içi; veritabanı YOKLAMAZ
    await expired(after) -> bool                 # `after` budanmış bölgede mi
    await register_consumer(name) / unregister_consumer(name)
    await consumer_seq(name, seq=None) -> int    # oku ya da ilerlet
    await prune() -> int                         # gece budaması
    on_close(hook)                               # kapanışta boşaltma (bakiye birleştirme)

İŞLEM. `emit` AÇIK İŞLEMİN İÇİNDE çağrılır ve olayı o işleme bağlar: iş
yazmasıyla olay ya birlikte kalıcı olur ya birlikte kaybolur. İşlem yoksa
`emit` kendi kısa işlemini açar. Satır commit'ten HEMEN ÖNCE ve OUTBOX KİLİDİ
ALTINDA yazılır; kilit commit'ten sonra bırakılır. Böylece `seq` sırası commit
sırasıdır: tüketici imleci, daha küçük `seq`'li bir işlem sonradan commit
edildiği için bir olayı atlayamaz. (KM tek süreçtir; çok süreçli kurulumda
kilit `pg_advisory_xact_lock` olur — ADR 0029.)

KİŞİSEL VERİ YOK (A6). Olay kimlik + revizyon + alan ADI taşır. `data` yalnız
küçük durum nesnesidir; ad, telefon ve kod anahtarları kodla reddedilir.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import structlog

from km_core.store.base import StoreLike
from km_core.store.transaction import Transaction
from km_platform.events.schema import CONSUMER_TABLE, OUTBOX_TABLE

log = structlog.get_logger("platform.events.outbox")

_ISTANBUL = ZoneInfo("Europe/Istanbul")

#: Kaynak sistemler (sözleşme `KmEvent.origin`).
ORIGINS = ("km", "canteen", "akademi", "tablet", "system")

#: Konu türleri (sözleşme `KmEventSubject.kind`).
SUBJECT_KINDS = ("student", "groups", "bell", "tablet_profile", "institution")

#: Olay türü biçimi: `alan.olay` (katalog KM ile büyür; alıcı bilinmeyeni yok sayar).
_TYPE = re.compile(r"^[a-z][a-z_]*\.[a-z][a-z_]*$")

#: `data` içinde ASLA bulunamayacak anahtarlar (A6 — kişisel veri / sır).
FORBIDDEN_DATA_KEYS = frozenset({
    "name", "firstname", "lastname", "displayname", "fullname", "parentname",
    "phone", "parentphone", "studentphone", "msisdn", "email",
    "accesscode", "code", "pin", "password", "token", "secret",
})

#: `data` nesnesinin JSON üst sınırı (bayt). Olay ince kalır.
DATA_MAX_BYTES = 2048

#: Bir okumada en çok satır.
READ_MAX = 500

#: Saklama (gün): tüketicilerin geçtiği ve bundan eski olaylar budanır
#: ("14 gün ya da tüketicilerin geçtiği seq — hangisi uzunsa").
RETENTION_DAYS = 14

#: Sert üst sınır (gün): takılmış ya da kapatılmış bir tüketici outbox'u
#: sonsuza dek büyütmesin. Bunu aşan tüketici tam uzlaştırma yapmalıdır
#: (`directory.resync_required`, `410 events_expired`).
HARD_RETENTION_DAYS = 30

#: Budama döngüsünün kontrol aralığı (sn) ve iki budama arası en az süre (sn).
PRUNE_CHECK_SECONDS = 3600.0
PRUNE_EVERY_SECONDS = 20 * 3600.0

#: Budama ilerlemesinin tutulduğu ayrılmış tüketici adı.
PRUNED_MARK = "#pruned"

_STATE_PENDING = "events.outbox.pending"
_STATE_LOCKED = "events.outbox.locked"
_STATE_SEQ = "events.outbox.seq"

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class OutboxError(ValueError):
    """Olay sözleşmeye uymuyor (tür, konu, köken ya da kişisel veri)."""


def new_ulid(now_ms: int | None = None) -> str:
    """ULID: 48 bit milisaniye + 80 bit rastgele, Crockford base32 (26 karakter)."""
    stamp = int(time.time() * 1000) if now_ms is None else int(now_ms)
    value = (stamp << 80) | int.from_bytes(os.urandom(10), "big")
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def _now() -> datetime:
    return datetime.now(UTC)


def _occurred(moment: datetime) -> str:
    return moment.astimezone(_ISTANBUL).isoformat(timespec="milliseconds")


def _subject(subject: Any) -> tuple[str, str | None]:
    if isinstance(subject, Mapping):
        kind, ident = subject.get("kind"), subject.get("id")
    elif isinstance(subject, (tuple, list)) and len(subject) == 2:
        kind, ident = subject[0], subject[1]
    else:
        raise OutboxError("Olay konusu (kind, id) olmalı.")
    kind = str(kind or "")
    if kind not in SUBJECT_KINDS:
        raise OutboxError(f"Bilinmeyen konu türü: {kind!r}")
    return kind, (str(ident) if ident not in (None, "") else None)


def _clean_data(data: Mapping[str, Any] | None) -> str | None:
    if data is None:
        return None
    if not isinstance(data, Mapping):
        raise OutboxError("`data` nesne olmalı.")

    def check(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, inner in value.items():
                if str(key).replace("_", "").lower() in FORBIDDEN_DATA_KEYS:
                    raise OutboxError(f"`data` kişisel veri taşıyamaz: {key!r} (A6)")
                check(inner)
        elif isinstance(value, (list, tuple)):
            for inner in value:
                check(inner)

    check(data)
    text = json.dumps(dict(data), ensure_ascii=False, separators=(",", ":"), default=str)
    if len(text.encode("utf-8")) > DATA_MAX_BYTES:
        raise OutboxError(f"`data` en çok {DATA_MAX_BYTES} bayt olabilir.")
    return text


def _type_matcher(types: Iterable[str] | None) -> Callable[[str], bool] | None:
    """`student.*,canteen.balance_changed` → eşleştirici; boşsa None (hepsi)."""
    patterns = [str(item).strip() for item in (types or []) if str(item).strip()]
    if not patterns:
        return None
    exact = {item for item in patterns if not item.endswith("*")}
    prefixes = tuple(item[:-1] for item in patterns if item.endswith("*"))
    return lambda name: name in exact or (bool(prefixes) and name.startswith(prefixes))


def envelope(row: Mapping[str, Any]) -> dict[str, Any]:
    """Outbox satırı → sözleşmenin `KmEvent` zarfı."""
    fields = json.loads(str(row.get("fields") or "[]"))
    data = row.get("data")
    revision = row.get("revision")
    return {
        "eventId": str(row["event_id"]),
        "seq": int(row["seq"]),
        "type": str(row["type"]),
        "occurredAt": str(row["occurred_at"]),
        "subject": {"kind": str(row["subject_kind"]),
                    "id": str(row["subject_id"]) if row.get("subject_id") else None},
        "revision": int(revision) if revision is not None else None,
        "fields": [str(item) for item in fields] if isinstance(fields, list) else [],
        "data": json.loads(str(data)) if data else None,
        "origin": str(row["origin"]),
        "causationId": str(row["causation_id"]) if row.get("causation_id") else None,
    }


class EventOutbox:
    """`events.outbox` yeteneğinin uygulaması (süreç başına tek nesne)."""

    def __init__(self, store: StoreLike, *, retention_days: int = RETENTION_DAYS,
                 hard_retention_days: int = HARD_RETENTION_DAYS,
                 clock: Callable[[], datetime] = _now) -> None:
        self._store = store
        self._retention = timedelta(days=retention_days)
        self._hard_retention = timedelta(days=max(hard_retention_days, retention_days))
        self._clock = clock
        #: Commit sırasını koruyan süreç içi kilit (yalnız commit anında tutulur).
        self._lock = asyncio.Lock()
        self._last_seq = 0
        self._changed = asyncio.Event()
        self._close_hooks: list[Callable[[], Awaitable[None] | None]] = []
        self._prune_task: asyncio.Task[None] | None = None
        self._last_prune = 0.0
        self._closed = False

    # ---------------------------------------------------------- yaşam döngüsü

    async def open(self, *, prune_loop: bool = True) -> None:
        row = await self._store.fetch_one(f"SELECT MAX(seq) AS seq FROM {OUTBOX_TABLE}")
        self._last_seq = int(row["seq"]) if row and row.get("seq") is not None else 0
        if prune_loop:
            self._prune_task = asyncio.get_running_loop().create_task(
                self._prune_loop(), name="km-events-prune")

    def on_close(self, hook: Callable[[], Awaitable[None] | None]) -> None:
        """Kapanışta koşacak boşaltma (ör. bakiye birleştirme tamponu)."""
        self._close_hooks.append(hook)

    async def close(self) -> None:
        """Kapanış kancalarını koşar, bekleyenleri uyandırır, budamayı durdurur."""
        for hook in list(self._close_hooks):
            try:
                result = hook()
                if result is not None:
                    await result
            except Exception as failure:  # noqa: BLE001 — kapanış devam etmeli (K7)
                log.warning("outbox kapanış kancası patladı", error=str(failure))
        self._closed = True
        self._notify()
        if self._prune_task is not None:
            self._prune_task.cancel()
            try:
                await self._prune_task
            except asyncio.CancelledError:
                pass
            self._prune_task = None

    # ------------------------------------------------------------------ yazma

    async def emit(self, type: str, subject: Any, *,
                   revision: int | None = None, fields: Sequence[str] = (),
                   data: Mapping[str, Any] | None = None, origin: str = "km",
                   causation_id: str | None = None) -> str:
        """Olayı AÇIK İŞLEME bağlar; işlem yoksa kendi işlemini açar. `eventId` döner."""
        name = str(type or "")
        if not _TYPE.match(name):
            raise OutboxError(f"Geçersiz olay türü: {name!r}")
        kind, ident = _subject(subject)
        if origin not in ORIGINS:
            raise OutboxError(f"Geçersiz köken: {origin!r}")
        clean_fields = []
        for item in fields or ():
            text = str(item)
            if text and text not in clean_fields:
                clean_fields.append(text)
        moment = self._clock()
        row = {
            "event_id": new_ulid(int(moment.timestamp() * 1000)),
            "type": name,
            "subject_kind": kind,
            "subject_id": ident,
            "revision": int(revision) if revision is not None else None,
            "fields": json.dumps(clean_fields, ensure_ascii=False),
            "data": _clean_data(data),
            "origin": origin,
            "causation_id": (str(causation_id)[:200] if causation_id else None),
            "occurred_at": _occurred(moment),
            "created_at": moment.astimezone(UTC).isoformat(timespec="milliseconds"),
        }

        transaction = self._store.current_transaction()
        if transaction is None:
            async with self._store.transaction() as own:
                self._attach(own, row)
            return str(row["event_id"])
        self._attach(transaction, row)
        return str(row["event_id"])

    def _attach(self, transaction: Transaction, row: dict[str, Any]) -> None:
        pending = transaction.state.get(_STATE_PENDING)
        if pending is None:
            pending = []
            transaction.state[_STATE_PENDING] = pending
            transaction.before_commit(lambda: self._write(transaction))
            transaction.after_commit(lambda: self._committed(transaction))
            transaction.after_end(lambda: self._release(transaction))
        pending.append(row)

    async def _write(self, transaction: Transaction) -> None:
        """Commit'ten hemen önce, outbox kilidi altında satırları yazar."""
        rows: list[dict[str, Any]] = transaction.state.get(_STATE_PENDING) or []
        if not rows:
            return
        await self._lock.acquire()
        transaction.state[_STATE_LOCKED] = True
        top = 0
        for row in rows:
            inserted = await self._store.fetch_one(
                f"INSERT INTO {OUTBOX_TABLE} (event_id, type, subject_kind, subject_id, "
                "revision, fields, data, origin, causation_id, occurred_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING seq",
                (row["event_id"], row["type"], row["subject_kind"], row["subject_id"],
                 row["revision"], row["fields"], row["data"], row["origin"],
                 row["causation_id"], row["occurred_at"], row["created_at"]),
            )
            if inserted is not None:
                top = max(top, int(inserted["seq"]))
        transaction.state[_STATE_SEQ] = top

    def _committed(self, transaction: Transaction) -> None:
        top = int(transaction.state.get(_STATE_SEQ) or 0)
        self._last_seq = max(self._last_seq, top)
        self._notify()

    def _release(self, transaction: Transaction) -> None:
        if transaction.state.pop(_STATE_LOCKED, False):
            self._lock.release()

    def _notify(self) -> None:
        event, self._changed = self._changed, asyncio.Event()
        event.set()

    # ------------------------------------------------------------------ okuma

    async def last_seq(self) -> int:
        return self._last_seq

    async def read_after(self, seq: int, limit: int = 100,
                         types: Iterable[str] | None = None) -> list[dict[str, Any]]:
        """`seq`'ten büyük olaylar, `seq` sırasıyla. `types` süzgeci `student.*` destekler."""
        size = max(1, min(int(limit), READ_MAX))
        rows = await self._store.fetch_all(
            f"SELECT * FROM {OUTBOX_TABLE} WHERE seq > ? ORDER BY seq LIMIT ?",
            (int(seq), size),
        )
        matcher = _type_matcher(types)
        events = [envelope(row) for row in rows]
        if matcher is None:
            return events
        return [event for event in events if matcher(event["type"])]

    async def scan_after(self, seq: int, limit: int = 100,
                         types: Iterable[str] | None = None) -> tuple[list[dict[str, Any]], int]:
        """`read_after` + taranan en büyük `seq` (süzülen olaylar dahil).

        Tür süzgeçli okuyan istemci imlecini taranan noktaya taşır; süzgece
        uymayan olaylar yüzünden aynı satırları yeniden okumaz.
        """
        size = max(1, min(int(limit), READ_MAX))
        rows = await self._store.fetch_all(
            f"SELECT * FROM {OUTBOX_TABLE} WHERE seq > ? ORDER BY seq LIMIT ?",
            (int(seq), size),
        )
        matcher = _type_matcher(types)
        events = [envelope(row) for row in rows]
        scanned = events[-1]["seq"] if events else int(seq)
        if matcher is not None:
            events = [event for event in events if matcher(event["type"])]
        return events, scanned

    async def wait_after(self, seq: int, timeout: float) -> bool:
        """`seq`'ten büyük bir olay commit edilene ya da süre dolana kadar bekler.

        Veritabanı YOKLANMAZ: commit kancası süreç içi bir olayı tetikler.
        Yeni olay varsa True.
        """
        deadline = time.monotonic() + max(0.0, float(timeout))
        while self._last_seq <= int(seq) and not self._closed:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            changed = self._changed
            try:
                await asyncio.wait_for(changed.wait(), timeout=remaining)
            except TimeoutError:
                return self._last_seq > int(seq)
        return self._last_seq > int(seq)

    async def expired(self, after: int) -> bool:
        """`after`'dan sonraki olaylardan bazıları budandı mı (410 events_expired)."""
        return int(after) < await self._pruned_through()

    async def _pruned_through(self) -> int:
        row = await self._store.fetch_one(
            f"SELECT last_seq FROM {CONSUMER_TABLE} WHERE name = ?", (PRUNED_MARK,))
        return int(row["last_seq"]) if row else 0

    # -------------------------------------------------------------- tüketiciler

    async def register_consumer(self, name: str, *, start: int | None = None) -> int:
        """Tüketiciyi kaydeder (varsa dokunmaz). İlk kayıtta imleç `start` ya da
        bugünkü `last_seq`'tir — geçmiş olaylar baştan iletilmez."""
        key = _consumer_name(name)
        row = await self._store.fetch_one(
            f"SELECT last_seq FROM {CONSUMER_TABLE} WHERE name = ?", (key,))
        if row is not None:
            return int(row["last_seq"])
        position = self._last_seq if start is None else int(start)
        await self._store.execute(
            f"INSERT OR IGNORE INTO {CONSUMER_TABLE} (name, last_seq, updated_at) "
            "VALUES (?, ?, ?)",
            (key, position, _stamp(self._clock())),
        )
        return position

    async def unregister_consumer(self, name: str) -> None:
        await self._store.execute(
            f"DELETE FROM {CONSUMER_TABLE} WHERE name = ?", (_consumer_name(name),))

    async def consumer_seq(self, name: str, seq: int | None = None) -> int:
        """İmleci okur; `seq` verilirse ilerletir (geri gitmez)."""
        key = _consumer_name(name)
        if seq is not None:
            await self._store.execute(
                f"UPDATE {CONSUMER_TABLE} SET last_seq = ?, updated_at = ? "
                "WHERE name = ? AND last_seq < ?",
                (int(seq), _stamp(self._clock()), key, int(seq)),
            )
        row = await self._store.fetch_one(
            f"SELECT last_seq FROM {CONSUMER_TABLE} WHERE name = ?", (key,))
        return int(row["last_seq"]) if row else 0

    async def consumers(self) -> dict[str, int]:
        rows = await self._store.fetch_all(
            f"SELECT name, last_seq FROM {CONSUMER_TABLE} WHERE name <> ?", (PRUNED_MARK,))
        return {str(row["name"]): int(row["last_seq"]) for row in rows}

    # ------------------------------------------------------------------ budama

    async def prune(self) -> int:
        """14 günden eski VE bütün tüketicilerin geçtiği olaylar; ayrıca sert sınırı
        (30 gün) aşan her olay. En yeni satır kalır (`seq` sayacı yeniden
        başlatılmadan okunabilsin). Silinen satır sayısını döner."""
        now = self._clock().astimezone(UTC)
        soft = (now - self._retention).isoformat(timespec="milliseconds")
        hard = (now - self._hard_retention).isoformat(timespec="milliseconds")
        consumers = await self.consumers()
        floor = min(consumers.values()) if consumers else self._last_seq
        top = self._last_seq
        before = await self._count()
        async with self._store.transaction():
            await self._store.execute(
                f"DELETE FROM {OUTBOX_TABLE} WHERE seq < ? AND "
                "((created_at < ? AND seq <= ?) OR created_at < ?)",
                (top, soft, floor, hard),
            )
            low = await self._store.fetch_one(f"SELECT MIN(seq) AS seq FROM {OUTBOX_TABLE}")
            through = (int(low["seq"]) - 1) if low and low.get("seq") is not None else top
            await self._store.execute(
                f"INSERT OR IGNORE INTO {CONSUMER_TABLE} (name, last_seq, updated_at) "
                "VALUES (?, 0, ?)", (PRUNED_MARK, _stamp(now)))
            await self._store.execute(
                f"UPDATE {CONSUMER_TABLE} SET last_seq = ?, updated_at = ? "
                "WHERE name = ? AND last_seq < ?",
                (through, _stamp(now), PRUNED_MARK, through))
        removed = before - await self._count()
        if removed:
            log.info("outbox budandı", removed=removed, through=through)
        return removed

    async def _count(self) -> int:
        row = await self._store.fetch_one(f"SELECT COUNT(*) AS n FROM {OUTBOX_TABLE}")
        return int(row["n"]) if row else 0

    async def oldest_pending_age(self, after: int) -> float | None:
        """`after`'dan sonraki ilk olayın yaşı (sn); yoksa None."""
        row = await self._store.fetch_one(
            f"SELECT created_at FROM {OUTBOX_TABLE} WHERE seq > ? ORDER BY seq LIMIT 1",
            (int(after),))
        if row is None:
            return None
        moment = datetime.fromisoformat(str(row["created_at"]))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return max(0.0, (self._clock() - moment).total_seconds())

    async def _prune_loop(self) -> None:
        while True:
            await asyncio.sleep(PRUNE_CHECK_SECONDS)
            if time.monotonic() - self._last_prune < PRUNE_EVERY_SECONDS:
                continue
            hour = self._clock().astimezone(_ISTANBUL).hour
            if not 2 <= hour < 5:  # gece
                continue
            try:
                await self.prune()
                self._last_prune = time.monotonic()
            except asyncio.CancelledError:
                raise
            except Exception as failure:  # noqa: BLE001 — budama döngüsü ölmez (K7)
                log.warning("outbox budaması patladı", error=str(failure))


def _consumer_name(name: str) -> str:
    key = str(name or "").strip()
    if not key or key == PRUNED_MARK or len(key) > 80:
        raise OutboxError(f"Geçersiz tüketici adı: {name!r}")
    return key


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")
