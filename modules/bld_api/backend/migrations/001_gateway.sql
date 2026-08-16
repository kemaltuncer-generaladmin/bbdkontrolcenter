-- BLD geçidinin tek tablosu. BLD sunucusundaki hiçbir veri BURAYA
-- KOPYALANMAZ; tablo, sunucuda karşılığı OLMAYAN bilgiyi tutar.
--
-- Sunucunun kendi denetim tablosu (`veykemtu_control_audit`, sözleşme §3)
-- gerekçeyi ve aktörü tutuyor ama iki şeyi bilmiyor: (1) hiç gönderilemeyen
-- istek (ağ koptu, acil fren kapattı), (2) imzası reddedildiği için
-- denetleyiciye ULAŞAMAYAN istek — `VerifyControlSignature` middleware'i
-- denetleyiciden önce çalışıyor. Satır istek ÇIKMADAN yazılır:
-- `result` boş kalmışsa "gönderildi mi belli değil" demektir.
--
-- TABLO VE İNDEKS ADLARI `mod_bld_api_` ÖNEKİYLE BAŞLAR: çekirdek göçü
-- ad denetiminden geçirir ve başka önekli adı reddeder (K5).
CREATE TABLE IF NOT EXISTS mod_bld_api_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id  TEXT NOT NULL UNIQUE,     -- YEREL anahtar; sunucuya gitmez
    method      TEXT NOT NULL,
    path        TEXT NOT NULL,
    action      TEXT NOT NULL DEFAULT '', -- 'revoke_device', 'command:reprint' ...
    reason      TEXT NOT NULL DEFAULT '', -- sözleşme §3: en az 10 karakter
    actor       TEXT NOT NULL DEFAULT '', -- işlemi yapan kişinin adı
    dry_run     INTEGER NOT NULL DEFAULT 1,
    body        TEXT NOT NULL DEFAULT '', -- sırları maskelenmiş istek gövdesi
    status      INTEGER,                  -- HTTP kodu; geçitte durduysa NULL
    result      TEXT NOT NULL DEFAULT '', -- ok · blocked · dry_run · error:<kod>
    created_at  TEXT NOT NULL,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS mod_bld_api_audit_created
    ON mod_bld_api_audit (created_at DESC);

-- Gönderilmiş ama sonucu işlenememiş satırları (ağ koptu) tek sorguda
-- bulmak için: result = '' olanlar.
CREATE INDEX IF NOT EXISTS mod_bld_api_audit_result
    ON mod_bld_api_audit (result, created_at DESC);
