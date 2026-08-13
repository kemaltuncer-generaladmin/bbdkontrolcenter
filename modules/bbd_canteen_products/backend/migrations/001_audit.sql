-- Kantin Ürünleri — modülün KENDİ tablosu (K5).
--
-- Ürünün kendisi kantinde durur, burada KOPYASI TUTULMAZ. Bu tablo yalnız
-- "kim, ne zaman, neyi, neyden neye çevirdi" sorusunu yanıtlar. Kantinde
-- böyle bir iz yok; yanlış fiyat/stok girildiğinde eski değere dönmenin
-- tek yolu budur.

CREATE TABLE IF NOT EXISTS mod_bbd_canteen_products_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER,                       -- yeni üründe kayıttan sonra dolar
    barcode     TEXT NOT NULL DEFAULT '',
    name        TEXT NOT NULL DEFAULT '',
    action      TEXT NOT NULL,                 -- create|update|stock|deactivate|activate|price_bulk
    before_json TEXT NOT NULL DEFAULT '',      -- işlemden önceki hâl (JSON)
    after_json  TEXT NOT NULL DEFAULT '',      -- işlemden sonraki hâl (JSON)
    note        TEXT NOT NULL DEFAULT '',
    actor       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_bbd_canteen_products_audit_product
    ON mod_bbd_canteen_products_audit (product_id, id);
CREATE INDEX IF NOT EXISTS mod_bbd_canteen_products_audit_at
    ON mod_bbd_canteen_products_audit (created_at);
