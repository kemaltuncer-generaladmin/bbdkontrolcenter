-- Bildirimler modülünün YEREL tabloları.
--
-- Buraya yalnız Bagisto'da KARŞILIĞI OLMAYAN veri yazılır. Gönderim geçmişi,
-- e-posta şablonları, kurallar ve abone listesi mağazadadır ve kopyalanmaz:
-- kopya, mağaza tarafında yapılan bir değişiklikten sonra sessizce yanlış
-- rakam gösterir.
--
-- Dört şeyin karşılığı yok:
--  1. GEREKÇE. Bagisto denetim kaydı tutuyor ama "neden" alanı yok. Ayrıca ağ
--     koparsa "ne yapmaya çalıştık" kaydı yalnız burada kalır.
--  2. SMS/PUSH ŞABLONU. Bagisto'da yalnız e-posta şablonu vardır; SMS ve push
--     metinlerinin saklanacağı bir yer yoktur.
--  3. SESSİZ SAATLER + GÜNLÜK LİMİT. Bu ekranın gönderim disiplinidir, mağaza
--     ayarı değildir; kapalıyken de geçerli olmalı ve mağaza düşse bile
--     korumayı sürdürmelidir.
--  4. TOPLU GÖNDERİM ÖNİZLEMESİ. Uygulanan işin önizlenen iş olduğunu
--     kanıtlar; jeton olmadan `broadcast/send` reddedilir.

CREATE TABLE IF NOT EXISTS mod_store_notifications_audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    action     TEXT NOT NULL,
    reason     TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    result     TEXT NOT NULL DEFAULT '',     -- denendi | ok | dry_run | sent | hata
    detail     TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_store_notifications_audit_time
    ON mod_store_notifications_audit (created_at);

-- SMS ve Push şablonları. E-posta şablonu BURAYA YAZILMAZ: onun sahibi
-- mağazadır ve Bagisto'nun kendi mailer'ı onu kullanır.
CREATE TABLE IF NOT EXISTS mod_store_notifications_templates (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    channel    TEXT NOT NULL DEFAULT 'sms',   -- sms | push | whatsapp
    subject    TEXT NOT NULL DEFAULT '',      -- SMS'te boş kalır
    body       TEXT NOT NULL DEFAULT '',
    event      TEXT NOT NULL DEFAULT '',
    active     INTEGER NOT NULL DEFAULT 1,    -- silme yok, pasifleştirme var
    actor      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_store_notifications_templates_channel
    ON mod_store_notifications_templates (channel, active);

CREATE TABLE IF NOT EXISTS mod_store_notifications_prefs (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

-- Toplu gönderim önizlemesi ve gerçekleşen gönderimler. Günlük limit sayacı
-- da buradan okunur: mağaza geçmişini saymak, uç düştüğünde korumayı sessizce
-- kaldırırdı.
CREATE TABLE IF NOT EXISTS mod_store_notifications_sends (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    token      TEXT NOT NULL UNIQUE,
    channel    TEXT NOT NULL,
    template   TEXT NOT NULL DEFAULT '',
    audience   TEXT NOT NULL DEFAULT '',      -- subscribers | customers | direct | …
    params     TEXT NOT NULL DEFAULT '{}',
    recipients INTEGER NOT NULL DEFAULT 0,
    parts      INTEGER NOT NULL DEFAULT 0,    -- SMS kredisi (parça × alıcı)
    cost       INTEGER NOT NULL DEFAULT 0,    -- kuruş; 0 = bilinmiyor
    status     TEXT NOT NULL DEFAULT 'preview',  -- preview | dry_run | sent
    actor      TEXT NOT NULL DEFAULT '',
    reason     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS mod_store_notifications_sends_day
    ON mod_store_notifications_sends (status, created_at);
