# Öğrenci Yönetimi

Öğrenci kayıtları, veli iletişimi, kart ve bakiye durumu. Kantin otoritedir:
ad, veli 1 telefonu, bakiye ve kantin kontrolleri kantinde durur; kantinde
karşılığı olmayan alanlar (ad/soyad ayrımı, sınıf, okul no, öğrenci telefonu,
ikinci veli, not, seviye/alan/hedef sınav) modülün kendi tablosundadır.

Grup: **BBD** · İzinler: `bbd_students.view`, `bbd_students.manage`,
`bbd_students.qr`, `bbd_students.access_code`, `bbd_students.canteen_controls`,
`bbd_students.groups`, `bbd_students.lifecycle`, `bbd_students.delete`

## Ekran BBD Akademi'de (13.10.2026)

Kullanıcı isteği: "Öğrenci yönetimini tamamıyla BBD Akademi kısmına taşıyalım."
Öğrenci listesi, toplu işlemler ve gruplar **BBD Akademi → Öğrenciler**'de;
öğrencinin tek kartında **"Kayıt ve veliler"** sekmesi bu modülün kaydıdır.
VERİ SAHİPLİĞİ DEĞİŞMEDİ: kayıt burada, kantin otorite; yazmalar bu modülün
uçlarından (önce KM, sonra kantin, outbox olayı). Bağ ARAYÜZ yetenekleriyle
(K3; `ui/panel/index.js` → `capabilities()`):

- `bbd_students.manager` `(root, {capability, openStudent, hideModes})` → temizlik
  işlevi (`.setMode('list'|'bulk'|'groups')`): liste + toplu işlemler + gruplar.
- `bbd_students.record` `(root, {kantinId, capability, toast, onChanged, onLoaded,
  onRemoved})` → `{reload, dirty, destroy}`: tek öğrencinin kaydı (`record.js`).

**Tek giriş noktası (0.5.3, ADR 0031).** "Öğrenci Yönetimi" menüde ÇİZİLMEZ
(`ui.nav.replacedBy: bbd_akademi.students`); ekran kayıtta kalır — yetenekleri
yüklenir, eski derin bağlantılar (`{kantinId}`) Akademi → Öğrenciler'e
yönlenir. BBD Akademi menüsü `bbd_students.view` taşıyana da görünür; Akademi
izni olmayan roller (`accountant`, `org_staff`) paneli yalnız "Öğrenciler"le
(KM kaydı) görür. BBD Akademi modülü kapalıysa bu ekran menüye geri döner ve
yönetim burada aynı bileşenlerle yapılır. İzinler değişmedi (`bbd_students.*`).

### Hızlı güncelleme (0.5.3)

Kullanıcı: "Adam bir öğrenciyi güncellemek için saatlerce uğraşıp durmasın."

- **Küresel arama** (kabuğun üst çubuğu, her ekrandan `/`): ad (Türkçe harfsiz
  de), okul no, veli / öğrenci telefonu (son hanelerle de), kantin kartı
  (öğrenci kimliği ya da kartın QR metni — okuyucu klavye gibi yazar). Sonuç
  Enter / tek tıkla öğrencinin kartını "Kayıt ve veliler"de açar.
  `GET /search?q=&limit=` (`bbd_students.view`; dizinin anlık görüntüsünden,
  kantine gitmez; telefon maskeli) — kural `backend/search.py`. Arayüz
  yeteneği `bbd_students.search`; kartı açan `bbd_students.open` (tablet ve
  ödeme talebi ekranları da bunu çağırır — öğrenci kaydının kopya düzenleyicisi yok).
- **Satır içi düzenleme** (`ui/panel/record.js`, kural `quick.js`): tıkla ·
  yaz · Enter (ya da alandan çık) → yalnız o alan `PATCH` edilir; "Geri al"
  eski değeri yazar; Esc vazgeçer; seçim kutusu seçilince kaydeder. Hata
  alanın altında, `aria-describedby` ile bağlı. Veli listesi (ekle / sırala /
  sil) "Velileri kaydet" ile birlikte gider.
