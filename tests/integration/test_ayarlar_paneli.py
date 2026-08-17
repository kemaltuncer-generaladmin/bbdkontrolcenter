"""Sistem Ayarları ekranı — kabuk tarafındaki sabitler (ADR 0017 · ADR 0018).

Burada ekranın görüntüsü değil, KIRILDIĞINDA SESSİZ KALACAK ŞEYLER sınanır:

* **Çekirdek panelleri sözdizimi denetiminden geçmiyordu.**
  `test_panel_sozdizimi.py` desenleri `apps/desktop/shell/*.js` ve
  `modules/*/ui/panel/**` — `shell/core-panels/**` ikisine de girmiyor. Yani
  bir çekirdek ekranında sözdizimi hatası olsa tüm süit yeşil kalır, kullanıcı
  ekranı açtığında boş bir gövde görürdü. Denetim `.mjs` kopyası üzerinden
  yapılır: `node --check` bir `.js` dosyasında `import` görünce SESSİZCE geçer
  (o dosyanın başlığındaki ölçüm).

* **Menü girdisi ile dosya birbirinden ayrılabilir.** `ui-kernel.js` içindeki
  `entry` diskte olmayan bir yolu gösterirse ekran "henüz yok" kartıyla açılır
  ve kimse bunun bir yazım hatası olduğunu anlamaz.

* **Kabukta modül adı geçmez (K1).** Sekmeler `GET /api/settings`ten gelir;
  ekrana modül adı yazılırsa `modules/` silindiğinde ekran yalan söyler.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHELL = ROOT / "apps" / "desktop" / "shell"
PANEL = SHELL / "core-panels" / "settings" / "index.js"


def _core_panel_sources() -> list[Path]:
    return sorted(SHELL.glob("core-panels/*/*.js"))


@pytest.mark.skipif(shutil.which("node") is None, reason="node kurulu değil")
def test_cekirdek_panelleri_gecerli_esm() -> None:
    kaynaklar = _core_panel_sources()
    assert kaynaklar, "Çekirdek paneli bulunamadı — yol kaymış olabilir."

    hatalar: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        for kaynak in kaynaklar:
            hedef = Path(tmp) / f"{kaynak.parent.name}-{kaynak.stem}.mjs"
            hedef.write_bytes(kaynak.read_bytes())
            sonuc = subprocess.run(
                ["node", "--check", str(hedef)],
                capture_output=True, text=True, check=False,
            )
            if sonuc.returncode != 0:
                hatalar.append(f"{kaynak.relative_to(ROOT)}: {sonuc.stderr.strip()}")

    assert not hatalar, "\n".join(hatalar)


def test_menu_girdisi_var_olan_dosyayi_gosterir() -> None:
    """`CORE_PANELS` içindeki her `entry` diskte gerçekten bulunmalı."""
    kaynak = (SHELL / "ui-kernel.js").read_text(encoding="utf-8")
    yollar = re.findall(r"entry:\s*'([^']+)'", kaynak)

    assert "core-panels/settings/index.js" in yollar, "Ayar ekranı menüye bağlanmamış"
    for yol in yollar:
        assert (SHELL / yol).is_file(), f"menüdeki dosya diskte yok: {yol}"


def test_ayar_ekrani_kendi_stilini_tasir() -> None:
    """Panel CSS'i `document.head`'e eklenir ve kaldırılmaz: önek benzersiz olmalı."""
    ham = (PANEL.parent / "panel.css").read_text(encoding="utf-8")
    # Yorumlar ayıklanır: dosyanın başlığı başka bir öneki (çakışma gerekçesi)
    # ANLATIYOR, kullanmıyor.
    css = re.sub(r"/\*.*?\*/", "", ham, flags=re.DOTALL)
    onekler = set(re.findall(r"\.([a-z]{2,4})-[a-z]", css)) - {"kit"}

    assert onekler == {"sa"}, f"beklenmeyen önek: {sorted(onekler)}"

    # `st-` bbd_students panelinde kullanılıyor; aynı öneki almak o ekranı
    # bozardı ve hata ancak "önce ayarları açtıysan" koşuluyla görünürdü.
    baskasi = (ROOT / "modules" / "bbd_students" / "ui" / "panel" / "panel.css")
    if baskasi.is_file():
        assert ".st-" in baskasi.read_text(encoding="utf-8"), \
            "bu testin dayandığı çakışma örneği kaybolmuş; öneki yeniden doğrulayın"


def test_ekranda_modul_adi_gecmez() -> None:
    """K1 — sekmeler veriden gelir; kabuğa modül adı yazılmaz."""
    kaynak = PANEL.read_text(encoding="utf-8")
    kacaklar = [
        path.parent.name
        for path in (ROOT / "modules").glob("*/module.yaml")
        if re.search(rf"\b{re.escape(path.parent.name)}\b", kaynak)
    ]
    assert not kacaklar, f"ekranda modül adı geçiyor: {kacaklar}"


def test_ui_kit_disina_import_yok() -> None:
    """Ortak bileşenler tek kopyadır (ADR 0011); ekran kendi setini doğurmaz."""
    kaynak = PANEL.read_text(encoding="utf-8")
    kaynaklar = re.findall(r"from\s+'([^']+)'", kaynak)

    assert kaynaklar, "import satırı bulunamadı"
    for yol in kaynaklar:
        assert yol.startswith("../../ui-kit/"), f"kit dışından import: {yol}"
