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

   > **Anahtarın izinleri oluşturulduğu an dondurulur.** Akademi `km_panel`
   > kümesini anahtar oluşturulurken kopyalar; kümeye sonradan giren izin eski
   > anahtarda YOKTUR. **KVKK talepleri** (`privacy.requests`, mağaza işi,
   > sözleşme README §14 madde 41) bu durumdadır: bu sürümden önce üretilmiş
   > KM panel anahtarı **privacy.requests için yeniden üretilmelidir** — aksi
   > halde KVKK talepleri bölümü Akademi'den `403 forbidden` alır (bölüm bunu
   > görür ve ne yapılacağını yazar). Sıra: Ayarlar → Entegrasyon anahtarları →
   > "Yeni anahtar" (izin kümesi "KM paneli") ya da yukarıdaki CLI komutu →
   > yeni anahtarı kasaya `bbd_akademi.api_key` olarak yaz (merkezden:
   > `scripts/push-secrets.py`) → panelin açıldığını gör → eski anahtarı iptal
   > et. Akademi tarafında dağıtımdan sonra `sync-roles` koşturulmuş olmalıdır
   > (yönetici rolüne `privacy.requests` girer).

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
   (ana şalter), `base_url` (varsayılan `https://api.benimbasaridunyam.com.tr`),
   `timeout_seconds`, `upload_timeout_seconds`, `max_upload_mb`,
   `download_hosts` (imzalı indirme bağlantılarının izinli ek alanları),
   `staff_sync_minutes`, `step_up_seconds`.

4. **Hazırlık (staging) ortamı:** `config/local.yaml` içinde
   `modules.bbd_akademi.base_url` hazırlık sunucusuna çevrilir ve hazırlık
   kiracısının anahtarı kasaya yazılır. Canlı anahtar hazırlıkta, hazırlık
   anahtarı canlıda kullanılmaz (Akademi anahtarı kiracıya bağlar). Panel
   açılışında anahtar yoksa ya da şalter kapalıysa ekranın tepesinde sebep
   yazar; vekil `503 akademi_not_configured` / `akademi_disabled` döner.

## Bilgi mimarisi (0.5.2 — sadeleştirme)

Kullanıcı isteği (13.10.2026): "Uygulama çok karışık olmaya başlamış, sadeleşip
basitleşsin" ve "öğrenci yönetimini de tamamıyla BBD Akademi kısmına taşıyalım".
Menü 21 düz bölümden **altı görev odaklı üst bölüme** indi; alt ekranlar yalnız
seçili üst bölümün altında açılır. Kayıt tek yerde: `ui/panel/sections.js`
(`AREAS`, `SECTIONS`, `STUDENT_CARD`; import'suz, Node testli —
`tests/test_akademi_bilgi_mimarisi.py`). Hiçbir ekran kaldırılmadı
(`ui_coverage.py` eksik 0).

```
Bugün ─────────────── Özet · Talepler · Duyurular
Öğrenciler ─────────── Öğrenciler (KM yönetimi) · Toplu işlemler · Gruplar ·
                       Sınıflar · Veliler ve uygulama · Uygulama kullanımı
   └ öğrenci kartı ─── Özet · Kayıt ve veliler · Program ve yoklama ·
                       Ödev ve çalışma · Ölçme ve gelişim · Cari, kantin ve kitap
Dersler ve Program ─── Ders programı ve etüt · Yoklama · Ödevler · Çalışma planı ·
                       Şablonlar · Kurum takvimi
Ölçme ve Gelişim ───── Denemeler · Değerlendirmeler · Kaynaklar · Müfredat · Okuma ·
                       Bilgi kartları* · Erken uyarı*
Kantin ve Finans ───── Kurum finansı* · Kantin* · Kitap satışı* · Kitap talepleri*
Ayarlar ────────────── Kurum ayarları (ilk sekme Özellikler) · Personel · KVKK talepleri
                                   * kurum özellik bayrağına bağlı
```

