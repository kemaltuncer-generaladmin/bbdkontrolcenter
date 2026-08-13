# Talepler (RMA)

Müşteriden gelen iade, değişim, arıza, bilgi, şikayet ve fatura talepleri:
liste ⇄ pano, SLA takibi, yazışma zinciri, iade edilecek kalem seçimi, karar
ve RMA formu.

Grup: **BBD Store** · CSS öneki: `rq` · Rapor rafı:
`Raporlar/Mağaza/Müşteri/<yıl>/<ay>`

## Ne yapar

| Alan | Davranış |
|---|---|
| Liste | **Sunucu tarafı sayfalama.** SLA kalan süre sütunu: saat SAYISI + yazı + renk. |
| Pano | Durum başına bir sütun. Sütun başlığındaki sayı **gerçek toplamdır**, gösterilen kart sayısı değil. |
| Çipler | Süresi geçen · Bugün doluyor · Yanıt bizde bekliyor. |
| Süzgeç | Tarih aralığı **boş başlar** (kitin "son 7 gün" varsayılanı değil): bir hafta önce açılmış gecikmiş talep listeden düşer, "Süresi geçen" 0 gösterirdi. |
| Çekmece | Özet (künye + sipariş özeti + karar) · İade kalemleri · Yazışma · İşlem geçmişi. |
| Yazışma | **Müşteri yanıtı ve iç not AYRI uçlardır.** İç not mağazaya hiç gitmez. |
| Kalem seçimi | Adet mutlaktır; sipariş adedini aşamaz, daha önce iade edilen düşülür. |
| Karar | Onayla → kalemler **İadeler'e devredilir** (yerel kayıt + `store_requests.approved` olayı) · Reddet (gerekçeli). |
| Toplu | Durum değiştir · Ata · Kapat (en çok 50 talep, sırayla). |
| Çıktı | **RMA formu PDF** (müşteriye, kutuya konur, yazdırılır) · SLA raporu PDF · görünen sayfa CSV · tüm kayıtlar CSV. |

## Ne yapmaz — ve neden

- **Para iade etmez.** "Onayla" talebi onaylar ve seçili kalemleri İadeler
  ekranına devreder. Parayı iade etmek ayrı ekranın, ayrı iznin ve ayrı onayın
  işidir; buradan iade başlatmak, iade izni olmayan personele para iade
  ettirirdi (K9).
- **İç notu mağazaya göndermez.** "internal" bayrağının müşteri portalında
  yanlış yorumlanması geri alınamaz bir sızıntı olurdu. İç not yerel tabloda
  durur ve zincirde "yerel" rozetiyle görünür.
- **Talep silmez.** Kapatma vardır; kapanan talep zinciriyle birlikte kalır
  (ADR 0012).
- **Şablon yönetimi ekranı açmaz.** Yanıt şablonları `config/default.yaml`
  içindedir: sayıları bir elin parmakları kadar ve yılda birkaç kez değişiyor;
  bunun için CRUD ekranı açmak bakılacak bir ekran daha demekti.
- **Ayrı bir "talep ayarları" ekranı yoktur.** SLA saatleri modül ayarındadır.

## Canlıya karşı doğrulanan iki tuzak

İkisi de sessiz hataydı: ekran açılıyor, kimse bir şey fark etmiyordu.

- **Mağaza camelCase konuşuyor.** `GET /api/admin/orders/{id}` yanıtında
  `incrementId · grandTotal · createdAt · shippingTitle` var; `increment_id`
  YOK. Yalnız snake_case aranırsa çekmecedeki sipariş kartı açılır ama
  Tarih/Tutar/Kargo satırlarının hepsi "—" kalır. `rma.order_summary()` her
  adı iki yazımıyla da arar.
- **Saat dilimsiz damga UTC değil, mağazanın YEREL saati.** 2026-08-13'te
  canlıdan okunan en yeni siparişin damgası `18:27:17` iken sunucunun kendi
  saati `16:44 UTC` idi — UTC varsayımı var olan bir siparişi 1 saat 43 dakika
  geleceğe atıyordu. Kozmetik değil: SLA kalan süresi bu damgadan hesaplanıyor
  ve 4 saatlik acil bir talepte "3,9 saat kaldı" yazarken gerçekte 0,9 saat
  kalmış oluyordu.

