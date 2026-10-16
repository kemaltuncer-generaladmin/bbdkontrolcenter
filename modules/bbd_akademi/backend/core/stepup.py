"""PIN teyidi (step-up) — kritik Akademi işleminden HEMEN ÖNCE.

Sözleşme (`km-akademi-paneli.md` §1): step-up isteyen işlem (tahsilat iptali)
entegrasyon anahtarıyla `X-Akademi-Step-Up: km-pin` başlığı taşıdığında kabul
edilir; KM, işlemden hemen önce kullanıcının PIN'ini yeniden doğrulamakla
yükümlüdür.

AKIŞ (iki adım, PIN Akademi'ye ASLA gitmez):

1. `POST /api/bbd_akademi/km/step-up {pin, operationId}` — PIN çekirdeğin
   yeniden doğrulama kapısından geçer (`km_sdk.confirm_pin`: kişinin kendi
   kaydıyla sabit sürede karşılaştırma, yanlış PIN denetim izine yazılır).
   Geçerse tek kullanımlık belirteç döner (varsayılan 120 sn).
2. Asıl istek belirteci taşır (`X-KM-Step-Up` başlığı ya da köprüde
   `stepUpToken`). Vekil belirteci TÜKETİR (kişi + işlem eşleşmeli, süresi
   dolmamış olmalı) ve ancak o zaman `X-Akademi-Step-Up: km-pin` ekler.

Tüketme TEK `UPDATE` ile yapılır: iki eşzamanlı istek aynı belirteci
kullanırsa yalnız biri kazanır (SQLite ve PostgreSQL'de aynı davranış).
"""

from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from collections.abc import Callable
from typing import Any


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class StepUpStore:
    def __init__(self, store: Any, *, ttl_seconds: int = 120,
                 clock: Callable[[], float] = time.time) -> None:
        self._store = store
        self._table = store.table("step_up")
        self._ttl = ttl_seconds
        self._clock = clock

    async def issue(self, user_id: str, operation_id: str) -> dict[str, Any]:
        now = int(self._clock())
        token = secrets.token_urlsafe(24)
        # Süresi dolmuş satırlar kendiliğinden temizlenir; tablo büyümez.
        await self._store.execute(f"DELETE FROM {self._table} WHERE expires_at < ?", (now - 3600,))
        await self._store.execute(
            f"INSERT INTO {self._table} (token_hash, user_id, operation_id, created_at, "
            "expires_at, consumed_by) VALUES (?, ?, ?, ?, ?, NULL)",
            (_digest(token), str(user_id), operation_id, now, now + self._ttl),
        )
        return {"stepUpToken": token, "operationId": operation_id,
                "expiresInSeconds": self._ttl}

    async def consume(self, token: str | None, *, user_id: str, operation_id: str) -> bool:
        if not token:
            return False
        nonce = uuid.uuid4().hex
        now = int(self._clock())
        digest = _digest(token)
        await self._store.execute(
            f"UPDATE {self._table} SET consumed_by = ? WHERE token_hash = ? AND user_id = ? "
            "AND operation_id = ? AND consumed_by IS NULL AND expires_at >= ?",
            (nonce, digest, str(user_id), operation_id, now),
        )
        row = await self._store.fetch_one(
            f"SELECT token_hash FROM {self._table} WHERE token_hash = ? AND consumed_by = ?",
            (digest, nonce),
        )
        return row is not None
