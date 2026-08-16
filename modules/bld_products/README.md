# Ürün Yönetimi (`bld_products`)

BLD ürün kataloğunun (`menus`, `categories`, `veykemtu_menu_soldout`) Kontrol
Merkezi ekranı. Ürün burada doğar, fiyatlanır, görsellenir ve satıştan kalkar;
**hangi gün satılacağı** Günlük Menü ekranının işidir. İki alan bilinçli olarak
ayrıdır: katalog haftalarca değişmez, günlük menü her gün değişir.

Sözleşme: `BLD/docs/control/products.md` + `00-genel.md`.
Geçit: `bld.api` (K4) — donmuş metot tablosu `modules/bld_api/README.md` §3.

Grup: **BLD** · İzinler: `bld_products.view`, `bld_products.manage`,
`bld_products.retire`

## Neden üç izin

| İzin | Kapsam |
|---|---|
| `bld_products.view` | Katalog, kategori ağacı, yerel deneme izi, ekran tercihi |
| `bld_products.manage` | Ürün açma/düzenleme, **yeniden satışa açma**, görsel, tükendi işareti, kategori |
| `bld_products.retire` | **Ürünü satıştan kaldırma** (`menu_status = 0`) |

Üçüncüsü ayrı durur çünkü satıştan kaldırma ürünü siteden ve sipariş yolundan
düşürür; sonucu ilk fark eden çoğu zaman müşteridir. `manage` fiyat ve ad
düzeltmesi için günlük bir yetkidir, kaldırma bir karardır.

**Ayrım kâğıt üstünde kalmasın diye `PATCH status: false` REDDEDİLİR.** Sunucuda
`PATCH status: false` ile `DELETE /{menu}` aynı sonucu üretiyor (`menu_status = 0`);
ikisine farklı izin verip birini serbest bırakmak `retire` iznini süs hâline
getirirdi. Yeniden **açmak** (`status: true`) serbesttir — ürünü satışa döndürmek
yıkıcı değildir ve sözleşmede ayrı bir "restore" ucu yok.

