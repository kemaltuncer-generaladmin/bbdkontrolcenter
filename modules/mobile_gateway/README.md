# Mobil Geçit (`mobile_gateway`)

BBD Akademi API'sinin (öğrenci/veli/yönetici Flutter uygulamasının sunucusu)
Kontrol Merkezi'ne **sunucudan sunucuya** açılan tek kapısı. Ekranı yoktur.

- Bağlayıcı sözleşme (`bbd-akademi` deposu):
  `docs/integrations/km-mobile-gateway.md` (v1, "Ekler" dahil),
  `docs/integrations/kantin-veli-kontrolleri.md` §2.2,
  `docs/integrations/odeme-akademi-kaynagi.md` "Ek" (`payToken`).
- Önek: `/api/mobile_gateway/v1` · İzin: `mobile_gateway.access`
- Sözleşme testi: `tests/test_contract_v1.py`
- Kurallar: [../../CLAUDE.md](../../CLAUDE.md) · Kılavuz: [../../docs/module-guide.md](../../docs/module-guide.md)

## Akademi nasıl kimlik doğrular

`Authorization: Bearer kmsk_<prefix>_<secret>` — çekirdeğe **ADR 0028** ile
eklenen servis anahtarı (`ozellik/mobil-gecit` dalında). Anahtar `mobile_service`
rolündeki bir KM servis kullanıcısına bağlıdır; izin o kullanıcının
rollerinden gelir. Bu modül anahtarı kendisi doğrulamaz: çekirdeğin oturum
çözümünden geçen `CurrentUser`'ı alır ve her uçta `mobile_gateway.access`
sorar (router tabanı + uç imzası, K9). Rol adı sorulmaz (K10).

| Başlık | Anlamı |
|---|---|
| `X-KM-On-Behalf-Of` | Uygulamada işlemi başlatan kişi (`akademi:staff:42:Ayşe Yılmaz`). **Yalnız denetim izine** yazılır, yetki vermez. |
| `X-Request-Id` | Denetim izine ve log'a geçer; anonsta zile aktarılır. |
| `Idempotency-Key` | Her POST/PATCH'te kabul edilir; 24 saat saklanır. Aynı anahtar + aynı gövde → saklı yanıt (`Idempotent-Replayed: true`); farklı gövde → `409 idempotency_conflict`. Yalnız başarılı yanıt saklanır. |

Hata zarfı: `{"error": {"status", "code", "message", …}}` — `message` Türkçe;
kilit ve hız sınırında `retryAfterSeconds` (ve `Retry-After` başlığı),
doğrulamada `fields`, eksik yetenekte `capability`, kantinin tutar
redlerinde (`exceeds_debt`) `details: {balance, maxAmount}`.

`X-KM-On-Behalf-Of` HTTP başlığıdır ve ASCII olmak zorundadır: Türkçe ad
**yüzde kodlu UTF-8** gönderilir (`Ay%C5%9Fe`). Ham UTF-8 bayt da çözülür.

## Uçlar