- **Hızlı işlem şeridi** (kartın tepesi): sınıf, birincil veli telefonu,
  günlük kantin limiti (Enter), tablet kısıtı (`bbd_tablet.student_quick`),
  pasifleştir. Kısayollar: `e` ilk alanı düzenler, Esc vazgeçer.
- **"Akademi'ye ulaştı ✓"**: kayıttan hemen sonra outbox'ın son sırası
  (`GET /api/events/wait`) okunur, geçidin teslim imleci onu geçince gösterilir
  (yoklayıcıyı BBD Akademi verir: `/api/bbd_akademi/km/events`, izin
  `mobile_gateway.events.view`; izin yoksa gösterge çizilmez).
- **Listede toplu düzenleme**: satırları işaretle (ya da "görünenleri seç") →
  alan (ana sınıf, alan / hedef sınav — öğrenciye özel istisna, gruba ekle,
  pasifleştir) → değer → Enter / Uygula. Mevcut toplu uçlar; öğrenci başına
  olay. Seviye toplu düzenlemede yoktur: sınıftan gelir (aşağıda).

Etkileşim sayısı (yazma hariç; tek alan): 0.5.2'de menü → BBD Akademi →
Öğrenciler → arama → satır → "Kayıt ve veliler" → alan → Kaydet = **7**;
0.5.3'te kart açıkken alan → Enter = **2**, herhangi bir ekrandan `/` → Enter
→ alan → Enter = **4**. Kayıt → Akademi isteği ölçümü ~0,2 sn
(`tests/integration/test_ogrenci_senkron_gecikme.py`).

### Veliler (göç 007)

En çok **4 veli**; sıra önceliktir, **ilk veli birincildir** (yuva 1: telefonu
kantinde — kantin kartı ve veli kantin kontrolleri bununla). Yuva 2 profilde
(`parent_name2`, `parent_phone2`), 3–4 `guardians_extra` (JSON). Her velinin
yakınlığı (anne, baba, vasi, büyükanne/büyükbaba, kardeş, diğer). Telefon
zorunlu ve cep olmalı (`+90 5xx xxx xx xx`; yazımda `0`, `+90`, boşluk serbest);
aynı öğrencide aynı telefon iki kez olamaz; **aynı telefon başka öğrencinin
velisi olabilir** (kartta "bu telefon X için de veli" notu). Kural tek yerde:
`backend/guardians.py`; ekranın aynısı `ui/panel/guardian-model.js` (Node testli).

- `GET /students/{id}/guardians` → `{items[{slot, name, phone, relation, primary,
  appAccess, siblings}], max, appSlots, relations}` (`bbd_students.view`)
- `PUT /students/{id}/guardians {items:[{name, phone, relation}]}` — tam liste
  (`bbd_students.manage`; 422 `validation_error` + `details[{index, field}]`).
  Değişmediyse yazılmaz. Birincil değişince 1. yuva telefonu kantine gider.
- Olaylar: yuva 1–4 telefonu `guardian.linked|unlinked|phone_changed`
  (`data.slot` 1..4), ad / yakınlık (ve velinin yuva değiştirmesi) `student.updated`
  (`["guardians"]`). Dizin `guardians[]` öğesi `relation` ve yuva 3–4'ü taşır;
  mobil geçit dört yuvayı da `relation` + `primary` ile Akademi'ye iletir — dört
  velinin de uygulama girişi açılır (`appSlots: [1, 2, 3, 4]`; sözleşme
  km-mobile-gateway §1, km-merkezi-senkron §2).

### Sınıf ve gruplar

**Ana sınıf tektir** (`class_name`: kantin kartı, Akademi sınıfı ve müfredat
önerisi ondan); bir öğrencinin **birden çok topluluğa** üyeliği tarihli
**gruplarla**dır (aşağıda). Toplu ana sınıf: `POST /students/class
{students, className}` (`bbd_students.manage`; öğrenci başına
`student.updated` `["className"]`, kantine gidilmez; boş ad temizler). Toplu
grup: mevcut `POST /groups/{id}/members`, `/members/end`, `POST /groups/move`
(çoklu `studentIds`). Seviye / alan / hedef sınav sınıfın tanımındadır
("Sınıflar" sekmesi, aşağıda). Tek öğrencinin kaydı: `GET /students/{id}` (kantine o an
ulaşılamazsa dizinin son görüntüsünden, `canteenUnavailable`).

