-- Dahili AI Ekranları modülünün YEREL tabloları.
--
-- Buraya yalnız Bagisto'da KARŞILIĞI OLMAYAN veri yazılır. Ürün, kategori,
-- yorum ve talep mağazadadır ve kopyalanmaz. Dört şeyin karşılığı yok:
--
--  1. ÖNERİ DEFTERİ (`run`). Ekranın kimliği "onaysız uygulama yok"
--     kuralıdır; bu kuralın kanıtı, uygulanan satırların ÖNCEDEN ve
--     DEĞİŞTİRİLMEDEN kaydedilmiş olmasıdır. Uygulama bu tabloyu okur ve
--     öneriyi YENİDEN ÜRETMEZ. Ayrıca gerekçe ve maliyet burada durur:
--     mağazanın AI kaydı gerekçe alanı taşımıyor ve ağ koparsa "ne yapmaya
--     çalıştık" bilgisi yalnız burada kalır.
--  2. BÜTÇE SAYACI. Aylık harcama mağazadan okunur ama uç düşerse bütçe
--     kapısı körleşirdi; yerel defter yedek sayaçtır (analytics.ledger_spend).
--  3. SUSTURULMUŞ BULGU (`ignored`). "Bu ürünün barkodu bilerek yok" bilgisi
--     mağazada tutulacak bir şey değil, bu ekranın denetim tercihidir.
--     Susturma SİLME DEĞİLDİR: satır kalır, `released_at` dolunca yeniden
--     görünür — kayıt eklenerek geri alınır, silinerek değil.
--  4. EKRAN TERCİHİ (`prefs`). Aylık bütçe, sınır davranışı, açık araçlar ve
--     denetim eşikleri vitrini etkilemez; mağaza ayarı değildir.
--
-- Denetim izi (`audit`) beşinci tablo değil, bilinçli bir ayrımdır: `run`
-- yalnız AI çalışmalarını tutar, `audit` ayar/susturma/uygulama gibi tüm
-- yazma girişimlerini tutar ve BAŞARISIZ denemeleri de kaydeder.

CREATE TABLE IF NOT EXISTS mod_store_ai_run (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    token       TEXT NOT NULL UNIQUE,          -- yerel jeton; uygulama bununla gelir
    run_id      INTEGER NOT NULL DEFAULT 0,    -- mağazadaki çalışma numarası (varsa)
    tool        TEXT NOT NULL,
    params      TEXT NOT NULL DEFAULT '{}',
    rows        TEXT NOT NULL DEFAULT '[]',    -- FARK TABLOSUNUN KENDİSİ
    -- Modelin ÜRETTİĞİ OKUNUR ÖZET. Fark tablosu olmayan araçların (yorum
    -- duygu özeti) çıktısının TAMAMI budur; saklanmazsa o çalışma geçmişte
    -- boş bir satıra dönüşür ve tıklanınca bomboş bir çekmece açılır.
    summary_text TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'preview',  -- preview | applied | failed | readonly
    actor       TEXT NOT NULL DEFAULT '',
    reason      TEXT NOT NULL DEFAULT '',
    tokens      INTEGER NOT NULL DEFAULT 0,
    cost        INTEGER NOT NULL DEFAULT 0,    -- KURUŞ
    targets     INTEGER NOT NULL DEFAULT 0,
    applied     INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    error       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    applied_at  TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS mod_store_ai_run_created
    ON mod_store_ai_run (created_at);

CREATE INDEX IF NOT EXISTS mod_store_ai_run_tool
    ON mod_store_ai_run (tool, created_at);

-- Susturulmuş katalog bulgusu. Anahtar `kural:hedef` biçimindedir; aynı ürün
-- başka bir kurala takılırsa o bulgu görünmeye devam eder.
CREATE TABLE IF NOT EXISTS mod_store_ai_ignored (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_key TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT '',
    target_id   INTEGER NOT NULL DEFAULT 0,
    reason      TEXT NOT NULL DEFAULT '',
    actor       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    released_at TEXT NOT NULL DEFAULT ''       -- dolduysa susturma kalkmıştır
);

CREATE INDEX IF NOT EXISTS mod_store_ai_ignored_key
    ON mod_store_ai_ignored (finding_key, created_at);

CREATE TABLE IF NOT EXISTS mod_store_ai_prefs (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mod_store_ai_audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id  INTEGER NOT NULL DEFAULT 0,     -- 0 = tek hedefe bağlı değil
    action     TEXT NOT NULL,
    reason     TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    result     TEXT NOT NULL DEFAULT '',       -- denendi | ok | dry_run | hata | reddedildi
    detail     TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_store_ai_audit_time
    ON mod_store_ai_audit (created_at);
