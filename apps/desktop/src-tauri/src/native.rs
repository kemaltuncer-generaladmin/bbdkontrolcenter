// Native his (ADR 0032) — pencere, menü çubuğu, bağlam menüsü, bildirim ve
// dosya pencereleri.
//
// KABUK YİNE BORUDUR (K1). Bu dosyada modül adı ya da iş kuralı geçmez:
// menünün "Git" bölümündeki ekran adlarını arayüz verir (`menu_sync`), bağlam
// menüsünün satırlarını da arayüz kurar (`context_menu`). Rust yalnız işletim
// sisteminin kendi bileşenini çizer ve seçilen satırın kimliğini geri yollar.
//
// EKLENTİLERİN JS TARAFI KULLANILMAZ. Kabukta paketleyici yok; bildirim ve
// dosya pencerelerine aşağıdaki `#[tauri::command]` sarmalayıcılarıyla
// ulaşılır. Uygulamanın kendi komutları ACL'e tabi değildir; eklentilerin JS
// komutlarına `capabilities/` altında HİÇBİR izin açılmaz (en az izin).
//
// MENÜ OLAYI ARAYÜZE `eval` İLE GİDER, olay veri yoluyla değil: `listen`
// kullanmak `core:event:*` izni açmayı gerektirirdi. Gönderilen tek şey
// menünün kendi kimliğidir (ASCII, JSON olarak kaçışlı).

use std::path::PathBuf;
use std::sync::Mutex;

use base64::Engine as _;
use tauri::menu::{
    CheckMenuItem, IsMenuItem, Menu, MenuItem, PredefinedMenuItem, Submenu,
};
use tauri::{AppHandle, Manager, Runtime, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

/// Ana pencerenin etiketi. `capabilities/*.json` de bu adı kullanır.
pub const MAIN: &str = "main";

/// Arayüz ilk boyayı yapıp `window_ready` demezse pencere yine de açılır.
/// Gizli kalmış bir uygulama, beyaz bir pencereden kat kat kötüdür.
const SHOW_FALLBACK_MS: u64 = 4000;

/// Yerel tercih dosyası — pencere KURULMADAN okunması gereken ayarlar.
/// Arayüzün `localStorage`ı Rust'tan okunamaz; pencere kurulurken bilinmesi
/// gereken tek ayar (Windows 11 Mica) bu yüzden ayrı bir dosyada durur.
const PREFS_FILE: &str = "native-prefs.json";

// ------------------------------------------------------------ platform bilgisi

#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct NativeInfo {
    /// `macos` · `windows` · `linux`
    platform: &'static str,
    /// Pencere Mica arka planıyla mı kuruldu (yalnız Windows 11 + tercih açık).
    mica: bool,
    /// Mica bu makinede KULLANILABİLİR mi (Windows 11).
    mica_supported: bool,
    /// macOS bütünleşik başlık çubuğu (trafik ışıkları içeriğin üstünde).
    overlay_title_bar: bool,
    version: String,
}

fn platform_name() -> &'static str {
    if cfg!(target_os = "macos") {
        "macos"
    } else if cfg!(windows) {
        "windows"
    } else {
        "linux"
    }
}

/// Mica yalnız Windows 11'de (derleme 22000+) vardır. Windows 10'da etki
/// sessizce uygulanmaz ve saydam pencere arkasındaki masaüstünü gösterirdi —
/// bu yüzden karar pencere KURULMADAN, sürüme bakılarak verilir.
#[cfg(windows)]
fn mica_supported() -> bool {
    windows_version::OsVersion::current().build >= 22000
}

#[cfg(not(windows))]
fn mica_supported() -> bool {
    false
}

#[derive(Default, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct NativePrefs {
    /// Windows 11 Mica arka planı. VARSAYILAN KAPALI: saydam WebView2 penceresi
    /// gerçek bir Windows 11 makinesinde denenmeden yüzlerce dershane
    /// bilgisayarına varsayılan olarak gönderilmez (ADR 0032 §Pencere).
    mica: bool,
}

fn prefs_path(app: &AppHandle) -> Option<PathBuf> {
    let dir = app.path().app_config_dir().ok()?;
    std::fs::create_dir_all(&dir).ok()?;
    Some(dir.join(PREFS_FILE))
}

fn read_prefs(app: &AppHandle) -> NativePrefs {
    prefs_path(app)
        .and_then(|path| std::fs::read(path).ok())
        .and_then(|bytes| serde_json::from_slice(&bytes).ok())
        .unwrap_or_default()
}

