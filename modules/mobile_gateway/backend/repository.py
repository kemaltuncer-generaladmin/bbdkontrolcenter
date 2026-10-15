"""Geçidin kendi tabloları (K5): idempotensi, sayaçlar, SMS kaydı, denetim izi.

SQL çekirdeğin SQLite lehçesindedir ve `km_core/store/dialect.py` tarafından
PostgreSQL'e çevrilebilir kalır (testler her ifadeyi çevirmenden geçirir).
Zaman damgaları epoch-milisaniyedir.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from km_sdk import ModuleStore


@dataclass(slots=True)
class IdempotencyRecord:
    key: str
    route: str
    request_hash: str
    owner: str
    status_code: int
    response: str
    created_at: int


@dataclass(slots=True)
class Window:
    """Bir penceredeki sayım ve en eski kaydın zamanı."""

    count: int
    oldest: int | None


class Repository:
    def __init__(self, store: ModuleStore) -> None:
        self._store = store
        self.idempotency = store.table("idempotency")
        self.hits = store.table("hits")
        self.sms_log = store.table("sms_log")
        self.audit_log = store.table("audit")

    # ------------------------------------------------------------ idempotensi

    async def idempotency_get(self, key: str) -> IdempotencyRecord | None:
        row = await self._store.fetch_one(
            f"SELECT key, route, request_hash, owner, status_code, response, created_at "
            f"FROM {self.idempotency} WHERE key = ?", (key,),
        )
        if row is None:
            return None
        return IdempotencyRecord(
            key=str(row["key"]), route=str(row["route"]),
            request_hash=str(row["request_hash"]), owner=str(row["owner"]),
            status_code=int(row["status_code"] or 0), response=str(row["response"] or ""),
            created_at=int(row["created_at"] or 0),
        )

    async def idempotency_claim(self, key: str, route: str, request_hash: str, owner: str,
                                now: int) -> bool:
        """Anahtarı bu istek için ayırır. Başka istek önce davrandıysa False."""
        await self._store.execute(
            f"INSERT OR IGNORE INTO {self.idempotency} "
            f"(key, route, request_hash, owner, status_code, response, created_at) "
            f"VALUES (?, ?, ?, ?, 0, '', ?)",
            (key, route, request_hash, owner, now),
        )
        record = await self.idempotency_get(key)
        return record is not None and record.owner == owner

    async def idempotency_finish(self, key: str, owner: str, status_code: int,
                                 body: dict[str, Any]) -> None:
        await self._store.execute(
            f"UPDATE {self.idempotency} SET status_code = ?, response = ? "
            f"WHERE key = ? AND owner = ?",
            (status_code, json.dumps(body, ensure_ascii=False, separators=(",", ":")),
             key, owner),
        )

    async def idempotency_release(self, key: str, owner: str) -> None:
        await self._store.execute(
            f"DELETE FROM {self.idempotency} WHERE key = ? AND owner = ?", (key, owner),
        )

    async def idempotency_drop(self, key: str) -> None:
        await self._store.execute(f"DELETE FROM {self.idempotency} WHERE key = ?", (key,))

    # --------------------------------------------------------------- sayaçlar

    async def window(self, bucket: str, subject: str, since: int) -> Window:
        row = await self._store.fetch_one(
            f"SELECT COUNT(*) AS n, MIN(at) AS oldest FROM {self.hits} "
            f"WHERE bucket = ? AND subject = ? AND at > ?",
            (bucket, subject, since),
        )
        count = int((row or {}).get("n") or 0)
        oldest = (row or {}).get("oldest")
        return Window(count=count, oldest=int(oldest) if oldest is not None else None)

    async def hit(self, bucket: str, subject: str, at: int) -> None:
        await self._store.execute(
            f"INSERT INTO {self.hits} (bucket, subject, at) VALUES (?, ?, ?)",
            (bucket, subject, at),
        )

    async def clear(self, bucket: str, subject: str) -> None:
        await self._store.execute(
            f"DELETE FROM {self.hits} WHERE bucket = ? AND subject = ?", (bucket, subject),
        )

    # ------------------------------------------------------------------- iz

    async def log_sms(self, *, at: int, phone_masked: str, purpose: str, status: str,
                      reason: str, user_id: str, on_behalf_of: str, request_id: str,
                      job_id: str = "", channel: str = "") -> None:
        await self._store.execute(
            f"INSERT INTO {self.sms_log} (created_at, phone_masked, purpose, status, reason, "
            f"user_id, on_behalf_of, request_id, job_id, channel, delivery) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (at, phone_masked, purpose, status, reason, user_id, on_behalf_of, request_id,
             job_id, channel, "pending" if job_id else ""),
        )

    async def sms_by_job(self, job_id: str) -> dict[str, Any] | None:
        """Gönderilmiş (status=sent) OTP kaydı — jobId indeksiyle (003)."""
        row = await self._store.fetch_one(
            f"SELECT id, created_at, channel, delivery, delivered_at, checked_at "
            f"FROM {self.sms_log} WHERE job_id = ? AND status = 'sent' "
            f"ORDER BY id DESC LIMIT 1", (job_id,),
        )
        return dict(row) if row is not None else None

    async def sms_delivery(self, row_id: int, *, delivery: str, delivered_at: int | None,
                           checked_at: int, reason: str | None = None) -> None:
        if reason is None:
            await self._store.execute(
                f"UPDATE {self.sms_log} SET delivery = ?, delivered_at = ?, checked_at = ? "
                f"WHERE id = ?", (delivery, delivered_at, checked_at, row_id),
            )
            return
        await self._store.execute(
            f"UPDATE {self.sms_log} SET delivery = ?, delivered_at = ?, checked_at = ?, "
            f"reason = ? WHERE id = ?",
            (delivery, delivered_at, checked_at, reason[:60], row_id),
        )

    async def sms_stats(self, since: int) -> dict[str, Any]:
        """Penceredeki OTP özeti (created_at indeksiyle; satır sayısı saatte en çok yüzler). Kurum SMS'leri
        (POST /sms/send: duyuru, ödeme hatırlatması, deneme sonucu — bbd-akademi madde 81, 88) sayılmaz."""
        rows = await self._store.fetch_all(
            f"SELECT status, delivery, created_at, delivered_at FROM {self.sms_log} "
            f"WHERE created_at > ? AND purpose NOT IN ('announcement', 'payment_reminder', 'exam_result')",
            (since,),
        )
        sent = delivered = failed = pending = limited = send_failed = 0
        durations: list[int] = []
        for row in rows:
            status = str(row.get("status") or "")
            if status == "rate_limited":
                limited += 1
                continue
            if status != "sent":
                send_failed += 1
                continue
            sent += 1
            delivery = str(row.get("delivery") or "")
            if delivery == "delivered":
                delivered += 1
                if row.get("delivered_at"):
                    durations.append(int(row["delivered_at"]) - int(row["created_at"]))
            elif delivery == "failed":
                failed += 1
            else:
                pending += 1
        return {
            "sent": sent, "delivered": delivered, "deliveryFailed": failed,
            "pending": pending, "sendFailed": send_failed, "rateLimited": limited,
            "avgDeliverySeconds": (round(sum(durations) / len(durations) / 1000, 1)
                                   if durations else None),
            "maxDeliverySeconds": (round(max(durations) / 1000, 1) if durations else None),
        }

    async def audit(self, *, at: int, action: str, student_id: str, user_id: str,
                    on_behalf_of: str, request_id: str, result: str,
                    detail: dict[str, Any]) -> None:
        await self._store.execute(
            f"INSERT INTO {self.audit_log} (created_at, action, student_id, user_id, "
            f"on_behalf_of, request_id, result, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (at, action, student_id, user_id, on_behalf_of, request_id, result,
             json.dumps(detail, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
        )

    # ------------------------------------------------------------- temizlik

    async def purge(self, *, idempotency_before: int, hits_before: int, sms_before: int,
                    audit_before: int) -> None:
        await self._store.execute(
            f"DELETE FROM {self.idempotency} WHERE created_at < ?", (idempotency_before,))
        await self._store.execute(f"DELETE FROM {self.hits} WHERE at < ?", (hits_before,))
        await self._store.execute(
            f"DELETE FROM {self.sms_log} WHERE created_at < ?", (sms_before,))
        await self._store.execute(
            f"DELETE FROM {self.audit_log} WHERE created_at < ?", (audit_before,))
