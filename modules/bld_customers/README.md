# Müşteriler (`bld_customers`)

BLD müşteri kayıtlarının Kontrol Merkezi'ndeki yönetim ekranı: arama, müşteri
kartı, adres defteri, sipariş geçmişi, abonelikler, SMS gönderim kaydı,
iletişim ve kurum bilgilerinin düzenlenmesi, hesabın kapatılması/açılması.

Grup: **BLD** · İzinler: `bld_customers.view`, `bld_customers.manage`,
`bld_customers.disable` · Geçit: `bld.api` (K4)

Sözleşme: [`BLD/docs/control/customers.md`](../../../BLD/docs/control/customers.md)
+ [`00-genel.md`](../../../BLD/docs/control/00-genel.md) §9 ·
Geçit metotları: [`modules/bld_api/README.md`](../bld_api/README.md) §7

---

## Bu ekran ötekilerden neden ayrı

**Sistemdeki en geniş kişisel veri yüzeyi burası.** Ad, telefon, e-posta,
kurum bilgisi, adres defteri, sipariş geçmişi, abonelik ve SMS kaydı tek
ekranda birleşiyor. Sözleşme bu yüzden tek bir istisna tanımlıyor ve modül onu
uyguluyor: **`control/customers/*` altında OKUMALAR DA denetim izine düşer.**

Bunun beş somut sonucu var:

| Karar | Neden |
|---|---|
| **Yoklama yok.** Panelde `pollLoop`/`setInterval` HİÇ GEÇMEZ. | 15 saniyede bir yoklayan bir ekran, izi günde binlerce anlamsız satırla doldurup içindeki gerçek erişimi görünmez kılardı. Tazeleme yalnız yöneticinin bastığı düğmeyle. |
| **Açılışta sayaç yok.** `GET /overview` BLD'ye HİÇ GİTMEZ. | Açılışta dört sayaç çekmek, kimsenin sormadığı bir soru için deftere dört satır yazmak olurdu. Sayılar aramanın `meta.total` alanından gelir. |
| **Çekmece sekmeleri tembel.** Her sekme ilk açıldığında yüklenir. | Beş sekmeyi birden çekmek, yöneticinin bakmadığı dört ekran için dört denetim satırı yazmak olurdu. Adres defteri bu yüzden "Bilgiler"e gömülmedi. |
| **Yazma sonrası liste yeniden okunmaz**, yerinde güncellenir. | Yazılan değerler zaten elimizde; yeni bir arama isteği ikinci bir denetim satırı yazardı. |
| **Aktör oturumdan gelir**, gövdeden/sorgudan değil. | Sözleşme `actor`ı sorgu dizesinde taşıyor ama o sınır BLD ile KM arasında. İstemcinin aktör adını yazabilmesi, silinmeyen bir deftere istediği adı yazabilmek olurdu. |

**Kullanıcı bunu bilir.** Ekranın tepesinde kalıcı bir uyarı kutusu var ve
metni sunucudan gelir (`overview.kvkk_notice`) — aynı cümlenin iki kopyası
zamanla ayrışır ve biri güncellenmez.

---

## Yapmadıkları

- **Maskelemez.** Liste ve kart telefonu, e-postayı olduğu gibi gösterir.
  Sözleşme bunu açıkça reddediyor: yönetici müşteriyi telefonundan tanır ve
  maskeli bir listede doğru kaydı seçemez, hepsini tek tek açmak zorunda kalır
  — yani her arama için bir düzine denetim satırı doğar. **Maske yalnız
  denetim izindedir** (`532****567`), ekranda değil.
- **Parola göstermez, yazmaz, sıfırlamaz.** Hiçbir uçta geçmiyor.
- **E-posta yazmaz.** Giriş kimliğidir; değiştirmek hesabı devretmektir.
- **Hesap türü yazmaz.** Kurumsal sipariş kapısı kaldırıldı; alan artık yalnız
  geçmiş kayıtların etiketi.
- **Silmez.** Silme ucu yok ve olmayacak; `DELETE` fiili router'da hiç geçmez.
  Hesap kapatılır, kayıt durur.
- **Adres yazmaz, harita çizmez.** Adres siparişe kopyalanıyor, bağlanmıyor —
  defteri buradan düzeltmek geçmiş siparişlerin adresini değiştirmez. Harita
  da yok: dış bir servise istek atmak müşterinin adresini üçüncü bir tarafa
  göndermek olurdu.
