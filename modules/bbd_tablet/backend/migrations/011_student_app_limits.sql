-- Öğrenciye özel uygulama süresi (Akademi "Tablet sınırları", iş tablet-sinir).
--
-- Süre sınırı profile bağlıdır; bu tablo bir öğrencinin bir uygulamadaki günlük
-- süresini GENEL (profil) süresinin ÜSTÜNE çıkarır. Tablet uygulaması ve
-- sözleşmesi değişmez: fark, oturumun ek süre listesine her gün kendiliğinden
-- eklenen bir satır olarak iner (`id` "ovr:<paket>"). Genelden düşük öğrenci
-- süresi tabletin protokolünde yoktur (ek süre negatif olamaz), sunucu reddeder.
-- Satır yoksa öğrenci genel ayarı kullanır ("genele döndür" satırı siler).
--
-- Yeni boş tablo; geri alma: DROP TABLE IF EXISTS mod_bbd_tablet_student_app_limits;

CREATE TABLE IF NOT EXISTS mod_bbd_tablet_student_app_limits (
    student_id TEXT NOT NULL,
    package_name TEXT NOT NULL,
    daily_limit_seconds INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (student_id, package_name)
);
