# Panel arayüz kiti

Panellerin ortak DOM, biçim ve etkileşim katmanı. **Tek kopya** — karar ve
gerekçe: [ADR 0011](../../../../docs/adr/0011-panel-arayuz-kiti-kabukta.md).

## Nasıl kullanılır

```js
// modules/store_orders/ui/panel/index.js
//
// Import yolu panelin KOPYALANMIŞ konumuna göredir:
//   shell/panels/store_orders/index.js  →  shell/ui-kit/
// Kaynak dosya modules/<id>/ui/panel/ altındayken bu yol dosya sisteminde
// ÇÖZÜLMEZ. Normaldir; `tools/build-ui-registry.py` paneli kopyaladıktan
// sonra çözülür.
import { button, h, money, toaster } from '../../ui-kit/kit.js';
import { dataTable, pager } from '../../ui-kit/table.js';
import { filterBar } from '../../ui-kit/filters.js';
import { card, kpiRow, tabBar } from '../../ui-kit/layout.js';
```

Panel kökü `kit-panel` sınıfını almalıdır — renk jetonları, `position:
relative` (toast ve overlay ona göre konumlanır) ve `min-height: 0` zinciri
oradan gelir:

```js
const view = h('div', 'kit-panel so');   // `so` = panelin kendi öneki
```

`kit.js` kendi stilini (`kit.css`) import anında yükler. Panelin **kendi**
`panel.css` dosyası `mount()` **içinde** yüklenmelidir: kabuk, yetenek çözümü
sırasında hiç açılmayan panelleri de import ediyor ve dosya tepesindeki
`loadStyles()` kullanılmayan stilleri `document.head`'e sızdırır.

## Dosyalar

| Dosya | İçerik |
|---|---|
| `kit.js` | `h` · para/tarih/sayı biçimi · `foldText` · `debounce` · `clip` · `copyText` · `csvBlob` · `button` · `blockedButton` · `toaster` · `confirmWithReason` · `confirmSimple` · `loadStyles` |
| `choice.js` | `resolveChoice` · `choiceFilter` · `choiceField` · `choiceValues` · `choiceNotice` · `choiceSummary` — tek seçenekli alanı ekrandan kaldırır |
| `kit.css` | Tüm paylaşılan görsel dil. Panelin kendi kuralları `panel.css`'te, kendi önekiyle |
| `table.js` | `dataTable` (sıralama, seçim, yoğun kip) · `pager` (sunucu tarafı sayfalama) |
| `filters.js` | `filterBar` (arama, açılır, tarih aralığı, sayı aralığı, anahtar) · `applyFilters` |
| `form.js` | `formGrid` — 10 alan tipi, doğrulama, **kirli alan takibi** ve `patch()` |
| `richtext.js` | `richText` — zengin metin düzenleyici · `sanitizeHtml` · `renderHtml` · `htmlToText` · `filterStyle` · `safeUrl` |
| `layout.js` | `card` · `tabBar` · `kpiRow` · `badge` · `chipRow` · `drawer` · `splitView` · `emptyState` · `alertBox` · `hintBox` · `progress` · `skeletonRows` · `statusLine` |
| `charts.js` | `lineChart` · `barChart` · `hourStrip` · `paretoChart` · `sparkline` · `stackedBar` · `groupedBar` |
| `datefield.js` | `dateField` · `dateRange` · ISO/TR dönüşümleri |
| `picker.js` | `createPicker` — gruplanmış, aranabilir çoklu/tekli seçici |
| `report.js` | `reportChain` — üret → önizle → yazdır (CUPS) |
| `util.js` | `groupBy` · `sum` · `average` · `sortBy` · `uniqueBy` · `topN` · `compare` · `fillDays` · `abcClassify` |

## Kesin kurallar

1. **`<input type="date">` KULLANMA.** WebKitGTK'da açılır takvim kapanmıyor.
   `dateField()` kullan.
2. **Panel kökü `kit-panel` almalı.** Yoksa toast ve overlay tüm pencereye taşar.
3. **Overlay `nodes.root`'a eklenir, `document.body`'ye DEĞİL.** Panel
   değişince kabuk `root.replaceChildren()` yapıyor; body'deki overlay orada
   asılı kalır.
4. **`cleanup` gerçek kaynak bırakmalı.** `dateField.destroy()`,
   `filterBar.destroy()`, `formGrid.destroy()` global dinleyici tutar;
   `debounce(...).cancel()` bekleyen çağrıyı iptal eder.
5. **Para her yerde KURUŞ (integer).** Gösterimde `money()`, girişte
   `moneyInput()` / `parseMoney()`.
6. **`todayIso()` kullan, `toISOString()` KULLANMA** — ikincisi UTC'ye kayar.
7. **Renk tek başına anlam taşımaz.** Her rozetin yanında sayı ya da yazı olsun.
8. **Silme yok, pasifleştirme var.** Yıkıcı işlem `confirmWithReason` ile;
   gerekçe backend'e gider ve denetim kaydına yazılır (ADR 0012).
9. **CSS öneki benzersiz seç.** Panel CSS'i `document.head`'e eklenir ve hiç
   kaldırılmaz; çakışan önek başka panelin görünümünü bozar.
