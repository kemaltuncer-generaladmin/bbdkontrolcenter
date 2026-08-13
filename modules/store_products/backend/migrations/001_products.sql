-- Ürünler modülünün YEREL tabloları.
--
-- Buraya yalnız Bagisto'da KARŞILIĞI OLMAYAN veri yazılır. Ürün, fiyat, stok
-- ve kategori mağazadadır ve kopyalanmaz: kopya, mağaza tarafında yapılan bir
-- değişiklikten sonra sessizce yanlış rakam gösterir.
--
-- Üç şeyin karşılığı yok:
--  1. GEREKÇE. Bagisto denetim kaydı tutuyor ama "neden" alanı yok. Ayrıca ağ
--     koparsa "ne yapmaya çalıştık" kaydı yalnız burada kalır.
--  2. TOPLU İŞLEM ÖNİZLEMESİ. Uygulanan şeyin önizlenen şey olduğunu
--     kanıtlar; jeton olmadan `bulk/apply` reddedilir.
--  3. EKRAN TERCİHİ. Kritik stok eşiği vitrini etkilemez, yalnız bu ekranda
--     hangi satırın "Kritik" boyanacağını belirler — mağaza ayarı değildir.

CREATE TABLE IF NOT EXISTS mod_store_products_audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL DEFAULT 0,   -- 0 = toplu iş, tek ürüne bağlı değil
    action     TEXT NOT NULL,
    reason     TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    result     TEXT NOT NULL DEFAULT '',     -- denendi | ok | dry_run | hata
    detail     TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_store_products_audit_product
    ON mod_store_products_audit (product_id, created_at);

CREATE INDEX IF NOT EXISTS mod_store_products_audit_time
    ON mod_store_products_audit (created_at);

-- Toplu işlem önizlemesi. `rows` fark tablosunun kendisidir; uygulama onu
-- okur ve YENİDEN HESAPLAMAZ — kullanıcı neyi onayladıysa o uygulanır.
CREATE TABLE IF NOT EXISTS mod_store_products_bulk (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    token      TEXT NOT NULL UNIQUE,
    kind       TEXT NOT NULL,                -- price | stock | category | status
    params     TEXT NOT NULL DEFAULT '{}',
    rows       TEXT NOT NULL DEFAULT '[]',
    status     TEXT NOT NULL DEFAULT 'preview',   -- preview | dry_run | applied
    actor      TEXT NOT NULL DEFAULT '',
    reason     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS mod_store_products_bulk_created
    ON mod_store_products_bulk (created_at);

CREATE TABLE IF NOT EXISTS mod_store_products_prefs (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
