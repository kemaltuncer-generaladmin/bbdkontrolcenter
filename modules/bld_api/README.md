# BLD Geçidi (`bld_api`)

BLD sunucusunun kontrol API'sine açılan **tek kapı** (K4). Ekranı, izni, HTTP
yüzeyi yoktur; yalnızca `bld.api` yeteneğini sağlar. KDS Yönetimi (`bld_kds`)
ve on iki BLD yönetim ekranı BLD verisine buradan ulaşır.

Hedef: `platform/extensions/veykemtu/bridgeapi` (TastyIgniter/Laravel).
Sözleşme iki parçalı ve ikisi de dondurulmuş:

| Yüzey | Uçlar | Sözleşme |
|---|---|---|
| Mutfak kasaları | `/api/control/kds/*` | K-21 §1-§4 |
| Panel alanları (13) | `/api/control/<alan>/*` | `BLD/docs/control/` |

Canlı adres **depoda yazmaz**: `config/default.yaml` içinde `base_url` boştur
ve gerçek değer `config/local.yaml` ile verilir. Adres boşken geçit ilk istekte
`config_missing` hatası döner — sessizce başka bir yere gitmez.

## Kullanımı

```yaml
# çağıran modülün module.yaml dosyasında
depends: [bld_api]
consumes:
  - capability: bld.api
    reason: "BLD verisi tek kapıdan geçer (K4)."
```

```python
api = ctx.capability("bld.api")

takvim = await api.menu_calendar(date_from="2026-08-17", date_to="2026-08-23")
sonuc  = await api.publish_menu_day("2026-08-17", reason=gerekce,
                                    actor=user.full_name, dry_run=False)
```

> ### HER YAZMA AÇIK `dry_run=` GEÇİRİR — geçidin varsayılanına GÜVENİLMEZ
>
> `config/default.yaml` içinde `dry_run_default: false` durur, ama
> `config/local.yaml` **git dışıdır** ve o dosyada bugün `true` yazıyor. Bayrağı
> atlayan bir modül, hiçbir şey yazmadan `{"ok": true}` alır ve ekran
> "kaydedildi" der. On iki panel modülünün tamamı için kural tektir:
> **`dry_run=` her çağrıda açıkça verilir.** Prova isteniyorsa `True`,
> uygulanacaksa `False`; varsayılana bırakılmaz.

## Neden tek modül, neden alan başına geçit değil

Sunucu hız sınırları IP başınadır ve Kontrol Merkezi tek IP'den çıkar:

| Sınır | Değer | Kimin |
|---|---|---|
| `bld-control` | 1200/saat/IP | `/api/control/kds/*` |
| `bld-control-panel` | 3000/saat/IP | 13 panel alanı |

Geçit alan başına bölünseydi her parça kendi 18/dk kovasını taşır, her biri
kendini uyumlu sanar ve toplam sunucu tavanını katlayarak aşardı — üstelik
geçit 429'u yalnız **bir kez** yeniden deniyor. Paylaşılan bir sunucu bütçesi
ancak paylaşılan bir istemci kovasıyla onurlandırılır.

Yoklama baskısı kovayı büyüterek değil, **referans veriyi önbelleğe alarak**
karşılanır (aşağıya bakın).

## İmza — sözleşme §1

Her istek üç başlık taşır; sunucu tarafındaki doğrulayıcı
`Veykemtu\BridgeApi\Http\Middleware\VerifyControlSignature`:

```
X-Control-Timestamp: <unix saniye>
X-Control-Nonce:     <16-128 karakter, rastgele>
X-Control-Signature: sha256=<64 hex>

kanonik = METOT \n YOL \n ZAMAN \n NONCE \n sha256_hex(ham gövde)
imza    = "sha256=" + hmac_sha256(kanonik, sır)
```

