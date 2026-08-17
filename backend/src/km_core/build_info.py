"""Paketin kimliği — hangi koddan derlendiği.

NEDEN VAR. 17.08.2026'da aynı arıza üç kez "düzeltildi" ve üç kez geri geldi;
sonunda anlaşıldı ki paket ESKİ KODDAN derlenmişti. Bunu anlamanın hiçbir yolu
yoktu:

  · Üç ayrı sürüm sabiti vardı ve ikisi (`app.py` → 0.1.0, istemcinin merkeze
    bildirdiği sürüm) hiç değişmiyordu.
  · Değişen tek sayı `tauri.conf.json`daki sürümdü ve o da commit'i tek anlamlı
    belirlemiyordu: ÜÇ ayrı commit aynı "0.1.2"yi taşıyordu.
  · `scripts/build-release.sh` git durumuna hiç bakmıyordu; `git pull`
    yapılmadan derlemek sessizce mümkündü.

Sonuç: "düzelttim" ile "sende düzelmedi" arasındaki fark ölçülemiyordu ve her
tur baştan teşhis gerekiyordu.

BU DOSYA DERLEME ANINDA ÜRETİLİR (`scripts/build-release.sh` → `--stamp`).
Depoda duran hâli geliştirme kurulumunun cevabıdır: "depodan çalışıyorum".
"""

from __future__ import annotations

import subprocess
from pathlib import Path

#: Derleme betiği bu üç değeri paketleme anında `_build_stamp.py` içine yazar
#: (git dışıdır). Dosya yoksa depodan çalışıyoruz demektir.
#: Dosya derleme anında üretilir ve git dışıdır; tip denetçisi onu göremez —
#: `ignore[import-not-found]` bu yüzden, eksikliği HATA DEĞİL beklenen hâldir.
try:  # pragma: no cover — yalnız paketlenmiş kurulumda dolu
    from km_core._build_stamp import (  # type: ignore[import-not-found]
        BUILT_AT,
        COMMIT,
        VERSION,
    )
except ImportError:
    COMMIT = ""
    BUILT_AT = ""
    VERSION = ""


def _git(root: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, timeout=5, check=True
        ).stdout.strip()
    except Exception:  # noqa: BLE001 — git yoksa/paketse sessizce boş dön
        return ""


def build_info(root: Path) -> dict[str, str]:
    """Çalışan kopyanın künyesi.

    Paketlenmiş uygulamada damga sabitlerden gelir. Depodan çalışırken git'e
    sorulur ve **kirli çalışma ağacı işaretlenir** — geliştirme makinesinde
    "hangi kod" sorusunun cevabı commit değil, commit + kaydedilmemiş değişiklik.
    """
    if COMMIT:
        return {"commit": COMMIT, "builtAt": BUILT_AT, "version": VERSION, "source": "paket"}

    commit = _git(root, "rev-parse", "--short", "HEAD")
    if not commit:
        return {"commit": "", "builtAt": "", "version": "", "source": "bilinmiyor"}

    kirli = bool(_git(root, "status", "--porcelain"))
    return {
        "commit": f"{commit}{'+degisiklik' if kirli else ''}",
        "builtAt": "",
        "version": "",
        "source": "depo",
    }
