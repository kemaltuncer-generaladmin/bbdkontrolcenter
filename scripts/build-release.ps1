<#
.SYNOPSIS
    Kontrol Merkezi — Windows kurucusu (.exe / .msi) üretimi. ADR 0023.

.DESCRIPTION
    Adımlar:
      1. Gömülü Python çalışma zamanını indirir ve bağımlılıklarını kurar
      2. Kabuk menü kaydını üretir (registry.json ikiliye gömülür)
      3. `cargo tauri build` ile NSIS kurucusunu ve MSI paketini üretir
      4. Çıktıyı dist\ altına toplar (paket + `.sig` imzası + güncelleme künyesi)

    ÇAPRAZ DERLEME YOKTUR: bu betik Windows üzerinde koşar. Linux/macOS için
    scripts/build-release.sh kullanılır.

    ÖN KOŞULLAR (elle kurulur, betik kurmaz):
      - Rust (rustup) + MSVC derleme araçları
      - Tauri CLI:  cargo install tauri-cli --version "^2" --locked
      - WebView2 Runtime (Windows 11'de yerleşiktir)
      - MSI hedefi için WiX'i Tauri CLI kendisi indirir

    Kurucu İMZASIZDIR: SmartScreen davranışı için deploy/README.md.

.PARAMETER SkipRuntime
    Var olan runtime\ klasörünü yeniden indirmez.

.PARAMETER RuntimeOnly
    Yalnız gömülü çalışma zamanını hazırlar, paket üretmez.

.PARAMETER Bundles
    Üretilecek hedefler, virgülle: nsis, msi. Boşsa yapılandırmadaki hepsi.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build-release.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build-release.ps1 -Bundles nsis
#>

[CmdletBinding()]
param(
    [switch]$SkipRuntime,
    [switch]$RuntimeOnly,
    [string]$Bundles = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest'in ilerleme çubuğu indirmeyi yavaşlatır

# --- kodlama --------------------------------------------------------------
#
# WINDOWS KONSOLU cp1252'DIR VE TURKCE CIKTIYI KODLAYAMAZ. Cagrilan Python'un
# stdout'u varsayilan olarak o kod sayfasini kullanir; `tools/build-ui-registry.py`
# menu kaydini basarken UnicodeEncodeError atip 1 ile dondu ve derleme
# "Menu kaydi uretilemedi" diyerek dustu (run 32038408984).
#
# Kok neden kodlamadir, tek bir karakter degil: `→` sadelestirilseydi siradaki
# Turkce ileti ayni yerde patlardi. Bu yuzden karakter degil KODLAMA sabitlenir.
# Betik kendi ciktisini de UTF-8'e zorluyor; burasi cocuk sureclerin tamami
# icin ayni guvenceyi verir, is akisi disinda elle calistirildiginda da.
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
# PowerShell'in yerel komutlarin ciktisini UTF-8 cozmesi icin. Konsolu olmayan
# barindiricilarda atilabilir; basarisiz olmasi derlemeyi durdurmamali.
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch { }

$Root    = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Tauri   = Join-Path $Root "apps\desktop\src-tauri"
$Dist    = Join-Path $Root "dist"

# --- gomulu calisma zamaninin YERI ----------------------------------------
#
# IKI AD, IKI IS. $Runtime pakete kopyalanan KOKtur (tauri.release.json ->
# "runtime": "runtime"); $RuntimePy yorumlayicinin kendisinin durdugu
# klasordur ve kokun ALTINDA bir basamak daha iceridedir.
#
# Fazladan gorunen bu basamak keyfi degil, ADR 0023 §1'in yazdigi yoldur ve
# main.rs (python_path) tam olarak orayi arar:
#
#     runtime\python\python.exe        (Windows)
#     runtime/python/bin/python3       (Linux, macOS)
#
# Arsiv zaten python\ klasoru olarak aciliyor; onu dogrudan runtime\ yapmak
# (eski davranis) yorumlayiciyi runtime\python.exe konumuna koyuyordu.
# Derleme yine yesil biterdi — kurulu uygulama acilirken gomulu Python'u
# bulamaz, sessizce sistem Python'una duser ya da hic acilmazdi. Kirilma
# calisma anina ertelendigi icin en pahali turden bir sapmaydi.
$Runtime   = Join-Path $Tauri "runtime"
$RuntimePy = Join-Path $Runtime "python"

# --- gömülü Python sürümü -------------------------------------------------
#
# BU İKİ DEĞER ELLE DOĞRULANIR — build-release.sh ile AYNI kalmalıdır; iki
# platformun farklı Python sürümü taşıması, yalnız birinde görülen hatalar
# demektir. Liste:
#   https://github.com/astral-sh/python-build-standalone/releases
$PyVersion  = if ($env:KM_PY_VERSION)  { $env:KM_PY_VERSION }  else { "3.12.11" }
$PbsRelease = if ($env:KM_PBS_RELEASE) { $env:KM_PBS_RELEASE } else { "20250612" }
$PbsBase    = "https://github.com/astral-sh/python-build-standalone/releases/download"

function Say([string]$Message) { Write-Host "`n$Message" -ForegroundColor Cyan }
function Die([string]$Message) { Write-Host "HATA: $Message" -ForegroundColor Red; exit 1 }
# UYARI PAKETI DUSURMEZ. Eksik bir yardimci yalnizca o ozelligi kapatir;
# derlemeyi tumuyle durdurmak, calisan her seyi de engellemek olurdu.
function Warn([string]$Message) { Write-Host "UYARI: $Message" -ForegroundColor Yellow }

Set-Location $Root

# --- 0. araçlar -----------------------------------------------------------
Say "0) Arac denetimi"
if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    Die "cargo yok. rustup ile kurulur: https://rustup.rs"
}
cargo tauri --version *> $null
if ($LASTEXITCODE -ne 0) {
    Die "Tauri CLI yok. Kurulum:  cargo install tauri-cli --version '^2' --locked"
}

# Mimari: PowerShell 5.1'de $env:PROCESSOR_ARCHITECTURE güvenilirdir.
$arch = switch ($env:PROCESSOR_ARCHITECTURE) {
    "AMD64" { "x86_64" }
    "ARM64" { "aarch64" }
    default { Die "Desteklenmeyen mimari: $env:PROCESSOR_ARCHITECTURE" }
}

# python-build-standalone Windows dağıtımında yorumlayıcı arşiv klasörünün
# KÖKÜNDE durur (Unix'teki gibi bin/ altında değil). main.rs bu farkı bilir.
$Python = Join-Path $RuntimePy "python.exe"

# --- 1. gömülü Python -----------------------------------------------------
if ($SkipRuntime) {
    Say "1) Gomulu Python — atlandi (-SkipRuntime)"
    if (-not (Test-Path $Python)) { Die "runtime\ bos; -SkipRuntime kullanilamaz." }
} else {
    Say "1) Gomulu Python indiriliyor ($PyVersion / $PbsRelease / $arch)"

    $asset = "cpython-$PyVersion+$PbsRelease-$arch-pc-windows-msvc-install_only.tar.gz"
    $url   = "$PbsBase/$PbsRelease/$asset"
    $work  = Join-Path ([System.IO.Path]::GetTempPath()) ("km-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $work -Force | Out-Null

    try {
        Write-Host "  $url"
        $archive = Join-Path $work $asset
        try { Invoke-WebRequest -Uri $url -OutFile $archive -UseBasicParsing }
        catch {
            Die ("Indirilemedi. Surum/etiket dogru mu?`n" +
                 "  KM_PY_VERSION / KM_PBS_RELEASE ile ezilebilir.`n" +
                 "  Liste: https://github.com/astral-sh/python-build-standalone/releases")
        }

        # BUTUNLUK DENETIMI ATLANMAZ: uygulamanin icine gomulen bir
        # yorumlayicidir, kullanicinin makinesinde bizim adimiza kod calistirir.
        $sumFile = "$archive.sha256"
        Invoke-WebRequest -Uri "$url.sha256" -OutFile $sumFile -UseBasicParsing
        $expected = (Get-Content $sumFile -Raw).Trim().ToLower()
        $actual   = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLower()
        if ($expected -ne $actual) { Die "SHA256 uyusmadi — indirilen dosya atildi." }

        # tar.exe Windows 10 1803'ten beri yerlesiktir.
        tar -xzf $archive -C $work
        if ($LASTEXITCODE -ne 0) { Die "Arsiv acilamadi (tar)." }
        $extracted = Join-Path $work "python"
        if (-not (Test-Path $extracted)) { Die "Arsiv beklenen 'python\' klasorunu tasimiyor." }

        # Arsivin python\ klasoru kokun ICINE tasinir, kokun YERINE degil:
        # sonuc runtime\python\python.exe olur (yukaridaki yer aciklamasi).
        if (Test-Path $Runtime) { Remove-Item $Runtime -Recurse -Force }
        New-Item -ItemType Directory -Path $Runtime -Force | Out-Null
        Move-Item $extracted $RuntimePy
        Write-Host "  yerlesti: $RuntimePy"
    }
    finally {
        Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if (-not (Test-Path $Python)) { Die "Gomulu Python bulunamadi: $Python" }
& $Python -V

# --- 2. çalışma zamanı bağımlılıkları -------------------------------------
Say "2) Bagimliliklar gomulu ortama kuruluyor"

# BAGIMLILIK ILAN EDILIR, KOPYALANMAZ (K11): liste burada yazilmaz,
# backend\pyproject.toml ve modullerin module.yaml dosyalarindan turetilir.
# Toplayici build-release.sh ile AYNI kurallari uygular; Windows'ta `printer`
# extra'si dislanir (ADR 0014 — CUPS yok, pycups derlenmez).
& $Python -m pip install --upgrade pip | Out-Null
& $Python -m pip install pyyaml | Out-Null

# Toplayici gecici klasore yazilir, runtime\ ICINE DEGIL: orasi pakete
# oldugu gibi kopyalanan agactir ve betik arada duserse arta kalan dosya
# kuruculara girer.
$collector = Join-Path ([System.IO.Path]::GetTempPath()) "km-collect-reqs.py"
@'
import pathlib, sys, tomllib

import yaml

HERE = "windows"

EXTRAS = ["ssh", "database", "audio", "notify"]

data = tomllib.load(open("backend/pyproject.toml", "rb"))
project = data["project"]
reqs = list(project.get("dependencies", []))
extras = project.get("optional-dependencies", {})
for name in EXTRAS:
    reqs += extras.get(name, [])

for manifest_path in sorted(pathlib.Path("modules").glob("*/module.yaml")):
    manifest = yaml.safe_load(open(manifest_path, encoding="utf-8")) or {}
    declared = manifest.get("platforms")
    if declared and HERE not in declared:
        continue
    reqs += ((manifest.get("dependencies") or {}).get("python") or [])

seen, out = set(), []
for req in reqs:
    if req not in seen:
        seen.add(req)
        out.append(req)
sys.stdout.write("\n".join(out))
'@ | Set-Content -Path $collector -Encoding UTF8

$reqs = & $Python $collector
if ($LASTEXITCODE -ne 0) { Die "Bagimlilik listesi cikarilamadi." }
Remove-Item $collector -Force

$reqFile = Join-Path ([System.IO.Path]::GetTempPath()) "km-requirements.txt"
$reqs | Set-Content -Path $reqFile -Encoding UTF8
($reqs -split "`n") | ForEach-Object { Write-Host "  $_" }

& $Python -m pip install -r $reqFile
if ($LASTEXITCODE -ne 0) { Die "Bagimlilik kurulumu basarisiz." }
Remove-Item $reqFile -Force

# Derlenmis onbellek pakete girmez.
Get-ChildItem -Path $Runtime -Filter "__pycache__" -Recurse -Directory -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

if ($RuntimeOnly) {
    Say "Calisma zamani hazir: $RuntimePy"
    exit 0
}

# --- 3. kabuk menü kaydı --------------------------------------------------
Say "3) Menu kaydi uretiliyor"
# registry.json ve panels\ ikiliye GOMULUR (frontendDist). Derlemeden once
# uretilmezse paket eski menuyle cikar.
$env:PYTHONPATH = Join-Path $Root "backend\src"
& $Python (Join-Path $Root "tools\build-ui-registry.py")
if ($LASTEXITCODE -ne 0) { Die "Menu kaydi uretilemedi; manifestlerden biri bozuk olabilir." }

Get-ChildItem -Path (Join-Path $Root "backend\src"), (Join-Path $Root "modules") `
    -Filter "__pycache__" -Recurse -Directory -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# --- 3b. yazdirma yardimcisi ----------------------------------------------
#
# NEDEN INDIRILIYOR: Windows'ta bir PDF'i diyalog acmadan secili yaziciya
# basacak hazir bir komut yok. `Start-Process -Verb PrintTo` kayitli PDF
# isleyicisine bagli ve Windows 10/11'in varsayilani Edge bu fiili
# DESTEKLEMIYOR -- cogu makinede sessizce hicbir sey basilmazdi.
#
# NEDEN DEPODA DURMUYOR: K11 ikiliyi depoya koymayi yasakliyor; bagimlilik
# ILAN EDILIR. Bu yuzden paketleme aninda indirilir ve `bundle.resources`
# ile kuruluma girer. Zaten varsa yeniden indirilmez.
Say "3b) Yazdirma yardimcisi hazirlaniyor"
$YazdirmaDir = Join-Path $Root "apps\desktop\src-tauri\yazdirma"
$SumatraExe = Join-Path $YazdirmaDir "SumatraPDF.exe"
if (Test-Path $SumatraExe) {
    Say "   zaten var, indirilmedi"
} else {
    New-Item -ItemType Directory -Force -Path $YazdirmaDir | Out-Null
    # Surum SABITLENMISTIR: "en son" indirmek, paketin icerigini derleme
    # gununE baglar ve iki derleme ayni girdiden farkli cikti verir.
    $SumatraUrl = "https://www.sumatrapdfreader.org/dl/rel/3.5.2/SumatraPDF-3.5.2-64.zip"
    $Zip = Join-Path $env:TEMP "sumatra.zip"
    try {
        Invoke-WebRequest -Uri $SumatraUrl -OutFile $Zip -UseBasicParsing
        Expand-Archive -Path $Zip -DestinationPath $YazdirmaDir -Force
        # Arsivdeki ad surum tasiyor; kabuk sabit adi ariyor (printing.rs).
        $Cikan = Get-ChildItem -Path $YazdirmaDir -Filter "SumatraPDF*.exe" |
                 Select-Object -First 1
        if ($null -eq $Cikan) { Die "Yazdirma yardimcisi arsivden cikmadi." }
        if ($Cikan.FullName -ne $SumatraExe) {
            Move-Item -Path $Cikan.FullName -Destination $SumatraExe -Force
        }
    } catch {
        # BASKI CALISMAZ AMA PAKET CIKAR. Yardimci olmadan Windows'ta
        # yazdirma yapilamaz; kabuk bunu acik bir hata ile soyluyor. Paketi
        # tumuyle dusurmek, yazdirma disindaki her seyi de engellerdi.
        Warn "Yazdirma yardimcisi indirilemedi: $($_.Exception.Message)"
        Warn "Windows paketinde YAZDIRMA CALISMAYACAK."
    } finally {
        Remove-Item $Zip -ErrorAction SilentlyContinue
    }
}

# --- 4. paket -------------------------------------------------------------
Say "4) Paket uretiliyor"
# tauri.release.json yalniz burada devreye girer: kaynak dosyalar (backend,
# modules, runtime) pakete YALNIZ yayin derlemesinde kopyalanir.
$buildArgs = @("tauri", "build", "--config", "tauri.release.json")
if ($Bundles) { $buildArgs += @("--bundles", $Bundles) }

Push-Location $Tauri
try {
    & cargo @buildArgs
    if ($LASTEXITCODE -ne 0) { Die "Paket uretimi basarisiz." }
}
finally { Pop-Location }

# --- 5. çıktı -------------------------------------------------------------
Say "5) Cikti toplaniyor"
New-Item -ItemType Directory -Path $Dist -Force | Out-Null
$bundleDir = Join-Path $Tauri "target\release\bundle"

# Desen listesi TEK YERDE durur: asagidaki hata iletisi de bunu yazar.
$patterns = @("nsis\*.exe", "msi\*.msi")

# --- guncelleme paketlerinin kunyesi --------------------------------------
#
# latest.json BURADA URETILMEZ: dosyalarin yayindaki ADRESI ancak Release
# olustuktan sonra bilinir (GitHub varlik adlarindaki bosluklari noktaya
# cevirir). Burada uretilen sey kunyedir — hangi imzali dosya hangi guncelleme
# hedefine karsilik geliyor; scripts\make-latest-json.py bunu gercek adreslerle
# birlestirir.
#
# HEDEF ADI EKLENTININ KENDI DUZENIDIR: {sistem}-{mimari}-{kurulum bicimi}
# (tauri-plugin-updater -> get_urls). Uydurulmus bir ad, guncelleyicinin
# "bu kurulum icin paket yok" demesiyle sonuclanir.
function Get-UpdaterKey([string]$Name) {
    if ($Name -like "*-setup.exe") { return "windows-$arch-nsis" }
    if ($Name -like "*.msi")       { return "windows-$arch-msi" }
    return ""
}

$updater = [ordered]@{}

$found = 0
foreach ($pattern in $patterns) {
    Get-ChildItem -Path (Join-Path $bundleDir $pattern) -ErrorAction SilentlyContinue |
        ForEach-Object {
            Copy-Item $_.FullName -Destination $Dist -Force
            Write-Host "  $($_.Name)"
            $script:found = 1

            # IMZASIZ PAKET GUNCELLEME PAKETI DEGILDIR. .sig yoksa dosya yayina
            # yine girer (elle indirilebilir) ama kunyeye YAZILMAZ: imzasiz bir
            # girdi guncelleyicide "signature could not be decoded" hatasidir.
            $sig = "$($_.FullName).sig"
            if (Test-Path $sig) {
                Copy-Item $sig -Destination $Dist -Force
                Write-Host "  $($_.Name).sig"
                $key = Get-UpdaterKey $_.Name
                if ($key) { $updater[$key] = $_.Name }
            }
        }
}

if ($updater.Count -gt 0) {
    $updaterDir = Join-Path $Dist "updater"
    New-Item -ItemType Directory -Path $updaterDir -Force | Out-Null
    $updaterFile = Join-Path $updaterDir "windows-$arch.json"
    # ConvertTo-Json kacislari kendisi yapar; dosya adinda bosluk olabilir.
    $updater | ConvertTo-Json | Set-Content -Path $updaterFile -Encoding UTF8
    Write-Host "  updater\windows-$arch.json ($($updater.Count) hedef)"
} else {
    # SESSIZ KALINMAZ: imzasiz paketlerle guncelleme calismaz ve bu, ancak
    # kullanici "denetle" dediginde ortaya cikardi.
    Write-Host "  UYARI: hic imza (.sig) uretilmedi — TAURI_SIGNING_PRIVATE_KEY" -ForegroundColor Yellow
    Write-Host "         verilmemis olabilir. Bu paketler elle kurulur." -ForegroundColor Yellow
}

# PAKET YOKSA ACIK HATAYLA DUSULUR — "alti bos" tek basina ayiklanamaz bir
# iletidir. Ne istendigi, nereye bakildigi ve kabugun ne urettigi yazilir.
if ($found -ne 1) {
    $istenen = if ($Bundles) { $Bundles } else { "(yapilandirmadaki tumu: tauri.conf.json -> bundle.targets)" }
    Write-Host "  istenen hedefler : $istenen"
    Write-Host "  aranan kok       : $bundleDir"
    Write-Host "  aranan desenler  : $($patterns -join ' ')"
    Write-Host "  kabugun urettigi :"
    if (Test-Path $bundleDir) {
        Get-ChildItem -Path $bundleDir -Recurse -Depth 1 -ErrorAction SilentlyContinue |
            ForEach-Object { Write-Host "    $($_.FullName)" }
    } else {
        Write-Host "    (klasor hic olusmadi - cargo tauri build tek bir hedef uretmedi)"
    }
    Die "Paket uretilmedi; dist\ bos kalacakti."
}

Say "Bitti. Cikti: $Dist"
Write-Host "Kurucu IMZASIZDIR — SmartScreen uyarisi icin deploy\README.md."