- **Yol sorgu dizesi HARİÇ** imzalanır; süzgeçler isteğe girer, imzaya girmez.
  (Müşteri okumalarındaki zorunlu `actor` da bu yüzden imzaya bağlı değildir —
  sınır bilinçli ve `00-genel.md` §9'da yazılı.)
- **Gövde ham bayt olarak imzalanır ve aynen gönderilir.** `json=` kullanılsaydı
  httpx gövdeyi yeniden serileştirir ve en küçük fark (ayraç boşluğu, `\uXXXX`
  kaçışı, anahtar sırası) sunucuda başka bir özet üretirdi. Hata da "gövde
  bozuk" demez, "imza doğrulanamadı" der — sahada teşhis edilemez.
  **Görsel yüklemenin base64 olmasının sebebi budur** (`upload.py`).
- **Her deneme yeniden imzalanır.** Nonce sunucuda 600 sn hatırlanıyor.
- Pencere ±300 sn. 401 alındığında geçit üç olası sebebi birden söyler.
- Sır **kasadadır**: `server.bld.control_secret`. Sır yoksa istek **hiç
  gönderilmez**.

## Bilmen gereken altı kural

1. **Acil fren** — `read_only` **varsayılan olarak açıktır**. Açıkken GET dışı
   her istek geçitte reddedilir (`code == "read_only"`), uzağa hiç gitmez.
   Deneme yine de `mod_bld_api_audit` tablosuna işlenir.
2. **Kuru prova ve KAYIT DEFTERİ** — yazma metotlarının `dry_run` varsayılanı
   `False`. Sözleşmedeki yazma uçları bayrağı anlar: yazma yapılmaz, sunucu
   denetim satırını `result="dry_run"` ile yine de yazar ve `would` döner —
   yani istek gerçekten gider. Sözleşmede **olmayan** bir yola kuru prova ile
   yazılmak istenirse istek **hiç gönderilmez** ve `{"ok": true, "dry_run":
   true, "sent": false}` döner; Laravel tanımadığı alanı yok saydığı için o
   bayrak sessizce düşer ve "prova" gerçek yazmaya dönüşürdü.
   Yolların defteri `client.py` içinde her alanın metot bloğunun başındaki
   `register_dry_run(...)` çağrılarıdır; kapsamı bir test kanıtlıyor
   (`test_bld_api_dry_run_registry.py`).
3. **Aktör her yazmada, gerekçe UÇ BAŞINA** — her yazma metodu `reason` ve
   `actor` alır; ikisi de **gövdeye** konur (sözleşme §3). `actor` istisnasız
   zorunludur ve her yerde 1–120 karakterdir. `reason` ise **küresel değil**:
   kuralı `client.py` içindeki `_REASON_OPTIONAL` kayıt defteri taşır ve
   **varsayılan "gerekçe İSTER"**dir — defterde olmayan her yol ister. Muaf bir
   uçta gerekçe verilirse gövdeye konur, verilmezse **alan hiç gönderilmez**.
   Kapsamı bir test kanıtlıyor (`test_bld_api_reason_policy.py`).

   Bugün defterde **yedi satır** var; kalan on bir alan değişmedi.
   `control/menu`'den altısı — gerekçe istemeyenler: `POST days` ·
   `PATCH days/{date}` · `POST/PATCH/DELETE items` · `PUT stock`; isteyenler:
   `POST publish` · `POST unpublish` · `DELETE days/{date}` · `POST duplicate`.
   `control/orders`'tan **yalnız `POST /orders`** (elle sipariş): telefon
   siparişini kaydetmek rutin bir veri girişidir ve sunucu da bu uçta
   `reasonRequired: false` diyor. Aynı alanın revizyon, durum ve **iptal**
   uçları gerekçe **ister**. Ölçüt tek cümledir: işlem müşteriye **görünür hâle
   geliyor** mu ve **geri alınması zor** mu. Defter fiili de tutar —
   `PATCH days/{date}` muaf, `DELETE` değil.

   Sınırlar **değişmedi**; yalnız nerede sorulduğu değişti:

   | Nerede | `reason` | Kaynak |
   |---|---|---|
   | Sipariş revizyon/durum/iptal (KDS ve panel) | 10–160 | `veykemtu_order_revisions.reason` sütunu |
   | Diğer panel uçları | 10–500 | `00-genel.md` §3 |
   | KDS cihaz/komut uçları | en az 10, üst sınır YOK | K-21 bir sınır söylemiyor; uydurulmaz |
   | Muaf uçlar (defterdekiler) | alt sınır YOK, üst sınır 500 | isteğe bağlı notu kısa diye reddetmek yazmayı durdururdu |

   `require_reason: false` ayarı bu defterin **üstündeki** küresel şalterdir ve
   kapsamı değişmedi: kapatıldığında hiçbir uçta gerekçe aranmaz.
4. **Hata biçimi** — her şey `BldApiError`: `.message` (Türkçe, maskelenmiş) ·
   `.status` · `.code` (`config_missing` · `read_only` · `reason_required` ·
   `actor_required` · `payload` · `unauthorized` · `forbidden` · `not_found` ·
   `control_endpoint_missing` · `validation` · `conflict` · `rate_limited` ·
   `transport` · `server` · `http`). Servis katmanı bunu yakalar ve ekran
   ayakta kalır (K7).
5. **Yineleme ve hız** — GET üç kez denenir, **yazma yinelenmez** (sözleşmede
   idempotency anahtarı taşıyan başlık yok). 429 istisnadır: `Retry-After`
   kadar beklenip bir kez yinelenir. Hız kovası **tek**tir ve dakikada 18
   istekte durur.
6. **Okuma önbelleği** — yalnızca **referans** veri. Ayrıntı aşağıda.

`not_found` ile `control_endpoint_missing` **ayrı** şeylerdir: ilki "uç var,
kayıt yok", ikincisi "uç sunucuya henüz dağıtılmamış, bekle".

## Önbellek — neyin alındığı ve neyin ALINMADIĞI

| Metot | Anahtar | Düşüren yazmalar |
|---|---|---|
| `categories()` | `category:list` | `create_category`, `update_category` |
| `product_picker()` | `product:picker:*` | bütün ürün yazmaları |
| `settings_reference()` | `settings:reference:*` | bütün ayar yazmaları |
| `audit_actions()` | `audit:actions` | — (salt okunur alan) |

**Bunların dışında hiçbir şey önbelleğe alınmaz.** Sipariş, stok sayısı,
müşteri, abonelik, fatura, SMS şablonu, izleme olayı ve gösterge paneli
**her çağrıda sunucuya gider**: personel "kaydettim ama listede yok"
yaşamamalı ve bayat bir satış şalteri, kazandığı istekten çok daha pahalıya
patlar. Bu olumsuz iddia testle sabitlenmiştir
(`test_bld_api_cache.py::test_canli_veri_onbellege_alinmaz`).

`reference_snapshot()` dört referans listesini tek çağrıda toplar ve SQLite'a
yazar (L2). Süreç yeniden başladığında ekran BLD'ye hiç gitmeden dolar; BLD
erişilemezken **son bilinen hâl** `stale: true` ve `errors` ile döner (K7).
Hiç görüntü yoksa hata yukarı gider: bayat veri iyi, uydurma veri değil.

## Yöntem yüzeyi — DONMUŞ TABLO

Panel modülleri bu tabloyu okur. Yazma metotlarında `actor` **zorunlu anahtar
argümandır**; `dry_run` her çağrıda açıkça verilir. `reason` da her yazma
metodunda **durur**, ama Gerekçe sütunu `—` olan satırlarda varsayılanı `""`
ve verilmezse gövdeye hiç konmaz (bkz. §3).

Yol sütunundaki önek gösterilmez: KDS satırlarında `/api/control/kds`, panel
satırlarında `/api/control` + alan adı.

Dönüş şekilleri:

| Kısaltma | Şekil |
|---|---|
| `dict` | Düz sözlük (`data` zarfı açılmış) |
| `liste` | `list[dict]` — **yalnız KDS metotları** |
| `sayfa` | `{"items": [...], "meta": {...}}` |
| `tarama` | `{"items": [...], "total": int, "pages": int, "truncated": bool}` |
| `belge` | `{"content": bayt, "text": str, "content_type", "filename", "bytes", "total_rows", "truncated", "status"}` |

### 0 · Geçit durumu (yerel, ağa çıkmaz)

| Metot | Fiil | Yol | Gerekçe | dry_run | Dönüş |
|---|---|---|---|---|---|
| `state()` | — | — | — | — | `dict` |
| `await audit_trail(*, limit=100)` | — | — | — | — | `liste` |
| `forget_reference(prefix="")` | — | — | — | — | `None` |
| `await health()` | GET | `/overview` | — | — | `dict` |

### 1 · KDS — mutfak kasaları (K-21, `bld_kds` kullanıyor)

| Metot | Fiil | Yol | Gerekçe | dry_run | Dönüş |
|---|---|---|---|---|---|
| `overview()` | GET | `/overview` | — | — | `dict` |
| `devices()` | GET | `/devices` | — | — | `liste` |
| `create_device(*, name)` | POST | `/devices` | ✔ | ✔ | `dict` |
| `rename_device(device_id, *, name)` | PATCH | `/devices/{id}` | ✔ | ✔ | `dict` |
| `new_pairing_code(device_id)` | POST | `/devices/{id}/pairing-code` | ✔ | ✔ | `dict` |
| `revoke_device(device_id)` | POST | `/devices/{id}/revoke` | ✔ | ✔ | `dict` |
| `update_device_settings(device_id, *, settings)` | PATCH | `/devices/{id}/settings` | ✔ | ✔ | `dict` |
| `device_commands(device_id)` | GET | `/devices/{id}/commands` | — | — | `liste` |
| `send_command(device_id, *, command, payload=None)` | POST | `/devices/{id}/commands` | ✔ | ✔ | `dict` |
| `print_jobs(*, device_id, order_id, limit)` | GET | `/print-jobs` | — | — | `liste` |
| `orders(*, include_completed=False, since="")` | GET | `/orders` | — | — | `liste` |
| `order(order_id)` | GET | `/orders/{id}` | — | — | `dict` |
| `order_revisions(order_id)` | GET | `/orders/{id}/revisions` | — | — | `liste` |
| `create_order_revision(order_id, *, items, note, requested_at, customer_note)` | POST | `/orders/{id}/revisions` | ✔ 160 | ✔ | `dict` |
| `set_order_status(order_id, *, status)` | POST | `/orders/{id}/status` | ✔ 160 | ✔ | `dict` |

### 2 · `menu` — günlük menü takvimi

| Metot | Fiil | Yol | Gerekçe | dry_run | Dönüş |
|---|---|---|---|---|---|
| `menu_calendar(*, date_from, date_to, location_id=None)` | GET | `/menu/calendar` | — | — | `sayfa` |
| `menu_day(date, *, location_id=None)` | GET | `/menu/days/{date}` | — | — | `dict` |
| `create_menu_day(*, date, title, description, internal_note, package_price_kurus, components_sellable, cutoff_time, capacity_total, items, location_id)` | POST | `/menu/days` | — | ✔ | `dict` |
| `update_menu_day(date, *, title, description, internal_note, package_price_kurus, components_sellable, cutoff_time, capacity_total, image_path, location_id)` | PATCH | `/menu/days/{date}` | — | ✔ | `dict` |
| `delete_menu_day(date, *, location_id=None)` | DELETE | `/menu/days/{date}` | ✔ 500 | ✔ | `dict` |
| `publish_menu_day(date, *, location_id=None)` | POST | `/menu/days/{date}/publish` | ✔ 500 | ✔ | `dict` |
| `unpublish_menu_day(date, *, location_id=None)` | POST | `/menu/days/{date}/unpublish` | ✔ 500 | ✔ | `dict` |
| `create_menu_item(date, *, menu_id, quantity, sort_order, label, price_override_kurus, is_required, sellable_alone, capacity, location_id)` | POST | `/menu/days/{date}/items` | — | ✔ | `dict` |
| `update_menu_item(date, item_id, *, quantity, sort_order, label, price_override_kurus, is_required, sellable_alone, capacity)` | PATCH | `/menu/days/{date}/items/{item}` | — | ✔ | `dict` |
| `delete_menu_item(date, item_id)` | DELETE | `/menu/days/{date}/items/{item}` | — | ✔ | `dict` |
| `menu_stock(date, *, location_id=None)` | GET | `/menu/days/{date}/stock` | — | — | `dict` |
| `set_menu_stock(date, *, capacity_total, items, location_id)` | PUT | `/menu/days/{date}/stock` | — | ✔ | `dict` |
| `duplicate_menu_day(date, *, target_date, overwrite=False, location_id)` | POST | `/menu/days/{date}/duplicate` | ✔ 500 | ✔ | `dict` |

### 3 · `products` — ürün kataloğu

| Metot | Fiil | Yol | Gerekçe | dry_run | Dönüş |
|---|---|---|---|---|---|
| `products(*, q, category_id, status, sold_out, sort, direction, page, per_page)` | GET | `/products` | — | — | `sayfa` |
| `product(menu_id)` | GET | `/products/{menu}` | — | — | `dict` |
| `product_picker(*, only_active=True)` | GET | `/products` (tüm sayfalar) | — | — | `tarama` **önbellekli** |
| `create_product(*, name, price_kurus, description, minimum_qty, priority, status, category_ids)` | POST | `/products` | ✔ 500 | ✔ | `dict` |
| `update_product(menu_id, *, name, description, price_kurus, minimum_qty, priority, status, category_ids)` | PATCH | `/products/{menu}` | ✔ 500 | ✔ | `dict` |
| `delete_product(menu_id)` | DELETE | `/products/{menu}` | ✔ 500 | ✔ | `dict` |
| `set_product_image(menu_id, *, content, filename)` | PUT | `/products/{menu}/image` | ✔ 500 | ✔ | `dict` (+`upload` künyesi) |
| `delete_product_image(menu_id)` | DELETE | `/products/{menu}/image` | ✔ 500 | ✔ | `dict` |
| `mark_product_sold_out(menu_id, *, note=None)` | POST | `/products/{menu}/sold-out` | ✔ 500 | ✔ | `dict` |
| `clear_product_sold_out(menu_id)` | DELETE | `/products/{menu}/sold-out` | ✔ 500 | ✔ | `dict` |
| `categories()` | GET | `/products/categories` | — | — | `sayfa` **önbellekli** |
| `create_category(*, name, description, parent_id, priority, status)` | POST | `/products/categories` | ✔ 500 | ✔ | `dict` |
| `update_category(category_id, *, name, description, parent_id, priority, status)` | PATCH | `/products/categories/{id}` | ✔ 500 | ✔ | `dict` |

### 4 · `settings` — satış ayarları

| Metot | Fiil | Yol | Gerekçe | dry_run | Dönüş |
|---|---|---|---|---|---|
| `sales_settings(*, location_id=None)` | GET | `/settings/sales` | — | — | `dict` |
| `settings_reference(*, location_id=None)` | GET | `/settings/sales` (`meta`) | — | — | `dict` **önbellekli** |
| `update_sales_settings(*, order_cutoff, max_lookahead_days, subscription_release_time, min_order_total_kurus, delivery_fee_kurus, payment_methods, busy, busy_message, prep_minutes, delivery_minutes, busy_extra_minutes, daily_menu_enabled, auto_invoice, location_id)` | PUT | `/settings/sales` | ✔ 500 | ✔ | `dict` |
| `pause_ordering(*, until=None, customer_message=None, location_id)` | POST | `/settings/ordering/pause` | ✔ 500 | ✔ | `dict` |
| `resume_ordering(*, location_id=None)` | POST | `/settings/ordering/resume` | ✔ 500 | ✔ | `dict` |
| `closed_days(*, date_from="", date_to="")` | GET | `/settings/closed-days` | — | — | `sayfa` |
| `create_closed_day(*, date, description=None)` | POST | `/settings/closed-days` | ✔ 500 | ✔ | `dict` |
| `delete_closed_day(date)` | DELETE | `/settings/closed-days/{date}` | ✔ 500 | ✔ | `dict` |

### 5 · `orders` — siparişler (panel yolu)

`create_order` **elle (telefonla alınan) siparişin tek yoludur** ve tek başına
`—` gerekçe sütunu taşır: kayıt akışıdır, onay akışı değil. Ekranın bilmesi
gereken üç davranış: sipariş **`onaylandi` doğar** (bugüne açılansa anında
KDS'e düşer, ileri tarihliyse o günün kesim anında); müşteri **iki kipten
biriyle** verilir (`customer_id` **ya da** `customer={"name", "phone"}`,
ikisi birden değil) ve yanıttaki `customer.created` yeni kayıt açılıp
açılmadığını söyler; kuru prova **kalemi, fiyatı ve stoğu denetlemez** — "gövde
doğru mu" sorusunun cevabıdır, "sipariş geçecek mi" sorusunun değil.

`agreed_total_kurus` **isteğe bağlıdır ve SEPETİN TAMAMI içindir**, kalem
başına değil. Dolu gönderilirse sunucu kalem toplamı yerine bu tutarı yazar ve
kalemleri fiyatsız bırakır (aboneliğin `agreed_unit_price_kurus` deseninin sepet
düzeyine taşınmış hâli). **Teslimat ücreti bu tutara eklenmez, dâhildir.** Alan
gönderilmezse (`None`) davranış bugünkünün aynısıdır: tutar katalogdan
hesaplanır ve adrese teslimde ücret eklenir. **Sıfır gönderilemez** — sıfır bir
fiyat kararı değil, boş bırakılmış bir kutudur; tavan `MAX_AGREED_TOTAL_KURUS`
(1.000.000 ₺) ve bir iş kuralı değil, fazladan basılmış sıfırlara karşı akıl
sınırıdır. Yetki denetimi ekranın tarafındadır
(`bld_manual_order.price_override`); geçit yalnız aralığa bakar.

| Metot | Fiil | Yol | Gerekçe | dry_run | Dönüş |
|---|---|---|---|---|---|
| `order_list(*, service_date, date_from, date_to, status, delivery_type, customer_id, subscription_id, source, q, page, per_page)` | GET | `/orders` | — | — | `sayfa` |
| `create_order(*, service_date, delivery_type, payment_method, items, customer_id, customer, address, customer_note, location_id, agreed_total_kurus)` | POST | `/orders` | — | ✔ | `dict` (+`customer`, `warnings`) |
| `order_detail(order_id)` | GET | `/orders/{order}` | — | — | `dict` |
| `order_revision_history(order_id)` | GET | `/orders/{order}/revisions` | — | — | `sayfa` |
| `revise_order(order_id, *, items, note, requested_at, customer_note)` | POST | `/orders/{order}/revisions` | ✔ 160 | ✔ | `dict` |
| `change_order_status(order_id, *, status)` | POST | `/orders/{order}/status` | ✔ 160 | ✔ | `dict` |
| `cancel_order(order_id, *, refund=True, notify_customer=True)` | POST | `/orders/{order}/cancel` | ✔ 160 | ✔ | `dict` |
| `export_orders(*, süzgeçler, max_rows)` | GET | `/orders/export` | — | — | `belge` |
| `order_invoice(order_id)` | GET | `/orders/{order}/invoice` | — | — | `dict` |

### 6 · `subscriptions` — abonelik, talep, sözleşme, ödeme

| Metot | Fiil | Yol | Gerekçe | dry_run | Dönüş |
|---|---|---|---|---|---|
| `subscriptions(*, status, customer_id, q, service_day, active_on, page, per_page)` | GET | `/subscriptions` | — | — | `sayfa` |
| `subscription(subscription_id)` | GET | `/subscriptions/{id}` | — | — | `dict` |
| `create_subscription(*, customer_id, start_date, service_days, default_quantity, delivery_type, menu_mode, payment_mode, end_date, delivery_time_from, delivery_time_to, agreed_unit_price_kurus, lines, delivery_points, location_id)` | POST | `/subscriptions` | ✔ 500 | ✔ | `dict` |
| `update_subscription(subscription_id, *, end_date, delivery_type, delivery_time_from, delivery_time_to, service_days, menu_mode, default_quantity, agreed_unit_price_kurus, lines, delivery_points)` | PATCH | `/subscriptions/{id}` | ✔ 500 | ✔ | `dict` |
| `activate_subscription(subscription_id)` | POST | `/subscriptions/{id}/activate` | ✔ 500 | ✔ | `dict` |
| `pause_subscription(subscription_id, *, start_date, end_date, pause_reason)` | POST | `/subscriptions/{id}/pause` | ✔ 500 | ✔ | `dict` |
| `resume_subscription(subscription_id)` | POST | `/subscriptions/{id}/resume` | ✔ 500 | ✔ | `dict` |
| `cancel_subscription(subscription_id, *, effective_date)` | POST | `/subscriptions/{id}/cancel` | ✔ 500 | ✔ | `dict` |
| `subscription_calendar(subscription_id, *, date_from, days)` | GET | `/subscriptions/{id}/calendar` | — | — | `sayfa` |
| `create_subscription_exception(subscription_id, *, service_date, skip, quantity_override, note)` | POST | `/subscriptions/{id}/exceptions` | ✔ 500 | ✔ | `dict` |
| `delete_subscription_exception(subscription_id, service_date)` | DELETE | `/subscriptions/{id}/exceptions/{date}` | ✔ 500 | ✔ | `dict` |
| `subscription_runs(subscription_id, *, date_from, date_to, page, per_page)` | GET | `/subscriptions/{id}/runs` | — | — | `sayfa` |
| `generate_subscription_orders(subscription_id, *, service_date, release_now=False)` | POST | `/subscriptions/{id}/generate` | ✔ 500 | ✔ | `dict` |
| `release_subscription_order(order_id)` | POST | `/subscriptions/orders/{order}/release` | ✔ 500 | ✔ | `dict` |
| `quote_requests(*, status, q, date_from, date_to, page, per_page)` | GET | `/subscriptions/requests` | — | — | `sayfa` |
| `quote_request(request_id)` | GET | `/subscriptions/requests/{id}` | — | — | `dict` |
| `update_quote_request(request_id, *, status, admin_note)` | PATCH | `/subscriptions/requests/{id}` | ✔ 500 | ✔ | `dict` |
| `convert_quote_request(request_id, *, customer_id, subscription)` | POST | `/subscriptions/requests/{id}/convert` | ✔ 500 | ✔ | `dict` |
| `subscription_contracts(subscription_id)` | GET | `/subscriptions/{id}/contracts` | — | — | `sayfa` |
| `create_subscription_contract(subscription_id, *, phone, expires_in_days=7, send_sms=True)` | POST | `/subscriptions/{id}/contracts` | ✔ 500 | ✔ | `dict` |
| `subscription_contract(contract_id)` | GET | `/subscriptions/contracts/{c}` | — | — | `dict` |
| `resend_subscription_contract(contract_id, *, expires_in_days)` | POST | `/subscriptions/contracts/{c}/resend` | ✔ 500 | ✔ | `dict` |
| `cancel_subscription_contract(contract_id)` | POST | `/subscriptions/contracts/{c}/cancel` | ✔ 500 | ✔ | `dict` |
| `subscription_payments(subscription_id, *, status, date_from, date_to)` | GET | `/subscriptions/{id}/payments` | — | — | `sayfa` |
| `create_subscription_payment(subscription_id, *, period_start, period_end, due_date, amount_kurus=None, note)` | POST | `/subscriptions/{id}/payments` | ✔ 500 | ✔ | `dict` |
| `mark_subscription_payment_paid(payment_id, *, method, paid_at, reference, create_invoice=False)` | POST | `/subscriptions/payments/{p}/mark-paid` | ✔ 500 | ✔ | `dict` |

### 7 · `customers` — müşteriler (**KVKK: okumalar da denetlenir**)

Her `GET` **`actor` anahtar argümanı** ister (1–120 karakter) ve sorgu
dizesine koyar. **Bu ekranlarda otomatik yenileme KURULMAZ.**

| Metot | Fiil | Yol | Gerekçe | dry_run | Dönüş |
|---|---|---|---|---|---|
| `customers(*, actor, q, status, has_subscription, sort, direction, page, per_page)` | GET | `/customers` | — | — | `sayfa` |
| `customer(customer_id, *, actor)` | GET | `/customers/{id}` | — | — | `dict` |
| `update_customer(customer_id, *, first_name, last_name, telephone, org_name, tax_office, tax_no, contact_person, org_phone)` | PATCH | `/customers/{id}` | ✔ 500 | ✔ | `dict` |
| `customer_orders(customer_id, *, actor, status, date_from, date_to, page, per_page)` | GET | `/customers/{id}/orders` | — | — | `sayfa` |
| `customer_subscriptions(customer_id, *, actor)` | GET | `/customers/{id}/subscriptions` | — | — | `sayfa` |
| `customer_addresses(customer_id, *, actor)` | GET | `/customers/{id}/addresses` | — | — | `sayfa` |
| `disable_customer(customer_id)` | POST | `/customers/{id}/disable` | ✔ 500 | ✔ | `dict` |
| `enable_customer(customer_id)` | POST | `/customers/{id}/enable` | ✔ 500 | ✔ | `dict` |

### 8 · `invoices` — fatura belgesi (mali değeri yoktur)

| Metot | Fiil | Yol | Gerekçe | dry_run | Dönüş |
|---|---|---|---|---|---|
| `invoices(*, customer_id, subscription_id, order_id, status, date_from, date_to, q, page, per_page)` | GET | `/invoices` | — | — | `sayfa` |
| `invoice(invoice_id)` | GET | `/invoices/{id}` | — | — | `dict` |
| `invoice_html(invoice_id)` | GET | `/invoices/{id}/html` | — | — | `belge` |
| `create_invoice(*, order_id \| subscription_id + period_start + period_end, subscription_payment_id)` | POST | `/invoices` | ✔ 500 | ✔ | `dict` |
| `void_invoice(invoice_id)` | POST | `/invoices/{id}/void` | ✔ 500 | ✔ | `dict` |

### 9 · `cms` — site içeriği

| Metot | Fiil | Yol | Gerekçe | dry_run | Dönüş |
|---|---|---|---|---|---|
| `site_content()` | GET | `/cms/content` | — | — | `dict` |
| `set_site_content(key, *, value, revalidate=True)` | PUT | `/cms/content/{key}` | ✔ 500 | ✔ | `dict` |
| `site_services(*, published="")` | GET | `/cms/services` | — | — | `sayfa` |
| `create_site_service(*, slug, title, fields, revalidate=True)` | POST | `/cms/services` | ✔ 500 | ✔ | `dict` |
| `update_site_service(service_id, *, fields, revalidate=True)` | PATCH | `/cms/services/{id}` | ✔ 500 | ✔ | `dict` |
| `delete_site_service(service_id, *, revalidate=True)` | DELETE | `/cms/services/{id}` | ✔ 500 | ✔ | `dict` |
| `site_posts(*, q, category, published, page, per_page)` | GET | `/cms/posts` | — | — | `sayfa` |
| `create_site_post(*, slug, title, body_html, fields, revalidate=True)` | POST | `/cms/posts` | ✔ 500 | ✔ | `dict` |
| `update_site_post(post_id, *, fields, revalidate=True)` | PATCH | `/cms/posts/{id}` | ✔ 500 | ✔ | `dict` |
| `delete_site_post(post_id, *, revalidate=True)` | DELETE | `/cms/posts/{id}` | ✔ 500 | ✔ | `dict` |
| `revalidate_site(*, paths=None)` | POST | `/cms/revalidate` | ✔ 500 | ✔ | `dict` |

`fields` sözlüğü sözleşmedeki kalan alanları **olduğu gibi** taşır; geçit
onları ayıklamaz — bilinen alanları seçen bir dönüşüm, sözleşmeye yeni bir
alan eklendiğinde onu sessizce düşürürdü.

### 10 · `sms` — şablon, kayıt, duyuru

| Metot | Fiil | Yol | Gerekçe | dry_run | Dönüş |
|---|---|---|---|---|---|
| `sms_templates()` | GET | `/sms/templates` | — | — | `sayfa` |
| `update_sms_template(key, *, body, enabled)` | PATCH | `/sms/templates/{key}` | ✔ 500 | ✔ | `dict` |
| `preview_sms_template(key, *, body="", sample=None)` | POST | `/sms/templates/{key}/preview` | ✔ 500 | ✔ | `dict` |
| `send_test_sms(*, phone, template_key \| body, sample)` | POST | `/sms/send-test` | ✔ 500 | ✔ | `dict` |
| `sms_log(*, phone, template_key, status, context, customer_id, date_from, date_to, page, per_page)` | GET | `/sms/log` | — | — | `sayfa` |
| `sms_announcement()` | GET | `/sms/announcement` | — | — | `dict` |
| `set_sms_announcement(*, body, audience)` | PUT | `/sms/announcement` | ✔ 500 | ✔ | `dict` |
| `run_sms_announcement(*, confirm_recipients)` | POST | `/sms/announcement/run` | ✔ 500 | ✔ | `dict` |

### 11 · `notifications` — uygulama-içi duyuru

| Metot | Fiil | Yol | Gerekçe | dry_run | Dönüş |
|---|---|---|---|---|---|
| `notifications(*, status, audience, level, live, q, page, per_page)` | GET | `/notifications` | — | — | `sayfa` |
| `create_notification(*, title, body, level, audience, starts_at, ends_at, action_label, action_url, dismissible)` | POST | `/notifications` | ✔ 500 | ✔ | `dict` |
| `update_notification(notification_id, *, title, body, level, audience, starts_at, ends_at, action_label, action_url, dismissible)` | PATCH | `/notifications/{id}` | ✔ 500 | ✔ | `dict` |
| `publish_notification(notification_id)` | POST | `/notifications/{id}/publish` | ✔ 500 | ✔ | `dict` |
| `archive_notification(notification_id)` | DELETE | `/notifications/{id}` | ✔ 500 | ✔ | `dict` |
| `notification_stats(notification_id)` | GET | `/notifications/{id}/stats` | — | — | `dict` |

### 12 · `monitor` — hata olayları ve cihaz sağlığı

| Metot | Fiil | Yol | Gerekçe | dry_run | Dönüş |
|---|---|---|---|---|---|
| `monitor_events(*, source, level, code, device_id, since, resolved, q, page, per_page)` | GET | `/monitor/events` | — | — | `sayfa` |
| `monitor_event(event_id)` | GET | `/monitor/events/{id}` | — | — | `dict` |
| `resolve_monitor_event(event_id, *, note="")` | POST | `/monitor/events/{id}/resolve` | ✔ 500 | ✔ | `dict` |
| `monitor_devices()` | GET | `/monitor/devices` | — | — | `sayfa` |
| `monitor_summary()` | GET | `/monitor/summary` | — | — | `dict` |

### 13 · `dashboard` — açılış özeti

| Metot | Fiil | Yol | Gerekçe | dry_run | Dönüş |
|---|---|---|---|---|---|
| `dashboard_overview(*, location_id=None, date="")` | GET | `/dashboard/overview` | — | — | `dict` |

### 14 · `audit` — denetim izi (salt okunur)

| Metot | Fiil | Yol | Gerekçe | dry_run | Dönüş |
|---|---|---|---|---|---|
| `server_audit(*, actor, action, target_type, target_id, result, date_from, date_to, q, page, per_page=50)` | GET | `/audit` | — | — | `sayfa` |
| `server_audit_entry(audit_id)` | GET | `/audit/{id}` | — | — | `dict` |
| `audit_actions()` | GET | `/audit/actions` | — | — | `sayfa` **önbellekli** |

Bu alanda yazma ucu **yoktur ve olmayacaktır**. `server_audit()` sunucunun
izini okur; `audit_trail()` geçidin **yerel** izini okur ve sunucuya hiç
gitmez. İkisi ayrı sorulara cevap verir.

### 15 · Referans görüntüsü

| Metot | Fiil | Yol | Gerekçe | dry_run | Dönüş |
|---|---|---|---|---|---|
| `reference_snapshot(*, refresh=False)` | GET | dört referans ucu | — | — | `dict` |

Yanıt alanları **snake_case**'tir ve dönüştürülmez (sözleşme §2). `store_api`
camelCase döndürür; iki geçidin biçimini birbirine benzetmek sözleşmeyle ekran
arasına sessiz bir çeviri katmanı sokardı.

## Geçidin istek göndermeden kestiği durumlar

Hepsinin sebebi tek: **Laravel tanımadığı alanı sessizce yok sayar.**
"Kaydedildi" diyen bir ekranın arkasında hiçbir yere yazılmamış bir değer
bırakmak, açık bir hatadan çok daha pahalıdır.

- **Kuru prova defterinde olmayan yol.** İstek gönderilmez, sentetik yanıt döner.
- **Tanınmayan ayar anahtarı** (KDS, 24 yönetilen ayar) ve **tanınmayan komut**.
- **Boş revizyon listesi.** `items` kalem farkı değil **tam listedir**.
- **Boş kısmi yazma.** Yalnız `reason`/`actor` taşıyan bir `PATCH`, hiçbir şey
  değiştirmeden denetim izine satır yazardı.
- **`item_id` taşımayan stok satırı.** Liste tam listedir; eksik satır o
  kalemin tavanını sessizce kaldırırdı.
- **Bozuk tarih** (`YYYY-MM-DD` değil). Yol kuru prova defterine bu kalıpla
  kayıtlı; bozuk bir tarih "uç sözleşmede yok" gibi görünen bir hata üretirdi.
- **`payment_mode="account"`, `method="account"` ve `payment_method="account"`.**
  Cari hesap kalktı.
- **Elle sipariş: müşteri kipinin belirsizliği** (`customer_id` ve `customer`
  birlikte ya da hiçbiri). İkisi birden gönderilseydi sunucu kimliği seçer,
  `customer` sessizce yok sayılır ve ekran yeni müşteri açtığını sanırdı.
- **Numarasız (ya da adsız) yeni müşteri.** Yer tutucu e-posta telefondan
  türüyor; numarasız iki kayıt aynı adrese düşerdi.
- **Kalemsiz sipariş** — mutfağa boş bir fiş olarak düşerdi.
- **Teslimatlı siparişte eksik adres** (`line1`/`district`/`city`).
- **`skip=True` + `quantity_override`.** "Atla ama 12 yap" tutarsız.
- **Fatura kipinin belirsizliği** (`order_id` ve `subscription_id` birlikte ya
  da hiçbiri).
- **Deneme SMS'inde şablon + serbest metnin birlikte verilmesi.**
- **Kapatılamayan bilgilendirme duyurusu** ve **etiketsiz düğme**.
- **Görselde**: bozuk base64, sınırı aşan boyut, içerikten okunan türün
  desteklenmemesi.
- **Aktör adının 120 karakteri aşması.**
- **`actor`süz müşteri okuması** (KVKK).

## Ayar ve sır

`config/default.yaml` · şema `config/schema.json`. Makineye özel değerler
`config/local.yaml` içine yazılır:

```yaml
modules:
  bld_api:
    base_url: "https://api.benimlezzetdunyam.com.tr"
    read_only: false          # yazma açılacaksa — varsayılan güvenli taraftadır
    dry_run_default: false    # çağrılar yine de açıkça dry_run= geçirir

secrets:
  server.bld.control_secret: "<sunucudaki BLD_CONTROL_SECRET ile aynı>"
```

Ayar anahtarları: `base_url` · `timeout_seconds` · `read_only` ·
`dry_run_default` · `require_reason` · `requests_per_minute` · `page_size` ·
`reference_ttl_seconds` · `snapshot_ttl_seconds` · `max_items` ·
`max_upload_mb`.

Sır depoda, ayarda, log'da ve hata metninde bulunmaz. Maskeleme iki
katmanlıdır: ad tabanlı desen ve **yüklenmiş sır değerinin kendisi**.

## Tablolar

| Tablo | İçerik |
|---|---|
| `mod_bld_api_audit` | Yazma denemeleri — istek **çıkmadan** yazılır. `result` boşsa "gönderildi mi belli değil". |
| `mod_bld_api_snapshot` | Referans verinin L2 anlık görüntüsü. BLD verisi **kopyalanmaz**; yalnız kategori/ayar/katalog/sözlük durur. |

Görsel yükleme gövdesi denetim izine **künye olarak** yazılır
(`{filename, mime, bytes}`); base64 içerik hiçbir yere yazılmaz (§8.2).

## Sözleşmede eksik görülenler

Bunlar uydurulmadı; olduğu gibi bildirilir:

1. **Ayar gövdesinin biçimi belirsiz** (KDS `PATCH /devices/{id}/settings`).
   Geçit ayarları `settings` nesnesine koyar; sunucu tarafı yazılırken teyit
   edilmelidir.
2. **İdempotency anahtarı yok.** `mod_bld_api_audit.request_id` yalnız yerel
   bir anahtardır ve yazma isteğinin yinelenmemesi bir tercih değil
   zorunluluktur.
3. **Ürün seçenekleri salt okunur.** Seçenek yazan uç sözleşmede tanımlanmadı;
   düzenleme TastyIgniter admin panelinde kalıyor.
4. **Uçların sunucuda yayında olması gerekiyor.** Panel alanları henüz
   dağıtılmadıysa çağrılar `control_endpoint_missing` koduyla döner ve ekran
   "sunucu eklentisi güncellenince çalışacak" diyerek ayakta kalır (K7).

## Testler

```bash
.venv/bin/python -m pytest modules/bld_api -q
.venv/bin/ruff check modules/bld_api
```

Testler ağa çıkmaz: `httpx.MockTransport` ile sahte sunucu, sahte kasa ve sahte
depo kullanılır. Üç davranış **sabitlenmiştir** ve değiştirilmesi bilinçli bir
karar gerektirir:

- kanonik imza biçimi (sabit vektör),
- kuru prova defterinin **kapsamı** — `BldApi` üzerindeki her yazma metodu
  (imzasında `reason` ve `actor` taşıyan her metot) listede sayılı olmalı ve
  kuru provada gerçekten istek göndermeli,
- önbelleğin **neyi almadığı** — sipariş, stok, müşteri, abonelik, fatura,
  şablon, izleme ve gösterge paneli her çağrıda sunucuya gider.
