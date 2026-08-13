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
| `kit.js` | `h` · para/tarih/sayı biçimi · `foldText` · `debounce` · `clip` · `copyText` · `csvBlob` · `button` · `toaster` · `confirmWithReason` · `confirmSimple` · `loadStyles` |
| `kit.css` | Tüm paylaşılan görsel dil. Panelin kendi kuralları `panel.css`'te, kendi önekiyle |
| `table.js` | `dataTable` (sıralama, seçim, yoğun kip) · `pager` (sunucu tarafı sayfalama) |
| `filters.js` | `filterBar` (arama, açılır, tarih aralığı, sayı aralığı, anahtar) · `applyFilters` |
| `form.js` | `formGrid` — 9 alan tipi, doğrulama, **kirli alan takibi** ve `patch()` |
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

## Mevcut BBD panelleri

`bbd_*` panelleri bu kite **taşınmadı**; kendi kopyalarıyla çalışıyorlar ve
çalışmaya devam edecekler. Taşıma ayrı ve isteğe bağlı bir iştir. Yeni panel
yazan kiti kullanır.
