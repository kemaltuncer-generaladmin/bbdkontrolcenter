-- BBD Store geçidinin iki tablosu. Bagisto'daki hiçbir veri BURAYA
-- KOPYALANMAZ; ikisi de Bagisto'da karşılığı OLMAYAN bilgiyi tutar.

-- 1) Yerel denetim izi. Bagisto `admin_api_audits` tablosunda yalnız
--    BAŞARILI yazmaların alan farkını tutuyor; gerekçeyi hiç tutmuyor ve
--    gönderilemeyen isteği bilmiyor. Satır istek ÇIKMADAN yazılır:
--    `result` boş kalmışsa "gönderildi mi belli değil" demektir.
CREATE TABLE IF NOT EXISTS mod_store_api_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id  TEXT NOT NULL UNIQUE,     -- X-Bbd-Request-Id (idempotency anahtarı)
    method      TEXT NOT NULL,
    path        TEXT NOT NULL,
    action      TEXT NOT NULL DEFAULT '', -- 'cancel_order', 'update_product' ...
    reason      TEXT NOT NULL DEFAULT '',
    actor       TEXT NOT NULL DEFAULT '', -- işlemi yapan kişinin adı
    dry_run     INTEGER NOT NULL DEFAULT 1,
    body        TEXT NOT NULL DEFAULT '', -- sırları maskelenmiş istek gövdesi
    status      INTEGER,                  -- HTTP kodu; geçitte durduysa NULL
    result      TEXT NOT NULL DEFAULT '', -- ok · blocked · failed · error:<kod>
    created_at  TEXT NOT NULL,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS mod_store_api_audit_created
    ON mod_store_api_audit (created_at DESC);

-- Gönderilmiş ama sonucu işlenememiş satırları (ağ koptu) tek sorguda
-- bulmak için: result = '' olanlar.
CREATE INDEX IF NOT EXISTS mod_store_api_audit_result
    ON mod_store_api_audit (result, created_at DESC);

-- 2) Referans verinin anlık görüntüsü (L2, 30 dk). Kanal/para/dil/vergi
--    kategorisi gibi ayda bir değişen listeler. Süreç yeniden başladığında
--    ekran mağazaya hiç gitmeden dolar; mağaza erişilemezken de SON BİLİNEN
--    hâl gösterilebilir (K7). Süresi geçen satır SİLİNMEZ, bayat işaretlenir.
CREATE TABLE IF NOT EXISTS mod_store_api_snapshot (
    key        TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    stored_at  TEXT NOT NULL
);
