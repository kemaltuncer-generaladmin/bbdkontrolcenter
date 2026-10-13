-- Mobil Geçit — OTP SMS teslim izi (sms-kale, 2026-10-13).
--
-- Kantin Netgsm görev kimliğini (jobId) ve hattı (`otp` | `normal`) döndürür;
-- geçit bunu kaydeder ve `GET /sms/otp/{jobId}` ile teslim durumunu kantinden
-- (Netgsm raporu) okuyup saklar. Telefon yine MASKELİ; kod ve metin YOK.
-- delivery: '' (gönderilmedi) | pending | delivered | failed
ALTER TABLE mod_mobile_gateway_sms_log ADD COLUMN job_id TEXT NOT NULL DEFAULT '';
ALTER TABLE mod_mobile_gateway_sms_log ADD COLUMN channel TEXT NOT NULL DEFAULT '';
ALTER TABLE mod_mobile_gateway_sms_log ADD COLUMN delivery TEXT NOT NULL DEFAULT '';
ALTER TABLE mod_mobile_gateway_sms_log ADD COLUMN delivered_at INTEGER;
ALTER TABLE mod_mobile_gateway_sms_log ADD COLUMN checked_at INTEGER;
CREATE INDEX IF NOT EXISTS mod_mobile_gateway_sms_log_job
    ON mod_mobile_gateway_sms_log (job_id);
