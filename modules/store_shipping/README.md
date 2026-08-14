# Kargo Yönetimi

Kargoya hazır siparişten teslim edilmiş gönderiye kadar tek ekran: gönderi
sihirbazı, taşıyıcı teklifleri, etiket satın alma, toplu etiket PDF'i,
teslimat manifestosu, desi→ücret matrisi, bölge tanımları ve taşıyıcı
performansı.

Grup: **BBD Store** · CSS öneki: `sh` · Rapor rafı:
`Raporlar/Mağaza/Kargo/<yıl>/<ay>`

---

## Ne yapar

| Sekme | İş |
|---|---|
| **Kargoya hazır** | Ödemesi alınmış ama kargolanmamış siparişler; satırda **«Kargoya ver»** (tek tık, ara onay yok) · toplu seçim → gönderi sihirbazı |
| **Gönderiler** | Takip, hareket geçmişi, toplu etiket, manifesto, senkron, iptal/iade |
| **Taşıyıcılar** | Maskeli API kimlikleri, sözleşme matrisi, `Bağlantıyı sına`, ekran tercihleri |
| **Ücretlendirme** | Desi kademeleri, ücretsiz kargo eşiği, kapıda ödeme bedeli, teslim vaadi |
| **Bölgeler** | İl/ilçe → bölge eşlemesi, bölgesel ek ücret, teslimat yapılmayan bölgeler |
| **Performans** | Teslim süresi, teslim edilemeyen oranı, gecikme dağılımı, kargo kâr/zararı |

**Sağladığı yetenek:** `store.shipment.byOrder` — bir siparişin gönderileri ve
son hareketleri. Siparişler, Talepler ve İadeler ekranları okur. Yüzey tek
metottur (`by_order`); yazma işlemleri paylaşılmaz.

---

## «Kargoya ver» — tek tık

Kargoya hazır listesindeki her satırda bir düğme durur. Tıklanınca mağazadaki
`POST /api/admin/bbd/orders/{id}/dispatch` ucu zincirin tamamını çalıştırır:

```
gönderi aç → teklif al → MÜŞTERİNİN ödediği firmayı yeğle → ETİKET SATIN AL
          → takip numarasını siparişe yaz → PDF'i döndür
```

Kontrol Merkezi tarafındaki iş, dönen etiketi ve siparişin faturasını kâğıda
dökmektir.

**Ara onay adımı yoktur.** Kullanıcının kararı: *"sipariş seçince 'kargoya ver'
dedik mi o sipariş yola çıkacak zaten. PARA HARCASIN."* Onay penceresi
açılmaz, `dryRun` varsayılanı `false`'tur. Gerekçe alanı listenin üstünde
durur ve **boş bırakılabilir**: boşsa denetim defterine otomatik bir metin
yazılır (`dispatch_reason`), akış durmaz. Koruma izin anahtarındadır —
`store_shipping.purchase`, etiket satın almayla aynı anahtar.

**Takip numarası elle girilmez.** Gövde PDF olduğu için künye `X-Bbd-*`
başlıklarında gelir; geçit ikisini birden taşıyan bir zarf döndürür
(`store_api.binary_envelope`). Numara ekranda büyük ve kopyalanabilir durur,
çünkü siparişe yazılmış olan odur. Yanıtta gelmezse **uydurulmaz**; ekran
"gelmedi" der.

**Otomatik basılan iki belge: kargo etiketi ve fatura.** Kargoya teslim fişi
(`handover`) bu akışa **girmez** — kullanıcının kararı "fiş yok". Fişin kodu
duruyor ve sihirbazdaki düğmesinden elle basılıyor.

**Etiket ancak satın alındıktan sonra vardır**, basım o adımdan sonra
tetiklenir. Uç etiketi indiremediyse (200 + `labelReady:false`) kâğıt çıkmaz;
gönderi açılmıştır ve ekran "etiketi yeniden al" düğmesini gösterir.

