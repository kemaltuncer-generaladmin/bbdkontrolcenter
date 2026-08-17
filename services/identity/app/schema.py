"""Servise özel tablolar ve göçleri.

Kullanıcı/rol/izin/denetim tabloları BURADA TANIMLANMAZ: onlar
`km_core/store/db.py` → `CORE_SCHEMA` ve `km_core/security/migrations.py`
üzerinden gelir. Bu dosya yalnızca merkezin FAZLADAN taşıdığı şeyleri açar:
kurulumlar, eşleme kodları, danışma kilitleri ve kadro revizyonu.

K5 NOTU. `Store.apply_migration` modül göçlerini `mod_<modül>_` ön ekine
zorlar; burada o yol KULLANILMAZ ve göçler doğrudan işlenir. K5 "bir modül
başka modülün tablosuna dokunamaz" der ve Kontrol Merkezi'nin veritabanı için
yazılmıştır. Burası KENDİ veritabanı olan ayrı bir uygulamadır; tek sahibi bu
servistir, paylaştığı bir komşu yoktur. Göç yine `schema_migrations` içinde
kayda geçer — iki kez uygulanmaz.
"""

from __future__ import annotations

import structlog

from km_core.store.db import Store

log = structlog.get_logger("identity.schema")

OWNER = "identity_service"

# Kurulum kaydı. TOKEN'IN KENDİSİ SAKLANMAZ, sha256'sı saklanır: veritabanı
# sızarsa kimse başka bir makineye "bu makine bizim" dedirtemez.
_0001_INSTALLATIONS = """
CREATE TABLE IF NOT EXISTS installations (
    id            TEXT PRIMARY KEY,
    machine_name  TEXT NOT NULL,
    platform      TEXT NOT NULL,
    version       TEXT NOT NULL,
    public_key    TEXT NOT NULL,
    token_hash    TEXT NOT NULL UNIQUE,
    status        TEXT NOT NULL DEFAULT 'active',   -- active | revoked
    paired_at     TEXT NOT NULL,
    last_seen_at  TEXT,
    revoked_at    TEXT
);

-- Eşleme kodu TEK KULLANIMLIKTIR ve SÜRELİDİR. Kod da düz durmaz; kaydı
-- silmek yerine `used_at` işaretlenir, böylece "bu kod kullanıldı mı"
-- sorusunun cevabı denetimde kalır.
CREATE TABLE IF NOT EXISTS pair_codes (
    code_hash   TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    used_at     TEXT,
    used_by     TEXT,
    note        TEXT
);

-- ADR 0020 §2 — danışma kilidi. UYARIR, ENGELLEMEZ; doğruluğu iyimser kilit
-- sağlar. `expires_at` zorunludur.
CREATE TABLE IF NOT EXISTS advisory_locks (
    id               TEXT PRIMARY KEY,
    resource         TEXT NOT NULL UNIQUE,
    installation_id  TEXT NOT NULL,
    holder_name      TEXT NOT NULL,
    acquired_at      TEXT NOT NULL,
    expires_at       TEXT NOT NULL
);

-- Kadro revizyonu TEK SATIRDIR: kurulum bunu sorar, değişmemişse veri çekmez.
CREATE TABLE IF NOT EXISTS roster_meta (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    revision    INTEGER NOT NULL DEFAULT 1,
    updated_at  TEXT NOT NULL
);

INSERT OR IGNORE INTO roster_meta (id, revision, updated_at)
VALUES (1, 1, datetime('now'));
"""

# Denetim kaydı hangi kurulumdan geldiğini söylemeli: "kim ne yaptı" sorusunun
# yanıtı makine adını da içerir. Sütun `audit_log`'a EKLENİR (ADR 0021 §5).
_0002_AUDIT_INSTALLATION = """
ALTER TABLE audit_log ADD COLUMN installation_id TEXT;
CREATE INDEX IF NOT EXISTS idx_audit_installation ON audit_log (installation_id);
"""

# Kurulum paketi (ADR 0025): dağıtılan sırlar ve modül ayarları.
#
# DEĞER SÜTUNU ŞİFRELİDİR ve şeması bunu söyleyemez — bu yüzden burada yazılı:
# `value` alanı Fernet çıktısıdır, anahtarı `KM_IDENTITY_VAULT_KEY` ortam
# değişkeninde durur ve BU VERİTABANINDA YOKTUR. Dosya sızarsa sırlar açılmaz.
#
# `kind` değerin kurulumda NEREYE yazılacağını söyler (`secret` → kasa,
# `setting` → çekirdek ayar deposu), ne kadar korunacağını değil: ikisi de aynı
# ölçüde şifrelenir.
#
# Revizyon KADRONUNKİNDEN AYRIDIR. Tek sayaç olsaydı her kullanıcı eklendiğinde
# sahadaki bütün kurulumlar bütün sırları yeniden çekerdi.
_0003_PROVISIONING = """
CREATE TABLE IF NOT EXISTS provisioning_items (
    key         TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,          -- secret | setting
    value       TEXT NOT NULL,          -- Fernet ile şifreli JSON; DÜZ DEĞİL
    updated_at  TEXT NOT NULL,
    updated_by  TEXT                    -- users.id; acil yoldan gelirse NULL
);

CREATE TABLE IF NOT EXISTS provisioning_meta (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    revision    INTEGER NOT NULL DEFAULT 1,
    updated_at  TEXT NOT NULL
);

INSERT OR IGNORE INTO provisioning_meta (id, revision, updated_at)
VALUES (1, 1, datetime('now'));
"""

SERVICE_MIGRATIONS: list[tuple[str, str]] = [
    ("0001_installations", _0001_INSTALLATIONS),
    ("0002_audit_installation", _0002_AUDIT_INSTALLATION),
    ("0003_provisioning", _0003_PROVISIONING),
]


async def apply_service_migrations(store: Store) -> list[str]:
    """Uygulanmamış servis göçlerini işler; uygulananların adını döndürür."""
    applied = await store.applied_migrations(OWNER)
    fresh: list[str] = []
    for name, sql in SERVICE_MIGRATIONS:
        if name in applied:
            continue
        await store.db.executescript(sql)
        await store.db.execute(
            "INSERT INTO schema_migrations (owner, name, applied_at) "
            "VALUES (?, ?, datetime('now'))",
            (OWNER, name),
        )
        await store.db.commit()
        log.info("servis göçü uygulandı", migration=name)
        fresh.append(name)
    return fresh
