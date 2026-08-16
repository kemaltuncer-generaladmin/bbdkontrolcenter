<#
.SYNOPSIS
    BBD Zil Ajanı'nı okulun zil bilgisayarına kurar.

.DESCRIPTION
    Yapılan üç şey:
      1. `.exe` ve yapılandırma `C:\ProgramData\BBDZil` altına yazılır.
      2. Açılışta çalışacak bir Görev Zamanlayıcı görevi kaydedilir.
      3. Kurulumun çalıştığı doğrulanır (görev durumu + günlük satırı).

    ─────────────────────────────────────────────────────────────────────────
    NEDEN "SYSTEM olarak, açılışta" DEĞİL

    İlk tasarım görevi SYSTEM hesabıyla "Bilgisayar başlatıldığında"
    çalıştıracaktı. BU SESSİZ ÇALIŞIR: SYSTEM hesabı 0 numaralı oturumda
    koşar ve Windows'un oturum yalıtımı yüzünden 0 numaralı oturumun ses
    aygıtına erişimi YOKTUR. Ajan sorunsuz çalışır, günlüğe "çaldı" yazar,
    hoparlörden hiçbir ses çıkmaz — bulunması en zor arıza türü.

    Ses için İNTERAKTİF OTURUM şarttır. Bu yüzden görev "oturum açıldığında"
    tetiklenir ve zil bilgisayarının açılışta kendiliğinden oturum açması
    gerekir (aşağıdaki -SetupAutoLogon anahtarı bunu anlatır).

.PARAMETER Token
    Köprünün cihaz belirteci (`BBD_BELL_DEVICE_TOKEN` ile aynı değer).

.PARAMETER BaseUrl
    Köprü adresi. Varsayılan: https://bbdstore.com.tr

.PARAMETER ExePath
    `bbd-zil.exe` yolu. VERİLMESİ ZORUNLU DEĞİL — yanında exe yoksa betik
    Python kipine geçer ve `agent.py` dosyasını `pythonw.exe` ile çalıştırır.

    Ajan yalnızca standart kütüphane kullanıyor (`urllib`, `winsound`, `wave`),
    bu yüzden Python kipinde `pip install` GEREKMEZ. Exe sadece paketleme
    kolaylığıdır; işlevsel fark yoktur.

.EXAMPLE
    .\install.ps1 -Token "cihaz-belirteci"

.EXAMPLE
    # Otomatik oturum açma nasıl kurulur, yalnız anlatır — değiştirmez.
    .\install.ps1 -Token "..." -SetupAutoLogon
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Token,

    [string]$BaseUrl = "https://bbdstore.com.tr",

    [string]$ExePath = "",

    [int]$PollSeconds = 3,

    [switch]$SetupAutoLogon,

    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

$TaskName = "BBD Zil Ajani"
$Root     = Join-Path $env:ProgramData "BBDZil"
$Target   = Join-Path $Root "bbd-zil.exe"
$ConfigPath = Join-Path $Root "config.json"

