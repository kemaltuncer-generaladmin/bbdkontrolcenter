-- Sipariş Yönetimi modülünün YEREL tabloları.
--
-- Buraya yalnız BLD'de KARŞILIĞI OLMAYAN veri yazılır. Sipariş, kalem,
-- revizyon, ödeme ve fatura kaydı BLD sunucusundadır ve KOPYALANMAZ: aynı
-- kayda mutfak kasası da yazıyor, durum dakikalar içinde değişiyor. Yerel bir
-- kopya her zaman bir tur geride kalır ve "hazırlanıyor" görünen bir sipariş
-- çoktan teslim edilmiş olabilir; yönetici buna bakarak müşteriye yanlış bilgi
-- verir. Ekranın yanlış bilgiyi doğru gibi göstermesi, hiç göstermemesinden
-- kötüdür.
--
-- İki şeyin karşılığı yok:
--  1. DENEME KAYDI. BLD `veykemtu_control_audit` tutuyor (`00-genel.md` §8)
--     ama o kayıt yalnız SUNUCUYA ULAŞAN isteği bilir. Ağ koparsa, geçit
--     patlarsa ya da istek yarıda kalırsa "kim neyi denedi" sorusunun cevabı
--     yalnız burada kalır. İptal isteği gönderilirken bağlantı düşerse
--     siparişin iptal edilip edilmediği belirsizdir; iz olmasa kimin denediği
--     de belirsiz olurdu. Yıkıcı işlemin ÇİFT SATIRI budur (ADR 0012):
--     `denendi` ve sonucu ayrı iki kayıttır.
--  2. EKRAN TERCİHİ. Sayfa boyutu, açılış aralığı, yoklama sıklığı. Yalnız bu
--     ekranın ne gösterdiğini belirler; BLD'yi ETKİLEMEZ.
--
-- K5: tablo VE index adlarının hepsi `mod_bld_orders_` önekiyle başlar.

-- Yazma denemelerinin yerel izi. SATIR SİLİNMEZ.
CREATE TABLE IF NOT EXISTS mod_bld_orders_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL DEFAULT 'order',
    target_id   INTEGER NOT NULL DEFAULT 0,      -- 0 = siparişe bağlı değil (dışa aktarım)
    action      TEXT NOT NULL,                   -- order.revise | order.status |
                                                 -- order.cancel | order.export
                                                 -- Adlar sunucudaki denetim eylem
                                                 -- adlarıyla AYNI tutulur
                                                 -- (`orders.md` → denetim eylemleri);
                                                 -- iki defterin aynı işi başka adla
                                                 -- yazması, ikisini yan yana koyan
                                                 -- kişiyi yanıltırdı.
    reason      TEXT NOT NULL DEFAULT '',
    actor       TEXT NOT NULL DEFAULT '',        -- oturumdan gelir, gövdeden DEĞİL
    result      TEXT NOT NULL DEFAULT '',        -- denendi | ok | dry_run |
                                                 -- engellendi | hata
    detail      TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_bld_orders_audit_target
    ON mod_bld_orders_audit (target_type, target_id, created_at);

CREATE INDEX IF NOT EXISTS mod_bld_orders_audit_time
    ON mod_bld_orders_audit (created_at);

-- Ekran tercihi. Anahtar başına tek satır; `key` birincil anahtar olduğu için
-- yazma UPSERT'tir ve eski değer üzerine yazılır. Burada geçmiş tutulmaz:
-- "sayfa boyutunu geçen hafta 50 yapmıştım" sorusunun kimseye faydası yok.
CREATE TABLE IF NOT EXISTS mod_bld_orders_prefs (
    key        TEXT PRIMARY KEY,   -- page_size | range_days | poll_seconds |
                                   -- auto_refresh
    value      TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
