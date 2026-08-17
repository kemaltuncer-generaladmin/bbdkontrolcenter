# 0018 — Sistem Ayarları tek ekrandır; sekmeleri modüller ilan eder

**Durum:** Kabul edildi · 2026-08-16

## Bağlam

Sistem Ayarları çekirdek bir ekrandır (ADR 0017) ve içinde modüle özel alanlar
görünmesi isteniyor: antivirüs tarama takvimi, yazıcı tanılaması, güncelleme
denetimi.

Burada doğrudan bir K1 tuzağı var: **çekirdek ayar ekranı "antivirüs"
kelimesini bilemez.** Sekmeleri kod içinde saymak, `km_core` içine modül adı
yazmak demektir ve `modules/` silindiğinde ekran boş sekmeler gösterir.

Elde iyi bir örnek var: KDS'nin ayar kataloğu
(`modules/bld_kds/backend/devices.py`) alanları tip, grup ve sınırlarıyla
bildirimsel olarak tanımlıyor —
`Setting("poll_seconds", "int", "Yoklama aralığı (sn)", "calisma", 3, 60)`.
Eksik olan, bunun **manifest düzeyine** çıkmış hâli.

## Karar

### 1. `module.yaml` içinde `settings:` bloğu

Modül, ayar alanlarını manifestinde ilan eder:

```yaml
settings:
  tab: "Antivirüs"              # sekme başlığı
  requires: [antivirus.manage]  # bu sekmeyi görmek/yazmak için gereken izin
  groups:
    - id: tarama
      title: "Tarama"
      fields:
        - key: schedule
          type: cron
          title: "Otomatik tarama zamanı"
          default: "0 3 * * *"
        - key: quick_paths
          type: path_list
          title: "Hızlı tarama yolları"
```

Alan tipleri KDS kataloğuyla uyumludur: `bool`, `int` (min/max), `text`
(max_length), `select`, `path`, `path_list`, `cron`. Şema
`docs/schemas/module.schema.json` içine eklenir; geçersiz `settings` bloğu
modülü düşürmez, sekmesiz yükler ve hatayı loglar (K7).

### 2. Ekran sekmeleri registry'den kurar

Çekirdek ekranı sabit sekmeleri (Genel, Yazıcı, Güncelleme, Tanılama) kendi
bilir; geri kalanları modül manifestlerinden okur. **Modül silinince sekmesi
de gider** — K6 korunur, çekirdekte tek satır değişmez.

### 3. Yazma izni alan başına ilan edilir, backend yeniden denetler

`requires` menüde gizlemek için değil, **yetkilendirme için** vardır. Ayar
yazan uç nokta aynı izni bağımsız olarak sorar (K9). İzin ilan etmeyen
`settings` bloğu reddedilir; varsayılan kapalıdır.

### 4. Arayüzden değişen ayar çekirdek deposuna yazılır

Bugünkü öncelik zinciri dosya tabanlı:

```
default.yaml → environments/<env>.yaml → local.yaml → ortam değişkeni
```

Arayüzden değişen ayarın dosyaya yazılması yanlış olurdu: `local.yaml` elle
düzenlenen, git dışı ve yorum taşıyan bir dosyadır; program onu yeniden
yazarsa yorumlar ve elle yapılmış düzenlemeler kaybolur.

Yeni zincir:

```
default.yaml → environments/<env>.yaml → local.yaml → çekirdek deposu → ortam değişkeni
```

Depo katmanı `local.yaml`'ı **ezer**. Bu yüzden ekran, bir değerin dosyadan
geldiğini ve arayüzden ezildiğini **görünür biçimde söyler**; aksi hâlde
`local.yaml`'a yazan yönetici değişikliğinin neden işlemediğini bulamaz.
Ortam değişkeni en üstte kalır: acil müdahale yolu kapanmaz.

### 5. Sır ayar değildir

Şifre, token ve anahtar bu ekrandan yazılmaz; `secrets` kasasına aittir (K8) ve
Kimlik Kasası ekranından yönetilir. `settings` bloğunda sır tipi yoktur.

## Elenen alternatifler

- **Çekirdekte sabit sekme listesi.** K1 ihlali. Ayrıca her yeni modül
  çekirdeğe dokunmayı gerektirirdi (K6 ihlali).
- **Her modülün kendi ayar ekranı.** Kullanıcı "otomatik tarama saati nerede"
  sorusunu 50 ekranda arar. Ayarın tek evi olmasının nedeni tam olarak budur.
- **Ayarları doğrudan `local.yaml`'a yazmak.** Yukarıda: elle yazılmış dosyayı
  program ezer, yorumlar gider, çakışma sessizdir.

## Sonuçlar

- `module.schema.json` genişler; mevcut manifestler geçerliliğini korur
  (`settings` isteğe bağlıdır).
- Çekirdek deposunda `settings` tablosu açılır (anahtar, değer, kim, ne zaman);
  değişiklikler denetim izine düşer.
- KDS'nin cihaz ayarları **bu ekrana taşınmaz.** Onlar Kontrol Merkezi'nin
  değil, uzaktaki kasanın ayarlarıdır ve `bld_kds` ekranında kalır — bir iş
  eyleminin tek evi olur, kısayolu bile açılmaz.
- Antivirüs sekmesi yalnız modülün yüklendiği platformda görünür (ADR 0022).
