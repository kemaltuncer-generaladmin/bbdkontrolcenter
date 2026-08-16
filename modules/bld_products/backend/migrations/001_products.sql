-- Ürün Yönetimi modülünün YEREL tabloları.
--
-- Buraya yalnız BLD'de KARŞILIĞI OLMAYAN veri yazılır. Ürün, kategori, fiyat,
-- görsel ve tükendi işareti BLD sunucusundadır ve KOPYALANMAZ: tükendi
-- işaretini mutfak da koyuyor (`Services\MenuAvailability`), fiyat başka bir
-- yöneticinin ekranından da değişebiliyor. Yerel bir kopya her zaman bir tur
-- geride kalır ve "satışta" görünen bir ürün aslında yarım saattir tükenmiş
-- olabilir. Ekranın yanlış bilgiyi doğru gibi göstermesi, hiç göstermemesinden
-- kötüdür.
--
-- İki şeyin karşılığı yok:
--  1. DENEME KAYDI. BLD `veykemtu_control_audit` tutuyor (`00-genel.md` §8) ama
--     o kayıt yalnız SUNUCUYA ULAŞAN isteği bilir. Ağ koparsa, geçit acil
--     freni kapatırsa ya da imza reddedilirse (doğrulama denetleyiciden ÖNCE
--     çalışıyor) "kim neyi denedi" sorusunun cevabı yalnız burada kalır.
--     Fiyat yazılırken bağlantı düşerse yeni fiyatın geçip geçmediği belirsizdir;
--     iz olmasa kimin denediği de belirsiz olurdu.
--  2. EKRAN TERCİHİ. Yalnız bu ekranın açılışta ne gösterdiğini belirler;
--     BLD'yi ve satışı ETKİLEMEZ. Bu yüzden gerekçe istemez.
--
-- K5: tablo VE index adlarının hepsi `mod_bld_products_` önekiyle başlar.

-- Yazma denemelerinin yerel izi. SATIR SİLİNMEZ.
CREATE TABLE IF NOT EXISTS mod_bld_products_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL DEFAULT 'menu',    -- menu | category
    target_id   INTEGER NOT NULL DEFAULT 0,      -- 0 = henüz kimliği yok (açma)
    action      TEXT NOT NULL,                   -- product.create | product.update |
                                                 -- product.delete | product.image |
                                                 -- product.image.delete |
                                                 -- product.sold_out |
                                                 -- product.sold_out.clear |
                                                 -- category.create | category.update
    reason      TEXT NOT NULL DEFAULT '',
    actor       TEXT NOT NULL DEFAULT '',        -- oturumdan gelir, gövdeden DEĞİL
    result      TEXT NOT NULL DEFAULT '',        -- denendi | ok | dry_run |
                                                 -- engellendi | hata
    -- İsteğin ÖZETİ, tam gövdesi değil. Görselde YALNIZ künye durur
    -- ({"mime", "bytes", "filename"}); base64 içerik yazılması `00-genel.md`
    -- §8.2 ile açıkça yasak — izi okunamaz ve tabloyu yönetilemez kılardı.
    detail      TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_bld_products_audit_target
    ON mod_bld_products_audit (target_type, target_id, created_at);

CREATE INDEX IF NOT EXISTS mod_bld_products_audit_time
    ON mod_bld_products_audit (created_at);

-- Ekran tercihi. Anahtar başına tek satır; `key` birincil anahtar olduğu için
-- yazma UPSERT'tir ve aynı tercihin ikinci bir kopyası doğmaz.
CREATE TABLE IF NOT EXISTS mod_bld_products_prefs (
    key        TEXT PRIMARY KEY,   -- page_size | status_filter | sort | direction
    value      TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
