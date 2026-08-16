"""Sipariş panelinin GÖRSEL sözleşmesi — kaynak dosya üzerinden.

NEDEN PYTHON'DAN. Depoda JS koşucusu yok ve buradaki kuralların hiçbiri çalışma
zamanında hata vermez: şerit yine çizilir, yalnız YANLIŞ şeyi söyler. Bir
siparişin "kargoya verildi" görünüp verilmemiş olması ekranın en pahalı
hatasıdır; kaynağı okuyup sözleşmeyi sınamak hiç sınamamaktan iyidir.

SINANAN ŞEY BİÇİM DEĞİL KARARDIR (docs/adr/0011 · ui-kit/README.md).
"""

from __future__ import annotations

from pathlib import Path

MODULE = Path(__file__).resolve().parents[1]
PANEL = (MODULE / "ui" / "panel" / "index.js").read_text(encoding="utf-8")
CSS = (MODULE / "ui" / "panel" / "panel.css").read_text(encoding="utf-8")


def _journey() -> str:
    """`journeyCard` gövdesi — sonraki fonksiyona kadar."""
    bas = PANEL.index("function journeyCard(")
    return PANEL[bas:PANEL.index("function actionBar(", bas)]


# ============================================== tek kopya: bileşen kitten gelir

def test_panel_kendi_grafigini_cizmez() -> None:
    for yasak in ("createElementNS", "<svg", "new Chart(", "d3."):
        assert yasak not in PANEL, f"Panel grafiği kendi çiziyor: {yasak}"


def test_asama_seridi_kit_flow_dan_gelir() -> None:
    assert "import { stepper } from '../../ui-kit/flow.js';" in PANEL
    assert "function stepper(" not in PANEL


def test_serit_kabina_ikinci_bir_bosluk_yazilmaz() -> None:
    """İç boşluk kitin `.kit-stepper`'ındadır; panelde tekrarlamak aynı ölçüyü
    iki yerde tutmak olurdu ve ikisi zamanla ayrışır."""
    kap = [line for line in CSS.splitlines() if line.strip().startswith(".so-journey {")]
    assert kap, ".so-journey kuralı yok"
    assert "padding" not in kap[0]


# ================================================================ aşama şeridi

def test_kunye_asama_seridiyle_baslar() -> None:
    assert "journeyCard(payload, shipmentCap)" in PANEL
    assert "stepper(steps, done)" in _journey()


def test_teslim_edildi_adimi_CIZILMEZ() -> None:
    """Bu çekmecenin künye ucu Bagisto'nun kendi kayıtlarını taşıyor; gönderi
    satırında taşıyıcının teslim bildirimi YOK (`shipment_rows`: firma, takip
    no, adet, tarih). Dördüncü adımı çizmek, teslim edilmiş bir siparişi de
    "bekliyor" göstermek olurdu. Teslim durumu Kargo sekmesindedir."""
    govde = _journey()
    assert "Teslim edildi" not in govde
    assert govde.count("{ label: '") == 3


def test_kismi_fatura_ve_kargo_kendi_cumlesini_kurar() -> None:
    """`invoiceState`/`shipmentState` üç durumludur. Yarım kalmış adıma
    "tamamlandı" yazmak olmamış işi olmuş, "bekliyor" yazmak başlamış işi hiç
    başlamamış gösterir; ikisi de yanlış ve ikisi de sessizdir."""
    govde = _journey()
    assert "'partial'" in govde
    assert "kısmen faturalandı" in govde
    assert "kısmen kargolandı" in govde


def test_adim_tonu_zincir_sinifiyla_karistirilmaz() -> None:
    """Ton TEK ADIM hakkındadır, `activeIndex` ZİNCİR hakkında. Sırası gelmeden
    gerçekleşmiş adım işaretlenir ama zinciri tamamlanmış göstermez; durum yine
    yazıyla da orada (renk tek başına anlam taşımaz)."""
    govde = _journey()
    assert "tone: 'good'" in govde
    assert "tone: 'warn'" in govde
    assert "state:" in govde


def test_tamamlanan_adimlar_KESINTISIZ_onektir() -> None:
    """`stepper` `index <= activeIndex` olan HER adımı tamamlanmış boyar;
    faturasız kargolanmış bir siparişte 2 vermek, kesilmemiş faturayı kesilmiş
    göstermek olurdu."""
    govde = _journey()
    assert "if (done === 1 && order.shipmentState === 'full') done = 2;" in govde


def test_iptal_edilen_siparis_kalan_adimlari_bekletmez() -> None:
    """"Bekliyor" bir söz verir: olacak demektir. İptal edilmiş siparişte o söz
    tutulmayacak."""
    govde = _journey()
    assert "'canceled', 'closed'" in govde
    assert "gerçekleşmedi" in govde


def test_tarih_en_eski_kayittan_okunur() -> None:
    """Uç kayıtları takvim sırasında vermiyor; `[0]` almak, ikinci faturanın
    tarihini "ilk faturalandığı an" diye yazdırabilirdi."""
    assert "function firstStamp(" in PANEL
    assert ".sort()[0]" in PANEL


def test_ipucu_olmayan_sekmeye_yollamaz() -> None:
    """Kargo yeteneği yoksa o sekme HİÇ açılmıyor; "Kargo sekmesi" demek arayan
    kişiyi boşuna dolaştırırdı."""
    govde = _journey()
    assert "shipmentCap" in govde
    assert "bu kurulumda okunamıyor" in govde
