# BBD Akademi yönetimi (`bbd_akademi`)

BBD Akademi'nin **tam yönetim konsolu**. Kullanıcı kararı: Kontrol Merkezi
çekirdektir ve en yetkili uygulamadır; Akademi'nin her yönetim işi buradan
yapılabilir, mobil yönetici uygulaması pratik bir alt kümedir.

İş kuralı bu modülde **değildir**, Akademi API'sindedir. Modül Akademi'nin
`/v1/admin/*` uçlarını (ve öğrenci görünümü için salt okunur
`/v1/students/{id}/*`, `/v1/catalog/*` uçlarını) sunucu tarafında, **izin
listesiyle sınırlı bir vekil** üzerinden, Akademi entegrasyon anahtarıyla
çağırır. Akademi verisinin kopyası tutulmaz.

Bağlayıcı sözleşme: `bbd-akademi/docs/integrations/km-akademi-paneli.md` ·
API: `bbd-akademi/docs/api/openapi.yaml` (+ `docs/api/README.md` §3 izin kataloğu).

```
KM masaüstü paneli (ui/panel)          KM oturumu (PIN), izin: bbd_akademi.*
      │  POST /api/bbd_akademi/km/call  (köprü: JSON zarf, dosya base64)
      ▼
KM backend  /api/bbd_akademi/{path}     izin listesi + KM izni (çift kapı, K9)
      │  Authorization: Bearer aksk_…   (kasa: bbd_akademi.api_key)
      │  X-Akademi-On-Behalf-Of: km:user:<id>:<ad soyad, yüzde kodlu>
      │  X-Request-Id · Idempotency-Key · X-Akademi-Step-Up: km-pin
      ▼
Akademi API  /v1/admin/*
```

## Kurulum

1. **Akademi'de entegrasyon anahtarı oluşturun** (Akademi sunucusunda, bir kez):

   ```bash
   python -m akademi.cli create-integration-key --tenant <kiracı> --name km-panel --preset km_panel
   ```

   Tam anahtar (`aksk_<önek>_<sır>`) **yalnız bir kez** yazdırılır. `km_panel`
   kümesi yalnız-KM izinleri + ortak yönetici izinlerinin tamamıdır
   (`docs/api/README.md` §3). İlk anahtar CLI ile oluşturulur; sonrakiler
   (yenileme, başka entegrasyonlar) bu panelin Ayarlar → Entegrasyon
   anahtarları sekmesinden oluşturulup iptal edilebilir.

2. **Anahtarı KM sır kasasına yazın** — adı `bbd_akademi.api_key` (K8; depoda,
   ayarda, logda değer yoktur). İki geçerli yol:
   - kasanın kendisi (`km_platform/secrets`, `Vault.set`), ya da
   - makineye özel, git dışı `config/local.yaml`:

     ```yaml
     secrets:
       bbd_akademi.api_key: "aksk_…"
     ```

   Merkezden dağıtım (ADR 0025): anahtar adı `scripts/push-secrets.py` →
   `SECRET_KEYS` listesindedir; merkez kasasına yazılan anahtar kurulumlara
   betikle dağıtılır.

3. **Ayarlar** (`config/default.yaml`, şema `config/schema.json`): `enabled`
   (ana şalter), `base_url` (varsayılan `https://akademi-api.bbdstore.com.tr`),
   `timeout_seconds`, `upload_timeout_seconds`, `max_upload_mb`,
   `download_hosts` (imzalı indirme bağlantılarının izinli ek alanları),
   `staff_sync_minutes`, `step_up_seconds`.

4. **Hazırlık (staging) ortamı:** `config/local.yaml` içinde
   `modules.bbd_akademi.base_url` hazırlık sunucusuna çevrilir ve hazırlık
   kiracısının anahtarı kasaya yazılır. Canlı anahtar hazırlıkta, hazırlık
   anahtarı canlıda kullanılmaz (Akademi anahtarı kiracıya bağlar). Panel
   açılışında anahtar yoksa ya da şalter kapalıysa ekranın tepesinde sebep
   yazar; vekil `503 akademi_not_configured` / `akademi_disabled` döner.

