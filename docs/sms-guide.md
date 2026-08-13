# SMS Kullanım Kılavuzu

`km_platform/notify` katmanının SMS tarafı. Sağlayıcı Netgsm.

Tasarım gerekçesi: [ADR 0010](adr/0010-sms-saglayici-entegrasyonu.md) ·
SDK bulguları ve tuzaklar: [netgsm-integration.md](netgsm-integration.md)

---

## Önce bilinmesi gerekenler

**SMS para harcar.** Bu yüzden `dry_run` varsayılan olarak açıktır ve
geliştirme boyunca açık kalır. Kapatmadan gerçek gönderim olmaz.

**Kabul edilmek teslim edilmek değildir.** `send()` başarılı dönerse sağlayıcı
işi **kuyruğa aldı** demektir. Teslim durumu `report()` ile sorgulanır.

**Netgsm SDK'sı doğrudan kullanılmaz.** SDK, yanıt gövdesindeki hata kodunu
denetlemez — gönderilmemiş bir SMS başarılı görünür. Buradaki sarmalayıcı
denetler. `from netgsm import Netgsm` yazmayın.

---

## Kurulum

```python
from km_platform.notify.providers.netgsm import NetgsmConfig, NetgsmSmsProvider

config = NetgsmConfig(
    username="...",          # kasadan gelir, ayar dosyasına yazılmaz (K8)
    password="...",
    header="KURUMADI",       # Netgsm'de TANIMLI gönderici başlığı
    appname="KontrolMerkezi",
    dry_run=True,            # geliştirmede açık kalır
)
provider = NetgsmSmsProvider(config)
```

`header` Netgsm panelinde tanımlı değilse gönderim **kod 40** ile reddedilir.
Tanımlı başlıkları görmek için:

```python
basliklar = await provider.headers()
```

`username`, `password` veya `header` boşsa nesne kurulurken `SmsConfigError`
atar — hata gönderim anına ertelenmez.

## Gönderim

```python
from km_platform.notify import SmsMessage

sonuc = await provider.send([
    SmsMessage(to="0532 123 45 67", text="Sunucu yeniden başlatıldı."),
    SmsMessage(to="+90 533 000 00 00", text="Sunucu yeniden başlatıldı."),
])

print(sonuc.job_id)      # sağlayıcı iş numarası
print(sonuc.recipients)  # 2
print(sonuc.parts)       # tahmini toplam parça — faturalanacak birim
print(sonuc.dry_run)     # kuru çalışmada True
```

Telefon numarası **serbest biçimde** verilebilir; katman normalleştirir:

| Girdi | Gönderilen |
|---|---|
| `0532 123 45 67` | `5321234567` |
| `+90 532 123 45 67` | `5321234567` |
| `(0532) 123-45-67` | `5321234567` |

Geçersiz numara **sağlayıcıya gitmeden** `SmsInvalidRecipient` ile reddedilir —
hatalı numara için kontör harcanmaz.

### Zamanlanmış gönderim

```python
from datetime import datetime

await provider.send(
    [SmsMessage(to="5321234567", text="Bakım başlıyor.")],
    scheduled_at=datetime(2026, 8, 15, 9, 0),
)
```

Saat dilimi taşıyan bir `datetime` verirseniz **Türkiye saatine çevrilir**.
Netgsm tarih alanları saat dilimi taşımaz ve yerel saat bekler; çevrilmeseydi
UTC verilen bir zamanlama üç saat kayardı.

### İptal

```python
await provider.cancel(sonuc.job_id)
```

Yalnızca henüz gönderilmemiş (zamanlanmış) işler iptal edilebilir.

## Teslim raporu

```python
from datetime import datetime

raporlar = await provider.report(
    datetime(2026, 8, 12, 0, 0),
    datetime(2026, 8, 12, 23, 59),
    job_ids=[sonuc.job_id],
)

for r in raporlar:
    print(r.to, r.status)     # DeliveryStatus.DELIVERED, PENDING, ...
```

Ölçüte uyan kayıt yoksa **boş liste** döner — hata değildir.

> Sağlayıcı bu uç noktayı **dakikada 10 sorguyla** sınırlar. Aşılırsa
> `SmsRateLimited` atar.

## Hata yönetimi

Hatalar sağlayıcıdan bağımsızdır; Netgsm kod numaraları dışarı sızmaz.

