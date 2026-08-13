-- Ödeme Talebi — modülün KENDİ tabloları (K5).
--
-- Kantin talebin kendisini tutar ama "kim, ne zaman, hangi toplu gönderimde
-- istedi" izini tutmaz. Ayrıca kantinde ÇİFT GÖNDERİM KORUMASI YOKTUR: aynı
-- öğrenciye arka arkaya talep açmak her seferinde yeni SMS kuyruğa atar.
-- Son gönderim zamanını bilmemizin tek yolu bu tablo.

CREATE TABLE IF NOT EXISTS mod_bbd_payment_request_batch (
    batch_ref   TEXT PRIMARY KEY,
    note        TEXT NOT NULL DEFAULT '',
    total_count INTEGER NOT NULL DEFAULT 0,
    ok_count    INTEGER NOT NULL DEFAULT 0,
    fail_count  INTEGER NOT NULL DEFAULT 0,
    amount_sum  INTEGER NOT NULL DEFAULT 0,   -- kuruş, gönderim anındaki borç toplamı
    created_at  TEXT NOT NULL,
    created_by  TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS mod_bbd_payment_request_batch_at
    ON mod_bbd_payment_request_batch (created_at);

CREATE TABLE IF NOT EXISTS mod_bbd_payment_request_entry (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_ref    TEXT NOT NULL DEFAULT '',
    kantin_id    TEXT NOT NULL,
    student_name TEXT NOT NULL DEFAULT '',
    phone        TEXT NOT NULL DEFAULT '',    -- VELİ numarası
    amount       INTEGER NOT NULL DEFAULT 0,  -- kuruş
    ref          TEXT NOT NULL DEFAULT '',    -- kantindeki PR-… referansı
    status       TEXT NOT NULL DEFAULT 'queued', -- queued | failed | skipped
    reason       TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_bbd_payment_request_entry_batch
    ON mod_bbd_payment_request_entry (batch_ref);
CREATE INDEX IF NOT EXISTS mod_bbd_payment_request_entry_student
    ON mod_bbd_payment_request_entry (kantin_id, created_at);

-- Nakit tahsilat izi. Kantin `localId` ile idempotent tutuyor; buradaki kayıt
-- "hangi tahsilatı bu panelden kim yaptı" sorusunun cevabıdır.
CREATE TABLE IF NOT EXISTS mod_bbd_payment_request_collection (
    local_id     TEXT PRIMARY KEY,
    kantin_id    TEXT NOT NULL,
    student_name TEXT NOT NULL DEFAULT '',
    amount       INTEGER NOT NULL,
    balance_after INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'ok',
    reason       TEXT NOT NULL DEFAULT '',
    note         TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    created_by   TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS mod_bbd_payment_request_collection_at
    ON mod_bbd_payment_request_collection (created_at);