**Yalnız açık olan görünür.** Kurumda kapalı bir özelliğin ekranı menüde, kartı
öğrenci kartında, düğmesi listede HİÇ çizilmez; `403 feature_disabled` hata
kartı görülmez (yine de gelirse sakin bir "kurumda kapalı" notu çıkar, "tekrar
dene" yok). Bayraklar açılışta `/km/status` → `features` ile gelir: modülün
sunucusu Akademi `admin_listFeatures`'ı kendi adına (sistem aktörü) okur, 60 sn
önbellekte tutar, 3 sn'de yanıt yoksa açılışı bekletmez (`backend/features.py`).
Okunamazsa `null` — panel her şeyi gösterir, karar Akademi'de kalır. Ayarlar →
Özellikler'de bir bayrak değişince menü hemen yenilenir (`fresh=1`). İşlem
düzeyinde aynı kural `api.js` → `can()` / `featureOff()` (işlemin
`featureFlag`'i, `/km/operations`). Bölüm bayrakları: Erken uyarı
`early_warning`, Bilgi kartları `flashcards`, Kurum finansı `finance`, Kantin
`canteen`, Kitap satışı ve Kitap talepleri `books`; kart/sekme düzeyinde `tablets`, `pa`,
`self_review`, `reading_league`, `class_comparison`.

**Hız.** Açılış TEK çağrıdır: `/km/status?operations=1` (bağlantı, izinler,
bayraklar, işlem listesi `operationList`). "Bugün" tek özet ucunu
(`admin_getDashboard`) çağırır; tabletler ve öğrenci çağırma katlıdır, açılınca
yüklenir. Öğrenci kartında yalnız açık bölüm yüklenir.

**Öğrenci yönetimi BBD Akademi'de.** KM Öğrenci Yönetimi'nin (`bbd_students`)
listesi, toplu işlemleri (sınıfa ata, gruba ekle/çıkar/taşı, seviye-alan,
giriş kodları, pasifleştir) ve grupları Öğrenciler bölümünde çizilir; satıra
tıklamak öğrencinin **tek kartını** açar (Akademi verisi + KM kaydı + kantin
özeti). Kartın "Kayıt ve veliler" sekmesi KM kaydıdır: kimlik, ana sınıf ve
gruplar, veliler (en çok 4, birincil, yakınlık, +90 5xx doğrulama), kantin
ayarları ve tahsilat, QR/giriş kodu, durum (pasifleştir / sil). Bağ arayüz
yetenekleriyle kurulur (K3): `bbd_students.manager`, `bbd_students.record`;
veri ve uçlar `bbd_students`'ta kalır (A5 — Akademi öğrenci yaratmaz). Yetenek
yoksa (modül kapalı ya da kullanıcının `bbd_students.view` izni yok) eski
salt okunur düzen kalır. Menüdeki "Öğrenci Yönetimi" ekranı Akademi'yi
görebilen kullanıcıyı buraya yönlendirir (`bbd_akademi.students` işaret
yeteneği; derin bağlantı `{kantinId}` → `{section: 'students', kmStudentId}`).

**Tek giriş noktası (0.5.3, ADR 0031).** "Öğrenci Yönetimi" artık menüde hiç
çizilmez (`ui.nav.replacedBy`); bu panelin menü satırı `bbd_akademi.view` YA DA
`bbd_students.view` ile görünür. Akademi izni olmayan kullanıcıda
`/km/status` 403 döner ve panel **yalnız öğrenci yönetimi** kipinde açılır
(`app.kmOnly()`; `sections.js` → `kmOnly: true`): menüde tek "Öğrenciler",
sekmeler KM listesi / toplu işlemler / gruplar, öğrenci kartı yalnız KM kaydı —
Akademi vekiline hiç istek gitmez. Kart "Kayıt ve veliler"de satır içi
düzenleme, hızlı işlem şeridi ve "Akademi'ye ulaştı ✓" vardır
(`bbd_students` README "Hızlı güncelleme"); yoklayıcı bu panelin
`/km/events` ucudur (Ayarlar → Senkron ile aynı veri). Erişilebilirlik:
22 bölüm Node'da axe benzeri denetimden geçer
(`tests/test_akademi_erisilebilirlik.py`, `tools/a11y`).

### Eski → yeni eşleme

| Eski menü / sekme | Yeni yeri |
|---|---|
| Panel | Bugün → Özet (rakamlar + "Sizi bekleyenler"; tablet/öğrenci çağırma katlı) |
| Talepler · Duyurular | Bugün → Talepler · Duyurular |
| Öğrenciler (Akademi listesi) | Öğrenciler → Uygulama kullanımı (yönetim yoksa "Öğrenciler") |
| Öğrenciler → Sınıflar · Veliler | Öğrenciler → Sınıflar · Veliler ve uygulama |
| Öğrenciler → Çalışma grupları (salt okunur) | Öğrenciler → Gruplar (KM, düzenlenebilir) |
| KM Öğrenci Yönetimi (ayrı menü) | Öğrenciler → Öğrenciler · Toplu işlemler · Gruplar; öğrenci kartı → Kayıt ve veliler |
| Ders programı · Yoklama · Ödevler · Kurum takvimi | Dersler ve Program → aynı adlarla ("Ders programı ve etüt") |
| Çalışma planı (Öğrenme araçları) | Dersler ve Program → Çalışma planı |
| Denemeler · Müfredat · Kaynaklar (Akademik) | Ölçme ve Gelişim |
| Okuma · Bilgi kartları · Değerlendirmeler · Erken uyarı (Öğrenme araçları) | Ölçme ve Gelişim |
| Kurum finansı · Kitap talepleri (Kurum) | Kantin ve Finans |
| Ayarlar → Kantin kuralları | Kantin ve Finans → Kantin (+ KM kantin ekranlarına geçiş) |
| Ayarlar · Personel · KVKK talepleri (Yönetim) | Ayarlar → Kurum ayarları · Personel · KVKK talepleri |
| Ayarlar sekme sırası | Özellikler · Kurum · Tema ve logo · Anons şablonları · Senkron · Dizin eşitleme · Entegrasyon anahtarları · Denetim kaydı |

| Eski öğrenci kartı sekmesi (15) | Yeni kart sekmesi → bölüm |
|---|---|
| Özet · Hedef & özet | Özet → Genel bakış · Hedef ve öğrenme özeti |
| — (KM Öğrenci Yönetimi ekranı) | Kayıt ve veliler |
| Program · Yoklama · Talepler | Program ve yoklama |
| Ödevler · Öğrenme · Çalışma · Bilgi kartları* | Ödev ve çalışma |
| Denemeler · Müfredat & ilerleme · Kaynaklar · Koçluk | Ölçme ve gelişim |
| Kurum cari* · Kantin* | Cari, kantin ve kitap (Kantin hareketleri; 0.5.4: + Kurumdan alınan kitaplar*) |

Eski sekme anahtarları derin bağlantıda çalışmaya devam eder
(`app.go('students', {studentId, tab: 'exams'})` → Ölçme ve gelişim → Denemeler
açık). Bölüm başına bir satır:

- **Bugün** — günün rakamları ve bekleyen işler tek özet çağrısıyla; hızlı düğmeler (yoklama, ödev, tahsilat, duyuru, öğrenci çağır).
- **Öğrenciler** — öğrenci ekle/düzenle, sınıf ve gruba (toplu) ata, veliler; öğrenciye tıklayınca tek kart.
- **Dersler ve Program** — haftalık program ve etüt, günün yoklaması, ödev verme (tek ya da toplu, üç adım) ve teslim, koç görevleri, şablonlar (ödev, atama paketi, plan), kurum takvimi.
- **Ölçme ve Gelişim** — denemeler ve sonuç yükleme, değerlendirme karneleri, kaynak ve müfredat, okuma ve (açıksa) kartlar/erken uyarı.
- **Kantin ve Finans** — kurum carisi ve tahsilat, kantinin kurum kuralları (para ayrı), kitap satışı (üç adım) ve kitap talepleri.
- **Ayarlar** — hangi özelliklerin açık olduğu, kurum ayarları, senkron, entegrasyon, personel ve KVKK.

## Bölümler

Panel bölümleri `ui/panel/sections.js` → `SECTIONS` dizisindedir (bölüm = bir
dosya + bir satır; ilk açılışta yüklenir; `area` üst bölümü, `feature` kurum
bayrağını verir). Görünürlük KM iznine, her düğme
işlemin izinli olup olmadığına göre çizilir; asıl kapı vekildedir.

| Bölüm | Dosya | İçerik |
|---|---|---|
| Bugün → Özet | `dashboard.js` | Tek özet çağrısı: günün dersleri, yoklama, kurumda olanlar, bekleyen talep ve kitap; kurum carisi ve kantin AYRI kartlarda; tablet panosu ve kullanım; öğrenci çağırma |
| Öğrenciler | `students*.js` | Akademi öğrenci listesi (seviye/alan/hedef salt okunur — Öğrenciler modülünde düzenlenir), öğrenci detayı: özet, program, müfredat ve ilerleme, kaynaklar, ödevler, denemeler (karne), yoklama, çalışma, öğrenme (plan, hata defteri, soru, okuma, ajanda — F-A24), hedef & özet, koçluk (koç notları, değerlendirmeler, öz değerlendirme, erken uyarılar), bilgi kartları, talepler, kurum cari, kantin (salt okunur); sınıflar, çalışma grupları, veliler. KM'de **pasifleştirilen** öğrenci varsayılan listede yoktur, “Durum → Pasif (KM)” süzgeciyle (`status=inactive`) görünür; çekmecesi salt okunurdur (yazma düğmeleri gerekçeli kapalı — kurum cari tahsilatı ve dışa aktarım açık) |
| Müfredat | `curriculum.js` | Katalog (seviye/sınav/ders), kuruma özel program/ünite/konu, sürümler, program çözümleme, öğrenci/sınıf istisnaları |
| Kaynaklar | `resources*.js` | Hazır kütüphane, kurum kaynakları (ISBN, mağaza kataloğu), içindekiler ve konu eşlemesi, "konulardan bölüm üret", paketler + otomatik atama + şimdi uygula, atamalar, ilerleme tablosu |
| Ders programı | `timetable*.js` | Zil planı durumu kartı (14 gün: kaynak, bugünün kipi, çağrı ve ses, zil ajanı, acil kapatma; "Planı şimdi it"), zil dilimine hizalı haftalık ızgara (çakışma gösterimi, kopyalama), etüt ve tek seferlik dersler, zil dilimleri, dönemler |
| Kurum takvimi | `institution-calendar.js` | Tatil, etkinlik, veli toplantısı, deneme günü, kayıt dönemi; hedef kitle, bağlı duyuru ve hatırlatma (F-A20); "Zil" alanı (normal / tatil — zil çalmaz / KM saat seti) ve "dersleri iptal et" — sözleşme şalteri açılana dek devre dışı |
| Yoklama | `attendance.js` | Günün oturumları, yoklama listesi ve düzeltme |
| Ödevler | `homework.js`, `homework-bulk.js`, `homework-content.js` | "Ödev ver" = üç adımlı sihirbaz (ne → kime → ne zaman; şablondan ya da yeni, birden çok ödev, tek / birkaç tarih / her hafta / şablon süresi, otomatik önizleme ve çakışmalar, büyük iş arka planda); seçilenlere toplu ertele / tarih / iptal / sil; toplu seri süzgeci; teslim tablosu, hatırlatma |
| Şablonlar | `templates.js` (+ `bulk-target.js`, `wizard.js`) | Ödev şablonları (kurum şablonu; yeni / düzenle / kopyala / sil / "bu şablonla ödev ver"), atama paketleri (yalnız KM tanımlar: program + kaynak + kaynak paketi + plan şablonu + kural + otomatik; uygula / kaldır — seçerek · süzgeçle · kuralına uyan herkes, hariç tutulanlar, önce say, 300+ öğrencide arka plan işi), plan şablonları |
| Denemeler | `exams*.js` | Tanımlar, geçmiş deneme, toplu yükleme sihirbazı (yükle → sütunlar → eşleme + yayınevi şablonları → önizleme ve belirsiz eşleşme çözümü → kaydet / geri al), cevap anahtarı ve soru→konu eşlemesi, tekil sonuç, yayımla, analiz, karşılaştırma, dışa aktarım |
| Çalışma planı | `plan*.js` | Plan görevi atama (öğrenci/grup/sınıf), plan şablonları ve sınıfa uygulama, uyum tablosu, erteleme talepleri (F-A18) |
| Okuma | `reading*.js` | Kurum okuma listeleri, hazır listeler (tür süzgeci; hazır şablondan oluştur), okuma ligi, öğrenci × ay raporu (F-A19) |
| Bilgi kartları | `decks.js` | Kurum desteleri (ders/konu, görselli kartlar), sınıf/grup/öğrenci ataması (F-A23) |
| Değerlendirmeler | `evaluations*.js` | Şablonlar (bölüm, kriter, ölçek, otomatik veri), dönemler ve tamamlama durumu, değerlendirme formu (otomatik veri yan paneli), yayımla / geri al, gelişim karnesi PDF (F-A21) |
| Erken uyarı | `insights*.js` | Kurallı risk listesi ve eylemler (not, görüşme, veliye bildirim, çöz / yeniden aç), eşikler, en çok hata yapılan konular ve nedenler (F-A22) |
| Kurum finansı | `finance.js` | Cari hesaplar, peşinatlı taksit planı, kalemler, tahsilat (seçili kalem / serbest tutar FIFO), PIN teyitli iptal, gecikenler + hatırlatma, ekstre ve makbuz — **kantinden tamamen ayrı (A12)** |
| Kantin | `canteen.js` | Kantinin kurum kuralları (F-A15; gövdesi `settings.js` → `canteenTab`) ve KM kantin ekranlarına geçiş; kantin parası kurum carisinden ayrı (A12) |
| Kitap satışı | `book-sales.js` | "Kitap sat" üç adım (kitaplar → öğrenciler → ödeme: cari / nakit / kart POS / veliye ödeme bağlantısı), satış listesi (açık / tümü, öğrenci süzgeci), ayrıntı (öğrenci satırları, seçilenleri / hepsini teslim et), satır: siparişi yeniden dene, mağaza durumunu yenile, ödeme bağlantısını yeniden gönder + kopyala, sipariş/fatura PDF, gerekçeli iptal / iade, geçmiş; öğrenci kartında kurumdan alınan kitaplar — **para yalnız kurum carisinde (A12)** |
| Kitap talepleri | `books.js` | Kuyruk, siparişi yeniden dene, teslim, sipariş PDF'i |
| Duyurular | `announcements.js` | Taslak → hedefle → şimdi ya da zamanlı yayımla, okunma oranı |
| Talepler | `requests.js` | İzin, görüşme ve etüt talepleri |
| Kurum ayarları | `settings*.js` | Özellikler (ilk sekme; değişince menü yenilenir), kurum ve bildirim varsayılanları, logodan tema + önizleme + yayımla (F-A16), özellik bayrakları, anons şablonları, entegrasyon anahtarları, dizin eşitleme (etkin / pasif sayısı), **Senkron** kartı (`settings-sync.js`, aşağıda), denetim kaydı |
| Personel | `staff.js` | Mobil yönetici erişimi (modülün kendi listesi ve anons adı "Hüseyin hoca", Akademi'ye itilir), Akademi personeli, roller ve izin kataloğu |
| KVKK talepleri | `privacy.js` (hüküm mantığı import'suz `privacy-logic.js`, Node testi) | Hesap silme ve veri indirme talep kuyruğu (`/v1/admin/data-requests*`): varsayılan açık talepler (en eski önce), tür süzgeci, son gün ve kalan gün rozeti (7 gün ve altı sarı, süresi geçen kırmızı), ayrıntı çekmecesi; **onayla** (silmede geri dönüşsüz silinenler yazılı, isteğe bağlı not), **reddet** (gerekçe zorunlu, en az 10 karakter, talep sahibine gider), silinmiş hesapta **erişimi yeniden aç**; öğrenci talebinde öğrenci kartına geçiş. Yazmalar `X-Akademi-On-Behalf-Of` ile gider; "Sonuçlandıran" alanında KM kullanıcısı görünür. Anahtar izni eksikse (Akademi 403) yeniden üretme notu |

Kapsam ölçümü: `tools/ui_coverage.py` izin listesindeki her işlemin panelde
nerede karşılandığını yazar; ekranda bilerek olmayan işlemler gerekçesiyle
`NOT_SURFACED` tablosundadır ve test (`tests/test_akademi_panel_coverage.py`)
bunu bağlar. Sözleşmeye yeni girmiş, ekranı henüz yazılmamış işlemler ayrı
`PENDING` ("sırada") tablosundadır — aynı kurallarla, gerekçeli.

**Kapsam (sözleşme 1.0.0-draft.3, bbd-akademi `main` 509ae50 — birleşim 2:
KM olayları, port bağlama, KVKK talep kuyruğu; 660 işlem):** izin listesinde
474 işlem — 376 `/v1/admin/*` (x-surface `both` 228, `km` 148) + 98 salt
okunur `/v1/students/{id}/*` ve `/v1/catalog/*`. Ekranda **346**, bilerek
yok **4** (gerekçeleri `NOT_SURFACED`'da: personel itmesi modül servisinde,
kantin tahsilatı ve kontrolleri Öğrenciler modülünde, öğrenci dışa aktarım
durumu; çalışma grubu yazma uçları sözleşmeden kalktı), sırada **124**
(draft.3'ün program kurucu, ders oturumları, grup üyelik görünümü, yoklama,
koçluk ve mesajlaşma denetimi işlemleri; `PENDING`), eksik **0**. Bölüm
başına karşılanan işlem (bir işlem birden çok bölümde sayılabilir):
Öğrenciler (çekmece sekmeleriyle) 101 · Denemeler 30 · Değerlendirmeler 23 ·
Ayarlar 22 · Çalışma planı 22 · Kaynaklar 22 · Kurum finansı 20 · Müfredat 16
· Ders programı 16 · Okuma 14 · Bilgi kartları 13 · Erken uyarı 13 · Ödevler
11 · Panel 10 · Talepler 10 · Personel 9 · Duyurular 7 · Kurum takvimi 6 ·
Kitap talepleri 5 · KVKK talepleri 5 · Yoklama 3 · ortak arama listeleri
(`index.js`, `common.js`) 8.

Birleşim 2'nin getirdiği izin listesi farkı (wp/magaza → main): yeni
`students_getExternalExam` (tek dış deneme — Öğrenci → Denemeler →
"Dışarıdaki denemeler" çekmecesi açılışta güncel kaydı bu uçtan okur; öğrenci
/ veli silmişse sebebi yazar ve listeyi tazeler; uç okunamazsa — ör. Akademi
henüz eski sürümde — liste satırındaki bilgi not ile kalır) ve `admin_getExport`
izinlerine `attendance.export`, `coaching.export`, `messages.audit`
(üçü de `permission_map.py`'de eşli). KM olay alıcısı (`POST /v1/webhooks/km`)
yönetici işlemi değildir — KM'den panelle değil `mobile_gateway` olay
göndericisiyle çağrılır; durumu `admin_getDirectorySyncStatus` → `events`
bloğuyla Ayarlar → Senkron kartında görünür.

**0.5.4 (km-toplu-kitap):** izin listesine toplu ödev / ödev şablonu / atama
paketi / toplu müfredat / toplu kaynak ataması kaldırma (F-A25/F-A26, 21 işlem,
bbd-akademi main 86e6ddf) ve kitap satışı (F-A27, 10 `admin_*BookSale*` +
`students_listBookPurchases` / `students_getBookPurchase`, birleşim 3) girdi;
hepsi ekranda (`admin_createHomework` bilerek yok: tek ödev de sihirbazdan
verilir). `books.sell` → `bbd_akademi.books`. Müfredat → Program istisnaları
"Toplu program" (ekle / çıkar / kurala dön, süzgeç, önce say), Kaynaklar →
Atamalar çoklu seçim + "Toplu kaldır", Kurum ayarları → "Atama paketlerini yeni
öğrencilere kendiliğinden uygula" (`assignments.autoApplyBundlesToNewStudents`,
varsayılan kapalı). Ortak adım adım pencere `wizard.js` (adım listesi
`aria-current="step"`, odak adım başlığında, canlı bölge, Geri / İleri, Esc;
toplu iş izleme `admin_getBulkJob` 1,5 sn) ve "Kime?" seçicisi `bulk-target.js`
(seçerek · süzgeçle · paket kuralı, hariç tutulanlar). `formDialog` kitin
`dialogA11y`sini kullanır.

**0.5.5 adayı (km-toplu-secim):** "Kime?" seçicisinin öğrenci bloğu ve "Hariç
tut" artık KM'nin ortak seçicisidir (`common.js` → `studentSelect`, `ui-kit/picker.js`
uzak kip): liste açılışta gelir (`admin_listStudents`, sayfa 50, imleçli "Daha
fazla yükle"), arama ve sınıf süzgeci sunucuda (`q`, `classGroupId`; 250 ms
gecikme, tuş başına istek yok), görünenleri seç / yalnız seçilenler / seçili
sayısı, klavye, yükleniyor / hata ("Tekrar dene") / boş. Eski "en az 2 harf ·
açılır kutudan tek tek" (`studentPicker`, `position: absolute`) pencerede
kesiliyordu; tek öğrenci arayan ekranlarda (süzgeç, kart) kalır. "Hariç tut"
bölümü açılana dek istek atmaz. Sihirbaz gövdesi pencereye sığmazsa kendi içinde
kayar (not ve düğmeler yerinde). Kullananlar: toplu ödev, atama paketi uygula /
kaldır, toplu program, toplu kaynak ataması, toplu kitap satışı, plan / plan
şablonu, kaynak paketi hedefleri.

Belgeler (0.5.4, akademi-belge bileşeniyle): Kitap satışı ayrıntısında **Satış
belgesi** (`book_sale` — kitaplar, öğrenci satırları, teslim alan imza sütunu;
kapı `admin_getBookSale`), satırda bbdstore **sipariş / fatura PDF**'i aynı
önizleme penceresinde (`book_sale_order`); toplu ödevden sonra liste o seriye
süzülür ve **Ödev listesi** belgesi seriyi basar; toplu kalıcı silmenin sonuç
penceresinde **Sonuç belgesi** (`student_deletion`, KM verisi — kapı
`bbd_students.delete`; Öğrenci Yönetimi bunu `bbd_akademi.deletionDocument`
arayüz yeteneğiyle çizer, K3).

**Sözleşme kaynağı:** `tools/gen_allowlist.py` → `CONTRACT_REF = "main"`
(bbd-akademi deposunun `main` dalındaki commit'lenmiş `docs/api/openapi.yaml`).
Sözleşme değişince betik yeniden koşturulur, `--check` ile doğrulanır ve
`tools/ui_coverage.py` eksik 0 vermelidir.

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
| `bbd_akademi.books` | admin, bbd_staff | Kitap talepleri ve kitap satışı (`books.manage`, `books.sell`; satış kurum carisine yazar, kantinden ayrı) |
| `bbd_akademi.announcements` | admin, bbd_staff | Duyurular |
| `bbd_akademi.requests` | admin, bbd_staff | İzin, görüşme, etüt talepleri |
| `bbd_akademi.settings` | admin | Kurum ayarları, kantin kuralları, tema, bayraklar, anahtarlar, anons şablonları, dizin, denetim; koçluk ayarları; mesaj şikâyet kuyruğu ve yazışma denetimi (`messages.moderate`, `messages.audit`) |
| `bbd_akademi.staff` | admin | Mobil yönetici erişimi, Akademi personeli ve rolleri |
| `bbd_akademi.campus` | admin, bbd_staff | Öğrenci çağırma, tablet ek süre/engel/oturum |
| `bbd_akademi.canteen` | admin | Akademi üzerinden kantin işlemleri (yalnız vekil; asıl ekran Öğrenciler modülü) |
| `bbd_akademi.learning` | admin, bbd_staff | Günlük koçluk: plan görevi atama ve erteleme, koç notları, erken uyarı eylemleri; görev onayı, soru-cevap, check-in, tebrik |
| `bbd_akademi.learning_setup` | admin | Öğrenme araçları kurulumu: plan şablonları, okuma listeleri ve ligi, desteler, değerlendirme şablon/dönemleri, erken uyarı eşikleri; koç eşlemesi, koç kütüphanesi küratörlüğü, rozetler, koçluk dışa aktarımı |
| `bbd_akademi.evaluations` | admin, bbd_staff | Değerlendirme formu, yayımlama, karne dışa aktarımı |
| `bbd_akademi.privacy` | admin | KVKK talepleri: hesap silme / veri indirme taleplerine karar, silinmiş hesabın erişimini yeniden açma (Akademi `privacy.requests`). Yıkıcı: silme onayı kişisel veriyi geri dönüşsüz siler |

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

## Merkezi senkron (km-merkezi-senkron)

KM tek merkezdir; öğrenci, veli, kantin kontrolü, tablet ve zil değişiklikleri
KM'nin kalıcı olay kuyruğundan (`events.outbox`, ADR 0029) geçit
(`mobile_gateway`) tarafından imzalı webhook ile Akademi'ye itilir. Bu modülün
payı:

- **Senkron kartı** (Ayarlar → Senkron, `settings-sync.js`; hüküm mantığı
  import'suz `sync-logic.js`, Node testi): geçidin gönderici durumu
  (`GET /api/bbd_akademi/km/events` → isteğe bağlı yetenek
  `mobile_gateway.events`: son olay, iletilen, bekleyen, gecikme, son hata,
  ölü mektuplar) ile Akademi'nin `admin_getDirectorySyncStatus.events` bloğu
  (mod, alınan son `seq`, uygulanmamış, p95 gecikme) yan yana. İki uç arası
  fark > 100 `seq` ya da gecikme > 60 sn → sarı; `stopped` ya da 6 saat
  ulaşılamıyor → kırmızı; iletim ayardan kapalı (varsayılan) → gri (Akademi
  2 dk yoklamayla çalışır). Ölü mektupları **yeniden gönder**
  (`POST /api/bbd_akademi/km/events/retry {seqs}`). Kapı geçidin kendi
  iznidir: görme `mobile_gateway.events.view`, yeniden gönderme
  `mobile_gateway.events.manage` (K9; geçit yoksa ya da eski sürümse kart
  "geçit sürümü olay iletimini desteklemiyor" der). Sekme görünürken 20 sn'de
  bir tazelenir.
- **`akademi.student_finance` yeteneği** (`backend/finance.py`):
  `has_institution_debt("STU-…") -> bool` — `bbd_students` kalıcı silme ön
  koşulu "Akademi'de kurum cari borcu yok". `admin_listStudents
  ?externalRef=&status=inactive` (bulunamazsa süzgeçsiz) → Akademi kimliği →
  `students_getFinanceSummary.openBalanceMinor > 0`. Akademi'de kayıt yoksa ya
  da özet `404` ise borç yok; özet `403 feature_disabled` ise
  `admin_getFinanceAccount`'tan okunur (o da kapalıysa kurumda cari yok).
  Akademi'ye ulaşılamaz ya da yanıt belirsizse istisna fırlar → `bbd_students`
  `503 akademi_unavailable` döner, silme kapalı kalır.

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

## Belgeler — önizle / yazdır (kurum logolu A4)

Kantin raporlarıyla aynı zincir: **sunucu PDF üretir** (reportlab,
`backend/document_pdf.py`), önizleme o PDF'in sayfa görüntüleridir
(`pdftoppm`), **"Yazdır" PDF'in kendisini bu cihazın yazıcısına verir**
(`ui-kit/printing.js` → `/api/outputs/document`, kapısı çekirdeğin
`outputs.print` izni; yazıcı seçme penceresi açılmaz). Masaüstü kabuğu yoksa
(web) yazıcı düğmesi kapalıdır ve "PDF kaydet" kalır. Kâğıt her zaman beyazdır;
koyu tema belgeye karışmaz.

- **Başlık:** kurum logosu (Akademi yayımlanmış teması, PNG/JPEG; yoksa
  monogram), kurum adı + adres/telefon/e-posta (Akademi kurum ayarı), belge
  adı, belge no (`AK-<tür>-yymmdd-hhmm-xxxx`, toplu işte `-001…`), düzenlenme
  tarihi. Başlık 10 dk önbellekte; Akademi'ye ulaşılamazsa son bilinen ya da
  `document_title` ayarındaki adla belge yine çıkar.
- **Altbilgi:** düzenleyen (KM oturumu), KVKK gizlilik notu, parça içinde
  "Sayfa x / y". Tablo başlığı her sayfada tekrarlanır; taslak/iptal filigranı.
- **Uçlar:** `POST /api/bbd_akademi/km/documents/preview` `{kind, document,
  preview}` · `POST /api/bbd_akademi/km/documents/akademi` `{kind, url, name}`
  (Akademi'nin kendi makbuz/ekstre/kitap sipariş PDF'i; yalnız izinli kökenden,
  `%PDF` doğrulanır). Dosya `Raporlar/Akademi/<tür>/yıl/ay/` altına 0600 yazılır.
- **Kapı (K9):** tür, taşıdığı verinin Akademi işlemlerine bağlıdır
  (`backend/documents.py` → `KINDS`); kullanıcı o işlemlerin KM iznini taşımalı.
  Karne `evaluations`, kurum carisi `finance`, kitap sipariş PDF'i `books` ister.
- **KVKK:** telefon hiçbir belgede açık yazılmaz (panel `maskPhone` + sunucu
  ikinci kez maskeler); karnede okundu bilgisi yok; veli listesi sınıf seçimi
  ister. Kantin ve kurum carisi ayrı belgelerdir (A12).
- **Toplu iş (A14):** seçili öğrencilerin karneleri / sonuç belgeleri tek PDF;
  Akademi'ye aynı anda en çok 4 istek, ilerleme ve iptal; listeler sayfalı okunur.

| Ekran | Belge (tür) |
|---|---|
| Değerlendirme formu · dönem ayrıntısı | Öğrenci gelişim karnesi — tekil / seçilenler / yayımlanmış hepsi (`report_card`) |
| Denemeler › Sonuçlar | Sonuç listesi (`exam_results`), seçilenlerin sonuç belgesi (`exam_result`) |
| Denemeler › Analiz | Deneme analiz raporu (`exam_analysis`) |
| Öğrenci kartı › Denemeler | Öğrencinin deneme sonuç belgesi (`exam_result`) |
| Yoklama | Günlük tahta (`attendance_day`), ders çizelgesi (`attendance_sheet`), aylık çizelge (`attendance_matrix`), devamsızlık listesi (`absentee_list`) |
| Ders programı | Sınıf haftalık programı (`timetable_class`), öğretmen programı (`timetable_teacher`) |
| Ödevler | Ödev listesi (`homework_list`) / öğrenci süzülmüşse öğrencinin ödevleri (`homework_student`) |
| Öğrenci kartı › Çalışma planı | Haftalık çalışma planı (`study_plan`) |
| Öğrenci kartı › Kayıt | Özet kartı (`student_card`), ödevler, kantin dökümü (`canteen_statement`), kurum cari ekstresi |
| Kurum finansı | Hesap ekstresi (`finance_statement`), tahsilat makbuzu (`finance_receipt`) + Akademi PDF'leri |
| Kitap talepleri | Talep / teslim belgesi (`book_request`) + mağaza sipariş PDF'i |
| Öğrenciler | Sınıfın veli listesi (`guardian_list`) |

Şablonlar `ui/panel/documents-logic.js` (import'suz, Node testli), bileşen
`ui/panel/documents.js`. Testler: `test_akademi_documents.py`,
`test_akademi_documents_logic.py`, `test_akademi_documents_ui.py`.

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
