# Link ile Ödeme

Uzaktaki müşteriden karttan tahsilat. Personel formu doldurur → ön izleme →
gerekçeli onay → ödeme bağlantısı → SMS → müşteri kendi kartıyla öder →
sipariş ve fatura mağazada oluşur → yoklamayla durum görünür.

Grup: **BBD Store** · CSS öneki: `pg` · Rapor rafı:
`Raporlar/Mağaza/Finans/<yıl>/<ay>`

## Bu ekran POS izleme değildir

Banka mutabakatı, terminal/anahtar ayarı, taksit-komisyon matrisi ve kart
ailesi yönlendirmesi burada **yoktur**. Burada tek bir iş vardır: uzaktaki
müşteriden para tahsil etmek. Kart numarası, CVV ve son kullanma tarihi bu
ekrana **hiç girilmez** — girilseydi ekran PCI kapsamına girer ve personelin
gördüğü yerde saklanmaması gereken veri saklanırdı.

## Sekmeler

| Sekme | Ne yapar |
|---|---|
| **Yeni Tahsilat** | Form + canlı ön izleme: tutar kırılımı, gidecek SMS'in birebir metni, parça/kredi sayacı, eksikler listesi |
| **Talepler** | Durum eşlemeli liste, süzgeç, detay çekmecesi (olay zinciri, POS denemeleri, yeniden SMS, iptal) |
| **Elden Kapatma** | Havale/nakit beyanı: tahsilat karttan geçmeden kapanır, mağazaya ödeme kaydı yazılır |
| **SMS Şablonu** | Yer tutucu çipleri, canlı parça sayacı, **Sadeleştir** düğmesi |

## Üç kural (kodun tamamı bunların etrafında)

**1. Serbest tutar KDV'sizdir; ürün kendi vergi kategorisini taşır.**
Personelin yazdığı rakam neyse müşteriden o çekilir. Ürün seçilirse ürünün
`taxCategoryId` alanından oran çözülür. İkisi **aynı tahsilatta birlikte**
kullanılabilir; kırılım her kalemi ayrı satırda ve etiketiyle gösterir.
(`collect.breakdown`, `collect.tax_rate_for`)

Oran **çözülemezse sıfır yazılmaz**: `tax_rate_for` `None` döner, ekran
"KDV okunamadı" der ve tahsilat başlatılmaz. Sessiz sıfır faturayı KDV kadar
eksik keser; %20 varsaymak da uydurmadır. Vergi kategorisi **olmayan** ürün
ise kesin olarak KDV'sizdir — bu ikisi karıştırılmaz.

**2. Bilinmeyen durum asla "başarısız" yazılmaz.**
Tanımadığımız her banka durumu `unknown` olur, `void_required` (provizyon
açık) ise ayrı bir durumdur. İkisi de ekranda *"para çekilmiş olabilir;
tekrar link göndermeyin"* der ve **o satır için yeni link üretimini
kilitler**. "Başarısız" yazmak personele ikinci link gönderttirir ve müşteri
iki kez ödeyebilir. Ham durum sözcüğü `store_status` sütununda saklanır:
eşlememizin yanlış olduğu anlaşılırsa veri elimizde kalsın.
(`collect.map_status`, `collect.can_relink`)

**3. SMS'in üç katmanlı freni vardır.** Gerçek mesaj ancak dördü birden izin
verirse çıkar:

| Katman | Ayar | Varsayılan |
|---|---|---|
| Modül | `modules.store_payment_gateway.sms_dry_run` | `true` (kapalı) |
| Platform | `platform.notify.sms.dry_run` | `true` (kapalı) |
| İstek | gövdedeki `dryRun` | `true` |
| Beyaz liste | `sms_allowlist` | boş = herkes |

Hangi katmanın tuttuğu ekranda **yazılı** görünür; "Gönderildi" yazısı yalnız
mesaj gerçekten gidince çıkar.

## Uçlar

| Yöntem | Yol | İzin |
|---|---|---|
| GET | `/state` · `/reference` · `/requests` · `/requests/{id}` · `/products` · `/template` · `/printer` | `view` |
| | `/reference` süzgecin durum listesini besler; panel ikinci bir kopya tutmaz | |
| POST | `/quote` (canlı ön izleme, yazmaz) | `view` |
| POST | `/requests` (taslak kaydet) | `manage` |
| POST | `/requests/{id}/start` (onay + bağlantı + SMS) | `collect` |
| POST | `/requests/{id}/sms` | `collect` |
| POST | `/requests/{id}/poll` | `view` |
| POST | `/requests/{id}/cancel` | `cancel` |
| POST | `/requests/{id}/settle` (havale/nakit) | `settle` |
| POST | `/template` | `manage` |
| POST | `/preview` · `/print` · `/export` | `view` |

Canlı ön izleme `/preview` **değil** `/quote` adındadır: `/preview` panel
kitinin rapor zincirine aittir (`reportChain`).

## Yerel tablolar

`mod_store_payment_gateway_requests` · `_events` · `_prefs`

