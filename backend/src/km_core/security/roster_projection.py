"""Merkezden gelen kadronun YEREL kayda yansıtılması (ADR 0021 §2).

Önbellek (`km_platform/identity_sync/cache.py`) merkezin ham kopyasıdır; giriş
ise `users` / `roles` / `user_roles` tablolarından yapılır. Aradaki köprü
buradadır. Köprü kurulmazsa **merkezde açılan kullanıcı ikinci kurulumda giriş
yapamaz** — ADR 0021'in asıl amacı budur.

Yansıtma ÇEKİRDEKTEDİR, yetenekte değil: yazılan tablolar çekirdeğin kendi
doğruluk kaydıdır ve kimlik modül değildir (CLAUDE.md — kavram ayrımı).
`km_platform/identity_sync` yalnız veriyi getirir, karar vermez.

Üç kural bağlayıcıdır:

  · **Yerelde doğmuş kayıt silinmez, ezilmez.** `users.origin` sütunu satırın
    merkezin kopyası mı (`central`) yoksa bu kurulumun kendi kaydı mı (`local`)
    olduğunu söyler. Yerel satıra yansıtma dokunmaz; kendisiyle aynı kimliği
    taşıyan bir merkez kaydı gelse bile atlanır ve loga düşer.
  · **Merkezden gelen pasifleştirme uygulanır.** Kadrodan düşen ya da `status`
    alanı `disabled` gelen merkez kaydı yerelde de pasifleşir ve açık oturumu
    kapanır. SATIR SİLİNMEZ: kimin ne zaman pasifleştiği kaydın kendisinde
    kalır ve yeniden aktifleşirse aynı kimlikle döner.
  · **İzin atamaları YALNIZ EKLENİR.** `role_permissions` bu kurulumdaki modül
    manifestlerinden de dolar (`Identity.grant_defaults`); merkezde olmayan bir
    izni silmek, o modülün ekranını burada yetkisiz bırakırdı. Merkezde geri
    alınan bir izin bu yüzden yerelde kendiliğinden düşmez — bilinen ve
    kabul edilmiş sınır.

**PEPPER'A BAĞLIDIR.** Kadro `secret_lookup` değerlerini taşır ve bu değer
sabit anahtarlı (pepper) bir HMAC'tir (`Identity.secret_lookup`). Kurulumun
kasasındaki `core.pin_pepper` merkezin `KM_IDENTITY_PEPPER` değeriyle AYNI
DEĞİLSE yansıtılan satırlar hiçbir PIN'le eşleşmez ve merkezde açılan kullanıcı
yine giriş yapamaz — tabloda görünüyor olsa bile. Şart `services/identity/README.md`
→ "`KM_IDENTITY_PEPPER` — bir kez belirlenir, bir daha değişmez" başlığında
yazılıdır; burada koda da not düşülür çünkü belirti (giriş çalışmıyor) sebebi
(pepper farklı) hiç ele vermez.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

import structlog

from km_core.store.db import Store

log = structlog.get_logger("km.security")

# `users.origin` alabileceği iki değer. Üçüncüsü yoktur: bir satır ya merkezin
# kopyasıdır ya da bu kurulumun kendi kaydı.
ORIGIN_LOCAL = "local"
ORIGIN_CENTRAL = "central"

# Merkezin kopyası olan satıra yazılan alanlar. AÇIK LİSTE: kadroya ileride
# eklenen bir alanın sessizce yerel tabloya sızmaması için.
#
# `failed_attempts`, `locked_until` ve `last_login_at` BİLEREK YOK: bunlar bu
# makinedeki giriş denemelerinin sayacıdır, merkezin bilgisi değildir. Merkezden
# yansıtılsalardı buradaki bir kilitlenme her senkronda sıfırlanırdı.
_INSERT_USER = """
INSERT INTO users (
    id, first_name, last_name, title, department, org_scope,
    phone_mobile, phone_ext, email, note, directory_visible, status,
    password_hash, secret_lookup, password_set_at, revision, origin,
    pin_hash, pin_lookup, pin_set_at, created_at, updated_at
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(id) DO UPDATE SET
    first_name        = excluded.first_name,
    last_name         = excluded.last_name,
    title             = excluded.title,
    department        = excluded.department,
    org_scope         = excluded.org_scope,
    phone_mobile      = excluded.phone_mobile,
    phone_ext         = excluded.phone_ext,
    email             = excluded.email,
    note              = excluded.note,
    directory_visible = excluded.directory_visible,
    status            = excluded.status,
    password_hash     = excluded.password_hash,
    secret_lookup     = excluded.secret_lookup,
    password_set_at   = excluded.password_set_at,
    revision          = excluded.revision,
    origin            = excluded.origin,
    updated_at        = excluded.updated_at
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


async def projected_revision(store: Store) -> int | None:
    """En son yansıtılan kadro revizyonu. Hiç yansıtılmadıysa `None`."""
    row = await store.fetch_one("SELECT revision FROM roster_projection WHERE id = 1")
    if row is None:
        return None
    value = row.get("revision")
    return int(value) if isinstance(value, int) else None


async def project_roster(store: Store, payload: dict[str, Any]) -> dict[str, Any]:
    """Önbellekteki kadroyu yerel tablolara yansıtır.

    `revision` en son yansıtılanla aynıysa HİÇBİR YAZMA YAPILMAZ: yansıtma her
    giriş denemesinde çağrılır ve kadro değişmediği sürece bedeli sıfırdır.

    Hata yükseltmez. Yansıtma başarısız olursa giriş yolu YEREL kayıtla devam
    eder (K7); sessiz kalmaz, loga düşer.
    """
    users = payload.get("users")
    if not isinstance(users, list):
        # Merkez `changed: false` dediğinde önbellekte kullanıcı listesi
        # bulunmaz; yansıtacak bir şey de yoktur.
        return {"projected": False, "reason": "kadro yok"}

    raw_revision = payload.get("revision")
    revision = int(raw_revision) if isinstance(raw_revision, int) else None

    try:
        if revision is not None and revision == await projected_revision(store):
            return {"projected": False, "reason": "değişmedi", "revision": revision}
        return await _project(store, payload, users, revision)
    except sqlite3.Error as error:
        # Göç koşmamış ya da veritabanı bir an için kilitli: giriş yolu
        # DÜŞMEZ, yerel kayıtla devam eder. Sessizce "yansıttım" demez.
        log.error("kadro yansıtılamadı", error=str(error), revision=revision)
        return {"projected": False, "reason": str(error)}


async def _project(store: Store, payload: dict[str, Any], users: list[Any],
                   revision: int | None) -> dict[str, Any]:
    # Roller ÖNCE: kullanıcı-rol bağı boş bir role işaret etmesin.
    await _mirror_roles(store, payload.get("roles") or [])

    origins = {
        str(row["id"]): str(row.get("origin") or ORIGIN_LOCAL)
        for row in await store.fetch_all("SELECT id, origin FROM users")
    }

    mirrored: list[str] = []
    protected: list[str] = []
    for entry in users:
        if not isinstance(entry, dict):
            continue
        user_id = str(entry.get("id") or "").strip()
        if not user_id:
            continue
        if origins.get(user_id) == ORIGIN_LOCAL:
            # YEREL KAYIT EZİLMEZ. Kimlikler uuid olduğu için bu çakışma
            # beklenmez; olursa yerel kayıt kazanır ve durum loga düşer.
            protected.append(user_id)
            log.warning("yerel kullanıcı korundu, merkez kaydı atlandı", user=user_id)
            continue
        await _mirror_user(store, entry)
        await _mirror_user_roles(store, user_id, entry.get("roles") or [])
        mirrored.append(user_id)

    disabled = await _disable_missing(store, set(mirrored))
    await _mirror_grants(store, payload)
    await _close_sessions_of_inactive(store)

    await store.execute(
        "INSERT INTO roster_projection (id, revision, users, projected_at) "
        "VALUES (1, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET revision = excluded.revision, "
        "users = excluded.users, projected_at = excluded.projected_at",
        (revision, len(mirrored), _now()),
    )
    log.info("kadro yansıtıldı", revision=revision, users=len(mirrored),
             disabled=len(disabled), local=len(protected))
    return {
        "projected": True,
        "revision": revision,
        "users": len(mirrored),
        "disabled": disabled,
        "localKept": protected,
    }


async def _mirror_roles(store: Store, roles: list[Any]) -> None:
    """Merkezin rollerini açar/tazeler. YEREL ROL SİLİNMEZ."""
    for role in roles:
        if not isinstance(role, dict) or not role.get("id"):
            continue
        role_id = str(role["id"])
        await store.execute(
            "INSERT INTO roles (id, name, description, builtin) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name = excluded.name, "
            "description = excluded.description",
            (role_id, str(role.get("name") or role_id), role.get("description"),
             int(bool(role.get("builtin")))),
        )


def _user_values(entry: dict[str, Any], *, with_secret: bool) -> list[Any]:
    user_id = str(entry["id"])
    stamp = str(entry.get("updated_at") or _now())
    return [
        user_id,
        str(entry.get("first_name") or ""),
        str(entry.get("last_name") or ""),
        entry.get("title"),
        entry.get("department"),
        str(entry.get("org_scope") or "org"),
        entry.get("phone_mobile"),
        entry.get("phone_ext"),
        entry.get("email"),
        entry.get("note"),
        int(bool(entry.get("directory_visible", True))),
        str(entry.get("status") or "active"),
        entry.get("password_hash") if with_secret else None,
        entry.get("secret_lookup") if with_secret else None,
        entry.get("password_set_at") if with_secret else None,
        int(entry.get("revision") or 1),
        ORIGIN_CENTRAL,
        # Eski PIN sütunları DÜŞÜRÜLMEDİ ve `NOT NULL`. Giriş yolunda asla
        # eşleşmeyecek bir yer tutucu yazılır — `Identity.create_user` ile aynı
        # desen; `pin-yok:<id>` HMAC hexdigest biçiminde olmadığı için hiçbir
        # sırla çakışmaz ve kimlik başına benzersizdir (sütun UNIQUE).
        "",
        f"pin-yok:{user_id}",
        stamp,
        str(entry.get("created_at") or stamp),
        stamp,
    ]


async def _mirror_user(store: Store, entry: dict[str, Any]) -> None:
    """Tek kullanıcıyı yansıtır.

    `secret_lookup` sütunu UNIQUE'tir: yerelde açılmış bir kullanıcı merkezdeki
    kullanıcıyla AYNI PIN'i taşıyorsa yazma çakışır. O durumda merkez kaydı yine
    açılır ama SIRSIZ açılır — kişi bu makinede giriş yapamaz, çünkü aynı PIN'i
    iki kişiye vermek `Identity`nin en temel kuralını (sır kimliği belirler)
    çiğnerdi. Çakışma loga düşer; çözüm merkezde PIN'i değiştirmektir.
    """
    try:
        await store.execute(_INSERT_USER, _user_values(entry, with_secret=True))
    except sqlite3.IntegrityError as error:
        log.warning(
            "merkez kullanıcısının PIN'i yerelde kullanımda; kayıt sırsız yansıtıldı",
            user=str(entry.get("id")), error=str(error),
        )
        await store.execute(_INSERT_USER, _user_values(entry, with_secret=False))


async def _mirror_user_roles(store: Store, user_id: str, roles: list[Any]) -> None:
    """Merkez kaydının rolleri MERKEZDEKİNİN AYNISI olur.

    Yalnız `origin='central'` kullanıcılar için çağrılır; yerel kullanıcının rol
    atamalarına dokunulmaz.
    """
    await store.execute("DELETE FROM user_roles WHERE user_id = ?", (user_id,))
    rows = [(user_id, str(role)) for role in roles if role]
    if rows:
        await store.execute_many(
            "INSERT OR IGNORE INTO user_roles (user_id, role_id) VALUES (?, ?)", rows
        )


async def _mirror_grants(store: Store, payload: dict[str, Any]) -> None:
    """Rol → izin bağını EKLER (modül başlığındaki üçüncü kural)."""
    grants = (payload.get("grants") or {}).get("role_permissions") or []
    rows = [
        (str(grant["role_id"]), str(grant["permission"]))
        for grant in grants
        if isinstance(grant, dict) and grant.get("role_id") and grant.get("permission")
    ]
    if rows:
        await store.execute_many(
            "INSERT OR IGNORE INTO role_permissions (role_id, permission) VALUES (?, ?)",
            rows,
        )


async def _disable_missing(store: Store, keep: set[str]) -> list[str]:
    """Kadrodan düşen merkez kaydını PASİFLEŞTİRİR — SİLMEZ.

    Silmek, kaydın ne zaman açıldığını ve denetim izindeki kimliğini götürürdü;
    merkezde yeniden aktifleşen kullanıcı da yabancı bir kimlikle geri dönerdi.
    """
    rows = await store.fetch_all(
        "SELECT id FROM users WHERE origin = ? AND status = 'active'", (ORIGIN_CENTRAL,)
    )
    vanished = [str(row["id"]) for row in rows if str(row["id"]) not in keep]
    for user_id in vanished:
        await store.execute(
            "UPDATE users SET status = 'disabled', updated_at = ?, "
            "revision = revision + 1 WHERE id = ?",
            (_now(), user_id),
        )
        log.info("merkezden düşen kullanıcı pasifleştirildi", user=user_id)
    return vanished


async def _close_sessions_of_inactive(store: Store) -> None:
    """Pasifleşen merkez kullanıcısının AÇIK OTURUMU DA KAPANIR.

    `Identity.set_status` yerel yolda aynısını yapar; merkezden gelen
    pasifleştirmenin daha zayıf olması, uzaktan kapatılan bir hesabın bu
    makinede açık kalmasına izin verirdi.
    """
    await store.execute(
        "DELETE FROM sessions WHERE user_id IN "
        "(SELECT id FROM users WHERE origin = ? AND status != 'active')",
        (ORIGIN_CENTRAL,),
    )
