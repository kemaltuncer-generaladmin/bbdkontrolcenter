# KDS Yönetimi

Mutfak ekranı (kasa) cihazlarının Kontrol Merkezi'nden yönetimi: bağlantı ve
yazıcı durumu, eşleme kodu, uzaktan uygulanan 24 yönetilen ayar, komut kuyruğu,
sipariş revizyonu ve durum akışı, basılan fişlerin denetim dökümü.

Kasa çekmecesi, **kasadaki ayarlar ekranının aynasıdır** (K-22 §6): on bölüm,
kasadakiyle aynı sırada, her bölümün düğmeleri kendi bölümünde.

Grup: **BLD** · CSS öneki: `bk`
İzinler: `bld_kds.view`, `bld_kds.manage`, `bld_kds.devices`

Sözleşme: **K-21** (Kontrol Merkezi ↔ BLD KDS köprüsü) + **K-22** (olay bazlı
sesler, üç yeni komut, zengin telemetri, kuru provanın kaldırılması). Uçlar,
alan adları ve ayarların listesi orada sabittir; burada uydurma alan eklenmez.

## Nereden bakar

BLD'ye **yalnız `bld.api` yeteneğinden** bakar (K4). Ham `httpx` yoktur: imzalı
`X-Control-Signature`, zaman penceresi, nonce, oran sınırı ve `dry_run` taşıma
`bld_api` geçidinin işidir. Kasa gibi eşleşmez — Kontrol Merkezi'nin kendi
imzalı kapısı vardır.

**Uzak verinin kopyası tutulmaz.** Kasa saniyede bir yokluyor; yerel bir kopya
her zaman bir tur geride kalır ve "çevrimiçi" görünen bir kasa yarım saattir
kapalı olabilir.

## Uçlar

Önek `/api/bld_kds`. Hepsi `requires(...)` ile korunur (K9) ve
`test_bld_kds_routes.py` tabloyu kendine karşı doğrular.

| Metot | Yol | İzin | Ne yapar |
|---|---|---|---|
| GET | `/overview` | `view` | Cihaz/sipariş/fiş sayaçları + `printer_available` |
| GET | `/devices` | `view` | Cihaz listesi + ayar sözleşmesi + komut kataloğu |
| POST | `/devices` | `manage` | Yeni cihaz + ilk eşleme kodu |
| PATCH | `/devices/{id}` | `manage` | **Yalnız ad** |
| POST | `/devices/{id}/pairing-code` | `manage` | Yeni kod (10 dk) |
| POST | `/devices/{id}/revoke` | **`devices`** | İptal. Satır silinmez |
| PATCH | `/devices/{id}/settings` | `manage` | 24 ayarı **kısmi** yazar |
| GET | `/devices/{id}/commands` | `view` | Son komutlar, üç damgasıyla |
| POST | `/devices/{id}/commands` | `manage` **+ `devices`** | Komut kuyruğa atar |
| GET | `/print-jobs` | `view` | Basılmış fişlerin **denetim** kaydı |
| GET | `/orders` | `view` | Aktif siparişler |
| GET | `/orders/{id}` | `view` | Düzenlenebilir sipariş görünümü |
| GET | `/orders/{id}/revisions` | `view` | Revizyon geçmişi |
| POST | `/orders/{id}/revisions` | `manage` | Revizyon — **tam kalem listesi** |
| POST | `/orders/{id}/status` | `manage` | Durum geçişi |
| GET | `/menu` | `view` | "Ürün ekle" seçicisinin listesi |

`POST /devices/{id}/commands` iki izinden **en az birini** ister; `restart`,
`clear_failed`, `clear_queue`, `update` ve `unpair` için `bld_kds.devices`
**şarttır** ve servis bunu ayrıca denetler (çift kapı).

## Üç izin, iki değil

`bld_kds.devices` ayrı durur çünkü bu yetkiyi taşıyan kişi **mutfağı sipariş
göremez hâle getirebilir**:

