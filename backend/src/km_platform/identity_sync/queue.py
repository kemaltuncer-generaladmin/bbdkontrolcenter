"""Denetim kaydının yeniden deneme kuyruğu (ADR 0021 §5).

ADR'nin cümlesi kesindir: gönderim başarısız olursa kayıt **yerelde birikir ve
yeniden denenir; asla düşürülmez**. Gönderim anında kaybolan bir kayıt, "kim ne
yaptı" sorusunu tam da ağın koptuğu anlar için cevapsız bırakırdı — oysa uzaktan
müdahalenin en çok yapıldığı anlar onlardır.

Kuyruk çekirdeğin deposundaki `identity_audit_queue` tablosudur; tabloyu
`km_core/security/migrations.py` → `0005_identity_audit_queue` açar. Burada
yalnız kuyruğun kendisi vardır: HTTP bilmez, token görmez, karar vermez.

**GERİ ÇEKİLME.** Merkez kapalıyken her saniye yeniden denemek ne kaydı kurtarır
ne de ağı; deneme sayısı arttıkça bekleme ikiye katlanır ve bir saatte durur.
Üst sınır olmasaydı uzun bir kesintiden sonra kuyruk günlerce beklerdi.

**KAYIT SİLİNMESİ.** Yalnız MERKEZE TESLİM EDİLMİŞ satır düşer; teslim edilen
kayıt artık merkezin denetim izindedir, kuyrukta ikinci kopyası tutulmaz.
Gönderilemeyen hiçbir satır silinmez.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import structlog

log = structlog.get_logger("km.identity_sync")

# Tek turda gönderilen en çok kayıt. Kuyruk uzun süre birikmişse bile tek
# istekte yüz binlerce satır göndermek isteğin kendisini düşürürdü.
BATCH_SIZE = 100

# Geri çekilme: 30sn, 60sn, 120sn … en çok 1 saat.
BASE_BACKOFF_SECONDS = 30.0
MAX_BACKOFF_SECONDS = 3600.0


class QueueStore(Protocol):
    """Kuyruğun depoya bakan yüzü (`km_core/store/db.py` → `Store`)."""

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> None: ...
    async def execute_many(self, sql: str, rows: Iterable[Sequence[Any]]) -> None: ...
    async def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]: ...
    async def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None: ...


def _now() -> datetime:
    return datetime.now(UTC)


def _stamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def backoff_seconds(attempts: int) -> float:
    """`attempts` kez denenmiş bir kaydın bir sonraki denemeye kalan süresi."""
    if attempts <= 0:
        return BASE_BACKOFF_SECONDS
    return min(BASE_BACKOFF_SECONDS * (2 ** (attempts - 1)), MAX_BACKOFF_SECONDS)


class AuditQueue:
    def __init__(self, store: QueueStore) -> None:
        self._store = store

    async def enqueue(self, entries: list[dict[str, Any]]) -> int:
        """Kayıtları kuyruğa alır. GÖNDERMEDEN ÖNCE yazılır.

        Sıra bilerek böyledir: önce gönderip başarısızlıkta yazmak, sürecin tam
        o anda ölmesi hâlinde kaydı düşürürdü.
        """
        if not entries:
            return 0
        stamp = _stamp(_now())
        await self._store.execute_many(
            "INSERT INTO identity_audit_queue "
            "(entry, queued_at, attempts, next_attempt_at) VALUES (?, ?, 0, ?)",
            [(json.dumps(entry, ensure_ascii=False), stamp, stamp) for entry in entries],
        )
        return len(entries)

    async def due(self, limit: int = BATCH_SIZE) -> list[dict[str, Any]]:
        """Sırası gelmiş kayıtlar: `{id, entry, attempts}`.

        Çözülemeyen (bozuk JSON) satır atlanmaz, DÜŞÜRÜLÜR: kuyruğu sonsuza dek
        tıkayan bir satır, arkasındaki geçerli kayıtların da gitmemesi demekti.
        Bu tek durum loga hata olarak düşer.
        """
        rows = await self._store.fetch_all(
            "SELECT id, entry, attempts FROM identity_audit_queue "
            "WHERE next_attempt_at <= ? ORDER BY id LIMIT ?",
            (_stamp(_now()), int(limit)),
        )
        batch: list[dict[str, Any]] = []
        broken: list[int] = []
        for row in rows:
            try:
                entry = json.loads(str(row["entry"]))
            except ValueError:
                broken.append(int(row["id"]))
                continue
            batch.append({"id": int(row["id"]), "entry": entry,
                          "attempts": int(row["attempts"] or 0)})
        if broken:
            log.error("kuyrukta çözülemeyen denetim kaydı düşürüldü", count=len(broken))
            await self.drop(broken)
        return batch

    async def drop(self, ids: list[int]) -> None:
        """TESLİM EDİLMİŞ satırları düşürür."""
        if not ids:
            return
        await self._store.execute_many(
            "DELETE FROM identity_audit_queue WHERE id = ?",
            [(int(item),) for item in ids],
        )

    async def defer(self, batch: list[dict[str, Any]], error: str) -> None:
        """Gönderilemeyen satırları geri çekilmeli olarak erteler. SİLMEZ."""
        if not batch:
            return
        now = _now()
        rows = [
            (
                int(item["attempts"]) + 1,
                error[:500],
                _stamp(now + timedelta(seconds=backoff_seconds(int(item["attempts"]) + 1))),
                int(item["id"]),
            )
            for item in batch
        ]
        await self._store.execute_many(
            "UPDATE identity_audit_queue "
            "SET attempts = ?, last_error = ?, next_attempt_at = ? WHERE id = ?",
            rows,
        )

    async def depth(self) -> int:
        row = await self._store.fetch_one("SELECT COUNT(*) AS n FROM identity_audit_queue")
        return int(row["n"]) if row else 0
