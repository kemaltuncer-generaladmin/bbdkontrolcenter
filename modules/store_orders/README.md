# Siparişler

Mağazanın günlük çalışma ekranı: sipariş listesi, süzgeçler, durum akışı ve
sipariş üzerinden fatura/kargo/iade eylemleri.

Grup: **BBD Store** · CSS öneki: `so` · Rapor rafı:
`Raporlar/Mağaza/Satış/<yıl>/<ay>`
İzinler: `store_orders.view`, `store_orders.manage`, `store_orders.cancel`

## Neden bu modül sonradan eklendi

Yan menüdeki diğer 19 ekran siparişin *çevresini* yönetiyordu — Kargo, Fatura,
İadeler, Sanal POS — ama siparişin kendisini yöneten bir yer yoktu. "Bugün
hangi siparişler geldi, hangileri kargolanmayı bekliyor" sorusunun tek bir
cevabı olmuyordu. Menüde Kontrol Paneli'nin hemen ardına girer (`order: 15`);
**mevcut ekranların sırası değişmez.**

## Ekran

| Bölüm | İçerik |
|---|---|
| Filtre şeridi | 17 süzgeç: arama · durum · ödeme durumu · kanal · müşteri grubu · **tarih alanı seçici** (sipariş/ödeme/kargo) + aralık · tutar aralığı · ödeme yöntemi · kargo firması · şehir · kupon · `Faturası kesilmemiş` `Kargoya verilmemiş` `Not içeren` `Riskli` `Kısmi iadeli` |
| Çipler (sayaçlı) | Bekleyen · Hazırlanıyor · Kargoda · Bugün gelen · **Geciken** · İptal |
| Mini toplam | Sipariş · Ciro · Ortalama sepet · İptal — süzgecin **tamamı** üzerinden |
| Tablo | Sipariş no+tarih · Müşteri+grup+şehir · Kalem · Ara toplam · Kargo · İndirim · KDV · **Toplam** · Ödeme · Durum · Takip no (kopyala) · Fatura · Kargo durumu |
| Toplu | **Seçilenleri kargoya ver** · Fatura kes · Etiket indir · Kargo manifestosu · CSV |
| Çekmece | Özet · Kalemler · Ödeme · *Kargo* · *Fatura* · İade · Notlar · İşlem geçmişi |
| Ayarlar sekmesi | Durum adları · sipariş no biçimi · iptal süresi · gecikme eşiği · (salt okunur) mağaza sipariş ayarları |

*İtalik sekmeler yeteneğe bağlıdır — aşağı bakın.*

## Mağazanın yanıt biçimi (canlıda doğrulandı)

Bunlar `bbdstore.com.tr` üzerinde salt okunarak sınandı; varsayım değildir.

- **Alan adları camelCase'tir**: `grandTotal`, `createdAt`, `incrementId`,
  `customerFirstName`, kalemde `qtyOrdered`. Bagisto'nun veritabanı sütunları ve
  belgeleri snake_case olduğu için buna kolayca aldanılıyor; `grand_total` diye
  bakan kod hiçbir şey bulamaz, **istisna da atmaz** — ekran sessizce "—" dolu
  görünür. Her okuma `orders.pick()` üzerinden geçer, iki yazımı da çözer.
- **Para ONDALIK sayıdır** (`grandTotal: 2` = ₺2,00). Kuruşa çevirmek bizim
  işimiz; `to_kurus` Decimal kullanır, float kullanmaz.
- **Zaman damgası saat dilimsizdir** (`"2026-08-13 18:27:17"`) ve YEREL saattir.
  UTC saymak siparişi üç saat gençleştirir ve iptal penceresini hiç çalıştırmaz.
- **Adresler `addresses[]` dizisindedir** (`addressType: order_billing |
  order_shipping`); `billing_address` diye bir alan yoktur.
- **Müşteri grubu `customer.group.name`**, ödeme yöntemi düz `paymentTitle`.
- **`per_page` 50'ye kırpılır**, `meta` camelCase'tir, `links` boş döner.

### Kanal süzgeci — sessiz boşaltıcı

`/api/admin/orders?channel=` kanal **kodunu değil kimliğini** bekliyor:
`channel=default` → **0 kayıt**, `channel=1` → 17 kayıt, hata yok. Kanal kodunu
her isteğe eklemek listeyi tamamen boşaltırdı. Bu yüzden kanal süzgeci
`channel_id` ayarından gelir ve **varsayılan 0'dır: hiç gönderilmez.**
`config/default.yaml` içindeki `channel` yalnız *mağaza ayarlarını* okurken
(`/configuration?channel=`) kullanılır.

## Üç okuma kipi (kritik tasarım kararı)

