# Abonelikler (`bld_subscriptions`)

Catering **aboneliklerinin** yönetimi: siteden gelen teklif talepleri, abonelik
kuralı, imzalı sözleşme ve 30 günlük peşin dönem ödemeleri.

Abonelik bir sipariş **değil**, sipariş üreten kuraldır. Gece işi `runsOnDate()`
true olan her (abonelik × teslimat noktası) için o günün siparişini üretir ve
sipariş mutfağa **07:00'de** düşer. Kural sonradan değişse bile üretilmiş
sipariş değişmez; onu düzeltmek **Sipariş Yönetimi** (`bld_orders`) ekranındaki
revizyonun işidir.

Grup: **BLD** · İzinler: `bld_subscriptions.view`, `bld_subscriptions.manage`

## Sözleşme

Uçlar, alan adları, hata kodları ve durum değerleri **dondurulmuştur** ve
buradan okunur:

- `BLD/docs/control/subscriptions.md` — 28 uç, abonelik/talep/sözleşme/ödeme şemaları
- `BLD/docs/control/00-genel.md` — ortak gövde (`actor`/`reason`/`dry_run`),
  sayfalama, biçimler, hata kodları, denetim izi
- `modules/bld_api/README.md` §6 — geçidin donmuş metot tablosu

Buradan okunmayan hiçbir alan adı, yol ya da başlık uydurulmaz.

## Ekran ne yapar

| Sekme | İş |
|---|---|
| **Talepler** | Siteden gelen "Teklif Al" kuyruğu: maskeli liste, maskesiz kayıt, durum + iç not, **aboneliğe çevirme**. Altında *fiyat/sözleşme bekleyen abonelikler*. |
| Aktif | `active` abonelikler; satır → kural, takvim, üretim defteri, istisnalar, fiyat geçmişi. |
| Duraklatılmış | `paused` abonelikler; aynı çekmece. |
| Sözleşmeler | Abonelik seç → sözleşme listesi + **imza bağlantısı gönder / yeniden gönder / iptal**. |
| Ödemeler | Abonelik seç → dönem tablosu + **dönem borcu aç** ve **tahsil edildi işaretle**. |

Kaldırılan `bld_quotes` modülünün işi **Talepler** sekmesine devroldu. Ayrı bir
modül, aynı sözleşmenin (`subscriptions.md`) iki modüle bölünmesi olurdu.

## OTP ve sözleşme SMS'i — burada YOK

İş kararı 9 sözleşmeyi imzalı link + SMS OTP onayına bağlıyor. O akışın
tamamı **sunucudadır**: müşteri bağlantıyı açar, metni okur, telefonuna gelen
kodu **sunucunun imza sayfasında** girer.

Kontrol Merkezi yalnız **tetikler** (`POST /{id}/contracts`). Bu ekranda kod
kutusu, "kodu doğrula" düğmesi ya da SMS gövdesi **yoktur ve olmayacaktır** —
ikinci bir OTP uygulaması, iki yerde ayrışabilen bir güvenlik akışı üretirdi ve
bu modülü `bld_sms`e bağlardı (K3). Akış şeridi yalnız *beklediğini* söyler.

İmza bağlantısı (`sign_url`) **yalnız** `send_sms: false` iken döner ve
**yalnız ekranda** gösterilir; yerel denetim izine yazılmaz. SMS gönderildiğinde
bağlantı hiç dönmez: zaten müşterinin telefonundadır ve panelde de göstermek onu
ikinci bir yerde sızdırılabilir kılardı.

## İki izin, üç değil

`bld_orders` iptali üçüncü bir anahtara aldı; burada almadık ve fark **paradır**.
Sipariş iptali ödenmiş bir siparişin **iade kaydını** üretir. Abonelik iptali
para üretmez: kuralı durdurur, üretilmiş siparişlere **dokunmaz** ve onları
düşürmek zaten `bld_orders.cancel` iznini ister. Üçüncü bir anahtar, taşıdığı
hiçbir ayrıcalık olmadan izin kataloğunu şişirirdi.

Yıkıcılığın karşılığı **gerekçedir** (ADR 0012): en az 10 karakter (backend'de
de doğrulanır) + iki denetim satırı ("ne denendi", "ne oldu"). PIN istenmiyor,
bu yüzden hiçbir izin `destructive: true` taşımaz.

**Gerekçe sınırı 10–500'dür**, `bld_orders`taki 160 değil: o daralma
`veykemtu_order_revisions.reason` sütunundan geliyor ve abonelik yazmaları o
sütuna hiç dokunmuyor (`00-genel.md` §3).

## Kuru prova — burada gerçekten işe yarıyor

Panel iki yerde açıkça "Önce prova et" sunar, çünkü sunucu prova modunda da
**hesap yapar**:

- **Talebi aboneliğe çevirme** → `first_service_dates`: kuralın gerçekten hangi
  günleri ürettiği, kaydetmeden görünür. Servis günü seçimindeki bir hata
  (cumartesi işaretlemek gibi) ancak burada ya da mutfakta fark edilirdi.
- **Dönem borcu açma** → `amount_kurus` + `order_count`: dönemdeki üretilmiş ve
  iptal edilmemiş siparişlerin toplamı, hiçbir satır yazılmadan.

