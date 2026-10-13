-- Kantin Geçidi — kantinden gelen olayların tekilleştirme kutusu
-- (bbd-akademi docs/integrations/km-merkezi-senkron.md §6.2).
--
-- Kantin olayları hem itilir (POST /api/bbd_canteen_api/v1/events) hem 30 sn'de
-- bir çekilir (GET /api/km/events?after=); aynı olay iki yoldan da gelebilir.
-- `canteen_seq` kantinin `km_events.id`'sidir: ilk gelen işlenir, ikincisi
-- `duplicates`'a sayılır. Çekme imleci MAX(canteen_seq)'tir.
-- Kişisel veri YOK: yalnız sıra, tür, opak öğrenci kimliği ve zaman.

CREATE TABLE IF NOT EXISTS mod_bbd_canteen_api_inbox (
    canteen_seq  INTEGER PRIMARY KEY,
    type         TEXT NOT NULL,
    opaque_id    TEXT,
    created_at   TEXT,
    received_at  TEXT NOT NULL
);
