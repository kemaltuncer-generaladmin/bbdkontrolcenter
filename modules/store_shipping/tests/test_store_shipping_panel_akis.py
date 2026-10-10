"""Kargo panelinin AKIŞ sözleşmesi — kaynak dosya üzerinden (10.10.2026).

NEDEN PYTHON'DAN. Depoda JS koşucusu yok (bkz. `test_store_shipping_panel_gorsel`).
Buradaki kuralların her biri canlıda SESSİZ bir yanlıştı: hata vermiyor,
yalnız yanlış şeyi yapıyordu. Kaynağı okuyup kararı sınamak, hiç sınamamaktan
iyidir.
"""

from __future__ import annotations

import re
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1]
PANEL = (MODULE / "ui" / "panel" / "index.js").read_text(encoding="utf-8")
KIT = (Path(__file__).resolve().parents[3] / "apps" / "desktop" / "shell" / "ui-kit"
       / "kit.js").read_text(encoding="utf-8")


def _body(name: str) -> str:
    """`function name(` gövdesi — bir sonraki üst düzey `function`a kadar."""
    start = PANEL.index(f"function {name}(")
    rest = PANEL[start + 1:]
    match = re.search(r"\n(?:async )?function |\n// =+", rest)
    return PANEL[start:start + 1 + (match.start() if match else len(rest))]


# ======================================================= sihirbaz: yol ve firma

def test_sihirbazda_KARGO_YOLU_secimi_yok() -> None:
    """"Bagisto" seçiliyken kırmızı «PARA HARCAR» düğmesi sessizce test
    gönderisi açıyordu; «Test gönderisi» ise seçime hiç bakmıyordu."""
    govde = _body("openWizard")
    assert "key: 'provider'" not in govde
    assert "Bagisto — TEST" not in govde


def test_kargoya_ver_YOL_gondermez_test_dugmesi_her_zaman_test() -> None:
    assert "provider" not in _body("dispatchOrder").split("body:")[1].split("}")[0]
    assert "provider: 'bagisto'" in _body("testShipOrder")


def test_sihirbazin_tasiyicisi_siparisin_firmasidir() -> None:
    """Ayardaki varsayılan ya da listenin ilk firması gönderiliyordu; mağaza
    müşterinin ödediğinden farklı firmayı 409 ile reddediyor."""
    govde = _body("openWizard")
    assert "carrier: state.settings?.defaultCarrier" not in govde
    assert "carrier: ''," in govde
    assert "draft.carrier !== row.carrierKey" in _body("wizardExtra")


def test_olcuye_dokunulduysa_olculerin_HEPSI_gider() -> None:
    """Taslakla aynı ölçü kümesi gitmezse sunucu aynı ölçüyü farklı sanıp
    taslağı boşuna iptal ederdi."""
    govde = _body("wizardExtra")
    assert "MEASURE_FIELDS.some((key) => dirty.has(key))" in govde


# ========================================================= satır ve toplu iş

def test_satir_dugmesi_panel_tahmini_yok_diye_DURMAZ() -> None:
    """Sipariş listesi kalem ölçüsü vermiyor; panelin tahmini canlıda her
    satırda boştu ve düğme "Sipariş desisi yok" deyip duruyordu."""
    assert "toast('Sipariş desisi yok" not in PANEL
    assert "measure: true" in _body("dispatchCell")         # «📏 Ölçü» tek tık


def test_hazir_tablosunun_secim_kimligi_siparistir() -> None:
    """Satırda `id` yok; kitin varsayılan kimliği her satıra aynı boş değeri
    veriyordu ve tek satır seçmek hepsini seçiyordu."""
    assert "rowKey: (row) => String(row.orderId)" in _body("renderReady")


def test_toplu_is_TEK_onay_ister_metinler_davranisla_ayni() -> None:
    govde = _body("dispatchQueue")
    assert govde.count("confirmDispatch(") == 1
    assert "confirmed: true" in govde
    assert "reason: answer.reason" in govde        # tek not bütün siparişlere
    assert "Her satın alma öncesi özet onayı alınır" not in PANEL
    assert "onay sormadan yürür" not in PANEL
    assert "ara onay YOK" not in PANEL


def test_kargoya_verilen_satir_yerelde_duser_liste_yeniden_cekilmez() -> None:
    assert "loadReady()" not in _body("dispatchOrder")
    assert "dropReadyRow(row.orderId)" in _body("dispatchOrder")


# ========================================================== gönderi süzgeçleri