## Yedi karar

Hepsinin karşılığı `backend/rma.py` içinde bir fonksiyon ve
`tests/test_store_requests_rma.py` içinde adı kararı söyleyen bir testtir.

1. **Uzak alan adları oynak.** `/api/admin/bbd/return-requests` hâlâ yazılıyor;
   `pick()` bir bilgiyi olası adlarının hepsinde arar, bulamazsa "—" yazar.
2. **`due_at` gelirse o kazanır.** Gelmezse SLA, açılış + öncelik saatidir.
3. **"Müşteri bekleniyor" durumunda sayaç durur.** Yanıt bizde değilken geçen
   süreyi kendi gecikmemiz saymak, personeli olmayan bir suçla cezalandırırdı.
4. **Kapanan talebin kalan süresi sıfır değil YOKTUR.** Sıfır "tam zamanında"
   demektir ve arşivi kırmızıya boyardı.
5. **Renk tek başına anlam taşımaz.** SLA hücresi her zaman saat sayısı + yazı
   + renk taşır; CSV'de de saat sayısı yer alır.
6. **Türetilmiş süzgeç uzakta uygulanmadıysa söylenir.** SLA ve "yanıt bizde"
   süzgeçleri uzak uç tanımıyorsa sayfa yerelde daraltılır ve ekran "bu SAYFA
   daraltıldı, toplam daraltmadan öncesine ait" der.
7. **İptal edilmiş kalem iade edilemez.** `maxQty` sipariş adedinden yalnız
   iade edilen değil, `qtyCanceled` de düşülerek bulunur: iptal edilen kalem
   hiç gönderilmedi, geri gelemez.

## Uzak uç henüz yayında olmayabilir

`/api/admin/bbd/return-requests` yazım aşamasında. Geçit 404'ü
`bbd_endpoint_missing` hatasına çeviriyor; ekran çökmez (K7): liste boş durum
kartında "uç mağazada yayınlanınca liste kendiliğinden dolacak" der, çekmece
yalnız yerel iç notları gösterir ve panel çalışmaya devam eder.

## Uçlar

`/api/store_requests` öneki altında. Hepsi `requires(...)` taşır (K9).

Okuma: `GET /requests` · `GET /board` · `GET /requests/{id}` ·
`GET /reference` · `GET /audit` · `GET /printer`

Yazma: `POST /requests/{id}/note` (yerel) · `POST /requests/{id}/reply` ·
`POST /requests/{id}/update` · `POST /requests/{id}/items` ·
`POST /requests/{id}/approve` · `POST /requests/{id}/reject` · `POST /bulk` ·
`POST /preview` · `POST /print` · `POST /export`

## İzinler

| Anahtar | Ne açar |
|---|---|
| `store_requests.view` | Ekran, RMA formu, SLA raporu, CSV |
| `store_requests.manage` | Yanıt, iç not, durum/öncelik/atama, kalem seçimi, toplu işlem |
| `store_requests.decide` | Onaylama/reddetme (onay İadeler'e devreder) |

## Yerel tablolar

Yalnız mağazada **karşılığı olmayan** veri:
`mod_store_requests_audit` (gerekçe), `mod_store_requests_notes` (iç not),
`mod_store_requests_handoff` (İadeler'e devir). Talep kaydı kopyalanmaz.

## Yetenekler

Tüketir (hepsi `optional`): `store.order.card` (sipariş özeti tazeleme),
`store.customer.card` (müşteri künyesi), `store.audit.for` (mağaza denetim
kaydı), `printer`. Yetenek yoksa ilgili bölüm çizilmez, ekran çalışır.

Yayınlar: `store_requests.approved` —
`{requestId, orderId, amount (kuruş), items, actor, reason}`.

## Testler

```bash
.venv/bin/python -m pytest modules/store_requests/tests -q
.venv/bin/ruff check modules/store_requests
```
