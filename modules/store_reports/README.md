# Raporlar

BBD Store'un rapor merkezi: **7 dal, 35 rapor**, birden çok raporu tek PDF'te
birleştiren **rapor paketi**, kaydedilmiş ve zamanlanmış raporlar.

Grup: **BBD Store** (sıra 180) · CSS öneki `rp` · Rapor rafı:
`Raporlar/Mağaza/Satış/<yıl>/<ay>`

## Ekran

`splitView`: solda **hiyerarşik ağaç**, sağda parametre şeridi → KPI → grafik →
tablo → eylem çubuğu.

**Ağaç klavyesi:** `↑↓` gez · `→` dalı aç · `←` dalı kapat (yapraktan dalına
çıkar) · `Enter` seç ve çalıştır · `Space` rapor paketine ekle/çıkar ·
`Home/End` başa/sona. Arama kutusu dalları kendiliğinden açar.

**Eylemler:** `[Ekranda göster]` (parametre şeridinde) · `[PDF]` (önizleme +
yazdırma) · `[CSV]` · `[Yazdır]` (üret → doğrudan CUPS) · `[Kaydet]` ·
`[Zamanla]` · `[Paket PDF]` (iki ya da daha çok yaprak işaretliyken).

Sekmeler: **Rapor · Kaydedilmiş · Zamanlanmış · Geçmiş**.

## Dallar

| Dal | Rapor |
|---|---|
| Satış | Genel özet · Kanal bazlı · Ödeme yöntemi · Saat/gün dağılımı · Sepet analizi · *Terk edilen sepet* |
| Ürün | En çok/az satan · Pareto (ABC) · Stok devir hızı · Tükenme tahmini · Ölü stok · Kategori kırılımı · İade oranı yüksek ürünler |
| Müşteri | Yeni vs tekrar eden · RFM segmentleri · En çok harcayan · Şehir dağılımı · Müşteri grubu kırılımı · Kayıp müşteri |
| Kargo | Taşıyıcı performansı · Teslim edilemeyen · Kargo kâr/zarar · Bölge dağılımı |
| Mali | KDV icmali · Fatura listesi · İade/iptal icmali · POS mutabakatı · Tahsilat özeti |
| Pazarlama | Kupon kullanımı · İndirim maliyeti · *Kampanya ROI* · Deneme Kulübü dönüşümü |
| Sistem | UDİT özeti · Yedek durumu · Bildirim gönderim özeti |

*Eğik* yazılan iki rapor geçitte karşılığı olmadığı için **çalıştırılamaz**;
ağaçta durur ve nedenini yazar (aşağıya bkz.). Bu yapraklar pakete de
alınamaz ve `[PDF]`/`[Yazdır]` onlarda nedeni söyler — fare ile yapılamayanı
klavye (Space) yapabiliyordu ve paket, içi "üretilemedi" notundan ibaret bir
bölümle çıkıyordu.

## Mağazanın yanıt biçimi (canlıda doğrulandı)

Salt okunarak sınandı; varsayım değildir. Her biri SESSİZ bir arızaydı —
hiçbiri istisna atmıyordu.

- **Alan adları camelCase** (`grandTotal`, `createdAt`, `totalItemCount`).
  `pick` adı ayraçsız-küçük harfe indirerek de arar.
- **Para ondalık** (`grandTotal: 2` = ₺2,00). `to_kurus` **Decimal** kullanır;
  float `"1.005"` değerini 100 kuruşa yuvarlıyordu, 101'e değil.
- **Kanal süzgeci iki uçta İKİ AYRI ŞEY ister.**
  `/orders?channel=default` → **0 kayıt**, `channel=1` → 18.
  `/catalog/products?channel=default` → **1421 ürün**, `channel=1` → 0.
  Bu yüzden ayar ikizdir: `channel` (kod, ürün ucuna) ve `channel_id`
  (kimlik, sipariş ucuna). `channel_id` varsayılanı **0**'dır — bilinmeyen
  kimlikle süzmektense hiç süzmemek doğrudur.