10. **HTML'i kendi elinle temizleme.** Mağaza içeriği (ürün açıklaması, CMS
    sayfası) `richtext.js` içindeki beyaz listeden geçer. Panelde ikinci bir
    liste tutma: `store_cms` bunu denedi ve iki liste sessizce ayrıştı.
    Yazma için `richText()`, çizme için `renderHtml()`.
11. **`innerHTML` ile içerik yazma.** `renderHtml()` düğümleri tek tek
    klonlar; `innerHTML` beyaz listeyi tümden atlar.

## Değişiklik günlüğü

Yayınlanmış imza **geriye dönük uyumsuz değiştirilmez**; yeni davranış yeni
parametreyle gelir. Kırıcı bir değişiklik gerekiyorsa yeni ad verilir ve eskisi
bir sürüm boyunca korunur.

### 1.0.0 — 2026-08-13
İlk sürüm. `kit.js` mevcut 16 dışa vurumu korur (7 panelin kopyasıyla
uyumlu); üzerine `num`, `percent`, `bytes`, `ago`, `clip`, `debounce`,
`copyText`, `csvBlob`, `confirmSimple` eklenmiştir.
`charts.js` mevcut 4 grafiği korur, `sparkline`/`stackedBar`/`groupedBar`
ekler; SVG sınıfı `cr-chart` → `kit-chart` olarak değişmiştir.

**Bilinçli davranış farkı — `parseMoney`.** Kantin sürümü noktalı binlik
ayracını reddediyordu: `parseMoney('1.250,00')` → `null`. Orada tutar
seçilemediği (öğrencinin borcu kullanılıyor) için sorun değildi. Mağazada
personel tutarı elle yazıyor ve Türkçe klavyede doğal yazım tam olarak
`1.250,00`; doğru yazanı hatalı göstermek kabul edilemezdi. Kit sürümü
`1250` · `1250,50` · `1250.50` · `1.250,00` · `1 250,00` · `1.234.567,89`
biçimlerini kabul eder. Belirsiz `1,234` **reddedilir** — parayı sessizce
yanlış okumaktansa kullanıcıya sormak doğrudur. Mevcut paneller kendi
kopyalarını kullandığı için etkilenmez.

### 1.1.0 — 2026-08-14
`richtext.js` eklendi ve `form.js` `type: 'richtext'` alanını tanır.

**Neden.** Ürün açıklaması ve CMS sayfası HTML tutuyor; personel `<strong>` ve
`<em>` etiketlerini elle yazıyordu. Renk hiç yoktu — tek yolu `style`
özniteliğiydi ve beyaz listede kapalıydı. "Yazıyı kırmızı yap" gibi en sıradan
istek kod bilgisi gerektiriyordu.

**`style` artık geçiyor, ama ham değil.** `filterStyle` üç özelliğe indirger —
`color`, `background-color`, `text-align` — ve değerleri de biçim denetiminden
geçirir. Eski yasağın gerekçesi "sayfayı kaplayan görünmez katman"dı; o saldırı
`position`, `width/height`, `opacity`, `z-index` ister ve hiçbiri listede yok.

**Sunucu kopyası birlikte değişti.** `modules/store_cms/backend/content.py`
içindeki `ALLOWED_TAGS` ve `STYLE_PROPS` aynı değerleri taşır; eşitlik
`modules/store_cms/tests/test_store_cms_content.py` içinde teste bağlıdır.
Biri genişletilip öteki unutulursa kullanıcı ekranda gördüğü biçimi
kaydettiğinde sessizce kaybeder.

### 1.2.0 — 2026-08-15
`choice.js` eklendi; `kit.js` `blockedButton` kazandı. `filterBar` ve
`formGrid` artık **yanlış (null) alanı atlar**.

**Neden `choice.js`.** Bu mağazada kanal bir tane, dil bir tane, para birimi
bir tane, stok kaynağı bir tane, vergi kategorisi bir tane. Yirmi ekranda
bunların açılır kutusu çiziliyor ve kullanıcıdan seçim isteniyordu — seçilecek
bir şey olmadan. Karar ekrana SERT KODLANMADI: `resolveChoice()` seçenek
sayısına bakar, `> 1` ise kutu geri gelir.

**Süzgeç ile form alanı ayrıştı.** Tek seçenekte form alanı değeri
kendiliğinden gönderir (`choiceValues`), süzgeç ise değeri GÖNDERMEZ. İkincisi
ölçülmüş bir hatadan geliyor: `channel=default` gönderilen sipariş listesi
HTTP 200 ile sıfır kayıt döndürüyordu. Tek kanallı mağazada hiçbir satır
elemeyen bir süzgeci göndermenin kazancı yok, riski var.

**`blockedButton` neden `button(..., {disabled})` değil.** Kapalı düğmenin
NEDENİ olmak zorunda: `title` + `aria-label` ile neden düğmenin üstünde durur,
`data-blocked` ile de testten görülebilir. Ham 404/405/409 metni gösteren
düğme bırakılmaz.

## Mevcut BBD panelleri

`bbd_*` panelleri bu kite **taşınmadı**; kendi kopyalarıyla çalışıyorlar ve
çalışmaya devam edecekler. Taşıma ayrı ve isteğe bağlı bir iştir. Yeni panel
yazan kiti kullanır.
