"""KM → BBD Akademi olay iletimi (bbd-akademi `docs/integrations/km-merkezi-senkron.md`
§3.2, `km-mobile-gateway.md` §10; API sözleşmesi `webhooks_km`, `KmEventBatch`).

GÖNDERİCİ. Platform outbox'unu (`events.outbox`, ADR 0029) `akademi` imleciyle
okur: `wait_after(imleç, 25 sn)` → en çok 100 olay → 200 ms bekleme penceresi →
birleştirme → TEK `POST {akademi_base_url}/v1/webhooks/km`. Başlıklar
`X-KM-Tenant` ve `X-KM-Signature: t=<unix>,v1=<hex HMAC-SHA256(sır, "<t>.<ham
gövde>")>`; sır kasada `mobile_gateway.webhook_secret`.

BİRLEŞTİRME. Pakette aynı `(type, subject)` için yalnız son olay kalır;
`fields` birleşir (biri boşsa "hepsini yenile" = boş), `canteen.balance_changed`
en büyük `ledgerSeq`'li değeri taşır (gerekçeler birleşir). Yaşam döngüsü
olayları (`student.deactivated|reactivated|deleted`) BİRLEŞTİRİLMEZ.

YANIT YORUMU. 2xx → imleç ilerler. 401 / 404 → yapılandırma hatası: gönderim
DURUR (panel kırmızı; 5 dakikada bir yoklanır — sır düzeltilince kendiliğinden
sürer). 400 / 422 → paket tek tek gönderilir; hâlâ reddedilen olay ÖLÜ MEKTUBA
gider, imleç geçer (zehirli olay akışı tıkamaz). 429 / 5xx / ağ → üstel geri
çekilme 1, 2, 4 … 60 sn (±%20), `Retry-After`'a uyulur. 6 saatten uzun
ulaşılamazsa gönderim sürer ama durum `unreachable` der. İmleç budanmış bölgede
kalırsa (14 gün) `directory.resync_required {scope: all}` üretilir.

HIZ. En çok 10 istek/sn. `events_enabled: false` (VARSAYILAN) iken HİÇ istek
atılmaz ve tüketici kaydı silinir (outbox budaması beklemez).

DURUM YETENEĞİ `mobile_gateway.events`: `status()`, `dead(limit)`,
`retry_dead(seqs)`, `replay_from(seq)` — KM paneli (Akademi bölümü, Senkron
kartı) kendi izniyle (`mobile_gateway.events.view|manage`) çağırır.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import random
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx

CONSUMER = "akademi"
SECRET_KEY = "mobile_gateway.webhook_secret"

#: Birleştirilmeyen olaylar (güvenlik olayları tek tek gider).
LIFECYCLE = frozenset({"student.deactivated", "student.reactivated", "student.deleted"})

BATCH_MAX = 100
WINDOW_SECONDS = 0.2
WAIT_SECONDS = 25.0
MIN_INTERVAL_SECONDS = 0.1          # ≤ 10 istek/sn
BACKOFF_MAX_SECONDS = 60.0
JITTER = 0.2
STOPPED_PROBE_SECONDS = 300.0
UNREACHABLE_AFTER_SECONDS = 6 * 3600
EXPIRE_DAYS = 14
REQUEST_TIMEOUT = 10.0
SECRET_CACHE_SECONDS = 60.0

#: Yapılandırma hatası sayılan yanıtlar (yeniden denemek anlamsız).
CONFIG_STATUSES = (401, 404)
#: Paketi tek tek göndermeyi gerektiren red.
REJECT_STATUSES = (400, 422)


def coalesce(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aynı `(type, subject)` → son olay; `fields` birleşimi; bakiye en büyük `ledgerSeq`."""
    kept: dict[tuple[str, str, str | None], dict[str, Any]] = {}
    singles: list[dict[str, Any]] = []
    for event in events:
        if event["type"] in LIFECYCLE:
            singles.append(event)
            continue
        subject = event.get("subject") or {}
        key = (event["type"], str(subject.get("kind")), subject.get("id"))
        previous = kept.get(key)
        if previous is None:
            kept[key] = dict(event)
            continue
        merged = dict(event)
        old_fields, new_fields = previous.get("fields") or [], event.get("fields") or []
        if old_fields and new_fields:
            merged["fields"] = list(dict.fromkeys([*old_fields, *new_fields]))
        else:
            merged["fields"] = []
        if event["type"] == "canteen.balance_changed":
            old_data, new_data = previous.get("data") or {}, event.get("data") or {}
            best = new_data if int(new_data.get("ledgerSeq") or 0) >= \
                int(old_data.get("ledgerSeq") or 0) else old_data
            reasons = sorted({*(old_data.get("reasons") or []), *(new_data.get("reasons") or [])})
            merged["data"] = {**best, "reasons": reasons}
        kept[key] = merged
    return sorted([*singles, *kept.values()], key=lambda item: int(item["seq"]))