pub struct WindowFlags {
    mica: bool,
}

// --------------------------------------------------------------- ana pencere

/// Ana pencereyi kurar.
///
/// NEDEN `tauri.conf.json` DEĞİL: platforma göre farklı seçenek gerekiyor
/// (macOS bütünleşik başlık çubuğu, Windows 11 Mica için saydamlık) ve
/// pencere ilk boyaya kadar GİZLİ açılır — beyaz/siyah bir kare yanıp
/// sönmez; arayüz hazır olunca `window_ready` ile görünür olur.
pub fn create_main_window(app: &AppHandle) -> tauri::Result<WebviewWindow> {
    let mica = mica_supported() && read_prefs(app).mica;

    let builder = WebviewWindowBuilder::new(app, MAIN, WebviewUrl::App("index.html".into()))
        .title("Kontrol Merkezi")
        .inner_size(1180.0, 780.0)
        .min_inner_size(900.0, 640.0)
        .center()
        .resizable(true)
        .visible(false)
        .background_color(tauri::window::Color(0xF3, 0xF4, 0xF7, 0xFF))
        // Windows'ta WebView2'nin kendi sürükle-bırak yakalayıcısı HTML5
        // sürükle-bırakı (görsel alanına dosya bırakma) kapatıyordu. Kabuk
        // Tauri'nin dosya bırakma olayını kullanmıyor.
        .disable_drag_drop_handler()
        // Ctrl+tekerlek / Ctrl+± ile rastgele yakınlaştırma kapalı; yerine
        // Görünüm → Yakınlaştır (kalıcı) var.
        .zoom_hotkeys_enabled(false)
        // Bağlam menüsündeki "Yapıştır" panoyu okuyabilsin (Windows/Linux;
        // macOS'ta her zaman açık). Sayfa yalnız kendi kodumuzu çalıştırır (CSP).
        .enable_clipboard_access()
        // UYGULAMA KENDİ PENCERESİNİ KAYBETMEZ. Dış bir adrese gidiş web
        // görünümünün içinde değil, sistemin tarayıcısında açılır; kabuğun
        // kendi kökeni (tauri://, http://tauri.localhost) ve yerel çekirdek
        // serbesttir.
        .on_navigation(|url| {
            if is_app_url(url) {
                return true;
            }
            if is_external(url) {
                let _ = open_with_system(url.as_str());
            }
            false
        })
        // `window.open` ve `target=_blank`: yeni bir tarayıcı penceresi (WebView2
        // açılır pencere) yerine sistemin tarayıcısı; arayüz `window.open`ı
        // zaten kendisi yönlendiriyor (native.js), bu son kapıdır.
        .on_new_window(|url, _features| {
            if is_external(&url) {
                let _ = open_with_system(url.as_str());
            }
            tauri::webview::NewWindowResponse::Deny
        });

    #[cfg(target_os = "macos")]
    let builder = builder
        .title_bar_style(tauri::TitleBarStyle::Overlay)
        .hidden_title(true)
        .traffic_light_position(tauri::LogicalPosition::new(16.0, 22.0));

    #[cfg(windows)]
    let builder = if mica {
        builder.transparent(true).effects(tauri::utils::config::WindowEffectsConfig {
            effects: vec![tauri::window::Effect::Mica],
            state: None,
            radius: None,
            color: None,
            // Tauri sürümleri yeni alan ekledikçe (2.12: `interactive`) derleme
            // kırılmasın: kalan alanlar varsayılan.
            ..Default::default()
        })
    } else {
        builder
    };

    let window = builder.build()?;
    app.manage(WindowFlags { mica });

    // GÜVENLİK AĞI: arayüz hiç `window_ready` demezse (betik hatası, beyaz
    // ekran) pencere yine görünür; gizli kalan bir uygulamayı kullanıcı
    // "açılmıyor" diye okur.
    let handle = app.clone();
    std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_millis(SHOW_FALLBACK_MS));
        if let Some(window) = handle.get_webview_window(MAIN) {
            if !window.is_visible().unwrap_or(true) {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }
    });

    Ok(window)
}

