"""Kantin → KM olay alıcısı (bbd-akademi `docs/integrations/km-merkezi-senkron.md` §6).

Kantin kendi outbox'una (`km_events`) yazdığı olayları iki yoldan getirir:

  · İTME: `POST /api/bbd_canteen_api/v1/events` (KM servis anahtarı, rol
    `canteen_service`) — gövde `{"source": "canteen", "sentAt", "events":
    [{id, type, opaqueId, payload, createdAt}]}`;
  · ÇEKME (yedek): açılışta ve 30 sn'de bir `GET /api/km/events?after=` —
    aynı öğe biçimi, aynı işleme yolu.

Her olay TEK İŞLEMDE işlenir: `inbox(canteen_seq)` tekilleştirmesi + KM
outbox'una karşılığı (köken `canteen`, `causationId = "CNT-<id>"`):

    student.upserted          → student.created (kasadan eklenen: taslak profil)
                                 | student.updated — `bbd_students.sync` yazar (K5)
    student.controls_changed  → canteen.controls_changed (fields aynen)
    payment_request.changed   → canteen.payment_request_changed {ref, status}
    balance.changed           → canteen.balance_changed — ÖĞRENCİ BAŞINA 1 SN
                                 BİRLEŞTİRİLİR (bellek, en çok 2.000 anahtar,
                                 en büyük `ledgerSeq` kazanır, kapanışta boşalır)
    (bilinmeyen tür)          → kutuya yazılır, yok sayılır (ileri uyum)

YANKI BASTIRMA (§6.2): KM'nin kantine az önce (2 sn) yazdığı öğrenci / kontrol
alanları kantinden olay olarak dönerse yeni KM olayı üretilmez (`canteen.api`
istemcisinin yazma belleği; öğrenci ve kontrol yazan her KM yolu olayını kendisi
yazar). Ödeme talebinde bellek KENDİLİĞİNDEN tutulmaz — talebi açıp olayını
kendisi yazan (mobil geçit) `remember_write(id, "payment_request", [ref])` ile
bildirir; KM ekranının açtığı talep kantin olayıyla duyurulur. Bakiye yankısı
yoktur (KM bakiye yazmaz).

Öğrenci / kontrol olayında öğrenci dizininin önbelleği HEMEN bırakılır
(`bbd_students.sync.invalidate`). Kantinden olay gelmesi kantinin ayakta
olduğunu gösterir: öğrenci uzlaştırması uyandırılır (`wake`).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

#: Bakiye birleştirme penceresi (sn) ve bellekteki en çok öğrenci.
COALESCE_SECONDS = 1.0
COALESCE_MAX_KEYS = 2000

#: Çekme aralığı (sn); uç yoksa (eski kantin) bu aralıkla yeniden yoklanır.
PULL_SECONDS = 30.0
PULL_UNSUPPORTED_SECONDS = 600.0
PULL_PAGE = 500

#: Kutu saklama (gün). Kantin itilmiş satırı 7 gün tutar; çekme imleci MAX'tır.
INBOX_DAYS = 14

BALANCE_REASONS = ("sale", "reversal", "collection", "payment", "adjustment")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class CanteenEvents:
    """Alıcı + bakiye birleştirici + çekme döngüsü."""

    def __init__(self, *, store: Any, canteen: Any, log: Any,
                 outbox: Callable[[], Any], students: Callable[[], Any],
                 coalesce_seconds: float = COALESCE_SECONDS,
                 max_keys: int = COALESCE_MAX_KEYS) -> None:
        self._store = store
        self._canteen = canteen
        self._log = log
        self._outbox = outbox
        self._students = students
        self._coalesce = coalesce_seconds
        self._max_keys = max_keys
        self._inbox = store.table("inbox")
        #: Aynı olayın itme ve çekmeyle eşzamanlı işlenmesini sıralar.
        self._lock = asyncio.Lock()
        self._balances: dict[str, dict[str, Any]] = {}
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._flushing: set[asyncio.Task[Any]] = set()
        self._pull_task: asyncio.Task[None] | None = None
        self.supported = True
        self.last_pull_at: str | None = None

    # ================================================================ alma

    async def receive(self, items: list[Any]) -> dict[str, int]:
        """Olay listesini işler (itme ya da çekme). `{received, duplicates, lastId}`."""
        clean = [item for item in items if isinstance(item, dict) and _seq(item) > 0]
        clean.sort(key=_seq)
        received = duplicates = 0
        last = 0
        touched_students = False
        async with self._lock:
            for item in clean:
                last = max(last, _seq(item))
                try:
                    fresh = await self._one(item)
                except Exception as failure:  # noqa: BLE001 — tek olay akışı tıkamaz
                    # İşlenemeyen olay kutuya yazılmaz: çekme bir sonraki turda yineler.
                    self._log.warning("kantin olayı işlenemedi", seq=_seq(item),
                                      type=str(item.get("type")), error=str(failure)[:200])
                    continue
                if fresh:
                    received += 1
                    touched_students = touched_students or item.get("type") != "balance.changed"
                else:
                    duplicates += 1
        students = self._students()
        if students is not None and clean:
            if touched_students:
                students.invalidate()
            students.wake()
        return {"received": received, "duplicates": duplicates, "lastId": last}

    async def _mark(self, item: dict[str, Any]) -> bool:
        """Kutuya yazar; daha önce alınmışsa False (işlemin İÇİNDE)."""
        seq = _seq(item)
        if await self._store.fetch_one(
                f"SELECT canteen_seq FROM {self._inbox} WHERE canteen_seq = ?",
                (seq,)) is not None:
            return False
        await self._store.execute(
            f"INSERT INTO {self._inbox} (canteen_seq, type, opaque_id, created_at, received_at) "
            f"VALUES (?, ?, ?, ?, ?)",
            (seq, str(item.get("type") or "")[:64], _opaque(item) or None,
             str(item.get("createdAt") or "")[:40] or None, _now_iso()))
        return True

    async def _one(self, item: dict[str, Any]) -> bool:
        kind = str(item.get("type") or "")
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        opaque = _opaque(item)
        causation = f"CNT-{_seq(item)}"
        fields = [str(name) for name in (payload.get("fields") or []) if str(name)]

        if kind == "student.upserted" and opaque:
            created = bool(payload.get("created"))
            if not created and self._echo(opaque, "student", fields):
                return await self._mark_only(item)
            students = self._students()
            if students is not None:
                fresh = True

                async def within() -> bool:
                    nonlocal fresh
                    fresh = await self._mark(item)
                    return fresh

                await students.student_upserted(opaque, fields, created=created,
                                                causation_id=causation, within=within)
                return fresh
            return await self._mark_and_emit(
                item, "student.created" if created else "student.updated",
                opaque, fields, None, causation)

        if kind == "student.controls_changed" and opaque:
            if self._echo(opaque, "controls", fields):
                return await self._mark_only(item)
            return await self._mark_and_emit(item, "canteen.controls_changed", opaque, fields,
                                             None, causation)

        if kind == "payment_request.changed" and opaque:
            data = {"ref": str(payload.get("ref") or "")[:64],
                    "status": str(payload.get("status") or "").upper()[:16]}
            if data["status"] == "PENDING" and self._echo(opaque, "payment_request",
                                                          [data["ref"]]):
                return await self._mark_only(item)
            return await self._mark_and_emit(item, "canteen.payment_request_changed", opaque,
                                             ["paymentRequests"], data, causation)

        if kind == "balance.changed" and opaque:
            fresh = await self._mark_only(item)
            if fresh:
                self._add_balance(opaque, payload, item, causation)
            return fresh

        if kind:
            self._log.info("bilinmeyen kantin olayı yok sayıldı", type=kind[:64])
        return await self._mark_only(item)

    async def _mark_only(self, item: dict[str, Any]) -> bool:
        async with self._store.transaction():
            return await self._mark(item)

    async def _mark_and_emit(self, item: dict[str, Any], kind: str, opaque: str,
                             fields: list[str], data: dict[str, Any] | None,
                             causation: str) -> bool:
        async with self._store.transaction():
            if not await self._mark(item):
                return False
            outbox = self._outbox()
            if outbox is not None:
                await outbox.emit(kind, ("student", opaque), fields=fields, data=data,
                                  origin="canteen", causation_id=causation)
        return True

    def _echo(self, opaque: str, kind: str, fields: list[str]) -> bool:
        check = getattr(self._canteen, "recently_written", None)
        if check is None or not fields:
            return False
        return bool(check(opaque, kind, fields))

    # ==================================================== bakiye birleştirme

    def _add_balance(self, opaque: str, payload: dict[str, Any], item: dict[str, Any],
                     causation: str) -> None:
        ledger = _int(payload.get("ledgerSeq"))
        reason = str(payload.get("reason") or "")
        entry = self._balances.get(opaque)
        if entry is None:
            entry = {"balance": _int(payload.get("balance")), "ledgerSeq": ledger,
                     "reasons": set(), "at": item.get("createdAt"), "causation": causation}
            self._balances[opaque] = entry
        elif ledger >= int(entry["ledgerSeq"]):
            entry.update({"balance": _int(payload.get("balance")), "ledgerSeq": ledger,
                          "at": item.get("createdAt"), "causation": causation})
        if reason in BALANCE_REASONS:
            entry["reasons"].add(reason)
        if len(self._balances) >= self._max_keys:
            self._spawn(self.flush_all())
            return
        if opaque not in self._timers:
            loop = asyncio.get_running_loop()
            self._timers[opaque] = loop.call_later(
                self._coalesce, lambda key=opaque: self._spawn(self._flush(key)))

    def _spawn(self, coroutine: Any) -> None:
        task = asyncio.get_running_loop().create_task(coroutine)
        self._flushing.add(task)
        task.add_done_callback(self._flushing.discard)

    async def _flush(self, opaque: str) -> None:
        timer = self._timers.pop(opaque, None)
        if timer is not None:
            timer.cancel()
        entry = self._balances.pop(opaque, None)
        if entry is None:
            return
        outbox = self._outbox()
        if outbox is None:
            return
        try:
            await outbox.emit(
                "canteen.balance_changed", ("student", opaque), fields=["balance"],
                data={"balance": int(entry["balance"]), "ledgerSeq": int(entry["ledgerSeq"]),
                      "reasons": sorted(entry["reasons"]), "at": entry["at"]},
                origin="canteen", causation_id=str(entry["causation"]))
        except Exception as failure:  # noqa: BLE001 — 15 dk bakiye uzlaştırması düzeltir
            self._log.warning("bakiye olayı yazılamadı", error=str(failure)[:200])

    async def flush_all(self) -> None:
        """Bekleyen bütün bakiye olaylarını hemen yazar (kapanış, tampon dolu)."""
        for opaque in list(self._balances):
            await self._flush(opaque)

    async def close(self) -> None:
        await self.flush_all()
        if self._flushing:
            await asyncio.gather(*list(self._flushing), return_exceptions=True)
        await self.stop()

    # ================================================================ çekme

    async def last_seq(self) -> int:
        row = await self._store.fetch_one(f"SELECT MAX(canteen_seq) AS seq FROM {self._inbox}")
        return int(row["seq"]) if row and row.get("seq") is not None else 0

    async def pull_once(self) -> int:
        """Kantin outbox'undan bekleyenleri çeker (sayfa sayfa). İşlenen yeni olay sayısı."""
        after = await self.last_seq()
        total = 0
        for _ in range(20):  # tek turda en çok 10.000 olay
            page = await self._canteen.events_after(after, PULL_PAGE)
            items = page.get("items") or []
            if items:
                result = await self.receive(items)
                total += result["received"]
            last = int(page.get("lastId") or after)
            if not page.get("hasMore") or last <= after:
                break
            after = last
        self.last_pull_at = _now_iso()
        return total

    def start(self) -> None:
        if self._pull_task is None or self._pull_task.done():
            self._pull_task = asyncio.get_running_loop().create_task(
                self._pull_loop(), name="km-canteen-events-pull")

    async def stop(self) -> None:
        if self._pull_task is None:
            return
        self._pull_task.cancel()
        try:
            await self._pull_task
        except asyncio.CancelledError:
            pass
        self._pull_task = None

    async def _pull_loop(self) -> None:
        pruned = 0.0
        while True:
            wait = PULL_SECONDS
            try:
                await self.pull_once()
                self.supported = True
            except asyncio.CancelledError:
                raise
            except Exception as failure:  # noqa: BLE001 — döngü ölmez (K7)
                if getattr(failure, "reason", None) == "not_supported":
                    if self.supported:
                        self._log.info("kantin olay çekme ucunu desteklemiyor (eski kantin)")
                    self.supported = False
                    wait = PULL_UNSUPPORTED_SECONDS
                else:
                    self._log.warning("kantin olayları çekilemedi", error=str(failure)[:200])
            if time.monotonic() - pruned > 6 * 3600:
                try:
                    await self.prune()
                    pruned = time.monotonic()
                except Exception as failure:  # noqa: BLE001
                    self._log.warning("kantin olay kutusu budanamadı", error=str(failure))
            await asyncio.sleep(wait)

    async def prune(self) -> None:
        """14 günden eski kutu satırları; en büyük sıra (çekme imleci) kalır."""
        cutoff = (datetime.now(UTC) - timedelta(days=INBOX_DAYS)).isoformat(timespec="seconds")
        top = await self.last_seq()
        await self._store.execute(
            f"DELETE FROM {self._inbox} WHERE received_at < ? AND canteen_seq < ?",
            (cutoff, top))


def _seq(item: dict[str, Any]) -> int:
    try:
        return int(item.get("id") or 0)
    except (TypeError, ValueError):
        return 0


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _opaque(item: dict[str, Any]) -> str:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    return str(item.get("opaqueId") or payload.get("opaqueId") or "").strip()[:64]
