# Kontrol Paneli

Mağazanın canlı özeti: ciro, sipariş, stok ve sistem durumu — ve sahibi başka
ekran olmayan mağaza ayarları.

Grup: **BBD Store** · CSS öneki: `sd` · Rapor rafı:
`Raporlar/Mağaza/Satış/<yıl>/<ay>`

## Ne yapar

| Alan | Davranış |
|---|---|
| KPI (8) | Ciro · Sipariş · Ortalama sepet · Yeni müşteri · İade tutarı · Bekleyen sipariş · Kargolanmayan · Tükenen ürün. Karşılaştırma: önceki dönem / geçen yıl aynı dönem / kapalı. |
| Grafikler | Günlük ciro (`lineChart`) · durum dağılımı (`stackedBar`) · en çok satan 10 (`barChart`) · saat yoğunluğu (`hourStrip`). |
| Kartlar | Son siparişler (satır → `store_orders`) · kritik stok (satır → `store_products`) · bekleyen işler (satır → ilgili ekran) · sistem sağlığı. |
| Yapılandırma | Mağazanın `core_config` ayarlarından işletmenin dokunduğu ~30 tanesi: mağaza kimliği · ödeme yöntemleri · kargo yöntemleri · stok · sepet/checkout · e-posta gönderen. Her alanda kaynak rozeti ve `[Varsayılana dön]`; üstte arama, altta sabit `[Kaydet] [Geri al] · N alan değişti` çubuğu. Bakım modu burada **salt okunur**. |
| Ayarlar | Çalışma kanalı/dil · yerelleştirme · karşılaştırma kipi · rapor klasörü. |
| Çıktı | Günlük özet raporu PDF · KPI + günlük ciro CSV. |

## Rakamlar nereden geliyor

**KPI'lar ve grafikler mağazanın pano ucundan (`dashboard_stats`) OKUNMAZ;
sipariş listesinden hesaplanır.** Gerekçe:

- Ekranda rakamla birlikte o rakamı üreten siparişler duruyor. İki ayrı kaynak
  birbirini tutmazsa kullanıcı hangisine güveneceğini bilemez ve ikisine de
  güvenmez.
- Uzak ucun hangi tarih alanını (sipariş mi ödeme mi), hangi kanalı ve
  iptalleri nasıl saydığı belgelenmemiş. Buradaki kural açık ve testli.
- Aynı taramadan hem sekiz KPI hem dört grafik çıkıyor; tek tarama yetiyor.

Sayma kuralları — ekranda da yazar:

- İptal/şüpheli sipariş **ciroya da sipariş sayısına da girmez**; durum
  dağılımında ayrıca görünür (çubuk toplamı ile "Sipariş" KPI'sı bu yüzden
  bilerek farklıdır).
- İade tutarı **ciradan düşülmez**; kendi kutusunda durur. Düşmek, iadenin
  hangi dönemin satışına ait olduğunu sessizce varsaymak olurdu.
- Saat kırılımı mağazanın kendi damgasıdır; **saat dilimi çevrilmez**.
- Bilinmeyen ile sıfır ayrıdır: okunamayan KPI `—` gösterir, `0` göstermez.

## Ne yapmaz — ve neden

- **Kendi kendine yenilenmez.** Otomatik yenileme, kullanıcı bir sayıyı
  okurken altından veriyi değiştirir. Yenileme elle yapılır ve panonun hangi
  saat itibarıyla dolduğu durum satırında yazar.
- **Veri kopyalamaz / önbelleğe almaz.** Panonun tek işi doğru rakam
  göstermek; kopya, mağaza tarafındaki bir değişiklikten sonra sessizce eski
  rakamı gösterirdi. Referans listeler (kanal/para/dil) geçidin kendi
  önbelleğinden gelir.
- **Sipariş/ürün düzenlemez.** Okur ve yol gösterir; işlem kendi ekranında.
- **Kanal/para birimi/dil tanımını değiştirmez.** Bunlar mağaza yönetiminde
  yaşar. Ekrandan seçilen kanal ve dil, Kontrol Merkezi'nin hangi kanalı
  okuduğunu belirleyen YEREL tercihtir.
- **Rapor klasörü yolunu ekrandan yazmaz.** Yol modül ayarındadır; çalışırken
  yazılsaydı sonraki açılışta geri dönen bir "ayar" olurdu. Ekran yolu, disk
  durumunu ve yazılabilirliği gösterir.
- **Ziyaretçi/dönüşüm göstermez.** Geçitte doğrulanmış bir kaynağı yok.

## Yayında olmayan uçlar

`/api/admin/bbd/*` uçları mağaza tarafında yazılıyor. Onlara bağlı kartlar
(kritik stok, son yedek, POS, kargo, BLD fiş kuyruğu) **sıfır göstermez**:
"uç hazır olunca açılacak" der ve pano çalışmaya devam eder. Sıfır göstermek,
olmayan bir sağlığı var göstermek olurdu.

