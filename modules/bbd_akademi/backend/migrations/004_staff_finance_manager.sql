-- FİNANS YÖNETİCİSİ personel ayarı (iş 83, Akademi README §14 madde 143).
--
-- Kurum finansına girişte istenen SMS onay kodu bu bayrağı taşıyan personelin telefonuna gider (rol değil,
-- personel ayarı). KM tek kaynaktır: bayrak `PUT /v1/admin/staff/sync` yükünde `financeManager` olarak itilir
-- (`contract_features.STAFF_FINANCE_MANAGER` açıkken).
--
-- EKLEYİCİ VE CANLIDA GÜVENLİ: varsayılanlı tek sütun; mevcut satırlar "finans yöneticisi değil" olarak kalır.

ALTER TABLE mod_bbd_akademi_staff ADD COLUMN finance_manager INTEGER NOT NULL DEFAULT 0;
