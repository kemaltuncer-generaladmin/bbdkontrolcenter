# KM uç envanteri

ÜRETİLEN BELGE — elle düzenlenmez. Üretici: `tools/security/endpoint_inventory.py`
(denetim: `tests/core/test_guvenlik_envanteri.py`). Sunucu görünümü: platform `linux`,
yalnız yüklenen modüller. Model ve kurallar: [README.md](README.md).

- Uç: **527** · yazma: **322** · kimliksiz: **23** · PIN step-up: **23** · gerekçeli onay (ADR 0012): **86**
- Yüklü modüller (27): `antivirus`, `bbd_akademi`, `bbd_bulk_sale`, `bbd_canteen_api`, `bbd_canteen_backups`, `bbd_canteen_devices`, `bbd_canteen_products`, `bbd_canteen_reports`, `bbd_lunch`, `bbd_payment_request`, `bbd_sms`, `bbd_students`, `bbd_tablet`, `bell`, `mobile_gateway`, `print`, `store_api`, `store_bundles`, `store_customers`, `store_dashboard`, `store_invoices`, `store_notifications`, `store_orders`, `store_products`, `store_refunds`, `store_shipping`, `store_udit_logs`
- Envanterde olmayan modüller (26): bbd_class_schedule (disabled), bld_api (disabled), bld_cms (disabled), bld_customers (disabled), bld_dashboard (disabled), bld_invoices (disabled), bld_kds (disabled), bld_manual_order (disabled), bld_menu (disabled), bld_notifications (disabled), bld_orders (disabled), bld_products (disabled), bld_sales_settings (disabled), bld_sms (disabled), bld_status_monitor (disabled), bld_subscriptions (disabled), store_ai (disabled), store_backups (disabled), store_cms (disabled), store_home_media (disabled), store_payment_gateway (disabled), store_promotions (disabled), store_reports (disabled), store_requests (disabled), store_tax (disabled), store_trial_club (disabled)

Sütunlar: **İzin** — gruplar arasında ∧ (hepsi), grup içinde ∨ (biri yeter); ilk grup
çoğunlukla router'ın taban iznidir (`module.yaml` → `http.requires`). **Gövde** — uç
gövdesinde ek `has_permission` denetimi var. **Step-up** — `pin` (PIN teyidi) ya da
`gerekçe` (ADR 0012). **Kimlik** — `—` ise uç oturum istemez; gerekçesi
`tests/core/test_guvenlik_envanteri.py` → `PUBLIC_ENDPOINTS`.


## antivirus (3)

| Yöntem | Yol | İzin | Gövde | Step-up | Kimlik |
|---|---|---|---|---|---|
| POST | `/api/antivirus/scan` | `antivirus.view` ∧ `antivirus.scan` |  |  | oturum |
| POST | `/api/antivirus/scan/cancel` | `antivirus.view` ∧ `antivirus.scan` |  |  | oturum |
| GET | `/api/antivirus/state` | `antivirus.view` |  |  | oturum |

## bbd_akademi (20)

| Yöntem | Yol | İzin | Gövde | Step-up | Kimlik |
|---|---|---|---|---|---|
| POST | `/api/bbd_akademi/km/call` | `bbd_akademi.view` |  | pin | oturum |
| POST | `/api/bbd_akademi/km/documents/akademi` | `bbd_akademi.view` |  |  | oturum |
| POST | `/api/bbd_akademi/km/documents/pages` | `bbd_akademi.view` |  |  | oturum |
| POST | `/api/bbd_akademi/km/documents/preview` | `bbd_akademi.view` |  |  | oturum |
| GET | `/api/bbd_akademi/km/download` | `bbd_akademi.view` |  |  | oturum |
| GET | `/api/bbd_akademi/km/events` | `bbd_akademi.view` |  |  | oturum |
| POST | `/api/bbd_akademi/km/events/retry` | `bbd_akademi.view` | evet |  | oturum |
| POST | `/api/bbd_akademi/km/fetch` | `bbd_akademi.view` |  |  | oturum |
| GET | `/api/bbd_akademi/km/operations` | `bbd_akademi.view` |  |  | oturum |
| GET | `/api/bbd_akademi/km/staff` | `bbd_akademi.view` ∧ `bbd_akademi.staff` |  |  | oturum |
| POST | `/api/bbd_akademi/km/staff` | `bbd_akademi.view` ∧ `bbd_akademi.staff` |  |  | oturum |
| POST | `/api/bbd_akademi/km/staff/sync` | `bbd_akademi.view` ∧ `bbd_akademi.staff` |  |  | oturum |
| PATCH | `/api/bbd_akademi/km/staff/{staff_id}` | `bbd_akademi.view` ∧ `bbd_akademi.staff` |  |  | oturum |
| GET | `/api/bbd_akademi/km/status` | `bbd_akademi.view` | evet |  | oturum |
| POST | `/api/bbd_akademi/km/step-up` | `bbd_akademi.view` | evet | pin | oturum |
| DELETE | `/api/bbd_akademi/{path:path}` | `bbd_akademi.view` |  |  | oturum |
| GET | `/api/bbd_akademi/{path:path}` | `bbd_akademi.view` |  |  | oturum |
| PATCH | `/api/bbd_akademi/{path:path}` | `bbd_akademi.view` |  |  | oturum |
| POST | `/api/bbd_akademi/{path:path}` | `bbd_akademi.view` |  |  | oturum |
| PUT | `/api/bbd_akademi/{path:path}` | `bbd_akademi.view` |  |  | oturum |

## bbd_bulk_sale (9)

| Yöntem | Yol | İzin | Gövde | Step-up | Kimlik |
|---|---|---|---|---|---|
| GET | `/api/bbd_bulk_sale/batches/{batch_ref}` | `bbd_bulk_sale.view` |  |  | oturum |
| POST | `/api/bbd_bulk_sale/commit` | `bbd_bulk_sale.view` ∧ `bbd_bulk_sale.manage` |  |  | oturum |
| PUT | `/api/bbd_bulk_sale/presets` | `bbd_bulk_sale.view` ∧ `bbd_bulk_sale.manage` |  |  | oturum |
| DELETE | `/api/bbd_bulk_sale/presets/{preset_id}` | `bbd_bulk_sale.view` ∧ `bbd_bulk_sale.manage` |  |  | oturum |
| POST | `/api/bbd_bulk_sale/preview` | `bbd_bulk_sale.view` |  |  | oturum |
| POST | `/api/bbd_bulk_sale/report` | `bbd_bulk_sale.view` |  |  | oturum |
| POST | `/api/bbd_bulk_sale/report/pages` | `bbd_bulk_sale.view` |  |  | oturum |
| POST | `/api/bbd_bulk_sale/reverse` | `bbd_bulk_sale.view` ∧ `bbd_bulk_sale.reverse` |  | gerekçe | oturum |
| GET | `/api/bbd_bulk_sale/workspace` | `bbd_bulk_sale.view` |  |  | oturum |

## bbd_canteen_api (1)

| Yöntem | Yol | İzin | Gövde | Step-up | Kimlik |
|---|---|---|---|---|---|
| POST | `/api/bbd_canteen_api/v1/events` | `bbd_canteen_api.events.push` |  |  | oturum |

## bbd_canteen_backups (4)

| Yöntem | Yol | İzin | Gövde | Step-up | Kimlik |
|---|---|---|---|---|---|
| POST | `/api/bbd_canteen_backups/create` | `bbd_canteen_backups.view` ∧ `bbd_canteen_backups.manage` |  |  | oturum |
| POST | `/api/bbd_canteen_backups/download` | `bbd_canteen_backups.view` ∧ `bbd_canteen_backups.manage` |  |  | oturum |
| GET | `/api/bbd_canteen_backups/overview` | `bbd_canteen_backups.view` |  |  | oturum |
| POST | `/api/bbd_canteen_backups/verify` | `bbd_canteen_backups.view` |  |  | oturum |

## bbd_canteen_devices (7)

| Yöntem | Yol | İzin | Gövde | Step-up | Kimlik |
|---|---|---|---|---|---|
| GET | `/api/bbd_canteen_devices/audit` | `bbd_canteen_devices.view` |  |  | oturum |
| GET | `/api/bbd_canteen_devices/kiosks` | `bbd_canteen_devices.view` |  |  | oturum |
| POST | `/api/bbd_canteen_devices/kiosks` | `bbd_canteen_devices.view` ∧ `bbd_canteen_devices.manage` |  | gerekçe | oturum |
| PATCH | `/api/bbd_canteen_devices/kiosks/{kiosk_id}` | `bbd_canteen_devices.view` ∧ `bbd_canteen_devices.manage` |  | gerekçe | oturum |
| POST | `/api/bbd_canteen_devices/kiosks/{kiosk_id}/pairing-code` | `bbd_canteen_devices.view` ∧ `bbd_canteen_devices.manage` |  | gerekçe | oturum |
| POST | `/api/bbd_canteen_devices/kiosks/{kiosk_id}/revoke` | `bbd_canteen_devices.view` ∧ `bbd_canteen_devices.devices` | evet | pin | oturum |
| GET | `/api/bbd_canteen_devices/printer` | `bbd_canteen_devices.view` |  |  | oturum |

