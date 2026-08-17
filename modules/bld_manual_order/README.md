# Elle Sipariş

Personel **müşteriyle telefondayken** siparişi merkezden açar: müşteri, servis
günü, kalemler, teslimat ve ödeme.

Grup: **BLD** (sıra 515 — Sipariş Yönetimi 510 ile KDS 520 arasında) ·
İzinler: `bld_manual_order.view`, `bld_manual_order.manage`

Sözleşme: [`BLD/docs/control/orders.md`](../../../BLD/docs/control/orders.md)
→ **`POST /`** bölümü. Geçit metotları
[`bld_api/README.md`](../bld_api/README.md) donmuş tablosundan alınır.

Devraldığı akış BLD'nin TastyIgniter admin panelindeki `Admin\PhoneOrders`
ekranıdır; o panel kapatılıyor ve **elle sipariş girmenin başka yolu kalmıyor.**

---

## Ne yapar

Tek sayfa, dört blok, aşağıda bir özet şeridi. **Sekme yoktur, sihirbaz
yoktur, ayrı müşteri ekranı yoktur** — ekranın tasarım ölçüsü tek: her fazladan
tık, hattaki müşteriyi bekletir.

| Blok | İçerik |
|---|---|
| 1 · Müşteri | Telefonla/adla arama; bulunursa seçilir, bulunmazsa **aynı ekranda** ad+telefonla açılır |
| 2 · Gün, teslimat, ödeme | Kendi takvimimiz (varsayılan bugün), gel-al/teslimat, `online`/`cash`, adres, müşteri notu |
| 3 · Kalemler | **Çoklu** ürün seçici — açık kalır, seçim birikir, tek "Ekle (N)" hepsini girer; adet ve not satırın üstünde |
| Özet | Tahmini toplam (canlı), iki uyarı, isteğe bağlı gerekçe, "Önce prova et" ve "Siparişi kaydet" |

Kaydetme başarılıysa **sipariş numarası** büyük yazıyla görünür; personel onu
telefondaki müşteriye okur. Sepet boşalır ama **müşteri ve gün kalır** — aynı
müşteri "bir de yarına verelim" diyor.

## Ne yapmaz

- **Sipariş penceresini yeniden uygulamaz.** Kesim saati, ileri görüş sınırı,
  sipariş alım şalteri, asgari sepet tutarı ve menü üyeliği sunucuda
  `OrderFactory::create(..., adminContext: true)` ile **bilerek atlanıyor**:
  personel müşteriyle telefonda anlaşmış, istisnayı insan vermiştir. Bu bir
  onay akışı değil, **kayıt akışıdır.**
- **Fiyat hesaplamaz.** Gösterilen toplam **tahmindir** ve ekranda öyle yazar;
  gerçek tutarı `OrderFactory` çözer (paket çözümlemesi, seçenek farkları).
  Tahmini "toplam" diye sunmak, personelin telefonda **yanlış tutar** söylemesi
  demekti.
- **Durum değiştirmez, iptal etmez, revizyon yazmaz.** Onlar Sipariş Yönetimi
  ekranının işi; buranın tek yazması sipariş **açmaktır.**
- **Uzak veriyi kopyalamaz ve kendi tablosu yoktur.** `migrations` manifestte
  ilan edilmemiştir; yazma izini geçit `mod_bld_api_audit` içinde, istek
  çıkmadan **önce** tutuyor.

## Gösterilen ama engellenmeyen iki durum

Sunucunun bilerek izin verdiği iki hâl ekranda **yazıyla** söylenir ve
**hiçbiri kaydetmeyi engellemez**. Engelleyen bir uyarı, sunucunun verdiği
kararı ekranda iptal etmek olurdu.

| Durum | Ekranda | Neden engel değil |
|---|---|---|
| Servis gününün sipariş alımı kesimde kapandı (ya da gün geçmişte) | "bu günün sipariş alımı kapalı, panelden giriyorsunuz" | Pencere `adminContext: true` ile atlanıyor; bugünün siparişi **anında** mutfağa düşer |
| Kalem gün ya da ürün tavanını aşıyor | "tavan aşılıyor — kalan N, istenen M" | `DailyStock::take()` `allowOvershoot: true`; sipariş reddedilmez, **aşım kayda geçer** |

