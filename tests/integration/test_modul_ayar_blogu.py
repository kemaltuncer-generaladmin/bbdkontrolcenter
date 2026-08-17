"""Manifestteki `settings` bloğu — şema ve doğrulama (ADR 0018 §1).

NEDEN TEST. Bu bloğun iki kuralı birbirinin tersi yönde çeker ve ikisi de
sessizce kaybolabilir:

1. **Geçersiz blok modülü DÜŞÜRMEZ** (K7). Blok `module.schema.json` içinde
   manifest doğrulamasının bir parçası olsaydı, bir alan başlığındaki hata
   modülün tamamını yüklenemez hâle getirirdi. Bu yüzden yapı `$defs/settings`
   altında durur ve ayrı bir kapıda uygulanır — modül sekmesiz yüklenir.
2. **İzin ilan etmeyen blok REDDEDİLİR** (K9). Varsayılan kapalıdır. Bu kapı
   kaldırılırsa hata görünmez: sekme herkese açılır ve kimse fark etmez.

Ayar EKRANI bu testin konusu değildir; burada yalnız sözleşme sınanır.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from km_core.kernel.kernel import Kernel

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "docs" / "schemas" / "module.schema.json"

GECERLI_BLOK: dict[str, Any] = {
    "tab": "Antivirüs",
    "requires": ["antivirus.manage"],
    "groups": [
        {
            "id": "tarama",
            "title": "Tarama",
            "fields": [
                {
                    "key": "schedule",
                    "type": "cron",
                    "title": "Otomatik tarama zamanı",
                    "default": "0 3 * * *",
                },
                {"key": "quick_paths", "type": "path_list", "title": "Hızlı tarama yolları"},
                {"key": "deep", "type": "bool", "title": "Derin tarama", "default": False},
                {"key": "timeout", "type": "int", "title": "Süre sınırı", "min": 1, "max": 600},
                {"key": "note", "type": "text", "title": "Not", "max_length": 120},
                {
                    "key": "engine",
                    "type": "select",
                    "title": "Motor",
                    "options": ["clamdscan", {"value": "clamscan", "title": "Daemon'suz"}],
                    "default": "clamdscan",
                },
                {"key": "log_path", "type": "path", "title": "Günlük dosyası"},
            ],
        }
    ],
}


def _kesfet(tmp_path: Path, module_id: str = "antivirus", **ek: Any) -> Kernel:
    manifest: dict[str, Any] = {
        "id": module_id,
        "name": module_id,
        "version": "0.1.0",
        "sdk": ">=0.1,<1.0",
        "entrypoint": "backend.module:register",
    }
    manifest.update(ek)
    dizin = tmp_path / module_id
    dizin.mkdir(parents=True)
    (dizin / "module.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    bos = cast(Any, None)
    kernel = Kernel(bos, bos, bos, bos, platform="linux")
    kernel.discover(tmp_path, SCHEMA)
    return kernel


def test_gecerli_blok_okunur(tmp_path: Path) -> None:
    kernel = _kesfet(tmp_path, settings=GECERLI_BLOK)
    kayit = kernel.records["antivirus"]

    assert kayit.settings_error == ""
    assert kayit.settings is not None
    assert kayit.settings["tab"] == "Antivirüs"
    assert kernel.problems == []


def test_blok_yoksa_hata_da_yoktur(tmp_path: Path) -> None:
    """Ayar sekmesi ilan etmemek bir eksiklik değildir."""
    kayit = _kesfet(tmp_path).records["antivirus"]

    assert kayit.settings is None
    assert kayit.settings_error == ""


@pytest.mark.parametrize(
    ("ad", "alan"),
    [
        ("bilinmeyen tip", {"key": "renk", "type": "renk", "title": "Renk"}),
        ("seçeneksiz select", {"key": "motor", "type": "select", "title": "Motor"}),
        ("metinde sayı sınırı", {"key": "not", "type": "text", "title": "Not", "min": 3}),
        ("sayıda metin varsayılanı", {"key": "sure", "type": "int", "title": "Süre",
                                      "default": "üç"}),
        ("cron olmayan cron", {"key": "plan", "type": "cron", "title": "Plan",
                               "default": "her gece"}),
        ("başlıksız alan", {"key": "sessiz", "type": "bool"}),
    ],
)
def test_gecersiz_blok_modulu_DUSURMEZ(tmp_path: Path, ad: str, alan: dict[str, Any]) -> None:
    """Modül yüklenmeye devam eder, yalnız sekmesi düşer (K7)."""
    blok = {
        "tab": "Antivirüs",
        "requires": ["antivirus.manage"],
        "groups": [{"id": "tarama", "title": "Tarama", "fields": [alan]}],
    }
    kernel = _kesfet(tmp_path, settings=blok)

    # Modül elden çıkmadı: manifest geçerli, kayıt yerinde, problem listesi boş.
    assert "antivirus" in kernel.records, f"{ad}: modül düştü"
    assert kernel.problems == []

    kayit = kernel.records["antivirus"]
    assert kayit.settings is None, f"{ad}: geçersiz blok kabul edildi"
    assert "settings" in kayit.settings_error


@pytest.mark.parametrize("requires", [None, []])
def test_izin_ilan_etmeyen_blok_reddedilir(tmp_path: Path, requires: list[str] | None) -> None:
    """Varsayılan kapalıdır: izinsiz sekme kurulmaz (K9)."""
    blok: dict[str, Any] = {
        "tab": "Antivirüs",
        "groups": [{"id": "tarama", "title": "Tarama",
                    "fields": [{"key": "deep", "type": "bool", "title": "Derin"}]}],
    }
    if requires is not None:
        blok["requires"] = requires

    kayit = _kesfet(tmp_path, settings=blok).records["antivirus"]

    assert kayit.settings is None
    assert "izin" in kayit.settings_error


def test_blok_nesne_degilse_reddedilir(tmp_path: Path) -> None:
    kayit = _kesfet(tmp_path, settings=["Antivirüs"]).records["antivirus"]

    assert kayit.settings is None
    assert kayit.settings_error != ""
