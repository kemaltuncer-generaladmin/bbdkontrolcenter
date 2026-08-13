# Müşteriler

BBD Store müşteri yönetimi: liste, RFM segmenti, müşteri künyesi, **yorum
moderasyonu** ve KVKK işlemleri.

Grup: **BBD Store** · CSS öneki: `cu` · Rapor rafı:
`Raporlar/Mağaza/Müşteri/<yıl>/<ay>` · Sağlar: `store.customer.card`

## Ne yapar

| Alan | Davranış |
|---|---|
| Liste | Ad/e-posta/telefon, grup ve durum süzgeçleri **sunucu tarafında**. Segment, harcama, sipariş sayısı ve tarih süzgeçleri mağazada YOK → nüfus bir kez taranır ve **bellekte** süzülür. |
| Mini KPI | Toplam · Yeni (30g) · Tekrar eden oranı · Ortalama yaşam boyu değer. Ayrı uçtan gelir ki liste beklemesin. |
| Segment | Şampiyon · Sadık · Yeni · Riskli · Uykuda · Kayıp · Hiç sipariş vermemiş. Çip şeridinde sayaçlarıyla. |
| Çekmece | 8 sekme: Özet (6 aylık harcama sparkline) · Siparişler · Adresler · İadeler · **Yorumlar** · İzin geçmişi · Notlar · İşlem geçmişi. |
| Yorumlar | Üst sekmede tüm mağazanın moderasyon kuyruğu: **puan süzgeci (1–5 çoklu)**, onayla/reddet/spam, mağaza yanıtı, **bekleyen sayacı**, puan dağılımı. |
| KVKK | Veri paketi isteme (mağaza üretir) · anonimleştirme (**ayrı izin**, geri alınamaz). |
| Ayarlar | E-posta doğrulama, yeni kayıtta bülten aboneliği, varsayılan grup (KOD ile), KVKK modülü ve aydınlatma metni. **Şu an okunamıyor** — aşağıdaki "Bilinen engel". |
| Çıktı | Segment raporu PDF · yorum özeti PDF · müşteri karnesi PDF · görünen sayfa/seçim CSV · tüm nüfus CSV. |

**Ayrı `store_reviews` modülü yoktur** — yorum müşterinin sesidir, müşteri
ekranında yönetilir. **Ayrı `store_settings` ekranı da yoktur** — müşteri
kaydı ve KVKK ayarları buranın Ayarlar sekmesinde durur.

## Ne yapmaz — ve neden

- **Müşteri silmez.** Silinen müşterinin siparişi, faturası ve iadesi öksüz
  kalır; mali kayıt geçmişe dönük bozulur. Her yerde pasifleştirme (ADR 0012).
- **Yorum silmez.** Reddedilen yorum vitrinde görünmez ama kaydı durur.
- **Nüfus taramasını diske yazmaz.** Taranan satırlar ad, e-posta ve telefon
  taşır; ikinci bir kopya ikinci bir KVKK yüküdür. Tarama kısa ömürlü bellek
  önbelleğindedir (`segment_scan_ttl`, varsayılan 300 sn).
- **Elle anonimleştirme yapmaz.** "Ad ve e-postayı üzerine yaz" yolu sipariş ve
  fatura kayıtlarındaki kişisel veriyi yerinde bırakır ve ekran işi bitirdiğini
  sandırır. Anonimleştirme mağazanın **KVKK talebi** üzerinden yürür; açık talep
  yoksa düğme çalışmaz ve nedenini söyler.
- **Şifre sıfırlama bağlantısı gönderemez, bülten aboneliğini tek tıkla
  değiştiremez.** Geçitte bu uçlar yok; düğmeler kapalı ve nedeni yazılı —
  sessizce patlamıyorlar.

## Tuzaklar

Hepsinin karşılığı `backend/analytics.py` içinde bir fonksiyon ve
`tests/test_store_customers_analytics.py` içinde adı tuzağı söyleyen bir test.

1. **RFM nüfusa görelidir** → mutlak eşik kullanılır (`config`). Nüfus dilimi
   her ekran açılışında tam tarama isterdi; ayrıca eşik nüfusla kayınca
   dönemler karşılaştırılamaz olurdu.
2. **Sipariş sayısı/harcama alanı sürüme göre değişiyor** → çok adlı okuma;
   bulunamazsa `None`, **sıfır uydurulmaz**. Sıfır uydurmak siparişi olan
   müşteriyi "hiç sipariş vermemiş" listesine düşürürdü.
3. **Ortalama sepet sıfıra bölünür** → sipariş yoksa `None`.
4. **Laravel tanımadığı süzgeci sessizce yok sayar** → müşterinin siparişi,
   iadesi ve yorumu çekildikten sonra süzgecin uygulandığı DOĞRULANIR.
   CANLIDA DOĞRULANDI: `/api/admin/orders?customer_id=12` süzgeci
   **uygulamıyor**, 17 siparişin tamamını döndürüyor. Bu yüzden liste satırdaki
   `customerId` ile burada süzülür ve ekran süzmenin nerede yapıldığını söyler;
   kimlik hiç yoksa satır GÖSTERİLMEZ. Başkasının siparişini bu müşteriye ait
   göstermek en kötü hata sınıfıdır.
   Aynı sebeple "e-postası doğrulanmamış" süzgeci sunucuya gönderilmez
   (mağaza `status` alanını yalnız 0/1 bilir) — taramada uygulanır.
