CREATE TABLE IF NOT EXISTS mod_bbd_tablet_profile_system_controls (
    profile_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    control_key TEXT NOT NULL,
    is_enabled INTEGER NOT NULL CHECK (is_enabled IN (0, 1)),
    PRIMARY KEY (profile_id, revision, control_key)
);
