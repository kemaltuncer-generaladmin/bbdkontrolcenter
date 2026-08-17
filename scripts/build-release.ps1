<#
.SYNOPSIS
    Kontrol Merkezi — Windows kurucusu (.exe / .msi) üretimi. ADR 0023.

.DESCRIPTION
    Adımlar:
      1. Gömülü Python çalışma zamanını indirir ve bağımlılıklarını kurar
      2. Kabuk menü kaydını üretir (registry.json ikiliye gömülür)
      3. `cargo tauri build` ile NSIS kurucusunu ve MSI paketini üretir
      4. Çıktıyı dist\ altına toplar

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
$Runtime = Join-Path $Tauri "runtime"
$Dist    = Join-Path $Root "dist"

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

# python-build-standalone Windows dağıtımında yorumlayıcı kökte durur
# (Unix'teki gibi bin/ altında değil). main.rs bu farkı bilir.
$Python = Join-Path $Runtime "python.exe"

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

        if (Test-Path $Runtime) { Remove-Item $Runtime -Recurse -Force }
        Move-Item $extracted $Runtime
        Write-Host "  yerlesti: $Runtime"
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

$collector = Join-Path $Runtime "km-collect-reqs.py"
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
    Say "Calisma zamani hazir: $Runtime"
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

$found = 0
foreach ($pattern in $patterns) {
    Get-ChildItem -Path (Join-Path $bundleDir $pattern) -ErrorAction SilentlyContinue |
        ForEach-Object {
            Copy-Item $_.FullName -Destination $Dist -Force
            Write-Host "  $($_.Name)"
            $script:found = 1
        }
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
