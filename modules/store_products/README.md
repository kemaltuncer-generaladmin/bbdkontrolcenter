# Ürünler

BBD Store kataloğu: arama, düzenleme, fiyat, stok, toplu işlem ve rapor.
Canlı ölçek **1.419 ürün, 41 kategori**.

Grup: **BBD Store** · CSS öneki: `sp` · Rapor rafı:
`Raporlar/Mağaza/Ürün/<yıl>/<ay>`

## Ne yapar

Ekranda üç üst sekme var: **Ürünler · Nitelikler · Ayarlar.**

| Alan | Davranış |
|---|---|
| Liste | **Sunucu tarafı sayfalama.** Tam katalog hiçbir zaman çekilip istemcide süzülmez. |
| Çipler | Tükendi · Kritik stok · Görselsiz · SEO eksik → `bbd/catalog/issues` (sayfalı). Pasif → `status=0`. |
| Düzenleyici | Çekmecede 9 sekme: Genel · **Kitap künyesi** · Fiyat · Stok · Görseller · Varyantlar · Kategoriler · SEO · Geçmiş. |
| Görsel | Çoklu seçim + sürükle-bırak, sırayla yükleme, ilerleme çubuğu, sıralama (ilk = kapak), kaldırma. **İki yerden yapılır:** Görseller sekmesi (sayı sınırı yok) ve **ürün açma formu** (tavan 6 dosya, zincirin 5. adımı). |
| Nitelikler | Nitelik listesi (kullanım sayısıyla), künye, seçenek yönetimi, pasifleştirme, silme. |
| Aileler | Aile listesi (ürün sayısıyla), grup düzeni, atanabilir nitelik havuzu. |
| Toplu işlem | Fiyat · stok · kategori · durum. **Önce fark tablosu, sonra gerekçeli onay.** |
| Silme | Satırdan, düzenleyiciden ve seçimden. **Önce ne silineceği + satış geçmişi**, sonra gerekçeli onay. Silinen satır listeden anında düşer. |
| Ayarlar | Kritik stok eşiği (yerel), tükenen ürün davranışı ve arka sipariş (mağaza `core_config`). |
| Çıktı | Stok raporu PDF · fiyat listesi PDF · görünen sayfa CSV · tüm katalog CSV. |

## Görsel yükleme

Panel dosyayı `FileReader.readAsDataURL` ile okur (Tauri kabuğunda fs eklentisi
yok), base64 olarak backend'e yollar, backend geçidin `upload_product_image`
metoduna verir. **Dosya başına bir istek** — sırayla, paralel değil: hata dosya
başına anlatılabiliyor, ilerleme çubuğu gerçek ilerlemeyi gösteriyor ve her
yüklemenin kendi denetim satırı oluyor.

**Reddedilen dosya mağazaya HİÇ gönderilmez.** Sunucu sınırı **4 MB**
(`AdminCatalogProductImageProcessor::MAX_BYTES`); aşan dosyayı yine de
göndermek kullanıcıyı bekletir, hız kovasından pay harcar ve karşılığında
"Mağaza isteği doğrulayamadı" gibi hiçbir şey anlatmayan bir metin döndürür.
Denetim üç yerde durur ve **üçü de gerekli**:

| Yer | Ne bakar | Neden orada |
|---|---|---|
| Panel | Tarayıcının verdiği tür, `file.size`, `Image` ile ölçü | Dosya seçilir seçilmez, ağa çıkmadan söyler |
| `backend/images.py` | Türü **içerikten** (imza baytları), boyutu çözülmüş bayttan | Uzantı yalan söyler; panel atlatılabilir (K9) |
| Geçit | Aynı 4 MB sınırı | Tek kapı; başka çağıran da olabilir |

Çözünürlük **engel değil uyarıdır**: küçük görsel yüklenir, yalnız ne olacağı
somut yazılır — "Önerilen en az 800×800; yüklenen 320×240 — listede ve ürün
sayfasında bulanık görünür." Aşırı en-boy oranı da aynı biçimde uyarılır
(vitrin ızgarası kareye yakın görsel bekler ve kenarlardan kırpar).

## Ürün açma — ekran ne dolduruyor

"Yeni ürün" çekmecesinde **SKU ve ürün adı** yeter; geri kalanı ekran doldurur
ve **doldurduğu her alanı “otomatik dolduruldu” rozetiyle gösterir.** Rozetli
alanın üstüne yazılabilir; yazılan değer bir daha ezilmez, boşaltılırsa alan
yeniden otomatiğe döner.

### Form artık neyi kapsıyor