## bbd_canteen_products (6)

| Yöntem | Yol | İzin | Gövde | Step-up | Kimlik |
|---|---|---|---|---|---|
| GET | `/api/bbd_canteen_products/audit` | `bbd_canteen_products.view` |  |  | oturum |
| POST | `/api/bbd_canteen_products/bulk-price` | `bbd_canteen_products.view` ∧ `bbd_canteen_products.manage` |  |  | oturum |
| GET | `/api/bbd_canteen_products/products` | `bbd_canteen_products.view` |  |  | oturum |
| POST | `/api/bbd_canteen_products/products` | `bbd_canteen_products.view` ∧ `bbd_canteen_products.manage` |  |  | oturum |
| PUT | `/api/bbd_canteen_products/products/{product_id}/active` | `bbd_canteen_products.view` ∧ `bbd_canteen_products.deactivate` |  |  | oturum |
| POST | `/api/bbd_canteen_products/products/{product_id}/stock` | `bbd_canteen_products.view` ∧ `bbd_canteen_products.manage` |  |  | oturum |

## bbd_canteen_reports (11)

| Yöntem | Yol | İzin | Gövde | Step-up | Kimlik |
|---|---|---|---|---|---|
| GET | `/api/bbd_canteen_reports/cache` | `bbd_canteen_reports.view` |  |  | oturum |
| POST | `/api/bbd_canteen_reports/export` | `bbd_canteen_reports.view` ∧ `bbd_canteen_reports.export` |  |  | oturum |
| POST | `/api/bbd_canteen_reports/pages` | `bbd_canteen_reports.view` ∧ `bbd_canteen_reports.export` |  |  | oturum |
| POST | `/api/bbd_canteen_reports/preview` | `bbd_canteen_reports.view` ∧ `bbd_canteen_reports.export` |  |  | oturum |
| POST | `/api/bbd_canteen_reports/print` | `bbd_canteen_reports.view` ∧ `bbd_canteen_reports.print` |  |  | oturum |
| GET | `/api/bbd_canteen_reports/printer` | `bbd_canteen_reports.view` |  |  | oturum |
| POST | `/api/bbd_canteen_reports/report` | `bbd_canteen_reports.view` |  |  | oturum |
| GET | `/api/bbd_canteen_reports/saved` | `bbd_canteen_reports.view` |  |  | oturum |
| PUT | `/api/bbd_canteen_reports/saved` | `bbd_canteen_reports.view` |  |  | oturum |
| DELETE | `/api/bbd_canteen_reports/saved/{report_id}` | `bbd_canteen_reports.view` |  |  | oturum |
| GET | `/api/bbd_canteen_reports/students/{kantin_id}` | `bbd_canteen_reports.view` |  |  | oturum |

## bbd_lunch (11)

| Yöntem | Yol | İzin | Gövde | Step-up | Kimlik |
|---|---|---|---|---|---|
| POST | `/api/bbd_lunch/commit` | `bbd_lunch.view` ∧ `bbd_lunch.manage` |  |  | oturum |
| POST | `/api/bbd_lunch/commit-range` | `bbd_lunch.view` ∧ `bbd_lunch.manage` |  |  | oturum |
| GET | `/api/bbd_lunch/days/{service_date}` | `bbd_lunch.view` |  |  | oturum |
| PUT | `/api/bbd_lunch/holidays` | `bbd_lunch.view` ∧ `bbd_lunch.manage` |  |  | oturum |
| GET | `/api/bbd_lunch/overview` | `bbd_lunch.view` |  |  | oturum |
| POST | `/api/bbd_lunch/preview` | `bbd_lunch.view` |  |  | oturum |
| POST | `/api/bbd_lunch/report` | `bbd_lunch.view` |  |  | oturum |
| POST | `/api/bbd_lunch/report/pages` | `bbd_lunch.view` |  |  | oturum |
| POST | `/api/bbd_lunch/reverse` | `bbd_lunch.view` ∧ `bbd_lunch.reverse` |  | gerekçe | oturum |
| PUT | `/api/bbd_lunch/roster` | `bbd_lunch.view` ∧ `bbd_lunch.manage` |  |  | oturum |
| POST | `/api/bbd_lunch/stock` | `bbd_lunch.view` ∧ `bbd_lunch.manage` |  |  | oturum |

## bbd_payment_request (15)

| Yöntem | Yol | İzin | Gövde | Step-up | Kimlik |
|---|---|---|---|---|---|
| GET | `/api/bbd_payment_request/batches` | `bbd_payment_request.view` |  |  | oturum |
| GET | `/api/bbd_payment_request/batches/{batch_ref}` | `bbd_payment_request.view` |  |  | oturum |
| POST | `/api/bbd_payment_request/collections` | `bbd_payment_request.view` ∧ `bbd_payment_request.collect` |  |  | oturum |
| POST | `/api/bbd_payment_request/preview` | `bbd_payment_request.view` |  |  | oturum |
| POST | `/api/bbd_payment_request/report` | `bbd_payment_request.view` |  |  | oturum |
| POST | `/api/bbd_payment_request/report/pages` | `bbd_payment_request.view` |  |  | oturum |
| GET | `/api/bbd_payment_request/requests/{ref}` | `bbd_payment_request.view` |  |  | oturum |
| POST | `/api/bbd_payment_request/requests/{ref}/cancel` | `bbd_payment_request.view` ∧ `bbd_payment_request.send` |  |  | oturum |
| POST | `/api/bbd_payment_request/requests/{ref}/reopen` | `bbd_payment_request.view` ∧ `bbd_payment_request.send` |  |  | oturum |
| POST | `/api/bbd_payment_request/requests/{ref}/resend` | `bbd_payment_request.view` ∧ `bbd_payment_request.send` |  |  | oturum |
| POST | `/api/bbd_payment_request/send` | `bbd_payment_request.view` ∧ `bbd_payment_request.send` |  |  | oturum |
| POST | `/api/bbd_payment_request/students/{kantin_id}/request` | `bbd_payment_request.view` ∧ `bbd_payment_request.send` |  |  | oturum |
| GET | `/api/bbd_payment_request/students/{kantin_id}/state` | `bbd_payment_request.view` |  |  | oturum |
| PUT | `/api/bbd_payment_request/templates` | `bbd_payment_request.view` ∧ `bbd_payment_request.manage_templates` |  |  | oturum |
| GET | `/api/bbd_payment_request/workspace` | `bbd_payment_request.view` |  |  | oturum |

## bbd_sms (10)

| Yöntem | Yol | İzin | Gövde | Step-up | Kimlik |
|---|---|---|---|---|---|
| GET | `/api/bbd_sms/batches/{batch_ref}` | `bbd_sms.view` |  |  | oturum |
| GET | `/api/bbd_sms/history` | `bbd_sms.view` |  |  | oturum |
| POST | `/api/bbd_sms/measure` | `bbd_sms.view` |  |  | oturum |
| PUT | `/api/bbd_sms/netgsm` | `bbd_sms.view` ∧ `bbd_sms.manage_settings` |  |  | oturum |
| PUT | `/api/bbd_sms/presets` | `bbd_sms.view` ∧ `bbd_sms.manage_settings` |  |  | oturum |
| DELETE | `/api/bbd_sms/presets/{preset_id}` | `bbd_sms.view` ∧ `bbd_sms.manage_settings` |  |  | oturum |
| POST | `/api/bbd_sms/preview` | `bbd_sms.view` |  |  | oturum |
| GET | `/api/bbd_sms/queue` | `bbd_sms.view` |  |  | oturum |
| POST | `/api/bbd_sms/send` | `bbd_sms.view` ∧ `bbd_sms.send` |  |  | oturum |
| GET | `/api/bbd_sms/workspace` | `bbd_sms.view` |  |  | oturum |

## bbd_students (55)

