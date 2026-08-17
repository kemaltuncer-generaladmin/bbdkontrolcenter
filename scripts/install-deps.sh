#!/usr/bin/env bash
# Bağımlılık kurulumu.
#
# Sistem paketlerini deploy/packaging/system-packages.yaml dosyasından,
# modül bağımlılıklarını modules/*/module.yaml içindeki `dependencies`
# bloğundan toplar. Çekirdeğin listesine dokunmadan modül eklenebilir (K6).
#
# Kullanım:
#   scripts/install-deps.sh              # çekirdek + kurulu modüllerin bağımlılıkları
#   scripts/install-deps.sh --with-desktop   # Tauri derleme bağımlılıklarını da kur
#   scripts/install-deps.sh --dry-run        # hiçbir şey kurma, ne yapılacağını yaz
#
# Sürücüler (hplip vb.) apt'tan gelir, depoya kopyalanmaz — ADR 0008.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DRY_RUN=0
WITH_DESKTOP=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --with-desktop) WITH_DESKTOP=1 ;;
    *) echo "Bilinmeyen seçenek: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
run() { if [ "$DRY_RUN" = 1 ]; then echo "  [dry-run] $*"; else "$@"; fi; }

# ---------------------------------------------------------------- sistem
say "1) Sistem paketleri toplanıyor"

SYSTEM_PKGS="$(python3 - "$WITH_DESKTOP" <<'PY'
import sys, pathlib, re
with_desktop = sys.argv[1] == "1"
try:
    import yaml
except ImportError:
    print("__NO_YAML__"); sys.exit(0)

# Bu platformda çalışmayan modülün bağımlılığı TOPLANMAZ — Windows kurulumunda
# clamav-daemon istemenin anlamı yok (ADR 0022 §4). Alanı olmayan modül her
# platformda geçerlidir. Liste stdout'a değil stderr'e yazılır: stdout paket
# listesidir, kirletilemez.
def platform_name():
    if sys.platform.startswith("win"): return "windows"
    if sys.platform == "darwin": return "macos"
    return "linux"

HERE = platform_name()

def skipped(manifest):
    declared = manifest.get("platforms")
    return bool(declared) and HERE not in declared

pkgs = []
data = yaml.safe_load(open("deploy/packaging/system-packages.yaml")) or {}
for group, body in data.items():
    if group == "desktop" and not with_desktop:
        continue
    pkgs += (body or {}).get("packages", []) or []

# modüllerin kendi sistem bağımlılıkları
for mf in sorted(pathlib.Path("modules").glob("*/module.yaml")):
    m = yaml.safe_load(open(mf)) or {}
    if skipped(m):
        print(f"  {m.get('id', mf.parent.name)} — bu platformda çalışmaz "
              f"({', '.join(m['platforms'])}), bağımlılığı atlandı", file=sys.stderr)
        continue
    pkgs += ((m.get("dependencies") or {}).get("system") or [])

seen, out = set(), []
for p in pkgs:
    if p not in seen:
        seen.add(p); out.append(p)
print(" ".join(out))
PY
)"

if [ "$SYSTEM_PKGS" = "__NO_YAML__" ]; then
  echo "  pyyaml yok — sistem paket listesi okunamadı."
  echo "  Önce şunu çalıştırın:  sudo apt-get install -y python3-yaml"
  exit 1
fi

MISSING=""
for p in $SYSTEM_PKGS; do
  dpkg -s "$p" >/dev/null 2>&1 || MISSING="$MISSING $p"
done

if [ -z "${MISSING// }" ]; then
  echo "  Tüm sistem paketleri kurulu."
else
  echo "  Eksik:$MISSING"
  run sudo apt-get update
  run sudo apt-get install -y $MISSING
fi

# ---------------------------------------------------------------- python
say "2) Python sanal ortamı"
VENV="$ROOT/.venv"
[ -d "$VENV" ] || run python3 -m venv "$VENV"
run "$VENV/bin/python" -m pip install --upgrade pip

say "3) Python bağımlılıkları"
PY_REQS="$(python3 - <<'PY'
import sys, pathlib, tomllib

# Sistem paketlerindeki eleme burada da geçerlidir: elenen modülün Python
# bağımlılığı da toplanmaz (ADR 0022 §4).
def platform_name():
    if sys.platform.startswith("win"): return "windows"
    if sys.platform == "darwin": return "macos"
    return "linux"

HERE = platform_name()

def skipped(manifest):
    declared = manifest.get("platforms")
    return bool(declared) and HERE not in declared

reqs = []
data = tomllib.load(open("backend/pyproject.toml", "rb"))
proj = data["project"]
reqs += proj.get("dependencies", [])
extras = proj.get("optional-dependencies", {})
# çalışma zamanı yetenekleri — dev ve isteğe bağlı sürücüler hariç
for name in ("ssh", "database", "printer", "audio", "notify"):
    reqs += extras.get(name, [])

try:
    import yaml
    for mf in sorted(pathlib.Path("modules").glob("*/module.yaml")):
        m = yaml.safe_load(open(mf)) or {}
        if skipped(m):
            continue
        reqs += ((m.get("dependencies") or {}).get("python") or [])
except ImportError:
    pass

seen, out = set(), []
for r in reqs:
    if r not in seen:
        seen.add(r); out.append(r)
print("\n".join(out))
PY
)"

echo "$PY_REQS" | sed 's/^/  /'
if [ "$DRY_RUN" = 0 ]; then
  echo "$PY_REQS" | "$VENV/bin/pip" install -r /dev/stdin
fi

say "4) Geliştirme araçları"
# Not: proje henüz kod içermiyor, bu yüzden editable kurulum (pip install -e)
# yapılmaz. Kaynak paketler yazıldığında bu adım -e "backend[dev]" olur.
DEV_REQS="$(python3 - <<'PY'
import tomllib
d = tomllib.load(open("backend/pyproject.toml", "rb"))
print("\n".join(d["project"]["optional-dependencies"].get("dev", [])))
PY
)"
echo "$DEV_REQS" | sed 's/^/  /'
if [ "$DRY_RUN" = 0 ]; then
  echo "$DEV_REQS" | "$VENV/bin/pip" install -r /dev/stdin
fi

# ---------------------------------------------------------------- tauri
if [ "$WITH_DESKTOP" = 1 ]; then
  say "5) Rust araç zinciri (Tauri)"
  if command -v cargo >/dev/null 2>&1; then
    echo "  cargo kurulu: $(cargo -V)"
  else
    echo "  cargo YOK. Kurmak için:"
    echo "    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
    echo "  (apt ile değil, rustup ile kurulur.)"
  fi
fi

say "Bitti."
echo "Sanal ortam: $VENV"
echo "Etkinleştirme: source .venv/bin/activate"
