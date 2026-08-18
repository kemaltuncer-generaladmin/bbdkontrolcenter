# Windows yazdırma yardımcısı

Bu klasöre **derleme sırasında** `SumatraPDF.exe` indirilir; depoda durmaz (K11:
"sürücü/kütüphane/ikili depoya konmaz"). İndirmeyi `scripts/build-release.ps1`
yapar ve yalnız Windows paketinde gerekir.

## Neden gerekli

Windows'ta bir PDF'i **diyalog açmadan** seçili yazıcıya basacak hazır bir
komut yoktur. `Start-Process -Verb PrintTo` kayıtlı PDF işleyicisine bağlıdır ve
Windows 10/11'in varsayılanı olan Edge bu fiili desteklemez — çoğu makinede
sessizce hiçbir şey basılmazdı. Kullanıcı kararı (18.08.2026) küçük bir
yardımcı aracın pakete konmasıydı.

Linux ve macOS'ta bu klasör BOŞ kalır ve kullanılmaz: oralarda CUPS (`lp`)
zaten var.

## Sözleşme

Kabuk şu komutu çağırır (`src/printing.rs`):

    SumatraPDF.exe -print-to "<yazıcı adı>" -silent -exit-when-done <dosya.pdf>

Aracın adı ya da bayrakları değişirse `printing.rs` içindeki `spool()` de
değişmeli; başka yerde geçmez.
