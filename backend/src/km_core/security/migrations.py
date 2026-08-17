"""Çekirdeğin kimlik göçleri.

ADR 0016 (giriş şifre ile) **reddedildi**, ama göçü bu dosyada koştu ve GERİ
ALINMADI: uygulanmış bir göçü geri sarmak veri riskidir. Sütun ve izin adları
bu yüzden "password" der; tutulan değer 6 haneli PIN'dir (bkz. `identity.py`
başlığı — "adı şifre, kuralı PIN").

`km_core/store/db.py` içindeki `CORE_SCHEMA` yalnız **yeni** bir veritabanını
kurar: `CREATE TABLE IF NOT EXISTS` var olan tabloya sütun eklemez. Zaten
çalışmış bir kurulumda `password_hash`, `secret_lookup` ve `password_set_at`
sütunları bu dosyadaki göçlerle açılır.

İki kural bağlayıcıdır:

  · **Hiçbir kayıt kaybolmaz.** `pin_hash` / `pin_lookup` / `pin_set_at`
    DÜŞÜRÜLMEZ; yalnız kullanılmaz hâle gelir. Yeni sütunda sırrı olmayan
    kullanıcı eski PIN'iyle doğrulanır, oturumu ancak yeni PIN'ini kurduktan
    sonra açılır — kimse kilitlenmez.
  · **Göçler iki kez uygulanmaz.** `schema_migrations` tablosu (`owner='core'`)
    zaten vardır; hangi göçün uygulandığı oradan okunur.

Her göç, çalıştırılmadan önce veritabanının GERÇEK hâline bakar: yeni kurulumda
sütun `CORE_SCHEMA` ile zaten gelmiştir ve o adım atlanır. Böylece aynı kod hem
yeni hem eski veritabanında çalışır.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import structlog

from km_core.store.db import Store

log = structlog.get_logger("km.security")

OWNER = "core"


async def table_columns(store: Store, table: str) -> set[str]:
    rows = await store.fetch_all(f"PRAGMA table_info({table})")
    return {str(row["name"]) for row in rows}


async def _password_columns(store: Store) -> str:
    """Yeni sır sütunlarını EKLER, eski PIN sütunlarına dokunmaz."""
    columns = await table_columns(store, "users")
    parts: list[str] = []
    if "password_hash" not in columns:
        parts.append("ALTER TABLE users ADD COLUMN password_hash TEXT;")
    if "secret_lookup" not in columns:
        parts.append("ALTER TABLE users ADD COLUMN secret_lookup TEXT;")
    if "password_set_at" not in columns:
        parts.append("ALTER TABLE users ADD COLUMN password_set_at TEXT;")
    # `ALTER TABLE` ile UNIQUE sütun eklenemez; benzersizlik indeksle kurulur.
    # NULL değerler SQLite'ta benzersizlik kısıtına takılmaz — yeni sütuna
    # henüz yazılmamış kullanıcılar birbirini engellemez.
    parts.append(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_secret_lookup ON users (secret_lookup);"
    )
    return "\n".join(parts)


async def _users_revision(store: Store) -> str:
    """ADR 0020 — iyimser kilit. `expectedRevision` gönderen istemci gerçekten
    korunur; korunuyormuş gibi davranmak hiç korumamaktan kötüdür."""
    if "revision" in await table_columns(store, "users"):
        return "-- revision sütunu zaten var"
    return "ALTER TABLE users ADD COLUMN revision INTEGER NOT NULL DEFAULT 1;"


async def _rename_set_pin_permission(store: Store) -> str:
    """`users.set_pin` → `users.set_password`.

    İzin ANAHTARI değişti; rol atamaları veritabanında durduğu için ad
    değişikliği tek başına yetmez. Önce yeni satır eklenir, sonra eskisi
    silinir: arada hiçbir rol yetkisiz kalmaz.

    ADR 0016 reddedilince anahtar GERİ ÇEVRİLMEDİ: bu göç koşmuş kurulumlarda
    ikinci bir göç yazmak demekti. `users.set_password` bugün PIN atama/sıfırlama
    yetkisidir.
    """
    del store  # bu göç veritabanının hâline bakmaz
    return """
