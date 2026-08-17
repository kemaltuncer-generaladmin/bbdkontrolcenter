# 0024 — macOS kod imzası ve notarization

**Durum:** Kabul edildi · 2026-08-17
**İlgili:** [ADR 0023](0023-paketleme-ve-veri-dizini.md) §5'i genişletir (geçersiz kılmaz)

## Bağlam

ADR 0023 §5 "Windows kurucusu imzalanmaz" diyor ve gerekçesi tek cümleydi:
**kod imzalama sertifikası alınmadı.** Karar o günün gerçeğine dayanıyordu,
bir ilkeye değil.

İki şey değişti:

1. **Apple Developer hesabı alındı.** Yani macOS tarafında imzalamanın önündeki
   tek engel kalktı.
2. **macOS'ta imzasızlık bir uyarı değil, DUVAR.** Windows'ta SmartScreen
   "yine de çalıştır" seçeneği sunar; macOS Gatekeeper imzasız bir `.dmg`
   içeriğini **"bu uygulama hasar görmüş"** diyerek açtırmaz. Kullanıcı
   uygulamanın bozuk olduğunu sanır — mesaj imzadan hiç söz etmez. Tek çare
   `xattr -cr` ile karantina özniteliğini elle silmektir ve bunu her kurulumda
   her kullanıcıya yaptırmak gerçekçi değildir.

Bu ikinci madde ADR 0023 yazılırken ölçülmemişti: §5 yalnız Windows'u konuşuyor
ve macOS imzalaması hiç karara bağlanmamıştı.

## Karar

### 1. macOS paketleri imzalanır ve notarize edilir

Derleme, ortam değişkenleri tanımlıysa `Developer ID Application` kimliğiyle
imzalar ve Apple'a notarization'a gönderir. Değişkenler:

| Değişken | Ne için |
|---|---|
| `APPLE_SIGNING_IDENTITY` | imzalayan kimlik (`Developer ID Application: … (TEAM)`) |
| `APPLE_CERTIFICATE` · `APPLE_CERTIFICATE_PASSWORD` | CI'da anahtarlığa kurulacak `.p12` (base64) |
| `APPLE_ID` · `APPLE_PASSWORD` · `APPLE_TEAM_ID` | notarization (uygulamaya özel parola) |

### 2. Sertifika depoda durmaz (K8)

GitHub Actions'ta Secrets'tan gelir, yerel derlemede geliştiricinin kendi
kabuğundan. `.p12` dosyası ve parolası depoya, `config/local.yaml`a ya da
`şifre env/` dışına **hiçbir biçimde yazılmaz.**

### 3. Değişken yoksa derleme DÜŞMEZ, imzasız üretir

Tauri bu değişkenler boşken imzalamayı denemez. Böylece sertifikası olmayan
bir makine de paket üretebilir; yalnız o paket Gatekeeper duvarına takılır ve
bunu bilerek üretmiş olur. Derlemeyi düşürmek, sertifikası olmayan herkesi
paket üretemez hâle getirirdi.

### 4. Windows İMZASIZ KALMAYA DEVAM EDER

**Apple Developer hesabı Windows ikililerini imzalayamaz.** Authenticode ayrı
bir sertifika türüdür ve ayrı bir CA'dan (DigiCert, Sectigo…) alınır. ADR 0023
§5 Windows için olduğu gibi geçerlidir: kurulum kendi şirket makinelerine
yapılıyor, SmartScreen'in nasıl geçileceği `deploy/README.md` içinde yazılı.

Bu ayrımın yazılmasının sebebi, "artık sertifikamız var" cümlesinin iki
platformu birden kapsadığının sanılmasıdır. Kapsamıyor.

### 5. Güncelleme imzası bundan AYRI bir şeydir

`TAURI_SIGNING_PRIVATE_KEY` güncelleyicinin indirdiği paketi doğrulaması
içindir ve minisign anahtarıdır. Apple imzası işletim sisteminin uygulamayı
açmasıyla ilgilidir. İkisi farklı anahtarlar, farklı amaçlar; biri ötekinin
yerine geçmez. İkisi de gereklidir.

## Sonuçlar

- macOS kurulumu artık "hasar görmüş" demez; kullanıcı `xattr` çalıştırmaz.
- Notarization derlemeye dakikalar ekler (Apple'ın kuyruğu) ve **internet
  ister**. Çevrimdışı bir makinede `--bundles dmg` imzasız üretir.
- Sertifikanın süresi dolarsa derleme imzasız paket üretmeye devam eder ve
  bunu kimse fark etmez. Süre takibi elle bir iştir; otomatik uyarı yoktur.

## Elenen alternatifler

- **`xattr -cr` talimatını belgeye yazıp geçmek.** Bugünkü durum bu ve
  çalışmıyor: mesaj "hasar görmüş" dediği için kullanıcı belgeye değil, paketin
  bozuk olduğu sonucuna varıyor.
- **Ad-hoc imza (`codesign -s -`).** Karantinayı kaldırmaz; Gatekeeper
  `Developer ID` ister. Yalnız yerel geliştirmede işe yarar.
- **Uygulamayı Mac App Store'dan dağıtmak.** Sandbox kısıtları SSH, yerel
  yazıcı ve rastgele dosya erişimiyle bağdaşmıyor.
