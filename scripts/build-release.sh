#!/usr/bin/env bash
#
# Kontrol Merkezi — kurulabilir paket üretimi (Linux / macOS). ADR 0023.
#
# Ne yapar:
#   1. Kabuk menü kaydını üretir (shell/registry.json ikiliye gömülür)
#   2. Gömülü Python çalışma zamanını indirir ve bağımlılıklarını kurar
#   3. `cargo tauri build` ile paketi üretir
#   4. Çıktıyı dist/ altına toplar (paket + `.sig` imzası + güncelleme künyesi)
#
# ÇAPRAZ DERLEME YOKTUR. Bu betik çalıştığı platformun paketini üretir:
# Linux'ta .deb/.AppImage, macOS'ta .dmg. Windows kurucusu Windows'ta,
# `scripts/build-release.ps1` ile üretilir — WebView2/WiX/NSIS zinciri orada.
#
# Kullanım:
#   scripts/build-release.sh                 # tam üretim
#   scripts/build-release.sh --skip-runtime  # var olan runtime/ ile derle
#   scripts/build-release.sh --runtime-only  # yalnız çalışma zamanını hazırla
#   scripts/build-release.sh --bundles deb   # yalnız belirtilen hedef(ler)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAURI="$ROOT/apps/desktop/src-tauri"
DIST="$ROOT/dist"

# --- gömülü çalışma zamanının YERİ ---------------------------------------
#
# İKİ AD, İKİ İŞ. `RUNTIME` pakete kopyalanan KÖKtür (`tauri.release.json` →
# "runtime": "runtime"); `RUNTIME_PY` yorumlayıcının kendisinin durduğu
# klasördür ve kökün ALTINDA bir basamak daha içeridedir.
#
# Fazladan görünen bu basamak keyfi değil, ADR 0023 §1'in yazdığı yoldur ve
# `main.rs` (python_path) tam olarak orayı arar:
#
#     runtime/python/python.exe        (Windows)
#     runtime/python/bin/python3       (Linux, macOS)
#
# Arşiv zaten `python/` klasörü olarak açılıyor; onu doğrudan `runtime/`
# yapmak (eski davranış) yorumlayıcıyı `runtime/bin/python3` konumuna
# koyuyordu. Derleme yine yeşil biterdi — kurulu uygulama açılırken gömülü
# Python'u bulamaz, sessizce sistem Python'una düşer ya da hiç açılmazdı.
# Kırılma çalışma anına ertelendiği için en pahalı türden bir sapmaydı.
RUNTIME="$TAURI/runtime"
RUNTIME_PY="$RUNTIME/python"

# --- gömülü Python sürümü -------------------------------------------------
#
# python-build-standalone: bağımsız, taşınabilir CPython. Kullanıcının
# makinesindeki Python'a güvenilmez (ADR 0023 — elenen alternatifler).
#
# BU İKİ DEĞER ELLE DOĞRULANIR. Yayın etiketleri tarih biçimindedir ve varlık
# adları sürümle birlikte değişir; listeyi görmeden yükseltilmez:
#   https://github.com/astral-sh/python-build-standalone/releases
# Ortamdan ezilebilir: KM_PY_VERSION=3.12.11 KM_PBS_RELEASE=20250612 ...
PY_VERSION="${KM_PY_VERSION:-3.12.11}"
PBS_RELEASE="${KM_PBS_RELEASE:-20250612}"
PBS_BASE="https://github.com/astral-sh/python-build-standalone/releases/download"

SKIP_RUNTIME=0
RUNTIME_ONLY=0
BUNDLES=""

while [ $# -gt 0 ]; do
  case "$1" in
    --skip-runtime) SKIP_RUNTIME=1 ;;
    --runtime-only) RUNTIME_ONLY=1 ;;
    --bundles) shift; BUNDLES="${1:-}" ;;
    -h|--help) sed -n '2,19p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Bilinmeyen seçenek: $1" >&2; exit 2 ;;
  esac
  shift