/// Ana pencereyi öne getirir — tek örnek eklentisi, macOS dock tıklaması.
pub fn focus_main<R: Runtime>(app: &AppHandle<R>) {
    if let Some(window) = app.get_webview_window(MAIN) {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

// ------------------------------------------------------------------- komutlar

#[tauri::command]
pub fn native_info(app: AppHandle) -> NativeInfo {
    let mica = app.try_state::<WindowFlags>().map(|flags| flags.mica).unwrap_or(false);
    NativeInfo {
        platform: platform_name(),
        mica,
        mica_supported: mica_supported(),
        overlay_title_bar: cfg!(target_os = "macos"),
        version: app.package_info().version.to_string(),
    }
}

/// Arayüz ilk boyayı yaptı: pencere görünür.
#[tauri::command]
pub fn window_ready(window: WebviewWindow) {
    if !window.is_visible().unwrap_or(false) {
        let _ = window.show();
        let _ = window.set_focus();
    }
}

/// Kalıcı yakınlaştırma (Görünüm → Yakınlaştır). Ölçek 0,5–2,0 arasına
/// sıkıştırılır.
#[tauri::command]
pub fn window_set_zoom(window: WebviewWindow, factor: f64) -> Result<(), String> {
    let factor = if factor.is_finite() { factor.clamp(0.5, 2.0) } else { 1.0 };
    window.set_zoom(factor).map_err(|error| error.to_string())
}

#[tauri::command]
pub fn window_toggle_fullscreen(window: WebviewWindow) -> Result<bool, String> {
    let next = !window.is_fullscreen().map_err(|error| error.to_string())?;
    window.set_fullscreen(next).map_err(|error| error.to_string())?;
    Ok(next)
}

/// macOS bütünleşik başlık çubuğunda sürükleme. Tauri'nin hazır
/// `data-tauri-drag-region` betiği eklenti iznine (`core:window:*`) muhtaçtır;
/// uygulamanın kendi komutu izin açmadan aynı işi görür.
#[tauri::command]
pub fn window_start_drag(window: WebviewWindow) -> Result<(), String> {
    window.start_dragging().map_err(|error| error.to_string())
}

/// Başlık çubuğuna çift tık: büyüt / eski boyuta dön.
#[tauri::command]
pub fn window_toggle_maximize(window: WebviewWindow) -> Result<(), String> {
    if window.is_maximized().unwrap_or(false) {
        window.unmaximize().map_err(|error| error.to_string())
    } else {
        window.maximize().map_err(|error| error.to_string())
    }
}

/// Pencerenin (başlık çubuğu, yerel menüler) teması: `system` · `light` · `dark`.
#[tauri::command]
pub fn window_set_theme(window: WebviewWindow, theme: String) -> Result<(), String> {
    let value = match theme.as_str() {
        "light" => Some(tauri::Theme::Light),
        "dark" => Some(tauri::Theme::Dark),
        _ => None,
    };
    window.set_theme(value).map_err(|error| error.to_string())
}

/// Pencere kurulmadan okunması gereken tercih (bugün yalnız Mica). Yeni değer
/// bir SONRAKİ açılışta geçerli olur.
#[tauri::command]
pub fn native_set_prefs(app: AppHandle, mica: bool) -> Result<(), String> {
    let path = prefs_path(&app).ok_or("tercih klasörü açılamadı")?;
    let bytes = serde_json::to_vec_pretty(&NativePrefs { mica }).map_err(|e| e.to_string())?;
    std::fs::write(path, bytes).map_err(|error| error.to_string())
}

#[tauri::command]
pub fn native_get_prefs(app: AppHandle) -> NativePrefs {
    read_prefs(&app)
}

// ------------------------------------------------- dış adresler ve dosyalar

/// Kabuğun kendi sayfası ya da yerel çekirdek.
fn is_app_url(url: &tauri::Url) -> bool {
    match url.scheme() {
        "tauri" | "asset" | "ipc" | "blob" | "data" | "about" => true,
        "http" | "https" => matches!(
            url.host_str(),
            Some("tauri.localhost" | "ipc.localhost" | "asset.localhost" | "localhost" | "127.0.0.1")
        ),
        _ => false,
    }
}

/// Sistemin açabileceği güvenli dış adres türleri.
fn is_external(url: &tauri::Url) -> bool {
    matches!(url.scheme(), "http" | "https" | "mailto" | "tel") && !is_app_url(url)
}

/// Adresi ya da dosyayı işletim sisteminin varsayılan uygulamasıyla açar.
///
/// Kabuk komutu `cmd /C start` KULLANILMAZ: adres `&` taşıdığında komut
/// satırı onu ikinci bir komut sayar. `url.dll` adresi tek argüman olarak alır.
fn open_with_system(target: &str) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    let mut command = {
        let mut command = std::process::Command::new("open");
        command.arg(target);
        command
    };
    #[cfg(windows)]
    let mut command = {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        let mut command = std::process::Command::new("rundll32");
        command.arg("url.dll,FileProtocolHandler").arg(target);
        command.creation_flags(CREATE_NO_WINDOW);
        command
    };
    #[cfg(all(unix, not(target_os = "macos")))]
    let mut command = {
        let mut command = std::process::Command::new("xdg-open");
        command.arg(target);
        command
    };
    command.spawn().map(|_| ()).map_err(|error| format!("açılamadı: {error}"))
}

/// Dış adresi sistemin tarayıcısında açar (`window.open`, bağlantılar).
/// Yalnız http(s)/mailto/tel kabul edilir — `file:` ya da program yolu değil.
#[tauri::command]
pub fn native_open_url(url: String) -> Result<(), String> {
    let parsed = tauri::Url::parse(&url).map_err(|_| "geçersiz adres".to_string())?;
    if !is_external(&parsed) {
        return Err("bu adres türü dışarıda açılmaz".into());
    }
    open_with_system(parsed.as_str())
}

/// Bellekteki bir dosyayı (ör. `window.open(blob:…)` ile açılmak istenen PDF)
/// geçici klasöre yazar ve varsayılan uygulamayla açar. Ad süzülür; yalnız
/// uygulamanın kendi önbellek klasörüne yazılır.
#[tauri::command]
pub fn native_open_temp_file(app: AppHandle, name: String, base64: String) -> Result<String, String> {
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(base64.as_bytes())
        .map_err(|error| format!("dosya verisi çözülemedi: {error}"))?;
    let dir = app
        .path()
        .app_cache_dir()
        .map_err(|error| error.to_string())?
        .join("acilan");
    std::fs::create_dir_all(&dir).map_err(|error| error.to_string())?;
    let clean: String = name
        .chars()
        .map(|c| if c.is_alphanumeric() || matches!(c, '.' | '-' | '_') { c } else { '-' })
        .take(80)
        .collect();
    let clean = if clean.trim_matches(|c| c == '.' || c == '-').is_empty() { "dosya".to_string() } else { clean };
    let path = dir.join(clean);
    std::fs::write(&path, bytes).map_err(|error| format!("dosya yazılamadı: {error}"))?;
    open_with_system(&path.display().to_string())?;
    Ok(path.display().to_string())
}

// ---------------------------------------------------------------- bildirimler

/// İşletim sisteminin bildirim merkezine yazar (`kit.notify`).
#[tauri::command]
pub fn native_notify(app: AppHandle, title: String, body: Option<String>) -> Result<(), String> {
    use tauri_plugin_notification::NotificationExt;
    let mut builder = app.notification().builder().title(clip(&title, 120));
    if let Some(text) = body.filter(|text| !text.trim().is_empty()) {
        builder = builder.body(clip(&text, 400));
    }
    builder.show().map_err(|error| error.to_string())
}

fn clip(text: &str, max: usize) -> String {
    text.chars().take(max).collect()
}

// ---------------------------------------------------------- dosya pencereleri

#[derive(serde::Deserialize)]
pub struct FileFilter {
    name: String,
    extensions: Vec<String>,
}

/// Okunacak dosyanın üst sınırı. Arayüze base64 ile taşındığı için sınırsız
/// bırakılmaz; büyük dosya isteyen akış (ders kaydı vb.) ayrı yoldan gider.
const OPEN_MAX_BYTES: u64 = 64 * 1024 * 1024;

#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OpenedFile {
    name: String,
    size: u64,
    base64: String,
}

