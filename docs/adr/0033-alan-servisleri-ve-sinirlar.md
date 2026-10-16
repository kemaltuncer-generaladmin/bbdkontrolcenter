# ADR 0033 — Alan servisleri ve sınırlar

**Durum:** Kabul edildi · 16.10.2026
**Etkilediği:** `docs/schemas/module.schema.json` (`domain`,
`data_classification`), 53 `modules/*/module.yaml`, `km_core/contracts/manifest.py`
ve `km_core/kernel/kernel.py` (alan modül kaydına yansır — yalnız veri),
`tools/build-ui-registry.py` (`registry.json` panel kaydında `domain`),
`km_sdk` (`values.py`, `printing.py`), yeni `docs/architecture/`
(`domains.yaml`, `module-graph.md`, `envanter-2026-10-16.md`), yeni araçlar
`tools/arch_scan.py`, `tools/check-boundaries.py`, `tools/module-graph.py`,
`tools/boundary-exceptions.yaml`, test `tests/integration/test_alan_sinirlari.py`
**İlgili:** ADR 0003, 0004, 0005 (modül sınırı), 0006, 0011, 0019, 0029
(outbox), 0031 (arayüz yeteneği). ADR 0032 aynı dalgada km-native paketinindir.

## Bağlam

Kullanıcı (16.10.2026): *"kodları hizmetleri vs öyle bi modülerleştirip
mikroservisleştirelim ki böyle düzenli güzel gruplanmış her birisi siber
güvenlik önlemleri alınmış mantıksal rls ler düzgün çalışır durumda işte her
şey adıyla belli fln tüüm kmyi ona göre elden geçirelim"*

KM bugün tek süreçtir: Coolify'da tek uvicorn süreci (`deploy/server/Dockerfile`,
`--workers` yok), tek PostgreSQL, 53 modül (`modules/*/module.yaml`; 28 açık,
25 kapalı). Modül sınırı ADR 0005'le "dikey dilim" olarak kurulu; K1–K5 kodla
denetlenmiyordu, yalnız yazılıydı. Envanter (`docs/architecture/envanter-2026-10-16.md`)
şunu gösterdi:

- Modüller arası Python importu ve başka modülün `mod_<id>_*` tablosuna SQL
  **yok** — ScopedStore öneki ve `km_mod_<id>` paket yalıtımı işini yapıyor.
- Gerçek sınır aşımları dar: çekirdek tablosuna SQL (Çıktı Merkezi → `outputs`,
  `users`), Akademi panelinden `/api/bbd_students` ve `/api/bell` çağrısı,
  çekirdekte modül adı (bir gerçek dal: `km_core/http/app.py` → `bbd_tablet`;
  geri kalanı göç geçmişi ve sözleşme değerleri).
- "Grup" kavramı yalnız menüde vardı (`ui.nav.group`: "BBD", "BBD Store", …);
  BBD grubunda kantin, öğrenci, tablet, zil, Akademi, ödeme aynı başlık
  altındaydı. Hangi modülün hangi veriden sorumlu olduğu, hangi veri sınıfını
  taşıdığı yazılı değildi.
- Kopyalar: 17 mağaza modülünde birebir aynı rapor basma güvenlik denetimi,
  14'ünde `as_int`, 11'inde `to_kurus`; kantin panellerinde dört özdeş
  `kit.js`/`picker.js`.

## Karar

### 1. Mikroservis değil, alan servisi

Canlı, tek sunuculu bir sistemi bir anda ayrı süreç + ayrı veritabanı + ağ
çağrısına bölmek **hızı ve güvenilirliği düşürür**: bugün öğrenci yazması ile
outbox olayı AYNI işlemdedir (ADR 0029) ve değişiklik kantine anında, uygulamaya
1–3 sn'de ulaşır. Ağ sınırı bu işlemi ikiye böler (iki aşamalı onay ya da
telafi akışı gerekir), her çağrıya gecikme ve yeni bir arıza biçimi ekler.

Hedef **alan servisi**dir: her alan

- adı belli bir servis paketidir (`domain` alanı, katalog
  `docs/architecture/domains.yaml`),
