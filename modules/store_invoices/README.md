# Fatura

Mağaza faturaları, irsaliyeler, seri/numara denetimi, dönem icmali ve **yasal
fatura numarası eşlemesi**.

Grup: **BBD Store** · CSS öneki: `iv` · Rapor rafı:
`Raporlar/Mağaza/Finans/<yıl>/<ay>`

## Önce bu okunmalı: bu ekran yasal fatura KESMEZ

Depoda hiçbir **e-Fatura / e-Arşiv (GİB) entegrasyonu yoktur** ve Bagisto'nun
ürettiği PDF mali belge değildir; sipariş dökümüdür.

Bu yüzden ekranın tepesinde **kapatılamaz** bir uyarı bandı durur ve
"GİB'e gönder" gibi bir düğme **hiç yoktur** — olmayan bir uca bağlı düğme
koymak, kullanıcıya belgeyi gönderdiğini sandırırdı.

Akış şudur: yasal belge **dış sistemde** (mali müşavir portalı / GİB
uygulaması) kesilir, numarası bu ekrandan Bagisto faturasıyla **eşlenir**.
Eşleme yerel tablodadır (`mod_store_invoices_legal`) ve mağazaya tek bir istek
bile göndermez.

## Sekmeler

| Sekme | Ne yapar |
|---|---|
| **Faturalar** | Sunucu tarafı sayfalanmış liste; satırdan çekmecede kalemler, oran kırılımı ve **Yasal fatura no** formu. Seçimle birleşik PDF ve toplu durum değişikliği. |
| **İrsaliyeler** | Bagisto gönderi kayıtları. Seçimle **sevk dökümü** PDF'i (taslak — yasal sevk irsaliyesi değildir). |
| **Seriler & numaralandırma** | Seri tanımları, kullanılmış numaralar ve **numara boşluğu uyarısı**: “A2026 serisinde 145-147 eksik.” |

## Mağazanın verdiği kadarı — canlıya karşı doğrulandı (2026-08-13)

Bu ekranın rakamları `/api/admin/*` uçlarından geliyor ve o uçların üç sınırı
var. Üçü de ekranda **yazıyor**; sessizce doldurulmuyor.

| Ne | Gerçek | Ekran ne yapıyor |
|---|---|---|
| **Süzgeçler** | Yalnız `state`, `date_from`, `date_to`, `order_id` uygulanıyor. `search` ve tutar aralığı **sessizce yok sayılıyor** (16 faturanın 16'sı geri geliyor). | Yok sayılanlar hiç gönderilmez; arama ve tutar **gelen sayfada yerel** süzülür ve "yalnız bu sayfada uygulandı, N satır elendi" uyarısı çıkar. |
| **KDV oranı** | Fatura kalemlerinde `taxPercent` alanı **yok**; liste ucu kalemleri `items: []` olarak veriyor. | Oran, KDV tutarının matraha bölümünden **türetilir** ve "türetildi" rozetiyle / icmalde notla gösterilir. Tutar hiç gelmezse "Ayrıştırılamadı" kalır — sıfıra yazmak muafiyet gibi görünürdü. |
| **Faturası olmayan sipariş** | Sipariş **listesi** `invoices` dizisini hiç taşımıyor (yalnız `/orders/{id}` detayında var). | Adaylar **fatura listesiyle çapraz** denetlenir; ayrıca kesme anında sipariş detayı okunup faturası çıkan sipariş atlanır. Çapraz denetim yapılamazsa liste "DOĞRULANAMADI" damgasıyla gösterilir. |

Alan adları telde **camelCase** (`grandTotal`, `incrementId`, `trackNumber`).
`ledger.pick` adı normalleştirerek arar; snake_case yazım da çalışır.

## Öne çıkan işler

- **Toplu fatura kes** — faturası olmayan siparişler listelenir, seçilenlere
  sırayla fatura kesilir. Kuru prova varsayılan; sonuç **satır satır** döner.
  Kesilen fatura silinemez, iptali iade faturasıdır. Tek seferde en çok **25**
  sipariş: her sipariş iki istektir (önce "faturası var mı" denetimi, sonra
  kesme) ve mağaza dakikada 60 istek kabul ediyor.
- **Birleşik PDF** — seçili faturaların Bagisto PDF'leri `pdfunite` ile tek
  dosyada birleştirilir ve yazdırılır. Yeni Python bağımlılığı eklenmez;
  `poppler-utils` zaten önizleme (`pdftoppm`) için gerekli.
- **Dönem icmali** — mali müşavir için oran bazlı matrah/KDV/toplam, gün
  kırılımı, iade düşümü ve **yasal numarası eşlenmemiş faturaların listesi**.
- **Muhasebe CSV** — yasal numara sütunuyla; iki sistemi karşılaştırmak için.

## Yerel tablolar

Yalnız Bagisto'da **karşılığı olmayan** veri: `mod_store_invoices_legal`
(yasal numara eşlemesi), `mod_store_invoices_series` (seri tanımı),
`mod_store_invoices_audit` (gerekçeli denetim izi). Fatura, kalem ve tutar
mağazadadır ve **kopyalanmaz**.

## Sağladığı yetenek

`store.invoice.byOrder` — sipariş kimliğinden fatura künyesi, tutarlar ve
eşlenmiş yasal numara. **Salt okunur**: yetenek nesnesi yazma metodu taşımaz.

## İzinler

| Anahtar | Kim | Ne |
|---|---|---|
| `store_invoices.view` | admin · bbd_staff · accountant | Liste, icmal, CSV |
| `store_invoices.manage` | admin · bbd_staff | Seri, durum, müşteriye kopya |
| `store_invoices.issue` | admin · bbd_staff | Fatura kesme (geri alınamaz) |
| `store_invoices.legal_no` | admin · bbd_staff · accountant | Yasal numara eşleme |

`legal_no` bilerek `manage`den ayrıldı: yasal numarayı bilen kişi mali
müşavirdir, ama onun mağazaya belge kestirmesi ya da müşteriye e-posta
göndermesi istenmez.

## Testler

```bash
.venv/bin/python -m pytest modules/store_invoices/tests -q
.venv/bin/ruff check modules/store_invoices
```
