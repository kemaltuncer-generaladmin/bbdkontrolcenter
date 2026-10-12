-- km: concurrently
-- Yaşam döngüsü indeksleri (005 göçünün sütun ve tabloları).
--
-- İşlemsiz göç: merkezde (PostgreSQL) her indeks CONCURRENTLY kipinde açılır,
-- tablo yazmaya kapanmaz. Yerel SQLite dosyayı düz göç gibi uygular.
--
-- Geri alma:
--   DROP INDEX CONCURRENTLY IF EXISTS mod_bbd_students_profile_canteen_sync;
--   DROP INDEX CONCURRENTLY IF EXISTS mod_bbd_students_profile_effective;
--   DROP INDEX CONCURRENTLY IF EXISTS mod_bbd_students_tombstone_deleted;
--   DROP INDEX CONCURRENTLY IF EXISTS mod_bbd_students_status_log_student;

CREATE INDEX IF NOT EXISTS mod_bbd_students_profile_canteen_sync
    ON mod_bbd_students_profile (canteen_sync, canteen_since);
CREATE INDEX IF NOT EXISTS mod_bbd_students_profile_effective
    ON mod_bbd_students_profile (status_effective_at);
CREATE INDEX IF NOT EXISTS mod_bbd_students_tombstone_deleted
    ON mod_bbd_students_tombstone (deleted_at);
CREATE INDEX IF NOT EXISTS mod_bbd_students_status_log_student
    ON mod_bbd_students_status_log (kantin_id, id);
