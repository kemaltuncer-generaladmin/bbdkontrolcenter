-- Öğrenci Yönetimi — DIŞ KAYNAKLI ÖĞRENCİ AÇILIŞI (iş 81-km-kulup, 2026-10-28).
--
-- Kullanıcı: "deneme kulübü diye sınıf açacaz biz mesela … sanki kurum öğrencisiymiş gibi
-- olacak". BBD Akademi'nin Deneme Kulübü formu dolunca Akademi, mobil geçidin
-- `POST /students` ucuyla KM'de öğrenci açtırır (bbd-akademi 81-api-taslak §C). Öğrenci
-- KM'de NORMAL öğrencidir (ayrı tür yok): "Deneme Kulübü" sınıfının üyesidir, kantine
-- mevcut kuralla yazılır.
--
-- Bu tablo YALNIZ idempotensiyi taşır: aynı dış kayıt (`external_ref` = Akademi davet
-- kimliği) ikinci kez öğrenci açmaz, ilk yanıt aynen döner.
--
--   external_ref  Akademi davet kimliği (ya da başka dış kaynağın tekil anahtarı)
--   source        'club' (şimdilik tek kaynak)
--   kantin_id     açılan ya da eşleşen öğrenci (STU-…); '' = işlem sürüyor (sahiplenme)
--   created       1 = yeni öğrenci açıldı, 0 = mevcut öğrenci sınıfa eklendi
--   class_name    eklendiği sınıf
--   state         'pending' (sahiplenildi) | 'done'
--   created_at / updated_at  ISO damga
--
-- Kişisel veri YOK (ad, telefon, TC profil tablosundadır).

CREATE TABLE IF NOT EXISTS mod_bbd_students_admission (
    external_ref TEXT PRIMARY KEY,
    source       TEXT NOT NULL DEFAULT 'club',
    kantin_id    TEXT NOT NULL DEFAULT '',
    created      INTEGER NOT NULL DEFAULT 0,
    class_name   TEXT NOT NULL DEFAULT '',
    state        TEXT NOT NULL DEFAULT 'pending',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_bbd_students_admission_student
    ON mod_bbd_students_admission (kantin_id);
