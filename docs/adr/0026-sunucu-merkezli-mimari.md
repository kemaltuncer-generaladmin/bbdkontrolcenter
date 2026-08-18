# ADR 0026 — Sunucu merkezli mimari: veri merkezde, kabuk ince

**Durum:** Kabul edildi · 18.08.2026
**Bağlam:** ADR 0021 (merkezî kimlik), ADR 0023 (paketleme ve veri dizini),
ADR 0025 (sırların dağıtımı) bu kararla kapsam değiştirdi.

## Sorun

Verinin tamamı tek makinenin diskindeydi. `data/kontrol-merkezi.sqlite`
içinde 131 tablo vardı ve 120'si modül tablosuydu: ders programı, öğle
yemeği, zil saatleri, öğrenci profilleri, bütün BLD/Store ekranları.

Sonuç sahada üç somut arıza olarak göründü:

- İkinci makinede (macOS) ders programı **boştu** — `mod_bell_time` bu
  makinede duruyordu.
- İkinci makinede **kimse giriş yapamıyordu** — yereldeki 7 aktif kullanıcının
  hepsi `origin='local'`'di, merkezde yalnız 2 pasif kayıt vardı.
- Öğrenci yönetimi hata veriyordu — modül `canteen.api` yeteneğini tüketiyor,
  o da kasadaki sırlara bağlı ve ADR 0025'in dağıtımı hiç koşturulmamıştı.

ADR 0021'in merkezî kimlik servisi yalnız beş şey taşıyordu: kullanıcı/rol/izin,
denetim izi, kurulum kaydı, danışma kilidi, sır+ayar dağıtımı. **Modül verisi
hiç akmıyordu** ve akıtacak bir mekanizma da yoktu.

## Değerlendirilen yollar

1. **Çift yönlü senkron.** Yerel SQLite kalır, arka planda replike edilir. Tam
   çevrimdışı çalışır ama iki makine aynı satırı değiştirdiğinde çakışma
   çözümü gerekir — en karmaşık ve en riskli yol.
2. **Merkez + yerel ayna.** Yazma merkeze, okuma yerel aynadan. Çevrimdışı
   okuma sürer. Değişiklik akışı, anlık görüntü ve çakışma penceresi gerekir.
3. **Sunucu merkezli.** Backend sunucuda koşar, masaüstü uygulaması ince
   kabuktur. Tek veritabanı, tek doğruluk kaynağı.

(2) bir süre tasarlandı ve depo katmanı yazıldı. Sonra ölçüm kararı değiştirdi:
ağ gidiş-dönüşü **61 ms**, bağlantı yeniden kullanımıyla HTTPS isteği
**~100 ms** ve bir panel açılışı **19 sorgu** yapıyor. Sorguları tek tek ağdan
geçirmek panel başına ~1,9 saniye demekti. Sorgular sıralı ve birbirine bağımlı
olduğu için otomatik gruplanamıyorlar.

Backend sunucuda koşarsa aynı 19 sorgu Postgres'e **1 ms**'de gider ve panel
kullanıcıya **tek istek** olur.

## Karar

**Backend sunucuda koşar. Masaüstü uygulaması ince kabuktur.**

- **Veri:** Coolify'daki PostgreSQL. Yalnız iç ağ; internete port açılmaz.
- **Uygulama:** `deploy/server/Dockerfile` — çekirdek + 49 modül tek imajda.
- **Kabuk:** Tauri; istekler `core_request` üzerinden sunucuya gider. Adres
  pakete gömülüdür, `KM_SERVER_URL` ile aşılır; `local` yazmak eski yerel
  davranışa döndürür.
- **Sidecar yalnız yerel kipte başlar.** Sunucu kipinde ikinci bir çekirdek
  açmak, kullanıcının BOŞ bir yerel veritabanına bakıp "verilerim gitti"
  sanmasına yol açardı.
- **İnternet kesilirse uygulama durur.** Kullanıcı kararı; kabuk bunu tek ve
  anlaşılır bir ekranla söyler, yarım panel açmaz.

## Sonuçları

### Depo iki motorlu

`km_core/store/dialect.py` SQLite lehçesini PostgreSQL'e çevirir; kapsam
ölçülerek daraltıldı ve **tanımadığını reddeder**. `km_core/store/bootstrap.py`
şemayı kuran tek yerdir — iki motor da oradan geçer, ayrışamazlar.

Gerçek koşu, yalnız PostgreSQL'de görünen dört hata buldu: `IS NOT ?`,
`ORDER BY rowid`, `GROUP BY`'da gruplanmamış sütun ve sessiz olan `LIKE`
harf duyarlılığı farkı.

### Zil değişmedi, iyileşti

Zil zaten KM'den çıkmıyordu: `modules/bell/backend/bridge.py` bbdstore
köprüsüne komut yazıyor, Windows ajanı köprüyü üç saniyede bir yokluyor.
Backend sunucuya taşınınca zil **daha güvenilir** oldu — bugüne kadar ancak
ofis makinesi açıkken programlanıyordu, artık 7/24.

Taşınmanın bozduğu tek şey ses **önizlemesiydi**: `audio.play` sesi sunucuda
çalmaya kalkardı. Uç artık sesi base64 veri URI'si olarak veriyor, kabuk
çalıyor. Üç platformda aynı yol; `afplay`/`winsound` dalına gerek kalmadı.

### İnternete açılan API iki yeni kapı istedi

- **Kasa anahtarı `KM_SECRET_KEY` ile ortamdan okunur.** Konteynerde `/data`
  boştur; anahtar yalnız dosyadan okunsaydı kasa kendine yenisini üretir ve
  veritabanındaki 18 şifreli sırrın hiçbirini çözemezdi — `core.pin_pepper`
  dahil. Belirti "kimse giriş yapamıyor" olurdu ve sebebi hiç ele vermezdi.
- **Giriş hız sınırı** (`km_core/security/rate_limit.py`). Giriş 6 haneli
  PIN'dir: 1.000.000 ihtimal. `users.failed_attempts` bu saldırıyı göremez ve
  tasarım gereği göremez — giriş kullanıcı adsızdır, yanlış PIN hiçbir satıra
  denk gelmez. Sınır bu yüzden isteğin geldiği yere konur.

### Kapsam değişen kararlar

- **ADR 0021 (merkezî kimlik).** Tek veritabanı olunca kadro yansıtması ve
  72 saatlik önbellek dalı anlamını yitirdi. Kod silinmedi, devre dışı kaldı.
- **ADR 0025 (sır dağıtımı).** Sırlar artık tek yerde; dağıtılacak ikinci bir
  kurulum yok.
- **ADR 0023 (veri dizini).** Kurulumun veri dizini yalnız kabuğun kendi
  tanılama dosyasını taşır; üretilen PDF'ler ve sesler sunucunun kalıcı
  diskindedir.

## Ölçülen sonuç

Göç edilmiş veritabanıyla sunucu ayağa kalktı: 49 modül, 0 sorun, 130 tablo.
224 parametresiz GET ucundan 217'si 200 döndü; kalan 6'sı uzak BBD/BLD API'sine
gidiyor (kapta erişilemez), 1'i beklenen 503. **Sıfır 500.**

`core.pin_pepper` özeti iki tarafta birebir aynı ve `secret_lookup` değerleri
aynen taşındı — yerelde çalışan her PIN sunucuda da çalışır.
