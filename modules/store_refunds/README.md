# İadeler

İade taleplerinin işlenmesi, **iade tutarının satır satır hesabı** ve para
iadesi takibi. Bir iadenin tamamı tek çekmecede biter.

Grup: **BBD Store** · CSS öneki: `rf` · Rapor rafı:
`Raporlar/Mağaza/Finans/<yıl>/<ay>`

## Ne yapar

| Alan | Davranış |
|---|---|
| Liste | İki kaynak tek listede: Bagisto kredi notları (**para**) + BBD iade talepleri (**süreç**). Aynı siparişin ikisi varsa **tek satır**. |
| Çipler | Talep · Onaylandı · Ürün bekleniyor · Ürün geldi · İade edildi · Reddedildi · Bilinmiyor. |
| KPI | Bekleyen tutar · Bu ay iade · **İade oranı %** (bu ayın iadesi / bu ayın satışı). |
| Süzme | **İstemcide** (ölçek "yüzler"). Yalnız tarih aralığı sunucuya gider ve pencereyi belirler. |
| Çekmece | Kalem adet seçimi → **Hesapla** → satır satır tutar → gerekçeli onay → kredi notu. |
| POS | Kredi notundan **ayrı** adım: ödeme denemesi seçilir, tutar girilir, karta iade edilir. |
| Kargo | Gönderi üzerinden **iade gönderisi** açar, takip numarası üretir. |
| Çıktı | İade icmali PDF · iade formu PDF · görünen liste CSV · aralığın tamamı CSV. |

## İade tutarı hesabı — ekranın varlık sebebi

Hesap tek yerdedir: `backend/calc.py::compute`. Panel ikinci bir aritmetik
yazmaz; seçim yapılırken **"yaklaşık"** diye etiketlenmiş kaba bir toplam
gösterir, kesin tutar `Hesapla` ile sunucudan gelir ve şu satırlarla çizilir:

```
KTP-1 · Matematik Soru Bankası   1 × 100,00 ₺        110,00 ₺
Ürün tutarı                                          100,00 ₺
İndirim payı                     düşülür             −10,00 ₺
KDV payı                         eklenir              20,00 ₺
Kargo iadesi                     HARİÇ (48,00 ₺)       0,00 ₺
İADE TOPLAMI                     1 adet               110,00 ₺
```

Aynı anda mağazanın kendi önizlemesi (`refunds/preview`) sorulur ve
karşılaştırılır. Üç cevap vardır ve üçü de yazılır: **uyuşuyor · uyuşmuyor ·
sorulamadı**. Sorulamayanı "uyuşuyor" saymak, doğrulanmamış bir tutarla para
çıkarmak olurdu.

## Ne yapmaz — ve neden

- **İade faturası kesmez.** Fatura `store_invoices` ekranının işidir; iki
  ekranın aynı belgeyi kesmesi seride çift numara üretir.
- **"İade edildi" durumunu süreç ucundan yazmaz.** O durum ancak para gerçekten
  geri verildiğinde (onay ucu) oluşur. Aksi hâlde mutabakatta kapatılamayan bir
  kayıt kalırdı.
- **Faturalanmamış kalemi iade etmez.** Parası hiç alınmamış kalemi "iade
  etmek" parayı ikinci kez çıkarır. Faturasız çalışan bir akış varsa ayar
  (`refundable_basis: ordered`) açıkça değiştirilir ve ekran bunu söyler.
- **Kargo bedelini kendiliğinden iade etmez.** Ayıplı üründe evet, "beğenmedim"
  iadesinde genelde hayır; karar her iadede tek tek verilir.
- **Kayıt silmez.** Reddedilen talep de kayıttır ve listede kalır (ADR 0012).
- **Her tuşta mağazaya gitmez.** Hesap ucu siparişi tazeler ve mağazaya
  önizleme sorar; bunu her değişiklikte yapmak dakikada 55 istek kovasını
  tüketir.
- **Şablonlu bildirim tutmaz.** Müşteri bildirimi siparişe not düşerek
  (e-postayla) gider; şablonlar `store_notifications` ekranındadır.

