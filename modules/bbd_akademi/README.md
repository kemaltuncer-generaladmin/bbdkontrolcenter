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
   `staff_sync_minutes`, `step_up_seconds`, `read_retry_seconds` (Akademi
   güncellenirken okumaların yeniden deneme bütçesi, varsayılan 20; 0 = kapalı).

4. **Hazırlık (staging) ortamı:** `config/local.yaml` içinde
   `modules.bbd_akademi.base_url` hazırlık sunucusuna çevrilir ve hazırlık
   kiracısının anahtarı kasaya yazılır. Canlı anahtar hazırlıkta, hazırlık
   anahtarı canlıda kullanılmaz (Akademi anahtarı kiracıya bağlar). Panel
   açılışında anahtar yoksa ya da şalter kapalıysa ekranın tepesinde sebep
   yazar; vekil `503 akademi_not_configured` / `akademi_disabled` döner.

5. **Ders kayıtları (F-DK, 16.10.2026) için anahtar yenileme.** Ders kaydı
   uçları yeni Akademi izinleri ister (`boards.manage`, `recordings.manage`;
   Akademi README §14 madde 110 — `km_panel` kümesine backend paketiyle girer).
   Anahtarın izinleri oluşturulduğu an dondurulduğu için bu sürümden önce
   üretilmiş KM panel anahtarıyla Dersler ve Program → Ders kayıtları
   `403 forbidden` alır; ekran bunu görür ve ne yapılacağını yazar. Sıra
   KVKK talepleriyle aynıdır: Akademi'de dağıtım + `sync-roles` → yeni anahtar
   ("KM paneli" kümesi) → kasaya `bbd_akademi.api_key` → eski anahtarı iptal.

## Bilgi mimarisi (15.10.2026 — tek gezinme)

Kullanıcı isteği (15.10.2026): "kontrol merkezindeki tüm menüler ve o menülerin
içerisindeki işlevleri de elden geçirip güzelce sadeleştirip kolay kullanım, full
görselliğe girmeni istiyorum … bbd akademiye girince yan yana iki tane sol menü
görünüyor." Önceki düzende (0.5.2, aşağıda) panel KM kabuğunun sol menüsünün
yanına kendi ikinci sol menüsünü (altı üst bölüm + açılır alt ekranlar) çiziyordu.

**Tek sol gezinme.** Sol menü yalnız KM kabuğunundur; panel kendi sol menüsünü
çizmez. Kabuk modül başına tek menü satırı taşır (`ui.nav`; alt öğe ilanı yok —
K1/K6 gereği kabuğa dokunulmadı), bu yüzden alt gezinme panelin ÜSTÜNE alındı:

* **Ana grup şeridi** — simge + ad, en çok on grup (dershane yöneticisinin
  günlük akışı sırasıyla) ve başta "Ana sayfa"; kaymaz, dar pencerede sarılır.
* **Haplar** — seçili grubun ekranları ikinci satırda (grup tek ekranlıysa yok).
* **Ana sayfa** — panel buradan açılır: büyük kutucuklar (simge, ad, ne işe
  yaradığı, günün sayısı, grubun BÜTÜN ekranları) + hızlı işler (yoklama al, ödev
  ver, görev ata, kontrol kuyruğu, duyuru yaz, tahsilat al, öğrenci bul). Günün
  sayıları "Bugün" ile aynı özet yükünden (`admin_getDashboard`, 60 sn önbellek).
* Her ekran ana sayfadan **tek**, her yerden en çok **iki** tıkta (grup + hap).
* **Koçluk önde** (lider kararı): koçluk ekranının sekmeleri Koçluk grubunun
  doğrudan haplarıdır (`sections.js` → `host: 'coaching'` + `payload`); ekran
  (`coaching.js`) tek kez kurulur, hap sekmeyi seçer, ekranın kendi sekme şeridi
  gizlenir; ekran içinden sekme değişirse hap da değişir (`app.syncHosted`).
  Hap, sekmeyle AYNI okuma işlemlerine bakar (`reads`; test bağlar).

Kayıt tek yerde: `ui/panel/sections.js` (`AREAS`, `SECTIONS`, `ALIASES`,
`homeTiles`, `STUDENT_CARD`; import'suz, Node testli —
`tests/test_akademi_bilgi_mimarisi.py`); davranış `tests/test_akademi_tek_gezinme.py`.
Hiçbir işlev kaldırılmadı (`ui_coverage.py` eksik 0).

