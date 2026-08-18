# Ana Ekran Görselleri

**Ekranın tek bir işi var:** siteye ilk girişte dönen ~10 görseli değiştirmek,
sıralarını belirlemek ve tıklanınca nereye gideceklerini seçmek.

**EKRANDA YAZILIM TERİMİ YOKTUR.** "Slot", "Slider", "Banner", "Alt metni",
"CMS sayfası" gibi terimlerin ekrana geri sızması
`tests/test_store_home_media_panel.py` tarafından kırılır.

Grup: **BBD Store / Vitrin ve İçerik** · CSS öneki: `hm`

## 18.08.2026 — üç sekme kaldırıldı

Ekran dört şeridi birlikte yönetiyordu: kayan görseller, tanıtım görselleri,
öne çıkan ürün grupları, üst duyuru yazısı. Üçünün de mağazada bu uçtan
yazılabilir bir karşılığı yoktu; kodun ve ekranın yarısı "bu bölüm şu an
düzenlenemiyor" demeye çalışıyordu. Kullanıcı kararı üçünün de kaldırılması
oldu.

Onlarla birlikte giden yüzeyler: yayın tarihleri, cihaz seçimi
(masaüstü/mobil), yayına alma-kaldırma, süzgeçler, durum rozetleri, KPI
şeridi, yerleşim raporu (PDF), CSV ve `store_home_media.publish` izni.

Bir yan fayda daha: `area_of` "tanımadığı değeri banner sayar" diyordu ve
mağazadan gelen gerçek kayan görsel şeridi bu yüzden **yanlış sekmede**
görünüyordu. Sekme kalmayınca yanlış sekme de kalktı.

## Ne yapar

| Alan | Davranış |
|---|---|
| Şerit | Üstte müşterinin gördüğü **sıranın** temsili: görseller önerilen oranlarında, soldan sağa. Kutuya tıklamak o görselin penceresini açar. |
| Liste | Tek liste. Her satırda küçük resim, görselin adı (yalnız personel görür) ve tıklanınca gidilecek adres. |
| Sıra | Sürükle-bırak **ve** `Ctrl+↑/↓`. Taşıma ekran okuyucuya duyurulur. Sıra ayrı bir işlem değildir: liste yazıldığında sıra da yazılır. |
| Görsel | Gizli `<input type=file>` + sürükle-bırak + `FileReader` → base64 gövde. Ölçü **sunucuda** ölçülür. |
| Ölçü denetimi | "Ana ekran 1920x640 piksel ister; seçtiğiniz görsel 1200x400 — küçük kaldığı için telefonda bulanık çıkar." + "…soldan ve sağdan toplam %25 KESİLECEK." **Uyarı onaylanmadan yükleme geçmez.** |
| Oran önizlemesi | İki kare: solda görselin **ana ekrandaki** (önerilen orana `cover` ile oturmuş, yani kırpılmış) hâli, sağda dosyanın **gerçek oranı**. Ölçüler sunucudan gelir. |
| Hedef | Adres kutusu (`/kampanya` ya da `https://…`) + "Ürün ara" yardımcısı: ürünü seçince adresi kutuya yazar. |
| Kaydetme | Tek düğme, tek gerekçe. Önce bekleyen dosyalar yüklenir, sonra liste (sıra + ad + adres) tek istekte yazılır. |
| Engeller | Bir iş yapılamıyorsa ekran **neden**i ve **sıradaki adım**ı birlikte yazar (`BLOCKERS` — deseni `store_shipping/backend/geliver.py` → `BLOCKER_ACTIONS`). Kapalı düğme de nedenini `title` ve `aria-label` ile söyler. |
| Geçmiş | "Değişiklik geçmişi" çekmecesi: kim, ne zaman, hangi gerekçeyle yazdı (`GET /audit`). |

## Ne yapmaz — ve neden

- **Listeyi boşaltmaz.** Son görsel de çıkarılırsa ana sayfanın en üstü bomboş
  kalır; hem ekran, hem servis, hem mağaza ucu bunu reddeder.
- **Görseli kırpmaz, büyütmez.** Bulanık bir görseli sessizce büyütmek onu daha
  da bozardı; ekran ölçüyü ve hangi kenardan ne kadar kırpılacağını söyler,
  kararı kullanıcı verir — ve karar `mod_store_home_media_assets` tablosuna
  "uyarıya rağmen yüklendi" diye geçer.
- **Gerekçesiz yazmaz** (ADR 0012). Bagisto denetim tutuyor ama gerekçe alanı
  yok; not `mod_store_home_media_audit` içinde kalır.
- **Önizlemeyi tek kareye sığdırmaz.** Sabit bir çerçeveye `cover` ile
  sığdırılan önizleme, tam da uyarmaya çalıştığımız kırpmayı gizlerdi.
- **Canlı sayfayı `iframe` ile göstermez.** CSP'de `frame-src` yok;
  `default-src 'self'` düşer ve davranış WebKitGTK'da öngörülemez.
- **Kısmi güncelleme yapmaz.** Sıra, dizinin kendi sırası olduğu için "yalnız
  3. satırı güncelle" diye bir işlem tanımlanamaz; sırayı iki isteğe bölmek de
  arada vitrini yarım listeyle çizerdi.

## Tuzaklar

Hepsinin karşılığı `backend/slots.py` içinde bir fonksiyon ve
`tests/test_store_home_media_slots.py` içinde adı tuzağı söyleyen bir testtir.