Yalnız Bagisto'da **karşılığı olmayan** veri: müşteri ödemeden önce sipariş
yoktur, dolayısıyla tahsilat talebi hiçbir mağaza tablosuna düşmez. Gerekçe
ve olay zinciri de mağazada tutulmaz. Sipariş, fatura ve POS denemesi
kopyalanmaz — `store.api` üzerinden okunur (K4).

## Mağaza tarafında eksik uçlar (13.08.2026 itibarıyla canlıda doğrulandı)

**1. Ödeme uçlarının tamamı yayında değil.** `/api/admin/bbd/payments/links`,
`.../attempts` ve `.../terminals` üçü de **404** döndürüyor (çekirdek uçları
200). Yani `bbd_create_payment_link` geçitte bir metot olarak dursa da bugün
bu ekrandan **hiçbir ödeme bağlantısı üretilemez** ve durum yoklanamaz.

Modül bunu **açılışta bir GET ile yoklar** (`service._payments_probe`) ve
kırmızı bir kutuyla söyler. Yalnız metodun varlığına bakmak yetmiyordu:
personel formu doldurup gerekçe yazıp onayladıktan **sonra** 404 görüyordu.

**2. Serbest (siparişe bağlı olmayan) tahsilat ucu yok.**
`store.api → bbd_create_payment_request` metodu geçitte de yok. Talep yine
kaydedilir; "Bağlantı üret" gerekçesiyle kapalı görünür. Uç gelince hiçbir kod
değişmeden açılır; gövde şekli `service._standalone_payload` içinde tek yerde
durur ve uç yayınlandığında alan adları oradan doğrulanmalıdır.

**Bugün gerçekten çalışan tek tahsilat yolu: Elden Kapatma** (havale/nakit
beyanı) — o da mağazanın çekirdek `POST /admin/transactions` ucunu kullanıyor.

### Geçitten (`store_api`) istenen ek metot

`tax_category(category_id)` → `GET /api/admin/settings/tax-categories/{id}`.

Gerekçesi: kategori **listesi** `taxRates: null` döndürüyor (oranlar yalnız
tekil uçta gömülü) ve `/settings/tax-rates` satırları kategori kimliği
taşımıyor. Yani bugün geçidin verdiği iki listeyle bir ürünün KDV oranı
**çözülemez**. `collect.tax_rate_for` bu durumda `None` döner ve ekran
"KDV okunamadı" yazıp tahsilatı durdurur — sessizce %0 yazmaz.

Bugün canlıda 1.421 ürünün tamamı `taxCategoryId: null` olduğu için pratikte
her kalem KDV'sizdir ve bu **kesin** bir cevaptır; ilk vergili ürün
tanımlandığı gün bu metot gerekir.

## Canlı alan adları — uydurma yok

Mağazanın admin API'si **camelCase** döndürür. Modülün okuduğu alanlar canlı
yanıtla karşılaştırıldı:

| Nerede | Doğru alan | Not |
|---|---|---|
| Ürün listesi/detayı | `taxCategoryId`, `specialPrice`, `specialPriceFrom/To` | `price` ondalık **metin** ("2780.0000") |
| Vergi oranı | `taxRate` | `tax_rate` canlıda hiç yok |
| Vergi kategorisi | `taxRates` (listede `null`) | oranlar yalnız tekil uçta |
| Fatura | `orderIncrementId`, `grandTotal` | `orderId` **boş** gelir |
| Sipariş detayı | `invoices[]` gömülü | fatura eşlemesi buradan yapılır |
| Sayfalama `meta` | `currentPage/perPage/lastPage/total` | `links` boş |

İki tuzak açıkça sınandı:

- `GET /admin/invoices?order_id=<n>` sipariş kimliğine değil **sipariş
  numarasının parçasına** bakıyor: `order_id=1` mağazadaki 11 faturanın
  hepsini döndürdü. Bu yüzden fatura, siparişin kendisinden okunur.
- `GET /admin/catalog/products?name=…` **gerçekten** süzüyor (süzgeçsiz
  1.421, `name=geometri` 67, `name=zzzzqqq` 0).

## Elden kapatma mağazaya nasıl yazılır

`POST /api/admin/transactions` gövdesi **`invoiceId` · `paymentMethod` ·
`amount`** ister ve kaydı **faturaya** işler (siparişe değil).

- `havale` → `moneytransfer`, `nakit` → `cashondelivery`. Bu kodlar canlı
  mağazanın kendi `sales.payment_methods` ayarından okundu; kurulum farklıysa
  `settle_payment_methods` ayarı ezer.
- Talebin faturası yoksa mağazaya **hiç istek gitmez**; beyan yerelde durur ve
  ekran nedenini yazar. Kaybolmaz.
- **Dekont/makbuz numarasının mağazada karşılığı yoktur**; yerel satırda ve
  denetim kaydında saklanır. Ekran bunu söyler.

## Testler

```bash
.venv/bin/python -m pytest modules/store_payment_gateway/tests -q
.venv/bin/ruff check modules/store_payment_gateway
```

`test_..._collect.py` ağsızdır: KDV kırılımı, durum eşlemesi, SMS planı ve
süzgeç→SQL dönüşümü. `test_..._service.py` sahte `store.api` ve sahte
`notify` ile iş kurallarını sınar; **hiçbir test gerçek SMS göndermez.**
