"""Kadro: kullanıcılar, roller ve izin atamaları — merkezin doğruluk kaynağı.

ADR 0021 §2: kadro merkezde tutulur, her kurulum kendi YEREL ÖNBELLEĞİNE yazar
ve girişte oraya bakar. Önbellek `revision` ile tazelenir; değişmemişse veri
çekilmez.

**KADRO PIN HASH'İNİ TAŞIR.** `password_hash` (Argon2id) ve `secret_lookup`
(peppered HMAC) roster'a girer; girmezse çevrimdışı giriş imkânsız olur ve
ADR 0021 §2'nin pazarlık dışı saydığı özellik ilk günden düşer. Bunun bedeli
açıktır ve kabul edilmiştir: paylaşılan pepper, TLS ve token iptali bu bedelin
karşılığıdır. DÜZ PIN HİÇBİR ZAMAN TAŞINMAZ — merkezde de yoktur.
"""

from __future__ import annotations

from typing import Any

from km_core.store.db import Store

# Kadroda taşınan kullanıcı alanları. AÇIK BEYAZ LİSTE: `SELECT *` sonucunu
# olduğu gibi göndermek, ileride eklenen bir sütunu sessizce ağa çıkarırdı.
USER_FIELDS = (
    "id",
    "first_name",
    "last_name",
    "title",
    "department",
    "org_scope",
    "phone_mobile",
    "phone_ext",
    "email",
    "note",
    "directory_visible",
    "status",
    "password_hash",
    "secret_lookup",
    "password_set_at",
    "failed_attempts",
    "locked_until",
    "created_at",
    "updated_at",
    "revision",
)


async def revision(store: Store) -> int:
    row = await store.fetch_one("SELECT revision FROM roster_meta WHERE id = 1")
    return int(row["revision"]) if row else 1


async def bump_revision(store: Store) -> int:
    """Kadro değişti. Kurulumlar bir sonraki sorgularında farkı görür."""
    await store.execute(
        "UPDATE roster_meta SET revision = revision + 1, updated_at = datetime('now')"
        " WHERE id = 1"
    )
    return await revision(store)


def user_entry(row: dict[str, Any], roles: list[str]) -> dict[str, Any]:
    entry = {name: row.get(name) for name in USER_FIELDS}
    entry["directory_visible"] = bool(entry.get("directory_visible", 1))
    entry["revision"] = int(entry.get("revision") or 1)
    entry["roles"] = roles
    return entry


async def build(store: Store) -> dict[str, Any]:
    """Tam kadro. Kurulum bunu olduğu gibi önbelleğine yazar."""
    users = await store.fetch_all("SELECT * FROM users ORDER BY first_name, last_name")
    roles = await store.fetch_all("SELECT id, name, description, builtin FROM roles ORDER BY id")
    user_roles = await store.fetch_all(
        "SELECT user_id, role_id FROM user_roles ORDER BY user_id, role_id"
    )
    role_permissions = await store.fetch_all(
        "SELECT role_id, permission FROM role_permissions ORDER BY role_id, permission"
    )

    by_user: dict[str, list[str]] = {}
    for grant in user_roles:
        by_user.setdefault(str(grant["user_id"]), []).append(str(grant["role_id"]))

    return {
        "revision": await revision(store),
        "users": [user_entry(row, by_user.get(str(row["id"]), [])) for row in users],
        "roles": [
            {
                "id": row["id"],
                "name": row["name"],
                "description": row["description"],
                "builtin": bool(row["builtin"]),
            }
            for row in roles
        ],
        # `grants` iki bağı birden taşır: rol → izin ve kullanıcı → rol. İkisi
        # de kadronun parçasıdır; biri eksik gelirse kurulum yetkiyi yeniden
        # kuramaz.
        "grants": {
            "role_permissions": [
                {"role_id": row["role_id"], "permission": row["permission"]}
                for row in role_permissions
            ],
            "user_roles": [
                {"user_id": row["user_id"], "role_id": row["role_id"]} for row in user_roles
            ],
        },
    }
