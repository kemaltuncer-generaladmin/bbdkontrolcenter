# deploy/

- `systemd/` — çekirdeği servis olarak çalıştıran unit dosyaları
- `packaging/` — Ubuntu paketi / Tauri bundle yapılandırması

Tauri kabuğu `webkit2gtk-4.1` bağımlılığını gerektirir; paketlemede karşılanır
(ADR 0002).

Paketleme kararı ve gerekçesi: [ADR 0023](../docs/adr/0023-paketleme-ve-veri-dizini.md).

---

## Paket üretimi

Çapraz derleme yoktur: her platformun paketi kendi platformunda üretilir.

| Platform | Betik | Çıktı |
|---|---|---|
| Linux | `scripts/build-release.sh` | `dist/*.deb`, `dist/*.AppImage` |
| macOS | `scripts/build-release.sh` | `dist/*.dmg` |
| Windows | `scripts/build-release.ps1` | `dist/*-setup.exe`, `dist/*.msi` |

Her iki betik de aynı sırayı izler: gömülü Python'u indirip doğrular,
bağımlılıkları oraya kurar, menü kaydını üretir, `cargo tauri build` çağırır ve
çıktıyı `dist/` altına kopyalar.

### 1. Yol — GitHub Actions (olağan yol)

Geliştirme makinesi Linux'tur; orada `.exe`, `.msi` ve `.dmg` **üretilemez.**
Üç platformun paketi `.github/workflows/release.yml` iş akışında, her biri
kendi koşucusunda derlenir (`ubuntu-latest`, `windows-latest`, `macos-latest`).
İş akışı derleme mantığını kopyalamaz; yukarıdaki betikleri çağırır.

**Elle tetikleme** — depoda **Actions** → **Paket üretimi** → **Run workflow**.
Paketler koşu bittiğinde o çalıştırmanın sayfasındaki **Artifacts** kutusundan
inilir; üç arşiv olur:

| Artefakt | İçerik |
|---|---|
| `kontrol-merkezi-windows` | `*-setup.exe` (NSIS), `*.msi` |
| `kontrol-merkezi-linux` | `*.deb`, `*.AppImage` |
| `kontrol-merkezi-macos` | `*.dmg` (arm64) |

Artefaktlar **30 gün** durur. Kalıcı olması istenen paket etiketle yayımlanır.

**Etiketle yayın** — `v` ile başlayan etiket push'landığında aynı iş akışı
koşar ve üç platformun çıktısını tek bir GitHub Release'e asar:

```bash
git tag v0.1.0
git push origin v0.1.0
```

`main`'e yapılan sıradan push'lar derleme başlatmaz: bu bir masaüstü
uygulamasıdır, her commit'te üç platform derlemek koşucu zamanını boşa harcar.

İş akışında **imzalama adımı yoktur** (ADR 0023 §5) — aşağıdaki SmartScreen
bölümü geçerlidir.

### 2. Yol — yerel betik

Elinde o platformun makinesi varsa betik doğrudan çalıştırılır; Actions'ı
beklemeden paket üretmenin ve derleme hatasını yerinde görmenin yolu budur.
Windows kurucusu için gerekenler aşağıdaki "Windows kurulumu" bölümündedir.

Gömülü Python sürümü betiklerin başındaki iki değişkendedir
(`KM_PY_VERSION`, `KM_PBS_RELEASE`) ve **iki betikte aynı kalmalıdır.**
Ortamdan ezilebilir:

```bash
KM_PY_VERSION=3.12.11 KM_PBS_RELEASE=20250612 scripts/build-release.sh
```

---

## Windows kurulumu

### Derleme makinesinde gerekenler

Betik bunları **kurmaz**, yokluğunda anlaşılır hata verir:

- Rust (rustup) ve MSVC derleme araçları (Visual Studio Build Tools →
  "Desktop development with C++")
