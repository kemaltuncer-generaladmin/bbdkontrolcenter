-- Durum Monitörü modülünün YEREL tabloları.
--
-- BU MODÜL, UZAK VERİDEN TÜREMİŞ VERİ SAKLAYAN TEK BLD MODÜLÜDÜR ve bu bir
-- istisna değil, gereksinimin kendisidir: "en ufak hata bile loglanıp orada
-- kalacak". Uzak taraf böyle bir geçmiş TUTMUYOR.
--
-- Ayrımı bir cümlede kurmak gerekirse: `veykemtu_monitor_events` yalnız
-- BİLEŞENLERİN YAZABİLDİĞİ hatayı bilir. Geçidin kopması, imzanın reddedilmesi,
-- ucun henüz dağıtılmamış olması ve sunucunun hiç cevap vermemesi SUNUCUYA HİÇ
-- ULAŞMAZ — yani tam olarak izlemek istediğimiz arıza, uzak defterde görünmez
-- olandır. O gözlem burada durur ve SİLİNMEZ.
--
-- BURAYA İŞ VERİSİ YAZILMAZ. Sipariş, müşteri, stok, abonelik ve fatura
-- BLD'dedir ve kopyalanmaz (K5 komşusu ilke): bu tablolarda tek bir kuruş,
-- tek bir telefon numarası, tek bir sipariş kalemi bulunmaz.
--
-- K5: tablo VE index adlarının hepsi `mod_bld_status_monitor_` önekiyle başlar.

-- ===================================================================== gözlem
--
-- Kontrol Merkezi'nin KENDİ araştırma defteri.
--
-- SATIR SİLİNMEZ, TEKRARDA BİRLEŞİR. Ekran 60 saniyede bir yokluyor; her
-- yoklamayı ayrı satır yazmak günde 1.440 satır × dört bileşen demekti ve
-- defter bir günde okunamaz hâle gelirdi. Bu yüzden satırlar SUNUCUDAKİ
-- KURALIN AYNISIYLA parmak izine göre birleşir (`monitor.md` → Tekilleştirme):
-- `occurrence_count` artar, `last_seen_at` ilerler, `first_seen_at` HİÇ
-- DEĞİŞMEZ. "Bu ne zamandır oluyor" sorusunun cevabı odur ve tekrar sayısı
-- kaybolmadığı için "en ufak hata bile orada kalır" sözü de bozulmaz.
--
-- Aynı kuralın seçilmesi bilinçli: iki geçmişi yan yana koyan kişi, aynı
-- hatanın iki defterde farklı bölünmüş olmasıyla uğraşmasın.
CREATE TABLE IF NOT EXISTS mod_bld_status_monitor_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    -- probe = bileşen şu durumda görüldü · fault = araştırmanın kendisi
    -- başarısız (geçit patladı, uç yayında değil, imza reddedildi)
    kind             TEXT NOT NULL DEFAULT 'probe',
    -- `MonitorEvent.source` sözlüğüyle AYNI değerler. Araştırmanın kendisi
    -- başarısızsa kaynak `kontrol_merkezi`dir: kopukluğu bir bileşenin
    -- arızası gibi yazmak, dört kutuyu birden kırmızıya boyayıp asıl sorunu
    -- (ağ) gizlerdi.
    source           TEXT NOT NULL,
    component        TEXT NOT NULL DEFAULT '',   -- mobil | web | kds | sunucu | ''
    level            TEXT NOT NULL DEFAULT 'info',   -- info|warning|error|critical
    code             TEXT NOT NULL DEFAULT '',
    message          TEXT NOT NULL DEFAULT '',
    -- Görülen sağlık: ok | degraded | down | unknown. `fault` satırlarında
    -- her zaman `unknown` — "soramadım" ile "sordum, kötü" ayrı şeylerdir.
    result           TEXT NOT NULL DEFAULT 'unknown',
    detail           TEXT NOT NULL DEFAULT '{}',
    -- sha256(source|code|device|normalize(message)) — sözleşmedeki kuralın
    -- aynısı. UNIQUE: aynı gözlem ikinci satır açmaz, mevcut satırı ilerletir.
    fingerprint      TEXT NOT NULL UNIQUE,
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    first_seen_at    TEXT NOT NULL,
    last_seen_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_bld_status_monitor_events_seen
    ON mod_bld_status_monitor_events (last_seen_at);

CREATE INDEX IF NOT EXISTS mod_bld_status_monitor_events_source
    ON mod_bld_status_monitor_events (source, result, last_seen_at);

