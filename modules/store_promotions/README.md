# Promosyonlar

BBD Store kampanyaları: sepet kuralları, katalog kuralları, kuponlar ve
kampanya performansı.

Grup: **BBD Store** · CSS öneki: `pm` · Rapor rafı:
`Raporlar/Mağaza/Satış/<yıl>/<ay>`

## Ne yapar

| Sekme | Davranış |
|---|---|
| Sepet kuralları | Kullanım çubuğu (kullanılan/limit) ve takvim durumu olan liste; satırdan **tek sayfa** kural düzenleyici. |
| Katalog kuralları | Vitrin fiyatına inen indirimler; ayrı ve daha küçük düzenleyici. |
| Kuponlar | Kural seçilir → kupon listesi + **üreteç** (önek + adet + uzunluk + biçim) → CSV. |
| Performans | Tarih aralığında kupon başına ciro, indirim maliyeti, ortalama sepet; kural kullanım çubukları. |
| Simülasyon | Düzenleyicinin içinde: **örnek sepet gir → indirim ne olur**; uygulanmayan kuralları da nedeniyle listeler. |
| Çıktı | Kampanya performansı PDF · kampanya listesi PDF · kural/kupon CSV · üretilen kupon partisi CSV. |

**Kural düzenleyici tek sayfadır.** Kuralın altı parçası (künye · koşullar ·
eylem · kısıtlar · takvim · simülasyon) birbirine bağlı: koşulu değiştirince
eylemin ne yapacağını görmek gerekiyor. Sekmelere bölmek her denemeye üç
tıklama ekliyordu.

## Ne yapmaz — ve neden

- **Kural silmez.** Durdurulur. Silinirse geçmiş siparişlerin hangi kampanyayla
  indirildiği kaybolur ve performans raporu "bilinmeyen kod" satırlarıyla
  dolar (ADR 0012).
- **Kullanılmış kuponu kaldırmaz.** Kodun izi siparişte duruyor; performans
  ekranı onu oradan okuyor.
- **Kupon kodu üretmez.** Kodları mağaza üretir — benzersizliği garanti
  edebilecek tek yer orası. Ekrandaki "örnek kodlar" yalnız biçim
  önizlemesidir ve hiçbir yere yazılmaz. Üretimden sonra kupon listesi yeniden
  okunur, fark alınır ve gerçek kodlar CSV olur. Tek partide en çok **3.000**
  kod: fark alabilmek için listeyi taramak gerekiyor ve tarama orada duruyor.
  Liste tam okunamazsa üretim **hiç yapılmaz** — eksik parti dosyası, dağıtılan
  kodların bir kısmının kayıp olması demektir.
- **Yeni kuralı yayına almaz.** Taslak açılır; yayına alma ayrı yetkidir.
- **Simülasyon mağazanın motoru değildir.** Kutunun altında böyle yazar: kesin
  tutar ödeme adımında oluşur. Buradaki hesap kuralın YANLIŞ yazıldığını
  (koşul hiç tutmuyor, iki kampanya üst üste biniyor, indirim sepetten büyük)
  yazmadan önce gösterir.

## Bagisto tuzakları

Hepsinin karşılığı `backend/promotions.py` içinde bir fonksiyon ve
`tests/` içinde adı tuzağı söyleyen bir testtir. 8–13 arası, canlı mağazaya
(`bbdstore.com.tr`) karşı **salt okuma** ile doğrulandı.

1. Kural kaydı kısmi PUT kabul etmez → `write_rule_body` **oku-değiştir-yaz**.
2. Mağaza yöneticisinin kendi eklediği koşulları bu ekran tanımaz →
   `decode_conditions` onları ayırır, `encode_conditions` **aynen geri koyar**;
   düzenleyici kaç tane olduğunu yazar. (Canlıda kural #5'in `product|sku`
   koşulu tam olarak böyle korunuyor.)
3. `status=1` "yayında" demek değildir → `rule_status` takvimi de okur; tarihi
   geçmiş kural **ölüdür** ve öyle rozetlenir.
4. Para telde ondalık, içeride kuruş → `to_kurus` / `from_kurus`, `Decimal`.
5. Yüzde de tutar da `discount_amount` alanında durur → `action_value` eylem
   tipine bakar; yüzdeyi kuruşa çevirmek %10'u "%1000" gösterirdi.
6. Kanal/grup bazen nesne, bazen kimlik gelir → `id_list`.
7. `max_discount_amount` her mağaza sürümünde yok → `field_supported`;
   bulunmayan alana yazmak "kaydettim" yanılgısı üretir, alan kapalı gelir.
   **Canlı mağazada bu alan YOK**; üst sınır kutusu kapalı açılır.
