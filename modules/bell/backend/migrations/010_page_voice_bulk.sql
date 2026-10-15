-- Zil Sistemi — TOPLU hazırlanmış sayfa sesleri (BBD Akademi anons şablonları).
--
-- Akademi anons şablonlarını (öğrenci çağrısı, sınıf çağrısı) her öğrenci ve
-- yaklaşan her ders için ÖNDEN seslendirtir (`bell.announce.prepare`, geçit
-- `POST /announcements/prepare`): çağrı anında Vertex beklenmez. Bunlar yüzlerce
-- olabilir; bu yüzden:
--   · üretim sırasında olağan seslerin (zil sonrası, grup, plan) ARKASINDAN gelir;
--   · diskte 60 gün tutulur ("kullanılmayan sesleri sil" silmez) ama köprüye ve zil
--     bilgisayarına ÖNDEN gitmez — çalınacağı an yüklenir, sonra 24 saat köprüde kalır.
-- `bulk` 1 = toplu hazırlandı. Tekil hazırlık (0) toplu işaretini düşürür.
-- SQLite ALTER TABLE ADD COLUMN yeniden çalıştırılamaz; göç bir kez uygulanır.

ALTER TABLE mod_bell_page_voice ADD COLUMN bulk INTEGER NOT NULL DEFAULT 0;
