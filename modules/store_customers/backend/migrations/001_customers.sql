-- Müşteriler modülünün YEREL tabloları.
--
-- Buraya yalnız Bagisto'da KARŞILIĞI OLMAYAN veri yazılır. Müşteri, adres,
-- sipariş ve yorum mağazadadır ve kopyalanmaz: kopya, mağaza tarafında yapılan
-- bir değişiklikten sonra sessizce yanlış bilgi gösterir. Kişisel veri söz
-- konusu olduğunda kopya ayrıca KVKK yüküdür — ikinci bir yerde daha
-- silinmesi, saklanması ve korunması gerekir.
--
-- ÜÇ ŞEYİN KARŞILIĞI YOK:
--  1. GEREKÇE. Bagisto denetim kaydı tutuyor ama "neden" alanı yok. Ağ
--     koparsa "ne yapmaya çalıştık" kaydı yalnız burada kalır.
--  2. YORUMUN SPAM ETİKETİ VE MAĞAZA YANITININ KOPYASI. Bagisto'nun üç yorum
--     durumu var (pending/approved/disapproved); SPAM YOK. Operatörün "bu
--     reklam" kararı mağazada "reddedildi" olarak görünür ve ayrımı burada
--     durur.
--  3. İZİN GEÇMİŞİ. Bagisto izin DEĞİŞİKLİĞİ geçmişi tutmuyor; yalnız o anki
--     değeri var. "Ne zaman, kim, hangi gerekçeyle bülten aboneliğini kapattı"
--     sorusunun cevabı KVKK'da gerekiyor ve yalnız buradan başlayarak birikir.
--
-- Nüfus taraması (RFM segmentasyonu) BİLEREK burada DEĞİL bellektedir: taranan
-- satırlar ad, e-posta ve telefon taşır; diske ikinci bir kopya yazmamak için
-- servis onu kısa ömürlü bellek önbelleğinde tutar.

CREATE TABLE IF NOT EXISTS mod_store_customers_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL DEFAULT 0,   -- 0 = müşteriye bağlı olmayan iş
    review_id   INTEGER NOT NULL DEFAULT 0,
    action      TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    actor       TEXT NOT NULL DEFAULT '',
    result      TEXT NOT NULL DEFAULT '',     -- denendi | ok | dry_run | hata
    detail      TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_store_customers_audit_customer
    ON mod_store_customers_audit (customer_id, created_at);

CREATE INDEX IF NOT EXISTS mod_store_customers_audit_review
    ON mod_store_customers_audit (review_id, created_at);

-- Yorumun yerel etiketi. `spam` mağazada karşılığı olmayan tek alandır;
-- `reply` mağazaya yazılan yanıtın kopyasıdır (ağ koparsa metin kaybolmasın).
CREATE TABLE IF NOT EXISTS mod_store_customers_review_flags (
    review_id  INTEGER PRIMARY KEY,
    spam       INTEGER NOT NULL DEFAULT 0,
    reply      TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    reason     TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

-- İzin geçmişi. Yalnız BU ekrandan yapılan değişiklikler; müşterinin vitrinden
-- yaptıkları buraya düşmez ve ekran bunu söyler.
CREATE TABLE IF NOT EXISTS mod_store_customers_consent (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id  INTEGER NOT NULL,
    kind         TEXT NOT NULL,               -- newsletter | sms | gdpr | verified
    before_value TEXT NOT NULL DEFAULT '',
    after_value  TEXT NOT NULL DEFAULT '',
    actor        TEXT NOT NULL DEFAULT '',
    reason       TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_store_customers_consent_customer
    ON mod_store_customers_consent (customer_id, created_at);
