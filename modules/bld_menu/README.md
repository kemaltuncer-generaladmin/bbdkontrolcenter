# Menü Yönetimi (`bld_menu`)

BLD'nin **günlük menü takvimi**: hangi güne ne girildiği, hangi günün yayında
olduğu, o günden kaç porsiyon satılabileceği. Satılan şey sabit bir katalog
değil, gün gün girilen menüdür; ürünlerin kendisi **Ürün Kataloğu**
(`bld_products`) alanındadır.

Grup: **BLD** · İzinler: `bld_menu.view`, `bld_menu.manage`, `bld_menu.remove`

## Sözleşme

Uçlar, alan adları, hata kodları ve `not_orderable_reason` değerleri
**dondurulmuştur** ve buradan okunur:

- `BLD/docs/control/menu.md` — 13 uç, gün/kalem/stok şemaları
- `BLD/docs/control/00-genel.md` — ortak gövde (`actor`/`reason`/`dry_run`),
  sayfalama, biçimler, hata kodları, denetim izi
- `modules/bld_api/README.md` §2 — geçidin donmuş metot tablosu

Buradan okunmayan hiçbir alan adı, yol ya da başlık uydurulmaz.

## Ekran ne yapar

| Bölüm | İş |
|---|---|
| Takvim | Ay ızgarası; gün başına rozet: **Yayın · Taslak · Tükendi**, rozetsiz = menü girilmemiş. Kapalı günler gri + ipucu. |
| Gün bilgileri | Başlık, açıklama, iç not, paket fiyatı (kuruş), bileşen satışı, **güne özel kesim saati**, gün tavanı, görsel yolu. Kısmi yazar. |
| Kalemler | **Hızlı ekleme**: gün düzenleyicinin içinde duran arama kutusuna yazıp Enter'a basmak kalemi anında ekler (adet 1, ürünün kendi fiyatı, listenin sonu); kutu temizlenir, odak kutuda kalır. Ayrıntı — adet, sıra, etiket, fiyat geçersiz kılma, zorunlu / yalnız pakette, kalem tavanı — satır üstünde düzenlenir. Az önce eklenen kalem tek tıkla geri alınır. |
| Stok | Gün toplamı **ve** kalem başına porsiyon tavanı; rezerve/kalan ölçerleri; iki adımlı yazma (kuru prova → jetonla uygulama). |
| Eylemler | Yayınla · Yayından çek · **Kopyala** (takvimi dolduranın en büyük zaman kazancı) · Sil. |

## Üç izin, iki değil

`bld_menu.remove` yalnız **silmeyi** taşır (gün + kalem). Ayrımın yeri şu:
yayından çekmek bir şalterdir ve tersi tek tıktır — gün, kalemleri ve
tavanlarıyla taslak olarak yerinde durur. Silmek geri alınamaz: gün kaydı ve
tüm kalemleri (fiyat geçersiz kılmaları, tavanları, etiketleri) birlikte gider.
İkisini aynı anahtara koymak, geri alınabilir bir işlemle geri alınamaz bir
işlemi aynı kapıdan geçirmek olurdu.

## Gerekçe uç başına istenir

Bu alanda gerekçe **her yazmada sorulmaz.** Yalnız iki şartı birden taşıyan
uçlarda zorunludur: sonucu **müşteriye görünür** hâle gelir **ve** geri
alınması zordur. Dört uç: `publish`, `unpublish`, `DELETE days/{date}`,
`duplicate`. Bağlayıcı tablo `BLD/docs/control/menu.md` → "Gerekçe politikası".

Gün kurmak, gün düzenlemek, kalem eklemek/güncellemek/silmek ve tavan yazmak
**gerekçesizdir.** Neden: bir güne beş ürün koymak beş kez on karakter demekti
ve ürettiği metinler "düzeltme", "ok", "asdasd" oldu — yani sınırın engellemek
için var olduğu şeyin ta kendisi. Az yerde istenen gerekçe, çok yerde
istenenden daha değerlidir.

