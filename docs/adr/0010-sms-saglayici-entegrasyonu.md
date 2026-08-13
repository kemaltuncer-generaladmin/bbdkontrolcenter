# 0010 — SMS sağlayıcı entegrasyonu: sarmalanmış Netgsm

**Durum:** Kabul edildi · 2026-08-12

## Bağlam
Bildirimler SMS ile gidecek; sağlayıcı Netgsm. Resmi `netgsm-sms` 1.1.2 SDK'sı
ve `github.com/netgsm/netgsm-sms-python` örnekleri incelendi
(bulgular: [docs/netgsm-integration.md](../netgsm-integration.md)).

SDK doğrudan kullanılabilir mi sorusu için kaynak kodu okundu. Dört engel
bulundu — hepsi sessiz hata üretme potansiyeli taşıyor.

## Karar

### 1. SDK doğrudan kullanılmaz, sarmalanır
`km_platform/notify/providers/netgsm/` altında bir adaptör yazılır. Gerekçeler:

| Bulgu | Sonuç |
|---|---|
| SDK, yanıt gövdesindeki `code` alanını başarı yolunda denetlemiyor; resmi örnekler de yalnızca `jobid` okuyor | **Gönderilmemiş SMS başarılı sayılır.** Adaptör her yanıtta kodu denetler |
| Üç ayrı tarih biçimi (`send`, `report`, `inbox`) | Adaptör yalnızca `datetime` kabul eder, biçimi uç noktaya göre üretir |
| `timeout=30` sabit kodlanmış, `Config.REQUEST_TIMEOUT` kullanılmıyor | Adaptör `asyncio.wait_for` ile kendi zaman aşımını uygular |
| Senkron `requests` | Çağrılar `asyncio.to_thread` ile iş parçacığına taşınır |

### 2. Sağlayıcı sözleşme arkasındadır
`SmsProvider` protokolü tanımlanır (`notify/contracts.py`). Netgsm bu
protokolün **bir uygulamasıdır**, sözleşmenin kendisi değil. Üst katmanlar ve
modüller sağlayıcı paketini hiçbir zaman doğrudan görmez (K4).

Hatalar da sağlayıcıdan bağımsızdır: `SmsAuthError`, `SmsRejected`,
`SmsRateLimited`, `SmsProviderError`, `SmsTransportError`,
`SmsInvalidRecipient`, `SmsConfigError`. Netgsm kodları bunlara eşlenir;
çağıran taraf kod numarası görmez.

### 3. Doğrulama gönderim öncesi yapılır
Telefon numarası normalleştirilir (`0532...`, `+90 532...`, `(0532)...` →
`5321234567`) ve geçersizse sağlayıcıya **gitmeden** reddedilir. SMS para
harcadığı için hata sağlayıcıda değil, kapıda yakalanır.

### 4. Kodlama ve parça sayısı hesaplanır
Metin GSM-7'ye sığıyorsa `encoding` parametresi **gönderilmez** (SDK notu).
Türkçe kaydırma tablosundaki karakter (ğ Ğ ı İ ş Ş) varsa `encoding="tr"`
gönderilir ve o karakterler 2 septet sayılır. GSM-7'ye sığmayan içerik
(emoji) UCS-2'ye düşer: parça başına 70 karakter.

Toplu gönderimde Netgsm tek kodlama kabul ettiği için, partideki **herhangi
bir** mesaj Türkçe karakter içeriyorsa tümü `tr` ile gider.

Parça sayısı maliyet önizlemesidir; faturalanan kesin sayı sağlayıcı
raporundan okunur. Bu ayrım arayüzde de belirtilir.

### 5. Kuru çalışma varsayılan güvenlik ağıdır
`dry_run: true` iken istek sağlayıcıya gönderilmez; normalleştirme ve parça
hesabı yapılır, sahte `job_id` döner. Geliştirme ve test bu modda çalışır —
geliştirirken yanlışlıkla gerçek SMS gönderilip kontör harcanmaz.

### 6. İYS varsayılanı bilgilendirme
Gönderimlerimiz kurum personeline giden işletimsel uyarılardır →
`iysfilter = "0"`. Ticari içerik gönderilecekse `11`/`12` gerekir ve İYS onay
yönetimi devreye girer; bu uygulamanın kapsamı dışındadır ve kurumun uyum
sorumlusuna danışılmasını gerektirir.

## Sonuçlar
- Sağlayıcı değiştirilebilir: ikinci bir sağlayıcı `SmsProvider` protokolünü
  uygulayıp `providers/` altına eklenir, üst katman değişmez.
- Zaman aşımında `asyncio.to_thread` iş parçacığını **durduramaz**; istek arka
  planda tamamlanabilir. Bu yüzden zaman aşımı "gönderilmedi" değil,
  "**bilinmiyor**" anlamına gelir ve `SmsTransportError` mesajı yeniden
  denemeden önce rapor sorgusuyla doğrulamayı söyler. Çift gönderim riski
  budur.
- Rapor uç noktası dakikada 10 sorguyla sınırlı; kod 85 aynı numaraya dakikada
  20 görev sınırı koyuyor. Hız sınırlama `notify` yeteneği katmanında ele
  alınacak, adaptör yalnızca `SmsRateLimited` yükseltiyor.
- 35 birim testi ağa çıkmadan çalışıyor (`tests/core/test_notify_sms.py`).
