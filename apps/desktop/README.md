# apps/desktop/

Tauri 2 masaüstü kabuğu. Python çekirdeği sidecar süreç olarak çalışır; arayüz
ona `127.0.0.1` üzerinden konuşur.

| Klasör | Sorumluluk |
|---|---|
| `shell/` | Kabuk arayüzü: pencere, menü, yerleşim, oturum |
| `shell/core-panels/` | **Çekirdek ekranları** — modül değildirler (ADR 0017) |
| `shell/ui-kit/` | Panellerin ortak bileşen seti (ADR 0011) |
| `shell/panels/` | Modül panellerinin **üretilen** kopyası — git dışı |
| `ui-kernel/` | Modül panellerinin keşfi ve dinamik yüklenmesi. Backend'in modül kayıt uç noktasını okur, `module.yaml` içindeki `ui` bloğuna göre paneli yükler ve menüye yerleştirir |
| `src-tauri/` | Rust tarafı: pencere yapılandırması, sidecar yaşam döngüsü, paketleme |

**Kabukta modül adı geçmez** — K1'in arayüz tarafındaki karşılığı. Yeni modülün
paneli, kabukta tek satır değişmeden görünür.

Kabuk seçimi bu klasörde izoledir; Electron'a geçiş backend'i, platformu ve
modülleri etkilemez (ADR 0002).

## Çalıştırma

Ön koşullar: `scripts/install-deps.sh --with-desktop` (apt paketleri) ve Rust
([rustup](https://rustup.rs) ile, apt ile değil).

```bash
cd apps/desktop
npm install          # Tauri CLI — ilan edilir, depoya kopyalanmaz (K11)
npm run dev          # geliştirme penceresi
npm run build        # .deb + AppImage
```

> **Snap tuzağı.** Terminal bir snap uygulamasının (ör. snap paketli VS Code)
> içinden açıldıysa ortamda snap'in kendi kütüphane yolları gelir
> (`LD_LIBRARY_PATH`, `GTK_EXE_PREFIX`, `GDK_PIXBUF_MODULE_FILE`, snap altına
> bakan `XDG_DATA_HOME`…) ve kabuk açılışta
> `__libc_pthread_init ... GLIBC_PRIVATE` hatasıyla düşer. Uygulamayla ilgisi
> yoktur. Tek değişken temizlemek yetmeyebilir; en güvenlisi ortamı sıfırlayıp
> yalnızca gerekeni vermektir:
>
> ```bash
> env -i HOME="$HOME" USER="$USER" PATH=/usr/local/bin:/usr/bin:/bin \
>   DISPLAY="$DISPLAY" WAYLAND_DISPLAY="$WAYLAND_DISPLAY" \
>   XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
>   DBUS_SESSION_BUS_ADDRESS="$DBUS_SESSION_BUS_ADDRESS" \
>   src-tauri/target/debug/kontrol-merkezi
> ```
>
> Snap dışı normal bir terminalde (GNOME Terminal, tty) böyle bir sorun yoktur.

## Menü nereden geliyor

Menü **iki kaynaktan** kurulur ve ikisi `shell/ui-kernel.js` →
`loadRegistry()` içinde birleşir (ADR 0017).

### 1. Modül ekranları — çalışma anında sidecar'dan

Kabuk hangi modüllerin olduğunu bilmez; **çekirdeğe sorar**:
`GET /modules`. Kayıt modüllerin `module.yaml` → `ui.nav` bloklarından gelir,
platforma göre elenir (ADR 0022) ve kullanıcının izinlerine göre süzülür —
süzmeyi çekirdek yapar, kabuk yalnızca çizer (K1, K9).

`shell/registry.json` **artık çalışma anında okunmaz.** Dosya hâlâ üretiliyor:
`tools/build-ui-registry.py` `modules/*/module.yaml` bloklarını toplar, şemaya
uymayan manifesti atlar (o modül düşer, kabuk ayakta kalır — K7) ve sonucu
`registry.json` olarak yazar; `--check` ile CI'da doğrulanır. Asıl işi ise
panel dosyalarını `shell/panels/<id>/` altına **kopyalamaktır** — webview
yalnız `shell/` kökünü görüyor ve paket toplayıcı yok. `npm run dev` /
`npm run build` betiği kendiliğinden çalıştırır; hem `registry.json` hem
`shell/panels/` git dışıdır ve **üretilen** çıktıdır. Kaynak her zaman modülün
kendi klasörüdür (K6).

Yeni ekran = `modules/` altına yeni klasör. Kabukta tek satır değişmez (K6).
Menü grubunun sırası da veriden gelir: bir grubun sırası içindeki en küçük
`ui.nav.order` değeridir, kabukta sabit grup listesi tutulmaz.

### 2. Çekirdek ekranları — `shell/core-panels/`

Kullanıcı Yönetimi, Sistem Ayarları gibi ekranlar **modül değildir**:
manifestleri yoktur, `registry.json`'a girmezler, kapatılamazlar ve `modules/`
klasörü tümüyle silinse bile çalışırlar (ADR 0017 §4).

```
shell/
  core-panels/
    users/      index.js · panel.css     Kullanıcı Yönetimi (users.view)
    settings/   index.js · panel.css     Sistem Ayarları    (settings.view)
  panels/       ← modüllerden KOPYALANAN paneller (git dışı)
```

Listeleri `shell/ui-kernel.js` içindeki sabit `CORE_PANELS` dizisidir; manifest
taramasından gelmez ve `shell/panels/` altına kopyalanmaz. `/modules` ucu
çekirdek ekranlarını da bildirir, ama `loadRegistry()` `source === 'core'`
girdilerini atlar — yoksa aynı ekran iki kez çizilirdi.

Menüde modül gruplarının arasına karışmazlar: en altta kendi **"Sistem"**
grubunda dururlar ve bu sıra bir `order` yarışına değil, kurala bağlıdır
(ADR 0017 §2). `entry` alanı boş olan çekirdek ekranı menüde durur, gövdesinde
"ekranı henüz yok" kartı çıkar — paneli yazılmamış ekranın bugünkü hâli budur.

`requires` burada **yetkilendirme değildir**, yalnız menü görünürlüğüdür; aynı
izin backend'de yeniden denetlenir (K9 — çift kapı, ADR 0017 §3). Çekirdek
panelleri de `shell/ui-kit/` bileşenlerini kullanır (ADR 0011); ayrı bir
bileşen seti doğmaz.

---

Arayüz saf HTML/CSS/JS'tir: paket toplayıcı (bundler) yok, `shell/` doğrudan
`frontendDist` olarak sunulur. Dosyayı kaydedip pencereyi yenilemek yeter.
