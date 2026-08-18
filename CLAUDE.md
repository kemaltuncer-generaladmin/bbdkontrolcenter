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

- **Modül** = silinebilir iş özelliği (zil sistemi, çıktı merkezi, BLD ürün
  yönetimi). `modules/` altında, `module.yaml` taşır, kapatılabilir.
- **Platform yeteneği** = silinemez altyapı (`ssh`, `database`, `printer`,
  `audio`, `scheduler`, `secrets`, `notify`). `km_platform/` altında, manifest
  taşımaz, kapatılamaz. **SSH gereken her şey buraya bağlanır.**
- `km_core/store` = çekirdeğin kendi metadata deposu.
  `km_platform/database` = yönetilen uzak veritabanları (BBD/BLD). Ayrı şeyler.
- **Kimlik çekirdektedir**, modül değildir. Giriş kullanıcı adsız, 6 haneli
  **PIN** iledir (ADR 0007; 0016 reddedildi — kodda kalan `password_*` adları
  göçten kalmıştır, kural PIN'dir).
  Rol = izin kümesi; bir kullanıcı **birden fazla rol** taşıyabilir, etkin
  izinler bunların birleşimidir. İzinler kapsamlıdır (`database.query:bld`).
- Kullanıcının `org_scope` alanı nereye bağlı olduğunu söyler, **yetkiyi
  belirlemez.** Yetki yalnızca rollerden gelir.

## Kimlik ve yetki

Katalog ve rol → ekran matrisi: [docs/permissions.md](docs/permissions.md)
· Veri modeli: [docs/identity-model.md](docs/identity-model.md)
· Gerekçe: [ADR 0007](docs/adr/0007-kimlik-ve-yetkilendirme.md)

Ön tanımlı roller: `admin`, `bld_staff`, `bbd_staff`, `org_staff`, `accountant`
(`km_core/security/identity.py` → `BUILTIN_ROLES`).
Modül izinlerini `module.yaml` içinde ilan eder; çekirdek yalnızca uygular.
Yıkıcı işlemler izin yeterli olsa bile PIN teyidi ister.

## Teknoloji

- Çekirdek: Python 3 + FastAPI · Arayüz: Tauri 2 kabuğu + Python sidecar
- BBD = Bagisto çekirdekli · BLD = Laravel tabanlı — adaptörleri
  `km_platform/database/bbd/` ve `.../bld/` altında.

## Mimari — ADR 0026 ile değişti (18.08.2026)

**Backend artık SUNUCUDA koşar; masaüstü uygulaması ince kabuktur.**

- Veri Coolify'daki **PostgreSQL**'dedir. Kurulumların kendi veritabanı YOKTUR.
- Sunucu imajı: `deploy/server/Dockerfile` (çekirdek + 49 modül).
- Kabuk adresi Rust tarafında tek yerde (`server_base`); `KM_SERVER_URL` ile
  aşılır, `local` yazmak eski yerel davranışa döndürür.
- Sidecar YALNIZ yerel kipte başlar.
- **İnternet kesilirse uygulama durur** — kullanıcı kararı.

Depo iki motorludur: `Store` (SQLite, yerel/test) ve `PostgresStore` (merkez).
Şemayı kuran tek yer `km_core/store/bootstrap.py`; lehçe farkını
`km_core/store/dialect.py` kapatır ve **tanımadığı yapıyı reddeder**.

Motor `KM_STORE_ENGINE` ile seçilir (`sqlite` varsayılan). Sunucuda ayrıca
`KM_CENTRAL_DSN` ve **`KM_SECRET_KEY` zorunludur** — ikincisi verilmezse kasa
kendine yeni anahtar üretir, sırların hiçbirini çözemez ve hiç kimse giriş
yapamaz; belirti sebebi ele vermez.

## Projenin şu anki durumu

Çekirdek (`km_core`), SDK ve platform yeteneklerinin çoğu (`audio`,
`scheduler`, `secrets`, `notify`, `printer`) yazıldı ve çalışıyor; sidecar
ayağa kalkıyor, kabuk modülleri dinamik yüklüyor. `modules/` altındaki
**49 modülün hepsi** `enabled: true`; iskelet olarak kapatılmış modül kalmadı.

`print` (Çıktı Merkezi — ADR 0019) ve `antivirus` (ADR 0009/0022) da yazıldı:
ikisinin de backend'i, paneli ve testleri var. `antivirus` yalnız Linux'ta
yüklenir (`platforms: [linux]`).

**Merkezî kimlik servisi canlıdır** (ADR 0021): `services/identity/`, Coolify
üzerinde `https://kontrolmerkezi.bbdstore.com.tr`. Adres pakete gömülü gelir
(`config/default.yaml` → `platform.identity_sync.base_url`) ve şalter açıktır.
Eşleme, kadro göçü, merkezin kimlik anahtarının (pepper) benimsenmesi ve
kurulumun kendi bozukluğunu görüp onarması (`/api/pairing/reset`) çalışır
durumdadır; yeni bir makine merkezden alınan 8 haneli tek kullanımlık kodla
eşlenir. Sıfırdan kurulumun tam sırası: [deploy/README.md](deploy/README.md)
→ "Yeni cihaz kurulumu".

Çekirdek ekranları kabukta ayrı hiyerarşide durur (ADR 0017):
`apps/desktop/shell/core-panels/`. Bugün **dört** çekirdek ekranı ilan edilir
(`shell/ui-kernel.js` → `CORE_PANELS`, backend karşılığı
`km_core/http/app.py` → `CORE_PANELS_UI`): **Kullanıcı Yönetimi**, **Sistem
Ayarları**, **Sistem Sağlığı** ve **KM Cihaz Eşle**. Üçünün paneli yazılmıştır;
Sistem Sağlığı menüde durur ama `entry` alanı hâlâ boştur — gövdesinde "ekranı
henüz yok" kartı çıkar. Roller ve İzinler / Denetim İzi / Kimlik Kasası ise
henüz kabuğun listesine girmemiştir.

KM Cihaz Eşle ekranı kurulumun merkezden ne aldığını da yazar (kadro
revizyonu, dağıtılan ayar/sır sayısı, son tazeleme) ve **Şimdi tazele**
düğmesini `POST /api/pairing/refresh` ucuna bağlar. **Bu uç henüz
yazılmamıştır**; yokluğunda ekran 404'ü ham hata olarak göstermez, sebebini
söyler.

> Bu bölüm eskimeye açıktır. Bir modülün gerçek durumu tek yerden okunur:
> kendi `module.yaml` dosyasındaki `enabled` alanı ve klasöründeki kod.

Proje henüz kurulabilir paket değil; `tests/conftest.py` kaynak dizinini
`sys.path`'e ekler.

## Komutlar

Tümü **depo kökünden** çalıştırılır — `pytest.ini` ve `ruff.toml` kökte durur.

```bash
.venv/bin/python -m pytest         # testler
.venv/bin/ruff check .             # lint
.venv/bin/python -m mypy backend/src --config-file backend/pyproject.toml   # tip
scripts/install-deps.sh            # bağımlılıklar (üç kaynaktan toplar)
```

Tip denetimi `-m` ile çağrılır: `.venv/bin/mypy` yorumlayıcı yolunu kurulum
anında gömen bir sarmalayıcıdır ve o sarmalayıcı bozulduğunda/eskidiğinde kapı
mypy hiç koşmadan da geçmiş görünebilir — `-m` biçimi denetimi sanal ortamın
kendi yorumlayıcısıyla çalıştırır ve çıkış kodu gerçekten mypy'den gelir.

`--config-file` ŞARTTIR ve komuttan düşürülmez. mypy ayarını
(`[tool.mypy] strict = true`) `backend/pyproject.toml` taşıyor; mypy
yapılandırmayı **çalışılan dizinde** arıyor ve depo kökünde `pyproject.toml`
yok. Bayrak olmadan komut katı kip olmadan koşar ve **geçmiş görünür**.
18.08.2026'da bayrak eklenip biriken 14 hata kapatıldı; kapı artık gerçek.

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
