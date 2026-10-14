-- Kendini güncelleme ve uzaktan tanılama (sözleşme v2.2).
--
-- Tabletin sync'te bildirdiği kurulu TabletKontrol versionCode'u, son
-- `selfUpdate` nesnesi (sıkıştırılmış JSON) ve politika hatasının özeti.
-- Sunucu bunları yalnız değer değişince yazar; eski tabletlerde NULL kalır.
ALTER TABLE mod_bbd_tablet_devices ADD COLUMN app_version_code INTEGER;
ALTER TABLE mod_bbd_tablet_devices ADD COLUMN self_update TEXT;
ALTER TABLE mod_bbd_tablet_devices ADD COLUMN policy_detail TEXT;

-- Tabletin `diagnostics` komutuyla yüklediği tanı raporları. Tablet başına
-- en yeni 5 rapor kalır; 30 günden eskiler günlük temizlikte silinir.
CREATE TABLE IF NOT EXISTS mod_bbd_tablet_diagnostics (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    command_id TEXT,
    created_at TEXT NOT NULL,
    app_version TEXT NOT NULL DEFAULT '',
    app_version_code INTEGER,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    text TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS mod_bbd_tablet_diagnostics_device
    ON mod_bbd_tablet_diagnostics (device_id, created_at);
