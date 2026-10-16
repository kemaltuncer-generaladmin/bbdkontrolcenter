# ADR 0032 — Masaüstü uygulaması hissi ("native his")

**Durum:** Kabul edildi · 16.10.2026
**İş paketi:** 63-km-native (lider cd1af019)
**Etkilediği:** `apps/desktop/src-tauri/` (`src/native.rs` yeni, `src/main.rs`,
`Cargo.toml`, `tauri.conf.json`), `apps/desktop/shell/` (`boot.js`, `native.js`,
`net.js`, `connection.js`, `findbar.js`, `dialogs.js`, `perf.js`, `update.js`
yeni; `app.js`, `ui-kernel.js`, `global-search.js`, `index.html`, `style.css`),
`apps/desktop/shell/ui-kit/` 1.6.0 (`native.js`, `select.js` yeni; `kit.js`,
`kit.css`), `tools/a11y/harness.py`
**Değiştirdiği:** ADR 0026'nın "İnternet kesilirse uygulama durur" notu
(aşağıda §5). Mimari (sunucu merkezli, ince kabuk) değişmez.

## Bağlam

Kullanıcı (16.10.2026): "km yi gerçek native uygulama hissiyatına kavuşturalım".

KM bir Tauri 2 kabuğu ama bir web sayfası gibi davranıyordu: yerel menü
çubuğu, bağlam menüsü, dosya pencereleri ve pencere durumu hatırlama yoktu;
sağ tık hiçbir şey yapmıyordu; açılır listeler tarayıcının listesiydi; her
gezinmede iskelet görünüp her şey yeniden çiziliyordu; arka plan sabit koyu,
yazı tipi `system-ui`; internet gidince uygulama duruyordu. Kullanıcılar
ağırlıkla Windows'lu dershane bilgisayarları ve macOS.

## Karar

### 1. Pencere ve sistem

* **Pencere kodda kurulur** (`native.rs → create_main_window`), `tauri.conf.json`
  `app.windows` boştur: platforma göre farklı seçenek gerekiyor. Pencere ilk
  boyaya kadar **gizli** açılır; arayüz giriş kartını boyayınca `window_ready`
  der (4 sn güvenlik ağı). Beyaz/siyah kare yanıp sönmez.
