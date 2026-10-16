# modules/

İş modülleri. Her klasör bir **silinebilir özelliktir** ve kendi dikey dilimini
taşır: backend + arayüz paneli + göçler + ayar + çeviri + testler.
Klasörü silmek özelliği tümüyle kaldırır (ADR 0005).

Buraya **altyapı konmaz.** `ssh`, `database`, `printer`, `audio`,
`secrets`, `events.outbox` platform yeteneğidir ve `backend/src/km_platform/`
altındadır (ADR 0006).

## Alanlar (ADR 0033)

Her modül `module.yaml` → `domain` ile tam olarak bir **alan servisine**
bağlıdır ve `data_classification` ile taşıdığı en yüksek veri sınıfını ilan
eder. Katalog: [../docs/architecture/domains.yaml](../docs/architecture/domains.yaml).
Bu tablo 16.10.2026'da yazıldı; güncel durum **tek yerden** okunur: modülün
kendi `module.yaml` dosyası (`enabled`, `domain`) ve üretilmiş
[../docs/architecture/module-graph.md](../docs/architecture/module-graph.md)
(`python3 tools/module-graph.py --write`).

| Alan | Modüller |
|---|---|
| **Platform ve kurumsal araçlar** (`platform`) | `antivirus` Antivirüs · `print` Çıktı Merkezi |
| **Akademi** (`academy`) | `bbd_akademi` BBD Akademi |
| **Öğrenci ve veli** (`students`) | `bbd_sms` SMS Sistemi · `bbd_students` Öğrenci Yönetimi |
| **Kantin** (`canteen`) | `bbd_bulk_sale` Toplu Satış Ekranı · `bbd_canteen_api` Kantin Geçidi *(ekransız)* · `bbd_canteen_backups` Kantin Yedekleri · `bbd_canteen_devices` Kantin Cihazları · `bbd_canteen_products` Kantin Ürünleri · `bbd_canteen_reports` Kantin Raporları · `bbd_lunch` Öğle Yemeği |
| **Ödeme talepleri** (`payments`) | `bbd_payment_request` Ödeme Talebi |
| **Öğrenci tableti** (`tablet`) | `bbd_tablet` Öğrenci Tablet Yönetimi |
| **Zil ve anons** (`bell`) | `bbd_class_schedule` Ders Takvimi · `bell` Zil Sistemi |
| **Mobil geçit** (`gateway`) | `mobile_gateway` Mobil Geçit *(ekransız)* |
| **Mağaza (BBD Store)** (`store`) | `store_ai` Dahili AI Ekranları *(kapalı)* · `store_api` Mağaza Geçidi *(ekransız)* · `store_backups` Manuel Yedekleme *(kapalı)* · `store_bundles` Setler · `store_cms` CMS *(kapalı)* · `store_customers` Müşteriler · `store_dashboard` Kontrol Paneli · `store_home_media` Ana Ekran Görselleri *(kapalı)* · `store_invoices` Fatura *(ekransız)* · `store_notifications` Bildirimler *(ekransız)* · `store_orders` Siparişler · `store_payment_gateway` Link ile Ödeme *(kapalı)* · `store_products` Ürünler · `store_promotions` Promosyonlar *(kapalı)* · `store_refunds` İadeler · `store_reports` Raporlar *(kapalı)* · `store_requests` Talepler *(kapalı)* · `store_shipping` Kargo Yönetimi · `store_tax` Vergilendirme *(kapalı)* · `store_trial_club` Deneme Kulübü *(kapalı)* · `store_udit_logs` UDİT — İşlem Kayıtları *(ekransız)* |
| **BLD** (`bld`) | `bld_api` BLD Geçidi *(kapalı)* · `bld_cms` Site İçeriği *(kapalı)* · `bld_customers` Müşteriler *(kapalı)* · `bld_dashboard` Kontrol Paneli *(kapalı)* · `bld_invoices` Faturalar *(kapalı)* · `bld_kds` KDS Yönetimi *(kapalı)* · `bld_manual_order` Elle Sipariş *(kapalı)* · `bld_menu` Menü Yönetimi *(kapalı)* · `bld_notifications` Bildirimler *(kapalı)* · `bld_orders` Sipariş Yönetimi *(kapalı)* · `bld_products` Ürün Yönetimi *(kapalı)* · `bld_sales_settings` Satış Ayarları *(kapalı)* · `bld_sms` SMS Paneli *(kapalı)* · `bld_status_monitor` Durum Monitörü *(kapalı)* · `bld_subscriptions` Abonelikler *(kapalı)* |

53 modül: 28 açık, 25 kapalı (10 mağaza + 15 BLD, kullanıcı kararı 10.10.2026).
"Ekransız" modül menüde görünmez ama arka ucu çalışır (geçitler, sipariş
ekranlarının dayandığı fatura/bildirim/işlem kaydı). `bbd_students` ekranı
menüde çizilmez (`replacedBy: bbd_akademi.students`, ADR 0031) — öğrenci
yönetimi Akademi → Öğrenciler'dedir; yetenekleri yüklenmeye devam eder.

## Sınırlar

- Modül başka modülün kodunu import etmez (K3), yalnız `km_sdk` görür (K2),
  yalnız kendi `mod_<id>_*` tablolarına dokunur (K5), başka modülün
  `/api/<id>` önekini (panelde de) çağırmaz.
- Başka modülle — aynı alanda da olsa — yetenek (`provides`/`consumes`),
  olay (`events`, kalıcı olanı `events.outbox`) ya da panel arayüz yeteneği
  (`ctx.capability`) üzerinden konuşur.
- Kapı: `python3 tools/check-boundaries.py` ve
  `tests/integration/test_alan_sinirlari.py`. Bugünkü bilinen ihlaller
  gerekçe ve azaltma planıyla `tools/boundary-exceptions.yaml`'dadır; yenisi
  eklenemez.
- Birden çok modülde birebir aynı yardımcı `km_sdk`'ye taşınır
  (`as_int`, `to_kurus`, `print_report_file`, …).

Yeni modül: `tools/module-template/` kopyalanır (`domain` ve
`data_classification` zorunlu). Kılavuz:
[../docs/module-guide.md](../docs/module-guide.md)
