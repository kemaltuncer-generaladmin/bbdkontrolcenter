//! Yerel yazdırma — baskı KULLANICININ MAKİNESİNDE yapılır.
//!
//! NEDEN KABUKTA. Çekirdek sunucuda koşuyor (ADR 0026) ve sunucu imajı CUPS'ı
//! bilerek kurmuyor ("sunucuda yazıcı ve hoparlör yok" — `deploy/server/Dockerfile`).
//! Yani baskı isteği sunucuya gittiğinde `lp` bulunamıyor ve iş hiç çıkmıyordu.
//! Yazıcılar ise kullanıcının makinesinde kurulu — tarayıcının hepsini görmesinin
//! sebebi de bu. Kâğıdın çıkacağı yer neresiyse, komutun koştuğu yer de orası
//! olmalı.
//!
//! SESSİZ BASAR, DİYALOG AÇMAZ. Kullanıcı "Yazdır"a bastığında yazıcı seçme
//! penceresi çıkmaz; hedef bir kez Sistem Ayarları'ndan seçilir. Kullanıcı
//! kararı: "tıklanmadan kendi kendine yazdırma olmasın, ama kargoya ver deyince
//! yazdırsın". Yani baskıyı hep bir kullanıcı eylemi başlatır, ama o eylemden
//! sonra soru sorulmaz.
//!
//! SEÇİLEN YAZICI CİHAZDA KALIR. Merkezî ayara yazılmaz: aynı kurulumu kullanan
//! iki makinenin yazıcısı farklıdır ve merkezde tutulan tek bir ad, birinin
//! çıktısını ötekinin odasına düşürürdü.

use std::path::{Path, PathBuf};
use std::process::Command;

use serde::{Deserialize, Serialize};
use tauri::Manager;

/// Seçilen yazıcının saklandığı dosya (uygulama veri dizini).
const PREF_FILE: &str = "yazici.json";

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct PrinterPref {
    /// CUPS kuyruğu ya da Windows yazıcı adı. Boş = seçilmedi.
    #[serde(default)]
    pub name: String,
}

#[derive(Debug, Serialize)]
pub struct PrinterList {
    /// Bulunan yazıcı adları.
    pub printers: Vec<String>,
    /// İşletim sisteminin varsayılanı (varsa) — ilk kurulumda öneri olur.
    pub system_default: String,
    /// Kullanıcının bu cihazda seçtiği yazıcı.
    pub selected: String,
    /// Liste alınamadıysa sebebi. Boş liste ile hata AYRI şeylerdir:
    /// birincisi "yazıcı kurulu değil", ikincisi "sorulamadı".
    pub error: String,
}

fn pref_path(app: &tauri::AppHandle) -> Option<PathBuf> {
    let dir = app.path().app_data_dir().ok()?;
    std::fs::create_dir_all(&dir).ok()?;
    Some(dir.join(PREF_FILE))
}

fn read_pref(app: &tauri::AppHandle) -> PrinterPref {
    let Some(path) = pref_path(app) else { return PrinterPref::default() };
    let Ok(text) = std::fs::read_to_string(path) else { return PrinterPref::default() };
    serde_json::from_str(&text).unwrap_or_default()
}

/// Komutu çalıştırır ve stdout'u döndürür. Hata metni ÇAĞIRANA taşınır:
/// "yazıcı yok" ile "komut bulunamadı" farklı sorunlardır.
fn run(program: &str, args: &[&str]) -> Result<String, String> {
    let out = Command::new(program)
        .args(args)
        .output()
        .map_err(|e| format!("{program} çalıştırılamadı: {e}"))?;
    if !out.status.success() {
        let err = String::from_utf8_lossy(&out.stderr).trim().to_string();
        return Err(if err.is_empty() {
            format!("{program} başarısız (çıkış {:?})", out.status.code())
        } else {
            err
        });
    }
    Ok(String::from_utf8_lossy(&out.stdout).to_string())
}

#[cfg(windows)]
fn discover() -> (Vec<String>, String, String) {
    // `Get-Printer` Windows 8'den beri var ve AĞ yazıcılarını da listeler —
    // kullanıcının yazıcılarının bir kısmı kablosuz.
    let script = "chcp 65001 > $null; \
        (Get-Printer | Select-Object -ExpandProperty Name) -join \"`n\"; \
        '---'; \
        (Get-CimInstance Win32_Printer | Where-Object Default -eq $true \
         | Select-Object -ExpandProperty Name)";
    match run("powershell", &["-NoProfile", "-NonInteractive", "-Command", script]) {
        Ok(text) => {
            let mut parts = text.split("---");
            let names = parts.next().unwrap_or("");
            let default = parts.next().unwrap_or("").trim().to_string();
            (clean(names), default, String::new())
        }
        Err(err) => (Vec::new(), String::new(), err),
    }
}

#[cfg(not(windows))]
fn discover() -> (Vec<String>, String, String) {
    // `lpstat -e` kuyruk adlarını tek sütun verir; `-a` çıktısı yerelleştirilmiş
    // cümleler taşıyor ve ayrıştırması kırılgan.
    let names = match run("lpstat", &["-e"]) {
        Ok(text) => clean(&text),
        Err(err) => return (Vec::new(), String::new(), err),
    };
    // Varsayılan yoksa hata değildir: kullanıcı zaten seçecek.
    let default = run("lpstat", &["-d"])
        .ok()
        .and_then(|t| t.split(':').nth(1).map(|s| s.trim().to_string()))
        .unwrap_or_default();
    (names, default, String::new())
}