Mağazanın sipariş listesi ucu yalnız birkaç süzgeç uyguluyor: `status`,
`channel` (kimlik), `date_from/date_to`, `grand_total_from/to`, `customer`,
`email`, `order_id`, `sort/order`. **Ayrıca satırı SIĞ verir:** fatura,
gönderi, not, ara toplam, KDV ve faturalanan tutar yalnız
`GET /orders/{id}` detayındadır.

1. **Sunucu sayfalaması** — sadece mağazanın uyguladığı süzgeçler seçiliyse.
2. **Tavanlı tarama** — ödeme yöntemi, şehir, kupon, kanal adı gibi süzgeçler
   için (`scan_cap`, varsayılan 2000); süzme bizim kodumuzda yapılır.
3. **Detaylandırma** — fatura/gönderi/not/ödeme durumuna dayanan süzgeçler,
   `Kargoda`/`Geciken` çipleri, arama, CSV ve liste raporu için taranan küme
   sipariş sipariş okunur (`detail_cap`, varsayılan 300). `updatedAt` anahtarlı
   bellek önbelleği aynı siparişi ikinci kez çekmez. Tavan aşılırsa yanıt
   `partial: true` döner ve ekran "bazı süzgeçler eksik uygulandı" der.

Neden: Laravel tanımadığı sorgu parametresini **sessizce yok sayar**. Süzgeci
gönderip uygulandığını varsaymak, süzülmemiş listeyi süzülmüş gibi göstermek
olurdu. Durum satırı hangi kipte olunduğunu ve kaç kaydın tarandığını yazar;
tavan yakalanırsa ekran uyarır.

## Ödeme durumu — üç kaynaktan çözülür

Bagisto'da "ödendi mi" diye bir alan **yok** ve sipariş listesi ucu ödeme
**yöntemini** taşıyor (`paymentTitle`), **durumunu** değil. Bilgi başka yerde
duruyor; ekran onu üç kaynaktan birleştirir:

| Kaynak | Uç | Ne söyler |
|---|---|---|
| Siparişin tutar dökümü | `GET /orders/{id}` | `grandTotalInvoiced` / `grandTotalRefunded` → kaç kuruşu tahsil edilmiş. **En keskin olan budur.** |
| Fatura kaydı | `GET /invoices` | `state` = `paid` |
| Sanal POS denemesi | `GET /bbd/payments/attempts` | `state` → `moneyTaken` (true / false / **null**) |

Canlıda doğrulandı: 18 sipariş · 17 fatura (hepsi `paid`) · 17 POS denemesi
(hepsi `order_created`). İki kaynak birbirini doğruluyor.

**Fatura kaydında `orderId` NULL geliyor**; bağ `orderIncrementId` üzerinden
kurulur. `orderId` ile eşleştiren kod hiçbir faturayı bağlayamaz, hata da
vermez ve ekranda **herkes "Ödenmedi" görünür**.

Sıra: fatura `paid` **veya** POS `moneyTaken=true` → **Ödendi** · POS
`moneyTaken=null` → **Belirsiz — bankayla mutabakat sürüyor** · ikisi de yok →
**Ödenmedi**.

### `unknown` ve `provisioning` asla "Ödenmedi" yazılmaz

`moneyTaken` üç durumludur ve `null` bir "henüz false" değildir: banka isteği
aldı, cevabı bize ulaşmadı — **para çekilmiş olabilir.** "Ödenmedi" yazmak
operatöre "tekrar tahsil edin" dedirtir, müşterinin kartından ikinci kez para
çıkar ve fark ancak ekstre gelince anlaşılır. Bu satırlar ayrı renkte (menekşe)
görünür, altında *"bankayla mutabakat gerekiyor"* yazar ve **toplu kargoya
girmez**. Eşlemenin sahibi mağaza paketidir (`Bbd\ControlApi\Support\
PaymentState`); yanıt `moneyTaken` taşıyorsa o kullanılır, taşımıyorsa aynı
eşleme durum adından uygulanır ve **tanınmayan durum `null`'a düşer.**

### Kanıt penceresi

