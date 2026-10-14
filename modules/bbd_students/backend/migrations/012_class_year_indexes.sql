-- km: concurrently
-- Sınıf dönemi + mezar taşı (011) — indeksler. İşlemsiz göç: merkezde CONCURRENTLY.
--
-- Geri alma:
--   DROP INDEX CONCURRENTLY IF EXISTS mod_bbd_students_group_academic_year;
--   DROP INDEX CONCURRENTLY IF EXISTS mod_bbd_students_group_tombstone_revision;

CREATE INDEX IF NOT EXISTS mod_bbd_students_group_academic_year
    ON mod_bbd_students_group (academic_year);
CREATE INDEX IF NOT EXISTS mod_bbd_students_group_tombstone_revision
    ON mod_bbd_students_group_tombstone (revision);