fn clean(text: &str) -> Vec<String> {
    text.lines()
        .map(|line| line.trim().to_string())
        .filter(|line| !line.is_empty())
        .collect()
}

/// Bu cihazda kurulu yazıcılar + seçili olan.
#[tauri::command]
pub fn printers(app: tauri::AppHandle) -> PrinterList {
    let (printers, system_default, error) = discover();
    PrinterList { printers, system_default, selected: read_pref(&app).name, error }
}

/// Yazıcıyı bu CİHAZ için seçer. Boş ad seçimi kaldırır.
#[tauri::command]
pub fn printer_select(app: tauri::AppHandle, name: String) -> Result<PrinterPref, String> {
    let pref = PrinterPref { name: name.trim().to_string() };
    let path = pref_path(&app).ok_or("Uygulama veri dizini bulunamadı.")?;
    let text = serde_json::to_string_pretty(&pref).map_err(|e| e.to_string())?;
    std::fs::write(&path, text).map_err(|e| format!("Yazıcı tercihi yazılamadı: {e}"))?;
    Ok(pref)
}

#[cfg(windows)]
fn spool(file: &Path, printer: &str, helper: Option<PathBuf>) -> Result<(), String> {
    // WINDOWS'TA PDF BASMANIN HAZIR BİR KOMUTU YOKTUR.
    //
    // `Start-Process -Verb PrintTo` kayıtlı PDF işleyicisine bağlıdır ve
    // Windows 10/11'in varsayılanı olan Edge bu fiili DESTEKLEMEZ: çoğu
    // makinede sessizce hiçbir şey basılmazdı. Bu yüzden kuruluma küçük bir
    // yardımcı (SumatraPDF) konur ve baskı ondan geçer — kullanıcı kararı.
    let exe = helper.ok_or(
        "Windows yazdırma yardımcısı (SumatraPDF) kurulumda bulunamadı. \
         Uygulamayı yeniden kurmak gerekiyor.",
    )?;
    let out = Command::new(&exe)
        .args([
            "-print-to",
            printer,
            "-silent",
            "-exit-when-done",
            &file.to_string_lossy(),
        ])
        .output()
        .map_err(|e| format!("Yazdırma yardımcısı çalıştırılamadı: {e}"))?;
    if !out.status.success() {
        return Err(format!(
            "Yazdırma yardımcısı başarısız (çıkış {:?}). Yazıcı adı doğru mu?",
            out.status.code()
        ));
    }
    Ok(())
}

#[cfg(not(windows))]
fn spool(file: &Path, printer: &str, _helper: Option<PathBuf>) -> Result<(), String> {
    run("lp", &["-d", printer, &file.to_string_lossy()]).map(|_| ())
}

/// Windows yardımcısının kurulumdaki yolu. Diğer sistemlerde `None`.
#[allow(unused_variables)]
fn helper_path(app: &tauri::AppHandle) -> Option<PathBuf> {
    if !cfg!(windows) {
        return None;
    }
    let path = app
        .path()
        .resolve("yazdirma/SumatraPDF.exe", tauri::path::BaseDirectory::Resource)
        .ok()?;
    path.is_file().then_some(path)
}

/// PDF'i seçili yazıcıya basar. `data` base64'tür.
///
/// BAYTLAR GÖVDEYLE GELİR, yol ile değil: PDF sunucunun diskinde üretiliyor ve
/// kullanıcının makinesinde öyle bir dosya yok. Kabuk onu geçici bir dosyaya
/// yazıp yerel kuyruğa verir ve İŞİ BİTİNCE SİLER — rapor kişisel veri taşıyor.
#[tauri::command]
pub fn printer_print(
    app: tauri::AppHandle,
    data: String,
    name: String,
    copies: Option<u32>,
) -> Result<String, String> {
    use base64::Engine as _;

    let printer = {
        let chosen = read_pref(&app).name;
        if chosen.is_empty() {
            return Err(
                "Bu cihazda yazıcı seçilmemiş. Sistem Ayarları → Yazıcı bölümünden seçin."
                    .into(),
            );
        }
        chosen
    };

    let bytes = base64::engine::general_purpose::STANDARD
        .decode(data.as_bytes())
        .map_err(|e| format!("Belge çözülemedi: {e}"))?;
    if bytes.is_empty() {
        return Err("Belge boş geldi; yazdırma denenmedi.".into());
    }

    let safe = name
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || *c == '.' || *c == '-' || *c == '_')
        .collect::<String>();
    let stem = if safe.is_empty() { "belge.pdf".to_string() } else { safe };
    let file = std::env::temp_dir().join(format!("km-{}-{}", std::process::id(), stem));
    std::fs::write(&file, &bytes).map_err(|e| format!("Geçici dosya yazılamadı: {e}"))?;

    let helper = helper_path(&app);
    let mut sonuc = Ok(());
    for _ in 0..copies.unwrap_or(1).clamp(1, 20) {
        sonuc = spool(&file, &printer, helper.clone());
        if sonuc.is_err() {
            break;
        }
    }

    // KİŞİSEL VERİ GEÇİCİ KLASÖRDE BIRAKILMAZ. Silinememesi baskıyı
    // başarısız saymaz; iş zaten kuyruğa girmiştir.
    let _ = std::fs::remove_file(&file);
    sonuc.map(|_| printer)
}
