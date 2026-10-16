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
//!
//! SEÇİM YAPILMADIYSA SİSTEMİN VARSAYILANI KULLANILIR. Önceden seçim şarttı ve
//! ayar ekranına uğramamış her kurulumda bütün "Yazdır" düğmeleri kapalı
//! duruyordu; işletim sisteminde çalışan bir varsayılan yazıcı varken uygulama
//! "yazıcı seçilmemiş" diyordu. Kullanıcının beklentisi bunun tersidir:
//! ayarlardan seçilmişse ORAYA basar, seçilmemişse sistemin bastığı yere basar.
//! Hiç yazıcı yoksa ancak o zaman durur — ve sebebini söyler.

use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use tauri::Manager;

/// Seçilen yazıcının saklandığı dosya (uygulama veri dizini).
const PREF_FILE: &str = "yazici.json";


/// AYNI BELGE BU SÜRE İÇİNDE İKİNCİ KEZ KUYRUĞA VERİLMEZ.
///
/// Çekirdekteki kapının (`km_platform/printer/cups.py`) aynısı. Yerel baskı o
/// kapıdan HİÇ geçmiyor — baytlar doğrudan kabuğa gelip `lp`ye veriliyor — bu
/// yüzden koruma burada ayrıca durmak zorunda; yoksa "bir tıklama bir baskı"
/// sözü yalnız sunucu yolunda geçerli olurdu.
const DUPLICATE_WINDOW: Duration = Duration::from_secs(12);

/// Bir baskı işinin kuyrukta yaşayabileceği en uzun süre (saniye).
/// Gerekçe `spool` içinde: `ErrorPolicy=retry-job` olan bir kuyrukta
/// hata alan iş sonsuza dek yeniden denenir.
const JOB_LIFETIME_SECONDS: u32 = 180;

/// Son gönderilen iş: (yazıcı, belge özeti) ve gönderim anı.
static SON_BASKI: Mutex<Option<(String, u64, Instant)>> = Mutex::new(None);

/// Belge içeriğinin ucuz özeti (FNV-1a). Şifreleme değil, AYIRT ETME içindir:
/// aynı raporun ikinci kez gönderilmesiyle başka bir raporun gönderilmesini
/// ayırmaya yeter.
fn ozet(bytes: &[u8]) -> u64 {
    let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
    for byte in bytes {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
    }
    hash
}

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
    /// Kullanıcının bu cihazda seçtiği yazıcı. Boş = seçim yapılmadı.
    pub selected: String,
    /// Baskının GERÇEKTEN gideceği kuyruk: seçim varsa o, yoksa sistemin
    /// varsayılanı. `selected` ile ayrı tutulur çünkü ayar ekranı seçimi
    /// listede işaretlemek için `selected`e, "nereye basacak" cümlesini
    /// kurmak için buna bakar. Boşsa basılabilecek yazıcı yoktur.
    pub effective: String,
    /// `effective` boşsa nedeni — kullanıcıya gösterilecek cümle.
    pub blocked: String,
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

