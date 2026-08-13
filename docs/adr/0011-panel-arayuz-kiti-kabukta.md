# 0011 — Panel arayüz kiti kabukta durur

**Durum:** Kabul edildi · 2026-08-13

## Bağlam

Panel sözleşmesi (`mount(root, ctx)`) ve tasarım dili (`--paper-*` jetonları)
sabitken, ortak DOM yardımcıları bugüne kadar her modülün **kendi kopyasıydı**.
Gerekçe `kit.js` dosyasının kendi başlığında yazılıydı: modül modülü import
etmez (K3), kabuk bir bileşen kitaplığı sunmaz.

Bu, 10 panelde işledi. Ölçülen tekrar:

```
kit.js       181 satır × 7 kopya  = 1.267 satır   (md5 hepsinde aynı)
picker.js    237 satır × 4 kopya  =   948 satır
datefield.js 205 satır × 2 kopya  =   410 satır
kit-* CSS   ~108 satır × 8 kopya  =   864 satır
                                    ─────────────
                                    ~3.489 satır
```

BBD Store 20 ekran ekliyor. Hepsi tablo, filtre şeridi, sayfalama, form
üreteci, sekme, KPI kutusu, rozet ve çekmece kullanacak. Bu ortak yüzeyin
gerçekçi büyüklüğü ~2.700 satırdır (JS + CSS). Kopyalama sürdürülürse
depodaki panel kodunun büyük çoğunluğu kopya olur.

İsraftan ayrı, SOMUT bir arıza daha var. Panel CSS'i `loadStyles()` ile
`document.head`'e eklenir ve **hiç kaldırılmaz**; `kit-*` kuralları bugün 8
ayrı `panel.css` içinde tekrar tanımlı. 30 kopyaya çıktığında bir kopyadaki
sapma, o panel bir kez açıldıktan sonra **tüm uygulamanın** düğmelerini ve
diyaloglarını değiştirir. Hata kaynağından tamamen kopuk bir ekranda görünür:
"Ürünler ekranını açtıktan sonra Siparişler'in düğmeleri neden maviydi?"

Bu ADR yazılırken aynı sınıftan bir hata gerçekten yaşandı: `money()`
fonksiyonu bir dosyadan diğerine kopyalanırken içindeki görünmez U+00A0
karakteri sıradan boşluğa normalleşti ve para biçimi bozuldu. Kopyalama,
sessiz sapmanın taşıyıcısıdır.

## Karar

Ortak arayüz bileşenleri **kabuğa aittir** ve `apps/desktop/shell/ui-kit/`
altında TEK kopya durur. Paneller bunları `../../ui-kit/<dosya>.js` ile import
eder; bu yol panelin **kopyalanmış** konumuna (`shell/panels/<id>/`) göredir.

Kit'in içeriği modül adı bilmez, iş kuralı taşımaz: yalnızca DOM, biçim ve
etkileşim. `icons.js` (ikon sözlüğü) ve `style.css` (renk jetonları) ile aynı
kategoridedir — ikisi de kabuk altyapısıdır, özellik değil.

`kit.js` içindeki "kabuk bileşen kitaplığı sunmaz" yorumu geçersizdir ve bu
ADR'ye yönlendiren bir yorumla değiştirilir.

Aynı değişiklikle `ctx.open(panelId, payload)` eklenir: paneller birbirine
gezinebilir (Siparişler → Kargo, Müşteri → Siparişleri). Kabuk yine modül adı
bilmez; kimliği çağıran panel verir.

## Gerekçe

Konumun hiçbir aracı bozmadığı tek tek doğrulandı:

| Endişe | Kanıt |
|---|---|
| Servis edilir mi? | `tauri.conf.json` `frontendDist: "../shell"` → tüm `shell/` servis edilir |
| Derlemede silinir mi? | `tools/build-ui-registry.py` yalnız `shell/panels/` klasörünü `rmtree` eder |
| Git'te durur mu? | `.gitignore` yalnız `shell/panels/` ve `shell/registry.json` satırlarını taşır |
| Yol doğru çözülür mü? | `shell/panels/<id>/index.js` → `../..` = `shell/` → `shell/ui-kit/` |
| CSP'ye takılır mı? | Aynı köken; `script-src 'self'` / `style-src 'self'` yeterli |
| Derleme betiği değişir mi? | **Hayır, tek satır bile** |

Değerlendirilen ve reddedilen alternatifler:

- **Betik ortak klasörü `panels/_kit/`'e kopyalasın.** Dağıtımı çözer,
  sahipliği çözmez. Hedef klasör `.gitignore`'da olduğu için servis edilen
  dosya git'te olmaz; betik kendi docstring'inde geçici ilan edilmiştir ve
  silindiğinde sözleşme yeniden yazılmak zorunda kalır.
- **`apps/desktop/ui-kernel/` (bugün boş) kullanılsın.** Kullanılamaz:
  `frontendDist` dışındadır, oradaki dosya webview'e servis edilmez. Ayrıca o
  klasör `apps/desktop/README.md`'de panel keşfi/yükleme için ayrılmıştır —
  bileşen kiti için değil.
- **Import map (`@kit/`).** Daha okunur olurdu ama satır içi
  `<script type="importmap">` CSP `script-src 'self'` altında çalışmaz;
  harici import map desteği WebKitGTK'da güvenilmez.

## Sonuçlar

- **K6 korunur.** Kit bir kez yazıldıktan sonra yeni modül eklemek çekirdekte
  sıfır satır değiştirir — K6'nın koruduğu tam olarak budur.
- **K3 korunur.** Kit modül değildir; modül modülü import etmemeye devam eder.
- **Bağlaşım kabul edilir.** Kit'te kırılan şey 20 paneli birden kırar.
  Karşılığı, 20 panelde birden düzelmesidir. Modüller bağımsız dağıtılmadığı
  için (tek depo, tek uygulama derlemesi) "20 bağımsız kopya" avantajı hiçbir
  zaman kullanılamaz; dezavantajı her gün ödenir.
- `shell/ui-kit/README.md` bir **değişiklik günlüğü** tutar. Yayınlanmış imza
  geriye dönük uyumsuz değiştirilmez; yeni davranış yeni parametreyle gelir.
- **Mevcut 10 panel bu ADR ile otomatik taşınmaz.** Kopyaları bugün çalışıyor;
  taşıma ayrı ve isteğe bağlı bir iştir. Yeni panel yazan kit'i kullanır. İki
  desen bir süre yan yana yaşar — bilinçli.
- Panel kaynağı (`modules/<id>/ui/panel/`) içindeyken `../../ui-kit/` yolu
  dosya sisteminde **çözülmez**; yalnız kopyalanmış konumda çözülür. Her panel
  dosyasının başında bunu söyleyen bir yorum bloğu zorunludur.
