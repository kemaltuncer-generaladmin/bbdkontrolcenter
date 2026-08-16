# Ana Ekran Görselleri

Vitrinin ilk ekranı. Ekranın amacı Bagisto yönetimindeki dağınık tema
ayarlarını tek yerde toplamak; **ne olduğu görünsün, iki tıkla değişsin.**

**EKRANDA YAZILIM TERİMİ YOKTUR.** Dört şerit kullanıcının sözlüğüyle
adlandırılır ve teknik anahtar yalnız kodda kalır:

| Anahtar (kod) | Ekranda görünen ad | Tekil |
|---|---|---|
| `slider` | Ana ekran kayan görseller | kayan görsel |
| `banner` | Tanıtım görselleri | tanıtım görseli |
| `collection` | Öne çıkan ürün grupları | ürün grubu |
| `announcement` | Üst duyuru yazısı | duyuru |

Adlar tek kaynaktan gelir (`backend/slots.py` → `AREA_LABELS` / `AREA_ONE` /
`AREA_WHAT`) ve panelin kopyasıyla ayrışması `tests/test_store_home_media_panel.py`
tarafından yakalanır. Aynı dosya, eski terimlerin (“Slot”, “Slider”, “CMS
sayfası”, “Alt metni”…) ekrana geri sızmasını da kırar.

Grup: **BBD Store** · CSS öneki: `hm` · Rapor rafı:
`Raporlar/Mağaza/Müşteri/<yıl>/<ay>`

## Ne yapar

