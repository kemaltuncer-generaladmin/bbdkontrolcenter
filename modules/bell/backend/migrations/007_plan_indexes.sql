-- km: concurrently
-- Zil planı — indeksler (006 göçünün tabloları).
--
-- İşlemsiz göç: merkezde (PostgreSQL) her indeks CONCURRENTLY kipinde açılır,
-- tablo yazmaya kapanmaz. Yerel SQLite dosyayı düz göç gibi uygular.
--
--   mod_bell_call (date, ref) TEKİL — bir günde aynı oturum iki kez çağrılmaz.
--   mod_bell_call (date, at)  — tetik anında "o dakikanın çağrıları" ve durum
--                                ucunun gün toplamları.
--   mod_bell_profile_time (profile_id, time) TEKİL — setin aynı saati iki kez yok.
--
-- Geri alma:
--   DROP INDEX CONCURRENTLY IF EXISTS mod_bell_call_date_ref;
--   DROP INDEX CONCURRENTLY IF EXISTS mod_bell_call_date_at;
--   DROP INDEX CONCURRENTLY IF EXISTS mod_bell_profile_time_slot;

CREATE UNIQUE INDEX IF NOT EXISTS mod_bell_call_date_ref
    ON mod_bell_call (date, ref);
CREATE INDEX IF NOT EXISTS mod_bell_call_date_at
    ON mod_bell_call (date, at);
CREATE UNIQUE INDEX IF NOT EXISTS mod_bell_profile_time_slot
    ON mod_bell_profile_time (profile_id, time);
