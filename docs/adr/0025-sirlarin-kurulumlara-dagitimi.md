# 0025 — Sırlar ve geçit ayarları kurulumlara dağıtılır

**Durum:** Kabul edildi · 2026-08-18
**ADR 0021 §6'yı ("sırlar bu sürümde merkeze taşınmaz") tersine çevirir.**

## Bağlam

Merkezî kimlik servisi canlı (ADR 0021) ve kimlik tarafı bitti: eşleme, kadro
göçü, pepper benimseme ve kendini onarma çalışıyor. 17–18.08.2026'da eşlenen
bir MacBook'ta **kimlik çalışıyor ama hiçbir iş ekranı çalışmıyordu**. Sebep
tek cümleyle: kimlik dışında hiçbir şey pakete girmiyor.

İki ayrı kaynak, ikisi de o makinenin diskinde doğup orada kalıyor:

| Nerede | Ne var | Neden geçmiyor |
|---|---|---|
| `config/local.yaml` | `modules.bld_api.base_url`, `modules.bbd_canteen_api.base_url`, `modules.store_api.read_only` | git dışıdır (K8) ve **pakete girmez** |
| `km_platform/secrets` kasası | 17 iş sırrı: `server.*.app_key`, `server.*.webhook_secret`, `server.bld.control_secret`, `canteen.device_token`, `canteen.qr_key`, `store.admin_token`, `bell.*` | kasa anahtarı (`data/secret.key`) makineye özeldir; kasa yeni kurulumda **boş doğar** |

Yani her yeni bilgisayar elle kuruluyordu: 17 sır tek tek giriliyor, `local.yaml`
elle yazılıyor. ADR 0021 bunu zaten öngörmüş ve kabul etmişti — "13 sır her
makinede elle yeniden girilir".

Bugün o kabul geçerli değil.

### Kullanıcının kararı

> "Her bilgisayar birebir merkez gibi olsun. Windows'tan da Mac'ten de kullanıcı,
> sanki merkez bilgisayardaymış gibi HER İŞİ yapabilsin. Sırmış şuymuş buymuş,
> birebir buradaki gibi. Bilgisayarların hepsi ortak kullanım, PIN ile ve denetim
> kaydıyla; kimse kötü amaçlı değil, sadece kurum personeli kullanıyor. Herkes
> rolünün izin verdiği her şeye erişebilsin."

Tehdit modeli **kullanıcı tarafından açıkça beyan edildi ve kabul edildi**:
kurulumlar kurum içindedir, fiziksel denetim altındadır, kullanıcılar personeldir.
Bu ADR o beyanın üzerine kuruludur; beyan değişirse karar da yeniden açılır.

### ADR 0021 §6 neden böyle yazılmıştı

Metin şunu diyordu: *"Sunucuların anahtarlarını ağa açık bir servise taşımak,
çözdüğünden büyük bir risk açar."* Endişe gerçekti ve bugün de gerçek. Değişen,
riskin öbür tarafı: elle kurulum artık **yapılmayan** bir iş. Yapılmadığı için
geçitler ölü kalıyor, ölü kaldığı için ikinci makine kullanılmıyor ve ADR 0021'in
tüm amacı (uygulamanın birden çok cihazda çalışması) boşa çıkıyor.

Riski kabul edilebilir kılan üç şey aşağıdaki karara gömüldü: sırlar merkezde
**şifreli** durur, dağıtım yalnız **eşlenmiş ve iptal edilmemiş** makinelere
yapılır, ve **her dağıtım denetim izine düşer**.

## Karar

### 1. Merkezde bir "kurulum paketi" ucu

```
GET /provisioning[?known_revision=N]   [kurulum]  → {revision, secrets, settings}
GET /provisioning/summary              [yönetim]  → anahtar ADLARI, değer YOK
PUT /provisioning                      [yönetim]  → sır ve ayar yükler
```

