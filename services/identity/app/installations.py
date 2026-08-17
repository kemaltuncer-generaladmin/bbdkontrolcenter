"""Cihaz eşlemesi (ADR 0021 §4).

Akış — KDS kasalarında çalışan desenin aynısı:

  1. Yönetici **tek kullanımlık, süreli** kod üretir (`create_pair_code`).
  2. Yeni kurulum ilk açılışta kodu girer, kendi anahtar çiftini üretmiş
     olarak açık anahtarını gönderir (`pair`).
  3. Karşılığında kurulum token'ı alır; token yalnızca O AN döner, sonra
     veritabanında yalnız sha256'sı kalır.
  4. Token iptal edilebilir (`revoke`); iptal edilen kurulum kadro çekemez.

KOD DA HASH'LENEREK SAKLANIR. Süreli ve tek kullanımlık olması, veritabanını
okuyabilen birinin bekleyen bir kodu alıp kendi makinesini eşlemesini
engellemez — hash engeller.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from km_core.store.db import Store

from .auth import constant_time_equals, hash_token

# Kod insan tarafından yazılır: rakam, sekiz hane, ekranda 4+4 gösterilir.
# Harf karıştırmak (O/0, I/1) telefonla okunurken hata üretiyordu.
PAIR_CODE_DIGITS = 8


class PairError(RuntimeError):
    """Eşleme reddedildi. Sebep AYIRT EDİLMEZ — dışarıya tek cümle çıkar."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def generate_pair_code() -> str:
    return "".join(secrets.choice("0123456789") for _ in range(PAIR_CODE_DIGITS))


async def create_pair_code(store: Store, *, ttl_seconds: int, note: str | None = None) -> dict[str, Any]:
    code = generate_pair_code()
    expires = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
    await store.execute(
        "INSERT INTO pair_codes (code_hash, created_at, expires_at, note) VALUES (?, ?, ?, ?)",
        (hash_token(code), _now(), expires.isoformat(timespec="seconds"), note),
    )
    # KOD YALNIZ BURADA DÜZ GÖRÜNÜR. Yönetici ekranına düşer, veritabanına
    # düşmez.
    return {"code": code, "expiresAt": expires.isoformat(timespec="seconds")}


async def _consume_code(store: Store, code: str) -> None:
    """Kodu tüketir. Süresi geçmiş, kullanılmış ve hiç var olmayan kod AYNI
    hatayı verir."""
    digest = hash_token(code)
    rows = await store.fetch_all(
        "SELECT code_hash, expires_at, used_at FROM pair_codes WHERE used_at IS NULL"
    )
    matched: dict[str, Any] | None = None
    for row in rows:
        if constant_time_equals(str(row["code_hash"]), digest):
            matched = row
    if matched is None:
        raise PairError("Eşleme kodu geçersiz.")
    if datetime.fromisoformat(str(matched["expires_at"])) < datetime.now(UTC):
        raise PairError("Eşleme kodu geçersiz.")
    await store.execute(
        "UPDATE pair_codes SET used_at = ? WHERE code_hash = ?", (_now(), matched["code_hash"])
    )


async def pair(store: Store, *, code: str, public_key: str, machine_name: str,
               platform: str, version: str) -> dict[str, Any]:
    """Kodu tüketir, kurulumu kaydeder ve token'ı TEK SEFER döndürür."""
    await _consume_code(store, code)

    installation_id = str(uuid.uuid4())
    token = secrets.token_urlsafe(32)
    await store.execute(
        "INSERT INTO installations (id, machine_name, platform, version, public_key, "
        "token_hash, status, paired_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)",
        (
            installation_id, machine_name, platform, version, public_key,
            hash_token(token), _now(), _now(),
        ),
    )
    await store.execute(
        "UPDATE pair_codes SET used_by = ? WHERE code_hash = ?",
        (installation_id, hash_token(code)),
    )
    return {"installationId": installation_id, "token": token}


def entry(row: dict[str, Any]) -> dict[str, Any]:
    """Kurulum listesi kaydı. `token_hash` ve `public_key` DIŞARI ÇIKMAZ."""
    return {
        "id": row["id"],
        "machineName": row["machine_name"],
        "platform": row["platform"],
        "version": row["version"],
        "status": row["status"],
        "pairedAt": row["paired_at"],
        "lastSeenAt": row["last_seen_at"],
        "revokedAt": row["revoked_at"],
    }


async def listing(store: Store) -> list[dict[str, Any]]:
    rows = await store.fetch_all("SELECT * FROM installations ORDER BY paired_at DESC")
    return [entry(row) for row in rows]


async def revoke(store: Store, installation_id: str) -> dict[str, Any] | None:
    """Token'ı iptal eder. KAYIT SİLİNMEZ: hangi makinenin ne zaman eşlendiği
    ve ne zaman iptal edildiği denetimin parçasıdır."""
    row = await store.fetch_one("SELECT * FROM installations WHERE id = ?", (installation_id,))
    if row is None:
        return None
    await store.execute(
        "UPDATE installations SET status = 'revoked', revoked_at = ? WHERE id = ?",
        (_now(), installation_id),
    )
    # İptal edilen kurulumun tuttuğu danışma kilitleri de bırakılır; aksi hâlde
    # kayıt TTL dolana dek boş yere uyarı verirdi.
    await store.execute(
        "DELETE FROM advisory_locks WHERE installation_id = ?", (installation_id,)
    )
    fresh = await store.fetch_one("SELECT * FROM installations WHERE id = ?", (installation_id,))
    return entry(fresh) if fresh else None
