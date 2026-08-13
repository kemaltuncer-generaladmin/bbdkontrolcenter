-- Toplu Satış — modülün KENDİ tabloları (K5).
--
-- Satışın otoritesi kantindir; bu tablolar "hangi partiyi kim, ne zaman, hangi
-- local_id ile gönderdi" izini tutar. Gönderim yarıda kalsa bile ne olduğu
-- kayıtlıdır ve tekrar gönderim aynı id ile yapılır (çift borç oluşmaz).

CREATE TABLE IF NOT EXISTS mod_bbd_bulk_sale_batch (
    batch_ref     TEXT PRIMARY KEY,
    sale_date     TEXT NOT NULL,                 -- YYYY-MM-DD; geçmişe tarihlenebilir
    mode          TEXT NOT NULL DEFAULT 'shared',-- shared (aynı sepet) | per_student
    cart_json     TEXT NOT NULL DEFAULT '[]',    -- ortak sepetin o anki hâli
    note          TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    created_by    TEXT NOT NULL DEFAULT '',
    total_count   INTEGER NOT NULL DEFAULT 0,
    ok_count      INTEGER NOT NULL DEFAULT 0,
    fail_count    INTEGER NOT NULL DEFAULT 0,
    total_amount  INTEGER NOT NULL DEFAULT 0     -- kuruş
);

CREATE INDEX IF NOT EXISTS mod_bbd_bulk_sale_batch_date
    ON mod_bbd_bulk_sale_batch (sale_date);

CREATE TABLE IF NOT EXISTS mod_bbd_bulk_sale_entry (
    local_id        TEXT PRIMARY KEY,            -- kantindeki transactions.local_id
    batch_ref       TEXT NOT NULL,
    sale_date       TEXT NOT NULL,
    kantin_id       TEXT NOT NULL,
    student_name    TEXT NOT NULL DEFAULT '',
    items_json      TEXT NOT NULL DEFAULT '[]',  -- o öğrenciye giden kalemler
    amount          INTEGER NOT NULL,            -- kuruş
    seq             INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'pending',
    reason          TEXT NOT NULL DEFAULT '',
    server_id       INTEGER,
    reversed_at     TEXT,
    reversed_reason TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_bbd_bulk_sale_entry_batch
    ON mod_bbd_bulk_sale_entry (batch_ref);
CREATE INDEX IF NOT EXISTS mod_bbd_bulk_sale_entry_date
    ON mod_bbd_bulk_sale_entry (sale_date);
CREATE INDEX IF NOT EXISTS mod_bbd_bulk_sale_entry_student
    ON mod_bbd_bulk_sale_entry (kantin_id, sale_date);

-- Kayıtlı sepet şablonları: "Kahvaltı paketi", "Etkinlik ikramı" gibi.
CREATE TABLE IF NOT EXISTS mod_bbd_bulk_sale_preset (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    cart_json  TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT ''
);

CREATE UNIQUE INDEX IF NOT EXISTS mod_bbd_bulk_sale_preset_name
    ON mod_bbd_bulk_sale_preset (name);