| Uç | Yanıt | Hata kodları |
|---|---|---|
| `GET /directory?since=` | 200 dizin + `etag`, `If-None-Match` → 304 | 422, 503 `capability_missing` |
| `POST /auth/student-code` | 200 `{match, studentId?}` | 422, 423 `locked`, 429 `rate_limited`, 503 `canteen_unavailable` |
| `POST /students/{id}/verify-code` | 200 `{valid}` | 422, 423, 429, 503 |
| `GET /students/{id}/canteen?days=` | 200 bakiye, işlemler, tahsilat, bekleyen talep (+ veli kontrolleri) | 404 `student_not_found`, 422, 503 |
| `GET /students/{id}/canteen/qr` | 200 `{qrText, version: 1}` | 404 |
| `GET /students/{id}/payment-requests` | 200 `{items}` (`payToken`, `amountFixed` dahil) | 503 |
| `POST /students/{id}/payment-requests` · `POST /students/{id}/canteen/payment-requests` | 201 `{ref, amount, status, linkUrl, amountFixed, payToken}` | 409 `no_debt`/`no_phone`, 422 `exceeds_debt`/`invalid_amount`, 404 |
| `GET /students/{id}/canteen/controls` | 200 kontroller | 404, 503 |
| `PATCH /students/{id}/canteen/controls` | 200 kontroller | 422 `validation_error`, 404, 503 |
| `POST /students/{id}/canteen/collections` | 201 `{amount, balance}` | 422 `no_debt`/`exceeds_debt`/`invalid_amount`, 404 |
| `GET /canteen/payment-requests?since=&limit=` | 200 `{items, nextSince, hasMore}` | 422, 503 |
| `GET /canteen/parent-notifications?since=&limit=` | 200 `{items, nextSince, hasMore}` — SMS metni/telefon YOK | 422, 503 |
| `GET /canteen/settings/public` | 200 `{allowPrepayment, maxPrepayment}` | 503 |
| `GET /canteen/products` | 200 `{items}` (5 dk önbellek) | 503 |
| `GET /tablets` | 200 `{summary, devices}` | 503 `capability_missing`, 502 |
| `GET /students/{id}/tablet-usage?from=&to=` | 200 (en çok 62 gün) | 422, 404, 503 |
| `POST /students/{id}/tablet/grant` | 201 `{grantId}` | 422, 404, 503 |
| `POST /students/{id}/tablet/block` · `/unblock` | 200 `{blockedUntil}` | 422, 404, 503 |
| `POST /tablet-sessions/{id}/end` | 200 `{ended}` | 404 `session_not_found`, 409, 503 |
| `POST /announcements/call` | 202 `{status, voiceCached, etaSeconds}` | 409 `bell_disabled`, 503 `tts_unavailable`, 429, 404, 503 `capability_missing` |
| `GET /schedule/bell-week` | 200 — `bbd_class_schedule` `/week` biçimi; zil yoksa `available: false` + `reason` | — |
| `POST /sms/otp` | 200 `{sent: true}` | 422, 429, 502 `sms_failed`, 503 |
| `GET /settings/public` | 200 kurum adı, saat dilimi, özellikler | — |

## Güvenlik kuralları

- Öğrenci şifresi, OTP kodu ve SMS metni **log'a, tabloya, denetim izine ve
  yanıta yazılmaz**. Idempotensi özeti kasadaki anahtarla HMAC'lenir
  (`secrets` → `mobile_gateway.idempotency_pepper`, ilk kullanımda üretilir);
  6 haneli kod düz sha256'dan geri bulunabilirdi.
- `/auth/student-code`: kantin `lookup-code` **bir kez**; bulunan öğrencinin
  kendi ya da 1./2. veli telefonu verilen telefona eşitse eşleşir. Telefon
  başına 15 dakikada 5 yanlış → 423. Geçit kantine dakikada en çok 10
  `lookup-code` gönderir (kantinin 30/dk bütçesi tabletlerle ortak) → 429.
- `/verify-code`: öğrenci başına aynı kilit; kod başkasınınsa yalnız `valid: false`.
- `/sms/otp`: metin `otp_template` ayarından üretilir, istemci metin yazamaz.
  Telefon başına saatte 5, toplam dakikada 60. Kayıt `mod_mobile_gateway_sms_log`
  tablosunda **maskeli** telefonla.
- Öğrenci çağırma: aynı öğrenci 60 sn içinde ikinci kez çağrılamaz (429).
- Akışlarda `nextSince` kantinin imlecidir (epoch-ms gelir, ISO +03:00'a
  çevrilir; vermezse satırlardan hesaplanır). Kantinin `updatedSince` sınırı
  **dahildir**: aynı satır yeniden gelebilir, istemci `ref`/`id` ile ayıklar.
  `hasMore` kantinden aynen geçer.
- Ödeme talebi kantine `origin: "api"` ile açılır; kantin öğrenci başına
  tek açık talebi yeniden kullanır.
- Çıktılar beyaz listeyle kurulur: erişim kodu, QR anahtarı, Netgsm bilgisi,
  akışlarda telefon ve SMS metni hiçbir uçtan dönmez.

## Staging güvenliği — ÖNEMLİ

Varsayılan adresler **canlıyı** gösterir:

