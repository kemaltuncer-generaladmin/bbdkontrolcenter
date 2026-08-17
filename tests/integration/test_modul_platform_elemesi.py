"""Modül platform kapsamı keşifte elenir (ADR 0022).

NEDEN TEST. Eleme bir "yükleme sonrası kontrol" olsaydı görünmezdi: modül
yüklenir, göçü koşar, menüye girer ve ancak tıklandığında "bu platformda yok"
derdi — kullanıcı bunu arıza olarak okur. Bu yüzden burada sınanan şey elemenin
YERİ: elenen modül çekirdeğin kayıt defterine hiç girmez, dolayısıyla router'ı
monte edilemez, göçü koşamaz, görevi planlanamaz ve menüye giremez.

İkinci sınanan şey varsayılan: `platforms` alanı OLMAYAN modül her platformda
yüklenir. Ters varsayılan, alanı olmayan mevcut manifestlerin hepsini bu
kararın yürürlüğe girdiği gün kapatırdı.

Eleme sessiz de değildir: `Kernel.skipped` içindeki cümle açılış günlüğüne
aynen düşer. Yönetici ekranını neden bulamadığını orada okur.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import jsonschema
import pytest
import yaml

from km_core.kernel.kernel import Kernel
from km_core.kernel.platforms import PLATFORMS, current_platform

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "docs" / "schemas" / "module.schema.json"


def _modul(kok: Path, module_id: str, **ek: Any) -> Path:
    """En küçük geçerli manifest; testin ilgilendiği alan `ek` ile eklenir."""
    manifest: dict[str, Any] = {
        "id": module_id,
        "name": module_id,
        "version": "0.1.0",
        "sdk": ">=0.1,<1.0",
        "entrypoint": "backend.module:register",
    }
    manifest.update(ek)
    dizin = kok / module_id
    dizin.mkdir(parents=True)
    (dizin / "module.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return dizin


def _cekirdek(platform: str) -> Kernel:
    """Keşif için çekirdek; bağımlılıkları keşif aşamasında kullanılmaz."""
    bos = cast(Any, None)
    return Kernel(bos, bos, bos, bos, platform=platform)


def test_uymayan_modul_kayda_HIC_girmez(tmp_path: Path) -> None:
    _modul(tmp_path, "tarayici", platforms=["linux"])

    kernel = _cekirdek("windows")
    kernel.discover(tmp_path, SCHEMA)

    assert "tarayici" not in kernel.records
    # Kayıt yoksa rapor da yoktur: menüye girecek bir satır oluşmaz.
    assert kernel.report() == []
    assert kernel.loaded() == []


def test_uyan_modul_yuklenir(tmp_path: Path) -> None:
    _modul(tmp_path, "tarayici", platforms=["linux"])

    kernel = _cekirdek("linux")
    kernel.discover(tmp_path, SCHEMA)

    assert "tarayici" in kernel.records
    assert kernel.skipped == []


def test_alani_olmayan_modul_her_platformda_yuklenir(tmp_path: Path) -> None:
    """Varsayılan "her yerde"dir — mevcut manifestler bu kararla kapanmaz."""
    _modul(tmp_path, "zil")

    for platform in PLATFORMS:
        kernel = _cekirdek(platform)
        kernel.discover(tmp_path, SCHEMA)
        assert "zil" in kernel.records, f"{platform}: alanı olmayan modül elendi"
        assert kernel.skipped == []


@pytest.mark.parametrize(
    ("platform", "beklenen"),
    [("linux", True), ("windows", True), ("macos", False)],
)
def test_coklu_platform_listesi(tmp_path: Path, platform: str, beklenen: bool) -> None:
    _modul(tmp_path, "ajan", platforms=["linux", "windows"])

    kernel = _cekirdek(platform)
    kernel.discover(tmp_path, SCHEMA)

    assert ("ajan" in kernel.records) is beklenen


def test_eleme_sessiz_degil(tmp_path: Path) -> None:
    """Günlüğe düşen cümlenin aynısı `skipped` içinde durur."""
    _modul(tmp_path, "tarayici", platforms=["linux"])

    kernel = _cekirdek("macos")
    kernel.discover(tmp_path, SCHEMA)

    assert kernel.skipped == ["tarayici — bu platformda çalışmaz (linux)"]


def test_eleme_ariza_sayilmaz(tmp_path: Path) -> None:
    """`problems` bozuk manifestin yeridir; eleme oraya yazılmaz.

    Aksi hâlde `/health` her Windows kurulumunda "sorun var" derdi."""
    _modul(tmp_path, "tarayici", platforms=["linux"])
    _modul(tmp_path, "bozuk", platforms=["ubuntu"])

    kernel = _cekirdek("windows")
    kernel.discover(tmp_path, SCHEMA)

    assert len(kernel.skipped) == 1
    # Bilinmeyen platform adı ŞEMADA reddedilir: yazım hatası sessizce
    # "her platformda çalışır" anlamına gelmez.
    assert len(kernel.problems) == 1
    assert "bozuk" in kernel.problems[0]
    assert "bozuk" not in kernel.records


def test_elenen_modulun_bagimlisi_da_yuklenmez(tmp_path: Path) -> None:
    """Elenen modüle bağlı modül yarım yüklenmez; nedeni kaydına yazılır."""
    _modul(tmp_path, "tarayici", platforms=["linux"])
    _modul(tmp_path, "rapor", depends=["tarayici"])

    kernel = _cekirdek("windows")
    kernel.discover(tmp_path, SCHEMA)

    assert kernel.order() == ["rapor"]
    assert "tarayici" not in kernel.records


def test_calisilan_platform_bilinen_kumeden() -> None:
    assert current_platform() in PLATFORMS


def test_mevcut_manifestlerin_hepsi_gecerli_kalir() -> None:
    """Şema genişledi; depodaki manifestlerin hiçbiri bundan etkilenmemeli."""
    schema = jsonschema.Draft202012Validator(
        yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    )
    manifestler = sorted((ROOT / "modules").glob("*/module.yaml"))
    assert manifestler, "modül manifesti bulunamadı"
    for dosya in manifestler:
        veri = yaml.safe_load(dosya.read_text(encoding="utf-8")) or {}
        hatalar = [hata.message for hata in schema.iter_errors(veri)]
        assert not hatalar, f"{dosya.parent.name}: {hatalar[0]}"
