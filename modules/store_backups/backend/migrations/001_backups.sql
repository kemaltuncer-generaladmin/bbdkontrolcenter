-- Manuel Yedekleme modülünün YEREL tablosu.
--
-- Yedeklerin KENDİSİ mağazadadır ve buraya kopyalanmaz: kopya envanter, mağaza
-- tarafında bir dosya taşındığında ya da rotasyon sildiğinde sessizce yalan
-- söyler. Ekran her açılışta gerçek envanteri okur.
--
-- Burada yalnız mağazada KARŞILIĞI OLMAYAN şey durur: NİYET ve GEREKÇE.
-- "Bu yedeği kim, hangi gerekçeyle geri yüklemeye çalıştı" sorusunun cevabı
-- Bagisto denetim kaydında yok; ayrıca ağ koparsa yalnız burada kalır.
-- Geri yüklemenin YARIDA KALDIĞI durumda elimizdeki tek kayıt bu satırdır:
-- önce `denendi`, sonra `ok`/`hata` yazılır; ikincisi hiç gelmediyse işlem
-- ortada kesilmiş demektir.

CREATE TABLE IF NOT EXISTS mod_store_backups_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    backup_name TEXT NOT NULL DEFAULT '',    -- boş = henüz adı olmayan yeni yedek
    action      TEXT NOT NULL,               -- create | verify | download | restore | delete
    reason      TEXT NOT NULL DEFAULT '',
    actor       TEXT NOT NULL DEFAULT '',
    result      TEXT NOT NULL DEFAULT '',    -- denendi | ok | bad | dry_run | iptal | hata | uc_yok
    detail      TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_store_backups_audit_name
    ON mod_store_backups_audit (backup_name, created_at);

CREATE INDEX IF NOT EXISTS mod_store_backups_audit_time
    ON mod_store_backups_audit (created_at);
