-- Zil Sistemi — öğrenci çağırma (anons sayfası) geçmişi ve hazır sesleri.
--
-- `bell.announce` yeteneği (mobil geçit, staff ekranı) serbest bir cümleyi
-- okulun hoparlöründen duyurur: "Elif Kaya, lütfen danışmaya gel." Ses
-- `mod_bell_voice` önbelleğinden gelir; yoksa Vertex ile ANLIK üretilir.
--
-- GEÇMİŞ (`mod_bell_page`). Her çağrı bir satırdır. Metnin KENDİSİ YAZILMAZ,
-- özeti yazılır (`text_hash`): bu tablo "kim, ne zaman, sonuç ne oldu"
-- sorusunun cevabıdır; öğrenci adlarının ikinci bir kopyası değildir.
-- `request_id` TEKİLDİR: aynı istek yeniden denenirse anons ikinci kez
-- çalınmaz, ilk sonucun aynısı döner.
--
-- KÖPRÜDEKİ SES. Zil ajanının kitaplığı `needed_sounds()` ile eşitlenir ve
-- listede olmayan ses köprüden SİLİNİR. Son 24 saatte çalınmış sayfa sesleri
-- ve hazırlanmış (gece üretilmiş ya da `cache_key` ile işaretlenmiş) sesler
-- 60 gün listede kalır; yoksa eşitleme döngüsü onları bir dakika içinde
-- sildirir ve ikinci çağrı yeniden yükleme bekler.

CREATE TABLE IF NOT EXISTS mod_bell_page (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    text_hash   TEXT NOT NULL,
    voice_key   TEXT NOT NULL DEFAULT '',
    result      TEXT NOT NULL,              -- queued | disabled | tts_unavailable | failed
    actor       TEXT NOT NULL DEFAULT '',
    request_id  TEXT UNIQUE,
    cache_key   TEXT NOT NULL DEFAULT '',
    cached      INTEGER NOT NULL DEFAULT 0,
    detail      TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS mod_bell_page_at ON mod_bell_page (at);

-- Hazırlanmış sayfa sesleri: hangi ses neden tutuluyor.
CREATE TABLE IF NOT EXISTS mod_bell_page_voice (
    voice_key     TEXT PRIMARY KEY,
    cache_key     TEXT NOT NULL DEFAULT '',
    prepared_at   TEXT NOT NULL DEFAULT '',
    last_used_at  TEXT NOT NULL DEFAULT ''
);
