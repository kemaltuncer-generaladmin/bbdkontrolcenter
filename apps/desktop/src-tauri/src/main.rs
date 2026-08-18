// Kontrol Merkezi — Tauri 2 kabuğu (ADR 0002).
//
// Kabukta modül adı geçmez (K1'in arayüz tarafındaki karşılığı). Bu dosyanın
// iki işi var: pencereyi açmak ve Python çekirdeğini (sidecar) yönetmek.
// Sidecar'ın ömrü kabuğa bağlıdır: kabuk kapanınca çekirdek de kapanır,
// arkada süreç kalmaz.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]


use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::Duration;

use tauri::Manager;
use tauri_plugin_updater::UpdaterExt;

/// Çekirdeğin dinlediği YEREL adres. Sunucu kipinde kullanılmaz.
/// (config/default.yaml → server.port)
const CORE_ADDR: &str = "127.0.0.1:8787";

/// Yerel kipin taban adresi.
const LOCAL_BASE: &str = "http://127.0.0.1:8787";

/// KM Sunucu'nun adresi (ADR 0026). Kabuk artık ince bir istemcidir ve
/// veriyi buradan alır; herkes aynı veritabanını görür.
const DEFAULT_SERVER_URL: &str = "https://kontrolmerkezi.bbdstore.com.tr";

/// Adresi elle söylemenin yolu — geliştirme, deneme kurulumu ve arıza takibi.
/// `local` yazmak eski davranışa (yerel sidecar) döndürür.
const SERVER_URL_ENV: &str = "KM_SERVER_URL";

/// Kabuğun konuşacağı taban adres.
///
/// TEK YER. İki farklı kararın (istek nereye gidecek, sidecar başlatılacak mı)
/// aynı cevaba dayanması gerekiyor; ayrı ayrı hesaplansaydı biri sunucuya
/// bakarken diğeri yerelde süreç başlatır ve hangisinin cevap verdiği belirsiz
/// kalırdı.
fn server_base() -> String {
    match std::env::var(SERVER_URL_ENV) {
        Ok(value) if value.trim().eq_ignore_ascii_case("local") => LOCAL_BASE.to_string(),
        Ok(value) if !value.trim().is_empty() => value.trim().trim_end_matches('/').to_string(),
        _ => DEFAULT_SERVER_URL.to_string(),
    }
}

/// Yerel çekirdeğe mi bakıyoruz? Sidecar YALNIZCA bu durumda başlatılır.
fn is_local_mode() -> bool {
    let base = server_base();
    base.contains("127.0.0.1") || base.contains("localhost")
}

struct Sidecar(Mutex<Option<Child>>);

/// Kökü elle söylemenin yolu — geliştirmede ve arıza takibinde.
const ROOT_ENV: &str = "KM_ROOT";

/// Bir klasörün çekirdek kaynağını taşıyıp taşımadığı. Kök arayan her yol bu
/// tek ölçüte bakar.
fn is_root(dir: &Path) -> bool {
    dir.join("backend/src/km_core").is_dir()
}

/// Çalıştırılabilir dosyadan yukarı doğru çekirdek kaynağını arar.
///
/// Geliştirmede ikili `target/release` altındadır ve depo kökü birkaç üsttedir.
/// Windows kurulumunda kaynaklar exe'nin YANINA açılır, yani ilk adımda bulunur.
fn root_beside_exe() -> Option<PathBuf> {
    let mut dir: PathBuf = std::env::current_exe().ok()?.parent()?.to_path_buf();
    for _ in 0..8 {
        if is_root(&dir) {
            return Some(dir);
        }
        dir = dir.parent()?.to_path_buf();
    }
    None
}

