"""Rapor panelinin GÖRSEL sözleşmesi — kaynak dosya üzerinden.

NEDEN BU TEST VAR. Bu ekranda grafik ÜRETECİ sunucudadır (`builders.py` her
raporun `chart.kind` alanını dolduruyor) ve panel yalnız o türü kitin
bileşenine bağlar. Bağ tek bir `switch` — sessizce kopabilir: eksik bir dal
`null` döndürür, rapor grafiksiz çizilir ve hata çıkmaz. Kimse fark etmeden
yirmi rapor grafiğini kaybedebiliriz.

SINANAN ŞEY BİÇİM DEĞİL KARARDIR (docs/adr/0011 · ui-kit/README.md).
"""

from __future__ import annotations

import re
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1]
PANEL = (MODULE / "ui" / "panel" / "index.js").read_text(encoding="utf-8")
BUILDERS = (MODULE / "backend" / "builders.py").read_text(encoding="utf-8")


def _chart_node() -> str:
    bas = PANEL.index("function chartNode(")
    return PANEL[bas:PANEL.index("function tableNode(", bas)]


def test_panel_kendi_svgsini_cizmez() -> None:
    for yasak in ("createElementNS", "<svg", "new Chart(", "d3."):
        assert yasak not in PANEL, f"Rapor ekranı grafiği kendi çiziyor: {yasak}"


def test_grafikler_kit_yolundan_gelir() -> None:
    assert "from '../../ui-kit/charts.js'" in PANEL
    for ad in ("barChart", "groupedBar", "hourStrip", "lineChart", "paretoChart",
               "stackedBar"):
        assert f"function {ad}(" not in PANEL, f"{ad} panelde yeniden tanımlanmış"


def test_ureticinin_urettigi_her_tur_ekranda_karsilik_bulur() -> None:
    """Sunucunun ürettiği bir `chart.kind` panelde karşılıksız kalırsa o rapor
    SESSİZCE grafiksiz çizilir: `chartNode` `null` döner, `renderWorkspace`
    kartı hiç açmaz, hata da vermez."""
    uretilen = set(re.findall(r'_chart\(\s*"([a-z]+)"', BUILDERS))
    cizilen = set(re.findall(r"case '([a-z]+)':", _chart_node()))
    assert uretilen - {""} <= cizilen, (
        f"Sunucu üretiyor, ekran çizmiyor: {sorted(uretilen - cizilen - {''})}")


def test_bos_grafik_kutusu_acilmaz() -> None:
    """Boş grafik, boş tablodan kötüdür: veri yokken kart hiç açılmaz, kullanıcı
    "grafik bozuk" sanmaz."""
    assert "if (!chart || !chart.kind || !(chart.data || []).length) return null;" in _chart_node()
    assert "if (chart) host.append(card(" in PANEL