## Yedi tuzak

Hepsinin karşılığı `backend/calc.py` içinde bir fonksiyon ve
`tests/test_store_refunds_calc.py` içinde adı tuzağı söyleyen bir testtir.

1. Kredi notu + talep aynı olayın iki kaydı → `merge_rows` tek satır yapar.
2. İade edilebilir adet = **faturalanan − iade edilen** → `order_view`.
3. Kısmi iadede indirim ve KDV **orantılı** paylaşılır → `share` (yarım kuruş
   yukarı; `float` yok).
4. Kargo bedeli otomatik iade edilmez, iade edilmiş kargo ikinci kez verilmez →
   `compute` + `shipping_refunded_of`. Sipariş ucu `shipping_amount_refunded`
   **yayınlamıyor** (canlıda doğrulandı); "daha önce ne kadar kargo iade
   edildi" yalnız siparişin kredi notlarından toplanabilir. Bu okuma
   başarısızsa ekran kutuyu kendiliğinden işaretlemez ve doğrulayamadığını
   yazar.
5. Mağazanın hesabı farklı olabilir → `compare`; sorulamadıysa "uyuşuyor"
   sayılmaz.
6. Mağaza durum adları sürüme göre değişir → `status_of`; `closed` gibi
   belirsiz durum **rastgele eşlenmez**, "Bilinmiyor" kalır.
7. Para telde ondalık, içeride kuruş → `to_kurus` / `from_kurus`.

## Canlı alan adları (2026-08-16'da doğrulandı)

Bagisto yönetici API'si **camelCase** döndürür ve bazı alanları hiç
yayınlamaz. Buradaki listeye güvenerek yazın; tahmin edilen ad ekranı
sessizce "—" ile doldurur.

| Nereden | Gerçekte gelen | Dikkat |
|---|---|---|
| `GET /refunds` | `orderIncrementId` · `customerName` · `billedTo` · `customerEmail` · `orderDate` · `totalQty` | Gömülü `order` nesnesi **yok**; `items` liste ucunda **hep boş** (kalemler yalnız `/refunds/{id}` detayında). Kredi notunun seri numarası yok → ekranda `#8`. |
| `GET /orders/{id}` | `incrementId` · `createdAt` · `customerEmail` · `customerFirstName/LastName` · kalemlerde `qtyOrdered/qtyInvoiced/qtyRefunded` | Zarfsız (düz sözlük). Gömülü `customer` **NULL olabilir**. `shipping_amount_refunded` ve `shippingTaxAmount` **yayınlanmıyor**. |
| `GET /dashboard/stats?type=total-sales` | Zarfsız **liste**; tutar `statistics.total_sales.current` | Alanın adı `total_sales`; `total`/`sales` diye aramak iade oranını hep boş bırakır. |
| `GET /bbd/return-requests` | `order_id` · `created_at` (snake) **ile** `orderIncrementId` · `customerName` · `itemCount` · `totalQuantity` (camel) yan yana | Durum **sözlüktür**: `{"id", "title", "color"}` ve başlığı **Türkçedir** ("İade Edildi"). Sebep de Türkçe **serbest metin** ("Üretim Hatası"). Kullanılabilir durum sözlüğü `meta.statuses` içinde gelir. |
| Süzgeç (çekirdek) | `date_from` · `date_to` · `order_id` **uygulanıyor** | `channel` ve `locale` sessizce yok sayılıyor (Laravel bilmediği parametreyi atar) — süzgeç sanılmamalı. |
| Süzgeç (`bbd/return-requests`) | `from` · `to` · `status` **uygulanıyor** | Adlar çekirdekten **farklı**: `date_from`/`date_to` gönderilirse sessizce yok sayılır ve aralık dışı talepler listeye karışır. |
| Sayfalama | `meta.currentPage/perPage/lastPage/total` | BBD ucu `page`/`last_page` der; **geçit** bunu çekirdek adlarına çevirir (`store_api`, K4). `per_page` sunucuda 50'ye kırpılır. |

