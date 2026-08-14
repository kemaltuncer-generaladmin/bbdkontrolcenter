#!/usr/bin/env bash
#
# Kontrol Merkezi — tek tıkla tam başlatma.
#
# Masaüstündeki kısayol bu betiği çağırır. İşi: kabuğu GÜNCEL koduyla açmak ve
# çekirdeğin (web sunucusu) gerçekten ayağa kalktığını doğrulamak.
#
# Sıra:
#   1. Ortam denetimi (.venv, cargo)
#   2. Kabuk zaten açıksa ikincisini açma
#   3. Menü kaydını üret (modules/*/module.yaml → shell/registry.json)
#   4. Kaynak ikiliden yeniyse yeniden derle (ilerleme penceresiyle)
#   5. Artık kabuğa bağlı olmayan çekirdek kalıntısını indir
#   6. Kabuğu başlat, 127.0.0.1:8787 açılana kadar bekle, açılmazsa söyle
#
# Çekirdeği kabuk kendi başlatır (src-tauri/src/main.rs); burada elle
# başlatılmaz — iki çekirdek aynı porta oturmasın.

set -uo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
DESKTOP="$ROOT/apps/desktop"
TAURI="$DESKTOP/src-tauri"
BIN="$TAURI/target/release/kontrol-merkezi"
PYTHON="$ROOT/.venv/bin/python"
LOG="$ROOT/data/launcher.log"
CORE_PORT=8787   # config/default.yaml → server.port (main.rs ile aynı sabit)

