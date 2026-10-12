-- km: concurrently
-- Kutu budaması (`received_at`) için. Geri alma:
--   DROP INDEX CONCURRENTLY IF EXISTS mod_bbd_canteen_api_inbox_received;
CREATE INDEX IF NOT EXISTS mod_bbd_canteen_api_inbox_received
    ON mod_bbd_canteen_api_inbox (received_at);