/// Baskının gideceği kuyruğu SEÇER — tek karar yeri.
///
/// Sıra: ayardan seçilen → sistemin varsayılanı → hata. `printers` komutu da,
/// `printer_print` de buradan geçer; ayar ekranının "şuraya basacak" dediği ile
/// kâğıdın çıktığı yer böylece aynı olur. İki ayrı yerde yazıldığında biri
/// varsayılana düşerken öteki düşmüyordu ve ekran yanlış söylüyordu.
fn resolve(chosen: &str, installed: &[String], system_default: &str, error: &str)
    -> Result<String, String>
{
    let known = |name: &str| installed.iter().any(|entry| entry == name);

    // LİSTE VARSA SEÇİM DOĞRULANIR. Silinmiş/adı değişmiş bir yazıcıya
    // gönderilen iş CUPS'tan ham hata metniyle döner ("The printer or class
    // does not exist"); kullanıcı bunu ayarındaki adla ilişkilendiremez.
    if !installed.is_empty() {
        if !chosen.is_empty() && known(chosen) {
            return Ok(chosen.to_string());
        }
        if !system_default.is_empty() && known(system_default) {
            return Ok(system_default.to_string());
        }
        let kurulu = installed.join(", ");
        return Err(if chosen.is_empty() {
            format!(
                "Bu cihazda yazıcı seçilmemiş ve işletim sisteminin varsayılan \
                 yazıcısı yok. Sistem Ayarları → “Bu cihazın yazıcısı” bölümünden \
                 seçin. Kurulu yazıcılar: {kurulu}."
            )
        } else {
            format!(
                "Ayardaki “{chosen}” yazıcısı bu cihazda artık kurulu değil ve \
                 işletim sisteminin varsayılan yazıcısı yok. Sistem Ayarları → \
                 “Bu cihazın yazıcısı” bölümünden yeniden seçin. \
                 Kurulu yazıcılar: {kurulu}."
            )
        });
    }

    // LİSTE ALINAMADIYSA SEÇİM DOĞRULANMAZ, YOK SAYILMAZ. "Doğrulayamadım" ile
    // "yanlış" ayrı şeylerdir: `lpstat` bir sebeple susmuşken kullanıcının
    // bilerek yazdığı adı çöpe atmak, çalışan baskıyı durdurmak olurdu.
    if !chosen.is_empty() {
        return Ok(chosen.to_string());
    }
    if !system_default.is_empty() {
        return Ok(system_default.to_string());
    }
    Err(if error.is_empty() {
        "Bu cihazda kurulu yazıcı bulunamadı. Yazıcıyı işletim sisteminin \
         ayarlarından ekleyin; başka programlardan çıktı alabiliyorsanız burada \
         da görünmesi gerekir."
            .to_string()
    } else {
        format!("Yazıcı listesi alınamadı: {error}")
    })
}

