-- Personelin ANONS ADI (sözleşme km-ogrenci-gruplari-ve-zil.md §1, §3.2, §7.D.1).
--
-- Zil sisteminin oturum başı grup çağrısında öğretmen bu adla okunur:
-- "TYT grubu, Hüseyin hoca ile dersiniz başlıyor." Ad soyad TTS için uygun
-- değildir ("Hüseyin Yılmaz" değil "Hüseyin hoca"), bu yüzden ayrı alandır.
--
-- KM TEK KAYNAKTIR: personel ve anons adı bu modülde yazılır, Akademi'ye
-- `PUT /v1/admin/staff/sync` ile itilir (Akademi `staff.announce_name`).
-- Boş dize = anons adı yok → Akademi çağrıda `teacherName: null` gönderir,
-- KM genel (öğretmensiz) şablonu okur.
--
-- EKLEYİCİ VE CANLIDA GÜVENLİ: yalnız varsayılanlı bir sütun eklenir; mevcut
-- satırlar boş anons adıyla aynı biçimde çalışmaya devam eder. İndeks yok
-- (alanla arama yapılmaz), bu yüzden `-- km: concurrently` gerekmez.

ALTER TABLE mod_bbd_akademi_staff ADD COLUMN announce_name TEXT NOT NULL DEFAULT '';