**Ad neden `provisioning`.** Türkçesi "kurulum paketi" ama API yolu İngilizce ve
ASCII (CLAUDE.md — Dil). `secrets` adı yanlış olurdu: uç sır olmayan modül
ayarlarını da taşıyor ve o ayarlar bilerek şifrelenmiş bir sır gibi
davranmıyor. `bootstrap` ise eşlemeyle karışırdı — eşleme bir kez olur, bu uç
her senkronda sorulur.

**Çekme kapısı yalnız `require_installation`dır.** Bir kişinin izni sorulmaz ve
bu K9'a aykırı değildir: paket **makinenin kendi kurulumudur**, bir kişinin
işlemi değil. Sidecar geçitleri açılışta, henüz kimse giriş yapmamışken kurmak
zorundadır; ize bağlansaydı taze bir kurulumda paket hiç çekilemezdi.

**İptal edilen kurulum paket alamaz.** `require_installation` yalnız
`status = 'active'` satırları eşleştirir; iptal edilmiş token 401 alır ve
"iptal edildin" bile denmez. İptalin kadroyu kesip sunucu parolalarını
kesmemesi, iptali anlamsız kılardı.

**`known_revision` gönderilir.** Değişmemişse `{"changed": false}` döner ve
**sırlar ağa hiç çıkmaz**. Kadroyla aynı desen (ADR 0021 §2); faydası burada
daha büyük.

Paket revizyonu **kadro revizyonundan ayrı sayaçtır**. Tek sayaç olsaydı her
kullanıcı eklendiğinde sahadaki bütün kurulumlar bütün sırları yeniden çekerdi.

### 2. Merkezde sır düz metin durmaz

Değerler `KM_IDENTITY_VAULT_KEY` ile Fernet (AES-128-CBC + HMAC) kullanılarak
şifrelenip `provisioning_items` tablosuna yazılır. Anahtar **veritabanında
durmaz**: aynı dosyada hem kilit hem anahtar tutmanın karşılığı yok. Coolify'da
ortam değişkenidir; `/data/identity.sqlite` yedeği sızarsa sırlar açılmaz.

**Anahtar yoksa uç kapalıdır (503).** Sessizce düz metne düşmek, bir gün bütün
sunucu parolalarını okunur bırakırdı. Bozuk biçimli anahtar da 503'tür, 500
değil: 500 "kod patladı" der, 503 "bu uç şu an kapalı" der ve nedeni cümlenin
içindedir. Aynı karar `KM_IDENTITY_ADMIN_TOKEN` için de verilmişti (ADR 0021).

**Modül ayarları da şifrelenir.** Sır değiller; ama sırla ayarı farklı
yollardan saklamak, bir gün yanlış etiketlenmiş bir sırrın düz metne düşmesi
demekti. Tek yol vardır ve o yol şifreler; `kind` sütunu yalnız değerin
kurulumda **nereye** yazılacağını söyler, ne kadar korunacağını değil.

Anahtar **adları** düz durur — özet ekranı kasa anahtarı olmadan da okunabilmeli:
anahtarı kaybolmuş bir merkezde bile "hangi sırlar dağıtılıyordu" sorusunun
cevabı kalmalı.

### 3. Merkeze yazma yönetim yolundan olur

`PUT /provisioning` kapısı `require_management`tır — iki yol: eşlenmiş makine +
`installations.manage` izinli kişi (olağan), ya da `KM_IDENTITY_ADMIN_TOKEN`
(acil). Acil yolda arkada bir kişi yoktur ve denetim satırı `user_id` alanını
**boş bırakır**; uydurmaz.

Gönderen taraf `scripts/push-secrets.py`. **Varsayılanı kuru provadır** ve kuru
provada tek bir ağ isteği yapmaz. Değerler hiçbir zaman ekrana yazılmaz —
anahtar adı, uzunluk ve sha256'nın ilk 8 hanesi yazılır; terminale düşen bir
sır oradan kayıtlara ve ekran görüntülerine düşer.

**Gönderilecek anahtarlar açık listedir** (`SECRET_KEYS`, `SETTING_KEYS`).
"Kasadaki her şeyi gönder" kuralı, kasanın bir gün doğuracağı yeni bir
makine-özel anahtarı da gönderirdi.

