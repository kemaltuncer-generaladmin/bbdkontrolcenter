-- Referans verinin anlık görüntüsü (L2 önbellek, varsayılan 30 dk).
--
-- AYRI DOSYA, 001'İN İÇİNE YAZILMIYOR: çekirdek uygulanmış göçleri ada göre
-- hatırlıyor (`schema_migrations`) ve `001_gateway.sql` kurulu sistemlerde
-- zaten uygulanmış durumda; içeriğini değiştirmek o sistemlerde hiç
-- çalışmazdı.
--
-- BURAYA BLD VERİSİ KOPYALANMAZ. Tabloda yalnız kategori, ödeme yöntemi,
-- ayar varsayılanı ve seçici ürün kataloğu gibi REFERANS listeler durur —
-- sipariş, stok, müşteri ve fatura ASLA (bkz. `cache.py` başlığı). Süreç
-- yeniden başladığında ekran BLD'ye hiç gitmeden dolar; BLD erişilemezken de
-- son bilinen hâl gösterilebilir (K7). Süresi geçen satır SİLİNMEZ, bayat
-- işaretlenir: bayat veri, veri olmamasından iyidir — yeter ki bayat olduğu
-- söylensin.
--
-- TABLO VE İNDEKS ADLARI `mod_bld_api_` ÖNEKİYLE BAŞLAR: çekirdek göçü ad
-- denetiminden geçirir ve başka önekli adı reddeder (K5).
CREATE TABLE IF NOT EXISTS mod_bld_api_snapshot (
    key        TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    stored_at  TEXT NOT NULL
);
