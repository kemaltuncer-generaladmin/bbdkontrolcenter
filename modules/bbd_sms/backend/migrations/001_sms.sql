-- SMS Sistemi — modülün KENDİ tabloları (K5).
--
-- NEDEN GEREKLİ: kantinin `POST /api/sms/send` ucu EŞ ZAMANLI çalışır ve
-- gönderdiğini HİÇBİR YERE KAYDETMEZ. Kuyruk tablosu yalnız ödeme SMS'lerini
-- tutar. Yani "geçen hafta velilere ne yazmıştık" sorusunun cevabı bizde olmazsa
-- hiçbir yerde yoktur.

CREATE TABLE IF NOT EXISTS mod_bbd_sms_batch (
    batch_ref    TEXT PRIMARY KEY,
    title        TEXT NOT NULL DEFAULT '',      -- gönderim başlığı (iz için)
    body         TEXT NOT NULL,                 -- şablon metni (yer tutucular çözülmemiş)
    encoding     TEXT NOT NULL DEFAULT '',      -- GSM-7 | UCS-2
    segments     INTEGER NOT NULL DEFAULT 1,    -- kişi başı segment
    include_debt INTEGER NOT NULL DEFAULT 0,
    include_daily INTEGER NOT NULL DEFAULT 0,
    total_count  INTEGER NOT NULL DEFAULT 0,
    sent_count   INTEGER NOT NULL DEFAULT 0,
    fail_count   INTEGER NOT NULL DEFAULT 0,
    credits      INTEGER NOT NULL DEFAULT 0,    -- harcanan toplam segment
    created_at   TEXT NOT NULL,
    created_by   TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS mod_bbd_sms_batch_at ON mod_bbd_sms_batch (created_at);

CREATE TABLE IF NOT EXISTS mod_bbd_sms_message (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_ref    TEXT NOT NULL DEFAULT '',
    kantin_id    TEXT NOT NULL DEFAULT '',      -- öğrenci (varsa)
    student_name TEXT NOT NULL DEFAULT '',
    class_name   TEXT NOT NULL DEFAULT '',
    phone        TEXT NOT NULL DEFAULT '',      -- VELİ numarası
    text         TEXT NOT NULL DEFAULT '',      -- gerçekten gönderilen tam metin
    segments     INTEGER NOT NULL DEFAULT 1,
    status       TEXT NOT NULL DEFAULT 'pending', -- sent | failed | skipped
    reason       TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_bbd_sms_message_batch ON mod_bbd_sms_message (batch_ref);
CREATE INDEX IF NOT EXISTS mod_bbd_sms_message_at ON mod_bbd_sms_message (created_at);
CREATE INDEX IF NOT EXISTS mod_bbd_sms_message_student ON mod_bbd_sms_message (kantin_id);

-- Hazır mesajlar: veli toplantısı, tatil duyurusu, borç hatırlatma…
CREATE TABLE IF NOT EXISTS mod_bbd_sms_preset (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT ''
);

CREATE UNIQUE INDEX IF NOT EXISTS mod_bbd_sms_preset_name ON mod_bbd_sms_preset (name);