- kendi tablo önekini (`mod_<modül>_*`), kendi izin ad alanını (`<modül>.*`)
  ve kendi HTTP önekini (`/api/<modül>`) taşır,
- dışarıya yalnız **açık sözleşme** sunar: yetenek (`provides` → Protocol),
  olay (`events.publishes`, kalıcı olanı `events.outbox`), panel arayüz
  yeteneği (`ctx.capability`),
- başka alanın tablosuna, iç koduna, HTTP önekine **dokunmaz**,
- aynı güvenlik tabanını uygular (§5).

Fiziksel ayrılma yalnız gerçekten değecek yerde, ayrı kararla (§10).

### 2. Alanlar ve modül eşlemesi

| Alan (`domain`) | Ad | Modüller | Veri sınıfı üst sınırı |
|---|---|---|---|
| `platform` | Platform ve kurumsal araçlar | `print`, `antivirus` (+ km_core, km_platform, km_sdk) | personal |
| `academy` | Akademi | `bbd_akademi` | financial |
| `students` | Öğrenci ve veli | `bbd_students`, `bbd_sms` | personal |
| `canteen` | Kantin | `bbd_canteen_api`, `bbd_canteen_backups`, `bbd_canteen_devices`, `bbd_canteen_products`, `bbd_canteen_reports`, `bbd_lunch`, `bbd_bulk_sale` | financial |
| `payments` | Ödeme talepleri | `bbd_payment_request` | financial |
| `tablet` | Öğrenci tableti | `bbd_tablet` | personal |
| `bell` | Zil ve anons | `bell`, `bbd_class_schedule` | personal |
| `gateway` | Mobil geçit | `mobile_gateway` | financial |
| `store` | Mağaza (BBD Store) | 21 `store_*` (10'u kapalı) | financial |
| `bld` | BLD | 15 `bld_*` (hepsi kapalı) | financial |

Gerekçeler:

- **`bbd_sms` → students**: işi veliye serbest metin SMS'tir; alıcıyı öğrenci/
  veli dizininden seçer. Kantin API'sini tüketmesi teknik bağımlılıktır
  (öğrenci listesi oradan gelir), alanı değil.
- **`bbd_payment_request` ayrı alan (payments)**: borç bildirimi + ödeme
  bağlantısı + nakit tahsilat para akışıdır; kantin bakiyesine bağlı olsa da
  güvenlik/denetim ihtiyacı (finansal, geri alınamaz) kantin ekranlarından
  farklıdır. A12: kantin parası ile kurum parası (Akademi finansı) AYRI
  alanlardadır ve hiçbir alan ikisini birden yazmaz.
- **`mobile_gateway` ayrı alan (gateway)**: kendi iş verisi yoktur, Akademi
  API'sine öteki alanların yeteneklerini birleştirip sunan cephedir. Alanlar
  arası OKUMA bu alanın işidir; bu yüzden en çok kenar ondadır (envanter §3).
- **`bbd_class_schedule` → bell**: `bell.week` yeteneğinin salt okunur
  aynasıdır; sahibi zil sistemidir.

`data_classification` modülün tuttuğu ya da gösterdiği EN YÜKSEK sınıftır:
`public` · `internal` · `personal` (KVKK kişisel veri) · `financial` (para/
bakiye + kişisel veri). Modül alanın üst sınırını aşamaz.

### 3. Adlandırma kuralları

Mevcut üslup korunur (CLAUDE.md "Dil"): **kod, tanımlayıcı, dosya/klasör adı,
API yolu, olay/yetenek/izin anahtarı İngilizce ve ASCII**; belge, arayüz metni,
commit, test adı ve yorum Türkçe.

| Ne | Kural | Kapı |
|---|---|---|
| Modül kimliği | `^[a-z][a-z0-9_]*$`, alanın öneki + konu: `bbd_*` (BBD okul tarafı), `store_*`, `bld_*`; platform araçları tek sözcük (`print`, `bell`). Klasör adıyla aynı. | şema + `read_manifest` |
| Alan kimliği | tek İngilizce sözcük, katalogda | `check-boundaries` |
| Tablo | `mod_<modül>_<ad>`; yalnız `ctx.store.table("<ad>")` ile üretilir, elle `mod_` yazılmaz | `ScopedStore` + `foreign-table` |
| İzin | `<modül>.<eylem>` ya da `<modül>.<konu>.<eylem>` (`view`, `manage`, `export`, …) | `read_manifest` (önek) |
| Yetenek | `<modül>.<ad>` ya da alan öneki (`canteen.api`, `store.order.card`, `akademi.student_finance`) | `naming` |
| Olay | `<modül|alan öneki>.<geçmiş zaman>` (`groups_changed`, `device_enrolled`); outbox türü `alan.olay` (`_TYPE` deseni) | `naming` |
| HTTP | `/api/<modül>[/v<N>]`; dış sözleşmesi olan geçitler sürümlüdür (`/api/mobile_gateway/v1`) | `naming` |
| Arayüz yeteneği | `<modül>.<ad>`; arama sağlayıcısı `.search` ile biter (ADR 0031) | — |

Bugün bütün modüller bu kurallara uyuyor (`naming` bulgusu 0). Yeni ad kuralı
bozarsa kapı düşer.

### 4. Servis arayüzü ve olay sözleşmesi

Bir modül başka bir modülle — aynı alanda da olsa (K3, K5) — yalnız şu yollarla
konuşur:

1. **Yetenek** (senkron): sağlayan `provides: [{capability, contract}]`,
   tüketen `consumes: [{capability, optional, reason}]`. `contract` bir
   Protocol'dür; yetenek yalnız bu yüzeyi açar. Alanlar arası tüketim
   mümkünse `optional: true` (K7 — sağlayan düşerse tüketen kısıtlı çalışır).
2. **Süreç içi olay** (`ctx.publish` / `ctx.subscribe`, manifest `events`):
   KM içindeki dinleyiciler için; kaybolabilir.
3. **Kalıcı olay** (`events.outbox`, ADR 0029): dış dünyaya (Akademi, kantin)
   giden tek yol; iş yazmasıyla aynı işlemde, kişisel veri taşımaz (A6).
4. **Panel arayüz yeteneği** (`ctx.capability('x.y')`, ADR 0031): bir ekranın
   başka ekranın parçasını açması.

Yasak: başka modülün kodunu import (`modules.<id>`, `km_mod_<id>`, panelde
kendi ağacı ve `shell/ui-kit/` dışı), başka modülün ya da çekirdeğin
tablosuna SQL, başka modülün HTTP önekini çağırmak (`/api/<başka>` — panel de
dahil: o modülün izin kapısını ve sözleşmesini atlatır).

### 5. Güvenlik tabanı — her serviste aynı

| Taban | Nasıl | Nerede uygulanır |
|---|---|---|
| İzin ad alanı | İzin anahtarı modül önekiyle başlar; uç `http.requires` + `requires(...)`; ilan etmeyen uç reddedilir (K9, varsayılan kapalı); rol adı sorulmaz (K10) | `read_manifest`, kernel `_mount_modules`, `km_core/http/deps.py` |
| Yıkıcı işlem | `destructive: true` → PIN teyidi + denetim izi (mağazada gerekçeli onay, ADR 0012) | `confirm_pin` |
| Girdi doğrulama | Gövde `pydantic.BaseModel` (`extra="forbid"`), sorgu `Query(..., ge/le/max_length)`; serbest sözlük kabul edilmez | modül; km-guvenlik denetimi |
| Denetim günlüğü | Çekirdek `audit_log` (kimlik, yetki, yıkıcı işlem) + modülün kendi `mod_<id>_audit` tablosu | çekirdek + modül |
| Gizli bilgi | Yalnız `ctx.capability("secrets")` (kasa, K8); ayar dosyasında/manifestte sır yok | `km_platform/secrets` |
| Veri sınıfı | `personal`/`financial` modül: günlükte kişisel veri maskelenir, dışa aktarma `write_private` (0600), outbox `data`sında ad/telefon/kod yok | `km_core/http/masking.py`, `km_sdk.write_private`, outbox `FORBIDDEN_DATA_KEYS` |
| Sınır | §4 yasakları | `tools/check-boundaries.py` + pytest |

Akademi tarafının mantıksal RLS'i ve KM izin haritalarının güvenlik denetimi
km-guvenlik paketinindir (aynı dalga); bu ADR yalnız tabanın HER serviste
aynı olmasını ve nerede uygulandığını sabitler.

### 6. Sınır denetimi kalıcı kapıdır

`tools/check-boundaries.py` (CI) ve `tests/integration/test_alan_sinirlari.py`
(pytest) aynı tarayıcıyı (`tools/arch_scan.py`) koşar. Kurallar:
`module-import`, `core-import`, `foreign-table`, `core-table`, `foreign-api`,
`core-knows-module`, `naming`, `manifest`. Belge dizgisi, yorum ve `tests/`
sayılmaz.

Bugünkü ihlaller `tools/boundary-exceptions.yaml`'dadır: her satırda `reason`
ve `plan` zorunlu. **Listede olmayan bulgu ve artık bulunmayan istisna kapıyı
düşürür** — liste yalnız küçülür. Alan/sınıf eksikliği (`manifest`)
istisnaya alınamaz.

Alan ve sınıf şemada **zorunlu değil, enum değil**: bir yazım hatası canlıda
modülü düşürmemeli (K7). Kapı yükleme anında değil, denetimdedir.

### 7. Ortak kod SDK'dedir

Gövdesi (AST düzeyinde) birebir aynı bir yardımcı **üç ya da daha çok modülde**
yaşıyorsa `km_sdk`'ye taşınır; modül adı korur (`from km_sdk import as_int`)
ki mevcut çağrılar değişmesin. Gövdesi FARKLI varyantlar birleştirilmez —
birleştirmek davranış değişikliğidir, ayrı karar ister. Panel kopyaları
(`kit.js`, `picker.js`, `datefield.js`) kabuğun `shell/ui-kit/`ine taşınır
(ADR 0011; km-native).

İlk taşınanlar: `km_sdk.values` (`as_int`, `to_kurus`), `km_sdk.printing`
(`print_report_file` — "yalnız kendi rapor klasörü" güvenlik denetimi,
`printer_status_of`).

### 8. Geçiş planı — her adım ayrı commit, ayrı geri alınabilir

| Aşama | Ne | Geri alma |
|---|---|---|
| 1 (16.10.2026, bu ADR) | Alan kataloğu, 53 manifeste `domain`/`data_classification`, kayıt/registry'de alan (veri), sınır kapısı + istisnalar, envanter ve grafik, SDK'ye ilk kopyalar, eskimiş belgeler | Manifest alanları zorunlu değil; commit geri alınınca davranış aynı |
| 2 (lider onayıyla) | `bbd_akademi` iç bölünmesi: TEK modül kalır (izin ad alanı `bbd_akademi.*`, vekil, izin listesi bir); panel ve backend alt alan klasörlerine ayrılır (envanter §7). Akademi panelinin `/api/bbd_students` ve `/api/bell` çağrıları arayüz yeteneğine taşınır → 2 istisna düşer | Dosya taşıma; her alt alan ayrı commit |
| 3 | İstisna azaltma: (3.1) çekirdeğe `outputs` okuma/sayaç API'si + kimlik ad çözücü → print SQL yazmaz; (3.2) `http.device_router` manifest alanı → `km_core/http/app.py`'deki `bbd_tablet` dalı kalkar (km-guvenlik); (3.3) kantin panel kopyaları `shell/ui-kit/`e (km-native) | Her biri tek istisna satırı |
| 4 | PIN adlandırma göçü (§9) | Takma adlarla geriye uyumlu |
| 5 | Fiziksel ayrılma (§10) — ayrı ADR | Ayrı süreç kapatılınca iş API sürecine döner |

### 9. `password_*` → PIN adları

Giriş PIN'dir (ADR 0007; 0016 reddedildi), ama `password_hash`,
`password_set_at`, `set_password`, `mustSetPassword`, `/api/auth/set-password`
adları göçten kaldı. Bunlar üç türdür:

- **DB sütunu** (`users.password_hash`, `password_set_at`; merkezî kimlik
  servisinin kadro yükü): yeniden adlandırma ancak eklemeli göçle — yeni sütun
  + tetikleyici/görünüm ya da iki sürüm boyunca çift yazma. Merkezî kimlik
  servisi ile kurulumlar aynı anda güncellenemeyeceği için ayrı sürümde.
- **API sözleşmesi** (`/api/auth/set-password`, gövde alanı `password`,
  `mustSetPassword`, izin `users.set_password`): yeni ad eklenir, eski ad bir
  sürüm takma ad olarak kalır, kabuk yeni adı kullanır, sonra eski düşer.
  İzin anahtarı rol tablolarında durduğu için göçle.
- **İç ad** (yerel değişken, parametre, yardımcı fonksiyon): serbestçe.
  Bu dalgada yalnız km-mimari'nin dosyasındaki iç ad değişti
  (`km_platform/identity_sync/http.py` → `_confirm_pin(…, pin)`); kalanı
  `km_core/security`, `km_core/http` (km-guvenlik) ve kabuktadır (km-native).

### 10. Fiziksel ayrılma adayları

Ayrı sürece değecek olanlar — hepsi **iş verisi yazmayan ya da outbox
üzerinden konuşabilen, ağır ya da zamanlanmış** işler:

1. **Zamanlanmış iş süreci (`km-worker`)** — Akademi personel eşitlemesi,
   mağaza sipariş aşaması SMS'i, mağaza raporu zamanlaması, outbox budaması,
   anons sesi üretimi (Vertex TTS), mağaza taraması. Gerekçe: API bugün tek
   uvicorn sürecidir; API'yi yatayda çoğaltmak istendiği an bu görevler HER
   kopyada koşar (çift SMS, çift eşitleme). Ayrı tek kopya worker bunu çözer
   ve uzun işler API'nin olay döngüsünü tutmaz. Ön koşul hazır: outbox ve
   modül işlemi (ADR 0029) süreçler arası güvenli iletişimi sağlar.
2. **Rapor/PDF üretimi ve önizleme** — ReportLab üretimi ve `pdftoppm`
   önizlemesi CPU'ya bağlıdır; bugün `asyncio.to_thread` ve alt süreçle
   kotarılıyor ve `_render_pages` 21 modülde kopyadır. Önce SDK'de tek
   uygulama (§7), sonra sınırlı eşzamanlılıklı bir iş kuyruğu/süreç.
3. **Antivirüs (ClamAV)** — clamd yüksek bellekli, yalnız Linux; zaten
   `platforms: [linux]`. Ayrı konteyner, KM'ye yetenek arayüzüyle bağlı.
4. **Merkezî kimlik servisi** zaten ayrıdır (`services/identity/`, ADR 0021)
   — emsal.

Ayrılmaması gerekenler: alan servislerinin kendisi (öğrenci, kantin, zil,
tablet, mağaza yönetimi). Aynı işlemde yazılan veri + olay, 1–3 sn hedefi
ve tek veritabanı birlikte gider; ağ sınırı burada yalnız gecikme ve arıza
ekler. Mağaza ve BLD zaten dış sistemlerin (Bagisto, Laravel) yönetim
yüzüdür — veri orada, KM'de yalnız önbellek ve iz var.

Uygulama ayrı karardır (yeni ADR); bu ADR yalnız adayları ve gerekçeyi kaydeder.

## Sonuçlar

- "Hangi modül hangi alandan, hangi veriyi taşıyor" sorusu tek yerden
  (`module.yaml`, `domains.yaml`, `module-graph.md`) okunur; kabuk menüyü ve
  aramayı alana göre kurabilir (`GET /modules` ve `registry.json` → `domain`).
- K1/K2/K3/K5 artık yazılı kural değil, CI kapısıdır. Bugün 32 bulgu
  16 istisna anahtarında; yeni ihlal eklenemez.
- Rapor basma güvenlik denetimi 17 kopyadan bire indi.
- Yeni modül `domain` ve `data_classification` olmadan kapıdan geçmez
  (`tools/module-template/module.yaml`).
