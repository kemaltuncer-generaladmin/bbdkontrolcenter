# İzin Kataloğu ve Rol Matrisi (SABİT)

Bağlayıcıdır. Gerekçe: [adr/0007-kimlik-ve-yetkilendirme.md](adr/0007-kimlik-ve-yetkilendirme.md)

## Kavramlar

- **İzin (permission):** yapılabilecek tek bir iş. `database.query` gibi.
- **Kapsam (scope):** iznin hangi alanda geçerli olduğu. `bbd`, `bld`, `org`,
  hepsi için `*`. Yazımı `izin:kapsam` → `database.query:bld`.
- **Rol:** izin kümesi. Ön tanımlı dört rol vardır, yenisi tanımlanabilir.
- **Kullanıcı:** **birden fazla rol** taşıyabilir. Etkin izinleri rollerinin
  **birleşimidir.**

Kodda rol adı sorulmaz, izin sorulur. `if role == "admin"` yasaktır.

## Roller

| id | Ad | Kapsam | Özet |
|---|---|---|---|
| `admin` | Admin | `*` | Tam yetki. Kullanıcı, rol, ayar, sır ve yıkıcı işlemler. |
| `bld_staff` | BLD Personeli | `bld` | BLD sunucuları ve Laravel tabanlı BLD veritabanı. |
| `bbd_staff` | BBD Personeli | `bbd` | BBD sunucuları ve Bagisto çekirdekli BBD veritabanı. |
| `org_staff` | Kurum Personeli | `org` | Zil, baskı, rehber. Sunucu ve veritabanına erişimi yok. |
| `accountant` | Mali Müşavir | `bbd` `bld` | Mali ekranlar: fatura, vergilendirme, cari hesap, raporlar. Kullanıcı, sır ve sunucu yönetimi yok. |

Bir kullanıcıya hem `bld_staff` hem `bbd_staff` atanırsa her iki kapsamı da alır.

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
| `users.set_pin` | hayır | PIN atar ve sıfırlar |
| `roles.view` | hayır | Rolleri ve izinlerini görür |
| `roles.manage` | hayır | Rol tanımlar, izin atar, kullanıcıya rol verir |

### Sistem
| İzin | Kapsamlı | Ne yapar |
|---|---|---|
| `settings.view` | hayır | Uygulama ayarlarını görür |
| `settings.manage` | hayır | Ayarları değiştirir, modül açar/kapatır |
| `audit.view` | hayır | Denetim izini okur |
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

---

## Modül izinleri

Modüller kendi izinlerini `module.yaml` içinde ilan eder. Aşağıdakiler mevcut
iskeletlerin ilan ettikleridir.

> **Geliştirme kuralı (geçici).** BBD, BBD Store ve BLD gruplarındaki ekran
> modülleri şu an ikişer izin ilan ediyor — `<id>.view` ve `<id>.manage` — ve
> ikisi de **beş rolün hepsine** düşüyor. Ekranlar sırayla kodlanacağı için
> daraltma, o modülün işi başlarken yapılır. Tek tek listelenmezler; kaynak
> her zaman modülün kendi `module.yaml` dosyasıdır. Aşağıdaki tablo ve
> matrisler, daraltması **bilinçli olarak yapılmış** modülleri gösterir.

| Modül | İzin | Ne yapar |
|---|---|---|
| `bell` | `bell.view` | Haftalık zil saatlerini, grupları ve çalma günlüğünü görür |
| `bell` | `bell.manage` | Saatleri, grupları, sesleri ve anons metinlerini düzenler |
| `bell` | `bell.ring_now` | Elle zil çalar, grup çağırır |
| `bbd_class_schedule` | `bbd_class_schedule.view` | Zil saatlerinin salt okunur görünümü |
| `print` | `print.view` | Yazıcıları ve kuyruğu görür |
| `print` | `print.submit` | Baskı işi gönderir |
| `print` | `print.manage` | Kuyruğu yönetir, iş iptal eder, yazıcı ayarlar |
| `antivirus` | `antivirus.view` | Tarama geçmişini, karantinayı, imza durumunu görür |
| `antivirus` | `antivirus.scan` | Tarama başlatır ve durdurur |
| `antivirus` | `antivirus.manage` | Tarama takvimi, hariç tutulan yollar, imza güncelleme |
| `antivirus` | `antivirus.quarantine` | **Yıkıcı.** Dosyayı karantinaya alır / geri yükler |
| `antivirus` | `antivirus.delete_threat` | **Yıkıcı.** Karantinadaki dosyayı kalıcı siler |

---

## Rol → izin matrisi

✓ verilir · ✗ verilmez · Kapsamlı izinler rolün kapsamıyla verilir.

