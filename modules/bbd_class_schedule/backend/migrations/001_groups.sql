-- Ders Takvimi — modülün KENDİ tablosu (K5).
--
-- Veri bugüne dek tarayıcı belleğindeydi (localStorage): makine değişince
-- kaybolurdu ve zil sistemi onu senkron okumak zorundaydı. Artık kalıcı.
--
-- Tek belge olarak saklanır: gruplar birlikte anlam taşır, panel de tümünü
-- birden yazıyor. Satır satır normalize etmek kazanç sağlamaz, karmaşıklık ekler.

CREATE TABLE IF NOT EXISTS mod_bbd_class_schedule_document (
    id         INTEGER PRIMARY KEY CHECK (id = 1),   -- tek satır
    payload    TEXT NOT NULL,                        -- {version, groups:[…]}
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT ''
);
