# 0023 — Paketleme: gömülü Python çalışma zamanı ve platforma göre veri dizini

**Durum:** Kabul edildi · 2026-08-17

## Bağlam

Bugünkü çalışma biçimi tek bir geliştirme makinesine göre kurulmuştur:

- Uygulama **depo klasöründen** çalışır; `main.rs` çalıştırılabilir dosyadan
  yukarı doğru `backend/src/km_core` arar.
- Python `.venv/bin/python`'dur; bulunamazsa sistemdeki `python3`.
- Yazılan her şey — SQLite deposu, kasa anahtarı, ses kitaplığı, günlük,
  yedekler — deponun içindeki `data/` klasörüne gider.
- Arayüz değiştiyse **açılışta Rust derlenir** (`launch-desktop.sh` adım 4).

Windows kurulumu geliyor (ADR 0014, 0022). Yukarıdaki dört maddenin **hiçbiri**
kurulu bir uygulamada geçerli değildir:

| Varsayım | Kurulu uygulamada |
|---|---|
| `.venv/bin/python` | Windows'ta yol `.venv\Scripts\python.exe`; zaten `.venv` yoktur |
| sistem `python3` | Kullanıcının makinesinde Python olmayabilir, 3.12'den eski olabilir, paketlerimiz orada bulunmaz |
| depo içi `data/` | `C:\Program Files\...` ve `/usr/lib/...` kullanıcıya kapalıdır; ilk yazma denemesinde uygulama ölür |
| açılışta derleme | Kullanıcının makinesinde derleyici yoktur ve olmamalıdır |

Bir de sessiz bir tuzak var: `data/` deponun içindeyken `git clean` ya da depo
klasörünün silinmesi **veritabanını da götürür**. Geliştirmede bunu bilerek
kabul ettik; kurulu makinede kabul edilemez.

## Karar

### 1. Python çözüm sırası: gömülü → `.venv` → sistem

`main.rs` üç adayı bu sırayla dener:

```
runtime/python/python.exe        (Windows)   ┐ gömülü çalışma zamanı —
runtime/python/bin/python3       (diğerleri) ┘ kurulu uygulamanın yanıtı
.venv/Scripts/python.exe         (Windows)   ┐ geliştirme —
.venv/bin/python                 (diğerleri) ┘ bugünkü davranış aynen korunur
python / python3                             son çare
```

Platform farkı **Rust tarafında**, `cfg!(windows)` ile biter. Sıra bilinçlidir:
gömülü çalışma zamanı varsa kullanıcının makinesindeki hiçbir Python
sorulmadan geçilir — sürüm ve paket kümesi bizim denetimimizdedir.

Gömülü çalışma zamanı **python-build-standalone** dağıtımıdır. Paketleme
betiği indirir, SHA256'sını doğrular, `apps/desktop/src-tauri/runtime/` altına
yerleştirir ve bağımlılıkları oraya kurar. İkili **depoya konmaz** (K11).