fn with_filters<R: Runtime>(
    mut dialog: tauri_plugin_dialog::FileDialogBuilder<R>,
    filters: &[FileFilter],
) -> tauri_plugin_dialog::FileDialogBuilder<R> {
    for filter in filters {
        let extensions: Vec<&str> = filter
            .extensions
            .iter()
            .map(|ext| ext.trim_start_matches('.'))
            .filter(|ext| !ext.is_empty())
            .collect();
        if !extensions.is_empty() {
            dialog = dialog.add_filter(&filter.name, &extensions);
        }
    }
    dialog
}

/// Yerel "Farklı kaydet" penceresi. Kullanıcı vazgeçerse `None`.
///
/// Dosyayı RUST YAZAR: arayüze dosya sistemi izni (`fs:*`) açılmaz; yazılan
/// tek yer kullanıcının pencerede seçtiği yoldur.
#[tauri::command]
pub async fn native_save_file(
    window: WebviewWindow,
    name: String,
    filters: Vec<FileFilter>,
    base64: String,
) -> Result<Option<String>, String> {
    use tauri_plugin_dialog::DialogExt;
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(base64.as_bytes())
        .map_err(|error| format!("dosya verisi çözülemedi: {error}"))?;
    let dialog = with_filters(
        window.dialog().file().set_parent(&window).set_file_name(name),
        &filters,
    );
    let Some(picked) = dialog.blocking_save_file() else {
        return Ok(None);
    };
    let path = picked.into_path().map_err(|error| error.to_string())?;
    std::fs::write(&path, bytes).map_err(|error| format!("dosya yazılamadı: {error}"))?;
    Ok(Some(path.display().to_string()))
}