**Gevşemeyen üç şey.** `actor` her yazmada gider; denetim satırı her yazmada
açılır; silmek hâlâ ayrı izin (`bld_menu.remove`) ve ayrı onay ister — kalem
silmede gerekçe kutusu kalktı, onay penceresi kalkmadı (ADR 0012'nin "yıkıcı
işlem onaysız geçmez" kuralı gerekçeden bağımsızdır). Gerekçe istenen dört
uçta alt sınır hâlâ **10 karakter**.

Zincirin dördü de birlikte gevşedi: panel · KM backend (`WriteBody` /
`ReasonBody` ayrımı) · geçit (`_REASON_OPTIONAL`) · BLD sunucusu
(`ControlController::write(..., $reasonRequired)`). Gerekçe istemeyen bir uca
`reason` göndermek `extra="forbid"` yüzünden **422** verir — panel o alanı hiç
göndermez.

## Satışa açma (`auto_open_on_price`)

Paket fiyatı girmek bu işletmede **"bu menü satılacak"** demenin kendisidir;
ayrı bir yayın adımı yalnızca unutuluyordu. Ayar açıkken (varsayılan) panel,
fiyat kaydından ve kalem yazmadan sonra `POST days/{date}/open-sale` ucunu
kendiliğinden çağırır. Uç iki iş yapar:

1. **Eksik "zorunlu kalem" işaretlerini kurar.** BLD paketi ancak ürünü
   çözülebilen en az bir zorunlu kalem varsa satışa açıyor
   (`DailyMenu::packageBlockReason` → `no_components`); yoksa günün menü
   ucunda `package` alanı `null` döner ve sitede paket kartı **hiç çizilmez.**
   Fiyat kaydedilmiş, gün yayında, takvim yeşil — ve müşteri paketi sepete
   koyamıyor. Sessiz arıza tam olarak buydu.
2. **Gün taslaktaysa yayınlar.**

`POST open-sale` (tarihsiz) aynı işi bir **aralığa** uygular; panelin takvim
sütunundaki "Tümünü satışa aç" düğmesi bugünden ileriye koşar ve önce kuru
prova ile ne olacağını sayar.

**Gerekçeyi ayar veriyor** (`menu.AUTO_OPEN_REASON`) ve denetim izine ayarın
adıyla, `actor` ile birlikte düşer — "kim" sorusu cevapsız kalmaz. Otomatikleşen
yalnız **açma** yönüdür: `unpublish` gerekçe istemeye devam eder.

Ayar kapatılırsa eski akış aynen çalışır: gün taslakta kalır, yayın elle ve
gerekçesiyle yapılır.

## Kuru prova

Panel `dryRun` alanını **hiç göndermez**; buradan yapılan her yazma gerçektir.
Varsayılan **ayardan okunmaz** ve bu bilinçlidir: `config/local.yaml` git
dışıdır, orada `true` yazabilir ve ayardan okunan bir varsayılan panelden
yapılan her yazmayı sessiz bir provaya çevirirdi. Servis geçide `dry_run=`
değerini **her çağrıda açıkça** geçer.

**İki istisna var ve ikisi de İSTENEN provadır** — panel bayrağı yalnız
buralarda, açıkça gönderir.

Birincisi **toplu satışa açma**: `POST open-sale` önce `dryRun: true` ile
koşar, kaç güne dokunulacağını ve hangilerinin atlanacağını sayar, kullanıcı
onaylayınca gerçeği koşar. Toplu ve müşteriye görünür bir işlemde "ne olacağını
görmeden onayla" demek, onayı biçimsel bir tıklamaya indirirdi.

İkincisi **stok tavanı yazma**: `PUT stock` tam liste yazar ve gönderilmeyen
kalemin tavanı kalkar. Bu yüzden akış iki adımlıdır —
`POST days/{date}/stock/preview` kuru provayı koşar, uyarıları hesaplar ve bir
jeton döndürür; `PUT days/{date}/stock` yalnız o jetonla gelir ve **temel
çizgiyi** (önizleme anındaki satılmış porsiyonlar) doğrular. Arada satış
değiştiyse uygulama reddedilir.

## Yerel tablolar

Uzak BLD verisi **kopyalanmaz**. `mod_bld_menu_*` önekli üç tablo yalnız
BLD'de karşılığı olmayanı tutar:

| Tablo | Ne için |
|---|---|
| `mod_bld_menu_audit` | Yazma **denemesi** kaydı. Uzak denetim izi yalnız sunucuya ULAŞAN isteği bilir; ağ koparsa "kim neyi denedi" yalnız burada kalır. Satır silinmez. |
| `mod_bld_menu_stock_preview` | Tavan önizlemesi + temel çizgi + kullanıcının GÖRDÜĞÜ uyarılar. |
| `mod_bld_menu_prefs` | Ekran tercihi. BLD'yi etkilemez. |

## Doğrulama

```bash
cd "Kontrol Merkezi"
.venv/bin/python -m pytest modules/bld_menu
.venv/bin/ruff check .
node --check modules/bld_menu/ui/panel/index.js
```

Testler ağa çıkmaz: `tests/bld_menu_fakes.py` gövdeleri sözleşmeden kopyalar ve
`FakeApi` metot adlarını geçidin donmuş tablosuyla birebir taşır.
