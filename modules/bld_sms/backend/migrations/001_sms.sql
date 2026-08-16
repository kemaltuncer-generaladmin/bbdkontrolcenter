-- SMS Paneli modülünün YEREL tabloları.
--
-- Buraya yalnız BLD'de KARŞILIĞI OLMAYAN veri yazılır. Şablon metni
-- (`veykemtu_sms_templates`), gönderim kaydı (`veykemtu_sms_log`) ve duyuru
-- taslağı (`location_options`) BLD sunucusundadır ve KOPYALANMAZ: metin oradan
-- düzenleniyor, kayıt oradan büyüyor, alıcı tahmini her okumada yeniden
-- hesaplanıyor. Yerel bir kopya her zaman bir tur geride kalır ve yöneticiye
-- kaydettiğini sandığı metni gösterir.
--
-- Üç şeyin karşılığı yok:
--  1. DENEME KAYDI. BLD `veykemtu_control_audit` tutuyor (00-genel.md §3) ama
--     o kayıt yalnız SUNUCUYA ULAŞAN isteği bilir. Ağ koparsa, geçit patlarsa
--     ya da toplu gönderim yarıda kalırsa "kim neyi denedi" sorusunun cevabı
--     yalnız burada kalır. Duyuru gönderimi akış hâlinde yapılıyor; istek
--     yarıda kesilirse bazı müşteriler mesajı almış olabilir ve bunu bilen tek
--     satır bu tablodadır.
--  2. TEMEL ÇİZGİ VE POLİTİKA. "Bu şablonu Kontrol Merkezi'nden en son kim,
--     ne zaman, hangi gerekçeyle açtı" sorusunun cevabı BLD'de yoktur: orada
--     yalnız `enabled` sütununun bugünkü değeri var. Metnin KENDİSİ burada
--     saklanmaz; yalnız özeti, uzunluğu ve segmenti tutulur — böylece metin
--     başka bir yerden değiştiğinde ekran sapmayı görebilir ve müşteriye giden
--     cümle ikinci bir yere yazılmamış olur.
--  3. TOPLU DUYURUNUN KURU PROVA JETONU. Gerçek gönderim ancak bir provanın
--     ardından yapılabilir ve provada görülen alıcı sayısı ile gönderilen
--     `confirm_recipients` aynı olmalıdır.
--
-- K5: tablo VE index adlarının hepsi `mod_bld_sms_` önekiyle başlar.

-- Yazma denemelerinin yerel izi. SATIR SİLİNMEZ.
CREATE TABLE IF NOT EXISTS mod_bld_sms_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL DEFAULT 'sms_template',  -- sms_template | announcement | test
    target_key  TEXT NOT NULL DEFAULT '',              -- şablon anahtarı ya da boş
    action      TEXT NOT NULL,                         -- template_update | template_enable |
                                                       -- template_disable | template_preview |
                                                       -- send_test | announcement_update |
                                                       -- announcement_dry_run | announcement_run
    reason      TEXT NOT NULL DEFAULT '',
    actor       TEXT NOT NULL DEFAULT '',              -- oturumdan gelir, gövdeden DEĞİL
    result      TEXT NOT NULL DEFAULT '',              -- denendi | ok | dry_run |
                                                       -- engellendi | hata
    detail      TEXT NOT NULL DEFAULT '{}',            -- metnin TAMAMI yazılmaz
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_bld_sms_audit_target
    ON mod_bld_sms_audit (target_type, target_key, created_at);

CREATE INDEX IF NOT EXISTS mod_bld_sms_audit_time
    ON mod_bld_sms_audit (created_at);

-- Şablonun TEMEL ÇİZGİSİ: Kontrol Merkezi'nden yazdığımız andaki hâlinin
-- ölçüsü. `body_hash` metnin sha256 özetidir, metnin kendisi DEĞİL — müşteriye
-- giden cümlenin ikinci bir kopyası tutulmaz ve denetim satırına da yazılmaz
-- (sözleşme: `payload_json` metnin tamamını yazmaz). Özet yalnız tek bir soruya
-- cevap verir: "sunucudaki metin, bizim yazdığımız metin mi?" Cevap hayırsa
-- ekran sapmayı söyler; sessizce üstüne yazmak, başkasının düzeltmesini
-- görmeden ezmek olurdu.
CREATE TABLE IF NOT EXISTS mod_bld_sms_templates (
    template_key TEXT PRIMARY KEY,
    body_hash    TEXT NOT NULL DEFAULT '',
    length       INTEGER NOT NULL DEFAULT 0,
    segments     INTEGER NOT NULL DEFAULT 0,
    enabled      INTEGER NOT NULL DEFAULT 0,   -- yazdığımız andaki değer
    actor        TEXT NOT NULL DEFAULT '',
    reason       TEXT NOT NULL DEFAULT '',
    updated_at   TEXT NOT NULL
);

-- Tetikleyici politikası: bir bildirimin Kontrol Merkezi'nden BİLİNÇLİ olarak
-- açıldığı an. BLD'de yalnız `enabled` sütununun bugünkü değeri var; "kim
-- açtı, neden açtı" orada yok ve açık doğmuş bir şablon ile bilerek açılmış
-- bir şablon oradan ayırt edilemez. Ayrım paraya dokunur: açık doğan bir
-- şablon tek dağıtımı binlerce SMS'e çevirir.
CREATE TABLE IF NOT EXISTS mod_bld_sms_triggers (
    template_key   TEXT PRIMARY KEY,
    confirmed      INTEGER NOT NULL DEFAULT 0,  -- 1 = bu ekrandan açıldı
    last_state     INTEGER NOT NULL DEFAULT 0,  -- son yazdığımız enabled değeri
    changed_by     TEXT NOT NULL DEFAULT '',
    reason         TEXT NOT NULL DEFAULT '',
    changed_at     TEXT NOT NULL DEFAULT ''
);

-- Toplu duyurunun kuru prova jetonu. Gerçek gönderim jetonsuz YAPILMAZ:
-- prova zorunlu ilk adımdır ve "kaç kişiye, hangi metin" sorusunun cevabı
-- gönderimden ÖNCE görülmelidir. `recipients` provada sunucunun söylediği
-- sayıdır; gerçek gönderimde `confirm_recipients` olarak geri gider ve sunucu
-- o andaki hesabıyla birebir eşleşmiyorsa 409 verir.
CREATE TABLE IF NOT EXISTS mod_bld_sms_announcement_dry (
    token       TEXT PRIMARY KEY,
    audience    TEXT NOT NULL DEFAULT '',
    recipients  INTEGER NOT NULL DEFAULT 0,
    segments    INTEGER NOT NULL DEFAULT 0,
    body_hash   TEXT NOT NULL DEFAULT '',   -- provadaki metin; gönderimde değişmiş mi
    actor       TEXT NOT NULL DEFAULT '',
    reason      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    used_at     TEXT NOT NULL DEFAULT ''    -- bir jeton yalnız BİR KEZ kullanılır
);

CREATE INDEX IF NOT EXISTS mod_bld_sms_announcement_dry_time
    ON mod_bld_sms_announcement_dry (created_at);
