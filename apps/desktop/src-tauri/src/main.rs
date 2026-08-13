// Kontrol Merkezi — Tauri 2 kabuğu (ADR 0002).
//
// Kabukta modül adı geçmez (K1'in arayüz tarafındaki karşılığı). Bu dosyanın
// iki işi var: pencereyi açmak ve Python çekirdeğini (sidecar) yönetmek.
// Sidecar'ın ömrü kabuğa bağlıdır: kabuk kapanınca çekirdek de kapanır,
// arkada süreç kalmaz.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::Duration;

use tauri::Manager;

/// Çekirdeğin dinlediği yerel adres. Ayarla değiştirilirse burası da değişir
/// (config/default.yaml → server.port).
const CORE_ADDR: &str = "127.0.0.1:8787";

struct Sidecar(Mutex<Option<Child>>);

/// Depo kökünü bulur: çalıştırılabilir dosyadan yukarı doğru, çekirdeğin
/// kaynağını taşıyan klasör aranır. Geliştirmede `target/debug` altından,
/// kurulumda paket kökünden çalışır.
fn find_root() -> Option<PathBuf> {
    let mut dir: PathBuf = std::env::current_exe().ok()?.parent()?.to_path_buf();
    for _ in 0..8 {
        if dir.join("backend/src/km_core").is_dir() {
            return Some(dir);
        }
        dir = dir.parent()?.to_path_buf();
    }
    None
}

fn python_path(root: &Path) -> PathBuf {
    let venv = root.join(".venv/bin/python");
    if venv.is_file() {
        venv
    } else {
        PathBuf::from("python3")
    }
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

fn spawn_core() -> Option<Child> {
    if core_is_running() {
        eprintln!("[kabuk] çekirdek zaten çalışıyor, yenisi başlatılmadı");
        return None;
    }

    let root = find_root()?;
    let python = python_path(&root);

    match Command::new(&python)
        .arg("-m")
        .arg("km_core.main")
        .current_dir(&root)
        .env("PYTHONPATH", root.join("backend/src"))
        .env("PYTHONUNBUFFERED", "1")
        .spawn()
    {
        Ok(child) => {
            eprintln!("[kabuk] çekirdek başlatıldı: {}", python.display());
            Some(child)
        }
        Err(error) => {
            // Çekirdek açılmazsa pencere yine açılır ve giriş ekranı durumu
            // söyler; kabuk sessizce ölmez.
            eprintln!("[kabuk] çekirdek başlatılamadı: {error}");
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
///
/// Kabuk burada yalnızca BORUdur: yol, gövde ve belirteç arayüzden gelir;
/// hiçbir modül adı ya da iş kuralı bu dosyada geçmez (K1).
///
/// Bağımlılık eklemedik: yerel, TLS'siz, kısa ömürlü bir HTTP/1.1 isteği için
/// `Connection: close` + sonuna kadar oku yeterli.
#[tauri::command]
async fn core_request(
    method: String,
    path: String,
    body: Option<String>,
    token: Option<String>,
) -> Result<CoreResponse, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let address = CORE_ADDR.parse().map_err(|_| "geçersiz çekirdek adresi".to_string())?;
        let mut stream = TcpStream::connect_timeout(&address, Duration::from_secs(5))
            .map_err(|error| format!("çekirdeğe bağlanılamadı: {error}"))?;
        stream
            .set_read_timeout(Some(Duration::from_secs(60)))
            .map_err(|error| error.to_string())?;

        let payload = body.unwrap_or_default();
        let mut request = format!(
            "{method} {path} HTTP/1.1\r\nHost: {CORE_ADDR}\r\nConnection: close\r\nAccept: application/json\r\n"
        );
        if let Some(value) = token {
            request.push_str(&format!("Authorization: Bearer {value}\r\n"));
        }
        if !payload.is_empty() {
            request.push_str("Content-Type: application/json\r\n");
            request.push_str(&format!("Content-Length: {}\r\n", payload.len()));
        }
        request.push_str("\r\n");
        request.push_str(&payload);

        stream
            .write_all(request.as_bytes())
            .map_err(|error| format!("istek gönderilemedi: {error}"))?;

        let mut raw = Vec::new();
        stream
            .read_to_end(&mut raw)
            .map_err(|error| format!("yanıt okunamadı: {error}"))?;

        let split = raw
            .windows(4)
            .position(|window| window == b"\r\n\r\n")
            .ok_or_else(|| "yanıt bozuk".to_string())?;

        let head = String::from_utf8_lossy(&raw[..split]).to_string();
        let status = head
            .lines()
            .next()
            .and_then(|line| line.split_whitespace().nth(1))
            .and_then(|code| code.parse::<u16>().ok())
            .ok_or_else(|| "durum kodu okunamadı".to_string())?;

        Ok(CoreResponse {
            status,
            body: String::from_utf8_lossy(&raw[split + 4..]).to_string(),
        })
    })
    .await
    .map_err(|error| format!("istek tamamlanamadı: {error}"))?
}

fn main() {
    let sidecar = Sidecar(Mutex::new(spawn_core()));

    tauri::Builder::default()
        .manage(sidecar)
        .invoke_handler(tauri::generate_handler![core_request])
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
