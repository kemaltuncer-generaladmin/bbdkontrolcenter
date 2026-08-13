-- Vergilendirme modülünün YEREL tabloları.
--
-- Buraya yalnız Bagisto'da KARŞILIĞI OLMAYAN veri yazılır. Oran, vergi
-- kategorisi ve ürün eşlemesi mağazadadır ve kopyalanmaz: kopya, mağaza
-- tarafında yapılan bir değişiklikten sonra sessizce yanlış oran gösterir ve
-- yanlış KDV beyanına yol açar.
--
-- Dört şeyin karşılığı yok:
--  1. GEREKÇE. Bagisto denetim kaydı tutuyor ama "neden" alanı yok. Ağ
--     koparsa "ne yapmaya çalıştık" kaydı yalnız burada kalır.
--  2. GEÇERLİLİK TARİHİ. Bagisto vergi oranında tarih alanı YOKTUR: oran
--     değişince yeni satışlar yeni orandan hesaplanır, geçmiş faturalar
--     olduğu gibi kalır. Muhasebe "bu oran ne zamandan beri geçerli"
--     sorusunun cevabını istiyor; o cevap burada tutulur ve ekranda
--     mağazanın davranışı olarak DEĞİL, bizim notumuz olarak gösterilir.
--  3. VERGİ KATEGORİSİ KULLANIMI. "Hiçbir ürüne atanmamış" süzgeci için
--     kategori başına ürün sayısı gerekir; bu sayı 1.419 ürünün taranmasıyla
--     çıkar ve her ekran açılışında tekrarlanamaz. Tarama sonucu ve tarihi
--     burada durur, ekran ne kadar bayat olduğunu SÖYLER.
--  4. TOPLU EŞLEME ÖNİZLEMESİ. Uygulanan şeyin önizlenen şey olduğunu
--     kanıtlar; jeton olmadan `mapping/apply` reddedilir.

CREATE TABLE IF NOT EXISTS mod_store_tax_audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    target     TEXT NOT NULL DEFAULT '',    -- rate:12 | category:3 | mapping | summary
    action     TEXT NOT NULL,
    reason     TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    result     TEXT NOT NULL DEFAULT '',    -- denendi | ok | dry_run | hata
    detail     TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_store_tax_audit_target
    ON mod_store_tax_audit (target, created_at);

CREATE INDEX IF NOT EXISTS mod_store_tax_audit_time
    ON mod_store_tax_audit (created_at);

-- Oranın geçerlilik tarihi. Bagisto'da bu alan YOK.
--
-- `effective_from` GEÇMİŞE yazılamaz (serviste doğrulanır): geriye dönük bir
-- geçerlilik tarihi, kesilmiş faturaların yanlış orandan kesildiğini iddia
-- eder ve mali müşavire yanlış bilgi verir.
CREATE TABLE IF NOT EXISTS mod_store_tax_effective (
    rate_id        INTEGER PRIMARY KEY,
    effective_from TEXT NOT NULL DEFAULT '',   -- YYYY-MM-DD
    note           TEXT NOT NULL DEFAULT '',
    actor          TEXT NOT NULL DEFAULT '',
    updated_at     TEXT NOT NULL
);

-- Vergi kategorisi başına ürün sayısı. Katalog taramasının SONUCUDUR, kopyası
-- değil: yalnız sayı ve tarama zamanı durur.
CREATE TABLE IF NOT EXISTS mod_store_tax_usage (
    tax_category_id INTEGER PRIMARY KEY,
    product_count   INTEGER NOT NULL DEFAULT 0,
    scanned_at      TEXT NOT NULL
);

-- Toplu ürün→vergi kategorisi eşlemesinin önizlemesi. `rows` fark tablosunun
-- kendisidir; uygulama onu okur ve YENİDEN HESAPLAMAZ.
CREATE TABLE IF NOT EXISTS mod_store_tax_assign (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    token      TEXT NOT NULL UNIQUE,
    params     TEXT NOT NULL DEFAULT '{}',
    rows       TEXT NOT NULL DEFAULT '[]',
    status     TEXT NOT NULL DEFAULT 'preview',   -- preview | dry_run | applied
    actor      TEXT NOT NULL DEFAULT '',
    reason     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS mod_store_tax_assign_created
    ON mod_store_tax_assign (created_at);
