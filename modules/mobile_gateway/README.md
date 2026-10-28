# Mobil Geçit (`mobile_gateway`)

BBD Akademi API'sinin (öğrenci/veli/yönetici Flutter uygulamasının sunucusu)
Kontrol Merkezi'ne **sunucudan sunucuya** açılan tek kapısı. Ekranı yoktur.

- Bağlayıcı sözleşme (`bbd-akademi` deposu):
  `docs/integrations/km-mobile-gateway.md` (v1, "Ekler" dahil),
  `docs/integrations/kantin-veli-kontrolleri.md` §2.2,
  `docs/integrations/odeme-akademi-kaynagi.md` "Ek" (`payToken`),
  `docs/integrations/km-ogrenci-gruplari-ve-zil.md` §2.4, §3.1, §3.3
  (öğrenci grupları, saat setleri, zil planı),
  `docs/integrations/km-merkezi-senkron.md` §3 (olay iletimi, yeni uçlar) ve
  geçit belgesi §9–§10; API sözleşmesi `webhooks_km` / `KmEventBatch`.
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
| `Idempotency-Key` | Her POST/PATCH/PUT'ta kabul edilir; 24 saat saklanır. Aynı anahtar + aynı gövde → saklı yanıt (`Idempotent-Replayed: true`); farklı gövde → `409 idempotency_conflict`. Yalnız başarılı yanıt saklanır. |

Hata zarfı: `{"error": {"status", "code", "message", …}}` — `message` Türkçe;
kilit ve hız sınırında `retryAfterSeconds` (ve `Retry-After` başlığı),
doğrulamada `fields`, eksik yetenekte `capability`, kantinin tutar
redlerinde (`exceeds_debt`) `details: {balance, maxAmount}`.

`X-KM-On-Behalf-Of` HTTP başlığıdır ve ASCII olmak zorundadır: Türkçe ad
**yüzde kodlu UTF-8** gönderilir (`Ay%C5%9Fe`). Ham UTF-8 bayt da çözülür.

## Uçlar