GİB / e-fatura sağlık kartı da böyledir: geçitte karşılığı olan bir metot
**yok** ve kart bunu açıkça söyler.

## Ayarlar — `store_settings` ekranı neden yok

Ayarlar doğal sahibi olan ekrana dağıtıldı: vergi `store_tax`, kargo ücreti
`store_shipping`, ödeme/sepet `store_payment_gateway`, bildirim
`store_notifications`, stok `store_products`, yasal metinler `store_cms`.
Buraya yalnız **sahibi başka ekran olmayanlar** düştü.

**Bulunmayan anahtara yazılmaz.** `core_config` anahtar adları Bagisto
sürümüne göre değişiyor; bulunamayan alan salt okunur gösterilir ve nedeni
yazılır. Bulunmayan anahtara yazmak etkisiz bir satır açar ve kullanıcı ayarı
değiştirdiğini sanır — bakım modunda bu, kapalı sanılan açık bir vitrin
demektir.

## Yapılandırma sekmesi

Aşağıdakiler `bbdstore.com.tr` üzerinde **salt okunarak** ölçüldü
(2026-08-13); varsayım değildir.

**Neden beyaz liste.** `GET /api/admin/configuration/menu` bu kurulumda
**344 alan** ve 160.668 bayt döndürüyor. Tamamını dökmek işletmeye yardım
etmez: aradığı beş ayarı üç yüz kırk dört satırın içinde kaybeder ve
yanlışlıkla sanal POS'un test kipini açar. Ekrana çıkan ~30 alan
`backend/config_map.py` içinde adıyla sayılıdır ve **hepsinin canlıda var
olduğu tek tek doğrulandı**. `password` · `image` · `file` tipli alanlar
(sanal POS parolası, kargo API belirteci) beyaz listeye girse bile ekrana
gelmez ve yazılmaz.

**Ağaç tek istekte okunur.** Alternatif on bir ayrı `configuration(slug)`
çağrısıydı; kıt olan bant genişliği değil, dakikada 55 isteklik hız kovası.

**Slug koddan türetilmez.** Alan kodu `<slug>.<alan>` biçiminde ama alan adı
da nokta içerebiliyor: `sales.order_settings.reorder.admin` içinde slug iki,
`general.general.locale_options.weight_unit` içinde üç parçalı. Slug her zaman
ağacın **düğüm anahtarından** okunur.

### Kanal süzgeci kod ister, kimlik değil — sipariş ucunun TERSİ

`/api/admin/configuration/menu` için ölçülen:

| Gönderilen | Sonuç |
|---|---|
| `channel=default` (kod) | 344 alan, etkin değerlerle — **doğru** |
| `channel=1` (kimlik) | 344 alan, **49 alan sessizce varsayılana düşmüş** |
| `channel=zzzyok` (yok) | `channel=1` ile **birebir aynı yanıt** |

Yani kanal kimliği göndermek, olmayan bir kanal göndermekle aynıdır ve **hata
vermez**: `kuveytturk.active` `"1"` yerine `"False"`, `kuveytturk.title`
mağazadaki başlık yerine varsayılan gelir. Ekran o zaman saklanmış bir ayara
"varsayılan" rozeti takar, kullanıcı `[Varsayılana dön]`e basıp hiçbir şeyin
değişmediğini görür ve en kötüsü, **yanlış kanaldan okunmuş değerin üzerine
yazar.**

`store_orders` bunun **tersini** yazıyor (`/api/admin/orders?channel=` kimlik
ister, kod göndermek listeyi boşaltır). İki ucun kuralı farklıdır; birini
diğerine benzetmek için "düzeltme" yapılmamalıdır. Bu modül her zaman kanal
**kodunu** gönderir ve `test_ayar_agaci_kanal_KODUYLA_okunur_kimlikle_degil`
bunu kilitler.

### Kaynak rozeti ne söyler, ne söylemez

Menü ucu her alan için hem `default` hem `value` veriyor ama Bagisto **"bu
değerin `core_config` içinde satırı var mı"** sorusunu API'den cevaplamıyor:
değer yoksa varsayılana düşüyor ve ikisi aynı görünüyor. Rozet bu yüzden
dürüst olanı söyler:

| Rozet | Anlamı |
|---|---|
| `tanımsız` | Ne değer var ne ilan edilmiş varsayılan |
| `varsayılan` | Etkin değer ilan edilen varsayılanla **aynı** |
| `veritabanından` | Etkin değer varsayılandan **farklı**, mağazada saklanıyor |

Uydurulmuş bir kesinlik göstermek, sonradan "ama ekran veritabanından diyordu"
demeye yol açardı. `[Varsayılana dön]` yalnız **varsayılanı ilan edilmiş**
alanda çıkar; 30 alanın 6'sı böyle. Olmayan bir varsayılana döndüren düğme,
alanı sessizce boşaltmak olurdu.

