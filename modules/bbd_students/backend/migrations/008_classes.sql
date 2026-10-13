-- Öğrenci Yönetimi — SINIF SEVİYEYİ TAŞIR (kullanıcı kararı 2026-10-13).
--
-- "Sınıf ve seviye farkı garip, bunları teke indiremez miyiz?" → seviye
-- (5..12 / mezun), alan (SAY/EA/SOZ/DIL) ve hedef sınav (LGS/YKS) ANA SINIF
-- tanımında girilir; öğrenci yalnız sınıfa atanır, değerleri sınıftan türetilir.
-- Öğrencinin kendi `grade_level/track/exam_target` sütunları (göç 002) yerinde
-- kalır: (a) sınıfına seviye girilmemiş öğrencide eski değer geçerlidir (geriye
-- uyum), (b) `level_override = 1` iken öğrencinin kendi ALAN ve HEDEF SINAVI
-- sınıfınkinin yerine geçer (seviye her zaman sınıftan).
--
-- `name` = öğrencinin `class_name`'i ile birebir (normalize_class). Sınıf adı
-- değişmez; yeni ad yeni sınıftır. Satır yoksa sınıf "seviye girilmemiş"tir.
--
-- EKLEYİCİ göç: yeni tablo + DEFAULT'lu sütun; mevcut satırların anlamı değişmez.

CREATE TABLE IF NOT EXISTS mod_bbd_students_class (
    name        TEXT PRIMARY KEY,
    grade_level TEXT,                       -- '5'..'12' | 'graduate' | NULL
    track       TEXT,                       -- 'SAY' | 'EA' | 'SOZ' | 'DIL' | NULL
    exam_target TEXT,                       -- 'LGS' | 'YKS' | NULL
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    updated_by  TEXT NOT NULL DEFAULT ''
);

ALTER TABLE mod_bbd_students_profile ADD COLUMN level_override INTEGER NOT NULL DEFAULT 0;
