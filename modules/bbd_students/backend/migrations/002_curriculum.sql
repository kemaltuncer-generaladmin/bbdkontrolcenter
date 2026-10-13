-- Öğrenci Yönetimi — seviye, alan ve hedef sınav (müfredat ataması için).
--
-- KONTROL MERKEZİ ANA KAYNAKTIR. BBD Akademi öğrencinin müfredatını buradan
-- türetir (`bbd_students.directory` yeteneği → mobil geçit dizini). Bu yüzden
-- alanlar serbest metin değil, YAPILANDIRILMIŞTIR:
--
--   grade_level  '5'..'12' ya da 'graduate'      (NULL = girilmemiş)
--   track        'SAY' | 'EA' | 'SOZ' | 'DIL'    (NULL = yok / girilmemiş)
--   exam_target  'LGS' | 'YKS'                   (NULL = girilmemiş)
--
-- Doğrulama serviste yapılır (`backend/curriculum.py`); veritabanı CHECK
-- taşımaz — lehçe çevirmeni (km_core/store/dialect.py) yalnız ölçülmüş yapıları
-- tanır ve kural tek yerde kalsın.
--
-- SINIF ADINDAN TAHMİN YAZILMAZ. `class_name` ("8-A", "12-SAY") yalnız ekranda
-- ÖNERİ üretir; personel tek tıkla kabul eder. Göç mevcut satırlara dokunmaz:
-- yeni sütunlar NULL başlar.
--
-- EKLEYİCİ göç: var olan sütun ve satırların anlamı değişmez.

ALTER TABLE mod_bbd_students_profile ADD COLUMN grade_level TEXT;
ALTER TABLE mod_bbd_students_profile ADD COLUMN track TEXT;
ALTER TABLE mod_bbd_students_profile ADD COLUMN exam_target TEXT;
