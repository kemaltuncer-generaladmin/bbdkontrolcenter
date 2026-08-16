# BLD Geçidi (`bld_api`)

BLD sunucusunun kontrol API'sine açılan **tek kapı** (K4). Ekranı, izni, HTTP
yüzeyi yoktur; yalnızca `bld.api` yeteneğini sağlar. KDS Yönetimi (`bld_kds`)
ve ilerideki BLD ekranları mutfak verisine buradan ulaşır.

Hedef: `platform/extensions/veykemtu/bridgeapi` (TastyIgniter/Laravel) ·
uçlar `/api/control/kds/*` · sözleşme **K-21 §1-§4**.

Canlı adres **depoda yazmaz**: `config/default.yaml` içinde `base_url` boştur
ve gerçek değer `config/local.yaml` ile verilir (canlı sistemin adresi
`https://api.benimlezzetdunyam.com.tr`). Adres boşken geçit ilk istekte
`config_missing` hatası döner — sessizce başka bir yere gitmez.

## Kullanımı

```yaml
# çağıran modülün module.yaml dosyasında
depends: [bld_api]
consumes:
  - capability: bld.api
    reason: "BLD verisi tek kapıdan geçer (K4)."
```

```python
api = ctx.capability("bld.api")

kasalar = await api.devices()                                   # list[dict]
ozet    = await api.overview()                                  # dict
sonuc   = await api.revoke_device(3, reason=gerekce, actor=user.full_name, dry_run=dry)
```

## İmza — sözleşme §1

Her istek üç başlık taşır; sunucu tarafındaki doğrulayıcı
`Veykemtu\BridgeApi\Http\Middleware\VerifyControlSignature`:

```
X-Control-Timestamp: <unix saniye>
X-Control-Nonce:     <16-128 karakter, rastgele>
X-Control-Signature: sha256=<64 hex>

kanonik = METOT \n YOL \n ZAMAN \n NONCE \n sha256_hex(ham gövde)
imza    = "sha256=" + hmac_sha256(kanonik, sır)
```

- **Yol sorgu dizesi HARİÇ** imzalanır (`/api/control/kds/devices`); süzgeçler
  isteğe girer, imzaya girmez.
- **Gövde ham bayt olarak imzalanır ve aynen gönderilir.** İstemci gövdeyi bir
  kez üretip `httpx`'e `content=` ile verir; `json=` kullanılsaydı httpx gövdeyi
  yeniden serileştirir ve en küçük fark (ayraç boşluğu, `\uXXXX` kaçışı, anahtar
  sırası) sunucuda başka bir özet üretirdi. Hata da "gövde bozuk" demez, "imza
  doğrulanamadı" der — sahada teşhis edilemez.
- **Her deneme yeniden imzalanır.** Nonce sunucuda 600 sn hatırlanıyor; 429
  sonrası aynı başlıklarla yinelemek "Bu istek daha önce işlendi" üretirdi.
- Pencere ±300 sn. 401 alındığında geçit üç olası sebebi birden söyler: yanlış
  sır, saat kayması, tekrar oynatma.
- Sır **kasadadır**: `server.bld.control_secret` (kasadaki adlandırma kuralı
  `server.<uygulama>.<alan>`). Değeri sunucudaki `BLD_CONTROL_SECRET` ortam
  değişkeniyle aynı olmalıdır. Sır yoksa istek **hiç gönderilmez**.

## Bilmen gereken beş kural

1. **Acil fren** — `read_only` **varsayılan olarak açıktır**. Açıkken GET dışı
   her istek geçitte reddedilir (`BldApiError.code == "read_only"`), uzağa hiç
   gitmez. Deneme yine de `mod_bld_api_audit` tablosuna işlenir.
2. **Kuru prova** — yazma metotlarının `dry_run` varsayılanı **False**
   (K-22 §4 ile kapatıldı; şalter arayüzden kaldırıldı ve `bld_kds` paneli
   bayrağı artık hiç göndermiyor). **Bayrağın kendisi durur:** `dry_run=True`
   veren bir çağrı hâlâ prova yapar — sözleşme §4 additive'dir ve kaldırmak
   bayrağı açıkça gönderen eski çağrıları kırardı. Yazmanın emniyeti artık tek
   başına `read_only` acil frenidir. Sözleşmedeki yazma uçları bayrağı anlar: yazma yapılmaz,
   sunucu denetim satırını `result="dry_run"` ile yine de yazar ve
   `{"ok": true, "dry_run": true, "would": {...}}` döner — yani istek gerçekten
   gider. Sözleşmede **olmayan** bir yola kuru prova ile yazılmak istenirse
   istek **hiç gönderilmez** ve `{"ok": true, "dry_run": true, "sent": false}`
   döner; Laravel tanımadığı alanı yok saydığı için o bayrak sessizce düşer ve
   "prova" gerçek yazmaya dönüşürdü.
3. **Gerekçe ve aktör zorunlu** — her yazma metodu `reason` (en az 10 karakter;
   revizyon ve durum uçlarında en çok 160) ve `actor` alır. İkisi de **gövdeye**
   konur (sözleşme §3); sözleşme başka başlık tanımlamadığı için başlıkla
   taşınmaz. İstek çıkmadan aynı bilgi `mod_bld_api_audit` tablosuna yazılır.
