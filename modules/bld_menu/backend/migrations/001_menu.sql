-- Menü Yönetimi modülünün YEREL tabloları.
--
-- Buraya yalnız BLD'de KARŞILIĞI OLMAYAN veri yazılır. Menü günü, kalemleri,
-- yayın durumu, satılan porsiyon ve tavanlar BLD sunucusundadır ve
-- KOPYALANMAZ: `sold` her sipariş ve her abonelik üretimiyle değişiyor; yerel
-- bir kopya her zaman bir tur geride kalır ve "34 porsiyon kaldı" diyen bir
-- ekran, aslında dolmuş bir günü açık gösterirdi. Ekranın yanlış bilgiyi doğru
-- gibi göstermesi, hiç göstermemesinden kötüdür.
--
-- Üç şeyin karşılığı yok:
--  1. DENEME KAYDI. BLD `veykemtu_control_audit` tutuyor (`00-genel.md` §8) ama
--     o kayıt yalnız SUNUCUYA ULAŞAN isteği bilir. Ağ koparsa, geçit patlarsa
--     ya da istek yarıda kalırsa "kim neyi denedi" sorusunun cevabı yalnız
--     burada kalır. Bir günü yayınlarken bağlantı düşerse günün yayına girip
--     girmediği belirsizdir; iz olmasa kimin denediği de belirsiz olurdu.
--  2. STOK ÖNİZLEMESİ (temel çizgi). `PUT stock` TAM LİSTE yazar: gönderilmeyen
--     kalemin tavanı `null`'a düşer. Kuru prova, tavanın kaç siparişin altında
--     kaldığını önceden söyler; uygulanan tablonun ONAYLANAN tablo olduğunu
--     kanıtlayan şey bu satırdır. `baseline` önizleme ANINDAKİ satılmış
--     porsiyon sayılarıdır: gerçek uygulama geldiğinde sayılar hâlâ o hâlde mi
--     diye bakılır, aksi hâlde yönetici yarım saat önceki bir tabloya bakarak
--     karar vermiş olur.
--  3. EKRAN TERCİHİ. Yalnız bu ekranın ne gösterdiğini belirler; BLD'yi ve
--     müşteriye görünen menüyü ETKİLEMEZ.
--
-- K5: tablo VE index adlarının hepsi `mod_bld_menu_` önekiyle başlar.

-- Yazma denemelerinin yerel izi. SATIR SİLİNMEZ.
CREATE TABLE IF NOT EXISTS mod_bld_menu_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL DEFAULT 'daily_menu',  -- daily_menu
    target_date TEXT NOT NULL DEFAULT '',            -- YYYY-MM-DD; gün kimliği
                                                     -- yola konmuyor, denetim
                                                     -- sorusu da tarihle sorulur
    target_id   INTEGER NOT NULL DEFAULT 0,          -- 0 = gün henüz yok (kurma)
    action      TEXT NOT NULL,                       -- day.create | day.update |
                                                     -- day.delete | publish |
                                                     -- unpublish | item.create |
                                                     -- item.update | item.delete |
                                                     -- stock | duplicate
    reason      TEXT NOT NULL DEFAULT '',
    actor       TEXT NOT NULL DEFAULT '',            -- oturumdan gelir, gövdeden DEĞİL
    result      TEXT NOT NULL DEFAULT '',            -- denendi | ok | dry_run |
                                                     -- engellendi | hata
    detail      TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_bld_menu_audit_date
    ON mod_bld_menu_audit (target_date, created_at);

CREATE INDEX IF NOT EXISTS mod_bld_menu_audit_time
    ON mod_bld_menu_audit (created_at);

-- Stok tavanı önizlemesi. `proposed` yöneticinin yazmak İSTEDİĞİ tam tablo,
-- `baseline` önizleme anındaki satılmış porsiyonlar, `warnings` de kullanıcının
-- EKRANDA GÖRDÜĞÜ uyarı listesidir. Üçü birlikte tutulur çünkü "onayladığı şey
-- neydi" sorusunun cevabı üçünün toplamıdır.
CREATE TABLE IF NOT EXISTS mod_bld_menu_stock_preview (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    token       TEXT NOT NULL UNIQUE,
    menu_date   TEXT NOT NULL,                    -- YYYY-MM-DD
    proposed    TEXT NOT NULL DEFAULT '{}',       -- {capacity_total, items:[{item_id, capacity}]}
    baseline    TEXT NOT NULL DEFAULT '{}',       -- önizleme anındaki sold sayıları
    warnings    TEXT NOT NULL DEFAULT '[]',       -- kullanıcının GÖRDÜĞÜ uyarılar
    status      TEXT NOT NULL DEFAULT 'dry_run',  -- dry_run | applied
    actor       TEXT NOT NULL DEFAULT '',
    reason      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    applied_at  TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS mod_bld_menu_stock_preview_date
    ON mod_bld_menu_stock_preview (menu_date, created_at);

-- Ekran tercihi. Yalnız bu ekranın ne gösterdiğini belirler.
CREATE TABLE IF NOT EXISTS mod_bld_menu_prefs (
    key        TEXT PRIMARY KEY,   -- calendar_refresh_seconds | product_limit
    value      TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
