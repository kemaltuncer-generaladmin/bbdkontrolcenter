# Zil Sistemi modülü

Kurum zilinin zamanlanması, çalınması, takvim ve istisna yönetimi.

Ses donanımına erişim bu modüle ait değildir — `audio` platform yeteneğinden
geçer (K4). Uzak makinede çalma gerekirse `ssh` yeteneği kullanılır.

- Sözleşme: [module.yaml](module.yaml) · Durum: iskelet (`enabled: false`)
- Giriş noktası: `backend/module.py` → `register(ctx)`