```
ÖNCE (0.5.2 — kabuk sol menüsü + panelin İKİNCİ sol menüsü)
Bugün ─────────────── Özet · Talepler · Veli toplantısı · Duyurular · Hoca soruları* · Mesaj denetimi*
Öğrenciler ─────────── Öğrenciler · Koçluk* (8 iç sekme: Kuyruk · Öğrenciler · Haftalık program ·
                       Koçlar · Kütüphane · Tebrik ve rozetler · Check-in · Görüşmeler)
Dersler ve Program ─── Ders programı ve etüt · Ders oturumları · Yoklama · Ödevler ·
                       Çalışma planı (+ Şablonlar sekmesi) · Şablonlar · Kurum takvimi
Ölçme ve Gelişim ───── Denemeler · Değerlendirmeler · Kaynaklar · Müfredat · Okuma ·
                       Bilgi kartları* · Erken uyarı*
Kantin ve Finans ───── Kurum finansı* · Kantin* · Kitap satışı* · Kitap talepleri*
Ayarlar ────────────── Kurum ayarları (… · SMS · …) · Personel · KVKK talepleri

SONRA (15.10.2026 — tek sol menü; panelde üst şerit + haplar + ana sayfa)
Ana sayfa ──────────── kutucuklar (her grup + bütün ekranları) · hızlı işler · günün sayıları
Bugün ─────────────── Özet · Talepler
Öğrenciler ─────────── Öğrenciler (KM yönetimi, sınıflar, veliler, öğrenci kartı)
Koçluk* ────────────── Kontrol kuyruğu · Haftalık program · Koçluk öğrencileri · Hoca soruları* ·
                       Mesajlar (denetim) · Görüşmeler · Koçlar ve atama · Kütüphane ·
                       Tebrik ve rozetler · Check-in
Dersler ve Program ─── Ders programı ve etüt · Ders oturumları · Kurum takvimi · Müfredat ·
                       Ders kayıtları* (16.10.2026 — akıllı tahta)
Yoklama ───────────── Yoklama (günlük · pano · raporlar · düzeltme kuyruğu · ayarlar)
Ödev ve Deneme ─────── Ödevler · Çalışma planı · Denemeler · Değerlendirmeler · Kaynaklar · Okuma ·
                       Bilgi kartları* · Şablonlar (ödev · atama paketi · plan şablonu)
İletişim ──────────── Duyurular · Veli toplantısı · SMS
Finans ve Kantin ───── Kurum finansı* · Kantin* · Kitap satışı* · Kitap talepleri*
Raporlar ──────────── Rapor merkezi · Erken uyarı*
Ayarlar ────────────── Kurum ayarları · Personel · KVKK talepleri
                                   * kurum özellik bayrağına bağlı
```

### 15.10.2026 eşlemesi (kaldırılan / birleştirilen / taşınan)

| Önce | Sonra |
|---|---|
| Panelin kendi sol menüsü (`.ak-nav`) | KALKTI — üstte grup şeridi + haplar (tek sol menü kabuğun) |
| Açılış ekranı Bugün → Özet | Ana sayfa kutucukları (Bugün bir tık) |
| Öğrenciler → Koçluk (iç sekmeler) | Koçluk grubu; her sekme bir hap (`coachQueue`, `coachWeek`, `coachStudents`, `coachMeetings`, `coachCoaches`, `coachLibrary`, `coachMotivation`, `coachCheckins`) |
| Bugün → Hoca soruları · Mesaj denetimi | Koçluk → Hoca soruları · Mesajlar |
| Bugün → Duyurular · Veli toplantısı | İletişim → Duyurular · Veli toplantısı |
| Ayarlar → Kurum ayarları → SMS sekmesi | İletişim → SMS (kendi ekranı, `sms.js`; gövde `settings-sms.js`) — BİRLEŞTİ/TAŞINDI |
| Dersler ve Program → Yoklama | Yoklama (kendi grubu) |
| Dersler ve Program → Ödevler · Çalışma planı · Şablonlar | Ödev ve Deneme |
| Çalışma planı → Şablonlar sekmesi | KALKTI — Şablonlar → Plan şablonları ile aynı gövdeydi (`{tab: 'templates'}` oraya gider) |
| Ölçme ve Gelişim → Denemeler · Değerlendirmeler · Kaynaklar · Okuma · Bilgi kartları | Ödev ve Deneme |
| Ölçme ve Gelişim → Müfredat | Dersler ve Program → Müfredat |
| Ölçme ve Gelişim → Erken uyarı | Raporlar → Erken uyarı |
| Ekran ekran dağınık raporlar | Raporlar → Rapor merkezi (`reports.js`): yoklama raporları, aylık ders dökümü, öğretmen haftası, deneme analizi, konu-hata analizi, plan uyumu, okuma raporu, koç performansı, aylık finans, gecikenler, denetim kaydı — kart raporun KENDİ ekranını o sekmeyle açar (kopya yok) |
| Kantin ve Finans | Finans ve Kantin (aynı dört ekran) |

Eski adlar çalışmaya devam eder (`ALIASES`): grup `progress` → `work`, `kocluk`
→ Koçluk, `kontrol-kuyrugu` → `coachQueue`, `koc-gorusmeleri` → `coachMeetings`,
`gorev-ata`/`haftalik-program` → `coachWeek`, `mesaj-denetimi` → `messages`;
`app.go('coaching', {coachingTab})` doğru hapı, `{studentId}` koçluk öğrencilerini
seçer; Ayarlar `{tab: 'sms'}` → İletişim › SMS.

### 0.5.2 sadeleştirme (13.10.2026 — tarihçe)

Kullanıcı isteği (13.10.2026): "Uygulama çok karışık olmaya başlamış, sadeleşip
basitleşsin" ve "öğrenci yönetimini de tamamıyla BBD Akademi kısmına taşıyalım".
Menü 21 düz bölümden altı görev odaklı üst bölüme inmişti (yukarıdaki "ÖNCE").

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
ana sayfa ve 38 hap Node'da axe benzeri denetimden geçer
(`tests/test_akademi_erisilebilirlik.py`, `tools/a11y`).

