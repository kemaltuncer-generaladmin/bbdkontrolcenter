# Öğrenci Yönetimi

Öğrenci kayıtları, veli iletişimi, kart ve bakiye durumu. Kantin otoritedir:
ad, veli 1 telefonu, bakiye ve kantin kontrolleri kantinde durur; kantinde
karşılığı olmayan alanlar (ad/soyad ayrımı, sınıf, okul no, öğrenci telefonu,
ikinci veli, not, seviye/alan/hedef sınav) modülün kendi tablosundadır.

Grup: **BBD** · İzinler: `bbd_students.view`, `bbd_students.manage`,
`bbd_students.qr`, `bbd_students.access_code`, `bbd_students.canteen_controls`

## Yetenekler

- `bbd_students.list` — ARAYÜZ yeteneği (panelin `capabilities()`); öğrenci
  seçen paneller kullanır.
- `bbd_students.directory` — BACKEND yeteneği (`backend/directory.py`): mobil
  geçit sözleşmesi §1'in öğrenci nesnesi. `list(since)`, `get(id)`,
  `find_by_phone(phone10)`, `card_qr_text(id)`. Telefonlar `5XXXXXXXXX` ya da
  `null`; `accessCode` asla dönmez; kantine ulaşılamazsa `complete: false`.

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
