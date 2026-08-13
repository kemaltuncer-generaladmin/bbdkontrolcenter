-- Öğrenci Yönetimi — modülün KENDİ tablosu (K5).
--
-- Kantin'in tuttuğu alanlar burada TEKRARLANMAZ: ad (display_name), veli
-- telefonu, harcama limiti, engel ve bakiye kantindedir ve oradan okunur.
-- Burada yalnızca kantinde KARŞILIĞI OLMAYAN alanlar durur; bağlantı
-- kantin_id (kantin `students.opaque_id`) üzerinden kurulur.
--
-- Tablo adı modül önekiyle açılır; çekirdek başka önek kabul etmez.

CREATE TABLE IF NOT EXISTS mod_bbd_students_profile (
    kantin_id      TEXT PRIMARY KEY,     -- kantin opaque_id
    first_name     TEXT NOT NULL DEFAULT '',
    last_name      TEXT NOT NULL DEFAULT '',
    class_name     TEXT NOT NULL DEFAULT '',
    school_no      TEXT NOT NULL DEFAULT '',
    student_phone  TEXT NOT NULL DEFAULT '',
    parent_name    TEXT NOT NULL DEFAULT '',
    parent_name2   TEXT NOT NULL DEFAULT '',
    parent_phone2  TEXT NOT NULL DEFAULT '',
    note           TEXT NOT NULL DEFAULT '',
    updated_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_bbd_students_profile_class
    ON mod_bbd_students_profile (class_name);