1. **Yanıt camelCase, istek snake_case.** Bagisto'nun yönetici zarfı çıktıyı
   camelCase'e çeviriyor. `pick` bir dönem yalnız verilen adı deniyordu ve
   belirtisi şuydu: **`sortOrder` her satırda 0 görünüyordu.** Değer geliyordu,
   biz `sort_order` diye arıyorduk. Aynı düzeltme
   `store_customers/backend/analytics.py` içinde zaten yapılmıştı.
2. **Tarayıcının bildirdiği ölçüye güvenilmez.** `image_dimensions` PNG/JPEG/
   WebP/GIF başlığını kendisi okur. Pillow kurulmaz: dört başlık kırk satır,
   Pillow 40 MB (K11).
3. **Görsel base64 gövdede taşınır** (Tauri'de dosya sistemi eklentisi yok).
   Tavan, MIME ve gerçek içerik `decode_image` içinde denetlenir — beyan edilen
   türe değil dosyanın kendisine bakılır.
4. **Sabit çerçeveli önizleme kırpmayı gizler.** `preview_box` gerçek oranı
   koruyan kutuyu, `crop_plan` hangi kenardan yüzde kaç gittiğini verir.
5. **Yükleme adı latin-1 başlıkta patlar, uzantı yalan söyler.**
   `safe_filename` adı ASCII'ye indirir (`Ekran Görüntüsü.png` →
   `ekran-goruntusu.png`) ve uzantıyı beyan edilen değil **gerçek** MIME'dan
   yazar (`afis.jpg` + PNG içerik → `afis.png`).

Ölçü **bilinmiyorsa satırda sessiz kalınır.** Mağazanın slayt ucu en/boy
taşımıyor; on satırın hepsinde "ölçü okunamadı" yazmak, gerçekten sorunlu tek
görseli gürültünün içinde kaybederdi. Karar yalnız ölçü eldeyken yazılır (taze
seçilen dosyada `/image/check` ölçer).

## Uçlar

`/api/store_home_media` öneki altında. Hepsi `requires(...)` taşır (K9).

| Yöntem | Yol | İzin |
|---|---|---|
| GET | `/slides` (sıralı liste) | `view` |
| GET | `/audit` | `view` |
| GET | `/link-search` | `manage` |
| POST | `/image/check` (yazmaz, ölçer) | `manage` |
| POST | `/image/upload` | `manage` |
| PUT | `/slides` (TAM liste: sıra + ad + adres) | `manage` |

Kaldırılanlar: `/slots*` · `/reorder` · `/reference` · `/preview` · `/print` ·
`/printer` · `/export`.

### Mağaza tarafı (18.08.2026'da yazıldı)

| Mağaza ucu | Ne için |
|---|---|
| `GET /api/admin/bbd/storefront/home-slides` | Sıralı slayt listesi |
| `PUT /api/admin/bbd/storefront/home-slides` | Tam listeyi yazar |
| `POST /api/admin/bbd/storefront/home-slides/image` | Tek görsel yükler |
| `PATCH /api/admin/bbd/storefront/carousels/{id}` | Şeridin kendisini aç/kapat + sıra |

Kaynak: `bbdstore/bagisto/packages/BBD/ControlApi` → `StorefrontController`.
Uç `theme_customization_translations.options.images[]` dizisini yazar; her
öğe `{title, link, image}` taşır ve **`image` serbest yol olamaz** — yalnız
yükleme ucunun döndürdüğü klasör (`storage/theme/{id}/sliders/…`) kabul edilir.
Yazma sonrası mağaza `responsecache:clear` çağırır; panel eskiden yalnız
"birkaç dakika sürebilir" diyen bir uyarı gösteriyordu.

`POST /api/admin/bbd/home/slides` diye bir uç **hiç var olmadı**; geçitteki
`upload_media` o adı çağırdığı için her yükleme 404 alıyordu. Yol düzeltildi.

Oran/ölçü uyarısı **onaylanmadan** yükleme geçmez: `acknowledged` bayrağı
gelmezse servis dosyayı ağa hiç çıkarmaz ve uyarıyı geri gönderir (K9 — panelde
onay kutusu göstermek yetkilendirme değildir).

## İzinler

| Anahtar | Ne açar |
|---|---|
| `store_home_media.view` | Ekran ve değişiklik geçmişi |
| `store_home_media.manage` | Görsel yükleme, sıra, ad ve adres yazma |

`store_home_media.publish` **kaldırıldı**: slaytta "taslak" diye bir durum yok,
liste yazıldığı anda vitrinde o liste dönüyor. Karşılıksız kalan bir izin
anahtarı, rol matrisinde bir şeyi koruduğunu sanmaktan başka işe yaramaz.

## Yerel tablolar

Yalnız Bagisto'da **karşılığı olmayan** veri: `mod_store_home_media_audit`
(gerekçe) ve `mod_store_home_media_assets` (yüklenen görselin ölçü kararı —
"uyarıya rağmen yüklendi" izi). Slaytların kendisi mağazadadır ve kopyalanmaz.

İki tablonun `area` sütunu ŞEMADA KALDI ama artık tek değer yazılıyor
(`slider`): eski satırlar diğer üç şeridi taşıyor ve tablo silinmiyor (BBD veri
silme yasağı). Sütunu düşürmek geçmiş kaydı okunamaz hâle getirirdi.

## Ayarlar

`config/default.yaml`: kanal/dil, önerilen ölçü (`recommended_slider`),
bulanıklık eşiği (`sharp_ratio`), oran toleransı (`aspect_tolerance`), görsel
tavanı (`max_image_bytes`), izinli MIME türleri, ürün arama tavanı.
Tema değişirse önerilen ölçü buradan güncellenir; koda gömülü değildir.

## Testler

```bash
.venv/bin/python -m pytest modules/store_home_media/tests -q
.venv/bin/ruff check modules/store_home_media
```
