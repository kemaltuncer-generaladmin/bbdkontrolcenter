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
Karşılaştırma aralığı ikinci kez çektiği için öğrenci dökümü bu yoldan geçmez.

## İşlem dökümü — hesap değildir

Öğrenci çekmecesi ve karne PDF'i, seçilen aralıktaki **tüm hareketleri** satır
sayısı sınırı olmadan gösterir. Üç tür bir arada durur ve her satır türünü
damga olarak taşır:

| Tür | Nereden gelir | Özete girer mi |
|---|---|---|
| Satış | `GET /api/transactions` | evet |
| İptal | aynı satır, `reversedAt` damgalı | **hayır** |
| Tahsilat | `GET /api/reports/collections` (cari CREDIT) | **hayır** |

Ayrım bilinçlidir: ciro, ürün ve sınıf kırılımları `analytics.live()` üzerinden
gider — iptal edilmiş bir satış ciroya yazılmaz, tahsilat da harcama değildir.
Döküm ise "o gün ne oldu" sorusunun cevabıdır; hiçbir satırı düşürmez.

Veri eksilebilecek iki nokta artık **sessiz değildir**: aralık 400 günü aşarsa
ve bir günün işlem sayısı kantinin tek istek sınırını (5000) doldurursa, sebebi
`meta.warnings` ile ekrana ve karne PDF'ine yazılır.

## Çıktı

- **PDF** (`reportlab`): özet raporu, ürün performans raporu, öğrenci karnesi.
  Türkçe karakterler için gömülü DejaVuSans kullanılır.
- **Excel uyumlu CSV** (UTF-8 BOM + `;`) — kantinin kendi dışa aktarma kuralıyla aynı.

Dosyalar `data/exports/` altına yazılır; panel yolu gösterir.

## Çekim izi — önbellek DEĞİL

Çekilen her gün modülün kendi tablosuna yazılır (`mod_bbd_canteen_reports_day`),
ama **rapor üretilirken buradan hiç okunmaz**. Tek işlevi "kantin o gün ne
demişti" sorusunun cevabını saklamak: anlaşmazlık çıkarsa bakılacak iz.

Önbellek olarak kullanılmıyor ve kullanılmamalı — iptal edilen bir satış ya da
sonradan girilen bir nakit tahsilat **geçmiş günün rakamını değiştiriyor**;
saklanan kopyadan okumak sessizce yanlış rapor üretirdi. Her rapor kantinden
taze çekilir.

İptal edilen satışlar ciroya, ürün ve günlük seriye dahil edilmez — ama işlem
dökümünde `iptal` damgasıyla görünür (yukarı bakın).

- Sözleşme: `module.yaml` · Giriş noktası: `backend/module.py` → `register(ctx)`
- Kurallar: [../../CLAUDE.md](../../CLAUDE.md) · Kılavuz: [../../docs/module-guide.md](../../docs/module-guide.md)

Grup: **BBD** · İzinler: `bbd_canteen_reports.view`, `bbd_canteen_reports.export`