**Uç yalnız ekler ve günceller, silmez.** Gönderilmeyen anahtar dokunulmadan
kalır; kısmi bir listeyle koşturulan betik merkezdeki paketi budamamalı.

### 4. Makineye özel anahtarlar dağıtılamaz

`identity_sync.*` (kurulum token'ı, özel anahtar, kurulum kimliği, yönetim
anahtarı) ve `core.pin_pepper` + yoldaşları **hiçbir koşulda** paketle
taşınmaz:

- `identity_sync.*` o makinenin **kendi kimliğidir**. Dağıtılsaydı bütün
  kurulumlar tek kurulum token'ını paylaşır, biri iptal edilince hepsi düşerdi.
- `core.pin_pepper` zaten **eşlemeyle** gelir (ADR 0021 §4 / `_adopt_pepper`).
  Paketle de gönderilseydi, çalışan bir makinenin anahtarı bir senkron turunda
  ezilir ve oradaki herkesin girişi bir anda kırılırdı — düz PIN'ler hiçbir
  yerde saklanmadığı için geri getirilemez.

Yasak **üç yerde birden** uygulanır: betikte (gönderilmez), merkezde (yazılmaz,
400 döner), kurulumda (gelirse de kasaya yazılmaz). Üçü fazlalık değil; betiği
atlayan bir istek ya da yanlış yapılandırılmış bir merkez, tek kapıda dururdu.

### 5. Her sır dağıtımı denetim izine düşer

`provisioning.pull` satırı **hangi kurulumun** (kurulumun `installation_id`
sütunu, ADR 0021 §5), **ne zaman** ve **kaç anahtar** aldığını yazar.
`provisioning.push` satırı kimin hangi anahtarları yüklediğini yazar.

**Değer yazılmaz**, yalnız anahtar adı: iz satırı silinmez ve sırrı ize yazmak
sırrın ömrünü sonsuz yapardı (`installations.pair_code` ile aynı gerekçe).

"Değişmedi" yanıtı dağıtım değildir ve iz bırakmaz; bıraksaydı her senkron turu
izi doldurur, gerçek dağıtımlar arasında kaybolurdu.

### 6. Kurulum paketi senkronda uygulanır

`km_platform/identity_sync` kadroyu tazeledikten sonra paketi de çeker
(`fetch_provisioning`). Sırlar **kasaya**, modül ayarları **çekirdek ayar
deposuna** (ADR 0018 §4) yazılır. İkisi ayrı yere gider çünkü ayrı şeylerdir:
kasa şifreler ve değeri ekranda göstermez, ayar deposu düz durur ve Sistem
Ayarları ekranında görünür (K8 — sır ayar deposuna yazılmaz).

Eşlemenin hemen ardından da gelir: `pair()` zaten `sync()` çağırıyor.

**Paket uygulanamazsa hiçbir yetenek gerilemez** (K7): revizyon işareti
yazılmaz, bir sonraki tur yeniden dener, kimlik ve giriş etkilenmez. Merkez
0025 öncesi bir sürümse (uç yok → 404) bu bir arıza değildir, sessizce geçilir.

## Elenen alternatifler

- **Sırları da eşleme yanıtında göndermek.** Pepper öyle gidiyor (ADR 0021 §4) ve
  kanal yeterince dar. Ama eşleme **bir kez** olur; bir sır döndüğünde her
  makineyi yeniden eşlemek gerekirdi. Paket ayrı bir uçta durmalı ki
  tazelenebilsin.
- **Kasa anahtarını eşleme token'ına bağlamak** (ADR 0021 §6'nın "değerli fikir"
  dediği şey): token iptal edilince makinedeki sırlar açılamaz hâle gelir.
  Çalınan dizüstü riskini gerçekten kapatır, ama **çevrimdışı çalışma** isteğiyle
  gerilim içindedir — merkez ulaşılamazken kasa açılamazsa uygulama açılmaz.
  Bu ADR'de yapılmadı; hâlâ ayrı bir karara açıktır.
