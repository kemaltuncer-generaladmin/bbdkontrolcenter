# 0009 — Antivirüs: ClamAV, modül olarak

**Durum:** Kabul edildi · 2026-08-12

## Bağlam
Sistemde virüs taraması yapan kendi ekranımız olacak. Motor olarak ClamAV
seçildi. Hedef makinede kurulu değil; Ubuntu 26.04 deposunda 1.5.3 mevcut.
Sistem belleği 7 GB.

## Karar

### 1. Antivirüs bir modüldür, platform yeteneği değildir
`modules/antivirus/` altında durur, `enabled: false` yapılabilir, silinebilir.

Ölçüt ADR 0005'te sabitlendi: **klasörü sil, özellik tümüyle gitsin.** Antivirüs
ekranı silinebilir bir iş özelliğidir; SSH gibi "her şeyin bağlandığı" bir
altyapı değildir. Şu an tek tüketicisi kendisidir.

**Yükseltme koşulu:** ikinci bir tüketici çıkarsa — yüklenen dosyaların
taranması, yedeklerin taranması gibi — motor istemcisi
`km_platform/antivirus/` altına taşınır ve modülde yalnızca arayüz, geçmiş ve
karantina yönetimi kalır. Bu, module-guide'daki "gerçekten paylaşılıyorsa
platform yeteneğine yükseltilir" kuralının uygulanmasıdır.

### 2. Tarama yolu: clamdscan birincil, clamscan yedek
- **Birincil:** `clamdscan` — `clamav-daemon` (clamd) imzaları bellekte tutar,
  tarama hızlıdır.
- **Yedek:** `clamscan` — daemon yoksa çalışır, her taramada imzaları diskten
  yükler; belirgin biçimde yavaştır.

Ses tarafındaki `paplay` → `aplay` deseninin aynısıdır: hızlı yol birincil,
bağımsız yol yedek.

### 3. Python istemci paketi kullanılmıyor
PyPI'daki `clamd` (son sürüm 2014) ve `pyclamd` (2017) bakımsızdır; kullanılmaz.
Güncel `clamav-client` (0.7.2, Mart 2026) mevcuttur ancak **GPL-2.0** lisanslıdır
ve asıl değeri bellek içi tampon taramasındadır — bizim işimiz dosya sistemi
taraması, onu `clamdscan` zaten yapıyor.

Bu yüzden Python bağımlılığı **yok**. Yüklenen dosyaların bellekte taranması
gerekirse `clamav-client` yeniden değerlendirilir; uygulama kurum içi kullanım
içindir ve dağıtılmadığı sürece GPL yükümlülüğü doğmaz — dağıtım gündeme
gelirse bu karar gözden geçirilir.

### 4. Yetki sorunu: tam sistem taraması kök yetkisi ister
Uygulama normal kullanıcı olarak çalışır; `/` altındaki her dosyayı okuyamaz.
Sessizce eksik tarama yapmak kabul edilemez — "temiz" raporu yanıltıcı olur.

Karar:
- Kullanıcının okuyabildiği yollar doğrudan `clamdscan --fdpass` ile taranır.
- Sistem geneli tarama, `deploy/systemd/` altında tanımlı **ayrı bir servis
  birimi** üzerinden yapılır; uygulama bu birimi tetikler ve sonucunu okur.
- Erişilemeyen yollar tarama raporunda **açıkça listelenir**. Atlanan yol
  varken tarama "temiz" olarak raporlanamaz.

### 5. İmza güncelliği
`clamav-freshclam` servisi imzaları kendi günceller. Uygulama içinden elle
`freshclam` çalıştırılmaz — servis çalışırken kilit çakışması yaratır.
Saatlik bir görev yalnızca **imza yaşını okur**; eşiği aşarsa
`antivirus.signatures_stale` olayı yayınlanır.

### 6. Karantina ve yıkıcı işlemler
Bulaşmış dosya `data/quarantine/` altına taşınır; özgün yolu ve zaman damgası
kayıtta tutulur, geri yükleme mümkündür. Karantinaya alma ve kalıcı silme
`destructive: true` işaretlidir: izin yeterli olsa bile **PIN teyidi** ister ve
denetim izine yazılır (ADR 0007).

## Sonuçlar
- Kurulum `clamav`, `clamav-daemon`, `clamav-freshclam` paketlerini gerektirir.
  Bunlar modülün kendi `dependencies.system` bloğunda ilan edilir — çekirdeğin
  listesine dokunulmaz (K6, K11).
- **İlk kurulumda freshclam ~300 MB imza indirir; bu bitmeden clamd başlamaz.**
  İlk açılışta motorun hazır olmaması normaldir, arayüz bunu "hazırlanıyor"
  olarak göstermelidir; hata olarak değil.
- clamd imzaları bellekte tutar, **~1–2 GB RAM** kullanır. 7 GB'lık sistemde
  kabul edilebilir, ancak bellek baskısı olursa daemon kapatılıp `clamscan`
  yedek yoluna düşülebilir.
- Tam sistem taraması G/Ç yoğundur; varsayılan takvim gece 03:00'e alındı.
