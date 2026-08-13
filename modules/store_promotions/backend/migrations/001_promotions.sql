-- Promosyonlar modülünün YEREL tabloları.
--
-- Buraya yalnız Bagisto'da KARŞILIĞI OLMAYAN veri yazılır. Kural, koşul, kupon
-- ve kullanım sayacı mağazadadır ve kopyalanmaz: kopya, mağaza tarafında
-- yapılan bir değişiklikten sonra sessizce yanlış rakam gösterir.
--
-- İki şeyin karşılığı yok:
--  1. GEREKÇE. Bagisto denetim kaydı tutuyor ama "neden" alanı yok. Ayrıca ağ
--     koparsa "ne yapmaya çalıştık" kaydı yalnız burada kalır.
--  2. KUPON PARTİSİ. Kodları mağaza üretir; hangi partinin hangi önekle, kaç
--     adet, kim tarafından ve hangi gerekçeyle üretildiği mağazada durmaz.
--     CSV dosyası kaybolursa parti buradan yeniden yazılabilir.

CREATE TABLE IF NOT EXISTS mod_store_promotions_audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id    INTEGER NOT NULL DEFAULT 0,   -- 0 = kurala bağlı olmayan iş
    scope      TEXT NOT NULL DEFAULT 'cart', -- cart | catalog | coupon
    action     TEXT NOT NULL,
    reason     TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    result     TEXT NOT NULL DEFAULT '',     -- denendi | ok | dry_run | hata
    detail     TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_store_promotions_audit_rule
    ON mod_store_promotions_audit (rule_id, created_at);

CREATE INDEX IF NOT EXISTS mod_store_promotions_audit_time
    ON mod_store_promotions_audit (created_at);

-- Üretilen kupon partisi. `codes` mağazadan ÜRETİMDEN SONRA okunan gerçek
-- kodlardır; ekranın ürettiği örnekler değildir (kodu mağaza üretir, biz
-- yalnız neye benzeyeceğini önizleriz).
CREATE TABLE IF NOT EXISTS mod_store_promotions_batches (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    token      TEXT NOT NULL UNIQUE,
    rule_id    INTEGER NOT NULL,
    rule_name  TEXT NOT NULL DEFAULT '',
    prefix     TEXT NOT NULL DEFAULT '',
    count      INTEGER NOT NULL DEFAULT 0,
    length     INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT NOT NULL DEFAULT '',
    codes      TEXT NOT NULL DEFAULT '[]',
    path       TEXT NOT NULL DEFAULT '',     -- yazılan CSV dosyasının yolu
    actor      TEXT NOT NULL DEFAULT '',
    reason     TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'ok',   -- ok | dry_run | hata
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_store_promotions_batches_rule
    ON mod_store_promotions_batches (rule_id, created_at);
