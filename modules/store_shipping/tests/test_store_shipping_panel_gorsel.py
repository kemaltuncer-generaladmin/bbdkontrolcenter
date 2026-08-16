"""Kargo panelinin GÖRSEL sözleşmesi — kaynak dosya üzerinden.

NEDEN PYTHON'DAN. Depoda JS koşucusu yok. Buradaki kuralların hiçbiri çalışma
zamanında hata vermez: panel yine açılır, grafik yine çizilir — yalnız kitin
kopyası panele sızmış olur ve kitteki düzeltmeler bir daha bu ekrana gelmez.
Kaynağı okuyup sözleşmeyi sınamak, hiç sınamamaktan iyidir.

SINANAN ŞEY BİÇİM DEĞİL KARARDIR (docs/adr/0011 · ui-kit/README.md):
grafik ve akış bileşenleri KİTTEN gelir, panelde çizilmez.
"""

from __future__ import annotations

from pathlib import Path

MODULE = Path(__file__).resolve().parents[1]
PANEL = (MODULE / "ui" / "panel" / "index.js").read_text(encoding="utf-8")
CSS = (MODULE / "ui" / "panel" / "panel.css").read_text(encoding="utf-8")


# ============================================== tek kopya: grafik kitten gelir

def test_panel_kendi_svgsini_cizmez() -> None:
    """Panelde SVG üretimi olsaydı kitin ekseni, etiketi ve boş-veri dalı
    yeniden yazılmış olurdu; iki kopya sessizce ayrışır."""
    for yasak in ("createElementNS", "<svg", "new Chart(", "d3."):
        assert yasak not in PANEL, f"Panel grafiği kendi çiziyor: {yasak}"


def test_grafik_ve_akis_bilesenleri_kit_yolundan_gelir() -> None:
    assert "from '../../ui-kit/charts.js'" in PANEL
    assert "from '../../ui-kit/flow.js'" in PANEL
    for ad in ("barChart", "groupedBar", "stackedBar", "measureBar", "stepper", "timeline"):
        assert f"function {ad}(" not in PANEL, f"{ad} panelde yeniden tanımlanmış"


# ================================================ gönderi çekmecesi: zaman akışı

def test_hareket_gecmisi_kit_timeline_ile_cizilir() -> None:
    """Elle yazılmış çizelge (`sh-timeline` / `sh-move`) kaldırıldı; kitin
    `timeline()`'ı bekleyen adımı ve tonu da biliyor."""
    assert "timeline(moves, {" in PANEL
    assert "h('div', 'sh-timeline')" not in PANEL
    assert "h('div', 'sh-move')" not in PANEL


def test_olu_cizelge_css_i_geride_birakilmadi() -> None:
    """Panel CSS'i `document.head`'e eklenip HİÇ kaldırılmıyor; ölü seçici bir
    gün aynı adı kullanan başka bir düğümü sessizce boyar.

    SEÇİCİ satırlarına bakılır, dosyanın tamamına değil: kaldırma GEREKÇESİ
    yorumda duruyor ve orada geçen adı "hâlâ kullanılıyor" saymak, açıklamayı
    silmeye zorlardı.
    """
    seciciler = [line.strip() for line in CSS.splitlines() if line.strip().startswith(".")]
    for olu in (".sh-timeline", ".sh-move"):
        assert not any(olu in line for line in seciciler), f"Kullanılmayan seçici duruyor: {olu}"


def test_hareketler_eskiden_yeniye_cevrilir() -> None:
    """`movement_rows` en yeniyi ÜSTE koyuyor (liste için doğru); `timeline`
    yolculuğu eskiden yeniye okutur ve "nereye kadar geldi" sorusunu son satıra
    baktırarak yanıtlar."""
    assert "].reverse()" in PANEL


def test_hareket_dizisi_kopyalanarak_cevrilir() -> None:
    """`reverse()` yerinde çalışır: kaynağı çevirmek, aynı çekmece ikinci kez
    çizildiğinde sırayı ters döndürürdü."""
    assert "[...(payload.movements || [])].reverse()" in PANEL


# ================================================== gönderi çekmecesi: ölçüler

def test_desi_karsilastirmasi_measurebar_ile_gosterilir() -> None:
    """İki ölçüden BÜYÜĞÜ tavana yuvarlanıp faturalanıyor. Sayıları alt alta
    yazmak hangisinin ücreti belirlediğini göstermiyordu."""
    assert "measureBar([" in PANEL
    assert "governs: true" in PANEL


def test_faturalanan_desi_kunyeden_kaldirildi() -> None:
    """Aynı bilgi iki yerde durursa biri güncellenmeden kalır."""
    assert "fact('Faturalanan desi'" not in PANEL


def test_olcu_yoksa_bos_cubuk_degil_neden_yazilir() -> None:
    """"Ölçü sıfır" ile "taşıyıcı ölçü bildirmedi" farklı şeyler; ikincisi
    sıfır gibi görünmemeli (boş grafik, boş tablodan kötüdür)."""
    govde = PANEL[PANEL.index("function measureView("):PANEL.index("// ====", PANEL.index(
        "function measureView("))]
    assert "if (!desi && !weight && !units)" in govde
    assert "emptyState({" in govde


# ============================================================ performans sekmesi

def test_durum_dagilimi_stackedbar_ile_cizilir() -> None:
    assert "function statusSpread(" in PANEL
    assert "stackedBar([" in PANEL


def test_geciken_dagilim_cubuguna_girmez() -> None:
    """`late` bir DURUM değil, yoldaki gönderiye takılan bayrak: parçalardan
    biri yapılsaydı aynı gönderi hem "Yolda" hem "Geciken" sayılır ve toplam
    gerçek gönderi sayısını aşardı."""
    govde = PANEL[PANEL.index("function statusSpread("):PANEL.index("async function renderPerformance(")]
    assert "late" not in govde


def test_iptal_edilen_gonderi_artiktan_hesaplanir() -> None:
    """`totals` iptali dört durumun hiçbirine yazmıyor; toplamdan düşmezsek
    çubuk var olmayan gönderileri de gösterdiğini iddia ederdi."""
    govde = PANEL[PANEL.index("function statusSpread("):PANEL.index("async function renderPerformance(")]
    assert "const iptal = Math.max(0," in govde
    assert "label: 'İptal'" in govde
