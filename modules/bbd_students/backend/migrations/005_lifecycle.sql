-- Öğrenci Yönetimi — yaşam döngüsü, revizyon ve kantin senkron durumu
-- (bbd-akademi docs/integrations/km-merkezi-senkron.md §5, §7; ADR 0029).
--
-- KM TEK MERKEZDİR. Öğrencinin durumu (etkin / pasif) yalnız burada değişir;
-- kantin ve BBD Akademi KM'nin kararını uygular.
--
--   status              'active' | 'inactive'  (silinen öğrencinin satırı yoktur)
--   status_reason       'left' | 'graduated' | 'suspended' | 'other' | NULL
--   status_since        ISO damga — son durum değişikliği
--   status_note         serbest not (yalnız KM'de kalır, olaya yazılmaz)
--   status_effective_at ileri tarihli pasifleştirme günü (YYYY-MM-DD); o gün
--                       00:00'da uzlaştırma görevi uygular. NULL = plan yok.
--   revision            öğrenci revizyonu — her yazmada küresel sayaçtan
--                       (`mod_bbd_students_student_rev`) damgalanır; olayın
--                       `revision`'ı budur.
--   canteen_sync        'ok' | 'pending' — KM kaydı kantine henüz yazılamadı
--   canteen_payload     kantine gidecek İDEMPOTENT gövde (JSON):
--                       {"upsert": {...}, "status": {...}}
--   canteen_attempts    başarısız deneme sayısı
--   canteen_since       ilk bekleyen yazmanın damgası (24 saat → panelde kırmızı)
--   canteen_error       son hata (makine kodu + kısa metin; kişisel veri yok)
--   source              'km' | 'canteen' — kasadan eklenen taslak profil
--
-- EKLEYİCİ göç: var olan satırlar etkin, senkron ve KM kaynaklı başlar.

ALTER TABLE mod_bbd_students_profile ADD COLUMN status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE mod_bbd_students_profile ADD COLUMN status_reason TEXT;
ALTER TABLE mod_bbd_students_profile ADD COLUMN status_since TEXT;
ALTER TABLE mod_bbd_students_profile ADD COLUMN status_note TEXT NOT NULL DEFAULT '';
ALTER TABLE mod_bbd_students_profile ADD COLUMN status_effective_at TEXT;
ALTER TABLE mod_bbd_students_profile ADD COLUMN revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE mod_bbd_students_profile ADD COLUMN canteen_sync TEXT NOT NULL DEFAULT 'ok';
ALTER TABLE mod_bbd_students_profile ADD COLUMN canteen_payload TEXT;
ALTER TABLE mod_bbd_students_profile ADD COLUMN canteen_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE mod_bbd_students_profile ADD COLUMN canteen_since TEXT;
ALTER TABLE mod_bbd_students_profile ADD COLUMN canteen_error TEXT;
ALTER TABLE mod_bbd_students_profile ADD COLUMN source TEXT NOT NULL DEFAULT 'km';

-- Tek satırlık kalıcı öğrenci revizyon sayacı (grup sayacı kalıbı).
CREATE TABLE IF NOT EXISTS mod_bbd_students_student_rev (
    id       INTEGER PRIMARY KEY CHECK (id = 1),
    revision INTEGER NOT NULL DEFAULT 0
);

-- Mezar taşı: kalıcı silinen öğrencinin YALNIZ kimliği ve tarihi (90 gün).
-- Dizinin `since` yanıtı `removed` listesini buradan kurar; kantine silme
-- iletilemediyse uzlaştırma buradan yeniden dener.
CREATE TABLE IF NOT EXISTS mod_bbd_students_tombstone (
    kantin_id        TEXT PRIMARY KEY,
    deleted_at       TEXT NOT NULL,
    canteen_sync     TEXT NOT NULL DEFAULT 'pending',
    canteen_attempts INTEGER NOT NULL DEFAULT 0,
    canteen_error    TEXT
);

-- Durum geçmişi (öğrenci kartı): kim, ne zaman, hangi gerekçeyle.
CREATE TABLE IF NOT EXISTS mod_bbd_students_status_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kantin_id    TEXT NOT NULL,
    at           TEXT NOT NULL,
    actor        TEXT NOT NULL DEFAULT '',
    action       TEXT NOT NULL,
    reason       TEXT,
    effective_at TEXT,
    note         TEXT NOT NULL DEFAULT ''
);
