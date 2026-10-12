-- km: concurrently
-- Öğrenci kartının son tahsilatları: `WHERE kantin_id = ? ORDER BY created_at
-- DESC LIMIT 5`. İndeks yokken plan tarih indeksini baştan sona yürüyordu
-- (öğrencinin az tahsilatı varsa bütün tablo). Eşitlik sütunu önce, sıra sonra.
-- Merkezde CONCURRENTLY ile açılır: tablo yazmaya kapanmaz.
-- Geri alma: DROP INDEX CONCURRENTLY IF EXISTS mod_bbd_payment_request_collection_student;
CREATE INDEX IF NOT EXISTS mod_bbd_payment_request_collection_student
    ON mod_bbd_payment_request_collection (kantin_id, created_at);
