"""Kullanıcıya teslim edilen çıktıların nereye yazılacağı.

TEK HİYERARŞİ, UYGULAMA GENELİ. Her modül kendi klasörünü uydurursa masaüstü
birkaç ayda çöplüğe döner ve "geçen ayın kantin raporu neredeydi" sorusunun
cevabı kalmaz. Bu yüzden yol burada, tek yerde kurulur:

    Masaüstü/
      Kontrol Merkezi/
        Raporlar/
          Kantin/
            2026/
              08 - Ağustos/
                kantin-ozet-2026-08-01_2026-08-13.pdf
          Öğrenci/
            2026/
              08 - Ağustos/
                ogrenci-kartlari-3A.pdf

Masaüstü adı YERELLEŞTİRİLMİŞTİR ("Masaüstü", "Desktop", "Skrivbord"…); bu
yüzden `xdg-user-dir DESKTOP` sorulur, tahmin edilmez. O da yoksa sırayla
`~/Desktop`, `~/Masaüstü` denenir; hiçbiri yoksa depo içindeki `data/exports`
kullanılır — çıktı üretilemeyip iş durmaz.

Klasörler 0700 açılır: rapor öğrenci adı ve veli telefonu taşır.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from km_core.config.paths import DATA_SEGMENT
from km_core.files.private import DIR_MODE, ensure_private_dir

APP_FOLDER = "Kontrol Merkezi"
REPORTS_FOLDER = "Raporlar"

#: Modüllerin fallback'inde geçen klasör adı: `<kurulum kökü>/data/exports`.
#: Modüller bu yolu `ctx.module_path.parents[1] / "data" / "exports"` diye
#: kendileri kuruyor; adı burada da yazılı ki kapı aynı yere bakabilsin.
EXPORTS_SEGMENT = "exports"

_TR_MONTHS = [
    "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
]


def _xdg(name: str) -> Path | None:
    """`xdg-user-dir` çıktısı — yerelleştirilmiş klasör adına saygı duyar."""
    binary = shutil.which("xdg-user-dir")
    if binary:
        try:
            result = subprocess.run(
                [binary, name], capture_output=True, text=True, timeout=5, check=False,
            )
            candidate = Path(result.stdout.strip())
            # xdg-user-dir tanımsız klasör için ev dizinini döner; bu "yok" demektir.
            if result.returncode == 0 and candidate.is_dir() and candidate != Path.home():
                return candidate
        except (OSError, subprocess.SubprocessError):
            pass

    env = os.environ.get(f"XDG_{name}_DIR", "").strip()
    if env:
        candidate = Path(env).expanduser()
        if candidate.is_dir():
            return candidate
    return None


def desktop_dir() -> Path | None:
    """Kullanıcının masaüstü klasörü; bulunamazsa `None`."""
    found = _xdg("DESKTOP")
    if found:
        return found
    for name in ("Desktop", "Masaüstü"):
        candidate = Path.home() / name
        if candidate.is_dir():
            return candidate
    return None


def month_folder(when: datetime | None = None) -> str:
    """`08 - Ağustos` — sıralanabilir olsun diye önce sayı."""
    moment = when or datetime.now().astimezone()
    return f"{moment.month:02d} - {_TR_MONTHS[moment.month - 1]}"


def reports_root(fallback: Path) -> Path:
    """`Masaüstü/Kontrol Merkezi/Raporlar`; masaüstü yoksa `fallback`."""
    desktop = desktop_dir()
    if desktop is None:
        return fallback
    return desktop / APP_FOLDER / REPORTS_FOLDER


def output_roots(root: Path, data_root: Path, configured: list[str] | None = None,
                 ) -> list[Path]:
    """Çıktının YAZILABİLECEĞİ bütün kökler — "bu dosya bizim mi" sorusunun yeri.

    NEDEN LİSTE. Çıktıyı yazan taraf ile "yazdırılabilir mi" diye soran taraf
    kökü AYRI AYRI hesaplıyordu ve ikisi kurulu sistemde farklı yer gösteriyordu:

      · Modüller `ctx.module_path.parents[1] / "data" / "exports"` veriyor —
        yani KURULUM KÖKÜ altındaki `data/exports`, her zaman.
      · Çekirdek ise `data_dir(root) / "exports"` soruyordu ve `data_dir`
        geliştirme dışında SİSTEM veri dizinine gidiyor
        (`~/.local/share/kontrol-merkezi`, `%APPDATA%\\…`).

    Masaüstü bulunan bir makinede ikisi de `Masaüstü/Kontrol Merkezi/Raporlar`a
    çözülüp fark görünmüyordu. Sunucuda masaüstü YOK (ADR 0026): iki taraf
    fallback'e düşüyor, adresler ayrışıyor ve her "Yazdır" düğmesi
    "Bu dosya rapor klasöründe değil; güvenlik gereği verilmez" alıyordu.
    Dosya tam da uygulamanın kendi yazdığı rapordu.

    KAPI KALDIRILMIYOR, DOĞRU YERE BAKIYOR. Serbest yol kabul etmek oturumu olan
    herkese sunucudaki her dosyayı okutmak olurdu; kapının işi bu değil,
    "uygulamanın ürettiği çıktı" ile "rastgele bir dosya" ayrımını yapmak.

    `configured` = kullanıcının ayarladığı `export_path` değerleri. Modül adı
    okunmaz (K1): çağıran hangi yolları kullandığını söyler, buradaki kod
    onların nereden geldiğini bilmez.
    """
    roots = [
        # Asıl yer: masaüstü varsa oradaki hiyerarşi, yoksa çekirdeğin fallback'i.
        reports_root(data_root),
        # Çekirdeğin fallback'i (masaüstü bulunmuşsa yukarıdaki ondan farklıdır).
        data_root,
        # Modüllerin fallback'i. Hepsi aynı ifadeyi kullanıyor, o yüzden tek yol
        # hepsini kapsar; modül başına liste tutmak gerekmiyor.
        root / DATA_SEGMENT / EXPORTS_SEGMENT,
    ]
    for entry in configured or []:
        text = str(entry).strip()
        if text:
            roots.append(Path(text).expanduser())

    # `resolve()` ŞART: çağıran tarafta karşılaştırma çözülmüş yolla yapılıyor.
    # Kökü çözmeden bırakmak, sembolik bağ taşıyan bir kurulumda geçerli dosyayı
    # reddetmek demekti.
    seen: list[Path] = []
    for candidate in roots:
        try:
            resolved = candidate.resolve()
        except OSError:  # pragma: no cover — çözülemeyen yol yok sayılır
            continue
        if resolved not in seen:
            seen.append(resolved)
    return seen


def report_dir(category: str, *, fallback: Path, when: datetime | None = None,
               configured: str = "", subcategory: str = "") -> Path:
    """Bir raporun yazılacağı klasörü kurar ve döner.

    `configured` doluysa (kullanıcı `export_path` yazmışsa) hiyerarşi
    uygulanmaz: kullanıcının açık tercihi kazanır.

    `subcategory` bir ara raf açar. Kantin tek düzey kullanır (`"Kantin"`);
    mağaza gibi çok türlü rapor üreten alanlar ikinci düzeyi ister:

        Raporlar/Mağaza/Kargo/2026/08 - Ağustos/

    Ayırıcıyı kategoriye gömmek (`"Mağaza/Kargo"`) da yolu üretirdi, ama
    aşağıdaki daraltma zinciri ara klasörü atlar ve 0755 bırakırdı. Açık
    parametre hem niyeti hem izinleri doğru tutar.
    """
    if configured.strip():
        return ensure_private_dir(Path(configured).expanduser())

    moment = when or datetime.now().astimezone()
    root = reports_root(fallback)

    target = root / category
    if subcategory.strip():
        target = target / subcategory.strip()
    target = target / str(moment.year) / month_folder(moment)
    ensure_private_dir(target)

    # Ara klasörler `mkdir(parents=True)` ile umask altında doğuyor ve 0755
    # kalıyordu. Dosyalar 0600 olduğu için içerik zaten korunuyor, ama klasör
    # adları da bilgi taşır (hangi ay, hangi alan rapor üretilmiş). Kökten
    # yaprağa kadar tüm zincir daraltılır — masaüstünün kendisine dokunulmaz.
    #
    # `target.parents` yaprakdan köke gider; `root`u da kapsayacak şekilde
    # dolaşılır ve orada durulur. Böylece kaç ara düzey olursa olsun hiçbiri
    # atlanmaz.
    for level in target.parents:
        try:
            level.chmod(DIR_MODE)
        except OSError:
            pass
        if level == root:
            break
    return target
