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

İş akışında **kod imzalama adımı yoktur** (ADR 0023 §5) — aşağıdaki
SmartScreen bölümü geçerlidir. Güncelleme imzası ayrı bir şeydir; bir sonraki
bölüm onu anlatır.

### Otomatik güncelleme — yayına ne eklenir

Kurulu uygulama, **Sistem Ayarları → Güncelleme** sekmesinden bu depodaki en
son yayına bakar. Denetleme, indirme ve kurulum üç ayrı düğmedir: uygulama
kendiliğinden indirmez ve kendiliğinden yeniden başlamaz.

Yayına üç tür dosya girer:

| Dosya | Ne işe yarar |
|---|---|
| `*-setup.exe`, `*.AppImage`, `*.dmg`, `*.deb` | elle kurulum |
| `*.app.tar.gz` | macOS'un **güncelleme** paketi (kullanıcı bunu indirmez) |
| `*.sig` | paketin imzası |
| `latest.json` | hangi platform hangi paketi indirecek |

`latest.json` iş akışının son adımında üretilir: paketler yayına yüklendikten
sonra gerçek indirme adresleri okunur ve dosya ona göre yazılır. Adres
tahmin edilmez — GitHub varlık adlarındaki boşlukları noktaya çevirdiği için
kalıptan kurulan bir adres indirme anında 404 verirdi.

**İki GitHub Secret gerekir** (Settings → Secrets and variables → Actions):

| Secret | İçerik |
|---|---|
| `TAURI_SIGNING_PRIVATE_KEY` | `tauri signer generate` ile üretilen özel anahtar dosyasının **içeriği** |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | anahtarın parolası; parolasız anahtarda **boş** bırakılır |

Açık anahtar sır değildir ve `apps/desktop/src-tauri/tauri.conf.json` içinde
`plugins.updater.pubkey` olarak durur. **Özel anahtar depoya konmaz (K8).**
İkisi bir çifttir: özel anahtar yenilenirse `pubkey` de aynı anda
güncellenmelidir, yoksa yeni paketleri eski kurulumlar reddeder — ve
kaybedilen bir özel anahtarın yerine yenisi konana kadar hiçbir kurulu
uygulama güncellenemez.

Secret tanımlı değilse paketler imzasız çıkar, `latest.json` adımı **açık bir
hatayla durur** ve güncellenemeyen bir sürüm sessizce yayınlanmış olmaz.

Kendi kendini güncelleyen biçimler: Windows kurucusu (NSIS), macOS uygulaması
ve Linux **AppImage**. `.deb` ile kurulmuş bir uygulama güncelleme paketini
bulamaz; ekran bunu sebebiyle birlikte söyler.

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

## Yeni cihaz kurulumu

Sıfırdan bir Mac ya da Windows makineye Kontrol Merkezi kurmanın tam sırası.
Linux için tek fark paket biçimidir (`.deb` / `.AppImage`); adımlar aynıdır.

Kurulum **merkezî kimlik servisiyle** eşlenerek tamamlanır (ADR 0021): makine
kullanıcı listesini kendi veritabanında doğurmaz, merkezden alır. Eşlenmemiş
bir kurulumda merkezdeki hiç kimse giriş yapamaz.

### 0. Önce eşleme kodunu alın

Kodu **başka bir makinede**, merkeze zaten eşli bir kurulumdan üretirsiniz:
**KM Cihaz Eşle** ekranı → **Eşleme kodu üret**. Gereken izin
`installations.manage`.

- Kod **8 hanelidir, tek kullanımlıktır** ve süresini merkez belirler
  (ekrandaki geri sayım sunucunun verdiği bitiş anından türer).
- Yeni kod üretmek **bekleyen eski kodları geçersiz kılar.**
- Kodu makinenin başına geçmeden üretmeyin: ömrü dakikalarla ölçülür.
- Kod hiçbir yere yazılmaz — denetim izine de girmez. Panoya yalnız siz
  isterseniz gider.

Hiç eşli makine kalmadıysa (ilk kurulum, ya da tek makine bozulduysa) kod
merkezin kendi yönetim ucundan üretilir; o yol `KM_IDENTITY_ADMIN_TOKEN` ister
ve token merkezin ortam değişkenlerindedir — depoda ve bu belgede durmaz (K8).

### 1. Paketi kurun

| Platform | Dosya | Kurulum |
|---|---|---|
| Windows | `*-setup.exe` (NSIS) veya `*.msi` | çift tıkla; SmartScreen uyarısı için aşağıdaki bölüm |
| macOS | `*.dmg` | aç, uygulamayı **Applications** klasörüne sürükle |
| Linux | `*.deb` / `*.AppImage` | `sudo apt install ./dosya.deb` / dosyayı çalıştırılabilir yapın |

