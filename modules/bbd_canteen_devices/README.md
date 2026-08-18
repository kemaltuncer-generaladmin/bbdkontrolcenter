# Kantin Cihazları

Kantindeki **kiosk** cihazlarının eşlenmesi: kayıt açma, tek kullanımlık eşleme
kodu üretme ve eşlemeyi iptal etme.

## Sınır — önce bunu okuyun

**Sahadaki kasa tabletine dokunulmaz.** O cihaz kantinde `devices` tablosunda
duruyor, paylaşılan `canteen.enrollment_secret` ile `POST /api/auth/device`
çağırıp token almış durumda ve o akış **aynen çalışmaya devam ediyor**. Bu
modülün kullandığı bütün uçlar `/api/kiosks*` altındadır ve kantinde yalnız
`kiosks` tablosuna bakar. Buradan yapılan hiçbir işlem çalışan tableti
etkilemez.

Yeni mantık eskisinin **yerine değil, yanına** kuruldu.

### Neden yeni mantık

| Bugünkü cihaz akışı | Kiosk akışı |
|---|---|
| Paylaşılan sır (`enrollment_secret`), uygulamaya gömülü | 8 haneli, **tek kullanımlık**, süreli kod |
| Sır sızarsa sınırsız cihaz kaydeder ve **sızdığı anlaşılmaz** | Kod bir kez yanar; ikinci deneme reddedilir |
| Sır düz metin karşılaştırılır | Kod veritabanında yalnız `sha256` olarak durur |
| **İptal ucu yok** — kaydolan cihazın erişimi kesilemez | `POST /kiosks/{id}/revoke` token'ı gerçekten siler |
| `last_seen_at` hiç güncellenmez | Her istekte tazelenir |

Desen BLD KDS ve KM Cihaz Eşle ekranlarıyla aynıdır, ama **kendi mantığıdır**:
kantinin kendi tablosu, kendi uçları, kendi izinleri.

## İzinler

| Anahtar | Ne yapar |
|---|---|
| `bbd_canteen_devices.view` | Liste, özet ve yerel işlem izini görme |
| `bbd_canteen_devices.manage` | Kiosk açma/adlandırma ve **eşleme kodu üretme** |
| `bbd_canteen_devices.devices` | **Yıkıcı**: eşleme iptali. `destructive: true` → PIN teyidi ister |

Kod üretmek ile iptal etmek ayrıdır: iptal edilen kiosk kantinde satış yapamaz
ve düzeltmesi yalnız merkezden gelir. Tek anahtarda toplansalardı, kod
üretebilen herkes bir kasayı durdurabilirdi. İptal edilmiş kioska yeni kod
**üretilmez** — üretilebilseydi `manage` taşıyan biri `devices` taşıyanın
kararını geri alırdı.

## Kontrol Merkezi uçları

```
GET   /api/bbd_canteen_devices/kiosks                     liste + özet + sözleşme
GET   /api/bbd_canteen_devices/audit                      yerel işlem izi
GET   /api/bbd_canteen_devices/printer                    yazıcı durumu
POST  /api/bbd_canteen_devices/kiosks                     kayıt + İLK kod
PATCH /api/bbd_canteen_devices/kiosks/{id}                yalnız ad
POST  /api/bbd_canteen_devices/kiosks/{id}/pairing-code   yeni kod (+ isteğe bağlı baskı)
POST  /api/bbd_canteen_devices/kiosks/{id}/revoke         iptal (gerekçe + PIN)
```

Kuru prova (`dryRun`) alanı **yoktur**: kantinde karşılığı olmadığı için
eklemek, "prova yaptım" diyen ama gerçekten yazan bir çağrı üretirdi.

## Kantin (Laravel) uçları

```
POST  /api/kiosks/pair                  token'sız · throttle:5,1
GET   /api/kiosks                       cihaz token'ı ister
POST  /api/kiosks                       cihaz token'ı ister
PATCH /api/kiosks/{id}                  cihaz token'ı ister
POST  /api/kiosks/{id}/pairing-code     cihaz token'ı ister
POST  /api/kiosks/{id}/revoke           cihaz token'ı ister
```

Kiosk token'ı yönetim uçlarında **reddedilir** (`KioskController::assertManager`):
kiosk kendine ikinci bir kiosk açamaz, kendi iptalini geri alacak kod üretemez.

---

# Android uygulamasının uyacağı sözleşme

Bu bölüm `bbdkantin/app` tarafında yazılacak kodun sözleşmesidir. **Kontrol
Merkezi tarafında karşılığı hazırdır**; aşağıdaki akış birebir uygulanırsa
başka bir değişiklik gerekmez.

## 1. Eşleme ekranı ne zaman açılır

```
Uygulama açılır
   └── SecureStore.deviceToken var mı?
         ├── HAYIR → EŞLEME EKRANI
         └── EVET  → normal akış
                      └── herhangi bir istekte 401 → token'ı sil → EŞLEME EKRANI
```

**Eşleme ekranı, mevcut `EnrollmentManager` akışının yerine geçer.** Bugünkü
`ensureEnrolled()` gömülü `BuildConfig.ENROLLMENT_SECRET` ile sessizce
kaydoluyor; kiosk yapılandırmasında bu çağrı **hiç yapılmamalıdır**. İki akışın
aynı uygulamada birlikte çalışması, kod girmeyi gereksiz kılar ve yeni mantığın
tek kazancını (paylaşılan sırrın ortadan kalkması) yok eder.

## 2. Eşleme isteği

```http
POST {BASE}/api/kiosks/pair
Content-Type: application/json
```

```json
{
  "code": "48210937",
  "deviceName": "Kantin Kiosk 1",
  "platform": "android",
  "appVersion": "1.4.0"
}
```