done

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
# `%b` — ileti içindeki `\n` kaçışları gerçek satır sonuna çevrilsin.
die() { printf '\033[31mHATA:\033[0m %b\n' "$*" >&2; exit 1; }
# Uyarı derlemeyi DÜŞÜRMEZ ama gözden kaçmasın diye renklidir: buradan geçen
# şeyler "paket üretildi ama beklediğin paket olmayabilir" anlamına gelir.
warn() { printf '\033[33mUYARI:\033[0m %b\n' "$*" >&2; }

# --- sessiz başarı yasağı -------------------------------------------------
#
# 30 SANİYEDE "BAŞARILI" GÖRÜNEN DERLEME, DÜŞEN DERLEMEDEN KÖTÜDÜR. Bu tuzak,
# betik sonuna varmadan 0 ile çıkarsa çıkışı hataya çevirir.
#
# Somut olay (run 32038408984, macOS): koşucunun /bin/bash'i 3.2'dir ve
# `$( … <<'PY' … PY … )` biçimini ayrıştıramaz. Ayrıştırma hatasını basıp
# betiği SON BAŞARILI KOMUTUN durumuyla — yani 0 ile — sonlandırdı. İş akışı
# adımı yeşil göründü, dist/ boş kaldı ve hata ancak artefakt yükleme
# adımında "No files were found" olarak ortaya çıktı. Ayrıştırma sorunu
# aşağıda giderildi; bu tuzak ise SINIFIN tamamına karşı durur: hangi neden
# olursa olsun, yarıda kesilen bir derleme bir daha sessizce başarılı sayılmaz.
FINISHED=0
WORK=""
COLLECT=""
on_exit() {
  status=$?
  if [ -n "$WORK" ]; then rm -rf "$WORK"; fi
  if [ -n "$COLLECT" ]; then rm -f "$COLLECT"; fi
  if [ "$status" -eq 0 ] && [ "$FINISHED" -ne 1 ]; then
    printf '\033[31mHATA:\033[0m betik sonuna varmadan sessizce çıktı (çıkış 0).\n' >&2
    printf '  Yukarıdaki son adım tamamlanmadı; üretilmiş paket YOKTUR.\n' >&2
    status=1
  fi
  exit "$status"
}
trap on_exit EXIT

cd "$ROOT"

# --- 0. araçlar -----------------------------------------------------------
say "0) Araç denetimi"
command -v cargo >/dev/null 2>&1 || die "cargo yok. Kurulum: scripts/install-deps.sh --with-desktop"
command -v curl  >/dev/null 2>&1 || die "curl yok."
command -v tar   >/dev/null 2>&1 || die "tar yok."

if ! cargo tauri --version >/dev/null 2>&1; then
  die "Tauri CLI yok. Kurulum:  cargo install tauri-cli --version '^2' --locked"
fi
echo "  cargo: $(cargo -V)"
echo "  tauri: $(cargo tauri --version)"

case "$(uname -s)" in
  Linux)  PBS_OS="unknown-linux-gnu"; PY_BIN="bin/python3" ;;
  Darwin) PBS_OS="apple-darwin";      PY_BIN="bin/python3" ;;
  *) die "Bu betik yalnız Linux ve macOS içindir. Windows: scripts/build-release.ps1" ;;
esac

case "$(uname -m)" in
  x86_64|amd64)  PBS_ARCH="x86_64" ;;
  aarch64|arm64) PBS_ARCH="aarch64" ;;
  *) die "Desteklenmeyen mimari: $(uname -m)" ;;
esac

PYTHON="$RUNTIME_PY/$PY_BIN"

# --- 1. gömülü Python -----------------------------------------------------
if [ "$SKIP_RUNTIME" = 1 ]; then
  say "1) Gömülü Python — atlandı (--skip-runtime)"
  [ -x "$PYTHON" ] || die "runtime/ boş; --skip-runtime kullanılamaz."
