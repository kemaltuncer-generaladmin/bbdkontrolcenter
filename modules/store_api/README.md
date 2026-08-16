# Mağaza Geçidi (`store_api`)

BBD Store yönetici API'sine açılan **tek kapı** (K4). Ekranı, izni, HTTP
yüzeyi yoktur; yalnızca `store.api` yeteneğini sağlar. 20 mağaza ekranı
mağaza verisine buradan ulaşır.

Hedef: `https://bbdstore.com.tr` · Bagisto 2.4.8 + `bagisto/bagisto-api`
v2.3.1 · `/api/admin/*` (286 uç) + `/api/admin/bbd/*` (canlıda 87 rota,
ölçüm 2026-08-16). Burada bir dönem "yazılmakta olan `/api/admin/bbd/*`"
yazıyordu; paket 2026-08-14'ten sonra dağıtıldı.

## Kullanımı

```yaml
# çağıran modülün module.yaml dosyasında
depends: [store_api]
consumes:
  - capability: store.api
    reason: "Mağaza verisi tek kapıdan geçer (K4)."
```

```python
api = ctx.capability("store.api")

liste = await api.orders({"status": "processing"}, page=1)       # {"items": [...], "meta": {...}}
detay = await api.order(2392)                                    # düz sözlük
sonuc = await api.cancel_order(2392, reason=gerekce, actor=user.full_name, dry_run=dry)
```

## Bilmen gereken beş kural

1. **Acil fren** — `read_only` **varsayılan olarak açıktır**. Açıkken GET
   dışı her istek geçitte reddedilir (`StoreApiError.code == "read_only"`),
   uzağa hiç gitmez. Ekran bunu anlaşılır bir mesajla göstermeli.
2. **Kuru prova** — yazma metotlarının `dry_run` varsayılanı **True**.
   Bagisto çekirdek uçlarında kuru prova **isteği hiç göndermez** ve
   `{"ok": True, "dryRun": True, "sent": False, ...}` döner; çekirdek
   `dryRun` alanını bilmediği için göndermek gerçek yazma olurdu.
   `/api/admin/bbd/*` uçlarında bayrak gövdeyle gider. Kullanıcı onayından
   sonra `dry_run=False` ile çağır.
3. **Gerekçe zorunlu** — her yazma metodu `reason` (en az 10 karakter) ve
   `actor` alır. `X-Bbd-Reason` / `X-Bbd-Actor` / `X-Bbd-Request-Id`
   başlıklarını geçit kendisi ekler; istek çıkmadan `mod_store_api_audit`
   tablosuna satır yazılır.
4. **Hata biçimi** — her şey `StoreApiError` olarak gelir:
   `.message` (Türkçe, maskelenmiş) · `.status` · `.code`
   (`config_missing` · `read_only` · `reason_required` · `unauthorized` ·
   `forbidden` · `not_found` · `bbd_endpoint_missing` · `validation` ·
   `rate_limited` · `conflict` · `transport` · `server` · `http` ·
   `payload`). Servis katmanı bunu
   yakalar ve `{"ok": False, "error": ...}` döner — ekran ayakta kalır (K7).
5. **Sayfalama** — sunucu `per_page` değerini **50**'ye kırpar. Tam liste
   için `all_pages=True`; sayfalar **sırayla** çekilir ve sonuç
   `truncated` bayrağı taşır.

## Dosya yükleme

Görsel uçları JSON değil **multipart/form-data** ister. Panel dosyayı
**base64** olarak yollar (Tauri kabuğunda fs eklentisi yok); geçit çözer,
türünü ve boyutunu doğrular, multipart parçasını kurar.

```python
await api.upload_product_image(
    12, content=base64_metin, filename="kapak.png", mime="image/png",
    position=1, reason=gerekce, actor=user.full_name, dry_run=dry)
```

- Dosya alanının adı **`image`**'dır (vendor kaynağından okundu —
  `images[]` ya da `file` değil). `position` isteğe bağlı metin alanıdır.
- Kabul edilen türler: `bmp` · `jpeg` · `jpg` · `png` · `webp`.
- İki boyut sınırı vardır, **küçüğü** uygulanır: ayar (`max_upload_mb`,
  varsayılan 24) ve ucun kendi sınırı (ürün görselinde **4 MB**). Sınırı
  aşan dosya için istek **hiç gönderilmez** (`code == "payload"`).
- Beş politika yüklemede de aynen işler: acil fren, gerekçe, kuru prova,
  hız kovası, denetim izi. Denetim izine ham bayt değil dosya özeti
  (`{filename, mime, bytes}`) yazılır.

`upload_media` ana ekran görselleri içindir; ucu (`/api/admin/bbd/home/slides`)
**henüz yayında değil**, çağrı `bbd_endpoint_missing` ile döner.

## Yöntem yüzeyi (224 metot)

Ayrıntı ve her metodun hangi uca gittiği **`backend/client.py`
docstring'lerindedir** — imzayı oradan oku, uydurma.