Şikâyet buydu: *"bir ürünün sahip olduğu tüm alanları ekleyemiyoruz — resim,
ISBN, yazar, yayın, çoğu şey yok."* Form bugün **üç bölüm**:

| Bölüm | İçindekiler | Katlanır mı |
|---|---|---|
| Temel | SKU · ad · kategori · fiyat · stok · depo · kısa açıklama · vergi kategorisi · durum | Hayır |
| **Gelişmiş alanlar** | Kitap künyesi: sayfa sayısı · ISBN · yayınevi · yazar · baskı yılı · kitap dili · sınav türü · yayın tipi · desi (dokuzu da canlıda açık) | **Evet — kapalı başlar** |
| **Ürün görselleri** | Çoklu seçim, önizleme, ◀/▶ ile sıra (ilk = kapak), "Çıkar" | Hayır — **her zaman görünür** |

**Künye neden katlanır, görsel neden değil.** Yeni ürün formu boş açılıyor;
künyenin dokuz alanını her seferinde göstermek asıl işi (SKU + ad + fiyat)
gürültüye boğuyordu. Görselsiz ürün ise vitrinde tıklanmıyor ve katalog
sağlığında "Görselsiz" bulgusunda çıkıyor — en sık unutulan işi en görünmez
yere koymak yanlış olurdu.

Katlamak bilgiyi **gizlemez**, üç kural birlikte çalışır: başlıkta canlı
**"N alan dolu"** damgası (bölüm kapalıyken de görünür) · sunucu taslakta bir
künye hatası bulursa bölüm **kendiliğinden açılır** · "Ürünü aç"ta alanlar
geçersizse yine açılır.

Alanlar **panelde sabit değil**, `GET /reference` → `bookFieldsOnCreate` ile
gelir ve düzenleme sekmesiyle **aynı tarifi, aynı üreteci, aynı ipuçlarını**
kullanır. Çözülemeyen nitelik bölümün altında **nedeniyle** listelenir.

| Alan | Nasıl doluyor |
|---|---|
| `url_key` | Ürün adından; Türkçe harfler katlanır (ı→i, ş→s, ğ→g, ü→u, ö→o, ç→c). |
| `url_key` çakışması | **Yazmadan önce** mağazaya sorulur; doluysa `-2`, `-3` diye artar (TUZAK 6). |
| Kategoriler | Seçilen yaprağın **üst kategorileri ağaçtan okunup** eklenir (roman → kitap). Ağaç geçitten gelir, varsayılmaz. |
| Öznitelik ailesi | Ekranda sorulmaz; `_default_family()` çözer — **künye niteliklerini taşıyan aileyi** seçer (aşağıya bakın). |
| `meta_title` / `meta_description` | Boşsa ürün adından ve kısa açıklamadan türetilir; açıklama **düz metne indirilir** (zengin metin etiketi meta alanına sızmaz), 60/160 karakterde sözcük sınırında kırpılır. |
| Stok | Girilmediyse depoya **0 yazılır** — ürün açıkça “stokta yok” doğar, stok takibinin dışında kalmaz. |
| Durum | Yeni ürün **pasif** doğar; çekmecedeki kutu ile aktif açılabilir. |
| Vergi kategorisi | Mağazada tek tanesi varsa ekranda sorulmaz, kendiliğinden uygulanır ve rozetle söylenir; ikincisi açılırsa alan geri gelir ve **hiçbiri sessizce seçilmez**. |
| Depo | Tek depo varsa alan yoktur; ikinci depo açılırsa seçim alanı geri gelir. |

Fiyat **uydurulmaz**: boş bırakılırsa yazılmaz ve ekran bunu söyler — 0 yazmak
0 TL'lik gerçek bir fiyat olurdu.

### Zincir beş adımdır

| # | Adım | Ne yazar |
|---|---|---|
| 1 | `create` | `create_product` — tip · aile · SKU |
| 2 | `details` | ürünü taze oku → `update_product` (ad · url_key · SEO · fiyat · durum · kategoriler, **tek gövdede**) |
| 3 | `book` | kitap künyesi — **düzenleme ekranıyla aynı yoldan** (`save`) |
| 4 | `inventory` | `update_inventory` (stok, depoda — TUZAK 5) |
| 5 | `images` | görseller, **sırayla** (`upload_product_image`) |