| Komut | Ne yapar |
|---|---|
| `restart` | Ekranı kapatır; systemd geri getirene kadar mutfak kör kalır |
| `clear_failed` | Basılamamış fişleri düşürür; o fişler **bir daha basılmaz** |
| `clear_queue` | Üstelik **bekleyen** işleri de düşürür — `clear_failed`in üst kümesi |
| `update` | `.deb` kurar ve servisi yeniden başlatır; kurulum boyunca ekran gider |
| `unpair` | Cihaz token'ını siler; kasa **sahada elle** eşleştirilene kadar sipariş göremez |

Cihaz iptali de buradadır ve iptal edilen kasa bir daha bağlanamaz.

`clear_queue` K-22 §2'de açıkça "yıkıcı" diye işaretlenmedi; bu listeye
**bilerek** eklendi. Yaptığı iş `clear_failed`in üst kümesidir ve dışarıda
bırakmak, yalnız `bld_kds.manage` taşıyan birinin kapıdan geçen
`clear_failed`ten **daha fazlasını** yapabilmesi olurdu. Bu, Kontrol
Merkezi'nin kendi izin politikasıdır; BLD tarafında karşılığı yoktur.

BLD tarafında da ayrı yetki kutusu var (`Veykemtu.KitchenDevices`); iki tarafın
ayrımı aynı tutulur.

**İptal edilmiş cihaza eşleme kodu üretilmez.** Nedeni kolaylık değil yetki:
kod üretimi `manage`e bağlı, iptal `devices`e. Kod üretilebilseydi yalnız
`manage` taşıyan biri, `devices` taşıyan kişinin kararını geri alırdı.

## Anahtar adları

Yanıtlar ve gövdeler K-21'in **snake_case** sözlüğünü korur. Tek istisna
gövdedeki **`dryRun`** alanıdır (panel→Kontrol Merkezi sınırında camelCase,
`store_orders` deseni); `dry_run` adına çeviriyi geçit yapar.

Gövdeler `extra="forbid"` taşır: yanlış yazılan bir alan 422 ile döner. İki adı
birden kabul etmek, kuru prova sanılan bir isteğin gerçek yazma yapmasına yol
açardı.

## Yazma zinciri

Her yazma ucu beş adımı bu sırayla uygular:

1. **gerekçe denetimi** (min 10 — arayüzde zorunlu göstermek yetmez, K9)
2. **taze okuma** (cihaz/sipariş aradan değişmiş olabilir)
3. yerel iz `result="denendi"` ← *ağ koparsa geriye yalnız bu kalır*
4. geçit çağrısı
5. yerel iz `ok` / `dry_run` / `hata`

Üçüncü adım kritiktir: `restart` gönderilirken ağ koparsa komutun kuyruğa girip
girmediği bilinmez; iz olmasa kimin denediği de bilinmezdi.

**Kuru prova kaldırıldı** (K-22 §4): panelde şalteri yok, `dryRun` alanı
gönderilmiyor ve `dry_run_default` **kapalı**. Uç ve geçit `dryRun: true`
kabul etmeye devam eder — sözleşme §4 additive'dir ve kaldırmak alanı açıkça
gönderen eski çağrıları kırardı. Kuru provada **olay yayınlanmaz**; BLD'de
hiçbir şey değişmedi.

**Gerekçe zorunluluğu durur** (min 10, `confirmWithReason`). Kuru prova bir
*güvenlik ağıydı*, gerekçe bir *denetim kaydıdır*; biri kalktı diye öteki
kalkmaz. Yazmanın ikinci emniyeti geçidin `read_only` acil frenidir.

**Eşleme kodu denetim izine yazılmaz.** Kod 10 dakikalık bir sırdır ve iz
satırı silinmez; ize düşseydi ömrü sonsuz olurdu.

## Panelin on bölümü — kasadaki ayarlar ekranının aynası