- **Sipariş listesindeki `items` SIĞDIR**: `{id, sku, name, qtyOrdered,
  productImage}`. Ürün kimliği, fiyat, tutar, KDV yok. "Kalem var" sayan kod
  detayı hiç okumaz ve bütün ürün/KDV raporları sıfır ciro gösterirdi;
  `usable_items` sığ kalemi yok sayar.
- **Liste satırı para dökümü taşımaz** (ara toplam, kargo, indirim, KDV, iade
  yalnız `GET /orders/{id}` detayında). Satır `shallow` işaretlenir ve rapor
  "sıfır" değil **"bilinmiyor"** yazar.
- **Adres `addresses[]` dizisindedir** (`addressType`); `shipping_address`
  diye bir alan yok. Liste ucunda adres hiç yok, `location`
  (`"Selçuklu, 42, TR"`) özeti var — şehir oradan çözülür.
- **Müşteri grubu `customer.group.name`** altındadır ve liste ucunda hiç
  yoktur; grup, `customers` listesiyle eşleştirilerek doldurulur.
- **Ürün listesinde `categories` `null`'dır**, kategori düz `categoryName`
  alanındadır. Kategori ağacı (`categories/tree`) **iç içedir** ve tepede tek
  bir "Kök" düğümü vardır; açılır düzleştirilerek doldurulur.
- **Fatura listesi sayısal `orderId` vermez**, `orderIncrementId` verir.
- `per_page` **50'ye kırpılır**, `meta` camelCase, `links` **boş**.
- `date_from`/`date_to` sipariş, fatura, iade ve işlem uçlarında **gerçekten
  uygulanır** (sınandı); yine de aralık yerelde bir kez daha uygulanır.
- **`/api/admin/bbd/*` uçlarının tamamı bugün 404.** Kargo ve Sistem dalları
  bu yüzden "veri alınamadı" der; geçit hatayı `bbd_endpoint_missing` koduyla
  anlaşılır bir mesaja çeviriyor ve ağacın gerisi çalışmaya devam ediyor.

## Kararlar

**Rakam istemcide hesaplanmaz.** Ekrandaki sayı, PDF'teki sayı ve CSV'deki
sayı sunucudaki tek üreteçten (`builders.py`) gelir. İki ayrı hesap zamanla
ayrışır; rapor ekranında bu, güvenin tamamen kaybolması demektir.

**Hata yaprak bazlıdır.** Veri kümeleri (sipariş, ürün, kargo…) tek tek
toplanır ve her birinin hatası kendi adıyla saklanır. Kargo ucu düşse bile
Satış raporları çalışır. Rapor paketinde patlayan bölüm PDF'te *"üretilemedi:
…"* notuyla yerinde durur, paketin gerisi basılır.

**Veri kümesi bir kez çekilir.** Pakette on yaprak seçildiğinde her yaprak
kendi siparişlerini çekseydi aynı liste on kez inerdi; mağaza dakikada 60
istek kabul ediyor.

**Yöntem saklanmaz.** Bir hesap tahmine dayanıyorsa (iade kalem kırılımı,
"yeni müşteri" penceresi, kategori cirosunun bölünmemesi) rapor bunu **Yöntem**
kartında ve PDF'in altında yazar. Sessizce yaklaşık rakam göstermek, yanlış
rakam göstermektir.

**Kalem ayrıntısı tavanlıdır.** Ürün bazlı raporlar sipariş kalemlerini ister;
Bagisto sipariş listesi kalem taşımayabiliyor ve kalem için sipariş tek tek
okunuyor. `detail_scan_limit` bunun tavanıdır — aşılırsa rapor "şu kadar
sipariş taranabildi" notuyla çıkar.

**Silme yok.** Kaydedilmiş rapor arşivlenir (`active = 0`), satır durur;
arşivleme gerekçe ister ve gerekçe backend'de de doğrulanır.

## Uç noktalar

