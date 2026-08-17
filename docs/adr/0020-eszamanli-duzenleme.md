# 0020 — Eşzamanlı düzenleme: iyimser kilit zorunlu, danışma kilidi uyarı

**Durum:** Kabul edildi · 2026-08-16

## Bağlam

Bugün tek kurulum, tek kullanıcı var. Çok kurulum gelince (ADR 0021) aynı kaydı
iki kişi aynı anda açabilecek ve bugünkü davranış **son yazan kazanır**: ikinci
kaydetme birincinin değişikliğini sessizce siler. Kimse fark etmez.

Bu, veri kaybının en sinsi biçimidir — hata yok, uyarı yok, yalnız eksilen bir
alan.

## Karar

### 1. İyimser kilit zorunludur

Düzenlenebilir her kayıt `revision` (tam sayı) taşır. Kaydetme isteği
**beklediği revizyonu** gönderir:

```
PUT /api/users/<id>   { "expected_revision": 7, ... }
  → revision == 7 ise yazılır, revision 8 olur
  → değilse 409 CONFLICT
```

409 yanıtı boş dönmez; **kimin, ne zaman değiştirdiğini** söyler:

> Bu kaydı Ayşe Yılmaz 2 dakika önce değiştirdi. Yenileyip tekrar deneyin.

Ekran kullanıcının yazdıklarını atmaz; çakışan alanları gösterir ve seçim
bırakır. Yazdığı yarım sayfayı kaybeden kullanıcı, ikinci kez yazmaz.

### 2. Danışma kilidi uyarır, engellemez

Kullanıcı düzenlemeye başlayınca merkezde **TTL'li** (varsayılan 2 dakika,
ekran açık kaldıkça yenilenir) bir kilit oluşur. İkinci kişi kaydı açtığında
uyarıyı görür ve ekran salt okunur açılır; "yine de düzenle" seçeneği vardır.

**Kilit yazmayı engellemez.** Nedeni K7: kilit servisi ya da ağ düştüğünde iş
durmamalıdır. Doğruluğu iyimser kilit sağlar; danışma kilidi yalnız boşa emeği
önler.

TTL zorunludur — süresiz kilit, çöken bir istemcinin kaydı sonsuza kapatması
demektir ve bunu açmanın yolu yoktur.

### 3. Kapsam: yalnız Kontrol Merkezi'nin kendi verisi

Bu koruma `km_core/store` ve modüllerin **kendi** tablolarındaki kayıtlar için
geçerlidir: kullanıcılar, roller, ayarlar, zil planı, çıktı kayıtları.

**Uzak sistemler kapsam dışıdır.** İki kurulum aynı anda aynı Bagisto siparişini
ya da BLD ürününü düzenliyorsa, çakışmayı ancak o sistemin kendi revizyon alanı
çözer; buradaki kilit oraya ulaşmaz. `store_api` ve `bld_api` geçitleri, uzak
sistem revizyon/etag veriyorsa onu **taşımakla** yükümlüdür; vermiyorsa ekran
bunu bilir ve "bu ekranda eşzamanlılık koruması yoktur" der.

Sessizce korunuyormuş gibi davranmak, hiç korumamaktan kötüdür.

### 4. Çevrimdışı yazma yoktur

Kurulum çevrimdışıyken **okur ve giriş yapar** (ADR 0021), ama merkezî veriye
yazmaz. Böylece iki kurulumun çevrimdışı yaptığı değişiklikleri birleştirme
sorunu hiç doğmaz — çözülmesi en pahalı sınıf budur ve baştan kapatılır.

## Elenen alternatifler

- **Kötümser kilit (kayıt açıldığında kilitle, kapanınca bırak).** Çöken
  istemci, kapatılan dizüstü ve kopan ağ kaydı kilitli bırakır; kilidi açmak
  için yönetici müdahalesi gerekir. TTL eklenirse zaten danışma kilidine dönüşür.
- **WebSocket ile gerçek zamanlı ortak düzenleme.** Bu ölçekte (bir avuç
  kurulum, seyrek çakışma) taşıdığı karmaşıklığın karşılığı yok.
- **Hiçbir şey yapmamak, "dikkat edilir" demek.** Bugün tek kullanıcı olduğu
  için sorun görünmüyor; ikinci kurulumun ertesi günü görünür ve o gün ilk
  kaybedilen veriyle öğrenilir.

## Sonuçlar

- Çekirdek ve modül tablolarına `revision` sütunu eklenir; göç yazılır.
- `km_core/http` tekdüze bir 409 hata biçimi tanımlar ve `shell/ui-kit/form.js`
  onu tek yerde ele alır — yirmi panelde ayrı ayrı yazılmaz.
- Danışma kilitleri merkezî serviste tutulur (ADR 0021); servis yoksa yalnız
  iyimser kilit çalışır ve **sistem tam olarak bugünkü kadar güvenlidir** —
  hiçbir gerileme olmaz.
