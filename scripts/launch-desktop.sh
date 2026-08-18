#!/usr/bin/env bash
#
# Kontrol Merkezi — tek tıkla tam başlatma.
#
# BU BETİK GELİŞTİRME KURULUMU İÇİNDİR (ADR 0023). Depo klasöründen çalışır,
# `.venv` kullanır ve arayüz değiştiyse kabuğu yeniden derler. Kurulan
# uygulamada bunların hiçbiri olmaz: orada gömülü Python vardır, derleme
# yapılmaz ve veri dizini deponun içinde değildir. Kurulabilir paket
# `scripts/build-release.sh` (Linux/macOS) ve `scripts/build-release.ps1`
# (Windows) ile üretilir.
#
# Masaüstündeki kısayol bu betiği çağırır. İşi: kabuğu GÜNCEL koduyla açmak ve
# çekirdeğin (web sunucusu) gerçekten ayağa kalktığını doğrulamak.
#
# Sıra:
#   1. Ortam denetimi (.venv, cargo)
#   2. Kabuk zaten açıksa ikincisini açma
#   3. `main`'den güncel kodu al (yerel değişiklik varsa DOKUNMAZ)
#   4. Menü kaydını üret (modules/*/module.yaml → shell/registry.json)
#   5. Kaynak ikiliden yeniyse yeniden derle (ilerleme penceresiyle)
#   6. Artık kabuğa bağlı olmayan çekirdek kalıntısını indir
#   7. Kabuğu başlat, 127.0.0.1:8787 açılana kadar bekle, açılmazsa söyle
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

# --- 3. güncel kodu al ----------------------------------------------------
# UZAKTAN GELİŞTİRME İÇİN. Kod başka bir makinede yazılıp `main`'e
# gönderilebiliyor; bu makine açılışta onu alsın ve derleme o kodla koşsun.
# Sıra önemli: çekme, menü kaydından ve derlemeden ÖNCE olmalı — yoksa bu
# açılış eski kodu derler, yenisi ancak bir sonrakinde görünür.
#
# ÜÇ ŞEYİ ASLA YAPMAZ:
#
#   1. YEREL DEĞİŞİKLİĞİ EZMEZ. Çalışma ağacı kirliyse çekme HİÇ denenmez.
#      Burası bir geliştirme kurulumu; yarım kalmış bir işi `git pull` ile
#      ezmek, kurtarılamayacak tek şeyi kurtarılamaz hâle getirirdi.
#   2. BİRLEŞTİRME YAPMAZ. Yalnız `--ff-only`. Yerelde push edilmemiş commit
#      varsa çekme durur ve söyler; arka planda çakışma çözmeye çalışan bir
#      açılış betiği, sahibinin haberi olmadan geçmişi değiştirirdi.
#   3. AÇILIŞI ENGELLEMEZ. Ağ yoksa ya da uzak sunucu yanıt vermiyorsa
#      uygulama YİNE AÇILIR, eldeki kodla. `timeout` şart: `git fetch`
#      kimlik doğrulama beklerken sonsuza kadar asılı kalabilir ve kullanıcı
#      tıkladığı simgenin neden açılmadığını anlayamazdı.
if [ -d "$ROOT/.git" ] && command -v git >/dev/null 2>&1; then
    if [ -n "$(git -C "$ROOT" status --porcelain 2>/dev/null)" ]; then
        log "git: çalışma ağacı kirli, çekme atlandı"
        note "Yerelde kaydedilmemiş değişiklik var — kod güncellenmedi."
    else
        # DAL AÇIKÇA VERİLİR, TAKİBE GÜVENİLMEZ. Çıplak `git pull` yerel dalın
        # upstream'inin kurulu olmasını ister. BU MAKİNEDE KURULU DEĞİLDİ:
        # çekme her açılışta "no tracking information" ile düşüyor, kullanıcı
        # "kod güncellenemedi" bildirimi görüp eski kodla açıyor ve sebep
        # ancak günlüğe bakınca anlaşılıyordu. Taze bir klonda ya da başka bir
        # makinede aynı tuzağın yeniden kurulmaması için dal adı doğrudan
        # geçilir.
        DAL="$(git -C "$ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'HEAD')"
        ONCE="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo '?')"
        if [ "$DAL" = "HEAD" ]; then
            # Ayrık HEAD: bir dalda değiliz, ileri sarılacak bir şey de yok.
            log "git: ayrık HEAD, çekme atlandı"
        elif timeout 45 git -C "$ROOT" pull --ff-only --quiet origin "$DAL" >>"$LOG" 2>&1; then
            SONRA="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo '?')"
            if [ "$ONCE" != "$SONRA" ]; then
                ADET="$(git -C "$ROOT" rev-list --count "$ONCE..$SONRA" 2>/dev/null || echo '?')"
                log "git: $ONCE → $SONRA ($ADET commit)"
                note "Güncel kod alındı ($ADET commit) — derleniyor."

                # BAĞIMLILIK DEĞİŞTİYSE SÖYLE. Yeni bir Python paketi gelmişse
                # `.venv` eskir ve arıza çalışma anında, anlaşılmaz bir import
                # hatası olarak çıkar. Kurulumu betik KENDİ BAŞINA yapmaz:
                # dakikalar sürebilir ve tek tıkla açılış beklentisini bozar.
                if git -C "$ROOT" diff --name-only "$ONCE..$SONRA" 2>/dev/null \
                     | grep -qE '^(backend/pyproject\.toml|modules/[^/]+/module\.yaml)$'; then
                    log "git: bağımlılık bildirimleri değişti"
                    note "Bağımlılıklar değişmiş olabilir — scripts/install-deps.sh çalıştırın."
                fi
            else
                log "git: zaten güncel ($ONCE)"
            fi
        else
            # Sebebi söylenir ama açılış SÜRER: ağ yok, kimlik doğrulama
            # istendi ya da yerelde push edilmemiş commit var (ff mümkün değil).
            log "git: çekilemedi, eldeki kodla devam ediliyor"
            note "Kod güncellenemedi (ağ ya da yerel commit) — mevcut sürümle açılıyor."
        fi
    fi
