-- Öğrenci Yönetimi — SINIFIN DÖNEMİ + kalıcı silinen sınıfın mezar taşı (iş 30, 2026-10-14).
--
-- Kullanıcı: "tüm mevcut sınıflara da 2026-2027 dönemini ata ... ayrıca arşive ek
-- sil fonksiyonu olsun." Her sınıfın bir DÖNEMİ (akademik yıl, "YYYY-YYYY"; 1 Eylül'de
-- döner) vardır. Yeni sınıf açılırken seçilir (varsayılan güncel dönem); liste
-- döneme göre süzülür. Sözleşme: bbd-akademi km-ogrenci-gruplari-ve-zil.md §2.6
-- (`GET /groups?include=classes` kaydında `academicYear`).
--
-- VERİ: mevcut BÜTÜN sınıf kayıtları (eski gruplar, `class_name`'den dönüşenler,
-- özel ders, arşivliler dahil) '2026-2027' alır. Bu SQL'de DEĞİL,
-- `backend/academic_year.py` → `YearBackfill`'de yapılır: grup revizyonu +1,
-- satırlara damga, `group.changed` (outbox) ve süreç içi olay aynı işlemde
-- olmalı ki Akademi'nin artımlı senkronu (`since`) dönemi hemen görsün; lehçe
-- çevirmeni bunları SQL'de yazdırmaz. İş modül açılışında koşar, tekrar
-- çalıştırılabilir (yalnız dönemi BOŞ satırlara yazar), silmez.
--
-- MEZAR TAŞI: arşivdeki sınıf KALICI silinince satırı ve üyelikleri gider; kimliği
-- burada kalır ki artımlı akış (`since`, `include=classes`) `{id, deleted: true,
-- deletedAt}` versin. Denetim izi (`group_log`) SİLİNMEZ.
--
-- EKLEYİCİ göç: yeni sütun NULL'lu (eski sürüm okumaz, yazarken boş bırakır —
-- YearBackfill doldurur), yeni tablo. İndeksler ayrı, işlemsiz göçte (012).
--
-- Geri alma: sütun ve tablo kalır (ekleyici); eski sürüm onları okumaz.

ALTER TABLE mod_bbd_students_group ADD COLUMN academic_year TEXT;

CREATE TABLE IF NOT EXISTS mod_bbd_students_group_tombstone (
    id           TEXT PRIMARY KEY,               -- silinen sınıfın kimliği (uuid)
    name         TEXT NOT NULL DEFAULT '',
    deleted_at   TEXT NOT NULL,                  -- ISO damga (UTC)
    deleted_by   TEXT NOT NULL DEFAULT '',
    reason       TEXT NOT NULL DEFAULT '',
    member_rows  INTEGER NOT NULL DEFAULT 0,     -- silinen üyelik satırı sayısı
    revision     INTEGER NOT NULL DEFAULT 0      -- silmenin grup revizyonu
);