### 0.5.2 eşlemesi (eski menü → 13.10.2026 yeri; 15.10.2026 yerleri yukarıda)

| Eski menü / sekme | Yeni yeri |
|---|---|
| Panel | Bugün → Özet (rakamlar + "Sizi bekleyenler"; tablet/öğrenci çağırma katlı) |
| Talepler · Duyurular | Bugün → Talepler · Duyurular |
| Öğrenciler (Akademi listesi) | Öğrenciler → Uygulama kullanımı (yönetim yoksa "Öğrenciler") |
| Öğrenciler → Sınıflar · Veliler | Öğrenciler → Sınıflar · Veliler ve uygulama |
| Öğrenciler → Çalışma grupları / Gruplar | Öğrenciler → Sınıflar (sınıf = grup, iş 23: tek liste) |
| KM Öğrenci Yönetimi (ayrı menü) | Öğrenciler → Öğrenciler · Toplu işlemler · Sınıflar; öğrenci kartı → Kayıt ve veliler |
| Ders programı · Yoklama · Ödevler · Kurum takvimi | Dersler ve Program → aynı adlarla ("Ders programı ve etüt") |
| Çalışma planı (Öğrenme araçları) | Dersler ve Program → Çalışma planı |
| Denemeler · Müfredat · Kaynaklar (Akademik) | Ölçme ve Gelişim |
| Okuma · Bilgi kartları · Değerlendirmeler · Erken uyarı (Öğrenme araçları) | Ölçme ve Gelişim |
| Kurum finansı · Kitap talepleri (Kurum) | Kantin ve Finans |
| Ayarlar → Kantin kuralları | Kantin ve Finans → Kantin (+ KM kantin ekranlarına geçiş) |
| Ayarlar · Personel · KVKK talepleri (Yönetim) | Ayarlar → Kurum ayarları · Personel · KVKK talepleri |
| Ayarlar sekme sırası | Özellikler · Kurum · Tema ve logo · Anons şablonları · Senkron · Dizin eşitleme · Entegrasyon anahtarları · Denetim kaydı (SMS 15.10.2026'dan beri İletişim → SMS) |

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
açık). Ana grup başına bir satır (15.10.2026):

- **Ana sayfa** — büyük kutucuklar (her grup, günün sayısı, grubun bütün ekranları) ve hızlı işler.
- **Bugün** — günün rakamları ve bekleyen işler tek özet çağrısıyla; hızlı düğmeler (yoklama, ödev, tahsilat, duyuru, öğrenci çağır); izin/görüşme/etüt talepleri.
- **Öğrenciler** — öğrenci ekle/düzenle, sınıf ve gruba (toplu) ata, veliler; öğrenciye tıklayınca tek kart.
- **Koçluk** — kontrol kuyruğu, haftalık program ve görev atama, koçluk öğrencileri, hoca soruları, mesaj denetimi, görüşmeler, koçlar ve toplu atama, kütüphane, tebrik ve rozetler, check-in.
- **Dersler ve Program** — haftalık program ve etüt, ders oturumları (iptal, telafi, yerine öğretmen, puantaj), kurum takvimi, müfredat.
- **Yoklama** — günün yoklaması, pano, raporlar, düzeltme kuyruğu, ayarlar.
- **Ödev ve Deneme** — ödev verme (tek ya da toplu, üç adım) ve teslim, çalışma planı, denemeler ve sonuç yükleme, değerlendirme karneleri, kaynaklar, okuma, (açıksa) bilgi kartları, şablonlar (ödev, atama paketi, plan).
- **İletişim** — duyurular (uygulama + SMS), veli toplantısı randevuları, SMS sağlığı / bütçe / kuyruk.
- **Finans ve Kantin** — kurum carisi ve tahsilat, kantinin kurum kuralları (para ayrı), kitap satışı (üç adım) ve kitap talepleri.
- **Raporlar** — rapor merkezi (bütün raporlar tek kapıda) ve (açıksa) erken uyarı.
- **Ayarlar** — hangi özelliklerin açık olduğu, kurum ayarları, senkron, entegrasyon, personel ve KVKK.

## Bölümler

Panel bölümleri `ui/panel/sections.js` → `SECTIONS` dizisindedir (bölüm = bir
dosya + bir satır; ilk açılışta yüklenir; `area` üst bölümü, `feature` kurum
bayrağını verir). Görünürlük KM iznine, her düğme
işlemin izinli olup olmadığına göre çizilir; asıl kapı vekildedir.

