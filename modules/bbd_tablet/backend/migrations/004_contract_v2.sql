-- Sözleşme v2: uzak cihaz durumu, telemetri, oturum sonu nedeni, komutlar,
-- öğrenci kısıtlaması ve profil kullanım saatleri.
ALTER TABLE mod_bbd_tablet_devices ADD COLUMN locked INTEGER NOT NULL DEFAULT 0;
ALTER TABLE mod_bbd_tablet_devices ADD COLUMN lock_message TEXT NOT NULL DEFAULT '';
ALTER TABLE mod_bbd_tablet_devices ADD COLUMN offline_mode TEXT NOT NULL DEFAULT 'local';
ALTER TABLE mod_bbd_tablet_devices ADD COLUMN announcement TEXT NOT NULL DEFAULT '';
ALTER TABLE mod_bbd_tablet_devices ADD COLUMN state_version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE mod_bbd_tablet_devices ADD COLUMN battery_percent INTEGER;
ALTER TABLE mod_bbd_tablet_devices ADD COLUMN charging INTEGER;
ALTER TABLE mod_bbd_tablet_devices ADD COLUMN foreground_package TEXT;
ALTER TABLE mod_bbd_tablet_devices ADD COLUMN pending_usage_batches INTEGER NOT NULL DEFAULT 0;
ALTER TABLE mod_bbd_tablet_devices ADD COLUMN next_sync_seconds INTEGER;
ALTER TABLE mod_bbd_tablet_devices ADD COLUMN inventory_hash TEXT;

ALTER TABLE mod_bbd_tablet_profiles ADD COLUMN usage_windows TEXT NOT NULL DEFAULT '[]';

ALTER TABLE mod_bbd_tablet_sessions ADD COLUMN end_reason TEXT;
ALTER TABLE mod_bbd_tablet_sessions ADD COLUMN end_message TEXT NOT NULL DEFAULT '';
ALTER TABLE mod_bbd_tablet_sessions ADD COLUMN profile_id TEXT;

-- Aynı paketin eşzamanlı iki kopyasından yalnız biri özet tabloya yazar.
ALTER TABLE mod_bbd_tablet_usage_batches ADD COLUMN claim TEXT;

ALTER TABLE mod_bbd_tablet_grants ADD COLUMN revoked_by TEXT;

UPDATE mod_bbd_tablet_sessions SET end_reason = 'logout'
    WHERE state = 'logged_out' AND end_reason IS NULL;
UPDATE mod_bbd_tablet_sessions SET end_reason = 'expired'
    WHERE state = 'expired' AND end_reason IS NULL;
UPDATE mod_bbd_tablet_sessions SET profile_id = (
    SELECT d.profile_id FROM mod_bbd_tablet_devices d
    WHERE d.id = mod_bbd_tablet_sessions.device_id
) WHERE profile_id IS NULL;

CREATE TABLE IF NOT EXISTS mod_bbd_tablet_commands (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    delivered_at TEXT,
    delivery_count INTEGER NOT NULL DEFAULT 0,
    acked_at TEXT,
    ok INTEGER,
    result_message TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS mod_bbd_tablet_student_blocks (
    id TEXT PRIMARY KEY,
    student_id TEXT NOT NULL,
    blocked_until TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    lifted_at TEXT
);

CREATE INDEX IF NOT EXISTS mod_bbd_tablet_devices_token
    ON mod_bbd_tablet_devices (token_hash);
CREATE INDEX IF NOT EXISTS mod_bbd_tablet_devices_enrollment
    ON mod_bbd_tablet_devices (enrollment_code_hash);
CREATE INDEX IF NOT EXISTS mod_bbd_tablet_sessions_token
    ON mod_bbd_tablet_sessions (token_hash);
CREATE INDEX IF NOT EXISTS mod_bbd_tablet_sessions_device_state
    ON mod_bbd_tablet_sessions (device_id, state);
CREATE INDEX IF NOT EXISTS mod_bbd_tablet_sessions_login
    ON mod_bbd_tablet_sessions (login_at);
CREATE INDEX IF NOT EXISTS mod_bbd_tablet_daily_usage_student_day
    ON mod_bbd_tablet_daily_usage (student_id, local_date);
CREATE INDEX IF NOT EXISTS mod_bbd_tablet_daily_usage_day
    ON mod_bbd_tablet_daily_usage (local_date);
CREATE INDEX IF NOT EXISTS mod_bbd_tablet_commands_device_ack
    ON mod_bbd_tablet_commands (device_id, acked_at);
CREATE INDEX IF NOT EXISTS mod_bbd_tablet_student_blocks_student
    ON mod_bbd_tablet_student_blocks (student_id);
