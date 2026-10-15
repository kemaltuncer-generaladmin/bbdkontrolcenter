-- Öğrenci Yönetimi — DÖNEM GEÇİŞİ kayıtları (iş 37 / İ4, 2026-10-15).
--
-- Yıl sonu sihirbazı (KM → Öğrenciler → Sınıflar → "Dönem geçişi"): bir dönemin canlı
-- sınıflarını yeni döneme taşır/kopyalar (ad önerisi 7-A → 8-A, seviye +1), mezun
-- sınıfların öğrencilerini pasifleştirir, eski sınıfları arşivler. Kurallar
-- `backend/academic_year.py` → `YearRollover`'dadır.
--
-- Her UYGULAMA tek satırdır. `snapshot` geri almanın ihtiyacı olan her şeyi taşır
-- (JSON): arşivlenen sınıflar + kapanan/kaldırılan üyelik satırlarının önceki hali,
-- açılan sınıflar, var olan hedef sınıfa eklenen üyelik satırları, seviyesi değişen
-- öğrencilerin önceki/sonraki değeri, pasifleştirilen öğrenciler. `summary` ekranın
-- gösterdiği özet ve Akademi ders programı kopyasının eşlemesidir (eski sınıf →
-- yeni sınıf). Geri alma satırı SİLMEZ: `state = 'undone'`.
--
-- EKLEYİCİ göç: yalnız yeni tablo. Eski sürüm tabloyu okumaz.

CREATE TABLE IF NOT EXISTS mod_bbd_students_rollover (
    id              TEXT PRIMARY KEY,               -- uuid
    created_at      TEXT NOT NULL,                  -- ISO damga (UTC)
    created_by      TEXT NOT NULL DEFAULT '',
    from_year       TEXT NOT NULL,                  -- "2026-2027"
    to_year         TEXT NOT NULL,                  -- "2027-2028"
    effective_from  TEXT NOT NULL,                  -- YYYY-MM-DD: yeni üyeliklerin ilk günü
    state           TEXT NOT NULL DEFAULT 'applied',  -- applied | undone
    revision        INTEGER NOT NULL DEFAULT 0,     -- uygulamanın grup revizyonu
    undone_at       TEXT NOT NULL DEFAULT '',
    undone_by       TEXT NOT NULL DEFAULT '',
    undo_revision   INTEGER NOT NULL DEFAULT 0,
    summary         TEXT NOT NULL DEFAULT '',       -- JSON özet + eşleme
    snapshot        TEXT NOT NULL DEFAULT ''        -- JSON geri alma verisi
);