```python
from km_platform.notify import (
    SmsInvalidRecipient, SmsAuthError, SmsRejected,
    SmsRateLimited, SmsProviderError, SmsTransportError, SmsError,
)

try:
    await provider.send(mesajlar)
except SmsInvalidRecipient as e:
    ...   # numara hatalı — kullanıcı kaydını düzelt. İstek gitmedi.
except SmsAuthError:
    ...   # kimlik/IP sorunu — YENİDEN DENEME, ayar düzeltilmeli
except SmsRejected:
    ...   # başlık/metin/parametre hatalı — YENİDEN DENEME, düzeltilmeli
except SmsRateLimited:
    ...   # sınır aşıldı — bekleyip yeniden dene
except SmsProviderError:
    ...   # sağlayıcı sistem hatası — geçici olabilir, yeniden denenebilir
except SmsTransportError:
    ...   # DİKKAT: gidip gitmediği BİLİNMİYOR (aşağıya bakın)
```

| Hata | Yeniden denenir mi |
|---|---|
| `SmsConfigError` | Hayır — ayar düzeltilmeli |
| `SmsInvalidRecipient` | Hayır — numara düzeltilmeli |
| `SmsAuthError` | Hayır — kimlik/IP izni düzeltilmeli |
| `SmsRejected` | Hayır — başlık/metin/parametre düzeltilmeli |
| `SmsRateLimited` | Evet, bekledikten sonra |
| `SmsProviderError` | Evet |
| `SmsTransportError` | **Önce doğrula** |

### `SmsTransportError` neden özel

Zaman aşımında istek arka planda tamamlanmış olabilir — `asyncio.to_thread`
çalışan iş parçacığını durduramaz. Bu hata "gönderilmedi" değil,
"**gidip gitmediği bilinmiyor**" anlamına gelir.

Körlemesine yeniden denemek **çift SMS** gönderir. Doğru sıra:

```python
except SmsTransportError:
    raporlar = await provider.report(baslangic, simdi)   # önce doğrula
    if not gonderilmis(raporlar):
        await provider.send(mesajlar)                    # sonra yeniden dene
```

## Metin, karakter ve maliyet

Gönderimden önce parça sayısını hesaplayabilirsiniz:

```python
from km_platform.notify import plan_text

plan = plan_text("Yedekleme başarısız oldu")
plan.encoding   # "tr"  — Türkçe karakter var
plan.parts      # 1
plan.units      # septet cinsinden uzunluk
plan.unicode    # False
```

| Metin | Kodlama | Parça başına |
|---|---|---|
| Türkçe karakter yok | GSM-7 | 160 (çoklu: 153) |
| ğ Ğ ı İ ş Ş içeriyor | `tr` | 160 (çoklu: 153), bu karakterler 2 septet |
| Emoji vb. içeriyor | UCS-2 | **70** (çoklu: 67) |

**Tek emoji mesajı dört katına çıkarabilir.** İşletimsel uyarılarda emoji
kullanmayın.

Netgsm toplu gönderimde tek kodlama kabul eder: partideki **herhangi bir**
mesaj Türkçe karakter içeriyorsa tümü `tr` ile gider.

Parça sayısı **tahmindir**, maliyet önizlemesi içindir. Faturalanan kesin sayı
sağlayıcı raporundan okunur.

## Ayar

`config/default.yaml` → `platform.notify.sms`:

```yaml
sms:
  provider: netgsm
  enabled: false           # kimlik bilgisi girilmeden açılmaz
  dry_run: true            # GERÇEK SMS GÖNDERMEZ. Üretimde false yapılır.
  header: ""               # Netgsm'de tanımlı gönderici başlığı
  appname: "KontrolMerkezi"
  iys_filter: "0"          # 0 = bilgilendirme
  timeout_seconds: 30
  rate_limit_per_minute: 20
  report_rate_limit_per_minute: 10
```

`username` ve `password` **buraya yazılmaz** — kasadan gelir (K8).

## İYS

`iys_filter` değerleri: `0` bilgilendirme (İYS kontrolü yok), `11` bireysel
alıcıya ticari içerik, `12` tacire ticari içerik.

Bizim gönderimlerimiz kurum personeline giden **işletimsel uyarılardır** →
varsayılan `0`. Ticari içerik gönderilecekse `11`/`12` gerekir ve İYS onay
yönetimi devreye girer; bu uygulamanın kapsamı dışındadır, kurumun uyum
sorumlusuna danışılmalıdır.

## Yeni sağlayıcı ekleme

Netgsm bir uygulamadır, sözleşme değil. İkinci bir sağlayıcı için:

1. `notify/providers/<ad>/` altında `SmsProvider` protokolünü uygulayan bir
   sınıf yazın (protokol: `notify/contracts.py`).
2. Sağlayıcıya özgü hata kodlarını `notify/errors.py` tiplerine eşleyin.
3. Ayardaki `provider` değerini değiştirin.

Çağıran taraf değişmez — üst katmanlar sağlayıcı paketini hiçbir zaman
doğrudan görmez (K4).