| Alan | Metotlar |
|---|---|
| Durum | `state` · `health` · `audit_trail` |
| Pano/rapor | `dashboard_stats` · `reporting_stats` · `reporting` · `reporting_export` |
| Sipariş | `orders` · `order` · `orders_export` · `order_comments` · `add_order_comment` · `cancel_order` · `create_invoice` · `create_shipment` · `refund_preview` · `create_refund` · `reorder` · `place_order` |
| Fatura | `invoices` · `invoice` · `invoice_pdf` · `invoices_export` · `send_invoice_copy` · `update_invoice_status` |
| İade/kargo/ödeme | `refunds` · `refund` · `refunds_export` · `shipments` · `shipment` · `transactions` · `transaction` · `record_transaction` |
| Ürün | `products` · `product` · `products_export` · `product_lookup` · `create_product` · `update_product` · `update_product_status` · `copy_product` · `product_inventories` · `update_inventory` · `add_product_image` · `upload_product_image` · `reorder_product_images` · `delete_product_image` (eski ad: `remove_product_image`) · `upload_media` · `customer_group_prices` · `save_customer_group_price` |
| Kategori | `categories` · `category_tree` · `category` · `create_category` · `update_category` · `update_category_status` |
| Öznitelik | `attributes` · `attribute` · `create_attribute` · `update_attribute` · `delete_attribute` · `attribute_options` · `create_attribute_option` · `update_attribute_option` · `delete_attribute_option` |
| Aile | `families` (eski ad: `attribute_families`) · `family` · `create_family` · `update_family` · `delete_family` |
| Müşteri | `customers` · `customer` · `create_customer` · `update_customer` · `update_customer_status` · `customer_addresses` · `save_customer_address` · `customer_notes` · `add_customer_note` · `customer_orders` · `customer_cart_items` · `customer_wishlist` · `customer_groups` · `save_customer_group` |
| KVKK | `gdpr_requests` · `gdpr_request` · `process_gdpr_request` · `gdpr_download_data` |
| Yorum | `reviews` · `review` · `update_review` · `update_review_status` |
| Promosyon | `cart_rules` · `cart_rule` · `save_cart_rule` · `copy_cart_rule` · `coupons` · `create_coupon` · `generate_coupons` · `delete_coupons` · `catalog_rules` · `catalog_rule` · `save_catalog_rule` |
| Pazarlama/CMS | `campaigns` · `send_campaign` · `email_templates` · `save_email_template` · `marketing_events` · `subscribers` · `cms_pages` · `cms_page` · `save_cms_page` |
| Arama ve SEO | `search_terms` · `search_term` · `update_search_term` · `delete_search_term` · `search_synonyms` · `search_synonym` · `create_search_synonym` · `update_search_synonym` · `delete_search_synonym` · `url_rewrites` · `url_rewrite` · `create_url_rewrite` · `update_url_rewrite` · `delete_url_rewrite` · `save_url_rewrite` · `sitemaps` · `sitemap` · `create_sitemap` · `update_sitemap` · `delete_sitemap` · `generate_sitemap` |
| Vergi/kanal/ayar | `tax_categories` · `tax_rates` · `tax_rate` · `save_tax_rate` · `save_tax_category` · `channels` · `channel` · `update_channel` · `currencies` · `exchange_rates` · `refresh_exchange_rates` · `locales` · `inventory_sources` · `themes` · `save_theme` · `admin_users` · `admin_roles` · `admin_permissions` · `admin_menu` · `configuration` · `update_configuration` · `configuration_menu` · `configuration_slugs` · `snapshot` |
| BBD kargo | `bbd_shipments` · `bbd_shipment` · `bbd_create_shipment` · `bbd_shipment_offers` · `bbd_purchase_shipment` · `bbd_cancel_shipment` · `bbd_return_shipment` · `bbd_sync_shipment` · `bbd_shipment_label` · `bbd_carriers` · `bbd_test_carrier` · `bbd_shipping_rates` · `bbd_update_shipping_rates` · `bbd_refresh_price_list` |
| BBD POS | `bbd_payment_attempts` · `bbd_payment_attempt` · `bbd_payment_links` · `bbd_create_payment_link` · `bbd_cancel_payment_link` · `bbd_refund_payment` · `bbd_pos_terminals` · `bbd_update_pos_terminal` · `bbd_reconciliation` |
| BBD BLD | `bbd_bld_jobs` · `bbd_retry_bld_job` · `bbd_reprint_order` · `bbd_bld_test` |
| BBD Deneme Kulübü | `bbd_trial_exams` · `bbd_trial_members` · `bbd_trial_results` · `bbd_save_trial_exam` · `bbd_upload_trial_results` · `bbd_publish_trial_results` |
| BBD set/ana ekran | `bbd_bundles` · `bbd_bundle` · `bbd_save_bundle` · `bbd_carousel` · `bbd_save_carousel_slot` · `bbd_reorder_carousel` |
| BBD yedek/sağlık | `bbd_backups` · `bbd_create_backup` · `bbd_verify_backup` · `bbd_download_backup` · `bbd_restore_backup` · `bbd_catalog_health` · `bbd_catalog_issues` · `bbd_reindex_catalog` |
| BBD AI/bildirim/talep/denetim | `bbd_ai_tools` · `bbd_ai_run` · `bbd_ai_apply` · `bbd_ai_runs` · `bbd_ai_usage` · `bbd_notifications` · `bbd_send_notification` · `bbd_notification_rules` · `bbd_save_notification_rule` · `bbd_mobile_settings` · `bbd_update_mobile_settings` · `bbd_review_requests` · `bbd_send_review_request` · `bbd_return_requests` · `bbd_return_request` · `bbd_update_return_request` · `bbd_audit` · `bbd_audit_entry` |

