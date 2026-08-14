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
| Düzenleyici | Çekmecede 8 sekme: Genel · Fiyat · Stok · Görseller · Varyantlar · Kategoriler · SEO · Geçmiş. |
| Görsel | **Yükleme buradan yapılır**: çoklu seçim + sürükle-bırak, sırayla yükleme, ilerleme çubuğu, sıralama (ilk = kapak), kaldırma. |
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

| Alan | Nasıl doluyor |
|---|---|
| `url_key` | Ürün adından; Türkçe harfler katlanır (ı→i, ş→s, ğ→g, ü→u, ö→o, ç→c). |
| `url_key` çakışması | **Yazmadan önce** mağazaya sorulur; doluysa `-2`, `-3` diye artar (TUZAK 6). |
| Kategoriler | Seçilen yaprağın **üst kategorileri ağaçtan okunup** eklenir (roman → kitap). Ağaç geçitten gelir, varsayılmaz. |
| Öznitelik ailesi | Ekranda sorulmaz; `_default_family()` çözer (tek satıcı, tek ürün tipi). |
| `meta_title` / `meta_description` | Boşsa ürün adından ve kısa açıklamadan türetilir; açıklama **düz metne indirilir** (zengin metin etiketi meta alanına sızmaz), 60/160 karakterde sözcük sınırında kırpılır. |
| Stok | Girilmediyse depoya **0 yazılır** — ürün açıkça “stokta yok” doğar, stok takibinin dışında kalmaz. |
| Durum | Yeni ürün **pasif** doğar; çekmecedeki kutu ile aktif açılabilir. |
| Vergi kategorisi | Mağazada tek tanesi varsa ekranda sorulmaz, kendiliğinden uygulanır ve rozetle söylenir; ikincisi açılırsa alan geri gelir ve **hiçbiri sessizce seçilmez**. |
| Depo | Tek depo varsa alan yoktur; ikinci depo açılırsa seçim alanı geri gelir. |

Fiyat **uydurulmaz**: boş bırakılırsa yazılmaz ve ekran bunu söyler — 0 yazmak
0 TL'lik gerçek bir fiyat olurdu.

Akış dört istektir: `create_product` (tip · aile · SKU) → ürünü taze oku →
`update_product` (ad · url_key · SEO · fiyat · durum · kategoriler, **tek
gövdede**) → `update_inventory` (stok). Kuru provada **tek istek** gider: ürün
doğmadığı için kimlik yoktur, sonraki adımlar hayalî bir kimliğe yazmak olurdu.
Bir adım düşerse ürün yine açılmıştır; yanıt kimliği ve düşen adımı söyler (K7).

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
