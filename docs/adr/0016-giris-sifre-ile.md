# 0016 — Giriş: kullanıcı adsız, kişiye özel şifre

**Durum:** Reddedildi · 2026-08-17
**Yerini aldığı sanılan:** [0007](0007-kimlik-ve-yetkilendirme.md) —
**süperseleme geri alındı.** 0007 yürürlüktedir; giriş 6 haneli PIN'dir.

## Neden reddedildi

Karar sahibi mevcut giriş ekranını korumayı seçti: tuş takımı ve 6 haneli PIN
kalıyor, şifre alanı gelmiyor. Aşağıdaki gerekçe (uzayın küçüklüğü, kadronun
merkezîleşmesi) yanlışlanmadı — **ertelendi**: 6 hane bugün yalnız YEREL
doğrulamada duruyor ve o dosyayı okuyan zaten her şeyi okumuştur. Sır uzunluğu,
merkezî kadro servisi (ADR 0021) gerçekten geldiğinde yeni bir ADR ile yeniden
ele alınacaktır; o zamana kadar sır makineden çıkmıyor.

**Kod tarafında geri ALINMAYAN üç şey var** — göç çoktan koşmuştu ve adları geri
çevirmek ikinci bir göç, yani veri riski demekti:

| Kalan | Nerede | Anlamı bugün |
|---|---|---|
| `password_hash`, `secret_lookup`, `password_set_at` sütunları | `users` tablosu | PIN'in Argon2id özeti ve arama hash'i |
| `users.set_password` izin anahtarı | roller | PIN atama/sıfırlama yetkisi |
| `POST /api/auth/set-password`, gövdedeki `password` alanı | HTTP sözleşmesi | PIN kurma |

Kural her yerde PIN'dir (`Identity.validate_pin`); yalnız ADLAR şifre der.

**`mustSetPassword` akışı ise KALDIRILDI.** Geri alma sırasında kodda bırakılan
"şifre belirlemeye zorlama" akışı 17.08.2026'da gerçek kurulumu kilitledi —
kullanıcı orijinal PIN'iyle girdi, akış yerine yeni bir sır yazdı, `secret_lookup`
değişti ve orijinal PIN o günden sonra reddedildi; kalıntı bu yüzden tümüyle
kaldırıldı (giriş tek sırra bakar, geride kalan satırları
`0006_backfill_secret_lookup` göçü onarır).

Aşağıdaki metin kararın kendisidir ve tarihsel kayıt olarak olduğu gibi
bırakılmıştır; **uygulanmamıştır.**

## Bağlam

ADR 0007 **tek makine** varsayımıyla yazıldı: giriş 6 haneli PIN'dir, PIN hem
kimliği hem girişi belirler, kullanıcı adı yoktur.

O varsayım kalkıyor. Uygulama Windows ve macOS kurulumlarına açılıyor
(ADR 0014) ve kadro merkezî bir servisten senkronlanacak (ADR 0021). İki şey
birden değişiyor:

- **Sır artık makineden çıkıyor.** Bugün doğrulama yerel SQLite'ta bitiyor;
  yarın kadro ağ üzerinden dağıtılacak.
- **Uzay çok küçük.** 6 hane = 10⁶. `pin_lookup` sabit anahtarlı bir HMAC'tir
  ve **hızlıdır** — Argon2 yalnız `pin_hash`'i korur. Yerel bir dosyada bu
  kabul edilebilirdi (o dosyayı okuyan zaten her şeyi okumuştur); ağa açık bir
  veritabanında kabul edilemez.

Kullanıcı yönetimi ekranının alanları da bu yönde belirlendi: ad, soyad,
telefon, **şifre**.

## Karar

### 1. Kullanıcı adı yoktur — bu korunur

Giriş ekranında tek alan vardır: **şifre.** Şifre hem kimliği hem girişi
belirler; kişi kendi şifresini yazar ve sistem kim olduğunu ondan bilir.

ADR 0007'nin bu tercihi isabetliydi ve değişmiyor. Değişen yalnız sırrın
biçimi: rakam dizisi yerine şifre.

### 2. Şifre benzersizdir

İki kullanıcı aynı şifreyi taşıyamaz; çakışma denemesi hata verir. Bu, kullanıcı
adsız girişin zorunlu sonucudur — sır kimliği belirliyorsa iki kişiye ait
olamaz.