4. **Hata biçimi** — her şey `BldApiError` olarak gelir: `.message` (Türkçe,
   maskelenmiş) · `.status` · `.code` (`config_missing` · `read_only` ·
   `reason_required` · `actor_required` · `payload` · `unauthorized` ·
   `forbidden` · `not_found` · `control_endpoint_missing` · `validation` ·
   `conflict` · `rate_limited` · `transport` · `server` · `http`). Servis
   katmanı bunu yakalar ve ekran ayakta kalır (K7).
5. **Yineleme ve hız** — GET üç kez denenir, **yazma yinelenmez** (sözleşmede
   idempotency anahtarı taşıyan başlık yok). 429 istisnadır: `Retry-After`
   kadar beklenip bir kez yinelenir. Hız kovası dakikada 18 istekte durur —
   sunucu sınırı saatte 1200 ve 18 × 60 = 1080 < 1200.

`not_found` ile `control_endpoint_missing` **ayrı** şeylerdir: ilki "uç var,
kayıt yok", ikincisi "uç sunucuya henüz dağıtılmamış, bekle". Ayrımı yanıtın
zarfı kanıtlar — sözleşmenin `{"error": {...}}` zarfı geldiyse istek
denetleyiciye ulaşmış demektir.

## Yöntem yüzeyi

`bld_kds` ekranı bu imzaları çağırır. Yazma metotlarında `reason` ve `actor`
**zorunlu anahtar argümandır**; `dry_run` verilmezse ayardaki varsayılan geçer.

| Alan | İmza | Uç |
|---|---|---|
| Durum | `state() -> dict` | — (yerel) |
| Durum | `await health() -> dict` | `GET /overview` |
| Durum | `await audit_trail(*, limit: int = 100) -> list[dict]` | — (yerel tablo) |
| Özet | `await overview() -> dict` | `GET /overview` |
| Cihaz | `await devices() -> list[dict]` | `GET /devices` |
| Cihaz | `await create_device(*, name: str, reason: str, actor: str, dry_run: bool \| None = None) -> dict` | `POST /devices` |
| Cihaz | `await rename_device(device_id: int, *, name: str, reason: str, actor: str, dry_run: bool \| None = None) -> dict` | `PATCH /devices/{id}` |
| Cihaz | `await new_pairing_code(device_id: int, *, reason: str, actor: str, dry_run: bool \| None = None) -> dict` | `POST /devices/{id}/pairing-code` |
| Cihaz | `await revoke_device(device_id: int, *, reason: str, actor: str, dry_run: bool \| None = None) -> dict` | `POST /devices/{id}/revoke` |
| Ayar | `await update_device_settings(device_id: int, *, settings: dict, reason: str, actor: str, dry_run: bool \| None = None) -> dict` | `PATCH /devices/{id}/settings` |
| Komut | `await device_commands(device_id: int) -> list[dict]` | `GET /devices/{id}/commands` |
| Komut | `await send_command(device_id: int, *, command: str, payload: dict \| None = None, reason: str, actor: str, dry_run: bool \| None = None) -> dict` | `POST /devices/{id}/commands` |
| Fiş | `await print_jobs(*, device_id: int \| None = None, order_id: int \| None = None, limit: int \| None = None) -> list[dict]` | `GET /print-jobs` |
| Sipariş | `await orders(*, include_completed: bool = False, since: str = "") -> list[dict]` | `GET /orders` |
| Sipariş | `await order(order_id: int) -> dict` | `GET /orders/{id}` |
| Sipariş | `await order_revisions(order_id: int) -> list[dict]` | `GET /orders/{id}/revisions` |
| Sipariş | `await create_order_revision(order_id: int, *, items: list[dict], reason: str, actor: str, note: str = "", requested_at: str = "", customer_note: str = "", dry_run: bool \| None = None) -> dict` | `POST /orders/{id}/revisions` |
| Sipariş | `await set_order_status(order_id: int, *, status: str, reason: str, actor: str, dry_run: bool \| None = None) -> dict` | `POST /orders/{id}/status` |

Yanıt alanları **snake_case**'tir ve dönüştürülmez (sözleşme §2). `store_api`
camelCase döndürür; iki geçidin biçimini birbirine benzetmek sözleşmeyle ekran
arasına sessiz bir çeviri katmanı sokardı.

### Geçidin istek göndermeden kestiği durumlar

Hepsinin sebebi tek: **Laravel tanımadığı alanı sessizce yok sayar.**
"Kaydedildi" diyen bir ekranın arkasında hiçbir yere yazılmamış bir değer
bırakmak, açık bir hatadan çok daha pahalıdır.

- **Tanınmayan ayar anahtarı.** Yönetilen ayar 24 tanedir (16 mevcut + 7 kilit,
  sözleşme §2.2, + `disabled_sound_events`, K-22 §1); listede olmayan anahtar
  `payload` koduyla reddedilir.
