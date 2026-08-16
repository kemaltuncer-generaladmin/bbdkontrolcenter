# Vergilendirme

BBD Store'un KDV oranları, vergi kategorileri, ürün eşlemesi ve **dönem KDV
icmali**. Ekran Bagisto admin panelinin aynası değildir: Bagisto'da vergi üç
ayrı sayfaya dağılmış durumda ve hangi ürüne hangi yüzdenin uygulandığı hiçbir
sayfada görünmüyor. Burada o soru tek ekranda cevaplanır.

Grup: **BBD Store** · CSS öneki: `tx` · Rapor rafı:
`Raporlar/Mağaza/Finans/<yıl>/<ay>`

## Ekran

| Sekme | Ne yapar |
|---|---|
| **Vergi oranları** | Oranlar, kapsamları (ülke/eyalet/posta kodu) ve bağlı vergi kategorileri. Süzgeçler: arama · oran aralığı · bölge · **Hiçbir ürüne atanmamış** · **Çakışan kural** |
| **Kategoriler** | Oran demetleri. Ürün sayısı sütunu ve elle tetiklenen katalog taraması |
| **Bölgeler** | Oranlardan **türetilmiş** ülke/eyalet görünümü — "bu bölgeye hangi oranlar bakıyor" |
| **Ürün eşlemesi** | Ürün → vergi kategorisi toplu atama. Önce **fark tablosu**, sonra gerekçeli onay |
| **KDV icmali** | `accountant` rolünün ana ekranı. Tarih aralığı → oran bazlı matrah/KDV/toplam + iade düşümü + kanal kırılımı → **yazdırılabilir PDF** |

## Bilerek verilmiş kararlar

**Oran değişikliği geçmiş faturaları etkilemez.** Bu cümle ekranda kapatılamaz
bir uyarı olarak durur, her yazma diyaloğunda tekrarlanır ve her rapora dipnot
düşer. Muhasebenin en sık sorduğu soru budur.

**Geçerlilik tarihi YEREL bir nottur.** Bagisto vergi oranında tarih alanı
tutmaz; mağazaya gönderilseydi Laravel alanı sessizce yok sayar ve kullanıcı
"tarihi ayarladım" sanırdı. Tarih `mod_store_tax_effective` tablosunda durur,
ekranda kesikli çizgiyle işaretlenir ve **geçmişe verilemez** — geçmiş bir
tarih, o tarihten beri kesilen faturaların yeni orandan kesildiğini iddia
ederdi.

