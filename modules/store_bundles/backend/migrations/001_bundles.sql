-- Setler modülünün YEREL tabloları.
--
-- Buraya yalnız mağazada KARŞILIĞI OLMAYAN veri yazılır. Ürünün fiyatı,
-- maliyeti, stoğu ve durumu Bagisto'dadır ve kopyalanmaz: kopya, mağaza
-- tarafındaki bir değişiklikten sonra sessizce yanlış kâr rakamı gösterir.
--
-- Karşılığı olmayan iki şey var:
--
--  1. SET KÜNYESİ VE BİLEŞEN TANIMI. Canlıda set diye bir ürün tipi yok;
--     "Setler" kategorisindeki (id 42) normal ürünler `product_cross_sells`
--     bağlarıyla birbirine bağlanmış durumda. O bağ yalnız "ilişkili" der;
--     ADET, BİLEŞEN İNDİRİMİ ve ZORUNLU/OPSİYONEL bilgisini TAŞIMAZ. Set
--     hesabı tam bu üçüne dayandığı için künye burada durur.
--  2. GEREKÇE. Bagisto denetim kaydı tutuyor ama "neden" alanı yok; ayrıca ağ
--     koparsa "ne yapmaya çalıştık" kaydı yalnız burada kalır.
--
-- `/api/admin/bbd/bundles` uçları yayına girdiğinde tanım mağazadan gelmeye
-- başlar; bu tablo o gün "yerel taslak" konumuna düşer ve ekran hangisinin
-- kullanıldığını söyler (satır silinmez).

CREATE TABLE IF NOT EXISTS mod_store_bundles_plan (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id      INTEGER NOT NULL UNIQUE,   -- setin kendisi (Bagisto ürünü)
    name            TEXT NOT NULL DEFAULT '',
    sku             TEXT NOT NULL DEFAULT '',
    pricing_mode    TEXT NOT NULL DEFAULT 'fixed',   -- fixed | percent
    discount_percent INTEGER NOT NULL DEFAULT 0,
    set_price       INTEGER,                   -- kuruş; percent kipinde NULL
    tax_rate        REAL,                      -- boşsa modül ayarındaki oran
    valid_from      TEXT NOT NULL DEFAULT '',
    valid_to        TEXT NOT NULL DEFAULT '',
    note            TEXT NOT NULL DEFAULT '',
    components      TEXT NOT NULL DEFAULT '[]',  -- [{productId, qty, discount, required}]
    actor           TEXT NOT NULL DEFAULT '',
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_store_bundles_plan_updated
    ON mod_store_bundles_plan (updated_at);

CREATE TABLE IF NOT EXISTS mod_store_bundles_audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    bundle_id  INTEGER NOT NULL DEFAULT 0,     -- 0 = henüz ürüne bağlanmamış taslak
    action     TEXT NOT NULL,
    reason     TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    result     TEXT NOT NULL DEFAULT '',       -- denendi | ok | dry_run | yerel | hata
    detail     TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_store_bundles_audit_bundle
    ON mod_store_bundles_audit (bundle_id, created_at);
