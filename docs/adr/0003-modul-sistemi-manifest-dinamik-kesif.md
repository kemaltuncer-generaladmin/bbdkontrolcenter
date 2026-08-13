# 0003 — Modül sistemi: manifest + dinamik keşif

**Durum:** Kabul edildi · 2026-08-12

## Bağlam
"Saf modüler" hedefi: yeni bir özellik eklemek çekirdeğe dokunmayı
gerektirmemeli.

## Karar
`modules/` altındaki her klasör kendi `module.yaml` manifest'ini taşır.
Çekirdek açılışta tarar, `docs/schemas/module.schema.json` ile doğrular,
bağımlılık grafiğini kurar ve topolojik sırayla `register(ctx)` çağırır.

Manifest, modülün tüm sözleşmesini ilan eder: kimlik, sürüm, SDK uyumluluğu,
`depends`, `provides`, `consumes`, olaylar, HTTP öneki, UI girişi, izinler,
ayar şeması, göç dizini.

## Gerekçe
- Modül eklemek = klasör atmak. Statik registry bunu sağlayamaz, çünkü her
  modül çekirdekte bir satır kayıt gerektirir.
- Manifest, bağımlılıkları ve yetenekleri **kod okumadan** görünür kılar;
  yükleme sırası ve eksik bağımlılık tespiti otomatikleşir.
- Şema doğrulaması hatayı yükleme anında yakalar, çalışma anına bırakmaz.

## Sonuçlar
- Geçersiz manifest yalnızca o modülü düşürür; uygulama ayağa kalkar (K7).
- Manifest şeması bir sözleşmedir; kırıcı değişikliği `sdk` sürüm aralığıyla
  yönetilir.
- Ayrı süreç + IPC izolasyonu şimdilik seçilmedi; gerekirse `provides`/`consumes`
  sözleşmesi korunarak taşıma katmanı değiştirilebilir.