**Görsel neden en sonda:** yükleme ucu ürün kimliği istiyor
(`POST /catalog/products/{id}/images`) ve kimlik ancak ürün doğunca oluşuyor.
Formda dosya seçilir, tür/boyut **seçilir seçilmez** denetlenir ve önizlenir;
mağazaya ürün açıldıktan sonra giderler. Tavan **6 dosya × 4 MB**: altı dosyanın
base64'ü 33,6 MB eder ve tek görsel yükleyen ucun zaten kabul ettiği 34 MB'lık
gövdenin altında kalır — yani ürün açma ucu belleğe yeni bir tavan getirmez.
Kalanlar ürün açıldıktan sonra Görseller sekmesinden eklenir (orada sayı sınırı
yok).

**Künye neden ayrı bir yol değil:** `_write_book` doğrudan `save`'i çağırır.
Doğrulama, çözülemeyen niteliğin reddi, seçenek kimliği denetimi, denetim satırı
ve TUZAK 1 koruması hepsi orada duruyor; ikinci bir yol, o kuralların birini
eksik uygulayan bir kopya üretirdi. Bedeli bir ek taze okumadır.

Kuru provada **tek istek** gider: ürün doğmadığı için kimlik yoktur, sonraki
adımlar hayalî bir kimliğe yazmak olurdu — **görsel de yüklenmez**, `steps`
içinde “planlandı” olarak (kaç dosya olduğuyla birlikte) döner.

Bir adım düşerse ürün yine açılmıştır; yanıt kimliği ve düşen adımı söyler (K7).
Görsellerde bu kural **dosya başınadır**: biri patlarsa diğerleri denenir ve
düşenin **adı** yazar — “2 görsel yüklenemedi” hangisini küçülteceğini
söylemiyordu. Künye hatası ise ürün **açılmadan önce** durdurur: geri alınamayan
bir kayıt açıp ardından “ISBN hatalı” demek, kullanıcıyı yarım bir ürünle
bırakırdı.

### Öznitelik ailesi — ada değil ŞEMAYA bakılır

Aile ekranda sorulmaz ama **hangi ailenin seçildiği künyenin yazılıp
yazılmadığını belirler.** Canlıda iki aile var (ölçüldü, 16.08.2026):

| id | kod | Kitap alanları | İçindeki ürün |
|---|---|---|---|
| 1 | `default` / "Varsayılan" | yalnız `desi` | 2 kalem — ikisi de kargoya girmeyen |
| 2 | `kitap` | **dokuzu da** | 1.420 gerçek kitap |

Sıra: ayardaki `default_family_id` → mağazadaki tek aile → **künye
niteliklerini en çok taşıyan aile** → kodu/adı `default` olan aile → ilk aile.

**Neden şema:** eski kural adına bakıp `default` olanı seçiyordu ve sonucu
sessizdi. Mağaza gövdenin anahtarlarını ailenin nitelik listesiyle kesiştirip
fazlasını **hata da uyarı da üretmeden** düşürüyor
(`AdminCatalogProductUpdateProcessor::resolveAttributeCodes`). Ürün "açıldı"
görünüyor, ISBN/yazar/yayınevi/sayfa sayısı hiçbir yere yazılmıyordu. Sayfa
sayısı gidince kargo hesabı da varsayılan **1,0 desiye** çıkıyordu — 176
sayfalık bir kitabın gerçeği 0,18 — yani her yeni üründe müşteriden fazla kargo
alınıyordu. Geri dönüşü de yok: aile ürün açıldıktan sonra gönderilmiyor
(TUZAK 3).

Beraberlikte ve şema okunamadığında **aile uydurulmaz**: karar eski sıraya
düşer ve `default_family_id` ayarı kurulumun kesin sözü olarak her şeyi ezer.

**İkinci kapı:** künye dolu gelmişken hedef aile o nitelikleri taşımıyorsa
ürün **hiç açılmaz** ve hangi alanın nerede olmadığı yazılır. Ekran o alanları
zaten çizmiyor olabilir; istek elle de kurulabilir (K9).

Panelin gösterdiği taslak `POST /products/plan` ile hesaplanır (yazmaz).
Aynı kurallar `POST /products` içinde **yeniden** uygulanır: istek elle de
kurulabilir ve onayla yazma arasında geçen sürede `url_key` kapılmış olabilir
(K9). Kategori listesi panelden `expandParents: false` ile gelir — liste
taslakta genişletilip kullanıcıya gösterildi; ikinci kez genişletmek onun
listeden çıkardığı üst kategoriyi geri koyardı.

## Nitelik ve aile

Nitelik **kataloğun şemasıdır, verisi değildir**. Ürün pasifleştirmek tek satırı
etkiler; nitelik silmek o niteliğin **bütün ürünlerdeki değerini** götürür.

