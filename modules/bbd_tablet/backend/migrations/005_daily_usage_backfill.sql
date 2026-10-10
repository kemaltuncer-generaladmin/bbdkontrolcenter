-- Günlük kullanım özeti, şimdiye kadar kabul edilmiş paketlerden yeniden
-- kurulur. Bundan sonra her kabul edilen paket özete eklenir; okumalar
-- SUM/JOIN yerine bu tablodan yapılır.
DELETE FROM mod_bbd_tablet_daily_usage;
INSERT INTO mod_bbd_tablet_daily_usage (student_id, package_name, local_date, used_seconds)
SELECT b.student_id, i.package_name, i.local_date, CAST(SUM(i.used_seconds) AS INTEGER)
FROM mod_bbd_tablet_usage_batch_items i
JOIN mod_bbd_tablet_usage_batches b ON b.id = i.batch_id
GROUP BY b.student_id, i.package_name, i.local_date;