- **Sipariş ve abonelik değiştirmez.** Revizyon `bld_orders`ın, abonelik
  `bld_subscriptions`ın işi; kısayol da konmadı (bir iş eylemi tek ekranda
  durur).

---

## Uçlar

| Metot | Yol | İzin |
|---|---|---|
| GET | `/overview` | `view` |
| GET | `/customers` | `view` |
| GET | `/customers/{id}` | `view` |
| GET | `/customers/{id}/orders` | `view` |
| GET | `/customers/{id}/subscriptions` | `view` |
| GET | `/customers/{id}/addresses` | `view` |
| GET | `/customers/{id}/sms` | `view` |
| PATCH | `/customers/{id}` | `manage` |
| POST | `/customers/{id}/disable` | **`disable`** |
| POST | `/customers/{id}/enable` | `manage` **veya** `disable` |
| GET | `/access-log` | `view` |
| GET | `/audit` | `view` |
| GET · PUT | `/prefs` | `view` |

**Üçüncü izin neden var:** kapalı bir hesap giriş yapamaz ve sipariş veremez;
sonucu ilk fark eden çoğu zaman müşterinin kendisi olur ve o an satış
kaybedilmiştir. `manage` bir telefon düzeltmesi için günlük bir yetkidir,
hesabı kapatmak bir karardır. **Hesabı açmak aynı anahtarı istemez**: kapatmak
yıkıcı, açmak onarıcıdır — açmayı da üçüncü anahtara bağlamak, yanlışlıkla
kapatılmış bir hesabı düzeltebilecek kişi sayısını azaltırdı.

Yıkıcı işlem **PIN değil gerekçe** ister (ADR 0012): ayrı izin anahtarı +
gerekçe (≥10 karakter, backend'de de doğrulanır) + çift denetim satırı.

---

## Yerel tablolar

Uzak BLD verisi **asla** yerele kopyalanmaz. Üç tablo var ve üçünde de müşteri
satırı bulunmaz:

| Tablo | İçerik |
|---|---|
| `mod_bld_customers_access` | **KVKK erişim izi** — her okuma bir satır. Aktör, kapsam, müşteri kimliği ve **yalnız süzgeçler**; dönen kayıtlar asla yazılmaz. |
| `mod_bld_customers_audit` | Yazma denemeleri (`denendi → ok/dry_run/hata`). Telefonlar **maskeli**. |
| `mod_bld_customers_prefs` | Ekran tercihi. BLD'yi etkilemez. |

Sunucuda da bir defter var (`customer.read`) ve ikisi **aynı soruya cevap
vermez**: sunucununki yalnız kendisine ULAŞAN okumayı bilir, yereldeki
DENENEN okumayı da bilir. Ağ koparsa, imza reddedilirse ya da geçit patlarsa
"kim kimin kaydını açmak istedi" sorusunun cevabı yalnız yerelde kalır.

Erişim izi yazılamazsa **okuma durmaz** (K7) ama olay `warning` değil
**`error`** seviyesinde günlüğe düşer: bu bir gözetim boşluğudur.

---

## Bilinen ayrım — SMS sekmesi

SMS gönderim kaydının ucu `control/sms/log` altındadır, `control/customers/*`
altında değil. Bunun iki sonucu var:

1. **Sunucu bu okuma için `customer.read` satırı yazmaz.** Yerel iz tek
   kayıttır ve `action` alanı bu yüzden `sms.read`'tir — `customer.read`
   deseydik, iki defteri karşılaştıran biri sunucuda karşılığı olmayan
   satırları "sunucu kayıp vermiş" diye okurdu.
2. Uç `actor` istemez; modül yine de aktör kapısından geçirir, çünkü aktörsüz
   yazılan bir yerel iz kimseyi işaret etmez.

---

## Doğrulama

```bash
cd "Kontrol Merkezi"
.venv/bin/python -m pytest modules/bld_customers
.venv/bin/ruff check .
node --check modules/bld_customers/ui/panel/index.js
```

Testler ağa çıkmaz. `tests/bld_customers_fakes.py` içindeki gövdeler
sözleşmedeki örneklerden **kısaltılmadan** kopyalanmıştır; modülün kendi
uydurduğu bir gövdeye karşı geçen test hiçbir şey kanıtlamaz.
