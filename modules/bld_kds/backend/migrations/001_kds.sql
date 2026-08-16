-- KDS Yönetimi modülünün YEREL tabloları.
--
-- Buraya yalnız BLD'de KARŞILIĞI OLMAYAN veri yazılır. Cihaz, ayar, komut,
-- sipariş, revizyon ve fiş kaydı BLD sunucusundadır ve KOPYALANMAZ: kasa
-- saniyede bir yokluyor, sağlık ve durum sürekli değişiyor; yerel bir kopya
-- her zaman bir tur geride kalır ve "çevrimiçi" görünen bir kasa aslında yarım
-- saattir kapalı olabilir. Ekranın yanlış bilgiyi doğru gibi göstermesi,
-- hiç göstermemesinden kötüdür.
--
-- Üç şeyin karşılığı yok:
--  1. DENEME KAYDI. BLD `veykemtu_control_audit` tutuyor (K-21 §3) ama o kayıt
--     yalnız SUNUCUYA ULAŞAN isteği bilir. Ağ koparsa, geçit patlarsa ya da
--     istek yarıda kalırsa "kim neyi denedi" sorusunun cevabı yalnız burada
--     kalır. `restart` komutu gönderilirken bağlantı düşerse, komutun kuyruğa
--     girip girmediği belirsizdir; iz olmasa kimin denediği de belirsiz olurdu.
--  2. AYAR İTME ÖNİZLEMESİ. Uygulanan tablonun onaylanan tablo olduğunu
--     kanıtlar ve iki yöneticinin aynı cihaza aynı anda yazmasını yakalar.
--  3. EKRAN TERCİHİ. Yalnız bu ekranın ne gösterdiğini belirler; BLD'yi ve
--     kasayı ETKİLEMEZ.
--
-- K5: tablo VE index adlarının hepsi `mod_bld_kds_` önekiyle başlar.

-- Yazma denemelerinin yerel izi. SATIR SİLİNMEZ.
CREATE TABLE IF NOT EXISTS mod_bld_kds_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL DEFAULT 'device',  -- device | order
    target_id   INTEGER NOT NULL DEFAULT 0,      -- 0 = henüz kimliği yok (cihaz açma)
    action      TEXT NOT NULL,                   -- create_device | rename_device |
                                                 -- pairing_code | revoke_device |
                                                 -- push_settings | command:<ad> |
                                                 -- create_revision | set_status
    reason      TEXT NOT NULL DEFAULT '',
    actor       TEXT NOT NULL DEFAULT '',        -- oturumdan gelir, gövdeden DEĞİL
    result      TEXT NOT NULL DEFAULT '',        -- denendi | ok | dry_run |
                                                 -- engellendi | hata
    detail      TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_bld_kds_audit_target
    ON mod_bld_kds_audit (target_type, target_id, created_at);

CREATE INDEX IF NOT EXISTS mod_bld_kds_audit_time
    ON mod_bld_kds_audit (created_at);

-- Ayar itme önizlemesi. `baseline` önizleme ANINDAKİ cihaz ayarıdır: gerçek
-- uygulama geldiğinde cihazın ayarı hâlâ o hâlde mi diye bakılır. Aksi hâlde
-- iki yönetici aynı anda yazdığında ikincisi birincinin değişikliğini
-- GÖRMEDEN ezerdi ve kimse fark etmezdi.
CREATE TABLE IF NOT EXISTS mod_bld_kds_settings_preview (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    token      TEXT NOT NULL UNIQUE,
    device_id  INTEGER NOT NULL,
    proposed   TEXT NOT NULL DEFAULT '{}',       -- yazılacak anahtarlar (kısmi)
    baseline   TEXT NOT NULL DEFAULT '{}',       -- önizleme anındaki 23 ayar
    diff       TEXT NOT NULL DEFAULT '[]',       -- kullanıcının GÖRDÜĞÜ tablo
    status     TEXT NOT NULL DEFAULT 'dry_run',  -- dry_run | applied
    actor      TEXT NOT NULL DEFAULT '',
    reason     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS mod_bld_kds_settings_preview_device
    ON mod_bld_kds_settings_preview (device_id, created_at);

-- Ekran tercihi. Şu turda YAZAN bir uç yok (sözleşmedeki uç listesinde ayar
-- ucu bulunmuyor); tablo okunuyor ve değer bulunmazsa modül ayarı geçerli
-- oluyor. Ayarlar sekmesi eklendiğinde yazacağı yer burasıdır.
CREATE TABLE IF NOT EXISTS mod_bld_kds_prefs (
    key        TEXT PRIMARY KEY,   -- command_limit | print_job_limit |
                                   -- include_completed
    value      TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
