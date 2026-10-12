-- Mobil Geçit — modülün KENDİ tabloları (K5). Başka modülün verisi burada
-- tutulmaz; öğrenci, kantin, tablet ve zil verisi kendi modülündedir.
--
-- Zaman damgaları epoch-milisaniye INTEGER'dır: pencere sayımları ve süre
-- dolumu sayısal karşılaştırmayla yapılır (çevirmen BIGINT'e genişletir).

-- İdempotensi: aynı `Idempotency-Key` 24 saat içinde aynı yanıtı alır.
-- `status_code = 0` istek hâlâ işleniyor demektir. `owner` yarışı çözer:
-- iki eşzamanlı istekten yalnız satırı yazan işler.
CREATE TABLE IF NOT EXISTS mod_mobile_gateway_idempotency (
    key           TEXT PRIMARY KEY,
    route         TEXT NOT NULL,
    request_hash  TEXT NOT NULL,
    owner         TEXT NOT NULL,
    status_code   INTEGER NOT NULL DEFAULT 0,
    response      TEXT NOT NULL DEFAULT '',
    created_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS mod_mobile_gateway_idempotency_at
    ON mod_mobile_gateway_idempotency (created_at);

-- Hız sınırı ve kilit sayaçları. `subject` telefon ise DÜZ TUTULMAZ (sha256).
-- bucket: auth_phone_fail | verify_student_fail | lookup | sms_phone |
--         sms_global | call_student
CREATE TABLE IF NOT EXISTS mod_mobile_gateway_hits (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    bucket   TEXT NOT NULL,
    subject  TEXT NOT NULL,
    at       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS mod_mobile_gateway_hits_window
    ON mod_mobile_gateway_hits (bucket, subject, at);
CREATE INDEX IF NOT EXISTS mod_mobile_gateway_hits_at
    ON mod_mobile_gateway_hits (at);

-- OTP SMS aktarım kaydı. Telefon MASKELİ, kod ve metin HİÇ yazılmaz.
CREATE TABLE IF NOT EXISTS mod_mobile_gateway_sms_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    INTEGER NOT NULL,
    phone_masked  TEXT NOT NULL,
    purpose       TEXT NOT NULL,
    status        TEXT NOT NULL,
    reason        TEXT NOT NULL DEFAULT '',
    user_id       TEXT NOT NULL DEFAULT '',
    on_behalf_of  TEXT NOT NULL DEFAULT '',
    request_id    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS mod_mobile_gateway_sms_log_at
    ON mod_mobile_gateway_sms_log (created_at);

-- Denetim izi: durum değiştiren her işlem. `on_behalf_of` yalnız iz içindir,
-- yetki VERMEZ. `detail` JSON'dur; telefon, şifre, OTP ve metin taşımaz.
CREATE TABLE IF NOT EXISTS mod_mobile_gateway_audit (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    INTEGER NOT NULL,
    action        TEXT NOT NULL,
    student_id    TEXT NOT NULL DEFAULT '',
    user_id       TEXT NOT NULL DEFAULT '',
    on_behalf_of  TEXT NOT NULL DEFAULT '',
    request_id    TEXT NOT NULL DEFAULT '',
    result        TEXT NOT NULL,
    detail        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS mod_mobile_gateway_audit_at
    ON mod_mobile_gateway_audit (created_at);
CREATE INDEX IF NOT EXISTS mod_mobile_gateway_audit_student
    ON mod_mobile_gateway_audit (student_id, id);