/// Yerel "Aç" penceresi. Kullanıcı vazgeçerse boş liste.
#[tauri::command]
pub async fn native_open_files(
    window: WebviewWindow,
    filters: Vec<FileFilter>,
    multiple: bool,
) -> Result<Vec<OpenedFile>, String> {
    use tauri_plugin_dialog::DialogExt;
    let dialog = with_filters(window.dialog().file().set_parent(&window), &filters);
    let picked = if multiple {
        dialog.blocking_pick_files().unwrap_or_default()
    } else {
        dialog.blocking_pick_file().into_iter().collect()
    };

    let mut out = Vec::with_capacity(picked.len());
    for item in picked {
        let path = item.into_path().map_err(|error| error.to_string())?;
        let size = std::fs::metadata(&path).map(|meta| meta.len()).unwrap_or(0);
        let name = path
            .file_name()
            .map(|name| name.to_string_lossy().into_owned())
            .unwrap_or_else(|| "dosya".into());
        if size > OPEN_MAX_BYTES {
            return Err(format!(
                "“{name}” çok büyük ({} MB); en fazla {} MB.",
                size / (1024 * 1024),
                OPEN_MAX_BYTES / (1024 * 1024)
            ));
        }
        let bytes = std::fs::read(&path).map_err(|error| format!("“{name}” okunamadı: {error}"))?;
        out.push(OpenedFile {
            name,
            size,
            base64: base64::engine::general_purpose::STANDARD.encode(bytes),
        });
    }
    Ok(out)
}

// ------------------------------------------------------------------- menü çubuğu

/// Arayüzün menüye yansıttığı durum. Menü her `menu_sync` çağrısında yeniden
/// kurulur — küçük bir ağaç, ucuz.
#[derive(Default, Clone, serde::Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct MenuState {
    /// Oturum açık mı: kapalıyken ekrana bağlı satırlar pasif durur.
    signed_in: bool,
    /// Git → sık kullanılan ekranlar (en fazla 9, Ctrl/Cmd+1…9).
    screens: Vec<String>,
    /// `system` · `light` · `dark`
    theme: String,
    /// `comfortable` · `compact`
    density: String,
    can_back: bool,
    can_forward: bool,
}

pub struct MenuStore(pub Mutex<MenuState>);

/// Oturum açılmadan önceki menü: ekrana bağlı satırlar pasif.
pub fn initial_menu_state() -> MenuState {
    MenuState {
        theme: "system".into(),
        density: "comfortable".into(),
        ..MenuState::default()
    }
}

impl Default for MenuStore {
    fn default() -> Self {
        Self(Mutex::new(initial_menu_state()))
    }
}

fn item<R: Runtime>(
    app: &AppHandle<R>,
    id: &str,
    text: &str,
    enabled: bool,
    accelerator: Option<&str>,
) -> tauri::Result<MenuItem<R>> {
    MenuItem::with_id(app, format!("km:{id}"), text, enabled, accelerator)
}

fn check<R: Runtime>(
    app: &AppHandle<R>,
    id: &str,
    text: &str,
    checked: bool,
) -> tauri::Result<CheckMenuItem<R>> {
    CheckMenuItem::with_id(app, format!("km:{id}"), text, true, checked, None::<&str>)
}

fn submenu<R: Runtime>(
    app: &AppHandle<R>,
    text: &str,
    items: &[&dyn IsMenuItem<R>],
) -> tauri::Result<Submenu<R>> {
    Submenu::with_items(app, text, true, items)
}