| Uç | Yanıt | Hata kodları |
|---|---|---|
| `GET /directory?since=` | 200 dizin + `etag`, `If-None-Match` → 304; öğede `status`, `statusReason`, `statusSince`, `revision`, `canteenSync`, yeni öğrenci modülünde `classes: [{id, name}]` (bugün geçerli sınıflar — sınıf = grup §2.6; `className` eski tekil değer olarak kalır) (pasif öğrenci görünür; profil-yalnız `pending` satır `canteen: null`); `since` verilirse `removed: [{id, at}]` (90 günlük mezar taşı) | 422 (`since` 90 günden eski → istemci tam liste ister), 503 `capability_missing` |
| `GET /directory?ids=STU-a,STU-b` | 200 yalnız bu öğrenciler (≤ 100), `full: false`, `classes: []`, bulunamayanlar `missingIds`; ETag'siz | 422 (100'den çok / bozuk kimlik), 503 |
| `GET /directory?balance=false` | 200 öğrencilerde `canteen.balance` YOK, ETag bakiyesiz (okul saatinde satış ETag'i değiştirmez) | 422 |
| `GET /students/{id}` | 200 tekil öğrenci (dizin öğesiyle birebir; `balance=false` geçerli; `mobile_gateway.identity.read` ile ek `identity` bloğu — aşağıda) | 404 `student_not_found` (silinmişse de), 503 |
| `POST /auth/student-code` | 200 `{match, studentId?}` | 422, 423 `locked`, 429 `rate_limited`, 503 `canteen_unavailable` |
| `POST /students/{id}/verify-code` | 200 `{valid}` | 422, 423, 429, 503 |
| `GET /students/{id}/canteen?days=` | 200 bakiye, işlemler, tahsilat, bekleyen talep (+ veli kontrolleri) | 404 `student_not_found`, 422, 503 |
| `GET /students/{id}/canteen/transactions?from=&to=&cursor=&limit=` | 200 `{items, nextCursor, hasMore}` — satışlar yeniden eskiye, kalemlerde `productId` | 404, 422 (`limit` 1–100, `from`>`to`, bozuk/eskimiş `cursor`), 503 |
| `GET /students/{id}/canteen/collections?from=&to=&cursor=&limit=` | 200 `{items: [{id, at, amount, method, ref}], nextCursor, hasMore}` | 404, 422, 503 |
| `GET /students/{id}/canteen/qr` | 200 `{qrText, version: 1}` | 404 |
| `GET /students/{id}/payment-requests` (`?ret=` isteğe bağlı) | 200 `{items}` (`payToken`, `amountFixed` dahil) | 503 |
| `POST /students/{id}/payment-requests` · `POST /students/{id}/canteen/payment-requests` | 201 `{ref, amount, status, linkUrl, amountFixed, payToken}` | 409 `no_debt`/`no_phone`, 422 `exceeds_debt`/`invalid_amount`, 404 |
| `GET /students/{id}/canteen/controls` | 200 kontroller | 404, 503 |
| `PATCH /students/{id}/canteen/controls` | 200 kontroller | 422 `validation_error`, 404, 503 |
| `POST /students/{id}/canteen/collections` | 201 `{amount, balance}` | 422 `no_debt`/`exceeds_debt`/`invalid_amount`, 404 |
| `GET /canteen/payment-requests?since=&limit=` | 200 `{items, nextSince, hasMore}` | 422, 503 |
| `GET /canteen/parent-notifications?since=&limit=` | 200 `{items, nextSince, hasMore}` — SMS metni/telefon YOK | 422, 503 |
| `GET /canteen/balances?ids=` | 200 `{ledgerSeq, items: [{studentId, balance, ledgerSeq}]}`; `ids` yoksa bütün öğrenciler; ETag = kantin defterinin son kimliği, `If-None-Match` → 304 | 422, 503 `canteen_unavailable` |
| `GET /canteen/totals` | 200 `{totalDebt, spentToday, pendingPaymentRequests, blockedStudents, totalStudents, generatedAt}` — kurum geneli kantin sayıları (kuruş; açık alacak = pozitif bakiyeler toplamı, bugünkü satış iptaller hariç); kantin `GET /api/reports/dashboard` üstünde, 30 sn önbellek; yalnız kantin parası (kurum carisiyle toplanmaz) | 503 `canteen_unavailable` / `capability_missing` |
| `GET /canteen/settings/public` | 200 `{allowPrepayment, maxPrepayment}` (60 sn önbellek) | 503 |
| `GET /canteen/products` | 200 `{items}` (5 dk önbellek) | 503 |
| `GET /tablets` | 200 `{summary, devices}` | 503 `capability_missing`, 502 |
| `GET /tablets/usage?date=YYYY-MM-DD` | 200 `{date, items: [{studentId, totalSeconds, blockedUntil}]}` — kurum geneli; o gün kullanımı ya da şu an süren kısıtı olan öğrenciler; tarih yoksa bugün, en çok 62 gün geriye; tek yanıt, imleç yok | 422 (gelecek gün, 62 günden eski, biçim), 503 `capability_missing` (`bbd_tablet.read.daily_usage` yoksa) |
| `GET /students/{id}/tablet-usage?from=&to=` | 200 (en çok 62 gün) | 422, 404, 503 |
| `POST /students/{id}/tablet/grant` | 201 `{grantId}` | 422, 404, 503 |
| `POST /students/{id}/tablet/block` · `/unblock` | 200 `{blockedUntil}` | 422, 404, 503 |
| `POST /tablet-sessions/{id}/end` | 200 `{ended}` | 404 `session_not_found`, 409, 503 |
| `GET /students/{id}/tablet/limits` (tablet-sinir) | 200 `{localDate, profile{id,name,revision,updatedAt,usageWindows,deviceCount}\|null, apps[{packageName,appName,mode,dailyLimitSeconds,usedSeconds,extraSeconds,remainingSeconds,inProfile}], grants[…], blockedUntil, sync{activeSeconds,idleSeconds}}` — öğrencinin BUGÜNKÜ sınırları; profil son oturumun profilidir (KM'de sınır profile bağlı), önbellek yok | 404, 503 `capability_missing` (`bbd_tablet.read.student_limits` yoksa) |
| `PUT /tablet-profiles/{id}/limits` (tablet-sinir) | 200 `{id, name, revision, updatedAt}` — gövde `{expectedRevision, apps?[{packageName, mode: blocked\|unlimited\|limited, dailyLimitSeconds}], usageWindows?, reason}`; yalnız profilde olan uygulamalar, anılmayan korunur; olay `tablet.profile_changed`, denetim `tablet.profile.limits` | 409 `profile_conflict` (revizyon değişti), 404 `profile_not_found`, 422, 503 |
| `GET /tablet-profiles` (tablet-sinir) | 200 `{items: [{id, name, revision, updatedAt, usageWindows, deviceCount, apps[{packageName, appName, mode, dailyLimitSeconds}]}]}` — GENEL süre ayarları (bütün profiller) | 503 |
| `PUT /students/{id}/tablet/app-limits/{package}` (tablet-sinir) | 200 `{packageName, studentLimitSeconds, previousSeconds}` — gövde `{dailyLimitSeconds: int\|null, reason?}`; öğrenciye özel günlük süre (genelden uzun olmalı; `null` = genele döndür); olay `tablet.policy_changed` (`limits`), denetim `tablet.student_limit` | 422 (genelden kısa, profilde süreli değil), 404, 503 |
| `POST /students/{id}/tablet/grants/{grantId}/revoke` (tablet-sinir) | 200 `{grantId, revoked, alreadyRevoked}`; olay `tablet.policy_changed` (`grants`), denetim `tablet.grant.revoke` | 404 `grant_not_found`, 422, 503 |
| `POST /announcements/call` | 202 `{status, voiceCached, etaSeconds}` | 409 `bell_disabled`, 503 `tts_unavailable`, 429, 404, 503 `capability_missing` |
| `GET /schedule/bell-week` | 200 — `bbd_class_schedule` `/week` biçimi + `profiles` (saat setleri `[{id, name, times: [{time, label, kind}]}]`, eski zilde boş); zil yoksa `available: false` + `reason`. `ETag`, `If-None-Match` → 304 | — |
| `GET /groups?since=<revizyon>[&include=classes]` | 200 `{generatedAt, revision, full, complete, groups: [{id, name, callName, kind, archived, archivedAt, updatedAt, revision, members: [{studentId, validFrom, validTo}]}], etag}`; `If-None-Match` → 304. `since` yoksa (ya da KM'nin revizyonundan büyükse) tam liste, `full: true`. **Sınıf = grup (km-ogrenci-gruplari-ve-zil §2.6):** `include=classes` → tepede `classesIncluded: true`, bütün sınıflar (öğrencinin eski `className`'inden dönüşenler dahil), kayıtta ek `legacyClassName` + `classLevel {gradeLevel, track, examTarget} \| null`; parametresiz yanıt birebir eskisidir (dönüşen sınıflar ve dönüşüm üyelikleri YOK — eski Akademi). İki görünümün önbelleği ve ETag'i ayrı; eski öğrenci modülü `view` almıyorsa `classesIncluded` yazılmaz | 422 (`since` tamsayı değil, `include` `classes` değil), 503 `capability_missing` |
| `PUT /bell/plan` | 200 `{days: [{date, result: applied\|unchanged\|stale\|overridden\|rejected, revision[, error: unknown_profile\|revision_conflict, details: {profileId}]}], voicesPending, bellEnabled}` — günler bağımsız uygulanır; reddedilen gün yanıtı 200 bırakır | 413 `payload_too_large` (> 512 KB), 422 `validation_error` (`fields` + `details.path`, eksik günde `details.missing`), 409 `idempotency_conflict`, 503 `capability_missing` |
| `GET /bell/plan/status?from=&to=` | 200 `{bellEnabled, sessionCallsEnabled, agent: {online, lastSeenAt}, days: [{date, mode, profileId, reason, source: akademi\|km\|none, revision, planHash, calls, voicesReady, voicesPending, lastDispatchAt, override, lastResult, lastError}]}`; varsayılan bugünden 14 gün, en çok 31; `ETag`, `If-None-Match` → 304 | 422, 503 `capability_missing` |
| `POST /sms/otp` | 200 `{sent: true, jobId?, channel?}` | 422, 429, 502 `sms_failed`, 503 |
| `GET /sms/otp/{jobId}` | 200 `{jobId, status: pending\|delivered\|failed, channel, sentAt, deliveredAt, deliverySeconds, checkedAt}` | 404 `sms_not_found`, 422 |
| `GET /sms/health` | 200 son 1 saat `{sent, delivered, deliveryFailed, pending, sendFailed, rateLimited, avgDeliverySeconds, maxDeliverySeconds, balance: {ok, smsCount}}` | — |
| `POST /sms/limits/reset` | 200 `{reset, clearedHour, clearedDay}` (denetimli) | 422 |
| `POST /sms/send` | 200 `{sent: true, segments, jobId?, channel?}` — kurum SMS'i (`purpose`: `announcement` \| `payment_reminder` \| `exam_result`) | 403 `sms_disabled`, 422, 429 `rate_limited` / `sms_phone_limit` / `sms_budget_exceeded`, 502 `sms_failed`, 503 |
| `GET /events?after=&limit=` | 200 `{items: [KmEvent], lastSeq, hasMore}` — KM outbox'undan çekme (webhook'un yedeği; zarf webhook ile aynı), `limit` 1–500 | 410 `events_expired` (`after` 14 günlük saklamadan eski → tam uzlaştırma), 422, 503 `capability_missing` (`events.outbox`) |
| `GET /settings/public` | 200 kurum adı, saat dilimi, özellikler | — |

**Ödeme dönüşü (`ret`).** Gövdede (`POST`) ya da sorguda (`GET … ?ret=`) gelen web dönüş adresi
(Akademi web istemcisinin kökeni + `/payment-result`, en çok 512 karakter) kantinin ödeme talebi
isteğine iletilir; kantin onu `payToken`'a yalnız `PAYMENT_APP_RETURN_URLS` listesindekiyle birebir
aynıysa koyar, değilse listenin ilkini (kantin `ozellik/odeme-donus`). KM yalnız biçimi süzer
(`https://`, boşluksuz); SMS linki `ret` taşımaz. `ret` gönderilmezse davranış değişmez.


**Sözleşme kimliği (iş 80-km-kimlik, 28.10.2026; bbd-akademi 80-ortak K2).** Çağıran anahtar
`mobile_gateway.identity.read` taşıyorsa `GET /directory` (tam, `since`, `ids`) ve
`GET /students/{id}` öğrencilerine EKLEMELİ `identity` bloğu gelir:
`{updatedAt, student: {nationalId, foreignId, birthDate, gender (female|male), registeredOn,
schoolName, homePhone, motherName, fatherName, address{line,district,city}|null}, guardians:
[{slot 1..4, fullName, nationalId, foreignId, relation, phone, occupation, workPhone, email,
homeAddress|null, workAddress|null}]}` (camelCase; alan yoksa `null`). Kaynak
`bbd_students.directory.identities(records)` — TEK çağrı, N+1 yok; beyaz liste
`service.identity_view`. Blok gelmezse (kasa okunamadı, eski öğrenci modülü) anahtar HİÇ
yazılmaz → Akademi aynası değişmez. İzni taşımayan anahtarın yanıtı, önbelleği ve ETag'i
birebir eskisidir (önbellek anahtarı `(balance, identity)`). Kimlik değişince öğrencinin
`revision`'ı artar ve `student.updated` (`fields: ["identity"]`) çıkar; Akademi 1–3 sn'de
`GET /students/{id}` / `GET /directory?ids=` ile yeniden okur. Değer log'a ve olaya yazılmaz.
İzin `default_roles: [admin, mobile_service]`: kurulu sistemde açılışta `grant_defaults`
`mobile_service` rolüne ekler; anahtar başka role bağlıysa o role elle verilir.

**Veliler (veli-senkron-4).** Öğrenci nesnesinin `guardians[]` öğesi
`{slot: 1..4, name, phone, relation, primary}`: yuva 1 birincil (telefonu
kantinde), 2 profil, 3–4 `bbd_students` ek velileri (göç 007). `relation`
beyaz listeden (`mother | father | guardian | grandparent | sibling | other`,
yoksa `null`), `primary = slot == 1`. Telefonu olmayan veli listelenmez.
`POST /auth/student-code` dört velinin telefonuyla da eşleşir. Olaylar:
yuva 1–4 telefonu `guardian.linked | unlinked | phone_changed` (`data.slot`
1..4), ad / yakınlık `student.updated` (`["guardians"]`). Eski istemci yeni
alanları yok sayar (geriye uyumlu).

## Olay iletimi (KM → Akademi, merkezi senkron)

KM tek merkezdir: öğrenci, veli, kantin kontrolü, ödeme talebi, tablet ve zil
değişiklikleri iş yazmasıyla aynı işlemde platform outbox'una (`events.outbox`,
ADR 0029) yazılır. Bu modül iki iş yapar:

