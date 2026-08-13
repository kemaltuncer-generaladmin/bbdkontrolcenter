-- Link ile tahsilat modülünün YEREL tabloları.
--
-- Buraya yalnız Bagisto'da KARŞILIĞI OLMAYAN veri yazılır. Sipariş, fatura,
-- ödeme denemesi ve POS kaydı mağazadadır ve kopyalanmaz.
--
-- Üç şeyin karşılığı yok:
--  1. TAHSİLAT TALEBİ. Müşteri ödemeden ÖNCE sipariş yoktur: personelin
--     doldurduğu ad/telefon/adres/tutar hiçbir Bagisto tablosuna düşmez.
--     Link üretilmeden önce (ve üretilemezse hiç) yaşayan tek kayıt budur.
--  2. GEREKÇE VE OLAY ZİNCİRİ. "Kim, neden, ne zaman link üretti / SMS attı /
--     elden kapattı" sorusunun cevabı yalnız burada durur. Ağ koparsa
--     "ne yapmaya çalıştık" kaydı da burada kalır.
--  3. SMS ŞABLONU VE EKRAN TERCİHİ. Mağazanın e-posta şablonlarıyla ilgisi
--     yoktur; bu metin bizim gönderdiğimiz SMS'tir.

CREATE TABLE IF NOT EXISTS mod_store_payment_gateway_requests (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Personelin telefonda okuduğu numara. Sıralı `id` okutulmaz: art arda
    -- giden sayılar müşteriye başka müşterilerin talep sayısını da söyler.
    code          TEXT NOT NULL UNIQUE,
    full_name     TEXT NOT NULL DEFAULT '',
    phone         TEXT NOT NULL DEFAULT '',      -- 5XXXXXXXXX (normalleştirilmiş)
    email         TEXT NOT NULL DEFAULT '',
    city          TEXT NOT NULL DEFAULT '',
    district      TEXT NOT NULL DEFAULT '',
    address       TEXT NOT NULL DEFAULT '',
    note          TEXT NOT NULL DEFAULT '',
    -- Kalemler JSON: [{kind, label, quantity, amount, taxRate, productId, sku}]
    -- Ürün adı ve fiyatı ANLIK KOPYADIR: ürün sonradan değişse bile
    -- müşteriden neyin tahsil edildiği kayıtta aynı kalmalı.
    items         TEXT NOT NULL DEFAULT '[]',
    net           INTEGER NOT NULL DEFAULT 0,    -- kuruş
    tax           INTEGER NOT NULL DEFAULT 0,    -- kuruş
    gross         INTEGER NOT NULL DEFAULT 0,    -- kuruş
    order_id      INTEGER NOT NULL DEFAULT 0,    -- ödeme sonrası mağazada oluşan sipariş
    invoice_id    INTEGER NOT NULL DEFAULT 0,
    token         TEXT NOT NULL DEFAULT '',      -- mağazanın ödeme linki jetonu
    link          TEXT NOT NULL DEFAULT '',
    -- draft | linked | sent | paid | expired | cancelled | failed |
    -- unknown | void_required | settled
    status        TEXT NOT NULL DEFAULT 'draft',
    -- Mağazadan gelen HAM durum sözcüğü. Eşlememiz yanlışsa ham veri elde
    -- kalsın diye saklanır; ekran eşlenmiş hâli gösterir.
    store_status  TEXT NOT NULL DEFAULT '',
    sms_state     TEXT NOT NULL DEFAULT '',      -- '' | dry_run | sent | error
    sms_at        TEXT NOT NULL DEFAULT '',
    settle_method TEXT NOT NULL DEFAULT '',      -- havale | nakit
    settle_ref    TEXT NOT NULL DEFAULT '',
    reason        TEXT NOT NULL DEFAULT '',
    actor         TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS mod_store_payment_gateway_requests_status
    ON mod_store_payment_gateway_requests (status, created_at);

CREATE INDEX IF NOT EXISTS mod_store_payment_gateway_requests_created
    ON mod_store_payment_gateway_requests (created_at);

CREATE INDEX IF NOT EXISTS mod_store_payment_gateway_requests_token
    ON mod_store_payment_gateway_requests (token);

-- Olay zinciri. Talep satırı ÜZERİNE YAZILIR (durum değişir); geçmiş burada
-- durur ve hiçbir zaman silinmez.
CREATE TABLE IF NOT EXISTS mod_store_payment_gateway_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL DEFAULT 0,   -- 0 = talebe bağlı olmayan işlem
    action     TEXT NOT NULL,
    reason     TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    result     TEXT NOT NULL DEFAULT '',     -- denendi | ok | dry_run | hata
    detail     TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_store_payment_gateway_events_request
    ON mod_store_payment_gateway_events (request_id, created_at);

CREATE TABLE IF NOT EXISTS mod_store_payment_gateway_prefs (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
