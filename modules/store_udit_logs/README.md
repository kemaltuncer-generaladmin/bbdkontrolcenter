# UDİT — İşlem Kayıtları

Mağazada kim, neyi, ne zaman ve **neden** değiştirdi. Alan alan fark tablosu,
gerekçe araması, denetim dökümü.

Grup: **BBD Store** · CSS öneki: `ud` · Rapor rafı:
`Raporlar/Mağaza/Denetim/<yıl>/<ay>`

> **Bu ekran salt okunurdur.** Denetim kaydı silinemez, düzenlenemez. Ekranın
> yaptığı tek yazma işlemi rapor dosyası üretmektir; onun da izi tutulur.

## Ne yapar

| Alan | Davranış |
|---|---|
| Kaynak | **İki kaynağın birleşimi:** uzak `admin_api_audits` + geçidin yerel izi `mod_store_api_audit`. |
| Sayfalama | **Sunucu tarafı, imleçli, 100'lük.** 20 ekran içindeki tek istisna. |
| Tarih aralığı | **ZORUNLU.** Saat hassasiyetlidir; en geniş aralık `max_range_days` (varsayılan 92 gün). |
| Tablo | Yoğun (`dense`) kip: Zaman · Kullanıcı · İşlem · Varlık · Kayıt · Özet · Gerekçe · IP · Sonuç. |
| Çekmece | Alan alan fark tablosu (öncesi → sonrası, değişenler vurgulu ve **yazıyla işaretli**) · ham JSON katlanır · `[Kayda git]` ilgili panele geçer. |
| Anahtarlar | `Yıkıcı işlemler` · `Gerekçeli işlemler` · serbest metin araması **gerekçe metnini de** tarar. |
| Çıktı | Denetim dökümü PDF · "bu kayıt için tüm geçmiş" PDF · görünen sayfa CSV · aralığın tümü CSV. |
| Sağlar | `store.audit.for` — 19 panelin çekmecesindeki "İşlem geçmişi" sekmesi. |

## İki kaynak, tek zaman çizgisi

Bagisto'nun denetim tablosu ile geçidin izi **birbirinin eksiğini kapatır**:

| | uzak `admin_api_audits` | yerel `mod_store_api_audit` |
|---|---|---|
| Alan farkı (öncesi/sonrası) | ✔ | ✖ (yalnız gönderilen gövde) |
| **Gerekçe** | ✖ (Bagisto tutmaz) | ✔ |
| Başarısız / reddedilen / kuru prova | ✖ | ✔ |
| Ağ koparsa | iz kalmaz | iz kalır |

Birleştirme üç tuzak doğurur; üçünün de karşılığı `backend/records.py` içinde
bir fonksiyon ve `tests/test_store_udit_logs_records.py` içinde adı tuzağı
söyleyen bir testtir:

1. **Saat ekseni.** Geçit UTC yazar, mağaza yerel saat yazar. Ham metin olarak
   sıralamak Türkiye'de üç saat kaydırır → `normalize_stamp`.
2. **Çift kayıt.** Başarılı yazma iki kaynakta da vardır. İstek kimliği
   (`X-Bbd-Request-Id`) ikisini bağlar: uzak satır **farkı**, yerel satır
   **gerekçeyi** verir → `join_reason` + `local_only`. Sonucu `ok` olan geçit
   satırı listede tekrar etmez, yalnız gerekçe kaynağıdır.
3. **Alan adı kararsızlığı.** Mağazanın yönetici zarfı sütunları **camelCase**
   döndürüyor (canlı doğrulandı: `incrementId`, `grandTotal`, `createdAt`),
   yani beklenen ad `oldValues`/`auditableType`/`ipAddress`'tır; `old_values`/
   `auditable_type` yalnız zarfsız bir sürümde gelir. Tek ada bel bağlamak
   ekranı sessizce boşaltır — satır gelir, fark tablosu boş çıkar, "Varlık" ve
   "IP" sütunları "—" dolar → `first_of`.

Aynı kural **yol → varlık** eşlemesi için de geçerlidir: desenler
`store_api/backend/client.py` içindeki GERÇEK yollardan alınır. Canlı mağazada
`/api/admin/sales/*`, `/promotions/*`, `/settings/taxes/*` ve `/reviews`
**yoktur** (404); doğruları `/orders`, `/invoices`, `/refunds`, `/shipments`,
`/transactions`, `/marketing/*`, `/settings/tax-rates`,
`/settings/tax-categories`, `/customers/reviews`'dır.

## Neden imleç, neden sayfa numarası değil

Kayıt sayısı sınırsız büyür; "sayfa 412/9.318" kimseye bir şey anlatmaz. Ayrıca
iki kaynak farklı sayfalanır (uzak taraf ofsetli, yerel iz tek liste) —
sayfa numarasıyla gezinmek kayıt sayıları değiştiğinde satır tekrarlatır.
İmleç her iki kaynaktaki konumu **ve** bir `after` sınırını taşır; `after`,
sayfa çekilirken araya giren yeni kaydın önceki sayfayı tekrar ettirmesini
engeller. Elenen satır sayfaya girmez ama **ofseti ilerletir**.

Uzak uç sayfa boyunu 50'de kırpıyor (`store_api/backend/paging.py`,
`MAX_PER_PAGE`); 100'lük sayfa bu yüzden **iki uzak istekle** dolar.