**Yazıcı yoksa iş durmaz (K7).** Belgeler her hâlükârda rapor klasörüne 0600
ile yazılır; basılamayan satır "yazdırılamadı" der ve dosya yolunu gösterir.
`auto_print: false` yapılırsa (ya da ekran tercihinden kapatılırsa) belgeler
yine üretilir, yalnız yazıcıya gönderilmez.

**Çift basım koruması.** Aynı gönderi ikinci kez kendiliğinden basılmaz;
denetim defterindeki `auto_print` satırı buna bakar. Elle **"Tekrar yazdır"**
bu kapıyı hiç görmez: kasıtlı tekrar ile kazara ikinci basım ayrı şeylerdir.

**Test yolu Geliver'a hiç uğramaz.** `provider: "bagisto"` seçiliyse istek
Bagisto'nun kendi gönderi ucuna gider; `bbd_dispatch_order` çağrılmaz, para
harcanmaz, takip numarası `TEST-` önekiyle bizim ürettiğimizdir.

---

## Bilerek verilmiş kararlar

**Etiketi biz çizmiyoruz.** Etiket üzerindeki barkod taşıyıcının kendi
numaralandırmasıdır ve şubede o okutulur. Kendi çizdiğimiz bir barkod
okunmazsa gönderi elde kalır. Etiketin kendisi her zaman taşıyıcıdan gelir
(`bbd_shipment_label`); modül yalnızca gelen PDF'leri **A4 4'lü** ya da
**termal 100×150** kâğıda yerleştirir. Yerleşim aritmetiği (`backend/labels.py`)
saftır ve `pypdf` olmadan test edilir; birleştirme `pypdf`'i tembel import
eder — paket yoksa modül yine yüklenir, yalnız toplu etiket düğmesi kapalı
görünür ve nedenini yazar.

**Taslak açmak ile etiket almak ayrı iki adım.** İlki ücretsizdir ve
düzeltilebilir, ikincisi para harcar ve geri alınamaz. Tek düğmede
birleştirmek, ölçüsü yanlış girilmiş bir gönderinin parasını ödetirdi.

**Etiket satın alma üç kapıdan geçer** (ADR 0012): ayrı izin anahtarı
(`store_shipping.purchase`), **en az 20 karakter** gerekçe (uçtaki şemada ve
serviste ayrı ayrı doğrulanır — K9) ve `dryRun` varsayılanı. İade gönderisi de
etiket satın alır; aynı kapıdan geçer. Denetim izine **istek gitmeden önce**
"denendi" satırı yazılır: zaman aşımına uğrayan bir yazma uzakta uygulanmış
olabilir.

**Desi ≠ ağırlık.** Faturalanan birim, desi ile fiili ağırlığın büyüğünün
tavana yuvarlanmışıdır. Ekran ham ölçüyü ve faturalanacak desiyi ayrı gösterir;
ürün ölçüsü eksikse otomatik hesap yapılmaz ve "elle girin" denir — uydurulmuş
bir kutu doğrudan yanlış faturadır.

**Ücret dökümü tahmindir.** Kesin tutar taşıyıcı teklifinden gelir. İkisi ayrı
kartlarda durur; tahmini gerçek fiyat gibi sunmak sonradan gelen faturayı
sürpriz yapardı.

**Tutarsız desi matrisi mağazaya yazılmaz.** Kademelerde boşluk kalırsa
vitrinde "kargo ücreti hesaplanamadı" hatası doğar, örtüşme varsa ücret sıraya
göre keyfî olur. Kaydetme reddedilir ve hangi aralığın açıkta kaldığı yazılır.

**Bölge eşlemesi yereldir.** Bagisto'da il/ilçe → bölge tablosu yok; buradaki
tanım ücret dökümünü ve sihirbaz uyarısını etkiler, **vitrini etkilemez** ve
ekran bunu söyler.

