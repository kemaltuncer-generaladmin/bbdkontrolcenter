# Netgsm Entegrasyonu — Araştırma Bulguları ve Tasarım

Kaynak: `netgsm-sms` 1.1.2 (resmi SDK) kaynak kodu, `github.com/netgsm/netgsm-sms-python`
resmi örnekleri ve hata kodu tabloları. İnceleme tarihi 2026-08-12.

Karar: [ADR 0010](adr/0010-sms-saglayici-entegrasyonu.md)

---

## SDK yüzeyi

```python
from netgsm import Netgsm                    # paket adı netgsm-sms, import adı netgsm

client = Netgsm(username=..., password=..., appname=...)
client.sms.send(msgheader, messages, encoding, startdate, stopdate, iysfilter, partnercode)
client.sms.cancel(jobid)
client.sms.get_report(startdate, stopdate, jobids, pagesize, pagenumber)
client.sms.get_headers()
client.sms.get_inbox(startdate, stopdate, pagesize, pageno)
```

`messages` bir liste: `[{"msg": "...", "no": "5XXXXXXXXX"}, ...]`
Kimlik doğrulama Basic Auth; başlık kurucuda bir kez üretilir.

---

## Tuzaklar

Aşağıdakiler SDK kaynağı okunarak tespit edildi. Sarmalayıcımızın var olma
nedeni bunlardır — SDK doğrudan kullanılmaz.

### 1. Başarılı yanıt gövdesindeki hata kodu denetlenmiyor

SDK yalnızca `raise_for_status()` çağırıyor; bu da sadece HTTP 4xx/5xx'te
istisna atar. Yanıt gövdesindeki `code` alanı **başarı yolunda hiç okunmuyor.**
Resmi örnekler de yalnızca `response.get("jobid")` okuyor, `code` bakmıyor.

Sonuç: `send()` istisna atmadan dönerse SMS'in gittiği **garanti değildir.**

→ Sarmalayıcı her yanıtta `code` alanını denetler. `"00"` dışındaki her değer
   tipli hataya çevrilir.

### 2. Üç ayrı tarih biçimi

| Metot | Biçim | Örnek |
|---|---|---|
| `send()` | `ddMMyyyyHHmm` | `011220231430` |
| `get_report()` | `dd.MM.yyyy HH:mm:ss` | `01.12.2023 14:30:00` |
| `get_inbox()` | `ddMMyyyyHHmmss` | `01122023143000` |

Aynı SDK içinde üç biçim. Elle string üretmek hata kaynağıdır.

→ Sarmalayıcı yalnızca `datetime` kabul eder, biçimlendirmeyi uç noktaya göre
   kendisi yapar.

### 3. Zaman aşımı sabit kodlanmış

Her çağrıda `timeout=30` gömülü. `Config.REQUEST_TIMEOUT` tanımlı ama
**kullanılmıyor** — dışarıdan ayarlanamaz.

→ Sarmalayıcı `asyncio.wait_for` ile kendi zaman aşımını uygular.

### 4. Senkron `requests`

SDK tümüyle bloklayan `requests` kullanıyor. Çekirdeğimiz async.

→ Çağrılar `asyncio.to_thread` içinde çalıştırılır, olay döngüsü bloklanmaz.

### 5. Import adı çakışması

Paket adı `netgsm-sms`, import adı `netgsm`. Bakımsız üçüncü taraf paket de
aynı import adını kullanıyor — **ikisi aynı ortamda bulunamaz.**

---

## Hata kodları

Resmi tablodan, tipli hatalara eşlenmiş hali:

| Kod | Anlam | Eşlendiği hata |
|---|---|---|
| `00` | Başarılı | — |
| `20` | Mesaj metni hatalı veya karakter sınırı aşıldı | `SmsRejected` |
| `30` | Kullanıcı adı/parola hatalı, API erişimi yok veya IP kısıtlı | `SmsAuthError` |
| `40` | Gönderici başlığı sistemde tanımlı değil | `SmsRejected` |
| `50` | Aboneliğiniz İYS kontrollü gönderim yapamıyor | `SmsRejected` |
| `51` | Aboneliğe ait İYS marka bilgisi yok | `SmsRejected` |
| `70` | Parametre hatalı veya zorunlu alan eksik | `SmsRejected` |
| `80` | Gönderim sınırı aşıldı | `SmsRateLimited` |
| `85` | **Aynı numaraya 1 dakikada 20'den fazla görev açılamaz** | `SmsRateLimited` |
| `100`, `101` | Sistem hatası | `SmsProviderError` |

Rapor uç noktası ayrıca **dakikada 10 sorgu** ile sınırlı (rapor `80` kodu).

---

## İYS (İleti Yönetim Sistemi)

`iysfilter` değerleri:

| Değer | Anlam |
|---|---|
| `0` | Bilgilendirme içerikli — İYS kontrolü yok |
| `11` | Bireysel alıcıya ticari içerik — İYS kontrollü |
| `12` | Tacire ticari içerik — İYS kontrollü |

Bizim gönderimlerimiz kurum personeline yönelik **işletimsel uyarılardır**
(bulaşma tespiti, yedek hatası, sunucu düştü). Varsayılan `0` seçildi.

Ticari içerik gönderilecekse `11`/`12` gerekir ve İYS onay yönetimi işin
içine girer. Bu, uygulamanın kapsamı dışındadır; ticari gönderim ihtiyacı
doğarsa kurumun uyum sorumlusuna danışılmalıdır.

---

## Karakter kodlaması ve parça sayısı

SDK notu: `encoding` yalnızca mesaj Türkçe karakter içeriyorsa `"tr"`
yapılmalı, aksi halde hiç gönderilmemeli.

Sarmalayıcının uyguladığı kural:

| Metin | encoding | Parça başına |
|---|---|---|
| Tümü GSM-7 temel kümesinde | gönderilmez | 160 (çoklu: 153) |
| Türkçe karakter içeriyor (ğ Ğ ı İ ş Ş) | `"tr"` | 160 (çoklu: 153), Türkçe karakterler 2 septet |
| GSM-7'ye sığmayan karakter (emoji vb.) | UCS-2 | 70 (çoklu: 67) |

Parça sayısı **maliyet önizlemesi** içindir; kesin sayı Netgsm raporundan
gelir. Uygulama gönderim öncesi parça sayısını gösterir — operatör bir uyarının
kaç SMS'e mal olacağını görmeden göndermez.

---

## Telefon numarası biçimi

Netgsm `5XXXXXXXXX` bekler: 10 hane, başında `0` veya `+90` **yok**.

Kullanıcı kaydındaki `phone_mobile` alanı serbest biçimde girilir
(`0532 123 45 67`, `+90 532 123 45 67`, `532-123-45-67`). Sarmalayıcı
normalleştirir ve geçersiz numarayı **gönderim öncesi** reddeder — sağlayıcıya
gitmeden.

---

## Kuru çalışma (dry-run)

SMS gerçek para harcar. Ayarda `dry_run: true` iken sarmalayıcı isteği
sağlayıcıya **göndermez**, normalleştirme ve parça hesabını yapar, sahte bir
`job_id` döner ve kaydı denetim izine "kuru çalışma" olarak yazar.

Geliştirme ve test varsayılan olarak bu modda çalışır.
