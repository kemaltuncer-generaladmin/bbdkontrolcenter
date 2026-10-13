# Öğrenci Tablet Yönetimi

Paylaşımlı okul tabletleri için profil, kullanım saatleri, cihaz, öğrenci oturumu, kullanım, ek süre, öğrenci kısıtlaması, uzaktan komutları ve uygulama dağıtımını (TabletKontrol'ün kendi güncellemesi dahil) tutar. Bağlayıcı sözleşme TabletKontrol deposundaki `docs/CONTROL_CENTER_INTEGRATION.md` (v2.1) dosyasıdır.

Öğrenci kimliği ve 6 haneli giriş kodu kantinde kalır. Tablet girişinde sunucu `canteen.api.lookup_student_by_code` çağırır; kantin cihaz token'ı tablete verilmez, öğrenci şifresi saklanmaz. Cihaz ve oturum token'ları yalnız SHA-256 özetiyle tutulur.

## Sunucuyu yormama

Kontrol Merkezi bbdstore ile aynı sunucuda koşar. Tablet tek bir periyodik çağrı yapar (`POST /devices/{id}/sync`); aralığı sunucu verir:

| Ayar | Varsayılan | Anlamı |
| --- | --- | --- |
| `sync_active_seconds` | 60 | Oturum açıkken sync aralığı |
| `sync_idle_seconds` | 300 | Boştayken sync aralığı |
| `usage_flush_seconds` | 300 | Kullanım paketi gönderme aralığı |
| `session_idle_minutes` | 30 | Bu süre sync gelmezse oturum düşer (en az 3 × etkin aralık) |
| `history_retention_days` | 180 | Bundan eski geçmiş günde en fazla bir kez silinir (7–3650) |

Değişmeyen veri gönderilmez: profil revizyonu, cihaz durumu sürümü ve ek süre sürümü aynıysa yanıtta `null` döner. Değişiklik yokken sync birkaç indeksli sorgu ve iki yazmadır. Bir yanıt en fazla 100 komut taşır ve bir istekten en fazla 100 komut sonucu okunur; fazlası sonraki sync'lerde gider, birikmiş komutlar tableti kilitlemez. Günlük kullanım `daily_usage` özet tablosundan okunur (paket kabulünde eklenir). Kantin öğrenci listesi 5 dakika, profil görünümü `(id, revizyon)` anahtarıyla bellekte tutulur; süresi dolan oturum taraması dakikada en fazla bir kez çalışır. Aynı uygulama listesi tekrar gönderilirse yazma yapılmaz. Giriş kilidi yalnız cihaz durumu yanıta girerken okunur; değişiklik yokken sync'e sorgu eklemez.

Geçmiş saklama: `history_retention_days` günden eski bitmiş oturumlar, kullanım paketleri ve satırları, günlük toplamlar, ek süreler, bitişi o kadar eski kısıtlamalar ve süresi bir haftadan önce dolmuş komutlar süreç başına günde en fazla bir kez silinir. Temizlik panelin `GET /overview` yolunda çalışır, tabletin sync'inde çalışmaz.

## Tablet API — `/api/tablet/v1`

| Yöntem ve yol | Bearer | İşlev |
| --- | --- | --- |
| `POST /devices/enroll` | Yok (eşleme kodu) | Cihazı eşle |
| `GET /profiles`, `GET /profiles/{id}` | Cihaz | Profil seçimi |
| `POST /devices/{id}/initial-profile` | Cihaz | İlk profili bir kez seç/oluştur |
| `PUT /devices/{id}/apps` | Cihaz | Uygulama envanteri |
| `GET /devices/{id}/releases/{releaseId}/files/{index}` | Cihaz + indirme yuvası | Dağıtılan paket dosyası (`Range`) |
| `POST /devices/{id}/sync` | Cihaz | Tek periyodik çağrı: profil, cihaz durumu, oturum, ek süre, komutlar, dağıtılan uygulamalar ve indirme yuvası |
| `POST /auth/login` | Cihaz | Öğrenci girişi |
| `POST /auth/logout` | Öğrenci oturumu | Çıkış (tekrarlanabilir) |
| `POST /usage/batch` | Öğrenci oturumu | İdempotent kullanım paketi |
| `POST /devices/{id}/unpair` | Cihaz | Tabletten eşlemeyi kaldır (tekrarlanabilir) |
| `GET /devices/{id}/policy`, `POST /devices/{id}/heartbeat` | Cihaz | Eski APK'lar için |

Makine tarafından okunan hatalar `{"detail": {"code", "message", ...}}` biçimindedir: `DEVICE_REVOKED`, `DEVICE_AUTH_INVALID`, `FORBIDDEN_DEVICE`, `INVALID_STUDENT_CODE`, `LOGIN_RATE_LIMITED` (+`retryAfterSeconds`), `SESSION_CONFLICT`, `STUDENT_BLOCKED` (+`until`), `OUTSIDE_USAGE_HOURS` (+`nextWindowStart`), `DEVICE_LOCKED`, `NO_PROFILE`, `CANTEEN_UNAVAILABLE`, `DOWNLOAD_NOT_GRANTED` (409), `RELEASE_GONE` (410).

Giriş kilidi: bir tablette 5 dakikada 5 hatalı şifre o tabletin girişini 5 dakika kilitler (`LOGIN_RATE_LIMITED`). Kilit oluşunca ve panelden kaldırılınca `deviceState.version` artar ve `deviceState.loginLockedUntil` (ofsetli ISO ya da `null`) taşır; kilidin kendiliğinden dolması sürümü artırmaz.

Eşleme kodu 8 karakterdir (`ABCDEFGHJKLMNPQRSTUVWXYZ23456789`), `ABCD-EFGH` biçiminde gösterilir; sunucu büyük harfe çevirir, tire ve boşlukları atar. İptal edilmiş tablete yeni kod verilince eski token kalıcı olarak geçersizleşir.

Oturum sonu nedenleri: `admin` (panelden kapatıldı ya da tablet kilitlendi), `expired`, `logout`, `revoked`, `blocked`, `outside_hours`. Panelden, kilitle veya kısıtlamayla kapanan oturumda kullanım paketi ve çıkış kabul edilmeye devam eder; çıkış ya da eşleme iptali sonrası paket reddedilir. `sessionId` göndermeyen sync, o tablette 2 dakikadan eski açık oturumu nedeni boş bırakarak kapatır (`expired` okunur); tabletin ardından gelen çıkışı gerçek nedeni yazar.

Kullanım paketi profil kuralına göre denetlenmez: oturum sırasında bir uygulama yasaklansa ya da başka profil atansa da ölçülmüş süre kabul edilir ve özete eklenir. Tablet yalnız profilin izinli uygulamalarını sayar. 400 yalnız bozuk paket içindir (oturum kimliği, zaman aralığı, saat dilimine uymayan gün, yinelenen satır, aralığı aşan toplam).

## Yönetim API — `/api/bbd_tablet`

İzinler: `bbd_tablet.view` görüntüleme, `bbd_tablet.manage` tablet/profil/komut/oturum ve silme, `bbd_tablet.grant` ek süre, öğrenci kısıtlaması ve giriş kilidini kaldırma. Eşleme iptali, tablet silme (tekli ve toplu `delete`), profil silme ve öğrenci verisi silme izin yeterli olsa bile gövdede `pin` ile PIN teyidi ister; yanlış PIN 403, eksik PIN 422 döner.

| Yöntem ve yol | İzin |
| --- | --- |
| `GET /overview`, `GET /students`, `GET /students/{id}/usage?localDate=`, `GET /students/{id}/blocks` | view |
| `GET /reports/daily?localDate=`, `GET /sessions?limit=&deviceId=&studentId=` | view |
| `GET /devices/{id}/apps`, `GET /devices/{id}/commands` | view |
| `POST /profiles`, `PUT /profiles/{id}`, `POST /profiles/{id}/duplicate`, `DELETE /profiles/{id}` | manage |
| `POST /devices`, `PATCH /devices/{id}`, `POST /devices/{id}/assign-profile`, `POST /devices/{id}/enrollment-code` | manage |
| `POST /devices/{id}/revoke`, `DELETE /devices/{id}`, `POST /devices/{id}/commands`, `DELETE /devices/{id}/commands/{commandId}`, `POST /devices/bulk` | manage (toplu `reset_login_lock` ayrıca grant) |
| `POST /sessions/{id}/end`, `DELETE /students/{id}/history` | manage |
| `POST /devices/{id}/login-lock/reset` | grant |
| `POST /students/{id}/apps/{package}/grants`, `POST /students/{id}/grants/{grantId}/revoke` | grant |
| `POST /students/{id}/block`, `POST /students/{id}/unblock` | grant |
| `GET /apps`, `GET /apps/{package}/devices` | view |
| `POST /apps/uploads`, `GET /apps/uploads/{id}`, `PUT /apps/uploads/{id}/chunk`, `POST /apps/uploads/{id}/finish` | manage |
| `POST /apps/releases/{id}/publish`, `DELETE /apps/releases/{id}`, `POST /apps/{package}/devices/{deviceId}/prioritize` | manage |
| `DELETE /apps/{package}` | manage + PIN |

Profilde uygulama üç kipten birindedir: yasak, sınırsız ya da günlük süreli (60–86400 sn). Adı boş bırakılan uygulama paket adıyla kaydedilir; profil adı kırpılır ve 2–100 karakter olmalıdır (`006_profile_names.sql` eski kayıtları düzeltir). `usageWindows` ISO hafta günleri (1 = Pazartesi) ve profil saat diliminde `HH:MM` başlangıç (dahil) – bitiş (hariç) taşır; boş liste her zaman açık demektir.

## Uygulama dağıtımı (v2.1)

Yönetici panelin **Uygulamalar** sekmesinden APK ya da bölünmüş paket (`.apks`, `.xapk`, `.apkm`; türü uzantıdan değil içerikten anlaşılır) yükler; eşli tabletler onu sunucunun belirlediği sırayla indirip Device Owner yetkisiyle sessizce kurar. TabletKontrol'ün kendi yeni sürümü (`com.veykemtu.tabletkontrol`) aynı yoldan gider (`kind: "self"`) ve sırada her zaman öndedir.

| Ayar | Varsayılan | Anlamı |
| --- | --- | --- |
| `download_slots` | 2 (1–20) | Aynı anda paket indirebilen tablet sayısı (indirme yuvası) |
| `download_lease_seconds` | 900 (300–3600) | Yuvanın süresi; tablet her `active` bildirimiyle uzatır, bildirim gelmezse yuva sıradakine geçer |
| `queue_stale_seconds` | 900 (300–7200) | Bu süredir sync yapmayan tablet sırada sayılmaz; kapalı tablet sırayı tıkamaz |
| `max_upload_mb` | 6144 | Yüklenebilecek en büyük dosya |
| `storage_dir` | `""` | Paket dosyalarının yeri; boşsa `$KM_DATA_DIR/bbd_tablet/apps` (sunucu imajında kalıcı disk `/data`), o da yoksa depo kökünde `data/bbd_tablet/apps`. İmajdaki `/app` kalıcı değildir |
| `public_base_url` | `""` | İndirme adreslerinin kökü (ör. `https://kontrolmerkezi.bbdstore.com.tr`). Boşsa istekten okunur: `CF-Visitor` şeması, sonra `X-Forwarded-Proto`/`X-Forwarded-Host`, sonra `Host` |

Neden böyle (sunucu bbdstore ile paylaşılıyor ve Cloudflare arkasında):

- **Parçalı yükleme.** Cloudflare 100 MB'tan büyük gövdeyi reddeder; panel köprüsü yalnız JSON taşır. Dosya `PUT /apps/uploads/{id}/chunk` ile 8 MiB'lık (`chunkBytes`) base64 parçalar hâlinde gelir ve diske kendi konumuna yazılır; dosya hiçbir zaman bütün olarak belleğe alınmaz. `offset` tam olarak `receivedBytes` olmalıdır, değilse 409 (doğru değer metinde ve `X-Received-Bytes` başlığında). Aynı ad + boyut + `fingerprint` (panel dosyanın değiştirilme zamanını yollar) ile yeniden başlatılan yükleme yenisini açmaz, kaldığı yerden sürer (`resumed: true`). Yükleme başlarken diskte dosya boyutu + 512 MB boş yer aranır. Sunucudaki yarım dosya kaybolduysa parça 409 alır ve kayıt `failed` olur (yeniden seçilen dosya baştan, yeni yükleme olarak başlar). Disk yazarken dolarsa 507 "Sunucuda yer kalmadı…", başka bir disk hatasında 500 ve Türkçe metin döner; `receivedBytes` ilerlemez, aynı parça yeniden gönderilebilir. Panel 409 sonrası durumu okur: kayıt `uploading` değilse ya da `receivedBytes` gönderdiği konumdan ilerlemiyorsa iki artan beklemeden sonra Türkçe hatayla durur (aynı parçayı sonsuza dek göndermez); 507'yi yeniden denemez.
- **`finish` beklemez.** 4 GB'lık paketi açıp SHA-256'sını hesaplamak Cloudflare'in 100 saniyelik yanıt sınırını aşabilir. `POST /apps/uploads/{id}/finish` işi arka planda başlatır (dosya işleri `asyncio.to_thread` ile; olay döngüsü tabletlerin sync'ine açık kalır) ve hemen `state: "processing"` döner. Panel `GET /apps/uploads/{id}` ile yoklar: `state` `uploading` | `processing` | `ready` | `failed`, `receivedBytes`, `error`; `ready` olunca `release` alanında taslak (paket, sürüm, dosyalar ve SHA-256'ları, varsa `current` mevcut sürüm). Süreç işleme sırasında yeniden başlarsa `GET /apps` (ya da durum sorgusu) işi yürüten görev olmayan `processing` kayıtlarını yeniden başlatır (bellekteki görev listesine bakılır, sorgu eklenmez). İşlenen taslak da atılabilir (`DELETE /apps/releases/{id}`): paket açma 1 MiB'lık blok sınırında durur, kayıt ve dosyalar silinir.
- **Paket açma.** Tek APK yerinde özetlenir (SHA-256), sonra taşınır (kopyalanmaz) ve `base.apk` olur; taşımadan hemen sonra süreç durursa yeniden başlayan iş `base.apk`'yı yükleme dosyasına geri alır. Paketin yükleme dosyası ise taslak kaydedildikten sonra silinir; yarıda kalan açma baştan yapılır. Pakette her APK ayrı dosyaya akış hâlinde açılır (`base.apk`, `split_<ad>.apk`; ad tabletin `^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$` kuralına uyar: uzun parça adı kısaltılıp sonuna 8 haneli özet eklenir, güvensiz karakterleri ayıklanınca çakışan adlar da özetle ayrılır); tek bir temel APK (`split` özniteliği yok) şarttır, hepsi aynı paket adını ve versionCode'u taşımalıdır. OBB içeren, şifreli ya da APK içermeyen paket reddedilir (`failed` ve Türkçe neden). bundletool'un `standalones/` klasörü (Android 5 öncesi) atlanır. Ad önerisi: daha önce dağıtılmış ad → manifestteki düz etiket → bir tabletin envanterindeki etiket → paket adı.
- **Yayın.** `POST /apps/releases/{id}/publish` taslağı dağıtır; aynı paketin güncel yayınından küçük ya da eşit versionCode 409'dur. Eski yayının dosyaları hemen silinir; onu indirmekte olan tablet `410 RELEASE_GONE` alır. Daha önce "tabletlerden kaldır" denmiş paket yeniden dağıtılınca kaldırma listesinden çıkar. Yayın, taslağı atma ve temizliğin taslak silmesi aynı yayının kilidini alır (sıra kilidi her zaman ondan sonra), yani birbirini kesmez. Modül deposunda işlem (transaction) olmadığından yazma sırası güvenlidir: önce `managed_apps` (upsert), sonra yayının durumu, sonra eski yayın. İlk yazmadan sonra süreç düşerse katalog yeni taslağı zaten sunar; temizlik `managed_apps`'in gösterdiği yayını asla silmez, onu `published` yapar ve hiçbir kaydın göstermediği `published` yayını `superseded` yapıp dosyalarını siler; aynı taslağı yeniden dağıtmak da işi bitirir.
- **Sıra ve yuvalar.** Sıra sunucudadır: önce `kind: self`, sonra öncelikli (öğrencinin dokunduğu `installs.priority` ya da paneldeki "Öne al"), sonra tabletin yayını ilk istediği an. Tablet başına tek yuva vardır. Yuva yalnız sync anında ve yalnız sıranın başındaki tablete verilir; sıranın ilk `download_slots` yerindeki tablete `nextSyncSeconds` en fazla 30 gider ki boşalan yuvayı hızlı devralsın. Sıra satırları değil tabletleri sayar: yuvası olmayan her tablet sırada bir kez, en öndeki satırıyla durur (yuvası olan tabletin istediği diğer yayınlar kimsenin önüne geçmez). `queue` her istenen yayın için ayrı girdi verir; `position` = 1 + o satırın önünde duran, yuvası olmayan başka tablet sayısı (tabletin en öndeki satırında bu, tabletin sıradaki yeridir). Süresi dolan yuvanın satırı sıranın sonuna geçer (öncelikliyse öncelikli grubun sonuna). Tablet bir yayını artık istemiyorsa (`wanted`/`active`/`priority` içinde yoksa) sıradan düşer ve yuvası boşalır. Sonuçlar: `ok: true` (`already_installed` dahil) kuruldu, `ok: false` hata; hatalı yayın yeniden istenince sıranın sonuna girer. Tablet silinince ya da eşlemesi iptal edilince sıra satırları da silinir.
- **Sync ucuz kalır.** Katalog (güncel yayınlar + kaldırma listesi) ve opak `apps.version` bellekte tutulur, yalnız yayın/kaldırma sonrası yeniden okunur (tek uvicorn süreci). `appsVersion` aynıysa `apps: null` ve sorgu yok; `installs` gelmeyen sync sıra tablosuna dokunmaz (`install: null`). `apps` yalnız istek `appsVersion` anahtarını taşıyorsa döner (değeri `null` olsa da: o zaman tam katalog); anahtarı hiç göndermeyen eski APK her sync'te kataloğu almaz, `apps: null` görür.
- **Dosya ucu.** `GET /api/tablet/v1/devices/{id}/releases/{releaseId}/files/{index}` cihaz bearer'ı ve bu tablete ait geçerli yuva ister (yoksa `409 DOWNLOAD_NOT_GRANTED`; yayın güncel değilse `410 RELEASE_GONE`). Starlette `FileResponse` ile `Range: bytes=N-` → 206 (`FileResponse`'un `Range` desteği Starlette 0.39 ile geldi; çekirdeğin bağımlılığı bunun altına inerse dosya ucu her istekte baştan 200 döner). Yanıt `Cache-Control: no-store` taşır, adreste uzantı yoktur (Cloudflare önbelleğe almaz) ve `Content-Type: application/zip`'tir: çekirdeğin `GZipMiddleware`'i bu türe ve 206'ya dokunmaz, `Content-Length` doğru kalır. Adresler mutlaktır (`public_base_url` ya da istekten).
- **Panel sayıları.** `GET /apps` her uygulama için şu an eşli tabletler üzerinden kuruldu / indiriliyor / sırada / hata / haber yok sayar (sıra satırı yoksa tabletin envanterindeki sürüm de "kuruldu" sayılır), taslakları, kaldırma listesini ve yuva doluluğunu verir. `GET /apps/{package}/devices` tablet başına durum, sıra, ilerleme yüzdesi ve son hata kodunu verir; panel kodu Türkçe cümleye çevirir.
- **Dağıtımı bitirme.** `DELETE /apps/{package}` gövdede `{pin, uninstall}` ister; dosyalar ve sıra satırları silinir, `uninstall: true` ise paket sync'teki `apps.remove` listesine girer. `kind: self` bitirilemez (409).
- **Temizlik.** Panelin `GET /overview` yolundaki günlük temizlik iki gündür dokunulmamış (`updated_at`) şu kayıtları dosyalarıyla siler: yarım yüklemeler (`uploading`), görevi çalışmayan `processing` kayıtları, işlenmiş ama hiç dağıtılmamış taslaklar (`draft`, panelde "Dağıtıma hazır") ve başarısız kayıtlar (`failed`). `managed_apps`'in gösterdiği yayın hiçbir durumda silinmez; her silme kaydı yayının kilidi altında yeniden denetler. Önce yarıda kalmış yayın/dağıtım bitirme işleri onarılır (bkz. Yayın). Hiçbir kayda bağlı olmayan dosyalar da bir saatten eskiyse atılır; `.part` yükleme dosyası yalnız kayıt `uploading`/`processing` iken canlı sayılır.

