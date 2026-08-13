-- Kantin Raporları — modülün KENDİ tabloları (K5).
--
-- Kantin işlem geçmişini tek istekte en çok 5000 satır veriyor ve her rapor
-- açılışında baştan çekmek hem yavaş hem gereksiz. Çekilen her GÜN buraya
-- ham kalem listesiyle yazılır; ikinci açılışta kantine hiç gidilmez.
--
-- Bu bir ÖNBELLEKTİR, otorite değildir: bir günü tazelemek onu yeniden
-- çekmekten ibarettir. Kantindeki hiçbir veri buraya taşınmaz, kopyalanır.

CREATE TABLE IF NOT EXISTS mod_bbd_canteen_reports_day (
    day          TEXT PRIMARY KEY,               -- YYYY-MM-DD
    payload_json TEXT NOT NULL,                  -- o günün işlemleri (kalem kırılımlı)
    tx_count     INTEGER NOT NULL DEFAULT 0,
    total        INTEGER NOT NULL DEFAULT 0,     -- kuruş, iptalliler hariç
    fetched_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_bbd_canteen_reports_day_fetched
    ON mod_bbd_canteen_reports_day (fetched_at);

-- Kayıtlı rapor tanımları: sık bakılan aralık/kırılım birleşimleri.
CREATE TABLE IF NOT EXISTS mod_bbd_canteen_reports_saved (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    params_json TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    created_by  TEXT NOT NULL DEFAULT ''
);

CREATE UNIQUE INDEX IF NOT EXISTS mod_bbd_canteen_reports_saved_name
    ON mod_bbd_canteen_reports_saved (name);
