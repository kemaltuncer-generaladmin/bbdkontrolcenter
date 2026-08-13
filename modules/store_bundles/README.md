# Setler

Set/paket bileşenlerini kurar ve tek soruyu cevaplar: **bu seti bu fiyata
satarsak kâr mı ediyoruz?**

Grup: **BBD Store** · CSS öneki: `sb` · Rapor rafı:
`Raporlar/Mağaza/Ürün/<yıl>/<ay>`

## Canlıdaki set düzeni

Mağazada **set diye bir ürün tipi yok** — Bagisto'nun `bundle` tipi
kullanılmıyor. Canlıdaki düzen üç parçadan oluşuyor:

| Parça | Nerede | Bu ekran ne yapıyor |
|---|---|---|
| Setler kategorisi (id **42**, slug `setler`) | Bagisto kataloğu | Set listesini buradan kurar |
| `product_cross_sells` bağları | Bagisto | “Çapraz satıştan al” ile bileşen olarak içeri alır |
| Ana sayfa carousel'i (`theme_customizations`, `image_carousel`, id **12**) | Bagisto | Yalnız **okur**: “ana sayfa” rozeti. Düzenleme `store_home_media`'da |

Bu düzenin taşımadığı üç bilgi var ve **set hesabı tam onlara dayanıyor**:
**adet**, **bileşen indirimi**, **zorunlu/opsiyonel**. Çapraz satış bağı
yalnız "şu ürünle şu ürün ilişkili" der. Bu yüzden set künyesi
`mod_store_bundles_plan` tablosunda durur; fiyat, stok ve durum her zaman
mağazadan taze okunur ve **kopyalanmaz**.

`/api/admin/bbd/bundles` uçları yayına girdiğinde tanım kendiliğinden oradan
gelmeye başlar; ekran hangi kaynağı kullandığını üstteki şeritte söyler.

## Ne yapar

| Alan | Davranış |
|---|---|
| Liste | `splitView` — solda setler (ad · set fiyatı · **kâr** · durum), sağda düzenleyici. Küme küçük olduğu için tek istekte gelir, süzme ekranda yapılır. |
| Süzgeçler | Arama · Durum · Geçerlilik · Anahtarlar: **Zararına satılıyor** · **Bileşeni tükenmiş** · **Bileşeni pasif** |
| Bileşen tablosu | Ürün seç (sunucu tarafı arama) → adet → bileşen indirimi (%) → zorunlu/opsiyonel |
| Canlı hesap | bileşen toplamı → bileşen indirimi → ara toplam → set indirimi → **KDV** → set fiyatı → **KÂR/ZARAR** + satılabilir set adedi |
| Fiyatlandırma | Sabit set fiyatı **veya** bileşen toplamı − %x (fiyat türetilir) |
| Stok | Set stoğu = **en kısıtlı zorunlu bileşen** (`stok ÷ adet`) |
| Çıktı | Set listesi PDF · Riskli setler PDF · görünen liste CSV · tüm setler CSV |

## Hesabın kuralları — ve neden

- **Hesap sunucuda yapılır.** Ekran yalnız *tanımı* (ürün, adet, indirim,
  zorunluluk) gönderir; birim fiyat ve maliyet backend'de mağaza verisinden
  çözülür. İstemcinin gönderdiği tutarla hesaplamak, ekranda kârlı görünen bir
  setin gerçekte zarar etmesine kapı açardı.
- **KDV karıştırılmaz.** Vitrin fiyatı KDV **dahil**, Bagisto'nun `cost` alanı
  KDV **hariç**. Kâr = *KDV hariç set fiyatı − bileşen maliyeti*. İkisini aynı
  torbaya koymak seti olduğundan %20 kârlı gösterirdi.
- **Maliyet bilinmiyorsa "kârlı" da denmez "zararlı" da.** Eksik maliyeti sıfır
  saymak, zararına satılan seti kârlı gösterirdi; durum `Maliyet girilmemiş`
  yazar ve hangi bileşenin eksik olduğunu söyler.
- **Set fiyatı künyede yoksa mağazadaki ürün fiyatı geçerlidir** — müşterinin
  ödediği tutar odur.
- **Opsiyonel bileşen set fiyatının içinde değildir**; ayrı toplanır ve set
  stoğunu kısıtlamaz.
- **Süresi geçmiş indirim geçerli sayılmaz**; bileşen fiyatı tarih penceresine
  göre seçilir.

