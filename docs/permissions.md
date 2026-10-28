# İzin Kataloğu ve Rol Matrisi (SABİT)

Bağlayıcıdır. Gerekçe: [adr/0007-kimlik-ve-yetkilendirme.md](adr/0007-kimlik-ve-yetkilendirme.md)

## Kavramlar

- **İzin (permission):** yapılabilecek tek bir iş. `database.query` gibi.
- **Kapsam (scope):** iznin hangi alanda geçerli olduğu. `bbd`, `bld`, `org`,
  hepsi için `*`. Yazımı `izin:kapsam` → `database.query:bld`.
- **Rol:** izin kümesi. Ön tanımlı **beş** rol vardır, yenisi tanımlanabilir.
  Kaynağı `km_core/security/identity.py` → `BUILTIN_ROLES`.
- **Kullanıcı:** **birden fazla rol** taşıyabilir. Etkin izinleri rollerinin
  **birleşimidir.**

Kodda rol adı sorulmaz, izin sorulur. `if role == "admin"` yasaktır.

## Roller

| id | Ad | Kapsam | Özet |
|---|---|---|---|
| `admin` | Admin | `*` | Tam yetki. Kullanıcı, rol, ayar, sır ve yıkıcı işlemler. |
| `bld_staff` | BLD Personeli | `bld` | BLD sunucuları ve Laravel tabanlı BLD veritabanı. BLD'ye dair her şey — **tek istisna KDS cihaz ayarlarıdır** ([aşağıya](#kds-cihaz-ayarları-ayrı-anahtardadır)). **BBD ekranlarını görmez** ([aşağıya](#bld-personeli-bbd-ekranlarını-görmez)). |
| `bbd_staff` | BBD Personeli | `bbd` | BBD sunucuları ve Bagisto çekirdekli BBD veritabanı. |
| `org_staff` | Kurum Personeli | `org` | Zil, çıktı merkezi, rehber. Sunucu ve veritabanına erişimi yok. |
| `accountant` | Mali Müşavir | `bbd` `bld` | Mali ekranlar: fatura, vergilendirme, cari hesap, raporlar. Kullanıcı, sır ve sunucu yönetimi yok. |

Bir kullanıcıya hem `bld_staff` hem `bbd_staff` atanırsa her iki kapsamı da alır.

### Servis rolleri (ADR 0028)

Kişiye verilmez; servis anahtarıyla bağlanan makinelerin (`users.kind =
'service'`) rolleridir. Kaynağı `km_core/security/identity.py` →
`BUILTIN_SERVICE_ROLES`. Çekirdek rolün yalnız varlığını garanti eder;
izinlerini onu `default_roles` ile öneren modüller verir.

| id | Ad | Özet |
|---|---|---|
| `mobile_service` | Mobil Servis | Mobil uygulama sunucusu (BBD Akademi API). İzinleri modül manifestlerinden gelir (ör. mobil geçit modülünün erişim izni). |
| `canteen_service` | Kantin Servisi | Kantin backend'i (olay itmesi, km-merkezi-senkron §6.1). Yalnız `bbd_canteen_api.events.push`. Anahtar `scripts/service-key.py create --name "Kantin" --role canteen_service --cidr <kantin sunucusu>`. |

**`admin` bütün izinleri taşır — servis rolüne önerilenler dahil.** Bir modül
bir izni `mobile_service`e öneriyorsa `default_roles` listesine `admin`i de
yazar (ör. mobil geçit: `mobile_gateway.access` → `[admin, mobile_service]`).
Bu, "admin tam yetkidir" kuralının sonucudur ve
`tests/core/test_rol_bld_staff.py` bunu sınar. Yönetici bu izinle kendi
oturumundan da geçidin uçlarını çağırabilir; dış uygulama ise yalnız servis
anahtarıyla bağlanır. Servis rolüne başka bir kişi rolünün izni sızdırılmaz:
`mobile_service` yalnız kendisine açıkça önerilen anahtarları alır.

Servis sorumlusu için izin denetimi kişininkiyle AYNIDIR: `has_permission`.
Yıkıcı işlemlerin PIN teyidi servis sorumlusunda her zaman reddedilir — PIN'i
yoktur. `X-KM-On-Behalf-Of` başlığı yetki vermez; yalnız denetim izine yazılır.

`accountant` kurum dışından da olabilir (serbest çalışan mali müşavir). Bu
yüzden kapsamı iki tarafı da kapsar ama çekirdek izinlerinin hiçbirini almaz:
gördüğü şey **kayıt**tır, **sistem** değil.

---

## Çekirdek izinleri

Bunları çekirdek tanımlar; modüller tanımlayamaz.