Panel: dördüncü sekme **Uygulamalar** — "APK yükle" (ilerleme, hız, iptal, hata alınca artan beklemeyle yeniden deneme, yarım yüklemeyi sürdürme), hazır olunca onay penceresi (paket, sürüm, boyut, parça sayısı, mevcut sürüm, düzenlenebilir ad), en üstte "Tablet uygulaması sürümü" kartı, dağıtılan uygulamalar tablosu ve sayaçları, tablet başına durum çekmecesi ("Öne al", PIN'li "Dağıtımı bitir" ve tabletlerden kaldırma seçimi), taslaklar ("Dağıt", "At"). Profil düzenleyicisinde uygulama kaynağı olarak "Dağıtılan uygulamalar" seçilebilir; henüz hiçbir tablete kurulmamış uygulama da profile eklenir.

## Silme

- **Eşlemeyi iptal et** (`POST /devices/{id}/revoke`): kayıt kalır, tablet yeni kodla yeniden bağlanabilir.
- **Tableti sil** (`DELETE /devices/{id}`): her durumda çalışır. Eşli tablet aynı işlemde önce iptal edilir (açık oturum `revoked` ile kapanır, cihaz tokenı ölür); sonra kayıt, yüklü uygulamalar, komutlar, indirme sırası satırları ve giriş kilidi silinir. Eski token her uçta 401 `DEVICE_AUTH_INVALID` alır, `unpair` tekrarı da. Oturum, kullanım ve ek süre geçmişi öğrenci verisi olarak kalır; geçmiş görünümlerinde tablet adı "Silinmiş tablet" olur (`deviceDeleted: true`). Toplu silmede (`POST /devices/bulk`, `action: "delete"`) sonuç tablet başınadır.
- **Komut iptali** (`DELETE /devices/{id}/commands/{commandId}`): tabletin onaylamadığı komut (iletilmiş olsa da) `cancelled` olur, sync'te bir daha gitmez. Tablet komutu aldığı anda çalıştırdığı için iletilmiş komutun sonradan gelen sonucu yine yazılır ve durum "Tamamlandı/Başarısız" olur. Onaylanmış komut 409.
- **Profil silme** (`DELETE /profiles/{id}`): atanmış (iptal edilmemiş) tablet varsa gövdede `replacementProfileId` (tabletler o profile atanır) ya da `"unassign": true` (tabletler profilsiz kalır; öğrenci girişi `NO_PROFILE`) gerekir; ikisi de yoksa 409 ve kaç tablete atandığını söyleyen Türkçe açıklama (yönetim uçlarındaki 409'lar çekirdek zarfında metin olarak döner). Profil, uygulama kuralları ve sistem kontrolleri silinir; açık oturumlar kapanmaz.
- **Öğrenci verisi silme** (`DELETE /students/{id}/history`): bitmiş oturumlar, kullanım paketleri ve satırları, günlük toplamlar, ek süreler ve kısıtlamalar (süren kısıtlama da) silinir; sayıları döner. Açık oturum varken 409; uzun süredir sessiz oturum önce kapatılır. Kantin hesabına dokunulmaz.
- **Giriş kilidini kaldır** (`POST /devices/{id}/login-lock/reset`): kaydı siler; kilit sürüyorsa `deviceState` sürümünü artırır ve `{"deviceId", "cleared": true}` döner. `GET /overview` her tablet için `login_locked_until` ve özette `loginLocked` sayısını verir.

Panel: tablet listesinde "Giriş kilitli · 3 dk" rozeti ve süzgeci; çekmecede "Giriş kilidini kaldır" (yalnız kilitliyken), komut geçmişinde "İptal et", en altta "Tableti sil"; toplu işlemlerde "Giriş kilidini kaldır" (grant izniyle) ve "Sil"; profil silmede atanmış tabletler için taşıma ya da profilsiz bırakma seçimi; öğrenci detayında en altta "Tablet verilerini sil" (açık oturumda nedeni yazılı pasif düğme).