8. **Okuma camelCase, yazma snake_case.** API kaynağı `startsFrom` ·
   `couponType` · `timesUsed` · `usagePerCustomer` · `usageLimit` · `expiredAt`
   yayınlıyor; yazma isteği veritabanı sütun adlarını bekliyor → `pick()` iki
   adı da okur, gövde **her zaman** snake_case kurulur.
9. **`apply_free_shipping` diye bir sütun yok.** Ücretsiz kargo
   `free_shipping`, "indirim kargoya da insin mi" ise ayrı bir sütun
   (`apply_to_shipping`) — ikincisine dokunulmaz, taşınır.
10. **Kural listesi kapsamı ve koşulu vermiyor**: `conditions` · `channels` ·
    `customerGroups` listede `null`, yalnız tekil kayıtta dolu. Simülasyon bu
    yüzden aktif kuralları **tek tek** okur; kanal/grup süzgeci liste bu bilgiyi
    vermediğinde ekranda **kapatılır** (sessizce hiçbir şey yapmaz görünmez).
11. **Sipariş listesi indirim tutarı taşımıyor** — `discountAmount` yalnız
    sipariş detayında. Kuponlu siparişler tek tek okunur; okunamayan varsa
    indirim "—" gösterilir, **sıfır yazılmaz**.
12. **`/api/admin/products` `name` parametresini tanımıyor**; Laravel onu
    sessizce yok sayıp 1.421 ürünün tamamını döndürüyor. Doğru ad `query`.
13. **Sıfır indirim + ücretsiz kargo geçerli bir kuraldır** (canlıda iki tane
    var). Sıfırı koşulsuz reddeden doğrulama o kampanyaların **adını bile**
    değiştirilemez yapıyordu.

### Mağaza tarafında kapalı kalan iki kapı

- **Ödeme yöntemleri okunamıyor.** `GET /api/admin/configuration?slug=…` bir
  **dizi** döndürüyor; geçidin tekil okuyucusu sözlük bekliyor ve diziyi `{}`'ye
  düşürüyor. Geçit paylaşılan dosya olduğu için burada düzeltilemedi. Ekran
  sessiz boş bir açılır göstermiyor: ödeme koşulunda kod elle yazılıyor ve
  nedeni yazıyor. `store_api` tekil okuyucusu dizi biçimini de kabul ederse
  burası kendiliğinden açılır.
- **`max_discount_amount`** mağaza sürümünde yok (yukarıda 7).

## Uçlar

`/api/store_promotions` öneki altında. Hepsi `requires(...)` taşır (K9).

Okuma: `GET /rules` · `GET /rules/{id}` · `GET /rules/{id}/coupons` ·
`GET /catalog-rules` · `GET /catalog-rules/{id}` · `GET /reference` ·
`GET /products` · `GET /performance` · `GET /audit` · `GET /batches` ·
`GET /batches/{token}` · `GET /coupon-preview` · `GET /printer`

Yazma: `POST /rules` · `PUT /rules/{id}` · `POST /rules/{id}/copy` ·
`POST /rules/{id}/status` · `POST /catalog-rules` · `PUT /catalog-rules/{id}` ·
`POST /rules/{id}/coupons` · `POST /rules/{id}/coupons/generate` ·
`POST /rules/{id}/coupons/remove`

Hesap/çıktı: `POST /simulate` (mağazaya yazmaz) · `POST /preview` ·
`POST /print` · `POST /export`

## İzinler

| Anahtar | Ne açar |
|---|---|
| `store_promotions.view` | Ekran, simülasyon, performans, rapor, CSV |
| `store_promotions.manage` | Kural içeriği (taslak kalır) |
| `store_promotions.activate` | **Yayına alma / durdurma** — yayındaki kural her siparişte para indirir |
| `store_promotions.coupons` | Kupon üretme ve kullanılmamış kodu kaldırma |

Düzenleme ile yayına alma ayrı tutulur: aksi hâlde taslak yazma yetkisi,
kampanya başlatma yetkisi olurdu. `accountant` bu ekranı görmez — promosyon
mali bir rapor değil, satış politikasıdır.

## Yerel tablolar

Yalnız Bagisto'da **karşılığı olmayan** veri:
`mod_store_promotions_audit` (gerekçe) ve `mod_store_promotions_batches`
(kupon partisi: önek, adet, kim, neden, dosya yolu, kodlar). Kural, koşul,
kupon ve kullanım sayacı mağazadadır ve kopyalanmaz.

## Olay

`store.promotion.changed` yayınlanır (kimlik + kapsam + eylem). Kural içeriği
taşınmaz: dinleyen modül veriyi kendi izniyle geçitten okur.

## Testler

```bash
.venv/bin/python -m pytest modules/store_promotions/tests -q
.venv/bin/ruff check modules/store_promotions
```
