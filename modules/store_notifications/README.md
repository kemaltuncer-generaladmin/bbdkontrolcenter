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
| Müşteri SMS'i | **Üç aşama** (sipariş alındı · kargoya verildi · teslim edildi): aşama başına Açık/Kapalı, düzenlenebilir metin, örnek veriyle önizleme, **tek segment zorlayan** sayaç ve gönderim izi. Elle gönderim **yoktur**. |
| Kurallar | Olay → koşul → şablon + kanal + gecikme. Aktif/pasif, son tetiklenme. **Mağaza tarafı salt okunur** (aşağıda). |
| Kanallar | SMTP + SMS sağlayıcı (**sırlar maskeli**), sessiz saatler, günlük limit, `[Bağlantıyı sına]`, mobil uygulama ayarları, **yerel denetim izi** (son 50 yazma, gerekçeleriyle). |
| Abonelikler | Bülten aboneleri, sayfalı. |
| Çıktı | Gönderim raporu PDF · SMS maliyet icmali PDF · görünen sayfa CSV · tüm geçmiş CSV. |

**Sağlar:** `store.notify.send` — Siparişler, Talepler, İadeler ve Deneme
Kulübü ekranları müşteriye bildirim göndermek için bunu çağırır. Sessiz saat,
günlük limit ve gerekçe kapıları yeteneğin İÇİNDEDİR: dört ekranın kendi
gönderim yolunu kurması, aynı disiplini dört kez (ve dört farklı biçimde
yanlış) kurmak olurdu.

**Sağlar:** `store.notify.stage` — müşteri aşama SMS'i. Siparişler ekranı
tetikler; metin, tek segment kuralı, üç katmanlı fren ve tekrar engeli burada.

## Müşteri aşama SMS'i (sipariş alındı · kargoya verildi · teslim edildi)

**Gönderim mağazadan değil, Kontrol Merkezi'nden çıkar — modülün geri kalanının
tersine.** Gerekçe tek cümle: **fren burada.** Mağaza tarafında kuru prova,
beyaz liste, tek segment zorlaması ve "aynı siparişe ikinci kez gönderme"
kaydı yok; Kontrol Merkezi'nde dördü de var ve ödeme linki SMS'i için zaten
kurulmuş durumda. Toplu bildirim ve e-posta **yine mağazadan** geçer.

| Aşama | Tetikleyici | Mesaj ne taşır |
|---|---|---|
| Sipariş alındı | Siparişler ekranının tarama işi (yeni sipariş) | ad, sipariş no |
| Kargoya verildi | "Kargoya ver" tamamlanınca (tek ya da toplu) | **firma + takip no + takip bağlantısı** |
| Teslim edildi | Geliver takip durumu teslime dönünce (webhook/senkron → tarama) | ad, sipariş no |

Kurallar:

- **TEK SEGMENT ZORUNLU.** Ölçüm `plan_text()` ile ve **bilerek uzun** örnek
  veriyle (`GUARD_SAMPLE`) yapılır: "Ayse Yilmaz" ile sığan metin "Mehmet Emin
  Karaosmanoglu" ile taşar. Tek parçayı aşan şablon **kaydedilmez**; ekran
  nedeni söyler ve sadeleştirmeyi önerir — metni kendiliğinden değiştirmez.
- **Varsayılan metinler ASCII'dir.** `ğ ı ş` iki septet yiyor; küçük `ç` ise
  GSM-7 **temel kümesinde yok** ve tek başına mesajı UCS-2'ye düşürüp 160
  sınırını 70'e indiriyor. Türkçe yazılabilir — sayaç maliyeti anında söyler.
- **Üç aşama da varsayılan KAPALI.** Göç çalışır çalışmaz müşterilere SMS
  gitmesi, kimsenin istemediği ve geri alınamayan bir davranıştır.
