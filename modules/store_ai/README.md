# Dahili AI Ekranları

İki iş, bir ekran: **kural tabanlı katalog sağlığı denetimi** ve **onaya bağlı
AI öneri araçları**.

Grup: **BBD Store** · CSS öneki: `ai` · Rapor rafı:
`Raporlar/Mağaza/Ürün/<yıl>/<ay>`

## Ekranın kimliği olan kural

> **AI hiçbir şeyi doğrudan yazmaz.** Her sonuç *öneri*dir. Fark tablosu satır
> satır gösterilir, kullanıcı tek tek ya da toplu onaylar. Onaysız uygulama
> yoktur — ve uygulama fark tablosunu **yeniden hesaplamaz**: onaylanan
> metinler jetonla birlikte `mod_store_ai_run` içinde durur, uygulama onları
> okur ve gönderir. Kullanıcının okuduğu metin ile mağazaya giden metin
> aynıdır.

Buna üç şey eşlik eder:

- **Boş seçim "hepsi" değildir.** Hiçbir satır varsayılan olarak seçili
  gelmez; "hepsini seç" bilinçli bir tıklamadır.
- **Uygulanan satır ikinci kez uygulanamaz.** Satırın durumu tabloda tutulur.
- **Ölçülmeyen para harcanmaz.** Bütçe sayacı okunamıyorsa ve tahmine izin
  verilmemişse çalıştırma durur.

## Ne yapar

| Sekme | Davranış |
|---|---|
| **Araçlar** | Beş araç kartı. Her biri: hangi alanları önerdiğini yazar, `[Maliyet provası]` (jeton harcamaz) ve `[Çalıştır]` sunar. Sonuç → fark tablosu → satır seçimi → gerekçeli uygulama. |
| **Katalog Sağlığı** | **AI YOKTUR.** Ürün listesi + kategori ağacı okunur, bulgu `backend/rules.py` içinde hesaplanır. Süzme ve sayfalama istemcide (50). |
| **Çalışma geçmişi** | Yerel defter + (varsa) mağaza geçmişi. Satır → fark tablosunu yeniden açar; uygulanmamış öneriler oradan da uygulanabilir. |
| **Kullanım & maliyet** | Günlük maliyet (`lineChart`), araç kırılımı (`barChart`), aylık bütçe ölçeri, `[Maliyet raporu]` (PDF → önizle → yazdır). |
| **Ayarlar** | Aylık bütçe + sınıra gelince davranış, denetim eşikleri, hangi araçlar açık, susturulmuş bulgular. |

### Katalog sağlığı kuralları

`no_image` · `thin_description` · `no_barcode` · `no_category` ·
`price_anomaly` · `empty_category` · `seo_broken` · `duplicate_url_key`

Fiyat anormalliği üç şeye bakar: satılabilir üründe boş/sıfır fiyat,
indirmeyen indirim (`special_price >= price`), maliyeti satış fiyatının
üstünde olan ürün — ve kategori **medyanının** N katı üstü/altı fiyat.

## Mağazanın yanıt biçimi (canlıda doğrulandı)

`bbdstore.com.tr` üzerinde salt okunarak sınandı (2026-08-13); varsayım
değildir. Karşılığı `tests/test_store_ai_live_shape.py` içindedir.

- **Alan adları camelCase'tir**: `urlKey`, `metaTitle`, `metaDescription`,
  `specialPrice`, `shortDescription`, `updatedAt`. Bagisto'nun veritabanı
  sütunları ve belgeleri snake_case olduğu için kolayca aldanılıyor;
  `url_key` diye bakan kod hiçbir şey bulmaz ve **istisna da atmaz** — alan
  "boş" görünür ve kural her üründe tetiklenir. Snake_case okuyan sürüm
  1.421 ürünün **tamamına** "görseli yok" + "kategorisiz" + "URL anahtarı
  boş" diyordu; gerçek bulgu sayısı sıfırdı. `rules.spellings()` iki yazımı
  da çözer.
- **Ürün listesi ucu görselleri ve kategorileri VERMEZ.** `images` ve
  `categories` alanları **null** gelir; elimizdeki tek şey `imagesCount`,
  `baseImageUrl` ve **birincil** `categoryId`'dir. Tam liste yalnız ürün
  detayındadır ve tarama detay okumaz (1.421 ürün = 1.421 istek).
- **Para ondalık metindir**, dört haneye kadar: `"299.0000"` = ₺299,00.
  `to_kurus` Decimal kullanır, float kullanmaz.
- **Zaman damgası saat dilimsizdir** (`"2026-08-02 11:38:16"`) ve YERELdir.
- **Kategori ağacı `parentId` verir**, `parent_id` değil.
- **`per_page` 50'ye kırpılır**, `meta` camelCase, `links` boş.

