# Antivirüs modülü

ClamAV ile sistem taraması, karantina yönetimi ve imza güncelliği takibi.

Motor erişimi bu modüle aittir: `clamdscan` (birincil, daemon üzerinden) ve
`clamscan` (yedek). Şu an tek tüketicisi olduğu için platform yeteneği
yapılmadı; ikinci bir tüketici çıkarsa `km_platform/antivirus/` altına
yükseltilir (ADR 0009).

Bilinmesi gerekenler:
- İlk kurulumda freshclam **~300 MB imza indirir**; bitmeden clamd başlamaz.
  Arayüz bu durumu hata değil "hazırlanıyor" olarak gösterir.
- clamd imzaları bellekte tutar, ~1–2 GB RAM kullanır.
- Tam sistem taraması kök yetkisi ister; `deploy/systemd/` altındaki ayrı
  servis birimi üzerinden çalışır. **Erişilemeyen yollar raporda listelenir;
  atlanan yol varken tarama "temiz" olarak raporlanmaz.**

- Sözleşme: [module.yaml](module.yaml) · Durum: iskelet (`enabled: false`)
- Giriş noktası: `backend/module.py` → `register(ctx)`