| Alan | Davranış |
|---|---|
| Önizleme | Üstte ana sayfanın **ölçekli temsili**. Yalnız yayında olan slotlar çizilir; masaüstü/mobil değiştirilebilir. Kutuya tıklamak düzenleyicisini açar. |
| Sekmeler | Ana ekran kayan görseller · Tanıtım görselleri · Öne çıkan ürün grupları · Üst duyuru yazısı. Her sekmenin altında “burası ne işe yarar” tek cümlesi durur. |
| Sıra | Sürükle-bırak **ve** `Ctrl+↑/↓`. Taşıma ekran okuyucuya duyurulur; sıra ayrı bir düğmeyle, gerekçeyle kaydedilir. |
| Görsel | Gizli `<input type=file>` + sürükle-bırak + `FileReader` → base64 gövde. Ölçü **sunucuda** ölçülür. |
| Yükleme | İki yol açık: **“Görseli yükle”** dosyayı mağazanın görsel ucuna gönderir (`POST /image/upload` → geçidin `upload_media`'sı); **“Kaydet”** görseli slot gövdesiyle taşır. Uç henüz yayında olmadığı için bugün çalışan yol ikincisidir. |
| Ölçü denetimi | "Bu bölüm 1920x640 piksel ister; seçtiğiniz görsel 1200x400 — küçük kaldığı için telefonda bulanık çıkar." + "…soldan ve sağdan toplam %25 KESİLECEK." İkisi de her görselin altında durur; **uyarı onaylanmadan yükleme geçmez.** |
| Oran önizlemesi | İki kare: solda görselin **vitrindeki** (önerilen orana `cover` ile oturmuş, yani kırpılmış) hâli, sağda dosyanın **gerçek oranı**. Ölçüler sunucudan gelir. |
| Düzenleyici | Başlık · **görsel açıklaması (zorunlu)** · tıklayınca gidilecek sayfa (ürün/kategori/bilgi sayfası seçici ya da elle adres) · sayfadaki yeri · hangi ekranda · hangi mağaza · yayın tarihleri. **Her alanın altında tek cümlelik ipucu vardır.** |
| Süzgeçler | Arama (başlık, adres, açıklama) · durum · sayfadaki yeri · hangi ekranda · mağaza · tarih aralığı. Hepsi varsayılan olarak “Hepsi” gelir. |
| Engeller | Bir iş yapılamıyorsa ekran **neden**i ve **sıradaki adım**ı birlikte yazar (`BLOCKERS` — deseni `store_shipping/backend/geliver.py` → `BLOCKER_ACTIONS`). Kapalı düğme de nedenini `title` ve `aria-label` ile söyler. |
| Çıktı | Yazdırılabilir rapor PDF · ekrandaki liste CSV · her şey CSV. |

## Ne yapmaz — ve neden

- **Slot silmez.** Yayından kaldırılan slot listede kalır; tıklama geçmişi ve
  "geçen kampanyada ne asmıştık" bilgisi silinmez (ADR 0012).
- **Kaydetmek yayına almaz.** Yayın ayrı bir izindir
  (`store_home_media.publish`) ve ayrı düğmedir; yeni slot her zaman **taslak**
  açılır. Düzenleme yaması `status` alanını taşımaz — taşısaydı yalnız `manage`
  izni olan biri ayrı tutulan izni arka kapıdan atlatırdı (K9).
- **Görseli kırpmaz, büyütmez.** Bulanık bir görseli sessizce büyütmek onu daha
  da bozardı; ekran ölçüyü ve hangi kenardan ne kadar kırpılacağını söyler,
  kararı kullanıcı verir — ve karar `mod_store_home_media_assets` tablosuna
  "uyarıya rağmen yüklendi" diye geçer.
- **Önizlemeyi tek kareye sığdırmaz.** Sabit bir çerçeveye `cover` ile
  sığdırılan önizleme, tam da uyarmaya çalıştığımız kırpmayı gizlerdi.
- **Süzgeç açıkken sıra değiştirtmez.** Sıra tüm şeridi ilgilendirir; süzülmüş
  bir alt kümeyi sıralamak kalan slotları rastgele yerlere atardı.
- **Canlı sayfayı `iframe` ile göstermez.** CSP'de `frame-src` yok;
  `default-src 'self'` düşer ve davranış WebKitGTK'da öngörülemez. Önizleme
  şeritleri **gerçek oranlarında** çizen bir temsildir ve öyle olduğunu söyler.

## Tuzaklar

Hepsinin karşılığı `backend/slots.py` içinde bir fonksiyon ve
`tests/test_store_home_media_slots.py` içinde adı tuzağı söyleyen bir testtir.

1. **Sıra ucu GLOBAL liste ister.** Panel yalnız açık şeridin sırasını
   gönderir; `merged_order` onu global sıraya oturtur ve diğer üç şeridi
   yerinde tutar. Eksik/fazla kimlikle gelen (bayat ekrandan) istek reddedilir.
2. **Tarayıcının bildirdiği ölçüye güvenilmez.** `image_dimensions` PNG/JPEG/
   WebP/GIF başlığını kendisi okur. Pillow kurulmaz: dört başlık kırk satır,
   Pillow 40 MB (K11).
3. **Görsel base64 gövdede taşınır** (Tauri'de dosya sistemi eklentisi yok).
   Tavan, MIME ve gerçek içerik `decode_image` içinde denetlenir — beyan edilen
   türe değil dosyanın kendisine bakılır.
4. **`alt` metni zorunlu.** Ekran okuyucu ve arama motoru banner'ı yalnız ondan
   okur; boşsa yazma reddedilir (panelde de, backend'de de).
5. **Yayın penceresi yerel takvim gününe göre.** `today_iso` UTC'ye kaymaz;
   saatli tarihler güne indirgenir.
6. **Bilinmeyen alan düşürülmez**, `banner` sayılır: vitrinde duran bir slot
   ekranda görünmezse kimse onu kaldıramaz.
7. **BBD uçları yazılıyor**, alan adları kesinleşmedi → `pick` aynı bilgiyi
   birkaç adda arar. Liste okunamazsa ekran tema kayıtlarından **salt okunur**
   dolar ve nedenini söyler (K7).
8. **Sabit çerçeveli önizleme kırpmayı gizler.** `preview_box` gerçek oranı
   koruyan kutuyu, `crop_plan` hangi kenardan yüzde kaç gittiğini verir.
   Ölçü tarayıcıdan değil sunucudan gelir (aynı sebep: TUZAK 2).
9. **Yükleme adı latin-1 başlıkta patlar, uzantı yalan söyler.**
   `safe_filename` adı ASCII'ye indirir (`Ekran Görüntüsü.png` →
   `ekran-goruntusu.png`) ve uzantıyı beyan edilen değil **gerçek** MIME'dan
   yazar (`afis.jpg` + PNG içerik → `afis.png`).

## Uçlar

`/api/store_home_media` öneki altında. Hepsi `requires(...)` taşır (K9).

Okuma: `GET /slots` (liste + önizleme + özet) · `GET /reference` ·
`GET /link-search` · `GET /audit` · `GET /printer`

Yazma: `POST /slots` · `PUT /slots/{id}` · `POST /slots/{id}/status` ·
`POST /reorder` · `POST /image/check` (yazmaz, ölçer) · `POST /image/upload` ·
`POST /preview` · `POST /print` · `POST /export`

### Mağaza tarafının canlı durumu (2026-08-16'da ölçüldü)

| Mağaza ucu | Ne için | Durum |
|---|---|---|
| `GET /api/admin/bbd/storefront/carousels` | Slot listesi | **200 — çalışıyor** |
| `PATCH /api/admin/bbd/storefront/carousels/{id}` | Slot güncelleme / yayına alma | **rota listesinde var** |
| `POST /api/admin/bbd/home/slides` | Ayrı görsel yükleme | 404 — yok |
| `POST .../storefront/carousels` | Slot **ekleme** | rota yok (405) |
| `PUT .../storefront/carousels/reorder` | Sıralama | rota yok (404) |

İlk iki satır bir dönem "henüz yayında değil" diye anlatılıyordu; **artık öyle
değil.** Salt okunur dal ve `pending` anlatımı yerinde duruyor (K7 — uç bir gün
geri çekilirse ekran ayakta kalmalı), ama salt okunur görünüme düşmek bugün
"paket eksik" değil "liste o an okunamadı" demektir; ekran da artık bunu yazar.

`POST /image/upload` mağaza tarafındaki `POST /api/admin/bbd/home/slides`
ucuna bağlıdır ve **o uç henüz yayında değil** (2026-08-16'da yeniden ölçüldü:
hâlâ 404). Yanıt `{"ok": false, "pending": true}` olur; bu bir hata değildir ve
ekran onu bilgi kutusuyla "uç hazır olunca açılacak" diye anlatır (K7). Deneme
yine de gerekçesiyle denetim izine `beklemede` sonucuyla geçer.

Oran/ölçü uyarısı **onaylanmadan** yükleme geçmez: `acknowledged` bayrağı
gelmezse servis dosyayı ağa hiç çıkarmaz ve uyarıyı geri gönderir (K9 — panelde
onay kutusu göstermek yetkilendirme değildir).

## İzinler

| Anahtar | Ne açar |
|---|---|
| `store_home_media.view` | Ekran, önizleme, rapor, CSV |
| `store_home_media.manage` | Slot künyesi, görsel, hedef, yayın aralığı, sıra |
| `store_home_media.publish` | Yayına alma / yayından kaldırma (silme yok) |

## Yerel tablolar

Yalnız Bagisto'da **karşılığı olmayan** veri: `mod_store_home_media_audit`
(gerekçe) ve `mod_store_home_media_assets` (yüklenen görselin ölçü kararı —
"uyarıya rağmen yüklendi" izi). Slotların kendisi mağazadadır ve kopyalanmaz.

## Ayarlar

`config/default.yaml`: kanal/dil, alan başına önerilen ölçü
(`recommended_slider` …), bulanıklık eşiği (`sharp_ratio`), oran toleransı
(`aspect_tolerance`), görsel tavanı (`max_image_bytes`), izinli MIME türleri.
Tema değişirse önerilen ölçüler buradan güncellenir; koda gömülü değildir.

## Testler

```bash
.venv/bin/python -m pytest modules/store_home_media/tests -q
.venv/bin/ruff check modules/store_home_media
```