### Yerel defterin saati de YEREL yazılır

`service._now()` UTC **değil** yerel saat üretir (offsetiyle:
`2026-08-13T23:49:56+03:00`). Bunu okuyan üç yer de değeri yerel sanıyor:
aylık bütçe `created_at[:7]`yi `rules.today_iso()`nun yerel ayıyla
karşılaştırıyor, günlük maliyet `[:10]` ile kovalanıyor, panel metni olduğu
gibi basıyor (çeviremez de — mağazadan gelen satırlar saat dilimsiz ve
yereldir). UTC yazan sürümde +03:00'ta ayın ilk üç saatinde yapılan
çalıştırma **bir önceki ayın bütçesine** işleniyordu ve denetim listesi her
satırı üç saat erken gösteriyordu.

Geçmiş listesi iki kaynağı birleştirdiği için sıralama da ham metinle
yapılamaz: yerel defter `T` ayırıcısı, mağaza boşluk kullanıyor ve `T` (0x54)
boşluktan (0x20) büyük. `analytics.sort_stamp()` ikisini aynı dile çevirir;
çevirmeyen sürüm ayırıcıdan sonraki saati hiç okumuyor, bütün yerel satırları
tarihten bağımsız olarak üste alıyordu.

### Kanal süzgeci — sipariş ucunun TERSİ

`/api/admin/catalog/products?channel=` kanal **kodunu** ister:
`channel=default` → 1.421 kayıt, `channel=1` (kimlik) → **0 kayıt**, hata yok.
Sipariş ucu (`/api/admin/orders`) bunun tam tersidir ve kimlik bekler. İki
ucun aynı davrandığını varsaymak listeyi sessizce boşaltır; bu yüzden
`config/default.yaml` içindeki `channel` **koddur** ve öyle kalmalıdır.

### Veri eksikse kural kendiliğinden kapanır

İki denetim, veri onları desteklemediği için kapalı gelir ve ekran nedenini
yazar. Susmak, yanlış söylemekten iyidir:

| Kural | Neden kapalı |
|---|---|
| `no_barcode` | `isbn` özniteliği ailede **tanımlı** ama liste ucu değerini döndürmüyor. Açık kalsaydı her ürün "barkodu yok" görünürdü. |
| `empty_category` | Liste ucu yalnız birincil kategoriyi veriyor (1418 numaralı ürün dört kategoride, listede biri görünüyor). Açık kalsaydı ürünü olan kategoriler boş çıkardı. |

## Ne yapmaz — ve neden

- **Tanımadığı aracı çalıştırmaz.** MagicAI başka araçlar da sunabilir; ama
  çıktının hangi alana yazılacağı bilinmeden anlamlı bir fark tablosu
  çizilemez ve "onaysız uygulama yok" kuralı boşa düşer. Tanınmayan araç
  listelenir ve nedeni yazılır.
- **Bulgu silmez.** "Bu bilerek böyle" demek için susturma vardır; susturma da
  gerekçe ister ve geri alma **satır ekleyerek** yapılır, silerek değil.
- **AI anahtarını görmez.** Model çağrısı mağaza tarafındadır (MagicAI);
  anahtar orada durur. Bu modül `secrets` yeteneği istemez.
- **Katalog denetimini mağazaya sormaz.** `bbd/catalog/health` ucu varken bile
  bulgu burada hesaplanır: kuralın kendisi bu ekranın işidir, eşikleri
  buradan değişir ve "neden bulgu çıktı" sorusunun cevabı tek satırda okunur.
  (`store_products` çipleri mağaza ucunu kullanır; ikisi ayrı iştir.)
- **Ürün seçici sunmaz.** `store.product.list` yeteneği henüz yayınlanmadı.
  Hedefler ya Katalog Sağlığı'ndan **"AI ile gider"** düğmesiyle taşınır, ya
  başka panelden `ctx.payload.productIds` ile gelir, ya da numara olarak
  yazılır.

## Mağaza uçları hazır değilse

**Uçların durumu (canlıda ölçüldü, 2026-08-16 — salt okuma):**

| Uç | Durum |
|---|---|
| `GET ai/tools` | **404** — mağazada "araç" kavramı yok |
| `POST ai/tools/{tool}/run` | **yok** (`route:list` içinde geçmiyor) |
| `GET ai/usage` | **404** — mağaza jeton/maliyet tutmuyor |
| `GET ai/drafts` | **200** (boş liste, `meta.total=0`) |
| `POST ai/drafts` · `.../{draftId}/apply` · `.../{draftId}/discard` | **var** (`route:list`) |