Üçüncü bir satır uyarı olarak değil **engel** olarak yazılır: mutfağın elle
koyduğu `sold_out` işareti tavandan ayrı bir şeydir ve sunucu o kalemi
`422 ITEM_UNAVAILABLE` ile gerçekten reddeder.

## Uçlar

| Metot | Yol | İzin | Not |
|---|---|---|---|
| GET | `/overview` | `.view` | Sözleşme ve sınırlar — **ağa çıkmaz** (K7) |
| GET | `/customers?q=` | `.view` | KVKK: `actor` oturumdan gider, kısa sorgu hiç gönderilmez |
| GET | `/products` | `.view` | Geçitte **önbellekli** referans veri, yalnız satıştakiler |
| GET | `/service-day?date=` | `.view` | Kesim + stok + ödeme listesi + teslimat ücreti (üç okuma, tek cevap) |
| POST | `/stock-check` | `.view` | **Okumadır**; fiil gövde şeklinden seçildi (kalem listesi sorgu dizesine sığmaz) |
| POST | `/orders` | `.manage` | Tek yazma ucu |

Okumalar **asla fırlatmaz**: geçit düşerse
`{"ok": true, "connected": false, "error": …}` döner ve panel `connected`
alanını okur. Yalnız `ok`a bakan bir ekran, geçit düştüğünde "bu müşteri
kayıtlı değilmiş" der ve aynı müşteri ikinci kez açılırdı.

## Gerekçe ve kuru prova

**Gerekçe zorunlu değildir** (`orders.md` → "KARAR"). Gönderilirse yalnız üst
sınır (500) denetlenir; on karakterlik alt sınır **uygulanmaz** — personel
müşteriyle konuşurken zorunlu tutulsaydı "sipariş", "asdasd" üretilirdi.
`actor` yine de her yazmada gider: gerekçe seyreldi, **iz seyrelmedi.**

**Her yazmada `dry_run=` açıkça geçer.** Geçidin varsayılanına güvenilmez:
`config/local.yaml` git dışıdır ve orada `dry_run_default: true` yazıyor
olabilir; bayrağı atlayan bir çağrı hiçbir şey yazmadan `{"ok": true}` alır ve
ekran "sipariş açıldı" derdi. Panelin "Önce prova et" düğmesi `dryRun: true`
gönderir — ve provanın **sınırı** ekranda yazılır: gövdeyi ve ödeme yöntemini
denetler, **kalem geçerliliğini, fiyatı ve stoğu denetlemez.**

## Yapı

```
backend/draft.py       saf kurallar — denetim, telefon, kesim hesabı, stok uyarısı
backend/service.py     iş kuralları — geçit çağrıları, K7, çift kapı
backend/api/routes.py  HTTP yüzeyi — her uçta requires(...)
ui/panel/index.js      tek sayfalık ekran
tests/                 3 dosya · 57 test
```

## Geçit metodu

`create_order(*, service_date, delivery_type, payment_method, items,
customer_id, customer, address, customer_note, location_id, reason, actor,
dry_run)` — [`bld_api/README.md`](../bld_api/README.md) §5 donmuş tablosu.

Metot `getattr` ile aranır ve bulunamazsa **sessiz kalınmaz**:
`gateway_method_missing` koduyla açık bir cümle döner ("BLD geçidinde
`create_order` metodu yok"). Geçit ile bu ekran ayrı sürümlenebilir modüller ve
tablo bu turda ekranla **paralel** yazıldı; eski bir `bld_api` yüklüyse metot
olmayabilir. Doğrudan çağırıp `AttributeError`i K7 yutucusuna bırakmak, eksik
metodu **düşmüş bir sunucudan ayırt edilemez** kılardı ve kimse geçidi
güncellemeyi akıl etmezdi.
