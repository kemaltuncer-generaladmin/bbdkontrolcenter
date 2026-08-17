# 0014 — Çok platformlu baskı: Linux sessiz, Windows/macOS sistem penceresi

**Durum:** Kabul edildi · 2026-08-16

## Bağlam

Uygulama bugüne dek tek bir Ubuntu makinesinde çalıştı ve
`km_platform/printer/cups.py` bütünüyle o makineye göre yazıldı: `lp`, `lpstat`,
`ipptool` komutları, `ipp-usb` tuzağının çözümü, hedef kuyruğun otomatik
seçilmesi.

Artık Windows ve macOS kurulumları gelecek. O makinelerde yazıcı bizim
bulacağımız bir kuyruk değil, **kullanıcının kendi seçtiği cihaz** (Epson vb.).

Zemin üç platformda farklı:

| Platform | CUPS | ipp-usb | PDF basan yerleşik komut |
|---|---|---|---|
| Linux | var | var | `lp` |
| macOS | var | yok | `lp` |
| Windows | **yok** | yok | **yok** |

Değişimin sığdığı yer dar: `printer` yeteneğini **22 modül** tüketiyor ve
hiçbiri `lp` çağırmıyor (K4 — tek kapı). Bu ADR o kapının arkasını değiştirir,
kapının kendisini değil.

## Karar

### 1. `printer` yeteneği tektir; arka ucu platform seçer

```
km_platform/printer/
  service.py           ortak sözleşme (discover / target / print / status / health)
  backends/cups.py     Linux — bugünkü kod
  backends/system.py   Windows + macOS — işletim sistemine devir
```

`km_core/http/app.py` içindeki tek kayıt satırı bir fabrikaya döner ve
`sys.platform`'a bakar. **`km_sdk` ve modüller değişmez** — K1, K2 ve K4
olduğu gibi korunur.

### 2. Linux'ta sessiz baskı, Windows/macOS'ta sistem yazdırma penceresi

| Platform | Davranış |
|---|---|
| Linux | Bugünkü davranış aynen: hedef kuyruk otomatik seçilir, `lp` ile basılır, kullanıcıya soru sorulmaz |
| Windows / macOS | `print_file` kâğıda basmaz; `{mode: "system", path}` döner. Kabuk PDF'i webview'de açar ve `window.print()` çağırır |

Yazıcı seçimi, kopya sayısı ve kâğıt boyutu **işletim sisteminin kendi
penceresinde** yapılır. Uygulama bir yazıcı listesi çizmez, bir seçim ekranı
tutmaz: kullanıcı zaten tanıdığı pencereyi görür ve orada Epson'unu seçer.

### 3. Neden webview, neden `ShellExecute` değil

- Windows'ta `ShellExecute`'un `print` fiili **diyalog açmaz**; belgeyi
  varsayılan yazıcıya gönderir. Kullanıcı ne seçim yapabilir ne de nereye
  gittiğini görür — istenenin tam tersi.
- macOS'ta `lp` çalışır ama o da seçimsizdir; `open -a Preview` ise kullanıcının
  elle Cmd+P demesini bekler, yani akış yarım kalır.
- WebView2 (Windows) ve WKWebView (macOS) PDF'i **yerleşik olarak** render eder
  ve `window.print()` işletim sisteminin kendi yazdırma penceresini açar. Ek
  bağımlılık, ek ikili, ek eklenti yoktur.
- WebKitGTK (Linux) PDF render **etmez**. Bu yol Linux'ta zaten yoktur; orada
  sessiz baskı isteniyor.

### 4. `media` / `PageSize` zorlaması yalnız Linux'ta geçerlidir

`lp` çağrısında kâğıt boyutunun iki adla birden söylenmesi, kullanıcının
`~/.cups/lpoptions` dosyasındaki `PageSize=A6` yüzünden yaşanan sessiz
arızanın çözümüydü ve Linux arka ucunda **korunur**.

Sistem penceresinde bu zorlama ne gereklidir ne de doğrudur: kâğıdı kullanıcı
seçer, seçimini uygulamanın ezmesi beklenmez.

### 5. Yazıcı durumu ve toner yalnız Linux'ta okunur

`device_info()` ve `health()` `ipp-usb` üzerinden cihazın kendisine sorar; bu
yol yalnız Linux'ta vardır. Windows/macOS'ta `status()` boş dönmez, **ne
olduğunu söyler**: yazıcı durumu bu platformda işletim sistemine aittir.

Kaybedilen somut şey: "Toner %10" uyarısı ve baskı öncesi `ready` denetimi.
Ekranda uydurulmaz.

### 6. Otonom (zamanlanmış) baskı yalnız Linux'ta mümkündür

Sistem penceresi bir insanın onayını ister; başında kimse olmayan bir iş onu
açamaz. Bugün zamanlanmış baskı işi yazılmış değil. Yazıldığında Windows/macOS
üzerinde **sessizce başarısız olmaz**: iş "atlandı — bu platformda otonom baskı
yok" gerekçesiyle günlüğe düşer (K7).

## Elenen alternatifler

- **Windows'ta `win32print` ile sessiz baskı.** `pywin32` bağımlılığı gelir ve
  asıl sorunu çözmez: Windows'ta PDF basan yerleşik bir yol olmadığı için ya
  harici bir araç gömülür ya da pdfium ile render edilip Win32 print DC'ye
  çizilir. Üstelik kullanıcının yazıcı seçme isteğini karşılamaz. Otonom baskı
  gerçekten gerekirse bu karar yeniden değerlendirilir.
- **macOS'u CUPS'ta bırakmak.** Teknik olarak çalışırdı (`lp`/`lpstat` orada
  var) ve toner/durum bilgisi de korunurdu. Elendi: aynı işlem iki platformda
  pencere açar, birinde sessizce basardı. Davranışın platforma göre bölünmesi,
  kazanılan durum bilgisinden pahalıdır.
- **Tauri yazdırma eklentisi.** `window.print()` webview'ın kendi yeteneğidir;
  eklenti kabuk sürümüne bağımlılık ekler, karşılığında bir şey vermez.

## Sonuçlar

- **`cups.py` silinmez, Linux arka ucu olur.** İçindeki `ipp-usb` tuzağı dersi
  (iş "tamamlandı" görünür, kâğıt çıkmaz) o dosyada kalır. O bilgi saatlerce
  kovalanarak kazanıldı; taşınma sırasında kaybedilmez.
- **`ui-kit/report.js` başlığındaki varsayım düşer.** Orada "Tauri'de yazdırma
  eklentisi yok; panel yalnız yolu gösterir" yazıyor. `POST /print` artık iki
  biçimde yanıt döner ve kit ikisini de bilmek zorundadır.
- **`default_printer` ve `usb_match` ayarları Linux'a özgüleşir.**
  Windows/macOS'ta yok sayılır; ayar şeması bunu söyler.
- **Windows/macOS'ta `{ok: true}` "pencere açıldı" demektir, "basıldı" değil.**
  Kullanıcı pencereyi iptal edebilir ve uygulama bunu bilemez. Baskı günlüğü bu
  farkı açıkça yazar — aynı dosyanın en başında duran ders budur:
  *"tamamlandı" kâğıt çıktığı anlamına gelmez.*
- 22 modülün hiçbirinde tek satır değişmez. Bu ADR'nin uygulanabilir olması
  K4'ün (tek kapı) doğrudan getirisidir.