- Tauri CLI: `cargo install tauri-cli --version "^2" --locked`
- MSI hedefi için WiX — Tauri CLI ilk çalıştırmada kendisi indirir
- `tar.exe` (Windows 10 1803'ten beri yerleşiktir)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-release.ps1
```

PowerShell 7 (`pwsh`) yeğlenir; betik yalnız ASCII ileti yazar, ama 5.1'in
kod sayfası davranışı Türkçe karakterli çıktıyı bozabilir.

### Kurulum makinesinde gerekenler

- **WebView2 Runtime.** Windows 11'de yerleşiktir. Windows 10'da yoksa Tauri
  kurucusu getirir. Baskı bu bileşene bağlıdır: PDF webview'de açılır ve
  `window.print()` sistemin yazdırma penceresini çağırır (ADR 0014).
- Python **gerekmez** — uygulama kendi yorumlayıcısını taşır (ADR 0023).

### Nereye ne kurulur

| | Yer |
|---|---|
| Uygulama | `C:\Program Files\Kontrol Merkezi\` (makine geneli kurulum) |
| Çekirdek kaynağı | aynı klasörün altında `backend\`, `modules\`, `config\` |
| Gömülü Python | aynı klasörün altında `runtime\python\python.exe` |
| **Veri** | `%APPDATA%\Kontrol Merkezi\` |

Veri klasörü **program klasöründen ayrıdır** ve uygulamayı kaldırmak onu
silmez. İçinde veritabanı (`kontrol-merkezi.sqlite`), kasa anahtarı
(`secret.key`), yedekler ve ses kitaplığı durur. **Yedeklenecek olan burasıdır.**

Başka bir yere almak gerekirse `KM_DATA_DIR` ortam değişkeni her şeyi ezer.

### Kurucuyu açarken: SmartScreen

Kurucu **imzasızdır** — kod imzalama sertifikası alınmadı (ADR 0023 §5).
Windows ilk çalıştırmada mavi bir pencere gösterir:

> **Windows kişisel bilgisayarınızı korudu**
> Microsoft Defender SmartScreen tanınmayan bir uygulamanın başlatılmasını engelledi.

Geçmek için: **Ek bilgi** → **Yine de çalıştır**.

Bilinmesi gerekenler:

- Uyarı **her yeni sürümde** yeniden çıkar. SmartScreen itibarı dosya
  özetine bağlıdır; yeni kurucu = yeni özet = sıfırdan itibar.
- Tarayıcı indirmeyi de "yaygın olarak indirilmiyor" diye engelleyebilir;
  indirmeler listesinden **Sakla** denir. Kurucuyu şirket içi paylaşımdan
  kopyalamak bu adımı bütünüyle atlatır.
- Sertifika alınırsa uyarı kalkar. Karar verilmedi: kurulum kendi şirket
  makinelerine yapılıyor ve kurucuyu çalıştıran kişi onu üreten ekiple aynı
  kurumda.

**Uyarıyı "geçici olarak" susturmak için SmartScreen'i kapatmak yanlıştır.**
Makinede sonra indirilecek her şeyi de korumasız bırakır; uyarı bu kurucu için
bir kez geçilir.

### Çekirdek açılmazsa

Kabuk penceresi yine açılır ve giriş ekranı durumu söyler (K7). Sıra:

1. Görev Yöneticisi'nde `python.exe` var mı — çekirdek ayakta mı?
2. `%APPDATA%\Kontrol Merkezi\` yazılabilir mi?
3. Kabuğu konsoldan çalıştırıp `[kabuk]` satırlarını okuyun:
   ```powershell
   & "C:\Program Files\Kontrol Merkezi\Kontrol Merkezi.exe"
   ```
   Kabuk hangi Python'u seçtiğini ve çekirdeği başlatıp başlatamadığını yazar.
4. Kaynak klasörü elle göstermek gerekirse: `KM_ROOT` ortam değişkeni.
