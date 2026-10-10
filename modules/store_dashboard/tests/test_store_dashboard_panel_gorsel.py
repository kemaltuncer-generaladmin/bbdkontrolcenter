"""Pano panelinin GÖRSEL sözleşmesi — kaynak dosya üzerinden.

NEDEN PYTHON'DAN. Depoda JS koşucusu yok. Buradaki kuralların hiçbiri çalışma
zamanında hata vermez: pano yine açılır, grafik yine çizilir. Kırıldığında
görünen tek şey, iki gösterimin aynı rakam için farklı şey söylemesidir — ve
panonun tek sermayesi güvendir.

SINANAN ŞEY BİÇİM DEĞİL KARARDIR (docs/adr/0011 · ui-kit/README.md).
"""

from __future__ import annotations

import re
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


# ================================================ ölü düğme ve boş kart yok

def test_kapatilmis_ekranlara_dugme_birakilmaz() -> None:
    """Raporlar, Yedekleme ve UDİT ekranları menüden kaldırıldı. Kabuk panele
    "şu ekran kayıtlı mı" bilgisini vermediği için düğme koşula bağlanamaz;
    hedef düğmesi kaldırılır — tıklanınca boş ekran açan düğme bırakılmaz."""
    for hedef in ("store_reports", "store_backups", "store_udit_logs"):
        assert f"'{hedef}'" not in PANEL, f"{hedef} ekranına giden düğme duruyor"


def test_denetim_karti_yalniz_son_islemler_verebilen_yetenekle_cizilir() -> None:
    """`store.audit.for` DÜZ bir fonksiyondur (tek kaydın geçmişi); `recent`
    alanı yoktur. Kart yeteneğin VARLIĞINA bakıp `recent?.(10)` çağırıyordu ve
    her açılışta boş tablo çiziyordu."""
    assert "typeof auditFor?.recent === 'function'" in PANEL
    assert "() => {})" not in PANEL, "hiçbir şey yapmayan 'Tekrar dene' duruyor"


# ===================================================== istek bütçesi

def test_acilis_hafif_referans_ucunu_kullanir() -> None:
    """`/settings` mağazanın ayar bölümünü de okuyor; kanal açılırı için
    gereksiz bir istekti."""
    bas = PANEL.index("async function loadReference(")
    govde = PANEL[bas:]
    assert "`${BASE}/reference`" in govde
    assert "`${BASE}/settings`" not in govde


def test_ozet_yoklamasi_hesabi_yeniden_baslatmaz() -> None:
    """Yoklama `fresh` gönderseydi her beş saniyede hesap baştan başlardı."""
    bas = PANEL.index("async function loadSummary(")
    govde = PANEL[bas:PANEL.index("async function loadRecent(", bas)]
    assert "query(poll ? {} : freshParam(fresh))" in govde
    assert "SUMMARY_POLL_LIMIT" in PANEL


def test_kapanista_ozet_yoklamasi_durur() -> None:
    bas = PANEL.index("return () => {")
    assert "stopSummaryPoll();" in PANEL[bas:PANEL.index("};", bas)]


# ============================================ sade ekran (docs/tasarim-sade-magaza.md)


CSS = (MODULE / "ui" / "panel" / "panel.css").read_text(encoding="utf-8")

#: Menüde kalan yedi ekrandan bu panelin gidebileceği beşi. Kapatılmış ekrana
#: giden düğme boş ekran açar.
YASAYAN = {"store_orders", "store_shipping", "store_products", "store_refunds",
           "store_customers"}


def _blok(bas: str, son: str) -> str:
    start = PANEL.index(bas)
    return PANEL[start:PANEL.index(son, start)]


def test_en_cok_uc_ust_sekme() -> None:
    sekmeler = _blok("const tabs = tabBar([", "]")
    assert sekmeler.count("key: '") <= 3
    assert "'config'" not in sekmeler          # Yapılandırma, Ayarlar'ın alt başlığı


def test_suzgec_sade_kipte_cikti_tek_menude() -> None:
    serit = _blok("nodes.filters = filterBar({", "nodes.status = statusLine();")
    assert "compact: true" in serit
    assert serit.count("primary: true") <= 2
    assert "menuButton('Dışa aktar'" in serit
    assert "variant: 'ghost'" in serit          # Yenile hayalet düğme
    assert "⤓" not in PANEL


def test_tek_kanalda_kanal_kutusu_cizilmez() -> None:
    assert "applyChoiceFilter(nodes.filters, 'channel'" in PANEL


def test_ust_serit_en_cok_dort_kpi() -> None:
    satir = _blok("const PRIMARY_KPIS = [", "];")
    assert satir.count("'") // 2 <= 4


def test_panoda_en_cok_alti_kart() -> None:
    izgara = _blok("grid.append(", "\n  );")
    assert izgara.count("panelCard(") <= 6


def test_kisayollar_yalniz_yasayan_ekranlara_gider() -> None:
    hedefler = set(re.findall(r"(?:target|panel): '(store_\w+)'", PANEL))
    hedefler |= set(re.findall(r"open\('(store_\w+)'", PANEL))
    assert hedefler, "hiç hedef bulunamadı — desen bozuk"
    assert hedefler <= YASAYAN, hedefler - YASAYAN


def test_bekleyen_ve_kargolanmayan_kutulari_kisayoldur() -> None:
    hedefler = _blok("const KPI_TARGETS = {", "};")
    assert "pending: { panel: 'store_orders'" in hedefler
    assert "unshipped: { panel: 'store_shipping'" in hedefler


def test_aciklama_kutusu_yok_ve_gizli_serit_gizlenir() -> None:
    assert "hintBox(" not in PANEL
    assert ".sd [hidden] { display: none !important; }" in CSS
