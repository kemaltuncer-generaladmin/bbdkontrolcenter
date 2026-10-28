-- Deneme Kulübü — BBDSTORE sipariş izleyicisi (iş 81-km-kulup, 2026-10-28).
--
-- KULLANICI (28.10): "pos bbdstore üzerinden ödeme … bbdstore ile iletişimde kalırız. oradan
-- gereken tüm bilgiler için de tek kullanımlık form göndeririz link ile sms ile kullanıcı
-- doldurunca aboneliği otomatik başlar". İzleyici 2 dk'da bir bbd-store'un deneme kulübü
-- siparişlerini (`GET /api/admin/bbd/deneme-kulubu/registrations?paid=1&updatedSince=…`) okur;
-- ödenmiş, iptal/iade edilmemiş her sipariş için Akademi'de BİR davet açar
-- (`admin_createClubInvite`, source store, storeOrderRef = sipariş no). Sonradan iptal/iade
-- edilen siparişin daveti iptal edilir (`admin_cancelClubInvite`).
--
-- `club_store_order` — işlenen siparişler (tekrar yok; Akademi de storeOrderRef'i tekil tutar):
--   order_number  bbd-store sipariş no (increment_id) — Akademi `storeOrderRef`
--   status        'invited' (davet açıldı) | 'cancelled' (davet iptal edildi) |
--                 'invalid_phone' (fatura telefonu cep değil — elle kayıt) |
--                 'failed' (Akademi reddetti / ulaşılamadı — her turda yeniden denenir) |
--                 'skipped' (davet açılmadan iptal/iade edildi)
--   invite_id     Akademi davet kimliği ('' = yok)
--   amount_minor  sipariş tutarı (kuruş)
--   error         son hata (Türkçe, kişisel veri yok)
--   attempts      başarısız deneme sayısı
--   order_updated_at  mağazadaki `updatedAt` (ISO)
--   created_at / updated_at  ISO damga
-- Ad / telefon YAZILMAZ (davette, Akademi'de durur).
--
-- `club_store_watch` — tek satır (id = 1): imleç ve son tur durumu (ekrandaki uyarı).

CREATE TABLE IF NOT EXISTS mod_bbd_akademi_club_store_order (
    order_number     TEXT PRIMARY KEY,
    status           TEXT NOT NULL,
    invite_id        TEXT NOT NULL DEFAULT '',
    amount_minor     INTEGER NOT NULL DEFAULT 0,
    error            TEXT NOT NULL DEFAULT '',
    attempts         INTEGER NOT NULL DEFAULT 0,
    order_updated_at TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_bbd_akademi_club_store_order_status
    ON mod_bbd_akademi_club_store_order (status, updated_at);

CREATE TABLE IF NOT EXISTS mod_bbd_akademi_club_store_watch (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    cursor        TEXT NOT NULL DEFAULT '',
    state         TEXT NOT NULL DEFAULT '',
    message       TEXT NOT NULL DEFAULT '',
    last_run_at   TEXT NOT NULL DEFAULT '',
    last_ok_at    TEXT NOT NULL DEFAULT '',
    invited_total INTEGER NOT NULL DEFAULT 0
);