/// Menü çubuğu.
///
/// KISAYOLLAR BURADA GÖRÜNÜR, ama arayüz de aynı tuşları dinler: Windows'ta
/// WebView2 odağı tutarken menü hızlandırıcısının sayfaya mı menüye mi
/// gideceği sürüme göre değişiyor. İki yol da aynı komuta varır; arayüz aynı
/// komutun 300 ms içinde ikinci kez gelmesini yok sayar.
///
/// DÜZEN MENÜSÜ: macOS'ta hazır Kes/Kopyala/Yapıştır öğeleri ŞARTTIR (yoksa
/// ⌘C/⌘V web görünümünde çalışmaz). Windows/Linux'ta hazır öğeler WebView2'ye
/// ulaşmadığı için aynı satırlar kısayolsuz, arayüzün `execCommand`ına giden
/// sıradan öğelerdir — Ctrl+C/V'yi web görünümü kendisi işler.
pub fn build_menu<R: Runtime>(app: &AppHandle<R>, state: &MenuState) -> tauri::Result<Menu<R>> {
    let signed = state.signed_in;
    let menu = Menu::new(app)?;
    let sep = || PredefinedMenuItem::separator(app);

    // ---- macOS uygulama menüsü
    #[cfg(target_os = "macos")]
    {
        let about = PredefinedMenuItem::about(app, Some("Kontrol Merkezi hakkında"), None)?;
        let prefs = item(app, "prefs", "Tercihler…", true, Some("CmdOrCtrl+Comma"))?;
        let update = item(app, "update", "Güncellemeleri denetle…", true, None)?;
        let services = PredefinedMenuItem::services(app, Some("Servisler"))?;
        let hide = PredefinedMenuItem::hide(app, Some("Kontrol Merkezi'ni gizle"))?;
        let others = PredefinedMenuItem::hide_others(app, Some("Diğerlerini gizle"))?;
        let all = PredefinedMenuItem::show_all(app, Some("Tümünü göster"))?;
        let quit = PredefinedMenuItem::quit(app, Some("Kontrol Merkezi'nden çık"))?;
        let (s1, s2, s3, s4) = (sep()?, sep()?, sep()?, sep()?);
        menu.append(&submenu(
            app,
            "Kontrol Merkezi",
            &[&about, &update, &s1, &prefs, &s2, &services, &s3, &hide, &others, &all, &s4, &quit],
        )?)?;
    }

    // ---- Dosya
    {
        let print = item(app, "print", "Yazdır…", signed, Some("CmdOrCtrl+P"))?;
        let export = item(app, "export", "Görünen listeyi dışa aktar…", signed, Some("CmdOrCtrl+Shift+E"))?;
        let signout = item(app, "signout", "Oturumu kapat", signed, Some("CmdOrCtrl+Shift+L"))?;
        let s1 = sep()?;
        let s2 = sep()?;
        #[cfg(not(target_os = "macos"))]
        {
            let prefs = item(app, "prefs", "Tercihler…", true, Some("CmdOrCtrl+Comma"))?;
            let s3 = sep()?;
            let quit = item(app, "quit", "Çıkış", true, None)?;
            menu.append(&submenu(
                app,
                "&Dosya",
                &[&print, &export, &s1, &prefs, &s2, &signout, &s3, &quit],
            )?)?;
        }
        #[cfg(target_os = "macos")]
        {
            let close = PredefinedMenuItem::close_window(app, Some("Pencereyi kapat"))?;
            menu.append(&submenu(app, "Dosya", &[&print, &export, &s1, &signout, &s2, &close])?)?;
        }
    }

    // ---- Düzen
    {
        let find = item(app, "find", "Sayfada bul…", signed, Some("CmdOrCtrl+F"))?;
        let next = item(app, "find-next", "Sonrakini bul", signed, Some("CmdOrCtrl+G"))?;
        let prev = item(app, "find-prev", "Öncekini bul", signed, Some("CmdOrCtrl+Shift+G"))?;
        let search = item(app, "search", "Genel arama…", signed, Some("CmdOrCtrl+K"))?;
        let (s1, s2) = (sep()?, sep()?);
        #[cfg(target_os = "macos")]
        {
            let undo = PredefinedMenuItem::undo(app, Some("Geri al"))?;
            let redo = PredefinedMenuItem::redo(app, Some("Yinele"))?;
            let cut = PredefinedMenuItem::cut(app, Some("Kes"))?;
            let copy = PredefinedMenuItem::copy(app, Some("Kopyala"))?;
            let paste = PredefinedMenuItem::paste(app, Some("Yapıştır"))?;
            let all = PredefinedMenuItem::select_all(app, Some("Tümünü seç"))?;
            let s0 = sep()?;
            menu.append(&submenu(
                app,
                "Düzen",
                &[&undo, &redo, &s0, &cut, &copy, &paste, &all, &s1, &find, &next, &prev, &s2, &search],
            )?)?;
        }
        #[cfg(not(target_os = "macos"))]
        {
            let undo = item(app, "edit:undo", "Geri al", true, None)?;
            let redo = item(app, "edit:redo", "Yinele", true, None)?;
            let cut = item(app, "edit:cut", "Kes", true, None)?;
            let copy = item(app, "edit:copy", "Kopyala", true, None)?;
            let paste = item(app, "edit:paste", "Yapıştır", true, None)?;
            let all = item(app, "edit:selectall", "Tümünü seç", true, None)?;
            let s0 = sep()?;
            menu.append(&submenu(
                app,
                "Dü&zen",
                &[&undo, &redo, &s0, &cut, &copy, &paste, &all, &s1, &find, &next, &prev, &s2, &search],
            )?)?;
        }
    }

    // ---- Görünüm
    {
        let refresh = item(app, "refresh", "Yenile", signed, Some("CmdOrCtrl+R"))?;
        let zin = item(app, "zoom-in", "Yakınlaştır", true, Some("CmdOrCtrl+Equal"))?;
        let zout = item(app, "zoom-out", "Uzaklaştır", true, Some("CmdOrCtrl+Minus"))?;
        let zreset = item(app, "zoom-reset", "Gerçek boyut", true, Some("CmdOrCtrl+Digit0"))?;
        let t_sys = check(app, "theme:system", "Sistemi izle", state.theme == "system")?;
        let t_light = check(app, "theme:light", "Açık", state.theme == "light")?;
        let t_dark = check(app, "theme:dark", "Koyu", state.theme == "dark")?;
        let theme = submenu(app, "Tema", &[&t_sys, &t_light, &t_dark])?;
        let d_comf = check(app, "density:comfortable", "Rahat", state.density != "compact")?;
        let d_comp = check(app, "density:compact", "Sıkı", state.density == "compact")?;
        let density = submenu(app, "Yoğunluk", &[&d_comf, &d_comp])?;
        let sidebar = item(app, "sidebar", "Kenar çubuğunu gizle/göster", signed, Some("CmdOrCtrl+Backslash"))?;
        let (s1, s2, s3) = (sep()?, sep()?, sep()?);
        #[cfg(target_os = "macos")]
        let full = PredefinedMenuItem::fullscreen(app, Some("Tam ekran"))?;
        #[cfg(not(target_os = "macos"))]
        let full = item(app, "fullscreen", "Tam ekran", true, Some("F11"))?;
        menu.append(&submenu(
            app,
            if cfg!(target_os = "macos") { "Görünüm" } else { "&Görünüm" },
            &[&refresh, &s1, &zin, &zout, &zreset, &s2, &theme, &density, &sidebar, &s3, &full],
        )?)?;
    }

    // ---- Git
    {
        let back = item(app, "back", "Geri", signed && state.can_back, Some("CmdOrCtrl+BracketLeft"))?;
        let fwd = item(app, "forward", "İleri", signed && state.can_forward, Some("CmdOrCtrl+BracketRight"))?;
        let filter = item(app, "go-filter", "Ekran listesinde ara", signed, Some("CmdOrCtrl+L"))?;
        let s1 = sep()?;
        let mut screens: Vec<MenuItem<R>> = Vec::new();
        for (index, title) in state.screens.iter().take(9).enumerate() {
            let n = index + 1;
            screens.push(item(
                app,
                &format!("go:{n}"),
                &clip(title, 60),
                signed,
                Some(&format!("CmdOrCtrl+Digit{n}")),
            )?);
        }
        let mut items: Vec<&dyn IsMenuItem<R>> = vec![&back, &fwd, &s1];
        let s2 = sep()?;
        let empty = item(app, "go:none", "Sık kullanılan ekran yok", false, None)?;
        if screens.is_empty() {
            items.push(&empty);
        } else {
            for screen in &screens {
                items.push(screen);
            }
        }
        items.push(&s2);
        items.push(&filter);
        menu.append(&submenu(app, if cfg!(target_os = "macos") { "Git" } else { "G&it" }, &items)?)?;
    }

    // ---- Pencere
    {
        let min = PredefinedMenuItem::minimize(app, Some("Simge durumuna küçült"))?;
        let max = PredefinedMenuItem::maximize(app, Some("Büyüt"))?;
        #[cfg(target_os = "macos")]
        {
            let s1 = sep()?;
            let front = PredefinedMenuItem::bring_all_to_front(app, Some("Tümünü öne getir"))?;
            menu.append(&submenu(app, "Pencere", &[&min, &max, &s1, &front])?)?;
        }
        #[cfg(not(target_os = "macos"))]
        menu.append(&submenu(app, "&Pencere", &[&min, &max])?)?;
    }

    // ---- Yardım
    {
        let keys = item(app, "shortcuts", "Klavye kısayolları", true, Some("CmdOrCtrl+Slash"))?;
        let perf = item(app, "perf", "Hız ölçümleri", true, None)?;
        let update = item(app, "update", "Güncellemeleri denetle…", true, None)?;
        let s1 = sep()?;
        #[cfg(not(target_os = "macos"))]
        {
            let about = item(app, "about", "Kontrol Merkezi hakkında", true, None)?;
            let s2 = sep()?;
            menu.append(&submenu(app, "&Yardım", &[&keys, &perf, &s1, &update, &s2, &about])?)?;
        }
        #[cfg(target_os = "macos")]
        menu.append(&submenu(app, "Yardım", &[&keys, &perf, &s1, &update])?)?;
    }

    Ok(menu)
}

