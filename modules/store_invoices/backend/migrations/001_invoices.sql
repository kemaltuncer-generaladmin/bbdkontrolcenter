-- Fatura modülünün YEREL tabloları.
--
-- Buraya yalnız Bagisto'da KARŞILIĞI OLMAYAN veri yazılır. Fatura, kalem,
-- tutar ve gönderi mağazadadır ve kopyalanmaz: kopya, mağaza tarafında
-- yapılan bir düzeltmeden sonra sessizce yanlış rakam gösterir.
--
-- Üç şeyin karşılığı yok:
--  1. YASAL FATURA NUMARASI. Depoda e-Fatura/e-Arşiv entegrasyonu YOKTUR ve
--     Bagisto'nun ürettiği PDF mali belge değildir. Yasal belge dış sistemde
--     (mali müşavir portalı / GİB uygulaması) kesilir; numarası burada
--     Bagisto faturasıyla eşlenir. Bu eşleme olmadan denetimde iki kayıt
--     birbirine bağlanamaz.
--  2. SERİ VE NUMARALANDIRMA. Bagisto'da fatura serisi diye bir kavram yok;
--     `increment_id` tek bir sayaçtır. Seriyi ve sıra numarasını burada
--     tutmak, "A2026 serisinde 145-147 eksik" uyarısını mümkün kılar.
--  3. GEREKÇE. Bagisto denetim kaydı tutuyor ama "neden" alanı yok. Ağ
--     koparsa "ne yapmaya çalıştık" kaydı yalnız burada kalır.

-- Dış sistemde kesilen yasal faturanın Bagisto faturasıyla eşlemesi.
-- SİLME YOKTUR: yanlış eşleme düzeltilirken satır güncellenir, geçmiş
-- gerekçesiyle denetim tablosunda kalır.
CREATE TABLE IF NOT EXISTS mod_store_invoices_legal (
    invoice_id INTEGER PRIMARY KEY,          -- Bagisto fatura kimliği
    series     TEXT NOT NULL DEFAULT '',     -- seri kodu, ör. 'A2026'
    number     INTEGER NOT NULL DEFAULT 0,   -- seri içindeki sıra
    legal_no   TEXT NOT NULL DEFAULT '',     -- tam numara (elle yazılabilir)
    issued_at  TEXT NOT NULL DEFAULT '',     -- yasal belgenin düzenlenme günü
    note       TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_store_invoices_legal_series
    ON mod_store_invoices_legal (series, number);

-- Seri tanımları. `current_no` yalnız ÖNERİ üretir; gerçek numara yasal
-- belgeyi kesen dış sistemden gelir ve elle girilebilir.
CREATE TABLE IF NOT EXISTS mod_store_invoices_series (
    code       TEXT PRIMARY KEY,
    label      TEXT NOT NULL DEFAULT '',
    start_no   INTEGER NOT NULL DEFAULT 1,
    pad        INTEGER NOT NULL DEFAULT 9,   -- sıra numarasının basamak sayısı
    year_reset INTEGER NOT NULL DEFAULT 1,   -- 1 = yıl başında sayaç sıfırlanır
    is_default INTEGER NOT NULL DEFAULT 0,
    note       TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

-- Yerel denetim izi.
CREATE TABLE IF NOT EXISTS mod_store_invoices_audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL DEFAULT 0,   -- 0 = toplu iş ya da sipariş bazlı
    order_id   INTEGER NOT NULL DEFAULT 0,
    action     TEXT NOT NULL,
    reason     TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    result     TEXT NOT NULL DEFAULT '',     -- denendi | ok | dry_run | hata
    detail     TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_store_invoices_audit_invoice
    ON mod_store_invoices_audit (invoice_id, created_at);

CREATE INDEX IF NOT EXISTS mod_store_invoices_audit_time
    ON mod_store_invoices_audit (created_at);
