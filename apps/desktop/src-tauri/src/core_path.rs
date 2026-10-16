//! `core_request` adres denetimi (güvenlik bulgusu KM-14, 63-duzeltici).
//!
//! Adres `format!("{base}{path}")` ile kurulur ve isteğe oturumun **Bearer**
//! belirteci eklenir. `path` arayüzden gelir; `/` ile başlamazsa
//! (`@evil.com/x` → `https://sunucu@evil.com/x`) ya da `//evil.com/x` gibi
//! şemasız mutlak adresse istek — belirteçle birlikte — BAŞKA KONAĞA gider.
//! Yalnız sayfaya betik enjekte edilirse istismar edilebilir (CSP
//! `script-src 'self'`), yine de kapı burada kapanır.
//!
//! Kural: yol `/` ile başlar; `//` ya da `/\` ile başlamaz; sorgudan önceki
//! bölümde `@`, `\`, şema (`://`), boşluk ya da denetim karakteri yoktur.
//! Sorgu (`?…`) serbesttir: arama metninde `@` geçebilir ve konağı değiştiremez.
//!
//! Bağımlılığı yoktur: `rustc --test src/core_path.rs` ile tek başına sınanır.

/// Çekirdek adresini kurar; yol kurala uymazsa istek HİÇ GÖNDERİLMEZ.
pub fn core_url(base: &str, path: &str) -> Result<String, String> {
    check_path(path)?;
    Ok(format!("{base}{path}"))
}

/// Yolun yalnız çekirdeğin kendi konağında kalabileceğini doğrular.
pub fn check_path(path: &str) -> Result<(), String> {
    let refuse = || Err(format!("geçersiz çekirdek yolu: {}", preview(path)));
    if !path.starts_with('/') || path.starts_with("//") || path.starts_with("/\\") {
        return refuse();
    }
    let head = path.split(['?', '#']).next().unwrap_or("");
    if head.contains('@')
        || head.contains('\\')
        || head.contains("://")
        || head.chars().any(|c| c.is_control() || c.is_whitespace())
    {
        return refuse();
    }
    // Sorgu/parça bölümünde de denetim karakteri (satır sonu ile başlık
    // enjeksiyonu) olmaz.
    if path.chars().any(char::is_control) {
        return refuse();
    }
    Ok(())
}

/// Hata metnine yolun kısa, güvenli bir önizlemesi (belirteç/sır taşımaz).
fn preview(path: &str) -> String {
    let short: String = path.chars().take(40).filter(|c| !c.is_control()).collect();
    if path.chars().count() > 40 {
        format!("{short}…")
    } else {
        short
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const BASE: &str = "https://kontrolmerkezi.bbdstore.com.tr";

    #[test]
    fn olagan_yollar_gecer() {
        for path in [
            "/health",
            "/api/bbd_students/students/STU-1/reactivate",
            "/api/bbd_akademi/km/students?q=ali%40veli.com",
            "/api/x?q=a@b.com&next=https://y",
            "/auth/me",
        ] {
            assert_eq!(core_url(BASE, path).unwrap(), format!("{BASE}{path}"), "{path}");
        }
    }

    #[test]
    fn baska_konaga_kacis_reddedilir() {
        for path in [
            "",
            "@evil.com/api/x",
            "evil.com/api",
            ".evil.com/api",
            "//evil.com/api",
            "/\\evil.com/api",
            "https://evil.com/api",
            "/api/x@evil.com",
            "/api/https://evil.com",
            "/api\\..\\x",
            "/api/x y",
            "/api/x\r\nHost: evil.com",
            "/api/x?q=1\nX: y",
        ] {
            assert!(core_url(BASE, path).is_err(), "kabul edilmemeliydi: {path:?}");
        }
    }

    #[test]
    fn hata_metni_kisa_ve_temiz() {
        let error = check_path(&format!("@{}", "a".repeat(100))).unwrap_err();
        assert!(error.chars().count() < 80, "{error}");
        assert!(error.ends_with('…'));
    }
}
