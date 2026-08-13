# Kantin Yedekleri modülü

Sunucudaki yedekleri görür, elle yedek aldırır ve yedeği **bu makineye indirip
sha256 ile doğrular**. Sunucu tümden kaybolsa bile elde doğrulanmış bir kopya kalır.

## Ne yapar

- **Tazelik göstergesi** — son yedek kaç dakika önce alındı. Eşik aşılırsa
  (zamanlayıcı durmuş, disk dolmuş) ekran kırmızıya döner. Yedek hiç yoksa da
  "bayat" sayılır; sessiz kalmak en kötüsüdür.
- **Elle yedek** — sunucuda anında tutarlı bir kopya oluşturur (`VACUUM INTO`).
- **Yerel kopya** — dosyayı indirir, sunucunun bildirdiği özetle karşılaştırır.
  **Tutmuyorsa dosyayı saklamaz:** bozuk bir yedeği "elimde kopya var" diye
  tutmak, hiç kopyası olmamaktan tehlikelidir.
- **Kopya doğrulama** — yereldeki dosyaları yeniden özetleyip sessiz bozulmayı
  (disk hatası, yarım kopya) yakalar.
- Yerelde en çok `keep_local` kopya tutulur; fazlası indirme sırasına göre silinir.

## Geri yükleme yoktur

Tek tıkla canlı veritabanının üzerine yazmak kabul edilemez bir risk olduğu için
kantine böyle bir uç **hiç eklenmedi**. Geri yükleme sunucuda elle yapılır.

## Bu modül için kantine eklenen uçlar

`GET /api/backups` · `POST /api/backups` · `GET /api/backups/{name}/download`
(indirme dizin geçişine karşı iki kapılı: ad kalıbı + gerçek yol denetimi).

- Sözleşme: `module.yaml` · Giriş noktası: `backend/module.py` → `register(ctx)`
- Kurallar: [../../CLAUDE.md](../../CLAUDE.md) · Kılavuz: [../../docs/module-guide.md](../../docs/module-guide.md)

Grup: **BBD** · İzinler: `bbd_canteen_backups.view`, `bbd_canteen_backups.manage`
