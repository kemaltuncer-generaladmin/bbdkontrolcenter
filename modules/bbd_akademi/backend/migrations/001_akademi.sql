-- BBD Akademi yönetim paneli — modülün KENDİ tabloları (K5).
--
-- Akademi verisinin kopyası burada TUTULMAZ: öğrenci, program, deneme, cari
-- Akademi'dedir ve vekil üzerinden anlık okunur. Burada yalnız KM'nin
-- otoritesi olan şey durur: mobil yöneticiye kimin erişeceği (personel) ve
-- vekilin kendi kısa ömürlü kayıtları.

-- Mobil yönetici erişimi: KM bu listenin TEK sahibidir; Akademi'ye
-- `PUT /v1/admin/staff/sync` ile tam liste olarak itilir. Kişi bir KM
-- kullanıcısına bağlı olabilir (`km_user_id`) ya da elle girilir.
-- Silme yoktur, pasifleştirme vardır: pasif satır Akademi'ye `active=false`
-- olarak gider ve oradaki hesap kapanır.
CREATE TABLE IF NOT EXISTS mod_bbd_akademi_staff (
    id TEXT PRIMARY KEY,
    km_user_id TEXT,
    display_name TEXT NOT NULL,
    phone TEXT NOT NULL,
    roles TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    note TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT '',
    last_error_code TEXT NOT NULL DEFAULT '',
    last_error_message TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS mod_bbd_akademi_staff_phone ON mod_bbd_akademi_staff (phone);

-- Son eşitlemenin durumu (tek satır: name = 'staff').
CREATE TABLE IF NOT EXISTS mod_bbd_akademi_sync_state (
    name TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'never',
    source TEXT NOT NULL DEFAULT '',
    attempted_at TEXT,
    succeeded_at TEXT,
    payload_hash TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT ''
);

-- PIN teyidi (step-up) belirteçleri. Tek kullanımlık, kısa ömürlü, kişiye ve
-- işleme bağlı. Belirtecin kendisi değil SHA-256 özeti saklanır.
CREATE TABLE IF NOT EXISTS mod_bbd_akademi_step_up (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    consumed_by TEXT
);