/// Çekirdek kaynağının kökü.
///
/// Sıra: `KM_ROOT` → exe'nin yanı/üstü → paketin kaynak klasörü. Linux `.deb`
/// kurulumunda exe `/usr/bin` altındadır ve kaynak `/usr/lib/<uygulama>` içine
/// açılır; oraya yalnızca Tauri'nin kendi kaynak yolu ulaşır. Üç adayın da
/// aynı ölçütten geçmesi, hangisinin geldiğinin önemsiz olmasını sağlar.
fn find_root(app: &tauri::AppHandle) -> Option<PathBuf> {
    if let Ok(value) = std::env::var(ROOT_ENV) {
        let dir = PathBuf::from(value);
        if is_root(&dir) {
            return Some(dir);
        }
        eprintln!("[kabuk] {ROOT_ENV} çekirdek kaynağı taşımıyor, yok sayıldı: {}", dir.display());
    }
    if let Some(dir) = root_beside_exe() {
        return Some(dir);
    }
    let resource = app.path().resource_dir().ok()?;
    if is_root(&resource) {
        return Some(resource);
    }
    None
}

/// Çekirdeği çalıştıracak Python.
///
/// Sıra bilinçlidir:
///   1. **gömülü çalışma zamanı** (`runtime/python`) — kurulu uygulamanın tek
///      doğru yanıtı. Kullanıcının makinesinde Python olmayabilir, olan sürüm
///      3.12'den eski olabilir ya da bizim paketlerimiz orada bulunmaz.
///   2. **`.venv`** — geliştirme kurulumu. Depo klasöründen çalışırken bugünkü
///      davranış aynen korunur.
///   3. **sistem Python'u** — son çare. Bulunursa çalışır, bulunmazsa kabuk
///      pencereyi yine açar ve giriş ekranı durumu söyler (K7).
///
/// Platform farkı burada, Rust tarafında biter: Python'un dosya yolu
/// Windows'ta `Scripts\python.exe`, ötekilerde `bin/python`'dur.
fn python_path(root: &Path) -> PathBuf {
    let embedded = if cfg!(windows) {
        root.join("runtime/python/python.exe")
    } else {
        root.join("runtime/python/bin/python3")
    };
    if embedded.is_file() {
        return embedded;
    }

    let venv = if cfg!(windows) {
        root.join(".venv/Scripts/python.exe")
    } else {
        root.join(".venv/bin/python")
    };
    if venv.is_file() {
        return venv;
    }

    PathBuf::from(if cfg!(windows) { "python" } else { "python3" })
}

/// Çekirdek zaten ayaktaysa ikincisini başlatmayız: geliştirme sırasında elle
/// başlatılmış bir sidecar varken çakışma olmasın.
fn core_is_running() -> bool {
    TcpStream::connect_timeout(
        &CORE_ADDR.parse().expect("geçerli adres"),
        Duration::from_millis(300),
    )
    .is_ok()
}

/// Açılış tanılaması — kabuğun kendi kararlarını yazdığı dosya.
///
/// NEDEN VAR. Bu fonksiyonun aldığı kararlar bugüne kadar `eprintln!` ile
/// yazılıyordu ve PAKETLENMİŞ UYGULAMADA HİÇBİR YERE ÇIKMIYORDU: kabuk
/// `windows_subsystem = "windows"` ile derleniyor, konsol yok. Çekirdek
/// başlamadığında kullanıcı yalnız "Çekirdeğe ulaşılamadı" görüyor ve NEDENİ
/// hiçbir yerde durmuyordu — v0.1.1'de tam olarak bu oldu ve uzaktan teşhis
/// edilemedi.
///
/// Dosya UYGULAMA VERİ DİZİNİNE yazılır, kurulum klasörüne değil: `Program
/// Files` altı salt okunurdur ve asıl arıza anında ikinci bir arıza üretirdi.
/// Kök bulunamadığında da yazılabilmesi gerekiyor — bu yüzden `find_root`a
/// bağlı DEĞİLDİR.
fn diag_dir(app: &tauri::AppHandle) -> Option<PathBuf> {
    let dir = app.path().app_data_dir().ok()?;
    std::fs::create_dir_all(&dir).ok()?;
    Some(dir)
}

fn diag(app: &tauri::AppHandle, satir: &str) {
    eprintln!("[kabuk] {satir}");
    let Some(dir) = diag_dir(app) else { return };
    let saniye = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    // Yazamamak sessizce geçilir: tanılama tutamamak, uygulamayı açmamak için
    // yeterli bir sebep değildir.
    if let Ok(mut dosya) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(dir.join("kabuk-acilis.log"))
    {
        use std::io::Write;
        let _ = writeln!(dosya, "{saniye} {satir}");
    }
}

