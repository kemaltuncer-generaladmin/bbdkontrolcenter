-- Talepler modülünün YEREL tabloları.
--
-- Buraya yalnız mağazada KARŞILIĞI OLMAYAN veri yazılır. Talep kaydı, durumu,
-- müşteri yazışması ve iade kalemleri mağazadadır ve kopyalanmaz: kopya,
-- mağaza tarafında yapılan bir değişiklikten sonra sessizce yanlış durum
-- gösterir.
--
-- Üç şeyin karşılığı yok:
--  1. GEREKÇE. Bagisto denetim kaydı tutuyor ama "neden" alanı yok. Ayrıca ağ
--     koparsa "ne yapmaya çalıştık" kaydı yalnız burada kalır.
--  2. İÇ NOT. Personelin kendi arasında yazdığı metin uzağa HİÇ gönderilmez;
--     "internal" bayrağının müşteri portalında yanlış yorumlanması geri
--     alınamaz bir sızıntı olurdu. Bu yüzden iç not mağazaya değil buraya
--     yazılır ve yazışma zincirinde yerel olarak işaretlenir.
--  3. İADE AKTARIMI. Onaylanan talebin İadeler ekranına devri. Olay yolunu
--     dinleyen olmasa bile (İadeler modülü kapalı olabilir) "devredildi mi"
--     sorusunun cevabı burada durur.

CREATE TABLE IF NOT EXISTS mod_store_requests_audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL DEFAULT 0,   -- 0 = toplu iş, tek talebe bağlı değil
    action     TEXT NOT NULL,
    reason     TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    result     TEXT NOT NULL DEFAULT '',     -- denendi | ok | dry_run | hata
    detail     TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_store_requests_audit_request
    ON mod_store_requests_audit (request_id, created_at);

CREATE INDEX IF NOT EXISTS mod_store_requests_audit_time
    ON mod_store_requests_audit (created_at);

-- İç notlar. Yazışma zincirinde müşteri mesajlarıyla birlikte gösterilir ama
-- ASLA gönderilmez; silme yoktur, yazılan not kayıtta kalır.
CREATE TABLE IF NOT EXISTS mod_store_requests_notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL,
    body       TEXT NOT NULL,
    actor      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_store_requests_notes_request
    ON mod_store_requests_notes (request_id, id);

-- İadeler ekranına devir. `items` devir anındaki seçimin kendisidir; sonradan
-- yeniden hesaplanmaz — kullanıcı neyi onayladıysa o devredilmiştir.
CREATE TABLE IF NOT EXISTS mod_store_requests_handoff (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL,
    order_id   INTEGER NOT NULL DEFAULT 0,
    amount     INTEGER NOT NULL DEFAULT 0,   -- kuruş
    items      TEXT NOT NULL DEFAULT '[]',
    actor      TEXT NOT NULL DEFAULT '',
    reason     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_store_requests_handoff_request
    ON mod_store_requests_handoff (request_id, created_at);
