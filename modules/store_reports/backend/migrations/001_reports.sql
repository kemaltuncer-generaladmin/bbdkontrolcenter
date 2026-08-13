-- Raporlar modülünün YEREL tabloları.
--
-- Buraya yalnız Bagisto'da KARŞILIĞI OLMAYAN veri yazılır. Rapor rakamlarının
-- hiçbiri burada durmaz: mağaza tarafında bir sipariş düzeltilince kopya
-- sessizce yanlış rakam gösterirdi. Saklanan üç şeyin karşılığı yoktur:
--
--  1. KAYDEDİLMİŞ RAPOR. "Şu yapraklar, şu tarih aralığı, şu kırılım" bir
--     KULLANICI TERCİHİDİR; mağazanın haberi olmaz ve olmamalıdır.
--  2. ZAMANLANMIŞ RAPOR. Hangi raporun ne zaman üretileceği Kontrol
--     Merkezi'nin işidir; Bagisto'da zamanlanmış rapor kavramı yok.
--  3. ÜRETİM İZİ. "Ne üretmeye çalıştık, dosya nereye yazıldı, neden
--     patladı" — ağ koparsa bu kayıt yalnızca burada kalır.
--
-- SİLME YOKTUR. Kaydedilmiş rapor arşivlenir (`active = 0`), satır durur;
-- BBD veri silme yasağı ve ADR 0012 böyle diyor.

CREATE TABLE IF NOT EXISTS mod_store_reports_saved (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    leaves          TEXT NOT NULL DEFAULT '[]',   -- yaprak anahtarları (JSON)
    params          TEXT NOT NULL DEFAULT '{}',   -- tarih aralığı, kırılım, süzgeç
    actor           TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    active          INTEGER NOT NULL DEFAULT 1,
    archived_reason TEXT NOT NULL DEFAULT '',
    archived_by     TEXT NOT NULL DEFAULT '',
    archived_at     TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS mod_store_reports_saved_active
    ON mod_store_reports_saved (active, id);

CREATE TABLE IF NOT EXISTS mod_store_reports_schedules (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    leaves       TEXT NOT NULL DEFAULT '[]',
    params       TEXT NOT NULL DEFAULT '{}',
    period       TEXT NOT NULL DEFAULT 'weekly',  -- weekly | monthly
    weekday      INTEGER NOT NULL DEFAULT 0,      -- pazartesi = 0
    day_of_month INTEGER NOT NULL DEFAULT 1,      -- 1..28 (29-31 her ayda yok)
    at_time      TEXT NOT NULL DEFAULT '07:00',
    active       INTEGER NOT NULL DEFAULT 1,
    actor        TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    last_run_at  TEXT NOT NULL DEFAULT '',
    last_result  TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS mod_store_reports_schedules_active
    ON mod_store_reports_schedules (active, period);

CREATE TABLE IF NOT EXISTS mod_store_reports_runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,                 -- yaprak anahtarı | package | csv
    leaves     TEXT NOT NULL DEFAULT '[]',
    actor      TEXT NOT NULL DEFAULT '',
    path       TEXT NOT NULL DEFAULT '',
    name       TEXT NOT NULL DEFAULT '',
    rows       INTEGER NOT NULL DEFAULT 0,
    ok         INTEGER NOT NULL DEFAULT 0,
    error      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_store_reports_runs_time
    ON mod_store_reports_runs (created_at);