- **Kod ve tip oluşturulduktan sonra değiştirilemez.** Ekranda `static` alan
  olarak çizilir (kutu bile yok) ve nedeni yanında yazar; backend `locked_error`
  ile aynı kuralı tekrar uygular ve `attribute_body` güncellemede bu iki alanı
  gövdeye hiç koymaz.
- **Kullanımdaki nitelik silinmez** — pasifleştirilir. Pasifleştirme üç bayrağı
  indirir (vitrin · süzgeç · zorunluluk); nitelik ve **ürünlerdeki değerleri
  yerinde kalır**, bayraklar geri açılarak işlem geri alınır.
- **Belirsizlik "hayır"dır.** Silme kararı iki ayrı soruya bakar: *nitelik hangi
  ailelerde* (`usageFamilies`) ve *o ailelerde kaç ürün var* (`usageProducts`).
  Birincisi ancak aile düzeninin **tamamı** okunabildiyse bilgidir; bu yüzden
  `_family_usage` üçüncü bir değer döndürür ve `delete_verdict` onu ister. Aksi
  hâlde "aileler okundu, nitelik hiçbirinde yok" (silinebilir) ile "aileler
  okunamadı" (silinemez) ayırt edilemez — ikisi de boş kullanım listesi üretir.
- **Mağaza 409 dönerse** ("bu nitelik bir ailede kullanılıyor") geçidin ham
  metni değil, ne yapılacağını söyleyen bir cümle gösterilir. Ekranın aile
  listesi geçitte 900 sn önbellekli; nitelik bu arada bir aileye eklenmiş
  olabilir ve son sözü mağaza söyler.
- **Aile düzeni gönderilirse mağazadakinin yerine geçer.** "Yalnız adı kaydet"
  ile "Grup düzenini kaydet" bu yüzden **ayrı iki düğmedir**: tek düğme olsaydı
  adı düzeltmek isteyen kullanıcı ailenin gruplarını da yeniden yazardı. Boş
  liste göndermek ailenin şemasını siler; `family_body` gruplar açıkça
  verilmedikçe `attribute_groups` alanını hiç koymaz.
- **Çekirdek nitelikler aileden çıkarılamaz** (`sku`, `name`, `url_key`,
  `status`, …). Çıkarılırsa ürün eklemek tümden durur ve sebebi ekranda hiçbir
  yerde görünmez; ekran eksikleri yazar, backend düzeni reddeder.
- **Aile silme ekranda yoktur.** Mağaza son aileyi ve ürünü olan aileyi zaten
  reddediyor; canlıda iki ailenin ikisinde de ürün var (`attribute_family=2` →
  1.420, `=1` → 1). Her zaman patlayacak bir düğme koymak yerine ne yapılacağı
  yazılır: ürünler başka aileye taşınır, aile boş bırakılır.

## Ürün silme

Silme **gerçek silmedir**, pasifleştirme değil ve geri alınamaz. Karar
kullanıcınındır: *"siparişi gönderildiyse de ürün silinebilir; geçmiş raporda
kırmızı 'silinmiş' ibaresi koyarız."*

**Neden güvenli — ölçüldü, varsayılmadı.** `order_items` ürünün adını,
SKU'sunu, fiyatını ve toplamını **kendi satırında** saklıyor; `product_id`
NULL kabul ediyor ve `products` tablosuna **yabancı anahtar kısıtı yok**
(`2018_09_27_113207_create_order_items_table.php`:51, 58-59 — kısıt yalnız
`order_id` ve `parent_id` için). Silme ne engellenir ne de geçmişi bozar:
kalem yerinde kalır, ürün bağlantısı boşa düşer.

**Mağaza tarafında guard yok.** `AdminCatalogProductDeleteProcessor` yalnız
`catalog.products.delete` iznine bakıp `ProductRepository::delete` çağırıyor;
kaynak dosyanın kendi yorumu: *"No in-order guard (matches monolith
ProductController::destroy)"*. Varyantlar mağaza tarafında zincirle düşüyor.

**Tek gerçek engel veritabanında:** `rma_items.variant_id → products` bağı
`ON DELETE RESTRICT` (`2025_11_14_173959`). Ürün bir iade talebinde geçiyorsa
MySQL silmeyi reddeder ve uç 500 döner. Ekran o ürünü "silinemedi" diye
**ayrı** raporlar ve nedenini yazar; toplu iş yüzünden durmaz.