### Kullanıcı ve rol yönetimi
| İzin | Kapsamlı | Ne yapar |
|---|---|---|
| `users.view` | hayır | Kullanıcı listesini ve profillerini görür |
| `users.manage` | hayır | Kullanıcı ekler, düzenler, pasifleştirir |
| `users.set_password` | hayır | PIN atar ve sıfırlar (anahtarın adı ADR 0016'nın göçünden kalmıştır; ADR 0016 reddedildi) |
| `roles.view` | hayır | Rolleri ve izinlerini görür |
| `roles.manage` | hayır | Rol tanımlar, izin atar, kullanıcıya rol verir |

### Sistem
| İzin | Kapsamlı | Ne yapar |
|---|---|---|
| `settings.view` | hayır | Uygulama ayarlarını görür |
| `settings.manage` | hayır | Ayarları değiştirir, modül açar/kapatır |
| `audit.view` | hayır | Denetim izini okur |
| `installations.view` | hayır | Merkeze eşlenmiş kurulumları ve bu makinenin eşleme durumunu görür (ADR 0021) |
| `installations.manage` | hayır | Eşleme kodu üretir, kurulum iptal eder, bu kurulumun eşlemesini çözer — **yıkıcı: PIN teyidi ister** |
| `secrets.view` | hayır | Kasadaki kayıtları **listeler** — değerleri değil |
| `secrets.manage` | hayır | Kimlik bilgisi ekler, değiştirir, siler |

### Sunucu ve SSH
| İzin | Kapsamlı | Ne yapar |
|---|---|---|
| `servers.view` | evet | Sunucu envanterini görür |
| `servers.manage` | evet | Sunucu ekler, düzenler, kimlik bağlar |
| `ssh.execute` | evet | Uzak komut çalıştırır |
| `ssh.transfer` | evet | Dosya gönderir/alır |

### Veritabanı
| İzin | Kapsamlı | Ne yapar |
|---|---|---|
| `database.view` | evet | Bağlantıları, şemaları, tabloları görür |
| `database.query` | evet | Salt-okunur sorgu çalıştırır |
| `database.write` | evet | Veri değiştiren sorgu çalıştırır |
| `database.backup` | evet | Yedek alır |
| `database.restore` | evet | **Yıkıcı.** Yedekten geri yükler |

### Rehber
| İzin | Kapsamlı | Ne yapar |
|---|---|---|
| `directory.view` | hayır | İç rehberi (kurum personeli) görür |
| `directory.view_external` | hayır | Dış rehberi görür |
| `directory.manage` | hayır | Rehber kayıtlarını düzenler |

### Çıktı basma
| İzin | Kapsamlı | Ne yapar |
|---|---|---|
| `outputs.print` | hayır | Üretilmiş bir belgeyi CİHAZIN yazıcısına gönderir |

Baskı kullanıcının makinesinde yapılır (ADR 0026): kabuk belgenin baytlarını
`POST /api/outputs/document` ucundan alır ve yerel kuyruğa verir. O uç bu izni
ister. **`print.view` ile karıştırılmaz:** o bir modül izni ve Çıktı
Merkezi'nde HERKESİN çıktısını görme yetkisidir. "Elimdeki belgeyi bastırabilir
miyim" ile "bütün çıktı kayıtlarını görebilir miyim" ayrı sorulardır; ikincisi
birincisinin ön koşulu değildir. Mali Müşavir bu ayrım yapılmadan önce kendi
ürettiği raporu bastıramıyordu.

Uç nokta eski `print.view` / `settings.view` anahtarlarını da kabul etmeye devam
eder: izinleri elle düzenlenmiş kurulumlarda yeni anahtar dağıtılana kadar
baskılar kesilmesin.

---

## Modül izinleri

Modüller kendi izinlerini `module.yaml` içinde ilan eder. Aşağıdaki tablo,
manifesti bilinçli olarak daraltılmış modüllerin ilan ettikleridir.

> **Geliştirme kuralı (geçici).** BBD, BBD Store ve BLD gruplarındaki ekran
> modülleri şu an ikişer izin ilan ediyor — `<id>.view` ve `<id>.manage` — ve
> ikisi de **beş rolün hepsine** düşüyor. Ekranlar sırayla kodlanacağı için
> daraltma, o modülün işi başlarken yapılır. Tek tek listelenmezler; kaynak
> her zaman modülün kendi `module.yaml` dosyasıdır. Aşağıdaki tablo ve
> matrisler, daraltması **bilinçli olarak yapılmış** modülleri gösterir.
>
> **`bbd_*` modüllerinde bu kural artık `bld_staff` için geçerli DEĞİLDİR.**
> 17.08.2026 kararıyla rol dokuz BBD modülünün hiçbirinde yer almaz; yeni bir
> `bbd_*` modülü açılırken `default_roles` listesine `bld_staff` yazılmaz.
> Ayrıntı: [BLD personeli BBD ekranlarını görmez](#bld-personeli-bbd-ekranlarını-görmez).
>
> **Daraltmayı yaparken:** manifesti düzeltmek kurulu bir sistemde tek başına
> yetmez — bkz. [Manifest'i daraltmak tek başına yetmez](#manifesti-daraltmak-tek-başına-yetmez).

| Modül | İzin | Ne yapar |
|---|---|---|
| `bell` | `bell.view` | Haftalık zil saatlerini, grupları ve çalma günlüğünü görür |
| `bell` | `bell.manage` | Saatleri, grupları, sesleri ve anons metinlerini düzenler |
| `bell` | `bell.delete` | Grubu, yüklenmiş zil sesini ve kullanılmayan anonsları KALICI siler (geri alınamaz, PIN ister) |
| `bell` | `bell.ring_now` | Elle zil çalar, grup çağırır, öğrenci çağırma anonsu yapar (`POST /api/bell/page`) |
| `bbd_students` | `bbd_students.canteen_controls` | Veli kantin kontrollerini düzenler: borç/günlük limit, kurum engeli, veli engelini kaldırma, yasaklı ürünler (admin, bbd_staff, org_staff) |
| `bbd_students` | `bbd_students.groups` | Öğrenci gruplarını açar, adlandırır, arşivler; tarihli üyelik ekler/bitirir/taşır, geçmiş düzeltir (admin, bbd_staff, org_staff). Okuma `bbd_students.view` ile |
| `bbd_students` | `bbd_students.lifecycle` | Öğrenciyi pasifleştirir (gerekçe, ileri tarih, toplu) ve yeniden etkinleştirir — kantin satışı, tablet ve Akademi girişi kapanır/açılır (admin, bbd_staff, org_staff) |
| `bbd_students` | `bbd_students.delete` | Pasif öğrenciyi KALICI siler — tek tek (kart → Tehlikeli işlemler; ad yazılarak) ya da toplu (liste, en çok 50; sayı yazılarak); önce ön kontrol, PIN teyidi (yıkıcı). Engeller: etkin öğrenci, kantin bakiyesi ≠ 0, açık ödeme talebi, kurum cari borcu, Akademi'ye ulaşılamıyor; kurum alacağı engellemez — yalnız admin |
| `bbd_students` | `bbd_students.groups_delete` | Arşivdeki sınıfı KALICI siler (Sınıflar → Arşiv → Sil; sınıfın adı yazılarak + gerekçe + PIN teyidi, yıkıcı). Üyelikleri gider, Akademi'de listelerden kalkar (geçmiş korunur), zil çağrıları iptal olur — yalnız admin |
| `mobile_gateway` | `mobile_gateway.events.view` | Akademi'ye olay iletiminin durumunu görür: gecikme, ölü mektup (BBD Akademi → Ayarlar → Senkron; admin, bbd_staff). Servis anahtarına verilmez |
| `mobile_gateway` | `mobile_gateway.events.manage` | Ölü mektupları Akademi'ye yeniden gönderir, iletimi bir sıradan yeniden başlatır — yalnız admin |
| `bbd_canteen_api` | `bbd_canteen_api.events.push` | Kantin backend'inin KM'ye olay itmesi (servis anahtarı; admin, canteen_service) |
| `bbd_class_schedule` | `bbd_class_schedule.view` | Zil saatlerinin salt okunur görünümü |
| `print` | `print.view` | Üretilmiş çıktıların listesini, önizlemesini ve baskı geçmişini görür |
| `print` | `print.reprint` | Üretilmiş bir çıktıyı yeniden yazıcıya gönderir |
| `antivirus` | `antivirus.view` | Tarama geçmişini, karantinayı, imza durumunu görür |
| `antivirus` | `antivirus.scan` | Tarama başlatır ve durdurur |
| `antivirus` | `antivirus.manage` | Tarama takvimi, hariç tutulan yollar, imza güncelleme |
| `antivirus` | `antivirus.quarantine` | **Yıkıcı.** Dosyayı karantinaya alır / geri yükler |
| `antivirus` | `antivirus.delete_threat` | **Yıkıcı.** Karantinadaki dosyayı kalıcı siler |
| `bld_kds` | `bld_kds.view` | Kasaları, ayarlarını, komut geçmişini, fiş kaydını ve siparişleri görür |
| `bld_kds` | `bld_kds.manage` | Kasa açar ve adlandırır, eşleme kodu üretir, zararsız komut gönderir, sipariş revizyonu yazar ve **durum ilerletir** |
| `bld_kds` | `bld_kds.settings` | Kasanın **yönetilen 24 ayarını** yazar (çalışma, yazıcı, ses, kilit politikası) |
| `bld_kds` | `bld_kds.devices` | **Yıkıcı.** Kasa iptali ve `restart` · `clear_failed` · `clear_queue` · `update` · `unpair` komutları |
| `bbd_canteen_devices` | `bbd_canteen_devices.view` | Kantin kiosklarını, eşleme durumlarını ve son görülme zamanlarını görür |
| `bbd_canteen_devices` | `bbd_canteen_devices.manage` | Kiosk açar ve adlandırır, 8 haneli eşleme kodu üretir |
| `bbd_canteen_devices` | `bbd_canteen_devices.devices` | **Yıkıcı.** Kiosk eşlemesini iptal eder (token silinir, cihaz satış yapamaz); PIN ister |
| `bld_subscriptions` | `bld_subscriptions.view` | Abonelikleri, talepleri, sözleşmeleri, dönem ödemelerini ve fiyat geçmişini görür |
| `bld_subscriptions` | `bld_subscriptions.manage` | Abonelik kurar, kuralını yazar, aktifleştirir/duraklatır, sözleşme gönderir, dönem borcu açar |
| `bld_subscriptions` | `bld_subscriptions.cancel` | **Yıkıcı.** Aboneliği iptal eder ve tahsilatı "ödendi" işaretler; ikisi de geri alınamaz, PIN ister |

### KDS cihaz ayarları ayrı anahtardadır

**Karar (17.08.2026).** BLD personeli **sipariş durumunu değiştirir, kasa
ayarına dokunmaz.** `bld_kds.manage` bu iki işi birden taşıyordu; ayar yazma
kısmı `bld_kds.settings` adıyla ayrıldı ve yalnız `admin`e verildi.

Ayrımın nedeni yetkinin büyüklüğü değil, **geri alınabilirliği**:

| İş | Anahtar | Yanlış yapılırsa |
|---|---|---|
| Sipariş durumu, revizyon, kasa adı | `bld_kds.manage` | Aynı ekrandan geri alınır; her adım revizyon geçmişine ve denetim izine yazılır |
| Yönetilen 24 ayarın kasaya yazılması | `bld_kds.settings` | Düzeltme **yalnız merkezden** gidebilir: yanlış bir `printer_device_path` fiş basımını tümüyle durdurur, `poll_seconds` kasayı oran sınırına sokar, bir kilit anahtarını `false` yapmak kasadaki düğmeyi öldürür |

**Anahtar BÖLÜNDÜ, DARALTILMADI** — ve bu bilinçli bir tercihtir.
`bld_kds.manage` daraltılsaydı `bld_staff`tan bir satır silmek gerekirdi;
kurulu bir sistemde bunun tek yolu göç yazmaktır ([Manifest'i daraltmak tek
başına yetmez](#manifesti-daraltmak-tek-başına-yetmez)) ve sipariş durumunu
ilerletme yetkisi de birlikte giderdi. Yeni anahtar ise **hiç kimseye
verilmemiş** olarak doğar: `grant_defaults` yalnız ekler, `bld_kds.settings`
satırı `bld_staff` için hiç yazılmaz ve geri alınacak bir şey olmaz. Daraltmayı
kurulu sistemde de geçerli kılmanın ucuz yolu budur.

`bld_sales_settings` **bu istisnanın dışındadır.** İki ekranın da adında "ayar"
geçiyor ve karışmaya en açık nokta burasıdır; ayrılan şey **kasanın** (KDS
cihazının) ayarıdır. Satış kuralları — sabah kesim saati, kapalı gün, satış
şalteri — BLD'nin günlük işidir ve üç anahtarı da `bld_staff`tadır.

`print` modülünün ekranı **Çıktı Merkezi**'dir: yazıcı kuyruğunu değil,
üretilmiş rapor ve dışa aktarımların kaydını yönetir (ADR 0019). Yazıcı
donanımına erişim modülde değil, `km_platform/printer` yeteneğindedir (ADR
0006/0014). ADR 0019 §6 izinleri `outputs.*` diye anıyor; manifest kapısı izin
anahtarının modül kimliğiyle başlamasını şart koştuğu için anahtarlar
`print.view` / `print.reprint` yazıldı — anlam birebir aynıdır.

### BLD personeli BBD ekranlarını görmez

**Karar (17.08.2026).** Kullanıcının sözü: *"bld personeli bbd ekranlarını da
göremesin."* `bld_staff` bugüne dek on dört `bbd_*` anahtarı taşıyordu; hepsi
alındı. Rolün izin sayısı 64'ten **50**'ye indi.

**Neden.** BBD ve BLD ayrı kurumların ekranlarıdır. BLD personelinin işi BLD
sipariş, menü, mutfak ve abonelik ekranlarındadır; kantin öğrencisinin adı,
veli telefonu, bakiyesi ve ders takvimi o işin parçası değildir. Bu izinler
rolde bir karar sonucu değil, geliştirme kuralının kalıntısı olarak duruyordu:
ekran modülleri "şimdilik beş rolün hepsine açık" ilan ediliyor ve daraltma
modülün işi başlayınca yapılıyor ([yukarı](#modül-izinleri)). BBD tarafında o
daraltma bugün yapıldı.

**Bu karar KDS istisnasıyla çelişmez.** İkisi ayrı şeyi söyler: KDS istisnası
rolün *kendi alanı* içinde nerede durduğunu, bu karar *alanın kendisinin*
nerede bittiğini belirler.

Alınan on dört anahtar:

| Modül | Alınan anahtarlar |
|---|---|
| `bbd_bulk_sale` | `.view` · `.manage` |
| `bbd_canteen_backups` | `.view` |
| `bbd_canteen_products` | `.view` · `.manage` |
| `bbd_canteen_reports` | `.view` |
| `bbd_class_schedule` | `.view` |
| `bbd_lunch` | `.view` · `.manage` |
| `bbd_payment_request` | `.view` |
| `bbd_sms` | `.view` |
| `bbd_students` | `.view` · `.manage` · `.qr` |

`bbd_canteen_api` listede yoktur: **hiç izin ilan etmez** ve ekranı da yoktur
(diğer modüllere `canteen.api` yeteneği verir). Alınacak bir anahtarı hiç
olmadı.

**Diğer roller DEĞİŞMEDİ.** On dört anahtarın hepsi `admin` ve `bbd_staff`ta,
çoğu `org_staff` ve `accountant`ta durmayı sürdürür. Kullanıcı tek bir rolden
söz etti.

**`store_*` izinlerine DOKUNULMADI** — dokunulacak bir şey de yoktu:
`bld_staff` bugün **hiçbir** `store_*` izni taşımıyor. Mağaza ekranları
`admin`, `bbd_staff`, `org_staff` ve `accountant` arasında dağılmıştır.

**Bu daraltma anahtar BÖLEREK yapılamazdı.** KDS ayarında olduğu gibi yeni bir
anahtar doğurmak burada işe yaramaz: kapatılmak istenen şey bir işlemin bir
parçası değil, ekranın **tamamıdır**. Karşılığı `role_permissions` satırının
yokluğudur ve kurulu sistemde bunun tek yolu satırı silen bir göçtür —
`0009_bld_staff_bbd_ayrimi` (`km_core/security/migrations.py`). Göç idempotenttir
ve sildiği her satırı denetim izine `roles.manage` olarak yazar. Bkz.
[Manifest'i daraltmak tek başına yetmez](#manifesti-daraltmak-tek-başına-yetmez).

---

## Rol → izin matrisi

✓ verilir · ✗ verilmez · Kapsamlı izinler rolün kapsamıyla verilir.

| İzin | Admin | BLD Personeli | BBD Personeli | Kurum Personeli | Mali Müşavir |
|---|:---:|:---:|:---:|:---:|:---:|
| `users.view` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `users.manage` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `users.set_password` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `roles.view` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `roles.manage` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `settings.view` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `settings.manage` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `audit.view` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `installations.view` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `installations.manage` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `secrets.view` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `secrets.manage` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `servers.view` | ✓ `*` | ✓ `bld` | ✓ `bbd` | ✗ | ✗ |
| `servers.manage` | ✓ `*` | ✗ | ✗ | ✗ | ✗ |
| `ssh.execute` | ✓ `*` | ✓ `bld` | ✓ `bbd` | ✗ | ✗ |
| `ssh.transfer` | ✓ `*` | ✓ `bld` | ✓ `bbd` | ✗ | ✗ |
| `database.view` | ✓ `*` | ✓ `bld` | ✓ `bbd` | ✗ | ✗ |
| `database.query` | ✓ `*` | ✓ `bld` | ✓ `bbd` | ✗ | ✗ |
| `database.write` | ✓ `*` | ✓ `bld` | ✓ `bbd` | ✗ | ✗ |
| `database.backup` | ✓ `*` | ✓ `bld` | ✓ `bbd` | ✗ | ✗ |
| `database.restore` | ✓ `*` | ✗ | ✗ | ✗ | ✗ |
| `directory.view` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `directory.view_external` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `directory.manage` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `outputs.print` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `bell.view` | ✓ | ✗ | ✓ | ✓ | ✗ |
| `bell.manage` | ✓ | ✗ | ✗ | ✓ | ✗ |
| `bell.delete` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `bell.ring_now` | ✓ | ✗ | ✓ | ✓ | ✗ |
| `bbd_class_schedule.view` | ✓ | ✗ | ✓ | ✓ | ✓ |
| `bbd_students.groups` | ✓ | ✗ | ✓ | ✓ | ✗ |
| `bbd_students.lifecycle` | ✓ | ✗ | ✓ | ✓ | ✗ |
| `bbd_students.delete` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `bbd_students.groups_delete` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `mobile_gateway.events.view` | ✓ | ✗ | ✓ | ✗ | ✗ |
| `mobile_gateway.events.manage` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `print.view` | ✓ | ✓ | ✓ | ✓ | ✗ |
| `print.reprint` | ✓ | ✓ | ✓ | ✓ | ✗ |
| `antivirus.view` | ✓ | ✓ | ✓ | ✗ | ✗ |
| `antivirus.scan` | ✓ | ✓ | ✓ | ✗ | ✗ |
| `antivirus.manage` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `antivirus.quarantine` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `antivirus.delete_threat` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `bld_kds.view` | ✓ | ✓ | ✗ | ✗ | ✗ |
| `bld_kds.manage` | ✓ | ✓ | ✗ | ✗ | ✗ |
| `bld_kds.settings` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `bld_kds.devices` | ✓ | ✓ | ✗ | ✗ | ✗ |
| `bbd_canteen_devices.view` | ✓ | ✗ | ✓ | ✗ | ✗ |
| `bbd_canteen_devices.manage` | ✓ | ✗ | ✓ | ✗ | ✗ |
| `bbd_canteen_devices.devices` | ✓ | ✗ | ✓ | ✗ | ✗ |
| `bld_subscriptions.view` | ✓ | ✓ | ✗ | ✗ | ✗ |
| `bld_subscriptions.manage` | ✓ | ✓ | ✗ | ✗ | ✗ |
| `bld_subscriptions.cancel` | ✓ | ✗ | ✗ | ✗ | ✗ |

Bilinçli kararlar:
- **`servers.manage` yalnızca Admin.** Personel sunucuya bağlanır ve iş yapar,
  envanteri ve kimlik eşlemesini değiştiremez.
- **`database.restore` yalnızca Admin.** Geri yükleme yıkıcıdır; yedek almak
  personelde, geri yüklemek yönetimdedir.
- **Kurum Personeli sunucu ve veritabanı görmez.** Zil, Çıktı Merkezi ve
  rehber ile sınırlıdır.
- **Yeniden baskı görüntülemeyle aynı rollerdedir.** Kayıt zaten görülebiliyorsa
  aynı çıktının kâğıda ikinci kez dökülmesi yeni bir yetki açmaz; ayrı anahtar
  tutulmasının nedeni yetki değil, denetim izinde ayrı görünmesidir.
- **BLD Personeli BBD ekranlarını görmez.** 17.08.2026 kullanıcı kararı; on
  dört `bbd_*` anahtarı rolden alındı. Ayrı kurumların ekranlarıdır ve BLD'nin
  işi orada bitmez, hiç başlamaz. Ayrıntı ve tam liste:
  [BLD personeli BBD ekranlarını görmez](#bld-personeli-bbd-ekranlarını-görmez).
- **Rehber herkeste okunur.** Uygulamanın ortak zeminidir.
- **Antivirüsü personel görür ve tarar, ama yönetemez.** Karantina ve kalıcı
  silme yıkıcıdır, yalnızca Admin'dedir. Kurum Personeli antivirüs görmez.
- **KDS'te yıkıcı komut BLD Personeli'nde, cihaz AYARI Admin'de.** Ters
  görünür ama değil: `bld_kds.devices` mutfağı geçici olarak durdurur ve
  düzeltmesi aynı ekrandadır (`restart` sonrası kasa kendi kendine döner);
  `bld_kds.settings` kasayı yanlış yapılandırır ve düzeltme yalnız merkezden
  gidebilir — kasa yanlış ayarla artık merkeze de ulaşamıyorsa hiç gidemez.
  Ayrıntı: [KDS cihaz ayarları ayrı anahtardadır](#kds-cihaz-ayarları-ayrı-anahtardadır).

---

## Rol → ekran matrisi

Bir ekran, gerektirdiği izinlerden **en az birine** sahip kullanıcıya görünür.
Menüde gizlenmesi yetmez; backend de reddeder (K9 — çift kapı).

| Ekran | Gerektirdiği izin | Admin | BLD | BBD | Kurum | Mali |
|---|---|:---:|:---:|:---:|:---:|:---:|
| Giriş (PIN) | — | ✓ | ✓ | ✓ | ✓ | ✓ |
| Ana panel | — | ✓ | ✓ | ✓ | ✓ | ✓ |
| Kullanıcı Yönetimi | `users.view` | ✓ | ✗ | ✗ | ✗ | ✗ |
| Sistem Ayarları | `settings.view` | ✓ | ✗ | ✗ | ✗ | ✗ |
| Sistem Sağlığı ¹ | `settings.view` | ✓ | ✗ | ✗ | ✗ | ✗ |
| KM Cihaz Eşle | `installations.view` | ✓ | ✗ | ✗ | ✗ | ✗ |
| Roller ve İzinler ² | `roles.view` | ✓ | ✗ | ✗ | ✗ | ✗ |
| Denetim İzi ² | `audit.view` | ✓ | ✗ | ✗ | ✗ | ✗ |
| Kimlik Kasası ² | `secrets.view` | ✓ | ✗ | ✗ | ✗ | ✗ |
| Sunucular | `servers.view` | ✓ | ✓ | ✓ | ✗ | ✗ |
| Uzak Terminal | `ssh.execute` | ✓ | ✓ | ✓ | ✗ | ✗ |
| Veritabanı | `database.view` | ✓ | ✓ | ✓ | ✗ | ✗ |
| Zil Sistemi | `bell.view` | ✓ | ✗ | ✓ | ✓ | ✗ |
| BBD Akademi ⁴ | `bbd_akademi.view` · `bbd_students.view` | ✓ | ✗ | ✓ | ✓ ⁴ | ✓ ⁴ |
| ~~Öğrenci Yönetimi~~ ⁴ | `bbd_students.view` | — | — | — | — | — |
| Ders Takvimi (salt okunur) | `bbd_class_schedule.view` | ✓ | ✗ | ✓ | ✓ | ✓ |
| Çıktı Merkezi | `print.view` | ✓ | ✓ | ✓ | ✓ | ✗ |
| Antivirüs *(yalnız Linux — ADR 0022)* | `antivirus.view` | ✓ | ✓ | ✓ | ✗ | ✗ |
| KDS Yönetimi ³ | `bld_kds.view` | ✓ | ✓ | ✗ | ✗ | ✗ |
| Kantin Kioskları | `bbd_canteen_devices.view` | ✓ | ✗ | ✓ | ✗ | ✗ |
| Rehber | `directory.view` | ✓ | ✓ | ✓ | ✓ | ✓ |
| Profilim | — | ✓ | ✓ | ✓ | ✓ | ✓ |

Kullanıcı Yönetimi, Sistem Ayarları, Sistem Sağlığı, Roller ve İzinler, Denetim
İzi ve Kimlik Kasası **çekirdek ekranıdır**: modül değildirler, kapatılamazlar
ve `modules/` tümüyle silinse bile dururlar. Menüde en alttaki "Sistem" grubunda
toplanırlar; dosyaları `apps/desktop/shell/core-panels/<ad>/` altındadır ve
kabuktaki sabit listeden (`shell/ui-kernel.js` → `CORE_PANELS`) gelirler
(ADR 0017). Kullanıcı Yönetimi ve Sistem Ayarları panelleri **yazılmıştır**.

¹ Menüde durur, paneli henüz yazılmadı; açıldığında "ekranı henüz yok" kartı
çıkar. ² Sözleşme olarak sabittir, kabuğun `CORE_PANELS` listesine henüz
girmedi — bugün menüde görünmez. Bu satırlar izin dağılımını bağlar; ekran
yazıldığında matris değil, yalnız kabuk listesi değişir.

³ **Ekran açılır, içindeki ayar bölümü izne göre kapanır.** BLD Personeli
ekranı görür ve siparişi ilerletir; `bld_kds.settings` taşımadığı için 24 ayar
alanı **salt okunur** çizilir ve yazma düğmesi nedeniyle birlikte kapanır.
Bayrağı sunucu söylüyor (`GET /api/bld_kds/devices` → `can.settings`), çünkü
kabuk panele izin listesi vermiyor. Bu K9'un **arayüz** yarısıdır; kapı
`PATCH /api/bld_kds/devices/{id}/settings` ucundaki izin denetimidir ve
arayüz hiç olmasa da o kapı kapalıdır.

⁴ **Öğrenci tek yerde (0.5.3, ADR 0031).** "Öğrenci Yönetimi" menüde çizilmez
(`ui.nav.replacedBy: bbd_akademi.students`); öğrenci yönetimi BBD Akademi →
Öğrenciler'dedir. BBD Akademi menüsü `bbd_students.view` taşıyana da görünür:
Akademi izni (`bbd_akademi.view`) olmayan Kurum Personeli ve Mali Müşavir paneli
YALNIZ "Öğrenciler" bölümüyle (KM kaydı: liste, toplu işlemler, gruplar,
öğrencinin "Kayıt ve veliler" kartı) görür; Akademi verisi ve vekil uçları
kapalıdır (vekil `bbd_akademi.view` ister). İşlem izinleri değişmedi
(`bbd_students.*`). BBD Akademi modülü kapatılırsa "Öğrenci Yönetimi" menüye
geri döner.

Kapsamlı ekranlarda (Sunucular, Veritabanı) **ekran açılır, içerik kapsamla
süzülür.** BLD Personeli yalnızca `bld` kapsamlı sunucu ve veritabanlarını
görür; iki rolü olan kişi ikisini birden görür.

---

## Manifest'i daraltmak tek başına yetmez

Bir modülün izinlerini `module.yaml` içinde daraltmak, **yalnızca hiç açılmamış
kurulumlarda** geçerlidir. Bir kez çalışmış her makinede eski geniş hâl
veritabanında durmaya devam eder.

### Neden

İki mekanizma da bilerek "ekleyici"dir ve ayrı ayrı doğrudur:

| Yer | Davranış | Neden böyle |
|---|---|---|
| `km_core/security/identity.py` → `grant_defaults()` | Yalnızca **ekler** (`INSERT OR IGNORE`) | Yöneticinin sonradan elle kaldırdığı izni her açılışta geri getirmemek için |
| `km_core/http/app.py` | **Keşfedilen her modülü** tohumlar, `enabled: false` olanlar dahil | İzin manifestte ilan edilir, kodun hazır olmasına bağlı değildir; aksi hâlde iskelet modülün ekranı menüden süzülür ve hiç görünmez |

İkisi birlikte tek yönlü bir kapı bırakır: **genişletme yürürlüğe girer,
daraltma girmez.** Yukarıdaki geliştirme kuralı gereği beş rolün hepsine açık
ilan edilmiş bir iskelet, manifesti daraltıldıktan sonra da o beş rolde durmayı
sürdürür. Ekran menüden kaybolmaz ve `/api/<id>` uçları hâlâ o rollere açıktır —
K9'un iki kapısı da açık kalır, çünkü sorun arayüzde değil, veridedir.

### Ne yapılır

Daraltma iki adımdır ve ikincisi elle yapılır:

1. `module.yaml` içindeki `permissions[].default_roles` daraltılır (gerekçesi
   manifest yorumuna yazılır).
2. `scripts/reconcile-permissions.py` çalıştırılır.

```bash
scripts/reconcile-permissions.py            # rapor — hiçbir şeyi değiştirmez
scripts/reconcile-permissions.py --uygula   # yalnız açık onayla geri alır
```

Betik sapmayı beş kutuya ayırır; yalnız **birincisi ve sonuncusu** elle karar
bekler:

| Kutu | Anlamı | Ne yapılır |
|---|---|---|
| `FAZLA` | Satır `grant_defaults` biçiminde yazılmış, manifest artık o rolü önermiyor | `--uygula` geri alır (onay: `UYGULA`) |
| `EKSİK` | Manifest öneriyor, veritabanında yok | Elle iş yok; çekirdek bir sonraki açılışta ekler |
| `ELLE` | Kapsam biçimi manifestten farklı (`izin:bld` gibi) | Dokunulmaz — `grant_defaults` çıktısı olamaz, biri bilerek yazmıştır |
| `YETİM ANAHTAR` | Modül duruyor ama o anahtarı hiç ilan etmiyor | Dokunulmaz — silinmiş mi, yeniden mi adlandırılmış, ayrımı insan yapar |
| `YETİM MODÜL` | Modülün manifesti diskte yok; izin satırları kalmış | `--uygula` siler (ayrı onay: `SIL`) — bkz. [Silinen modülün izinleri kalır](#silinen-modülün-izinleri-kalır) |

### Betik mi, göç mü

İkinci adımın **iki** yolu vardır ve seçim yöneticiye kalmaz:

| Yol | Ne zaman | Örnek |
|---|---|---|
| `scripts/reconcile-permissions.py` | Sapma **bu makinede** düzeltilir; karar yerel, yönetici başında | Bir modülün işi başlarken yapılan olağan daraltma |
| Çekirdek göçü (`km_core/security/migrations.py`) | Daraltma **her kurulumda** yürürlüğe girmeli; kimsenin betik koşturmasına bağlı olamaz | `0009_bld_staff_bbd_ayrimi` — kullanıcı kararıyla BLD personelinden alınan BBD ekranları |

Ölçüt "kaç satır" değil, **kararın nereye ait olduğudur.** Betik bir makinenin
sapmasını onarır; göç, kararın kendisini taşır. Elle koşturulan bir betiğe
bırakılan kullanıcı kararı, betiği koşturmayan makinede hiç yürürlüğe girmez ve
bunu kimse fark etmez.

Göç yazılırken iki şey zorunludur: **idempotentlik** (iki kez koşarsa bozmaz)
ve **denetim izi** (silinen her satır için bir `roles.manage` kaydı; iz,
silinen satırın yerini tutan tek kayıttır).

### Neden otomatik budama yok

`grant_defaults`'un ekleyici olmasının **tek sebebi** yöneticinin bilinçli
verdiği izni sessizce geri almamaktır. Açılışta otomatik budama yapmak tam da
onu kırardı: elle verilmiş her izin bir sonraki yeniden başlatmada sessizce yok
olurdu ve kimse nedenini bulamazdı. Bu yüzden betik raporlar, **insan karar
verir**; `--uygula` etkileşimli terminal ve açık onay ister, zamanlayıcıya
bağlanamaz. Geri alınan her satır denetim izine (`roles.manage`) yazılır.

Çekirdek geri alınanları geri getirmez: manifest o rolleri artık önermemektedir.

---

## Silinen modülün izinleri kalır

Bir modülü `modules/` altından kaldırmak izin satırlarını götürmez. Çekirdek
**keşfettiği** modülü tohumlar; keşfetmediğini ne tohumlar ne de siler
(`grant_defaults` yalnızca ekler). Silinen modülün manifesti de yoktur — yani
karşılaştırılacak taraf hiç kalmaz. Uzlaştırma betiği bu yüzden uzun süre bu
satırları **göremedi**: yalnızca diskte manifesti duran modülleri karşılaştırıyordu.

`<modul>.view` ve `<modul>.manage` satırları rollerde durmayı sürdürür. İlk
bakışta zararsızdır, çünkü o izni soran hiçbir uç kalmamıştır. İki sorun yaratır:

- **Roller ve İzinler ekranında** var olmayan bir modülün yetkisi gibi görünür;
  yetki tablosu artık sistemin gerçeğini anlatmaz.
- **Aynı kimlikle yeni bir modül yazılırsa** eski geniş küme yürürlükte olur.
  `bld_mail` yeniden açıldığında manifesti dar ilan edilse bile satırlar zaten
  veritabanındadır; daraltma yine yürürlüğe girmez ve modül, ilk günün
  geliştirme kuralıyla (beş rolün hepsi) açık başlar.

İkincisi asıl tehlikelidir: kimse yeni modülün izinlerini genişletmemiştir,
geniş hâl silinen modülden **miras** kalmıştır.

### Ne zaman koşulur

**Bir modül silindikten sonra**, aynı temizliğin parçası olarak:

```bash
scripts/reconcile-permissions.py            # YETİM MODÜL kutusunu listeler
scripts/reconcile-permissions.py --uygula   # ayrı bir onayla siler
```

`--uygula` iki silinebilir kutuyu iki **ayrı** soruya bağlar ve onay sözcükleri
bilerek farklıdır (`UYGULA` ve `SIL`). İki kutu iki ayrı karardır: manifestin
daralttığı rolleri onaylayan biri, silinen modülün artıklarını da onaylamış
sayılmaz.

Silinen her satır denetim izine `roles.manage` olarak yazılır; gerekçe alanı
`reconcile-permissions (yetim modül)` etiketini taşır.

### Betiğin sormadıkları

Bu kutu yıkıcıdır, bu yüzden "diskte yok" iddiası üç yerde daraltılmıştır:

| Satır | Neden sorulmaz |
|---|---|
| Çekirdek izni (`users.view`, `database.query:bld`) | Çekirdek izinleri de hiçbir manifestte geçmez; katalog `km_core/security/permissions.py` içinden **okunur**, betikte kopyası tutulmaz. Kopya olsaydı çekirdeğe eklenen her yeni izin ertesi gün "yetim" görünürdü |
| Manifesti okunamayan modül | Bozuk YAML "bu modül silinmiş" demek değildir. Klasör diskte durduğu sürece izinlerine dokunulmaz |
| Klasörü duran ama `module.yaml` dosyası olmayan modül | Yarım kalmış silme de olabilir, yeni açılmış klasör de. Ayrımı insan yapar |

Ayrıca: **kimliği okunamayan tek bir klasör bile varsa** `YETİM MODÜL` kutusu
hiç silinmez, yalnızca listelenir. Silmenin dayanağı "diskte yok" iddiasıdır ve
o klasörün hangi kimliği ilan ettiği bilinmezken iddia doğrulanamaz. Önce
manifest düzeltilir, sonra betik yeniden koşulur.

---

## Uygulama kuralları

1. Yetki denetimi tek bir yerden geçer; her uç nokta gerektirdiği izni ilan
   eder. İlan etmeyen uç nokta reddedilir (varsayılan: kapalı).
2. Kapsamlı bir izin kapsam belirtilmeden sorulamaz.
3. Yıkıcı **çekirdek** işlemleri (`database.restore`, `users.manage` silme,
   `roles.manage` — kullanıcı açmak ve rollerini DEĞİŞTİRMEK —, başkasının
   PIN'ini sıfırlamak (`users.set_password`), `installations.manage` ile eşleme
   çözme) izin yeterli olsa bile **PIN teyidi** ister (yöneticinin kendi PIN'i,
   gövdede `confirmPin`; art arda 5 yanlış PIN'de teyit 15 dk kapanır). Bunlar
   seyrek ve kurumsal işlemlerdir. Uç başına durum:
   [security/endpoint-envanteri.md](security/endpoint-envanteri.md).
   **İstisna — BBD Store:** `store_*` modüllerindeki yıkıcı ve para harcayan
   işlemler PIN yerine **gerekçeli onay** ister (ayrı izin anahtarı + zorunlu
   gerekçe + çift denetim kaydı + kuru prova). Gerekçe:
   [ADR 0012](adr/0012-magaza-yikici-islem-onayi.md). Bu yüzden `store_*`
   izinleri `destructive: true` bayrağı **taşımaz**.
4. Her yetki reddi ve her yıkıcı işlem denetim izine yazılır.
5. Yeni modül kendi izinlerini ilan eder ve `permissions[].default_roles` ile
   hangi ön tanımlı rollere düşeceğini önerir. Çekirdekte değişiklik olmaz.
6. Son admin pasifleştirilemez veya `admin` rolü elinden alınamaz.

<!-- izin-katalogu:basla — tools/security/endpoint_inventory.py üretir, elle düzenlenmez -->

## Modül izinleri — tam katalog (üretilir)

Kaynak: her modülün `module.yaml` → `permissions`. Keşfedilen HER modül (kapalılar dahil:
izin manifestte ilan edilir, rollere düşer). Yıkıcı = `destructive: true` — uçta PIN
teyidi ya da gerekçeli onay beklenir (`tests/core/test_guvenlik_envanteri.py`).

| İzin | Modül | Durum | Yıkıcı | Varsayılan roller | Açıklama |
|---|---|---|---|---|---|
| `antivirus.delete_threat` | `antivirus` | loaded | evet | `admin` | Karantinadaki dosyayı kalıcı silme. |
| `antivirus.manage` | `antivirus` | loaded |  | `admin` | Tarama takvimi, hariç tutulan yollar, imza güncelleme. |
| `antivirus.quarantine` | `antivirus` | loaded | evet | `admin` | Bulaşmış dosyayı karantinaya alma ve geri yükleme. |
| `antivirus.scan` | `antivirus` | loaded |  | `admin`, `bld_staff`, `bbd_staff` | Tarama başlatma ve durdurma. |
| `antivirus.view` | `antivirus` | loaded |  | `admin`, `bld_staff`, `bbd_staff` | Tarama geçmişini, karantinayı ve imza durumunu görüntüleme. |
| `bbd_akademi.academic` | `bbd_akademi` | loaded |  | `admin`, `bbd_staff` | Ders programı ve zil dilimleri, etüt ve tek seferlik ders, ödev, dönem, kurum takvimi, sınıf grubu ve çalışma grubu, öğrencinin ve sınıfın müfredat seti (atama), hedef önerisi. |
| `bbd_akademi.announcements` | `bbd_akademi` | loaded |  | `admin`, `bbd_staff` | Duyuru oluşturma, hedefleme, zamanlama, yayımlama ve silme. |
| `bbd_akademi.attendance` | `bbd_akademi` | loaded |  | `admin`, `bbd_staff` | Yoklama alma ve düzeltme. |
| `bbd_akademi.books` | `bbd_akademi` | loaded |  | `admin`, `bbd_staff` | Kitap talepleri (siparişi yeniden deneme, teslim, sipariş PDF'i) ve kitap satışı: öğrencilere kurumdan kitap satma (kurum carisine kalem, nakit / kart tahsilatı ya da veliye ödeme bağlantısı; kantinden ayrı), satır iptali / iadesi, toplu teslim. |
| `bbd_akademi.campus` | `bbd_akademi` | loaded |  | `admin`, `bbd_staff` | Akademi üzerinden anlık kurum işleri: öğrenci çağırma (anons), tablet ek süre, engel ve oturum bitirme. |
| `bbd_akademi.canteen` | `bbd_akademi` | loaded |  | `admin` | Akademi üzerinden kantin işlemleri (kurum engeli, kantin nakit tahsilatı). Asıl ekranı Öğrenciler modülüdür; bu izin yalnız vekil içindir. |
| `bbd_akademi.contracts` | `bbd_akademi` | loaded |  | `admin`, `bbd_staff` | Sözleşme kurulumu ve oluşturma: Kayıt ve ödeme planı sihirbazıyla sözleşme oluşturma, sözleşme maddelerini düzenleme (yeni sürüm), güncel bilgilerle yenileme, belge üretme, sözleşme iptali, hizmet kataloğu. Sözleşmeleri görmek, ıslak imza işaretlemek, tarama eklemek ve toplu belge çıkarıp yazdırmak kurum finansı iznidir (bbd_akademi.finance); madde şablonu ve sözleşme ayarları kurum ayarıdır (bbd_akademi.settings). |
| `bbd_akademi.curriculum` | `bbd_akademi` | loaded |  | `admin` | Müfredat kataloğu: kuruma özel program/ünite/konu ekleme, program istisna kuralları (toplu). Öğrenciye / sınıfa müfredat ATAMAK akademik iştir (bbd_akademi.academic). |
| `bbd_akademi.evaluations` | `bbd_akademi` | loaded |  | `admin`, `bbd_staff` | Öğrenci değerlendirme: dönem formlarını doldurma, yayımlama ve geri alma, değerlendirme raporlarının dışa aktarımı. |
| `bbd_akademi.exams` | `bbd_akademi` | loaded |  | `admin`, `bbd_staff` | Deneme tanımı, sonuç girişi ve düzeltme, toplu sonuç yükleme ve geri alma, cevap anahtarı ve soru-konu eşlemesi, yayımlama, dışa aktarım. |
| `bbd_akademi.finance` | `bbd_akademi` | loaded |  | `admin`, `bbd_staff` | Kurum cari hesapları (kantinden ayrı): görüntüleme, taksit planı, borç kalemi, tahsilat kaydı, gecikme hatırlatması, ekstre. |
| `bbd_akademi.finance_void` | `bbd_akademi` | loaded | evet | `admin` | Kurum tahsilatını iptal etme. İzin yeterli olsa bile işlemden hemen önce PIN teyidi ister. |
| `bbd_akademi.learning` | `bbd_akademi` | loaded |  | `admin`, `bbd_staff` | Öğrenme araçlarında günlük koçluk: çalışma planı görevi atama ve erteleme talepleri, koç notları, erken uyarı takibi (not, görüşme, veliye bildirim, çözme). |
| `bbd_akademi.learning_setup` | `bbd_akademi` | loaded |  | `admin` | Öğrenme araçları kurulumu (yalnız KM): çalışma planı şablonları, okuma listeleri ve okuma ligi, bilgi kartı desteleri, kurum görselleri, değerlendirme şablonları ve dönemleri, erken uyarı eşikleri. |
| `bbd_akademi.privacy` | `bbd_akademi` | loaded | evet | `admin` | KVKK talepleri: uygulama kullanıcılarının hesap silme ve veri indirme taleplerini görme, onaylama ya da reddetme, silinmiş hesabın erişimini yeniden açma. Silme onayı kişisel veriyi geri dönüşsüz siler. |
| `bbd_akademi.recordings` | `bbd_akademi` | loaded |  | `admin`, `bbd_staff` | Ders kayıtları (akıllı tahta): tahta ekleme, eşleme kodu, iptal ve tanılama; derslikler; kayıtları gizleme / gösterme, silme, kim izledi, ek öğrenci / grup paylaşımı; depolama kotası. Kayıt ayarlarını (saklama süresi, depolama tavanı, kalite, kamera izni) değiştirmek kurum ayarıdır (bbd_akademi.settings). |
| `bbd_akademi.requests` | `bbd_akademi` | loaded |  | `admin`, `bbd_staff` | İzin (mazeret), görüşme ve etüt taleplerini onaylama, reddetme, zaman verme. |
| `bbd_akademi.resources` | `bbd_akademi` | loaded |  | `admin`, `bbd_staff` | Kaynaklar: kurum kaynağı ekleme, içindekiler ve konu eşlemesi, kaynak paketleri ve otomatik atama, kaynak atama. |
| `bbd_akademi.settings` | `bbd_akademi` | loaded |  | `admin` | Kurum ayarları: kantin kuralları, bildirim varsayılanları, tema ve logo, özellik bayrakları, entegrasyon anahtarları, anons şablonları, dizin eşitleme, denetim kaydı. |
| `bbd_akademi.staff` | `bbd_akademi` | loaded |  | `admin` | Mobil yönetici erişimi (personel listesi, Akademi'ye itilir) ve Akademi rolleri. |
| `bbd_akademi.view` | `bbd_akademi` | loaded |  | `admin`, `bbd_staff` | Akademi ekranlarını görüntüleme: panel, öğrencilerin akademik görünümü, müfredat, kaynaklar, ders programı, yoklama, ödev, deneme sonuçları, kitap talepleri. |
| `bbd_bulk_sale.manage` | `bbd_bulk_sale` | loaded |  | `admin`, `bbd_staff`, `org_staff`, `accountant` | Toplu satış işleme ve sepet şablonlarını düzenleme. |
| `bbd_bulk_sale.reverse` | `bbd_bulk_sale` | loaded | evet | `admin`, `bbd_staff` | İşlenmiş satışı geri alma (kantinde ters kayıt oluşturur). |
| `bbd_bulk_sale.view` | `bbd_bulk_sale` | loaded |  | `admin`, `bbd_staff`, `org_staff`, `accountant` | Toplu satış ekranını ve geçmiş partileri görüntüleme. |
| `bbd_canteen_api.events.push` | `bbd_canteen_api` | loaded |  | `admin`, `canteen_service` | Kantin backend'inin KM'ye olay itmesi (satış/bakiye, kasadan öğrenci, kontroller, ödeme talebi). Servis anahtarı içindir. |
| `bbd_canteen_backups.manage` | `bbd_canteen_backups` | loaded |  | `admin`, `bbd_staff` | Elle yedek aldırma ve yedeği bu makineye indirme. |
| `bbd_canteen_backups.view` | `bbd_canteen_backups` | loaded |  | `admin`, `bbd_staff`, `org_staff`, `accountant` | Yedek listesini ve tazelik durumunu görüntüleme. |
| `bbd_canteen_devices.devices` | `bbd_canteen_devices` | loaded | evet | `admin`, `bbd_staff` | YIKICI: kiosk eşlemesinin iptali. Bu yetkiyi taşıyan kişi kantindeki bir cihazı satış yapamaz hâle getirir — token silinir, cihaz eşleme ekranına düşer ve sahada yeni kod girilene kadar HİÇ çalışmaz; iptal edilen kiosk yeni kod da alamaz. Kayıt silinmez, karar geri alınamaz. |
| `bbd_canteen_devices.manage` | `bbd_canteen_devices` | loaded |  | `admin`, `bbd_staff` | Kiosk kaydı açma, adlandırma ve TEK KULLANIMLIK eşleme kodu üretme. İPTALİ İÇERMEZ — o `bbd_canteen_devices.devices`tedir. |
| `bbd_canteen_devices.view` | `bbd_canteen_devices` | loaded |  | `admin`, `bbd_staff` | Kiosk listesini, eşleme durumunu ve yerel işlem izini görüntüleme. |
| `bbd_canteen_products.deactivate` | `bbd_canteen_products` | loaded | evet | `admin`, `bbd_staff` | Ürünü pasifleştirme — kasada satışa çıkmaz. Veri silinmez. |
| `bbd_canteen_products.manage` | `bbd_canteen_products` | loaded |  | `admin`, `bbd_staff`, `org_staff`, `accountant` | Ürün ekleme, düzenleme, görsel yükleme ve stok işlemleri. |
| `bbd_canteen_products.view` | `bbd_canteen_products` | loaded |  | `admin`, `bbd_staff`, `org_staff`, `accountant` | Ürün listesini ve değişiklik günlüğünü görüntüleme. |
| `bbd_canteen_reports.export` | `bbd_canteen_reports` | loaded |  | `admin`, `bbd_staff`, `accountant` | Raporu PDF/CSV olarak dışa aktarma. |
| `bbd_canteen_reports.print` | `bbd_canteen_reports` | loaded |  | `admin`, `bbd_staff` | Raporu yazıcıya gönderme. |
| `bbd_canteen_reports.view` | `bbd_canteen_reports` | loaded |  | `admin`, `bbd_staff`, `org_staff`, `accountant` | Raporları görüntüleme. |
| `bbd_class_schedule.view` | `bbd_class_schedule` | disabled |  | `admin`, `bbd_staff`, `org_staff`, `accountant` | Ders Takvimi ekranını görüntüleme. |
| `bbd_lunch.manage` | `bbd_lunch` | loaded |  | `admin`, `bbd_staff`, `org_staff`, `accountant` | Toplu yemek kaydı işleme, sabit liste ve tatil günlerini düzenleme. |
| `bbd_lunch.reverse` | `bbd_lunch` | loaded | evet | `admin`, `bbd_staff` | İşlenmiş yemek kaydını geri alma (kantinde ters kayıt oluşturur). |
| `bbd_lunch.view` | `bbd_lunch` | loaded |  | `admin`, `bbd_staff`, `org_staff`, `accountant` | Öğle Yemeği ekranını ve geçmiş girdileri görüntüleme. |
| `bbd_payment_request.collect` | `bbd_payment_request` | loaded | evet | `admin`, `bbd_staff`, `accountant` | Elden alınan nakit tahsilatı cari deftere işleme. |
| `bbd_payment_request.manage_templates` | `bbd_payment_request` | loaded |  | `admin` | Ödeme SMS şablonlarını düzenleme. |
| `bbd_payment_request.send` | `bbd_payment_request` | loaded | evet | `admin`, `bbd_staff` | Veliye ödeme talebi gönderme. Gerçek SMS gider, geri alınamaz. |
| `bbd_payment_request.view` | `bbd_payment_request` | loaded |  | `admin`, `bbd_staff`, `org_staff`, `accountant` | Ödeme taleplerini ve tahsilat geçmişini görüntüleme. |
| `bbd_sms.manage_settings` | `bbd_sms` | loaded |  | `admin` | Netgsm kimlik bilgilerini ve hazır mesajları düzenleme. |
| `bbd_sms.send` | `bbd_sms` | loaded | evet | `admin`, `bbd_staff` | Velilere SMS gönderme. Gerçek para harcar ve geri alınamaz. |
| `bbd_sms.view` | `bbd_sms` | loaded |  | `admin`, `bbd_staff`, `org_staff`, `accountant` | SMS ekranını, gönderim geçmişini ve kuyruğu görüntüleme. |
| `bbd_students.access_code` | `bbd_students` | loaded |  | `admin`, `bbd_staff`, `org_staff` | Öğrencinin 6 haneli kantin giriş kodunu görüntüleme (liste/PDF) ve sıfırlama. |
| `bbd_students.canteen_controls` | `bbd_students` | loaded |  | `admin`, `bbd_staff`, `org_staff` | Veli kantin kontrollerini (limitler, engeller, yasaklı ürünler) düzenleme. |
| `bbd_students.delete` | `bbd_students` | loaded | evet | `admin` | Pasif öğrenciyi kalıcı silme — tek tek ya da toplu (bakiye 0, açık talep ve kurum cari borcu yok; ad ya da sayı yazılarak onay ve PIN teyidi). Kurum carisindeki alacak engellemez. |
| `bbd_students.groups` | `bbd_students` | loaded |  | `admin`, `bbd_staff`, `org_staff` | Öğrenci gruplarını ve üyelikleri yönetme. |
| `bbd_students.groups_delete` | `bbd_students` | loaded | evet | `admin` | Arşivdeki sınıfı kalıcı silme (sınıfın adı yazılarak, gerekçe ve PIN teyidiyle; geri alınamaz). |
| `bbd_students.lifecycle` | `bbd_students` | loaded |  | `admin`, `bbd_staff`, `org_staff` | Öğrenciyi pasifleştirme (gerekçe, ileri tarih, toplu) ve yeniden etkinleştirme. |
| `bbd_students.manage` | `bbd_students` | loaded |  | `admin`, `bbd_staff`, `org_staff`, `accountant` | Öğrenci ekleme ve bilgilerini düzenleme (kantine yazar). |
| `bbd_students.qr` | `bbd_students` | loaded |  | `admin`, `bbd_staff`, `org_staff`, `accountant` | Öğrencinin kantin kart QR'ını ve toplu kart PDF'ini üretme. |
| `bbd_students.view` | `bbd_students` | loaded |  | `admin`, `bbd_staff`, `org_staff`, `accountant` | Öğrenci listesini ve bilgilerini görüntüleme. |
| `bbd_tablet.grant` | `bbd_tablet` | loaded |  | `admin`, `bbd_staff`, `org_staff` | Öğrenciye uygulama bazında günlük ek süre verme ve geri alma; öğrenciyi belirli bir zamana kadar tabletten kısıtlama; hatalı şifreyle kilitlenen tablet girişini açma. |
| `bbd_tablet.manage` | `bbd_tablet` | loaded |  | `admin`, `bbd_staff` | Tablet, profil ve uygulama kurallarını düzenleme; tablet kilidi, duyuru, komut, oturum kapatma; APK yükleme ve dağıtma, indirme sırasını öne alma; tablet, profil, öğrenci tablet verisini silme ve dağıtımı bitirme (PIN ile). |
| `bbd_tablet.view` | `bbd_tablet` | loaded |  | `admin`, `bbd_staff`, `org_staff` | Tabletleri, profilleri, oturumları, komutları, öğrenci kullanımını, ek süreleri ve kısıtlamaları görüntüleme. |
| `bell.delete` | `bell` | loaded | evet | `admin` | Grubu, yüklenmiş zil sesini ve kullanılmayan anonsları KALICI silme. Geri alınamaz; PIN teyidi ister. |
| `bell.manage` | `bell` | loaded |  | `admin`, `org_staff` | Zil saatlerini, grupları, sesleri ve anons metinlerini düzenleme. |
| `bell.ring_now` | `bell` | loaded |  | `admin`, `org_staff`, `bbd_staff` | Elle zil çalma ve grup çağırma. |
| `bell.view` | `bell` | loaded |  | `admin`, `org_staff`, `bbd_staff` | Zil saatlerini, grupları ve çalma günlüğünü görüntüleme. |
| `bld_cms.delete` | `bld_cms` | disabled |  | `admin` | YIKICI: hizmet sayfasını ve yazıyı GERÇEKTEN silme. Sözleşme yumuşak silme sunmuyor (cms.md — "Gerçek silme"), yani kayıt geri gelmez ve o adrese verilmiş bütün bağlantılar kırılır. Günlük ihtiyaç olan "sitede görünmesin" için `is_published = false` zaten var ve `bld_cms.manage` ile yapılır; bu anahtar yalnız kaydın tümden gitmesi içindir. Yerel düzenleme geçmişi silinen kaydın son hâlini saklamaya devam eder ama o bir yedek değil, bir denetim izidir — geri yükleyen bir uç YOKTUR. |
| `bld_cms.manage` | `bld_cms` | disabled |  | `admin`, `bld_staff` | İçerik anahtarlarını yazma, hizmet ve yazı açma/güncelleme, yayına alma ve yayından çıkarma, siteyi yeniden çizdirme. |
| `bld_cms.view` | `bld_cms` | disabled |  | `admin`, `bld_staff` | Site metinlerini, hizmetleri, yazıları ve yerel düzenleme geçmişini görüntüleme. |
| `bld_customers.disable` | `bld_customers` | disabled |  | `admin`, `bld_staff` | YIKICI: müşteri hesabını kapatma (`customers.status = false`). Kapalı hesap giriş yapamaz ve sipariş veremez; aktif aboneliği olan bir hesapta abonelik üretimi DURMAZ (kural hesaba değil aboneliğe bağlıdır) ve bu yüzden yanıttaki uyarı yöneticiye gösterilir. Hesap kapatmak VERİ SİLMEZ; silme ucu yoktur. |
| `bld_customers.manage` | `bld_customers` | disabled |  | `admin`, `bld_staff` | İletişim bilgilerini (ad, soyad, telefon) ve serbest metin kurum etiketlerini (kurum adı, vergi dairesi, vergi/TC numarası, yetkili kişi, kurum telefonu) düzenleme; kapatılmış bir hesabı yeniden açma. E-POSTA VE PAROLA BU İZİNLE DE YAZILAMAZ — sözleşmede yazma yolu yok. |
| `bld_customers.view` | `bld_customers` | disabled |  | `admin`, `bld_staff` | Müşteri arama, müşteri kartı, adres defteri, sipariş geçmişi, abonelik ve SMS gönderim kaydını görüntüleme. BU İZİN BİR OKUMA İZNİNDEN FAZLASIDIR: taşıyan kişi bütün müşteri telefon ve e-posta defterine erişir ve her erişimi hem BLD'de hem yerelde denetim izine yazılır. |
| `bld_dashboard.manage` | `bld_dashboard` | disabled |  | `admin`, `bld_staff` | Bu ekranın görüntüleme tercihini kaydetme (yoklama aralığı, işletme seçimi, akış satır sayısı). BLD'ye hiçbir şey göndermez. |
| `bld_dashboard.view` | `bld_dashboard` | disabled |  | `admin`, `bld_staff` | Açılış özetini görüntüleme: bugünün satış tablosu, bekleyen işler listesi ve canlı sipariş akışı. |
| `bld_invoices.manage` | `bld_invoices` | disabled |  | `admin`, `bld_staff` | Sipariş ya da dönem belgesi kesme (kuru prova dâhil). Kesilen belge DÜZENLENEMEZ ve SİLİNEMEZ. |
| `bld_invoices.view` | `bld_invoices` | disabled |  | `admin`, `bld_staff` | Belge listesini, tek belgenin donmuş içeriğini, yerel arşivi ve denetim izini görüntüleme; A4 belge üretme, önizleme ve yazdırma. |
| `bld_invoices.void` | `bld_invoices` | disabled |  | `admin`, `bld_staff` | YIKICI: geçerli bir belgeyi iptal etme. Müşterinin elindeki kâğıt geçersiz olur, numara seride ölü kalır (boşluk bırakılmaz, geri kullanılmaz) ve işlem geri alınamaz — düzeltme, iptalden sonra kesilen YENİ bir belgedir. |
| `bld_kds.devices` | `bld_kds` | disabled |  | `admin`, `bld_staff` | YIKICI cihaz işlemleri: cihaz iptali ve `restart` · `clear_failed` · `clear_queue` · `update` · `unpair` komutları. Bu yetkiyi taşıyan kişi mutfağı sipariş göremez hâle getirebilir — `restart` ekranı kapatır, `clear_failed` basılamamış fişleri kuyruktan düşürür ve o fişler bir daha basılmaz, `clear_queue` üstelik bekleyenleri de düşürür, `update` kurulum boyunca ekranı alır, `unpair` cihaz token'ını siler ve kasa sahada elle eşleştirilene kadar hiç sipariş göremez, iptal edilen kasa bir daha bağlanamaz. BLD tarafında da ayrı yetki kutusu var (`Veykemtu.KitchenDevices`); iki tarafın ayrımı aynı tutulur. |
| `bld_kds.manage` | `bld_kds` | disabled |  | `admin`, `bld_staff` | Cihaz açma ve adlandırma, eşleme kodu üretme, zararsız komutlar (test fişi, yeniden bas, alarmı sustur), sipariş revizyonu ve durum değişikliği. AYAR YAZMAYI İÇERMEZ — o `bld_kds.settings`tedir. |
| `bld_kds.settings` | `bld_kds` | disabled |  | `admin` | KASA AYARLARI: yönetilen 24 ayarın kasaya yazılması (çalışma, yazıcı, ses ve kilit politikası). Ayrı durur çünkü mutfağın günlük işi değil, donanımın biçimlendirilmesidir: yanlış bir `printer_device_path` fiş basımını tümüyle durdurur, `poll_seconds` kasayı oran sınırına sokar ve bir kilit anahtarını `false` yapmak kasadaki düğmeyi öldürür — hepsinin düzeltmesi yalnız merkezden gelebilir. Sipariş durumunu değiştirmek (`bld_kds.manage`) bunların hiçbirini yapmaz. |
| `bld_kds.view` | `bld_kds` | disabled |  | `admin`, `bld_staff` | Cihazları, ayarlarını, komut geçmişini, fiş kaydını ve siparişleri görüntüleme. |
| `bld_manual_order.manage` | `bld_manual_order` | disabled |  | `admin`, `bld_staff` | Sipariş açma. Sipariş `onaylandi` doğar ve servis günü bugünse ANINDA mutfak ekranına düşer; ileri tarihliyse o günün kesim anında düşer. Kayıtlı müşterisi olmayan arayan için telefonla yeni müşteri kaydı da bu yetkiyle açılır. |
| `bld_manual_order.price_override` | `bld_manual_order` | disabled |  | `admin`, `bld_staff` | Müşteriye özel SEPET fiyatı uygulama: siparişin toplamı katalog yerine personelin telefonda anlaştığı tutar olur. Tutar sepetin tamamı içindir, kalem başına değil; kalemler siparişe fiyatsız yazılır ve teslimat ücreti bu tutara DÂHİL sayılır (ayrıca eklenmez). Anlaşma girilen her sipariş BLD denetim izinde tutarıyla birlikte durur. |
| `bld_manual_order.view` | `bld_manual_order` | disabled |  | `admin`, `bld_staff` | Elle sipariş ekranını açma: müşteri arama, ürün kataloğu, servis gününün kesim saati ve stok durumu. Müşteri okumaları KVKK gereği BLD tarafında `actor` ile denetlenir. |
| `bld_menu.manage` | `bld_menu` | disabled |  | `admin`, `bld_staff` | Gün kurma, gün başlığı/fiyatı/notu/kesim saati düzenleme, kalem ekleme ve güncelleme, yayınlama ve taslağa çekme, porsiyon tavanı yazma, günü başka güne kopyalama. |
| `bld_menu.remove` | `bld_menu` | disabled |  | `admin`, `bld_staff` | GERİ ALINAMAZ silme: menü gününü (kalemleriyle birlikte) ve tek kalemi silme. Sunucu iki kapı daha tutuyor — yayınlanmış gün ve siparişi olan gün silinemez, bugünün siparişlerinde kullanılmış kalem silinemez — ama taslak bir haftalık menü tek tıkla yok edilebilir ve geri getirmenin yolu yoktur. Yayından çekmek bu anahtara girmez: o geri alınabilir. |
| `bld_menu.view` | `bld_menu` | disabled |  | `admin`, `bld_staff` | Menü takvimini, gün ayrıntısını, kalemleri ve stok sayaçlarını görüntüleme. |
| `bld_notifications.manage` | `bld_notifications` | disabled |  | `admin`, `bld_staff` | Duyuru taslağı oluşturma ve duyuru içeriğini düzenleme. |
| `bld_notifications.publish` | `bld_notifications` | disabled |  | `admin`, `bld_staff` | DIŞA DÖNÜK: duyuruyu yayına alma ve arşivleme. Bu yetkiyi taşıyan kişi bütün müşterilerin uygulamasında açılan metni değiştirir; yayının geri alma ucu yoktur (yayından kaldırmanın yolu bitiş anını geçmişe çekmek ya da arşivlemektir) ve arşivlenen duyuru anında görünmez olur. |
| `bld_notifications.view` | `bld_notifications` | disabled |  | `admin`, `bld_staff` | Duyuruları, görülme istatistiğini ve yerel yazma izini görüntüleme. |
| `bld_orders.cancel` | `bld_orders` | disabled |  | `admin`, `bld_staff` | YIKICI: sipariş iptali. Ödenmiş siparişte iade kaydı üretir, müşteriye iptal SMS'i gönderir ve porsiyonları gün toplamı ile ürün tavanından düşürür (o kadar sipariş yeniden alınabilir hâle gelir). Teslim edilmiş sipariş iptal edilemez; abonelikten üretilmiş bir siparişin iptali aboneliği DURDURMAZ. |
| `bld_orders.manage` | `bld_orders` | disabled |  | `admin`, `bld_staff` | Sipariş revizyonu yazma (tam kalem listesi) ve durum ilerletme. İPTAL BUNA DÂHİL DEĞİLDİR. |
| `bld_orders.view` | `bld_orders` | disabled |  | `admin`, `bld_staff` | Sipariş listesini, ayrıntısını, revizyon geçmişini, fatura künyesini ve bu ekranın yerel denetim izini görüntüleme; CSV dışa aktarım ve kendi görüntüleme tercihini kaydetme. |
| `bld_products.manage` | `bld_products` | disabled |  | `admin`, `bld_staff` | Ürün açma ve düzenleme (ad, açıklama, fiyat, en az adet, sıra, kategori), ürünü yeniden satışa açma, görsel yükleme ve kaldırma, gün bazlı tükendi işareti koyma ve kaldırma, kategori açma ve düzenleme. |
| `bld_products.retire` | `bld_products` | disabled |  | `admin`, `bld_staff` | Ürünü satıştan kaldırma (`menu_status = 0`). Satır SİLİNMEZ — gerçek silme geçmiş siparişlerin ürün bağını koparır ve "bu sipariş neydi" sorusunu cevapsız bırakırdı. Yine de ürün siteden ve sipariş yolundan düşer; yayınlanmış bir günlük menüde kullanılıyorsa sunucu `409` ile engeller ve yönetici önce menüden çıkarmak zorundadır. |
| `bld_products.view` | `bld_products` | disabled |  | `admin`, `bld_staff` | Ürün kataloğunu, kategori ağacını ve yerel deneme izini görüntüleme. |
| `bld_sales_settings.manage` | `bld_sales_settings` | disabled |  | `admin`, `bld_staff` | Satış KURALLARINI yazma: sabah kesim saati, ileri gün sınırı, minimum sepet tutarı, teslimat ücreti, ödeme yöntemleri, yoğunluk anahtarı ve müşteriye görünen metni, hazırlık / teslimat / yoğunluk dakikaları, günün menüsü rejimi, otomatik fatura belgesi ve günün hızlı stok tavanları. Ekran tercihi de burada, çünkü tercih tablosu kullanıcı başına değil kurulum başınadır. |
| `bld_sales_settings.ordering` | `bld_sales_settings` | disabled |  | `admin`, `bld_staff` | SATIŞ KANALININ AÇIK/KAPALI olması: satışı durdurma ve açma, kapalı gün ekleme ve kaldırma. `manage` satış AÇIKKEN geçerli kuralları değiştirir ve yanlış girilen bir kesim saati bir dakikada düzeltilir; buradaki yetkinin hatası GERİ ALINAMAZ. Unutulan bir durdurma o günün bütün siparişlerini kaybettirir, yanlış eklenen bir kapalı gün abonelik üretimini atlatır (`SubscriptionGenerateCommand` o günü hiç işlemez) ve kaldırılan bir tatil mutfağı resmî tatilde üretime sokar. Kaybedilen gün geri gelmez. `busy` anahtarı BİLEREK bunun dışındadır: satışı kesmez, yalnız müşteriye "hazırlanması uzun sürebilir" uyarısı gösterir. |
| `bld_sales_settings.view` | `bld_sales_settings` | disabled |  | `admin`, `bld_staff` | Satış ayarlarını, kapalı günleri, günün stok tavanlarını ve bu ekranın yerel denetim izini görüntüleme. |
| `bld_sms.announce` | `bld_sms` | disabled |  | `admin` | TOPLU DUYURUYU GÖNDERME. Bu yetkiyi taşıyan kişi tek istekle yüzlerce müşteriye SMS yollar; gönderilen mesaj geri alınamaz, her segment faturalanır ve yanlış metin spam şikâyeti ile numara kaybı demektir. Ayrıca `all_customers` kitlesi panelde ikinci bir onay ister. |
| `bld_sms.manage` | `bld_sms` | disabled |  | `admin`, `bld_staff` | Şablon metnini yazma, bir bildirimi açma/kapatma, tek numaraya `[DENEME]` SMS'i ve duyuru TASLAĞINI yazma. Taslak yazmak göndermek değildir; gönderim ayrı anahtardadır. |
| `bld_sms.view` | `bld_sms` | disabled |  | `admin`, `bld_staff` | Şablonları, tetikleyici durumlarını, gönderim kaydını ve duyuru taslağını görüntüleme; metin ölçümü ve sunucu önizlemesi (hiçbir SMS göndermez). |
| `bld_status_monitor.manage` | `bld_status_monitor` | disabled |  | `admin`, `bld_staff` | Sunucudaki hata olayını çözüldü işaretleme, düzeltme defteri kaydı yazma/pasifleştirme ve defterdeki bir düzeltme komutunu kasaya gönderme. Komut `bld.api` geçidinden gider; ham kabuk erişimi YOKTUR. |
| `bld_status_monitor.view` | `bld_status_monitor` | disabled |  | `admin`, `bld_staff` | Sağlık kutularını, kasa sağlık özetini, sunucudaki hata olaylarını, bu ekranın YEREL araştırma geçmişini, düzeltme defterini ve yerel yazma izini görüntüleme; kendi görüntüleme tercihini kaydetme. |
| `bld_subscriptions.cancel` | `bld_subscriptions` | disabled | evet | `admin` | YIKICI: abonelik iptali ve dönem borcunu TAHSİL EDİLDİ işaretleme. İptal geri alınamaz — iptal edilmiş abonelik aktifleştirilemez, ödemeyle geri açılamaz ve yeniden başlatmak yeni abonelik açmak demektir. Tahsilat işaretleme GERÇEK PARA HAREKETİDİR ve geri alma ucu yoktur; yanlış işaretlenen bir tahsilat ancak yeni bir dönem kaydıyla düzeltilir. İkisi de izin yeterli olsa bile PIN teyidi ister. |
| `bld_subscriptions.manage` | `bld_subscriptions` | disabled |  | `admin`, `bld_staff` | Talep durumu ve iç notu yazma, talebi aboneliğe çevirme; abonelik açma/güncelleme, aktifleştirme, duraklatma, devam ettirme; tek-gün istisnası; elle üretim ve siparişi KDS'e erken düşürme; sözleşme oluşturma/yeniden gönderme/iptal; dönem borcu AÇMA. ABONELİK İPTALİ VE TAHSİLAT İŞARETLEME BUNA DÂHİL DEĞİLDİR. |
| `bld_subscriptions.view` | `bld_subscriptions` | disabled |  | `admin`, `bld_staff` | Teklif taleplerini, abonelik listesini ve ayrıntısını, servis takvimini, üretim defterini, sözleşmeleri, dönem ödemelerini ve bu ekranın yerel denetim izini (fiyat geçmişi dâhil) görüntüleme; kendi görüntüleme tercihini kaydetme. |
| `mobile_gateway.access` | `mobile_gateway` | loaded |  | `admin`, `mobile_service` | Mobil geçidin bütün uçları: öğrenci dizini ve grupları, kantin okuma/yazma, tablet denetimi, anons, zil saatleri ve zil planı, OTP SMS aktarımı. Servis anahtarı içindir. |
| `mobile_gateway.events.manage` | `mobile_gateway` | loaded |  | `admin` | Ölü mektupları yeniden gönderme ve iletimi bir sıradan yeniden başlatma. |
| `mobile_gateway.events.view` | `mobile_gateway` | loaded |  | `admin`, `bbd_staff` | Akademi'ye olay iletiminin durumunu (gecikme, ölü mektup) görme. |
| `print.reprint` | `print` | loaded |  | `admin`, `org_staff`, `bld_staff`, `bbd_staff` | Üretilmiş bir çıktıyı yeniden yazıcıya gönderme. |
| `print.view` | `print` | loaded |  | `admin`, `org_staff`, `bld_staff`, `bbd_staff` | Üretilmiş çıktıların listesini, önizlemesini ve baskı geçmişini görüntüleme. |
| `store_ai.apply` | `store_ai` | disabled |  | `admin`, `bbd_staff` | Onaylanan AI önerilerini mağazaya uygulama. Katalog metinlerini değiştirir. |
| `store_ai.manage` | `store_ai` | disabled |  | `admin` | Aylık bütçe, sınır davranışı, açık araçlar ve denetim eşikleri; bulgu susturma. |
| `store_ai.run` | `store_ai` | disabled |  | `admin`, `bbd_staff` | AI aracı çalıştırma. PARA HARCAR: her çalışma jeton tüketir ve bütçeye işlenir. |
| `store_ai.view` | `store_ai` | disabled |  | `admin`, `bbd_staff` | Ekranı, katalog sağlığı bulgularını, çalışma geçmişini ve raporları görüntüleme. |
| `store_backups.delete` | `store_backups` | disabled |  | `admin` | Eski yedek dosyasını silme (yalnız N günden eski, gerekçeyle). |
| `store_backups.manage` | `store_backups` | disabled |  | `admin`, `bbd_staff` | Yeni yedek alma, sağlama toplamı doğrulama, yedeği yerel diske indirme. |
| `store_backups.restore` | `store_backups` | disabled |  | `admin` | Yedeği geri yükleme. Mevcut mağaza verisini değiştirir; yalnız yönetici. |
| `store_backups.view` | `store_backups` | disabled |  | `admin`, `bbd_staff` | Yedek envanterini, disk durumunu ve denetim izini görüntüleme, CSV alma. |
| `store_bundles.deactivate` | `store_bundles` | loaded |  | `admin` | Seti satıştan kaldırma. Silme yoktur: satılmış bir setin içeriği, geçmişi okumak için saklanır. |
| `store_bundles.manage` | `store_bundles` | loaded |  | `admin`, `bbd_staff` | Setin adını, fiyatını ve içindeki ürünleri değiştirme. |
| `store_bundles.view` | `store_bundles` | loaded |  | `admin`, `bbd_staff` | Setleri ve kâr hesabını görüntüleme, rapor ve Excel dosyası alma. |
| `store_cms.manage` | `store_cms` | disabled |  | `admin`, `bbd_staff` | Sayfa açma, içerik/SEO düzenleme, SSS sorularını yazma. |
| `store_cms.redirects` | `store_cms` | disabled |  | `admin` | 301/302 yönlendirme yazma ve silme. Yanlış kayıt vitrini döngüye sokar. |
| `store_cms.restore` | `store_cms` | disabled |  | `admin` | Sayfayı eski sürüme döndürme. Yürürlükteki metnin üstüne yazar. |
| `store_cms.sitemap` | `store_cms` | disabled |  | `admin` | Site haritası tanımlama ve ÜRETME. Üretim 1.419 ürünü dolaşan ağır iştir ve vitrinde yayınlanan XML'i yeniden yazar. |
| `store_cms.view` | `store_cms` | disabled |  | `admin`, `bbd_staff` | CMS ekranını görüntüleme, sürüm geçmişini okuma, rapor ve CSV alma. |
| `store_customers.anonymize` | `store_customers` | loaded |  | `admin` | KVKK anonimleştirme. GERİ ALINAMAZ; ad, e-posta ve telefon kalıcı silinir. |
| `store_customers.deactivate` | `store_customers` | loaded |  | `admin` | Müşteriyi pasifleştirme. Silme yoktur: siparişi olan müşteri silinmez. |
| `store_customers.gdpr` | `store_customers` | loaded |  | `admin` | KVKK veri paketi isteme. Paket kişisel veri taşır. |
| `store_customers.manage` | `store_customers` | loaded |  | `admin`, `bbd_staff` | Müşteri künyesi, grup ve not düzenleme; müşteri ayarlarını kaydetme. |
| `store_customers.reviews` | `store_customers` | loaded |  | `admin`, `bbd_staff` | Yorum moderasyonu (onayla/reddet/spam) ve mağaza yanıtı yazma. |
| `store_customers.view` | `store_customers` | loaded |  | `admin`, `bbd_staff` | Müşteri listesi, künye, yorumlar, rapor ve CSV. |
| `store_dashboard.maintenance` | `store_dashboard` | loaded |  | `admin` | Bakım modunu açma/kapatma. Vitrini kapatır; gerekçe zorunludur. |
| `store_dashboard.manage` | `store_dashboard` | loaded |  | `admin`, `bbd_staff` | Mağaza kimliği, SEO, çalışma kanalı ve yerelleştirme ayarlarını düzenleme. |
| `store_dashboard.view` | `store_dashboard` | loaded |  | `admin`, `bbd_staff` | Kontrol Panelini görüntüleme, pano raporu ve CSV alma. |
| `store_home_media.manage` | `store_home_media` | disabled |  | `admin`, `bbd_staff` | Ana ekrandaki görselleri değiştirme: yeni görsel yükleme, sırayı belirleme ve tıklanınca gidilecek adresi seçme. Yazma anında müşteri de görür. |
| `store_home_media.view` | `store_home_media` | disabled |  | `admin`, `bbd_staff` | Ana ekran görsellerini görüntüleme ve değişiklik geçmişini okuma. |
| `store_invoices.issue` | `store_invoices` | loaded |  | `admin`, `bbd_staff` | Siparişe fatura kesme. Kesilen fatura silinemez; iptali iade faturasıdır. |
| `store_invoices.legal_no` | `store_invoices` | loaded |  | `admin`, `bbd_staff`, `accountant` | Dış sistemde kesilen yasal faturanın numarasını mağaza faturasıyla eşleme. |
| `store_invoices.manage` | `store_invoices` | loaded |  | `admin`, `bbd_staff` | Seri tanımlama, fatura durumu değiştirme, müşteriye kopya e-postalama. |
| `store_invoices.view` | `store_invoices` | loaded |  | `admin`, `bbd_staff`, `accountant` | Fatura ve irsaliye listesini görüntüleme, icmal ve CSV alma. |
| `store_notifications.broadcast` | `store_notifications` | loaded |  | `admin` | Toplu bildirim. Canlıda 1.800+ kişiye gider ve geri alınamaz. |
| `store_notifications.manage` | `store_notifications` | loaded |  | `admin`, `bbd_staff` | Şablon, olay kuralı, sessiz saat, günlük limit ve mobil uygulama ayarları. |
| `store_notifications.send` | `store_notifications` | loaded |  | `admin`, `bbd_staff` | Tek gönderim: kendine test ve başarısız gönderimi yineleme. Para harcar. |
| `store_notifications.view` | `store_notifications` | loaded |  | `admin`, `bbd_staff` | Gönderim geçmişi, şablon, kural, kanal ve abonelikleri görüntüleme; rapor alma. |
| `store_orders.cancel` | `store_orders` | loaded |  | `admin`, `bbd_staff` | Sipariş iptali. Geri alınamaz; gerekçe ve süre penceresi denetlenir. |
| `store_orders.manage` | `store_orders` | loaded |  | `admin`, `bbd_staff` | "Fatura kesme, gönderi kaydı açma, not ekleme, toplu işlem, ekran ayarları ve sipariş durumunu ELLE değiştirme. Durum ucundan İPTAL, KAPATMA ve SAHTE İŞARETİ yazılamaz: ilki ayrı anahtar ve süre penceresi ister (`store_orders.cancel`), diğer ikisi siparişi kalıcı olarak kilitler." |
| `store_orders.view` | `store_orders` | loaded |  | `admin`, `bbd_staff` | Sipariş listesini, detayını, raporlarını ve CSV'sini görüntüleme. |
| `store_payment_gateway.cancel` | `store_payment_gateway` | disabled |  | `admin` | Ödeme bağlantısını iptal etme. Kayıt silinmez, bağlantı ölür. |
| `store_payment_gateway.collect` | `store_payment_gateway` | disabled |  | `admin`, `bbd_staff` | Ödeme bağlantısı üretme ve SMS gönderme. Para hareketi başlatır. |
| `store_payment_gateway.manage` | `store_payment_gateway` | disabled |  | `admin`, `bbd_staff` | Tahsilat talebi açma, SMS şablonunu ve Netgsm ayarlarını düzenleme. |
| `store_payment_gateway.settle` | `store_payment_gateway` | disabled |  | `admin` | Elden kapatma: havale/nakit beyanı. Mağazaya ödeme kaydı yazar. |
| `store_payment_gateway.view` | `store_payment_gateway` | disabled |  | `admin`, `bbd_staff` | Tahsilat ekranını görüntüleme, liste, rapor ve CSV alma. |
| `store_products.attributes` | `store_products` | loaded |  | `admin` | Nitelik ve aile düzenleme, seçenek yönetimi, nitelik pasifleştirme. |
| `store_products.attributes_delete` | `store_products` | loaded |  | `admin` | Nitelik/seçenek silme. Değeri bütün ürünlerden düşer; geri alınamaz. |
| `store_products.bulk` | `store_products` | loaded |  | `admin`, `bbd_staff` | Toplu fiyat/stok/kategori/durum uygulaması (önizleme zorunlu). |
| `store_products.deactivate` | `store_products` | loaded |  | `admin` | Ürünü pasifleştirme (vitrinden kaldırma). Geri alınabilir; kayıt silinmez. |
| `store_products.delete` | `store_products` | loaded |  | `admin` | Ürünü katalogdan silme. GERİ ALINAMAZ; gerekçe ve önizleme zorunlu. |
| `store_products.manage` | `store_products` | loaded |  | `admin`, `bbd_staff` | Ürün künyesi, fiyat, stok, görsel ve kategori düzenleme. |
| `store_products.rename_sku` | `store_products` | loaded |  | `admin` | SKU değiştirme. product_flat yeniden yazılır, eski vitrin bağlantıları kırılır. |
| `store_products.view` | `store_products` | loaded |  | `admin`, `bbd_staff` | Ürünler ekranını görüntüleme, rapor ve CSV alma. |
| `store_promotions.activate` | `store_promotions` | disabled |  | `admin` | Kuralı yayına alma / durdurma. Yayındaki kural her siparişte para indirir; bu yüzden düzenleme izninden ayrı tutulur. |
| `store_promotions.coupons` | `store_promotions` | disabled |  | `admin`, `bbd_staff` | Kupon üretme ve kullanılmamış kupon kodunu kaldırma. |
| `store_promotions.manage` | `store_promotions` | disabled |  | `admin`, `bbd_staff` | Kural künyesi, koşul, eylem, kısıt ve takvim düzenleme (taslak olarak). |
| `store_promotions.view` | `store_promotions` | disabled |  | `admin`, `bbd_staff` | Promosyon ekranını, kuponları ve kampanya performansını görüntüleme. |
| `store_refunds.approve` | `store_refunds` | loaded |  | `admin`, `bbd_staff` | İadeyi onaylayıp parayı geri verme (kredi notu; banka iadesi mağazada otomatik başlar). Para hareketidir. |
| `store_refunds.manage` | `store_refunds` | loaded |  | `admin`, `bbd_staff` | Talep durumu, sipariş notu ve müşteri bildirimi. |
| `store_refunds.ship_return` | `store_refunds` | loaded |  | `admin`, `bbd_staff` | Müşteriden geri alım için iade kargosu açma ve etiketini satın alma. Kargo ücreti mağazaya yansır. |
| `store_refunds.view` | `store_refunds` | loaded |  | `admin`, `bbd_staff`, `accountant` | İadeler ekranını görüntüleme, iade icmali ve CSV alma. |
| `store_reports.manage` | `store_reports` | disabled |  | `admin`, `bbd_staff` | Kaydedilmiş ve zamanlanmış rapor tanımlarını yönetme. |
| `store_reports.view` | `store_reports` | disabled |  | `admin`, `bbd_staff`, `accountant` | Raporları görüntüleme, PDF/CSV üretme ve yazdırma. |
| `store_requests.decide` | `store_requests` | disabled |  | `admin`, `bbd_staff` | Talebi onaylama/reddetme. Onay, seçili kalemleri İadeler ekranına devreder; para iadesi orada ayrı izinle yapılır. |
| `store_requests.manage` | `store_requests` | disabled |  | `admin`, `bbd_staff` | Yanıt yazma, iç not, durum/öncelik/atama, iade kalemi seçimi, toplu işlem. |
| `store_requests.purchase` | `store_requests` | disabled |  | `admin`, `bbd_staff` | İade kargo etiketini SATIN ALMA. PARA HARCAR; gerekçe en az 20 karakter. |
| `store_requests.view` | `store_requests` | disabled |  | `admin`, `bbd_staff` | Talepler ekranını görüntüleme, RMA formu ve SLA raporu alma. |
| `store_shipping.cancel` | `store_shipping` | loaded |  | `admin`, `bbd_staff` | Satın alınmış gönderiyi iptal etme. Ayrı anahtar: sonradan daraltılabilsin diye. |
| `store_shipping.integration` | `store_shipping` | loaded |  | `admin` | Geliver kurulum ayarları: API tokenı, kurulu/canlı/test bayrakları, gönderici adres, webhook kaydı. |
| `store_shipping.manage` | `store_shipping` | loaded |  | `admin`, `bbd_staff` | Gönderi taslağı açma, elle takip girişi (dışarıdan alınmış etiket), senkron, adres düzeltme, taşıyıcı sınama. |
| `store_shipping.purchase` | `store_shipping` | loaded |  | `admin`, `bbd_staff` | Taşıyıcıdan etiket SATIN ALMA ve iade etiketi açma. PARA HARCAR; gerekçe en az 20 karakter. |
| `store_shipping.rates` | `store_shipping` | loaded |  | `admin` | Desi→ücret matrisi, ücretsiz kargo eşiği ve bölge tanımlarını yazma. Vitrindeki kargo ücretini değiştirir. |
| `store_shipping.view` | `store_shipping` | loaded |  | `admin`, `bbd_staff` | Gönderi listesi, taşıyıcılar, performans ve rapor/etiket alma. |
| `store_tax.assign` | `store_tax` | disabled |  | `admin` | Ürünlerin vergi kategorisini toplu değiştirme. Kataloğa yazar: yanlış eşleme yanlış KDV'yle satış demektir. |
| `store_tax.manage` | `store_tax` | disabled |  | `admin`, `bbd_staff` | Vergi oranı ve vergi kategorisi ekleme/düzenleme. |
| `store_tax.view` | `store_tax` | disabled |  | `admin`, `bbd_staff`, `accountant` | Vergilendirme ekranını görüntüleme, KDV icmali alma, rapor ve CSV üretme. |
| `store_trial_club.enroll` | `store_trial_club` | disabled |  | `admin`, `bbd_staff` | Katılımcı ekleme ve kayıt iptali. Kayıt silinmez, iptal edilir. |
| `store_trial_club.manage` | `store_trial_club` | disabled |  | `admin`, `bbd_staff` | Deneme açma/düzenleme, kontenjan değiştirme ve ekran tercihleri. |
| `store_trial_club.notify` | `store_trial_club` | disabled |  | `admin` | Toplu bildirim gönderme. SMS PARA HARCAR. |
| `store_trial_club.publish` | `store_trial_club` | disabled |  | `admin` | Sonuç yükleme ve yayınlama. Yayınlanan sonuç katılımcıya görünür olur. |
| `store_trial_club.view` | `store_trial_club` | disabled |  | `admin`, `bbd_staff` | Deneme Kulübü ekranını görüntüleme, yoklama/karne PDF ve CSV alma. |
| `store_udit_logs.export` | `store_udit_logs` | loaded |  | `admin` | Denetim dökümü alma (PDF/CSV) ve yazdırma. Kaydı okumak ile kaydı binlerce satır hâlinde binadan çıkarmak aynı şey değildir: döküm personel adı ve IP taşır, ayrı anahtar ister ve iz bırakır. |
| `store_udit_logs.manage` | `store_udit_logs` | loaded |  | `admin` | Sözleşme gereği ilan edilir ve HİÇBİR uç noktaya bağlı DEĞİLDİR. Bu ekran salt okunurdur: denetim kaydı silinemez, düzenlenemez. Bir uç bu anahtara bağlanırsa ekranın kalıcı notu yalana döner. |
| `store_udit_logs.view` | `store_udit_logs` | loaded |  | `admin`, `bbd_staff`, `accountant` | Denetim kayıtlarını görüntüleme, süzme ve fark tablosunu okuma. |

<!-- izin-katalogu:bitir -->
