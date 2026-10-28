-- Öğrenci Yönetimi — SÖZLEŞME KİMLİK BİLGİLERİ (iş 80-km-kimlik, 2026-10-28).
--
-- Kullanıcı: "eksikleri de ekleriz öğrenciler panelinde düzenleriz", "akademi ile km
-- birebir senkron olsun". Sözleşme / senet / MEB formu için gereken kimlik ve adres
-- bilgileri öğrencinin ANA KAYDINDADIR (KM); Akademi aynalar (mobil geçit `identity`
-- bloğu, yetenek `mobile_gateway.identity.read`). Kural ve biçimler
-- `backend/identity.py`'dedir.
--
-- ÖĞRENCİ (profil satırına eklenir — mevcut yazma yolu: revizyon + outbox tek işlem):
--   national_id / foreign_id     TC kimlik no / pasaport-yabancı kimlik no. ŞİFRELİ
--                                ("v1:" + AES-256-GCM, anahtar sır kasasında
--                                `bbd_students.identity_key`); düz değer YAZILMAZ.
--   national_id_tail / foreign_id_tail  son 4 hane — maskeli gösterim (`*******0146`)
--                                şifre çözmeden yapılır.
--   birth_date, registered_on    YYYY-MM-DD ('' = girilmemiş)
--   gender                       'female' | 'male' | ''
--   school_name, home_phone (10 hane), mother_name, father_name
--   address_line / address_district / address_city
--   guardian_identity            JSON: velinin CEP TELEFONU (kişinin çapası; yuva
--                                değişse de kimlik kişiyle gider) → {nationalId (şifreli),
--                                nationalIdTail, foreignId (şifreli), foreignIdTail,
--                                occupation, workPhone, email, homeAddress{line,district,
--                                city}, workAddress{…}}. Velinin yasal ad soyadı MEVCUT
--                                veli ad alanıdır (parent_name / parent_name2 / ek veli
--                                adı) — ayrı alan açılmadı.
--   identity_updated_at          kimlik ya da veli adı/telefonu/yakınlığı son değişti
--                                (geçit `identity.updatedAt`).
--
-- DENETİM: `mod_bbd_students_identity_log` — kim, ne zaman, HANGİ ALANLAR (değer YOK).
--
-- EKLEYİCİ göç: var olan satırlar boş başlar; eski sürüm yeni sütunları okumaz.

ALTER TABLE mod_bbd_students_profile ADD COLUMN national_id TEXT NOT NULL DEFAULT '';
ALTER TABLE mod_bbd_students_profile ADD COLUMN national_id_tail TEXT NOT NULL DEFAULT '';
ALTER TABLE mod_bbd_students_profile ADD COLUMN foreign_id TEXT NOT NULL DEFAULT '';
ALTER TABLE mod_bbd_students_profile ADD COLUMN foreign_id_tail TEXT NOT NULL DEFAULT '';
ALTER TABLE mod_bbd_students_profile ADD COLUMN birth_date TEXT NOT NULL DEFAULT '';
ALTER TABLE mod_bbd_students_profile ADD COLUMN gender TEXT NOT NULL DEFAULT '';
ALTER TABLE mod_bbd_students_profile ADD COLUMN registered_on TEXT NOT NULL DEFAULT '';
ALTER TABLE mod_bbd_students_profile ADD COLUMN school_name TEXT NOT NULL DEFAULT '';
ALTER TABLE mod_bbd_students_profile ADD COLUMN home_phone TEXT NOT NULL DEFAULT '';
ALTER TABLE mod_bbd_students_profile ADD COLUMN address_line TEXT NOT NULL DEFAULT '';
ALTER TABLE mod_bbd_students_profile ADD COLUMN address_district TEXT NOT NULL DEFAULT '';
ALTER TABLE mod_bbd_students_profile ADD COLUMN address_city TEXT NOT NULL DEFAULT '';
ALTER TABLE mod_bbd_students_profile ADD COLUMN mother_name TEXT NOT NULL DEFAULT '';
ALTER TABLE mod_bbd_students_profile ADD COLUMN father_name TEXT NOT NULL DEFAULT '';
ALTER TABLE mod_bbd_students_profile ADD COLUMN guardian_identity TEXT NOT NULL DEFAULT '';
ALTER TABLE mod_bbd_students_profile ADD COLUMN identity_updated_at TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS mod_bbd_students_identity_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kantin_id  TEXT NOT NULL,
    at         TEXT NOT NULL,
    actor      TEXT NOT NULL DEFAULT '',
    action     TEXT NOT NULL,                -- 'updated' | 'created'
    fields     TEXT NOT NULL DEFAULT ''      -- virgüllü alan adları (DEĞER YOK)
);

CREATE INDEX IF NOT EXISTS mod_bbd_students_identity_log_student
    ON mod_bbd_students_identity_log (kantin_id, id);
