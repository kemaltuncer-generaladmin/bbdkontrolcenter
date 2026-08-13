# CMS

BBD Store'un vitrin metinleri: sayfalar, **yasal metinler**, SSS ve **arama &
SEO** (URL yeniden yazma, arama terimleri, eş anlamlılar, site haritası). Sol
tarafta dallara ayrılmış sayfa ağacı, sağda düzenleyici.

Grup: **BBD Store** · CSS öneki: `cm` · Rapor rafı:
`Raporlar/Mağaza/Müşteri/<yıl>/<ay>`

## Ne yapar

| Alan | Davranış |
|---|---|
| Ağaç | Yasal metinler · SSS · Kurumsal · Diğer. Dal slug'dan kurulur; mağaza CMS sayfalarını düz liste tutuyor. |
| Arama | Başlık, adres **ve içerik metni**. Eşleşen cümleden alıntı gösterilir. |
| Çipler | Yasal metinler · SEO eksik · Kırık iç bağlantı · İçeriği zayıf (sayaçlı). |
| Düzenleyici | Künye (adres başlıktan türetilir, elle yazılınca kilitlenir) · İçerik (araç çubuğu + kaynak + canlı önizleme) · SEO (arama sonucu kartı + karakter sayacı) · Sürümler. |
| Sürüm geçmişi | Her yazmadan **önce** eski hâl saklanır; "Bu sürüme dön" geri alır. |
| Yasal metinler | Mesafeli satış · iade · gizlilik/KVKK · çerez. Üst şeritte var/yok/çok kısa durumu. |
| SSS | Soru-cevap listesi olarak düzenlenir, sayfaya `<h3>` soru + cevap olarak yazılır. |
| Arama & SEO | Dört alt bölüm — aşağıda. |
| Çıktı | İçerik envanteri PDF · **yasal metinler arşivi PDF** · sayfa listesi CSV. |

## Arama & SEO sekmesi

Dördü de aynı soruya bakar: *müşteri aradığını bulabiliyor mu, bulduğu adres
çalışıyor mu.* Ayrı modül açılmadı; sekme CMS ekranının içindedir.

| Alt bölüm | Davranış |
|---|---|
| URL yeniden yazma | 301/302 listesi, ekleme, **düzenleme ve silme**. Döngü, kendine dönme ve **çakışan kaynak adres** yazmadan önce yakalanır; listede duran çakışmalar satır satır işaretlenir. |
| Arama terimleri | Ne arandı, kaç sonuç döndü, kaç kez arandı. **Sonuçsuz aramalar** ayrı süzgeç ve ayrı sayaçtır. Salt okunur. |
| Eş anlamlılar | "kalem = tükenmez". En az iki kelime; aynı kelimenin iki gruba girmesi engellenir. |
| Site haritası | Tanım listesi, **son üretim zamanı** ve "Üret" düğmesi. Tanım dosyayı üretmez. |

**Yönlendirmeler eskiden ayrı sekmeydi**, buraya taşındı: aynı kaydı iki
ekrandan yönetmek, iki ekranın birbirinin üstüne yazması demektir.

**Sonuçsuz aramalar bu ekranın en değerli verisidir** — katalog boşluğunu
gösterir. Satırdaki "Eş anlamlı kur" düğmesi terimi doğrudan eş anlamlı
formuna taşır: müşterinin kelimesi katalogdakinden farklıysa çözüm odur.

Uçta `results` süzgeci **yok**; liste sırayla çekilip (tavan 10×50 terim)
burada süzülür. Canlıda 19 terim var — ölçek buna izin veriyor, 1.419 üründe
aynı yaklaşım yasak olurdu. Tavana dayanılırsa ekran söyler.

**Site haritası üretimi ağır iştir**: yayındaki her kategori, ürün ve sayfa
dolaşılır. Ayrı izin anahtarı (`store_cms.sitemap`) ve önden uyarı vardır.

## Güvenlik kararı — önizleme `iframe`/`srcdoc` kullanmaz

Kabuğun CSP'sinde `frame-src` yok; `default-src 'self'` devreye giriyor ve
`srcdoc` davranışı WebKitGTK'da öngörülemez. Bunun yerine içerik `DOMParser`
ile ayrıştırılır ve **beyaz listeli** etiketler (`p h1-h4 ul ol li a img strong
em br table thead tbody tr td th`) klonlanarak çizilir. `script`/`style`/
`iframe` ve tüm `on*` öznitelikleri atılır, `javascript:` ve `data:` bağlantısı
düşürülür, `innerHTML` hiçbir yerde kullanılmaz.

**Aynı beyaz liste sunucuda da uygulanır** (`backend/content.py:sanitize_html`)
ve kaydedilen içerik ondan geçer — iki kapı, tek liste (K9). Mağazadaki mevcut
içerik de kaydederken temizlenir; ekran bunu önden söyler.

Tanınmayan etiket (`<div>`, `<span>`) **açılır, içeriği durur**: kullanıcının
yazısını etiketle birlikte silmek veri kaybıdır.

## Ne yapmaz — ve neden

