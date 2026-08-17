# 0021 — Merkezî kimlik servisi ve cihaz eşlemesi

**Durum:** Kabul edildi · 2026-08-16

## Bağlam

Bugün her şey tek makinededir: `data/kontrol-merkezi.sqlite` (kullanıcılar,
roller, denetim izi) ve `data/secret.key` (13 sırrı çözen anahtar).

Uygulama Windows ve macOS'a açılıyor (ADR 0014). İkinci kurulum yapıldığı anda
bugünkü model çöker:

- Yeni kurulum **kendi boş veritabanını ve kendi yeni anahtarını** üretir.
- Bir makinede oluşturulan kullanıcı diğerinde yoktur; rol değişikliği yayılmaz.
- Kasa boş açılır; 13 sır her makinede elle yeniden girilir (K8 gereği depoya
  konamaz).
- "Kim ne yaptı" sorusunun cevabı kurulum sayısı kadar dosyaya bölünür.

Elde çalışan bir desen var: KDS kasaları sunucudan **tek kullanımlık, süreli
eşleme kodu** ile eşleşiyor, `unpair` token'ı siliyor ve kasa sahada yeniden
kod istiyor. Kontrol Merkezi bugüne dek bu akışın *kod üreten* tarafıydı;
şimdi *kod giren* taraf da olacak.

## Karar

### 1. Ayrı uygulama, ayrı veritabanı

Kimlik servisi **kendi Coolify uygulamasıdır**, kendi veritabanıyla, BLD
sunucusunda barınır. Adresi: `kontrolmerkezi.bbdstore.com.tr` — DNS BLD
sunucusuna yönlendirilir.

**bbdstore'un içine konmaz.** `BBD/ControlApi` paketi teknik olarak uygundu
(kimlik, ability, denetim, gerekçe makinesi kurulu) ama üç gerekçeyle elendi:

| Gerekçe | Ayrıntı |
|---|---|
| BBD/BLD eşitliği | `store_api` ve `bld_api` manifestleri ikisi için de "iş sistemidir, altyapı değil" diyor. Kimlik ikisinin de üstünde. Mağazaya konursa **BLD yönetimi BBD mağazasının sağlığına bağlanır** |
| Dağıtım rejimi | bbdstore'da `main`'e her push canlıya çıkar ve `migrate --force` otomatik koşar; revert kodu döndürür, tabloyu bırakır. Kimlik şeması için tek yönlü kapı |
| Patlama yarıçapı | Mağaza en geniş açık yüzey (Bagisto + eklentiler + ödeme). Kimlik oraya taşınınca mağaza açığı kimlik açığı olur |

`api.bbdstore.com.tr` **yerinde bırakılır** — başka projeler ona bağlıdır,
taşınması bu işin kapsamı dışındadır ve gereksiz risktir.

### 2. Merkez doğruluk kaynağıdır, kurulum çalışma zamanı otoritesidir

Kadro (kullanıcılar, roller, izin atamaları) merkezde tutulur; her kurulum
**yerel önbelleğine** yazar ve girişte oraya bakar.

**Giriş çevrimdışı çalışır.** Bu pazarlık konusu değildir: merkez düştüğünde,
BLD sunucusu bakımdayken ya da ağ koptuğunda hiç kimse Kontrol Merkezi'ne
giremiyor olmak kabul edilemez — hem de uzaktan müdahaleye en çok ihtiyaç
duyulan anda.

Önbellek `revision` ile tazelenir: değişmemişse veri çekilmez.

### 3. Yazma yalnız çevrimiçidir

Kullanıcı ekleme, rol atama ve pasifleştirme **merkeze gider**; kurulum bunları
yerelde yazmaz. Böylece iki kurulumun çevrimdışı yaptığı değişiklikleri
birleştirme sorunu hiç doğmaz (ADR 0020 §4).

Ağ yoksa ekran "bu işlem için bağlantı gerekiyor" der ve **denemez**; yarım
yazılmış bir kadro, hiç yazılmamıştan kötüdür.

### 4. Cihaz eşlemesi

1. Merkezde "Kurulumlar" listesi: makine adı, platform, sürüm, son görülme, durum.
2. Yönetici **tek kullanımlık, süreli** kod üretir.
3. Yeni kurulum ilk açılışta giriş ekranı yerine **eşleme ekranı** gösterir.
4. Kod girilir; kurulum kendi anahtar çiftini üretir, açık anahtarını gönderir,
   karşılığında kurulum token'ı alır.
5. Token iptal edilebilir; iptal edilen kurulum kadro çekemez.

**Eşleme token'ı kullanıcı oturumu değildir.** Token *"bu makine bizim"* der;
kişi yine kendi şifresini girer (ADR 0016). İkisi karışırsa çalınan bir makine
herkesin hesabı olur — KDS'de cihaz token'ının mağaza yönetim belirtecinden
ayrı tutulmasının nedeni de tam olarak buydu.

### 5. Denetim izi merkeze itilir

Kurulumlar denetim kayıtlarını merkeze gönderir. "Kim ne yaptı" tek yerde
toplanır. Gönderim başarısız olursa kayıt **yerelde birikir ve yeniden
denenir**; asla düşürülmez.

### 6. Sırlar bu sürümde merkeze taşınmaz

13 sır (SSH anahtarları, veritabanı parolaları) yerel kasada kalır. Sunucuların
anahtarlarını ağa açık bir servise taşımak, çözdüğünden büyük bir risk açar.

Kasa anahtarını eşleme token'ına bağlamak — token iptal edilince makinedeki
sırların açılamaz hâle gelmesi — çalınan dizüstü riskini gerçekten kapatır ve
**değerli bir fikirdir**, ama çevrimdışı çalışma isteğiyle gerilim içindedir.
Ayrı bir kararla ele alınır; bu ADR'de yapılmaz.

## Elenen alternatifler

- **"Altın kopya": sqlite + `secret.key` dosyalarını elle kopyalamak.** Bugün
  sıfır kodla çalışır. Bedeli: **tüm sunucu parolaları her dizüstünde** durur,
  biri kaybolursa hepsi döndürülür; kullanıcı eklendikçe kopyalama tekrarlanır;
  denetim izleri karışır.
- **bbdstore `BBD/ControlApi` içinde.** Yukarıda, üç gerekçe.
- **Merkezî *doğrulama*** (giriş her seferinde merkeze sorulsun). Servis
  düştüğünde herkes kilitlenir. Merkez kadroya karar verir, girişe değil.

## Sonuçlar

- **Kırmızı çizgi: bu servis ayağa kalkmadan ikinci kurulum yapılmaz.** Sonraya
  bırakılırsa şifreler ve denetim izleri iki makineye bölünür; birleştirmek elle
  iştir.
- Kimlik kodu sıfırdan yazılmaz: `km_core/security/identity.py` ve
  `km_core/store/db.py` aynı şemayı, aynı Argon2id'yi ve aynı izin modelini
  zaten taşıyor; servis onları yeniden kullanır.
- Önbelleğin ne kadar eskiyebileceği bir ayardır ve **sınırsız değildir**:
  pasifleştirilen bir kullanıcının çevrimdışı bir makinede sonsuza dek giriş
  yapabilmesi kabul edilemez. Sınır aşıldığında kurulum yalnız çevrimiçi giriş
  kabul eder.
- Danışma kilitleri (ADR 0020 §2) bu serviste tutulur.
- Servis yoksa Kontrol Merkezi **bugünkü gibi** tek makinede çalışmaya devam
  eder; hiçbir yetenek gerilemez.