/// Bu cihazda kurulu yazıcılar + seçili olan + baskının gideceği kuyruk.
#[tauri::command]
pub fn printers(app: tauri::AppHandle) -> PrinterList {
    let (printers, system_default, error) = discover();
    let selected = read_pref(&app).name;
    let (effective, blocked) = match resolve(&selected, &printers, &system_default, &error) {
        Ok(name) => (name, String::new()),
        Err(reason) => (String::new(), reason),
    };
    PrinterList { printers, system_default, selected, effective, blocked, error }
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
fn spool(file: &Path, printer: &str, helper: Option<PathBuf>, job: &Job) -> Result<(), String> {
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
    // SumatraPDF `-print-settings`: virgülle ayrılmış sayfa aralıkları ve
    // `Nx` kopya sayısı ("1-3,5,2x"). Kopya tek işte basılır.
    let mut settings: Vec<String> = Vec::new();
    if let Some(pages) = &job.pages {
        settings.push(pages.clone());
    }
    if job.copies > 1 {
        settings.push(format!("{}x", job.copies));
    }
    let mut args: Vec<String> = vec!["-print-to".into(), printer.into()];
    if !settings.is_empty() {
        args.push("-print-settings".into());
        args.push(settings.join(","));
    }
    args.extend(["-silent".into(), "-exit-when-done".into(), file.to_string_lossy().into_owned()]);
    let out = Command::new(&exe)
        .args(&args)
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

/// Tek baskı işinin seçenekleri (yazdırma penceresinden gelir).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Job {
    /// 1–20 kopya. Yazıcı harmanlar; iş kuyrukta tek satırdır.
    pub copies: u32,
    /// Doğrulanmış sayfa aralığı ("1-3,5"); `None` = bütün sayfalar.
    pub pages: Option<String>,
}

/// Kullanıcının yazdığı sayfa aralığını doğrular ve sadeleştirir.
///
/// SERBEST METİN KOMUTA GİTMEZ. Aralık `lp`ye ve Windows yardımcısına argüman
/// olarak geçiyor; yalnız rakam, virgül ve tire kabul edilir, her parça
/// `a` ya da `a-b` (1 ≤ a ≤ b ≤ 9999) olmalıdır. Boş metin "bütün sayfalar"dır.
pub fn page_ranges(text: &str) -> Result<Option<String>, String> {
    let compact: String = text.chars().filter(|c| !c.is_whitespace()).collect();
    if compact.is_empty() {
        return Ok(None);
    }
    let invalid = || {
        format!(
            "Sayfa aralığı anlaşılamadı: “{}”. Örnek: 1-3, 5 (boş bırakılırsa bütün sayfalar).",
            text.trim()
        )
    };
    let mut parts: Vec<String> = Vec::new();
    for piece in compact.split(',') {
        if piece.is_empty() {
            continue;
        }
        let mut bounds = piece.splitn(2, '-');
        let start: u32 = bounds.next().unwrap_or("").parse().map_err(|_| invalid())?;
        let end: u32 = match bounds.next() {
            Some(raw) => raw.parse().map_err(|_| invalid())?,
            None => start,
        };
        if start == 0 || end < start || end > 9999 {
            return Err(invalid());
        }
        parts.push(if start == end { start.to_string() } else { format!("{start}-{end}") });
    }
    if parts.is_empty() || parts.len() > 50 {
        return Err(invalid());
    }
    Ok(Some(parts.join(",")))
}

/// Raporların kâğıt boyutu. Çekirdek A4 üretiyor (`platform.printer.media`).
const MEDIA: &str = "A4";

#[cfg(not(windows))]
fn spool(file: &Path, printer: &str, _helper: Option<PathBuf>, job: &Job) -> Result<(), String> {
    // KÂĞIT BOYUTU İKİ ADLA BİRDEN SÖYLENİR — pahalıya mal olmuş bir ders.
    //
    // Söylenmezse kullanıcının `~/.cups/lpoptions` dosyasındaki varsayılan
    // geçerli olur. Bu makinede orada `PageSize=A6` yazıyor: yazıcı A4 belgeyi
    // A6 sanıp HATA VERİYOR. İş CUPS'a sorunsuz giriyor, hatayı cihaz veriyor —
    // yani uygulama "gönderildi" derken yazıcı kırmızı yanıyor. Grafik yazdırma
    // pencereleri o dosyayı okumadığı için normal belgeler düzgün çıkıyor ve
    // suç uygulamada sanılıyor.
    //
    // `media` ile `PageSize` CUPS'ta AYRI iki seçenek: ilki genel IPP adı,
    // ikincisi PPD'nin kendi anahtarı. Yalnız `media` verilince PPD tabanlı
    // sürücü (hpcups) lpoptions'daki `PageSize`i kullanmaya devam edebiliyor.
    // İkisi birden verilir ki hangi yoldan okunursa okunsun aynı boyut çıksın.
    //
    // Kural `km_platform/printer/cups.py` içinde yazılıydı; baskı kabuğa
    // taşınırken buraya GELMEMİŞTİ ve hata aynen geri döndü.
    // İŞİN ÖMRÜ SINIRLIDIR — "bir tıklama bir baskı"nın ikinci yarısı.
    //
    // Uygulama tek iş gönderiyor; tekrarın kaynağı `lp`den SONRASI olabilir.
    // CUPS kuyruğunun `ErrorPolicy` ayarı `retry-job` olduğunda, cihazda hata
    // alan bir iş kuyrukta KALIR ve düzenli aralıklarla yeniden denenir.
    // Kullanıcı bir kez "Yazdır" der, yarım saat sonra gelip aynı raporun
    // defalarca çıktığını görür — uygulamada hiçbir döngü olmadığı için de
    // suç uzun süre uygulamada aranır.
    //
    // `job-cancel-after` işin toplam ömrünü bağlar: bu süre içinde basılamayan
    // iş kuyruktan DÜŞER, sonsuza dek yeniden denenmez. Rapor basımı saniyeler
    // sürer; buradaki süre cömert bir tavandır ve normal baskıya dokunmaz.
    //
    // KOPYA VE SAYFA ARALIĞI TEK İŞTE. Kopya için işi N kez kuyruğa vermek
    // yerine `-n` kullanılır (yazıcı harmanlar, kuyrukta tek satır görünür);
    // aralık `page-ranges` ile CUPS'a söylenir ("1-3,5").
    let mut args: Vec<String> = vec![
        "-d".into(), printer.into(),
        "-o".into(), format!("media={MEDIA}"),
        "-o".into(), format!("PageSize={MEDIA}"),
        "-o".into(), format!("job-cancel-after={JOB_LIFETIME_SECONDS}"),
    ];
    if job.copies > 1 {
        args.push("-n".into());
        args.push(job.copies.to_string());
    }
    if let Some(pages) = &job.pages {
        args.push("-o".into());
        args.push(format!("page-ranges={pages}"));
    }
    args.push(file.to_string_lossy().into_owned());
    let refs: Vec<&str> = args.iter().map(String::as_str).collect();
    run("lp", &refs).map(|_| ())
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

/// PDF'i yazıcıya basar. `data` base64'tür.
///
/// BAYTLAR GÖVDEYLE GELİR, yol ile değil: PDF sunucunun diskinde üretiliyor ve
/// kullanıcının makinesinde öyle bir dosya yok. Kabuk onu geçici bir dosyaya
/// yazıp yerel kuyruğa verir ve İŞİ BİTİNCE SİLER — rapor kişisel veri taşıyor.
///
/// YAZDIRMA PENCERESİ (finansal raporlar, 63-finans-pdf): `printer` doluysa
/// baskı o yazıcıya gider — ama YALNIZ bu cihazda kuruluysa (liste
/// alınabildiyse doğrulanır); boşsa ayardaki seçim / sistemin varsayılanı.
/// `pages` sayfa aralığıdır ("1-3,5"), `copies` 1–20. Pencere açmayan eski
/// çağrılar (yalnız `data`, `name`, `copies`) aynen çalışır.
#[tauri::command]
pub fn printer_print(
    app: tauri::AppHandle,
    data: String,
    name: String,
    copies: Option<u32>,
    printer: Option<String>,
    pages: Option<String>,
) -> Result<String, String> {
    use base64::Engine as _;

    // HEDEF `printers` KOMUTUYLA AYNI KURALDAN ÇIKAR (`resolve`): ekranda
    // "şuraya basacak" yazan ad ile işin gittiği kuyruk ayrı hesaplanırsa
    // kullanıcı ekrana bakarak hatayı çözemez.
    let (installed, system_default, error) = discover();
    let wanted = printer.unwrap_or_default().trim().to_string();
    let printer = if wanted.is_empty() {
        resolve(&read_pref(&app).name, &installed, &system_default, &error)?
    } else if installed.is_empty() || installed.iter().any(|entry| *entry == wanted) {
        wanted
    } else {
        return Err(format!(
            "“{wanted}” yazıcısı bu cihazda kurulu değil. Kurulu yazıcılar: {}.",
            installed.join(", ")
        ));
    };
    let job = Job {
        copies: copies.unwrap_or(1).clamp(1, 20),
        pages: page_ranges(pages.as_deref().unwrap_or(""))?,
    };

    let bytes = base64::engine::general_purpose::STANDARD
        .decode(data.as_bytes())
        .map_err(|e| format!("Belge çözülemedi: {e}"))?;
    if bytes.is_empty() {
        return Err("Belge boş geldi; yazdırma denenmedi.".into());
    }

    // BİR TIKLAMA BİR BASKI. Aynı belge aynı yazıcıya AYNI seçeneklerle
    // birkaç saniye içinde ikinci kez gelirse kuyruğa VERİLMEZ. Kasıtlı ikinci
    // baskı engellenmez: pencere geçtikten sonra ya da `copies` ile alınır;
    // farklı sayfa aralığı ayrı iştir.
    let damga = ozet(&bytes)
        ^ ozet(format!("{}|{}", job.copies, job.pages.clone().unwrap_or_default()).as_bytes());
    {
        let mut son = SON_BASKI.lock().unwrap_or_else(|e| e.into_inner());
        if let Some((onceki, onceki_damga, an)) = son.as_ref() {
            if *onceki == printer
                && *onceki_damga == damga
                && an.elapsed() < DUPLICATE_WINDOW
            {
                return Ok(printer);
            }
        }
        // Damga ŞİMDİ yazılır: `lp` saniyeler sürebiliyor ve ikinci tıklama
        // tam o aralıkta geliyor. Gönderim sonrasına bırakmak, korumanın en
        // çok gerektiği anda kapalı olması demekti.
        *son = Some((printer.clone(), damga, Instant::now()));
    }

    // AD BELGEYE ÖZGÜ OLMALI. Türkçe rapor adları ASCII süzgecinden geçince
    // ("Öğrenci Raporu.pdf" → "renciRaporu.pdf") farklı raporlar aynı geçici
    // dosyaya düşebiliyordu; özet eklenince her belge kendi dosyasını alır.
    let file = std::env::temp_dir()
        .join(format!("km-{}-{:016x}-{}", std::process::id(), damga, safe_stem(&name)));
    std::fs::write(&file, &bytes).map_err(|e| format!("Geçici dosya yazılamadı: {e}"))?;

    let sonuc = spool(&file, &printer, helper_path(&app), &job);

    // KİŞİSEL VERİ GEÇİCİ KLASÖRDE BIRAKILMAZ. Silinememesi baskıyı
    // başarısız saymaz; iş zaten kuyruğa girmiştir.
    let _ = std::fs::remove_file(&file);
    sonuc.map(|_| printer)
}

/// Dosya adının güvenli ASCII gövdesi (geçici dosya için).
fn safe_stem(name: &str) -> String {
    let safe = name
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || *c == '.' || *c == '-' || *c == '_')
        .collect::<String>();
    if safe.is_empty() { "belge.pdf".to_string() } else { safe }
}

// ------------------------------------------------------------- PDF kaydet

/// Önerilen dosya adını kaydetmeye uygun hâle getirir: yol ayracı ve denetim
/// karakteri atılır, `.pdf` uzantısı garanti edilir. Türkçe harfler KALIR —
/// kullanıcı "Kurum Cari Hesaplar 16.10.2026.pdf" görmeli.
pub fn suggested_name(name: &str) -> String {
    let cleaned: String = name
        .chars()
        .map(|c| if matches!(c, '/' | '\\' | ':' | '*' | '?' | '"' | '<' | '>' | '|') || c.is_control() { '-' } else { c })
        .collect::<String>()
        .trim()
        .trim_matches('.')
        .to_string();
    let base = if cleaned.is_empty() { "rapor".to_string() } else { cleaned };
    let mut base: String = base.chars().take(120).collect();
    if !base.to_lowercase().ends_with(".pdf") {
        base.push_str(".pdf");
    }
    base
}

/// `klasör/ad.pdf` doluysa `ad (2).pdf`, `ad (3).pdf` … — üzerine yazılmaz.
pub fn unique_path(dir: &Path, name: &str) -> PathBuf {
    let first = dir.join(name);
    if !first.exists() {
        return first;
    }
    let stem = name.strip_suffix(".pdf").or_else(|| name.strip_suffix(".PDF")).unwrap_or(name);
    for index in 2..1000 {
        let candidate = dir.join(format!("{stem} ({index}).pdf"));
        if !candidate.exists() {
            return candidate;
        }
    }
    dir.join(format!("{stem}-{}.pdf", std::process::id()))
}

/// Kullanıcının "İndirilenler" klasörü (yoksa ev dizini).
fn downloads_dir() -> PathBuf {
    let home = std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" })
        .map(PathBuf::from)
        .unwrap_or_else(std::env::temp_dir);
    for name in ["Downloads", "İndirilenler"] {
        let candidate = home.join(name);
        if candidate.is_dir() {
            return candidate;
        }
    }
    home
}

/// İşletim sisteminin kaydet penceresi. `Ok(None)` = kullanıcı vazgeçti;
/// `Err` = pencere açılamadı (çağıran İndirilenler'e yazar).
#[cfg(target_os = "macos")]
fn save_dialog(dir: &Path, name: &str) -> Result<Option<PathBuf>, String> {
    let quote = |text: &str| text.replace('\\', "\\\\").replace('"', "\\\"");
    let script = format!(
        "POSIX path of (choose file name with prompt \"PDF olarak kaydet\" \
         default name \"{}\" default location (POSIX file \"{}\"))",
        quote(name),
        quote(&dir.to_string_lossy())
    );
    let out = Command::new("osascript")
        .args(["-e", "activate", "-e", &script])
        .output()
        .map_err(|e| format!("Kaydet penceresi açılamadı: {e}"))?;
    if !out.status.success() {
        // -128 = "User canceled". Başka hata pencerenin açılamadığıdır.
        let err = String::from_utf8_lossy(&out.stderr);
        return if err.contains("-128") { Ok(None) } else { Err(err.trim().to_string()) };
    }
    let path = String::from_utf8_lossy(&out.stdout).trim().to_string();
    Ok((!path.is_empty()).then(|| PathBuf::from(path)))
}

#[cfg(windows)]
fn save_dialog(dir: &Path, name: &str) -> Result<Option<PathBuf>, String> {
    let quote = |text: &str| text.replace('\'', "''");
    let script = format!(
        "Add-Type -AssemblyName System.Windows.Forms; \
         $d = New-Object System.Windows.Forms.SaveFileDialog; \
         $d.Filter = 'PDF belgesi (*.pdf)|*.pdf'; $d.Title = 'PDF olarak kaydet'; \
         $d.FileName = '{}'; $d.InitialDirectory = '{}'; $d.OverwritePrompt = $true; \
         if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {{ \
           [Console]::OutputEncoding = [Text.Encoding]::UTF8; $d.FileName }}",
        quote(name),
        quote(&dir.to_string_lossy())
    );
    let out = Command::new("powershell")
        .args(["-NoProfile", "-NonInteractive", "-STA", "-Command", &script])
        .output()
        .map_err(|e| format!("Kaydet penceresi açılamadı: {e}"))?;
    if !out.status.success() {
        return Err(String::from_utf8_lossy(&out.stderr).trim().to_string());
    }
    let path = String::from_utf8_lossy(&out.stdout).trim().to_string();
    Ok((!path.is_empty()).then(|| PathBuf::from(path)))
}

#[cfg(all(not(windows), not(target_os = "macos")))]
fn save_dialog(dir: &Path, name: &str) -> Result<Option<PathBuf>, String> {
    let target = dir.join(name).to_string_lossy().into_owned();
    // zenity (GNOME) → kdialog (KDE). Çıkış 1 = vazgeçildi.
    let attempts: [(&str, Vec<String>); 2] = [
        ("zenity", vec!["--file-selection".into(), "--save".into(), "--confirm-overwrite".into(),
                        "--title=PDF olarak kaydet".into(), format!("--filename={target}")]),
        ("kdialog", vec!["--getsavefilename".into(), target.clone(), "*.pdf".into()]),
    ];
    for (program, args) in attempts {
        let Ok(out) = Command::new(program).args(&args).output() else { continue };
        if out.status.code() == Some(1) {
            return Ok(None);
        }
        if out.status.success() {
            let path = String::from_utf8_lossy(&out.stdout).trim().to_string();
            return Ok((!path.is_empty()).then(|| PathBuf::from(path)));
        }
    }
    Err("Kaydet penceresi açılamadı (zenity/kdialog yok).".into())
}

/// PDF'i BU CİHAZA kaydeder ve kaydedilen yolu döndürür; vazgeçilirse boş metin.
///
/// NEDEN KABUKTA. Web görünümünde `<a download>` macOS'un WKWebView'ında
/// hiçbir şey yapmıyor (indirme işleyicisi yok); kullanıcı "PDF kaydet"e basıp
/// dosyanın nereye gittiğini bulamıyordu. Kaydet penceresi işletim sisteminin
/// kendisidir (macOS `choose file name`, Windows `SaveFileDialog`, Linux
/// zenity/kdialog) — eklenti gerekmez. Pencere açılamazsa dosya
/// "İndirilenler"e, üzerine yazmadan kaydedilir ve yolu söylenir.
///
/// ASYNC: pencere açıkken kabuğun ana iş parçacığı bekletilmez.
#[tauri::command]
pub async fn pdf_save(data: String, name: String) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || save_blocking(&data, &name))
        .await
        .map_err(|e| format!("Kaydetme yarıda kaldı: {e}"))?
}

