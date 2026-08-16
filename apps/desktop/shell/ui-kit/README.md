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
| `kit.js` | `h` · para/tarih/sayı biçimi · `foldText` · `debounce` · `pollLoop` · `clip` · `copyText` · `csvBlob` · `button` · `blockedButton` · `toaster` · `confirmWithReason` · `confirmSimple` · `loadStyles` |
| `choice.js` | `resolveChoice` · `choiceFilter` · `choiceField` · `choiceValues` · `choiceNotice` · `choiceSummary` — tek seçenekli alanı ekrandan kaldırır |
| `kit.css` | Tüm paylaşılan görsel dil. Panelin kendi kuralları `panel.css`'te, kendi önekiyle |
| `table.js` | `dataTable` (sıralama, seçim, yoğun kip) · `pager` (sunucu tarafı sayfalama) |
| `filters.js` | `filterBar` (arama, açılır, tarih aralığı, sayı aralığı, anahtar) · `applyFilters` |
| `form.js` | `formGrid` — 10 alan tipi, doğrulama, **kirli alan takibi** ve `patch()` |
| `richtext.js` | `richText` — zengin metin düzenleyici · `sanitizeHtml` · `renderHtml` · `htmlToText` · `filterStyle` · `safeUrl` |
| `layout.js` | `card` · `tabBar` · `kpiRow` · `badge` · `chipRow` · `drawer` · `splitView` · `emptyState` · `alertBox` · `hintBox` · `progress` · `skeletonRows` · `statusLine` |
| `charts.js` | `lineChart` · `barChart` · `hourStrip` · `paretoChart` · `sparkline` · `stackedBar` · `groupedBar` |
| `flow.js` | `timeline` (dikey olay akışı) · `stepper` (aşama şeridi) · `measureBar` (iki ölçü, hangisi faturalanıyor) |
| `datefield.js` | `dateField` · `dateRange` · ISO/TR dönüşümleri |
| `calendar.js` | `monthCalendar` — ay ızgarası, gün başına rozet, tatil işaretleme |
| `imagefield.js` | `imageField` — dosya seç/sürükle-bırak, ön denetim, önizleme · `inspectFile` · `measureImage` · `readAsDataUrl` |
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
   `debounce(...).cancel()` bekleyen çağrıyı iptal eder;
   `pollLoop(...).stop()` hem zamanlayıcıyı hem `visibilitychange`
   dinleyicisini bırakır; `imageField.destroy()` önizlemelerin nesne
   URL'lerini bırakır (bırakılmazsa panel açıldıkça birikirler).
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

### 1.3.0 — 2026-08-16
`stepper` adım başına `state` yazısı, `kpiRow` kutu başına `spark` şeridi alır.
İkisi de İSTEĞE BAĞLI; eski çağrılar birebir aynı çıktıyı verir.

**Neden `stepper(steps).state`.** Şerit adımı "tamamlandı" ya da "bekliyor"
diye yazıyordu. Mağaza siparişi bu ikisine sığmıyor: kısmi fatura ve kısmi
kargo olağandır (`invoiceState`/`shipmentState` üç durumlu). Yarım kalmış bir
adıma "tamamlandı" yazmak olmamış bir işi olmuş, "bekliyor" yazmak başlamış bir
işi hiç başlamamış gösterir — ikisi de yanlış, ikisi de sessiz. `activeIndex`
artık yalnız KESİNTİSİZ tamamlanan başlangıcı işaretler; yarım adım kendi
cümlesini kurar ("3/5 kalem faturalandı").

**Neden `kpiRow(tiles).spark`.** `delta` iki noktayı karşılaştırır; aynı yüzde
düzgün bir tırmanıştan da, dibe vurup son iki günde toparlanmadan da çıkar.
Şerit sayının yerine geçmez, altında durur ve `aria-hidden`'dır: `sparkline`
eksensizdir, tek başına okunmaz. `layout.js` bunun için `charts.js` import eder
— kit içi bağımlılık, panele sızan bir grafik değil.

**`stepper` adım tonu artık görünüyor.** `kit-step-mark` sınıfına `tone`
yazılıyordu ama `kit.css`'te karşılığı yoktu — sessiz ölü kod. `done`/`now`
ZİNCİR hakkındadır (kesintisiz tamamlanan başlangıç), ton TEK ADIM hakkında;
sırası gelmeden gerçekleşmiş bir adım artık zinciri tamamlanmış göstermeden
işaretlenebiliyor. Zincir sınıfları tonu yener, böylece tamamlanan başlangıç
tek bir dolu şerit gibi okunur.

**Bileşenler iç boşluğunu kendi taşıyor.** `.kit-timeline`, `.kit-stepper` ve
`.kit-measures` artık `.kit-chart` ile aynı `padding`'i alıyor: üçü de doğrudan
`card()` içine konuluyor ve `card()` çocuklarına boşluk vermiyordu. Boşluğu
panele bırakmak her panelin kendi ölçüsünü uydurması demekti.

**Panelde grafik çizilmiyor.** Aynı sürümde `store_shipping` gönderi
çekmecesindeki elle yazılmış zaman çizelgesi (`sh-timeline` / `sh-move`) kaldı
ve yerine `timeline()` geçti. Tek kopya kuralı (ADR 0011) yalnız yeni kod için
değil: ikinci kopya kitteki düzeltmeleri almıyordu.

