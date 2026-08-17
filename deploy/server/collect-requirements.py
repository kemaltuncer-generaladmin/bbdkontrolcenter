"""Sunucu imajının Python bağımlılıklarını İLAN EDİLEN kaynaklardan toplar.

K11 — bağımlılık ilan edilir, kopyalanmaz. Bu betik `scripts/install-deps.sh`
ile AYNI kaynakları okur: `backend/pyproject.toml` ve her modülün kendi
`dependencies.python` bloğu. İkinci bir liste tutulsaydı (imaj için ayrı bir
`requirements.txt`) er geç ayrışır ve modülün biri sunucuda "import hatası"
ile düşerdi — hem de yalnız üretimde.

## Sunucuda hangi yetenekler var

`ssh`, `database` ve `notify` alınır; `printer` ve `audio` ALINMAZ.

  · `printer` alınmaz çünkü `pycups` DEPODA HİÇ IMPORT EDİLMİYOR:
    `km_platform/printer/cups.py` `lp`/`lpstat` ikililerini çağırıyor.
    Kurmak `libcups2-dev` + `build-essential` getirir, imajı üç katına
    çıkarır ve karşılığında hiçbir satır çalışmaz. Yetenek yine kayıtlıdır;
    sunucuda yazıcı bulamaz ve "yazıcı yok" der (K7).
  · `audio` alınmaz: sunucuda hoparlör yok ve zil zaten bbdstore köprüsü
    üzerinden Windows ajanına gidiyor (`modules/bell/backend/bridge.py`).

`platforms:` alanı sunucuyu dışlayan modüllerin bağımlılığı toplanmaz
(ADR 0022 §4) — `antivirus` yalnız Linux'ta yüklenir ve sunucu Linux'tur, o
yüzden o modül DAHİLDİR.
"""

from __future__ import annotations

import pathlib
import sys

import tomllib
import yaml

#: Sunucuda kurulan yetenek extra'ları. `printer` ve `audio` bilerek yok
#: (gerekçesi başlıkta).
SERVER_EXTRAS = ("ssh", "database", "notify")

#: İmaj Linux'tur; `platforms:` alanı bunu dışlayan modül elenir.
PLATFORM = "linux"


def main(root: pathlib.Path) -> int:
    requirements: list[str] = []

    data = tomllib.loads((root / "backend/pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]
    requirements += project.get("dependencies", [])
    extras = project.get("optional-dependencies", {})
    for name in SERVER_EXTRAS:
        requirements += extras.get(name, [])

    for manifest_file in sorted((root / "modules").glob("*/module.yaml")):
        manifest = yaml.safe_load(manifest_file.read_text(encoding="utf-8")) or {}
        declared = manifest.get("platforms")
        if declared and PLATFORM not in declared:
            continue
        requirements += ((manifest.get("dependencies") or {}).get("python") or [])

    seen: set[str] = set()
    for entry in requirements:
        # Manifestlerde satır sonu yorumu olabiliyor; pip onu anlamaz.
        cleaned = entry.split("#", 1)[0].strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            print(cleaned)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")))
