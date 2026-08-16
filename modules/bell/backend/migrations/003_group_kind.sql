-- Zil Sistemi — grup türü.
--
-- İKİ TÜR ÇAĞRI VARDIR ve cümleleri aynı olamaz:
--
--   grup  → "TYT grubu, Hüseyin hoca ile dersiniz başlıyor."      (siz)
--   ozel  → "İlayda, Hüseyin hoca ile özel dersin başlıyor."      (sen)
--
-- Özel ders tek bir öğrenciyle yapılıyor; ona "dersiniz" demek yanlış, "grubu"
-- demek daha da yanlış. Ayrımı grubun ADINDAN tahmin etmeye çalışmak (içinde
-- "grup" geçiyor mu, büyük harfle mi başlıyor) kırılgan olurdu — "LGS" ve
-- "Zehra" arasındaki farkı hiçbir kural güvenilir biçimde bilemez.
--
-- Bu yüzden tür AÇIKÇA saklanır. Varsayılan 'grup': mevcut satırların anlamı
-- değişmez, yeni sütun eskiyi bozmaz.

ALTER TABLE mod_bell_group ADD COLUMN kind TEXT NOT NULL DEFAULT 'grup';
