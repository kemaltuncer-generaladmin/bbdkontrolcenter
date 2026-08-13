-- Kargo Yönetimi modülünün YEREL tabloları.
--
-- Buraya yalnız mağazada KARŞILIĞI OLMAYAN veri yazılır. Gönderi, hareket
-- geçmişi, taşıyıcı tanımı ve desi→ücret matrisi mağazadadır
-- (`bbd_shipments`, `bbd_shipping_carriers`, `bbd_shipping_desi_rates`) ve
-- kopyalanmaz: kopya, taşıyıcıdan gelen bir hareketten sonra sessizce yanlış
-- durum gösterir.
--
-- Dört şeyin karşılığı yok:
--  1. GEREKÇE. Mağaza denetim kaydı tutuyor ama "neden" alanı yok. Ayrıca ağ
--     koparsa "ne yapmaya çalıştık" kaydı yalnız burada kalır — etiket satın
--     alma para harcayan bir iştir, izi yerelde de durmalı.
--  2. BÖLGE EŞLEMESİ. Bagisto'da il/ilçe → bölge tablosu yok; bölgesel ek
--     ücret ve "teslimat yapılmayan bölge" bilgisi bu eşlemeden çıkar.
--  3. TESLİMAT MANİFESTOSU. Şoföre imzalatılan liste bir BELGEDİR: hangi
--     gönderiler hangi gün kime teslim edildi sorusu sonradan sorulur.
--  4. EKRAN TERCİHİ. Varsayılan taşıyıcı, etiket biçimi ve gecikme eşiği
--     vitrini etkilemez; yalnız bu ekranın davranışıdır.

CREATE TABLE IF NOT EXISTS mod_store_shipping_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id INTEGER NOT NULL DEFAULT 0,   -- 0 = henüz gönderi yok (taslak/toplu iş)
    order_id    INTEGER NOT NULL DEFAULT 0,
    action      TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    actor       TEXT NOT NULL DEFAULT '',
    result      TEXT NOT NULL DEFAULT '',     -- denendi | ok | dry_run | hata
    detail      TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_store_shipping_audit_shipment
    ON mod_store_shipping_audit (shipment_id, created_at);

CREATE INDEX IF NOT EXISTS mod_store_shipping_audit_order
    ON mod_store_shipping_audit (order_id, created_at);

CREATE INDEX IF NOT EXISTS mod_store_shipping_audit_time
    ON mod_store_shipping_audit (created_at);

-- İl/ilçe → bölge. `district` boş satır o ilin TAMAMI için geçerlidir; ilçe
-- satırı varsa o kazanır (en özel eşleşme). `delivers = 0` teslimat yapılmayan
-- bölgedir ve gönderi sihirbazı uyarır.
CREATE TABLE IF NOT EXISTS mod_store_shipping_zones (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    city       TEXT NOT NULL,
    district   TEXT NOT NULL DEFAULT '',
    zone       TEXT NOT NULL DEFAULT '',
    surcharge  INTEGER NOT NULL DEFAULT 0,    -- KURUŞ; bölgesel ek ücret
    delivers   INTEGER NOT NULL DEFAULT 1,    -- 0 = teslimat yapılmıyor
    note       TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    UNIQUE (city, district)
);

CREATE INDEX IF NOT EXISTS mod_store_shipping_zones_zone
    ON mod_store_shipping_zones (zone);

-- Üretilen teslimat manifestosu / şoför listesi. `shipments` manifestoya giren
-- takip numaralarıdır; dosya silinse bile hangi gönderilerin o gün teslim
-- edildiği burada kalır.
CREATE TABLE IF NOT EXISTS mod_store_shipping_manifests (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    path       TEXT NOT NULL DEFAULT '',
    carrier    TEXT NOT NULL DEFAULT '',
    shipments  TEXT NOT NULL DEFAULT '[]',
    count      INTEGER NOT NULL DEFAULT 0,
    actor      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_store_shipping_manifests_time
    ON mod_store_shipping_manifests (created_at);

CREATE TABLE IF NOT EXISTS mod_store_shipping_prefs (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