## Ne yapmaz — ve neden

- **Kayıt düzenlemez, silmez.** Değiştirilebilen denetim kaydı denetim kaydı
  değildir. `store_udit_logs.manage` izni bilerek **hiçbir uca bağlı değildir**.
- **Tarih aralığı olmadan sorgu atmaz.** Açık uçlu denetim sorgusu mağazayı
  dakikalarca meşgul eder ve ekranı da doldurmaz. Aralık yoksa mağazaya hiç
  gidilmez; ekran kuralı söyler.
- **Denetim kaydının kopyasını tutmaz.** Kopya, mağaza tarafındaki bir
  düzeltmeden sonra sessizce yanlış geçmiş gösterir.
- **Süzülmemiş listeyi süzülmüş gibi göstermez.** Mağaza tanımadığı sorgu
  parametresini sessizce yok sayabilir; backend bunu yakalar, sayfa içinde
  süzer ve ekranda "sayfa içinde süzüldü, sayılar yanıltıcı olabilir" der.
- **Sır göstermez.** `token · password · secret · iban · card…` içeren alanın
  **değeri** maskelenir; alanın **varlığı** gizlenmez ("belirteç değişti"
  bilgisi denetimdir, belirtecin kendisi değil).

## `store.audit.for` — 19 panelin "İşlem geçmişi" sekmesi

Panel tarafı (`ui/panel/index.js` → `capabilities()`):

```js
const audit = ctx.capability('store.audit.for');   // yoksa null → sekme gizlenir
const geçmiş = await audit('order', 12, { limit: 50, days: 30 });
// → { ok, connected, error, items: [...], hasMore, range, readOnly, screen }
```

Backend tarafı (`ctx.capability('store.audit.for')`):

```python
provider = ctx.capability("store.audit.for")
payload = await provider.for_record("order", 12, limit=50, days=30)
```

**Varlık adı ZORUNLUDUR.** Boş ad geçilirse yetenek `{ok: false}` döner ve
mağazaya hiç gidilmez: bu yüzey "bir kaydın geçmişi"dir, adsız çağrı çağıranın
çekmecesine mağazanın tamamını dökerdi.

**İmza bilerek dardır:** varlık adı + kayıt numarası + kaç satır + kaç gün.
Süzgeç, sayfalama ve imleç bu yüzeyde yoktur. Daha fazlası gerekiyorsa panel
`ctx.open('store_udit_logs', {entity, entityId})` ile bu ekrana geçer ve iş
burada devam eder. Yayınlanmış imza geriye dönük uyumsuz değiştirilmez; yeni
davranış `options` içinde yeni bir alan olarak gelir. Yetenek de **açık uçlu
sorgu atmaz**: `days` verilmezse aralık tavanı uygulanır.

`items` satırları ekranın satırlarıyla **aynı biçimdedir** (`at · user ·
actionLabel · entityLabel · summary · reason · resultLabel · diff · …`), böylece
çağıran panel kendi biçimlendirmesini yazmak zorunda kalmaz.

## Uçlar

`/api/store_udit_logs` öneki altında. Hepsi `requires(...)` taşır (K9).

Okuma (`store_udit_logs.view`): `GET /entries` · `GET /entry?key=` ·
`GET /history` · `GET /reference` · `GET /exports` · `GET /printer`

Döküm (`store_udit_logs.export`): `POST /preview` · `POST /print` ·
`POST /export`

`GET /entry` anahtarı `r:<id>` (mağaza) ya da `g:<istek kimliği>` (geçit)
biçimindedir — sayısal kimlik tek başına yetmez, iki kaynakta da aynı numara
vardır.

## Mağaza tarafı sözleşmesi (`GET /api/admin/bbd/audit`)

Geçit süzgeçleri şu adlarla iletir: `from` · `to` (ISO tarih-saat) · `q` ·
`user` · `action` · `entity` · `entity_id` · `ip` · `result` · `destructive`
· `page` · `per_page`. Uç bunları yok sayarsa **ekran çökmez**: aynı süzgeç
sayfa içinde uygulanır ve kullanıcı uyarılır.

## İzinler

| Anahtar | Ne açar |
|---|---|
| `store_udit_logs.view` | Ekran, süzgeçler, fark tablosu, geçmiş yeteneği |
| `store_udit_logs.export` | PDF/CSV dökümü ve yazdırma (iz bırakır) |
| `store_udit_logs.manage` | **Hiçbir şey.** Sözleşme gereği ilan edilir; ekran salt okunurdur |

Kaydı okumak ile kaydı binlerce satır hâlinde binadan çıkarmak aynı şey
değildir: döküm personel adı ve IP taşır, ayrı anahtar ister ve
`mod_store_udit_logs_exports` tablosuna iz düşer.

## Yerel tablo

`mod_store_udit_logs_exports` — **döküm alma olayı**. Denetim kaydının
kendisi burada tutulmaz; o iki kaynağın malıdır ve kopyalanmaz. Dosya bizim
tarafımızda üretildiği için mağaza bu olayı göremez, izi burada durur.

## Testler

```bash
.venv/bin/python -m pytest modules/store_udit_logs/tests -q
.venv/bin/ruff check modules/store_udit_logs
```
