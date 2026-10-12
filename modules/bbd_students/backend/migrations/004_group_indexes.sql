-- km: concurrently
-- Öğrenci grupları — indeksler (003 göçünün tabloları).
--
-- İşlemsiz göç: merkezde (PostgreSQL) her indeks CONCURRENTLY kipinde
-- açılır, tablo yazmaya kapanmaz (docs/module-guide.md → "Büyük tabloya
-- indeks"). Yerel SQLite dosyayı düz göç gibi uygular.
--
-- Geri alma:
--   DROP INDEX CONCURRENTLY IF EXISTS mod_bbd_students_group_name_fold;
--   DROP INDEX CONCURRENTLY IF EXISTS mod_bbd_students_group_revision;
--   DROP INDEX CONCURRENTLY IF EXISTS mod_bbd_students_group_member_group;
--   DROP INDEX CONCURRENTLY IF EXISTS mod_bbd_students_group_member_student;
--   DROP INDEX CONCURRENTLY IF EXISTS mod_bbd_students_group_member_revision;
--   DROP INDEX CONCURRENTLY IF EXISTS mod_bbd_students_group_log_group;

CREATE INDEX IF NOT EXISTS mod_bbd_students_group_name_fold
    ON mod_bbd_students_group (name_fold);
CREATE INDEX IF NOT EXISTS mod_bbd_students_group_revision
    ON mod_bbd_students_group (revision);
CREATE INDEX IF NOT EXISTS mod_bbd_students_group_member_group
    ON mod_bbd_students_group_member (group_id, valid_from);
CREATE INDEX IF NOT EXISTS mod_bbd_students_group_member_student
    ON mod_bbd_students_group_member (kantin_id, valid_from);
CREATE INDEX IF NOT EXISTS mod_bbd_students_group_member_revision
    ON mod_bbd_students_group_member (revision);
CREATE INDEX IF NOT EXISTS mod_bbd_students_group_log_group
    ON mod_bbd_students_group_log (group_id, id);
