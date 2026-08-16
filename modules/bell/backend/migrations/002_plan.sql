-- Zil Sistemi 0.2 — haftalık saatler, gruplar ve anons ses önbelleği.
--
-- 001'de ders saatleri Ders Takvimi modülünündü ve zil onları yetenek üzerinden
-- okuyordu. Artık saatlerin ve grupların sahibi burasıdır; Ders Takvimi ekranı
-- `bell.week` yeteneğinden okuyup salt okunur gösterir. Eski
-- `mod_bbd_class_schedule_document` tablosuna DOKUNULMAZ — o modülün verisidir (K5),
-- yalnızca artık kimse okumaz.

-- Haftalık zil saatleri. Otomasyonun TEK kaynağı.
-- Bir satır = "pazartesi 08:40'ta zil çalsın, arkasından derse geçiniz anonsu".
CREATE TABLE IF NOT EXISTS mod_bell_time (
    id    TEXT PRIMARY KEY,
    day   TEXT NOT NULL,               -- mon | tue | wed | thu | fri | sat | sun
    time  TEXT NOT NULL,               -- HH:MM
    label TEXT NOT NULL DEFAULT ''     -- "teneffüs" / "mola" — yalnız ekranda görünür
);

-- Aynı güne aynı saat iki kez girilemez: zil iki kez çalmasın.
CREATE UNIQUE INDEX IF NOT EXISTS mod_bell_time_slot ON mod_bell_time (day, time);

-- Gruplar. Elle çağrılırlar: "İlayda, Hüseyin hoca ile dersiniz başlıyor."
--
-- SATIR SİLİNMEZ. Kaldırma `deleted_at` yazar; grubun üretilmiş sesi ve çalma
-- günlüğü yerinde kalır. Silinen bir grup sonradan aynı adla açılırsa ses
-- yeniden üretilmez — metin aynı, özet aynı.
CREATE TABLE IF NOT EXISTS mod_bell_group (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    sort       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    deleted_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS mod_bell_group_live ON mod_bell_group (deleted_at, sort);

-- Anons sesi önbelleği. AYNI METİN İKİNCİ KEZ ÜRETİLMEZ.
--
-- Anahtar metnin kendisi değil, (metin + model + ses) üçlüsünün özetidir: ses
-- değiştirilirse aynı metin yeniden üretilmelidir, metin aynı kaldığı için değil.
-- `error` doluysa üretim başarısızdır; ekran bunu kırmızı gösterir ve o sesi
-- kullanan düğme kapalı kalır. Sessiz arıza yoktur.
CREATE TABLE IF NOT EXISTS mod_bell_voice (
    hash       TEXT PRIMARY KEY,        -- sha256(metin | model | ses)
    text       TEXT NOT NULL,
    file       TEXT NOT NULL DEFAULT '',    -- data/sounds altındaki dosya adı
    model      TEXT NOT NULL DEFAULT '',
    voice      TEXT NOT NULL DEFAULT '',
    bytes      INTEGER NOT NULL DEFAULT 0,
    seconds    REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT '',
    error      TEXT NOT NULL DEFAULT ''
);

-- 001'deki çalma günlüğü duruyor; ne çalındığını ayırt edebilmek için tür eklendi.
-- (zil | anons | cagri). SQLite ALTER TABLE ADD COLUMN yeniden çalıştırılamaz,
-- ama göç yalnız bir kez uygulanır — çekirdek `schema_migrations` ile korur.
ALTER TABLE mod_bell_log ADD COLUMN kind TEXT NOT NULL DEFAULT '';

-- Ajanın son görülme kaydı burada tutulmaz: o köprünün bilgisidir ve
-- `bridge.status()` ile taze okunur. Eskimiş bir "son görülme" satırı,
-- ajan çökmüşken ekranı yeşil göstermekten kötüdür.
