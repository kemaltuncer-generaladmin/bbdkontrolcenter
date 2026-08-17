# Antivirüs modülü

ClamAV ile sistem taraması ve imza güncelliği takibi.

Motor erişimi bu modüle aittir: `clamdscan` (birincil, daemon üzerinden) ve
`clamscan` (yedek). Şu an tek tüketicisi olduğu için platform yeteneği
yapılmadı; ikinci bir tüketici çıkarsa `km_platform/antivirus/` altına
yükseltilir (ADR 0009).

**Yalnız Ubuntu'da bulunur.** `module.yaml` → `platforms: [linux]`. Windows ve
macOS kurulumunda modül keşifte elenir: router monte edilmez, göç koşmaz,
görev planlanmaz, menüde görünmez (ADR 0022).

## Bilinmesi gerekenler

- İlk kurulumda freshclam **~300 MB imza indirir**; bitmeden clamd başlamaz.
  Arayüz bu durumu hata değil "hazırlanıyor" olarak gösterir.
- clamd imzaları bellekte tutar, ~1–2 GB RAM kullanır. Daemon kapalıysa modül
  `clamscan` yedeğine düşer ve ekran taramanın neden yavaş olduğunu yazar.
- **Erişilemeyen yollar raporda listelenir; atlanan yol varken tarama "temiz"
  olarak raporlanmaz.** Bilerek hariç tutulan ve var olmayan yollar ayrı
  sayılır: onlar yöneticinin kararıdır, taramanın eksiği değildir.
- İmzaları `clamav-freshclam` servisi günceller. Modül içinden `freshclam`
  **çalıştırılmaz** (kilit çakışması yaratır); saatlik iş yalnızca imza yaşını
  okur ve eşiği aşarsa `antivirus.signatures_stale` yayınlar.

## Ekran

Bilerek küçük: **Tam Tarama** / **Hızlı Tarama** düğmeleri, süren taramanın
ilerlemesi, son taramanın sonucu (ne zaman, kaç dosya, kaç tehdit, kaç atlanan
yol) ve imza durumu. Tarama takvimi, hızlı tarama yolları, hariç tutulan
yollar ve imza yaşı eşiği **Sistem Ayarları**'ndaki kendi sekmesindedir
(`module.yaml` → `settings`, ADR 0018).

## Dosyalar

| Yol | İçerik |
|---|---|
| `backend/module.py` | Giriş noktası `register(ctx)`; yetenek, router ve tarama takvimi |
| `backend/engine.py` | ClamAV sarmalayıcısı: birincil/yedek seçimi, çıktı ayrıştırma, zaman aşımı |
| `backend/service.py` | Koşu yönetimi, kayıt, olaylar, imza güncelliği |
| `backend/api/routes.py` | `/state` · `/scan` · `/scan/cancel` |
| `backend/tasks/` | Manifestteki iki zamanlanmış işin gövdesi |
| `backend/migrations/` | Modülün kendi tabloları (K5) |

## Bu sürümde olmayanlar

- **Karantina ve kalıcı silme.** `antivirus.quarantine` ve
  `antivirus.delete_threat` izinleri sözleşmede duruyor (ADR 0009 §6) ama
  karşılığı olan uç nokta ve ekran yazılmadı; bulaşmış dosya yerinde bırakılır
  ve raporda listelenir.
- **Kök yetkili tam sistem taraması.** ADR 0009 §4'teki ayrı systemd servis
  birimi (`deploy/systemd/`) henüz yok. Tarama uygulamayı çalıştıran
  kullanıcının yetkisiyle koşar; okuyamadığı her yol raporda görünür ve sonuç
  "temiz" değil "eksik" yazılır. Sessiz eksik tarama yapılmaz.
- **Uzak sunucuda tarama ve SMS uyarısı.** `ssh`, `secrets` ve `notify`
  manifestte isteğe bağlı ilan edilmiştir, bu sürümde çözülmez. Bulaşma haberi
  `antivirus.threat_found` olayıyla veri yoluna düşer; bildirim ona bağlanır.

- Sözleşme: [module.yaml](module.yaml) · Gerekçe: [ADR 0009](../../docs/adr/0009-antivirus-clamav.md), [ADR 0022](../../docs/adr/0022-modul-platform-kapsami.md)
- Giriş noktası: `backend/module.py` → `register(ctx)`
