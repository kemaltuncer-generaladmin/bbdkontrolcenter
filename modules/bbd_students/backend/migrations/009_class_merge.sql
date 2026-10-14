-- Öğrenci Yönetimi — SINIF = GRUP (kullanıcı kararı 2026-10-14, iş 23).
--
-- "sınıf=grup bak öğrenci yönetiminde burası çok karışıyor." → TEK KAVRAM:
-- "Sınıf". Öğrenci grup tablosu (`mod_bbd_students_group`, göç 003) artık
-- sınıf tablosudur; bir öğrenci BİRDEN ÇOK sınıfa EŞİT üye olabilir ("ana
-- sınıf" yok), üyelik tarihli kalır (003'ün kuralı aynen).
--
-- VERİ DÖNÜŞÜMÜ BU DOSYADA DEĞİL, `backend/classes_merge.py`'de: her farklı
-- öğrenci `class_name`'i bir sınıf kaydına, öğrencinin o sınıfa tarihli
-- üyeliğine dönüşür. Kimlik (uuid), Türkçe katlama, revizyon ve outbox olayı
-- SQL'de yazılamaz (lehçe çevirmeni) — iş modül açılışında bir kez koşar,
-- tekrar çalıştırılabilir (yapılmışı yeniden yapmaz), geri alınabilir.
--
-- Yeni sütunlar:
--   group.origin            'group' (eski grup ya da ekrandan açılan sınıf) |
--                           'class' (öğrenci `class_name`'inden dönüşen sınıf)
--   group.legacy_class_name bu sınıfın yerini aldığı eski sınıf adı (dizinde
--                           `className` olarak eski Akademi'ye gitmeye devam
--                           eder); '' = yok
--   group.grade_level/track/exam_target
--                           sınıfın seviyesi (göç 008'deki sınıf tanımından
--                           taşınır) — ŞABLONDUR: öğrenci eklenirken /
--                           "üyelere uygula" ile öğrenciye yazılır. Öğrencinin
--                           seviye/alan/hedef sınavı ÖĞRENCİDE kalır.
--   group_member.origin     'class' = dönüşümün eklediği üyelik (eski Akademi
--                           görünümünde gizlenir; çift kayıt olmasın)
--   profile.class_ids       öğrencinin BUGÜN geçerli sınıf kimlikleri (virgülle,
--                           sıralı) — dizin `classes` alanı ve "değişti mi"
--                           denetimi için türetilmiş kopya; tek kaynak üyelik
--                           tablosudur
--
-- `profile.class_name` SİLİNMEZ ve ANLAMI DARALIR: öğrencinin bugün geçerli,
-- eski sınıftan dönüşmüş üyeliğinin adı (eski Akademi uyumu). Göç 008'in
-- `mod_bbd_students_class` tablosu yerinde kalır (yalnız okunur, tarihçe).
--
-- EKLEYİCİ göç: var olan sütun ve satırların anlamı değişmez; yeni sütunlar
-- DEFAULT'ludur. İndeksler ayrı, işlemsiz göçte (010).
--
-- Geri alma (veri): `classes_merge.py` → `rollback()` (ya da
-- `POST /api/bbd_students/classes/merge/rollback`, PIN): dönüşümün açtığı
-- sınıflar ve üyelikleri kaldırılır, öğrenci seviyeleri yedekten döner.
-- Sütunlar kalır (ekleyici; eski sürüm onları okumaz).

ALTER TABLE mod_bbd_students_group ADD COLUMN origin TEXT NOT NULL DEFAULT 'group';
ALTER TABLE mod_bbd_students_group ADD COLUMN legacy_class_name TEXT NOT NULL DEFAULT '';
ALTER TABLE mod_bbd_students_group ADD COLUMN grade_level TEXT;
ALTER TABLE mod_bbd_students_group ADD COLUMN track TEXT;
ALTER TABLE mod_bbd_students_group ADD COLUMN exam_target TEXT;
ALTER TABLE mod_bbd_students_group_member ADD COLUMN origin TEXT NOT NULL DEFAULT 'group';
ALTER TABLE mod_bbd_students_profile ADD COLUMN class_ids TEXT NOT NULL DEFAULT '';

-- Dönüşümden ÖNCEKİ öğrenci seviyesi (geri alma için). Satır başına bir kez
-- yazılır; dönüşüm tekrar koşsa da ilk değer korunur.
CREATE TABLE IF NOT EXISTS mod_bbd_students_level_backup (
    kantin_id      TEXT PRIMARY KEY,
    class_name     TEXT NOT NULL DEFAULT '',
    grade_level    TEXT,
    track          TEXT,
    exam_target    TEXT,
    level_override INTEGER NOT NULL DEFAULT 0,
    saved_at       TEXT NOT NULL
);

-- Dönüşümün durumu (tek satır): sürüm, durum, sayılar.
CREATE TABLE IF NOT EXISTS mod_bbd_students_class_merge (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    version     INTEGER NOT NULL DEFAULT 0,
    state       TEXT NOT NULL DEFAULT '',     -- '' | 'done' | 'rolled_back'
    started_at  TEXT NOT NULL DEFAULT '',
    finished_at TEXT NOT NULL DEFAULT '',
    actor       TEXT NOT NULL DEFAULT '',
    summary     TEXT NOT NULL DEFAULT ''      -- JSON özet (sayılar)
);
