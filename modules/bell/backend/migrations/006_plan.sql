-- Zil Sistemi 0.5 — saat setleri, Akademi'den gelen zil planı ve grup taşıma izi.
--
-- Sözleşme: bbd-akademi/docs/integrations/km-ogrenci-gruplari-ve-zil.md §3.
--
-- SAAT SETİ (`mod_bell_profile` + `_time`). Haftalık saatler (`mod_bell_time`)
-- varsayılan settir. Saat seti tek bir güne uygulanan ADLANDIRILMIŞ saat
-- listesidir ("Yarım gün", "Deneme günü"); Akademi kurum takviminde o güne
-- `profile` kipiyle bağlar. Tür (`ders`/`teneffus`) burada da AÇIKÇA saklanır
-- (ADR 0027): etiketten tahmin edilmez. Dilimler gün bağımsızdır.
--
-- ZİL PLANI (`mod_bell_day` + `mod_bell_call`). Akademi'den türetilmiş
-- çalışma kopyası: gün kipi (`normal` · `off` · `profile`) ve oturum başı grup
-- çağrıları, önümüzdeki 14 gün. Çalma anında Akademi'ye gidilmez; tetik bu
-- tablolardan kurulur. `override_*` KM'nin o güne sınırlı acil kapatmasıdır
-- ("Bugün zili kapat"); Akademi takvimine yazılmaz, durum ucunda görünür.
-- Gün satırı yalnız acil kapatma ya da reddedilen bir itme için de açılabilir
-- (`source` = ''): o gün için Akademi planı yok demektir.
--
-- Çağrı satırı METNİ değil yer tutucu değerlerini tutar (öğretmenin anons
-- adı, derslik, ders); metin zil modülünde tek yerde üretilir. `voice_key` ve
-- `generic_voice_key` köprü eşitlemesinin silmemesi gereken seslerdir.
--
-- `mod_bell_group.student_group_id`: eski zil gruplarının Öğrenci
-- Yönetimi'ndeki gruplara taşınma izi (§6). Satır SİLİNMEZ (K5).
--
-- EKLEYİCİ göç: mevcut tablolara yalnız sütun eklenir. İndeksler 007'de,
-- işlemsiz açılır.

CREATE TABLE IF NOT EXISTS mod_bell_profile (
    id          TEXT PRIMARY KEY,                -- "yarim-gun"
    name        TEXT NOT NULL,
    name_fold   TEXT NOT NULL,
    sort        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    updated_by  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS mod_bell_profile_time (
    id          TEXT PRIMARY KEY,                -- "<profil>-<SSDD>"
    profile_id  TEXT NOT NULL,
    time        TEXT NOT NULL,                   -- HH:MM
    label       TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'ders'     -- ders | teneffus
);

CREATE TABLE IF NOT EXISTS mod_bell_day (
    date              TEXT PRIMARY KEY,          -- YYYY-MM-DD (Europe/Istanbul)
    mode              TEXT NOT NULL DEFAULT 'normal',   -- normal | off | profile
    profile_id        TEXT NOT NULL DEFAULT '',
    reason            TEXT NOT NULL DEFAULT '',
    revision          INTEGER NOT NULL DEFAULT 0,       -- Akademi'nin gün revizyonu
    plan_hash         TEXT NOT NULL DEFAULT '',
    source            TEXT NOT NULL DEFAULT '',         -- akademi | '' (plan yok)
    override_mode     TEXT NOT NULL DEFAULT '',         -- '' | off
    override_by       TEXT NOT NULL DEFAULT '',
    override_at       TEXT NOT NULL DEFAULT '',
    received_at       TEXT NOT NULL DEFAULT '',
    last_result       TEXT NOT NULL DEFAULT '',         -- applied | unchanged | stale | rejected
    last_error        TEXT NOT NULL DEFAULT '',         -- revision_conflict | unknown_profile
    last_dispatch_at  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS mod_bell_call (
    id                 TEXT PRIMARY KEY,
    date               TEXT NOT NULL,
    at                 TEXT NOT NULL,            -- HH:MM
    ref                TEXT NOT NULL,            -- Akademi oturum kimliği
    group_kind         TEXT NOT NULL DEFAULT 'group',   -- group | individual
    group_name         TEXT NOT NULL DEFAULT '',
    group_ref          TEXT NOT NULL DEFAULT '',        -- km_group:<id> | km_class:<ad>
    teacher_name       TEXT NOT NULL DEFAULT '',
    room               TEXT NOT NULL DEFAULT '',
    subject            TEXT NOT NULL DEFAULT '',
    kind               TEXT NOT NULL DEFAULT 'lesson',  -- lesson | etut | makeup
    text_hash          TEXT NOT NULL DEFAULT '',
    voice_key          TEXT NOT NULL DEFAULT '',
    generic_voice_key  TEXT NOT NULL DEFAULT '',
    state              TEXT NOT NULL DEFAULT 'planned', -- planned | dispatched | skipped | failed
    detail             TEXT NOT NULL DEFAULT '',
    updated_at         TEXT NOT NULL DEFAULT ''
);

ALTER TABLE mod_bell_group ADD COLUMN student_group_id TEXT NOT NULL DEFAULT '';
