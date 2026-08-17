# Mimari Karar Kayıtları (ADR)

Her dosya bir kararı, gerekçesini ve sonuçlarını kaydeder. Karar verildikten
sonra dosya **değiştirilmez**. Karar değişirse yeni bir ADR yazılır ve eskisinin
durumu `Superseded by NNNN` yapılır.

Durum değerleri: `Kabul edildi` · `Superseded by NNNN` · `Reddedildi`

| # | Karar | Durum |
|---|---|---|
| 0001 | Çekirdek: Python 3 + FastAPI | Kabul edildi |
| 0002 | Arayüz: Tauri 2 masaüstü kabuğu | Kabul edildi |
| 0003 | Modül sistemi: manifest + dinamik keşif | Kabul edildi |
| 0004 | Bağımlılık yönü: çekirdek modülü bilmez | Kabul edildi |
| 0005 | Modül sınırı: dikey dilim | Kabul edildi |
| 0006 | ssh ve database platform yeteneğidir | Kabul edildi |
| 0007 | Kimlik ve yetkilendirme: PIN ile giriş, izin tabanlı çok rollü model | Kabul edildi |
| 0008 | Bağımlılık yönetimi ve sürücü politikası | Kabul edildi |
| 0009 | Antivirüs: ClamAV, modül olarak | Kabul edildi |
| 0010 | SMS sağlayıcı entegrasyonu: sarmalanmış Netgsm | Kabul edildi |
| 0011 | Panel arayüz kiti kabukta durur | Kabul edildi |
| 0012 | Mağaza yıkıcı işlemleri PIN yerine gerekçeli onay ister | Kabul edildi |
| 0013 | Anons sesi: Vertex AI, önden üretim | Kabul edildi |
| 0014 | Çok platformlu baskı: Linux sessiz, Windows/macOS sistem penceresi | Kabul edildi |
| 0015 | Bildirilmiş ama diskte olmayan panel girişi, giriş sayılmaz | Kabul edildi |
| 0016 | Giriş: kullanıcı adsız, kişiye özel şifre | Reddedildi |
| 0017 | Çekirdek ekranlar kabukta ayrı hiyerarşide durur | Kabul edildi |
| 0018 | Sistem Ayarları tek ekrandır; sekmeleri modüller ilan eder | Kabul edildi |
| 0019 | Çıktı Merkezi: kayıt, dosyayı yazan tek yerde doğar | Kabul edildi |
| 0020 | Eşzamanlı düzenleme: iyimser kilit zorunlu, danışma kilidi uyarı | Kabul edildi |
| 0021 | Merkezî kimlik servisi ve cihaz eşlemesi | Kabul edildi |
| 0022 | Modül platform kapsamı manifestte ilan edilir | Kabul edildi |
| 0023 | Paketleme: gömülü Python çalışma zamanı ve platforma göre veri dizini | Kabul edildi |

---

## Sonradan düşülen notlar

ADR gövdesi **değişmez**. Yazıldığı gün doğru olan bir çapraz gönderme veya ad
sonradan yanlışlanabilir; o zaman metin düzeltilmez, sapma buraya kaydedilir.
Bu bölüm kararların kendisini değiştirmez — hangi cümlenin bugünkü karşılığının
ne olduğunu söyler.

| ADR | Metinde yazan | Bugünkü gerçek |
|---|---|---|
| 0021 §4 | "kişi yine kendi şifresini girer (**ADR 0016**)" | **0016 reddedildi.** Giriş 6 haneli **PIN**'dir (ADR 0007). Cümlenin savı geçerlidir — eşleme token'ı kullanıcı oturumu değildir, kişi ayrıca kendi sırrını girer; o sır bugün şifre değil PIN'dir. |
| 0012, Bağlam | `auth.require_pin_for_destructive` | Anahtarın gerçek adı `config/default.yaml` içinde **`auth.require_password_for_destructive`**. Ad, ADR 0016'nın koşmuş göçünden kalmıştır; 0016 reddedilince adlar geri çevrilmedi (ikinci göç = veri riski — bkz. 0016). İstenen teyit yine PIN teyididir; 0012'nin kararı (mağaza yıkıcı işlemleri PIN yerine gerekçeli onay ister) bundan etkilenmez. |

Not düşmek ADR yazmanın yerine geçmez: **kararın kendisi** değişiyorsa yeni bir
ADR açılır ve eskisinin durumu `Superseded by NNNN` yapılır. Buraya yalnız
kararı değiştirmeyen sapmalar — kırık çapraz gönderme, eskimiş ad — girer.
