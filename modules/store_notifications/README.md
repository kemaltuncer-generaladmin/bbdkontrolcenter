# Bildirimler

Mağaza müşteri iletişimi: şablon, olay kuralı, kanal ayarı, gönderim geçmişi,
abonelikler ve toplu bildirim. Canlıda alıcı kitlesi **1.800+ kişi**.

Grup: **BBD Store** · CSS öneki: `nt` · Rapor rafı:
`Raporlar/Mağaza/Müşteri/<yıl>/<ay>`

## Ne yapar

| Sekme | Davranış |
|---|---|
| Gönderim geçmişi | **Sunucu tarafı sayfalama** (100/sayfa). Süzgeç: arama · kanal · durum · olay · tarih aralığı · maliyet aralığı · anahtar `Başarısızlar`. Başarısız satırda `[Yeniden gönder]`. |
| Şablonlar | Değişken paleti (tıklayınca **imlece ekler**), örnek veriyle önizleme, SMS karakter/parça/kredi sayacı, `[Kendime test gönder]`, `[Toplu gönderim]`. |
| Kurallar | Olay → koşul → şablon + kanal + gecikme. Aktif/pasif, son tetiklenme. |
| Kanallar | SMTP + SMS sağlayıcı (**sırlar maskeli**), sessiz saatler, günlük limit, `[Bağlantıyı sına]`, mobil uygulama ayarları, **yerel denetim izi** (son 50 yazma, gerekçeleriyle). |
| Abonelikler | Bülten aboneleri, sayfalı. |
| Çıktı | Gönderim raporu PDF · SMS maliyet icmali PDF · görünen sayfa CSV · tüm geçmiş CSV. |

**Sağlar:** `store.notify.send` — Siparişler, Talepler, İadeler ve Deneme
Kulübü ekranları müşteriye bildirim göndermek için bunu çağırır. Sessiz saat,
günlük limit ve gerekçe kapıları yeteneğin İÇİNDEDİR: dört ekranın kendi
gönderim yolunu kurması, aynı disiplini dört kez (ve dört farklı biçimde
yanlış) kurmak olurdu.

## Ne yapmaz — ve neden

- **Kendi SMTP'sini ya da SMS istemcisini kurmaz.** Gönderim `store.api`
  geçidinden geçer (K4); mağazanın gönderim kaydı tektir ve ekran ikinci bir
  gerçeklik üretmez. `notify` platform yeteneği yalnızca SMS katmanının
  durumunu göstermek ve `[Bağlantıyı sına]` için kullanılır.
- **Sır yazmaz.** Parola ve API anahtarı kasada durur (K8); ekran son dört
  haneyi gösterir ve nereden değiştirileceğini söyler. Ham değer yanıtta,
  log'da ve raporda bulunmaz.
- **Toplu bildirimi tek tıkla göndermez.** Önce kuru prova: alıcı sayısı ve
  tahmini maliyet mağazadan sorulur, jeton üretilir; gerçek gönderim o jetonla
  ve gerekçeyle gelir. Arada süzgeç değişse bile önizlenen iş uygulanır.
- **Alıcı kitlesi serbest metin değildir.** `subscribers` · `customers` ·
  `recent_buyers` dışındaki bir ad kuru provaya bile girmez; kapı backend'dedir
  ve mağazaya istek çıkmadan kapanır. Laravel tanımadığı alanı sessizce yok
  sayar (canlıda kanıtlandı), yani yazım hatalı bir kitle adı süzgeci düşürür
  ve iş "kime?" sorusu olmayan — yani herkese giden — bir gönderime dönerdi.
  Liste `GET /catalog` ile panele verilir; panel kendi kopyasını tutmaz.
- **Şablon ve kural silmez, pasifleştirir** (ADR 0012). Geçmiş gönderimler
  hangi şablonla gittiğini göstermeye devam etmeli; kapalı kural neyin neden
  durdurulduğunu anlatır.
- **E-posta şablonlarını kopyalamaz.** Onların sahibi mağazadır ve Bagisto'nun
  kendi mailer'ı onları kullanır; kopya ilk değişiklikte yalan söyler. SMS ve
  Push şablonlarının Bagisto'da karşılığı YOK — onlar yereldedir.

## Mağazanın yanıt biçimi (canlıda doğrulandı — 2026-08-13)

Salt okunarak sınandı; varsayım değildir.

