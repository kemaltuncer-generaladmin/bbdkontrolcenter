# Merkezî kimlik servisi

Kontrol Merkezi'nin **kadro** (kullanıcı, rol, izin), **cihaz eşlemesi**,
**denetim izi** ve **danışma kilidi** sunucusu. Karar: [ADR 0021](../../docs/adr/0021-merkezi-kimlik-ve-cihaz-eslemesi.md)
· Kilit sözleşmesi: [ADR 0020](../../docs/adr/0020-eszamanli-duzenleme.md)

> **Kırmızı çizgi.** Bu servis ayağa kalkmadan ikinci Kontrol Merkezi kurulumu
> yapılmaz. Sonraya bırakılırsa PIN'ler ve denetim izleri iki makineye bölünür;
> birleştirmek elle iştir.

Bu **ayrı bir uygulamadır**: kendi veritabanı, kendi dağıtımı, kendi adresi.
bbdstore'un içine konmadı — gerekçeler ADR 0021 §1'de.

---

## Adres ve barındırma

| | |
|---|---|
| Adres | `kontrolmerkezi.bbdstore.com.tr` |
| Sunucu | BLD sunucusu |
| Konteyner portu | 8000 |

**DNS.** `kontrolmerkezi` A kaydı **BLD sunucusunun** IP'sine yönlendirilir.
`api.bbdstore.com.tr` **yerinde bırakılır** ve dokunulmaz: başka projeler ona
bağlıdır, taşınması bu işin kapsamı dışındadır (ADR 0021 §1).

TLS sertifikası Coolify'ın Let's Encrypt entegrasyonundan gelir. Kadro
**PIN hash'i taşır** (çevrimdışı giriş bunun için mümkündür); servis
sertifikasız yayına alınmaz.

---

## Coolify kurulumu

Yeni uygulama → **Public/Private Repository** → bu depo.

| Ayar | Değer | Neden |
|---|---|---|
| Build Pack | **Dockerfile** | Nixpacks bu depoyu Python uygulaması sanıp `backend/`'i kurmaya çalışır |
| Base Directory | **`/`** (depo kökü) | derleme bağlamı budur |
| Dockerfile Location | **`services/identity/Dockerfile`** | dosya alt klasörde, bağlam kökte |
| Branch | **`main`** | |
| Automatic Deployment | **KAPALI** | aşağıya bakın |
| Ports Exposes | `8000` | |
| Persistent Storage | `/data` | |

### Base Directory ile Dockerfile Location'ı karıştırmayın

Coolify'da bu iki alan ayrıdır ve burada **bilerek farklıdır**:

* **Base Directory** derleme *bağlamıdır* — `COPY` komutlarının kökü.
* **Dockerfile Location** yalnızca dosyanın yeri.

Bu servis `backend/src/km_core` içindeki kimlik kodunu **yeniden yazmaz,
kullanır** (ADR 0021 — Sonuçlar). Bağlam `services/identity` yapılırsa imaj
derlenir, ama `km_core` imaja hiç girmez ve konteyner ilk açılışta
`ModuleNotFoundError` ile ölür. Aynı uyarı `Dockerfile`'ın başında da yazılıdır.

### Otomatik dağıtım neden kapalı başlar

bbdstore'da `main`'e her push canlıya çıkıyor ve `migrate --force` otomatik
koşuyor; revert kodu döndürüyor, tabloyu bırakıyor. Kimlik şeması için bu **tek
yönlü bir kapıdır** (ADR 0021 §1). İlk kurulumdan sonra dağıtım elle tetiklenir.
Otomatiğe geçirilecekse önce şema göçlerinin geri alınabilirliği ayrı bir
kararla çözülür.

### Sağlık kontrolü

`GET /health` → `{"status": "ok", "version": …, "rosterRevision": …}`
Kimlik istemez; kadro sayısını değil yalnız revizyon numarasını verir.

---

## Ortam değişkenleri

**Sırlar depoya girmez (K8).** Hepsi Coolify → Environment Variables altında,
`Build Variable?` işareti KAPALI (imaja gömülmemeli) tanımlanır.

