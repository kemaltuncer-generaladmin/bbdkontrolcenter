"""Müşteriler panelinin istek bütçesi sözleşmesi — kaynak dosya üzerinden.

NEDEN PYTHON'DAN. Depoda JS koşucusu yok. Buradaki kuralların hiçbiri çalışma
zamanında hata vermez: ekran yine açılır. Kırıldığında görünen tek şey,
geçidin dakikada 55 isteklik ORTAK kovasının bu ekran yüzünden tükenmesi ve
öteki ekranların sırada beklemesidir.
"""

from __future__ import annotations

from pathlib import Path

MODULE = Path(__file__).resolve().parents[1]
PANEL = (MODULE / "ui" / "panel" / "index.js").read_text(encoding="utf-8")


def _govde(bas: str, son: str) -> str:
    start = PANEL.index(bas)
    return PANEL[start:PANEL.index(son, start)]


def test_tarama_surerken_liste_degil_durum_ucu_yoklanir() -> None:
    """Eskiden 15 saniyede bir LİSTE yeniden isteniyordu; her yoklama mağazaya
    bir istekti. Yoklama artık `/scan-status` ucuna gider (mağazaya gitmez)."""
    yoklama = _govde("async function pollScan(", "function stopScanWatch(")
    assert "/scan-status" in yoklama
    assert "SCAN_POLL_MS = 15_000" not in PANEL


def test_durum_degisikligi_siparis_taramasini_yeniden_baslatmaz() -> None:
    """Pasifleştirme siparişleri değiştirmez; sunucu nüfusu zaten bayatlatıyor.
    `refreshScan: true` sipariş taramasını da baştan başlatırdı."""
    toplu = _govde("async function bulkStatus(", "function applySegment(")
    assert "refreshScan" not in toplu


def test_kapanista_yoklama_durur() -> None:
    assert "stopScanWatch();" in _govde("return () => {", "settingsForms.forEach")


# ================================ sade ekran (docs/tasarim-sade-magaza.md)

CSS = (MODULE / "ui" / "panel" / "panel.css").read_text(encoding="utf-8")


def test_tarih_araliklari_bos_baslar() -> None:
    """Kitin `dateRange`ı başlangıç verilmezse "son 7 gün"e kurulur. Liste bu
    yüzden varsayılan olarak son 7 günde kaydolup sipariş verenlere süzülüyordu
    (canlı önizlemede görüldü). Müşteri Listesi TÜM müşterilerle açılmalı."""
    assert "key: 'registered', label: 'Kayıt', start: '', end: ''" in PANEL
    assert "key: 'lastOrder', label: 'Son sipariş', start: '', end: ''" in PANEL


def test_suzgec_seridi_sade_kipte_ve_cikti_tek_menude() -> None:
    serit = _govde("nodes.filters = filterBar({", "nodes.chips = chipRow(")
    assert "compact: true" in serit
    assert serit.count("primary: true") == 2           # arama + Durum
    assert "menuButton('Dışa aktar'" in serit
    assert "⤓" not in PANEL                             # yan yana çıktı düğmesi yok


def test_cip_satiri_en_cok_bes_segment() -> None:
    satir = _govde("const CHIP_SEGMENTS = [", "];")
    assert satir.count("'") // 2 <= 5


def test_tablo_en_cok_yedi_sutun_ve_ad_ilk_sutunda() -> None:
    tablo = _govde("const COLUMNS = [", "\n];")
    sutunlar = tablo.count("\n    key: '")
    assert sutunlar <= 6, "onay kutusuyla birlikte yediyi aşıyor"
    assert tablo.index("key: 'name'") < tablo.index("key: 'segment'")
    # Ad sütununun alt sınırı var; `minmax(0, …)` dar panelde onu sıfırlıyordu.
    assert "minmax(220px" in tablo


def _alert_tonlari(kaynak: str) -> list[str]:
    """Her `alertBox(...)` çağrısının son argümanı (ton)."""
    tonlar = []
    start = kaynak.find("alertBox(")
    while start != -1:
        depth, index = 0, start + len("alertBox")
        while True:
            char = kaynak[index]
            depth += char == "("
            depth -= char == ")"
            if depth == 0:
                break
            index += 1
        govde = kaynak[start:index]
        tonlar.append(govde.rsplit(",", 1)[-1].strip().strip("'"))
        start = kaynak.find("alertBox(", index)
    return tonlar


def test_varsayilan_gorunumde_aciklama_kutusu_yok() -> None:
    assert "hintBox(" not in PANEL
    assert "info" not in _alert_tonlari(PANEL)          # mavi alertBox yok


def test_gizli_serit_gercekten_gizlenir() -> None:
    """Kitin şerit sınıfı `display: flex` taşıyor ve `hidden`ı eziyordu: Yorumlar
    şeridi Müşteriler sekmesinde de görünüyordu."""
    assert ".cu [hidden] { display: none !important; }" in CSS


def test_kapatilmis_talepler_ekranina_gidilmez() -> None:
    assert "'store_requests'" not in PANEL
