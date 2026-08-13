-- Deneme Kulübü modülünün YEREL tabloları.
--
-- Buraya yalnız Bagisto'da KARŞILIĞI OLMAYAN veri yazılır. Deneme, katılımcı
-- ve sonuç kayıtları mağazadadır (`/api/admin/bbd/trial-club/*`) ve
-- kopyalanmaz: kopya, mağaza tarafında yapılan bir değişiklikten sonra
-- sessizce yanlış kontenjan ya da yanlış net gösterir.
--
-- Üç şeyin karşılığı yok:
--  1. GEREKÇE. Mağaza denetim kaydı tutuyor ama "neden" alanı yok. Ayrıca ağ
--     koparsa "ne yapmaya çalıştık" kaydı yalnız burada kalır.
--  2. SONUÇ YÜKLEME ÖNİZLEMESİ. Eşleştirme tablosu burada durur; uygulanan
--     satırların önizlenen satırlar olduğunu kanıtlar. Jeton olmadan
--     `results/apply` reddedilir.
--  3. EKRAN TERCİHİ. Kontenjan uyarı eşiği ve yoklama çizelgesindeki boş
--     satır sayısı vitrini etkilemez; yalnız bu ekranın görünümüdür.

CREATE TABLE IF NOT EXISTS mod_store_trial_club_audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_id    INTEGER NOT NULL DEFAULT 0,   -- 0 = denemeye bağlı olmayan iş
    member_id  INTEGER NOT NULL DEFAULT 0,
    action     TEXT NOT NULL,
    reason     TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    result     TEXT NOT NULL DEFAULT '',     -- denendi | ok | dry_run | hata | uc_yok
    detail     TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_store_trial_club_audit_exam
    ON mod_store_trial_club_audit (exam_id, created_at);

CREATE INDEX IF NOT EXISTS mod_store_trial_club_audit_time
    ON mod_store_trial_club_audit (created_at);

-- Sonuç yükleme önizlemesi. `rows` mağazaya gidecek gövdenin KENDİSİDİR;
-- uygulama onu okur ve YENİDEN EŞLEŞTİRMEZ — kullanıcı neyi onayladıysa o
-- yazılır. Aradan geçen sürede katılımcı listesi değişse bile.
CREATE TABLE IF NOT EXISTS mod_store_trial_club_upload (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    token      TEXT NOT NULL UNIQUE,
    exam_id    INTEGER NOT NULL,
    filename   TEXT NOT NULL DEFAULT '',
    summary    TEXT NOT NULL DEFAULT '{}',
    rows       TEXT NOT NULL DEFAULT '[]',
    status     TEXT NOT NULL DEFAULT 'preview',   -- preview | dry_run | applied
    actor      TEXT NOT NULL DEFAULT '',
    reason     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS mod_store_trial_club_upload_exam
    ON mod_store_trial_club_upload (exam_id, created_at);

CREATE TABLE IF NOT EXISTS mod_store_trial_club_prefs (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