## Ne yapmaz — ve neden

- **Set fiyatını mağazaya yazmaz.** Set bir üründür; ürün fiyatı yazmak
  Bagisto'da oku-değiştir-yaz ister (kısmi PUT alanları boşaltıyor) ve o kural
  Ürünler ekranının işidir. Buradan “Ürünler ekranında aç” ile o ekrana geçilir.
- **Set silmez.** Satılmış setin bileşen künyesi geçmişi okumak için gerekir;
  vitrinden kaldırma vardır (ADR 0012) ve künye korunur.
- **Carousel düzenlemez.** Ana sayfa yerleşimi `store_home_media` ekranındadır;
  burada yalnız rozet çizilir.
- **Bileşen ürünlerini sınırsız okumaz.** Geçit dakikada 55 istek veriyor;
  `component_fetch_cap` (varsayılan 80) aşılırsa ekran kaç ürünün okunmadığını
  ve hangi ayarın büyütüleceğini **söyler**, sessiz kalmaz.

## Uçlar

`/api/store_bundles` öneki altında. Hepsi `requires(...)` taşır (K9).

Okuma: `GET /bundles` · `GET /bundles/{id}` · `GET /lookup` · `GET /audit` ·
`GET /printer`

Hesap: `POST /calc` (yazmaz, gerekçe istemez)

Yazma: `PUT /bundles/{id}` · `POST /bundles/{id}/status`

Çıktı: `POST /preview` · `POST /print` · `POST /export`

## İzinler

| Anahtar | Ne açar |
|---|---|
| `store_bundles.view` | Ekran, kâr hesabı, rapor, CSV |
| `store_bundles.manage` | Künye ve bileşen tablosu düzenleme |
| `store_bundles.deactivate` | Seti vitrinden kaldırma (silme yok) |

## Yerel tablolar

Yalnız mağazada **karşılığı olmayan** veri:

- `mod_store_bundles_plan` — set künyesi ve bileşen tanımı (adet, indirim,
  zorunluluk). Çapraz satış bağı bu üçünü taşımıyor.
- `mod_store_bundles_audit` — yazma gerekçesi. Bagisto denetim kaydında
  "neden" alanı yok; ağ koparsa "ne yapmaya çalıştık" kaydı burada kalır.

## `store.api` metotları

Kullanılan: `bbd_bundles` · `bbd_save_bundle` · `bbd_carousel` · `product` ·
`product_lookup` · `update_product_status`.

**`products` BİLEREK KULLANILMIYOR.** `/api/admin/catalog/products`
`category_id` parametresini tanımıyor ve Laravel tanımadığı sorgu
parametresini sessizce yok sayıyor: canlıda süzgeçli de süzgeçsiz de
`total: 1421` dönüyor, yani o uçtan "set listesi" diye kataloğun ilk sayfası
gelirdi. Süzgeci gerçekten uygulayan uç seçici ucu (`product_lookup` →
`/api/admin/products`); `category_id=42` ile `total: 1`. Bir test bu uca hiç
gidilmediğini sınıyor.

`bbd_*` uçları canlıda **henüz yayında değil** (14.08.2026'da
`/api/admin/bbd/bundles` ve `/bbd/carousel` → 404); hepsi tek tek denenir,
404 gelirse ekran o özelliği kapatır ve nedenini yazar (K7).

## Bağımlılık

`poppler-utils` (`pdftoppm`) — yalnız rapor **önizlemesi** için; yoksa rapor
gene yazılır ve basılır, ekran sebebini söyler. `module.yaml` içindeki
`dependencies.system` bloğunda ilan edilir (K11), depoya konmaz.

## Testler

```bash
.venv/bin/python -m pytest modules/store_bundles/tests -q
.venv/bin/ruff check modules/store_bundles
```

- `test_store_bundles_math.py` — saf hesap: para, KDV, kâr, stok, geçerlilik.
- `test_store_bundles_service.py` — iş kuralları, K7, gerekçe kapısı, çıktı.
- `test_store_bundles_panel.py` — panel ve künye **sözleşmesi** (kök
  `kit-panel`, `loadStyles` yeri, overlay'in nereye eklendiği, cleanup,
  K5/K9/K11). Bunların hiçbiri çalışma zamanında hata vermiyor: panel açılır,
  yalnızca yanlış çalışır.