else
  say "1) Gömülü Python indiriliyor ($PY_VERSION / $PBS_RELEASE / $PBS_ARCH-$PBS_OS)"

  ASSET="cpython-${PY_VERSION}+${PBS_RELEASE}-${PBS_ARCH}-${PBS_OS}-install_only.tar.gz"
  URL="$PBS_BASE/$PBS_RELEASE/$ASSET"
  # Temizliği `on_exit` üstlenir; buradaki ayrı `trap … EXIT` onu ezerdi.
  WORK="$(mktemp -d)"

  echo "  $URL"
  curl -fL --retry 3 -o "$WORK/$ASSET" "$URL" \
    || die "İndirilemedi. Sürüm/etiket doğru mu?\n  KM_PY_VERSION / KM_PBS_RELEASE ile ezilebilir.\n  Liste: https://github.com/astral-sh/python-build-standalone/releases"

  # BÜTÜNLÜK DENETİMİ ATLANMAZ: uygulamanın içine gömülen bir yorumlayıcıdır,
  # kullanıcının makinesinde bizim adımıza kod çalıştırır.
  curl -fL --retry 3 -o "$WORK/$ASSET.sha256" "$URL.sha256" \
    || die "Özet dosyası indirilemedi; doğrulanmamış çalışma zamanı gömülmez."

  # Özet dosyası yalnız onaltılık değeri taşır; `-c` biçimi ad da ister.
  if command -v sha256sum >/dev/null 2>&1; then CHECK=(sha256sum -c -)
  else CHECK=(shasum -a 256 -c -); fi
  ( cd "$WORK" \
      && printf '%s  %s\n' "$(tr -d '[:space:]' < "$ASSET.sha256")" "$ASSET" | "${CHECK[@]}" ) \
    || die "SHA256 uyuşmadı — indirilen dosya atıldı."

  tar -xzf "$WORK/$ASSET" -C "$WORK"
  [ -d "$WORK/python" ] || die "Arşiv beklenen 'python/' klasörünü taşımıyor."

  # Arşivin `python/` klasörü kökün İÇİNE taşınır, kökün YERİNE değil:
  # sonuç `runtime/python/bin/python3` olur (yukarıdaki yer açıklaması).
  rm -rf "$RUNTIME"
  mkdir -p "$RUNTIME"
  mv "$WORK/python" "$RUNTIME_PY"
  echo "  yerleşti: $RUNTIME_PY"
fi

[ -x "$PYTHON" ] || die "Gömülü Python bulunamadı: $PYTHON"
echo "  sürüm: $("$PYTHON" -V)"

# --- 2. çalışma zamanı bağımlılıkları -------------------------------------
say "2) Bağımlılıklar gömülü ortama kuruluyor"

# BAĞIMLILIK İLAN EDİLİR, KOPYALANMAZ (K11): liste burada yazılmaz,
# `backend/pyproject.toml` ve modüllerin `module.yaml` dosyalarından türetilir
# — `scripts/install-deps.sh` ile aynı kaynak.
"$PYTHON" -m pip install --upgrade pip >/dev/null
"$PYTHON" -m pip install pyyaml >/dev/null   # aşağıdaki toplayıcı manifest okur

# TOPLAYICI ÖNCE DOSYAYA YAZILIR, `$( … <<'PY' … PY … )` İÇİNE GÖMÜLMEZ.
#
# macOS koşucusunun /bin/bash'i 3.2.57'dir ve komut ikamesinin içindeki
# here-document'ı tanımaz: kapanış parantezini ararken metni salt karakter
# olarak tarar, Python yorumlarındaki kesme işaretlerini (Windows'ta,
# macOS'ta …) dizgi başlangıcı sayar. Buradaki üç kesme işareti TEK sayı
# olduğu için tarayıcı açık bir tırnakla dosya sonuna kadar sürükleniyor,
# `unexpected EOF while looking for matching` verip betiği yarıda bırakıyordu
# (run 32038408984). Sorun Türkçe metnin kendisi değil, bash 3.2'nin bu
# birleşimi ayrıştıramaması — bu yüzden yorumlar sadeleştirilmiyor, yapı
# değiştiriliyor: dosyaya yazılan betik her bash sürümünde aynı çalışır ve
# .ps1 karşılığı da zaten bu yolu izliyor.
COLLECT="$(mktemp)"
cat > "$COLLECT" <<'PY'
import pathlib, sys, tomllib

import yaml


def platform_name() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


HERE = platform_name()

# Çalışma zamanı yetenekleri. `dev` ve isteğe bağlı sürücüler dışarıda —
# scripts/install-deps.sh ile AYNI küme; iki liste ayrışmasın.
EXTRAS = ["ssh", "database", "printer", "audio", "notify"]
if HERE in ("windows", "macos"):
    # Baskı işletim sistemine devredilir (ADR 0014): Windows'ta CUPS hiç yok,
    # macOS'ta var ama arka uç `backends/system.py` — pycups çağrılmaz. İki
    # platformda da kurulmaz; Windows'ta zaten derlenmez.
    EXTRAS.remove("printer")

