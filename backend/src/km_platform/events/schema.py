"""`events.outbox` tabloları — çekirdek sahipli göçler (ADR 0029).

Tablolar modül değil PLATFORM altyapısıdır: adları `mod_` önekini taşımaz, göç
kaydının sahibi `core`'dur (ayar deposu göçü kalıbı, `0010_settings_store`).
Şemayı kuran tek yer yine `km_core/store/bootstrap.py` → `build_schema`:
yerel SQLite ile merkezdeki PostgreSQL ayrışmasın.

İki göç vardır. `0012` tabloları açar (eklemeli; var olan hiçbir şeye
dokunmaz). `0013` indeksi `-- km: concurrently` işaretiyle açar: merkezde
`CREATE INDEX CONCURRENTLY` koşar, tablo yazmaya kapanmaz.
"""

from __future__ import annotations

import structlog

from km_core.store.base import StoreLike

log = structlog.get_logger("platform.events.schema")

OWNER = "core"

OUTBOX_TABLE = "km_event_outbox"
CONSUMER_TABLE = "km_event_consumer"

MIGRATIONS: list[tuple[str, str]] = [
    ("0012_event_outbox", f"""
-- Kalıcı olay outbox'u (km-merkezi-senkron §3.1). `seq` commit sırasıdır:
-- satırlar commit'ten hemen önce outbox kilidi altında yazılır.
-- `fields` ve `data` JSON metnidir; `data` kişisel veri TAŞIMAZ (A6).
CREATE TABLE IF NOT EXISTS {OUTBOX_TABLE} (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id      TEXT NOT NULL UNIQUE,
    type          TEXT NOT NULL,
    subject_kind  TEXT NOT NULL,
    subject_id    TEXT,
    revision      INTEGER,
    fields        TEXT NOT NULL DEFAULT '[]',
    data          TEXT,
    origin        TEXT NOT NULL DEFAULT 'km',
    causation_id  TEXT,
    occurred_at   TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

-- Tüketici imleçleri: gece budaması hiçbir tüketicinin geçmediği olayı silmez.
-- `#pruned` adlı satır budamanın ulaştığı `seq`'i tutar (410 events_expired).
CREATE TABLE IF NOT EXISTS {CONSUMER_TABLE} (
    name        TEXT PRIMARY KEY,
    last_seq    INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL
);
"""),
    ("0013_event_outbox_indexes", f"""-- km: concurrently
-- Budama `created_at` ile seçer. Geri alma:
--   DROP INDEX CONCURRENTLY IF EXISTS km_event_outbox_created;
CREATE INDEX IF NOT EXISTS km_event_outbox_created ON {OUTBOX_TABLE} (created_at);
"""),
]


async def apply_event_migrations(store: StoreLike) -> list[str]:
    """Uygulanmamış outbox göçlerini işler; uygulananları döndürür."""
    applied = await store.applied_migrations(OWNER)
    fresh: list[str] = []
    for name, sql in MIGRATIONS:
        if name in applied:
            continue
        await store.apply_migration(OWNER, name, sql)
        log.info("çekirdek göçü uygulandı", migration=name)
        fresh.append(name)
    return fresh