| İzin | Admin | BLD Personeli | BBD Personeli | Kurum Personeli | Mali Müşavir |
|---|:---:|:---:|:---:|:---:|:---:|
| `users.view` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `users.manage` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `users.set_pin` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `roles.view` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `roles.manage` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `settings.view` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `settings.manage` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `audit.view` | ✓ | ✗ | ✗ | ✗ | ✗ |
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
| `bell.view` | ✓ | ✗ | ✓ | ✓ | ✗ |
| `bell.manage` | ✓ | ✗ | ✗ | ✓ | ✗ |
| `bell.ring_now` | ✓ | ✗ | ✓ | ✓ | ✗ |
| `bbd_class_schedule.view` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `print.view` | ✓ | ✓ | ✓ | ✓ | ✗ |
| `print.submit` | ✓ | ✓ | ✓ | ✓ | ✗ |
| `print.manage` | ✓ | ✗ | ✗ | ✓ | ✗ |
| `antivirus.view` | ✓ | ✓ | ✓ | ✗ | ✗ |
| `antivirus.scan` | ✓ | ✓ | ✓ | ✗ | ✗ |
| `antivirus.manage` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `antivirus.quarantine` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `antivirus.delete_threat` | ✓ | ✗ | ✗ | ✗ | ✗ |

Bilinçli kararlar:
- **`servers.manage` yalnızca Admin.** Personel sunucuya bağlanır ve iş yapar,
  envanteri ve kimlik eşlemesini değiştiremez.
- **`database.restore` yalnızca Admin.** Geri yükleme yıkıcıdır; yedek almak
  personelde, geri yüklemek yönetimdedir.
- **Kurum Personeli sunucu ve veritabanı görmez.** Zil, baskı ve rehber ile
  sınırlıdır.
- **Rehber herkeste okunur.** Uygulamanın ortak zeminidir.
- **Antivirüsü personel görür ve tarar, ama yönetemez.** Karantina ve kalıcı
  silme yıkıcıdır, yalnızca Admin'dedir. Kurum Personeli antivirüs görmez.

---

## Rol → ekran matrisi

Bir ekran, gerektirdiği izinlerden **en az birine** sahip kullanıcıya görünür.
Menüde gizlenmesi yetmez; backend de reddeder (K9 — çift kapı).

| Ekran | Gerektirdiği izin | Admin | BLD | BBD | Kurum | Mali |
|---|---|:---:|:---:|:---:|:---:|:---:|
| Giriş (PIN) | — | ✓ | ✓ | ✓ | ✓ | ✓ |
| Ana panel | — | ✓ | ✓ | ✓ | ✓ | ✓ |
| Kullanıcılar | `users.view` | ✓ | ✗ | ✗ | ✗ | ✗ |
| Roller ve İzinler | `roles.view` | ✓ | ✗ | ✗ | ✗ | ✗ |
| Ayarlar | `settings.view` | ✓ | ✗ | ✗ | ✗ | ✗ |
| Denetim İzi | `audit.view` | ✓ | ✗ | ✗ | ✗ | ✗ |
| Kimlik Kasası | `secrets.view` | ✓ | ✗ | ✗ | ✗ | ✗ |
| Sunucular | `servers.view` | ✓ | ✓ | ✓ | ✗ | ✗ |
| Uzak Terminal | `ssh.execute` | ✓ | ✓ | ✓ | ✗ | ✗ |
| Veritabanı | `database.view` | ✓ | ✓ | ✓ | ✗ | ✗ |
| Zil Sistemi | `bell.view` | ✓ | ✗ | ✓ | ✓ | ✗ |
| Ders Takvimi (salt okunur) | `bbd_class_schedule.view` | ✓ | ✓ | ✓ | ✓ | ✓ |
| Baskı Yönetimi | `print.view` | ✓ | ✓ | ✓ | ✓ | ✗ |
| Antivirüs | `antivirus.view` | ✓ | ✓ | ✓ | ✗ | ✗ |
| Rehber | `directory.view` | ✓ | ✓ | ✓ | ✓ | ✓ |
| Profilim | — | ✓ | ✓ | ✓ | ✓ | ✓ |

Kapsamlı ekranlarda (Sunucular, Veritabanı) **ekran açılır, içerik kapsamla
süzülür.** BLD Personeli yalnızca `bld` kapsamlı sunucu ve veritabanlarını
görür; iki rolü olan kişi ikisini birden görür.

---

## Uygulama kuralları

1. Yetki denetimi tek bir yerden geçer; her uç nokta gerektirdiği izni ilan
   eder. İlan etmeyen uç nokta reddedilir (varsayılan: kapalı).
2. Kapsamlı bir izin kapsam belirtilmeden sorulamaz.
3. Yıkıcı **çekirdek** işlemleri (`database.restore`, `users.manage` silme,
   `roles.manage`) izin yeterli olsa bile **PIN teyidi** ister. Bunlar seyrek
   ve kurumsal işlemlerdir.
   **İstisna — BBD Store:** `store_*` modüllerindeki yıkıcı ve para harcayan
   işlemler PIN yerine **gerekçeli onay** ister (ayrı izin anahtarı + zorunlu
   gerekçe + çift denetim kaydı + kuru prova). Gerekçe:
   [ADR 0012](adr/0012-magaza-yikici-islem-onayi.md). Bu yüzden `store_*`
   izinleri `destructive: true` bayrağı **taşımaz**.
4. Her yetki reddi ve her yıkıcı işlem denetim izine yazılır.
5. Yeni modül kendi izinlerini ilan eder ve `permissions[].default_roles` ile
   hangi ön tanımlı rollere düşeceğini önerir. Çekirdekte değişiklik olmaz.
6. Son admin pasifleştirilemez veya `admin` rolü elinden alınamaz.
