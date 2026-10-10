-- Tablet her uygulama için görünen bir ad ister: adsız kurallar paket adını
-- alır. Yalnız boşluktan oluşan profil adları da okunur bir ada döner.
UPDATE mod_bbd_tablet_profile_apps SET app_name = package_name
    WHERE TRIM(app_name) = '';
UPDATE mod_bbd_tablet_profiles SET name = 'Adsız profil'
    WHERE TRIM(name) = '';
UPDATE mod_bbd_tablet_profiles SET name = TRIM(name)
    WHERE name <> TRIM(name);
