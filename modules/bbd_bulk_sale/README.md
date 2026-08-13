# Toplu Satış modülü

Kasada sorun yaşandığında ya da toplu ikram/etkinlik girildiğinde, idarecinin
öğrenci öğrenci uğraşmadan ürünleri toplu işleyebilmesi için. Yazma kantinin
kendi satış ucundan geçer; kayıt kasada girilmiş gibi oluşur.

## İki kip

- **Aynı sepet herkese** — öğrencileri seç, tek sepet kur, hepsine işle.
- **Öğrenci başına ayrı sepet** — öğrenci seç, sepetini kur, kuyruğa ekle;
  sonunda hepsi tek seferde işlenir.

## Ne yapar

- Ürün arama ve **barkod okutma** ile sepet kurma; birim fiyat kantindeki
  güncel fiyattan gelir, değiştirilirse görsel olarak işaretlenir.
- **Tarih seçimi** — kasa arızası yaşanan güne geriye dönük yazma.
- **Sepet şablonları** — "Kahvaltı paketi" gibi kayıtlı sepetler.
- **Ön izleme** — öğrenci başına tutar, engel/limit durumu ve parti genelinde
  stok karşılığı (40 öğrenciye birer su için kantinde 40 su olmalı).
- **Geçmiş partiler ve geri alma** — parti ya da tek satır.

## Veri güvenliği

`local_id` gün + öğrenci + deneme sırası + **sepet parmak izinden** deterministik
üretilir: aynı sepet iki kez gönderilirse kantin `duplicate` der, farklı sepet
gönderilirse yeni satış açılır. Geri alma silme değildir — ters cari kayıt yazar.

- Sözleşme: `module.yaml` · Giriş noktası: `backend/module.py` → `register(ctx)`
- Kurallar: [../../CLAUDE.md](../../CLAUDE.md) · Kılavuz: [../../docs/module-guide.md](../../docs/module-guide.md)

Grup: **BBD** · İzinler: `bbd_bulk_sale.view`, `.manage`, `.reverse`
