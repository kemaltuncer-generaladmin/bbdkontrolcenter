# apps/desktop/

Tauri 2 masaüstü kabuğu. Python çekirdeği sidecar süreç olarak çalışır; arayüz
ona `127.0.0.1` üzerinden konuşur.

| Klasör | Sorumluluk |
|---|---|
| `shell/` | Kabuk arayüzü: pencere, menü, yerleşim, oturum |
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

Kabuk hangi ekranların olduğunu bilmez; `shell/registry.json` dosyasından
okur. O dosyayı `tools/build-ui-registry.py` üretir: `modules/*/module.yaml`
içindeki `ui.nav` bloklarını toplar, şemaya uymayan manifesti atlar (o modül
düşer, kabuk ayakta kalır — K7) ve çekirdeğin kendi ekranlarını ekler. `npm run
dev` / `npm run build` bunu kendiliğinden çalıştırır; dosya git dışıdır.

Yeni ekran = `modules/` altına yeni klasör. Kabukta tek satır değişmez (K6).

Çekirdek ayağa kalkınca kaydın kaynağı sidecar'ın modül kayıt ucu olacak;
değişecek tek yer `shell/ui-kernel.js` içindeki `loadRegistry()`.

Arayüz saf HTML/CSS/JS'tir: paket toplayıcı (bundler) yok, `shell/` doğrudan
`frontendDist` olarak sunulur. Dosyayı kaydedip pencereyi yenilemek yeter.
