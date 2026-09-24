# Öğrenci Tablet Yönetimi

Bu modül kurum tabletleri için profil, cihaz, öğrenci oturumu, kullanım ve ek süreyi tutar. Öğrenci kimliği ve 6 haneli giriş kodu kantinde kalır. Tablet girişinde sunucu `canteen.api.lookup_student_by_code` çağırır; kantin cihaz token'ı tablete verilmez. Kontrol Merkezi çalışan PIN'i öğrenci girişinde kullanılmaz.

## Yönetim

Menü: **BBD → Öğrenci Tablet Yönetimi**. Profillerde izinli uygulamalar, günlük saniye limiti ve revizyon bulunur. Tablet kaydı bir kez gösterilen eşleme kodu üretir. Öğrenci sekmesinde günlük kullanım ve öğrenci hesabına bağlı uygulama ek süresi görünür. Yönetim uçları `/api/bbd_tablet` altında normal Kontrol Merkezi oturumu ve modül izinleriyle korunur.

## Tablet API

`/api/tablet/v1` yolları tabletler içindir. Enrollment dışında her uç `Authorization: Bearer` ister. Eşleme `deviceToken` üretir; yalnız SHA-256 özeti sunucuda saklanır. Giriş `sessionToken` üretir; yalnız özeti saklanır. Öğrenci oturumu 30 dakika hareketsizlikten sonra sona erer. Aktif tabletten heartbeat süreyi yeniler.

| Yöntem ve yol | Bearer | İşlev |
| --- | --- | --- |
| `POST /devices/enroll` | Yok; tek kullanımlık `enrollmentCode` | Fiziksel cihazı eşle |
| `GET /profiles`, `GET /profiles/{id}` | Cihaz | Ortak profilleri oku |
| `GET /devices/{id}/policy?currentRevision=N` | Cihaz | Profil değişikliğini, aktif öğrencinin kullanımını ve ek sürelerini oku |
| `POST /devices/{id}/heartbeat` | Cihaz | Bağlantı ve oturum süresini yenile |
| `PUT /devices/{id}/apps` | Cihaz | Uygulama envanterini gönder |
| `POST /auth/login` | Cihaz | `{deviceId,studentCode}` ile mevcut kantin kodunu doğrula |
| `POST /auth/logout` | Öğrenci oturumu | `{deviceId,sessionId}` oturumunu bitir |
| `POST /usage/batch` | Öğrenci oturumu | İdempotent kullanım paketi gönder |

Kullanım paketi `{batchId,deviceId,sessionId,studentId,startedAt,endedAt,usages}` taşır. `usages` öğeleri `{packageName,localDate,usedSeconds}` biçimindedir. Zamanlar ISO-8601 ofsetli, `localDate` profil saat diliminde `YYYY-MM-DD` biçimindedir. Aynı `batchId` ikinci kez yazılmaz. Sunucu öğrenci, oturum, cihaz ve tarih sınırlarını doğrular. Yanıt `{accepted,duplicate,usage,serverTime}` biçimindedir.

Profil `{id,name,revision,timezone,updatedAt,apps}` biçimindedir. Uygulama kuralları `{packageName,appName,allowed,unlimited,dailyLimitSeconds,sortOrder}` taşır. Oturum giriş yanıtı yalnız öğrencinin `id` ve `name` alanlarını taşır; kantin bakiyesi, iletişim bilgisi veya giriş kodu döndürülmez.
