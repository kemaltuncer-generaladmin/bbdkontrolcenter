-- Uygulama dağıtımı (sözleşme v2.1): yüklemeler/yayınlar, dağıtılan
-- uygulamalar, "tabletlerden kaldır" listesi ve indirme sırası.

-- Bir yükleme ile ondan doğan yayın aynı satırdır (uploadId = releaseId).
-- state: uploading → processing → draft → published → superseded/withdrawn;
-- işlenemeyen paket failed olur.
CREATE TABLE IF NOT EXISTS mod_bbd_tablet_app_releases (
    id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    file_name TEXT NOT NULL DEFAULT '',
    fingerprint TEXT NOT NULL DEFAULT '',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    received_bytes INTEGER NOT NULL DEFAULT 0,
    package_name TEXT NOT NULL DEFAULT '',
    app_name TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'app',
    version_code INTEGER NOT NULL DEFAULT 0,
    version_name TEXT NOT NULL DEFAULT '',
    min_sdk INTEGER,
    total_bytes INTEGER NOT NULL DEFAULT 0,
    files_json TEXT NOT NULL DEFAULT '[]',
    error TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    published_at TEXT
);

-- Paket başına güncel yayın. Silinen satır dağıtımın bittiği anlamına gelir.
CREATE TABLE IF NOT EXISTS mod_bbd_tablet_managed_apps (
    package_name TEXT PRIMARY KEY,
    app_name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'app',
    release_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Sync'teki `apps.remove`: tabletler bu paketleri sessizce kaldırır.
-- Paket yeniden dağıtılınca listeden düşer.
CREATE TABLE IF NOT EXISTS mod_bbd_tablet_app_removals (
    package_name TEXT PRIMARY KEY,
    removed_at TEXT NOT NULL,
    removed_by TEXT NOT NULL DEFAULT ''
);

-- Tablet × yayın. state: waiting | installed | failed. İndirme yuvası
-- `lease_until` gelecekteyse sürer; `seen_at` tabletin yayını son istediği an.
CREATE TABLE IF NOT EXISTS mod_bbd_tablet_install_queue (
    device_id TEXT NOT NULL,
    release_id TEXT NOT NULL,
    package_name TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'waiting',
    wanted_at TEXT NOT NULL,
    seen_at TEXT,
    priority_at TEXT,
    lease_until TEXT,
    received_bytes INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    error_at TEXT,
    installed_version_code INTEGER,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (device_id, release_id)
);

CREATE INDEX IF NOT EXISTS mod_bbd_tablet_app_releases_state
    ON mod_bbd_tablet_app_releases (state, updated_at);
CREATE INDEX IF NOT EXISTS mod_bbd_tablet_app_releases_upload
    ON mod_bbd_tablet_app_releases (file_name, size_bytes);
CREATE INDEX IF NOT EXISTS mod_bbd_tablet_install_queue_release
    ON mod_bbd_tablet_install_queue (release_id);
CREATE INDEX IF NOT EXISTS mod_bbd_tablet_install_queue_state
    ON mod_bbd_tablet_install_queue (state);