fn spawn_core(app: &tauri::AppHandle) -> Option<Child> {
    if core_is_running() {
        diag(app, "çekirdek zaten çalışıyor, yenisi başlatılmadı");
        return None;
    }

    let Some(root) = find_root(app) else {
        // ADAYLARI DA YAZ: "bulunamadı" tek başına hangi yola bakıldığını
        // söylemiyor ve teşhis yine tahmine kalıyor.
        let exe = std::env::current_exe()
            .map(|p| p.display().to_string())
            .unwrap_or_else(|_| "?".into());
        let kaynak = app
            .path()
            .resource_dir()
            .map(|p| p.display().to_string())
            .unwrap_or_else(|_| "?".into());
        diag(app, "ÇEKİRDEK KAYNAĞI BULUNAMADI — aranan ölçüt: backend/src/km_core");
        diag(app, &format!("  exe        : {exe}"));
        diag(app, &format!("  kaynak dizini: {kaynak}"));
        return None;
    };
    diag(app, &format!("kök bulundu: {}", root.display()));

    let python = python_path(&root);
    diag(app, &format!(
        "python: {} (var mı: {})",
        python.display(),
        python.is_file()
    ));

    let mut command = Command::new(&python);
    command
        .arg("-m")
        .arg("km_core.main")
        .current_dir(&root)
        .env("PYTHONPATH", root.join("backend/src"))
        .env("PYTHONUNBUFFERED", "1");

    // ÇEKİRDEĞİN ÇIKTISI TUTULUR. Python açılışta patlarsa (eksik DLL, bozuk
    // gömülü kurulum, import hatası) izi yalnız burada kalır: kendi log
    // dosyasını `setup_logging`ten SONRA açıyor, yani ondan önceki her hata
    // bugüne kadar tümüyle kayboluyordu.
    if let Some(dir) = diag_dir(app) {
        if let Ok(cikti) = std::fs::File::create(dir.join("cekirdek-cikti.log")) {
            if let Ok(hata) = cikti.try_clone() {
                command.stdout(cikti).stderr(hata);
            }
        }
    }

    // Windows'ta kabuk konsolsuz derleniyor (`windows_subsystem = "windows"`).
    // Bayrak konmazsa çekirdek süreci KENDİ konsol penceresini açar: kullanıcı
    // uygulamanın yanında siyah bir pencere görür ve onu kapatınca çekirdeği
    // öldürür.
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }

    match command.spawn() {
        Ok(child) => {
            diag(app, &format!("çekirdek başlatıldı: {}", python.display()));
            Some(child)
        }
        Err(error) => {
            // Çekirdek açılmazsa pencere yine açılır ve giriş ekranı durumu
            // söyler; kabuk sessizce ölmez.
            diag(app, &format!(
                "ÇEKİRDEK BAŞLATILAMADI ({}): {error}",
                python.display()
            ));
            None
        }
    }
}

/// Çekirdeğe giden isteğin sonucu.
#[derive(serde::Serialize)]
struct CoreResponse {
    status: u16,
    body: String,
}