| Yöntem | Yol | İzin | Gövde | Step-up | Kimlik |
|---|---|---|---|---|---|
| POST | `/api/bbd_students/cards` | `bbd_students.view` ∧ `bbd_students.qr` |  |  | oturum |
| GET | `/api/bbd_students/classes` | `bbd_students.view` |  |  | oturum |
| GET | `/api/bbd_students/classes/merge` | `bbd_students.view` |  |  | oturum |
| POST | `/api/bbd_students/classes/merge` | `bbd_students.view` ∧ `bbd_students.groups` |  |  | oturum |
| POST | `/api/bbd_students/classes/merge/rollback` | `bbd_students.view` ∧ `bbd_students.delete` |  | pin | oturum |
| DELETE | `/api/bbd_students/classes/{class_name}` | `bbd_students.view` ∧ `bbd_students.manage` |  |  | oturum |
| PUT | `/api/bbd_students/classes/{class_name}` | `bbd_students.view` ∧ `bbd_students.manage` |  |  | oturum |
| GET | `/api/bbd_students/groups` | `bbd_students.view` |  |  | oturum |
| POST | `/api/bbd_students/groups` | `bbd_students.view` ∧ `bbd_students.groups` |  |  | oturum |
| POST | `/api/bbd_students/groups/academic-year` | `bbd_students.view` ∧ `bbd_students.groups` |  |  | oturum |
| POST | `/api/bbd_students/groups/from-class` | `bbd_students.view` ∧ `bbd_students.groups` |  |  | oturum |
| GET | `/api/bbd_students/groups/memberships` | `bbd_students.view` |  |  | oturum |
| POST | `/api/bbd_students/groups/move` | `bbd_students.view` ∧ `bbd_students.groups` |  | gerekçe | oturum |
| GET | `/api/bbd_students/groups/rollover` | `bbd_students.view` |  |  | oturum |
| POST | `/api/bbd_students/groups/rollover` | `bbd_students.view` ∧ `bbd_students.groups` |  | pin | oturum |
| POST | `/api/bbd_students/groups/rollover/preview` | `bbd_students.view` ∧ `bbd_students.groups` |  |  | oturum |
| GET | `/api/bbd_students/groups/rollover/{rollover_id}` | `bbd_students.view` |  |  | oturum |
| POST | `/api/bbd_students/groups/rollover/{rollover_id}/undo` | `bbd_students.view` ∧ `bbd_students.groups` |  | pin | oturum |
| DELETE | `/api/bbd_students/groups/{group_id}` | `bbd_students.view` ∧ `bbd_students.groups_delete` |  | pin | oturum |
| GET | `/api/bbd_students/groups/{group_id}` | `bbd_students.view` |  |  | oturum |
| PUT | `/api/bbd_students/groups/{group_id}` | `bbd_students.view` ∧ `bbd_students.groups` |  |  | oturum |
| POST | `/api/bbd_students/groups/{group_id}/apply-level` | `bbd_students.view` ∧ `bbd_students.groups` |  |  | oturum |
| POST | `/api/bbd_students/groups/{group_id}/archive` | `bbd_students.view` ∧ `bbd_students.groups` |  |  | oturum |
| GET | `/api/bbd_students/groups/{group_id}/history` | `bbd_students.view` |  |  | oturum |
| GET | `/api/bbd_students/groups/{group_id}/members` | `bbd_students.view` |  |  | oturum |
| POST | `/api/bbd_students/groups/{group_id}/members` | `bbd_students.view` ∧ `bbd_students.groups` |  | gerekçe | oturum |
| POST | `/api/bbd_students/groups/{group_id}/members/end` | `bbd_students.view` ∧ `bbd_students.groups` |  | gerekçe | oturum |
| PATCH | `/api/bbd_students/groups/{group_id}/members/{member_id}` | `bbd_students.view` ∧ `bbd_students.groups` |  |  | oturum |
| POST | `/api/bbd_students/groups/{group_id}/restore` | `bbd_students.view` ∧ `bbd_students.groups` |  |  | oturum |
| POST | `/api/bbd_students/preview` | `bbd_students.view` ∧ `bbd_students.access_code` |  |  | oturum |
| GET | `/api/bbd_students/search` | `bbd_students.view` |  |  | oturum |
| GET | `/api/bbd_students/status` | `bbd_students.view` |  |  | oturum |
| GET | `/api/bbd_students/students` | `bbd_students.view` |  |  | oturum |
| POST | `/api/bbd_students/students` | `bbd_students.view` ∧ `bbd_students.manage` |  |  | oturum |
| POST | `/api/bbd_students/students/access-codes` | `bbd_students.view` ∧ `bbd_students.access_code` |  |  | oturum |
| POST | `/api/bbd_students/students/class` | `bbd_students.view` ∧ `bbd_students.groups` |  |  | oturum |
| POST | `/api/bbd_students/students/curriculum` | `bbd_students.view` ∧ `bbd_students.manage` |  |  | oturum |
| POST | `/api/bbd_students/students/deactivate` | `bbd_students.view` ∧ `bbd_students.lifecycle` |  | gerekçe | oturum |
| POST | `/api/bbd_students/students/delete` | `bbd_students.view` ∧ `bbd_students.delete` |  | pin | oturum |
| POST | `/api/bbd_students/students/reactivate` | `bbd_students.view` ∧ `bbd_students.lifecycle` |  |  | oturum |
| DELETE | `/api/bbd_students/students/{kantin_id}` | `bbd_students.view` ∧ `bbd_students.delete` |  | pin | oturum |
| GET | `/api/bbd_students/students/{kantin_id}` | `bbd_students.view` |  |  | oturum |
| PATCH | `/api/bbd_students/students/{kantin_id}` | `bbd_students.view` ∧ `bbd_students.manage` |  |  | oturum |
| POST | `/api/bbd_students/students/{kantin_id}/access-code` | `bbd_students.view` ∧ `bbd_students.access_code` |  |  | oturum |
| GET | `/api/bbd_students/students/{kantin_id}/canteen` | `bbd_students.view` |  |  | oturum |
| PATCH | `/api/bbd_students/students/{kantin_id}/canteen/controls` | `bbd_students.view` ∧ `bbd_students.canteen_controls` |  |  | oturum |
| POST | `/api/bbd_students/students/{kantin_id}/deactivate` | `bbd_students.view` ∧ `bbd_students.lifecycle` |  | gerekçe | oturum |
| GET | `/api/bbd_students/students/{kantin_id}/delete-check` | `bbd_students.view` ∧ `bbd_students.delete` |  |  | oturum |
| GET | `/api/bbd_students/students/{kantin_id}/groups` | `bbd_students.view` |  |  | oturum |
| GET | `/api/bbd_students/students/{kantin_id}/guardians` | `bbd_students.view` |  |  | oturum |
| PUT | `/api/bbd_students/students/{kantin_id}/guardians` | `bbd_students.view` ∧ `bbd_students.manage` |  |  | oturum |
| GET | `/api/bbd_students/students/{kantin_id}/qr` | `bbd_students.view` ∧ `bbd_students.qr` |  |  | oturum |
| POST | `/api/bbd_students/students/{kantin_id}/reactivate` | `bbd_students.view` ∧ `bbd_students.lifecycle` |  |  | oturum |
| GET | `/api/bbd_students/students/{kantin_id}/status-history` | `bbd_students.view` |  |  | oturum |
| GET | `/api/bbd_students/sync` | `bbd_students.view` |  |  | oturum |

## bbd_tablet (57)