- **`local.yaml`'ı pakete koymak.** K8'i doğrudan çiğner: sır depoya girer,
  paket herkese dağıtılır.
- **"Altın kopya": `secret.key` + sqlite dosyalarını elle kopyalamak.** ADR 0021
  bunu zaten elemişti; sıfır kodla çalışır ama tüm sunucu parolaları her
  dizüstünde durur, biri kaybolursa hepsi döndürülür, ve kopyalama her
  değişiklikte tekrarlanır.

## Sonuçlar

- **Yeni kurulum artık elle sır girmez.** Eşlenen makine sırlarını ve geçit
  ayarlarını aynı turda alır.
- `KM_IDENTITY_VAULT_KEY` **zorunlu değildir ama olmadan bu özellik yoktur.**
  Tanımsızsa uçlar 503 döner; kurulum bugünkü gibi kendi kasasıyla çalışır.
  Anahtar bir kez üretilir ve **kaybedilirse dağıtılan paket açılamaz** —
  merkeze yeniden yüklenmesi gerekir (`push-secrets.py --uygula`).
- Servis kabı artık `cryptography` paketini gerektirir
  (`services/identity/requirements.txt`, K11).
- **Kabul edilen risk, açıkça:** merkezî kimlik servisi ele geçirilirse
  saldırgan yalnız kadroyu değil **sunucu sırlarını** da hedefleyebilir. Bunu
  daraltan şey şifrelemenin merkezde olması değil — anahtar da orada. Daraltan
  şey, anahtarın **veritabanının dışında** (ortam değişkeni) durması ve
  yedeklerde bulunmamasıdır. Kullanıcı bu riski beyan edilen tehdit modeliyle
  kabul etti.
- **Kabul edilen ikinci risk:** eşlemesi çözülmüş bir makinede iş sırları kasada
  durmaya devam eder. `unpair` paketi silmez — silseydi yeniden eşlemek isteyen
  yöneticinin elindeki makine o an çalışmaz hâle gelirdi. Kaybolan cihazın
  çaresi `unpair` değil, **merkezden iptal ve yerinde silmedir**; `config/local.yaml`
  içindeki önbellek yaşı notu da aynı şeyi söylüyor.

## Açık kalan kapılar

Bunlar bilinen eksiklerdir; sessizce bırakılmadılar.

1. **Ayar hemen etkili olmaz.** Modül geçitleri adreslerini kurulurken okuyor
   (`modules/*/backend/module.py` → `setup`) ve ayar katmanı açılışta
   uygulanıyor (`km_core/http/app.py`). İlk eşlemeden sonra geçitler **bir
   sonraki açılışta** çalışır. Bu yeni bir davranış değil: Sistem Ayarları
   ekranından değiştirilen ayar da aynı yolu izler (ADR 0018 §4). Canlı
   yeniden yükleme ayrı bir karardır.
2. **Anahtar dağıtımdan çıkarılamıyor.** `PUT /provisioning` yalnız ekler ve
   günceller. Bir sırrı dağıtımdan tamamen çıkarmak bugün merkezin
   veritabanında elle yapılır. Silme ucu, "geri alma ekleyerek yapılır"
   ilkesiyle birlikte ayrıca tasarlanmalı.
3. **Kasa anahtarı döndürme (rotation) yolu yok.** `KM_IDENTITY_VAULT_KEY`
   değiştirilirse eski satırlar çözülemez; uç 503 der ve paket
   `push-secrets.py --uygula` ile yeniden yüklenir. Bu bilinçli olarak
   yeterli sayıldı (paket zaten kurulumların birinde tam olarak duruyor), ama
   otomatik bir yeniden şifreleme adımı yok.
4. **Dağıtılan sır kurulumda geri alınamıyor.** Merkezden bir sır kaldırılsa
   bile daha önce dağıtıldığı makinelerin kasasında kalır; kurulum tarafı
   yalnız ekler ve günceller.
