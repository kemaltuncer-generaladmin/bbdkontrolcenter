"""Pano panelinin GÖRSEL sözleşmesi — kaynak dosya üzerinden.

NEDEN PYTHON'DAN. Depoda JS koşucusu yok. Buradaki kuralların hiçbiri çalışma
zamanında hata vermez: pano yine açılır, grafik yine çizilir. Kırıldığında
görünen tek şey, iki gösterimin aynı rakam için farklı şey söylemesidir — ve
panonun tek sermayesi güvendir.

SINANAN ŞEY BİÇİM DEĞİL KARARDIR (docs/adr/0011 · ui-kit/README.md).
"""

from __future__ import annotations

from pathlib import Path

MODULE = Path(__file__).resolve().parents[1]
PANEL = (MODULE / "ui" / "panel" / "index.js").read_text(encoding="utf-8")


def _kpi() -> str:
    bas = PANEL.index("function paintKpi(")
    return PANEL[bas:PANEL.index("function paintNotes(", bas)]


# ============================================== tek kopya: grafik kitten gelir

def test_panel_kendi_svgsini_cizmez() -> None:
    for yasak in ("createElementNS", "<svg", "new Chart(", "d3."):
        assert yasak not in PANEL, f"Pano grafiği kendi çiziyor: {yasak}"


def test_dort_grafik_de_kit_yolundan_gelir() -> None:
    assert "from '../../ui-kit/charts.js'" in PANEL
    for ad in ("barChart", "hourStrip", "lineChart", "stackedBar", "sparkline"):
        assert f"function {ad}(" not in PANEL, f"{ad} panelde yeniden tanımlanmış"


# =========================================================== KPI eğilim şeridi

def test_kpi_egilim_seridi_ayni_seriden_gelir() -> None:
    """Şerit ile "Günlük ciro" grafiği AYNI diziyi çizer. İkinci bir kaynak
    (ör. mağazanın kendi pano ucu) kullanılsaydı iki gösterim zamanla ayrışır
    ve kullanıcı hangisine güveneceğini bilemezdi — bu, panonun ilan ettiği
    "rakamları iki kaynaktan almaz" kuralının ta kendisi."""
    govde = _kpi()
    assert "fillDays(payload.daily, payload.range.start, payload.range.end)" in govde
    assert "box.spark = seri;" in govde


def test_egilim_seridi_YALNIZ_ciroda() -> None:
    """Gün gün serisi olan tek rakam ciro; sipariş sayısının, sepetin ya da
    iadenin günlük dökümü uçtan gelmiyor. Başka kutuya da şerit koymak, ciro
    eğilimini onların eğilimiymiş gibi göstermek olurdu ve bakan kişi bunu fark
    edemezdi: şeridin ekseni yok."""
    assert "tile.key === 'revenue' && known && seri.length > 1" in _kpi()


def test_bilinmeyen_degere_serit_cizilmez() -> None:
    """Değeri `None` olan kutu "—" gösterir (bilinmiyor ≠ sıfır); altına eğilim
    çizmek, olmayan bir rakama geçmiş uydurmak olurdu."""
    assert "&& known &&" in _kpi()