- **Aynı müşteriye aynı aşama için ikinci SMS gitmez.** `(stage, order_id)`
  yerel tabloda **benzersizdir**; webhook iki kez düşse, tarama iki kez koşsa
  ya da personel iki kez tıklasa da müşteri bir kez rahatsız olur, bir kez
  ödenir. Engel yalnız **gerçekten gitmiş** mesaj için çalışır: numarası
  olmadığı için gidememiş bir mesaj, numara düzeltilince gitmelidir.
- **Numarası olmayan/geçersiz müşteride sessiz geçilmez.** Satır
  "gönderilemedi: numara yok" diye ize yazılır ve ekranda listelenir. Numara
  ize **maskeli** girer (son dört hane).
- **Yarım kargo mesajı gönderilmez.** Takip numarası ya da bağlantısı yoksa
  mesaj durur ve nedeni yazılır; çalışmayan bir bağlantı göndermek hiç
  göndermemekten kötüdür. Bağlantı **uydurulmaz**: taşıyıcının kendi
  `trackingUrl` alanından, o yoksa yapılandırılmış önekten gelir.
- **Sessiz saat uygulanmaz** — ve bu bir unutma değildir. Aşama SMS'i
  işlemseldir ve **ertelenemez** (kuyruk yok); sessiz saatte "gönderme" demek,
  o siparişin takip kodunun müşteriye hiç ulaşmaması demekti. Üçü de zaten
  uyanık saatlerde tetikleniyor.

### Üç katmanlı fren

Gerçek SMS yalnız **üçü de** kapalıysa çıkar; ekran hangisinin tuttuğunu yazar:

| # | Anahtar | Varsayılan |
|---|---|---|
| 1 | `platform.notify.sms.dry_run` | AÇIK |
| 2 | `modules.store_notifications.lifecycle_sms_dry_run` | AÇIK |
| 3 | `modules.store_orders.stage_sms_dry_run` (tetikleyici) | AÇIK |

Dördüncü daraltma: `lifecycle_sms_allowlist` doluyken yalnız listedeki
numaralara gerçek mesaj gider — canlıya geçerken önce kendi numaranız.

## Ne yapmaz — ve neden

- **Kendi SMTP'sini ya da SMS istemcisini kurmaz.** E-posta ve toplu bildirim
  `store.api` geçidinden geçer (K4); mağazanın gönderim kaydı tektir ve ekran
  ikinci bir gerçeklik üretmez. **Tek istisna müşteri aşama SMS'idir** ve
  gerekçesi yukarıda: o üç mesaj için gereken frenler (kuru prova, beyaz liste,
  tek segment, tekrar engeli) mağaza tarafında yok, Kontrol Merkezi'nde var.
  Orada da kendi istemcisi kurulmaz — platformun `notify` yeteneği kullanılır.
- **Aşama SMS'ini elle göndermez.** Ekran metni yazdırır ve aşamayı açar;
  gönderim yalnız `store.notify.stage` yeteneğinden, yani siparişin kendi
  akışından çıkar. Aksi hâlde "ekrandan 1.800 kişiye aşama SMS'i" diye bir
  düğme olurdu.
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
- **Kural YAZMAZ — mağazada öyle bir uç yok.** Canlıda doğrulandı
  (2026-08-16, `route:list --path=api/admin/bbd/notifications`): önek altında
  `GET notifications` · `GET notifications/rules` · `POST notifications/send`
  var, kural için POST/PUT **yok**. Bir dönem "kural ucu henüz dağıtılmadı,
  liste 404 dönüyor; yazma da aynı pakette gelecek" deniyordu — **ikisi de
  artık doğru değil**: liste ucu yayında ve gerçek kuralları döndürüyor, yazma
  ucu ise gecikmedi, mağaza onu **bilerek** yazmadı. Kurallar veritabanında
  değil **kodda** yaşar (her biri bir `Event::listen` satırı ya da bir
  zamanlayıcı kaydı); düzenlenebilir bir kural tablosu aynı gerçeğin ikinci
  kopyasını üretirdi ve iki kopya ayrıştığında hiçbir belirti kalmazdı — tablo
  "kapalı" derken dinleyici bildirim göndermeye devam ederdi. Ekran listeyi
  gösterir, satırı salt okunur açar ve yazma düğmelerini **nedeniyle** kapatır.
  Liste ucu bir gün geri çekilirse "okunamadı" / "uç yayında değil" dalları
  yerinde durur (K7).
