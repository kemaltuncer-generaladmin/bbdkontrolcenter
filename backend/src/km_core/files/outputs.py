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

from km_core.files.private import DIR_MODE, ensure_private_dir

APP_FOLDER = "Kontrol Merkezi"
REPORTS_FOLDER = "Raporlar"

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
