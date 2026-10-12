-- Öğrenci Yönetimi — öğrenci grupları ve TARİHLİ üyelik (sözleşme
-- bbd-akademi/docs/integrations/km-ogrenci-gruplari-ve-zil.md §2.2).
--
-- GRUBUN TEK SAHİBİ BURASIDIR. Grup = ad + öğrenci seçimi. BBD Akademi grubu
-- buradan çeker (`bbd_students.groups` yeteneği → mobil geçit `GET /groups`);
-- Akademi'de grup yazma yolu yoktur. Zil sistemi elle çağrı listesini de
-- buradan okur (K3: yetenek üzerinden, tablo okunmaz).
--
-- SİLME YOKTUR, ARŞİV VARDIR (`archived_at`). Geçmiş yoklama ve raporlar
-- grubun kimliğine bağlıdır; arşivlenen grubun açık üyelikleri arşiv gününde
-- kapanır.
--
-- ÜYELİK TARİHLİDİR. `valid_from` (dahil) ve `valid_to` (dahil, son gün; ''
-- = açık uç) Europe/Istanbul yerel GÜNÜDÜR (YYYY-MM-DD). Kesişme, bireysel
-- grupta tek üye ve ad tekilliği SERVİSTE denetlenir (`backend/groups.py`):
-- lehçe çevirmeni kısıt türlerini sınırlı tanıyor ve kural tek yerde kalsın
-- (002 göçündeki gerekçe).
--
-- REVİZYON. Her yazma `mod_bbd_students_group_rev` sayacını +1 artırır ve
-- dokunduğu grup/üyelik satırlarına damgalar. Bu KALICI senkron imlecidir;
-- dizinin süreç içi `revision`'ından (yalnız önbellek anahtarı) farklıdır.
--
-- EKLEYİCİ göç: mevcut tablolara dokunmaz. İndeksler ayrı, işlemsiz göçte
-- (004) açılır.

CREATE TABLE IF NOT EXISTS mod_bbd_students_group (
    id          TEXT PRIMARY KEY,               -- uuid
    name        TEXT NOT NULL,
    name_fold   TEXT NOT NULL,                  -- Türkçe duyarlı katlama (ad tekilliği)
    call_name   TEXT NOT NULL DEFAULT '',       -- anons adı; boşsa `name` okunur
    kind        TEXT NOT NULL DEFAULT 'group',  -- group | individual
    note        TEXT NOT NULL DEFAULT '',
    archived_at TEXT NOT NULL DEFAULT '',       -- ISO damga; '' = canlı
    created_at  TEXT NOT NULL,
    created_by  TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL,
    updated_by  TEXT NOT NULL DEFAULT '',
    revision    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS mod_bbd_students_group_member (
    id          TEXT PRIMARY KEY,               -- uuid
    group_id    TEXT NOT NULL,
    kantin_id   TEXT NOT NULL,                  -- STU-…
    valid_from  TEXT NOT NULL,                  -- YYYY-MM-DD, dahil
    valid_to    TEXT NOT NULL DEFAULT '',       -- YYYY-MM-DD, dahil; '' = açık uç
    reason      TEXT NOT NULL DEFAULT '',       -- geçmiş tarihli düzeltmenin gerekçesi
    created_at  TEXT NOT NULL,
    created_by  TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL,
    updated_by  TEXT NOT NULL DEFAULT '',
    revision    INTEGER NOT NULL DEFAULT 0
);

-- Tek satırlık kalıcı sayaç: her grup yazmasında +1.
CREATE TABLE IF NOT EXISTS mod_bbd_students_group_rev (
    id       INTEGER PRIMARY KEY CHECK (id = 1),
    revision INTEGER NOT NULL DEFAULT 0
);

-- Denetim izi: grup ve üyelik değişikliklerinin kim/ne zaman/ne kaydı.
-- Öğrenci kartındaki "grup geçmişi" üyelik satırlarından okunur; bu tablo
-- "kim değiştirdi, neden" sorusunun cevabıdır.
CREATE TABLE IF NOT EXISTS mod_bbd_students_group_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL,
    actor      TEXT NOT NULL DEFAULT '',
    action     TEXT NOT NULL,
    group_id   TEXT NOT NULL DEFAULT '',
    revision   INTEGER NOT NULL DEFAULT 0,
    detail     TEXT NOT NULL DEFAULT ''        -- JSON özet
);
