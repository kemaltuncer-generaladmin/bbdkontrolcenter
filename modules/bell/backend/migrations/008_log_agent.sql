-- Zil Sistemi — çalma günlüğüne ZİL BİLGİSAYARININ cevabı.
--
-- Günlük şimdiye dek yalnız "komut köprü kuyruğuna yazıldı" diyordu
-- ("ajan · köprü kuyruğu #1275", yeşil tik). Zil bilgisayarının o komutu
-- gerçekten çalıp çalmadığı hiçbir ekranda görünmüyordu: canlıda her zilin
-- ikinci komutu ajan tarafından "zamanı 26 sn geçmiş, atlandı" diye
-- reddediliyor, ekran ise ikisini de yeşil gösteriyordu.
--
-- `command_id` köprünün komut numarasıdır (0 = köprüye gitmedi). Ajan her
-- komutu `ack` eder; köprü durumunda son ack görünür (`lastAck`). Servis
-- durumu her okuduğunda o numaralı satırlara cevabı yazar:
--   agent_ok      -1 bilinmiyor · 1 çaldı · 0 çalmadı/atlandı
--   agent_detail  ajanın kendi cümlesi ("2 ses çaldı", "zamanı … geçmiş")
-- SQLite ALTER TABLE ADD COLUMN yeniden çalıştırılamaz; göç bir kez uygulanır.
-- İndeks ayrı, işlemsiz göçte (009).

ALTER TABLE mod_bell_log ADD COLUMN command_id INTEGER NOT NULL DEFAULT 0;
ALTER TABLE mod_bell_log ADD COLUMN agent_ok INTEGER NOT NULL DEFAULT -1;
ALTER TABLE mod_bell_log ADD COLUMN agent_detail TEXT NOT NULL DEFAULT '';