Yıkıcı işlem PIN değil **gerekçe** ister (ADR 0012): ayrı izin anahtarı + gerekçe
(en az 10 karakter, backend'de de doğrulanır) + çift denetim satırı. Hiçbir izin
`destructive: true` taşımaz — o bayrak çekirdekte PIN kapısına bağlanacak ve bu
ekran PIN istemiyor.

## Uçlar (16)

| Metot | Yol | İzin |
|---|---|---|
| GET | `/overview` | view |
| GET | `/products` | view |
| GET | `/products/{menu_id}` | view |
| GET | `/categories` | view |
| GET | `/audit` | view |
| GET · PUT | `/prefs` | view |
| POST | `/products` | manage |
| PATCH | `/products/{menu_id}` | manage |
| PUT · DELETE | `/products/{menu_id}/image` | manage |
| POST · DELETE | `/products/{menu_id}/sold-out` | manage |
| POST | `/categories` | manage |
| PATCH | `/categories/{category_id}` | manage |
| POST | `/products/{menu_id}/retire` | **retire** |

**Silen uç yoktur.** Ürün `retire` ile satıştan kalkar, kategori `status: false`
ile gizlenir. `DELETE /categories/{id}` sözleşmede de yok: kategori silmek
altındaki ürünleri kategorisiz bırakır ve site menüsünü sessizce boşaltırdı.
İki `DELETE` kayıt silmez — biri görsel bağını, öteki bugünkü tükendi işaretini
kaldırır.

`PUT /prefs` **gerekçe istemez ve `view` ile yazılır**: yerel bir tabloya yazıyor
ve BLD'de hiçbir şey değiştirmiyor. Her sayfa boyutu değişikliğinde gerekçe
istemek gerekçenin kendisini anlamsızlaştırırdı.

## Bilinmesi gereken beş şey

1. **Her yazmada `dry_run=` AÇIKÇA geçilir.** Geçidin varsayılanı ayardan gelir
   ve `config/local.yaml` git dışıdır; bayrağı atlayan bir çağrı hiçbir şey
   yazmadan `{"ok": true}` alabilir ve ekran "kaydedildi" der. `_dry()` her
   zaman gerçek bir `bool` üretir. Bir test bunu bütün yazma metotlarına karşı
   doğruluyor.
2. **Okumalar ASLA fırlatmaz** (K7): `{"ok": true, "connected": false, "error": …}`.
   `ok` OKUMANIN başarısını değil UCUN sağlığını anlatır; ayrımı `connected`
   taşır ve panel onu okumak zorundadır. Süzgeç sözleşmesi yerel üretilir, yani
   geçit düşse bile kutular ve boş liste çizilebilir.
3. **Paket ürününe fiyat yazılmaz.** `is_package_product: true` olan ürünün
   gerçek fiyatı o günün paket fiyatıdır; istek gönderilmeden engellenir ve
   panelde fiyat alanı kapalı çizilir.
4. **Kısmi gövde `fields` altında yuvalıdır** ve tanınmayan anahtar
   **reddedilir**. Laravel bilmediği alanı sessizce yok sayar; "kaydedildi"
   diyen bir ekranın arkasında hiçbir yere yazılmamış değer bırakmak açık bir
   hatadan pahalıdır. `description: null` boşaltmak, anahtarın hiç bulunmaması
   dokunmamak demektir.
5. **Görsel base64 gider, multipart değil.** İmza kanonik dizesi ham gövdeyi
   hashliyor; gövdeyi yeniden kodlayan herhangi bir vekil imzayı bozar ve arıza
   sahada "sır yanlış" gibi görünür. Çözme, boyut ve içerikten tür okuma
   geçidin işidir (`bld_api/backend/upload.py`); bu modül baytı yalnız taşır ve
   **denetim izine içerik yazmaz** (`00-genel.md` §8.2).

## Yerel tablolar

| Tablo | İçerik |
|---|---|
| `mod_bld_products_audit` | Yazma **denemeleri** — istek çıkmadan `denendi` ile yazılır. |
| `mod_bld_products_prefs` | Ekran tercihi (sayfa boyutu, süzgeç, sıralama). |

Uzak veri **kopyalanmaz**. BLD'nin kendi defteri (`veykemtu_control_audit`)
yalnız sunucuya ULAŞAN isteği bilir; ağ koparsa, acil fren kapatırsa ya da imza
reddedilirse (doğrulama denetleyiciden önce çalışıyor) "kim neyi denedi"
sorusunun cevabı yalnız burada kalır. Panelin "Deneme kaydı" sekmesi o satırları
**Sonucu bilinmiyor** diye işaretler.

## Ekran

Üç sekme: **Ürünler** (sayfalı liste + küçük resim sütunu, çekmecede künye ·
görsel · durum), **Kategoriler** (ağaç, çekmecede düzenleme), **Deneme kaydı**.

**Yoklama yoktur.** Katalog haftalarca değişmez ve `00-genel.md` §2'deki yoklama
bütçesi tablosunda bu ekran yok; tazeleme düğmeye bağlıdır. Süzme ve sıralama
**sunucudadır** — istemcide süzmek yalnız görünen sayfayı süzerdi.

## Doğrulama

```bash
.venv/bin/python -m pytest modules/bld_products
.venv/bin/ruff check modules/bld_products
node --check modules/bld_products/ui/panel/index.js
```

Testler ağa çıkmaz: `FakeApi` geçidin donmuş imzalarını **birebir** taşır ve
alanları `**kwargs` ile yutmaz — uydurma bir alan adı canlıda `AttributeError`
verir ve servis istisnayı K7 gereği yuttuğu için hata ekranda "BLD'ye
ulaşılamadı" diye görünürdü; yani yanlış ad, düşmüş bir sunucudan ayırt
edilemezdi.

## Sözleşmede eksik görülenler

Uydurulmadı, olduğu gibi bildiriliyor:

1. **Seçenekler salt okunur.** `menu_options` yazan bir uç sözleşmede yok;
   düzenleme TastyIgniter yönetim panelinde kalıyor. Ekran gösterir, düğme
   çizmez ve nedenini yazar.
2. **`description` uzunluk sınırı yazılı değil.** Gövdede bir üst sınır
   zorlanmıyor; sınır sunucunun sütunudur ve aşılırsa `422` ile döner.
3. **`minimum_qty` alt sınırı yazılı değil.** Yalnız negatif değer reddediliyor;
   "en az 1" kuralı uydurulmadı.
4. **Görselsiz ürün sayısı sayılamıyor.** Ancak tam tarama ile bulunur ve o
   tarama bu ekranın istek bütçesini aşar; `overview.counts.no_image` **-1**
   döner ("bilinmiyor") ve panel kutuyu çizmez — sıfır yazmak yalan olurdu.
5. **İzin adı ayrımı.** `00-genel.md` §10 bu alan için `bld_menu.view/manage`
   diyor; modül iskeletinde donmuş manifest `bld_products.*` taşıyordu ve
   Kontrol Merkezi izin kataloğu modül kimliğine bağlı. Manifest korundu.