**Anahtar kaydetme anında yeniden doğrulanır.** Ekran dakikalarca açık
kalabiliyor; o sırada mağazada bir eklenti kapatılıp alan ilan edilmez hâle
gelirse açılıştaki bilgiye güvenmek tam da kaçınmak istediğimiz sessiz satırı
açardı. Kısmi yazma da gizlenmez: patlayan slug'tan önce yazılanlar yanıtta
adıyla döner.

### Bakım modu neden buradan YAZILMIYOR

`general.content.maintenance_mode.*` ve `general.content.shop_information.*`
anahtarları **bu kurulumda yoktur** — `slug=general.content` 15 anahtar
döndürüyor ve hiçbiri bunlar değil. Bagisto 2.4.8 mağaza kimliğini
`sales.shipping.origin` altına, bakım modunu ise `core_config` yerine **satış
kanalı kaydına** taşımış: `isMaintenanceOn` · `maintenanceModeText` ·
`allowedIps` (`GET /api/admin/settings/channels`).

Kanal **yazma** ucu ise kanal adını, bakım metnini ve vitrin SEO'sunu dile
bağlı `translations[]` alt nesneleriyle alıyor. Tek kanallı canlı mağazada bu
gövde denenmeden gönderilseydi 422 almak iyi ihtimal, **kanal adının ve vitrin
meta alanlarının boşalması** kötü ihtimaldi. Bu yüzden bakım modu burada
durumuyla birlikte **salt okunur** gösterilir ve nedeni ekranda yazar;
vitrini kapatma işlemi doğrulanmış bir `store.api` metodu gelene kadar mağaza
yönetiminden yapılır. Sahte bir düğme koymak, basan kişiye vitrinin
kapandığını sandırırdı.

`store_dashboard.maintenance` izni ve `POST /settings/maintenance` ucu
duruyor; uç **anahtarı bulamadığı için yazmayı reddeder** (ayrı izin + gerekçe
≥10 karakter + kuru prova). Aynı sebeple Ayarlar sekmesindeki mağaza kimliği
ve SEO alanları da bu kurulumda "anahtar bulunamadı" der — kimlik alanlarının
çalışan karşılığı Yapılandırma sekmesindedir.

## Uçlar

`/api/store_dashboard` öneki altında. Hepsi `requires(...)` taşır (K9).

Okuma: `GET /summary` · `GET /orders/recent` · `GET /stock/critical` ·
`GET /pending` · `GET /system` · `GET /settings` · `GET /config` ·
`GET /audit` · `GET /printer`

Yazma: `POST /settings` · `POST /config` · `POST /settings/maintenance` ·
`POST /preview` · `POST /print` · `POST /export`

`POST /config` gövdesi `{changes: {kod: metin}, reason, dryRun}`; değerler
**metindir**. Mantıksal alana JSON `true` göndermek `core_config` içinde
`"true"` **metni** olarak saklanır ve `(bool)` dönüşümünde her zaman doğru
çıkardı — kapatmak isteyen kullanıcı yöntemi açık bırakırdı. Tek istekte en
çok 60 alan yazılır.

Kartlar **ayrı uçlardan** yüklenir: tek büyük uç, en yavaş kaynağın hızında
bir ekran üretir ve bir kaynağın patlaması tüm panoyu boşaltırdı.

## İzinler

| Anahtar | Ne açar |
|---|---|
| `store_dashboard.view` | Pano, Yapılandırma sekmesini görme, rapor, CSV |
| `store_dashboard.manage` | Yapılandırma yazma, çalışma kanalı, yerelleştirme |
| `store_dashboard.maintenance` | Bakım modu ucu (bu kurulumda anahtar yok, yazma reddedilir) |

## Yerel tablolar

Yalnız Bagisto'da **karşılığı olmayan** veri: `mod_store_dashboard_audit`
(gerekçeli yazma izi) ve `mod_store_dashboard_prefs` (çalışma kanalı, dil,
karşılaştırma kipi, saat dilimi, tarih biçimi). Mağaza verisi kopyalanmaz.

## Testler

```bash
.venv/bin/python -m pytest modules/store_dashboard/tests -q
.venv/bin/ruff check modules/store_dashboard
```

- `test_store_dashboard_metrics.py` — saf dönüşümler (KPI, aralık, seri).
- `test_store_dashboard_service.py` — kart kart hata (K7), yıkıcı işlem kapıları.
- `test_store_dashboard_config.py` — Yapılandırma sekmesi. İçindeki ağaç
  (`live_tree()`) canlıdan alınmıştır: kodlar, tipler ve değer biçimleri
  olduğu gibidir. Modülün kendi uydurduğu snake_case veriye karşı geçen test
  hiçbir şey kanıtlamıyordu.

Ağa çıkılmaz; `store.api` taklit edilir.
