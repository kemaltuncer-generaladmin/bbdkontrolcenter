-- Kantin Yedekleri — modülün KENDİ tablosu (K5).
--
-- Sunucudaki yedeklerin listesi kantinden canlı okunur, burada KOPYALANMAZ.
-- Bu tablo yalnızca BU MAKİNEYE indirilmiş kopyaların defteridir: hangi dosya,
-- ne zaman, hangi sha256 ile indirildi ve doğrulaması tuttu mu.

CREATE TABLE IF NOT EXISTS mod_bbd_canteen_backups_local (
    name         TEXT PRIMARY KEY,           -- sunucudaki dosya adı
    path         TEXT NOT NULL,              -- yereldeki tam yol
    size         INTEGER NOT NULL DEFAULT 0,
    sha256       TEXT NOT NULL DEFAULT '',   -- yerel dosyadan hesaplanan
    server_sha256 TEXT NOT NULL DEFAULT '',  -- sunucunun bildirdiği
    verified     INTEGER NOT NULL DEFAULT 0, -- ikisi tutuyor mu
    created_at   TEXT NOT NULL,              -- sunucudaki yedek zamanı
    downloaded_at TEXT NOT NULL,
    downloaded_by TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS mod_bbd_canteen_backups_local_at
    ON mod_bbd_canteen_backups_local (downloaded_at);