Kasa çekmecesi, kasadaki ayarlar ekranıyla **aynı sırada** on bölüme ayrılır
(K-22 §6). Sıra keyfî değil: mutfaktaki personelle telefonda konuşan yönetici
"üçüncü bölümdeki ses ayarı" dediğinde ikisi de aynı yere bakabilmeli. Her
bölümün başında **"boş = yönetici dokunmadı"** ipucu durur ve her kasa
düğmesinin merkezdeki karşılığı **kendi bölümündedir** — "deneme fişi" yazıcı
ayarının, "alarmı sustur" ses ayarının yanında.

| # | Bölüm | İçerik | Düğmeler |
|---|---|---|---|
| 1 | Sunucu | adres (**salt okunur**), cihaz kimliği, eşleme durumu ve kodu | eşleme kodu yenileme |
| 2 | Yazıcı | aygıt yolu, kod sayfası | `test_receipt` |
| 3 | Ses ve alarm | açık/kapalı, seviye, çıkış, **5 olay tek tek**, alarm tekrarı/üst sınırı, susturulabilirlik + ses/alarm telemetrisi | `silence_alarm` |
| 4 | Anons (TTS) | açık/kapalı, hız | — |
| 5 | Zamanlama | yoklama, sağlık, bağlantı alarmı | — |
| 6 | Eşikler | uyarı, geciken | — |
| 7 | Dokunmatik | dokunmatik kip | — |
| 8 | Kilit politikası | 7 alan (K-21) | — |
| 9 | Kuyruk | bekleyen, hatalı, **en eskisinin yaşı** | `clear_failed`, `clear_queue` |
| 10 | Cihaz | sürüm, son görülme, sağlık ayrıntısı, son hata | `update`, `restart`, `unpair`, cihaz iptali |

**Kasa adı** ve **fiş yeniden basma** bu on bölümün dışında, kendi kartlarında
durur: ilki Kontrol Merkezi'nin kendi kaydıdır ve kasaya hiç gitmez, ikincisi
bir ayar değil bir *sipariş* işidir ve sipariş kimliği ister.

**Beş sesin üç hâli.** `disabled_sound_events` telde virgüllü bir dizedir ama
panelde beş onay kutusudur. Kutuların üstünde ayrı bir "dokunulmadı /
merkezden yönetiliyor" seçimi var, çünkü onay kutusu üçüncü hâli anlatamaz:
`null` "yönetici dokunmadı" (kasa kendi listesini korur), boş dize "hiçbiri
kapalı olmasın". Tek kutu değişse bile tele **listenin tamamı** gider —
yalnız değişeni göndermek dokunulmayan dört sesi sessizce açardı.

## 24 yönetilen ayar

16 mevcut (`KitchenDeviceSettings::forDevice`) + 7 kilit politikası + olay
bazlı sesler (`disabled_sound_events`, K-22 §1). Hepsi
nullable ve `null` = **"yönetici dokunmadı"** = serbest. Alan eklenmesi
sahadaki kasaları **kilitlemez**; kilit ancak yönetici açıkça `false` yazınca
doğar. Kilidi kaldırmanın tek yolu `null` yazmaktır.

Üç tuzağın karşılığı `devices.py` içindedir:

- **Sunucu aralık dışını kırpar, reddetmez** (`normalize`). `poll_seconds: 2`
  gönderen yönetici sessizce 3 alır ve tutmadığını fark etmez — burada
  **reddedilir** ve izinli aralık yazılır.
- **Boş dize iki alanda değer taşır:** `audio_sink` ("varsayılan çıkışa dön")
  ve `disabled_sound_events` ("hiçbiri kapalı olmasın", yani hepsini aç).
  Diğer alanlarda `null` ile eşdeğerdir. İkincisinde ayrım kritik: `null`
  zaten "dokunmadım"a ayrılmış durumda ve *"kasadaki bütün susturmaları
  kaldır"* demenin başka yolu kalmıyordu.
- **Gecikme eşiği uyarı eşiğinden küçükse** sunucu sessizce düzeltir; kısmi
  yazmada yönetici bunu hiç görmez, bu yüzden önizlemede **yazılır**.

