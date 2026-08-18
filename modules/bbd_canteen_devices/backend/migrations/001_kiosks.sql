-- Kantin Cihazları modülünün YEREL tabloları.
--
-- Buraya yalnız KANTİNDE KARŞILIĞI OLMAYAN veri yazılır. Kiosk kaydı, eşleme
-- kodu, token ve iptal damgası kantindeki `kiosks` tablosundadır ve
-- KOPYALANMAZ: kopya her zaman bir tur geride kalır ve "eşlenmiş" görünen bir
-- kiosk aslında yarım saat önce iptal edilmiş olabilir.
--
-- İki şeyin karşılığı yok:
--  1. DENEME KAYDI. Kantin `kiosks` satırında yalnız SONUCU tutuyor. Ağ
--     koparsa, geçit patlarsa ya da istek yarıda kalırsa "kim neyi denedi"
--     sorusunun cevabı YALNIZ burada kalır. Eşleme kodu üretilirken bağlantı
--     düşerse, kodun üretilip üretilmediği belirsizdir; iz olmasa kimin
--     denediği de belirsiz olurdu.
--  2. SON GÖRÜLEN EŞLEME DURUMU. `canteen.device_enrolled` olayını yayınlamak
--     için gerekli: eşlemeyi Kontrol Merkezi başlatmıyor (kodu cihaz giriyor),
--     bu yüzden "yeni eşlendi" ancak ÖNCEKİ okumayla karşılaştırılarak
--     anlaşılır. Bu tablo olmadan olay ya hiç yayınlanmaz ya da her okumada
--     yeniden yayınlanır — ikisi de yanlış.
--
-- KOD BURAYA HİÇ YAZILMAZ. Eşleme kodu bir sırdır, iz satırı ise silinmez;
-- yazsaydık kodun ömrü 10 dakika yerine sonsuz olurdu.
--
-- K5: tablo VE index adlarının hepsi `mod_bbd_canteen_devices_` önekiyle başlar.

-- Yazma denemelerinin yerel izi. SATIR SİLİNMEZ.
CREATE TABLE IF NOT EXISTS mod_bbd_canteen_devices_audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kiosk_id   INTEGER NOT NULL DEFAULT 0,   -- 0 = henüz kimliği yok (kayıt açma)
    action     TEXT NOT NULL,                -- create_kiosk | rename_kiosk |
                                             -- pairing_code | revoke_kiosk
    reason     TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',     -- oturumdan gelir, gövdeden DEĞİL
    result     TEXT NOT NULL DEFAULT '',     -- denendi | ok | engellendi | hata
    detail     TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_bbd_canteen_devices_audit_kiosk
    ON mod_bbd_canteen_devices_audit (kiosk_id, created_at);

CREATE INDEX IF NOT EXISTS mod_bbd_canteen_devices_audit_time
    ON mod_bbd_canteen_devices_audit (created_at);

-- Son görülen eşleme durumu. Otorite KANTİNDİR; bu tablo bir önbellek değil,
-- yalnız "bir önceki okumada ne görmüştüm" hatırasıdır ve tek işi olayın bir
-- kez yayınlanmasını sağlamaktır.
CREATE TABLE IF NOT EXISTS mod_bbd_canteen_devices_seen (
    kiosk_id   INTEGER PRIMARY KEY,
    paired_at  TEXT NOT NULL DEFAULT '',   -- boş = henüz eşlenmemiş
    revoked_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