İki liste **tek sayfa** çekilir (uç 50'de kırpıyor) — sipariş başına istek
atmak yasak (N+1): 50 siparişlik bir sayfa 100 istek eder, geçit dakikada 55
istekte tutar. Sayfa dolduysa daha eski kayıtlar okunmamıştır ve
**yokluklarından "ödeme yok" sonucu çıkarılmaz**: pencerenin dibindeki günden
eski siparişler "Bilinmiyor" kalır ve ekran nedenini yazar. Pencere ancak liste
yeniden eskiye sıralıysa kullanılır ve bu **veriden doğrulanır**
(`sort=id&order=desc` gönderiyoruz ama Laravel tanımadığı parametreyi sessizce
yok sayar).

Kanıt 15 saniye önbelleklenir (liste + sayaç ucu aynı çizimde iki kez çekmesin)
ve **her yazmadan sonra düşürülür**: fatura kesen kişi ekranı yenilediğinde
"Ödenmedi" görmemeli.

## Toplu kargoya verme

Onay kutusuyla çoklu seçim → **Seçilenleri kargoya ver** → önizleme → **kuru
prova** → gerekçeli tek onay.

- **Kim seçilebilir.** Kargoya verilemeyen siparişin nedeni *satırda* yazar
  (Kargo durumu sütunu): *iptal/kapalı · zaten tamamı kargolanmış · ödenmedi ·
  ödeme belirsiz · faturası kesilmemiş.* Düğme yalnız uygun olanları götürür ve
  seçim çubuğu *"3 seçili sipariş kargoya verilemez — 2 × … · 1 × …"* diye
  gerekçeyi gruplayarak yazar. Aynı kural (`ship_block`) hem satırı hem
  önizlemeyi besler: ayrışsalar kullanıcı seçebildiği bir siparişin neden
  atlandığını anlayamazdı.
- **Sığ satır engel sayılmaz.** Varsayılan görünümde fatura/kargo kaydı satırda
  yoktur; o satırlar seçilebilir ve *"önizleme siparişi tek tek doğrular"* der.
- **Onay ekranı** kaç sipariş, hangi taşıyıcı, toplam adet ve toplam tutarı
  gösterir. Taşıyıcı kuru provada seçilir ve **onayda değiştirilemez** —
  farklı taşıyıcıyla gelen gerçek uygulama sunucuda reddedilir.
- **Kuru prova önce çalışır ve kural sunucudadır.** Jeton `preview` →
  `dry_run` → `applied` sırasını izler; `dry_run`'dan geçmemiş bir jetonla
  gelen gerçek uygulama reddedilir (K9: arayüzde gizlemek yetkilendirme
  değildir). Kuru provada mağazaya tek satır yazılmaz — geçit çekirdek uçlarda
  isteği hiç göndermez.
- **Kısmi başarı başarısızlık değildir.** 10 siparişin 3'ü patlarsa 7'si
  başarılıdır: sonuç tablosu satır satır *Tamam/Hata* ve hatanın **nedenini**
  gösterir, ekran "başarılı olanları yeniden denemeyin — ikinci gönderi ikinci
  kargo demektir" diye uyarır.
- **Takip numarası toplu işte girilmez** (her gönderininki ayrıdır) ve **etiket
  satın alınmaz**; yalnız mağazada gönderi kaydı açılır.

## Sağladığı yetenek

`store.order.card` — bir siparişin künyesi (özet, kalemler, para dökümü,
adresler, gönderi/fatura/iade listeleri). **Salt okurdur:** yazan hiçbir yordam
dışarı verilmez; yazma her zaman bu modülün kendi uçlarından ve kendi
izinlerinden geçer. Hem backend registry'sine (`ctx.provide`) hem de panel
tarafına (`export function capabilities`) yazılır.

## Tükettiği isteğe bağlı yetenekler

Sözleşme (üçü de aynı biçim):

```js
async (orderId) => ({ ok, error?, title?, node?, columns?, rows? })
```

`node` verilirse olduğu gibi çizilir; yoksa `columns`/`rows` ile tablo çizilir.

| Yetenek | Sahibi | Yoksa ne olur |
|---|---|---|
| `store.shipment.byOrder` | `store_shipping` | **Kargo sekmesi hiç açılmaz.** Özet sekmesi "Kargo: N kayıt" kısa dökümünü ve nedenini gösterir. |
| `store.invoice.byOrder` | `store_invoices` | **Fatura sekmesi hiç açılmaz.** Aynı şekilde özet dökümü kalır. |
| `store.audit.for` | `store_udit_logs` | İşlem geçmişi sekmesi **açık kalır** ve yalnız yerel gerekçe izini gösterir; mağaza kaydının neden görünmediğini yazar. |

İşlem geçmişi sekmesinin kapanmaması bilinçlidir: o sekmenin *kendi* verisi
(gerekçeli yerel denetim izi) başka hiçbir modülde yok; kapatmak bizim
verimizi de gizlerdi.

## Sınırlar

Bu ekran siparişin kendisini yönetir. Şunlar başka modüllerin işidir ve buradan
yalnız *tetiklenir*, burada *uygulanmaz*:

| İş | Sahibi |
|---|---|
| Etiket satın alma, taşıyıcı hareketleri, takip | `store_shipping` |
| Fatura PDF'i, fatura durumu, kopya gönderme | `store_invoices` |
| İade tutarı hesabı ve para iadesi | `store_refunds` |
| Müşteri kaydı ve adresleri | `store_customers` |
| Ürün, fiyat, stok | `store_products` |

## Bilerek yapılmayanlar

- **Sipariş durumunu serbestçe değiştirme.** Mağazanın admin API'sinde sipariş
  durumunu yazan bir uç yok; durum fatura/kargo/iptal eylemlerinin *sonucu*
  olarak değişiyor. Çekmecedeki düğme **"Durum değiştir (kapalı)"** yazar ve
  hemen altında görünür bir uyarı nedeni anlatır — sahte bir düğme, basan
  kişiye hiçbir şey olmadığını göstermezdi; sadece `title` ipucu ise
  dokunmatikte ve klavyeyle hiç görünmez.
- **Toplu etiketi tek PDF'te birleştirme.** Elimizde PDF birleştirici yok;
  sahte bir "birleşik etiket" yazıcıdan bozuk kâğıt çıkarırdı. Her etiket ayrı
  dosyadır.
- **Toplu kargo onayında desi göstermek.** Desi ürünün en/boy/yükseklik
  ölçüsünden hesaplanır; sipariş gövdesi (liste de detay da) o ölçüyü taşımaz
  ve hesabın sahibi Kargo Yönetimi'dir (`store_shipping`, kendi `desi_divisor`
  ayarıyla). Buradan bir desi uydurmak, taşıyıcı faturası geldiğinde tutmayan
  bir rakam olurdu; onay ekranı **adet ve tutar** gösterir ve desiyi neden
  göstermediğini yazar.