Değişmeyen ayar için uca **istek gönderilmez**: aynı değeri yeniden yazmak
`settings_updated_at` damgasını ileri atar ve "en son ne zaman dokunuldu"
sorusunun cevabını bozar.

## Ayar itme önizlemesi

Kuru prova bir **jeton** döndürür (`mod_bld_kds_settings_preview`). Jeton
isteğe bağlıdır — sözleşmede jeton isteyen bir uç yok — ama verilirse
denetlenir: istek önizlemeden farklıysa veya **aradan cihazın ayarı değiştiyse**
reddedilir. İki yönetici aynı cihaza yazdığında ikincisi birincinin
değişikliğini görmeden ezmesin diye.

## Revizyon

Kalem farkı değil **tam liste** gönderilir: gönderilen liste siparişin **yeni
hâlidir**. Boş liste reddedilir — siparişi boşaltırdı.

Kalemler **olduğu gibi geçer, ayıklanmaz**. Bilinen alanları seçip gerisini
atan bir dönüşüm `option_value_ids`'i düşürürdü: "ekstra peynir" silinir,
sipariş ucuzlar, mutfak yanlış yemeği yapar ve hata hiçbir yerde görünmez.
Denetim yalnız zorunlu alanların varlığını ve sınırlarını doğrular.

## Fiş kuyruğu bir kuyruk değildir

`GET /print-jobs` **basılmış** fişlerin denetim kaydıdır; KDS'in kendi disk
kuyruğu sunucuda **yoktur** (K-21 §2.4). Yanıt `audit_only: true` taşır.
Bekleyen/başarısız iş sayısı cihaz sağlığından okunur
(`health.print_queue_pending` / `print_queue_failed`).

## Üç durumlu alanlar

`printer_ok`, `sound_ok`, `alarm_muted`, `succeeded` ve 6 kilit alanı **üç
durumludur**. `None` "bilinmiyor / dokunulmadı" demektir ve korunur:

- Sağlık bildirmemiş cihaz **arızalı sayılmaz** (`printer_fault: false`).
- Sonucu gelmemiş komut **başarısız sayılmaz** — başarısız saymak komutu
  ikinci kez gönderttirir ve `restart` için bu ikinci bir kesintidir.
- Telemetri bildirmeyen eski kasa "sesi bozuk" ya da "alarmı susturulmuş"
  sayılmaz. `bool(None)` yazmak olmayan iki arıza uydururdu.

## Zengin telemetri (K-22 §3)

Cihaz künyesine beş salt okunur alan eklendi ve hepsi **opsiyoneldir**:
`last_error` · `alarm_muted` · `alarm_mute_reason` · `queue_oldest_at` ·
`sound_ok`.

Bunların içinde en çok işe yarayanı `queue_oldest_at`: **"kuyrukta 3 iş var"
ile "kuyrukta 3 iş var ve en eskisi 40 dakikadır bekliyor" arasındaki fark,
sahaya gitme kararını değiştirir.** İlki yazıcı meşgulse normaldir, ikincisi
kuyruğun akmadığı anlamına gelir; ekran ikisini aynı sayıyla gösteriyordu.
Panel bu yüzden hem cihaz listesinde hem çekmecede `ago()` ile en eskisinin
yaşını yazar ve 15 dakikayı aşarsa uyarı kutusu çizer.

## Yerel tablolar

`mod_bld_kds_audit` · `mod_bld_kds_settings_preview` · `mod_bld_kds_prefs`

BLD'de karşılığı olmayan üç şey: **deneme kaydı** (BLD'nin
`veykemtu_control_audit` tablosu yalnız sunucuya *ulaşan* isteği bilir), **ayar
itme önizlemesi** ve **ekran tercihi**. Sipariş, cihaz ve komut verisinin
kopyası tutulmaz.

## Bilerek yapılmayanlar

