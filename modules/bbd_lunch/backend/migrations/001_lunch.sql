-- Öğle Yemeği — modülün KENDİ tabloları (K5).
-- Tablo adı modül önekiyle açılır; çekirdek başka önek kabul etmez.
--
-- Buradaki kayıtlar kantinin kopyası DEĞİLDİR. Kantin satışın otoritesidir;
-- bu tablolar "hangi partiyi, kim, hangi gün, hangi local_id ile gönderdi"
-- sorusunun cevabını tutar. Gönderim yarıda kalsa bile ne olduğu kayıtlıdır.

-- Bir "parti" = tek seferde işlenen toplu yemek kaydı.
CREATE TABLE IF NOT EXISTS mod_bbd_lunch_batch (
    batch_ref     TEXT PRIMARY KEY,              -- KM üretir; partinin kimliği
    service_date  TEXT NOT NULL,                 -- YYYY-MM-DD — yemeğin verildiği gün
    product_id    INTEGER NOT NULL,              -- kantindeki ürün
    product_name  TEXT NOT NULL DEFAULT '',      -- o anki adı (sonradan değişse de iz kalsın)
    unit_price    INTEGER NOT NULL,              -- kuruş, gönderim anındaki fiyat
    portion       INTEGER NOT NULL DEFAULT 1,    -- öğrenci başına porsiyon
    note          TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    created_by    TEXT NOT NULL DEFAULT '',      -- işlemi yapan kullanıcının adı
    total_count   INTEGER NOT NULL DEFAULT 0,
    ok_count      INTEGER NOT NULL DEFAULT 0,
    fail_count    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS mod_bbd_lunch_batch_date
    ON mod_bbd_lunch_batch (service_date);

-- Parti içindeki tek öğrenci satırı. `local_id` kantindeki transactions.local_id'dir.
-- GÖNDERİMDEN ÖNCE yazılır: ağ koparsa bile hangi local_id'nin gittiği bilinir,
-- tekrar gönderim aynı id ile yapılır ve kantin "duplicate" der — çift borç olmaz.
CREATE TABLE IF NOT EXISTS mod_bbd_lunch_entry (
    local_id        TEXT PRIMARY KEY,
    batch_ref       TEXT NOT NULL,
    service_date    TEXT NOT NULL,
    kantin_id       TEXT NOT NULL,               -- öğrencinin opaque_id'si
    student_name    TEXT NOT NULL DEFAULT '',
    class_name      TEXT NOT NULL DEFAULT '',
    amount          INTEGER NOT NULL,            -- kuruş
    seq             INTEGER NOT NULL DEFAULT 0,  -- aynı gün/öğrenci için kaçıncı deneme
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending|created|duplicate|failed
    reason          TEXT NOT NULL DEFAULT '',
    server_id       INTEGER,
    reversed_at     TEXT,
    reversed_reason TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_bbd_lunch_entry_date
    ON mod_bbd_lunch_entry (service_date);
CREATE INDEX IF NOT EXISTS mod_bbd_lunch_entry_batch
    ON mod_bbd_lunch_entry (batch_ref);
CREATE INDEX IF NOT EXISTS mod_bbd_lunch_entry_student
    ON mod_bbd_lunch_entry (kantin_id, service_date);

-- Sabit liste: her gün yemek yiyen öğrenciler. Yeni gün açıldığında ön seçili gelir.
CREATE TABLE IF NOT EXISTS mod_bbd_lunch_roster (
    kantin_id  TEXT PRIMARY KEY,
    updated_at TEXT NOT NULL
);

-- Tatil/idari izin günleri. Aralık işlemede bu günler atlanır.
CREATE TABLE IF NOT EXISTS mod_bbd_lunch_holiday (
    day        TEXT PRIMARY KEY,                 -- YYYY-MM-DD
    label      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
