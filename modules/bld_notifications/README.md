# Bildirimler (`bld_notifications`)

Müşteri uygulamasında ve sitede gösterilen **uygulama-içi duyuruların**
yönetim ekranı: bakım bildirimi, tatil duyurusu, yeni hizmet tanıtımı.

Grup: **BLD** · Sözleşme: [`BLD/docs/control/notifications.md`](../../../BLD/docs/control/notifications.md)
(+ ortak kurallar `00-genel.md`) · Geçit: `bld.api` (K4)

## Push bildirimi YOKTUR

Bu bir eksiklik değil, bir iş kararıdır (BLD kararı 11). Duyuru
**ittirilmez**: müşteri uygulamayı açtığında görür. Ekranda "push", "gönder",
"ilet" gibi bir dil kullanılmaz — yöneticiye telefonların titreyeceği hissini
veren tek kelime, gerçekleşmeyecek bir vaattir. Acil bir şey duyurulacaksa SMS
kullanılır ve o başka bir ekranın işidir (`bld_sms`).

## Ekran

| Bölüm | Ne yapar |
|---|---|
| Liste | Süzgeçli, sayfalı duyuru listesi; "şu an görünen" sayısı ayrı rozet |
| Çekmece | Başlık, gövde, düzey, kitle, pencere, düğme, kapatılabilirlik |
| Önizleme | **Müşterinin göreceği kartın aynısı**, canlı |
| İstatistik | Görülme / kapatılma / oran + günlük döküm |
| Denetim izi | Bu ekrandan yapılan yazma **denemeleri** (yerel) |

## Bilinmesi gereken yedi şey

1. **`live` sunucuda hesaplanır.** Ekran onu yeniden hesaplamaz; yalnız
   *açıklar*: "yayında ama henüz başlamadı" ile "süresi doldu" aynı
   `live: false` değerinden çıkar ve ikisi bambaşka şeylerdir. İstemcide
   hesaplansaydı saati kaymış bir panelde duyuru bir gün erken "bitmiş"
   görünürdü.
2. **Gövde düz metindir, HTML değil.** Duyuru üç istemcide birden çiziliyor
   (Next.js, Flutter müşteri, ileride başkaları) ve HTML'i üçünde tutarlı
   çizmek imkânsız. Bu yüzden zengin metin düzenleyicisi yoktur ve önizleme
   metni `textContent` ile yazılır: yazılan `<b>` müşteride de `<b>` görünür ve
   önizleme bunu **yazarken** söyler.
3. **Kitlesi "Herkes" olan duyuru ölçülemez.** Giriş yapmamış ziyaretçinin
   kimliği yok, okunma kaydı yazılamıyor. Ekran "0 görülme" yazmaz,
   "ölçülemez" yazar: sıfır "kimse görmedi" demektir ve çalışan bir duyuruyu
   başarısız gösterirdi.
4. **Yayından kaldırma ucu yoktur.** Yolu bitiş gününü geçmişe çekmek ya da
   arşivlemektir; üçüncü bir yol, "duyuru neden görünmüyor" sorusunun üç ayrı
   cevabı olması demekti.
5. **Arşiv yumuşaktır.** Satır silinmez, duyuru anında görünmez olur ve
   istatistik çalışmaya devam eder — bir duyurunun kaç kişiye ulaştığı
   sonradan sorulan bir sorudur.
6. **Pencere gün olarak seçilir, an olarak yazılır.** "Son görüneceği gün S"
   alanı `ends_at = (S+1)T00:00:00Z` olarak gider; sözleşmenin kendi örneğiyle
   birebir aynı (30 Ağustos duyurusunun bitişi `2026-08-31T00:00:00Z`). Saat
   dilimi çevirisi **yapılmaz** (`00-genel.md` §6): pencere İstanbul saatiyle
   03:00'te açılıp kapanır ve ekran bunu yazar. Sunucudaki an gün başlangıcına
   oturmuyorsa gün alanları **boş bırakılır** ve durum açıkça söylenir; sessizce
   yuvarlamak, yöneticinin dokunmadığı bir pencereyi değiştirmek olurdu.
7. **Kapatılamaz duyuru yalnız "Kritik" olabilir.** Kapatılamayan bir
   bilgilendirme, müşteri uygulamasını kullanılamaz hâle getirir.

## İzinler — üç anahtar

| Anahtar | Ne açar |
|---|---|
| `bld_notifications.view` | Liste, istatistik, yerel iz |
| `bld_notifications.manage` | Taslak oluşturma ve düzenleme |
| `bld_notifications.publish` | **Yayınlama ve arşivleme** (dışa dönük) |

Üçüncüsü ayrı durur çünkü onu taşıyan kişi **müşteriye görünen metni**
değiştirir. Taslak yazmak dışa dönük değildir; metni hazırlayan kişiye
hazırladığını yayınlama yetkisini otomatik vermemek için `manage` ile kalır.

Yıkıcı/dışa dönük işlemler **PIN değil gerekçe** ister (ADR 0012): ayrı izin
anahtarı + gerekçe (en az 10 karakter, backend'de de doğrulanır) + çift denetim
satırı. Hiçbir izin `destructive: true` taşımaz.

## Uçlar

| Metot | Yol | İzin |
|---|---|---|
| GET | `/api/bld_notifications/notices` | `view` |
| GET | `/api/bld_notifications/notices/{id}/stats` | `view` |
| GET | `/api/bld_notifications/audit` | `view` |
| POST | `/api/bld_notifications/notices` | `manage` |
| PATCH | `/api/bld_notifications/notices/{id}` | `manage` |
| POST | `/api/bld_notifications/notices/{id}/publish` | `publish` |
| POST | `/api/bld_notifications/notices/{id}/archive` | `publish` |

`DELETE` fiili **yoktur**: yapılan iş silme değil pasifleştirmedir (kit
kuralı 8) ve gerekçe gövdede taşınır.

Kısmi güncellemede **eş alanlar birlikte gönderilir**
(`starts_at`+`ends_at`, `action_label`+`action_url`): bu iki kural ikisinin
*birlikte* hâli hakkındadır ve sözleşmede tek duyuru okuyan bir uç yok — yalnız
birini alan bir `PATCH`, kuralı yerelde doğrulanamaz bırakırdı.

## Sunucu ucu henüz yayında değil

Duyuru tabloları (`veykemtu_notifications`,
`veykemtu_notification_reads`) ve `/api/control/notifications/*` uçları **başka
bir ajanın kulvarında** ve sonraki fazda geliyor. O ana kadar geçit temiz bir
`control_endpoint_missing` kodu veriyor; ekran "sunucu eklentisi güncellenince
çalışacak" der ve zarifçe bozulur (K7). `not_found` ile karıştırılmaz: ilki
"uç yok", ikincisi "kayıt yok".

## Yerel tablo

`mod_bld_notifications_audit` — yazma **denemelerinin** izi. BLD de
`veykemtu_control_audit` tutuyor ama o kayıt yalnız sunucuya **ulaşan** isteği
bilir; ağ koparsa "kim hangi duyuruyu yayınlamaya çalıştı" sorusunun cevabı
yalnız burada kalır. Uzak veri **kopyalanmaz**.

## Doğrulama

```bash
cd "Kontrol Merkezi"
.venv/bin/python -m pytest modules/bld_notifications
.venv/bin/ruff check .
node --check modules/bld_notifications/ui/panel/index.js
```