## Yetenekler

- `bbd_students.list` — ARAYÜZ yeteneği (panelin `capabilities()`); öğrenci
  seçen paneller kullanır.
- `bbd_students.directory` — BACKEND yeteneği (`backend/directory.py`): mobil
  geçit sözleşmesi §1'in öğrenci nesnesi. `list(since)`, `get(id)`,
  `find_by_phone(phone10)`, `card_qr_text(id)`. Telefonlar `5XXXXXXXXX` ya da
  `null`; `accessCode` asla dönmez; kantine ulaşılamazsa `complete: false`.

- `bbd_students.groups` — BACKEND yeteneği (`backend/groups.py`): öğrenci
  grupları ve tarihli üyelik. `list(since_revision)`, `get(id)`,
  `for_student(id)`, `import_named(name, kind=…)` (yalnız zil gruplarının
  taşınması). Her yazmadan sonra `bbd_students.groups_changed` (`{revision}`).

## Öğrenci grupları

Sözleşme: `bbd-akademi/docs/integrations/km-ogrenci-gruplari-ve-zil.md` §2.
**Grubun tek sahibi bu modüldür**; BBD Akademi (mobil geçit `GET /groups`) ve
zil sistemi (elle çağrı) buradan okur. Grup = ad + öğrenci seçimi; tür
`group` (toplu ders) ya da `individual` (özel ders, aynı gün tek öğrenci);
isteğe bağlı anons adı (`callName`).

- Üyelik **tarihlidir**: başlangıç ve son gün Europe/Istanbul yerel günüdür,
  ikisi de dahildir. Aynı öğrencinin aynı grupta kesişen iki üyeliği olmaz.
- Ekleme varsayılanı bugünden başlar; "Çıkar" seçilen günü son gün yapar;
  "Taşı" eski grupta son günü bir önceki güne, yenide başlangıcı seçilen güne
  yazar (birlikte doğrulanır, birlikte yazılır).
- Bugünden önceki bir güne dokunan değişiklik **gerekçe** ister; her yazma
  denetim izine (`mod_bbd_students_group_log`) düşer.
- **Silme yoktur, arşiv vardır.** Arşivde açık üyelikler arşiv gününde kapanır,
  başlamamış üyelikler kaldırılır; arşivden geri almak serbesttir.
- Her yazma kalıcı **grup revizyonunu** +1 artırır (dizinin süreç içi
  `revision`'ından ayrıdır; senkron imlecidir).
- Ekran: BBD Akademi → Öğrenciler → "Gruplar" sekmesi (liste, bugünkü üye sayısı, tür ve arşiv süzgeci,
  grup ayrıntısı, çoklu öğrenci seçici, çıkar/taşı, üyelik geçmişi ve
  düzeltme, "Sınıftan grup oluştur") ve öğrenci kartında "Gruplar" bölümü.
  Yazma `bbd_students.groups`, okuma `bbd_students.view` ister.

### Dönem geçişi (yıl sonu sihirbazı — iş 37 / İ4, göç 013)

Ekran: Öğrenciler → Sınıflar → **"Dönem geçişi"** (derin bağlantı `view: 'rollover'`).
Kaynak dönemin canlı sınıflarının her birine işlem seçilir: **Üst seviyeye taşı**
(yeni dönemde ad önerisiyle 7-A → 8-A, seviye +1; öğrenciler geçiş gününden üye),
**Aynen kopyala**, **Mezun et** (öğrenciler "mezun" gerekçesiyle pasifleşir; istisna
işaretlenebilir), **Yalnız arşivle**, **Dokunma**. Varsayılan: seviyeli sınıf → taşı,
8./12./mezun → mezun et, seviyesiz ve özel ders → arşivle. Yeni ad yeni dönemde zaten
açılmış sınıfa denk gelirse öğrenciler ona eklenir; başka canlı sınıfla çakışma sorundur.

- **Önizleme** yazmaz: yeni/arşivlenecek sınıflar, taşınan öğrenciler, mezunlar,
  seviyesi değişenler (sınıf şablonundan; seviyesiz sınıftakilerde kendi seviyesi +1 —
  yalnız kaynak dönemin sınıflarındaki öğrenciler), sorunlar ve engel sebebi.
- **Uygula** PIN ister; sınıf tarafı tek işlem/tek revizyon. Eski üyelikler
  `min(bugün, geçiş günü − 1)` günü kapanır. Geçiş günü bugünden 14 gün geri – 60 gün
  ileri ve hedef dönemin 1 Eylül'ünden en çok 92 gün önce olabilir (ekimde yanlışlıkla yıl
  atlanmasın); önizleme her zaman serbest. Mezun pasifleştiren plan `bbd_students.lifecycle`
  da ister.
