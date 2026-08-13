-- Siparişler modülünün YEREL tabloları.
--
-- Buraya yalnız Bagisto'da KARŞILIĞI OLMAYAN veri yazılır. Sipariş, kalem,
-- fatura ve gönderi mağazadadır ve kopyalanmaz: kopya, mağaza tarafında yapılan
-- bir değişiklikten sonra sessizce yanlış rakam gösterir.
--
-- Üç şeyin karşılığı yok:
--  1. GEREKÇE. Bagisto denetim kaydı tutuyor ama "neden" alanı yok. Ayrıca ağ
--     koparsa "ne yapmaya çalıştık" kaydı yalnız burada kalır — iptal edilmeye
--     çalışılmış bir sipariş için bu bilgi paradan önemlidir.
--  2. TOPLU İŞLEM ÖNİZLEMESİ. Uygulanan şeyin önizlenen şey olduğunu kanıtlar;
--     jeton olmadan `batch/apply` reddedilir.
--  3. EKRAN TERCİHİ. Durum adları, sipariş no biçimi ve iptal süresi mağazayı
--     ETKİLEMEZ; yalnız bu ekranda ne yazdığını ve neyin engellendiğini
--     belirler.

CREATE TABLE IF NOT EXISTS mod_store_orders_audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id   INTEGER NOT NULL DEFAULT 0,   -- 0 = toplu iş, tek siparişe bağlı değil
    action     TEXT NOT NULL,                -- cancel | invoice | ship | add_comment | ...
    reason     TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    result     TEXT NOT NULL DEFAULT '',     -- denendi | ok | dry_run | engellendi | hata
    detail     TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_store_orders_audit_order
    ON mod_store_orders_audit (order_id, created_at);

CREATE INDEX IF NOT EXISTS mod_store_orders_audit_time
    ON mod_store_orders_audit (created_at);

-- Toplu kargo/fatura önizlemesi. `rows` kullanıcının GÖRDÜĞÜ listedir; uygulama
-- onu okur ve YENİDEN HESAPLAMAZ — onaylanan neyse o uygulanır.
CREATE TABLE IF NOT EXISTS mod_store_orders_batch (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    token      TEXT NOT NULL UNIQUE,
    kind       TEXT NOT NULL,                     -- ship | invoice
    params     TEXT NOT NULL DEFAULT '{}',
    rows       TEXT NOT NULL DEFAULT '[]',
    status     TEXT NOT NULL DEFAULT 'preview',   -- preview | dry_run | applied
    actor      TEXT NOT NULL DEFAULT '',
    reason     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS mod_store_orders_batch_created
    ON mod_store_orders_batch (created_at);

CREATE TABLE IF NOT EXISTS mod_store_orders_prefs (
    key        TEXT PRIMARY KEY,   -- status_names | order_no_format |
                                   -- cancel_window_hours | late_days
    value      TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