Çakışma yöneticiye **kimin şifresiyle çakıştığını söylemez**; yalnız "bu şifre
kullanılamaz, başka bir tane seçin" der. Aksi hâli, bir yöneticinin deneme
yoluyla başkasının şifresini öğrenmesine kapı açardı.

### 3. Kurallar

| Kural | Değer |
|---|---|
| En az uzunluk | 10 karakter |
| Karmaşıklık dayatması | **yok** — büyük harf/rakam/simge zorunluluğu yazılmaz |
| Yaygın şifre listesi | reddedilir (`BASIT_PINLER`'in yerini alır) |
| Ad/soyad/telefon içeren şifre | reddedilir |
| Hash | Argon2id (değişmedi) |
| Arama hash'i | `secret_lookup` — sabit anahtarlı HMAC, pepper kasada (`pin_lookup`'ın yerini alır, mekanizma aynı) |

Karmaşıklık dayatması bilerek yoktur: kullanıcıyı `Ay!2026x` gibi kısa ve
tahmin edilebilir şifrelere iter. Uzunluk tek başına daha iyi korur.

### 4. Deneme sınırı ve zamanlama korunur

Kilitlenme, `failed_attempts`, `locked_until` ve **kullanıcı bulunamasa bile
sabit süre harcanması** aynen sürer. Şifre uzayı büyüdü diye bu korumalar
gevşetilmez; birbirlerinin yerine geçmezler.

### 5. Yıkıcı işlem teyidi şifre ister

`require_pin_for_destructive` → `require_password_for_destructive`. Yıkıcı işlem
izin yeterli olsa bile ikinci kez sır ister; değişen yalnız sorulan şeyin adı.

ADR 0012'nin mağaza tarafındaki kararı (PIN değil, gerekçeli onay + kuru prova)
etkilenmez.

### 6. Telefon: giriş anahtarı değil, kurtarma ve bildirim kanalı

`phone_mobile` alanı veri modelinde zaten var. Giriş için kullanılmaz —
kullanıcı adsızlık korunuyor. İki işi vardır:

- **Şifre sıfırlama** — yönetici sıfırlar; tek kullanımlık kod SMS ile gider
  (`notify` yeteneği zaten çalışıyor).
- Kadro bildirimi.

İkinci etken doğrulama bu ADR'de **zorunlu kılınmaz**; altyapısı (telefon +
çalışan SMS) hazır olduğu için ayrı bir kararla açılabilir.

### 7. Geçiş: hiçbir kayıt kaybolmaz

- `users` tablosuna `password_hash` ve `secret_lookup` **eklenir**; `pin_hash`
  ve `pin_lookup` düşürülmez, kullanılmaz hâle gelir.
- Mevcut kullanıcı ilk açılışta **şifre belirlemeye zorlanır**; eski PIN'i
  kabul edilir ama oturum ancak şifre kurulduktan sonra açılır.
- Hiç kimse kilitlenmez, hiçbir satır silinmez.

## Elenen alternatifler

- **PIN'i uzatmak (8–10 hane).** Dokunmatik kasada doğru tercihtir; Kontrol
  Merkezi klavyeli bir masaüstü uygulamasıdır ve rakam kısıtı burada yalnız
  uzayı daraltır.
- **Telefon + şifre.** Teknik olarak sağlam ve kullanıcı numarasını hatırlar.
  Elendi: kullanıcı adsızlık ADR 0007'nin bilinçli ve iyi çalışan bir
  tercihiydi; iki alanlı giriş onu gereksizce bozardı.
- **E-posta + şifre.** `email` alanı isteğe bağlı ve birçok kullanıcıda boş.

## Sonuçlar

- **Kabuğun PIN tuş takımı kaldırılır** (`apps/desktop/shell/app.js`); yerine
  tek şifre alanı gelir. Hane noktaları, `PIN_MIN`/`PIN_MAX` ve rakam
  yakalayan tuş dinleyicileri gider.
- `docs/identity-model.md`, `docs/permissions.md` (Giriş satırı,
  `users.set_pin` izni) ve `CLAUDE.md`'nin kimlik bölümü bu ADR'ye göre
  güncellenir.
- `users.set_pin` izni `users.set_password` olur. İzin **anahtarı** değiştiği
  için rol atamaları göç ister; göç eklenmeden izin adı değiştirilmez.
- Şifre uzayının büyümesi, kadronun merkezî servise taşınmasını (ADR 0021)
  savunulabilir kılan asıl teknik gerekçedir. İki karar birlikte ele alınır.