- **`None` düşürülmez.** Kilit alanlarında `null` = "yönetici dokunmadı" =
  **serbest**, `false` = kilitli. `None`'ları gövdeden atmak bir kilidi
  kaldırmayı imkânsız kılardı — geçit `null`'ı JSON `null` olarak gönderir.
  Aynı ayrım `disabled_sound_events`te de var ve orada üçüncü bir hâl daha
  taşıyor: `null` "dokunmadım", **boş dize** "hiçbiri kapalı olmasın". Boş
  dizeyi düşüren bir geçit, kasadaki bütün susturmaları kaldırma komutunu
  "hiç dokunma"ya çevirirdi.
- **Tanınmayan komut.** `test_receipt · reprint · clear_failed ·
  silence_alarm · restart · update · unpair · clear_queue`
  (`KitchenCommand::ALL`; son üçü K-22 §2). `reprint` için fiş türü
  (`mutfak` · `musteri` · `kurye`) zorunludur, son üçü **yüksüzdür**.
- **Boş revizyon listesi.** `items` kalem farkı değil **tam listedir**; boş
  liste "hepsini sil" anlamına gelirdi ve iptal işi durum ucunun işidir.

### Fiş kuyruğu bir kuyruk değildir

`print_jobs()` **denetim kaydı** döndürür: basılmış işler, en yeni önce.
Kasanın kendi disk kuyruğu sunucuda **yoktur** (sözleşme §2.4). "Bekleyen iş"
sayısı cihaz sağlığından okunur:
`device["health"]["print_queue_pending"]` ve `["print_queue_failed"]`.
Bu tabloyu kuyruk sanan bir ekran bekleyen işi hiç göremezdi.

## Ayar ve sır

`config/default.yaml` · şema `config/schema.json`. Makineye özel değerler
`config/local.yaml` içine yazılır:

```yaml
modules:
  bld_api:
    base_url: "https://api.benimlezzetdunyam.com.tr"
    read_only: false          # yazma açılacaksa — varsayılan güvenli taraftadır
    dry_run_default: false    # K-22 §4; bayrak durur, varsayılanı kalktı

secrets:
  server.bld.control_secret: "<sunucudaki BLD_CONTROL_SECRET ile aynı>"
```

Sır depoda, ayarda, log'da ve hata metninde bulunmaz. Maskeleme iki katmanlıdır:
ad tabanlı desen (`secret`, `token`, `password` … geçen alanlar) ve
**yüklenmiş sır değerinin kendisi**. İkincisi şart: sır rastgele bir dizedir,
sunucu onu alan adı olmadan yankılarsa desen yakalayamaz.

## Tablolar

| Tablo | İçerik |
|---|---|
| `mod_bld_api_audit` | Yazma denemeleri — istek **çıkmadan** yazılır, gerekçe ve aktör taşır. `result` boşsa "gönderildi mi belli değil". |

Sunucunun kendi denetim tablosu (`veykemtu_control_audit`) iki şeyi bilmez:
hiç gönderilemeyen istek (ağ koptu, acil fren kapattı) ve imzası reddedildiği
için denetleyiciye **ulaşamayan** istek — imza doğrulaması middleware'de,
denetleyiciden önce çalışıyor.

## Sözleşmede eksik görülenler

Bunlar uydurulmadı; olduğu gibi bildirilir:

1. **Liste zarfı belirsiz.** §2 yalnız alan adlarının snake_case olduğunu
   söylüyor; liste yanıtının düz dizi mi `{"data": [...]}` mı olduğu yazmıyor.
   Geçit ikisini de açar, üçüncü bir ad (`items`, `rows`) aramaz.
2. **Ayar gövdesinin biçimi belirsiz.** `PATCH /devices/{id}/settings` gövdesi
   sözleşmede tarif edilmiyor. Geçit ayarları `settings` nesnesinin içine
   koyar — §2.1'deki `device` nesnesi de onları orada taşıyor ve §3'ün zorunlu
   `reason`/`actor` alanlarıyla karışma ihtimali böyle kalkıyor. **Sunucu
   tarafı yazılırken teyit edilmelidir.**
3. **İdempotency anahtarı yok.** Sözleşme istek kimliği taşıyan bir başlık
   tanımlamıyor. Bu yüzden `mod_bld_api_audit.request_id` yalnız yerel bir
   anahtardır ve yazma isteğinin yinelenmemesi bir tercih değil zorunluluktur.
4. **Uçlar sunucuda henüz yayında değil.** `routes/api.php` içinde
   `/api/control/kds` öneki yok (imza middleware'i yazılmış durumda). Bu hâlde
   çağrılar `control_endpoint_missing` koduyla döner ve ekran "sunucu eklentisi
   güncellenince çalışacak" diyerek ayakta kalır (K7).

## Testler

```bash
.venv/bin/python -m pytest modules/bld_api/tests -q
.venv/bin/ruff check modules/bld_api
```

Testler ağa çıkmaz: `httpx.MockTransport` ile sahte sunucu, sahte kasa ve sahte
depo kullanılır. Kanonik imza biçimi **sabit vektörle** çakılıdır — ayraç, sıra
ya da kodlama bir gün değişirse hata sahada değil testte çıkar.
