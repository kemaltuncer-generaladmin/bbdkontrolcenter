"""İki katmanlı önbellek.

L1 — REFERANS ÖNBELLEĞİ (900 sn, bellekte).
    Kanal, para birimi, dil, müşteri grubu, vergi kategorisi, öznitelik
    ailesi… 20 ekranın neredeyse hepsi bu listeleri açılışta istiyor. Değişme
    sıklığı ayda bir; her panel açılışında yeniden çekmek sunucunun dakikalık
    60 istek bütçesini tek başına yerdi.

L2 — ANLIK GÖRÜNTÜ (1800 sn, SQLite).
    Referans kümesinin tamamı tek satırda, JSON olarak. Süreç yeniden
    başladığında L1 boşalır ama L2 durur: açılışta mağazaya hiç gitmeden
    ekran dolar. Ayrıca mağaza erişilemezken de SON BİLİNEN hâli verir —
    ekran ayakta kalır (K7), yalnız verinin bayat olduğunu söyler.

Önbellek YALNIZCA referans veri içindir. Sipariş, ürün ve müşteri listesi
önbelleğe alınmaz: personel "kaydettim ama listede yok" yaşamamalı.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any

REFERENCE_TTL = 900
SNAPSHOT_TTL = 1800


class ReferenceCache:
    """L1 — süreç belleğinde, monotonik saatle yaşlanan sözlük.

    Duvar saati DEĞİL monotonik saat kullanılır: makine saati NTP ile geri
    alınırsa duvar saatli önbellek "gelecekte" kalır ve hiç tazelenmez.
    """

    def __init__(self, ttl: int = REFERENCE_TTL) -> None:
        # ttl = 0 "önbelleğe alma" demektir; ayarla kapatılabilsin diye
        # aşağı sınır 1'e çekilmez.
        self._ttl = max(0, int(ttl))
        self._values: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        if self._ttl <= 0:
            return None
        entry = self._values.get(key)
        if entry is None:
            return None
        stamp, value = entry
        if time.monotonic() - stamp > self._ttl:
            del self._values[key]
            return None
        return value

    def put(self, key: str, value: Any) -> Any:
        self._values[key] = (time.monotonic(), value)
        return value

    def drop(self, prefix: str = "") -> None:
        """Yazma sonrası ilgili dalı düşürür. Öneksiz çağrı hepsini siler."""
        if not prefix:
            self._values.clear()
            return
        for key in [key for key in self._values if key.startswith(prefix)]:
            del self._values[key]

    def __len__(self) -> int:
        return len(self._values)


class SnapshotCache:
    """L2 — `mod_store_api_snapshot` tablosunda JSON anlık görüntü.

    Tablo modülün kendi göçüyle açılır (K5). Depo erişimi patlarsa önbellek
    devre dışı kalır ama geçit çalışmaya devam eder: önbellek bir hızlandırma
    katmanıdır, veri kaynağı değil.
    """

    def __init__(self, store: Any, log: Any = None, ttl: int = SNAPSHOT_TTL) -> None:
        self._store = store
        self._log = log
        self._ttl = max(1, int(ttl))
        self._table = store.table("snapshot") if store is not None else "mod_store_api_snapshot"

    async def get(self, key: str) -> dict[str, Any] | None:
        """Kaydı döndürür: `{payload, storedAt, ageSeconds, stale}`.

        Süresi geçmiş kayıt SİLİNMEZ, `stale: True` ile döner — mağaza
        erişilemezken ekranın gösterebileceği tek veri odur.
        """
        if self._store is None:
            return None
        try:
            row = await self._store.fetch_one(
                f"SELECT payload, stored_at FROM {self._table} WHERE key = ?", (key,)
            )
        except Exception as failure:  # noqa: BLE001 - önbellek geçidi düşürmez
            self._warn("anlık görüntü okunamadı", failure)
            return None
        if not row:
            return None
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            return None
        age = _age_seconds(str(row["stored_at"]))
        return {
            "payload": payload,
            "storedAt": row["stored_at"],
            "ageSeconds": age,
            "stale": age > self._ttl,
        }

    async def put(self, key: str, payload: Any) -> None:
        if self._store is None:
            return
        try:
            await self._store.execute(
                f"INSERT INTO {self._table} (key, payload, stored_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET payload = excluded.payload, "
                "stored_at = excluded.stored_at",
                (key, json.dumps(payload, ensure_ascii=False), _now()),
            )
        except Exception as failure:  # noqa: BLE001 - önbellek geçidi düşürmez
            self._warn("anlık görüntü yazılamadı", failure)

    def _warn(self, message: str, failure: Exception) -> None:
        if self._log is not None:
            self._log.warning(message, error=str(failure))


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def _age_seconds(stamp: str) -> int:
    try:
        stored = datetime.fromisoformat(stamp)
    except ValueError:
        return 10**9
    if stored.tzinfo is None:
        stored = stored.replace(tzinfo=UTC)
    return max(0, int((datetime.now(UTC) - stored).total_seconds()))
