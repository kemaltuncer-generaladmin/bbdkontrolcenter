-- Abonelikler modülünün YEREL tabloları.
--
-- Buraya yalnız BLD'de KARŞILIĞI OLMAYAN veri yazılır. Abonelik, satırları,
-- teslimat noktaları, duraklamaları, istisnaları, üretim defteri, sözleşmeleri
-- ve dönem ödemeleri BLD sunucusundadır ve KOPYALANMAZ: gece işi aynı kayda
-- yazıyor, müşteri sözleşmeyi kendi telefonundan imzalıyor ve dönem ödemesi
-- ileride uygulamadan da kapanacak. Yerel bir kopya her zaman bir tur geride
-- kalır; "sözleşme bekleniyor" görünen bir abonelik çoktan imzalanmış olabilir
-- ve yönetici müşteriyi ikinci kez arar. Ekranın yanlış bilgiyi doğru gibi
-- göstermesi, hiç göstermemesinden kötüdür.
--
-- İki şeyin karşılığı yok:
--  1. DENEME KAYDI. BLD `veykemtu_control_audit` tutuyor (`00-genel.md` §8)
--     ama o kayıt yalnız SUNUCUYA ULAŞAN isteği bilir. Sözleşme gönderilirken
--     bağlantı düşerse müşteriye SMS gidip gitmediği belirsizdir; iz olmasa
--     kimin denediği de belirsiz olurdu. Yıkıcı işlemin ÇİFT SATIRI budur
--     (ADR 0012): `denendi` ve sonucu ayrı iki kayıttır.
--  2. EKRAN TERCİHİ. Sayfa boyutu, takvim penceresi, bağlantı ömrü
--     varsayılanı. Yalnız bu ekranın ne gösterdiğini belirler.
--
-- K5: tablo VE index adlarının hepsi `mod_bld_subscriptions_` önekiyle başlar.

-- Yazma denemelerinin yerel izi. SATIR SİLİNMEZ.
CREATE TABLE IF NOT EXISTS mod_bld_subscriptions_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL DEFAULT 'subscription',
                                     -- subscription | subscription_contract |
                                     -- subscription_payment | quote_request | order
                                     -- Adlar sunucudaki denetim hedefleriyle AYNI
                                     -- tutulur (`subscriptions.md` → denetim
                                     -- eylemleri); iki defterin aynı işi başka
                                     -- adla yazması, ikisini yan yana koyan
                                     -- kişiyi yanıltırdı. `order` bilerek burada:
                                     -- erken serbest bırakmanın hedefi abonelik
                                     -- değil SİPARİŞTİR, çünkü soru "bu sipariş
                                     -- neden erken düştü" biçiminde sorulur.
    target_id   INTEGER NOT NULL DEFAULT 0,   -- 0 = henüz kimliği olmayan kayıt
                                              -- (yeni abonelik yazılırken)
    action      TEXT NOT NULL,                -- subscription.create | .update |
                                              -- .activate | .pause | .resume |
                                              -- .cancel | .exception.create |
                                              -- .exception.delete | .generate |
                                              -- .order.release | .request.update |
                                              -- .request.convert |
                                              -- .contract.create | .contract.resend |
                                              -- .contract.cancel |
                                              -- .payment.create | .payment.paid
    reason      TEXT NOT NULL DEFAULT '',
    actor       TEXT NOT NULL DEFAULT '',     -- oturumdan gelir, gövdeden DEĞİL
    result      TEXT NOT NULL DEFAULT '',     -- denendi | ok | dry_run | hata

    -- FİYAT AYRI BİR SÜTUNDUR, `detail` JSON'unun içinde değil.
    -- Bu ekranın en çok sorulan sorusu "fiyatı kim, ne zaman, neden anlaştı"
    -- ve JSON içinden aranan bir alan ne sıralanabilir ne indekslenebilir.
    -- NULL = bu satır fiyatla ilgili değil (duraklatma, sözleşme gönderimi…).
    -- Değer KURUŞ TAM SAYIDIR; ondalık yok.
    price_kurus INTEGER,

    detail      TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_bld_subscriptions_audit_target
    ON mod_bld_subscriptions_audit (target_type, target_id, created_at);

CREATE INDEX IF NOT EXISTS mod_bld_subscriptions_audit_time
    ON mod_bld_subscriptions_audit (created_at);

-- Fiyat geçmişi sorgusu bu indeksten geçer: "bu aboneliğin fiyatı kaç kez,
-- kim tarafından değişti" sorusu tablonun tamamını taramamalı.
CREATE INDEX IF NOT EXISTS mod_bld_subscriptions_audit_price
    ON mod_bld_subscriptions_audit (target_id, price_kurus, created_at);

-- Ekran tercihi. Anahtar başına tek satır; `key` birincil anahtar olduğu için
-- yazma UPSERT'tir ve eski değer üzerine yazılır. Burada geçmiş tutulmaz:
-- "takvim penceresini geçen hafta 60 yapmıştım" sorusunun kimseye faydası yok.
CREATE TABLE IF NOT EXISTS mod_bld_subscriptions_prefs (
    key        TEXT PRIMARY KEY,   -- page_size | calendar_days | expires_in_days
    value      TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