/// Arayüzün çekirdeğe konuşma yolu.
///
/// NEDEN DOĞRUDAN `fetch` DEĞİL: WebKit, sayfayı `tauri://` şemasıyla güvenli
/// bir köken sayıyor ve oradan `http://127.0.0.1`'e giden isteği KARIŞIK
/// İÇERİK sayıp kesiyor ("Load failed"). Chromium loopback'i ayrık tutar,
/// WebKitGTK tutmaz. İsteği kabuk taşıyınca sorun tümüyle ortadan kalkar.
/// Sunucu kipinde ayrıca CSP'yi tek yerden yönetmiş oluyoruz.
///
/// Kabuk burada yalnızca BORUdur: yol, gövde ve belirteç arayüzden gelir;
/// hiçbir modül adı ya da iş kuralı bu dosyada geçmez (K1).
///
/// ELDE YAZILMIŞ HTTP KALDIRILDI. Eski hâli ham TCP üzerinden HTTP/1.1
/// yazıyordu; yerel ve TLS'siz bir istek için yeterliydi ama KM Sunucu
/// `https://` konuşuyor ve TLS elde yazılmaz. `reqwest` zaten bağımlılık
/// ağacındaydı (`tauri-plugin-updater` onu getiriyor).
#[tauri::command]
async fn core_request(
    method: String,
    path: String,
    body: Option<String>,
    token: Option<String>,
) -> Result<CoreResponse, String> {
    let base = server_base();
    let url = format!("{base}{path}");
    let verb = reqwest::Method::from_bytes(method.as_bytes())
        .map_err(|_| format!("geçersiz istek yöntemi: {method}"))?;

    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(60))
        .build()
        .map_err(|error| format!("istemci kurulamadı: {error}"))?;

    let mut request = client.request(verb, &url).header("Accept", "application/json");
    if let Some(value) = token {
        request = request.header("Authorization", format!("Bearer {value}"));
    }
    if let Some(payload) = body.filter(|text| !text.is_empty()) {
        request = request.header("Content-Type", "application/json").body(payload);
    }

    let response = request
        .send()
        .await
        .map_err(|error| format!("çekirdeğe bağlanılamadı: {error}"))?;
    let status = response.status().as_u16();
    let text = response
        .text()
        .await
        .map_err(|error| format!("yanıt okunamadı: {error}"))?;

    Ok(CoreResponse { status, body: text })
}

/// Kabuğun hangi adrese baktığı — arayüz bunu Sistem Sağlığı ekranında yazar.
#[tauri::command]
fn server_info() -> ServerInfo {
    ServerInfo { base: server_base(), local: is_local_mode() }
}

#[derive(serde::Serialize)]
struct ServerInfo {
    base: String,
    local: bool,
}

// --------------------------------------------------------------- güncelleme
//
// GÜNCELLEYİCİ KABUKTADIR, ÇEKİRDEKTE DEĞİL. Yerine konacak dosya kabuğun
// kendi ikilisi (ve yanındaki kaynaklar); Python sidecar kendi altındaki
// zemini değiştiremez. Bu yüzden denetleme/indirme/kurma üç komut olarak
// burada durur, arayüz de bunlara `invoke` ile ulaşır — `core_request` ile
// aynı desen.
//
// ÜÇ ADIM DA KULLANICININ ELİNDE. `download_and_install` tek çağrıda her şeyi
// yapardı ve buradaki durum taşımaya gerek kalmazdı; ama o zaman uygulama
// kasada sipariş girilirken kendi kendine kapanabilirdi. Bu yüzden denetleme
// indirmeyi, indirme de kurulumu KENDİLİĞİNDEN başlatmaz; her adımı kullanıcı
// başlatır ve adımlar arasında taşınan şey aşağıdaki durumdur.

/// Adımlar arasında taşınan durum: bulunan güncelleme ve inen paket.
#[derive(Default)]
struct UpdateFlow {
    /// `update_check`in bulduğu güncelleme. İndirme ve kurulum bunu kullanır.
    found: Mutex<Option<tauri_plugin_updater::Update>>,
    /// İnmiş paket. Kurulum ayrı bir düğme olduğu için bellekte bekler.
    package: Mutex<Option<Vec<u8>>>,
    /// İndirme ilerlemesi — arayüz `update_progress` ile yoklar.
    progress: Mutex<Progress>,
}

/// Zehirlenmiş kilit yüzünden kabuk düşmez.
///
/// İndirme görevi panikleyip kilidi zehirlerse `unwrap()` çağıran her yer de
/// panikler ve pencere kapanırdı. Güncelleme ekranının bozulması, uygulamanın
/// kapanmasından kat kat ucuzdur (K7'nin kabuk tarafındaki karşılığı).
fn lock<T>(cell: &Mutex<T>) -> MutexGuard<'_, T> {
    cell.lock().unwrap_or_else(|poison| poison.into_inner())
}

