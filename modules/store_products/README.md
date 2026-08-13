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

## Ne yapmaz — ve neden

- **Ürün silmez.** Siparişi olan ürün silinirse geçmiş siparişlerin kalemleri
  öksüz kalır. Her yerde pasifleştirme vardır (ADR 0012).
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
9. Siparişi olan ürün silinmez → `status=0`.
10. Configurable/bundle/grouped ürünün fiyatı varyantlarındadır → o tipte
    fiyat alanları kapalı, gövdeye fiyat konmaz.

## Uçlar

`/api/store_products` öneki altında. Hepsi `requires(...)` taşır (K9).

Okuma: `GET /products` · `GET /products/{id}` · `GET /products/{id}/images` ·
`GET /reference` · `GET /health` · `GET /audit` · `GET /url-key` ·
`GET /settings` · `GET /printer` · `GET /attributes` ·
`GET /attributes/{id}` · `GET /families` · `GET /families/{id}`

Yazma: `PUT /products/{id}` · `POST /products` · `POST /products/{id}/copy` ·
`POST /products/{id}/stock` · `POST /products/{id}/categories` ·
`POST /products/{id}/group-price` · `POST /products/{id}/images` ·
`POST /products/{id}/images/reorder` ·
`POST /products/{id}/images/{imageId}/remove` · `POST /products/status` ·
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
| `store_products.deactivate` | Pasifleştirme (silme yok) |
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
