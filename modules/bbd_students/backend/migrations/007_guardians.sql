-- Öğrenci Yönetimi — veli yönetimi: yakınlık ve ek veliler.
--
-- KULLANICI İSTEĞİ (13.10.2026): birden çok veli, birincil veli, yakınlık,
-- telefon doğrulama. Mevcut iki veli yuvası DEĞİŞMEZ:
--
--   1. veli (BİRİNCİL)  telefonu kantinde (`parentPhone`, kantin otoritesi),
--                       adı `parent_name`, yakınlığı `parent_relation`
--   2. veli             `parent_name2`, `parent_phone2`, `parent_relation2`
--   3.–4. veli          `guardians_extra` — JSON dizi
--                       [{"name": "...", "phone": "5XXXXXXXXX", "relation": "..."}]
--
-- "Birincil veli" = 1. yuva: kantine giden ve veli kantin kontrollerini açan
-- telefon odur; birincili değiştirmek telefonları yuvalar arasında taşır.
-- Yakınlık: '' | 'mother' | 'father' | 'guardian' | 'grandparent' | 'sibling' | 'other'
-- (kural `backend/guardians.py`; veritabanı CHECK taşımaz — 002 göçündeki gerekçe).
--
-- 3.–4. veli Kontrol Merkezi'nde durur ve dizinde (`guardians[]`) yuva 3–4 ile
-- görünür; mobil geçit sözleşmesi bugün yalnız yuva 1–2'yi taşır (geçit diğerlerini
-- düşürür) — Akademi tarafı ayrı işte genişletilir.
--
-- EKLEYİCİ göç: var olan satırlar boş yakınlık ve ek velisiz başlar.

ALTER TABLE mod_bbd_students_profile ADD COLUMN parent_relation TEXT NOT NULL DEFAULT '';
ALTER TABLE mod_bbd_students_profile ADD COLUMN parent_relation2 TEXT NOT NULL DEFAULT '';
ALTER TABLE mod_bbd_students_profile ADD COLUMN guardians_extra TEXT NOT NULL DEFAULT '';