/// Arayüz durumu değişti (oturum, sık ekranlar, tema, geri/ileri): menü
/// yeniden kurulur.
#[tauri::command]
pub fn menu_sync(
    app: AppHandle,
    store: tauri::State<'_, MenuStore>,
    state: MenuState,
) -> Result<(), String> {
    let menu = build_menu(&app, &state).map_err(|error| error.to_string())?;
    app.set_menu(menu).map_err(|error| error.to_string())?;
    *store.0.lock().unwrap_or_else(|poison| poison.into_inner()) = state;
    Ok(())
}

/// Menü olayını arayüze iletir: `km:*` menü çubuğu, `ctx:*` bağlam menüsü.
pub fn forward_menu_event<R: Runtime>(app: &AppHandle<R>, id: &str) {
    if !(id.starts_with("km:") || id.starts_with("ctx:")) {
        return;
    }
    if id == "km:quit" {
        app.exit(0);
        return;
    }
    let Some(window) = app.get_webview_window(MAIN) else { return };
    let Ok(literal) = serde_json::to_string(id) else { return };
    let _ = window.eval(format!(
        "window.__kmNative && window.__kmNative.menu({literal})"
    ));
}

// ---------------------------------------------------------------- bağlam menüsü

#[derive(serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ContextItem {
    /// Boşsa ayraç.
    #[serde(default)]
    id: String,
    #[serde(default)]
    label: String,
    #[serde(default = "yes")]
    enabled: bool,
    #[serde(default)]
    separator: bool,
    /// Yalnız GÖSTERİM içindir (bağlam menüsünde kısayol tuşu çalışmaz,
    /// menü çubuğundaki aynı komut çalışır).
    #[serde(default)]
    accelerator: Option<String>,
}

