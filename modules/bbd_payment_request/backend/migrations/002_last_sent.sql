-- Öğrenci başına SON BAŞARILI talep gönderimi (özet tablo).
--
-- Panel açılışı ve toplu gönderim provası "kime ne zaman gönderildi"yi
-- soruyor. Bu, `entry` tablosunda `GROUP BY kantin_id` ile BÜTÜN geçmişin
-- taranması demekti (200 bin satırda SQLite ~840 ms, PostgreSQL ~50 ms) ve
-- geçmiş büyüdükçe uzuyordu. Özet tablo öğrenci başına tek satırdır; her
-- `queued` kayıtla birlikte güncellenir (PERFORMANCE.md §3.6 — ön hesaplanmış
-- özet). `entry` tablosu değişmez, doğruluk kaynağı odur.
--
-- Canlı veride güvenli: yeni tablo + tek seferlik toplama okuması; `entry`
-- tablosuna yazma kilitlenmez (yalnız okunur). EKLEYİCİDİR: eski sürüm bu
-- tabloyu bilmeden çalışmayı sürdürür.
-- Geri alma: DROP TABLE IF EXISTS mod_bbd_payment_request_last_sent;

CREATE TABLE IF NOT EXISTS mod_bbd_payment_request_last_sent (
    kantin_id  TEXT PRIMARY KEY,
    last_at    TEXT NOT NULL            -- en son `queued` kaydın created_at'i
);

INSERT OR IGNORE INTO mod_bbd_payment_request_last_sent (kantin_id, last_at)
SELECT kantin_id, MAX(created_at) FROM mod_bbd_payment_request_entry
WHERE status = 'queued'
GROUP BY kantin_id;
