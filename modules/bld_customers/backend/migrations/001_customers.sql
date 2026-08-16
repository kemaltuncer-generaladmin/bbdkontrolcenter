-- Müşteriler modülünün YEREL tabloları.
--
-- MÜŞTERİ SATIRI BURAYA ASLA YAZILMAZ. Ad, telefon, e-posta, adres, sipariş ve
-- abonelik BLD'dedir ve KOPYALANMAZ. Kopyalansaydı bu modül ikinci bir müşteri
-- veritabanına dönerdi: KVKK yüzeyi ikiye katlanır, silme talebi iki yerde
-- karşılanmak zorunda kalır ve yerel kopya her zaman bir tur geride olduğu için
-- "kapalı" görünen bir hesap aslında yarım saattir açık olabilirdi.
--
-- Üç şeyin BLD'de karşılığı yok:
--
--  1. KVKK ERİŞİM İZİ (`access`). Sunucu da `customer.read` satırı yazıyor
--     (`00-genel.md` §9) ama o kayıt yalnız SUNUCUYA ULAŞAN okumayı bilir. Ağ
--     koparsa, imza reddedilirse ya da geçit patlarsa "kim kimin kaydını açmak
--     istedi" sorusunun cevabı yalnız burada kalır. İki defter yan yana
--     konabilsin diye `action` adları sunucudakiyle AYNIDIR.
--  2. YAZMA DENEMESİ İZİ (`audit`). Aynı gerekçe, yazma tarafı: telefon
--     yazılırken ağ koparsa yeni numaranın geçip geçmediği bilinmez.
--  3. EKRAN TERCİHİ (`prefs`). Yalnız bu ekranın ne gösterdiğini belirler;
--     BLD'yi ETKİLEMEZ.
--
-- NE YAZILIR, NE YAZILMAZ: `access.filters` yalnız SÜZGEÇLERİ taşır, dönen
-- kayıtları değil (sözleşme §9.4). `audit.detail` eski ve yeni değeri taşır
-- ama TELEFON MASKELİDİR (`532****567`, sözleşme PATCH bölümü). Denetim izi
-- "ne değişti" sorusuna cevap vermeli, kişisel verinin ikinci bir kopyasını
-- tutmamalı.
--
-- SATIR SİLİNMEZ. İki iz tablosunda da `DELETE` yazan bir kod yoktur ve
-- olmayacaktır; denetim izini temizleyebilen bir ekran, denetim izi değildir.
--
-- K5: tablo VE index adlarının hepsi `mod_bld_customers_` önekiyle başlar.

-- KVKK erişim izi — HER OKUMA bir satır. Yazma izinden AYRI tablodur: okuma
-- satırları yazma satırlarından çok daha hızlı birikir ve tek tabloda
-- birleşselerdi "bu ay kim ne değiştirdi" sorusu binlerce okuma satırının
-- altında kaybolurdu.
CREATE TABLE IF NOT EXISTS mod_bld_customers_access (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    actor       TEXT NOT NULL DEFAULT '',        -- oturumdan gelir, gövdeden DEĞİL
    action      TEXT NOT NULL DEFAULT 'customer.read',
    scope       TEXT NOT NULL DEFAULT '',        -- list | detail | orders |
                                                 -- subscriptions | addresses | sms
    customer_id INTEGER NOT NULL DEFAULT 0,      -- 0 = liste okuması (tekil kayıt yok)
    filters     TEXT NOT NULL DEFAULT '{}',      -- YALNIZ süzgeçler; dönen kayıt YAZILMAZ
    result      TEXT NOT NULL DEFAULT '',        -- okundu | hata
    error       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_bld_customers_access_time
    ON mod_bld_customers_access (created_at);

CREATE INDEX IF NOT EXISTS mod_bld_customers_access_customer
    ON mod_bld_customers_access (customer_id, created_at);

CREATE INDEX IF NOT EXISTS mod_bld_customers_access_actor
    ON mod_bld_customers_access (actor, created_at);

-- Yazma denemelerinin yerel izi. SATIR SİLİNMEZ.
CREATE TABLE IF NOT EXISTS mod_bld_customers_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL DEFAULT 0,
    action      TEXT NOT NULL,                   -- customer.update |
                                                 -- customer.disable | customer.enable
    reason      TEXT NOT NULL DEFAULT '',
    actor       TEXT NOT NULL DEFAULT '',        -- oturumdan gelir, gövdeden DEĞİL
    result      TEXT NOT NULL DEFAULT '',        -- denendi | ok | dry_run |
                                                 -- engellendi | hata
    detail      TEXT NOT NULL DEFAULT '{}',      -- telefon MASKELİ
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_bld_customers_audit_customer
    ON mod_bld_customers_audit (customer_id, created_at);

CREATE INDEX IF NOT EXISTS mod_bld_customers_audit_time
    ON mod_bld_customers_audit (created_at);

-- Ekran tercihi. BLD'yi ETKİLEMEZ: yalnız listenin açılışta hangi süzgeçle ve
-- kaç satırla geldiğini belirler. Bu yüzden yazması gerekçe istemez.
CREATE TABLE IF NOT EXISTS mod_bld_customers_prefs (
    key        TEXT PRIMARY KEY,   -- page_size | status_filter | sort | direction
    value      TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
