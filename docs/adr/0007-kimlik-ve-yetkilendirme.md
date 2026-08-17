# 0007 — Kimlik ve yetkilendirme: PIN ile giriş, izin tabanlı çok rollü model

**Durum:** Superseded by [0016](0016-giris-sifre-ile.md) · 2026-08-12

> Yerine geçen ADR **yalnız kimlik doğrulama bölümünü** değiştirir: giriş 6 haneli
> PIN yerine kişiye özel şifre iledir. Kullanıcı adsızlık, çok rollü izin modeli,
> kapsam mekanizması ve `has_permission` kuralı (K10) aynen sürer ve 0016'da
> yeniden onaylanır.

## Bağlam
Uygulamayı kurum içinden sınırlı sayıda kişi kullanacak. Kullanıcıları yönetici
elle ekleyecek (ad, soyad, telefon vb.); kendi kayıt olma yok. Girişin hızlı
olması isteniyor: kullanıcı adı girilmeyecek, kişiye özel şifre/PIN hem girişi
hem kimliği belirleyecek. Yetkiye göre açılabilen ekranlar değişecek.

Dört rol var: Admin, BLD Personeli, BBD Personeli, Kurum Personeli. Bir kişi
birden fazla rol taşıyabilmeli (ör. hem BLD hem BBD personeli).

## Karar

### 1. Kimlik doğrulama — PIN
Kullanıcı adı alanı yoktur. Girişte yalnızca PIN girilir; PIN aynı zamanda
kimliği belirler.

- PIN **benzersizdir** — çakışma kabul edilmez, aynı PIN ikinci kullanıcıya
  atanamaz.
- PIN en az **6 hane**dir, Argon2id ile hash'lenerek saklanır. Düz metin PIN
  hiçbir yerde (log, denetim izi, hata mesajı) görünmez.
- Kullanıcıyı PIN'den bulmak için tüm kayıtlar taranamaz; kayıtta arama için
  ayrıca sabit bir arama anahtarı (peppered lookup hash) tutulur.
- Yönetici PIN atar ve sıfırlar. Kullanıcı kendi PIN'ini değiştirebilir.

### 2. Kimlik doğrulama — kaba kuvvete karşı
Kullanıcı adı olmadığı için saldırgan belirli bir kişiyi değil **herhangi bir
kişiyi** hedefler; başarı olasılığı kullanıcı sayısıyla doğru orantılı artar.
Bu yüzden aşağıdakiler tasarımın zorunlu parçasıdır:

- Cihaz/oturum başına deneme sınırı ve artan gecikme.
- Ardışık başarısız denemeden sonra giriş ekranı kilitlenir; kilit yalnızca
  yönetici tarafından veya süre dolduğunda açılır.
- Her başarısız deneme denetim izine yazılır.
- Yıkıcı işlemler (kullanıcı silme, rol değiştirme, veritabanı geri yükleme)
  oturum açık olsa bile **PIN teyidi** ister.

### 3. Yetkilendirme — izin tabanlı
Rol, izin kümesidir. Kodda "eğer rol Admin ise" biçiminde dal **yazılmaz**;
her yerde izin sorulur.

- Dört rol ön tanımlı gelir; yeni rol tanımlanabilir, izinleri düzenlenebilir.
- **Bir kullanıcıya birden fazla rol atanabilir.** Etkin izin kümesi,
  rollerin **birleşimidir** (union). Kullanıcı bazında tekil izin verme/alma
  şimdilik yoktur — gerekirse yeni rol tanımlanır.
- İzinler **kapsamlıdır** (scope): `database.query:bld` gibi. Kapsam değerleri
  `bbd`, `bld`, `org` ve tümünü kapsayan `*`. BLD ile BBD ayrımı bu mekanizmayla
  kurulur; ayrı kod yolu gerekmez.
- Modüller kendi izinlerini `module.yaml` içinde ilan eder. Çekirdek izinleri
  **uygular**, tanımlamaz (K6 korunur: yeni modül çekirdeğe dokunmadan
  yetkilenir).

### 4. Ekran görünürlüğü
Bir ekranın açılabilirliği tek bir kaynaktan gelir: `module.yaml` içindeki
`ui.nav.requires` izin listesi. Kullanıcıda izin yoksa menü öğesi **görünmez**
ve doğrudan yönlendirmede backend de reddeder.

**Kural K9 — Çift kapı.** Arayüzde gizlemek yetkilendirme değildir. Her
korunan işlem sunucu tarafında da izin denetimine tabidir; arayüz gizleme
yalnızca kullanılabilirlik içindir.

## Gerekçe
- İzin tabanlı model olmadan "yetkiye göre ekran değişsin" isteği, çekirdeğe
  rol adı gömmeyi gerektirirdi; bu K1'i ihlal ederdi.
- Kapsam (`:bld`, `:bbd`) sayesinde BLD ve BBD personeli **aynı izin
  tanımlarını** farklı kapsamlarla kullanır. İki ayrı izin ağacı bakımı ortadan
  kalkar ve üçüncü bir kurum eklendiğinde yalnızca kapsam eklenir.
- Çok rollülük union ile çözülür: hem BLD hem BBD rolü taşıyan kişi iki kapsamı
  da alır, ek kural gerekmez.

## Sonuçlar
- PIN'siz/kullanıcı adsız model, parola tabanlı bir modelden zayıftır. Yukarıdaki
  sınırlama ve kilitleme önlemleri isteğe bağlı değil, sözleşmenin parçasıdır.
- Kullanıcı sayısı büyürse PIN uzunluğu artırılmalı; 6 hane ~50 kullanıcıya
  kadar makul kabul edilmiştir.
- Kullanıcı bazında tekil izin istisnası bilinçli olarak dışarıda bırakıldı;
  ihtiyaç doğarsa yeni ADR ile eklenir.
- Kullanıcı kaydı, iç rehberi besleyecek alanları (telefon, dahili, birim,
  unvan) baştan taşır; rehber modülü geldiğinde veri modeli değişmez.