Bu ekranın kullandığı `/api/admin/bbd/*` uçları bugün **yayında** (ölçüm
2026-08-16: `return-requests`, `shipments` → 200). **Bir dönem hepsi 404'tü** ve
kod bunu varsayıyordu; artık öyle değil. Eksik-uç dalı yine de duruyor: geçit
`bbd_endpoint_missing` döndürürse ekran o bölümü kapalı gösterir ve gerisi
çalışmaya devam eder (K7).

## Uçlar

`/api/store_refunds` öneki altında. Hepsi `requires(...)` taşır (K9).

Okuma: `GET /refunds` · `GET /orders/{orderId}` · `GET /audit` · `GET /printer`

Hesap: `POST /calculate` *(yazmaz; jeton üretir)*

Para hareketi (`store_refunds.approve`): `POST /approve` · `POST /pos-refund`

Süreç (`store_refunds.manage`): `POST /requests/{id}/status` ·
`POST /orders/{orderId}/notify`

Kargo ücreti doğuran (`store_refunds.ship_return`):
`POST /orders/{orderId}/return-shipment`

Rapor: `POST /preview` · `POST /print` · `POST /export`

## İzinler

| Anahtar | Ne açar | Roller |
|---|---|---|
| `store_refunds.view` | Ekran, hesap, rapor, CSV | admin · bbd_staff · **accountant** |
| `store_refunds.manage` | Talep durumu, müşteri bildirimi | admin · bbd_staff |
| `store_refunds.approve` | Kredi notu + POS iadesi — **para hareketi** | admin · bbd_staff |
| `store_refunds.ship_return` | Geri alım kargosu — **ücreti mağazaya yansır** | admin · bbd_staff |

`accountant` bu ekranı görür ama hiçbir şey yazamaz: para hareketini muhasebeci
başlatmaz, okur ve raporlar.

Koruma üç katmanlı (ADR 0012): ayrı izin anahtarı + `confirmWithReason`
(gerekçe ≥10 karakter, **backend'de de doğrulanır**) + `dryRun` varsayılanı.

## Yerel tablolar

Yalnız Bagisto'da **karşılığı olmayan** veri:

- `mod_store_refunds_audit` — gerekçe, aktör, sonuç. Mağazanın denetim kaydında
  "neden iade edildi" alanı yok; ağ koparsa "ne yapmaya çalıştık" burada kalır.
- `mod_store_refunds_calc` — onaya sunulan hesabın kendisi. Onay bu gövdeyi
  gönderir, **yeniden hesaplamaz**; jeton yoksa onay reddedilir.

## BBD uçlarına bağımlılık

Bu ekranın okuduğu/yazdığı BBD uçları (`return-requests`, `shipments`) mağaza
tarafında **yayında** — 2026-08-16'da ölçüldü. Bir dönem hepsi 404 dönüyordu ve
belge de kod da bunu yazıyordu; o cümle artık geçerli değil.

Bağımlılık dalı buna rağmen **kaldırılmadı**: uç bir gün geri çekilirse ya da
mağaza sürümü değişirse geçit `bbd_endpoint_missing` döner, ilgili bölüm
ekranda **kapalı** görünür ve "uç yayına girince açılacak" der. Etkilenen
bölümler: iade talepleri listesi, iade gönderisi, talep süreci. Kredi notları,
kalem seçimi, iade tutarı hesabı ve raporlar bu uçlar olmadan da çalışır (K7).

**POS iadesi ayrı bir durumdur ve bu listeye girmez:** mağazada BİLEREK yoktur.
`payments/attempts` grubu salt okunurdur; para hareketi başlatan hiçbir uç
Kontrol Merkezi'ne açılmamıştır ve geçit isteği hiç göndermeden reddeder. Bu bir
"henüz yayına girmedi" değil, kalıcı karardır.

## Testler

```bash
.venv/bin/python -m pytest modules/store_refunds/tests -q
.venv/bin/ruff check modules/store_refunds
```