fn save_blocking(data: &str, name: &str) -> Result<String, String> {
    use base64::Engine as _;

    let bytes = base64::engine::general_purpose::STANDARD
        .decode(data.as_bytes())
        .map_err(|e| format!("Belge çözülemedi: {e}"))?;
    if !bytes.starts_with(b"%PDF") {
        return Err("Kaydedilecek dosya PDF değil.".into());
    }
    let name = suggested_name(name);
    let dir = downloads_dir();
    let target = match save_dialog(&dir, &name) {
        Ok(Some(path)) => {
            if path.extension().map(|ext| ext.eq_ignore_ascii_case("pdf")).unwrap_or(false) {
                path
            } else {
                path.with_extension("pdf")
            }
        }
        Ok(None) => return Ok(String::new()),
        Err(_) => unique_path(&dir, &name),
    };
    std::fs::write(&target, &bytes).map_err(|e| format!("PDF kaydedilemedi: {e}"))?;
    Ok(target.to_string_lossy().into_owned())
}

#[cfg(test)]
mod tests {
    use super::{page_ranges, resolve, suggested_name, unique_path};

    #[test]
    fn sayfa_araligi_dogrulanir_ve_sadelesir() {
        assert_eq!(page_ranges("").unwrap(), None);
        assert_eq!(page_ranges("  ").unwrap(), None);
        assert_eq!(page_ranges("1-3, 5").unwrap().as_deref(), Some("1-3,5"));
        assert_eq!(page_ranges("2-2").unwrap().as_deref(), Some("2"));
        // Komuta serbest metin gitmez.
        for bad in ["0", "3-1", "1;rm", "a-b", "1--2", "10000", "-", "1-"] {
            assert!(page_ranges(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn kaydet_adi_guvenli_ve_pdf_uzantili() {
        assert_eq!(suggested_name("Kurum Cari 16.10.2026"), "Kurum Cari 16.10.2026.pdf");
        assert_eq!(suggested_name("../a/b:c.pdf"), "-a-b-c.pdf");
        assert_eq!(suggested_name(""), "rapor.pdf");
        assert_eq!(suggested_name("Öğrenci.PDF"), "Öğrenci.PDF");
    }

    #[test]
    fn kaydet_uzerine_yazmaz() {
        let dir = std::env::temp_dir().join(format!("km-kaydet-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let first = unique_path(&dir, "rapor.pdf");
        assert_eq!(first, dir.join("rapor.pdf"));
        std::fs::write(&first, b"%PDF").unwrap();
        assert_eq!(unique_path(&dir, "rapor.pdf"), dir.join("rapor (2).pdf"));
        let _ = std::fs::remove_dir_all(&dir);
    }

    fn liste(names: &[&str]) -> Vec<String> {
        names.iter().map(|n| n.to_string()).collect()
    }

    #[test]
    fn ayardan_secilen_yazici_baglayicidir() {
        let kurulu = liste(&["Brother_DCP", "HP_LaserJet"]);
        // Sistemin varsayılanı başka olsa bile kullanıcının seçimi kazanır.
        let hedef = resolve("Brother_DCP", &kurulu, "HP_LaserJet", "").unwrap();
        assert_eq!(hedef, "Brother_DCP");
    }

    #[test]
    fn secim_yoksa_sistemin_varsayilanina_dusulur() {
        // KULLANICININ İSTEDİĞİ DAVRANIŞ: ayara hiç uğramamış bir kurulumda
        // "Yazdır" çalışsın. Eskiden burada hata dönüyordu ve bütün yazdırma
        // düğmeleri "Bu cihazda yazıcı seçilmemiş" diye kapalı kalıyordu.
        let kurulu = liste(&["HP_LaserJet"]);
        assert_eq!(resolve("", &kurulu, "HP_LaserJet", "").unwrap(), "HP_LaserJet");
    }

    #[test]
    fn silinmis_yazici_varsayilana_duser() {
        // Ayarda adı yazan yazıcı kaldırılmış. Ona göndermek CUPS'tan ham
        // "does not exist" hatası getirirdi; kullanıcı bunu kendi ayarındaki
        // adla ilişkilendiremez.
        let kurulu = liste(&["HP_LaserJet"]);
        assert_eq!(resolve("Eski_Yazici", &kurulu, "HP_LaserJet", "").unwrap(),
                   "HP_LaserJet");
    }

    #[test]
    fn silinmis_yazici_ve_varsayilan_yoksa_neden_soylenir() {
        let kurulu = liste(&["HP_LaserJet"]);
        let hata = resolve("Eski_Yazici", &kurulu, "", "").unwrap_err();
        // Hata KURULU YAZICILARI sayar: kullanıcı hangi adı seçeceğini görür.
        assert!(hata.contains("Eski_Yazici"), "{hata}");
        assert!(hata.contains("HP_LaserJet"), "{hata}");
    }

    #[test]
    fn hic_yazici_yoksa_kurulum_istenir() {
        let hata = resolve("", &[], "", "").unwrap_err();
        assert!(hata.contains("kurulu yazıcı bulunamadı"), "{hata}");
    }

    #[test]
    fn liste_alinamadiysa_secim_cope_atilmaz() {
        // `lpstat` susmuşsa seçim DOĞRULANAMAZ; "doğrulayamadım" ile "yanlış"
        // ayrı şeylerdir ve çalışan baskıyı durdurmak yanlış olandır.
        let hedef = resolve("HP_LaserJet", &[], "", "lpstat çalıştırılamadı").unwrap();
        assert_eq!(hedef, "HP_LaserJet");
    }

    #[test]
    fn liste_alinamadi_ve_secim_yoksa_sebep_tasinir() {
        let hata = resolve("", &[], "", "lpstat çalıştırılamadı").unwrap_err();
        assert!(hata.contains("lpstat çalıştırılamadı"), "{hata}");
    }
}
