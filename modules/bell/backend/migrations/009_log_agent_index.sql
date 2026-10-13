-- km: concurrently
-- Zil Sistemi — günlükte komut numarasına göre arama (008 göçünün sütunu).
--
-- Ajanın cevabı (`lastAck`) gelince o komut numaralı satırlar güncellenir.
-- Günlük yılda on binlerce satıra varır; tarama her durum okumasında olmasın.
--
-- Geri alma:
--   DROP INDEX CONCURRENTLY IF EXISTS mod_bell_log_command;

CREATE INDEX IF NOT EXISTS mod_bell_log_command
    ON mod_bell_log (command_id);