| Yöntem | Yol | İzin | Gövde | Step-up | Kimlik |
|---|---|---|---|---|---|
| GET | `/api/bbd_tablet/apps` | `bbd_tablet.view` |  |  | oturum |
| DELETE | `/api/bbd_tablet/apps/releases/{release_id}` | `bbd_tablet.view` ∧ `bbd_tablet.manage` |  |  | oturum |
| POST | `/api/bbd_tablet/apps/releases/{release_id}/publish` | `bbd_tablet.view` ∧ `bbd_tablet.manage` |  |  | oturum |
| POST | `/api/bbd_tablet/apps/uploads` | `bbd_tablet.view` ∧ `bbd_tablet.manage` |  |  | oturum |
| GET | `/api/bbd_tablet/apps/uploads/{upload_id}` | `bbd_tablet.view` ∧ `bbd_tablet.manage` |  |  | oturum |
| PUT | `/api/bbd_tablet/apps/uploads/{upload_id}/chunk` | `bbd_tablet.view` ∧ `bbd_tablet.manage` |  |  | oturum |
| POST | `/api/bbd_tablet/apps/uploads/{upload_id}/finish` | `bbd_tablet.view` ∧ `bbd_tablet.manage` |  |  | oturum |
| DELETE | `/api/bbd_tablet/apps/{package_name}` | `bbd_tablet.view` ∧ `bbd_tablet.manage` |  | pin | oturum |
| GET | `/api/bbd_tablet/apps/{package_name}/devices` | `bbd_tablet.view` |  |  | oturum |
| POST | `/api/bbd_tablet/apps/{package_name}/devices/{device_id}/prioritize` | `bbd_tablet.view` ∧ `bbd_tablet.manage` |  |  | oturum |
| POST | `/api/bbd_tablet/devices` | `bbd_tablet.view` ∧ `bbd_tablet.manage` |  |  | oturum |
| POST | `/api/bbd_tablet/devices/bulk` | `bbd_tablet.view` ∧ `bbd_tablet.manage` | evet | pin | oturum |
| DELETE | `/api/bbd_tablet/devices/{device_id}` | `bbd_tablet.view` ∧ `bbd_tablet.manage` |  | pin | oturum |
| PATCH | `/api/bbd_tablet/devices/{device_id}` | `bbd_tablet.view` ∧ `bbd_tablet.manage` |  |  | oturum |
| GET | `/api/bbd_tablet/devices/{device_id}/apps` | `bbd_tablet.view` |  |  | oturum |
| POST | `/api/bbd_tablet/devices/{device_id}/assign-profile` | `bbd_tablet.view` ∧ `bbd_tablet.manage` |  |  | oturum |
| GET | `/api/bbd_tablet/devices/{device_id}/commands` | `bbd_tablet.view` |  |  | oturum |
| POST | `/api/bbd_tablet/devices/{device_id}/commands` | `bbd_tablet.view` ∧ `bbd_tablet.manage` |  |  | oturum |
| DELETE | `/api/bbd_tablet/devices/{device_id}/commands/{command_id}` | `bbd_tablet.view` ∧ `bbd_tablet.manage` |  |  | oturum |
| GET | `/api/bbd_tablet/devices/{device_id}/diagnostics` | `bbd_tablet.view` |  |  | oturum |
| GET | `/api/bbd_tablet/devices/{device_id}/diagnostics/{diagnostics_id}` | `bbd_tablet.view` ∧ `bbd_tablet.manage` |  |  | oturum |
| POST | `/api/bbd_tablet/devices/{device_id}/enrollment-code` | `bbd_tablet.view` ∧ `bbd_tablet.manage` |  |  | oturum |
| POST | `/api/bbd_tablet/devices/{device_id}/login-lock/reset` | `bbd_tablet.view` ∧ `bbd_tablet.grant` |  |  | oturum |
| POST | `/api/bbd_tablet/devices/{device_id}/revoke` | `bbd_tablet.view` ∧ `bbd_tablet.manage` |  | pin | oturum |
| GET | `/api/bbd_tablet/overview` | `bbd_tablet.view` |  |  | oturum |
| POST | `/api/bbd_tablet/profiles` | `bbd_tablet.view` ∧ `bbd_tablet.manage` |  |  | oturum |
| DELETE | `/api/bbd_tablet/profiles/{profile_id}` | `bbd_tablet.view` ∧ `bbd_tablet.manage` |  | pin | oturum |
| PUT | `/api/bbd_tablet/profiles/{profile_id}` | `bbd_tablet.view` ∧ `bbd_tablet.manage` |  |  | oturum |
| POST | `/api/bbd_tablet/profiles/{profile_id}/duplicate` | `bbd_tablet.view` ∧ `bbd_tablet.manage` |  |  | oturum |
| GET | `/api/bbd_tablet/reports/daily` | `bbd_tablet.view` |  |  | oturum |
| GET | `/api/bbd_tablet/sessions` | `bbd_tablet.view` |  |  | oturum |
| POST | `/api/bbd_tablet/sessions/{session_id}/end` | `bbd_tablet.view` ∧ `bbd_tablet.manage` |  |  | oturum |
| GET | `/api/bbd_tablet/students` | `bbd_tablet.view` |  |  | oturum |
| GET | `/api/bbd_tablet/students/{student_id}/app-limits` | `bbd_tablet.view` |  |  | oturum |
| DELETE | `/api/bbd_tablet/students/{student_id}/app-limits/{package_name}` | `bbd_tablet.view` ∧ `bbd_tablet.grant` |  |  | oturum |
| POST | `/api/bbd_tablet/students/{student_id}/apps/{package_name}/grants` | `bbd_tablet.view` ∧ `bbd_tablet.grant` |  | gerekçe | oturum |
| POST | `/api/bbd_tablet/students/{student_id}/block` | `bbd_tablet.view` ∧ `bbd_tablet.grant` |  | gerekçe | oturum |
| GET | `/api/bbd_tablet/students/{student_id}/blocks` | `bbd_tablet.view` |  |  | oturum |
| POST | `/api/bbd_tablet/students/{student_id}/grants/{grant_id}/revoke` | `bbd_tablet.view` ∧ `bbd_tablet.grant` |  |  | oturum |
| DELETE | `/api/bbd_tablet/students/{student_id}/history` | `bbd_tablet.view` ∧ `bbd_tablet.manage` |  | pin | oturum |
| POST | `/api/bbd_tablet/students/{student_id}/unblock` | `bbd_tablet.view` ∧ `bbd_tablet.grant` |  |  | oturum |
| GET | `/api/bbd_tablet/students/{student_id}/usage` | `bbd_tablet.view` |  |  | oturum |
| POST | `/api/tablet/v1/auth/login` | — |  |  | — |
| POST | `/api/tablet/v1/auth/logout` | — |  |  | — |
| POST | `/api/tablet/v1/devices/enroll` | — |  |  | — |
| PUT | `/api/tablet/v1/devices/{device_id}/apps` | — |  |  | — |
| POST | `/api/tablet/v1/devices/{device_id}/diagnostics` | — |  |  | — |
| POST | `/api/tablet/v1/devices/{device_id}/heartbeat` | — |  |  | — |
| POST | `/api/tablet/v1/devices/{device_id}/initial-profile` | — |  |  | — |
| GET | `/api/tablet/v1/devices/{device_id}/policy` | — |  |  | — |
| GET | `/api/tablet/v1/devices/{device_id}/releases/{release_id}/files/{index}` | — |  |  | — |
| GET | `/api/tablet/v1/devices/{device_id}/self-update/{release_id}` | — |  |  | — |
| POST | `/api/tablet/v1/devices/{device_id}/sync` | — |  |  | — |
| POST | `/api/tablet/v1/devices/{device_id}/unpair` | — |  |  | — |
| GET | `/api/tablet/v1/profiles` | — |  |  | — |
| GET | `/api/tablet/v1/profiles/{profile_id}` | — |  |  | — |
| POST | `/api/tablet/v1/usage/batch` | — |  |  | — |

## bell (23)

| Yöntem | Yol | İzin | Gövde | Step-up | Kimlik |
|---|---|---|---|---|---|
| POST | `/api/bell/call` | `bell.view` ∧ `bell.ring_now` |  |  | oturum |
| POST | `/api/bell/groups` | `bell.view` ∧ `bell.manage` |  |  | oturum |
| POST | `/api/bell/groups/migrate` | `bell.view` ∧ `bell.manage` |  |  | oturum |
| PUT | `/api/bell/groups/{group_id}` | `bell.view` ∧ `bell.manage` |  |  | oturum |
| POST | `/api/bell/groups/{group_id}/delete` | `bell.view` ∧ `bell.delete` |  | pin | oturum |
| POST | `/api/bell/page` | `bell.view` ∧ `bell.ring_now` |  |  | oturum |
| POST | `/api/bell/plan/override` | `bell.view` ∧ `bell.manage` |  | pin | oturum |
| POST | `/api/bell/plan/override/clear` | `bell.view` ∧ `bell.manage` |  |  | oturum |
| GET | `/api/bell/plan/status` | `bell.view` |  |  | oturum |
| POST | `/api/bell/preview` | `bell.view` |  |  | oturum |
| POST | `/api/bell/profiles` | `bell.view` ∧ `bell.manage` |  |  | oturum |
| PUT | `/api/bell/profiles/{profile_id}` | `bell.view` ∧ `bell.manage` |  |  | oturum |
| POST | `/api/bell/profiles/{profile_id}/delete` | `bell.view` ∧ `bell.manage` |  |  | oturum |
| POST | `/api/bell/reschedule` | `bell.view` ∧ `bell.manage` |  |  | oturum |
| POST | `/api/bell/ring` | `bell.view` ∧ `bell.ring_now` |  |  | oturum |
| PUT | `/api/bell/settings` | `bell.view` ∧ `bell.manage` |  |  | oturum |
| POST | `/api/bell/sound` | `bell.view` ∧ `bell.manage` |  |  | oturum |
| POST | `/api/bell/sound/{name}/delete` | `bell.view` ∧ `bell.delete` |  | pin | oturum |
| GET | `/api/bell/state` | `bell.view` |  |  | oturum |
| POST | `/api/bell/sync` | `bell.view` ∧ `bell.manage` |  |  | oturum |
| PUT | `/api/bell/times` | `bell.view` ∧ `bell.manage` |  |  | oturum |
| POST | `/api/bell/voices/prune` | `bell.view` ∧ `bell.delete` |  | pin | oturum |
| POST | `/api/bell/voices/rebuild` | `bell.view` ∧ `bell.manage` |  |  | oturum |

## core (22)

