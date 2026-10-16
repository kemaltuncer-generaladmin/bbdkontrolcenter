-- Profil geneli günlük toplam süre (tablet 0.7.6+): öğrencinin bir günde tüm
-- süreli uygulamalarda birlikte geçirebileceği en çok saniye, uygulama başı
-- sınırlara ek. 0 = toplam sınır yok; eski profiller böyle kalır.
ALTER TABLE mod_bbd_tablet_profiles ADD COLUMN daily_total_limit_seconds INTEGER NOT NULL DEFAULT 0;