**Türetilmiş süzgeçler sayfa içinde çalışır.** `Geciken`, `Adres sorunu` ve
`Tahsil edilmemiş` taşıyıcıda bir alan değil, bizim çıkardığımız bulgudur;
liste süzülmüş sayfa üzerinde daraltılır ve ekran bunu yazar. Aynı şey
"kargoya hazır" için de geçerli: mağaza "gönderisi olmayan sipariş" süzgeci
sunmuyor, kalan adet kontrolü sayfa içinde yapılıyor — `total` ve `shown`
ayrı gösterilir.

**Silme yok.** Gönderi iptal edilir ya da iade gönderisine çevrilir; bölge
satırı silinmez, "teslimat yapılmıyor" işaretlenir. Böylece "neden bu ilçeye
göndermiyorduk" kaydı kalır.

---

## Canlı mağazada doğrulanmış tuzaklar

Aşağıdakiler `https://bbdstore.com.tr` üzerinde **salt okuma** ile
sınanmıştır; her birinin karşılığı
`tests/test_store_shipping_live_shape.py` içinde bir gerileme testidir.

**Kanal süzgeci kimlik ister, kod değil.** `/api/admin/orders?channel=default`
**sıfır** sipariş döndürüyor, `channel=1` on yedi. Laravel eşleşmeyen değeri
hata da vermiyor — "Kargoya hazır" sekmesi sessizce boş açılıyordu. Ayardaki
kanal kodu artık `channels()` üzerinden kimliğe çevriliyor; çevrilemezse
süzgeç **hiç gönderilmiyor** (tek kanallı mağazada süzmemek zararsız, yanlış
süzmek yıkıcı).

**Alan adları camelCase.** Canlı yanıtta `incrementId`, `grandTotal`,
`totalQtyOrdered`, `customerName`, `paymentTitle`, `qtyOrdered` var;
`grand_total` / `customer_full_name` / `qty_ordered` **yok**. snake_case
karşılıklar yalnız yedek olarak duruyor.

**Teslimat adresi `addresses` listesinde.** `shipping_address` diye bir alan
yok; adresler `addressType` (`order_shipping` / `order_billing`) ile
ayrılıyor ve sipariş **listesi** ucunda hiç gelmiyor. Teslimat adresi yoksa
**fatura adresine düşülmez** — yanlış adres paketi yanlış şehre yollar.
İlçe bilgisi `state` alanında geliyor.

**Alan yokluğu değer sıfır değildir.** Sipariş listesi faturalanan ve
kargolanan adedi hiç göndermiyor. Bunları 0 sayıp "ödeme beklemede" demek
listedeki **her** siparişi engelliyordu. Bilgi yoksa engel konmuyor; ekran
`verified: false` ile hazırlığın doğrulanmadığını yazıyor ve sihirbaz
siparişi tek tek okuyup yeniden denetliyor.

**BBD kargo uçları henüz yayında değil.** `/api/admin/bbd/shipments` bugün 404
dönüyor; geçit bunu `bbd_endpoint_missing` koduyla anlaşılır bir metne
çeviriyor ve `Gönderiler` sekmesi "gönderi listesi okunamadı — uç henüz
yayında değil" diyor. Ekran ayakta kalıyor (K7); `Kargoya hazır`,
`Bölgeler` ve tercihler bundan etkilenmiyor.

---

## Yerel tablolar

Mağazada **karşılığı olmayan** dört şey için:

| Tablo | Neden |
|---|---|
| `mod_store_shipping_audit` | Gerekçe. Mağaza denetim tutuyor ama "neden" alanı yok; ağ koparsa "ne yapmaya çalıştık" kaydı yalnız burada kalır |
| `mod_store_shipping_zones` | İl/ilçe → bölge eşlemesi ve bölgesel ek ücret |
| `mod_store_shipping_manifests` | Hangi gönderiler hangi gün kime teslim edildi |
| `mod_store_shipping_prefs` | Varsayılan taşıyıcı, etiket biçimi, gecikme eşiği |

Gönderi, taşıyıcı ve desi→ücret matrisi **kopyalanmaz**: kopya, taşıyıcıdan
gelen bir hareketten sonra sessizce yanlış durum gösterir.