INSERT OR IGNORE INTO role_permissions (role_id, permission)
SELECT role_id, replace(permission, 'users.set_pin', 'users.set_password')
FROM role_permissions
WHERE permission = 'users.set_pin' OR permission LIKE 'users.set_pin:%';

DELETE FROM role_permissions
WHERE permission = 'users.set_pin' OR permission LIKE 'users.set_pin:%';
"""


async def _roster_projection(store: Store) -> str:
    """ADR 0021 §2 — merkezden gelen kadro YEREL tablolara yansıtılır.

    İki şey açılır:

      · **`users.origin`.** Satır merkezin kopyası mı (`central`), yoksa bu
        kurulumda mı doğdu (`local`)? Ayırt edilemezse bir sonraki yansıtma
        yerelde elle açılmış kaydı sessizce ezer ya da siler. Varsayılan
        `local`: göç koştuğu anda tabloda ne varsa bu kurulumun kendi kaydıdır
        — merkez henüz hiçbir şey göndermemiştir.
      · **`roster_projection`.** En son hangi `revision` yansıtıldı. Her giriş
        denemesinde tüm kadroyu yeniden yazmamak için buraya bakılır.

    Yansıtmanın kendisi `km_core/security/roster_projection.py` içindedir.
    """
    parts: list[str] = []
    if "origin" not in await table_columns(store, "users"):
        parts.append("ALTER TABLE users ADD COLUMN origin TEXT NOT NULL DEFAULT 'local';")
    parts.append("""
CREATE TABLE IF NOT EXISTS roster_projection (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    revision      INTEGER,
    users         INTEGER NOT NULL DEFAULT 0,
    projected_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_users_origin ON users (origin);
""")
    return "\n".join(parts)


async def _identity_audit_queue(store: Store) -> str:
    """ADR 0021 §5 — merkeze itilemeyen denetim kaydı YERELDE BİRİKİR.

    ADR'nin cümlesi kesindir: kayıt "yerelde birikir ve yeniden denenir; asla
    düşürülmez". Gönderim anında kaybolan bir kayıt, "kim ne yaptı" sorusunu
    tam da ağın koptuğu anlar için cevapsız bırakırdı.

    Tablo çekirdeğin deposundadır çünkü denetim izi (`audit_log`) da oradadır;
    kuyruk onun gönderilmeyi bekleyen kuyruğudur. Kuyruğu işleyen kod
    `km_platform/identity_sync/queue.py` içindedir — tablo burada açılır çünkü
    açılışta koşan tek göç yolu budur.

    `next_attempt_at` geri çekilmeli yeniden denemeyi taşır: merkez kapalıyken
    her saniye yeniden denemek ne kaydı kurtarır ne de ağı.
    """
    del store  # bu göç veritabanının hâline bakmaz
    return """
CREATE TABLE IF NOT EXISTS identity_audit_queue (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    entry            TEXT NOT NULL,      -- tek denetim kaydı, JSON
    queued_at        TEXT NOT NULL,
    attempts         INTEGER NOT NULL DEFAULT 0,
    next_attempt_at  TEXT NOT NULL,
    last_error       TEXT
);

CREATE INDEX IF NOT EXISTS idx_identity_audit_queue_next
    ON identity_audit_queue (next_attempt_at);
"""


CORE_MIGRATIONS: list[tuple[str, Callable[[Store], Awaitable[str]]]] = [
    ("0001_password_columns", _password_columns),
    ("0002_users_revision", _users_revision),
    ("0003_users_set_password_permission", _rename_set_pin_permission),
    ("0004_roster_projection", _roster_projection),
    ("0005_identity_audit_queue", _identity_audit_queue),
]


async def apply_core_migrations(store: Store) -> list[str]:
    """Uygulanmamış çekirdek göçlerini sırayla işler; uygulananları döndürür."""
    applied = await store.applied_migrations(OWNER)
    fresh: list[str] = []
    for name, build in CORE_MIGRATIONS:
        if name in applied:
            continue
        await store.apply_migration(OWNER, name, await build(store))
        log.info("çekirdek göçü uygulandı", migration=name)
        fresh.append(name)
    return fresh
