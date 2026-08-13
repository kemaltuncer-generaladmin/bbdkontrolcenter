-- CMS modülünün YEREL tabloları.
--
-- Buraya yalnız Bagisto'da KARŞILIĞI OLMAYAN veri yazılır. Sayfa, menü ve
-- yönlendirme kaydı mağazadadır ve kopyalanmaz: kopya, mağaza tarafında
-- yapılan bir düzenlemeden sonra sessizce eski metni gösterir.
--
-- İki şeyin karşılığı yok:
--  1. GEREKÇE. Bagisto denetim kaydı tutuyor ama "neden" alanı yok. Ayrıca ağ
--     koparsa "ne yapmaya çalıştık" kaydı yalnız burada kalır.
--  2. SÜRÜM GEÇMİŞİ. Bagisto CMS sayfasının eski hâlini TUTMUYOR. "Dün ne
--     yazıyordu" ve "geri al" bu tablo olmadan cevapsızdır. Yasal metinlerde
--     bu bir kolaylık değil zorunluluk: mesafeli satış sözleşmesinin
--     değişmeden önceki hâli kanıt niteliğindedir.

CREATE TABLE IF NOT EXISTS mod_store_cms_audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id    INTEGER NOT NULL DEFAULT 0,   -- 0 = sayfaya bağlı olmayan iş (yönlendirme)
    action     TEXT NOT NULL,                -- save | create | restore | save_redirect
    reason     TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    result     TEXT NOT NULL DEFAULT '',     -- denendi | ok | dry_run | hata
    detail     TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_store_cms_audit_page
    ON mod_store_cms_audit (page_id, created_at);

CREATE INDEX IF NOT EXISTS mod_store_cms_audit_time
    ON mod_store_cms_audit (created_at);

-- Sürüm geçmişi. Satır YAZMADAN ÖNCE eklenir ve HİÇ SİLİNMEZ: geri alma da
-- yeni bir sürüm bırakır, böylece yanlış sürüme dönmek de geri alınabilir.
CREATE TABLE IF NOT EXISTS mod_store_cms_versions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id          INTEGER NOT NULL,
    title            TEXT NOT NULL DEFAULT '',
    slug             TEXT NOT NULL DEFAULT '',
    html_content     TEXT NOT NULL DEFAULT '',
    meta_title       TEXT NOT NULL DEFAULT '',
    meta_description TEXT NOT NULL DEFAULT '',
    meta_keywords    TEXT NOT NULL DEFAULT '',
    actor            TEXT NOT NULL DEFAULT '',
    reason           TEXT NOT NULL DEFAULT '',
    action           TEXT NOT NULL DEFAULT 'save',   -- save | restore
    created_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_store_cms_versions_page
    ON mod_store_cms_versions (page_id, id);