| Yöntem | Yol | İzin | Gövde | Step-up | Kimlik |
|---|---|---|---|---|---|
| POST | `/api/auth/login` | — |  |  | — |
| POST | `/api/auth/logout` | — |  |  | — |
| POST | `/api/auth/set-password` | — |  |  | — |
| GET | `/api/events/wait` | — |  |  | oturum |
| POST | `/api/outputs/document` | (`outputs.print` ∨ `print.view` ∨ `settings.view`) |  |  | oturum |
| GET | `/api/roles` | `roles.view` |  |  | oturum |
| GET | `/api/settings` | `settings.view` | evet |  | oturum |
| PUT | `/api/settings` | `settings.manage` ∧ `settings.view` |  |  | oturum |
| GET | `/api/settings/diagnostics` | `settings.view` |  |  | oturum |
| GET | `/api/settings/printer` | `settings.view` |  |  | oturum |
| POST | `/api/settings/printer/test-page` | `settings.manage` ∧ `settings.view` |  |  | oturum |
| POST | `/api/settings/support-bundle` | `settings.manage` ∧ `settings.view` |  |  | oturum |
| GET | `/api/settings/update` | `settings.view` |  |  | oturum |
| GET | `/api/users` | `users.view` |  |  | oturum |
| POST | `/api/users` | `users.set_password` ∧ `users.manage` |  | pin | oturum |
| PUT | `/api/users/{user_id}` | `users.manage` |  | pin | oturum |
| POST | `/api/users/{user_id}/password` | `users.set_password` |  | pin | oturum |
| POST | `/api/users/{user_id}/status` | `users.manage` |  |  | oturum |
| POST | `/auth/logout` | — |  |  | — |
| GET | `/auth/me` | — |  |  | oturum |
| GET | `/health` | — |  |  | — |
| GET | `/modules` | — | evet |  | oturum |

## mobile_gateway (47)

| Yöntem | Yol | İzin | Gövde | Step-up | Kimlik |
|---|---|---|---|---|---|
| POST | `/api/mobile_gateway/v1/announcements/call` | `mobile_gateway.access` |  |  | oturum |
| POST | `/api/mobile_gateway/v1/announcements/prepare` | `mobile_gateway.access` |  |  | oturum |
| POST | `/api/mobile_gateway/v1/announcements/preview` | `mobile_gateway.access` |  |  | oturum |
| POST | `/api/mobile_gateway/v1/announcements/say` | `mobile_gateway.access` |  |  | oturum |
| POST | `/api/mobile_gateway/v1/auth/student-code` | `mobile_gateway.access` |  |  | oturum |
| PUT | `/api/mobile_gateway/v1/bell/plan` | `mobile_gateway.access` |  |  | oturum |
| GET | `/api/mobile_gateway/v1/bell/plan/status` | `mobile_gateway.access` |  |  | oturum |
| GET | `/api/mobile_gateway/v1/canteen/balances` | `mobile_gateway.access` |  |  | oturum |
| GET | `/api/mobile_gateway/v1/canteen/parent-notifications` | `mobile_gateway.access` |  |  | oturum |
| GET | `/api/mobile_gateway/v1/canteen/payment-requests` | `mobile_gateway.access` |  |  | oturum |
| GET | `/api/mobile_gateway/v1/canteen/products` | `mobile_gateway.access` |  |  | oturum |
| GET | `/api/mobile_gateway/v1/canteen/settings/public` | `mobile_gateway.access` |  |  | oturum |
| GET | `/api/mobile_gateway/v1/canteen/totals` | `mobile_gateway.access` |  |  | oturum |
| GET | `/api/mobile_gateway/v1/directory` | `mobile_gateway.access` |  |  | oturum |
| GET | `/api/mobile_gateway/v1/events` | `mobile_gateway.access` |  |  | oturum |
| GET | `/api/mobile_gateway/v1/groups` | `mobile_gateway.access` |  |  | oturum |
| GET | `/api/mobile_gateway/v1/schedule/bell-week` | `mobile_gateway.access` |  |  | oturum |
| GET | `/api/mobile_gateway/v1/settings/public` | `mobile_gateway.access` |  |  | oturum |
| GET | `/api/mobile_gateway/v1/sms/health` | `mobile_gateway.access` |  |  | oturum |
| POST | `/api/mobile_gateway/v1/sms/limits/reset` | `mobile_gateway.access` |  |  | oturum |
| POST | `/api/mobile_gateway/v1/sms/otp` | `mobile_gateway.access` |  |  | oturum |
| GET | `/api/mobile_gateway/v1/sms/otp/{job_id}` | `mobile_gateway.access` |  |  | oturum |
| POST | `/api/mobile_gateway/v1/sms/send` | `mobile_gateway.access` |  |  | oturum |
| GET | `/api/mobile_gateway/v1/students/{student_id}` | `mobile_gateway.access` |  |  | oturum |
| GET | `/api/mobile_gateway/v1/students/{student_id}/canteen` | `mobile_gateway.access` |  |  | oturum |
| GET | `/api/mobile_gateway/v1/students/{student_id}/canteen/collections` | `mobile_gateway.access` |  |  | oturum |
| POST | `/api/mobile_gateway/v1/students/{student_id}/canteen/collections` | `mobile_gateway.access` |  |  | oturum |
| GET | `/api/mobile_gateway/v1/students/{student_id}/canteen/controls` | `mobile_gateway.access` |  |  | oturum |
| PATCH | `/api/mobile_gateway/v1/students/{student_id}/canteen/controls` | `mobile_gateway.access` |  |  | oturum |
| POST | `/api/mobile_gateway/v1/students/{student_id}/canteen/payment-requests` | `mobile_gateway.access` |  |  | oturum |
| GET | `/api/mobile_gateway/v1/students/{student_id}/canteen/qr` | `mobile_gateway.access` |  |  | oturum |
| GET | `/api/mobile_gateway/v1/students/{student_id}/canteen/transactions` | `mobile_gateway.access` |  |  | oturum |
| GET | `/api/mobile_gateway/v1/students/{student_id}/payment-requests` | `mobile_gateway.access` |  |  | oturum |
| POST | `/api/mobile_gateway/v1/students/{student_id}/payment-requests` | `mobile_gateway.access` |  |  | oturum |
| GET | `/api/mobile_gateway/v1/students/{student_id}/tablet-usage` | `mobile_gateway.access` |  |  | oturum |
| PUT | `/api/mobile_gateway/v1/students/{student_id}/tablet/app-limits/{package_name}` | `mobile_gateway.access` |  |  | oturum |
| POST | `/api/mobile_gateway/v1/students/{student_id}/tablet/block` | `mobile_gateway.access` |  |  | oturum |
| POST | `/api/mobile_gateway/v1/students/{student_id}/tablet/grant` | `mobile_gateway.access` |  |  | oturum |
| POST | `/api/mobile_gateway/v1/students/{student_id}/tablet/grants/{grant_id}/revoke` | `mobile_gateway.access` |  |  | oturum |
| GET | `/api/mobile_gateway/v1/students/{student_id}/tablet/limits` | `mobile_gateway.access` |  |  | oturum |
| POST | `/api/mobile_gateway/v1/students/{student_id}/tablet/unblock` | `mobile_gateway.access` |  |  | oturum |
| POST | `/api/mobile_gateway/v1/students/{student_id}/verify-code` | `mobile_gateway.access` |  |  | oturum |
| GET | `/api/mobile_gateway/v1/tablet-profiles` | `mobile_gateway.access` |  |  | oturum |
| PUT | `/api/mobile_gateway/v1/tablet-profiles/{profile_id}/limits` | `mobile_gateway.access` |  |  | oturum |
| POST | `/api/mobile_gateway/v1/tablet-sessions/{session_id}/end` | `mobile_gateway.access` |  |  | oturum |
| GET | `/api/mobile_gateway/v1/tablets` | `mobile_gateway.access` |  |  | oturum |
| GET | `/api/mobile_gateway/v1/tablets/usage` | `mobile_gateway.access` |  |  | oturum |

## platform (8)

| Yöntem | Yol | İzin | Gövde | Step-up | Kimlik |
|---|---|---|---|---|---|
| GET | `/api/pairing/installations` | `installations.view` |  |  | oturum |
| POST | `/api/pairing/installations/{installation_id}/revoke` | `installations.manage` |  |  | oturum |
| POST | `/api/pairing/pair` | — |  |  | — |
| POST | `/api/pairing/pair-code` | `installations.manage` |  |  | oturum |
| POST | `/api/pairing/refresh` | `installations.view` |  |  | oturum |
| POST | `/api/pairing/reset` | — |  |  | — |
| GET | `/api/pairing/state` | — |  |  | — |
| POST | `/api/pairing/unpair` | `installations.manage` |  | pin | oturum |

## print (5)

