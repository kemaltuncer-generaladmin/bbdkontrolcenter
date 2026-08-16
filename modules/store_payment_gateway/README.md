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

İstisna **`link_id`** (göç `002_link_id.sql`): mağazadaki bağlantının sayısal
birincil anahtarı. Veri kopyası değil **adrestir** — onsuz mağazadaki kaydı
tekil olarak okumanın ya da iptal etmenin yolu yok (iki uç da
`->whereNumber('id')`). `token` sütunu insanın okuduğu `code`u taşımaya devam
eder; ikisi ayrı kavramdır.

## Eksik olan taraf değişti (16.08.2026 itibarıyla canlıda doğrulandı)

> **Bu bölüm bir dönem "mağazada ödeme uçları yok (404)" diyordu; artık
> geçerli değil.** Eski hâli okuyan biri yanlış yeri arar, o yüzden eskidiği
> burada açıkça yazılıyor. Aşağıdaki ölçümler `route:list` çıktısıyla ve
> yalnız **GET** çağrılarıyla alındı.

**1. Mağazanın ödeme uçları YAYINDA.** 13.08.2026'da üçü de 404 dönüyordu;
bugün üçü de **200**:

| Uç | 13.08.2026 | 16.08.2026 |
|---|---|---|
| `GET /api/admin/bbd/payment-links` | 404 | **200** (liste boş, `total: 0`) |
| `GET /api/admin/bbd/payments/attempts` | 404 | **200** (gerçek satır geliyor) |
| `GET /api/admin/bbd/payments/terminals` | 404 | **200** (kuveytturk, canlı) |
| `POST /api/admin/bbd/payment-links` | — | **kayıtlı** (`route:list`) |

**Yol adı tuzağı:** link ucunun öneki `payment-links`tir, `payments/links`
**değil**. Eski belgelerdeki `payments/links` yazımı bugün de 404 döner —
"uç yok" yanılgısını büyük olasılıkla bu üretmişti.

Modül yine **açılışta bir GET ile yoklar** (`service._payments_probe`).
Yoklama kaldırılmadı: sorduğu soru tarihe değil **ana** aittir — mağaza
kapalıysa, belirteç düştüyse ya da uç geri çekilirse ekran yine
"üretilemez" demeli (K7). Yalnız metodun varlığına bakmak yetmiyordu:
personel formu doldurup gerekçe yazıp onayladıktan **sonra** hata görüyordu.

**2. Geçit eşlemesi BAĞLANDI — "Bağlantı üret" artık açık.**
`POST /api/admin/bbd/payment-links` **zaten siparişe bağlanmayan** bir link
üretir (denetleyici gövdedeki `orderId` alanını hiç okumaz). Ekran bir dönem
geçitte hiç var olmamış bir metodu (`bbd_create_payment_request`) yokluyordu, bu
yüzden düğme hiç açılmadı; artık geçidin gerçek metodu çağrılıyor:
`store.api → bbd_create_payment_link`.