else
    log "git: depo değil ya da git yok, çekme atlandı"
fi

# --- 4. menü kaydı --------------------------------------------------------
# Modül eklendiyse/açılıp kapandıysa menü buradan güncellenir. Ucuz (~0,3 sn),
# her açılışta koşulsuz çalışır.
if ! "$PYTHON" "$ROOT/tools/build-ui-registry.py" >>"$LOG" 2>&1; then
    die "Menü kaydı üretilemedi (tools/build-ui-registry.py).\nModül manifestlerinden biri bozuk olabilir."
fi
log "menü kaydı güncellendi"

# --- 5. gerekiyorsa derle -------------------------------------------------
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

# --- 6. sahipsiz çekirdek --------------------------------------------------
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

# --- 7. başlat ve doğrula --------------------------------------------------
log "kabuk başlatılıyor: $BIN"
cd "$ROOT" || die "Proje klasörüne girilemedi: $ROOT"

# ÇEKİRDEK KÖKÜ AÇIKÇA SÖYLENİR — tahmine bırakılmaz.
#
# Kabuk kökü kendi başına arıyor (`main.rs` → `find_root`): önce `KM_ROOT`,
# sonra ikilinin yanından yukarı doğru `backend/src/km_core` taşıyan ilk
# klasör. İkinci ölçüt bu betikte SESSİZCE YANLIŞ SONUÇ VERİYORDU.
#
# `scripts/build-release.sh` depo içinde koşturulduğunda Tauri, paket
# kaynaklarını (`backend/`, `modules/`, `config/`, `runtime/`) ikilinin YANINA
# — yani `target/release/` altına — kopyalıyor. Kabuk o klasörü kök sayıyor;
# orada `.git` olmadığı için çekirdek kendini KURULU UYGULAMA sanıyor ve veri
# dizini olarak sistemin kullanıcı veri klasörünü seçiyor
# (`km_core/config/paths.py`), deponun `data/` klasörünü değil.
#
# Sonuç: aynı ikili, aynı depo, BAŞKA veritabanı. Kullanıcılar, kasa ve
# ayarlar görünmez oluyor; belirti "giriş yapılamadı" oluyor ve hiçbir günlükte
# "veri dizini değişti" yazmıyor.
#
# Değişkeni burada vermek en dar ve en kesin çözüm: geliştirme başlatıcısının
# kökü NE OLDUĞU zaten kesin biliniyor (betiğin kendi konumundan türedi) ve
# arama sırasının ilk basamağı bu. Kalıntıları ayrıca `build-release.sh`
# temizler; ikisi birbirinin yedeğidir.
export KM_ROOT="$ROOT"
log "KM_ROOT=$ROOT"

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