- **Sunucu adresini yönetmek** (K-22 §5). Panelde alan **salt okunur** durur
  ve gerekçesi ekranda yazar: yanlış bir adres yazıldığı anda kasa yeni adrese
  gider, oradan hiçbir şey alamaz ve **düzeltmeyi de alamaz** — düzeltme eski
  adresten gelecekti. Kasa sahada elle kurtarılana kadar sipariş göremez. Tek
  bir yazım hatasının mutfağı durdurduğu tek ayar budur. Merkezden
  yapılabilecek olan, kasadaki o düğmeyi `allow_server_change: false` ile
  kilitlemektir.
- **`connectionLost` sesini kapatılabilir yapmak.** Sunucu listede görürse
  sessizce eler; Kontrol Merkezi **reddeder ve nedenini yazar** — sessizce
  elenen bir ad, "beş sesi de kapattım" sanan yöneticiye hiçbir şey söylemezdi.
- **Listede olmayan komut eklemek.** `KitchenCommand::ALL` sekiz komuttur;
  listede olmayan bir ad kuyruğa atılıp kasada sessizce yok sayılırdı.
- **`online` alanını burada hesaplamak.** Eşik ve saat sunucunundur; Kontrol
  Merkezi'nin saati üç dakika kaysa bütün mutfak çevrimdışı görünürdü.
- **Durum geçiş matrisini tek kapı yapmak.** `status_error` bir **ön
  denetimdir**; karar `OrderStatusTransition`'ındır. Amaç imkânsız geçişi ağ
  turu atmadan ve anlaşılır bir cümleyle söylemek.
- **Çevrimdışı cihaza komutu engellemek.** Komut kuyrukta bekler; engellemek
  kapanmış bir kasaya "aç" diyememek olurdu. Ekran `queued_offline` ile söyler.

## Bu turda eksik kalanlar

- **Ekran tercihi yazan uç yok.** `mod_bld_kds_prefs` okunuyor ve değer
  bulunmazsa modül ayarı geçerli oluyor; sözleşmedeki uç listesinde ayar ucu
  bulunmadığı için yazan uç eklenmedi.
- **Yerel denetim izini okuyan uç yok.** İz yazılıyor ama uç listesinde
  `/audit` bulunmuyor.
- **`printer` yeteneğini tüketen uç yok.** Yetenek ilan ediliyor ve
  `/overview` yanıtında `printer_available` olarak bildiriliyor; baskı ucu
  (eşleme kodu kâğıdı) uç listesinde yok.
- **`GET /menu`'nün geçitte karşılığı yok.** `bld.api` bu turda `menu()`
  taşımıyor; uç `getattr` ile bakıp `ok: false` ve nedenini döndürüyor, ekran
  ürün kimliğini elle girmeye devam ediyor (K7).

## Testler

```bash
.venv/bin/python -m pytest modules/bld_kds/tests -q
.venv/bin/ruff check modules/bld_kds
```

- `test_bld_kds_devices.py` — saf dönüşümler: üç durumlu alanlar, komut
  denetimi, 24 ayarın sınırları, olay listesi, telemetri, revizyon kalemi,
  durum matrisi.
- `test_bld_kds_service.py` — iş kuralları: K7, gerekçe kapısı, kuru prova
  parametresi (varsayılanı kapalı ama yeteneği duruyor), yıkıcı izin ayrımı,
  taze okuma, önizleme jetonu, olay yayını.
- `test_bld_kds_routes.py` — dış yüzey: her ucun ilan ettiği izin (K9) ve
  gövde alan adları.

Ağa çıkılmaz; `bld.api` taklit edilir. **`FakeApi` metot adları ve imzaları
`modules/bld_api/backend/client.py` ile birebir aynıdır** — uydurma bir ad
testleri yeşil tutar ama canlıda `AttributeError` verir ve K7 onu yutunca hata
"BLD'ye ulaşılamadı" diye görünür: yanlış metot adı, düşmüş bir sunucudan
ayırt edilemez.
