# Kontrol Merkezi — Çalışma Kuralları

## Çalışma biçimi

**Aksi söylenmedikçe ileri hamle yapma.** İstenen adım neyse yalnızca o yapılır.
Bir sonraki aşamaya geçmek, "hazır olmuşken" ek dosya/kod eklemek, iskeleti
kendiliğinden doldurmak yasaktır. Emin olunmayan noktada sorulur.

## Bağlayıcı mimari

Tam metin: [ARCHITECTURE.md](ARCHITECTURE.md) · Kararlar: [docs/adr/](docs/adr/)

Bu kararlar sabittir. Değişiklik ancak yeni bir ADR ile olur — kod içinde
"geçici" istisna açılmaz.

| # | Kural |
|---|---|
| K1 | `km_core` ve `km_platform` içinde modül adı, modül importu veya modüle özel dal bulunamaz. `modules/` silinse bile çekirdek ayağa kalkar. |
| K2 | Modüller yalnızca `km_sdk` import eder. `from km_core...` / `from km_platform...` yazan modül hatalıdır. |
| K3 | Modül modülü import etmez. İletişim yalnızca registry (`provides`/`consumes`) veya olay veri yolu üzerinden. |
| K4 | Tek kapı: modülde ham `asyncssh`/`paramiko` veya doğrudan DB sürücüsü çağrısı yasak. Erişim `ssh` / `database` platform yeteneğinden geçer. |
| K5 | Her modül yalnızca kendi göçleriyle oluşturduğu tablolara yazar. Başka modülün tablosunu okumak yasak. |
| K6 | Yeni özellik = `modules/` altına klasör. Çekirdekte tek satır değişmez. |
| K7 | Bir modülün patlaması diğerlerini ve çekirdeği düşürmez. |
| K8 | Depoda sır bulunmaz. Anahtar/parola `config/local.yaml` (git dışı) veya `km_platform/secrets` kasasından gelir. |
| K9 | Çift kapı: arayüzde gizlemek yetkilendirme değildir. Her korunan işlem backend'de de izin denetiminden geçer. İzin ilan etmeyen uç nokta reddedilir. |
| K10 | Rol adı sorulmaz. `if role == "admin"` yasak; her yerde `has_permission(key, scope)`. |
| K11 | Bağımlılık ilan edilir, kopyalanmaz. Sürücü/kütüphane/ikili depoya konmaz; `pyproject.toml`, `system-packages.yaml` veya modülün `dependencies` bloğunda ilan edilir. |

## Kavram ayrımı — karıştırılmaz

- **Modül** = silinebilir iş özelliği (zil sistemi, baskı yönetimi, BLD ürün
  yönetimi). `modules/` altında, `module.yaml` taşır, kapatılabilir.
- **Platform yeteneği** = silinemez altyapı (`ssh`, `database`, `printer`,
  `audio`, `scheduler`, `secrets`, `notify`). `km_platform/` altında, manifest
  taşımaz, kapatılamaz. **SSH gereken her şey buraya bağlanır.**
- `km_core/store` = çekirdeğin kendi metadata deposu.
  `km_platform/database` = yönetilen uzak veritabanları (BBD/BLD). Ayrı şeyler.
- **Kimlik çekirdektedir**, modül değildir. Giriş kullanıcı adsız, PIN iledir.
  Rol = izin kümesi; bir kullanıcı **birden fazla rol** taşıyabilir, etkin
  izinler bunların birleşimidir. İzinler kapsamlıdır (`database.query:bld`).
- Kullanıcının `org_scope` alanı nereye bağlı olduğunu söyler, **yetkiyi
  belirlemez.** Yetki yalnızca rollerden gelir.

## Kimlik ve yetki

Katalog ve rol → ekran matrisi: [docs/permissions.md](docs/permissions.md)
· Veri modeli: [docs/identity-model.md](docs/identity-model.md)
· Gerekçe: [ADR 0007](docs/adr/0007-kimlik-ve-yetkilendirme.md)

Ön tanımlı roller: `admin`, `bld_staff`, `bbd_staff`, `org_staff`.
Modül izinlerini `module.yaml` içinde ilan eder; çekirdek yalnızca uygular.
Yıkıcı işlemler izin yeterli olsa bile PIN teyidi ister.

## Teknoloji

- Çekirdek: Python 3 + FastAPI · Arayüz: Tauri 2 kabuğu + Python sidecar
- BBD = Bagisto çekirdekli · BLD = Laravel tabanlı — adaptörleri
  `km_platform/database/bbd/` ve `.../bld/` altında.

## Projenin şu anki durumu

Kod içeren tek alan: `backend/src/km_platform/notify/` — SMS/bildirim katmanı,
çalışır durumda, 36 test. Geri kalan her şey sözleşmesi sabitlenmiş iskelettir:
`km_core`, `km_sdk` ve diğer platform yetenekleri yazılmadı, modüller
`enabled: false`.

Proje henüz kurulabilir paket değil; `tests/conftest.py` kaynak dizinini
`sys.path`'e ekler.

## Komutlar

Tümü **depo kökünden** çalıştırılır — `pytest.ini` ve `ruff.toml` kökte durur.

```bash
.venv/bin/python -m pytest       # testler
.venv/bin/ruff check .           # lint
.venv/bin/mypy backend/src       # tip denetimi
scripts/install-deps.sh          # bağımlılıklar (üç kaynaktan toplar)
```

Testler ağa çıkmaz; dış servisler taklit edilir. Gerçek SMS gönderen test
yazılmaz — SMS katmanında `dry_run` varsayılan açıktır.

Ayrıntı: [docs/development-guide.md](docs/development-guide.md)

## Yeni modül eklerken

[docs/module-guide.md](docs/module-guide.md) izlenir. `tools/module-template/`
kopyalanır, `module.yaml` doldurulur. Çekirdeğe dokunulmaz.

Bağımlılık da modülün kendi `dependencies` bloğuna yazılır; çekirdeğin
listesine dokunulmaz (K6, K11).

## Dil

Belgeler, arayüz metinleri ve commit mesajları Türkçe. Kod (tanımlayıcılar,
dosya/klasör adları, API yolları) İngilizce ve ASCII.
