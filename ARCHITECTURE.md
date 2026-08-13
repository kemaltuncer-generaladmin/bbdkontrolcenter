# Kontrol Merkezi — Mimari (SABİT)

Bu belge bağlayıcıdır. Buradaki kararlar, açıkça değiştirilmediği sürece
tartışmaya kapalıdır. Değişiklik ancak `docs/adr/` altına yeni bir ADR
eklenerek ve eskisi "Superseded" işaretlenerek yapılır.

---

## 1. Katmanlar

Sistem üç katmandan oluşur. Bağımlılık **tek yönlüdür** ve asla ters çevrilemez:

```
        modules/          (iş modülleri — özellik)
             │  import eder
             ▼
        km_sdk            (kararlı yüzey — modüllerin görebildiği TEK paket)
             │  yeniden dışa vurur
             ▼
   km_core  +  km_platform (çekirdek + paylaşılan yetenekler)
```

**Kural K1 — Çekirdek modülü bilmez.**
`km_core` ve `km_platform` içinde hiçbir modül adı, modül importu veya
modüle özel `if` bulunamaz. `modules/` klasörünü tamamen silseniz bile
çekirdek ayağa kalkmalıdır.

**Kural K2 — Modül çekirdeği doğrudan import etmez.**
Modüller yalnızca `km_sdk` import eder. `from km_core...` veya
`from km_platform...` yazan bir modül hatalıdır. SDK, çekirdeği modülden
yalıtan sözleşme katmanıdır; çekirdek içi yeniden düzenleme modülleri kırmaz.