Bir dönem burada "`/api/admin/bbd/ai/*` uçlarının tamamı yayında değil"
yazıyordu; **artık böyle değil.** Taslak ailesi (`ai/drafts`) yayında —
eksik olan yalnız araç/kullanım uçları. Fark önemli: engel artık "uç yok"
değil, **akış modeli uyuşmazlığı**. Bu ekran "aracı çalıştır → sonucu al"
varsayıyor, mağaza ise "taslağı KM üretir → mağaza saklar → onaylanınca
uygular" diyor. İsim eşlemesiyle kapanmaz; metin üretimi Kontrol
Merkezi'nde kalmalı ve mağazaya yalnız sonuç yazılmalı.

Araç uçları çağrıldığında geçit bunu `bbd_endpoint_missing` koduyla bildirir
ve ekran:

- Araç kartlarını **"uç hazır olunca açılacak"** notuyla kapatır (sessizce
  patlamaz),
- Katalog Sağlığı sekmesini **çalıştırmaya devam eder** — o yarı yalnız
  çekirdek Bagisto uçlarına dayanır,
- Bütçe sayacını yerel defterden **tahmin eder** ve "tahmini" der.

## Uçlar

`/api/store_ai` öneki altında. Hepsi `requires(...)` taşır (K9).

Okuma: `GET /tools` · `GET /catalog` · `GET /runs` · `GET /runs/{token}` ·
`GET /usage` · `GET /settings` · `GET /printer`

Yazma: `POST /tools/{tool}/run` · `POST /runs/{token}/apply` ·
`POST /catalog/mute` · `POST /catalog/unmute` · `POST /settings` ·
`POST /preview` · `POST /print` · `POST /export`

## İzinler

Dört izin, dört ayrı zarar:

| Anahtar | Ne açar | Zarar |
|---|---|---|
| `store_ai.view` | Ekran, bulgular, geçmiş, rapor | yok |
| `store_ai.run` | AI aracı çalıştırma | **para** harcar (jeton) |
| `store_ai.apply` | Onaylanan öneriyi uygulama | **mağazaya yazar** |
| `store_ai.manage` | Bütçe, eşikler, araç anahtarları, susturma | politika değiştirir |

`run` ile `apply` bilerek ayrıdır: "önerileri görmek isteyen" ile "kataloğu
değiştiren" aynı kişi değildir. `manage` yalnız `admin`'dedir çünkü aylık
bütçe bir para politikasıdır.

## Yerel tablolar

Yalnız Bagisto'da karşılığı olmayan veri:

- `mod_store_ai_run` — öneri defteri (fark tablosu + gerekçe + maliyet).
  "Onaysız uygulama yok" kuralının kanıtı budur. `summary_text` sütunu modelin
  okunur özetini tutar: yazmayan araçlarda (yorum duygu özeti) fark tablosu
  yoktur ve çıktının tamamı odur — saklanmazsa geçmişten açılan çalışma
  bomboş bir çekmece olarak açılır.
- `mod_store_ai_ignored` — susturulmuş bulgular (silme yok). Liste hem
  `GET /catalog` hem `GET /settings` yanıtında döner; Ayarlar sekmesindeki
  "geri al" düğmesi ikincisini okur ve böylece katalog taraması yapılmadan da
  çalışır (tarama 29 istek eder).
- `mod_store_ai_prefs` — bütçe, sınır davranışı, denetim eşikleri, açık araçlar.
- `mod_store_ai_audit` — tüm yazma girişimleri, **başarısız olanlar dâhil**.

Katalog verisi kopyalanmaz; tarama sonucu yalnız **bellekte** taze tutulur
(`scan_ttl_seconds`) ve ekran tarama zamanını yazar.

## Testler

```bash
.venv/bin/python -m pytest modules/store_ai/tests -q
.venv/bin/ruff check modules/store_ai
```

- `test_store_ai_rules.py` — saf kural mantığı (yedi tuzağın her biri).
- `test_store_ai_analytics.py` — fark tablosu, bütçe kararı, maliyet.
- `test_store_ai_service.py` — iş kuralları, K7, yıkıcı işlem kapıları.
- `test_store_ai_live_shape.py` — **canlı gövde biçimi.** İçindeki sözlükler
  `GET /api/admin/catalog/products`, `/catalog/products/1418` ve
  `/catalog/categories/tree` yanıtlarından kısaltılarak alınmıştır; alan
  adları ve değer tipleri olduğu gibidir. Modülün kendi uydurduğu snake_case
  veriye karşı geçen test hiçbir şey kanıtlamıyordu.

Ağa çıkılmaz; `store.api` taklit edilir.