`bbd_*` metotlarının uçları **yayında** (ölçüm 2026-08-16): mağazada
`route:list --path=api/admin/bbd` 87 rota sayıyor. Bu satır bir dönem
"`bbd_*` metotlarının uçları henüz yayında değil" diyordu ve o cümle
2026-08-14 dağıtımına kadar doğruydu — **artık değil.** Toptan "yayında
değil" varsayarak ekran kapatmak, bugün çalışan bir bölümü kullanıcıya
"kullanılamıyor" diye göstermek olur.

Bugün yayında **olmayan** dört uç kalmıştır ve hepsi tek tek bilinir:

| Metot | Uç | Durum |
|---|---|---|
| `bbd_ai_tools` | `GET bbd/ai/tools` | 404 — mağazada "araç/bütçe" kavramı yok |
| `bbd_ai_run` | `POST bbd/ai/tools/{tool}/run` | 404 — model sunucuda çalışmıyor |
| `bbd_ai_usage` | `GET bbd/ai/usage` | 404 — mağaza jeton/maliyet tutmuyor |
| `upload_media` | `POST bbd/home/slides` | 404 — vitrin görselleri `storefront/carousels` üzerinden |

Ayrıca birkaç uç **bilerek** yazılmamıştır (`bbd_refund_payment`,
`bbd_restore_backup`) ya da mağaza tarafında karşılığı yoktur
(`bbd_bundle`, `bbd_save_bundle`, `bbd_reorder_carousel`,
`bbd_save_notification_rule`); ayrıntı ilgili docstring'de.

Bu dallar yine de **durur ve kaldırılmaz**: bir uç geri çekilirse çağrı
`StoreApiError(code="bbd_endpoint_missing")` ile döner ve ekran o bölümü
"mağaza paketi yayınlanınca çalışacak" diyerek göstermeli, çökmemelidir
(K7). Bir metodun bugünkü durumu tek yerden okunur: `backend/client.py`
içindeki kendi docstring'i.

## Yapılandırma (`core_config`) okumanın kuralı

`configuration(slug)` **slug'sız çağrılmaz**: parametresiz istek canlıda 422
+ çevrilmemiş `configuration.slug-required` anahtarı döndürüyor, o metin
kullanıcıya hiçbir şey anlatmaz. Geçit boş slug'ı istek çıkmadan reddeder.

Slug keşfi: `configuration_slugs()` (düz liste, `hasFields` alanı yazılabilir
düğümü söyler) ya da `configuration_menu()` (alan tanımlarıyla ağaç —
form kendiliğinden üretilebilir).

**Slug koddan türetilmez.** Alan kodu `<slug>.<alan>` biçimindedir ama alan
adı da nokta içerebiliyor: `sales.order_settings.reorder.admin` içinde slug
iki, `general.general.locale_options.weight_unit` içinde üç parçalıdır.
Bu yüzden `update_configuration` düz bir "değişiklikler" sözlüğü almaz;
grubu çağırandan ister.

## Ayar

`config/default.yaml` · şema `config/schema.json`. Makineye özel değerler
`config/local.yaml` → `modules.store_api.*`.

Yükleme sınırı `max_upload_mb` (varsayılan 24) ile ayarlanır; geçidin o anki
kuralları `state()` çağrısında (`maxUploadBytes` dahil) döner.

Belirteç **kasadadır**: `secrets.store.admin_token` (biçim `<id>|<düz metin>`,
Bagisto → Entegrasyon → Belirteçler). Depoda, ayarda, log'da ve hata
metninde bulunmaz; hata metinlerindeki `token/password/secret` alanları
otomatik maskelenir.

## Tablolar

| Tablo | İçerik |
|---|---|
| `mod_store_api_audit` | Yazma denemeleri — istek **çıkmadan** yazılır, gerekçe ve aktör taşır. `result` boşsa "gönderildi mi belli değil". |
| `mod_store_api_snapshot` | Referans verinin 30 dakikalık anlık görüntüsü (kanal, para, dil, grup, vergi, aile, depo). |

## Testler

```bash
.venv/bin/python -m pytest modules/store_api/tests -q
.venv/bin/ruff check modules/store_api
```

Testler ağa çıkmaz: `httpx.MockTransport` ile sahte sunucu, sahte kasa ve
sahte depo kullanılır.
