-- Bildirimler modülünün YEREL tablosu.
--
-- Buraya yalnız BLD'de KARŞILIĞI OLMAYAN veri yazılır. Duyuru kaydı ve okunma
-- sayaçları BLD sunucusundadır (`veykemtu_notifications`,
-- `veykemtu_notification_reads`) ve KOPYALANMAZ: duyurunun `live` alanı
-- sunucuda hesaplanıyor ve zamanla kendiliğinden değişiyor (pencere açılıyor,
-- kapanıyor). Yerel bir kopya her zaman bir tur geride kalır ve "yayında"
-- görünen bir duyuru aslında dün sona ermiş olabilir. Ekranın yanlış bilgiyi
-- doğru gibi göstermesi, hiç göstermemesinden kötüdür.
--
-- TEK ŞEYİN KARŞILIĞI YOK: YAZMA DENEMESİ. BLD `veykemtu_control_audit`
-- tutuyor (sözleşme §8) ama o kayıt yalnız SUNUCUYA ULAŞAN isteği bilir. Ağ
-- koparsa, geçit patlarsa ya da istek yarıda kalırsa "kim hangi duyuruyu
-- yayınlamaya çalıştı" sorusunun cevabı yalnız burada kalır. Duyuru dışa dönük
-- içeriktir; "yayınlandı mı, yayınlanmadı mı" sorusunun cevapsız kalması kabul
-- edilemez.
--
-- EKRAN TERCİHİ TABLOSU YOK: bu ekranın kalıcı tutulacak bir tercihi yok
-- (sayfa boyutu ve yoklama aralığı modül ayarından geliyor). Yazanı olmayan
-- bir tablo açmak, ilk bakanı "burası neden boş" diye aratırdı.
--
-- K5: tablo VE index adlarının hepsi `mod_bld_notifications_` önekiyle başlar.

-- Yazma denemelerinin yerel izi. SATIR SİLİNMEZ.
CREATE TABLE IF NOT EXISTS mod_bld_notifications_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL DEFAULT 'notification',
    target_id   INTEGER NOT NULL DEFAULT 0,      -- 0 = henüz kimliği yok (yeni taslak)
    action      TEXT NOT NULL,                   -- notification.create | .update |
                                                 -- .publish | .archive
    reason      TEXT NOT NULL DEFAULT '',
    actor       TEXT NOT NULL DEFAULT '',        -- oturumdan gelir, gövdeden DEĞİL
    result      TEXT NOT NULL DEFAULT '',        -- denendi | ok | dry_run |
                                                 -- engellendi | hata
    -- Sözleşme §"Denetim eylemleri" gövdenin tamamını değil KÜNYESİNİ istiyor
    -- (`{"title": …, "audience": …, "body_length": 118}`). Yerel iz de aynı
    -- ölçüyü tutar: 2000 karakterlik duyuru metnini her denemede yazmak izi
    -- okunamaz kılardı.
    detail      TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_bld_notifications_audit_target
    ON mod_bld_notifications_audit (target_id, created_at);

CREATE INDEX IF NOT EXISTS mod_bld_notifications_audit_time
    ON mod_bld_notifications_audit (created_at);