Üçüncü adım (sistem Python'u) silinmedi: çekirdek açılmazsa kabuk pencereyi
yine açar ve giriş ekranı durumu söyler (K7). Sessizce ölmez.

### 2. Veri dizini platforma göre çözülür, tek fonksiyondan

`backend/src/km_core/config/paths.py`:

| | Yer |
|---|---|
| Linux | `~/.local/share/kontrol-merkezi` (`XDG_DATA_HOME` varsa o) |
| Windows | `%APPDATA%\Kontrol Merkezi` |
| macOS | `~/Library/Application Support/Kontrol Merkezi` |
| Geliştirme | `<depo>/data` — bugünkü davranış |
| Elle | `KM_DATA_DIR` ortam değişkeni her şeyi ezer |

**Geliştirme ölçütü `.git`tir.** Depo kökünde `.git` varsa depo içi `data/`
kullanılır. Ayrı bir "geliştirme kipi" bayrağı olsaydı, o bayrağı paketlemede
kapatmayı unutmak kurulu uygulamayı sessizce program klasörüne yazdırırdı;
`.git` paketlenmiş uygulamada zaten bulunmaz, yani ölçüt kendiliğinden doğru
tarafa düşer.

Çözüm **tek kapıdan** geçer: `Config.path()` artık `paths.resolve_path()`
çağırır. `data/` ile başlayan her göreli ayar değeri (`data/sounds`,
`data/backups`, `data/identity-roster.json`, depo dosyası, kasa anahtarı)
kendiliğinden doğru diske düşer; mutlak yol yazan kullanıcının tercihi
korunur; `data/` dışındaki göreli yollar paket köküne göre çözülür — orası
okunan dosyaların (ayar, manifest) yeridir.

Klasör yoksa **oluşturulur ve 0700'e çekilir**: `%APPDATA%\Kontrol Merkezi`
ilk açılışta mevcut değildir ve SQLite olmayan klasöre dosya açamaz. İzin
daraltması keyfi değil — içinde öğrenci ve veli telefonu taşıyan veritabanı ve
kasa anahtarı durur (`km_core/files/private.py` başlığındaki denetim bulgusu).

### 3. Kaynak dosyalar pakete YALNIZ yayın derlemesinde kopyalanır

`bundle.targets` listesine `nsis` ve `msi` eklendi; `deb` ve `appimage` kaldı.

Ama `bundle.resources` **ana yapılandırmada durmaz**, ayrı bir örtü dosyada
durur: `apps/desktop/src-tauri/tauri.release.json`. Betikler `cargo tauri
build --config tauri.release.json` ile çağırır.

Gerekçe ölçülmüştür: `tauri-build` her kaynak dosya için `rerun-if-changed`
damgası basar. `backend/src` ve `modules` (32 MB, içinde `.pyc`) ana
yapılandırmada olsaydı, Python her çalıştığında `__pycache__` tazelenir ve
`launch-desktop.sh` her açılışta yeniden derlerdi — 40 saniye. Ayrıca
`tauri-build`, var olmayan bir kaynak yolunda **derlemeyi durdurur**; `runtime/`
ise geliştirme makinesinde yoktur.

`config/local.yaml` bilerek kopyalanmaz (K8): kaynak listesi `config/default.yaml`
dosyasını **tek tek** sayar, `config/` klasörünü toptan almaz. Sır taşıyan bir
dosyanın kuruculara girmesi, bir gözden kaçmaya bakardı.

### 4. Paketleme betikleri iki tanedir, çapraz derleme yoktur

- `scripts/build-release.sh` — Linux ve macOS
- `scripts/build-release.ps1` — Windows

Her biri **çalıştığı platformun** paketini üretir. Tauri'nin Windows kurucusu
NSIS/WiX zincirine, macOS paketi imzalama araçlarına bağlıdır; çapraz derleme
bu iki zincirin de taklit edilmesini isterdi.

Betikler bağımlılık listesi **yazmaz**, türetir: `backend/pyproject.toml` ve
her modülün `module.yaml` dosyası (K11). Windows'ta `printer` extra'sı listeden
düşer — orada CUPS yoktur, baskı işletim sistemine devredilir (ADR 0014) ve
`pycups` zaten derlenmez.

### 5. Windows kurucusu imzalanmaz

Kod imzalama sertifikası alınmadı. Kurulum kendi şirket makinelerine yapılıyor;
kurucuyu indirip çalıştıran kişi onu üreten ekiple aynı kurumda. SmartScreen
uyarısının nasıl geçileceği `deploy/README.md` içinde yazılıdır.

## Elenen alternatifler

- **Sistem Python'una güvenmek.** En ucuz yol: kurucu Python istemez, `python3`
  neredeyse çağrılır. Elendi çünkü kurulum makinesinde Python olmayabilir,
  olan sürüm 3.12'den eski olabilir ve paketlerimiz (`fastapi`, `argon2-cffi`,
  `aiomysql`) orada bulunmaz. Onları sistem Python'una kurmak, kullanıcının
  makinesindeki başka bir Python kurulumunu bozma riskini bize yükler.
  Windows'ta ayrıca "Microsoft Store'dan Python yükleyin" ekranı çıkar ve
  uygulama kurulumu oradan devam edemez.
