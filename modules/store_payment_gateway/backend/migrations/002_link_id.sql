-- Link ile tahsilat — mağazadaki ödeme linkinin SAYISAL kimliği.
--
-- NEDEN AYRI SÜTUN: `token` sütunu mağazanın `code` dizesini taşıyor
-- ("insanın okuduğu kod": telefonda söylenen, SMS'e giren 12 haneli kod).
-- Mağazanın UÇLARI ise o kodu kabul etmiyor; hem tekil okuma
-- (`GET /api/admin/bbd/payment-links/{id}`) hem iptal
-- (`POST /api/admin/bbd/payment-links/{id}/cancel`) rotaları
-- `->whereNumber('id')` ile daraltılmış ve BİRİNCİL ANAHTARI istiyor.
--
-- Tek sütunda iki kavramı taşımaya çalışmanın bedeli ölçüldü:
--
--  1. "İptal" düğmesi HİÇ çalışmıyordu. Çağrıya `code` gidiyordu, geçit
--     `key.isdigit()` denetiminde reddediyordu. `LinkCode` alfabesi
--     `0123456789ABCDEFGHJKMNPQRSTVWXYZ`; 12 hanenin tamamının rakam çıkma
--     olasılığı (10/32)^12 ≈ 1,2e-6. Ekran düşmüyordu (K7) ama personel
--     yanlış giden bir bağlantıyı kapatamıyordu.
--
--  2. Yoklama YANLIŞ SATIRI okuyabiliyordu. Liste ucu `token`/`order_id`
--     süzgeci tanımıyor (yalnız `status` ve `q`) ve sayfa başına en çok 50
--     satır veriyor; aranan link sayfada yoksa liste boş dönmüyor, EN YENİ
--     linkleri döndürüyor. Sayısal kimlikle tekil uç çağrıldığında bu
--     belirsizlik ortadan kalkıyor: ya istenen kayıt gelir ya 404.
--
-- Varsayılan 0 = "mağazada karşılığı yok". Mevcut satırların anlamı değişmez;
-- eski satırlarda kimlik boş kalır ve yoklama `q` araması yoluna düşer.
-- VERİ SİLİNMEZ: `token` sütunu duruyor, üzerine yazılmıyor, yanına ekleniyor.

ALTER TABLE mod_store_payment_gateway_requests
    ADD COLUMN link_id INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS mod_store_payment_gateway_requests_link
    ON mod_store_payment_gateway_requests (link_id);
