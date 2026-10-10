-- Komut iptali, öğrenci verisi silme ve geçmiş saklama.
-- İptal edilen komut tablete bir daha gitmez. Tablete iletilmiş komutun
-- sonradan gelen sonucu yine yazılır (tablet komutu aldığı anda çalıştırır).
ALTER TABLE mod_bbd_tablet_commands ADD COLUMN cancelled_at TEXT;
ALTER TABLE mod_bbd_tablet_commands ADD COLUMN cancelled_by TEXT NOT NULL DEFAULT '';

-- Günlük saklama temizliği ve öğrenci verisi silme tam tablo taramasız çalışsın.
CREATE INDEX IF NOT EXISTS mod_bbd_tablet_usage_batches_accepted
    ON mod_bbd_tablet_usage_batches (accepted_at);
CREATE INDEX IF NOT EXISTS mod_bbd_tablet_usage_batches_student
    ON mod_bbd_tablet_usage_batches (student_id);
CREATE INDEX IF NOT EXISTS mod_bbd_tablet_commands_expires
    ON mod_bbd_tablet_commands (expires_at);
