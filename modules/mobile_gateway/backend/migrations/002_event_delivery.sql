-- Mobil Geçit — KM → BBD Akademi olay iletimi (bbd-akademi
-- docs/integrations/km-merkezi-senkron.md §3.2; ADR 0029).
--
-- İmleç: tüketici (`akademi`) başına son iletilen outbox `seq`'i ve panel
-- durumu. Platform outbox'unun tüketici kaydı (`km_event_consumer`) aynı
-- `seq`'i budama için tutar; bu tablo geçidin kendi durumudur (K5).
CREATE TABLE IF NOT EXISTS mod_mobile_gateway_event_cursor (
    consumer           TEXT PRIMARY KEY,
    last_seq           INTEGER NOT NULL DEFAULT 0,
    updated_at         INTEGER NOT NULL DEFAULT 0,
    last_success_at    INTEGER,
    last_attempt_at    INTEGER,
    last_error         TEXT,
    state              TEXT NOT NULL DEFAULT 'live'
);

-- Ölü mektup: Akademi'nin tek tek gönderimde de reddettiği (400/422) olay.
-- Zarf kişisel veri taşımaz (A6); yeniden gönderim için saklanır. İmleç
-- olayın üstünden geçer: zehirli olay akışı tıkamaz.
CREATE TABLE IF NOT EXISTS mod_mobile_gateway_event_dead (
    seq              INTEGER PRIMARY KEY,
    event_id         TEXT NOT NULL,
    type             TEXT NOT NULL,
    error_code       TEXT NOT NULL DEFAULT '',
    attempts         INTEGER NOT NULL DEFAULT 1,
    last_attempt_at  INTEGER NOT NULL,
    event            TEXT NOT NULL
);

-- Küçük anahtar/değer: kurum ayarı özeti (`institution.settings_changed`).
CREATE TABLE IF NOT EXISTS mod_mobile_gateway_meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