**Öncelik ve bileşik vergi alanı yoktur.** Bagisto çekirdeğinde bu kavramlar
bulunmuyor (Magento'da var). Boş bir sütun koymak "ayarlayabilirim" izlenimi
verirdi; gövdeye de konmaz.

**Vergi oranı silinmez.** Silinen oran geçmiş faturaların dayanağını görünmez
kılar. Kullanımdan kaldırmak için oran vergi kategorisinden çıkarılır — geri
alınabilir bir işlemdir.

**Çakışma yalnız aynı vergi kategorisi içinde aranır.** Mağaza ürüne önce
kategoriye bakar, sonra o kategorinin oranları içinden adrese uyanı seçer.
Kitap %0, kalem %20 normaldir; aynı kategoride iki oranın aynı adresi
karşılaması ise belirsizdir ve Bagisto hangisini uyguladığını söylemez.

**KDV icmalinde eksik veriyle rapor üretilmez.** Üç emniyet kemeri var:
belge sayısı tavana dayanırsa rapor reddedilir, mağaza tarih süzgecini
uygulamadıysa (Laravel tanımadığı parametreyi sessizce yok sayar) rapor
reddedilir, kalem toplamları belgenin KDV'sini tutmuyorsa fark **kendi
satırında** gösterilir. "Yaklaşık doğru" bir beyan, yanlış beyandır.

**Kaynak belge faturadır, sipariş değil.** Sipariş satış vaadi, fatura vergi
doğuran olaydır. İptaller icmalden düşülmez: faturası kesilmiş bir iptalin
düşümü iade (credit memo) kaydıyla zaten gelir, iki kez düşmek beyanı
eksiltirdi. İptaller yalnız uyarı satırı olarak rapor edilir.

**Ürün listesi vergi kategorisi taşımıyorsa "Atanmamış" yazılmaz.** Alan yoksa
satır "Bilinmiyor" der; kesin değer, seçim yapılıp önizleme alındığında ürün
tek tek okunarak gelir. Aynı kural katalog taramasında da geçerli: alan hiç
görülmediyse hiçbir sayı kaydedilmez.

**Toplu eşleme küçük tutulur.** Bagisto'da ürünün vergi kategorisini toplu
yazan uç yok; her ürün ayrı PUT ile ve **oku-değiştir-yaz** ile gider. Mağaza
dakikada 60 istek kabul ettiği için varsayılan sınır 50 üründür ve ekran işin
kaç dakika süreceğini söyler.

## Canlı mağazanın dayattığı gerçekler (2026-08-13'te doğrulandı, 2026-08-16'da yeniden ölçüldü)

Aşağıdakilerin tamamı 2026-08-16'da canlıya karşı **salt okunur** GET ile
yeniden sınandı. Bir tanesi (kanal süzgeci — sipariş ucu) eskimişti ve
düzeltildi; gerisi hâlâ geçerli. Sayılar ölçüm tarihiyle birlikte okunmalıdır:
katalog ve belge sayıları büyüyor, iddianın özü (süzgeç uygulanıyor mu, alan
geliyor mu) büyümüyor.

**Yanıtlar camelCase, istekler snake_case.** `GET /settings/tax-rates`
`{"identifier":…, "taxRate":20, "isZip":false}` döndürüyor; aynı kaydın
`POST`/`PUT` gövdesi ise (`/api/admin/docs` şemasına göre) `identifier`,
`tax_rate`, `is_zip` bekliyor. Aynısı fatura (`baseSubTotal`, `createdAt`,
`channelName`) ve üründe (`taxCategoryId`, `urlKey`) geçerli. Okuma iki adı da
dener (`taxes.pick`), yazma yalnız snake_case üretir.

**Vergi kategorisi liste ucu oran ilişkisini vermiyor.**
`GET /settings/tax-categories` her kayıt için `"taxRates": null` döndürüyor;
tekil uç (`/settings/tax-categories/{id}`) ilişkiyi dolu veriyor. `null` "oranı
yok" DEĞİLDİR, bu yüzden ekran «oransız/bağlantısız/çakışma» işaretlerini
göstermez ve **mevcut kategorinin düzenlenmesini kapatır** — Bagisto kısmi
`taxrates` kabul etmediği için kaydetmek, göremediğimiz oranları kategoriden
düşürürdü. Kalıcı çözüm `store.api` geçidine `tax_category(id)` metodu
eklenmesidir (uç canlıda çalışıyor, geçitte metot yok).

**Ürün liste ucu `taxCategoryId` alanını doldurmuyor.** 1428 numaralı ürün
listede `null`, tekil uçta `1` dönüyor. Bu yüzden eşleme sütunu "Bilinmiyor"
der, katalog taraması sayı üretmeyi reddeder ve vergi kategorisi süzgeci
uygulanamadığında ekran bunu **yazıyla** söyler.

**Uygulanmayan süzgeçler kaldırıldı.** `?channel=…` **fatura ve iade**
listelerinde yok sayılıyor (2026-08-16: `invoices?channel=zzzz` de
`invoices?channel=1` de 17 faturanın hepsini, `refunds?channel=zzzz` 3 iadenin
hepsini döndürüyor); kanal ayıklaması bu yüzden Kontrol Merkezi'nde yapılır ve
rapor bunu yazar. `?category_id=…` ürün listesinde yok sayılıyor; uç parametresi
tamamen kaldırıldı. Uygulanan süzgeçler (`date_from`/`date_to`, `status`,
`name`) canlıda tek tek denendi.

> Bu satır bir dönem "fatura/iade/**sipariş**" diyordu; sipariş için artık
> doğru değil. 2026-08-16 ölçümü: `orders?channel=1` → 18, `orders?channel=default`
> → 0, `orders?channel=zzzz` → 0. Yani sipariş ucu kanalı GERÇEKTEN uyguluyor ve
> **kimlik** bekliyor (aşağıdaki ürün tablosunun tam tersi). İcmal bunu zaten
> güvenle karşılıyor: iptal listesine kanal parametresi hiç gönderilmiyor,
> ayıklama `summary.summarize` içinde kanal ADIYLA yapılıyor. Not düzeltildi ki
> sonraki okuyan "sipariş ucu da yok sayıyor" sanıp `channel=default` göndermesin
> — o parametre iptal listesini sessizce boşaltırdı.

**Mağaza şu an KDV'siz satıyor.** 17 faturanın hepsi `baseTaxAmount: 0`. Alanın
dolu ve sıfır olması mağazanın BEYANIDIR: bu belgeler `%0` satırına yazılır,
"çözülemedi" kovasına değil. Alan hiç yoksa oran türetilmez ve belge
çözülemeyenlere düşer.

**Ürün ucunda `channel` KOD ister, KİMLİK değil — siparişin TAM TERSİ.** Bu
ayrım canlıda tek tek denendi:

    /catalog/products                        → 1.422   (süzgeçsiz)
    /catalog/products?channel=default        → 1.422   ✔ kod çalışıyor
    /catalog/products?channel=1              → 0       ✘ kimlik listeyi boşaltır
    /catalog/products?channel=zzzz           → 0       (süzgeç GERÇEKTEN uygulanıyor)

Siparişte kural terstir (`channel=default` → 0 kayıt, `channel=1` → dolu; bkz.
`store_orders`). Bu yüzden buradaki `config/default.yaml → channel: "default"`
DOĞRUDUR ve sipariş ekranının `channel_id` ayarıyla karıştırılmamalıdır. İki uç
aynı parametre adını farklı anlamda kullanıyor; birini diğerine benzetmek
listeyi sessizce boşaltır.

**Ürünün `taxCategoryId` alanı üç ayrı şey söyleyebilir.** Canlıda tekil uç:
1428 → `1`, 1426 → `0`, 1427 → `null`. Yani `0` "atanmamış" (mağazanın beyanı),
`null` "bilinmiyor" (alan gelmedi). Liste ucunda **hepsi** `null` geliyor.
Üçünü tek kovaya atmak, atanmamış ürünü okunamayan ürünle karıştırırdı;
`product_row` bunları `taxKnown` ile ayırır.

## Uçlar

Önek: `/api/store_tax`

| Uç | İzin | Ne yapar |
|---|---|---|
| `GET /rates` | `view` | Oran listesi + süzgeç anahtarları + çakışmalar |
| `GET /rates/{id}` | `view` | Oranın düzenleme künyesi |
| `POST /rates` · `PUT /rates/{id}` | `manage` | Oran ekle/güncelle (oku-değiştir-yaz) |
| `POST /rates/{id}/effective` | `manage` | Yalnız yerel geçerlilik notu — mağazaya istek gitmez |
| `GET /categories` | `view` | Vergi kategorileri + ürün sayıları |
| `POST /categories` · `PUT /categories/{id}` | `manage` | Kategori ekle/güncelle (oran listesi TAM gider) |
| `GET /regions` | `view` | Oranlardan türetilmiş bölge görünümü |
| `GET /mapping` | `view` | Ürün → vergi kategorisi listesi (sunucu tarafı sayfalama). Vergi kategorisi listesi AYRI uçtan gelir: düşerse `categoriesConnected: false` + `note` döner, ürün listesi çizilmeye devam eder |
| `POST /mapping/scan` | `view` | Katalog taraması: kategori başına ürün sayısı |
| `POST /mapping/preview` | `assign` | Fark tablosu (jeton üretir) |
| `POST /mapping/apply` | `assign` | Önizlenen eşlemeyi uygular |
| `GET /summary` | `view` | **KDV icmali** (kanal süzgeci burada uygulanır) |
| `GET /audit` | `view` | Bu ekrandan yapılan yazmaların yerel izi — oran çekmecesindeki "İşlem geçmişi" kartı okur |
| `POST /preview` · `POST /print` · `GET /printer` | `view` | Rapor zinciri (`vat` · `rates`) |
| `POST /export` | `view` | CSV (`vat` · `rates`) |

## Tablolar

Yalnız Bagisto'da **karşılığı olmayan** veri:

- `mod_store_tax_audit` — yazma gerekçesi ve sonucu
- `mod_store_tax_effective` — oranın geçerlilik tarihi ve notu
- `mod_store_tax_usage` — vergi kategorisi başına ürün sayısı + tarama zamanı
- `mod_store_tax_assign` — toplu eşleme önizlemesi (jeton)

## Sağladığı yetenek

`store.tax.rates` — oranlar ve **vergi kategorisi → yüzde** eşlemesi. Ürünler,
Fatura ve Siparişler ekranları "bu ürün yüzde kaç KDV'li" sorusunu buradan
sorar; ilişkiyi kuran tek yer bu modüldür. Salt okunur, hata fırlatmaz
(mağaza erişilemezse boş liste + `connected: false`), 60 saniyelik önbelleği
her yazmadan sonra düşer.

`for_category()` `None` dönerse bu **"KDV'siz" demek değildir**, "kategori
bilinmiyor" demektir; çağıran ekran sıfır varsaymaz.

**Şu an eksik:** liste ucu ilişkiyi vermediği için `percent` alanı `null`
geliyor ve tablo `membershipKnown: false` taşıyor. Tüketen ekran bu bayrağı
görürse "KDV oranı okunamadı" yazmalı, sıfır varsaymamalıdır. Bayrak
`store.api` geçidine `tax_category(id)` metodu eklenince kendiliğinden
düzelir — modülün başka bir değişikliğe ihtiyacı yok.

## İzinler

| İzin | admin | bbd_staff | accountant |
|---|---|---|---|
| `store_tax.view` | ✔ | ✔ | ✔ |
| `store_tax.manage` | ✔ | ✔ | ✗ |
| `store_tax.assign` | ✔ | ✗ | ✗ |

`accountant` ekranı görür (KDV icmali onun ana ekranıdır) ama hiçbir şey
yazamaz: oran değiştirmek satışın vergisini değiştirir ve bu muhasebenin değil
mağaza yönetiminin kararıdır. `store_tax.assign` ayrı bir anahtardır çünkü o uç
**kataloğa** yazar — yanlış eşleme, yanlış KDV'yle satış demektir.

## Derin inceleme — onarılan kusurlar (2026-08-14)

Hepsi canlıya karşı doğrulandı ve her birinin bir testi var.

| Kusur | Ne oluyordu | Ne yapıldı |
|---|---|---|
| **Künyesiz yanıt düzenlemeye açılıyordu** | Mağaza 200 dönüp boş gövde verirse `rate_row` yine satır üretiyordu (id `0`, ad `#0`); çekmece hayali kaydı düzenlemeye açıyordu. Panelin `if (!row)` koruması hiç çalışmıyordu — `rate_row` her zaman bir sözlük döner. | `rate()` kimliksiz gövdeyi `ok:false` ile reddeder; panel koruması ikinci emniyet kemeri olarak kaldı. |
| **`PUT /rates/0` mağazaya gidiyordu** | `if rate_id:` sıfırı "yeni kayıt" sanıyor, geçitteki `rate_id is None` ise sanmıyordu: sonuç `PUT /settings/tax-rates/0`. | `taxes.id_error` üç yazma yolunda da (oran, kategori, geçerlilik notu) kimliği kapıda durdurur (K9: backend'de). |
| **Mağaza kapalıyken "kategori bulunamadı"** | `assign_preview` bağlantıyı denetlemiyordu; boş kategori listesinde arama boş dönüyor ve ekran kullanıcıyı olmayan bir veri sorununu aramaya yolluyordu. | Önce bağlantı denetlenir, hata mağazanın hatasını söyler (`save_category` zaten böyleydi). |
| **Katalog taraması yarım tablo yazıyordu** | Kategori listesi okunamazsa yalnız üründe GÖRÜLEN kategoriler yazılıyordu; ürünü kalmamış kategori eski sayısıyla asılı kalıyor ve «hiçbir ürüne atanmamış» süzgeci sessizce yanlış çalışıyordu. | Liste okunamazsa **hiçbir şey yazılmaz** ve neden söylenir — `if not seen` kuralının aynısı. Tarama elle tetiklenir, tekrarlanabilir. |
| **Eşleme sekmesinde sessiz ölü düğme** | Vergi kategorisi listesi ayrı uçtan gelir ve ayrı düşer. Düştüğünde açılır kutu boşalıyor, «Fark tablosunu göster» hiç çalışmıyor, sütunda ad yerine `#1` görünüyor ve sebebi hiçbir yerde yazmıyordu. | Yanıt `categoriesConnected`/`categoriesError` taşır, `note` durumu anlatır; panel açılır kutuyu ve düğmeyi **açıkça** kapatıp nedenini yazar. |
| **Kâğıt ekranla aynı şeyi söylemiyordu** | İlişki okunamazken ekran «okunamadı» derken CSV ve PDF `0` yazıyordu. Canlıda liste ucu ilişkiyi hiç döndürmediği için bu satır kâğıtta **her zaman** 0 çıkıyordu ve mali müşavire "bu oran hiçbir kategoride kullanılmıyor" diye okunurdu. | `_category_count` bilinmeyeni «okunamadı» yazar (sunucu CSV'si, PDF ve panelin «⤓ Görünen» CSV'si); PDF ayrıca nedeni dipnota düşürür. |
| **Kapanan pencerelerin bırakıcıları birikiyordu** | Her çekmece/diyalog açılışı `disposers` dizisine kalıcı bir kayıt ekliyor, pencere kapanınca silinmiyordu. | `trackWhileOpen()` hem panel kapanışında bırakır hem pencere kapanınca listeden düşer; iki kez çağrılmaya dayanıklıdır. |

Fark tablosu diyaloğu **bilerek** elle kuruluyor: kit'in `drawer`'ı 760 px ve
«önce/sonra» sütunları kesiliyor (`panel.css` `.tx-assign` = 860 px). Kit
kuralına uyar — overlay `nodes.root`'a eklenir, `document.body`'ye değil.

## Testler

```bash
.venv/bin/python -m pytest modules/store_tax/tests -q     # 128 test
.venv/bin/ruff check modules/store_tax
```