Kurulan makinede **Python gerekmez** — uygulama kendi yorumlayıcısını taşır
(ADR 0023). Windows'ta **WebView2 Runtime** gerekir; Windows 11'de yerleşiktir,
yoksa kurucu getirir.

macOS paketleri imzalı ve notarize üretilir (ADR 0024). İmzasız üretilmiş bir
paket Gatekeeper'a takılır ve **"uygulama hasar görmüş"** der; ileti imzadan hiç
söz etmez. Böyle bir paketi kurmayın, imzalısını isteyin.

Makinenin merkeze **https** ile ulaşabilmesi gerekir:
`https://kontrolmerkezi.bbdstore.com.tr`. Adres pakete gömülü gelir
(`config/default.yaml` → `platform.identity_sync.base_url`); taze kurulumda
elle ayar yapılmaz.

### 2. İlk açılış — ne zaman ne beklenir

| Sıra | Ekranda | Süre | Arkada olan |
|---|---|---|---|
| 1 | "Çekirdek başlatılıyor…" | ilk açılışta 5–20 sn | kabuk gömülü Python'u başlatır, çekirdek 127.0.0.1:8787'yi açar |
| 2 | **"Bu kurulum henüz eşlenmedi"** + 8 kutucuk | kod yazılana kadar | eşleme ekranı; giriş ekranı gizli |
| 3 | kutucuklar dolar, "Eşleniyor…" | 1–3 sn | kod merkeze gider; kurulum token'ı **kasaya** yazılır, merkezin kimlik anahtarı benimsenir |
| 4 | giriş ekranı (6 haneli PIN) | — | eşleme bitti |
| 5 | ilk giriş | 1–3 sn | kadro merkezden çekilir ve yerel tablolara yansıtılır |

İkinci ve sonraki açılışlar 2. ve 3. adımı atlar: eşleme bir kez yapılır.

**Eşleme ekranı gelmediyse** ve doğrudan giriş ekranı açıldıysa, kurulum kendini
"eşlenmiş" sanıyor ya da merkezi hiç tanımıyordur. İkisi de aşağıdaki "Takılırsa
nereye bakılır" bölümünde.

### 3. Kurulumu doğrulayın

Giriş yaptıktan sonra **KM Cihaz Eşle** ekranını açın (izin:
`installations.view`). **Merkezden gelenler** kartında şunlar yazar:

- **Kadro revizyonu** ve içeriği (kaç kullanıcı, kaç rol),
- **son kadro tazeleme** anı,
- merkezden alınan **kurulum paketinin revizyonu** — dağıtılan ayar ve sırlar
  (ADR 0025); "hiç alınmamış" yazıyorsa merkez bu kuruluma paket göndermemiştir,
- **Şimdi tazele** düğmesi — kadroyu ve kurulum paketini beklemeden çeker; o
  turda **kaç ayar ve kaç sırrın yazıldığını** da söyler (değişmeyene
  dokunulmaz, sayı yalnız yazılanları gösterir).

Aynı ekrandaki **Kurulumlar** listesinde yeni makinenin satırı "Etkin" rozetiyle
görünür. Görünmüyorsa eşleme tamamlanmamıştır.

### Veri dizini — nerede durur

Uygulamanın yazdığı her şey (veritabanı, kasa anahtarı, kadro önbelleği,
yedekler, ses kitaplığı) **program klasöründen ayrı** bir yerde durur; program
klasörü salt okunurdur (ADR 0023).