data = tomllib.load(open("backend/pyproject.toml", "rb"))
project = data["project"]
reqs = list(project.get("dependencies", []))
extras = project.get("optional-dependencies", {})
for name in EXTRAS:
    reqs += extras.get(name, [])

# Bu platformda çalışmayan modülün bağımlılığı toplanmaz (ADR 0022).
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
print("\n".join(out))
PY

REQS="$("$PYTHON" "$COLLECT")" || die "Bağımlılık listesi çıkarılamadı."
rm -f "$COLLECT"
COLLECT=""

[ -n "$REQS" ] || die "Bağımlılık listesi boş çıktı; toplayıcı hiçbir şey bulamadı."

echo "$REQS" | sed 's/^/  /'
echo "$REQS" | "$PYTHON" -m pip install -r /dev/stdin

# Derlenmiş önbellek pakete girmez: hem şişirir hem başka bir Python
# sürümünde geçersizdir.
find "$RUNTIME" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

if [ "$RUNTIME_ONLY" = 1 ]; then
  say "Çalışma zamanı hazır: $RUNTIME_PY"
  FINISHED=1   # istenen iş buydu: erken çıkış meşrudur
  exit 0
fi

# --- 2.5 künye: HANGİ KODDAN DERLENDİĞİ -----------------------------------
#
# 17.08.2026'da aynı arıza üç kez düzeltildi ve üç kez geri geldi; sonunda
# paketin ESKİ KODDAN derlendiği anlaşıldı. Bunu anlamanın hiçbir yolu yoktu:
# sürüm numarası commit'i tek anlamlı belirlemiyor (üç ayrı commit aynı
# "0.1.2"yi taşıyordu) ve bu betik git'e hiç bakmıyordu.
say "2.5) Künye damgalanıyor"

KM_COMMIT="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo '')"
KM_KIRLI="$(git -C "$ROOT" status --porcelain 2>/dev/null || echo '')"
KM_SURUM="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['version'])" \
            "$TAURI/tauri.conf.json" 2>/dev/null || echo '')"

if [ -z "$KM_COMMIT" ]; then
  warn "git bulunamadı — paket künyesiz çıkacak, hangi koddan geldiği anlaşılmaz."
else
  # KİRLİ AĞAÇ SESSİZ GEÇİLMEZ. Kaydedilmemiş değişiklikle üretilen paket,
  # hiçbir commit'e karşılık gelmez ve "sende çalışıyor mu" sorusu cevapsız kalır.
  if [ -n "$KM_KIRLI" ]; then
    KM_COMMIT="${KM_COMMIT}+degisiklik"
    warn "ÇALIŞMA AĞACI KİRLİ — paket hiçbir commit'e birebir karşılık gelmiyor."
  fi
  # Uzakla farkı da söyle: `git pull` unutulmuşsa paket eski kodu taşır.
  if git -C "$ROOT" rev-parse '@{u}' >/dev/null 2>&1; then
    GERIDE="$(git -C "$ROOT" rev-list --count 'HEAD..@{u}' 2>/dev/null || echo 0)"
    if [ "${GERIDE:-0}" -gt 0 ]; then
      warn "UZAKTA $GERIDE COMMIT DAHA VAR — 'git pull' yapmadan derliyorsunuz."
    fi
  fi
  printf 'COMMIT = "%s"\nBUILT_AT = "%s"\nVERSION = "%s"\n' \
    "$KM_COMMIT" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$KM_SURUM" \
    > "$ROOT/backend/src/km_core/_build_stamp.py"
  echo "  künye: $KM_COMMIT · sürüm $KM_SURUM"
fi

# --- 3. kabuk menü kaydı --------------------------------------------------
say "3) Menü kaydı üretiliyor"
# registry.json ve panels/ ikiliye GÖMÜLÜR (frontendDist). Derlemeden önce
# üretilmezse paket eski menüyle çıkar.
PYTHONPATH="$ROOT/backend/src" "$PYTHON" "$ROOT/tools/build-ui-registry.py" \
  || die "Menü kaydı üretilemedi; manifestlerden biri bozuk olabilir."