| Değişken | Zorunlu | Varsayılan | Açıklama |
|---|---|---|---|
| `KM_IDENTITY_PEPPER` | **evet** | — | `secret_lookup` HMAC anahtarı |
| `KM_IDENTITY_ADMIN_TOKEN` | **evet** | — | yönetim uçlarının anahtarı |
| `KM_IDENTITY_DB_PATH` | hayır | `/data/identity.sqlite` | kalıcı diskte olmalı |
| `KM_IDENTITY_BOOTSTRAP_PIN` | hayır | üretilir | ilk yöneticinin PIN'i |
| `KM_IDENTITY_PAIR_CODE_TTL_SECONDS` | hayır | `600` | eşleme kodu ömrü |
| `KM_IDENTITY_LOCK_TTL_SECONDS` | hayır | `120` | danışma kilidi TTL'i |
| `KM_IDENTITY_LOCK_MAX_TTL_SECONDS` | hayır | `900` | istemcinin isteyebileceği üst sınır |
| `KM_IDENTITY_PIN_MIN_LENGTH` | hayır | `6` | |
| `KM_IDENTITY_MAX_FAILED_ATTEMPTS` | hayır | `5` | |
| `KM_IDENTITY_LOCKOUT_MINUTES` | hayır | `15` | |

Değer üretmek için:

```bash
openssl rand -base64 32     # KM_IDENTITY_PEPPER
openssl rand -base64 32     # KM_IDENTITY_ADMIN_TOKEN
```

### `KM_IDENTITY_PEPPER` — bir kez belirlenir, bir daha değişmez

Pepper, PIN arama hash'inin (`secret_lookup`) sabit anahtarıdır ve **her Kontrol
Merkezi kurulumunda aynı olmak zorundadır**: kadro bu anahtarla üretilmiş
lookup değerlerini taşır. Değiştirilirse hiçbir kullanıcı giriş yapamaz ve
bunu geri almanın yolu, eski pepper'ı bulmaktan başka bir şey değildir.

Tanımsızsa servis **açılmaz**. Rastgele üretip veritabanına yazmak, bir
kurtarma sonrası herkesin girişini sessizce bozardı.

### `KM_IDENTITY_ADMIN_TOKEN` — tanımsızsa uçlar 503

Eşleme kodu üretimi, kurulum listesi ve iptal bu token'a bağlıdır. Token
tanımsızken bu uçlar **503** döner ve `503` demek **açık kalmamak** demektir:
"denetim yoksa serbest" davranışı, kurulum listesini ve kod üretimini internete
açık bırakırdı.

**Kontrol Merkezi'ndeki karşılığı `identity_sync.admin_token` kasa
anahtarıdır.** "KM Cihaz Eşle" ekranı (`shell/core-panels/pairing/`) kodu
buradaki `curl` yerine uygulamanın içinden üretir ve o istek bu token'la
imzalanır. Anahtar **kurulum token'ından ayrıdır** ve ayrı olmalıdır: kurulum
token'ı "bu makine bizim" der, yönetim anahtarı "bu makine yeni makineler
kaydedebilir" der. İkisini birleştirmek, eşlenmiş her makineyi yeni makine
eşleyebilir hâle getirirdi. Anahtar yalnız yönetimin oturduğu kurulumun
kasasına yazılır; olmadığı kurulumda ekran açılır, yalnız yönetim düğmeleri
nedenini yazarak kapanır.

---

## Uçlar

| | | Kim |
|---|---|---|
| `GET /health` | sağlık | herkes |
| `GET /roster?known_revision=N` | kadro; değişmemişse `{"changed": false}` | kurulum |
| `POST /roster/import` | var olan bir kurulumun kadrosunu taşır — **yalnız ekler** | yönetim |
| `POST /users` · `PUT /users/{id}` · `POST /users/{id}/status` | kadro yazma | kurulum + kişi |
| `POST /audit` | kurulumlardan denetim kaydı | kurulum |
| `POST /installations/pair-code` | tek kullanımlık, süreli kod — **bekleyen eski kodları geçersiz kılar** | yönetim |
| `POST /pair` | `{code, publicKey, machineName, platform, version}` → token | kod |
| `GET /installations` | makine adı, platform, sürüm, son görülme, durum | yönetim |
| `POST /installations/{id}/revoke` | token iptali | yönetim |
| `POST /locks` · `DELETE /locks/{id}` | TTL'li danışma kilidi | kurulum |

