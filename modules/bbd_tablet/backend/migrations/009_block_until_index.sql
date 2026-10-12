-- km: concurrently
-- Süren kısıtların kurum geneli okuması (`bbd_tablet.read.daily_usage`,
-- mobil geçit `GET /tablets/usage`) tablo taramasız çalışsın.
--
-- İşlemsiz göç: merkezde (PostgreSQL) indeks CONCURRENTLY kipinde açılır,
-- tablo yazmaya kapanmaz (docs/module-guide.md → "Büyük tabloya indeks").
-- Yerel SQLite dosyayı düz göç gibi uygular.
--
-- Geri alma:
--   DROP INDEX CONCURRENTLY IF EXISTS mod_bbd_tablet_student_blocks_until;

CREATE INDEX IF NOT EXISTS mod_bbd_tablet_student_blocks_until
    ON mod_bbd_tablet_student_blocks (blocked_until);