function Install-Python {
    <#
    .SYNOPSIS
        Python 3 yoksa kurar ve pythonw.exe yolunu doner.

    .DESCRIPTION
        Zil bilgisayarinda kimse basinda durmuyor; kurulumun "once sunu indir"
        diye durmasi, isin o gun bitmemesi demek. Bu yuzden Python otomatik
        kurulur.

        IKI YOL, bu sirayla:
          1. winget  — Windows 10 1809+ ve 11'de hazir gelir, en temizi.
          2. Dogrudan indirme — winget yoksa python.org'un resmi kurucusu
             sessiz kipte calistirilir.

        `PrependPath=1` ONEMLI: sonraki calistirmalarda pythonw.exe PATH'te
        bulunsun, yoksa gorev tanimi kirilir.

        Ajanin HICBIR ek paketi yok (yalniz standart kutuphane), bu yuzden
        kurulumdan sonra `pip install` adimi da yok.
    #>

    Write-Host "Python aranyor..."
    $pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
    if ($pyw) {
        $ver = & (Join-Path (Split-Path $pyw) "python.exe") --version 2>&1
        Write-Host "  bulundu: $pyw  ($ver)"
        return $pyw
    }

    Write-Host "  Python yok, kuruluyor (birkac dakika surebilir)..."

    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "  winget ile..."
        & winget install --id Python.Python.3.12 --source winget `
            --accept-package-agreements --accept-source-agreements `
            --silent --disable-interactivity 2>&1 | Out-Null
    }
    else {
        Write-Host "  winget yok, python.org kurucusu indiriliyor..."
        $url = "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe"
        $tmp = Join-Path $env:TEMP "python-kurucu.exe"
        try {
            # TLS 1.2: eski Windows kurulumlarinda varsayilan degil ve
            # indirme sessizce basarisiz olur.
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
        } catch {
            throw "Python indirilemedi: $_`nInternet baglantisini kontrol edin ya da python.org'dan elle kurun."
        }
        Write-Host "  kuruluyor (sessiz)..."
        Start-Process -FilePath $tmp -ArgumentList `
            "/quiet", "InstallAllUsers=1", "PrependPath=1", "Include_test=0" `
            -Wait
        Remove-Item $tmp -ErrorAction SilentlyContinue
    }

    # PATH bu oturumda guncellenmis olmayabilir; makine genelindeki degeri
    # yeniden okuyoruz.
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")

    $pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
    if (-not $pyw) {
        $pyw = @(
            "$env:ProgramFiles\Python312\pythonw.exe",
            "$env:ProgramFiles\Python313\pythonw.exe",
            "$env:LOCALAPPDATA\Programs\Python\Python312\pythonw.exe",
            "$env:LOCALAPPDATA\Programs\Python\Python313\pythonw.exe"
        ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    }
    if (-not $pyw) {
        throw "Python kuruldu ama pythonw.exe bulunamadi. Bilgisayari yeniden baslatip bu dosyayi tekrar calistirin."
    }

    Write-Host "  kuruldu: $pyw"
    return $pyw
}

function Assert-Admin {
    $identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Bu betik yonetici olarak calistirilmali (PowerShell'i 'Yonetici olarak calistir' ile acin)."
    }
}

# ---------------------------------------------------------------- kaldirma

if ($Uninstall) {
    Assert-Admin
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Gorev kaldirildi: $TaskName"
    }
    Get-Process -Name "bbd-zil" -ErrorAction SilentlyContinue | Stop-Process -Force
    Write-Host "Ajan durduruldu."
    Write-Host "Veri klasoru DURUYOR: $Root"
    Write-Host "Sesleri ve gunlugu de silmek isterseniz o klasoru elle kaldirin."
    return
}

# ------------------------------------------------------------------ kurulum

Assert-Admin

New-Item -ItemType Directory -Path $Root -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $Root "sounds") -Force | Out-Null