**Yönetim** = `Authorization: Bearer <KM_IDENTITY_ADMIN_TOKEN>`
**Kurulum** = `Authorization: Bearer <eşlemede alınan token>`
**Kişi** = ek olarak `X-KM-Actor-Id: <kullanıcı kimliği>`

### Eşleme token'ı kullanıcı oturumu değildir

Token *"bu makine bizim"* der; kişi yine kurulumda kendi **PIN'ini** girer.
Yazma isteklerinde kurulum, işlemi yapan kişiyi `X-KM-Actor-Id` ile bildirir ve
servis o kişinin iznini **kendi veritabanından** denetler (K9 — çift kapı).
İkisi karışırsa çalınan bir makine herkesin hesabı olur.

### Burada giriş ucu yoktur

`POST /auth/login` **bilerek yoktur**. Merkez kadroya karar verir, girişe değil
(ADR 0021 §2): giriş kurulumda, yerel önbellekten yapılır ve **çevrimdışı
çalışır**. Merkezî *doğrulama* elenmiş bir alternatiftir — servis düştüğünde
herkesin kilitlenmesi, uzaktan müdahaleye en çok ihtiyaç duyulan anda olurdu.

### Var olan kadroyu taşımak — `POST /roster/import`

Merkez, kadro biriktikten SONRA kuruldu. Kadrosunda yalnız dağıtımda doğan
bootstrap yöneticisi vardır; ilk kurulumun kullanıcıları orada yoktur ve
taşınmazsa eşlenen ikinci cihazda kimse kendi PIN'iyle giremez.

**PIN'ler korunur.** Gövde düz PIN değil, `password_hash` (Argon2id) +
`secret_lookup` (peppered HMAC) taşır; kimseye yeni PIN verilmez. Bu yalnızca
`KM_IDENTITY_PEPPER` ile kurulumun kasasındaki `core.pin_pepper` AYNI ise
çalışır (yukarıdaki "bir kez belirlenir" başlığı).

Uç **yalnız ekler**: var olan bir `id` ikinci kez geldiğinde satır ezilmez,
atlanır. Düzenleme `PUT /users/{id}` yolunda kalır — orada işlemin arkasındaki
kişi ve izni denetlenir (K9), burada yalnız yönetim token'ı vardır. `secret_lookup`
çakışan ve merkezde tanımsız rol taşıyan satırlar da atlanır; hepsi yanıtta
nedeniyle döner (`added`, `skipped`, `skips[]`). Bir şey eklendiyse kadro
revizyonu artar.

Gönderen taraf: `scripts/push-roster.py` (varsayılan **kuru prova**).

---

## İlk kurulum sırası

1. Uygulamayı Coolify'da yukarıdaki ayarlarla oluşturun, **dağıtmayın**.
2. Ortam değişkenlerini girin (`PEPPER`, `ADMIN_TOKEN` zorunlu).
3. `/data` kalıcı diskini bağlayın.
4. Dağıtın. Göçler açılışta koşar; ayrı bir `migrate` adımı yoktur.
5. Logdan ilk yönetici PIN'ini alın (`KM_IDENTITY_BOOTSTRAP_PIN` vermediyseniz).
6. Eşleme kodu üretin:

   ```bash
   curl -X POST https://kontrolmerkezi.bbdstore.com.tr/installations/pair-code \
        -H "Authorization: Bearer $KM_IDENTITY_ADMIN_TOKEN" \
        -H "Content-Type: application/json" -d '{"note":"MSI geliştirme"}'
   ```

7. Kodu Kontrol Merkezi'nin eşleme ekranına girin.

## Yedek

Tek dosya: `/data/identity.sqlite` (+ `-wal`, `-shm`). WAL kipi açık olduğu
için üçü birlikte alınır; yalnız ana dosyayı kopyalamak son yazmaları kaçırır.