/// İndirme ilerlemesi. `state`: `idle` · `running` · `done` · `error`.
#[derive(Clone, serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct Progress {
    state: String,
    downloaded: u64,
    /// Sunucu `Content-Length` vermezse boş kalır: ekran o zaman yüzde değil
    /// inen miktarı gösterir. Uydurulmuş bir yüzde, hiç yüzde olmamasından
    /// kötüdür.
    total: Option<u64>,
    error: Option<String>,
    /// İnen (ya da inmekte olan) paketin sürümü.
    ///
    /// DURUM BURADA DURUYOR ki ekran onu unutabilsin: kullanıcı sekmeden çıkıp
    /// geri geldiğinde panel sıfırdan kurulur ama indirme kabukta sürmüştür.
    /// Bu alan olmasaydı ekran "Sürüm ___ indirildi" diye boşluklu bir cümle
    /// yazardı.
    version: Option<String>,
}

impl Default for Progress {
    fn default() -> Self {
        Self {
            state: "idle".into(),
            downloaded: 0,
            total: None,
            error: None,
            version: None,
        }
    }
}

/// Güncelleyicinin bu kurulumda çalışıp çalışmadığı.
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct UpdateSupport {
    /// Denetlemeye girişilebilir mi (kaynak tanımlı ve mimari destekli mi).
    ready: bool,
    /// `tauri dev` / `cargo run` ile çalışıyoruz: paket yerine derleme
    /// klasörü var, kurulum yapılmaz.
    dev: bool,
    /// Kabuğun sürümü — güncelleyicinin karşılaştırdığı sayı budur.
    current_version: String,
    /// `ready` yanlışsa nedeni; doğruysa boş.
    reason: String,
}

/// Denetleme sonucu.
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct UpdateStatus {
    available: bool,
    current_version: String,
    version: Option<String>,
    /// Yayın notu (`latest.json` → `notes`). Yoksa boş.
    notes: Option<String>,
    /// Yayın tarihi (`YYYY-AA-GG`). Yoksa boş.
    date: Option<String>,
}

/// Güncelleyici hatalarını kullanıcının okuyabileceği cümleye çevirir.
///
/// Ham `Display` metni İngilizce ve teknik: "None of the fallback platforms
/// were found" cümlesi, kullanıcıya ".deb ile kurdunuz, o biçim kendini
/// güncellemiyor" demez. En sık karşılaşılacak üç hâl çevrilir; gerisi
/// olduğu gibi geçer — uydurma bir açıklama, teknik metinden kötüdür.
fn explain(error: tauri_plugin_updater::Error) -> String {
    use tauri_plugin_updater::Error;

    // `latest.json` her biçim için ayrı paket taşır; aranan anahtar
    // `{işletim sistemi}-{mimari}-{kurulum biçimi}` düzenindedir.
    let no_package = |targets: String| {
        format!(
            "Yayında bu kurulum biçimi için paket yok ({targets}). Kendi kendini \
             güncelleyen biçimler: Windows kurucusu, macOS uygulaması ve Linux \
             AppImage; .deb ile kurulmuş uygulama depodan yenilenir."
        )
    };

    match error {
        Error::EmptyEndpoints => "Bu yapıda güncelleme kaynağı tanımlı değil \
             (tauri.conf.json → plugins.updater.endpoints)."
            .into(),
        Error::TargetNotFound(target) => no_package(target),
        Error::TargetsNotFound(targets) => no_package(targets.join(", ")),
        Error::ReleaseNotFound => "Yayın bilgisi okunamadı: adres bir sürüm listesi \
             döndürmedi. Henüz hiç sürüm yayınlanmamış olabilir."
            .into(),
        other => format!("Güncelleme başarısız: {other}"),
    }
}

#[tauri::command]
fn update_support(app: tauri::AppHandle) -> UpdateSupport {
    let current_version = app.package_info().version.to_string();
    match app.updater() {
        Ok(_) => UpdateSupport {
            ready: true,
            dev: tauri::is_dev(),
            current_version,
            reason: String::new(),
        },
        Err(error) => UpdateSupport {
            ready: false,
            dev: tauri::is_dev(),
            current_version,
            reason: explain(error),
        },
    }
}