| Yöntem | Yol | İzin | Gövde | Step-up | Kimlik |
|---|---|---|---|---|---|
| POST | `/api/print/folder` | `print.view` |  |  | oturum |
| GET | `/api/print/outputs` | `print.view` | evet |  | oturum |
| POST | `/api/print/preview` | `print.view` |  |  | oturum |
| GET | `/api/print/printer` | `print.view` |  |  | oturum |
| POST | `/api/print/reprint` | `print.view` ∧ `print.reprint` |  |  | oturum |

## store_bundles (11)

| Yöntem | Yol | İzin | Gövde | Step-up | Kimlik |
|---|---|---|---|---|---|
| GET | `/api/store_bundles/audit` | `store_bundles.view` |  |  | oturum |
| GET | `/api/store_bundles/bundles` | `store_bundles.view` |  |  | oturum |
| GET | `/api/store_bundles/bundles/{bundle_id}` | `store_bundles.view` |  |  | oturum |
| PUT | `/api/store_bundles/bundles/{bundle_id}` | `store_bundles.view` ∧ `store_bundles.manage` |  | gerekçe | oturum |
| POST | `/api/store_bundles/bundles/{bundle_id}/status` | `store_bundles.view` ∧ `store_bundles.deactivate` |  | gerekçe | oturum |
| POST | `/api/store_bundles/calc` | `store_bundles.view` |  |  | oturum |
| POST | `/api/store_bundles/export` | `store_bundles.view` |  |  | oturum |
| GET | `/api/store_bundles/lookup` | `store_bundles.view` |  |  | oturum |
| POST | `/api/store_bundles/preview` | `store_bundles.view` |  |  | oturum |
| POST | `/api/store_bundles/print` | `store_bundles.view` |  |  | oturum |
| GET | `/api/store_bundles/printer` | `store_bundles.view` |  |  | oturum |

## store_customers (25)

| Yöntem | Yol | İzin | Gövde | Step-up | Kimlik |
|---|---|---|---|---|---|
| GET | `/api/store_customers/audit` | `store_customers.view` |  |  | oturum |
| GET | `/api/store_customers/customers` | `store_customers.view` |  |  | oturum |
| POST | `/api/store_customers/customers/status` | `store_customers.view` ∧ `store_customers.deactivate` |  | gerekçe | oturum |
| GET | `/api/store_customers/customers/{customer_id}` | `store_customers.view` |  |  | oturum |
| PUT | `/api/store_customers/customers/{customer_id}` | `store_customers.view` ∧ `store_customers.manage` |  | gerekçe | oturum |
| GET | `/api/store_customers/customers/{customer_id}/addresses` | `store_customers.view` |  |  | oturum |
| POST | `/api/store_customers/customers/{customer_id}/anonymize` | `store_customers.view` ∧ `store_customers.anonymize` |  | gerekçe | oturum |
| GET | `/api/store_customers/customers/{customer_id}/consents` | `store_customers.view` |  |  | oturum |
| POST | `/api/store_customers/customers/{customer_id}/gdpr-export` | `store_customers.view` ∧ `store_customers.gdpr` |  | gerekçe | oturum |
| GET | `/api/store_customers/customers/{customer_id}/notes` | `store_customers.view` |  |  | oturum |
| POST | `/api/store_customers/customers/{customer_id}/notes` | `store_customers.view` ∧ `store_customers.manage` |  | gerekçe | oturum |
| GET | `/api/store_customers/customers/{customer_id}/orders` | `store_customers.view` |  |  | oturum |
| GET | `/api/store_customers/customers/{customer_id}/returns` | `store_customers.view` |  |  | oturum |
| POST | `/api/store_customers/export` | `store_customers.view` |  |  | oturum |
| GET | `/api/store_customers/overview` | `store_customers.view` |  |  | oturum |
| POST | `/api/store_customers/preview` | `store_customers.view` |  |  | oturum |
| POST | `/api/store_customers/print` | `store_customers.view` |  |  | oturum |
| GET | `/api/store_customers/printer` | `store_customers.view` |  |  | oturum |
| GET | `/api/store_customers/reference` | `store_customers.view` |  |  | oturum |
| GET | `/api/store_customers/reviews` | `store_customers.view` |  |  | oturum |
| POST | `/api/store_customers/reviews/status` | `store_customers.view` ∧ `store_customers.reviews` |  | gerekçe | oturum |
| POST | `/api/store_customers/reviews/{review_id}/reply` | `store_customers.view` ∧ `store_customers.reviews` |  | gerekçe | oturum |
| GET | `/api/store_customers/scan-status` | `store_customers.view` |  |  | oturum |
| GET | `/api/store_customers/settings` | `store_customers.view` |  |  | oturum |
| POST | `/api/store_customers/settings` | `store_customers.view` ∧ `store_customers.manage` |  | gerekçe | oturum |

## store_dashboard (16)

| Yöntem | Yol | İzin | Gövde | Step-up | Kimlik |
|---|---|---|---|---|---|
| GET | `/api/store_dashboard/audit` | `store_dashboard.view` |  |  | oturum |
| GET | `/api/store_dashboard/config` | `store_dashboard.view` |  |  | oturum |
| POST | `/api/store_dashboard/config` | `store_dashboard.view` ∧ `store_dashboard.manage` |  | gerekçe | oturum |
| POST | `/api/store_dashboard/export` | `store_dashboard.view` |  |  | oturum |
| GET | `/api/store_dashboard/orders/recent` | `store_dashboard.view` |  |  | oturum |
| GET | `/api/store_dashboard/pending` | `store_dashboard.view` |  |  | oturum |
| POST | `/api/store_dashboard/preview` | `store_dashboard.view` |  |  | oturum |
| POST | `/api/store_dashboard/print` | `store_dashboard.view` |  |  | oturum |
| GET | `/api/store_dashboard/printer` | `store_dashboard.view` |  |  | oturum |
| GET | `/api/store_dashboard/reference` | `store_dashboard.view` |  |  | oturum |
| GET | `/api/store_dashboard/settings` | `store_dashboard.view` |  |  | oturum |
| POST | `/api/store_dashboard/settings` | `store_dashboard.view` ∧ `store_dashboard.manage` |  | gerekçe | oturum |
| POST | `/api/store_dashboard/settings/maintenance` | `store_dashboard.view` ∧ `store_dashboard.maintenance` |  | gerekçe | oturum |
| GET | `/api/store_dashboard/stock/critical` | `store_dashboard.view` |  |  | oturum |
| GET | `/api/store_dashboard/summary` | `store_dashboard.view` |  |  | oturum |
| GET | `/api/store_dashboard/system` | `store_dashboard.view` |  |  | oturum |

## store_invoices (16)

| Yöntem | Yol | İzin | Gövde | Step-up | Kimlik |
|---|---|---|---|---|---|
| GET | `/api/store_invoices/audit` | `store_invoices.view` |  |  | oturum |
| GET | `/api/store_invoices/by-order/{order_id}` | `store_invoices.view` |  |  | oturum |
| POST | `/api/store_invoices/export` | `store_invoices.view` |  |  | oturum |
| GET | `/api/store_invoices/invoices` | `store_invoices.view` |  |  | oturum |
| POST | `/api/store_invoices/invoices/issue` | `store_invoices.view` ∧ `store_invoices.issue` |  | gerekçe | oturum |
| POST | `/api/store_invoices/invoices/state` | `store_invoices.view` ∧ `store_invoices.manage` |  | gerekçe | oturum |
| GET | `/api/store_invoices/invoices/{invoice_id}` | `store_invoices.view` |  |  | oturum |
| POST | `/api/store_invoices/invoices/{invoice_id}/legal-no` | `store_invoices.view` ∧ `store_invoices.legal_no` |  | gerekçe | oturum |
| POST | `/api/store_invoices/invoices/{invoice_id}/send` | `store_invoices.view` ∧ `store_invoices.manage` |  | gerekçe | oturum |
| POST | `/api/store_invoices/preview` | `store_invoices.view` |  |  | oturum |
| POST | `/api/store_invoices/print` | `store_invoices.view` |  |  | oturum |
| GET | `/api/store_invoices/printer` | `store_invoices.view` |  |  | oturum |
| GET | `/api/store_invoices/series` | `store_invoices.view` |  |  | oturum |
| POST | `/api/store_invoices/series` | `store_invoices.view` ∧ `store_invoices.manage` |  | gerekçe | oturum |
| GET | `/api/store_invoices/shipments` | `store_invoices.view` |  |  | oturum |
| GET | `/api/store_invoices/uninvoiced` | `store_invoices.view` |  |  | oturum |

## store_notifications (26)

