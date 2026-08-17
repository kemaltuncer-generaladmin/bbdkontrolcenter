-- Kontrol Paneli modülünün YEREL tablosu. TEK TABLO, VE BU BİLİNÇLİ.
--
-- Buraya yalnız BLD'de KARŞILIĞI OLMAYAN veri yazılır. Sipariş sayısı, ciro,
-- kapasite, abonelik borcu ve olay sayaçları BLD sunucusundadır ve
-- KOPYALANMAZ: gösterge paneline bakan kişinin sorduğu soru "şu an ne oluyor"
-- ve yerel bir kopya her zaman bir tur geride kalır. Bir tur geride kalan bir
-- "canlı" ekran, ekranın tek işini bozar.
--
-- DENETİM TABLOSU YOKTUR. Kardeş modüllerin hepsinde `mod_<id>_audit` var;
-- burada yok çünkü denetlenecek bir yazma yok: sözleşme bu alanda yazma ucu
-- saymıyor ve okumaları da denetlemiyor (`BLD/docs/control/dashboard.md` →
-- "Bu alanda yazma ucu yoktur ve okumalar denetlenmez"). Gösterge paneli 30
-- saniyede bir yoklanan bir ekran; her yoklamayı denetim iznine yazmak, izi
-- tamamen bu trafiğe boğar ve gerçek yazmaların satırlarını görünmez kılardı.
--
-- Geriye tek şey kalıyor: EKRAN TERCİHİ. Yoklama aralığı, işletme seçimi ve
-- akış satır sayısı yalnız bu ekranın ne gösterdiğini belirler; BLD'yi
-- ETKİLEMEZ.
--
-- K5: tablo VE index adlarının hepsi `mod_bld_dashboard_` önekiyle başlar.

-- Ekran tercihi. Anahtar başına tek satır; `key` birincil anahtar olduğu için
-- yazma UPSERT'tir ve eski değer üzerine yazılır. Burada geçmiş tutulmaz:
-- "yoklama aralığını geçen hafta 60 yapmıştım" sorusunun kimseye faydası yok.
CREATE TABLE IF NOT EXISTS mod_bld_dashboard_prefs (
    key        TEXT PRIMARY KEY,   -- poll_seconds | location_id | flow_limit |
                                   -- flow_enabled
    value      TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',   -- oturumdan gelir, gövdeden DEĞİL
    updated_at TEXT NOT NULL
);