### 1.4.0 — 2026-08-16
`imagefield.js` ve `calendar.js` eklendi; `kit.js` `pollLoop` kazandı;
`richtext.js` satır içi görsel ekleyebiliyor (`onInsertImage`). Eklenen dört
şeyin dördü de İSTEĞE BAĞLI; hiçbir eski çağrı değişmedi.

**Neden şimdi ve neden kitte.** Yakında yazılacak BLD yönetim panellerinden
dördü aynı dört şeyi istiyor: görsel yükleme, ay takvimi, metin içinde görsel,
canlı tazeleme. Bunlar bugün ya kitte hiç yok ya da panel kopyalarında var.
Kite konmasaydı her panel kendi kopyasını üretirdi — ADR 0011 tam olarak bunu
yasaklıyor ve gerekçesi ölçülmüş: `store_shipping` kendi zaman çizelgesini
yazdığı için kitteki düzeltmeleri hiç almadı (1.3.0).

**`imageField` üç kopyanın ortak paydası.** `store_products` sürümü en
olgunuydu ve taban o oldu: dosya başına inceleme (`inspectFile`), ret sebebini
kendi satırında yazan günlük, kapak sırası, nesne URL'lerini bırakan temizlik.
`store_home_media`'nın İKİ KARELİ önizlemesi (vitrindeki kırpılmış hâl +
gerçek oran) genelleştirilebildi ve `frameRatio` seçeneğine bağlandı; aynı
panelin ölçüyü SUNUCUYA sorması (`/image/check`) genelleştirilemez — o bir
modül ucudur, kit uç adresi bilmez. `bbd_canteen_products` sürümünün fazlası
yoktu. Paneller kendi kopyalarıyla çalışmaya devam eder; taşıma ayrı iştir.

**İstemci denetimi UX'tir, kapı değil.** Tür/boyut/ölçü denetimi burada
kullanıcıya erken ve anlaşılır cevap vermek içindir; asıl kapı
`modules/store_api/backend/upload.py` içindedir (K9). Bir tek yer değişince
öteki değişmez: buradaki kural gevşerse sunucu yine reddeder, buradaki kural
sunucudan KATI olursa kullanıcı sunucunun kabul edeceği dosyayı gönderemez —
bu yüzden tür denetimi sunucudaki gibi "mime uygun **ya da** uzantı uygun"
diye yazıldı (WebKitGTK bazı sürüklemelerde `file.type` alanını boş bırakıyor).

**`monthCalendar` terfi, taşıma değil.** Kaynak `bbd_lunch/ui/panel/calendar.js`
yerinde duruyor ve dokunulmadı (BBD panelleri kite taşınmadı). Kit sürümü
rozeti ÇİZMEZ: `renderBadge(iso, info)` çağırana aittir, çünkü menü ekranı
"yayınlandı/taslak/yok", yemek ekranı "işlenen porsiyon" gösterecek. Rozeti
çizen `legend` de vermek zorundadır — kural 7 burada sözleşmenin parçası.
Ay gezinmesi artık ayrı geri çağrı (`onMonth`): kaynakta oklar
`onPick(null, month)` çağırıyordu ve çağıran her seçim işlemine `if (day)`
diye başlamak zorunda kalıyordu.

**`pollLoop` sekme gizliyken durur.** `bld_kds`'in `setInterval` kalıbı doğru
ve temizlik kuralına uyuyordu ama `document.hidden` denetlemiyordu. Tek
yoklayan panelle sorun değil; dördü aynı anda yoklayınca (panel · siparişler ·
durum izleme · KDS) arka planda duran bir pencere paylaşılan hız bütçesini
boşuna yakar. Üst üste binen koşu da engellenir: yavaş bir uçta aralık dolduğu
için ikinci istek atmak, ilk isteğin üstüne binmekti.

**Zengin metinde görsel: önce yükle, sonra adresi ekle.** `img` beyaz listede
ve `src/alt/title/width/height` geçiyordu — yani görseller zaten çiziliyordu;
eksik olan araç çubuğu düğmesi ve yükleme yoluydu, tek yol "Kaynak" sekmesine
elle `<img>` yazmaktı. `safeUrl` `data:` kabul etmediği için akış base64 gömmez
ve gömmemeli: gövdeye gömülü görsel kaydedilen sayfayı şişirir, tarayıcı
önbelleğine hiç girmez. Düzenleyici hangi ucun yüklediğini bilmez;
`onInsertImage(file) → url` çağırana aittir ve verilmezse düğme HİÇ ÇİZİLMEZ
(çalışmayan düğme bırakılmaz).

**Yazı tipi seçimi bilerek eklenmedi.** `font` etiketi `span`'e katlanıyor ve
`filterStyle` `font-family`'yi zaten çıkarıyor. Eklemek beyaz listeyi ve
sunucudaki aynasını (`store_cms/backend/content.py`) birlikte genişletmeyi
gerektirir, üstüne herkese açık sitede her sayfayı başka bir yazı tipiyle
çıkarır. İhtiyaç mevcut blok biçimleriyle karşılanıyor; gerekçe `richtext.js`
başlığında da yazılı ki sonradan gelen "eksik kalmış" sanmasın.

## Mevcut BBD panelleri

`bbd_*` panelleri bu kite **taşınmadı**; kendi kopyalarıyla çalışıyorlar ve
çalışmaya devam edecekler. Taşıma ayrı ve isteğe bağlı bir iştir. Yeni panel
yazan kiti kullanır.