fn yes() -> bool {
    true
}

/// İşletim sisteminin KENDİ bağlam menüsü. Seçilen satır `ctx:<id>` olarak
/// arayüze döner (`forward_menu_event`).
#[tauri::command]
pub fn context_menu(app: AppHandle, window: WebviewWindow, items: Vec<ContextItem>) -> Result<(), String> {
    let menu = Menu::new(&app).map_err(|error| error.to_string())?;
    for entry in items.iter().take(40) {
        if entry.separator || entry.id.is_empty() {
            let separator = PredefinedMenuItem::separator(&app).map_err(|error| error.to_string())?;
            menu.append(&separator).map_err(|error| error.to_string())?;
            continue;
        }
        // Kısayol yalnız etiket olarak gösterilir: gerçek hızlandırıcı
        // bağlamak, menü kapandıktan sonra da tuşu yakalayabilirdi.
        let label = match &entry.accelerator {
            Some(keys) if !keys.is_empty() && !cfg!(target_os = "macos") => {
                format!("{}\t{}", clip(&entry.label, 80), keys)
            }
            _ => clip(&entry.label, 80),
        };
        let row = MenuItem::with_id(&app, format!("ctx:{}", entry.id), label, entry.enabled, None::<&str>)
            .map_err(|error| error.to_string())?;
        menu.append(&row).map_err(|error| error.to_string())?;
    }
    window.popup_menu(&menu).map_err(|error| error.to_string())
}
