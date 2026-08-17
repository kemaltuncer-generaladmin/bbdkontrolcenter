"""Danışma kilitleri (ADR 0020 §2).

**KİLİT UYARIR, ENGELLEMEZ.** Doğruluğu iyimser kilit (`revision`) sağlar;
buradaki kilit yalnız boşa emeği önler. Bu yüzden kilit servisi düştüğünde iş
durmaz (K7) ve kurulum kilit alamadığı için yazmayı bırakmaz.

**TTL ZORUNLUDUR.** Süresiz kilit, çöken bir istemcinin kaydı sonsuza
kapatması ve bunu açmanın yolunun olmaması demektir. Süresi geçmiş kilit her
istekte temizlenir; ayrı bir bakım işine bağlanmaz — koşmayan bir bakım işi,
olmayan bir TTL'dir.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from km_core.store.db import Store


def _now() -> datetime:
    return datetime.now(UTC)


def _stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def entry(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "resource": row["resource"],
        "installationId": row["installation_id"],
        "holderName": row["holder_name"],
        "acquiredAt": row["acquired_at"],
        "expiresAt": row["expires_at"],
    }


async def purge_expired(store: Store) -> None:
    await store.execute(
        "DELETE FROM advisory_locks WHERE expires_at < ?", (_stamp(_now()),)
    )


async def acquire(store: Store, *, resource: str, installation_id: str, holder_name: str,
                  ttl_seconds: int, max_ttl_seconds: int) -> tuple[dict[str, Any], bool]:
    """Kilit almayı dener.

    Dönüş: (kayıt, alındı_mı). Alınamadıysa **kimin tuttuğu** döner — ADR 0020
    §1'in 409 kuralıyla aynı gerekçe: "kilitli" demek yetmez, kimin ne zaman
    tuttuğu söylenmezse kullanıcı bekleyeceğini bilmez.

    AYNI KURULUM AYNI KAYNAĞI YENİDEN İSTERSE kilit TAZELENİR: ekran açık
    kaldıkça yenilenmesi beklenen davranıştır.
    """
    await purge_expired(store)
    ttl = max(1, min(int(ttl_seconds), int(max_ttl_seconds)))
    expires = _now() + timedelta(seconds=ttl)

    current = await store.fetch_one(
        "SELECT * FROM advisory_locks WHERE resource = ?", (resource,)
    )
    if current is not None and str(current["installation_id"]) != installation_id:
        return entry(current), False

    if current is not None:
        await store.execute(
            "UPDATE advisory_locks SET holder_name = ?, expires_at = ? WHERE resource = ?",
            (holder_name, _stamp(expires), resource),
        )
        fresh = await store.fetch_one(
            "SELECT * FROM advisory_locks WHERE resource = ?", (resource,)
        )
        return entry(fresh or current), True

    lock_id = str(uuid.uuid4())
    await store.execute(
        "INSERT INTO advisory_locks (id, resource, installation_id, holder_name, "
        "acquired_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
        (lock_id, resource, installation_id, holder_name, _stamp(_now()), _stamp(expires)),
    )
    row = await store.fetch_one("SELECT * FROM advisory_locks WHERE id = ?", (lock_id,))
    return entry(row or {}), True


async def release(store: Store, lock_id: str, *, installation_id: str) -> bool:
    """Kilidi bırakır. YALNIZ TUTAN KURULUM BIRAKABİLİR — başka bir kurulumun
    kilidini düşürmek, uyarıyı işlevsiz kılardı."""
    row = await store.fetch_one("SELECT * FROM advisory_locks WHERE id = ?", (lock_id,))
    if row is None or str(row["installation_id"]) != installation_id:
        return False
    await store.execute("DELETE FROM advisory_locks WHERE id = ?", (lock_id,))
    return True