# Calisan HER ornek durdurulur - exe kipi VE Python kipi.
#
# Python kipinde surecin adi "pythonw", "bbd-zil" degil. Yalniz exe adina
# bakmak, eski bir ajanin eski ayarla calismaya devam etmesine ve iki ajanin
# ayni zili iki kez calmasina yol acardi. Komut satirina bakip yalnizca BIZIM
# agent.py'yi calistiran sureci durduruyoruz - makinedeki baska Python
# programlarina dokunmadan.
Get-Process -Name "bbd-zil" -ErrorAction SilentlyContinue | Stop-Process -Force
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*BBDZil*agent.py*" } |
    ForEach-Object {
        Write-Host "  eski ajan durduruluyor (PID $($_.ProcessId))"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
Start-Sleep -Milliseconds 800

# ─────────────────────────────────────────────────────────────────────────
# IKI KIP: exe varsa onu kur, yoksa Python ile calistir.
#
# Ajan yalnizca standart kutuphane kullaniyor, bu yuzden Python kipinde
# hicbir paket kurulumu gerekmez. Exe sadece "Python kurulu olmayan makine"
# icindir; islevsel fark YOKTUR.
# ─────────────────────────────────────────────────────────────────────────

if (-not $ExePath) {
    $candidate = Join-Path $PSScriptRoot "bbd-zil.exe"
    if (Test-Path $candidate) { $ExePath = $candidate }
}

if ($ExePath -and (Test-Path $ExePath)) {
    Copy-Item -Path $ExePath -Destination $Target -Force
    $RunCommand = $Target
    $RunArgs    = ""
    Write-Host "Kip: exe  ($Target)"
}
else {
    # pythonw.exe PENCERE ACMAZ — arayuzsuz calismasi gereken bir ajan icin
    # dogru olan bu. python.exe kullanilsaydi ekranda siyah bir konsol
    # penceresi durur ve birileri onu kapatirdi.
    #
    # Yoksa KURULUR: zil bilgisayarinda kimse basinda durmuyor, kurulumun
    # "once Python indir" diye durmasi isin o gun bitmemesi demek.
    $pyw = Install-Python

    $agent = Join-Path $PSScriptRoot "agent.py"
    if (-not (Test-Path $agent)) {
        throw "agent.py bulunamadi: $agent"
    }
    Copy-Item -Path $agent -Destination (Join-Path $Root "agent.py") -Force

    $RunCommand = $pyw
    $RunArgs    = "`"$(Join-Path $Root 'agent.py')`""
    Write-Host "Kip: Python  ($pyw)"
}

# Belirtec BU DOSYADA durur; gorev tanimina yazilmaz. Gorev tanimi
# `schtasks /query` ile herkese okunabilir, bu klasor ise daraltilabilir.
# BOM'SUZ YAZILIR. `Set-Content -Encoding UTF8` Windows PowerShell 5.1'de
# dosyanin basina BOM koyar; Python'un json okuyucusu ilk karakterde patlar
# ve ajan her acilista olur. PowerShell 7'de ayni komut BOM'suz yazar, yani
# hata yalnizca eski PowerShell'de gorunur - tam da okul makinelerindeki.
# .NET cagrisi her iki surumde de ayni davranir.
$json = @{
    baseUrl     = $BaseUrl.TrimEnd('/')
    token       = $Token
    pollSeconds = $PollSeconds
} | ConvertTo-Json
[System.IO.File]::WriteAllText($ConfigPath, $json, (New-Object System.Text.UTF8Encoding $false))

# ─────────────────────────────────────────────────────────────────────────
# config.json IZINLERINE DOKUNULMAZ - ve bu bilincli bir geri adim.
#
# ILK SURUM SUNU YAPIYORDU:
#     $acl.SetAccessRuleProtection($true, $false)   # kalitsal izinleri SIL
#     ... sadece Administrators + SYSTEM + $env:USERDOMAIN\$env:USERNAME
#
# CANLIDA KIRDI. Gorev "oturum acildiginda" tetikleniyor ve calisan kimlik
# her zaman kurulumu yapan `$env:USERDOMAIN\$env:USERNAME` ile ayni cikmiyor:
# Microsoft hesabinda kullanici adi farkli yazilir, etki alani adi farkli
# cozulur, yukseltilmemis oturumda belirtec farklidir. Bu durumda ajan KENDI
# ayar dosyasini okuyamiyor ve tek bir sorgu yapip oluyor - hicbir hata da
# gostermeden, cunku sorun ajanin kodunda degil dosya izninde.
#
# TAKAS: varsayilan ProgramData izinleriyle makinedeki diger yerel
# kullanicilar da bu dosyayi okuyabilir. Kabul ediyoruz, cunku:
#   · bu makine yalniz zil calmak icin duruyor, uzerinde baska kullanici yok,
#   · icindeki belirtec YALNIZ CIHAZ rolu - sorgular, ses indirir, bildirir;
#     komut YAZAMAZ. Ele gecirse en fazla zil seslerini indirir.
#   · calismayan bir zil, okunabilir bir belirtecten daha buyuk bir zarar.
#
# Belirteci daraltmak gerekirse dogru yol dosya izni degil, koprude cihaz
# belirtecini degistirmektir (BBD_BELL_DEVICE_TOKEN).
# ─────────────────────────────────────────────────────────────────────────

# Onceki bir kurulum sikilastirdiysa geri al: yukseltme yapan makinede de
# ajan calisabilmeli.
icacls "$ConfigPath" /reset 2>&1 | Out-Null

Write-Host "Dosyalar yazildi: $Root"

# ------------------------------------------------------------------- gorev

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# TETIKLEYICI: "oturum acildiginda". "Bilgisayar baslatildiginda" DEGIL —
# betik basindaki oturum yalitimi notuna bakin: 0 numarali oturumda ses cikmaz.
$trigger = New-ScheduledTaskTrigger -AtLogOn

if ($RunArgs) {
    $action = New-ScheduledTaskAction -Execute $RunCommand -Argument $RunArgs
} else {
    $action = New-ScheduledTaskAction -Execute $RunCommand
}

# Interaktif oturumda, oturumu acan kullanicinin haklariyla.
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $TaskName `
    -Trigger $trigger `
    -Action $action `
    -Principal $principal `
    -Settings $settings `
    -Description "Okul zil sistemi ajani. Kontrol Merkezi'nden gelen sesleri hoparlorden calar." `
    | Out-Null

Write-Host "Gorev kaydedildi: $TaskName (oturum acildiginda)"

Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 4

# --------------------------------------------------------------- dogrulama

$state = (Get-ScheduledTask -TaskName $TaskName).State
Write-Host "Gorev durumu: $state"

$logPath = Join-Path $Root "agent.log"
if (Test-Path $logPath) {
    Write-Host "`n--- agent.log (son 10 satir) ---"
    Get-Content $logPath -Tail 10
} else {
    Write-Warning "agent.log olusmadi. Ajan baslamamis olabilir."
    Write-Host   "Elle denemek icin:  $RunCommand $RunArgs"
}

# ------------------------------------------------------- otomatik oturum

if ($SetupAutoLogon) {
    Write-Host @"

============================================================================
OTOMATIK OTURUM ACMA — ELLE YAPILMASI GEREKEN ADIM
============================================================================

Zil sesinin duyulabilmesi icin makinede bir INTERAKTIF OTURUM acik olmali.
Elektrik kesintisinden sonra kimse gelip sifre girmeyecsegi icin, zil
bilgisayari acilista kendiliginden oturum acmali.

Bu betik parolayi kayit defterine yazMAZ (orada duz metin durur). Iki
guvenli yoldan birini secin:

  1) Sysinternals AutoLogon (onerilen — parolayi LSA kasasinda saklar):
       https://learn.microsoft.com/sysinternals/downloads/autologon
       Autologon64.exe ile kullanici/parola girin.

  2) netplwiz:
       Win+R -> netplwiz -> kullaniciyi secin ->
       "Kullanicilar bu bilgisayari kullanmak icin ... girmelidir" isaretini
       kaldirin -> parolayi iki kez girin.

Kurulumdan sonra makineyi yeniden baslatin ve SIFRE GIRMEDEN zilin
caldigini dogrulayin (Kontrol Merkezi > Zil Sistemi > "Simdi zil cal").

Ek not: bu bilgisayarin uyku ve ekran kapanma ayarlari KAPALI olmali —
uyuyan makine zil calmaz.
   powercfg /change standby-timeout-ac 0
   powercfg /change hibernate-timeout-ac 0
============================================================================
"@
}

Write-Host "`nKurulum bitti."
Write-Host "Calisan komut: $RunCommand $RunArgs"
Write-Host "Gunluk: $logPath"
Write-Host "Sesler: $(Join-Path $Root 'sounds')"
Write-Host "Kaldirmak icin: .\install.ps1 -Token x -Uninstall"
