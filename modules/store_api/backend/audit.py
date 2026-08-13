"""Yerel denetim izi — istek GÖNDERİLMEDEN ÖNCE yazılır.

Bagisto'nun kendi denetim tablosu (`admin_api_audits`) yalnız BAŞARILI
yazmaları ve yalnız alan farklarını tutar. İki şey eksiktir:

  1. GEREKÇE. "Bu siparişi neden iptal ettik" bilgisi Bagisto'da hiç yok.
  2. GÖNDERİLEMEYEN istek. Ağ koparsa, zaman aşımı olursa ya da acil fren
     açıkken istek reddedilirse Bagisto'da hiçbir iz kalmaz.

Bu yüzden satır isteğin ÖNÜNDE yazılır, sonucu sonradan işlenir. Yarım kalan
satır (result boş) "gönderildi mi belli değil" demektir — zaman aşımına
uğrayan yazmada durumun gerçeği de budur, uzakta uygulanmış olabilir.

Tablo yalnızca bu modülündür: `mod_store_api_audit` (K5).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from .errors import mask_mapping

#: Denetim satırındaki gövde alanı bu boyutta kırpılır. Toplu ürün
#: güncellemesinde gövde megabaytlara çıkabiliyor; denetim izi arşiv değil.
BODY_LIMIT = 4_000


class AuditTrail:
    """`mod_store_api_audit` yazarı."""

    def __init__(self, store: Any, log: Any = None) -> None:
        self._store = store
        self._log = log
        self._table = store.table("audit") if store is not None else "mod_store_api_audit"

    @staticmethod
    def new_request_id() -> str:
        """`X-Bbd-Request-Id` değeri. Aynı zamanda denetim satırının anahtarı.

        Yazma isteği YİNELENMEZ; idempotency bu kimlikle sağlanır: uzak taraf
        aynı kimliği ikinci kez görürse işlemi tekrarlamaz.
        """
        return uuid.uuid4().hex

    async def before(
        self,
        *,
        request_id: str,
        method: str,
        path: str,
        action: str = "",
        reason: str = "",
        actor: str = "",
        dry_run: bool = True,
        body: Any = None,
    ) -> None:
        """İstek çıkmadan satırı yazar. Yazamazsa isteği ENGELLEMEZ.

        Karar: yerel SQLite hıçkırığı yüzünden mağaza işlemini durdurmak,
        izsiz kalmaktan daha çok zarar verir (personel işini yapamaz, hata
        mesajı da anlaşılmaz olur). Yazamama durumu `error` seviyesinde
        loglanır — sessiz kalmaz.
        """
        payload = ""
        if body is not None:
            payload = json.dumps(mask_mapping(body), ensure_ascii=False)[:BODY_LIMIT]
        await self._write(
            f"INSERT INTO {self._table} "
            "(request_id, method, path, action, reason, actor, dry_run, body, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (request_id, method.upper(), path, action, reason, actor,
             1 if dry_run else 0, payload, _now()),
        )

    async def after(self, request_id: str, *, result: str, status: int | None = None) -> None:
        """Sonucu işler: `ok` · `blocked` · `failed` · `error:<kod>`."""
        await self._write(
            f"UPDATE {self._table} SET result = ?, status = ?, finished_at = ? "
            "WHERE request_id = ?",
            (result[:200], status, _now(), request_id),
        )

    async def recent(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Son yazma denemeleri — UDİT ekranı "gönderilemeyenler" için okur."""
        if self._store is None:
            return []
        try:
            return await self._store.fetch_all(
                f"SELECT * FROM {self._table} ORDER BY id DESC LIMIT ?", (max(1, int(limit)),)
            )
        except Exception as failure:  # noqa: BLE001 - okuma da geçidi düşürmez
            self._report("denetim izi okunamadı", failure)
            return []

    async def _write(self, sql: str, params: tuple[Any, ...]) -> None:
        if self._store is None:
            return
        try:
            await self._store.execute(sql, params)
        except Exception as failure:  # noqa: BLE001 - bkz. `before` docstring'i
            self._report("denetim izi yazılamadı", failure)

    def _report(self, message: str, failure: Exception) -> None:
        if self._log is not None:
            self._log.error(message, error=str(failure))


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")