---

## Geliver kurulumu — «bir daha Bagisto paneline girme»

Yedinci sekme. Mağazanın kargo entegrasyonunun kurulum ayarları buradan
yönetilir: **API tokenı · entegrasyon kurulu · canlı mod · test modu ·
gönderici adres · webhook durumu · bağlantı sınaması.**

Bu sekme, hemen üstündeki `Ekran tercihleri` ile **aynı şey değildir**: orası
bu panelin tercihleri (etiket biçimi, gecikme eşiği) ve yerel tabloda yaşar,
burası mağazanın `core_config` satırları ve checkout'ta müşterinin gördüğü
kargo ücretini belirler.

**Token geri okunamaz.** Mağaza onu hiçbir gövdede döndürmüyor; ekran yalnız
`hasToken` bayrağını ve son dört karakterlik maskeyi görür. Maske token
değildir ve geri gönderilemez — yazma ucu maske biçimli ve `enc:` önekli
değeri açıkça reddeder.

**Boş token mevcudu silmez.** Form kaydedilirken kutu çoğu zaman boş gelir;
boşu yazmak mağazanın kargo kimliğini silmek olurdu ve belirtisi ancak ilk
gerçek siparişte görünürdü (gönderi açılamaz). Tokenı gerçekten kaldırmanın
yolu `active: false` ya da **yenisiyle değiştirmek**tir.

**Canlıya geçiş uyarı değil engeldir.** `Müşteriye açık` anahtarı açılırken
kararı sunucu verir: token var mı, entegrasyon kurulu mu, Geliver'a okuma
çağrısı gidiyor mu, gönderici adres çözülüyor mu. Geçemezse istek 422
(`GELIVER_PRECHECK_FAILED`) ile reddedilir ve **nedenler kod+metin olarak
ekrana aynen basılır**, her birinin yanında sıradaki adımla. Ekran kendi
tahminini sunucunun cevabının yerine koymaz — Geliver'a mağaza sordu, panel
sormadı.

`Bağlantıyı sına` yalnız **okuma** çağrısı yapar (il listesi + gönderici
adres), önbelleği atlar ve gerekçe istemez. Webhook kaydı **ayrı düğmedir**:
Geliver hesabında bir kayıt açar, bu yüzden her "kaydet"e iliştirilmez.
Webhook kayıtlı değilse gönderi durumu kendiliğinden güncellenmez ve ekran
bunu açıkça söyler.

Bu uçtan **değiştirilemeyen** alanlar (checkout başlığı, fiyat ayarlaması,
ücretsiz kargo eşiği, gösterilecek firmalar, sıralama) sessizce yok sayılmaz:
mağazanın kendi ret gerekçeleriyle birlikte listelenir.

---

## İzinler

| Anahtar | Ne açar |
|---|---|
| `store_shipping.view` | Listeler, performans, rapor ve etiket alma |
| `store_shipping.manage` | Taslak açma, senkron, taşıyıcı sınama, bildirim |
| `store_shipping.purchase` | **Etiket satın alma ve iade etiketi — para harcar** |
| `store_shipping.cancel` | Satın alınmış gönderiyi iptal etme |
| `store_shipping.rates` | Desi matrisi, ücretsiz kargo eşiği, bölge tanımları |
| `store_shipping.integration` | **Geliver kurulumu**: API tokenı, kurulu/canlı/test, gönderici adres, webhook kaydı |

`integration` `manage`'den ayrıdır: gönderi açıp etiket alan personelin
mağazanın kargo kimliğini değiştirmeye ya da canlı modu çevirmeye ihtiyacı
yoktur. Gerekçesi para harcayan uçlarla aynı uzunluktadır (20 karakter).

`accountant` bu ekranı görmez: kargo mali değil operasyon ekranıdır. Kargo
maliyeti mali tarafa performans raporuyla gider.

---

## Çalıştırma

```bash
.venv/bin/python -m pytest modules/store_shipping/tests -q
.venv/bin/ruff check modules/store_shipping
```
