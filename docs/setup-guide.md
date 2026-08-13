# Kurulum Kılavuzu

Sıfırdan bir Ubuntu makinesinde çalışır hale getirme. Hedef: Ubuntu 24.04+
(geliştirme makinesinde 26.04 LTS, Python 3.14 ile doğrulandı).

---

## 1. Sistem paketleri

```bash
sudo apt-get update
sudo apt-get install -y \
  python3-venv python3-dev build-essential \
  mariadb-client libcups2-dev pulseaudio-utils \
  clamav clamav-daemon clamav-freshclam
```

Ne işe yaradıkları: [deploy/packaging/system-packages.yaml](../deploy/packaging/system-packages.yaml)

Zaten kurulu gelenler (kontrol edilir, tekrar kurulmaz): `cups`, `cups-client`,
`hplip`, `printer-driver-hpcups`, `pipewire`, `alsa-utils`, `openssh-client`.

> **Yazıcı sürücüleri depoya konmaz.** apt üzerinden gelir ve güncellemelerini
> oradan alır (K11 — [ADR 0008](adr/0008-bagimlilik-yonetimi-ve-surucu-politikasi.md)).

## 2. Python ortamı

```bash
cd "/home/kemaltuncer/Desktop/Kontrol Merkezi"
scripts/install-deps.sh
```

Betik `.venv` oluşturur ve bağımlılıkları **üç kaynaktan** toplar:
`backend/pyproject.toml`, `deploy/packaging/system-packages.yaml` ve her
modülün `module.yaml` içindeki `dependencies` bloğu.

Ne yapacağını önce görmek için:

```bash
scripts/install-deps.sh --dry-run
```

Masaüstü kabuğu da derlenecekse `--with-desktop` eklenir; o durumda ayrıca
Rust gerekir (`cargo`, apt ile değil [rustup](https://rustup.rs) ile kurulur).

## 3. ClamAV'ı ayağa kaldırma

İlk kurulumda `freshclam` yaklaşık **300 MB** imza indirir. Bu bitmeden
`clamav-daemon` başlamaz — kurulumdan hemen sonra servisin ölü görünmesi
normaldir.

```bash
sudo systemctl start clamav-daemon
systemctl is-active clamav-daemon        # active dönmeli
```

Motorun çalıştığını doğrulama (EICAR, antivirüs testi için tasarlanmış
zararsız standart dizi):

```bash
printf 'X5O!P%%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*' > /tmp/eicar.txt
clamscan /tmp/eicar.txt          # "Eicar-Test-Signature FOUND" beklenir
rm /tmp/eicar.txt
```

Daemon çalışmıyorsa tarama yine olur ama her seferinde 112 MB imza diskten
yüklenir — belirgin biçimde yavaştır.

## 4. Yerel ayar dosyası

Sırlar depoya girmez (K8). Makineye özel ayarlar için:

```bash
cp config/local.example.yaml config/local.yaml   # dosya yoksa elle oluşturun
```

`config/local.yaml` git dışıdır. Buraya girecekler: Netgsm kullanıcı
adı/parolası, veritabanı bağlantıları, SSH kimlikleri.

Ayar öncelik sırası:

```
config/default.yaml → config/environments/<env>.yaml → config/local.yaml → ortam değişkeni
```

## 5. Kurulumu doğrulama

```bash
.venv/bin/python -m pytest        # tüm testler
.venv/bin/ruff check .            # lint
```

Yeteneklerin sistemle gerçekten konuştuğunu görmek için:

```bash
# CUPS — yazıcılar ve varsayılan
.venv/bin/python -c "import cups; c=cups.Connection(); print(list(c.getPrinters()), c.getDefault())"

# Ses
paplay --version && aplay --version | head -1

# ClamAV
clamscan --version
```

---

## Bilinen durumlar

| Belirti | Sebep | Çözüm |
|---|---|---|
| `venv` kuruluyor ama `pip` yok | `python3-venv` (ensurepip) eksik | 1. adımdaki apt komutu |
| `clamav-daemon` inactive | İmzalar inmeden başlatılmış | `sudo systemctl start clamav-daemon` |
| `pycups` derlenmiyor | `libcups2-dev` veya `build-essential` eksik | 1. adımdaki apt komutu |
| `import netgsm` yanlış paketi buluyor | Bakımsız üçüncü taraf `netgsm` paketi kurulu | `pip uninstall netgsm && pip install netgsm-sms` |
| Tauri derlenmiyor | `cargo` yok | rustup ile kurun; çekirdek geliştirmesi için gerekmez |
