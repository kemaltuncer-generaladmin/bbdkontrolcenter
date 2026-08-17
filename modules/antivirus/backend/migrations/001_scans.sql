-- Antivirüs — modülün KENDİ tabloları (K5).
--
-- Tarama sonucu başka hiçbir modülün tablosuna yazılmaz; buradan okunur.
-- Satır tarama BİTİNCE yazılır (başarısız, zaman aşımına uğramış ve
-- durdurulmuş taramalar dahil). Devam eden tarama bellekte tutulur ve
-- /state ucunun `active` alanından okunur.

CREATE TABLE IF NOT EXISTS mod_antivirus_scan (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL DEFAULT 'quick',    -- quick | full
    engine        TEXT NOT NULL DEFAULT '',         -- clamdscan | clamscan | ''
    started_at    TEXT NOT NULL,
    finished_at   TEXT NOT NULL DEFAULT '',
    seconds       REAL NOT NULL DEFAULT 0,
    files         INTEGER NOT NULL DEFAULT 0,
    threat_count  INTEGER NOT NULL DEFAULT 0,
    -- ENGELLEYEN atlanan yol sayısı. Sıfırdan büyükse verdict 'clean'
    -- OLAMAZ: erişilemeyen yol varken tarama "temiz" raporlanmaz (ADR 0009 §4).
    skipped_count INTEGER NOT NULL DEFAULT 0,
    verdict       TEXT NOT NULL DEFAULT 'failed',   -- clean | incomplete | infected | failed
    error         TEXT NOT NULL DEFAULT '',
    actor         TEXT NOT NULL DEFAULT '',
    paths         TEXT NOT NULL DEFAULT '[]',       -- JSON: taranmak üzere verilen yollar
    threats       TEXT NOT NULL DEFAULT '[]',       -- JSON: [{path, name}]
    skipped       TEXT NOT NULL DEFAULT '[]'        -- JSON: [{path, reason, blocking}]
);

CREATE INDEX IF NOT EXISTS mod_antivirus_scan_started ON mod_antivirus_scan (started_at);

-- İmza yaşı denetiminin son durumu. Tek satır.
--
-- NEDEN SAKLANIYOR: denetim saatlik koşuyor. Durum saklanmasaydı eski imzalı
-- bir makinede `antivirus.signatures_stale` her saat yeniden yayınlanır ve
-- bildirim kanalını kullanılamaz hâle getirirdi. Olay yalnız duruma GİRİŞTE
-- ve sonrasında günde bir kez yayınlanır.
CREATE TABLE IF NOT EXISTS mod_antivirus_signature (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    checked_at  TEXT NOT NULL DEFAULT '',
    age_hours   REAL,                               -- NULL = okunamadı
    stale       INTEGER NOT NULL DEFAULT 0,
    notified_at TEXT NOT NULL DEFAULT ''
);
