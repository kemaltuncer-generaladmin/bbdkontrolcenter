-- Faturalar modülünün YEREL tabloları.
--
-- Buraya yalnız BLD'de KARŞILIĞI OLMAYAN veri yazılır. Belge kaydı, numarası,
-- durumu ve donmuş içeriği (`snapshot_json`) BLD sunucusundadır ve
-- KOPYALANMAZ: belgenin tek doğru hâli oradadır, iptal orada işlenir ve yerel
-- bir kopya her zaman bir tur geride kalır — "geçerli" görünen bir belge
-- aslında yarım saat önce iptal edilmiş olabilir. Ekranın yanlış bilgiyi doğru
-- gibi göstermesi, hiç göstermemesinden kötüdür.
--
-- İki şeyin karşılığı yok:
--  1. YAZMA DENEMESİ. BLD `veykemtu_control_audit` tutuyor ama o kayıt yalnız
--     SUNUCUYA ULAŞAN isteği bilir. Belge kesilirken ağ koparsa numaranın
--     üretilip üretilmediği belirsizdir; iz olmasa kimin denediği de belirsiz
--     olurdu.
--  2. ÜRETİLEN DOSYANIN KÜNYESİ. Fatura verisi DEĞİL, DOSYA: yol, sha256,
--     boyut ve basıldığı an. Müşterinin elindeki kâğıdın hangi üretimden
--     çıktığı ancak böyle bilinir. Belgenin içeriği buraya YAZILMAZ — kişisel
--     veriyi ve adresi ikinci bir yerde çoğaltırdı.
--
-- K5: tablo VE index adlarının hepsi `mod_bld_invoices_` önekiyle başlar.

-- Yazma denemelerinin yerel izi. SATIR SİLİNMEZ.
CREATE TABLE IF NOT EXISTS mod_bld_invoices_audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL DEFAULT 0,   -- 0 = belge henüz yok (kesme denemesi)
    action     TEXT NOT NULL,                -- invoice.create | invoice.void
    reason     TEXT NOT NULL DEFAULT '',     -- iptalde bu metin belgeye de basılır
    actor      TEXT NOT NULL DEFAULT '',     -- oturumdan gelir, gövdeden DEĞİL
    result     TEXT NOT NULL DEFAULT '',     -- denendi | ok | dry_run | engellendi | hata
    detail     TEXT NOT NULL DEFAULT '{}',   -- kip, kaynak, dönem, belge no, toplam
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_bld_invoices_audit_invoice
    ON mod_bld_invoices_audit (invoice_id, created_at);

CREATE INDEX IF NOT EXISTS mod_bld_invoices_audit_time
    ON mod_bld_invoices_audit (created_at);

-- ÜRETİLEN DOSYANIN ARŞİVİ. Belge değil, dosya.
--
-- `sha256` elindeki kâğıdın hangi üretimden çıktığını kanıtlar: aynı belge iki
-- kez üretilirse iki satır olur ve ikisi de kalır. `printed_at` yalnız CUPS'a
-- gönderim başarılı olduğunda dolar — "üretildi" ile "basıldı" ayrı şeylerdir
-- ve yazıcı yokken ilki yine olur (K7).
--
-- SATIR SİLİNMEZ: dosya diskten kalksa bile künyesi kalır, çünkü soru "bu
-- kâğıt nereden çıktı" sorusudur ve dosyanın varlığı onu cevaplamaz.
CREATE TABLE IF NOT EXISTS mod_bld_invoices_archive (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id   INTEGER NOT NULL DEFAULT 0,   -- 0 = liste dökümü (tek belgeye bağlı değil)
    invoice_no   TEXT NOT NULL DEFAULT '',     -- üretim anındaki belge numarası
    kind         TEXT NOT NULL DEFAULT 'pdf',  -- pdf | html | list
    path         TEXT NOT NULL,
    name         TEXT NOT NULL DEFAULT '',
    sha256       TEXT NOT NULL DEFAULT '',
    bytes        INTEGER NOT NULL DEFAULT 0,
    actor        TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    printed_at   TEXT NOT NULL DEFAULT '',     -- boş = üretildi ama basılmadı
    print_copies INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS mod_bld_invoices_archive_invoice
    ON mod_bld_invoices_archive (invoice_id, created_at);

-- Baskı anı yolla güncelleniyor (`UPDATE ... WHERE path = ?`); indekssiz
-- arama, arşiv büyüdükçe her baskıda tam tarama yapardı.
CREATE INDEX IF NOT EXISTS mod_bld_invoices_archive_path
    ON mod_bld_invoices_archive (path);