- **Alan adları camelCase'tir.** Abone satırı `{id, email, isSubscribed,
  customerId, customerName, channel, createdAt}` döner. `is_subscribed` diye
  bakan kod hiçbir şey bulamaz ve **istisna da atmaz**; üstüne `as_int(None, 1)`
  varsayılana düşerdi: abonelikten çıkmış müşteri "Abone" görünür ve toplu
  gönderim listesine girerdi. Her okuma `messaging.pick()` üzerinden geçer.
- **`isSubscribed` bir JSON mantıksalıdır** (`true`/`false`), sayı değil.
  Mantıksal alanlar `as_bool` ile çözülür; `as_int` kullanılmaz.
- **E-posta şablonunun konusu yoktur.** `AdminMarketingTemplate` yalnız `name` ·
  `status` · `content` tanır ve POST'ta üçü de zorunludur. Konu ZORUNLU DEĞİL,
  KABUL EDİLMEZDİR: yazılan konu Laravel tarafından sessizce yok sayılırdı.
- **Şablon durumu kelimedir:** `active | inactive | draft`. `1`/`0` göndermek
  422 döndürür; ekran "kaydedildi" derken şablon değişmemiş olurdu.
- **SMTP ayarı iki ayrı gruptadır:** bağlantı `emails.configure.smtp.*`,
  gönderen kimliği `emails.configure.email_settings.sender_email` /
  `sender_name`. `from_address`/`from_name` diye bir anahtar YOKTUR.
- **Kanal süzgeci gönderilmez.** Canlıda tek kanal var (`id: 1`,
  `code: "default"`) ve Siparişler ekranında `channel=default` hata vermeden
  sıfır kayıt döndürüyor. Uygulandığı kanıtlanamayan süzgeç listeyi sessizce
  boşaltır; `channel` ayarı yalnız `/configuration?channel=` okumasında kullanılır.
- **`per_page` 50'ye kırpılır** (`?per_page=200` → `meta.perPage: 50`), `meta`
  camelCase'tir (`currentPage` · `perPage` · `lastPage` · `total`). Rapor
  taraması bu yüzden 50'lik sayfa ister: 100 istemek 50 almaktı ve sayfa tavanı
  satır tavanının yarısında duruyordu.
- **`is_subscribed` ve `email` süzgeçleri gerçekten uygulanıyor** (canlıda
  ölçüldü: `is_subscribed=1` → 5, `is_subscribed=0` → 0, `email=zafer` → 1).
  Tanınmayan bir parametre ise sessizce yutuluyor (`?uydurma_suzgec=xyz` → 5
  kayıt, hata yok) — süzgeç göndermek onun uygulandığı anlamına gelmez.

## Yedi tuzak

Hepsinin karşılığı `backend/messaging.py` içinde bir fonksiyon ve
`tests/test_store_notifications_messaging.py` içinde adı tuzağı söyleyen bir
testtir.

1. **Bilinmeyen değişken boşa çevrilmez.** `{{musteri_adi}}` yerinde kalır ve
   `missing` listesiyle bildirilir; "Sayın , siparişiniz kargolandı" 1.800
   kişiye gitmez.
2. **Türkçe harf SMS'i pahalılaştırır** (ş/ğ/ı 2 septet). Sayaç hangi
   karakterin ne kadara mal olduğunu söyler ve sadeleştirmeyi **önerir** —
   metni otomatik değiştirmez.
3. **`₺` GSM-7'de yoktur:** tek karakter mesajı UCS-2'ye düşürür ve 160 sınırı
   70'e iner. Örnek veride para birimi bilerek `TL` yazılır, yoksa önizleme
   gerçek şablondan üç kat pahalı görünürdü.
4. **Sessiz saat başlangıcı = bitişi** olursa 24 saatlik pencere olur ve
   hiçbir bildirim gitmez; pencere uygulanmaz ve neden söylenir.
5. **Gece yarısını aşan pencere** (22:00–08:00) düz karşılaştırmayla hiçbir
   zaman doğru çalışmaz; sarma ayrıca ele alınır.
6. **Şablon kimliği kaynağını taşır** (`store:12` · `local:3`). Çıplak sayı,
   mağazadaki 12 numaralı e-posta şablonu ile yereldeki 12 numaralı SMS
   şablonunu karıştırır.
7. **Maliyet bilinmiyorsa sıfır gösterilmez.** Birim fiyat girilmemişse `—`
   yazılır; sıfır yazmak "bedava" demektir.

## Uçlar

`/api/store_notifications` öneki altında. Hepsi `requires(...)` taşır (K9).

Okuma: `GET /catalog` · `GET /history` · `GET /templates` · `GET /rules` ·
`GET /channels` · `GET /channels/test` · `GET /subscribers` · `GET /audit` ·
`GET /printer`

Yazma: `POST /templates/preview` · `POST /templates` ·
`POST /templates/deactivate` · `POST /templates/test` · `POST /rules` ·
`POST /rules/{id}/toggle` · `POST /channels` · `POST /broadcast/preview` ·
`POST /broadcast/send` · `POST /history/{id}/resend` · `POST /preview` ·
`POST /print` · `POST /export`

## İzinler

| Anahtar | Ne açar |
|---|---|
| `store_notifications.view` | Ekran, önizleme, rapor, CSV |
| `store_notifications.manage` | Şablon, kural, sessiz saat, limit, mobil ayar |
| `store_notifications.send` | Kendine test ve yeniden gönderme (para harcar) |
| `store_notifications.broadcast` | Toplu bildirim — 1.800+ kişi, geri alınamaz |

## Yerel tablolar

Yalnız Bagisto'da **karşılığı olmayan** veri:
`mod_store_notifications_audit` (gerekçe),
`mod_store_notifications_templates` (SMS/Push şablonu),
`mod_store_notifications_prefs` (sessiz saatler, günlük limit),
`mod_store_notifications_sends` (toplu gönderim önizleme jetonu ve günlük
sayaç). Gönderim geçmişi, e-posta şablonu, kural ve abone listesi kopyalanmaz.

## Testler

```bash
.venv/bin/python -m pytest modules/store_notifications/tests -q
.venv/bin/ruff check modules/store_notifications
```