* **macOS:** bütünleşik başlık çubuğu (`TitleBarStyle::Overlay`, trafik ışıkları
  içerik üstünde, `data-km-drag` bölgeleri sürükler — `window_start_drag`
  komutu; Tauri'nin hazır betiği `core:window:*` izni isterdi). Pencereyi
  kapatmak uygulamadan çıkmaz, gizler; Dock tıklaması geri getirir; çıkış ⌘Q.
* **Windows 11:** Mica arka planı **isteğe bağlı, varsayılan KAPALI**
  (Tercihler → "Mica arka planı", bir sonraki açılışta). Saydam WebView2
  penceresi gerçek bir Windows 11 makinesinde denenmeden yüzlerce dershane
  bilgisayarına varsayılan olarak gönderilmez. Windows 10'da seçenek görünmez
  (`windows-version`, derleme ≥ 22000). Başlık çubuğu ve yerel menüler uygulama
  temasını izler (`window_set_theme`).
* **Yerel menü çubuğu:** Dosya / Düzen / Görünüm / Git / Pencere / Yardım
  (+ macOS uygulama menüsü). Git menüsü kullanıcının **en sık açtığı 9 ekranı**
  Ctrl/⌘+1…9 ile listeler (arayüz `menu_sync` ile bildirir). Menü olayı arayüze
  `eval` ile gider (`window.__kmNative.menu(id)`) — `core:event:*` izni açmamak için.
* **Kısayollar** (menüde görünür, arayüz de dinler; Windows'ta WebView2 odağı
  tutarken hızlandırıcının sayfaya mı menüye mi gideceği sürüme göre değiştiği
  için aynı komut 350 ms içinde FARKLI yoldan ikinci kez gelirse yok sayılır):
  Ctrl/⌘+K genel arama · +F sayfada bul (+G/⇧G sonraki/önceki) · +P etkin
  ekranın yazdır eylemi · +[ / ] geri/ileri (ekran geçmişi; Türkçe Q'da Ğ/Ü
  tuşu, fiziksel tuş) · +1…9 sık ekranlar · +R / F5 ekranı yenile (oturumu
  düşüren sayfa yenilemesi değil) · F11 (macOS ⌃⌘F) tam ekran · +, Tercihler ·
  +± / 0 yakınlaştırma (kalıcı; basılan karaktere bakar) · +\ kenar çubuğu ·
  +L ekran listesi · +/ kısayollar · ⇧+L oturumu kapat · Alt+←/→ (Windows).
  PIN rakamları, Ctrl+C/V/X/Z ve AltGr karakterleri kısayola dönüşmez.
* **Yazdır (Ctrl/⌘+P):** önce açık pencere/çekmece, sonra ekran: `data-command="print"`
  taşıyan düğme, yoksa adı "Yazdır" ile başlayan görünür düğme. Yoksa kabuk
  "yazdırılacak belge yok" der. (Rapor ekranları için `data-command="print"`
  önerilir — panel uyumu listesi raporda.)
* **Eklentiler** (K11: `Cargo.toml`'da ilan): `tauri-plugin-window-state`
  (boyut/konum/ekran/büyütülmüş/tam ekran — görünürlük HARİÇ),
  `tauri-plugin-single-instance` (ilk eklenti; ikinci açılış mevcut pencereyi öne
  getirir), `tauri-plugin-notification`, `tauri-plugin-dialog`. **JS tarafları
  kullanılmaz** (paketleyici yok); kabuk onlara kendi `#[tauri::command]`
  sarmalayıcılarıyla ulaşır. Uygulama komutları ACL'e tabi olmadığından
  `capabilities/` altında **hiçbir yeni izin açılmadı** (yalnız eski
  `updater:default`). Dosyayı Rust okur/yazar; arayüze `fs:*` izni yoktur.
* **ui-kit API'si** (diğer paketler kullanır): `notify(başlık, metin, {whenInactive})`,
  `saveFile({name, data, filters})`, `openFile({accept, multiple}) → File[]`
  (`ui-kit/native.js`, `kit.js` de yeniden dışa vurur). Tarayıcıda aynı çağrı
  `<a download>` / `<input type=file>` / `Notification` yoluna düşer.
  `csvBlob` masaüstünde yerel "Farklı kaydet" açar (imza değişmedi).
* **Dış adresler** uygulamanın içinde açılmaz: `window.open`, `target=_blank`,
  dış bağlantı ve dış adrese gidiş sistemin tarayıcısında açılır
  (`on_navigation` / `on_new_window` son kapı). `window.open(blob:…)` (ör. PDF)
  geçici klasöre yazılıp varsayılan uygulamayla açılır. `<a download>` (panel
  kodu dahil, belgeye eklenmemiş bağlantı) yerel "Farklı kaydet"e gider.

### 2. Tarayıcı izleri

Sağ tıkta bağlama uygun menü — masaüstünde **işletim sisteminin kendi menüsü**
(`context_menu` komutu, `Window::popup_menu`), tarayıcıda sayfa içi yedek:
tablo satırı → Satırı kopyala, Hücreyi kopyala, Ayrıntıyı aç, Tabloyu dışa aktar
(CSV); yazı alanı → Geri al/Yinele/Kes/Kopyala/Yapıştır/Tümünü seç; seçili
metin → Kopyala, "… için genel arama"; bağlantı/görsel → adresi kopyala/
görseli kaydet; her yerde Geri/İleri/Yenile/Bul/Yazdır. Panel kendi satırını
`km-contextmenu` olayıyla ekler (`event.detail.add({label, run})`; K1).
Metin seçimi yalnız içerikte (`.panel-body`; düğme/sekme/başlık seçilmez);
bağlantı/görsel sürüklenmez; pencereye bırakılan dosya sayfayı o dosyaya
götürmez; Backspace ve fare yan tuşları ekran geçmişinde gezer; Ctrl+tekerlek
ve kıstırma yakınlaştırmaz (yerine Görünüm → Yakınlaştır, kalıcı); lastik
bant kaydırma yok; odak halkası yalnız klavyeyle (`:focus-visible`); düğme,
sekme, satır ve bağlantıda ok imleci.

### 3. Görünüm

Tema **sistemi izler** (+ Tercihler'den Açık/Koyu); `boot.js` ilk boyadan önce
`data-theme` yazar. Açık tema varsayılandır ve kenar çubuğu da açıktır.
**Panel kâğıdı koyu temada AÇIK kalır**; panel `export const darkReady = true`
derse kâğıt da koyulaşır (`.km-dark-paper`). Sabit renk yazmış ~50 panelin
koyu temada okunmaz hâle gelmesi kabul edilemezdi; kit (1.6.0) artık
zemine sabit beyaz yazmaz, yüzey jetonları kâğıttan gelir. Yazı tipi yığını
SF Pro / Segoe UI Variable; denetim ölçüsü platforma (Windows 4 px köşe,
macOS 30 px yükseklik) ve yoğunluğa (Rahat/Sıkı) göre. **Bütün tekli
`<select>`lar** klavyeyle aranabilen listeyle açılır (`ui-kit/select.js`,
`<select>` DOM'da kalır — paneller değişmeden kazanır). Kaydırma çubukları
platform gibi; geçişler ≤ 150 ms, `prefers-reduced-motion` uyulur.

### 4. Hız hissi

* **API yanıt önbelleği** (`net.js`, bellekte, oturumla silinir): GET yanıtları
  saklanır; **yalnız ekran açılış penceresindeki (2 sn) okumalar** son bilinen
  yanıtla HEMEN cevaplanır, arkada ağa sorulur (stale-while-revalidate, en
  fazla 10 dk eski). Yoklama ve sonraki her okuma ağa gider — merkezdeki
  değişikliğin 1–3 sn'de ekrana ulaşma sözü bozulmaz. Tazeleme farklı veri
  getirirse ekran (kullanıcı dokunmadıysa) sessizce, önbellekten anında
  yeniden çizilir; dokunduysa başlıkta "Yeni veriler var · Yenile" çıkar.
  Yazma (GET dışı) önbelleğin TAMAMINI boşaltır.
* **Neden ekranları canlı (DOM'da) tutmadık:** 8+ panel `document`/`window`
  düzeyinde tuş dinleyicisi kuruyor; gizli tutulan panel başka ekran açıkken
  tuşlara (Ctrl+S, Esc, oklar) tepki verir, yoklamalar arkada sürerdi. Aynı
  "anında geri dönüş" önbellekli yeniden kurulumla, panellere dokunmadan
  sağlanır; kaydırma konumu geri yüklenir.
* İskelet yalnız 150 ms'den uzun yüklemede (`.km-fresh`: ilk 150 ms
  `*skel*`/`aria-busy` öğeleri görünmez — bütün panellerin iskeletini kapsar).
* Açılışta son ekran (Tercihler'den kapatılabilir); boşta en sık 5 ekranın
  dosyası önceden indirilir (`preloadPanel`).
* **Ölçüm** (`perf.js`, Yardım → Hız ölçümleri, `[hız]` konsol günlüğü):
  taklit merkez, 160 ms gidiş-dönüş, aynı tarayıcı, dış ölçüm (tık → DOM
  oturdu):

  | Ekran | Önce: ilk / dönüş | Sonra: ilk / dönüş |
  |---|---|---|
  | Zil Sistemi | 173 / 172 ms | 177 / 3 ms |
  | Müşteri Listesi | 508 / 503 ms | 508 / 4 ms |
  | Öğle Yemeği | 516 / 514 ms | 512 / 4 ms |
  | SMS Sistemi | 343 / 338 ms | 279 / 3 ms |

  İlk açılış ağa bağlıdır ve değişmedi; geri dönüş ağdan bağımsızlaştı.

### 5. Bağlantı dayanıklılığı (ADR 0026 notunu değiştirir)

İnternet/sunucu kesilince uygulama **durmaz**: üstte ince "Bağlantı yok,
yeniden deneniyor…" şeridi (+ Şimdi dene), `/health` artan aralıkla yoklanır;
okumalar son bilinen yanıttan gelir; bağlantı gelince şerit "geri geldi" der
ve etkin ekran ağdan tazelenir. Açılışta merkez yoksa giriş ekranı 20 sn
sonra pes etmez, süresiz bekler ve kaçıncı deneme olduğunu yazar.

**Yazmalar açık hata verir, sessiz kuyruk YOK.** Rust köprüsü hatanın türünü
taşır (`[connect]`/`[timeout]`/`[network]`, `failure_kind`):
* istek sunucuya **hiç ulaşmadıysa** (`connect`) kullanıcıya "İşlem
  gönderilemedi — HİÇBİR ŞEY KAYDEDİLMEDİ · Yeniden dene / Vazgeç" sorulur;
  aynı anda düşen yazmalar tek pencereyi paylaşır;
* istek **gitmiş olabilirse** (zaman aşımı, yolda kopma, 502–504) yeniden
  deneme ÖNERİLMEZ: "işlem kaydedilmiş OLABİLİR — yenileyip kontrol edin".
  Para ve yoklama gibi işlemlerde kör yeniden deneme çift kayıt üretir.
Kimlik/eşleme uçları kendi hata cümlesini kurar (pencere açılmaz).

### 6. Güncelleme

Kabuk açıldıktan 45 sn sonra ve 6 saatte bir sessizce denetler, varsa arkada
indirir (aynı Rust komutları); paket hazır olunca altta şerit ve (pencere
arkadaysa) sistem bildirimi: "Yeniden başlat ve güncelle". Kurulum yalnız
kullanıcı basınca; "Sonra" o oturumda bir daha sormaz. Zorla kapatma yok.

## Sonuçlar

* Menü/kısayol/bağlam menüsü komut kimlikleri Rust (`native.rs`) ve arayüz
  (`app.js → defineCommand`) arasında aynıdır; eşleşme testlidir
  (`tests/integration/test_kabuk_native_his.py`).
* `CLAUDE.md` / `ARCHITECTURE.md`'deki "İnternet kesilirse uygulama durur"
  cümlesi bu ADR ile eskidi (o dosyalar km-mimari paketinindir; lider günceller).
* Windows'ta gerçek makinede denenmesi gerekenler (WebView2 hızlandırıcıları,
  Mica, `rundll32 url.dll` ile dış adres, yerel bağlam menüsü, tek örnek)
  sürüm notunda listelenir.