# Depo ağacındaki .pyc dosyaları pakete kopyalanır ve boşuna yer kaplar.
find "$ROOT/backend/src" "$ROOT/modules" -name '__pycache__' -type d -prune \
  -exec rm -rf {} + 2>/dev/null || true

# --- 4. paket -------------------------------------------------------------
say "4) Paket üretiliyor"
# `tauri.release.json` yalnız burada devreye girer: kaynak dosyaları (backend,
# modules, runtime) pakete YALNIZ yayın derlemesinde kopyalanır. Ana
# yapılandırmada dursaydı her geliştirme derlemesi 30 MB modülü yeniden
# damgalar ve `scripts/launch-desktop.sh` her açılışta yeniden derlerdi.
BUILD_ARGS=(--config tauri.release.json)
[ -n "$BUNDLES" ] && BUILD_ARGS+=(--bundles "$BUNDLES")

( cd "$TAURI" && cargo tauri build "${BUILD_ARGS[@]}" ) || die "Paket üretimi başarısız."

# --- 5. çıktı -------------------------------------------------------------
say "5) Çıktı toplanıyor"
mkdir -p "$DIST"
BUNDLE_DIR="$TAURI/target/release/bundle"

# Desen listesi TEK YERDE durur: aşağıdaki hata iletisi de bunu yazar, yoksa
# "aradım bulamadım" diyen ileti neyi aradığını söylemeyen bir ileti olurdu.
# Dizi: düz dizge olsaydı `$PATTERNS` açılırken desenler ÇALIŞMA DİZİNİNE göre
# genişler, `$BUNDLE_DIR` altına bakılmadan önce eşleşip bozulurdu.
#
# `macos/*.app.tar.gz` KURULACAK PAKET DEĞİL, GÜNCELLEME PAKETİDİR: macOS'ta
# kullanıcı .dmg indirir, güncelleyici ise uygulamanın kendisini sıkıştırılmış
# olarak çeker (.dmg'yi bağlayıp kopyalayamaz). İkisi de yayına konur.
PATTERNS=("deb/*.deb" "appimage/*.AppImage" "appimage/*.AppImage.tar.gz" \
          "rpm/*.rpm" "dmg/*.dmg" "macos/*.app.tar.gz")

# --- güncelleme paketlerinin künyesi --------------------------------------
#
# `latest.json`ı burada üretmiyoruz: dosyaların yayındaki ADRESİ ancak Release
# oluştuktan sonra bilinir (GitHub varlık adlarındaki boşlukları noktaya
# çevirir; adresi tahmin etmek sessizce kırık bir bağlantı demektir). Burada
# üretilen şey künyedir — hangi imzalı dosya hangi güncelleme hedefine karşılık
# geliyor. `scripts/make-latest-json.py` bunu gerçek adreslerle birleştirir.
#
# HEDEF ADI EKLENTİNİN KENDİ DÜZENİDİR: `{sistem}-{mimari}-{kurulum biçimi}`
# (tauri-plugin-updater → `get_urls`). Uydurulmuş bir ad, güncelleyicinin
# "bu kurulum için paket yok" demesiyle sonuçlanır.
case "$(uname -s)" in
  Linux)  UPD_OS="linux" ;;
  Darwin) UPD_OS="darwin" ;;
esac
UPD_ARCH="$PBS_ARCH"          # x86_64 | aarch64 — updater'ın kullandığı adlar

updater_key() {
  case "$1" in
    *.AppImage|*.AppImage.tar.gz) printf '%s-%s-appimage' "$UPD_OS" "$UPD_ARCH" ;;
    *.app.tar.gz)                 printf '%s-%s-app'      "$UPD_OS" "$UPD_ARCH" ;;
    *.deb)                        printf '%s-%s-deb'      "$UPD_OS" "$UPD_ARCH" ;;
    *.rpm)                        printf '%s-%s-rpm'      "$UPD_OS" "$UPD_ARCH" ;;
    *) printf '' ;;
  esac
}

UPDATER_KEYS=()
UPDATER_FILES=()

