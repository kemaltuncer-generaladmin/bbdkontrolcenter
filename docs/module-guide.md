# Modül Yazma Kılavuzu

## Önce: bu gerçekten modül mü?

| Soru | Evet ise |
|---|---|
| Klasörü silsem özellik tümüyle gider mi? | Modül |
| Silinemez, birden çok özelliğin ortak zemini mi? | **Platform yeteneği** — `km_platform/` altına, `modules/` altına değil |

Modül örnekleri: BLD ürün yönetimi, zil sistemi, baskı yönetimi, sunucu izleme.
Modül **olmayan** örnekler: `ssh`, `database`, `printer`, `audio` — bunlar
altyapıdır (ADR 0006).

## Adımlar

1. `tools/module-template/` klasörünü `modules/<id>/` olarak kopyala.
   `<id>` küçük harf + alt çizgi; klasör adı `module.yaml` içindeki `id` ile
   birebir aynı olmak zorunda.
2. `module.yaml` doldur. Şema: `docs/schemas/module.schema.json`.
3. `backend/module.py` içine `register(ctx)` yaz — modülün tek giriş noktası.
4. Panelini `ui/panel/`, ayarını `config/`, göçlerini `backend/migrations/`,
   testlerini `tests/` altına koy.
5. `enabled: true` yap. Çekirdekte hiçbir dosyaya dokunma.

## `register(ctx)` içinde ne yapılır

Modül burada kendini **bildirir**; iş yapmaz, ağa çıkmaz, DB'ye yazmaz.

- Servislerini registry'ye kaydeder (`provides` ile ilan edilenler)
- İhtiyaç duyduğu yetenekleri registry'den çözer (`consumes` ile ilanlı)
- Router'ını, olay dinleyicilerini ve görevlerini bildirir

Ağır başlatma işi `start` aşamasına bırakılır. Yaşam döngüsü:
`load → setup → start → stop`.

## Yasaklar

- `from km_core...` / `from km_platform...` — yalnızca `km_sdk` (K2)
- `from modules.<başka_modül>...` — registry veya olay kullan (K3)
- Ham `asyncssh` / `paramiko` / DB sürücüsü çağrısı — platform yeteneğinden
  geçir (K4)
- Başka modülün tablosuna erişim (K5)
- Başka modülün HTTP önekini çağırmak (`/api/<başka>` — panelde de) (K12)
- Başka modülde zaten olan bir yardımcıyı kopyalamak — `km_sdk`'ye taşı
  (ADR 0033 §7)
- Depoya sır yazmak (K8)

## Başka modülün verisine ihtiyacım var

Import etme. İki yol var:

- **Senkron ihtiyaç:** o modül yeteneği `provides` ile ilan etsin, sen
  `consumes` ile iste, registry'den çöz. Bulunamazsa `optional: true` ise
  modülün kısıtlı çalışır, değilse devre dışı bırakılır.
- **Asenkron bildirim:** olay veri yolu. Yayınlayan dinleyeni bilmez.

## Manifest kontrol listesi

- [ ] `id` klasör adıyla aynı
- [ ] `domain` katalogda (`docs/architecture/domains.yaml`) ve
      `data_classification` alanın üst sınırını aşmıyor (ADR 0033)
- [ ] Sağlanan yetenek ve yayımlanan olay adı modül kimliği ya da alanın
      önekiyle başlıyor
- [ ] `python3 tools/check-boundaries.py` geçiyor (yeni sınır ihlali yok)
- [ ] `sdk` aralığı doğru
- [ ] `consumes` içindeki her yeteneğin `reason`'ı yazılı
- [ ] `provides` içindeki her yeteneğin sözleşmesi (`contract`) belirtilmiş
- [ ] `http.prefix` `/api/<id>` biçiminde ve başka modülle çakışmıyor
- [ ] `permissions` tanımlı — yetkilendirme çekirdekte, tanım modülde
- [ ] `config/schema.json` yazılmış; şemasız ayar kabul edilmez
- [ ] Göç dizini modülün kendi tablolarını oluşturuyor

## Büyük tabloya indeks (canlı veri)

Göçler sunucuda açılışta, PostgreSQL'de bir işlem (transaction) içinde koşar.
Dolu bir tabloya indeks açan göç tabloyu yazmaya kapatmamalıdır; bunun için
indeks AYRI bir göç dosyasına konur ve dosyanın başına işaret yazılır:

```sql
-- km: concurrently
-- Geri alma: DROP INDEX CONCURRENTLY IF EXISTS mod_<id>_<ad>;
CREATE INDEX IF NOT EXISTS mod_<id>_<ad> ON mod_<id>_<tablo> (esitlik_sutunu, sira_sutunu);
```

Merkez (`PostgresStore`) bu dosyadaki her indeksi işlem dışında
`CREATE INDEX CONCURRENTLY` ile açar (okuma ve yazma sürer); yarıda kalmış
geçersiz bir indeks bulursa önce kaldırır. Yerel SQLite dosyayı düz göç gibi
uygular. Böyle bir dosya yalnız `CREATE [UNIQUE] INDEX IF NOT EXISTS` içerebilir —
tablo açmak ya da veri değiştirmek normal (işlemli) göçte kalır. Göç sistemi
ileriye doğrudur; geri alma ifadesi dosyada yorum olarak durur.
