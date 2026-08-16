# BBD Zil Ajanı

Okulun zil sistemine bağlı Windows bilgisayarında çalışan, **arayüzsüz** küçük
bir program. Tek işi: köprüyü sorgulamak, ses dosyalarını yerelinde taze tutmak
ve söylendiği anda hoparlörden çalmak. Hiçbir kararı kendisi vermez.

```
Kontrol Merkezi ──(komut + sesler)──▶ bbdstore /api/bell ──▶ ZİL AJANI ──▶ 🔊
   (Linux, ofis)                        (köprü)              (Windows)
```

## Neden bu makinede bir program var

Kontrol Merkezi ile zil bilgisayarı aynı ağda değil ve ikisinin de sabit adresi
yok. Aradaki tek ortak nokta zaten ayakta duran mağaza sunucusu. Ajan oraya üç
saniyede bir uğrar.

## Kurulum

```powershell
# 1. Derle (geliştirici makinesinde, Windows'ta)
pip install pyinstaller
pyinstaller build.spec          # → dist/bbd-zil.exe

# 2. Zil bilgisayarına kur (YÖNETİCİ PowerShell)
.\install.ps1 -Token "<cihaz-belirteci>" -SetupAutoLogon
```

`<cihaz-belirteci>` sunucudaki `BBD_BELL_DEVICE_TOKEN` ile **aynı** değerdir.

### ⚠ Otomatik oturum açma zorunlu

Windows'ta **0 numaralı oturumun ses aygıtına erişimi yoktur.** Ajan SYSTEM
hesabıyla "bilgisayar başlatıldığında" çalıştırılırsa sorunsuz koşar, günlüğe
"çaldı" yazar ve **hoparlörden hiçbir ses çıkmaz.** Bulunması en zor arıza türü.

Bu yüzden görev "oturum açıldığında" tetiklenir ve makinenin elektrik
kesintisinden sonra kimseyi beklemeden oturum açması gerekir. `-SetupAutoLogon`
anahtarı bunun nasıl yapılacağını yazdırır (parolayı kayıt defterine yazmaz).

Uyku da kapatılmalı — uyuyan makine zil çalmaz:

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
```

## Dosyalar

```
C:\ProgramData\BBDZil\
├── bbd-zil.exe        program
├── config.json        köprü adresi + cihaz belirteci (yalnız yönetici okur)
├── agent.log          tek görünen iz; 1 MB'de döner, 3 kopya saklanır
├── sounds\            <özet>.wav — köprüden inen sesler
└── cache\             ses düzeyi uygulanmış kopyalar
```

## Nasıl çalışıyor

**Sesler önceden iner.** Her sorgulama yanıtı, komutların yanında "sende
bulunması gereken sesler" listesini taşır. Ajan arka planda eksikleri indirir,
listede olmayanı siler. Komut geldiğinde indirme yapılmaz — dosya zaten
diskte, yalnız açılıp çalınır.

**Ses değişince kendiliğinden güncellenir.** Dosya adı içeriğinin özetidir.
Zil sesini değiştirdiğinde ya da bir grubun adı değişip anonsu yeniden
üretildiğinde özet değişir; ajan bir sonraki turda (≤3 sn) yenisini indirir,
eskisini atar. `.exe` yeniden kurulmaz.

**İndirilen doğrulanır.** Özet tutmazsa dosya diske hiç yazılmaz. Yarım inen
bir dosyanın "ses var" görünüp çalma anında patlaması engellenir.

**Saniye hassasiyeti.** Komut `playAt` damgası taşır ve zil saatinden bir
dakika önce yazılır. Ajan komutu erkenden alır, kendi saatinde bekler, tam
vaktinde çalar. Sorgulama gecikmesi zile yansımaz.

**Geç kalan zil çalınmaz.** Zamanı 20 saniyeden fazla geçmiş komut atlanır ve
nedeni bildirilir. Uykudan geç uyanan makine sabahki bütün zilleri arka arkaya
çalmamalı.

**Ses düzeyi dosyada uygulanır.** `winsound` düzey bilmez ve Windows'un ana
ses ayarını değiştirmek makinenin geri kalanını etkilerdi. Örnekler
ölçeklenip önbelleğe alınır.

## Sorun giderme

| Belirti | Bakılacak yer |
|---|---|
| Hiç ses çıkmıyor, günlükte "çaldı" yazıyor | Oturum açık mı? 0 numaralı oturumda ses çıkmaz (yukarı bakın) |
| `agent.log` oluşmadı | `bbd-zil.exe`'yi elle çalıştırın; hata konsola düşer |
| "köprüye ulaşılamıyor" | İnternet, `config.json` içindeki `baseUrl`, sunucuda `BBD_BELL_ENABLED=true` |
| 401 | `config.json` belirteci ile `BBD_BELL_DEVICE_TOKEN` aynı mı |
| 503 | Köprü kapalı — sunucuda `BBD_BELL_ENABLED` ve `config:cache` |
| Kontrol Merkezi "ajan görünmüyor" diyor | Görev çalışıyor mu: `Get-ScheduledTask "BBD Zil Ajani"` |
| Ses eksik kalıyor | `sounds\` klasörünü silin; ajan hepsini yeniden indirir |

Elle çalıştırıp izlemek (görev durdurulmuş olmalı):

```powershell
Stop-ScheduledTask -TaskName "BBD Zil Ajani"
& "$env:ProgramData\BBDZil\bbd-zil.exe"      # konsola da yazar
```

## Kaldırma

```powershell
.\install.ps1 -Token x -Uninstall
```

Görev ve süreç kaldırılır; veri klasörü **durur** (günlük tanı için gerekebilir).
