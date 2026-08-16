-- Satış Ayarları modülünün YEREL tabloları.
--
-- Buraya yalnız BLD'de KARŞILIĞI OLMAYAN veri yazılır. Satış ayarları
-- TastyIgniter'ın `location_options` tablosunda, kapalı günler
-- `veykemtu_closed_days` içinde, stok tavanları günlük menü kayıtlarındadır ve
-- KOPYALANMAZ: gövdede `ordering_enabled`, `paused_until` ve `busy` gibi CANLI
-- şalterler var. Yerel bir kopya her zaman bir tur geride kalır ve "satışı
-- durdurdum ama panel hâlâ açık gösteriyor" cümlesi, kopyanın en pahalı
-- hâlidir.
--
-- Üç şeyin karşılığı yok:
--  1. DENEME KAYDI. BLD `veykemtu_control_audit` tutuyor (00-genel.md §8) ama
--     o kayıt yalnız SUNUCUYA ULAŞAN isteği bilir. Ağ koparsa, geçit patlarsa
--     ya da istek yarıda kalırsa "kim neyi denedi" sorusunun cevabı yalnız
--     burada kalır. Satış durdurulurken bağlantı düşerse, satışın durup
--     durmadığı belirsizdir; iz olmasa kimin denediği de belirsiz olurdu.
--  2. TABAN ÇİZGİSİ. Formun AÇILDIĞI andaki ayar görüntüsü. `bld_busy`
--     anahtarını mutfak ekranı da değiştiriyor: yönetici formu 09:00'da açar,
--     mutfak 09:10'da yoğunluğu açar, yönetici 09:30'da kaydeder ve yarım saat
--     önceki hâli geri yazar. Karşılaştırılacak bir taban olmadan bu yarış
--     görünmez.
--  3. EKRAN TERCİHİ. Yalnız bu ekranın ne gösterdiğini belirler; BLD'yi
--     ETKİLEMEZ.
--
-- K5: tablo VE index adlarının hepsi `mod_bld_sales_settings_` önekiyle başlar.

-- Yazma denemelerinin yerel izi. SATIR SİLİNMEZ.
CREATE TABLE IF NOT EXISTS mod_bld_sales_settings_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL DEFAULT 'settings',  -- settings | closed_day | stock
    target_id   TEXT NOT NULL DEFAULT '',          -- kapalı gün / stok tarihi
    action      TEXT NOT NULL,                     -- settings.sales |
                                                   -- settings.ordering.pause |
                                                   -- settings.ordering.resume |
                                                   -- settings.closed_day.create |
                                                   -- settings.closed_day.delete |
                                                   -- menu.stock
    reason      TEXT NOT NULL DEFAULT '',
    actor       TEXT NOT NULL DEFAULT '',          -- oturumdan gelir, gövdeden DEĞİL
    result      TEXT NOT NULL DEFAULT '',          -- denendi | ok | onizleme |
                                                   -- engellendi | hata
    detail      TEXT NOT NULL DEFAULT '{}',        -- {changes:[{field,from,to}], …}
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_bld_sales_settings_audit_target
    ON mod_bld_sales_settings_audit (target_type, target_id, created_at);

CREATE INDEX IF NOT EXISTS mod_bld_sales_settings_audit_time
    ON mod_bld_sales_settings_audit (created_at);

-- Formun açıldığı andaki ayar görüntüsü. Ekrana ÇİZİLMEZ ve uzak verinin
-- kopyası değildir: yalnız "yönetici neyin üstüne yazdığını sanıyordu"
-- sorusunun cevabıdır. Yazma isteği jetonu geri getirir; servis, YAZILAN
-- ALANLARIN aradan değişip değişmediğini buna bakarak anlar.
--
-- Yazılmayan alanlar karşılaştırılmaz ve karşılaştırılmamalı: onlara zaten
-- dokunulmuyor (kısmi yazma) ve mutfağın değiştirdiği bir anahtar yüzünden
-- kesim saati kaydını reddetmek ekranı kullanılamaz yapardı.
CREATE TABLE IF NOT EXISTS mod_bld_sales_settings_baseline (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    token       TEXT NOT NULL UNIQUE,
    location_id INTEGER NOT NULL DEFAULT 0,    -- 0 = varsayılan vitrin
    snapshot    TEXT NOT NULL DEFAULT '{}',    -- yazılabilir 13 alanın o anki hâli
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_bld_sales_settings_baseline_time
    ON mod_bld_sales_settings_baseline (created_at);

-- Ekran tercihi. KULLANICI BAŞINA DEĞİL, kurulum başınadır — bu yüzden yazması
-- `bld_sales_settings.manage` ister: bir kullanıcının seçimi ötekinin ekranını
-- da değiştirir.
CREATE TABLE IF NOT EXISTS mod_bld_sales_settings_prefs (
    key        TEXT PRIMARY KEY,   -- stock_days | tab
    value      TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
