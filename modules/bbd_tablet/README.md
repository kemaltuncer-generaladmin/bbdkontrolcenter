# Öğrenci Tablet Yönetimi

Paylaşımlı okul tabletleri için profil, kullanım saatleri, cihaz, öğrenci oturumu, kullanım, ek süre, öğrenci kısıtlaması ve uzaktan komutları tutar. Bağlayıcı sözleşme TabletKontrol deposundaki `docs/CONTROL_CENTER_INTEGRATION.md` (v2) dosyasıdır.

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
| `POST /devices/{id}/sync` | Cihaz | Tek periyodik çağrı: profil, cihaz durumu, oturum, ek süre, komutlar |
| `POST /auth/login` | Cihaz | Öğrenci girişi |
| `POST /auth/logout` | Öğrenci oturumu | Çıkış (tekrarlanabilir) |
| `POST /usage/batch` | Öğrenci oturumu | İdempotent kullanım paketi |
| `POST /devices/{id}/unpair` | Cihaz | Tabletten eşlemeyi kaldır (tekrarlanabilir) |
| `GET /devices/{id}/policy`, `POST /devices/{id}/heartbeat` | Cihaz | Eski APK'lar için |

Makine tarafından okunan hatalar `{"detail": {"code", "message", ...}}` biçimindedir: `DEVICE_REVOKED`, `DEVICE_AUTH_INVALID`, `FORBIDDEN_DEVICE`, `INVALID_STUDENT_CODE`, `LOGIN_RATE_LIMITED` (+`retryAfterSeconds`), `SESSION_CONFLICT`, `STUDENT_BLOCKED` (+`until`), `OUTSIDE_USAGE_HOURS` (+`nextWindowStart`), `DEVICE_LOCKED`, `NO_PROFILE`, `CANTEEN_UNAVAILABLE`.

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

Profilde uygulama üç kipten birindedir: yasak, sınırsız ya da günlük süreli (60–86400 sn). Adı boş bırakılan uygulama paket adıyla kaydedilir; profil adı kırpılır ve 2–100 karakter olmalıdır (`006_profile_names.sql` eski kayıtları düzeltir). `usageWindows` ISO hafta günleri (1 = Pazartesi) ve profil saat diliminde `HH:MM` başlangıç (dahil) – bitiş (hariç) taşır; boş liste her zaman açık demektir.

## Silme

- **Eşlemeyi iptal et** (`POST /devices/{id}/revoke`): kayıt kalır, tablet yeni kodla yeniden bağlanabilir.
- **Tableti sil** (`DELETE /devices/{id}`): her durumda çalışır. Eşli tablet aynı işlemde önce iptal edilir (açık oturum `revoked` ile kapanır, cihaz tokenı ölür); sonra kayıt, yüklü uygulamalar, komutlar ve giriş kilidi silinir. Eski token her uçta 401 `DEVICE_AUTH_INVALID` alır, `unpair` tekrarı da. Oturum, kullanım ve ek süre geçmişi öğrenci verisi olarak kalır; geçmiş görünümlerinde tablet adı "Silinmiş tablet" olur (`deviceDeleted: true`). Toplu silmede (`POST /devices/bulk`, `action: "delete"`) sonuç tablet başınadır.
- **Komut iptali** (`DELETE /devices/{id}/commands/{commandId}`): tabletin onaylamadığı komut (iletilmiş olsa da) `cancelled` olur, sync'te bir daha gitmez. Tablet komutu aldığı anda çalıştırdığı için iletilmiş komutun sonradan gelen sonucu yine yazılır ve durum "Tamamlandı/Başarısız" olur. Onaylanmış komut 409.
- **Profil silme** (`DELETE /profiles/{id}`): atanmış (iptal edilmemiş) tablet varsa gövdede `replacementProfileId` (tabletler o profile atanır) ya da `"unassign": true` (tabletler profilsiz kalır; öğrenci girişi `NO_PROFILE`) gerekir; ikisi de yoksa 409 `{"code": "PROFILE_IN_USE", "message", "devices": [adlar]}`. Profil, uygulama kuralları ve sistem kontrolleri silinir; açık oturumlar kapanmaz.
- **Öğrenci verisi silme** (`DELETE /students/{id}/history`): bitmiş oturumlar, kullanım paketleri ve satırları, günlük toplamlar, ek süreler ve kısıtlamalar (süren kısıtlama da) silinir; sayıları döner. Açık oturum varken 409; uzun süredir sessiz oturum önce kapatılır. Kantin hesabına dokunulmaz.
- **Giriş kilidini kaldır** (`POST /devices/{id}/login-lock/reset`): kaydı siler; kilit sürüyorsa `deviceState` sürümünü artırır ve `{"deviceId", "cleared": true}` döner. `GET /overview` her tablet için `login_locked_until` ve özette `loginLocked` sayısını verir.

Panel: tablet listesinde "Giriş kilitli · 3 dk" rozeti ve süzgeci; çekmecede "Giriş kilidini kaldır" (yalnız kilitliyken), komut geçmişinde "İptal et", en altta "Tableti sil"; toplu işlemlerde "Giriş kilidini kaldır" (grant izniyle) ve "Sil"; profil silmede atanmış tabletler için taşıma ya da profilsiz bırakma seçimi; öğrenci detayında en altta "Tablet verilerini sil" (açık oturumda nedeni yazılı pasif düğme).
