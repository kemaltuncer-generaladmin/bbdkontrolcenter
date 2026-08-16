# 0015 — Bildirilmiş ama diskte olmayan panel girişi, giriş sayılmaz

**Durum:** Kabul edildi · 2026-08-16

## Bağlam

Bir modülün ekranı `module.yaml` içindeki `ui.entry` alanıyla bildirilir.
Modüller aşama aşama kodlandığı için **manifest yazılmış ama panel dosyası
henüz yazılmamış** durum normaldir: bugün depoda `enabled: false` bekleyen
onlarca iskelet var.

Kabuk bu durumu zaten doğru anlatıyordu. `apps/desktop/shell/ui-kernel.js`
içindeki `panelUnavailable()` iki ayrı cümle kurar ve **kırmızı kullanmaz**;
yorumu gerekçesini açıkça yazıyor: bu bir arıza değil, bir eksiktir. Kırmızı
kart, kullanıcıyı olmayan bir hatayı aramaya gönderirdi.

Sorun, aynı soruya **iki yerden iki farklı yanıt** verilmesiydi:

| Yol | Davranış | Sonuç |
|---|---|---|
| Derleme anı — `tools/build-ui-registry.py` → `copy_panel()` | Dosya yoksa `None` döner, `collect_panels` `entry` alanını hiç yazmaz | `registry.json` içinde `entry` yok → kabuk GRİ "ekranı henüz yok" kartını çizer |
| Çalışma anı — `km_core/kernel/kernel.py` → `report()` | Ham manifesti (`record.manifest.ui`) varlık denetimi yapmadan döndürür | `/modules` yanıtında `entry` dolu → kabuk `import()` dener → patlar → yığın izli KIRMIZI arıza kartı |

Yani `registry.json` üzerinden açılan kabuk doğru kartı, sidecar'ın `/modules`
ucundan beslenen kabuk yanlış kartı gösteriyordu. Aynı iskelet modül, hangi
yoldan gelindiğine göre "henüz yazılmadı" ya da "bozuk" görünüyordu.

Yakın vadede bu daha da kötüleşir: `tools/build-ui-registry.py` kendi
belgesinde geçici olduğunu söylüyor — kayıt defterinin kaynağı sidecar'a
taşınınca **yalnız yanlış olan yol kalacaktı.**

## Karar

Kural çekirdeğe, tek yere yazılır:

> **Bildirilmiş ama diskte olmayan giriş, giriş değildir.**

`Kernel.report()` artık ham manifesti değil, `_reported_ui()` süzgecinden
geçmiş `ui` bloğunu döndürür: `manifest.path / ui["entry"]` bir dosya değilse
`entry` **rapor kopyasından** düşülür. Manifest nesnesine dokunulmaz; süzgeç
yalnızca dışarıya bildirilen görüntüyü düzeltir.

Böylece iki tüketici (derleme aracı ve çalışma anı raporu) tek doğru kaynağa
bağlanır ve iskelet modüller her iki yolda da aynı gri kartı gösterir.

## Gerekçe

- **K1 ihlal edilmiyor.** Kural geneldir: içinde modül adı, modül importu ya da
  modüle özel dal yoktur. "Dosya var mı?" sorusu modül bilgisi değildir.
  `modules/` klasörü tümüyle silinse `report()` yine çalışır.
- **K6 korunuyor.** Yeni modül eklemek çekirdeğe dokunmayı gerektirmiyor; tam
  tersine, bugüne kadar her yeni iskelet çekirdeğin yanlış davranışını bir kez
  daha tetikliyordu.
- **Tek doğru kaynak.** Derleme aracı bu kuralı 2026-08 itibarıyla zaten
  uyguluyordu. Kuralı çekirdeğe taşımak kopyayı azaltır; `build-ui-registry.py`
  silindiğinde davranış kaybolmaz.
- **Doğru kart, doğru iş.** Gri kart "bu ekran henüz yazılmadı" der ve
  kullanıcıyı beklemeye yönlendirir; kırmızı kart "bir şey bozuldu" der ve
  birini hata aramaya gönderir. İkisi farklı işlerdir.

## Reddedilen alternatif — hatayı kabukta yakalayıp geri düşmek

`mountPanel` içinde `import()` çağrısını `try/catch` ile sarıp hata hâlinde
`panelUnavailable()` kartına düşmek ilk bakışta daha basit görünüyor: çekirdeğe
hiç dokunulmuyor, tek dosya değişiyor.

**Reddedildi.** `import()` iki tamamen farklı sebeple patlar ve `catch` bloğu
ikisini ayırt edemez:

1. **Dosya yok** — modül henüz kodlanmadı. Arıza değil; gri kart doğrudur.
2. **Dosya var, içi bozuk** — sözdizimi hatası, eksik export, çalışma anında
   patlayan üst düzey kod. Bu **gerçek bir arızadır** ve kırmızıyı, yığın izini
   ve "birinin bakması gerek" mesajını hak eder.

İkisini tek `catch` ile aynı gri karta düşürmek, ikinci durumu **sessizce
gizlerdi.** Depoda aynı anda onlarca panel paralel yazılıyor; bir ajanın
bıraktığı sözdizimi hatası "ekranı henüz yok" diyen sakin bir kartın arkasına
saklanır ve kimse bakmazdı. Bir hata sınıfını görünmez kılmak, gösterdiği kartı
düzeltmekten pahalıdır.

Ayrıca bu alternatif belirtiyi kabukta örter, nedeni çekirdekte bırakırdı:
`/modules` yanıtı hâlâ var olmayan bir dosyayı bildiriyor olurdu ve o yanıtın
ikinci bir tüketicisi çıktığında aynı hata yeniden doğardı.

## Sonuçlar

- `Kernel.report()` içindeki `ui` alanı artık **türetilmiş** veridir, ham
  manifest değil. Manifest'in kendisine (`record.manifest.ui`) ihtiyaç duyan
  çağıran onu doğrudan okuyabilir.
- Panel dosyasını sonradan yazmak yeterlidir; manifest değişmez, çekirdek
  yeniden başlatıldığında `entry` kendiliğinden görünür.
- Dosya adı yanlış yazılmış bir `ui.entry` (`indeks.js` gibi) artık kırmızı
  hata değil, gri "ekranı henüz yok" kartı üretir. Bu kabul edilen bedeldir:
  yazım hatası ile yazılmamış dosya diskten ayırt edilemez. Belirtiyi hemen
  gösteren yer manifest kontrol listesidir (docs/module-guide.md), kırmızı kart
  değil.
- `tools/build-ui-registry.py` içindeki `copy_panel()` davranışı **doğrudur ve
  değişmez**; bu ADR onu çekirdeğe taşınan kuralın ikinci uygulaması olarak
  onaylar.