- **Geri al** (yalnız son geçiş, PIN): açılan sınıflar kalıcı silinir (Akademi'ye mezar taşı),
  eski sınıflar ve üyelikler döner, seviyeler (sonradan elle değişmediyse) geri yazılır,
  geçişin pasifleştirdiği mezunlar etkinleşir.
- Ders programı ve sınıf müfredatı Akademi'dedir: **Akademi → Ders programı → "Dönem geçişi
  kopyası"** son geçişin eşlemesiyle eski sınıfın programını/müfredatını yeni sınıfa aktarır.
- Uçlar: `GET /groups/rollover` (view), `POST /groups/rollover/preview`, `POST /groups/rollover`
  (PIN), `GET /groups/rollover/{id}`, `POST /groups/rollover/{id}/undo` (PIN) — yazma
  `bbd_students.groups`. Kural: `backend/academic_year.py` → `YearRollover`.

## Seviye / alan / hedef sınav — SINIF taşır (göç 008, 0.5.5, 13.10.2026)

Kullanıcı kararı: "Sınıf ve seviye farkı garip, teke indirelim." Seviye
(5–12 / `graduate`), alan (`track` SAY/EA/SOZ/DIL; 10. sınıf ve üstü / mezun)
ve hedef sınav (`examTarget` LGS/YKS) **ana sınıfın tanımındadır**
(`mod_bbd_students_class`); öğrenci yalnız sınıfa atanır, değerleri sınıftan
türer (`backend/curriculum.py`). Akademi müfredatı (hangi programlar) bu
değerlerden manifest kuralıyla kendiliğinden bağlanır.

- **Geçerli değer**: sınıfa seviye girilmişse sınıfınki; öğrenci kartında
  "Bu öğrenci için farklı alan / hedef sınav" istisnası (`level_override`,
  varsayılan kapalı, rozetle görünür) açıksa öğrencinin kendi alan / hedef
  sınavı (seviye her zaman sınıftan). Sınıfa seviye girilmemişse öğrencinin
  eski alanları (geriye uyum). Öğrenciye seviye yazılmaz (400).
- **Uçlar**: `GET /classes` (tanımlı ∪ kullanılan sınıflar; seviyesiz sınıf
  `defined: false` + addan öneri — "12-SAY" → 12 · SAY · YKS; öneri yazılmaz),
  `PUT /classes/{ad}` ve `DELETE /classes/{ad}` (`bbd_students.manage`).
  Sınıftaki her öğrencinin geçerli değeri değişirse öğrenci başına
  `student.updated` → Akademi müfredatı 1–3 sn'de yeniden çözer.
- **Dizin / geçit**: öğrenci nesnesi türetilmiş `gradeLevel/track/examTarget`
  ve `classLevel` (sınıf tanımı ya da `null` = seviye girilmemiş) taşır
  (km-mobile-gateway "Ekler"). Etüt / çalışma grupları seviye taşımaz.

### Kurulum rehberi (kurum yöneticisi, bir kez)

1. **Akademi → Öğrenciler → Sınıflar**: her sınıfa seviye seçin
   (ör. "11-A → 11. sınıf · SAY · YKS", "Mezun → Mezun · EA · YKS",
   "8-B → 8. sınıf · LGS"). "Seviye seçin" rozetli satırlarda addan öneri
   hazır gelir; doğruysa yalnız **Seviyeyi kaydet**. Yeni sınıf: alttaki
   "Yeni sınıf tanımla".
2. **Öğrencileri sınıflara alın**: Öğrenciler listesinde işaretle → "Ana
   sınıf" → sınıf adı → Uygula (aynı adı yazmak yeterli; var olan ad korunur);
   çok öğrencide **Toplu işlemler** → ortak seçicide süz / "Görünenleri seç" →
   "Sınıfa ata".
3. İstisna gereken öğrenci (ör. 11'de YKS yerine yalnız okul): öğrenci kartı →
   "Bu öğrenci için farklı alan / hedef sınav".
4. Kontrol: Akademi'de ödev / plan verirken program seçicisi sınıfın
   seviyesini gösterir ("Hedefin seviyesi: 11. sınıf · SAY · YKS") ve uygun
   programı kendiliğinden seçer (toplu ödevde "Kime?" seçildikten sonra
   "Ne?"ye dönüp ödevi düzenleyince); "sınıfına seviye girilmemiş" uyarısı 1.
   adımda eksik kalan sınıfı söyler.

## Veli kantin kontrolleri

Öğrenci detayında: borç limiti, günlük limit, kurum engeli, veli engeli
(personel yalnız kaldırır), yasaklı ürünler, bugün harcanan, son değişiklik.
Kantinde durur ve satış anında kantin uygular
(`GET/PATCH /api/bbd_students/students/{id}/canteen…`, izin
`bbd_students.canteen_controls`).

Kantin tahsilatı ve ödeme talebi kartı Ödeme Talebi modülünden gelir
(`bbd_payment_request.student_card` arayüz yeteneği); akış orada kalır.

## Merkezi senkron ve yaşam döngüsü (KM tek merkez)

Sözleşme: bbd-akademi `docs/integrations/km-merkezi-senkron.md`; çekirdek
desteği ADR 0029 (modül işlemi + `events.outbox`).

- **Önce KM, sonra kantin.** Her öğrenci yazması tek işlemde profil +
  `revision` (küresel sayaç) + `canteen_sync = pending` + bekleyen kantin
  gövdesi + outbox olayı yazar, sonra kantine gider. Kantin reddederse (4xx)
  telafi edilir (yeni kayıt silinir / eski değerler geri yazılır, köken
  `system`); ulaşılamazsa kayıt `pending` kalır ve uzlaştırma görevi (1 dk,
  kantinden olay gelince hemen) yineler. Listede "Kantine iletilemedi"
  rozeti; 24 saati geçen kırmızı. `GET /api/bbd_students/sync` özeti verir.
- **Olaylar** (`events.outbox`, kişisel veri yok): `student.created|updated`
  (değişen mantıksal alanlar), `guardian.linked|unlinked|phone_changed`
  (`data.slot`), `canteen.controls_changed`, `student.deactivated|reactivated|
  deleted`, `tablet.policy_changed` (pasifleştirmede), `group.changed`.
- **Yaşam döngüsü:** Pasifleştir (gerekçe ayrıldı/mezun/geçici durdurma/diğer,
  bugün ya da ileri tarih — o gün 00:00'da uygulanır; toplu seçim),
  Yeniden etkinleştir (grup üyelikleri kendiliğinden AÇILMAZ), Kalıcı sil
  (yalnız pasif, ad yazılarak, bakiye tam 0, açık ödeme talebi yok, Akademi
  kurum cari borcu yok — `akademi.student_finance` yoksa silme kapalı).
  Uçlar: `POST /students/{id}/deactivate`, `POST /students/deactivate`
  (toplu), `POST /students/{id}/reactivate`, `DELETE /students/{id}`,
  `GET /students/{id}/status-history`. İzinler `bbd_students.lifecycle`,
  `bbd_students.delete` (yalnız admin).
- **Kalıcı silme ekranları (0.5.4, kullanıcı isteği):** öğrenci kartının EN
  ALTINDA ayrı "Tehlikeli işlemler" — önerilen yol "Pasifleştir" yanında;
  "Kalıcı olarak sil…" önce ön kontrol yapar (`GET /students/{id}/delete-check`,
  yazma yok): engellerin hepsi tutarıyla ve açık Türkçe ("Kantin bakiyesi
  42,50 ₺ borç — önce tahsil edin", "Açık ödeme talebi var (PR-…)", "Kurum cari
  borcu var (1.200,00 ₺)", "Öğrenci etkin: önce pasifleştirin", Akademi'ye /
  kantine ulaşılamıyor) + ilgili bölüme / Kurum carisine götüren düğme. Kurum
  carisindeki ALACAK engellemez (kullanıcı kararı; onay penceresinde not).
  Engel yoksa onay penceresi neyin silineceğini yazar (KM kaydı; kantinde
  anonimleşme; Akademi'de KVKK silme / anonimleştirme, uygulama hesabı kapanır;
  geri alınamaz), adı aynen yazdırır ve **PIN** ister; Enter yalnız alanlarda
  onaylar, Esc vazgeçer. Listede çoklu seçim → "Kalıcı olarak sil" (en çok 50,
  `POST /students/delete`, seçilen sayı + PIN): her öğrenci ayrı denetlenir,
  sonuç penceresi öğrenci başına "silindi" ya da engelin nedenini gösterir;
  her silinen öğrenci ayrı `student.deleted`; kantin çağrıları arka planda
  sıralı. İki silme ucu da PIN teyidi ister (`confirm_pin`; yanlış PIN denetim
  izine düşer; PIN göndermeyen 0.5.3 kabuğu `422 pin_required` alır, silme
  olmaz). Durum geçmişine "Kalıcı silindi" + yapan yazılır.
- **Toplu işlemler seçicisi (0.5.5, kullanıcı: "toplu işlemlerde öğrenci
  seçilemiyor, UI görünmüyor").** Sekme kitin ortak seçicisini (`ui-kit/picker.js`)
  kullanır — BBD Akademi'nin bütün "Kime?" seçicileriyle AYNI bileşen: satırda onay
  kutusu, arama (Türkçe katlamalı), sınıf süzgeci, "Görünenleri seç", "Yalnız
  seçilenler", seçili sayısı (ekran okuyucuya duyurulur), "Daha fazla göster",
  klavye (aramada ↓, satırlarda ↑/↓/Home/End, Boşluk), ilk okumada "Yükleniyor…",
  kantine ulaşılamazsa sebep + "Tekrar dene". Akademi içinde (`.st-embedded`)
  seçici görüntü alanı yüksekliğindedir, liste kendi içinde kayar ve sağdaki
  işlemler kaydırılırken yapışık kalır; 1000 px altında tek sütun. Grup üyeliği
  Türkçe harfli kantin kimliklerini (ör. "…yazılım") kabul eder — eski ASCII
  kuralı toplu "Gruba ekle"nin tamamını 400 ile düşürüyordu.
- **Dizin** öğe başına `status`, `statusReason`, `statusSince`, `revision`,
  `canteenSync` taşır; kantine henüz yazılamamış kayıt `canteen: null` ile
  döner; pasif görünür, silinen `since` yanıtında `removed`'da (90 gün).
  `get_many(ids)`, `balance=False`.
- **Kasadan eklenen öğrenci** (`bbd_students.sync`, kantin olay alıcısı
  çağırır): taslak profil + "Kasadan eklendi — bilgileri tamamla" rozeti.
- **Canlı ekran:** liste, öğrenci kartı ve kantin kontrolleri
  `GET /api/events/wait` uzun yoklamasıyla kendini yeniler.