/// 1. adım — DENETLE. İndirmeyi başlatmaz, yalnız sorar.
#[tauri::command]
async fn update_check(
    app: tauri::AppHandle,
    state: tauri::State<'_, Arc<UpdateFlow>>,
) -> Result<UpdateStatus, String> {
    let flow = state.inner().clone();
    let current_version = app.package_info().version.to_string();

    // İNDİRME SÜRERKEN DENETLENMEZ. Yeni denetim `found`u değiştirir; arkada
    // süren indirme bittiğinde paket BAŞKA bir sürümün paketiyken "hazır"
    // görünür ve kurulum onu kurardı. Arayüz düğmeyi zaten kapatıyor — ama
    // kural burada da durmalı: kabuk kendi tutarlılığını arayüze emanet etmez.
    if lock(&flow.progress).state == "running" {
        return Err("İndirme sürerken yeni denetim yapılmaz.".into());
    }

    let found = app.updater().map_err(explain)?.check().await.map_err(explain)?;

    // Yeni denetim, eski indirmeyi geçersiz kılar: elde bekleyen paket bir
    // önceki sürümün paketi olabilir.
    *lock(&flow.package) = None;
    *lock(&flow.progress) = Progress::default();

    let status = match &found {
        Some(update) => UpdateStatus {
            available: true,
            current_version,
            version: Some(update.version.clone()),
            notes: update.body.clone().filter(|text| !text.trim().is_empty()),
            date: update.date.map(|stamp| stamp.date().to_string()),
        },
        None => UpdateStatus {
            available: false,
            current_version,
            version: None,
            notes: None,
            date: None,
        },
    };
    *lock(&flow.found) = found;
    Ok(status)
}

/// 2. adım — İNDİR. Kurmaz; paket bellekte bekler.
///
/// HEMEN DÖNER, indirmeyi arka plana bırakır: 100 MB'lık bir paketi tek bir
/// `invoke` çağrısının içinde beklemek, ilerlemeyi gösterecek hiçbir aralık
/// bırakmazdı. Arayüz `update_progress` ile yoklar (kabukta olay dinlemek
/// için eklenti izni gerekirdi; yoklama aynı işi ek yüzey açmadan görüyor).
#[tauri::command]
fn update_download(state: tauri::State<'_, Arc<UpdateFlow>>) -> Result<Progress, String> {
    let flow = state.inner().clone();
    let Some(update) = lock(&flow.found).clone() else {
        return Err("Önce güncelleme denetlenmeli.".into());
    };

    {
        let mut progress = lock(&flow.progress);
        // İKİNCİ TIK İKİNCİ İNDİRME BAŞLATMAZ: aynı paketi iki kez indirmek
        // ilerlemeyi de bozardı (iki görev aynı sayacı artırırdı).
        if progress.state == "running" {
            return Ok(progress.clone());
        }
        *progress = Progress {
            state: "running".into(),
            version: Some(update.version.clone()),
            ..Progress::default()
        };
    }
    *lock(&flow.package) = None;

    let task = flow.clone();
    tauri::async_runtime::spawn(async move {
        let outcome = update
            .download(
                |chunk, total| {
                    let mut progress = lock(&task.progress);
                    progress.downloaded += chunk as u64;
                    progress.total = total;
                },
                || {},
            )
            .await;

        match outcome {
            Ok(bytes) => {
                // İMZA BURADA DOĞRULANDI. `download` paketi indirdikten sonra
                // `pubkey` ile imzayı denetler; doğrulanmamış bayt buraya hiç
                // ulaşmaz.
                *lock(&task.package) = Some(bytes);
                let mut progress = lock(&task.progress);
                let downloaded = progress.downloaded;
                if progress.total.is_none() {
                    progress.total = Some(downloaded);
                }
                progress.state = "done".into();
            }
            Err(error) => {
                let mut progress = lock(&task.progress);
                progress.state = "error".into();
                progress.error = Some(explain(error));
            }
        }
    });

    // Kilit AYRI SATIRDA bırakılır: son ifadenin içinde tutulsaydı geçici
    // `MutexGuard`, `flow` düştükten sonra bırakılacağı için ödünç denetimi
    // reddederdi.
    let started = lock(&flow.progress).clone();
    Ok(started)
}

/// İndirme durumu — arayüz bunu yoklar.
#[tauri::command]
fn update_progress(state: tauri::State<'_, Arc<UpdateFlow>>) -> Progress {
    lock(&state.progress).clone()
}