- **Sayfa silmez.** Silinen sayfanın adresi 404 verir; arama motorunda,
  e-postalarda ve basılı belgelerde duran bağlantılar kırılır (ADR 0012).
  Adres değişirse ekran 301 önerir.
- **Menü yönetmez.** Geçitteki tek menü ucu (`admin_menu`) **yönetici
  panelinin** menüsüdür; vitrin menüsüyle ilgisi yok. Onu buradan düzenlemek
  personelin Bagisto arayüzünü bozmasına kapı açardı. Sekme bunu yazar.
- **Blok düzenlemez.** Bloklar tema özelleştirmesi olarak duruyor ve
  slider/banner slotları aynı tabloda; onların sahibi Ana Ekran Görselleri
  ekranı. İki ekranın aynı kaydı yazması, birinin diğerini ezmesi demektir.
  Burada salt okunur listelenir.
- **Arama terimi düzenlemez/silmez.** Terimler aramadan doğar; elle
  düzeltilecek bir kayıt değil, bir ölçümdür. Mağazadaki güncelleme ucu bir
  terime `redirect_url` bağlamaya izin veriyor ama canlıda doğrulanmadı;
  sonuçsuz aramanın doğru çözümü zaten eş anlamlı ya da eksik ürün.
- **Site haritası tanımını silmez/düzenlemez.** Liste, tanımlama ve üretim
  var. Tanım silmek yayındaki XML'i öksüz bırakır; ihtiyaç doğarsa ayrı bir
  adımda ele alınır.
- **WYSIWYG değildir.** Düzenleme kaynak üzerinden yapılır, araç çubuğu seçimi
  etiketle sarar. Sahte bir WYSIWYG'in ürettiği `<div style=…>` çorbası beyaz
  listeden geçerken sessizce biçimini kaybederdi.

## Ölçek kararı — tam liste çekilir

CMS sayfası onlarla ölçülür ve mağaza ucu **içerik metninde arayamaz**; bu
yüzden liste tamamı (tavan 500) tek istekte çekilip burada süzülür. Aynı
yaklaşım 1.419 üründe **yasaktır** (`store_products` tam liste çekmez); fark
ölçektedir, keyfî değildir.

## Uçlar

`/api/store_cms` öneki altında. Hepsi `requires(...)` taşır (K9).

Okuma: `GET /pages` · `GET /pages/{id}` · `GET /pages/{id}/versions` ·
`GET /versions/{id}` · `GET /legal` · `GET /faq` · `GET /redirects` ·
`GET /search-terms` · `GET /synonyms` · `GET /sitemaps` · `GET /blocks` ·
`GET /menus` · `GET /reference` · `GET /audit` · `GET /printer`

Yazma: `PUT /pages/{id}` · `POST /pages` · `POST /pages/{id}/restore` ·
`POST /faq` · `POST /redirects` · `POST /redirects/{id}/delete` ·
`POST /synonyms` · `POST /synonyms/{id}/delete` · `POST /sitemaps` ·
`POST /sitemaps/{id}/generate` · `POST /preview` · `POST /print` ·
`POST /export`

Silme **DELETE değil POST**: gerekçe gövdeyle taşınıyor ve gövdeli `DELETE`
isteği ara katmanlarda sessizce düşebiliyor.

## İzinler

| Anahtar | Ne açar |
|---|---|
| `store_cms.view` | Ekran, sürüm geçmişi, rapor, CSV |
| `store_cms.manage` | Sayfa açma, içerik/SEO düzenleme, SSS |
| `store_cms.restore` | Eski sürüme döndürme (yürürlükteki metnin üstüne yazar) |
| `store_cms.redirects` | 301/302 yazma ve silme |
| `store_cms.sitemap` | Site haritası tanımlama ve üretme (ağır iş, yayındaki XML'i yeniden yazar) |

## Yerel tablolar

Yalnız Bagisto'da **karşılığı olmayan** veri:

- `mod_store_cms_audit` — yazma gerekçesi ve denemenin sonucu.
- `mod_store_cms_versions` — **sürüm geçmişi.** Bagisto CMS sayfasının eski
  hâlini tutmuyor; "dün ne yazıyordu" ve "geri al" bu tablo olmadan cevapsız.
  Yasal metinlerde bu kolaylık değil zorunluluktur. Satır **hiç silinmez**:
  geri alma da yeni bir sürüm bırakır, yanlış sürüme dönmek de geri alınabilir.

Sayfa metni kopyalanmaz — kopya, mağaza tarafında yapılan bir düzenlemeden
sonra sessizce eski metni gösterirdi.

## Ayarlar

`config/default.yaml`: `faq_slug` ve `legal_slugs` **koda gömülmez** — mağaza
sayfaları farklı adlandırmış olabilir ve gömülü liste yanlış "eksik metin"
uyarısı üretir. `site_url` yalnız SEO önizleme kartındaki adresi kurar.

## Testler

```bash
.venv/bin/python -m pytest modules/store_cms/tests -q
.venv/bin/ruff check modules/store_cms
```
