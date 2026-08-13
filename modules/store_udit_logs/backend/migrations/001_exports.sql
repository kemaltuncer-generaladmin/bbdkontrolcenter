-- UDİT İşlem Kayıtları modülünün YEREL tablosu.
--
-- BU EKRAN SALT OKUNURDUR ve denetim kaydının kopyasını TUTMAZ. Kayıt iki
-- kaynakta durur ve ikisi de bizim değildir:
--   · uzak `admin_api_audits` (mağaza)
--   · yerel `mod_store_api_audit` (store_api geçidinin kendi tablosu — o
--     modülün malıdır, buradan YAZILMAZ; `store.api` yeteneği üzerinden
--     okunur, K5)
-- Kaydı kopyalamak, mağaza tarafında bir düzeltme yapıldığında sessizce
-- yanlış geçmiş göstermek demektir.
--
-- Karşılığı olmayan TEK veri şudur: DÖKÜM ALMA OLAYI. Denetim kaydını okumak
-- serbesttir; onu binlerce satır hâlinde bir dosyaya döküp binadan çıkarmak
-- izlenmesi gereken bir olaydır. Dosya bizim tarafımızda üretildiği için
-- mağaza bunu göremez; izi burada durur.

CREATE TABLE IF NOT EXISTS mod_store_udit_logs_exports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,                  -- csv | pdf:dump | pdf:record
    range_start TEXT NOT NULL DEFAULT '',
    range_end   TEXT NOT NULL DEFAULT '',
    rows        INTEGER NOT NULL DEFAULT 0,
    path        TEXT NOT NULL DEFAULT '',
    actor       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_store_udit_logs_exports_time
    ON mod_store_udit_logs_exports (created_at);

CREATE INDEX IF NOT EXISTS mod_store_udit_logs_exports_actor
    ON mod_store_udit_logs_exports (actor, created_at);
