ALTER TABLE mod_bbd_tablet_devices
    ADD COLUMN applied_profile_revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE mod_bbd_tablet_devices
    ADD COLUMN applied_profile_id TEXT;
