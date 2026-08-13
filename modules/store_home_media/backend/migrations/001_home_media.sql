-- Ana Ekran Görselleri modülünün YEREL tabloları.
--
-- Buraya yalnız Bagisto'da KARŞILIĞI OLMAYAN veri yazılır. Slotun kendisi
-- (başlık, görsel, hedef, yayın aralığı) mağazadadır ve kopyalanmaz: kopya,
-- mağaza tarafında yapılan bir değişiklikten sonra vitrinde olmayan bir
-- yerleşimi varmış gibi gösterir.
--
-- İki şeyin karşılığı yok:
--  1. GEREKÇE. Bagisto denetim kaydı tutuyor ama "neden" alanı yok. Ayrıca ağ
--     koparsa "ne yapmaya çalıştık" kaydı yalnız burada kalır.
--  2. ÖLÇÜ KARARI. "Önerilen 1920x640, yüklenen 1200x400 — mobilde bulanık"
--     bilgisi mağazada tutulmuyor; görsel yüklendikten sonra dosyaya bakıp
--     yeniden hesaplamak da mümkün değil (dosya uzakta). Uyarıya rağmen
--     yüklenen görselin izi burada durur ve "bu banner neden bulanık"
--     sorusunun cevabı kaybolmaz.

CREATE TABLE IF NOT EXISTS mod_store_home_media_audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_id    INTEGER NOT NULL DEFAULT 0,   -- 0 = henüz kimliği yok (yeni slot) ya da alan geneli
    area       TEXT NOT NULL DEFAULT '',     -- slider | banner | collection | announcement
    action     TEXT NOT NULL,
    reason     TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    result     TEXT NOT NULL DEFAULT '',     -- denendi | ok | dry_run | hata
    detail     TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_store_home_media_audit_slot
    ON mod_store_home_media_audit (slot_id, created_at);

CREATE INDEX IF NOT EXISTS mod_store_home_media_audit_time
    ON mod_store_home_media_audit (created_at);

-- Yüklenen görselin ölçü kaydı. `sha256` aynı görselin ikinci kez yüklendiğini
-- gösterir; `verdict` yükleme anındaki kararı (uygun / bulanık / oran farklı)
-- dondurur.
CREATE TABLE IF NOT EXISTS mod_store_home_media_assets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_id    INTEGER NOT NULL DEFAULT 0,
    area       TEXT NOT NULL DEFAULT '',
    sha256     TEXT NOT NULL DEFAULT '',
    mime       TEXT NOT NULL DEFAULT '',
    width      INTEGER NOT NULL DEFAULT 0,
    height     INTEGER NOT NULL DEFAULT 0,
    bytes      INTEGER NOT NULL DEFAULT 0,
    verdict    TEXT NOT NULL DEFAULT '',
    note       TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_store_home_media_assets_slot
    ON mod_store_home_media_assets (slot_id, created_at);
