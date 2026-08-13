# Baskı Yönetimi modülü

Yazıcı listesi, kuyruk yönetimi, baskı işleri ve iş geçmişi.

CUPS erişimi bu modüle ait değildir — `printer` platform yeteneğinden geçer
(K4). Modül kuyruğu, yetkilendirmeyi ve arayüzü yönetir.

- Sözleşme: [module.yaml](module.yaml) · Durum: iskelet (`enabled: false`)
- Giriş noktası: `backend/module.py` → `register(ctx)`