def signature(secret: str, stamp: int, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), f"{stamp}.".encode() + body,
                      hashlib.sha256).hexdigest()
    return f"t={stamp},v1={digest}"


def _iso(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    return datetime.fromtimestamp(seconds, tz=UTC).astimezone().isoformat(timespec="seconds")


class EventSender:
    """Gönderici döngüsü + `mobile_gateway.events` durum yeteneği."""

    def __init__(self, *, store: Any, outbox: Callable[[], Any], secrets: Callable[[], Any],
                 config: dict[str, Any], log: Any,
                 settings: Callable[[], dict[str, Any]] | None = None,
                 transport: httpx.AsyncBaseTransport | None = None,
                 clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
                 rand: Callable[[], float] = random.random,
                 window: float = WINDOW_SECONDS) -> None:
        self._store = store
        self._outbox = outbox
        self._secrets = secrets
        self._log = log
        self._settings = settings
        self._transport = transport
        self._clock = clock
        self._sleep = sleep
        self._rand = rand
        self._window = window
        self.base_url = str(config.get("akademi_base_url") or "").rstrip("/")
        self.tenant = str(config.get("akademi_tenant") or "").strip()
        self.instance_id = str(config.get("events_instance_id") or "km")
        self.enabled = bool(config.get("events_enabled", False)) and bool(self.base_url) \
            and bool(self.tenant)
        self.configured = bool(config.get("events_enabled", False))
        self._cursor_table = store.table("event_cursor")
        self._dead_table = store.table("event_dead")
        self._meta = store.table("meta")
        self._task: asyncio.Task[None] | None = None
        self._http: httpx.AsyncClient | None = None
        self._failures = 0
        self._last_post = 0.0
        self._secret: tuple[float, str] | None = None
        self.stopped_until = 0.0
        self.last_lag_ms: int | None = None
        self._alerted = False

    # ============================================================ yaşam döngüsü

    async def start(self, *, loop: bool = True) -> None:
        """Açılış: kurum ayarı olayı, sonra (açıksa) gönderici döngüsü.

        `loop=False` döngüyü başlatmaz (testler turları elle koşturur)."""
        try:
            await self.announce_settings()
        except Exception as failure:  # noqa: BLE001 — açılış düşmez (K7)
            self._log.warning("kurum ayarı olayı yazılamadı", error=str(failure))
        box = self._outbox()
        if not self.enabled:
            if box is not None:
                # Kapalı tüketici outbox budamasını bekletmesin; açılınca bugünden başlar.
                await box.unregister_consumer(CONSUMER)
            await self._store.execute(
                f"DELETE FROM {self._cursor_table} WHERE consumer = ?", (CONSUMER,))
            if self.configured:
                self._log.warning("olay iletimi açık ama akademi_base_url / akademi_tenant boş")
            return
        if box is None:
            self._log.warning("olay outbox'u yok; Akademi'ye olay iletilmiyor")
            return
        await self._ensure_cursor(box)
        if not loop:
            return
        self._task = asyncio.get_running_loop().create_task(self._loop(),
                                                            name="km-gateway-events")
        self._log.info("Akademi olay iletimi açık", base_url=self.base_url, tenant=self.tenant)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _ensure_cursor(self, box: Any) -> int:
        row = await self._store.fetch_one(
            f"SELECT last_seq FROM {self._cursor_table} WHERE consumer = ?", (CONSUMER,))
        if row is not None:
            await box.register_consumer(CONSUMER, start=int(row["last_seq"]))
            return int(row["last_seq"])
        start = await box.register_consumer(CONSUMER)
        await self._store.execute(
            f"INSERT OR IGNORE INTO {self._cursor_table} (consumer, last_seq, updated_at, state) "
            f"VALUES (?, ?, ?, 'live')", (CONSUMER, int(start), self._now_ms()))
        return int(start)

    async def _loop(self) -> None:
        while True:
            try:
                delay = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as failure:  # noqa: BLE001 — döngü ölmez (K7)
                self._log.warning("olay iletimi turu patladı", error=str(failure)[:200])
                delay = 5.0
            if delay > 0:
                await self._sleep(delay)

    # ================================================================ tur

    async def run_once(self, wait: float = WAIT_SECONDS) -> float:
        """Bir iletim turu. Dönen değer bir sonraki tura kadar beklenecek saniyedir."""
        box = self._outbox()
        if box is None:
            return 30.0
        now = self._clock()
        if self.stopped_until and now < self.stopped_until:
            return min(self.stopped_until - now, STOPPED_PROBE_SECONDS)
        cursor = await self._cursor()
        if not await box.wait_after(cursor, wait):
            return 0.0
        if self._window > 0:
            await self._sleep(self._window)  # aynı anda gelenler tek pakete girsin
        if await box.expired(cursor):
            await self._resync(box, "outbox budandı")
            return 0.0
        events = await box.read_after(cursor, BATCH_MAX)
        if not events:
            return 0.0
        if self._age(events[0]) > EXPIRE_DAYS * 86400:
            await self._resync(box, "14 günden eski olay")
            return 0.0
        secret = await self._webhook_secret()
        if not secret:
            await self._state("stopped", "webhook_secret_missing")
            self.stopped_until = now + STOPPED_PROBE_SECONDS
            return STOPPED_PROBE_SECONDS
        status, retry_after, code = await self._post(coalesce(events), secret)
        if 200 <= status < 300:
            await self._delivered(events)
            return 0.0
        if status in CONFIG_STATUSES:
            self.stopped_until = self._clock() + STOPPED_PROBE_SECONDS
            await self._state("stopped", f"{status} {code or ''}".strip())
            self._log.warning("Akademi olay iletimi durdu (yapılandırma)", status=status,
                              code=code)
            return STOPPED_PROBE_SECONDS
        if status in REJECT_STATUSES:
            return await self._one_by_one(events, secret)
        return await self._backoff(status, retry_after, code)

    async def _delivered(self, events: list[dict[str, Any]]) -> None:
        last = events[-1]
        self._failures = 0
        self.stopped_until = 0.0
        self._alerted = False
        self.last_lag_ms = int(self._age(last) * 1000)
        await self._advance(int(last["seq"]), success=True)
        # Metrik: commit → teslim gecikmesi (km_event_delivery_lag_ms).
        self._log.info("olaylar iletildi", count=len(events), last_seq=int(last["seq"]),
                       km_event_delivery_lag_ms=self.last_lag_ms)

    async def _one_by_one(self, events: list[dict[str, Any]], secret: str) -> float:
        """400/422: olaylar tek tek; reddedilen ölü mektuba, imleç geçer."""
        for event in events:
            status, retry_after, code = await self._post([event], secret)
            if 200 <= status < 300:
                await self._advance(int(event["seq"]), success=True)
                continue
            if status in REJECT_STATUSES:
                await self._dead(event, code or str(status))
                await self._advance(int(event["seq"]), success=False)
                continue
            if status in CONFIG_STATUSES:
                self.stopped_until = self._clock() + STOPPED_PROBE_SECONDS
                await self._state("stopped", f"{status} {code or ''}".strip())
                return STOPPED_PROBE_SECONDS
            return await self._backoff(status, retry_after, code)
        self._failures = 0
        return 0.0

    async def _backoff(self, status: int, retry_after: float | None, code: str | None) -> float:
        self._failures += 1
        base = min(BACKOFF_MAX_SECONDS, float(2 ** (self._failures - 1)))
        delay = base * (1 + JITTER * (2 * self._rand() - 1))
        if retry_after is not None and retry_after > 0:
            delay = min(max(retry_after, 0.0), 3600.0)
        error = f"{status} {code or ''}".strip() if status else (code or "network_error")
        await self._state("retrying", error)
        row = await self._cursor_row()
        last_success = (row or {}).get("last_success_at")
        reference = (int(last_success) / 1000) if last_success else None
        if reference is not None and self._clock() - reference > UNREACHABLE_AFTER_SECONDS \
                and not self._alerted:
            self._alerted = True
            self._log.warning("Akademi'ye 6 saattir olay iletilemiyor", last_error=error)
        return delay

    async def _resync(self, box: Any, why: str) -> None:
        """İmleç budanmış bölgede: Akademi'ye tam uzlaştırma söylenir, imleç atlar."""
        top = int(await box.last_seq())
        await box.emit("directory.resync_required", ("institution", None),
                       data={"scope": "all"}, origin="system")
        await self._advance(top, success=False)
        self._log.warning("olay imleci atladı; directory.resync_required", why=why, seq=top)

    # ============================================================= HTTP

    def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=REQUEST_TIMEOUT, transport=self._transport)
        return self._http

    async def _post(self, events: list[dict[str, Any]],
                    secret: str) -> tuple[int, float | None, str | None]:
        """Paketi imzalayıp gönderir. `(status, retry_after, code)`; ağ hatası status 0."""
        gap = MIN_INTERVAL_SECONDS - (self._clock() - self._last_post)
        if gap > 0:
            await self._sleep(gap)
        body = json.dumps({
            "source": {"system": "km", "instanceId": self.instance_id},
            "sentAt": datetime.now(UTC).astimezone().isoformat(timespec="milliseconds"),
            "events": events,
        }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        stamp = int(self._clock())
        headers = {"Content-Type": "application/json", "X-KM-Tenant": self.tenant,
                   "X-KM-Signature": signature(secret, stamp, body)}
        self._last_post = self._clock()
        await self._touch_attempt()
        try:
            response = await self._client().post(f"{self.base_url}/v1/webhooks/km",
                                                  content=body, headers=headers)
        except httpx.HTTPError as failure:
            return 0, None, type(failure).__name__
        code: str | None = None
        try:
            payload = response.json()
            error = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(error, dict):
                code = str(error.get("code") or "") or None
            elif isinstance(payload, dict) and payload.get("code"):
                code = str(payload["code"])
        except ValueError:
            code = None
        retry_after: float | None = None
        header = response.headers.get("Retry-After")
        if header:
            try:
                retry_after = float(header)
            except ValueError:
                retry_after = None
        return response.status_code, retry_after, code

    async def _webhook_secret(self) -> str:
        cached = self._secret
        if cached is not None and self._clock() - cached[0] < SECRET_CACHE_SECONDS:
            return cached[1]
        vault = self._secrets()
        value = ""
        if vault is not None:
            try:
                result = vault.get(SECRET_KEY)
                raw = (await result) if asyncio.iscoroutine(result) else result
                value = str(raw or "")
            except Exception:  # noqa: BLE001 — kasa okunamadı = sır yok
                value = ""
        self._secret = (self._clock(), value)
        return value

    # ============================================================ kayıt

    def _now_ms(self) -> int:
        return int(self._clock() * 1000)

    def _age(self, event: dict[str, Any]) -> float:
        try:
            moment = datetime.fromisoformat(str(event.get("occurredAt")))
        except ValueError:
            return 0.0
        return max(0.0, self._clock() - moment.timestamp())

    async def _cursor_row(self) -> dict[str, Any] | None:
        return await self._store.fetch_one(
            f"SELECT * FROM {self._cursor_table} WHERE consumer = ?", (CONSUMER,))

    async def _cursor(self) -> int:
        row = await self._cursor_row()
        return int(row["last_seq"]) if row else 0

    async def _advance(self, seq: int, *, success: bool) -> None:
        now = self._now_ms()
        if success:
            await self._store.execute(
                f"UPDATE {self._cursor_table} SET last_seq = ?, updated_at = ?, "
                f"last_success_at = ?, last_error = NULL, state = 'live' "
                f"WHERE consumer = ? AND last_seq < ?", (seq, now, now, CONSUMER, seq))
        else:
            await self._store.execute(
                f"UPDATE {self._cursor_table} SET last_seq = ?, updated_at = ? "
                f"WHERE consumer = ? AND last_seq < ?", (seq, now, CONSUMER, seq))
        box = self._outbox()
        if box is not None:
            await box.consumer_seq(CONSUMER, seq)

    async def _state(self, state: str, error: str | None) -> None:
        await self._store.execute(
            f"UPDATE {self._cursor_table} SET state = ?, last_error = ?, updated_at = ? "
            f"WHERE consumer = ?", (state, (error or "")[:200] or None, self._now_ms(), CONSUMER))

    async def _touch_attempt(self) -> None:
        await self._store.execute(
            f"UPDATE {self._cursor_table} SET last_attempt_at = ? WHERE consumer = ?",
            (self._now_ms(), CONSUMER))

    async def _dead(self, event: dict[str, Any], code: str) -> None:
        now = self._now_ms()
        existing = await self._store.fetch_one(
            f"SELECT attempts FROM {self._dead_table} WHERE seq = ?", (int(event["seq"]),))
        if existing is None:
            await self._store.execute(
                f"INSERT INTO {self._dead_table} (seq, event_id, type, error_code, attempts, "
                f"last_attempt_at, event) VALUES (?, ?, ?, ?, 1, ?, ?)",
                (int(event["seq"]), str(event["eventId"]), str(event["type"]), code[:64], now,
                 json.dumps(event, ensure_ascii=False)))
        else:
            await self._store.execute(
                f"UPDATE {self._dead_table} SET attempts = attempts + 1, error_code = ?, "
                f"last_attempt_at = ? WHERE seq = ?", (code[:64], now, int(event["seq"])))
        self._log.warning("olay ölü mektuba düştü", seq=int(event["seq"]),
                          type=str(event["type"]), code=code)

    # ============================================= kurum ayarları (§2.2)

    async def announce_settings(self) -> list[str]:
        """Kurum ayarları (ad, özellikler) son açılıştan beri değiştiyse
        `institution.settings_changed`. İlk açılışta yalnız kaydedilir."""
        if self._settings is None:
            return []
        current = self._settings()
        text = json.dumps(current, sort_keys=True, ensure_ascii=False)
        row = await self._store.fetch_one(
            f"SELECT value FROM {self._meta} WHERE key = 'public_settings'")
        if row is not None and str(row["value"]) == text:
            return []
        changed: list[str] = []
        if row is not None:
            try:
                before = json.loads(str(row["value"]))
            except ValueError:
                before = {}
            changed = sorted(key for key in {*before, *current}
                             if before.get(key) != current.get(key))
        box = self._outbox()
        if row is not None and box is not None and changed:
            await box.emit("institution.settings_changed", ("institution", None),
                           fields=changed)
        await self._store.execute(
            f"INSERT INTO {self._meta} (key, value) VALUES ('public_settings', ?) "
            f"ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (text,))
        return changed

    # ================================================ durum yeteneği (§3.2)

    async def status(self) -> dict[str, Any]:
        box = self._outbox()
        last = int(await box.last_seq()) if box is not None else 0
        row = await self._cursor_row() or {}
        delivered = int(row.get("last_seq") or 0)
        dead = await self._store.fetch_one(f"SELECT COUNT(*) AS n FROM {self._dead_table}")
        lag = 0.0
        if box is not None and delivered < last:
            lag = float(await box.oldest_pending_age(delivered) or 0.0)
        state = str(row.get("state") or "live") if self.enabled else "stopped"
        last_success = row.get("last_success_at")
        reference = int(last_success) / 1000 if last_success else None
        unreachable = bool(self.enabled and delivered < last and (
            (reference is not None and self._clock() - reference > UNREACHABLE_AFTER_SECONDS)
            or (reference is None and lag > UNREACHABLE_AFTER_SECONDS)))
        return {
            "enabled": self.enabled,
            "lastSeq": last,
            "deliveredSeq": delivered,
            "lagSeconds": round(lag, 1),
            "lastSuccessAt": _iso(reference),
            "lastError": row.get("last_error") or (None if self.enabled else "events_disabled"),
            "deadCount": int(dead["n"]) if dead else 0,
            "state": state,
            "unreachable": unreachable,
            "deliveryLagMs": self.last_lag_ms,
            "tenant": self.tenant or None,
        }

    async def dead(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = await self._store.fetch_all(
            f"SELECT seq, event_id, type, error_code, attempts, last_attempt_at "
            f"FROM {self._dead_table} ORDER BY seq LIMIT ?", (max(1, min(int(limit), 500)),))
        return [{"seq": int(row["seq"]), "eventId": str(row["event_id"]),
                 "type": str(row["type"]), "errorCode": str(row["error_code"] or ""),
                 "attempts": int(row["attempts"]),
                 "lastAttemptAt": _iso(int(row["last_attempt_at"]) / 1000)} for row in rows]

    async def retry_dead(self, seqs: list[int]) -> dict[str, int]:
        """Ölü mektupları tek tek yeniden gönderir; iletilen silinir."""
        secret = await self._webhook_secret()
        delivered = failed = 0
        for seq in list(dict.fromkeys(int(item) for item in seqs))[:500]:
            row = await self._store.fetch_one(
                f"SELECT event FROM {self._dead_table} WHERE seq = ?", (seq,))
            if row is None:
                continue
            if not secret or not self.enabled:
                failed += 1
                continue
            event = json.loads(str(row["event"]))
            status, _retry, code = await self._post([event], secret)
            if 200 <= status < 300:
                await self._store.execute(f"DELETE FROM {self._dead_table} WHERE seq = ?", (seq,))
                delivered += 1
            else:
                await self._dead(event, code or str(status))
                failed += 1
        return {"delivered": delivered, "failed": failed}

    async def replay_from(self, seq: int) -> dict[str, Any]:
        """İmleci `seq`'ten öncesine alır: o olaydan itibaren yeniden iletilir
        (Akademi `eventId` ile tekilleştirir)."""
        box = self._outbox()
        target = max(0, int(seq) - 1)
        if box is not None and await box.expired(target):
            return {"replayed": False, "reason": "events_expired"}
        await self._store.execute(
            f"UPDATE {self._cursor_table} SET last_seq = ?, updated_at = ? WHERE consumer = ?",
            (target, self._now_ms(), CONSUMER))
        self.stopped_until = 0.0
        return {"replayed": True, "deliveredSeq": target}


class GatewayEvents:
    """`mobile_gateway.events` yeteneği — gönderici durumunun panel yüzü."""

    def __init__(self, sender: EventSender) -> None:
        self._sender = sender

    async def status(self) -> dict[str, Any]:
        return await self._sender.status()

    async def dead(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self._sender.dead(limit)

    async def retry_dead(self, ids: list[int]) -> dict[str, int]:
        return await self._sender.retry_dead(ids)

    async def replay_from(self, seq: int) -> dict[str, Any]:
        return await self._sender.replay_from(seq)