- **Şablon ve kural silmez, pasifleştirir** (ADR 0012). Geçmiş gönderimler
  hangi şablonla gittiğini göstermeye devam etmeli; kapalı kural neyin neden
  durdurulduğunu anlatır.
- **E-posta şablonlarını kopyalamaz.** Onların sahibi mağazadır ve Bagisto'nun
  kendi mailer'ı onları kullanır; kopya ilk değişiklikte yalan söyler. SMS ve
  Push şablonlarının Bagisto'da karşılığı YOK — onlar yereldedir.

## Mağazanın yanıt biçimi (canlıda doğrulandı — 2026-08-16)

Salt okunarak sınandı; varsayım değildir. Aşağıdakiler ilk kez 2026-08-13'te
ölçüldü ve **2026-08-16'da yeniden ölçülüp hepsi hâlâ geçerli bulundu**
(abone alanları, `perPage: 50` kırpması, `AdminMarketingTemplate` şeması,
SMTP anahtar grupları). Tarih, "bir zamanlar böyleydi" ile "bugün de böyle"
arasındaki farkı okuyucuya göstermek için duruyor.

**Kural listesi ayrı hikâye:** `GET /api/admin/bbd/notifications/rules` bir
dönem 404 dönüyordu, artık 200 dönüyor ve satırlar `{id, title, description,
trigger, event, schedule, channel, audience, handler, enabled, blockedBy}`
biçiminde geliyor — `id` **sayı değil dize** (`order.created.telegram`),
açıklık alanı `active`/`status` değil **`enabled`**, olay adı mağazanın kendi
olayı (`checkout.order.save.after`). Üçü de `messaging.rule_row` içinde
karşılanır; karşılanmasaydı ekran çalışan kuralları "Kapalı" ve "tanınmayan
olay" diye çizerdi.

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
`GET /printer` · `GET /stages` · `GET /stages/log`

Yazma: `POST /templates/preview` · `POST /templates` ·
`POST /templates/deactivate` · `POST /templates/test` · `POST /rules` ·
`POST /rules/{id}/toggle` · `POST /channels` · `POST /broadcast/preview` ·
`POST /broadcast/send` · `POST /history/{id}/resend` · `POST /stages/preview` ·
`POST /stages` · `POST /preview` · `POST /print` · `POST /export`

Aşama uçlarından **SMS gönderilmez**: yalnız metin ve Açık/Kapalı yazılır.
Gönderim tek yoldan, `store.notify.stage` yeteneğinden geçer — böylece
"ekrandan elle 1.800 kişiye aşama SMS'i" diye bir kapı açılmaz.

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
sayaç), `mod_store_notifications_lifecycle` (aşama metni + Açık/Kapalı),
`mod_store_notifications_lifecycle_log` (aşama gönderim izi —
`(stage, order_id)` **benzersizdir**, tekrarı önleyen tek kanıt budur).
Gönderim geçmişi, e-posta şablonu, kural ve abone listesi kopyalanmaz.

## Testler

```bash
.venv/bin/python -m pytest modules/store_notifications/tests -q
.venv/bin/ruff check modules/store_notifications
```

- `test_store_notifications_messaging.py` — yedi tuzağın saf mantığı.
- `test_store_notifications_service.py` — iş kuralları, geçit taklit edilir.
- `test_store_notifications_lifecycle.py` — üç aşama SMS'i. **Gerçek SMS
  gönderilmez ve bu kanıtlanır:** sahte sağlayıcının `sent` listesi gönderilen
  her mesajı tutar, "hiç gönderilmedi" iddiası o listenin BOŞ olmasıyla kurulur
  — "hata almadık" demek gönderilmediğini göstermez.