| Platform | Veri dizini |
|---|---|
| Windows | `%APPDATA%\Kontrol Merkezi\` |
| macOS | `~/Library/Application Support/Kontrol Merkezi/` |
| Linux | `~/.local/share/kontrol-merkezi/` (`XDG_DATA_HOME` ezerse orası) |
| Depodan çalışırken | `<depo kökü>/data/` — ölçüt kökte `.git` bulunmasıdır |

`KM_DATA_DIR` ortam değişkeni **hepsini ezer.** Uygulamayı kaldırmak veri
dizinini silmez; **yedeklenecek olan burasıdır.**

**Kabuğun tanılama dosyaları BAŞKA bir klasördedir** ve bu ayrım en çok vakit
kaybettiren şeydir: kabuk (Tauri) kendi klasörünü *paket kimliğiyle* açar,
çekirdek (Python) ise ürün adıyla.

| Platform | Kabuk tanılama klasörü |
|---|---|
| Windows | `%APPDATA%\com.benimdunyam.kontrolmerkezi\` |
| macOS | `~/Library/Application Support/com.benimdunyam.kontrolmerkezi/` |
| Linux | `~/.local/share/com.benimdunyam.kontrolmerkezi/` |

İçinde iki dosya vardır:

| Dosya | Ne yazar |
|---|---|
| `kabuk-acilis.log` | kabuğun kararları: çekirdek kökü nerede bulundu, hangi Python seçildi, çekirdek başlatılabildi mi |
| `cekirdek-cikti.log` | çekirdeğin ham çıktısı: import hatası, eksik kütüphane, açılışta patlayan her şey |

### Takılırsa nereye bakılır

**1. `/health` künyesi — hangi kod çalışıyor.** Çekirdek ayaktaysa yanıt verir:

```bash
curl http://127.0.0.1:8787/health          # macOS / Linux
```
```powershell
Invoke-RestMethod http://127.0.0.1:8787/health   # Windows
```

Yanıtta `build.commit`, `build.version`, `build.builtAt` ve `build.source`
bulunur. `source` **`paket`** ise kurulu uygulama, **`depo`** ise depo
klasöründen çalışan bir kopya konuşuyordur — "düzelttim ama düzelmedi"
tartışmasını bitiren alan budur. `modules.problems` yüklenemeyen modülleri
sayar; çekirdek onlarsız da ayağa kalkar (K7).

Yanıt hiç gelmiyorsa çekirdek başlamamıştır → `cekirdek-cikti.log`.

**2. Belirtiye göre:**

| Belirti | Bakılacak yer / yapılacak |
|---|---|
| "Çekirdeğe ulaşılamadı" | `kabuk-acilis.log` (kök ve Python seçimi), sonra `cekirdek-cikti.log` |
| Eşleme ekranı hiç gelmiyor, PIN'ler çalışmıyor | kurulum merkezi tanımıyor ya da kimlik anahtarı ayrışmış olabilir; **KM Cihaz Eşle** ekranı hangisi olduğunu ve adımları yazar |
| "Kod geçersiz" | kod kullanılmış ya da süresi dolmuş — yeni kod üretin |
| "Çok fazla deneme yapıldı" | beş yanlış koddan sonra 1 dakika beklenir |
| Giriş yapılamıyor, veriler "kaybolmuş" gibi | veri dizini beklenen yerde olmayabilir: `KM_DATA_DIR` tanımlı kalmış olabilir ya da **depo içinde paket derlemesi** yapılmıştır (aşağıdaki "Depo içinde derleme" uyarısı) |
| Kurulum merkezde iptal edilmiş | kurulum bir sonraki tazelemede kendi eşlemesini düşürür ve eşleme ekranı geri gelir; yeni kodla yeniden eşlenir |
| Çekirdek kaynağı bulunamadı | `KM_ROOT` ortam değişkeniyle kök elle gösterilir |

**3. Kurulum kendini onarabilir.** Merkezle anahtarı uyuşmayan ya da iptal
edilmiş bir kurulumda kimse giriş yapamaz — dolayısıyla oturum isteyen hiçbir
düğmeye ulaşılamaz. Bu kilidi açan uç oturum istemez:

```bash
curl -X POST http://127.0.0.1:8787/api/pairing/reset
```

Yalnız **bu makinenin** eşleme durumunu düşürür: yerel kullanıcılara, kasadaki
öteki sırlara ve özel anahtara dokunmaz. Yeniden eşlenmek merkezden yeni bir kod
ister ve onu ancak yetkili biri üretir.

### Depo içinde derleme — kurulumu bozan tuzak

`scripts/build-release.sh` depo klasöründe koşturulduğunda `backend/`,
`modules/`, `config/` ve `runtime/` klasörlerinin birer **kopyası**
`apps/desktop/src-tauri/target/release/` altına düşer (Tauri kaynakları ikilinin
yanına koyar). Bu kopya `.git` taşımadığı için kabuk oradan başlatılan çekirdeği
**kurulu uygulama** sanır ve veri dizini olarak sistem klasörünü seçer — deponun
`data/` klasörünü değil. Belirti: "giriş yapılamadı", kullanıcılar yok olmuş
gibi görünür.

İki taraf da kapatıldı: `scripts/launch-desktop.sh` artık `KM_ROOT`u açıkça
verir, `scripts/build-release.sh` de derleme bittikten sonra bu kalıntıları
temizler. Elle derleme yapıyorsanız (`cargo tauri build --config
tauri.release.json`) kalıntıları kendiniz silin:

```bash
rm -rf apps/desktop/src-tauri/target/release/{backend,modules,config,docs,runtime}
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