- **Uygun olmayan satırın onay kutusunu kilitlemek.** Ortak tablo bileşeni
  (`ui-kit/table.js`) satır başına seçim kilidi sunmuyor ve kit bu turda
  değiştirilmiyor. Bugünkü karşılık: satır seçilebilir ama **nedeni satırda
  yazılı**, düğme yalnız uygunları götürür ve çubuk kaç siparişin neden
  dışarıda kaldığını söyler. Kite `dataTable({selectableRow})` eklenirse kutu
  da kapanır.
- **Mağazanın sipariş ayarlarını yazma.** `core_config` anahtar adları Bagisto
  sürümüne göre değişiyor; bulunmayan anahtara yazmak etkisiz bir satır açar ve
  kullanıcı ayarı değiştirdiğini sanır. Ayarlar sekmesi mağaza tarafını **salt
  okunur** gösterir.
- **Kalem iptali / kısmi iade.** Para hareketidir, `store_refunds`'ın işidir.
  Buradan yalnız kısmi **fatura** ve kısmi **kargo** yapılabilir.

## Yerel tablolar

`mod_store_orders_audit` · `mod_store_orders_batch` · `mod_store_orders_prefs`

Bagisto'da karşılığı olmayan üç şey: yazma **gerekçesi**, toplu işlem
**önizlemesi** (jeton olmadan `batch/apply` reddedilir) ve **ekran tercihi**.
Sipariş verisinin kopyası tutulmaz.

## Uçlar

`GET /orders` · `GET /overview` · `GET /orders/{id}` ·
`GET /orders/{id}/comments` · `GET /reference` · `GET /audit` ·
`POST /orders/{id}/comments` · `POST /orders/{id}/invoice` ·
`POST /orders/{id}/ship` · `POST /orders/{id}/cancel` ·
`POST /batch/preview` · `POST /batch/apply` · `POST /labels` ·
`GET|POST /settings` · `POST /preview` · `POST /print` · `GET /printer` ·
`POST /export`

## Testler

```bash
.venv/bin/python -m pytest modules/store_orders/tests -q
.venv/bin/ruff check modules/store_orders
```

- `test_store_orders_data.py` — saf dönüşümler (dokuz tuzağın her biri), ödeme
  kanıtı eşlemesi ve kargo uygunluğu.
- `test_store_orders_service.py` — iş kuralları, K7, yıkıcı işlem kapıları,
  N+1 yokluğu, kuru prova kapısı ve kısmi başarı.
- `test_store_orders_live_shape.py` — **canlı gövde biçimi.** İçindeki sözlükler
  `GET /api/admin/orders`, `/orders/19`, `/orders/12`, `/invoices` ve
  `/bbd/payments/attempts` yanıtlarından kısaltılarak alınmıştır; alan adları ve
  değer tipleri olduğu gibidir (fatura kaydında `orderId: null` dahil). Modülün
  kendi uydurduğu snake_case veriye karşı geçen test hiçbir şey kanıtlamıyordu.

Ağa çıkılmaz; `store.api` taklit edilir.