# AYNI HEDEF İKİ KEZ YAZILMAZ. Kabuk sürümüne göre AppImage hem düz imzalı
# dosya hem `.tar.gz` olarak çıkabiliyor; ikisi de aynı hedefe düşer ve künyede
# çift anahtar bırakmak, hangisinin geçerli olduğunu okuyucuya bırakmak olurdu.
# İlk eşleşen kazanır — desen sırası (`.AppImage`, sonra `.AppImage.tar.gz`)
# kararı belirler.
has_key() {
  for existing in ${UPDATER_KEYS[@]+"${UPDATER_KEYS[@]}"}; do
    [ "$existing" = "$1" ] && return 0
  done
  return 1
}

FOUND=0
for pattern in "${PATTERNS[@]}"; do
  # KÖK TIRNAKLI, DESEN TIRNAKSIZ. `$BUNDLE_DIR` tırnaksız bırakılınca yol
  # BOŞLUKTAN bölünüyordu: depo `…/Kontrol Merkezi/…` altında durduğu için
  # desen `…/Kontrol` ve `Merkezi/…/deb/*.deb` diye iki söze ayrılıyor, ikisi
  # de hiçbir şeyle eşleşmiyor ve betik "paket üretilmedi" diyerek düşüyordu —
  # paket ve imzası bundle/ altında dururken. `dist/` bu yüzden hep boştu.
  #
  # Tırnak yalnız KÖKE konur; deseni tırnaklamak globu dizgeye çevirir ve
  # hiçbir dosyayla eşleşmez. Glob sonuçları ayrıca söze bölünmez, o yüzden
  # dosya adındaki boşluk ("Kontrol Merkezi_0.2.1_amd64.deb") sorun değildir.
  for file in "$BUNDLE_DIR"/$pattern; do
    [ -e "$file" ] || continue
    cp -f "$file" "$DIST/"
    echo "  $(basename "$file")"
    FOUND=1

    # İMZASIZ PAKET GÜNCELLEME PAKETİ DEĞİLDİR. `.sig` yoksa dosya yayına yine
    # girer (elle indirilebilir) ama künyeye YAZILMAZ: imzasız bir girdi
    # güncelleyicide "signature could not be decoded" hatasına dönerdi.
    [ -e "$file.sig" ] || continue
    cp -f "$file.sig" "$DIST/"
    echo "  $(basename "$file").sig"
    key="$(updater_key "$file")"
    if [ -n "$key" ] && ! has_key "$key"; then
      UPDATER_KEYS+=("$key")
      UPDATER_FILES+=("$(basename "$file")")
    fi
  done
done

if [ "${#UPDATER_KEYS[@]}" -gt 0 ]; then
  mkdir -p "$DIST/updater"
  {
    printf '{\n'
    index=0
    while [ "$index" -lt "${#UPDATER_KEYS[@]}" ]; do
      comma=","
      [ "$((index + 1))" -eq "${#UPDATER_KEYS[@]}" ] && comma=""
      printf '  "%s": "%s"%s\n' "${UPDATER_KEYS[$index]}" "${UPDATER_FILES[$index]}" "$comma"
      index=$((index + 1))
    done
    printf '}\n'
  } > "$DIST/updater/$UPD_OS-$UPD_ARCH.json"
  echo "  updater/$UPD_OS-$UPD_ARCH.json (${#UPDATER_KEYS[@]} hedef)"
else
  # SESSİZ KALINMAZ: imzasız paketlerle güncelleme çalışmaz ve bu, ancak
  # kullanıcı "denetle" dediğinde ortaya çıkardı.
  echo "  UYARI: hiç imza (.sig) üretilmedi — TAURI_SIGNING_PRIVATE_KEY verilmemiş" >&2
  echo "         olabilir. Bu paketler elle kurulur; kendi kendini güncelleyemez." >&2
fi