**Toplu silme neden tek tek yapılıyor.** Mağazada toplu uç var
(`POST /catalog/products/mass-delete`) ama tek ürün patlayınca **tamamına**
500 dönüyor ve hangisinin gittiği yanıttan okunamıyor. "Kısmi başarı gerçekçi
raporlanır" isteği o uçla karşılanamaz.

**Tek seferde en çok 25 ürün.** Hesap: ürün başına dört istek (önizlemede
taze okuma + satış özeti, silmede taze okuma + DELETE), geçit dakikada 55
istekte tutuyor → 25 ürün ≈ iki dakika. Daha büyük temizlik birkaç turda
yapılır; her tur kendi önizlemesini gösterir.

**Dört katmanlı koruma:** ayrı izin (`store_products.delete`) + gerekçe
(≥10 karakter, şemada *ve* serviste) + önizleme (ne silinecek, kaç siparişte
geçti, kaç adet satıldı) + `dryRun` varsayılanı. Silinen satır listeden
**anında** düşer; sayfa yeniden yüklenmeyi beklemez.

**Satış geçmişi üç cevap verir:** sayı · `bilinmiyor` · `uç yok`. Bilinmeyeni
sıfır saymak, "hiç satılmamış" diye gösterip kullanıcıyı yanlış güvenle
sildirmek olurdu.

### Geçit eksikleri (bugün açık)

Bu modül mağazaya **ham istek atmaz** (K4); her şey `store.api` geçidinden
geçer. Silme akışının ihtiyaç duyduğu iki metot geçitte **henüz yok**:

| Geçit metodu | Mağaza ucu | Etkisi |
|---|---|---|
| `delete_product` | `DELETE /api/admin/catalog/products/{id}` | Silme düğmesi açılmaz; ekran "uç yok" der |
| `bbd_bestsellers` | `GET /api/admin/bbd/catalog/bestsellers` | Satış geçmişi `bilinmiyor` görünür |

İkisi de `modules/store_api/` içindedir ve bu görevin dosya sınırının
dışındadır. Eklendikleri gün akış kod değişmeden çalışır: servis metodu
`getattr` ile arıyor, testler ikisinin de bulunduğu ve bulunmadığı hâli
kapsıyor.

## Silinmiş ürün ibaresi

Kural `backend/deleted.py` içinde **saf fonksiyondur** ve
`store_products.deleted_marker` yeteneğiyle ilan edilir; rapor ve sipariş
ekranları kuralı registry'den alır, **kopyalamaz** (K3).

> Kalem ad/SKU taşıyor ama `productId` kataloğa çözülmüyorsa o kalem silinmiş
> üründendir.

**Üç cevap vardır, iki değil.** "Katalog okundu, ürün yok" ile "katalog
okunamadı" ikisi de boş bir kimlik kümesi üretir; ilkinde kalem gerçekten
silinmiştir, ikincisinde hiçbir şey bilinmiyordur. İkisini tek cevaba toplamak,
mağaza bir dakika yanıt vermeyince bütün geçmişi kırmızı boyardı — ibare bir
daha güvenilmez olurdu. Bu yüzden çözümün tam yapılıp yapılmadığı ayrı bir
bayrakla taşınır ve eksikse cevap `doğrulanamadı` olur.

## Tek seçenekli alanlar

Kanal · dil · para birimi · stok deposu · vergi kategorisi ekranda **yok**;
değerleri kendiliğinden gider. **Sert kodlama yok:** karar mağazadan gelen
seçenek sayısından çıkar (`catalog.choice_fields`). İkinci kanal açıldığı gün
alan kendiliğinden geri gelir.

Üç hâl ayrı anlatılır: `none` (okunamadı — hiçbir değer uydurulmaz) ·
`single` (gizlenir, değer uygulanır ve ekranda "kendiliğinden uygulandı"
yazar) · `many` (geri gelir).

İki tür alan var ve farkı sunucu söylüyor (`writable`):

- **Yazılabilen** (depo, vergi kategorisi) — ürünün kendi alanı. >1 seçenekte
  **gerçek form alanı** olarak döner.