| Yöntem | Yol | İzin | Gövde | Step-up | Kimlik |
|---|---|---|---|---|---|
| GET | `/api/store_notifications/audit` | `store_notifications.view` |  |  | oturum |
| POST | `/api/store_notifications/broadcast/preview` | `store_notifications.view` ∧ `store_notifications.broadcast` |  |  | oturum |
| POST | `/api/store_notifications/broadcast/send` | `store_notifications.view` ∧ `store_notifications.broadcast` |  | gerekçe | oturum |
| GET | `/api/store_notifications/catalog` | `store_notifications.view` |  |  | oturum |
| GET | `/api/store_notifications/channels` | `store_notifications.view` |  |  | oturum |
| POST | `/api/store_notifications/channels` | `store_notifications.view` ∧ `store_notifications.manage` |  | gerekçe | oturum |
| GET | `/api/store_notifications/channels/test` | `store_notifications.view` ∧ `store_notifications.manage` |  |  | oturum |
| POST | `/api/store_notifications/export` | `store_notifications.view` |  |  | oturum |
| GET | `/api/store_notifications/history` | `store_notifications.view` |  |  | oturum |
| POST | `/api/store_notifications/history/{notification_id}/resend` | `store_notifications.view` ∧ `store_notifications.send` |  | gerekçe | oturum |
| POST | `/api/store_notifications/preview` | `store_notifications.view` |  |  | oturum |
| POST | `/api/store_notifications/print` | `store_notifications.view` |  |  | oturum |
| GET | `/api/store_notifications/printer` | `store_notifications.view` |  |  | oturum |
| GET | `/api/store_notifications/rules` | `store_notifications.view` |  |  | oturum |
| POST | `/api/store_notifications/rules` | `store_notifications.view` ∧ `store_notifications.manage` |  | gerekçe | oturum |
| POST | `/api/store_notifications/rules/{rule_id}/toggle` | `store_notifications.view` ∧ `store_notifications.manage` |  | gerekçe | oturum |
| GET | `/api/store_notifications/stages` | `store_notifications.view` |  |  | oturum |
| POST | `/api/store_notifications/stages` | `store_notifications.view` ∧ `store_notifications.manage` |  | gerekçe | oturum |
| GET | `/api/store_notifications/stages/log` | `store_notifications.view` |  |  | oturum |
| POST | `/api/store_notifications/stages/preview` | `store_notifications.view` |  |  | oturum |
| GET | `/api/store_notifications/subscribers` | `store_notifications.view` |  |  | oturum |
| GET | `/api/store_notifications/templates` | `store_notifications.view` |  |  | oturum |
| POST | `/api/store_notifications/templates` | `store_notifications.view` ∧ `store_notifications.manage` |  | gerekçe | oturum |
| POST | `/api/store_notifications/templates/deactivate` | `store_notifications.view` ∧ `store_notifications.manage` |  | gerekçe | oturum |
| POST | `/api/store_notifications/templates/preview` | `store_notifications.view` |  |  | oturum |
| POST | `/api/store_notifications/templates/test` | `store_notifications.view` ∧ `store_notifications.send` |  |  | oturum |

## store_orders (21)

| Yöntem | Yol | İzin | Gövde | Step-up | Kimlik |
|---|---|---|---|---|---|
| GET | `/api/store_orders/audit` | `store_orders.view` |  |  | oturum |
| POST | `/api/store_orders/batch/apply` | `store_orders.view` ∧ `store_orders.manage` |  | gerekçe | oturum |
| POST | `/api/store_orders/batch/preview` | `store_orders.view` ∧ `store_orders.manage` |  |  | oturum |
| POST | `/api/store_orders/export` | `store_orders.view` |  |  | oturum |
| POST | `/api/store_orders/labels` | `store_orders.view` ∧ `store_orders.manage` |  |  | oturum |
| GET | `/api/store_orders/orders` | `store_orders.view` |  |  | oturum |
| GET | `/api/store_orders/orders/{order_id}` | `store_orders.view` |  |  | oturum |
| POST | `/api/store_orders/orders/{order_id}/cancel` | `store_orders.view` ∧ `store_orders.cancel` |  | gerekçe | oturum |
| GET | `/api/store_orders/orders/{order_id}/comments` | `store_orders.view` |  |  | oturum |
| POST | `/api/store_orders/orders/{order_id}/comments` | `store_orders.view` ∧ `store_orders.manage` |  | gerekçe | oturum |
| POST | `/api/store_orders/orders/{order_id}/invoice` | `store_orders.view` ∧ `store_orders.manage` |  | gerekçe | oturum |
| POST | `/api/store_orders/orders/{order_id}/status` | `store_orders.view` ∧ `store_orders.manage` |  | gerekçe | oturum |
| GET | `/api/store_orders/overview` | `store_orders.view` |  |  | oturum |
| POST | `/api/store_orders/preview` | `store_orders.view` |  |  | oturum |
| POST | `/api/store_orders/print` | `store_orders.view` |  |  | oturum |
| GET | `/api/store_orders/printer` | `store_orders.view` |  |  | oturum |
| GET | `/api/store_orders/reference` | `store_orders.view` |  |  | oturum |
| GET | `/api/store_orders/settings` | `store_orders.view` |  |  | oturum |
| POST | `/api/store_orders/settings` | `store_orders.view` ∧ `store_orders.manage` |  | gerekçe | oturum |
| GET | `/api/store_orders/stage-sms` | `store_orders.view` |  |  | oturum |
| POST | `/api/store_orders/stage-sms/sweep` | `store_orders.view` ∧ `store_orders.manage` |  |  | oturum |

## store_products (42)

| Yöntem | Yol | İzin | Gövde | Step-up | Kimlik |
|---|---|---|---|---|---|
| GET | `/api/store_products/attributes` | `store_products.view` |  |  | oturum |
| POST | `/api/store_products/attributes` | `store_products.view` ∧ `store_products.attributes` |  | gerekçe | oturum |
| GET | `/api/store_products/attributes/{attribute_id}` | `store_products.view` |  |  | oturum |
| PUT | `/api/store_products/attributes/{attribute_id}` | `store_products.view` ∧ `store_products.attributes` |  | gerekçe | oturum |
| POST | `/api/store_products/attributes/{attribute_id}/deactivate` | `store_products.view` ∧ `store_products.attributes` |  | gerekçe | oturum |
| POST | `/api/store_products/attributes/{attribute_id}/delete` | `store_products.view` ∧ `store_products.attributes_delete` |  | gerekçe | oturum |
| POST | `/api/store_products/attributes/{attribute_id}/options` | `store_products.view` ∧ `store_products.attributes` |  | gerekçe | oturum |
| POST | `/api/store_products/attributes/{attribute_id}/options/{option_id}/delete` | `store_products.view` ∧ `store_products.attributes_delete` |  | gerekçe | oturum |
| GET | `/api/store_products/audit` | `store_products.view` |  |  | oturum |
| POST | `/api/store_products/bulk/apply` | `store_products.view` ∧ `store_products.bulk` | evet | gerekçe | oturum |
| POST | `/api/store_products/bulk/preview` | `store_products.view` ∧ `store_products.bulk` |  |  | oturum |
| POST | `/api/store_products/export` | `store_products.view` |  |  | oturum |
| GET | `/api/store_products/families` | `store_products.view` |  |  | oturum |
| POST | `/api/store_products/families` | `store_products.view` ∧ `store_products.attributes` |  | gerekçe | oturum |
| GET | `/api/store_products/families/{family_id}` | `store_products.view` |  |  | oturum |
| PUT | `/api/store_products/families/{family_id}` | `store_products.view` ∧ `store_products.attributes` |  | gerekçe | oturum |
| GET | `/api/store_products/health` | `store_products.view` |  |  | oturum |
| POST | `/api/store_products/order-items/mark` | `store_products.view` |  |  | oturum |
| POST | `/api/store_products/preview` | `store_products.view` |  |  | oturum |
| POST | `/api/store_products/print` | `store_products.view` |  |  | oturum |
| GET | `/api/store_products/printer` | `store_products.view` |  |  | oturum |
| GET | `/api/store_products/products` | `store_products.view` |  |  | oturum |
| POST | `/api/store_products/products` | `store_products.view` ∧ `store_products.manage` |  | gerekçe | oturum |
| POST | `/api/store_products/products/delete` | `store_products.view` ∧ `store_products.delete` |  | gerekçe | oturum |
| POST | `/api/store_products/products/delete/preview` | `store_products.view` ∧ `store_products.delete` |  |  | oturum |
| POST | `/api/store_products/products/plan` | `store_products.view` ∧ `store_products.manage` |  |  | oturum |
| POST | `/api/store_products/products/status` | `store_products.view` ∧ `store_products.deactivate` |  | gerekçe | oturum |
| GET | `/api/store_products/products/{product_id}` | `store_products.view` |  |  | oturum |
| PUT | `/api/store_products/products/{product_id}` | `store_products.view` ∧ `store_products.manage` |  | gerekçe | oturum |
| POST | `/api/store_products/products/{product_id}/categories` | `store_products.view` ∧ `store_products.manage` |  | gerekçe | oturum |
| POST | `/api/store_products/products/{product_id}/copy` | `store_products.view` ∧ `store_products.manage` |  | gerekçe | oturum |
| POST | `/api/store_products/products/{product_id}/group-price` | `store_products.view` ∧ `store_products.manage` |  | gerekçe | oturum |
| GET | `/api/store_products/products/{product_id}/images` | `store_products.view` |  |  | oturum |
| POST | `/api/store_products/products/{product_id}/images` | `store_products.view` ∧ `store_products.manage` |  | gerekçe | oturum |
| POST | `/api/store_products/products/{product_id}/images/reorder` | `store_products.view` ∧ `store_products.manage` |  | gerekçe | oturum |
| POST | `/api/store_products/products/{product_id}/images/{image_id}/remove` | `store_products.view` ∧ `store_products.manage` |  | gerekçe | oturum |
| POST | `/api/store_products/products/{product_id}/sku` | `store_products.view` ∧ `store_products.rename_sku` |  | gerekçe | oturum |
| POST | `/api/store_products/products/{product_id}/stock` | `store_products.view` ∧ `store_products.manage` |  | gerekçe | oturum |
| GET | `/api/store_products/reference` | `store_products.view` |  |  | oturum |
| GET | `/api/store_products/settings` | `store_products.view` |  |  | oturum |
| POST | `/api/store_products/settings` | `store_products.view` ∧ `store_products.manage` |  | gerekçe | oturum |
| GET | `/api/store_products/url-key` | `store_products.view` ∧ `store_products.manage` |  |  | oturum |

