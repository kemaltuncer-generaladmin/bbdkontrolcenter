-- km: concurrently
-- Sınıf = grup (009) — indeksler. İşlemsiz göç: merkezde CONCURRENTLY.
--
-- Geri alma:
--   DROP INDEX CONCURRENTLY IF EXISTS mod_bbd_students_group_legacy;
--   DROP INDEX CONCURRENTLY IF EXISTS mod_bbd_students_group_member_valid_to;

CREATE INDEX IF NOT EXISTS mod_bbd_students_group_legacy
    ON mod_bbd_students_group (legacy_class_name);
CREATE INDEX IF NOT EXISTS mod_bbd_students_group_member_valid_to
    ON mod_bbd_students_group_member (valid_to, valid_from);