def test_magazanin_okumadigi_suzgec_ekranda_YOK() -> None:
    sorgu = _body("shipmentQuery")
    for olu in ("city", "dateFrom", "dateTo", "dateField", "payer", "desiMin", "desiMax"):
        assert f"put('{olu}'" not in sorgu, olu
    bar = PANEL[PANEL.index("nodes.shipFilters = filterBar({"):
                PANEL.index("onChange: () => loadShipments({ page: 1 })")]
    for olu in ("kind: 'dateRange'", "kind: 'numRange'", "key: 'payer'", "key: 'city'",
                "key: 'dateField'"):
        assert olu not in bar, olu
    assert "put('orderId', state.shipments.orderId)" in sorgu


def test_sayfa_ici_suzgec_durum_satirinda_SOYLENIR_kutu_yok() -> None:
    """Süzgeç çekilen sayfanın içindedir; bu söylenir ama bilgi kutusuyla
    değil (docs/tasarim-sade-magaza.md §3): durum satırı ve boş durum."""
    assert "data.pageFiltered" in _body("shipStatusText")
    assert "data.pageFiltered" in _body("shipEmpty")
    assert "alertBox(" not in _body("renderShipTable")


# ================================================ sade ekran (tasarım belgesi)

def test_en_cok_UC_ust_sekme_kurulum_ayarlarda() -> None:
    sekmeler = PANEL[PANEL.index("const TABS = ["):PANEL.index("];", PANEL.index("const TABS = ["))]
    anahtarlar = re.findall(r"key: '(\w+)'", sekmeler)
    assert anahtarlar == ["ready", "shipments", "settings"]
    bolumler = PANEL[PANEL.index("const SETTINGS_SECTIONS = ["):]
    for ad in ("carriers", "rates", "zones", "geliver"):
        assert f"key: '{ad}'" in bolumler[:400], ad


def test_varsayilan_gorunumde_BILGI_KUTUSU_yok() -> None:
    assert "hintBox(" not in PANEL
    assert "reasonBar" not in PANEL and "dispatchReason" not in PANEL


def test_tablolar_en_cok_YEDI_sutun() -> None:
    hazir = _body("renderReady")
    sutunlar = hazir[hazir.index("columns: ["):hazir.index("].filter(Boolean)")]
    assert sutunlar.count("{ key: '") + sutunlar.count("key: 'customer'") <= 7
    gonderi = PANEL[PANEL.index("const SHIP_COLUMNS = ["):PANEL.index("];", PANEL.index(
        "const SHIP_COLUMNS = ["))]
    assert len(re.findall(r"key: '", gonderi)) <= 7


def test_suzgecler_katlanir_ikincil_eylemler_TEK_menude() -> None:
    assert PANEL.count("compact: true") == 2
    assert "menuButton('Dışa aktar', [" in PANEL
    gonderi = PANEL[PANEL.index("nodes.shipFilters = filterBar({"):
                    PANEL.index("nodes.flagChips =")]
    assert gonderi.count("primary: true") == 2           # arama + durum
    assert "⤓" not in gonderi                             # dağınık dışa aktarma düğmesi yok


def test_kpi_seridi_en_cok_DORT_kutu() -> None:
    for ad in ("renderKpi", "renderPerformance"):
        govde = _body(ad)
        serit = govde[govde.index("kpiRow(["):govde.index("]));", govde.index("kpiRow(["))]
        assert serit.count("{ label:") <= 4, ad


def test_onay_penceresinde_not_ISTEGE_BAGLI_ve_cipli() -> None:
    govde = _body("confirmDispatch")
    assert "DISPATCH_CHIPS" in govde
    assert "minLength: 0" in govde                        # boş not kabul
    # Pencere kitin kapısıdır; çipler `presets` ile gider. Enter "Vazgeç"te ya
    # da çipte işlemi yürütmez — kural kitte (`confirmWithReason`).
    assert "confirmWithReason(nodes.root" in _body("reasonDialog")
    assert "presets: chips" in _body("reasonDialog")
    assert "event.target?.tagName === 'BUTTON'" in KIT


def test_hazir_gerekce_cipleri_kapinin_alt_sinirini_gecer() -> None:
    """Tasarım §7: gerekçe gereken yerde hazır çip; alan yine en az 10 (para
    harcayan işte 20) karakter ister. Kısa bir çip, basıldığında kapıda
    takılır ve "çip çalışmıyor" izlenimi verirdi."""
    blok = PANEL[PANEL.index("const REASON_CHIPS = {"):PANEL.index("};", PANEL.index(
        "const REASON_CHIPS = {"))]
    for anahtar, govde in re.findall(r"(\w+): \[([^\]]*)\]", blok):
        sinir = 20 if anahtar in ("retour", "purchase", "geliver") else 10
        for metin in re.findall(r"'([^']*)'", govde):
            assert len(metin) >= sinir, (anahtar, metin)
    assert "minLength," in _body("reasonDialog")
    assert "reason.length < minLength" in KIT