## store_refunds (13)

| Yöntem | Yol | İzin | Gövde | Step-up | Kimlik |
|---|---|---|---|---|---|
| POST | `/api/store_refunds/approve` | `store_refunds.view` ∧ `store_refunds.approve` |  | gerekçe | oturum |
| GET | `/api/store_refunds/audit` | `store_refunds.view` |  |  | oturum |
| POST | `/api/store_refunds/calculate` | `store_refunds.view` |  |  | oturum |
| POST | `/api/store_refunds/export` | `store_refunds.view` |  |  | oturum |
| GET | `/api/store_refunds/orders/{order_id}` | `store_refunds.view` |  |  | oturum |
| POST | `/api/store_refunds/orders/{order_id}/notify` | `store_refunds.view` ∧ `store_refunds.manage` |  | gerekçe | oturum |
| POST | `/api/store_refunds/orders/{order_id}/return-shipment` | `store_refunds.view` ∧ `store_refunds.ship_return` |  | gerekçe | oturum |
| POST | `/api/store_refunds/pos-refund` | `store_refunds.view` ∧ `store_refunds.approve` |  | gerekçe | oturum |
| POST | `/api/store_refunds/preview` | `store_refunds.view` |  |  | oturum |
| POST | `/api/store_refunds/print` | `store_refunds.view` |  |  | oturum |
| GET | `/api/store_refunds/printer` | `store_refunds.view` |  |  | oturum |
| GET | `/api/store_refunds/refunds` | `store_refunds.view` |  |  | oturum |
| POST | `/api/store_refunds/requests/{request_id}/status` | `store_refunds.view` ∧ `store_refunds.manage` |  | gerekçe | oturum |

## store_shipping (34)

| Yöntem | Yol | İzin | Gövde | Step-up | Kimlik |
|---|---|---|---|---|---|
| GET | `/api/store_shipping/audit` | `store_shipping.view` |  |  | oturum |
| GET | `/api/store_shipping/carriers` | `store_shipping.view` |  |  | oturum |
| POST | `/api/store_shipping/carriers/{carrier}/test` | `store_shipping.view` ∧ `store_shipping.manage` |  | gerekçe | oturum |
| POST | `/api/store_shipping/dispatch/estimate` | `store_shipping.view` |  |  | oturum |
| POST | `/api/store_shipping/export` | `store_shipping.view` |  |  | oturum |
| GET | `/api/store_shipping/geliver` | `store_shipping.view` ∧ `store_shipping.manage` |  |  | oturum |
| POST | `/api/store_shipping/geliver` | `store_shipping.view` ∧ `store_shipping.integration` |  | gerekçe | oturum |
| GET | `/api/store_shipping/geliver/test` | `store_shipping.view` ∧ `store_shipping.manage` |  |  | oturum |
| POST | `/api/store_shipping/geliver/webhook` | `store_shipping.view` ∧ `store_shipping.integration` |  | gerekçe | oturum |
| POST | `/api/store_shipping/orders/{order_id}/dispatch` | `store_shipping.view` ∧ `store_shipping.purchase` |  | gerekçe | oturum |
| POST | `/api/store_shipping/orders/{order_id}/manual-shipment` | `store_shipping.view` ∧ `store_shipping.manage` |  | gerekçe | oturum |
| GET | `/api/store_shipping/orders/{order_id}/shipments` | `store_shipping.view` |  |  | oturum |
| POST | `/api/store_shipping/orders/{order_id}/shipments` | `store_shipping.view` ∧ `store_shipping.manage` |  | gerekçe | oturum |
| GET | `/api/store_shipping/performance` | `store_shipping.view` |  |  | oturum |
| POST | `/api/store_shipping/preview` | `store_shipping.view` |  |  | oturum |
| POST | `/api/store_shipping/print` | `store_shipping.view` |  |  | oturum |
| GET | `/api/store_shipping/printer` | `store_shipping.view` |  |  | oturum |
| POST | `/api/store_shipping/quote` | `store_shipping.view` |  |  | oturum |
| GET | `/api/store_shipping/rates` | `store_shipping.view` |  |  | oturum |
| POST | `/api/store_shipping/rates` | `store_shipping.view` ∧ `store_shipping.rates` |  | gerekçe | oturum |
| POST | `/api/store_shipping/rates/refresh` | `store_shipping.view` ∧ `store_shipping.rates` |  | gerekçe | oturum |
| GET | `/api/store_shipping/ready` | `store_shipping.view` |  |  | oturum |
| GET | `/api/store_shipping/settings` | `store_shipping.view` |  |  | oturum |
| POST | `/api/store_shipping/settings` | `store_shipping.view` ∧ `store_shipping.manage` |  | gerekçe | oturum |
| GET | `/api/store_shipping/shipments` | `store_shipping.view` |  |  | oturum |
| POST | `/api/store_shipping/shipments/sync` | `store_shipping.view` ∧ `store_shipping.manage` |  | gerekçe | oturum |
| GET | `/api/store_shipping/shipments/{shipment_id}` | `store_shipping.view` |  |  | oturum |
| POST | `/api/store_shipping/shipments/{shipment_id}/cancel` | `store_shipping.view` ∧ `store_shipping.cancel` |  | gerekçe | oturum |
| POST | `/api/store_shipping/shipments/{shipment_id}/notify` | `store_shipping.view` ∧ `store_shipping.manage` |  | gerekçe | oturum |
| GET | `/api/store_shipping/shipments/{shipment_id}/offers` | `store_shipping.view` ∧ `store_shipping.manage` |  |  | oturum |
| POST | `/api/store_shipping/shipments/{shipment_id}/purchase` | `store_shipping.view` ∧ `store_shipping.purchase` |  | gerekçe | oturum |
| POST | `/api/store_shipping/shipments/{shipment_id}/return` | `store_shipping.view` ∧ `store_shipping.purchase` |  | gerekçe | oturum |
| GET | `/api/store_shipping/zones` | `store_shipping.view` |  |  | oturum |
| POST | `/api/store_shipping/zones` | `store_shipping.view` ∧ `store_shipping.rates` |  | gerekçe | oturum |

## store_udit_logs (9)

| Yöntem | Yol | İzin | Gövde | Step-up | Kimlik |
|---|---|---|---|---|---|
| GET | `/api/store_udit_logs/entries` | `store_udit_logs.view` |  |  | oturum |
| GET | `/api/store_udit_logs/entry` | `store_udit_logs.view` |  |  | oturum |
| POST | `/api/store_udit_logs/export` | `store_udit_logs.view` ∧ `store_udit_logs.export` |  |  | oturum |
| GET | `/api/store_udit_logs/exports` | `store_udit_logs.view` |  |  | oturum |
| GET | `/api/store_udit_logs/history` | `store_udit_logs.view` |  |  | oturum |
| POST | `/api/store_udit_logs/preview` | `store_udit_logs.view` ∧ `store_udit_logs.export` |  |  | oturum |
| POST | `/api/store_udit_logs/print` | `store_udit_logs.view` ∧ `store_udit_logs.export` |  |  | oturum |
| GET | `/api/store_udit_logs/printer` | `store_udit_logs.view` |  |  | oturum |
| GET | `/api/store_udit_logs/reference` | `store_udit_logs.view` |  |  | oturum |