Gövde mağazanın sözleşmesidir: `kind` · `amount` · `items` · `billing` ·
`description`. Verilen dört karar (gerekçeleri `service._link_payload`
docstring'inde):

| Konu | Karar |
|---|---|
| Tutar | Kuruş → **ondalık TL metni** (`collect.from_kurus`, `Decimal`, iki basamak sabit). Mağaza tutarı sepetin toplamıyla kuruş kuruş karşılaştırıyor (`AmountGate`); kuruş göndermek garantili 422 `AMOUNT_DRIFT`ti. |
| Tür | Kalemlerde ürün varsa `product` (+`items`, tutar YOK), yoksa `custom` (+`amount`, `items` YOK). |
| Karma talep | Serbest tutar + ürün aynı linke **konamaz**; istek mağazaya hiç gitmez, ekran nedenini yazar. Birini sessizce düşürmek eksik tahsilat olurdu. |
| Adres | Yerel **İl → `state`**, yerel **İlçe → `city`**. Mağaza `state`i 81 ilin listesiyle doğruluyor (`TurkishProvinces`), `city`yi bankanın `BillAddrCity` alanına geçiriyor. |
| `dryRun` | Gövdeye **açıkça** yazılır (`true` da `false` da). Mağaza varsayılanı `true`; alan hiç gitmezse gerçek istek **sessizce kuru provaya düşer**. |

Ad/soyad ayrımı `collect.split_name`: **son boşluktan** bölünür (Türkçede ikinci
ad yaygın, ikinci soyad değil) ve tek sözcüklü adda **soyad uydurulmaz** — ekran
soyadı ister, mağazaya eksik istek gitmez. Fatura alanlarında yalnız **boşluk**
denetlenir; il listesinin kopyası burada TUTULMAZ (iki liste ayrışırdı), yanlış
yazılmış ili mağaza reddeder ve ret metni ekrana çıkar.

Sipariş kimliği gövdeye **hiç konmaz** (mağaza okumaz, geçit reddeder); yerel
satırda durmaya devam eder, mağaza tarafındaki bağ ödeme tamamlanınca kurulur ve
yoklamayla geri gelir.

**Elden Kapatma** (havale/nakit beyanı, `POST /admin/transactions`) tahsilatın
ikinci yolu olarak duruyor: mağaza kapalıyken ya da geçit metodu bulunamadığında
açık kalan yol odur.

## Sessiz kalan dört yol açıldı (16.08.2026, ikinci tur)

Bağlantı üretimi bağlandıktan **sonra** ölçüldüğünde dört yolun daha sessizce
yanlış çalıştığı görüldü. Dördü de "hata vermiyor ama doğru işi yapmıyor"
cinsindendi — bu yüzden hiçbiri ekranda görünmüyordu.

**1. Gerçek bağlantı üretimi mağazada kuru provaya düşüyordu.**
`PaymentLinkController::store` bayrağı `$this->dryRun($request, true)` ile
okuyor (varsayılan **true**, "para ile ilgili uç" sigortası) ve `DryRun::parse`
alan **hiç gelmediğinde** varsayılana düşüyor. Geçitteki `_write` ise `dryRun`u
yalnız kuru provada gövdeye ekliyordu; `dry_run=False` iken alan hiç gitmiyordu.
Sonuç tam tersiydi: personel kuru provayı **kapatıp** "Bağlantı üret" dediğinde
mağaza `DB::rollBack()` yapıyor, ekran *"kuru prova: bağlantı üretilmedi"*
diyordu. Yani ekrandan **hiçbir zaman** gerçek bir tahsilat bağlantısı
üretilememişti. `bbd_create_payment_link` artık `dryRun`u gövdeye açıkça yazıyor
(`bbd_dispatch_order` ile aynı desen). Gerileme testi **geçidin kendi
katmanında**: modül taklidi `_write`e uğramadığı için bu hatayı göremez.

**2. Yoklama başkasının linkini bu talebe yazabiliyordu.**
`PaymentLinkController::index` yalnız `status` ve `q` süzgeçlerini okur;
`token`/`order_id` diye bir süzgeç **yoktur** ve Laravel tanımadığı parametreyi
sessizce yok sayar — uç "en yeni 50 link"i döndürür (`orderByDesc('id')`).
Kod da eşleşme bulamayınca `match = rows[0]` ile ilk yabancı satırı alıyordu:
ödenmemiş bir talep **"Ödendi"** oluyor, başkasının siparişine bağlanıyor ve
`paid` kilidi yüzünden yeni bağlantı da üretilemiyordu. Artık yoklama
**sayısal kimlikle tekil ucu** çağırıyor (`GET /payment-links/{id}`); kimlik
yoksa mağazanın gerçekten okuduğu `q` süzgeciyle arıyor ve dönen satırı yine
doğruluyor. `rows[0]` yedeği **kaldırıldı**: eşleşme yoksa doğru cevap "en
yenisi" değil, `unknown` + çift çekim uyarısıdır. Elde yalnız sipariş no varken
mağazaya **hiç gidilmez** (sipariş kimliği link listesinde aranabilir bir şey
değil).

**3. Çekmecedeki "POS denemeleri" başka müşterilerin kartlarını gösteriyordu.**
`PaymentAttemptController::applyFilters` yalnız `state · orderId · from · to`
okur — süzgeç adı **camelCase**. Gönderilen `order_id` yok sayılıyor ve uç
**süzülmemiş** listeyi döndürüyordu. Canlıda ölçüldü (salt GET):
`?order_id=999999` → 17 satır, `?orderId=999999` → 0 satır. Bu yalnız yanlış
veri değil, **kart verisi sızıntısıydı**. Süzgeç düzeltildi; sipariş kimliği
yokken blok **hiç çalışmıyor** ve ekran nedenini yazıyor (belirteçle deneme
aranamıyor, uçta öyle bir süzgeç yok).

**4. "İptal" düğmesi hiç çalışmıyordu ve "Yeni bağlantı üret" eskisini
öldürmüyordu.** Her iki uç da (`{id}` ve `{id}/cancel`) `->whereNumber('id')`
ile daraltılmış, yani **sayısal birincil anahtar** istiyor; gönderilen ise
mağazanın `code` dizesiydi (`LinkCode` alfabesi `0123456789ABCDEFGHJKMNPQRSTVWXYZ`
— 12 hanenin tamamının rakam çıkma olasılığı ≈1,2e-6). Ayrıca yeni bağlantı
üretimi yerel `token`/`link` alanlarını üzerine yazıp mağazadaki eski kaydı
elleşmiyordu; `persistLink` yalnız INSERT yapıyor ve eski link `expires_at`
dolana kadar (varsayılan 48 saat) **ödenebilir** kalıyor. Müşteri elindeki ilk
SMS'i öderse o belirteç artık yerel satırda yok, yoklama onu hiç aramıyor ve
personel parayı ikinci kez istiyordu. Artık mağazanın sayısal `id`'si ayrı bir
sütunda tutuluyor (`link_id`, göç `002_link_id.sql`), yeni bağlantı
üretilmeden **önce** eskisi mağazada kapatılıyor ve **iptal başarısızsa yeni
link üretilmiyor** — "kapatamadım ama yenisini ürettim" tam olarak iki-link
durumudur. Eski belirteç olay zincirine düşüyor (veri silinmez).

> `token` ve `id` bundan sonra **ayrı kavramlar**: `token` (= mağazanın `code`u)
> insanın okuduğu, telefonda söylenen, SMS'e giren koddur; `id` uçların istediği
> anahtardır. Tek sütunda taşımanın bedeli yukarıdaki 4. maddedir.

## Kuruş → TL çevrimi neden AÇIK yapılıyor

Yerelde para **kuruş tam sayısıdır** (`net`/`tax`/`gross` sütunları INTEGER);
mağaza ise `amount` alanında **ondalık TL metni** ister ("125.00"). Çevrim tek
bir yerde, `collect.from_kurus` içinde ve `Decimal` ile yapılır:

```python
str((Decimal(as_int(kurus)) / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
```

**Neden geçitte sessizce yapılmıyor:** `bbd_create_payment_link` sayı verilirse
isteği **hiç göndermiyor**. Çevirinin geçide gömülmesi `12500` değerini hem
125,00 TL hem 12.500,00 TL okunabilir yapardı ve yanlış olanı müşteriye giden
bir tahsilat linki üretirdi. Belirsiz niyetle para işi yapılmaz; çağıran ne
demek istediğini yazar.

**Neden `float` yok:** `12500/100` float'ta 124,999…9 üretebilir. Ölçüldü:
`int(1999 / 100 * 100) == 1998` (19,99 TL → 19,98 TL) ve ilk 200.000 kuruş
değerinin **9.174'ü** bu tuzağa düşüyor. `Decimal` yolunda 600.000 değerde
birebir tur doğrulandı: her çıktı mağazanın `Money::toMinor` süzgecini geçiyor
ve tam olarak başlangıç kuruşuna dönüyor. (Aynı gerekçe deponun kendi
`store_shipping/backend/shipping.py::to_kurus` yardımcısında da yazılı.)

**Neden `quantize`:** üssü −2'ye çiviliyor; bilimsel gösterim (`1E+3`) ya da
tek basamaklı kuruş asla üretilemiyor. Sıfır ve eksi tutar çevrime hiç
ulaşmıyor — `create()` ve `start()` ikisi de önce kapıyor.

**Mağazanın 1.000.000 TL tavanı burada tekrarlanmaz** (`AmountGate::MAX_MINOR`).
İl listesiyle aynı gerekçe: iki kopya zamanla ayrışır ve yerel kopya ya doğru
talebi bloklar ya yanlışını geçirir. Tavanı aşan istek mağazadan 422 ile döner
ve ret metni K7 ile ekrana okunur Türkçe olarak çıkar.

### Geçitten (`store_api`) istenen ek metot

`tax_category(category_id)` → `GET /api/admin/settings/tax-categories/{id}`.

Gerekçesi: kategori **listesi** `taxRates: null` döndürüyor (oranlar yalnız
tekil uçta gömülü) ve `/settings/tax-rates` satırları kategori kimliği
taşımıyor. Yani bugün geçidin verdiği iki listeyle bir ürünün KDV oranı
**çözülemez**. `collect.tax_rate_for` bu durumda `None` döner ve ekran
"KDV okunamadı" yazıp tahsilatı durdurur — sessizce %0 yazmaz.

16.08.2026'da katalogdaki **1.422 ürünün tamamı** `taxCategoryId: null`
(143 sayfa tek tek okundu; sayı 13.08.2026'da 1.421'di, aradaki fark serbest
tahsilatın sanal ürünü `BBD-OZEL-TAHSILAT`). Yani pratikte her kalem
KDV'sizdir ve bu **kesin** bir cevaptır; ilk vergili ürün tanımlandığı gün bu
metot gerekir.

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