5. **Bagisto'da yorum "spam" durumu YOK** → spam, mağazada *reddedildi*'ye
   karşılık gelir ve ayrıca yerel etikete yazılır; operatörün kararı kaybolmaz.
6. **Yorum ucu tek puan süzgeci alıyor** → tek puan sunucuya gider, çoklu puan
   sayfada süzülür ve ekran bunu söyler.
7. **`str.casefold()` Türkçe'de bozuk** → "İzmir" araması `izmir` yazan
   personeli sonuçsuz bırakıyordu; `fold()` kitin `foldText` tablosunu kullanır.
8. **"Onay yok" ile "onay bilgisi tutulmuyor" aynı şey değil** → izin bayrağı
   bulunamazsa `bilinmiyor` gösterilir.
9. **Bulunmayan `core_config` anahtarına yazmak** etkisiz bir satır açar ve
   "kaydettim" yanılgısı üretir → anahtar bulunamazsa alan hiç gösterilmez.
10. **Para float ile çevrilirse bir kuruş kaybolur** → her yerde `Decimal`.
11. **Yanıt camelCase, istek snake_case.** Mağazanın yönetici API zarfı ÇIKTIYI
    camelCase'e çeviriyor (`createdAt` · `totalOrders` · `grandTotal` ·
    `subscribedToNewsLetter`), sorgu ve gövde tarafında ise camelCase sessizce
    yok sayılıyor (canlıda `customerGroupId` süzgeci uygulanmadı,
    `customer_group_id` uygulandı). `analytics.pick` her adı iki biçimde de
    dener; yazma gövdeleri snake_case kalır.
12. **Müşteri kaydında son sipariş tarihi ve şehir YOK**, liste ucunda sipariş
    sayısı ve harcama da yok (detay ucu yalnız ilk ikisini veriyor). Üçü
    olmadan RFM segmenti hesaplanamaz; bu yüzden siparişler bir kez taranıp
    müşteriye göre toplulaştırılır (`order_stats`). Tarama yapılamazsa ya da
    tavana dayanırsa sayı **uydurulmaz**, sütun boş kalır ve ekran nedenini
    söyler.

## Bilinen engel — Ayarlar sekmesi

`GET /api/admin/configuration?slug=…` canlıda **tek elemanlı liste** döndürüyor
(`[{slug, channel, locale, values:{…}}]`); `store_api` geçidinin tekil-kayıt
yolu (`_item`) listeyi açamayıp boş sözlük veriyor. Bu yüzden mağaza ayarları
şu an okunamıyor ve yazılamıyor. Ekran bunu **açıkça** söyler ve hiçbir ayar
gönderilmez; "anahtar bulunamadı" gibi yanlış bir teşhis koymaz. Çözüm
geçittedir: `configuration` metodu tek elemanlı listeyi açmalı. Anahtar adları
canlıdan okunup düzeltildi (`customer.settings.email.verification`,
`customer.settings.create_new_account_options.default_group` — değeri grup
KODUDUR, kimlik değil).

"Üyeliksiz alışveriş" ayarı bu ekrandan **çıkarıldı**: canlıda
`customer.settings` altında böyle bir anahtar yok, Bagisto onu `sales.checkout`
bölümünde tutuyor ve orası ödeme/sipariş ekranlarının işidir.

## Uçlar

`/api/store_customers` öneki altında, 24 uç. Hepsi `requires(...)` taşır (K9).

Okuma: `GET /customers` · `GET /customers/{id}` · `/orders` · `/addresses` ·
`/returns` · `/notes` · `/consents` · `GET /overview` · `GET /reference` ·
`GET /reviews` · `GET /audit` · `GET /settings` · `GET /printer`

Yazma: `PUT /customers/{id}` · `POST /customers/status` ·
`POST /customers/{id}/notes` · `POST /customers/{id}/gdpr-export` ·
`POST /customers/{id}/anonymize` · `POST /reviews/status` ·
`POST /reviews/{id}/reply` · `POST /settings` · `POST /preview` ·
`POST /print` · `POST /export`

## İzinler

| Anahtar | Ne açar | Varsayılan roller |
|---|---|---|
| `store_customers.view` | Ekran, künye, yorum listesi, rapor, CSV | admin, bbd_staff |
| `store_customers.manage` | Künye, grup, not, ayarlar | admin, bbd_staff |
| `store_customers.deactivate` | Pasifleştirme (silme yok) | admin |
| `store_customers.reviews` | Yorum moderasyonu ve mağaza yanıtı | admin, bbd_staff |
| `store_customers.gdpr` | KVKK veri paketi | admin |
| `store_customers.anonymize` | Anonimleştirme — **geri alınamaz** | admin |

## Yerel tablolar

Yalnız Bagisto'da **karşılığı olmayan** veri:

- `mod_store_customers_audit` — yazma gerekçesi ve sonucu
- `mod_store_customers_review_flags` — yorumun spam etiketi + yanıt kopyası
- `mod_store_customers_consent` — izin değişikliği geçmişi (KVKK)

Müşteri, adres, sipariş ve yorum verisi **kopyalanmaz**.

## Sağladığı yetenek

`store.customer.card` — `card(customer_id)` tam künye, `summary(customer_id)`
tek satırlık özet. Sipariş, iade, talep ve fatura ekranları müşteriyi buradan
sorar; kendi müşteri isteğini atmaz ve bu modülü import etmez (K3). Salt okunur.

## Yayınladığı olaylar

`store.customer.anonymized` · `store.review.moderated`

## Testler

```bash
.venv/bin/python -m pytest modules/store_customers/tests -q
.venv/bin/ruff check modules/store_customers
```