- **PyInstaller ile tek dosya.** Çekirdeği tek `.exe` yapmak paketlemeyi
  basitleştirirdi. Elendi: modül sistemi **dinamik keşfe** dayanır (ADR 0003 —
  `modules/*/module.yaml` okunur, router çalışma anında montajlanır) ve
  PyInstaller'ın statik import çözümlemesi bunu göremez; her modül için elle
  `hiddenimports` yazılırdı ve K6 ("yeni modül = klasör, çekirdekte tek satır
  değişmez") ilk modülde çökerdi. Tek dosya kip ayrıca her açılışta kendini
  geçici klasöre açar; antivirüs yazılımlarının en sevmediği davranıştır ve
  bizim kendi antivirüs modülümüz olan bir üründe bu kötü bir şaka olurdu.
- **Depo içi `data/` bırakmak.** Kurulum klasörünü yazılabilir yapmak
  (Windows'ta `Program Files` yerine `%LOCALAPPDATA%\Programs`) teknik olarak
  mümkündü ve tek bir klasörde her şey dururdu. Elendi: makinede iki kullanıcı
  varsa veritabanı ortaklaşır ve izinler karışır; uygulamayı kaldırmak
  veritabanını da siler; güncelleme kurucusu veri klasörünün üzerine yazar.
  Kullanıcı verisinin uygulama ikilisinden ayrı durması, üç platformun da
  kendi kılavuzunda yazdığı kuraldır.
- **Veri dizinini ayardan okumak (`core.data_path`).** Ayarın kendisi ayar
  dosyasından okunuyor, ayar dosyası da bir yerde duruyor: yumurta-tavuk.
  `KM_DATA_DIR` ortam değişkeni acil müdahale ve test için yeterli kaçış
  yoludur.

## Sonuçlar

- **Geliştirme akışı değişmez.** Depoda `.git` var, `runtime/` yok, `.venv`
  var: üç çözüm de bugünkü yanıtı verir. `launch-desktop.sh` aynen çalışır.
- **Testler değişmez.** Depo kökünde `.git` bulunduğu için `data/` yine depo
  içidir; testlerin çoğu zaten mutlak `tmp_path` veriyor.
- **`config/local.yaml` hâlâ paket kökünden okunur.** Kurulu Windows
  makinesinde o dosyayı yazmak yönetici hakkı ister. Bugün acil değil —
  ayarların çoğu Sistem Ayarları ekranından çekirdek deposuna (veri dizinine)
  yazılıyor ve sırlar kasada (`data/secret.key`) duruyor; `local.yaml` yalnız
  ilk açılış PIN'i ve sunucu erişimleri için gerekiyor. **Sırada duran iş
  budur** ve ayrı bir kararla çözülür.
- **`km_core/http/settings.py` içindeki çıktı klasörü yedeği hâlâ
  `config.root / "data" / "exports"` diyor.** Raporlar normalde
  `Masaüstü/Kontrol Merkezi/Raporlar` altına gider (`reports_root`), bu yol
  yalnız masaüstü klasörü bulunamazsa devreye girer — o durumda kurulu
  uygulamada yazılamayan bir yere düşer. Tek satırlık düzeltmedir, bu ADR'nin
  kapsamı dışında bırakıldı.
- **Paket boyutu ~100 MB artar.** Gömülü CPython yaklaşık bu kadardır.
  Karşılığında kurulum makinesinde hiçbir ön koşul kalmaz.
- **Gömülü Python sürümü elle yükseltilir.** Betiklerin başındaki iki değişken
  (`KM_PY_VERSION`, `KM_PBS_RELEASE`) tek kaynaktır ve iki betikte AYNI
  kalmalıdır; ayrışırlarsa yalnız bir platformda görülen hatalar doğar.
- **Paketler CI'da üretilir.** Çapraz derleme olmadığı için (§4) geliştirme
  makinesi üç platformun paketini veremez: burası Linux, `.exe`/`.msi` ve
  `.dmg` orada çıkmaz. `.github/workflows/release.yml` üç koşucuyu yan yana
  çalıştırır ve her biri **bu ADR'nin betiklerini** çağırır — iş akışı ayrı bir
  derleme reçetesi tutmaz, ayrışacak ikinci bir kaynak doğmaz. Elle tetiklenir
  ya da `v*` etiketiyle koşar; `main`'e her push'ta derlenmez. Kullanımı:
  `deploy/README.md`.
- **İmzasız kurucu SmartScreen uyarısı verir.** Sertifika alınana kadar her
  yeni sürümde "yine de çalıştır" adımı gerekir. Ticari sertifika kararı
  verildiğinde bu ADR yeni bir kararla güncellenir.