-- ============================================================ düzeltme defteri
--
-- "Bu hata çıkınca ne yapıyoruz" sorusunun yazılı cevabı.
--
-- SATIR SİLİNMEZ, PASİFLEŞTİRİLİR (`enabled = 0`). Bir düzeltme adımını
-- silmek, o adımın hiç denenmemiş olduğunu iddia etmektir; oysa denetim
-- izinde `key` ile duran kayıtlar var ve karşılığı olmayan bir anahtar,
-- geçmişi okunamaz kılardı.
--
-- KOMUT BURADA DEĞİL, EYLEM ADI BURADA. `action` sütunu `monitor.py`
-- içindeki KAPALI listeden bir anahtar taşır; oradan çözülmeyen bir ad
-- çalıştırılmaz. Sütuna serbest metin bir kabuk komutu yazılabilseydi,
-- deftere yazma yetkisi olan biri sunucuda istediğini çalıştırırdı — bu
-- tablonun en önemli kararı budur.
CREATE TABLE IF NOT EXISTS mod_bld_status_monitor_runbook (
    key         TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    -- bld.api = geçitten geçer ve gerçekten çalışır
    -- manual   = kabuk erişimi ister; YAZILIR ama ÇALIŞTIRILMAZ (`ssh`
    --            platform yeteneği bugün boş bir iskelet)
    channel     TEXT NOT NULL DEFAULT 'bld.api',
    action      TEXT NOT NULL DEFAULT 'manual.note',
    device_id   INTEGER NOT NULL DEFAULT 0,      -- 0 = cihaza bağlı değil
    enabled     INTEGER NOT NULL DEFAULT 1,
    actor       TEXT NOT NULL DEFAULT '',        -- oturumdan gelir, gövdeden DEĞİL
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_bld_status_monitor_runbook_enabled
    ON mod_bld_status_monitor_runbook (enabled, key);

-- ================================================================ yazma izi
--
-- Bu EKRANDAN yapılan yazma denemeleri. SATIR SİLİNMEZ.
--
-- Gözlem defteriyle KARIŞTIRILMAZ: orası "sistem ne durumdaydı", burası "kim
-- ne yapmaya çalıştı" sorusunun cevabıdır. İkisini tek tabloya koymak, bir
-- yöneticinin kasayı yeniden başlatmasını bir arıza kaydı gibi gösterirdi.
--
-- BLD de `veykemtu_control_audit` tutuyor (`00-genel.md` §8) ama o kayıt yalnız
-- SUNUCUYA ULAŞAN isteği bilir. Ağ koparsa "kim neyi denedi" sorusunun cevabı
-- yalnız burada kalır — yıkıcı işlemin ÇİFT SATIRI budur (ADR 0012):
-- `denendi` ve sonucu ayrı iki kayıttır.
CREATE TABLE IF NOT EXISTS mod_bld_status_monitor_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL DEFAULT 'monitor_event',  -- monitor_event | runbook | device
    target_id   TEXT NOT NULL DEFAULT '',   -- olay kimliği ya da defter anahtarı
    action      TEXT NOT NULL,              -- monitor.resolve | runbook.save |
                                            -- runbook.run
                                            -- `monitor.resolve` adı SUNUCUDAKİ
                                            -- denetim eylem adıyla AYNI
                                            -- (`monitor.md` → Denetim
                                            -- eylemleri); iki defterin aynı işi
                                            -- başka adla yazması, ikisini yan
                                            -- yana koyan kişiyi yanıltırdı.
    reason      TEXT NOT NULL DEFAULT '',
    actor       TEXT NOT NULL DEFAULT '',
    result      TEXT NOT NULL DEFAULT '',   -- denendi | ok | dry_run |
                                            -- engellendi | hata
    detail      TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_bld_status_monitor_audit_time
    ON mod_bld_status_monitor_audit (created_at);

CREATE INDEX IF NOT EXISTS mod_bld_status_monitor_audit_target
    ON mod_bld_status_monitor_audit (target_type, target_id, created_at);

-- ============================================================= ekran tercihi
--
-- Anahtar başına tek satır; `key` birincil anahtar olduğu için yazma UPSERT'tir.
-- Burada geçmiş tutulmaz: "yoklama aralığını geçen hafta 30 yapmıştım"
-- sorusunun kimseye faydası yok. Tercihler BLD'yi ETKİLEMEZ.
CREATE TABLE IF NOT EXISTS mod_bld_status_monitor_prefs (
    key        TEXT PRIMARY KEY,   -- poll_seconds | page_size | levels |
                                   -- resolved | auto_refresh
    value      TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
