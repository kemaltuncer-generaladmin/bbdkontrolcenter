# 0008 — Bağımlılık yönetimi ve sürücü politikası

**Durum:** Kabul edildi · 2026-08-12

## Bağlam
Yazdırma, ses çalma, SSH, veritabanı erişimi ve SMS gönderimi dış yazılımlara
dayanıyor. Sürücülerin (özellikle HP yazıcı sürücülerinin) depo içine
konulması gündeme geldi.

Hedef makinenin taraması (2026-08-12, Ubuntu 26.04 LTS): `cups`, `cups-client`,
`hplip`, `printer-driver-hpcups`, `pipewire`, `alsa-utils`, `openssh-client`
zaten kurulu. HP LaserJet MFP M139-M142 USB'de bağlı ve sistem varsayılan
yazıcısı. `cargo`, `python3-pip`, `libcups2-dev`, `build-essential` yok.

## Karar

### 1. Bağımlılıklar ilan edilir, kopyalanmaz

**Kural K11.** Sürücüler, kütüphaneler ve ikili dosyalar depoya konmaz.
Depoya giren şey, neyin gerektiğinin **ilanıdır**:

| Tür | Nerede ilan edilir |
|---|---|
| Python paketleri (çekirdek + platform) | `backend/pyproject.toml`, yetenek başına extra |
| Sistem (apt) paketleri | `deploy/packaging/system-packages.yaml`, yetenek başına grup |
| Modülün kendi bağımlılıkları | `modules/<id>/module.yaml` → `dependencies` |

`scripts/install-deps.sh` bunları toplar ve kurar. **Yeni modül, çekirdeğin
bağımlılık listesine dokunmadan kendi bağımlılığını getirir** (K6 korunur).

### 2. Yazıcı sürücüleri apt'tan gelir

HP sürücüleri `hplip` ve `printer-driver-hpcups` paketleriyle dağıtımdan
alınır. Depoya kopyalanmaz.

Tek istisna: dağıtımda bulunmayan bir modelin **PPD dosyası**.
`backend/src/km_platform/printer/ppd/` altına konur, kaynağı belgelenir.
PPD küçük bir metin tanım dosyasıdır — sürücünün kendisi değildir.

### 3. Varsayılan yazıcı ayardır, kod değildir

Varsayılan yazıcı `config/default.yaml` içinde `platform.printer.default_printer`
ile belirlenir; şu an `HP_LaserJet_MFP_M139-M142`. Yazıcılar rolleriyle
tanımlıdır (`default`, `receipt`, `secondary`), böylece fiş baskısı 80mm termal
yazıcıya, belge baskısı HP'ye gider — kodda yazıcı adı geçmez.

### 4. Ses çalma pipewire üzerinden

Sistemde pipewire + wireplumber çalışıyor. Birincil çalma yolu `paplay`
(`pulseaudio-utils`); ses sunucusu düşerse `aplay` (ALSA) yedeğe geçer.
ALSA'ya doğrudan yazmak aygıtı tekelci kilitleyebileceği için birincil yol
değildir.

### 5. SMS için Netgsm'in resmi SDK'sı

`netgsm-sms` kullanılır — Netgsm tarafından yayımlanan, MIT lisanslı, güncel
(2025-03) paket. PyPI'daki `netgsm` paketi üçüncü taraftır ve 2023'ten beri
bakımsızdır; kullanılmaz.

SDK senkron çalışır (requests tabanlı); `notify` yeteneği içinde iş parçacığı
havuzunda sarmalanır, olay döngüsü bloklanmaz. Netgsm kimlik bilgileri kasadan
gelir, ayar dosyasına yazılmaz (K8).

## Gerekçe
- Depoya kopyalanan sürücü güvenlik güncellemelerinden kopar, mimariye bağımlı
  hale gelir ve GPL yeniden dağıtım yükümlülüğü doğurur. Zaten kurulu ve
  dağıtım tarafından bakılan bir paketi kopyalamanın kazancı yoktur.
- Bağımlılığın yetenek/modül başına ilan edilmesi, "modül eklemek çekirdeğe
  dokunmaz" kuralının bağımlılık tarafındaki karşılığıdır. Tek bir merkezi
  liste bu kuralı ilk günde bozardı.

## Sonuçlar
- `pycups` derleme gerektirir: `libcups2-dev` + `build-essential` sistem
  bağımlılığı olarak ilan edildi.
- Tauri kabuğu için `cargo` gerekir; apt ile değil `rustup` ile kurulur ve
  yalnızca masaüstü derlemesi yapılacaksa gerekir.
- Yeni bir yazıcı modeli PPD gerektirirse, PPD eklenir ama sürücü paketi
  `system-packages.yaml` içine yazılır.
