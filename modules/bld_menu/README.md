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
| Kalemler | Ürün seçicisiyle kalem ekleme; adet, sıra, etiket, fiyat geçersiz kılma, zorunlu / yalnız pakette, kalem tavanı. |
| Stok | Gün toplamı **ve** kalem başına porsiyon tavanı; rezerve/kalan ölçerleri; iki adımlı yazma (kuru prova → jetonla uygulama). |
| Eylemler | Yayınla · Yayından çek · **Kopyala** (takvimi dolduranın en büyük zaman kazancı) · Sil. |

## Üç izin, iki değil

`bld_menu.remove` yalnız **silmeyi** taşır (gün + kalem). Ayrımın yeri şu:
yayından çekmek bir şalterdir ve tersi tek tıktır — gün, kalemleri ve
tavanlarıyla taslak olarak yerinde durur. Silmek geri alınamaz: gün kaydı ve
tüm kalemleri (fiyat geçersiz kılmaları, tavanları, etiketleri) birlikte gider.
İkisini aynı anahtara koymak, geri alınabilir bir işlemle geri alınamaz bir
işlemi aynı kapıdan geçirmek olurdu.

Yıkıcı işlemler PIN değil **gerekçe** ister (ADR 0012): ayrı izin + en az 10
karakterlik gerekçe (backend'de de doğrulanır) + çift denetim satırı.

## Kuru prova

Panel `dryRun` alanını **hiç göndermez**; buradan yapılan her yazma gerçektir.
Varsayılan **ayardan okunmaz** ve bu bilinçlidir: `config/local.yaml` git
dışıdır, orada `true` yazabilir ve ayardan okunan bir varsayılan panelden
yapılan her yazmayı sessiz bir provaya çevirirdi. Servis geçide `dry_run=`
değerini **her çağrıda açıkça** geçer.

Tek istisna **stok tavanı yazma**: `PUT stock` tam liste yazar ve gönderilmeyen
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
