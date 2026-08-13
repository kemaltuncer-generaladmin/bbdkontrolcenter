# Kantin Ürünleri modülü

Kantin kiosk'unun yönetim ekranından yapılabilen her ürün işlemi burada da
yapılır. Ürünler kantinde durur; bu ekran **kopya tutmaz**, her açılışta oradan
okur ve yazmayı kantinin kendi `POST /api/products` ucundan yapar.

## Ne yapar

- Görselli kart listesi; arama, aktif/pasif ve eksik filtreleri, sıralama.
- Ürün ekleme/düzenleme: ad, barkod, fiyat (₺ girilir, kuruşa çevrilir), stok.
  Barkod okuyucu Enter'ı doğrudan kaydeder.
- **Görsel yükleme** (sürükle-bırak dahil). Kantin 1280 piksele küçültüp saklar;
  görsel reddedilse bile ürün yazılır ve durumu bildirilir.
- **Stok işlemleri**: kart üzerinde hızlı ±, ayrıntıda sebepli giriş/çıkış.
- **Toplu fiyat güncelleme**: yüzde ya da sabit tutar, önce kuru prova.
- **Sağlık denetimi**: barkodsuz, görselsiz, stoğu biten, pasif ama stoklu
  ürünler — her biri tıklanınca filtreye dönüşür.
- **Değişiklik günlüğü**: kim, ne zaman, neyi neye çevirdi. Kantinde böyle bir
  iz yok; modülün kendi tablosunda tutulur.

## Silme yoktur

"Sil" demek `isActive: false` demektir: ürün kasada satışa çıkmaz ama satırı,
geçmiş satışlardaki bağı ve raporlardaki payı olduğu gibi kalır. Kantin
API'sinde de silme ucu yoktur; bu kısıt bilinçlidir.

- Sözleşme: `module.yaml` · Giriş noktası: `backend/module.py` → `register(ctx)`
- Kurallar: [../../CLAUDE.md](../../CLAUDE.md) · Kılavuz: [../../docs/module-guide.md](../../docs/module-guide.md)

Grup: **BBD** · İzinler: `bbd_canteen_products.view`, `.manage`, `.deactivate`