| Bölüm | Dosya | İçerik |
|---|---|---|
| Ana sayfa | `home.js`, `home.css`, `nav-icons.js` | Açılış ekranı: grup kutucukları (simge, açıklama, günün sayısı — `admin_getDashboard` 60 sn önbellek —, grubun bütün ekranları tek tıkta; Koçluk geniş ve vurgulu), hızlı işler. Gezinme kabuğu `index.js` (üst şerit + haplar) |
| Raporlar → Rapor merkezi | `reports.js` | Bütün raporların tek kapısı: kart raporun kendi ekranını o sekmeyle açar (yoklama raporları, aylık ders dökümü, öğretmen haftası, deneme analizi, konu-hata analizi, plan uyumu, okuma raporu, koç performansı, aylık finans, gecikenler, denetim kaydı); görünmeyen ekranın kartı yok |
| İletişim → SMS | `sms.js` (+ `settings-sms.js`) | Eski Ayarlar → SMS sekmesinin kendi ekranı: SMS sağlığı ve paket, kurum SMS bütçesi ve ayarları, kurum SMS kuyruğu, OTP günlüğü, telefon sayacı sıfırlama |
| Bugün → Özet | `dashboard.js` | Tek özet çağrısı: günün dersleri, yoklama, kurumda olanlar, bekleyen talep ve kitap; kurum carisi ve kantin AYRI kartlarda; tablet panosu ve kullanım; öğrenci çağırma |
| Öğrenciler | `students*.js` | Akademi öğrenci listesi (seviye/alan/hedef salt okunur — Öğrenciler modülünde düzenlenir), öğrenci detayı: özet, program, müfredat ve ilerleme, kaynaklar, ödevler, denemeler (karne), yoklama, çalışma, öğrenme (plan, hata defteri, soru, okuma, ajanda — F-A24), hedef & özet, koçluk (koç — eşleme, koç notları, değerlendirmeler, öz değerlendirme, erken uyarılar), bilgi kartları, talepler, kurum cari, kantin (salt okunur); sınıflar, çalışma grupları, veliler. KM'de **pasifleştirilen** öğrenci varsayılan listede yoktur, “Durum → Pasif (KM)” süzgeciyle (`status=inactive`) görünür; çekmecesi salt okunurdur (yazma düğmeleri gerekçeli kapalı — kurum cari tahsilatı ve dışa aktarım açık) |
| Dersler ve Program → Ders kayıtları | `lesson-recordings.js`, `lesson-recordings-logic.js`, `lesson-recordings.css` | Akıllı tahta ders kaydı (F-DK, Akademi README §14 madde 110; bayrak `lesson_recordings` kapalıyken menüde yok). Web / mobil yönetici ile AYNI uçlar ve metinler (tasarım `DkYonetim*`). Dört sekme: **Tahtalar ve derslikler** (tahta ekle → tek seferlik eşleme kodu; durum — çevrimiçi / kayıtta / çevrimdışı / eşleme bekliyor / iptal —, mikrofon ve türü — harici mikrofon zorunlu, Bluetooth HFP ve yedeğe düşme uyarısı —, son görülme, sürüm; eşleme kodu / yeniden eşle, kodu iptal, iptal et, düzenle, sil; ayrıntıda tanılama raporu + "Tanılama iste"; derslik ekle / düzenle / sil — ad ders programındaki derslik metniyle eşleşir; üstte "Kayıt sürüyor" bandı), **Kayıtlar** (arama — derslik, ders, öğretmen; `q`, en az 2 harf —, Tümü / Yayında / Gizli / Kurtarılmış / Kayıtta / Başarısız, tarih, öğretmen, sınıf, sıralama; ayrıntı `admin_getRecording` ile tazelenir — bildirimden yalnız kimlikle açılınca da dolu —; ayrıntıda gizle / göster, kaydı sil, kim izledi, ek öğrenci / grup paylaşımı + bildirim), **Depolama** (800 GB çubuğu %80 / %90 işaretli, tahmini dolma günü, son temizlik), **Ayarlar** (saklama ≤ 120 gün, tavan ≤ 800 GB, kalite 1080p / 720p, kamera sahneleri; süre / tavan düşerse onay). Video baytı KM'den geçmez; oynatıcı web / mobilde |
| Müfredat | `curriculum.js` | Katalog (seviye/sınav/ders), kuruma özel program/ünite/konu, sürümler, program çözümleme, öğrenci/sınıf istisnaları |
| (ortak) | `curriculum-picker.js` | Program / konu seçicisi (ödev, plan görevi, deneme, deste, kaynak içindekiler): etiket seviyeyi taşır ("11. sınıf · Matematik"), seviye sırası (5→8, LGS, 9→12, TYT, AYT), ödevde hedefin (sınıf / grup / öğrenci) seviyesinden önerilen program kendiliğinden seçilir (ortak ödev içerik alanları `homework-content.js` üzerinden: tek ödevde formdaki hedef, toplu ödevde "Kime?" seçildikten sonra; şablonda hedef yok → seviye çipleri); atama paketinin program listesi de seviyeli; seviye sınıftan gelir (Öğrenciler → Sınıflar). Konu ağacı yalnız konu kimliği seçtirir |
| Kaynaklar | `resources*.js` | Hazır kütüphane, kurum kaynakları (ISBN, mağaza kataloğu), içindekiler ve konu eşlemesi, "konulardan bölüm üret", paketler + otomatik atama + şimdi uygula, atamalar, ilerleme tablosu |
| Ders programı | `timetable*.js` | Zil planı durumu kartı (14 gün: kaynak, bugünün kipi, çağrı ve ses, zil ajanı, acil kapatma; "Planı şimdi it"), zil dilimine hizalı haftalık ızgara (çakışma gösterimi, kopyalama, **"Çakışmaları denetle"** — kaydetmeden öğretmen/derslik/öğrenci çakışması, öğretmensiz satır, öğretmen yükü), etüt ve tek seferlik dersler, zil dilimleri, dönemler, **program şablonları** (`timetable-templates.js`: oluştur — boş ya da bir sınıfın programından; ortak sınıflar ve müfredat da alınır —, düzenle — satır başına ortak sınıflar (en çok 12) ve isteğe bağlı müfredat, Akademi README §14 madde 72 —, sil, sınıflara uygula + ön denetim; yalnız KM) |
| Ders oturumları | `sessions.js` | Tek tek dersler (KM-3): oturum takvimi (sınıf, öğretmen, durum, tür, yoklama süzgeci; 31 gün), oturum çekmecesi — yerine öğretmen, derslik, saat, zil çağrısı, gerekçeli iptal (yoklaması alınmış derste önce yoklamayı silmeye yönlendirir), telafi, iptali geri al —, öğretmen haftası ve ders yükü, **aylık ders dökümü (puantaj)** — CSV özet / ders ders döküm, **Ayı kapat** (biten ay; yoklaması alınmamış ders bilerek onaylanır, not) ve kapanmış ayda kayıtlı sayılar + kapanıştan sonraki yoklama düzeltmesi fark tablosu, gerekçeli **Yeniden aç** (Akademi README §14 madde 90, İ7). Çoklu sınıflı derste "8-A + 8-B". Zil modülüne yazmaz: planı Akademi yeniden üretir, KM Zil Sistemi her zamanki eşitlemesiyle alır |
| Kurum takvimi | `institution-calendar.js` | Tatil, etkinlik, veli toplantısı, deneme günü, kayıt dönemi; hedef kitle, bağlı duyuru ve hatırlatma (F-A20); "Zil" alanı (normal / tatil — zil çalmaz / KM saat seti) ve "dersleri iptal et" — sözleşme şalteri açılana dek devre dışı; **"Etkisini önizle"** (kaç ders tatil/iptal olur, yoklaması alınmış dersler, zil planı değişen günler) |
| Yoklama | `attendance*.js` | Beş sekme (KM-2): **Günlük yoklama** (günün oturumları, yoklama listesi; listede geçmiş, listeyi yenile, gerekçeli "yoklamayı sil", öğrenci "çıktı", öğretmene hatırlat; düzeltme penceresi kapandıysa gerekçeli düzeltme; KM sunucusuna ulaşılamazsa yoklama bu bilgisayarda bekler ve `admin_syncAttendance` ile gönderilir), **Pano** (günün tahtası, alınmamış dersler + hatırlat, erken çıkışlar, varlık ipucu tutarsızlıkları — `attendance_signals` kapalıysa açık not), **Raporlar** (devam oranı ders/öğretmen/sınıf, öğretmen uyumu, izin nedenleri, dışa aktarım), **Düzeltme kuyruğu** (geç senkron onay/ret; öğrenci adı ve sınıfı satırın `student` alanından — oturum listesi ayrıca okunmaz, dizinde olmayan öğrencide kimlik gösterilir, madde 72), **Ayarlar** (okuma + KM'ye özel kaydetme). Öğrenci kartında devam takvimi + gün ayrıntısı, dönem karnesi, veli adına izin (`students-attendance.js`); sınıf çekmecesinde ve öğrenci kartında tarihli sınıf üyelikleri (`students-class-history.js`) |
| Ödevler | `homework.js`, `homework-bulk.js`, `homework-content.js` | "Ödev ver" = üç adımlı sihirbaz (ne → kime → ne zaman; şablondan ya da yeni, birden çok ödev, tek / birkaç tarih / her hafta / şablon süresi, otomatik önizleme ve çakışmalar, büyük iş arka planda); seçilenlere toplu ertele / tarih / iptal / sil; toplu seri süzgeci; teslim tablosu, hatırlatma |
| Şablonlar | `templates.js` (+ `bulk-target.js`, `wizard.js`) | Ödev şablonları (kurum şablonu; yeni / düzenle / kopyala / sil / "bu şablonla ödev ver"), atama paketleri (yalnız KM tanımlar: program + kaynak + kaynak paketi + plan şablonu + kural + otomatik; uygula / kaldır — seçerek · süzgeçle · kuralına uyan herkes, hariç tutulanlar, önce say, 300+ öğrencide arka plan işi), plan şablonları |
| Denemeler | `exams*.js` | Tanımlar, geçmiş deneme, toplu yükleme sihirbazı (yükle → sütunlar → eşleme + yayınevi şablonları → önizleme ve belirsiz eşleşme çözümü → kaydet / geri al), cevap anahtarı ve soru→konu eşlemesi, tekil sonuç, yayımla, analiz, karşılaştırma, dışa aktarım |
| Çalışma planı | `plan*.js` | Plan görevi atama (öğrenci/grup/sınıf), plan şablonları ve sınıfa uygulama, uyum tablosu, erteleme talepleri (F-A18) |
| Okuma | `reading*.js` | Kurum okuma listeleri, hazır listeler (tür süzgeci; hazır şablondan oluştur), okuma ligi, öğrenci × ay raporu (F-A19) |
| Bilgi kartları | `decks.js` | Kurum desteleri (ders/konu, görselli kartlar), sınıf/grup/öğrenci ataması (F-A23) |
| Değerlendirmeler | `evaluations*.js` | Şablonlar (bölüm, kriter, ölçek, otomatik veri), dönemler ve tamamlama durumu, değerlendirme formu (otomatik veri yan paneli), yayımla / geri al, gelişim karnesi PDF (F-A21) |
| Erken uyarı | `insights*.js` | Kurallı risk listesi ve eylemler (not, görüşme, veliye bildirim, çöz / yeniden aç), eşikler, en çok hata yapılan konular ve nedenler (F-A22) |
| Kurum finansı | `finance.js`, `finance-closing.js` | Cari hesaplar, peşinatlı taksit planı, kalemler, tahsilat (seçili kalem / serbest tutar FIFO), PIN teyitli iptal, gecikenler + hatırlatma, ekstre ve makbuz; indirim / burs, ayrılma (okunan süre hesabı: ödenen − katılım süresi oranında hizmet bedeli − kurum kesintisi, gerekçeli düzeltme) / kayıt dondurma ve "Kaydı yeniden başlat" (taksitler yeni vadelere), iade kaydı + iade makbuzu, ödeme planı belgesi (kayıt sözleşmesi eki, veli + kurum imza alanı), aylık rapor, dışa aktarım — **kantinden tamamen ayrı (A12)** |
| Kantin | `canteen.js` | Kantinin kurum kuralları (F-A15; gövdesi `settings.js` → `canteenTab`) ve KM kantin ekranlarına geçiş; kantin parası kurum carisinden ayrı (A12) |
| Kitap satışı | `book-sales.js` | "Kitap sat" üç adım (kitaplar → öğrenciler → ödeme: cari / nakit / kart POS / veliye ödeme bağlantısı), satış listesi (açık / tümü, öğrenci süzgeci), ayrıntı (öğrenci satırları, seçilenleri / hepsini teslim et), satır: siparişi yeniden dene, mağaza durumunu yenile, ödeme bağlantısını yeniden gönder + kopyala, sipariş/fatura PDF, gerekçeli iptal / iade, geçmiş; öğrenci kartında kurumdan alınan kitaplar — **para yalnız kurum carisinde (A12)** |
| Kitap talepleri | `books.js` | Kuyruk, siparişi yeniden dene, teslim, sipariş PDF'i |
| Duyurular | `announcements.js` | Taslak → hedefle → şimdi ya da zamanlı yayımla, okunma oranı |
| Talepler | `requests.js` | İzin, görüşme ve etüt talepleri |
| Veli toplantısı | `parent-meetings.js` (Node + DOM testi `test_akademi_iletisim_puantaj.py`) | Akademi README §14 madde 90 (İ8): öğretmenlerin veli görüşmesi saatleri gün gün (öğretmen süzgeci; dolu · boş, öğrenci (sınıf) · veli, not), **Saat aç** (öğretmen adına; gün + saat aralığı → süre + ara ile eşit saatler, yer, şekil, bütün velilere açık; kaç saat açılacağı önizlenir), **Veli adına randevu** (telefonla arayan veli; öğrenci seçici), **Saati kapat** (boş saat silinir) / **Randevuyu iptal et** (görüşme iptal, veliye not), **Listeyi yazdır** (günün PDF'i: saat, öğrenci, veli, not, imza; belge türü `parent_meeting_list`). Randevu Akademi'de planlanmış görüşme talebidir — Talepler → Görüşme'de de görünür. İzin `parent_meetings.manage` → KM `requests` |
| Hoca soruları | `questions.js` (saf mantık import'suz `questions-logic.js`, Node testi) | Öğrencinin hocaya sorusu (Akademi §14 madde 63; koçu yoksa dersin öğretmenine / öğretmen havuzuna gider): süzgeç (bekleyen, geciken, yanıtlanan, kapalı, tümü) + öğretmen, SLA rozeti (geciken kırmızı, 4 saatten az sarı), ayrıntı çekmecesi (soru, görseller, önceki yanıtlar — sesli not oynatıcısı 1x / 1.5x / 2x); **yanıt yaz**: yanıtlayan öğretmen (varsayılan sorumlu hoca; KM öğretmen ADINA yazar, denetimde KM kullanıcısı kalır), metin, görsel / PDF / m4a ekleri ya da tarayıcıdan m4a kayıt (MediaRecorder `audio/mp4`; desteklemeyen tarayıcıda kapalı), iletişim bilgisi uyarısında "yine de gönder"; yönlendir, kapat; **Soru ayarları**: öğretmen havuzu ve veliye bildirim (`admin_putCoachingSettings`, iyimser kilit). Bayrak `coach_questions` kapalıysa menüde yok |
| Mesaj denetimi | `messages.js` (saf mantık import'suz `messages-logic.js`, Node testi `test_akademi_mesaj_denetimi.py`) | KM-1 (F-K15 moderasyon, F-K21 denetim; PLATFORM §5 kural 6). **Şikâyetler**: kuyruk (bekleyen / işlem yapılan / reddedilen), ayrıntıda şikâyet edilen mesajın öncesi-sonrası (en çok 10 + 10; mesaj işaretli), **karar** (reddet · göndereni uyar · mesajı gizle · konuşmayı kilitle; not zorunlu), konuşma kilidi / kilit açma (gerekçeli), "Konuşmanın tamamını denetle". **Denetim araması**: gerekçe zorunlu (en az 10 karakter, vaka no isteğe bağlı; gerekçe URL'ye konmaz — POST), süzgeç öğrenci · personel · tarih (≤ 1 yıl) · konuşma türü · metin (≥ 3) · yalnız işaretli; konuşmayı denetimde açar (silinen mesajın özgün içeriği, düzenleme geçmişi) ve PDF dışa aktarır. Her erişim Akademi denetim kaydına yazılır. İzin `messages.moderate` / `messages.audit` → KM `settings`; bayrak `coaching` kapalıysa menüde yok. Şikâyet nedeni metinleri Flutter `ReportReason` / React `REPORT_REASONS` ile aynı **Saklama süresi** (başlık düğmesi): yazışmaların ilişki / öğrenim bitişinden kaç yıl sonra silineceği, 1–10 yıl (vars. 2; Akademi gece işi `messaging_retention`, README §14 madde 84; `admin_putCoachingSettings`, iyimser kilit; KM izni koçluk ayarı yazma) |
| Koçluk | `coaching*.js` (saf mantık import'suz `coaching-logic.js`; Node + DOM testi `test_akademi_kocluk.py`) | KM-4 (K1 koçluk bloğu) + F-K17 görüşmeler + F-K10 video; **öğretmen = koç**, KM her koç işini koç ADINA yapar. Sekmeler (izinli olan ve kurumda açık olan çizilir, dosya ilk açılışta yüklenir): **Kuyruk** — koç iş kuyruğu (aciliyet sırası, koç süzgeci) ve kontrol kuyruğu (D/Y/B, süre, zorluk, "anlamadığım yer var", fotoğraf kanıtı, video izlenme; onayla · tekrar iste (gerekçe + yeni gün) · yorum; seçerek ya da süzgeçle toplu onay, en çok 200); **Öğrenciler** — koçluk listesi (uyum, risk, bekleyen, son deneme) ve 360° koçluk kartı (hedef öner / geri çek, tebrik ve rozet, öğrenciye özel check-in soruları, görüşme planla, dönem raporu PDF'i, öğrencinin kütüphanesi, sorular, "Öğrencinin gördüğü"); **Haftalık program** — öğrenci × hafta geniş ızgara (ders programı, tatil, çalışma saati gölge katman; kapasite uyarısı), görev oluşturucu (raf + bölüm, konu, sayfa/soru aralığı, talimat, öncelik, kanıt, başarı ölçütü, YouTube / kütüphane videosu + kesit + durak sorusu, foto/PDF eki), her değişiklik tek istek (`expectedVersion`), taslak → **Yayımla**, geçen haftadan devret, kopyala (önce deneme), PDF, **şablonla toplu ata** (kurum plan şablonu, önizleme → arka plan işi); **Koçlar** — yük tablosu (kapasite aşan kırmızı), koç profili (yalnız KM), performans metrikleri, **toplu koç ata** (sınıf/grup/seviye/öğrenci → önizleme → uygula; koçu değişene gerekçe zorunlu); **Kütüphane** — kurum öğesi ekle/düzenle/kaldır (video, kesit, liste, bağlantı, dosya, not, görev paketi), koç önerilerine karar; **Tebrik ve rozetler** — rozet tanımları (simge = uygulama ikon anahtarı), haftalık kutlama adayları (uyum ≥ %90) ve toplu tebrik; **Check-in** — kurum soru seti (en çok 5) ve haftanın check-in'leri + koç yorumu (bayrak `self_review`); **Görüşmeler** — karar (onayla, reddet, ertele, iptal, yapıldı, gelinmedi), not + eylem maddeleri, eylem maddesi → görev / hedef önerisi (bayrak `coach_meetings`). Başlıkta **Dışa aktar** (koç performansı, dönem raporları, haftalık programlar). Bayrak `coaching` kapalıysa menüde yok |
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
koçluk ve mesajlaşma denetimi işlemleri; `PENDING`), eksik **0**.
**KM-2/KM-3 (dal `ozellik/km-yoklama-program`):** yoklama (22), program kurucu ve
oturumlar (14), tarihli üyelik (3) ve takvim etkisi (1) — 40 işlem `PENDING`'den
çıktı; sırada kalan yalnız koçluk ve mesajlaşma denetimi (70, `km-koclik` işi). Bölüm
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

**0.5.5 (km-toplu-secim):** "Kime?" seçicisinin öğrenci bloğu ve "Hariç
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

**0.5.5 (mufredat-duzeltme, birleşim km-055):** program / konu seçicisi seviyelidir
(`curriculum-picker.js`; "11. sınıf · Matematik", seviye sırası, seviye çipleri,
arama, yalnız KONU kimliği). Ödev içerik alanları ortak `homework-content.js`'tedir;
seviye önerisinin hedefi `homeworkContent(..., {target})` ile verilir: tek ödev
formunda formdaki Hedef, toplu ödevde "Kime?" seçimi (adım 1'e dönülüp ödev
düzenlenince öneri o hedefin seviyesinden gelir), ödev şablonunda hedef yok
(seviye çipleri). Atama paketinin program listesi de seviye sıralı ve seviyeli
adlıdır. Öğrenciler'de "Sınıflar" sekmesi (KM sınıf tanımı: seviye / alan / hedef
sınav) ile "Dönem ve rehber" (Akademi sınıf eşlemesi) ayrıdır; gömülü KM
yöneticisi içeriden sekme değiştirince (ör. "Sınıfların seviyesi →") Akademi
şeridi de o sekmeyi gösterir (`onMode`).

**0.5.6 (sms-kale, Akademi README §14 madde 58):** Ayarlar → **SMS** sekmesi (15.10.2026'dan beri
İletişim → **SMS** ekranı, `sms.js`)
(`settings-sms.js`, okuma `admin_getSmsHealth`): son 1 saatin OTP SMS teslim oranı,
ortalama / en uzun teslim, geciken ve başarısız sayısı, Netgsm SMS paketi; maskeli OTP
SMS günlüğü (`admin_listOtpSmsLog`, teslim durumuyla) ve bir telefonun saat / gün
sayaçlarını sıfırlama (`admin_resetSmsLimits` — Akademi + KM geçidi birlikte, denetim
kaydı `sms.limits_reset`). İzin `integrations.manage`. Geçit tarafı (jobId, hat, teslim
izi, göç 003, `GET /sms/otp/{jobId}`, `POST /sms/limits/reset`) `mobile_gateway`
README'sindedir.

**0.5.8 (ogretmen-koc-tek, kullanıcı kararı 14.10.2026 "Koç = öğretmen"):** öğretmen ve koç
TEK kavramdır — Akademi rolü `teacher` ("Öğretmen / Koç"); ayrı koç rolü, listesi ya da koç
tanımı adımı yoktur. Personel → Mobil yönetici erişimi rolleri **Yönetici**, **Öğretmen / Koç**
(çoklu seçim: bir kişi hem yönetici hem öğretmen / koç olabilir; uygulamada rolünü seçerek
geçer). Öğrenci detayı → Koçluk → **Koç** iç sekmesi (`students-coaching.js`): öğrencinin
koçu ve eşleme geçmişi (`admin_listStudentCoachHistory`), **Koç ata**
(`admin_createCoachAssignment`), **Koçu değiştir** (`admin_changeCoach`, gerekçe + devir
notu), **Eşlemeyi bitir** (`admin_endCoachAssignment`); seçici `admin_listCoaches` = Öğretmen /
Koç rolündeki bütün etkin personel (yönetici + öğretmen olan dahil), yanında üstlendiği öğrenci
sayısı; kapasite aşımı uyarılır, engellenmez. Rehber öğretmen bir rol değil sınıf eşlemesidir
(Sınıflar → Dönem ve rehber; seçici öğretmenler). Akademi tarafı: aynı telefondaki **yerel**
personel (kurulumda CLI ile açılan ilk yönetici) KM itmesinde artık sahiplenilir
(`phone_conflict` yerine `source=km`, denetim `staff.adopt`) — `backend/staff.py` başlığı.

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
| `bbd_akademi.academic` | admin, bbd_staff | Program (çoklu sınıflı ders, isteğe bağlı müfredat), zil, etüt, ödev, dönem, sınıf eşlemesi, hedef önerisi |
| `bbd_akademi.curriculum` | admin | Kuruma özel müfredat, program istisnaları; öğrencinin müfredat seti ve sınıf müfredatı (`curriculum.assign` — öğrenci kartı → Müfredat ve gelişim, Öğrenciler → Dönem, öğretmen ve müfredat) |
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
| `bbd_akademi.recordings` | admin, bbd_staff | Ders kayıtları (akıllı tahta): tahta ekleme, eşleme kodu, iptal, tanılama; derslikler; kayıtları gizleme / silme, kim izledi, paylaşım; depolama kotası (Akademi `boards.manage`, `recordings.manage`). Kayıt AYARLARINI yazmak (`admin_putRecordingSettings`) kurum ayarıdır → `bbd_akademi.settings` |
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
- **Akademi güncellenirken** (dağıtım anı; ağ geçidi JSON'suz 502/503/504 ya
  da bağlantı yok): köprü OKUMALARI kısa geri çekilmeyle (0,5 → 1 → 2 → 3 → 4 → 5
  sn) toplam `read_retry_seconds` içinde yeniden dener; olmazsa `503
  akademi_unavailable` + `details.retryable: true`. Panel bunu görünce üstte
  "Akademi şu an güncelleniyor, birkaç saniye sonra otomatik yeniden
  denenecek." bandını gösterir (`ui/panel/updating.js`), son okumayı geri
  sayımla (5, 10, 15 … 30 sn) yeniden dener; Akademi dönünce bant kalkar ve açık
  ekran yeniden kurulur (pencere/çekmece açıksa yalnız "Yenile" önerilir).
  **Yazmalar yeniden denenmez** (`details.retryable: false`, "kaydedilmemiş
  olabilir"); form açıkken aynı `Idempotency-Key` ile elle tekrar göndermek
  güvenlidir (Akademi ilk sonucu döndürür). Akademi'nin kendi JSON hata zarfı
  (bakım 503'ü dahil) yeniden denenmez; zaman aşımı da denenmez.
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
