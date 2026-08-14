-- Müşteriye giden ÜÇ AŞAMA SMS'inin yerel tabloları.
--
-- Bagisto'da karşılığı olmayan iki şey:
--  1. AŞAMA ŞABLONU + AÇIK/KAPALI. Mağazada SMS şablonu diye bir kavram yok
--     (yalnız e-posta şablonu var) ve "sipariş alındı SMS'i açık mı" sorusunun
--     mağazada cevabı yok. Metin ekrandan düzenlenir, aşama tek tek kapatılır.
--  2. GÖNDERİM İZİ. Asıl işi TEKRARI ÖNLEMEKTİR: Geliver webhook'u iki kez
--     düşerse ya da tarama iki kez koşarsa müşteri iki kez rahatsız olmaz ve
--     iki kez para ödenmez. `(stage, order_id)` BENZERSİZDİR; ikinci gönderim
--     veritabanı düzeyinde imkânsızdır, kod nezaketine bırakılmamıştır.

CREATE TABLE IF NOT EXISTS mod_store_notifications_lifecycle (
    stage      TEXT PRIMARY KEY,             -- order_placed | shipped | delivered
    body       TEXT NOT NULL DEFAULT '',
    -- VARSAYILAN KAPALI. Modül kurulur kurulmaz müşterilere SMS gitmesi,
    -- kimsenin istemediği ve geri alınamayan bir davranıştır: aşama ekrandan,
    -- gerekçeyle ve bilerek açılır.
    enabled    INTEGER NOT NULL DEFAULT 0,
    actor      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

-- Sipariş başına AŞAMA BAŞINA tek satır. `result` yalnız 'sent' olduğunda
-- ikinci gönderim engellenir: numarası olmadığı için gidememiş bir mesaj,
-- numara düzeltilince gitmelidir (bkz. `lifecycle.blocks_resend`).
CREATE TABLE IF NOT EXISTS mod_store_notifications_lifecycle_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    stage      TEXT NOT NULL,
    order_id   INTEGER NOT NULL,
    order_no   TEXT NOT NULL DEFAULT '',
    customer   TEXT NOT NULL DEFAULT '',
    -- Numara HAM tutulur (yeniden deneme için gerekir), ekrana MASKELİ gider.
    phone      TEXT NOT NULL DEFAULT '',
    result     TEXT NOT NULL DEFAULT '',  -- sent | dry_run | no_phone | bad_phone |
                                          -- missing | error
    note       TEXT NOT NULL DEFAULT '',  -- "gönderilemedi: numara yok" gibi
    parts      INTEGER NOT NULL DEFAULT 0,
    job_id     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE UNIQUE INDEX IF NOT EXISTS mod_store_notifications_lifecycle_log_key
    ON mod_store_notifications_lifecycle_log (stage, order_id);

CREATE INDEX IF NOT EXISTS mod_store_notifications_lifecycle_log_time
    ON mod_store_notifications_lifecycle_log (created_at);
