# modules/

İş modülleri. Her klasör bir **silinebilir özelliktir** ve kendi dikey dilimini
taşır: backend + arayüz paneli + göçler + ayar + çeviri + testler.
Klasörü silmek özelliği tümüyle kaldırır (ADR 0005).

Buraya **altyapı konmaz.** `ssh`, `database`, `printer`, `audio` platform
yeteneğidir ve `backend/src/km_platform/` altındadır (ADR 0006).

| Modül | Durum |
|---|---|
| `bell/` — Zil Sistemi | iskelet (`enabled: false`) |
| `print/` — Baskı Yönetimi | iskelet (`enabled: false`) |
| `antivirus/` — Antivirüs (ClamAV) | iskelet (`enabled: false`) |

Planlanan: BLD ürün yönetimi ve diğer BBD/BLD iş modülleri — istendiğinde açılır.

Yeni modül: `tools/module-template/` kopyalanır. Kılavuz:
[../docs/module-guide.md](../docs/module-guide.md)