/// 3. adım — KUR VE YENİDEN BAŞLAT. Yalnız kullanıcı bastığında çalışır.
#[tauri::command]
fn update_install(
    app: tauri::AppHandle,
    state: tauri::State<'_, Arc<UpdateFlow>>,
) -> Result<(), String> {
    if tauri::is_dev() {
        return Err("Geliştirme kipinde kurulum yapılmaz: ortada paket değil, \
                    derleme klasörü var."
            .into());
    }

    let Some(update) = lock(&state.found).clone() else {
        return Err("Önce güncelleme denetlenmeli.".into());
    };

    // PAKET TÜKETİLMEDEN KURULUR: `take()` ile alınsaydı kurulum patladığında
    // inen 100 MB da kaybolur, kullanıcı baştan indirmek zorunda kalırdı.
    let package = lock(&state.package);
    let Some(bytes) = package.as_ref() else {
        return Err("Önce paket indirilmeli.".into());
    };
    update.install(bytes.as_slice()).map_err(explain)?;
    drop(package);

    // WINDOWS'TA BURAYA HİÇ GELİNMEZ: `install` kurucuyu başlatır ve süreci
    // `exit(0)` ile kapatır; uygulamayı kurucunun kendisi geri açar. Linux ve
    // macOS'ta paket yerine konur, yeniden başlatmak bize kalır.
    app.restart();
}

/// Kurulum tamamlandıktan sonra uygulamayı yeniden başlatır.
///
/// NEDEN GEREKLİ. Modüller çekirdek açılırken yüklenir; cihaz eşlemesiyle gelen
/// geçit ayarları (BLD/kantin/mağaza adresleri) ise eşleme ANINDA iner. Yani
/// ilk eşlemede modüller o ayarları göremeden yüklenmiş olur: kantin geçidi
/// "adres yok" diyerek hiç yüklenmez, BLD geçidi boş adresle ve salt okunur
/// açılır. Ölçüldü — yeniden başlatınca üçü de doğru bağlanıyor ve 49/49 modül
/// sorunsuz yükleniyor.
///
/// KULLANICIYA BIRAKILMAZ. "Uygulamayı yeniden başlatın" yazan bir cümle,
/// kurulumu yapan kişinin okumasına bağlı kalırdı; okumazsa ekranlar boş gelir
/// ve sebebi hiçbir yerde görünmez. Eşleme zaten girişten önce, kimse çalışmaya
/// başlamadan yapılıyor — kaybolacak iş yok.
#[tauri::command]
fn restart_app(app: tauri::AppHandle) {
    app.restart();
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(Sidecar(Mutex::new(None)))
        .manage(Arc::new(UpdateFlow::default()))
        .setup(|app| {
            // Çekirdek BURADA başlatılır, `main`in başında değil: kurulu
            // uygulamada kaynak klasörünün yerini yalnız Tauri bilir
            // (`resource_dir`) ve o yol ancak uygulama kurulduktan sonra
            // sorulabilir.
            // SİDECAR YALNIZ YEREL KİPTE BAŞLAR (ADR 0026). Sunucu kipinde
            // veri merkezdedir; burada ikinci bir çekirdek açmak, kullanıcının
            // BOŞ bir yerel veritabanına bakmasına ve "verilerim gitti"
            // sanmasına yol açardı.
            if is_local_mode() {
                let child = spawn_core(app.handle());
                if let Ok(mut guard) = app.state::<Sidecar>().0.lock() {
                    *guard = child;
                }
            } else {
                diag(app.handle(), &format!("sunucu kipi: {}", server_base()));
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            core_request,
            server_info,
            update_support,
            update_check,
            update_download,
            update_progress,
            update_install,
            restart_app,
        ])
        .build(tauri::generate_context!())
        .expect("Kontrol Merkezi kabuğu başlatılamadı")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                // Kabuk kapanıyor: çekirdeği de indir.
                let state: tauri::State<Sidecar> = app.state();
                let taken = state.0.lock().ok().and_then(|mut guard| guard.take());
                if let Some(mut child) = taken {
                    let _ = child.kill();
                    let _ = child.wait();
                    eprintln!("[kabuk] çekirdek kapatıldı");
                }
            }
        });
}
