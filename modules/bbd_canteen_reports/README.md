# Kantin Raporları modülü

Öğrenci ve ürün bazlı derin satış analizi. Kaynak kantinin işlem geçmişidir
(`GET /api/transactions`, kalem kırılımlı); tüm kırılımlar bu modülde hesaplanır.
Kantine ek yük binmez, kantin kodu değişmez.

## Sekmeler

- **Genel bakış** — ciro, işlem, tekil öğrenci, ortalama sepet, açık alacak;
  günlük ciro çizgisi, **saat yoğunluk şeridi** (hangi teneffüs yoğun),
  en çok satan ürün ve en çok harcayan öğrenci çubukları, tahsilat özeti.
- **Öğrenci bazlı** — sıralanabilir tablo (işlem, adet, tutar, ortalama, bakiye,
  favori ürün, son işlem). Satıra tıklayınca o öğrencinin zaman serisi, aldığı
  ürünler ve işlem dökümü açılır; **karne PDF'i** oradan alınır.
- **Ürün bazlı** — **Pareto (ABC) analizi**, adet/ciro/pay, benzersiz alıcı,
  **stok tükenme tahmini** (günlük ortalamaya göre kalan gün) ve **ölü stok**
  (dönemde hiç satılmayan ama stokta duran aktif ürünler + bağlı tutar).
- **Sınıf bazlı** — sınıf cirosu ve öğrenci başına harcama (mevcut farkını düzeltir).

Her sekmede önceki eşit uzunluktaki dönemle **karşılaştırma** yüzdesi gösterilir.

## Çıktı

- **PDF** (`reportlab`): özet raporu, ürün performans raporu, öğrenci karnesi.
  Türkçe karakterler için gömülü DejaVuSans kullanılır.
- **Excel uyumlu CSV** (UTF-8 BOM + `;`) — kantinin kendi dışa aktarma kuralıyla aynı.

Dosyalar `data/exports/` altına yazılır; panel yolu gösterir.

## Önbellek

Çekilen her gün modülün kendi tablosuna yazılır: ikinci açılış anında gelir ve
kantin erişilemese bile geçmiş raporlar okunur. Bugün her zaman tazelenir.
İptal edilen satışlar hiçbir hesaba dahil edilmez.

- Sözleşme: `module.yaml` · Giriş noktası: `backend/module.py` → `register(ctx)`
- Kurallar: [../../CLAUDE.md](../../CLAUDE.md) · Kılavuz: [../../docs/module-guide.md](../../docs/module-guide.md)

Grup: **BBD** · İzinler: `bbd_canteen_reports.view`, `bbd_canteen_reports.export`
