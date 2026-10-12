# Öğrenci Yönetimi

Öğrenci kayıtları, veli iletişimi, kart ve bakiye durumu. Kantin otoritedir:
ad, veli 1 telefonu, bakiye ve kantin kontrolleri kantinde durur; kantinde
karşılığı olmayan alanlar (ad/soyad ayrımı, sınıf, okul no, öğrenci telefonu,
ikinci veli, not, seviye/alan/hedef sınav) modülün kendi tablosundadır.

Grup: **BBD** · İzinler: `bbd_students.view`, `bbd_students.manage`,
`bbd_students.qr`, `bbd_students.access_code`, `bbd_students.canteen_controls`,
`bbd_students.groups`

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
- Ekran: "Gruplar" sekmesi (liste, bugünkü üye sayısı, tür ve arşiv süzgeci,
  grup ayrıntısı, çoklu öğrenci seçici, çıkar/taşı, üyelik geçmişi ve
  düzeltme, "Sınıftan grup oluştur") ve öğrenci kartında "Gruplar" bölümü.
  Yazma `bbd_students.groups`, okuma `bbd_students.view` ister.

## Seviye / alan / hedef sınav

KM ana kaynaktır; Akademi müfredatı buradan türetir. `gradeLevel` 5–12 ya da
`graduate`, `track` SAY/EA/SOZ/DIL (yalnız 10. sınıf ve üstü / mezun),
`examTarget` LGS/YKS. Kural `backend/curriculum.py`. Sınıf adından ("12-SAY")
yalnız ÖNERİ üretilir; ekranda tek tıkla kabul edilir, kendiliğinden yazılmaz.
Toplu atama: "Toplu işlemler" → "Seviye / alan ata".

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
- **Dizin** öğe başına `status`, `statusReason`, `statusSince`, `revision`,
  `canteenSync` taşır; kantine henüz yazılamamış kayıt `canteen: null` ile
  döner; pasif görünür, silinen `since` yanıtında `removed`'da (90 gün).
  `get_many(ids)`, `balance=False`.
- **Kasadan eklenen öğrenci** (`bbd_students.sync`, kantin olay alıcısı
  çağırır): taslak profil + "Kasadan eklendi — bilgileri tamamla" rozeti.
- **Canlı ekran:** liste, öğrenci kartı ve kantin kontrolleri
  `GET /api/events/wait` uzun yoklamasıyla kendini yeniler.