# --- 0. ortam temizliği ---------------------------------------------------
# Betik snap kabuğu içinden çağrılabiliyor (VS Code'un gömülü terminali bunu
# yapar). Snap, kendi kütüphane ve XDG yollarını ortama yazar; bu yollar
# devralınırsa kabuk açılır açılmaz çöker:
#   symbol lookup error: /snap/core20/.../libpthread.so.0: __libc_pthread_init
# Masaüstü kısayolundan tıklandığında ortam zaten temizdir; burada her iki
# yoldan da AYNI ortamla açılmasını garanti ediyoruz.
if [ -n "${SNAP:-}" ] || [ -n "${SNAP_INSTANCE_NAME:-}" ]; then
    unset LD_LIBRARY_PATH LD_PRELOAD GTK_PATH GTK_EXE_PREFIX GTK_IM_MODULE_FILE \
          GDK_PIXBUF_MODULE_FILE GDK_PIXBUF_MODULEDIR GSETTINGS_SCHEMA_DIR \
          GIO_MODULE_DIR LOCPATH PYTHONHOME PYTHONPATH GTK_MODULES \
          SNAP SNAP_NAME SNAP_INSTANCE_NAME SNAP_ARCH SNAP_COMMON SNAP_CONTEXT \
          SNAP_DATA SNAP_LIBRARY_PATH SNAP_REEXEC SNAP_REVISION SNAP_USER_COMMON \
          SNAP_USER_DATA SNAP_VERSION SNAP_EUID SNAP_UID SNAP_REAL_HOME \
          SNAP_LAUNCHER_ARCH_TRIPLET GIO_LAUNCHED_DESKTOP_FILE 2>/dev/null

    export XDG_DATA_HOME="$HOME/.local/share"
    export XDG_CONFIG_HOME="$HOME/.config"
    export XDG_CACHE_HOME="$HOME/.cache"
    export XDG_DATA_DIRS="/usr/local/share:/usr/share"
    export XDG_CONFIG_DIRS="/etc/xdg/xdg-ubuntu:/etc/xdg"

    # PATH'ten snap kabuğunun kendi klasörlerini ayıkla.
    CLEAN_PATH=""
    IFS=':' read -ra _parts <<<"$PATH"
    for part in "${_parts[@]}"; do
        case "$part" in /snap/*|*/snap/code/*) continue ;; esac
        CLEAN_PATH="${CLEAN_PATH:+$CLEAN_PATH:}$part"
    done
    export PATH="${CLEAN_PATH:-/usr/local/bin:/usr/bin:/bin}"
fi

export PATH="$HOME/.cargo/bin:$PATH"

mkdir -p "$(dirname "$LOG")"

log() { printf '%s  %s\n' "$(date '+%F %T')" "$*" >>"$LOG"; }

# Hata masaüstünde görünür: kısayoldan açılınca terminal yoktur, sessizce
# ölmek en kötüsüdür.
die() {
    # `\n` kaçışları burada gerçek satır sonuna çevrilir; yoksa pencerede düz
    # metin olarak görünür.
    local msg
    msg="$(printf '%b' "$1")"
    log "HATA: ${msg//$'\n'/ | }"
    if command -v zenity >/dev/null 2>&1; then
        zenity --error --width=520 --title="Kontrol Merkezi" \
            --text="$msg"$'\n\n'"Günlük: $LOG" 2>/dev/null
    else
        notify-send -u critical "Kontrol Merkezi" "$msg" 2>/dev/null
    fi
    exit 1
}

note() { notify-send -a "Kontrol Merkezi" "Kontrol Merkezi" "$1" 2>/dev/null; }

log "=== başlatma isteği ==="

# --- 1. ortam -------------------------------------------------------------
[ -x "$PYTHON" ] || die "Python ortamı yok: .venv bulunamadı.\nÖnce: scripts/install-deps.sh"
command -v cargo >/dev/null 2>&1 || CARGO_MISSING=1

# --- 2. tek örnek ---------------------------------------------------------
# Aynı anda iki kabuk = iki çekirdek denemesi, iki pencere, karışık yazıcı işi.
if pgrep -x kontrol-merkezi >/dev/null 2>&1; then
    log "kabuk zaten çalışıyor, ikincisi açılmadı"
    note "Zaten çalışıyor — açık pencereye geçin."
    exit 0
fi

# --- 3. menü kaydı --------------------------------------------------------
# Modül eklendiyse/açılıp kapandıysa menü buradan güncellenir. Ucuz (~0,3 sn),
# her açılışta koşulsuz çalışır.
if ! "$PYTHON" "$ROOT/tools/build-ui-registry.py" >>"$LOG" 2>&1; then
    die "Menü kaydı üretilemedi (tools/build-ui-registry.py).\nModül manifestlerinden biri bozuk olabilir."
fi
log "menü kaydı güncellendi"

# --- 4. gerekiyorsa derle -------------------------------------------------
# Kabuk arayüzü ikiliye gömülür: shell/ altı değişmişse ikili eskimiştir.
#
# Ölçü TARİH DEĞİL İÇERİKtir. Bir üstteki adım `registry.json` ve `panels/`
# dosyalarını her açılışta yeniden yazıyor; tarihe bakılsaydı hiçbir şey
# değişmese bile her tıklamada 40 saniye derlerdik. Özet almak 0,03 sn sürüyor.
STAMP="$TAURI/target/.km-ui-stamp"

ui_hash() {
    {
        find "$DESKTOP/shell" "$TAURI/src" -type f -exec sha1sum {} + 2>/dev/null
        sha1sum "$TAURI/tauri.conf.json" "$TAURI/Cargo.toml" 2>/dev/null
    } | sort -k2 | sha1sum | cut -d' ' -f1
}

HASH="$(ui_hash)"

needs_build() {
    [ -x "$BIN" ] || { echo "ikili yok"; return 0; }
    [ -f "$STAMP" ] || { echo "derleme damgası yok"; return 0; }
    [ "$(cat "$STAMP")" = "$HASH" ] && return 1
    echo "arayüz/kabuk kaynağı değişmiş"
    return 0
}

if reason="$(needs_build)"; then
    log "yeniden derleme gerekiyor ($reason)"
    [ -z "${CARGO_MISSING:-}" ] || die "Arayüz değişmiş, yeniden derlemek gerekiyor ama cargo bulunamadı.\nKurulum: scripts/install-deps.sh --with-desktop"

    BUILD_LOG="$(mktemp)"
    ( cd "$TAURI" && cargo build --release ) >"$BUILD_LOG" 2>&1 &
    BUILD_PID=$!

    if command -v zenity >/dev/null 2>&1; then
        (
            while kill -0 "$BUILD_PID" 2>/dev/null; do
                echo "#Arayüz değişmiş — güncel sürüm derleniyor…"
                sleep 1
            done
        ) | zenity --progress --pulsate --auto-close --no-cancel --width=420 \
              --title="Kontrol Merkezi" --text="Derleniyor…" 2>/dev/null &
        ZENITY_PID=$!
    else
        note "Arayüz değişmiş — derleniyor, bir dakika sürebilir."
        ZENITY_PID=""
    fi

    wait "$BUILD_PID"; BUILD_RC=$?
    [ -n "$ZENITY_PID" ] && wait "$ZENITY_PID" 2>/dev/null

    cat "$BUILD_LOG" >>"$LOG"
    if [ "$BUILD_RC" -ne 0 ]; then
        TAIL="$(tail -n 12 "$BUILD_LOG")"
        rm -f "$BUILD_LOG"
        die "Derleme başarısız:\n\n$TAIL"
    fi
    rm -f "$BUILD_LOG"
    # Damga derlemeden SONRA, derlenen içeriğin özetiyle yazılır.
    ui_hash >"$STAMP"
    log "derleme tamam"
else
    log "ikili güncel, derleme atlandı"
fi

# --- 5. sahipsiz çekirdek --------------------------------------------------
# Kabuk kapanınca çekirdeği de indirir; ama kabuk çökerek ölmüşse çekirdek
# portu tutmaya devam eder ve yeni kabuk ESKİ koda bağlanır. Kabuk çalışmıyor
# olduğuna yukarıda baktık; buradaki kalıntı gerçekten sahipsizdir.
if pgrep -f "km_core.main" >/dev/null 2>&1; then
    log "sahipsiz çekirdek bulundu, indiriliyor"
    pkill -f "km_core.main"
    for _ in $(seq 1 20); do
        pgrep -f "km_core.main" >/dev/null 2>&1 || break
        sleep 0.25
    done
    pgrep -f "km_core.main" >/dev/null 2>&1 && pkill -9 -f "km_core.main"
fi

# --- 6. başlat ve doğrula --------------------------------------------------
log "kabuk başlatılıyor: $BIN"
cd "$ROOT" || die "Proje klasörüne girilemedi: $ROOT"
nohup "$BIN" >>"$LOG" 2>&1 &
SHELL_PID=$!

# Çekirdek portu açılana kadar bekle. Uvicorn + modül yüklemesi birkaç saniye
# sürüyor; 30 sn cömert bir üst sınır.
core_up() { (exec 3<>"/dev/tcp/127.0.0.1/$CORE_PORT") 2>/dev/null; }

UP=""
for _ in $(seq 1 60); do
    if ! kill -0 "$SHELL_PID" 2>/dev/null; then
        die "Kabuk açılır açılmaz kapandı.\nGünlüğün sonuna bakın: $LOG"
    fi
    if core_up; then UP=1; break; fi
    sleep 0.5
done

if [ -n "$UP" ]; then
    log "çekirdek ayakta: 127.0.0.1:$CORE_PORT"
else
    # Pencere açık, çekirdek yok: uygulama yarım çalışır. Sessiz kalmıyoruz.
    log "UYARI: çekirdek 30 sn içinde ayağa kalkmadı"
    TAIL="$(tail -n 12 "$LOG")"
    command -v zenity >/dev/null 2>&1 && zenity --warning --width=560 \
        --title="Kontrol Merkezi" \
        --text="Pencere açıldı ama çekirdek (127.0.0.1:$CORE_PORT) yanıt vermiyor."$'\n'"Ekranlar veri getiremeyebilir."$'\n\n'"Son satırlar:"$'\n'"$TAIL" 2>/dev/null &
fi

log "başlatma tamam"
exit 0
