# 0022 — Modül platform kapsamı manifestte ilan edilir

**Durum:** Kabul edildi · 2026-08-16

## Bağlam

Uygulama üç platforma açılıyor (ADR 0014) ve her modül her platformda anlamlı
değil. İlk somut örnek antivirüs: **tarama ekranı yalnız Ubuntu kurulumunda
bulunacak.**

Gerekçesi teknik olarak da sağlam: modül ClamAV'ye dayanıyor
(`clamdscan` birincil, `clamscan` yedek), tam sistem taraması kök yetkisi
istiyor ve `deploy/systemd/` altındaki ayrı bir servis birimiyle çalışıyor.
Bunların hiçbirinin macOS ve Windows'ta karşılığı yok; macOS'ta üçüncü taraf
bir tarayıcının diski okuması ayrıca kullanıcının elle vereceği bir izne bağlı.

Ama bugün bunu **söyleyecek bir yer yok**:

- `docs/schemas/module.schema.json` içinde platform alanı yok.
- `enabled` depo düzeyinde tek bir boolean — makineye göre değişmez.
- Çekirdeğin modül keşfi (ARCHITECTURE §5) platforma göre eleme bilmez.

## Karar

### 1. `module.yaml` içinde `platforms:` alanı

```yaml
platforms: [linux]        # antivirüs
```

Değerler: `linux`, `windows`, `macos`. **Alan yoksa modül her platformda
yüklenir** — mevcut manifestlerin hepsi geçerliliğini korur ve hiçbir modül bu
kararla kapanmaz.

### 2. Eleme keşif aşamasında olur

Manifest okunduğunda çalışılan platform listede değilse modül **hiç
yüklenmez**: router montajlanmaz, göç koşmaz, görev planlanmaz, menüde
görünmez. Yarı yüklü bir modül, olmayan bir modülden kötüdür.

Eleme `enabled: false` ile aynı sessizlikte olmaz: açılış günlüğü "antivirüs —
bu platformda çalışmaz (linux)" satırını yazar. Ekranını arayan yönetici
nedenini bulur.

### 3. Bu, çekirdeğe bir yetenek eklemektir — K6 ihlali değildir

K6 "modül eklemek çekirdeğe dokunmayı gerektirmez" der. Burada eklenen şey
modül değil, **çekirdeğin yeni bir eleme yeteneğidir**; bir kez yazılır,
sonrasında yeni modül eklemek yine çekirdeğe dokunmaz.

Çekirdek yine hiçbir modül adı bilmez (K1): `platforms` alanını **veri** olarak
okur, `if modül == "antivirus"` yazmaz.

### 4. Bağımlılık toplama da platforma bakar

`scripts/install-deps.sh` modül bağımlılıklarını manifestlerden topluyor.
Elenen modülün `dependencies` bloğu **toplanmaz**: Windows kurulumunda
`clamav-daemon` istemenin anlamı yok.

## Elenen alternatifler

- **Kurulum başına ayarla kapatmak** (`modules.antivirus.enabled: false`).
  Mevcut ayar katmanını kullanır, kod değişikliği istemez. Elendi: her makinede
  elle yapılır ve unutulur; unutulduğunda modül yüklenir, bağımlılığı aranır,
  ekranı görünür ve tıklandığında `clamdscan` bulunamaz — kullanıcı bunu
  "bozuk" olarak okur. Ayrıca kararın kendisi ayarda değil sözleşmede durmalı.
- **Kodun içinde `sys.platform` denetimi.** Modül yine yüklenir, göçü koşar,
  menüye girer ve yalnız çalışma anında "bu platformda yok" der. Elenmenin
  bildirimsel olmasının nedeni tam olarak budur.
- **Ayrı bir Windows/macOS depo dalı.** Kod ikiye ayrılır ve düzeltmeler iki
  yerde yapılır; bakımı imkânsızdır.

## Sonuçlar

- `docs/schemas/module.schema.json` genişler; `platforms` isteğe bağlıdır.
- `modules/antivirus/module.yaml` → `platforms: [linux]`.
- `docs/permissions.md` rol → ekran matrisinde Antivirüs satırı **platforma
  bağlı** olarak işaretlenir; izin matrisi değişmez.
- ADR 0009 (ClamAV, modül olarak) geçerliliğini korur — bu ADR onu
  değiştirmez, yalnız **nerede yükleneceğini** söyler.
- Modül platform kapsamı bir kez tanımlandığı için, ileride yalnız Windows'ta
  anlamlı bir modül (ör. Windows'a özgü bir ajan yönetimi) aynı alanla
  ilan edilir; çekirdeğe ikinci kez dokunulmaz.
