-- Kontrol Paneli'nin YEREL tabloları.
--
-- Pano hiçbir mağaza verisini kopyalamaz: ciro, sipariş, stok ve sağlık her
-- açılışta `store.api` geçidinden TAZE okunur. Kopya tutmak, mağaza tarafında
-- yapılan bir değişiklikten sonra sessizce eski rakamı gösterirdi — ve panonun
-- tek işi doğru rakam göstermektir.
--
-- Karşılığı olmayan iki şey burada durur:
--  1. GEREKÇE. Bagisto denetim kaydı tutuyor ama "neden" alanı yok. Bakım modu
--     gibi vitrini kapatan bir işlemde gerekçe kaydın kendisi kadar önemlidir;
--     ayrıca ağ koparsa "ne yapmaya çalıştık" bilgisi yalnız burada kalır.
--  2. EKRAN TERCİHİ. Çalışma kanalı, dil, karşılaştırma kipi, saat dilimi ve
--     tarih biçimi Kontrol Merkezi'nin GÖRÜNTÜ tercihidir; vitrini etkilemez
--     ve mağaza ayarı değildir.

CREATE TABLE IF NOT EXISTS mod_store_dashboard_audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    action     TEXT NOT NULL,                 -- save_settings | maintenance
    reason     TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    result     TEXT NOT NULL DEFAULT '',      -- denendi | ok | dry_run | hata
    detail     TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_store_dashboard_audit_time
    ON mod_store_dashboard_audit (created_at);

CREATE TABLE IF NOT EXISTS mod_store_dashboard_prefs (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