Servis geçide `dry_run=` değerini **her çağrıda açıkça** geçer ve geçidin
varsayılanına güvenmez: `config/local.yaml` git dışıdır ve orada `true`
yazabilir; bayrağı atlayan bir modül hiçbir şey yazmadan `{"ok": true}` alır ve
ekran "gönderildi" der (`bld_api/README.md`).

## Yeniden yazılmayan üç kural

| Kural | Nerede | Neden burada değil |
|---|---|---|
| Üretim takvimi | `Subscription::upcomingServiceDays()` | Kapalı gün + duraklama + istisna birlikte uygulanıyor; ekranda tekrarlansaydı ayrışmanın fark edileceği yer **mutfak** olurdu. |
| Gecikmiş borç (`overdue`) | Sunucu | Saati kaymış bir panelde borç bir gün erken kırmızıya dönerdi. |
| Sözleşme durum makinesi | Sunucu | `signed` terminaldir; ekrandaki `open`/`terminal` alanları yalnız **düğme çizmek** içindir, kapıyı sunucu tutar. |

## Yerel tablolar

Uzak verinin **kopyası tutulmaz**. `mod_bld_subscriptions_audit` yalnız BLD'de
karşılığı olmayanı saklar:

- **Deneme kaydı.** Sunucunun izi (`veykemtu_control_audit`) yalnız sunucuya
  *ulaşan* isteği bilir. Sözleşme gönderilirken bağlantı düşerse müşteriye SMS
  gidip gitmediği belirsizdir; "kim denedi" sorusunun cevabı yalnız burada kalır.
- **Fiyat sütunu.** `price_kurus` ayrı bir kolondur, `detail` JSON'unun içinde
  değil: *"fiyatı kim, ne zaman, neden anlaştı"* bu ekranın en çok sorulan
  sorusudur ve JSON içinden aranan bir alan ne sıralanabilir ne indekslenebilir.
  Abonelik çekmecesindeki **Fiyat geçmişi** kartı bu sütunu okur.

`mod_bld_subscriptions_prefs` yalnız görüntüleme tercihini tutar (sayfa boyutu,
takvim penceresi, bağlantı ömrü varsayılanı) ve BLD'yi etkilemez.

## Yoklama yok

Ayarda `poll_seconds` **yoktur** ve bu bilinçlidir. Abonelik saatler-günler
ölçeğinde değişir: kural yazılır, sözleşme gönderilir, müşteri ertesi gün
imzalar. 15 saniyede bir yoklayan bir ekran, paylaşılan `bld-control-panel`
kovasını (3000/saat/IP, **tüm** BLD ekranları için) hiçbir şey öğrenmeden yakar
ve ikinci bir yöneticinin ekranını 429'a düşürürdü. Liste "Yenile" ile tazelenir.

## Müşteri araması yok

Abonelik açarken müşteri kimliği **elle yazılır**. Müşteri okumaları KVKK gereği
sunucuda tek tek denetleniyor (`customers.md` §9); bu ekrandan açılan bir arama
kutusu, denetim izini abonelik açan herkesin her denemesiyle doldurup içindeki
gerçek erişimi görünmez kılardı. Müşteri kartı `bld_customers` ekranının işi ve
bu uç müşteri **yaratmaz** — hesap açmak parola ve e-posta doğrulaması ister.

## Tek eylem, tek ev

Sözleşme göndermek/iptal etmek **yalnız** "Sözleşmeler" sekmesinde, dönem borcu
ve tahsilat **yalnız** "Ödemeler" sekmesinde durur. Abonelik çekmecesi ikisini de
**okur** ama düğmesini açmaz: aynı işi iki yerden yapabilmek "hangisinden
yaptım" sorusunu doğurur ve iki ekran arasındaki küçük fark zamanla ayrışır.

## Sunucu ucu henüz yoksa

Sunucu tarafı paralel yazılıyor. Dağıtılmamış bir uçta geçit
`control_endpoint_missing` döndürür; okuma yanıtları bunu `missing_endpoint`
ile taşır ve ekran **kırmızı hata yerine sarı bir bilgi kutusu** çizer. Bu
beklenen bir durumdur, arıza değil — kırmızı bir kutu personelin her açılışta
var olmayan bir sorunu bildirmesi olurdu.

## Testler

```bash
cd "Kontrol Merkezi"
.venv/bin/python -m pytest modules/bld_subscriptions
.venv/bin/ruff check .
```

- `test_bld_subscriptions_subs.py` — saf yardımcılar (biçim, etiket, ön denetim,
  akış şeridi). Ağa çıkmaz, servis kurmaz.
- `test_bld_subscriptions_service.py` — iş kuralları, yazma zinciri, kuru prova,
  K7. `FakeApi` geçidin metot adlarını **birebir** taşır; uydurma bir ad
  testleri yeşil tutar ama canlıda `AttributeError` verir ve servis onu K7
  gereği yuttuğu için düşmüş bir sunucudan ayırt edilemez.
- `test_bld_subscriptions_routes.py` — **elle yazılmış** uç → izin tablosu, rota
  sırası ve gövde alan adları.
