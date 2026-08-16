# Sipariş Yönetimi

Gelen siparişlerin listesi, ayrıntısı, revizyonu, durum ilerletmesi, iptali ve
muhasebe için CSV dışa aktarımı.

Grup: **BLD** · İzinler: `bld_orders.view`, `bld_orders.manage`,
`bld_orders.cancel`

Sözleşme: [`BLD/docs/control/orders.md`](../../../BLD/docs/control/orders.md)
(sekiz uç) + `00-genel.md`. Geçit metotları
[`bld_api/README.md`](../bld_api/README.md) §5'teki donmuş tablodan alınır.

---

## Ne yapar

| Sekme | İçerik |
|---|---|
| Siparişler | Süzülen, sayfalanan, **geçmişe bakan** liste; satıra tıklayınca ayrıntı çekmecesi |
| Dışa aktarım | Aynı süzgeçlerle CSV üretir ve masaüstündeki rapor klasörüne 0600 izinle yazar |
| Yerel iz | Bu ekrandan yapılan yazma **denemelerinin** kaydı — sunucununki değil |

Çekmecede: aşama şeridi (`stepper`), geri alma penceresi, kalemler, revizyon
yazma, durum ilerletme, iptal (gerekçeli onay), revizyon geçmişi (`timeline`)
ve fatura künyesi.

## Ne yapmaz

- **Durum makinesini yeniden uygulamaz.** Geçiş matrisi ve 120 saniyelik tek
  adım geri alma penceresi `OrderStatusTransition`'da, yani sunucudadır. Bu
  ekran hangi geçişin geçerli olduğuna dair **tek bir iddia taşımaz**; sunucu
  reddederse cevabı Türkçeleştirir ve isteğin çıktığı andaki durumu cümleye
  ekler. `bld_kds` matrisin bir kopyasını taşıyor ve "ön denetim" diyor; buradaki
  karar farklı ve gerekçesi `backend/orders.py` başlığında yazılı.
- **Fatura üretmez.** `GET /{order}/invoice` var olan belgeye bakar; üretim
  `bld_invoices` alanının işi.
- **Kayıt silmez.** İptal edilen sipariş listede kalır, revizyonlar üst üste
  yazılır, denetim satırı hiç silinmez.
- **Uzak veriyi kopyalamaz.** Yerel tablolar yalnız denetim izi ve ekran
  tercihi içindir.

## Uçlar

| Metot | Yol | İzin |
|---|---|---|
| GET | `/overview` | `bld_orders.view` |
| GET | `/orders` | `bld_orders.view` |
| GET | `/orders/{id}` | `bld_orders.view` |
| GET | `/orders/{id}/revisions` | `bld_orders.view` |
| GET | `/orders/{id}/invoice` | `bld_orders.view` |
| GET | `/audit` | `bld_orders.view` |
| POST | `/orders/{id}/revisions` | `bld_orders.manage` |
| POST | `/orders/{id}/status` | `bld_orders.manage` |
| POST | `/orders/{id}/cancel` | **`bld_orders.cancel`** |
| POST | `/export` | `bld_orders.view` |
| PUT | `/prefs` | `bld_orders.view` |

`/overview` **ağa çıkmaz**: durum kodları, ödeme sözlüğü ve gerekçe sınırları
yereldir, böylece geçit düşükken de süzgeç şeridi çizilebilir (K7).

## Bilinmesi gerekenler

- **İptal `status` ucundan yapılamaz.** `status="iptal"` gövdesi serviste
  reddedilir: iptal iade + SMS + stok iadesi üretir ve ayrı izin ister.
- **Revizyon TAM kalem listesidir**, kalem farkı değil. Boş liste reddedilir —
  siparişi boşaltmak iptal değildir.
- **Bileşen satırları listede görünmez** (B-19). Personel günün menüsünü **tek
  birim** olarak düzenler; sunucu bileşenleri yeniden açar.
- **`stock_released` iptalin en önemli yan etkisidir** ve ekranda yazılır:
  düşen porsiyonlar gün toplamı ve ürün tavanından geri gelir.
- **CSV baytları olduğu gibi diske yazılır.** Dosya UTF-8 BOM ile başlar; metne
  çevirip yeniden kodlamak Excel'de "ğ" yerine kutu gösterirdi. Kesilen dosya
  hata değildir ama ekran kesilmeyi **söyler**.
- **Sayfa sayaçları yalnız o sayfayı sayar.** Toplam sayı `meta.total`'dedir;
  ikisi ekranda ayrı yazılır.

## Sözleşmede eksik görülenler

1. **`can_undo` alanı sözleşmede yok.** `orders.md` geri alma penceresinden söz
   ediyor (120 sn) ama `GET /{order}` gövdesinde böyle bir alan saymıyor.
   Uydurulmadı: servis yalnız `can_undo` ve `undo_until` adlarını okur, ikisi de
   yoksa ekran "bilinmiyor" der ve geri alma düğmesini **hiç çizmez**.
2. **Geçit `error.details` bloğunu taşımıyor**, bu yüzden `INVALID_TRANSITION`
   öteki 422'lerden ayırt edilemiyor (`bld_api/backend/errors.py`).
3. **Fatura ucunun izni.** Sözleşme `bld_invoices.view` diyor; bir modül kendi
   kimliği dışında izin tanımlayamadığı için uç `bld_orders.view` ile
   korunuyor.

## Doğrulama

```
cd "Kontrol Merkezi"
.venv/bin/python -m pytest modules/bld_orders
.venv/bin/ruff check .
node --check modules/bld_orders/ui/panel/index.js
```