| Alan | Kural |
|---|---|
| `code` | **Tam 8 hane, yalnız rakam.** Boşluk/tire gönderilmez — kullanıcı `4821 0937` yazarsa uygulama temizler. Harf yoktur (O/0 ve I/1 karışıyordu). |
| `deviceName` | 2–100 karakter. Yöneticinin merkezde açtığı ad **bu değerle güncellenir**: sahadaki cihazın kendini nasıl adlandırdığı listede görünsün. |
| `platform` | En çok 40 karakter. `"android"` yeterli; istenirse `"android-14"`. |
| `appVersion` | En çok 40 karakter. `BuildConfig.VERSION_NAME`. |

`Authorization` başlığı **gönderilmez**: bu uç token'sızdır. Var olan bir token
gönderilirse yok sayılır.

## 3. Başarılı yanıt — HTTP 201

```json
{
  "token": "17|k0k2Ff...",
  "kiosk": {
    "id": 4,
    "name": "Kantin Kiosk 1",
    "platform": "android",
    "appVersion": "1.4.0",
    "paired": true,
    "pairedAt": "2026-08-18T12:03:11+03:00",
    "lastSeenAt": "2026-08-18T12:03:11+03:00",
    "revokedAt": null,
    "pairing": { "usable": false, "expiresAt": "...", "usedAt": "..." }
  }
}
```

**`token` yalnız bu yanıtta görünür.** Sunucuda düz metni saklanmaz; ikinci kez
sorulamaz. Kaybedilirse tek yol yeni bir eşleme kodudur.

Uygulamanın yapacağı:

```kotlin
// Kalıcı kopya: EncryptedSharedPreferences (Android Keystore, AES-256-GCM).
// Mevcut desen aynen geçerli — SecureStore.kt:41-43.
secureStore.deviceToken = response.token
session.deviceToken = response.token      // bellekteki kopya; AuthInterceptor okur
```

Token normal `Authorization: Bearer <token>` başlığıyla taşınır — `AuthInterceptor`
değişmez. Kiosk token'ı bütün mevcut kasa uçlarını kullanabilir; **yalnız**
`/api/kiosks*` yönetim uçlarında 403 alır (kiosk kendi cinsini yönetemez).

`qrKey` bu yanıtta **dönmez**. Eşleme başarılı olduktan sonra mevcut
`GET /api/device/qr-key` ucu çağrılmalıdır (`EnrollmentManager.syncQrKey()`
zaten bunu yapıyor); tek fark, artık token'ın eşlemeden gelmesidir.

## 4. Reddedilen eşleme — HTTP 422

```json
{ "message": "Eşleme kodu geçersiz.", "reason": "pairing_denied" }
```

**Sebep ayırt edilmez ve edilmemelidir.** Kodun hiç var olmaması, süresinin
geçmesi, daha önce kullanılmış olması ve kioskun iptal edilmiş olması aynı
cümleyi verir. Uygulama bu cümleyi olduğu gibi göstermeli, kendi tahminini
("kod süresi dolmuş olabilir") **eklememelidir** — sunucunun bilerek
gizlediği bilgiyi ekran uydurmuş olur.

Diğer durumlar:

| Durum | Anlamı | Ekranda |
|---|---|---|
| `422` `code` doğrulama hatası | 8 haneli rakam değil | "Kod 8 haneli olmalı." (istekten önce de denetlenebilir) |
| `429` | Oran sınırı: dakikada 5 deneme | "Çok fazla deneme. Bir dakika sonra tekrar deneyin." |
| Ağ hatası | — | Yeniden dene düğmesi. **Kod otomatik yeniden gönderilmez**: yanmış olabilir. |

**Otomatik yeniden deneme yapılmaz.** İstek sunucuya ulaşıp yanıt yolda
kaybolduysa kod yanmıştır; ikinci deneme "geçersiz" alır ve kullanıcı kodun
yanlış olduğunu sanar. Bu durumda doğru davranış yöneticiden yeni kod
istemektir ve ekran bunu yazmalıdır.

## 5. Token geçersizleştiğinde

Merkezden iptal edilen kioskun token'ı **silinir**; bir sonraki istek `401`
döner.

```kotlin
// 401 alan her istek:
secureStore.deviceToken = null
session.deviceToken = null
// → eşleme ekranına dön
```

`401` sonrası **gömülü sırla yeniden kaydolmaya çalışılmamalıdır**: bu, iptal
kararını sessizce delerdi. Tek yol yeni bir eşleme kodudur.

## 6. Eşleme ekranının kendisi

- 8 haneli sayısal giriş, `inputMode = numeric`, 4+4 gösterim.
- Kod **maskelenmez**: kullanıcı yazdığını görmeli, kod 10 dakika yaşıyor.
- "Cihaz adı" alanı önceden doldurulmuş gelmeli (`BuildConfig.DEVICE_NAME`),
  düzenlenebilir olmalı.
- Eşleme başarılıysa ekran bir daha açılmamalı (token kalıcı).
- Ekran, kodun nereden alınacağını yazmalı: *"Kod Kontrol Merkezi → Kantin
  Cihazları ekranından üretilir ve 10 dakika geçerlidir."*

## 7. Yapılmayacaklar

- Kodu cihazda saklamak (SharedPreferences, dosya, log). Kod tek kullanımlıktır
  ve saklanmasının hiçbir karşılığı yoktur.
- Token'ı düz `SharedPreferences`'a yazmak. Kalıcı kopya **yalnız**
  `EncryptedSharedPreferences`'tadır (`SecureStore`).
- Token'ı loglamak, hata raporuna koymak, ekranda göstermek.
- Kod denemelerini otomatik tekrarlamak (bkz. §4).
- `enrollmentSecret` ile ikinci bir kayıt yolu bırakmak (bkz. §1, §5).