- **Yazılamayan** (kanal, dil, para birimi) — ürünün alanı değil; kanal ve dil
  her isteğe ayardan konuyor, para birimi kanalın özelliği. >1 seçenekte
  **uyarı** olarak döner ("mağazada iki kanal var, bu ekran hepsine `default`
  yazıyor"). Olmayan bir seçim kutusu çizip kullanıcının seçtiğini yok saymak
  sessiz yanlış veri demekti.

Nitelik ailesi zaten gizliydi, öyle kaldı. Müşteri grubu bu ekranda yok.

## Ne yapmaz — ve neden

- **Ürünü önizlemeden silmez.** Silme var ve gerçek (aşağıya bakın) ama ne
  silineceği — künye, stok, kaç siparişte geçtiği, kaç adet satıldığı —
  gösterilmeden düğme açılmaz.
- **Tam listeyi çekip istemcide süzmez.** 29 sayfa × dakikada 60 istek sınırı;
  ekran dakikalarca kilitlenir ve hız payı başka araçlara kapanır.
- **Barkod etiketi basmaz.** Rapor üretecinde gerçek barkod çizimi yok; sahte
  barkod basmaktansa hiç basmamak doğrudur.
- **Toplu fiyat/stok/kategoriyi varsayılan olarak uygulamaz.** Mağazada toplu
  uç yok; 1.419 ürüne tek tek PUT atmak yarıda kalırsa kataloğun yarısı yeni
  yarısı eski değerde kalır. Önizleme her zaman çalışır ve CSV olarak alınır.
  Yönetici `bulk_direct_limit` ayarını açarak küçük seçimlere (varsayılan
  tavan 100) sıralı yazmaya izin verebilir; ekran ne olacağını söyler.

## Kitap künyesi ve desi

Katalog kitap satıyor ama künye alanları (**sayfa sayısı · ISBN · yayınevi ·
yazar · baskı yılı · kitap dili · sınav türü · yayın tipi · desi**) hiçbir
ekrandan düzenlenemiyordu. Ürün çekmecesinde artık **Kitap künyesi** sekmesi,
ürün AÇMA formunda da katlanır bir **Gelişmiş alanlar** bölümü var — ikisi de
aynı üreteci ve aynı ipuçlarını kullanır. Tarif **iki uçtan** gelir ve ikisi
ayrı bir soruya cevap verir: `GET /reference` → `bookFields` "katalogda hangi
künye nitelikleri var" (toplu yazma ekranı bunu sorar) · `bookFieldsOnCreate`
"yeni ürünün doğacağı **ailede** hangileri gerçekten yazılabilir". Düzenleme
sekmesi ise açtığı ürünün kendi ailesine bakar (`GET /products/{id}` → `book`).

**Sayfa sayısı yazıldıkça desi anında yeniden hesaplanır** ve rakam mağazanın
hesapladığıyla aynıdır. Zincir:

```
kalınlık_cm = (sayfa × 0,04375 mm + 1,0 mm kapak) / 10
taban_cm²   = (19,5 + 2×1,0) × (27,5 + 2×1,0) = 634,25
desi        = taban_cm² × kalınlık_cm / 3000        (küsurat YUKARI)
```

Öncelik sırası — mağazadaki `DesiCalculator::explainProduct` ile birebir:

1. Ürünün `desi` niteliği doluysa **o kazanır**. Elle girilen değer bir
   ÖLÇÜMDÜR, buradaki hesap bir MODELDİR.
2. `page_count` okunabiliyorsa yukarıdaki hesap.
3. İkisi de yoksa `1,0` desi — bilerek cömert (gerçek bir kitap ≈ 0,2 desi).

Katsayılar **panelde sabit değildir**: `GET /reference` → `desiRules` ile
gelirler. Aynı sayı mağazada (PHP), geçitte (Python) ve ekranda (JS) yaşıyor;
üçünün ayrışması, müşteriden alınan kargo ücretiyle Geliver'a beyan edilen
desinin tutmaması demek olurdu. Python kopyası
`tests/test_store_products_book.py` ile kilitli.

**Nitelik kodları varsayılmaz, çözülür.** Kataloğun hangi kodu kullandığı
kuruluma göre değişir; aday adlar mağazanın nitelik listesinde aranır.
Bulunamayan alan **ekranda açılmaz** ve nedeni yazılır — var olmayan bir koda
yazmak sessiz veri kaybıdır (Bagisto tanımadığı özniteliği yok sayar, istek 200
döner, personel "kaydettim" sanır). Canlıda baskı yılının kodu `print_year`
(ölçüldü, 16.08.2026).

**Tip de varsayılmaz.** Canlıda yayınevi, kitap dili, sınav türü ve yayın tipi
`select` tipinde ve değerlerini **seçenek kimliğiyle** saklıyor (yayınevi =
`76`, "Panama Yayıncılık" değil). Bu alana serbest metin yazmak, olmayan bir
koda yazmakla aynı sessiz kayıptır; bu yüzden:

- `bookFields` alanın **tipini ve seçeneklerini** de taşır, panel açılır kutu
  çizer,
- listede olmayan bir seçenek kimliği **reddedilir** (mağaza tanımadığı kimliği
  sessizce yok sayar ve ürün yayınevsiz kalırdı),
- seçenekleri **okunamayan** seçimli alan hiç açılmaz ve nedeni yazılır.

Seçenekler nitelik **detayından** gelir: liste ucu (`GET /catalog/attributes`)
her satırın `options` alanını `null` döndürüyor (ölçüldü). Bu yüzden seçimli
alan başına bir ek istek gider ve sonuç kodlarla birlikte **bir kez** çözülür.
**Eksik kalan çözüm saklanmaz:** seçenek ucu bir kez patladıysa sonuç
saklansaydı dört seçimli alan, alanın kendi gerekçesi "bağlantı düzelince
kendiliğinden gelir" derken sidecar yeniden başlayana kadar kapalı kalırdı.

**Öznitelik ailesi de varsayılmaz.** "Nitelik katalogda var" ile "nitelik BU
ürüne yazılabilir" ayrı iki sorudur; mağaza gövdeyi ailenin nitelik listesiyle
kesiştirip fazlasını sessizce düşürüyor. Bu yüzden alanın **dört** hâli var:
yazılabilir · katalogda yok · **ürünün ailesinde yok** · seçenekleri okunamadı.
Ürünün ailesi ek istek gerektirmez — ürün detayının `attributes` dizisi zaten
aile kapsamlı geliyor (aile 1 → 22 satır, aile 2 → 36 satır). Aile **okunamazsa
kısıt uygulanmaz**: geçici bir ağ hatasını kalıcı bir eksikliğe çevirmek, bu
modülün her yerde reddettiği davranış.

**Sayfa sayısı ve desi bu esnekliğin dışındadır** ve yalnız `page_count` /
`desi` kodlarını kabul eder: mağazanın kargo hesabı bu ikisini adıyla okuyor,
eş anlamlı bir koda yazmak ekranda "güncellendi" gösterip kargo ücretini hiç
değiştirmezdi.

**Türkçe virgül okunurken kabul, yazılırken çevrilir.** Personel `0,45` yazar
ve ekran bunu doğru gösterir; mağaza tarafı ise aynı değeri PHP `is_numeric()`
ile okuyor ve PHP ondalık ayracı olarak yalnız noktayı tanır — `is_numeric("0,45")`
FALSE'tur. Ham yazılan virgül mağazada **hiç okunmaz** ve ürün varsayılan 1,0
desiye düşer: ekran "1 desi" derken müşteriden başka bir rakam tahsil edilir.
Bu yüzden yazma yolu sayısal alanları kanonikleştirir (`canonical_number`) —
doğrulayıcıyı katılaştırmak, kullanıcıyı kendi dilinde yazmaktan caydırırdı.
Kilidi bir test tutuyor: `patch_for` çıktısında virgül bulunmaz.

**Toplu yazma** yalnız bu iki alan için açıktır (`Sayfa/desi yaz` düğmesi):
önizleme → gerekçe → uygula. ISBN/yazar/yayınevi ürüne özgüdür ve toplu
yazılamaz. "Alanı boşalt" ayrı bir kiptir — yanlışlıkla girilmiş bir `desi`
ölçümü sayfa hesabını ezmeye devam eder ve onu kaldırmanın tek yolu budur.

**Kitap alanları her kaydetmede gövdeye konur** (`write_body(extra=…)`), kitap
sekmesine hiç girilmese bile: kısmi PUT `page_count`'u boşaltabilir ve boşalan
sayfa sayısı ürünü varsayılan 1,0 desiye çıkarır — yani sessizce paraya
dokunur.

## Bagisto EAV tuzakları

Hepsinin karşılığı `backend/catalog.py` içinde bir fonksiyon ve
`tests/test_store_products_catalog.py` içinde adı tuzağı söyleyen bir testtir.

1. Kısmi PUT alanları NULL'lar → `write_body` **oku-değiştir-yaz** kurar.
2. `channel` + `locale` her istekte gider.
3. `attribute_family_id` salt gösterilir, asla gönderilmez.
4. Fiyat tek alan değil: liste + indirimli (tarih pencereli) + grup fiyatları.
5. Stok `product.quantity`'de değil `inventory_sources`'ta; liste değeri
   yaklaşıktır ve `~` ile gösterilir.
6. `url_key` benzersiz → yazmadan önce yoklanır; sunucu süzgeci yok saydıysa
   "bilinmiyor" denir, uydurulmaz.
7. `sku` değişikliği `product_flat`'ı yeniden yazar → ayrı izin, ayrı onay.
8. `status` değişikliği indeksleme ister → ekran gecikmeyi söyler.
9. Siparişi olan ürün **silinebilir**: `order_items` adı/SKU'yu/fiyatı kendi
   satırında saklıyor ve `products`'a yabancı anahtar kısıtı yok
   (`2018_09_27_113207`). Kalem `backend/deleted.py` kuralıyla kırmızı
   "silinmiş" gösterilir.
10. Configurable/bundle/grouped ürünün fiyatı varyantlarındadır → o tipte
    fiyat alanları kapalı, gövdeye fiyat konmaz.

## Uçlar

`/api/store_products` öneki altında. Hepsi `requires(...)` taşır (K9).

`PUT /products/{id}` gövdesinde kitap künyesi **ayrı alandır**: `patch` bu
ekranın sabit alanlarını, `book` ise çalışma anında çözülen nitelik kodlarını
taşır. `POST /bulk/preview` kitap için `kind: "book"` + `field`
(`pageCount` | `desi`) + `mode` (`set` | `clear`) + `value` alır.

`POST /products` ve `POST /products/plan` gövdesi de `book` (künye) ve
`images` (base64 dosyalar) alır. `GET /reference` iki künye listesi döndürür:
`bookFields` (katalog kapsamlı) ve `bookFieldsOnCreate` (yeni ürünün ailesi
kapsamlı) — farkın gerekçesi "Kitap künyesi ve desi" bölümünde.

Okuma: `GET /products` · `GET /products/{id}` · `GET /products/{id}/images` ·
`GET /reference` · `GET /health` · `GET /audit` · `GET /url-key` ·
`GET /settings` · `GET /printer` · `GET /attributes` ·
`GET /attributes/{id}` · `GET /families` · `GET /families/{id}`

Yazma: `PUT /products/{id}` · `POST /products/plan` (yazmaz, taslak) ·
`POST /products` · `POST /products/{id}/copy` ·
`POST /products/{id}/stock` · `POST /products/{id}/categories` ·
`POST /products/{id}/group-price` · `POST /products/{id}/images` ·
`POST /products/{id}/images/reorder` ·
`POST /products/{id}/images/{imageId}/remove` · `POST /products/status` ·
`POST /products/delete/preview` (yazmaz, önizleme) · `POST /products/delete` ·
`POST /order-items/mark` (yazmaz, ibare) ·
`POST /products/{id}/sku` · `POST /bulk/preview` · `POST /bulk/apply` ·
`POST /settings` · `POST /preview` · `POST /print` · `POST /export`

Şema: `POST /attributes` · `PUT /attributes/{id}` ·
`POST /attributes/{id}/deactivate` · `POST /attributes/{id}/delete` ·
`POST /attributes/{id}/options` ·
`POST /attributes/{id}/options/{optionId}/delete` · `POST /families` ·
`PUT /families/{id}`

## İzinler

| Anahtar | Ne açar |
|---|---|
| `store_products.view` | Ekran, rapor, CSV |
| `store_products.manage` | Künye, fiyat, stok, görsel, kategori |
| `store_products.bulk` | Toplu önizleme ve uygulama |
| `store_products.deactivate` | Pasifleştirme (geri alınabilir) |
| `store_products.delete` | Ürün silme (**geri alınamaz**, önizleme + gerekçe zorunlu) |
| `store_products.rename_sku` | SKU değiştirme |
| `store_products.attributes` | Nitelik/aile düzenleme, seçenek, nitelik pasifleştirme |
| `store_products.attributes_delete` | Nitelik/seçenek silme (değer bütün ürünlerden düşer) |

Nitelik yazma `store_products.manage` ile **açılmaz**: ürün düzenlemek tek
kaydı, nitelik düzenlemek o niteliği taşıyan bütün ürünleri etkiler.

## Yerel tablolar

Yalnız Bagisto'da **karşılığı olmayan** veri: `mod_store_products_audit`
(gerekçe), `mod_store_products_bulk` (önizleme jetonu ve fark tablosu),
`mod_store_products_prefs` (kritik eşik). Katalog verisi kopyalanmaz.

## Testler

```bash
.venv/bin/python -m pytest modules/store_products/tests -q
.venv/bin/ruff check modules/store_products
```

Testler **ağa çıkmaz ve canlıya ürün açmaz**: geçit `tests/store_products_fakes.py`
içinde taklit edilir. Ürün açma otomasyonunun saf mantığı (slug üretimi, çakışma
artırımı, üst kategori toplama, meta türetme) DB'siz ve ağsız
`tests/test_store_products_create.py` içinde durur.