# PAKET YOKSA AÇIK HATAYLA DÜŞÜLÜR. Eskiden buradaki ileti yalnız "altı boş"
# diyordu; hangi hedefin istendiği, nereye bakıldığı ve kabuğun ne ürettiği
# yazılmadan hata koşucu günlüğünden ayıklanamıyor.
if [ "$FOUND" != 1 ]; then
  {
    printf '  istenen hedefler : %s\n' "${BUNDLES:-(yapılandırmadaki tümü: tauri.conf.json → bundle.targets)}"
    printf '  aranan kök       : %s\n' "$BUNDLE_DIR"
    printf '  aranan desenler  : %s\n' "${PATTERNS[*]}"
    printf '  kabuğun ürettiği :\n'
    if [ -d "$BUNDLE_DIR" ]; then
      find "$BUNDLE_DIR" -mindepth 1 -maxdepth 2 | sed 's/^/    /' || true
    else
      printf '    (klasör hiç oluşmadı — `cargo tauri build` tek bir hedef üretmedi)\n'
    fi
  } >&2
  die "Paket üretilmedi; dist/ boş kalacaktı."
fi

# --- 6. depo içi kalıntılar -----------------------------------------------
#
# TAURI KAYNAKLARI İKİLİNİN YANINA KOPYALAR. `tauri.release.json` → `resources`
# altındaki her hedef, paketin içine girmeden önce `target/release/` altına da
# düşer: `backend/`, `modules/`, `config/`, `docs/`, `runtime/`.
#
# BU KOPYALAR MASUM DEĞİL. `scripts/launch-desktop.sh` geliştirmede
# `target/release/kontrol-merkezi` ikilisini çalıştırıyor; kabuk kökü ikilinin
# yanından yukarı doğru arıyor (`main.rs` → `root_beside_exe`) ve İLK olarak bu
# kopyayı buluyor. Kopyada `.git` yok, dolayısıyla çekirdek kendini kurulu
# uygulama sanıp veri dizinini sistemin kullanıcı veri klasörüne alıyor
# (`km_core/config/paths.py`) — deponun `data/` klasörüne değil. Belirti "giriş
# yapılamadı"; sebebi hiçbir günlükte yazmıyor.
#
# Bir paket üretimi, aynı depodaki geliştirme kurulumunu bu yüzden bozuyordu.
# Kalıntı burada temizlenir; `launch-desktop.sh` ayrıca `KM_ROOT` vererek
# kökü açıkça söyler. İkisi birbirinin yedeğidir: biri unutulursa öteki tutar.
#
# Silinen şey ÜRETİLMİŞ KOPYADIR, kaynak değil — depodaki `backend/`,
# `modules/`, `config/` klasörlerine dokunulmaz. Paket de etkilenmez: bu adım
# `dist/` toplandıktan sonra koşar.
say "6) Derleme kalıntıları temizleniyor"

# Adlar `tauri.release.json` → `bundle.resources` hedeflerinin İLK basamağıdır
# ve elle yazılır. Yeni bir kaynak eklenirse buraya da eklenir; JSON'u burada
# ayrıştırmak, bash 3.2'de yasak olan "komut ikamesi içinde here-document"
# yapısına geri dönmek olurdu (bkz. 2. adımdaki not).
LEFTOVERS=(backend modules config docs runtime)
CLEANED=0
for name in "${LEFTOVERS[@]}"; do
  target="$TAURI/target/release/$name"
  [ -e "$target" ] || continue
  # SİLEMEMEK DERLEMEYİ DÜŞÜRMEZ: paketler `dist/` altına çoktan kopyalandı ve
  # üretilmiş bir yayını temizlik yüzünden çöpe atmak orantısız olurdu. Ama
  # sessiz de kalınmaz — kalıntı kalırsa `launch-desktop.sh` yine `KM_ROOT`
  # verdiği için geliştirme kurulumu ayakta kalır, yalnız yedek kapı iner.
  if rm -rf "$target"; then
    echo "  silindi: target/release/$name"
    CLEANED=$((CLEANED + 1))
  else
    warn "target/release/$name silinemedi — depo içinden başlatılan kabuk bu kopyayı kök sanabilir."
  fi
done

if [ "$CLEANED" -eq 0 ]; then
  echo "  kalıntı yok"
else
  # Sessiz temizlik, bir dahaki sefere kalıntının geri geldiğini fark
  # etmemize engel olurdu.
  warn "$CLEANED kalıntı klasör silindi (target/release). Depodaki kaynaklara dokunulmadı."
fi

FINISHED=1
say "Bitti. Çıktı: $DIST"