## Bölümler

Panel bölümleri `ui/panel/index.js` → `SECTIONS` dizisindedir (bölüm = bir
dosya + bir satır; ilk açılışta yüklenir). Görünürlük KM iznine, her düğme
işlemin izinli olup olmadığına göre çizilir; asıl kapı vekildedir.

| Bölüm | Dosya | İçerik |
|---|---|---|
| Panel | `dashboard.js` | Günün dersleri, yoklama, kurumda olanlar, bekleyen talep ve kitap; kurum carisi ve kantin AYRI kartlarda; tablet panosu ve kullanım; öğrenci çağırma |
| Öğrenciler | `students*.js` | Akademi öğrenci listesi (seviye/alan/hedef salt okunur — Öğrenciler modülünde düzenlenir), öğrenci detayı: özet, program, müfredat ve ilerleme, kaynaklar, ödevler, denemeler (karne), yoklama, çalışma, öğrenme (plan, hata defteri, soru, okuma, ajanda — F-A24), hedef & özet, koçluk (koç notları, değerlendirmeler, öz değerlendirme, erken uyarılar), bilgi kartları, talepler, kurum cari, kantin (salt okunur); sınıflar, çalışma grupları, veliler |
| Müfredat | `curriculum.js` | Katalog (seviye/sınav/ders), kuruma özel program/ünite/konu, sürümler, program çözümleme, öğrenci/sınıf istisnaları |
| Kaynaklar | `resources*.js` | Hazır kütüphane, kurum kaynakları (ISBN, mağaza kataloğu), içindekiler ve konu eşlemesi, "konulardan bölüm üret", paketler + otomatik atama + şimdi uygula, atamalar, ilerleme tablosu |
| Ders programı | `timetable*.js` | Zil planı durumu kartı (14 gün: kaynak, bugünün kipi, çağrı ve ses, zil ajanı, acil kapatma; "Planı şimdi it"), zil dilimine hizalı haftalık ızgara (çakışma gösterimi, kopyalama), etüt ve tek seferlik dersler, zil dilimleri, dönemler |
| Kurum takvimi | `institution-calendar.js` | Tatil, etkinlik, veli toplantısı, deneme günü, kayıt dönemi; hedef kitle, bağlı duyuru ve hatırlatma (F-A20); "Zil" alanı (normal / tatil — zil çalmaz / KM saat seti) ve "dersleri iptal et" — sözleşme şalteri açılana dek devre dışı |
| Yoklama | `attendance.js` | Günün oturumları, yoklama listesi ve düzeltme |
| Ödevler | `homework.js` | Ödev ver (sınıf/grup/öğrenci), teslim tablosu, hatırlatma |
| Denemeler | `exams*.js` | Tanımlar, geçmiş deneme, toplu yükleme sihirbazı (yükle → sütunlar → eşleme + yayınevi şablonları → önizleme ve belirsiz eşleşme çözümü → kaydet / geri al), cevap anahtarı ve soru→konu eşlemesi, tekil sonuç, yayımla, analiz, karşılaştırma, dışa aktarım |
| Çalışma planı | `plan*.js` | Plan görevi atama (öğrenci/grup/sınıf), plan şablonları ve sınıfa uygulama, uyum tablosu, erteleme talepleri (F-A18) |
| Okuma | `reading*.js` | Kurum okuma listeleri, hazır listeler (tür süzgeci; hazır şablondan oluştur), okuma ligi, öğrenci × ay raporu (F-A19) |
| Bilgi kartları | `decks.js` | Kurum desteleri (ders/konu, görselli kartlar), sınıf/grup/öğrenci ataması (F-A23) |
| Değerlendirmeler | `evaluations*.js` | Şablonlar (bölüm, kriter, ölçek, otomatik veri), dönemler ve tamamlama durumu, değerlendirme formu (otomatik veri yan paneli), yayımla / geri al, gelişim karnesi PDF (F-A21) |
| Erken uyarı | `insights*.js` | Kurallı risk listesi ve eylemler (not, görüşme, veliye bildirim, çöz / yeniden aç), eşikler, en çok hata yapılan konular ve nedenler (F-A22) |
| Kurum finansı | `finance.js` | Cari hesaplar, peşinatlı taksit planı, kalemler, tahsilat (seçili kalem / serbest tutar FIFO), PIN teyitli iptal, gecikenler + hatırlatma, ekstre ve makbuz — **kantinden tamamen ayrı (A12)** |
| Kitap talepleri | `books.js` | Kuyruk, siparişi yeniden dene, teslim, sipariş PDF'i |
| Duyurular | `announcements.js` | Taslak → hedefle → şimdi ya da zamanlı yayımla, okunma oranı |
| Talepler | `requests.js` | İzin, görüşme ve etüt talepleri |
| Ayarlar | `settings*.js` | Kurum ve bildirim varsayılanları, kantin kuralları (F-A15), logodan tema + önizleme + yayımla (F-A16), özellik bayrakları, anons şablonları, entegrasyon anahtarları, dizin eşitleme, denetim kaydı |
| Personel | `staff.js` | Mobil yönetici erişimi (modülün kendi listesi ve anons adı "Hüseyin hoca", Akademi'ye itilir), Akademi personeli, roller ve izin kataloğu |

Kapsam ölçümü: `tools/ui_coverage.py` izin listesindeki her işlemin panelde
nerede karşılandığını yazar; ekranda bilerek olmayan işlemler gerekçesiyle
`NOT_SURFACED` tablosundadır ve test (`tests/test_akademi_panel_coverage.py`)
bunu bağlar. Sözleşmeye yeni girmiş, ekranı henüz yazılmamış işlemler ayrı
`PENDING` ("sırada") tablosundadır — aynı kurallarla, gerekçeli.

**Kapsam (sözleşme 1.0.0-draft.3, bbd-akademi 2d91b96):** izin listesinde 469
işlem — 372 `/v1/admin/*` (x-surface `both` 224, `km` 148) + 97 salt okunur
`/v1/students/{id}/*` ve `/v1/catalog/*`. Ekranda **341**, bilerek yok **4**
(gerekçeleri `NOT_SURFACED`'da: personel itmesi modül servisinde, kantin
tahsilatı ve kontrolleri Öğrenciler modülünde, öğrenci dışa aktarım durumu;
çalışma grubu yazma uçları sözleşmeden kalktı), sırada **124** (draft.3'ün
program kurucu, ders oturumları, grup üyelik görünümü, yoklama, koçluk ve
mesajlaşma denetimi işlemleri; `PENDING`), eksik **0**. Bölüm başına
karşılanan işlem (bir işlem birden çok bölümde sayılabilir): Öğrenciler
(çekmece sekmeleriyle) 100 · Denemeler 30 · Değerlendirmeler 23 · Ayarlar 22 ·
Çalışma planı 22 · Kaynaklar 22 · Kurum finansı 20 · Müfredat 16 · Ders
programı 16 · Okuma 14 · Bilgi kartları 13 · Erken uyarı 13 · Ödevler 11 ·
Panel 10 · Talepler 10 · Personel 9 · Duyurular 7 · Kurum takvimi 6 · Kitap
talepleri 5 · Yoklama 3 · ortak arama listeleri (`index.js`, `common.js`) 8.

## İzinler

Akademi izni → KM izni eşlemesi **tek tablodadır**: `backend/permission_map.py`
(router'da ya da panelde izin adı yazılmaz). Sözleşmeye eşlenmemiş yeni bir
izin gelirse test kırılır.

| KM izni | Varsayılan roller | Kapsam |
|---|---|---|
| `bbd_akademi.view` | admin, bbd_staff | Bütün okuma ekranları |
| `bbd_akademi.academic` | admin, bbd_staff | Program, zil, etüt, ödev, dönem, sınıf/çalışma grubu, hedef önerisi |
| `bbd_akademi.curriculum` | admin | Kuruma özel müfredat, program istisnaları |
| `bbd_akademi.resources` | admin, bbd_staff | Kurum kaynağı, içindekiler, paketler, atamalar |
| `bbd_akademi.attendance` | admin, bbd_staff | Yoklama |
| `bbd_akademi.exams` | admin, bbd_staff | Deneme tanımı, sonuç, toplu yükleme, cevap anahtarı, yayımlama, dışa aktarım |
| `bbd_akademi.finance` | admin, bbd_staff | Kurum carisi (görüntüleme dahil) |
| `bbd_akademi.finance_void` | admin | Tahsilat iptali — ayrıca PIN teyidi |
| `bbd_akademi.books` | admin, bbd_staff | Kitap talepleri |
| `bbd_akademi.announcements` | admin, bbd_staff | Duyurular |
| `bbd_akademi.requests` | admin, bbd_staff | İzin, görüşme, etüt talepleri |
| `bbd_akademi.settings` | admin | Kurum ayarları, kantin kuralları, tema, bayraklar, anahtarlar, anons şablonları, dizin, denetim; koçluk ayarları; mesaj şikâyet kuyruğu ve yazışma denetimi (`messages.moderate`, `messages.audit`) |
| `bbd_akademi.staff` | admin | Mobil yönetici erişimi, Akademi personeli ve rolleri |
| `bbd_akademi.campus` | admin, bbd_staff | Öğrenci çağırma, tablet ek süre/engel/oturum |
| `bbd_akademi.canteen` | admin | Akademi üzerinden kantin işlemleri (yalnız vekil; asıl ekran Öğrenciler modülü) |
| `bbd_akademi.learning` | admin, bbd_staff | Günlük koçluk: plan görevi atama ve erteleme, koç notları, erken uyarı eylemleri; görev onayı, soru-cevap, check-in, tebrik |
| `bbd_akademi.learning_setup` | admin | Öğrenme araçları kurulumu: plan şablonları, okuma listeleri ve ligi, desteler, değerlendirme şablon/dönemleri, erken uyarı eşikleri; koç eşlemesi, koç kütüphanesi küratörlüğü, rozetler, koçluk dışa aktarımı |
| `bbd_akademi.evaluations` | admin, bbd_staff | Değerlendirme formu, yayımlama, karne dışa aktarımı |

## Vekil, köprü ve PIN teyidi

- **İzin listesi üretilir:** `tools/gen_allowlist.py` sözleşmeden
  `backend/allowlist.py`'yi yazar (her `/v1/admin/*` işlemi + entegrasyon
  anahtarıyla okunabilen `/v1/students/{id}/*` ve `/v1/catalog/*` GET'leri).
  Sözleşme değişince betik koşturulur ve dosya commit'lenir; test ikisinin aynı
  olduğunu doğrular. Liste dışı her istek `403 operation_not_allowed`.
- **Ham vekil** `/api/bbd_akademi/v1/...`: gövde, sorgu, `Idempotency-Key`
  aynen geçer; multipart yükleme ve ikili indirme akış olarak geçer; Akademi'nin
  hata zarfı olduğu gibi döner; Akademi'ye ulaşılamazsa `503 akademi_unavailable`.
- **Panel köprüsü** `POST /api/bbd_akademi/km/call`: kabuğun istek kanalı yalnız
  JSON taşıdığı için panel buradan konuşur (aynı kapılar; `{ok, status, data,
  error, headers, file?}` zarfı).
- **PIN teyidi** (`x-step-up` işlemler, ör. tahsilat iptali): panel
  `POST /api/bbd_akademi/km/step-up {pin, operationId}` ile çekirdeğin
  `confirm_pin` kapısından tek kullanımlık belirteç alır; vekil belirteci
  tüketip `X-Akademi-Step-Up: km-pin` ekler. PIN Akademi'ye gitmez.
- **Personel eşitleme:** mobil yöneticiye kimin gireceği KM'dedir
  (`mod_bbd_akademi_staff`) ve `PUT /v1/admin/staff/sync` ile tam liste olarak
  itilir — değişiklikte, `staff_sync_minutes` aralığıyla ve "Şimdi eşitle" ile.
  Personelin **anons adı** (`announce_name`, göç 002; zil çağrısında "Hüseyin
  hoca") da KM'dedir (sözleşme `km-ogrenci-gruplari-ve-zil.md` §1); yüke
  `announceName` olarak girer (şalter açık; aşağıda).

## Sözleşme şalterleri

Sözleşmeye sonradan giren alanlar tek dosyada, şalterle durur:
`backend/contract_features.py` (panel `/km/status` → `contractFeatures` ile
okur). Vekil gövdeyi doğrulamadan geçirdiği için sözleşmede olmayan alan
gönderilmez. İki şalter de **açıktır**: alanlar sözleşmeye bbd-akademi
2d91b96 ile girdi.

| Şalter | Açıkken | Kapalıyken |
|---|---|---|
| `STAFF_ANNOUNCE_NAME` | `admin_syncStaff` yükünde `announceName` (`StaffSyncRequest.items[]`; boşsa `null`) | Anons adı KM'de saklanır, ekranda düzenlenir, yüke girmez |
| `CALENDAR_EVENT_BELL` | Takvim formundaki "Zil" alanı gövdeye `bell` (`{mode, profileId}`) ve `cancelLessons` olarak girer (`InstitutionEventInput`) | "Zil" alanı devre dışı ve gerekçeli; gövdeye alan eklenmez; KM saat setleri okunmaz |

Şalteri açan commit `allowlist.py`'yi de yeniden üretir;
`tests/test_akademi_contract_features.py` açık şalterin sözleşmede karşılığı
olduğunu doğrular. Akademi `admin_getBellPlanStatus` şalter istemez: izin
listesinde olduğu için kart onu kullanır.

**KM Zil Sistemi'nden okunan uçlar** (panel, `app.kmGet`; yalnız GET, izin
`bell.view`; yoksa 404/403 Türkçe notla karşılanır):

- `GET /api/bell/plan/status?from=YYYY-MM-DD&to=YYYY-MM-DD` — zil planı durumu
  kartının yedeği: Akademi işlemi sunmuyorsa (404) ya da `bell_plan_sync`
  kapalıysa (`feature_disabled`) bir kez buraya düşülür (§3.3 biçimi).
- `GET /api/bell/state` → `profiles: [{id, name, times}]` — takvim formundaki
  saat setleri (yalnız `CALENDAR_EVENT_BELL` açıkken, form açılınca, 5 dk
  önbellek).

## Hız (Akademi A14)

Kalıcı bağlantı havuzu ve katı süreler (vekil); panelde her liste imleçli
(sonsuz kaydırma + "Daha fazla"), satır başına ek istek yok, arama 250 ms
gecikmeli, seyrek değişen veri (katalog, şablon, ayar, zil, ders) ETag'li
önbellekte, bağımsız çağrılar paralel, bekleme yerine iskelet.

## Geliştirme

```bash
.venv/bin/python modules/bbd_akademi/tools/gen_allowlist.py           # izin listesini üret
.venv/bin/python modules/bbd_akademi/tools/gen_allowlist.py --check   # güncel mi
.venv/bin/python modules/bbd_akademi/tools/ui_coverage.py             # panel kapsamı
.venv/bin/python -m pytest modules/bbd_akademi tests/integration
.venv/bin/ruff check modules/bbd_akademi
```

Testler ağa çıkmaz: Akademi `httpx.MockTransport` ile taklit edilir.
