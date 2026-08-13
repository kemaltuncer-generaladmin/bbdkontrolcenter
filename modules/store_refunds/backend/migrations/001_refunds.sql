-- İadeler modülünün YEREL tabloları.
--
-- Buraya yalnız Bagisto'da KARŞILIĞI OLMAYAN veri yazılır. Kredi notu, sipariş
-- kalemi, ödeme denemesi ve iade tutarı mağazadadır ve kopyalanmaz: kopya,
-- mağaza tarafında yapılan bir düzeltmeden sonra sessizce yanlış rakam
-- gösterir — para ekranında bu kabul edilemez.
--
-- İki şeyin karşılığı yok:
--
--  1. GEREKÇE. Bagisto denetim kaydı tutuyor ama "neden iade edildi" alanı
--     yok. Ayrıca ağ koparsa "ne yapmaya çalıştık" kaydı yalnız burada kalır.
--
--  2. ONAYLANAN HESABIN KENDİSİ. Personel ekranda satır satır bir hesap görüp
--     onaylıyor; onay ile mağazaya giden gövde arasında hiçbir yeniden
--     hesaplama olmamalı. Hesap jetonla saklanır, onay o jetonla gelir ve
--     UYGULANAN ŞEYİN ÖNİZLENEN ŞEY OLDUĞU kanıtlanır. Jeton yoksa onay
--     reddedilir; "ekranda ne gördüysem o gitti" sorusunun cevabı budur.

CREATE TABLE IF NOT EXISTS mod_store_refunds_audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id   INTEGER NOT NULL DEFAULT 0,   -- 0 = siparişe bağlı olmayan iş
    action     TEXT NOT NULL,
    reason     TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    result     TEXT NOT NULL DEFAULT '',     -- denendi | ok | dry_run | hata
    detail     TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_store_refunds_audit_order
    ON mod_store_refunds_audit (order_id, created_at);

CREATE INDEX IF NOT EXISTS mod_store_refunds_audit_time
    ON mod_store_refunds_audit (created_at);

-- Onaya sunulan iade hesabı. `lines` ekranda görünen satırların kendisidir;
-- `body` mağazaya gidecek gövdedir. Onay ikisini de YENİDEN HESAPLAMAZ.
CREATE TABLE IF NOT EXISTS mod_store_refunds_calc (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    token      TEXT NOT NULL UNIQUE,
    order_id   INTEGER NOT NULL,
    total      INTEGER NOT NULL DEFAULT 0,   -- kuruş
    lines      TEXT NOT NULL DEFAULT '[]',
    body       TEXT NOT NULL DEFAULT '{}',
    store_total INTEGER,                     -- mağazanın kendi önizlemesi (yoksa NULL)
    status     TEXT NOT NULL DEFAULT 'preview',   -- preview | dry_run | applied
    actor      TEXT NOT NULL DEFAULT '',
    reason     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS mod_store_refunds_calc_order
    ON mod_store_refunds_calc (order_id, created_at);