**Kural K3 — Modül modülü import etmez.**
Modüller birbirini yalnızca iki yoldan görür: `registry` üzerinden
(manifest'te `consumes` ile ilan edilmiş yetenek) veya `event bus` üzerinden.
Doğrudan `from modules.x` importu yasaktır.

---

## 2. Çekirdek (`backend/src/km_core/`)

Kernel. İş bilgisi taşımaz — sadece "modüller nasıl bulunur, yüklenir,
birbirine nasıl bağlanır" sorusunu çözer.

| Klasör | Sorumluluk |
|---|---|
| `kernel/` | Modül keşfi, manifest doğrulama, bağımlılık sıralaması, yaşam döngüsü (`load → setup → start → stop`) |
| `contracts/` | Modül sözleşmesi (Protocol/ABC), manifest modeli, yetenek arayüzleri |
| `registry/` | Servis/yetenek kayıt ve çözümleme (DI konteyneri) |
| `bus/` | Olay veri yolu (publish/subscribe), modüller arası gevşek bağ |
| `config/` | Katmanlı ayar yükleme (varsayılan → ortam → yerel → modül) ve şema doğrulama |
| `http/` | FastAPI uygulama fabrikası, router montajı, tekdüze hata biçimi |
| `store/` | Çekirdeğin **kendi** metadata veritabanı (modül kaydı, ayar, denetim izi) |
| `security/` | Kimlik doğrulama, yetki, sır (secret) erişim politikası, denetim |
| `logging/` | Yapısal log, korelasyon kimliği |
| `tasks/` | Arka plan işi / zamanlanmış görev çalıştırıcısı |

> `store/` yerel metadata içindir. Yönetilen uzak veritabanları (BBD/BLD)
> `km_platform/database/` sorumluluğundadır. Bu ikisi karıştırılmaz.

---

## 3. Platform (`backend/src/km_platform/`)

**Paylaşılan yetenekler.** Bunlar modül DEĞİLDİR — kaldırılamazlar,
uygulamanın altyapısıdır. Modüller bunları tüketir.

| Klasör | Sorumluluk |
|---|---|
| `ssh/` | Sunucu envanteri, bağlantı havuzu, uzak komut çalıştırma, dosya aktarımı, tünel. **SSH'a ihtiyaç duyan her şey buraya bağlanır.** |
| `database/` | Yönetilen veritabanlarına erişim. `engines/` sürücü sarmalayıcıları (MySQL/MariaDB/PostgreSQL), `bbd/` Bagisto çekirdekli BBD şema adaptörü, `bld/` Laravel tabanlı BLD şema adaptörü |
| `printer/` | CUPS erişimi: yazıcı keşfi, kuyruk, iş gönderimi, durum |
| `audio/` | Ses aygıtı ve çalma (zil sisteminin dayandığı katman) |
| `scheduler/` | Takvim/cron soyutlaması |
| `secrets/` | Kimlik bilgisi kasası (SSH anahtarı, DB parolası) — düz metin yok |
| `notify/` | Bildirim kanalları (e-posta, webhook, masaüstü) |

**Kural K4 — Tek kapı.** Bir yeteneğe erişim yalnızca kendi platform
paketinden geçer. Bir modülde ham `paramiko`/`asyncssh` çağrısı ya da
doğrudan DB sürücüsü kullanımı yasaktır; `ssh` ve `database` yetenekleri
üzerinden gider. Bağlantı havuzu, kimlik, denetim ve hız sınırı orada tutulur.

---

## 4. Modül (`modules/<id>/`)

Modül = **dikey dilim halinde bir iş özelliği.** Örnek: "BLD ürün yönetimi",
"zil sistemi", "baskı yönetimi".

Bir modül kendi backend'ini, kendi arayüz panelini, kendi göçlerini, kendi
ayar şemasını ve kendi testlerini taşır. **Klasörü silmek özelliği tümüyle
kaldırır** — geride hiçbir iz kalmaz. Bu, "saf modüler"in ölçütüdür.

```
modules/<id>/
├── module.yaml              # kimlik + sözleşme (zorunlu)
├── backend/
│   ├── module.py            # giriş noktası: register(ctx)
│   ├── api/                 # HTTP router'ları
│   ├── services/            # iş kuralları
│   ├── repositories/        # veri erişimi
│   ├── schemas/             # istek/yanıt modelleri
│   ├── tasks/               # arka plan işleri
│   └── migrations/          # modülün kendi tabloları
├── ui/
│   ├── panel/               # masaüstü kabuğuna dinamik yüklenen arayüz
│   └── locales/             # modülün kendi metinleri
├── config/                  # default.yaml + schema.json
└── tests/
```

**Kural K5 — Modül tabloları izole.** Her modül yalnızca kendi göçleriyle
oluşturduğu tablolara yazar. Başka modülün tablosunu okumak/yazmak yasaktır;
veri paylaşımı servis veya olay üzerinden olur.

---

## 5. Modül keşfi ve yükleme

1. Çekirdek açılışta `modules/*/module.yaml` dosyalarını tarar.
2. Manifest `docs/schemas/module.schema.json` ile doğrulanır; geçersiz manifest
   → modül yüklenmez, hata loglanır, **uygulama yine ayağa kalkar.**
3. `depends` alanından bağımlılık grafiği kurulur, topolojik sıraya dizilir.
   Döngü → yükleme reddedilir.
4. `consumes` içindeki her yetenek registry'de aranır; eksikse modül devre dışı
   bırakılır ve nedeni raporlanır.
5. Sırayla `register(ctx)` çağrılır. Modül burada servislerini registry'ye
   yazar, router'ını, olay dinleyicilerini ve görevlerini bildirir.
6. `enabled: false` olan modül hiç yüklenmez.

**Kural K6 — Modül eklemek çekirdeğe dokunmayı gerektirmez.** Yeni bir özellik
= `modules/` altına bir klasör atmak. Çekirdekte tek satır değişmez.

**Kural K7 — İzolasyon.** Bir modülün yüklenirken veya çalışırken patlaması
diğerlerini ve çekirdeği düşürmez. Hata sınırı modül düzeyindedir.

---

## 6. Masaüstü kabuk (`apps/desktop/`)

Tauri 2 kabuğu. İçinde çalışan Python çekirdeği (FastAPI) yerel bir sidecar
sürecidir; arayüz ona `127.0.0.1` üzerinden konuşur.

`ui-kernel/`, backend'in modül kayıt uç noktasını okuyup her modülün
`ui/panel/` girişini dinamik yükler ve menüye yerleştirir. **Kabukta modül
adı geçmez** — K1'in arayüz tarafındaki karşılığıdır.

Kabuk seçimi (Tauri ↔ Electron) `apps/desktop/` içinde izoledir; değişirse
backend, platform ve modüller etkilenmez.

---

## 7. Ayar ve sır

Ayar öncelik sırası (sonraki öncekini ezer):

```
config/default.yaml → config/environments/<env>.yaml → config/local.yaml → ortam değişkenleri
```

Modül ayarı `modules/<id>/config/default.yaml` içinde durur, kök ayardaki
`modules.<id>.*` bloğuyla ezilir. Her modül `config/schema.json` sağlar;
şemaya uymayan ayar açılışta hata verir.

**Kural K8 — Depoda sır bulunmaz.** SSH anahtarı, DB parolası, token
`config/local.yaml` (git dışı) veya `km_platform/secrets` kasasından gelir.

---

## 8. Kimlik ve yetkilendirme

Kimlik çekirdeğe aittir (`km_core/security` + `km_core/store`). Modül değildir,
kapatılamaz.

Ayrıntı: [docs/identity-model.md](docs/identity-model.md) ·
İzin kataloğu ve rol matrisi: [docs/permissions.md](docs/permissions.md) ·
Gerekçe: [ADR 0007](docs/adr/0007-kimlik-ve-yetkilendirme.md)

**Giriş.** Kullanıcı adı yoktur. Kişiye özel PIN hem girişi hem kimliği
belirler. PIN benzersizdir, en az 6 hanedir, Argon2id ile hash'lenir. Deneme
sınırı, kilitlenme ve denetim izi sözleşmenin zorunlu parçasıdır.

**Roller.** Rol = izin kümesi. Ön tanımlı dört rol: `admin`, `bld_staff`,
`bbd_staff`, `org_staff`. Yeni rol tanımlanabilir. **Bir kullanıcıya birden
fazla rol atanabilir**; etkin izinler rollerin birleşimidir.

**Kapsam.** İzinler kapsamlıdır: `database.query:bld`. Kapsam değerleri `bbd`,
`bld`, `org`, tümü için `*`. BLD/BBD ayrımı bu mekanizmayla kurulur — ayrı kod
yolu yoktur.

**Kural K9 — Çift kapı.** Arayüzde gizlemek yetkilendirme değildir. Ekran
görünürlüğü `module.yaml` içindeki `ui.nav.requires` ile belirlenir; aynı işlem
backend'de de izin denetiminden geçer. İzin ilan etmeyen uç nokta reddedilir
(varsayılan: kapalı).

**Kural K10 — Rol adı sorulmaz.** Kodda `if role == "admin"` biçiminde dal
yazılamaz; her yerde `has_permission(key, scope)` sorulur. Modüller izinlerini
`module.yaml` içinde ilan eder, çekirdek yalnızca uygular.

---

## 9. Bağımlılıklar

Ayrıntı: [ADR 0008](docs/adr/0008-bagimlilik-yonetimi-ve-surucu-politikasi.md)
· Sistem paketleri: [deploy/packaging/system-packages.yaml](deploy/packaging/system-packages.yaml)
· Python: [backend/pyproject.toml](backend/pyproject.toml)

**Kural K11 — Bağımlılık ilan edilir, kopyalanmaz.** Sürücüler, kütüphaneler ve
ikili dosyalar depoya konmaz; neyin gerektiği ilan edilir. Yazıcı sürücüleri
(`hplip`, `printer-driver-hpcups`) apt'tan gelir. Tek istisna, dağıtımda
bulunmayan modellerin PPD metin dosyalarıdır.

İlan üç yerde yapılır ve `scripts/install-deps.sh` hepsini toplar:

| Tür | Yer |
|---|---|
| Çekirdek + platform Python paketleri | `backend/pyproject.toml` — yetenek başına extra |
| Sistem (apt) paketleri | `deploy/packaging/system-packages.yaml` — yetenek başına grup |
| Modülün kendi bağımlılıkları | `modules/<id>/module.yaml` → `dependencies` |

Modül kendi bağımlılığını kendisi getirir; çekirdeğin listesine dokunulmaz (K6).

---

## 10. Sabitlenen teknoloji kararları

| Konu | Karar | ADR |
|---|---|---|
| Çekirdek dili | Python 3 + FastAPI | 0001 |
| Arayüz | Tauri 2 masaüstü kabuğu + Python sidecar | 0002 |
| Modül sistemi | Manifest + dinamik keşif | 0003 |
| Bağımlılık yönü | Çekirdek modülü bilmez | 0004 |
| Modül sınırı | Dikey dilim (backend+ui+göç+ayar+test) | 0005 |
| ssh / database | Platform yeteneği, modül değil | 0006 |
| Kimlik ve yetki | PIN ile giriş, izin tabanlı çok rollü model | 0007 |
| Bağımlılıklar | İlan edilir, kopyalanmaz; sürücüler apt'tan | 0008 |
| Panel bileşenleri | Ortak kit kabukta, tek kopya (`shell/ui-kit/`) | 0011 |
| Mağaza yıkıcı işlemi | PIN değil, gerekçeli onay + kuru prova | 0012 |