1. **Yazma uçlarında olay** (`origin: akademi`, `causationId` = isteğin
   `Idempotency-Key`'i, yoksa `X-Request-Id`): `PATCH …/canteen/controls` →
   `canteen.controls_changed` (kantin yankısı bastırılır), ödeme talebi →
   `canteen.payment_request_changed`, tablet grant / block / unblock ve oturum
   bitirme → `tablet.policy_changed`. Nakit tahsilatın ayrı geçit olayı YOKTUR:
   bakiye değişimi kantinin defter olayından `canteen.balance_changed` olarak
   gelir (belge §8). Anons ve zil planı olay üretmez. Geçidin kurum ayarı
   (`/settings/public` içeriği) değişince `institution.settings_changed`.
2. **Gönderici** (`backend/events.py`, modül kaydında `asyncio` görevi):
   `akademi` imleciyle `wait_after(25 sn)` → ≤ 100 olay → 200 ms pencere →
   birleştirme (aynı `(type, subject)` → son; yaşam döngüsü birleştirilmez) →
   `POST {akademi_base_url}/v1/webhooks/km`, başlıklar `X-KM-Tenant` ve
   `X-KM-Signature: t=<unix>,v1=<hex>` (HMAC-SHA256, imzalanan `"<t>.<ham
   gövde>"`). 2xx → imleç ilerler; 401/404 → DURUR (5 dk'da bir yoklar, sır
   düzelince kendiliğinden sürer); 400/422 → tek tek, hâlâ reddedilen ölü
   mektuba; 429/5xx/ağ → 1→60 sn üstel geri çekilme (±%20, `Retry-After`).
   6 saat ulaşılamazsa durum `unreachable`; imleç 14 günlük saklamanın dışına
   düşerse `directory.resync_required`. En çok 10 istek/sn. Zarfta kişisel veri
   yoktur (telefon, ad, kod gitmez — `fields` yalnız değişen alanların adı).

**Ayarlar** (`config/default.yaml`): `events_enabled` (**varsayılan kapalı** —
kapalıyken hiç istek atılmaz ve tüketici kaydı silinir, outbox budaması
beklemez), `akademi_base_url` (varsayılan `https://api.benimbasaridunyam.com.tr`),
`akademi_tenant` (`X-KM-Tenant`, ör. `bbd`; boşsa açık olsa da gönderilmez),
`events_instance_id`. **Sır** kasada `mobile_gateway.webhook_secret` (K8;
Akademi'de kiracının `km` entegrasyonuna girilen sırla aynı).

**Açma sırası** (RELEASE_NOTES_INTEGRATION "Yayın sırası"): Akademi API
`webhooks_km` ile yayında → Akademi'de webhook sırrı girildi → KM kasasına aynı
sır `mobile_gateway.webhook_secret` → `akademi_tenant` → `events_enabled: true`.
Sıra bozulursa hiçbir şey kırılmaz: Akademi 2 dk yoklamayla (`GET /directory
?since=`, `GET /events`) çalışır, yalnız gecikme artar.

**Durum yeteneği** `mobile_gateway.events` (`status()`, `dead(limit)`,
`retry_dead(seqs)`, `replay_from(seq)`): KM panelinde *BBD Akademi → Ayarlar →
Senkron* kartı bunu Akademi'nin `admin_getDirectorySyncStatus.events` bloğuyla
yan yana gösterir. İzinler: `mobile_gateway.events.view` (admin, bbd_staff),
`mobile_gateway.events.manage` (admin) — servis anahtarına VERİLMEZ. Metrik:
`deliveryLagMs` (commit → istek, ölçüm ~200 ms; bütçe 300 ms).

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
- `/sms/otp`: metin `otp_template` ayarından üretilir, istemci metin yazamaz; metin ASCII'ye
  indirilir (Netgsm OTP hattı Türkçe karakter almaz). Kantine `purpose="otp"` gider: kantin önce
  Netgsm OTP SMS hattını (öncelikli, 7/24) dener, kapalıysa olağan hatta düşer. Telefon başına
  saatte 5 ve günde 10, toplam dakikada 60. Kayıt `mod_mobile_gateway_sms_log` (maskeli) — sms-kale
  ile `job_id`, `channel`, `delivery` (pending/delivered/failed), `delivered_at`, `checked_at`
  (göç 003). `GET /sms/otp/{jobId}` kantinin Netgsm raporunu jobId başına en çok 55 sn'de bir sorar
  ve kesin sonucu saklar; `POST /sms/limits/reset` telefon sayaçlarını sıfırlar (Akademi
  `admin_resetSmsLimits`).
- `/sms/send` (bbd-akademi README §14 madde 81, duyuruda "SMS olarak da gönder"): veli telefonuna
  serbest metin, kantinin Netgsm hesabıyla **olağan hat** (`purpose` kantine gitmez). Metin ASCII'ye
  indirilir (Türkçe harf parçayı 160'tan 70'e düşürür), en çok 459 birim (3 SMS). KM'nin duyuru SMS
  bütçesi `announcement_sms`: son 24 saatte toplam `daily_budget` (2000) SMS parçası → 429
  `sms_budget_exceeded`; telefon başına günde `per_phone_per_day` (5) → 429 `sms_phone_limit`; dakikada
  `per_minute` (120) → 429 `rate_limited` (Akademi kuyruğu bekleyip yeniden dener); `enabled: false` →
  403 `sms_disabled`. `Idempotency-Key` (Akademi: alıcı satırı) aynı SMS'in ikinci kez gitmesini önler.
  Kayıt `sms_log`'a `purpose` ile (maskeli, metin yazılmaz); `/sms/health` OTP özeti bunları saymaz.
  Madde 88: `purpose` ayrıca `payment_reminder` (ödeme hatırlatması) ve `exam_result` (deneme sonucu) —
  hepsi aynı bütçeden; `/sms/health` yanıtında `textSms {enabled, dailyBudget, used, remaining,
  perPhonePerDay}` (Akademi Ayarlar › SMS bütçe kartı ve duyuru önizlemesi okur); `sms_disabled`
  `retryAfterSeconds: 3600` taşır. Akademi bütçe / telefon sınırı / kapalı yanıtında SMS'i kaybetmez, bekletir.
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
- Akademi olay alıcısı: `akademi_base_url: https://api.benimbasaridunyam.com.tr`
  (yalnız `events_enabled: true` iken kullanılır)

Staging/test KM kurulumu bu geçidi açmadan önce ikisini de ezmelidir, yoksa
Akademi'nin staging ortamı canlı kantine ödeme talebi açar, canlı velilere
SMS gönderir ve okulda gerçek anons çalar:

```bash
KM__modules__bbd_canteen_api__base_url=https://<staging-kantin>
KM__modules__bell__bridge__base_url=https://<staging-köprü>
KM__modules__mobile_gateway__akademi_base_url=https://<staging-akademi>
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
| `canteen.api` (zorunlu) | `lookup_student_by_code(code)`, `ledger(id, limit=, cursor=, from_ms=, to_ms=)` (yoksa ya da `reason="ledger_unsupported"` verirse eski yol), `get_student(id)` (yoksa `student(id)`), `transactions(from_ms=, to_ms=, limit=[, student_id=])`, `collections(from_ms=, to_ms=, student_id=)`, `payment_requests()`, `create_payment_request(id[, amount=])`, `update_controls(id, patch)`, `record_collection(id, amount=, local_id=)` (yoksa `collect_cash`), `products()`, `payment_requests_feed(updated_since=, limit=)`, `parent_notifications_feed(updated_since=, limit=)`, `canteen_settings_public()`, `send_sms(phone=, message=[, purpose="otp"])` (tanıyorsa `purpose` gönderilir), `sms_report(job_id)`, `sms_balance()` (sms-kale; yoksa teslim `pending`, paket `ok: false`) — `create_payment_request` `origin` parametresini tanıyorsa `origin="api"` gönderilir; akışlar liste ya da `{data, nextSince, hasMore}` dönebilir; `CanteenError` red gövdesini `details`/`body` alanında taşırsa `balance`/`maxAmount` dışarı aktarılır |
| `bbd_students.directory` (zorunlu) | `list(since=<datetime>\|None)`, `get(id)` (yoksa `None`), `card_qr_text(id)` — QR'dan önce `get` ile varlık sınanır; `identities(records)` (yoksa `identity` bloğu yazılmaz — yalnız `mobile_gateway.identity.read` taşıyan anahtara) |
| `bbd_tablet.read` | `board()`, `student_usage(id, date_from=, date_to=)` (ISO gün), `daily_usage(local_date)` (ISO gün; yoksa yalnız `GET /tablets/usage` 503), `student_limits(id)` (yoksa yalnız `GET /students/{id}/tablet/limits` 503), `profiles()` |
| `bbd_tablet.control` | `grant(id, package_name=, extra_seconds=, reason=, actor=)`, `block(id, until=<datetime\|None>, reason=, actor=)`, `unblock(id, reason=, actor=)`, `end_session(session_id, reason=, actor=)`, `update_profile_limits(profile_id, expected_revision=, apps=, usage_windows=, reason=, actor=)`, `revoke_grant(id, grant_id, reason=, actor=)`, `set_student_app_limit(id, package, daily_limit_seconds=, actor=, reason=)` — hata `reason`ı: `validation_error` → 422, `student_not_found`/`session_not_found`/`profile_not_found`/`grant_not_found` → 404, `profile_conflict` → 409, `canteen_unavailable` → 503 |
| `bell.week` | çağrılabilir: `() -> {"times", "groups"[, "profiles", "days"]}` — `days` geçitten dönmez |
| `bell.announce` | `page(text, *, cache_key=, actor=, request_id=)` |
| `bell.plan` | `apply_days(body, *, actor=)`, `status(from, to)` (ISO gün) — kural reddi `PlanError(status, code, details={path})` olarak gelir ve aynı kodla zarfa geçer; `actor` = `KM servis kullanıcısı (X-KM-On-Behalf-Of)`, Akademi `akademi:bell-plan` yazar |
| `bbd_students.groups` | `list(since_revision=[, view="classes"])`, `revision()` (yoksa önbellek süreyle sınırlanır); `view` parametresi yoksa (eski modül) `include=classes` eski yanıtı verir |
| `events.outbox` (platform) | `emit(...)`, `read_after(seq, limit)`, `last_seq()`, `wait_after(seq, timeout)`, `register_consumer(name, start=)`, `unregister_consumer(name)`, `consumer_seq(name, seq)`, `oldest_pending_age(seq)`, `expired(seq)` — yoksa olay yazılmaz, `GET /events` 503, gönderici çalışmaz |

Dinlenen olaylar: `bell.week_changed` (zil haftası önbelleği), `bbd_students.groups_changed`
(grup listesi önbelleği). İkisi de yalnız önbellek bırakır; olay gelmese de revizyon ya da
süre sınırı tazeliği korur.

Kantin hataları `CanteenError.reason` ile sınıflanır: `no_debt`, `no_phone`,
`exceeds_debt`, `invalid_amount`, `validation_error`, `student_not_found`;
`ledger_unsupported` hata değildir, eski yola dönüş işaretidir.

## Kantin hesap dökümü (kantin §1.7)

Öğrencinin kantin uçları kantinin `GET /api/students/{id}/ledger` ucundan
(`canteen.api` → `ledger()`) okunur:

- **Özet** (`/students/{id}/canteen`): ilk defter sayfası öğrenciyi (bakiye,
  kontroller), bekleyen talebi ve satırları birlikte verir — TEK kantin
  isteği (eskiden dört). `from` = şimdi − `days`, `to` gönderilmez (kantin
  ilk sayfanın bakiyesini ek sorgusuz hesaplar). 200 satırı aşan aralıkta kalan
  kısım ilk sayfanın yoğunluğuna göre en çok 4 zaman dilimine bölünür ve
  dilimler AYNI ANDA okunur (en yeni dilim kantinin imleciyle; satırlar
  defter kimliğiyle tekilleşir; toplam en çok 50 sayfa). Satış satırı iptal
  edildiyse `reversed: true` olur; iptal kaydı ayrı satır olmaz. Tahsilat =
  defterde `payment` (online, `PAYMENT`) ve `cash` (`CASH`) satırları.
- **İmleçli listeler** (`/canteen/transactions`, `/canteen/collections`):
  `from`/`to` kurum saat diliminde gün (dahil), `limit` 1–100 (varsayılan 20).
  `nextCursor` geçidin opak imlecidir (base64url JSON, kişisel veri yok):
  kantinin `nextCursor`'unu OLDUĞU GİBİ taşır, sayfa bir kantin sayfasının
  ortasında dolduysa son verilen defter satırının kimliğini de. Sayfalar
  arasında gelen yeni satış sırayı kaydırmaz, satır tekrar etmez. Seyrek
  türde (çok satış, az tahsilat) bir istek kantine en çok 5 kez gider; dolmazsa
  kısa sayfa + geçerli imleç döner. Akademi `CanteenTransactionPage` /
  `CanteenCollectionPage` `cursor`'u bu imleçtir.
- **Eski kantin** (defter ucu yok; istemci bunu 10 dk hatırlar) ya da `ledger`
  bilmeyen istemci: özet eski dört okumalı yoldan (aynı yanıt, alan alan);
  listeler aralığı okuyup sıra numaralı imleçle böler (`from` yoksa son 180
  gün). Kip değişirse (kantin güncellendi / geri alındı) eski imleç
  `422 validation_error` (`cursor`) alır; istemci listeyi baştan ister.

## Kendi tabloları (K5)

`mod_mobile_gateway_idempotency`, `_hits` (kilit ve hız sınırı sayaçları;
telefon sha256 özetiyle), `_sms_log` (maskeli), `_audit` (denetim izi,
`on_behalf_of` ve `request_id` ile), `_event_cursor` (gönderici imleci ve
durumu), `_event_dead` (ölü mektup — zarf kişisel veri taşımaz), `_meta`
(kurum ayarı özeti; göç 002). Süresi dolan satırlar on dakikada bir
temizlenir (idempotensi 24 saat, sayaç 1 gün, SMS kaydı 90 gün, iz 365 gün).

## Bilinen sınırlar

- Defter ucu olan kantinde (§1.7) kurum geneli satır kesintisi YOKTUR:
  öğrencinin satırları kendi `(student_id, id)` indeksinden sayfa sayfa gelir.
  Özetin tek sınırı 50 × 200 = 10.000 defter satırıdır (aşılırsa log uyarısı).
- Aralığında 200'den çok defter satırı olan öğrencinin özeti iki kantin
  turu sürer (ilk sayfa, sonra paralel dilimler): ölçümde 40 ms kantin
  gecikmesiyle 450 satışlı öğrenci p50 93 ms (eski dört paralel okuma 56 ms;
  bütçe 300 ms). Tipik öğrenci (30 günde ~12 satış) tek turdadır.
- Yalnız defter ucu OLMAYAN eski kantinde: işlemler ve talepler kantine
  `studentId` süzgeciyle istenir; parametreyi de tanımayan kantinde işlemler
  aralıktaki en çok 5000 satırdan, talepler kurum genelindeki son 100
  talepten geçitte süzülür. Satırlar her sürümde geçitte de süzülür
  (başkasınınki sızmaz). Bu yolda imleçli listeler bilinmeyen öğrenciye 404
  değil boş sayfa verir (öğrenci ayrıca sorgulanmaz).
- Defter `from`/`to`'su deftere yazılma anıdır; satışın cihaz saati
  (`createdAt`) çevrimdışı kuyruktan geç gelen satışta biraz daha eski olabilir.

## Hız (PERFORMANCE.md A14)

- **Tek üst çağrı.** Kantin özeti ve imleçli listeler kantinin hesap dökümü
  ucundan okunur: özet 1 kantin isteği (eskiden 4, biri kurum geneli 5000
  satırlık işlem aralığı), liste sayfası 1 (seyrek türde en çok 5). Aralığı
  tek sayfaya sığmayan öğrencide özet 1 + en çok 4 paralel dilim: süre
  ~2 kantin turu (sıralı sayfalamada sayfa sayısı kadar tur olurdu).
- **Paralel okuma.** Eski kantinde özet dört kantin okumasını (öğrenci,
  işlemler, tahsilat, talepler), QR ucu dizin sınaması ile QR metnini AYNI
  ANDA ister; hata önceliği sıralı koddaki gibidir.
- **Süre sınırı.** Her yetenek çağrısı `timeouts.read_seconds` (5) /
  `timeouts.write_seconds` (20) ile sarılır; dolarsa 503 (kantin/dizin için
  `canteen_unavailable`). Kantin istemcisinin yeniden denemeleri geçidi
  dakikalarca bekletemez.
- **Önbellek.** Dizin yanıtı (tam liste) dizin yeteneğinin `revision`ı
  değişmedikçe yeniden kurulmaz; geçit kantine kontrol/tahsilat yazınca dizinin
  `invalidate()`ı çağrılır. Zil haftası 60 sn (`bell_week_cache_seconds`)
  tutulur ve Zil Sistemi'nin `bell.week_changed` olayıyla hemen bırakılır.
  Ön ödeme ayarı 60 sn (`canteen_settings_cache_seconds`), ürünler 5 dk.
- **Gruplar** (`GET /groups`): tam liste (200 grup, 4.000 üyelik, ≈ 330 KB ham,
  gzip ≈ 30 KB) grup revizyonu değişmedikçe yeniden kurulmaz; her istek yalnız
  yeteneğin `revision()`ını okur (tek satır). Ölçüm: soğuk < 150 ms, sıcak < 30 ms.
  `since` yanıtı istemciye özgüdür, önbelleğe girmez.
- **Zil planı**: `PUT /bell/plan` geçitte yalnız gövde okuma + denetim izi (1 ifade);
  850 çağrılık 14 gün geçit payı < 300 ms. Durum ucu önbelleksiz (zilin yerel
  tabloları), ETag ile uzlaştırma çoğunlukla 304.
- **Sorgu bütçesi** (`tests/test_hiz.py`): okuma uçları geçidin kendi
  tablolarına 0 ifade; idempotensi anahtarsız yazma 1 (denetim izi),
  anahtarlı en çok 5. Kantin üst çağrı bütçesi: özet ve liste sayfası 1
  `ledger` isteği; eski kantinde özet 1 deneme + 4 okuma (deneme istemcide
  10 dk hatırlanır, sonrakiler yalnız 4).
