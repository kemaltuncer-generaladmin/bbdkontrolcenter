CREATE TABLE IF NOT EXISTS mod_bbd_tablet_profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    timezone TEXT NOT NULL DEFAULT 'Europe/Istanbul',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mod_bbd_tablet_profile_apps (
    profile_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    package_name TEXT NOT NULL,
    app_name TEXT NOT NULL DEFAULT '',
    allowed INTEGER NOT NULL DEFAULT 0,
    unlimited INTEGER NOT NULL DEFAULT 0,
    daily_limit_seconds INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (profile_id, revision, package_name)
);
CREATE TABLE IF NOT EXISTS mod_bbd_tablet_devices (
    id TEXT PRIMARY KEY,
    uuid TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    profile_id TEXT,
    token_hash TEXT,
    enrollment_code_hash TEXT,
    enrollment_expires_at TEXT,
    manufacturer TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    android_version TEXT NOT NULL DEFAULT '',
    app_version TEXT NOT NULL DEFAULT '',
    last_seen_at TEXT,
    last_policy_applied_at TEXT,
    policy_health TEXT NOT NULL DEFAULT '',
    revoked_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mod_bbd_tablet_inventory (
    device_id TEXT NOT NULL,
    package_name TEXT NOT NULL,
    app_label TEXT NOT NULL DEFAULT '',
    version_name TEXT NOT NULL DEFAULT '',
    version_code TEXT NOT NULL DEFAULT '',
    is_system_app INTEGER NOT NULL DEFAULT 0,
    is_launchable INTEGER NOT NULL DEFAULT 0,
    installed_at TEXT,
    updated_at TEXT,
    PRIMARY KEY (device_id, package_name)
);
CREATE TABLE IF NOT EXISTS mod_bbd_tablet_sessions (
    id TEXT PRIMARY KEY,
    student_id TEXT NOT NULL,
    student_name TEXT NOT NULL DEFAULT '',
    device_id TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    login_at TEXT NOT NULL,
    last_heartbeat_at TEXT NOT NULL,
    ended_at TEXT,
    state TEXT NOT NULL DEFAULT 'active',
    policy_revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS mod_bbd_tablet_login_failures (
    device_id TEXT PRIMARY KEY,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT NOT NULL,
    blocked_until TEXT
);
CREATE INDEX IF NOT EXISTS mod_bbd_tablet_sessions_student_active
    ON mod_bbd_tablet_sessions (student_id, state);
CREATE UNIQUE INDEX IF NOT EXISTS mod_bbd_tablet_one_active_student
    ON mod_bbd_tablet_sessions (student_id) WHERE state = 'active';
CREATE UNIQUE INDEX IF NOT EXISTS mod_bbd_tablet_one_active_device
    ON mod_bbd_tablet_sessions (device_id) WHERE state = 'active';
CREATE TABLE IF NOT EXISTS mod_bbd_tablet_daily_usage (
    student_id TEXT NOT NULL,
    package_name TEXT NOT NULL,
    local_date TEXT NOT NULL,
    used_seconds INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (student_id, package_name, local_date)
);
CREATE TABLE IF NOT EXISTS mod_bbd_tablet_usage_batches (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    accepted_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mod_bbd_tablet_usage_batch_items (
    batch_id TEXT NOT NULL,
    package_name TEXT NOT NULL,
    local_date TEXT NOT NULL,
    used_seconds INTEGER NOT NULL,
    PRIMARY KEY (batch_id, package_name, local_date)
);
CREATE TABLE IF NOT EXISTS mod_bbd_tablet_grants (
    id TEXT PRIMARY KEY,
    student_id TEXT NOT NULL,
    package_name TEXT NOT NULL,
    local_date TEXT NOT NULL,
    extra_seconds INTEGER NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS mod_bbd_tablet_grants_student_day
    ON mod_bbd_tablet_grants (student_id, local_date);