| Yöntem | Yol | İzin |
|---|---|---|
| GET | `/api/store_reports/tree` | `store_reports.view` |
| GET | `/api/store_reports/reference` | `store_reports.view` |
| POST | `/api/store_reports/run` | `store_reports.view` |
| GET | `/api/store_reports/runs` | `store_reports.view` |
| POST | `/api/store_reports/preview` | `store_reports.view` |
| POST | `/api/store_reports/print` | `store_reports.view` |
| GET | `/api/store_reports/printer` | `store_reports.view` |
| POST | `/api/store_reports/export` | `store_reports.view` |
| GET · POST | `/api/store_reports/saved` | `view` · `manage` |
| POST | `/api/store_reports/saved/{id}/archive` | `store_reports.manage` |
| GET · POST | `/api/store_reports/schedules` | `view` · `manage` |
| POST | `/api/store_reports/schedules/{id}/toggle` | `store_reports.manage` |

`preview` gövdesindeki `kind` bir yaprak anahtarı ya da `package` olabilir;
rapor paketi de aynı önizleme/yazdırma zincirinden geçer.

## Tablolar

| Tablo | Ne tutar |
|---|---|
| `mod_store_reports_saved` | Kaydedilmiş rapor tanımı (yapraklar + parametreler) |
| `mod_store_reports_schedules` | Zamanlanmış rapor (haftalık/aylık, saat, son çalışma) |
| `mod_store_reports_runs` | Üretim izi: ne üretildi, **kim istedi**, nereye yazıldı, neden patladı |

Üretim izindeki `actor` uçtan gelen kullanıcıyla doldurulur; zamanlayıcının
bastığı rapor `Zamanlanmış: <ad>` diye imzalanır. İmza olmasaydı "Geçmiş"
sekmesinde elle alınan raporla gece üretilen rapor ayırt edilemezdi. Sekme
yaprağı ham anahtarla değil (`sales_summary`) ağacın Türkçe etiketiyle yazar.

Rapor **rakamları** saklanmaz: mağazada bir sipariş düzeltilince kopya sessizce
yanlış rakam gösterirdi.

## Zamanlanmış rapor

`module.yaml → tasks` içindeki `backend.tasks:run_scheduled` 15 dakikada bir
vakti gelen tanımları arar. Saat geçtiyse iş **yine çalışır** (makine 07:00'de
kapalıysa açılışta üretilir); aynı gün ikinci kez basılmaz. Çekirdeğin
zamanlayıcısı henüz yazılmadığı için koşucu hem bağlamlı hem bağlamsız çağrıya
dayanır.

## `store.api` ihtiyacı

Kullanılanlar: `orders` · `order` · `products` · `customers` · `invoices` ·
`refunds` · `transactions` · `bbd_shipments` · `bbd_audit` · `bbd_backups` ·
`bbd_notifications` · `bbd_trial_members` · `bbd_reconciliation` ·
`bbd_carriers` · `category_tree` · `snapshot` · `state`.

Hepsi `modules/store_api/backend/client.py` içinde gerçekten vardır.

Eksik olan iki uç — ilgili yapraklar **devre dışı** ve nedeni ekranda yazıyor:

- `abandoned_carts(filters)` → **Satış · Terk edilen sepet**. Bugün yalnız
  `customer_cart_items(customer_id)` var; 1.400 müşteriyi tek tek taramak
  dakikada 60 istek sınırına takılır.
- `bbd_campaign_stats(filters)` → **Pazarlama · Kampanya ROI**. Kampanya
  maliyeti ve sipariş eşlemesi mağazada tutulmuyor; ROI ancak uydurma bir
  maliyetle hesaplanabilirdi.

## Test

```bash
.venv/bin/python -m pytest modules/store_reports/tests -q
.venv/bin/ruff check modules/store_reports
```

- `test_store_reports_analytics.py` — saf hesaplar, ağaç bütünlüğü, göç adları.
- `test_store_reports_service.py` — iş kuralları, K7, kanal süzgeci, arşivleme.
- `test_store_reports_live_shape.py` — **canlı gövde biçimi.** İçindeki
  sözlükler `GET /api/admin/orders`, `/orders/20`, `/catalog/products`,
  `/catalog/categories/tree`, `/customers`, `/invoices` ve `/transactions`
  yanıtlarından kısaltılarak alınmıştır; alan adları ve tipler olduğu gibidir.
  Modülün kendi uydurduğu snake_case veriye karşı geçen test hiçbir şey
  kanıtlamıyordu.

Ağa çıkılmaz; `store.api` taklit edilir.
