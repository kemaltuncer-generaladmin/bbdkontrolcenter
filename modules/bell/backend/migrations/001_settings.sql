-- Zil Sistemi — modülün KENDİ tablosu (K5).
--
-- BURADA DERS SAATİ TUTULMAZ. Saatler Ders Takvimi modülünündür; zil onları
-- `bbd_class_schedule.week` yeteneğinden okur. Burada yalnız "hangi grup hangi
-- sesi, hangi düzeyde, hangi derste çalsın" kararı durur.

CREATE TABLE IF NOT EXISTS mod_bell_settings (
    id         INTEGER PRIMARY KEY CHECK (id = 1),   -- tek satır
    payload    TEXT NOT NULL,                        -- {version, enabled, groups:{…}}
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT ''
);

-- Çalma günlüğü: zil çaldı mı, çalamadıysa neden. Sessiz arıza en kötüsü.
CREATE TABLE IF NOT EXISTS mod_bell_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL,
    group_id   TEXT NOT NULL DEFAULT '',
    group_name TEXT NOT NULL DEFAULT '',
    edge       TEXT NOT NULL DEFAULT '',   -- start | end | manual
    sound      TEXT NOT NULL DEFAULT '',
    ok         INTEGER NOT NULL DEFAULT 1,
    detail     TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS mod_bell_log_at ON mod_bell_log (at);