- Kantin: `bbd_canteen_api` → `base_url: https://kantin.bbdstore.com.tr`
- Zil köprüsü: `bell` → `bridge.base_url: https://bbdstore.com.tr`

Staging/test KM kurulumu bu geçidi açmadan önce ikisini de ezmelidir, yoksa
Akademi'nin staging ortamı canlı kantine ödeme talebi açar, canlı velilere
SMS gönderir ve okulda gerçek anons çalar:

```bash
KM__modules__bbd_canteen_api__base_url=https://<staging-kantin>
KM__modules__bell__bridge__base_url=https://<staging-köprü>
```

(Ortam değişkeni biçimi `KM__<yol>__<alt_yol>`, bkz. `km_core/config/loader.py`.)
Kantin cihaz token'ı kasada `canteen.device_token` olarak durur; staging
kasası canlının token'ını taşımamalıdır.

## Tüketilen yetenekler

Bütün çağrılar `backend/caps.py` içindedir; imza farkı yalnız orayı değiştirir.
Yetenekler istek anında çözülür. İsteğe bağlı olan (tablet, zil) yoksa ilgili
uç `503 capability_missing` döner, modül yine yüklenir.

| Yetenek | Kullanılan |
|---|---|
| `secrets` (zorunlu) | `get(key)`, `set(key, value)` |
| `canteen.api` (zorunlu) | `lookup_student_by_code(code)`, `get_student(id)` (yoksa `student(id)`), `transactions(from_ms=, to_ms=, limit=[, student_id=])`, `collections(from_ms=, to_ms=, student_id=)`, `payment_requests()`, `create_payment_request(id[, amount=])`, `update_controls(id, patch)`, `record_collection(id, amount=, local_id=)` (yoksa `collect_cash`), `products()`, `payment_requests_feed(updated_since=, limit=)`, `parent_notifications_feed(updated_since=, limit=)`, `canteen_settings_public()`, `send_sms(phone=, message=)` — `create_payment_request` `origin` parametresini tanıyorsa `origin="api"` gönderilir; akışlar liste ya da `{data, nextSince, hasMore}` dönebilir; `CanteenError` red gövdesini `details`/`body` alanında taşırsa `balance`/`maxAmount` dışarı aktarılır |
| `bbd_students.directory` (zorunlu) | `list(since=<datetime>\|None)`, `get(id)` (yoksa `None`), `card_qr_text(id)` — QR'dan önce `get` ile varlık sınanır |
| `bbd_tablet.read` | `board()`, `student_usage(id, date_from=, date_to=)` (ISO gün) |
| `bbd_tablet.control` | `grant(id, package_name=, extra_seconds=, reason=, actor=)`, `block(id, until=<datetime\|None>, reason=, actor=)`, `unblock(id, reason=, actor=)`, `end_session(session_id, reason=, actor=)` — hata `reason`ı: `validation_error` → 422, `student_not_found`/`session_not_found` → 404, `canteen_unavailable` → 503 |
| `bell.week` | çağrılabilir: `() -> {"times", "groups"}` |
| `bell.announce` | `page(text, *, cache_key=, actor=, request_id=)` |

Kantin hataları `CanteenError.reason` ile sınıflanır: `no_debt`, `no_phone`,
`exceeds_debt`, `invalid_amount`, `validation_error`, `student_not_found`.

## Kendi tabloları (K5)

`mod_mobile_gateway_idempotency`, `_hits` (kilit ve hız sınırı sayaçları;
telefon sha256 özetiyle), `_sms_log` (maskeli), `_audit` (denetim izi,
`on_behalf_of` ve `request_id` ile). Süresi dolan satırlar on dakikada bir
temizlenir (idempotensi 24 saat, sayaç 1 gün, SMS kaydı 90 gün, iz 365 gün).

## Bilinen sınırlar

- Öğrencinin işlemleri kantinin aralık ucundan okunur; istemci `student_id`
  süzgeci tanımıyorsa aralıktaki en çok 5000 satır geçitte süzülür.
- Öğrenci ödeme talepleri kantinin son 100 talebinden süzülür.
