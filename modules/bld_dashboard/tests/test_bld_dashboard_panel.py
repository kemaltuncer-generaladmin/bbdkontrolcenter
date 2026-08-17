"""Panelin GÖRSEL ve KAYNAK sözleşmesi — kaynak dosya üzerinden.

NEDEN PYTHON'DAN. Depoda JS koşucusu yok (`store_dashboard` aynı yolu izliyor).
Buradaki kuralların çoğu çalışma zamanında hata VERMEZ: panel yine açılır,
kutular yine çizilir. Kırıldığında görünen şey ya sızan bir zamanlayıcı, ya
kapanmayan bir takvim, ya da başka panelin görünümünü bozan bir CSS kuralıdır —
hepsi de sahada "arada bir garipleşiyor" diye tarif edilen türden.

SINANAN ŞEY BİÇİM DEĞİL KARARDIR (docs/adr/0011 · ui-kit/README.md).
"""

from __future__ import annotations

import re
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1]
PANEL = (MODULE / "ui" / "panel" / "index.js").read_text(encoding="utf-8")
CSS = (MODULE / "ui" / "panel" / "panel.css").read_text(encoding="utf-8")


def _code(source: str) -> str:
    """Yorumsuz kaynak — YASAK ARAMALARI BUNUN ÜZERİNDE yapılır.

    Bu paneldeki yorumlar kuralın kendisini alıntılıyor (`<input type="date">`
    YASAK, `cutoff_at`tan hesaplanmaz). Ham metinde arama yapan bir test,
    kuralı AÇIKLAYAN satırı kuralın İHLALİ sanıp düşerdi — ve düzeltmenin tek
    yolu açıklamayı silmek olurdu. Gerekçesini yazamadığın kural, korunmayan
    kuraldır.

    Ayrıştırıcı değil, kaba bir süzgeç: dosyada dize içinde `//` ya da `*/`
    geçmiyor ve geçtiği gün bu testler yanlış YÖNE düşer (fazladan eleme),
    yani sessiz kalmaz.
    """
    govde = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    satirlar = []
    for satir in govde.splitlines():
        kesik = "" if satir.lstrip().startswith("//") else satir.split("//", 1)[0]
        satirlar.append(kesik)
    return "\n".join(satirlar)


CODE = _code(PANEL)


# ==================================================== tek kopya: kit kuralları

def test_panel_kendi_grafigini_cizmez() -> None:
    # Tek kopya kuralı (ADR 0011) yalnız yeni kod için değil: `store_shipping`
    # kendi zaman çizelgesini yazdığı için kitteki düzeltmeleri hiç almadı.
    for yasak in ("createElementNS", "<svg", "new Chart(", "d3."):
        assert yasak not in CODE, f"panel grafiği kendi çiziyor: {yasak}"
    for ad in ("stackedBar", "timeline", "progress", "kpiRow", "statusLine"):
        assert f"function {ad}(" not in CODE, f"{ad} panelde yeniden tanımlanmış"


def test_tarih_alani_native_input_kullanmaz() -> None:
    # WebKitGTK'da `<input type="date">` açılır takvimi kapanmıyor (kit kuralı 1).
    assert "type=\"date\"" not in CODE
    assert "type: 'date'" not in CODE
    assert "dateField(" in CODE


def test_icerik_innerHTML_ile_yazilmaz() -> None:
    # `innerHTML` beyaz listeyi tümden atlar (kit kuralı 11). Bu panel zaten
    # sunucudan gelen Türkçe cümleleri yazıyor ve onları düğüm olarak koymak
    # tek doğru yol.
    assert "innerHTML" not in CODE


def test_panel_koku_kit_panel_sinifini_alir() -> None:
    # Yoksa toast ve overlay tüm pencereye taşar (kit kuralı 2).
    assert "'kit-panel bd'" in PANEL


def test_panel_stili_mount_icinde_yuklenir() -> None:
    # Dosya tepesinde yüklenirse, kabuk yetenek çözümü sırasında hiç açılmayan
    # panelleri de import ettiği için kullanılmayan stiller `head`e sızar.
    tepe = PANEL[:PANEL.index("export function mount(")]
    assert "loadStyles(import.meta.url)" not in tepe
    govde = PANEL[PANEL.index("export function mount("):]
    assert "loadStyles(import.meta.url)" in govde


def test_css_yalniz_kendi_onekini_boyar() -> None:
    # Panel CSS'i `document.head`e eklenir ve HİÇ KALDIRILMAZ; `bd-` dışında
    # bir seçici, bu panel bir kez açıldıktan sonra başka panelleri de boyar
    # (kit kuralı 9).
    for satir in CSS.splitlines():
        temiz = satir.strip()
        if not temiz.startswith("."):
            continue
        assert temiz.startswith((".bd ", ".bd{", ".bd,", ".bd>", ".bd-")), temiz


# =============================================================== temizlik

def test_cleanup_gercek_kaynak_birakir() -> None:
    # `pollLoop.stop()` hem zamanlayıcıyı hem `visibilitychange` dinleyicisini,
    # `dateField.destroy()` takvimi bırakır (kit kuralı 4). Bırakılmazsa panel
    # kapalıyken de sunucuya gidilir ve paylaşılan hız bütçesi yanar.
    govde = PANEL[PANEL.index("return () => {"):]
    for cagri in ("nodes.poll?.stop()", "nodes.ticker?.stop()", "nodes.date?.destroy()"):
        assert cagri in govde, f"cleanup {cagri} çağırmıyor"


def test_gec_kalan_acilis_sozu_dongu_kurmaz() -> None:
    # Yoklama döngüsü `/overview` çözüldükten SONRA kuruluyor. Panel bu arada
    # kapanırsa `cleanup` çoktan koşmuş olur ve döngüyü durduracak kimse
    # kalmaz — ekran kapalıyken 30 saniyede bir sunucuya giden, hiçbir yerde
    # görünmeyen bir döngü kalırdı.
    assert "const mounted = epoch;" in PANEL
    assert "if (mounted !== epoch) return;" in PANEL
    assert PANEL.count("epoch += 1;") == 2, "sayaç hem mount hem cleanup'ta artmalı"


def test_yoklama_kit_dongusunu_kullanir() -> None:
    # Ham `setInterval` `document.hidden` denetlemez; dört panel aynı anda
    # yoklarken arka planda duran bir pencere bütçeyi boşuna yakardı.
    assert "setInterval" not in CODE
    assert "pollLoop(" in CODE


# ========================================================== ekranın kararları

def test_geri_sayim_istemcinin_saatini_taban_almaz() -> None:
    # Taban sunucunun verdiği saniye; üzerine yalnız GEÇEN SÜRE eklenir.
    # `cutoff_at` MUTLAK bir andır ve ondan yerel hesap yapmak, saati kaymış
    # bir makinede olmayan bir aciliyet (ya da olmayan bir rahatlık) yaratırdı.
    assert "seconds - elapsed" in CODE
    assert "cutoff_at" not in CODE, "geri sayım mutlak andan hesaplanıyor"


def test_sayaclar_panelde_toplanmaz() -> None:
    # "Kaç sipariş aktif" sorusunun tek cevabı sunucudadır. Panelde bir toplama
    # görünürse iki ekran aynı soruya farklı cevap verebilir hâle gelir.
    for yasak in ("reduce(", ".sum(", " sum("):
        assert yasak not in CODE, f"panel kendi sayacını hesaplıyor: {yasak}"


def test_bilinmiyor_ile_sifir_ayri_gosterilir() -> None:
    # `count()` `null` gördüğünde tire yazar; sıfır yazsaydı "ölçüldü, sıfır
    # çıktı" ile "hiç ölçülmedi" aynı görünürdü ve kapasitede bu, satışın
    # kapandığını sanmak demektir.
    assert "function count(value)" in PANEL
    assert "return value === null || value === undefined ? '—' : num(value);" in PANEL


def test_baglanti_yokken_bekleyen_isler_bos_denmez() -> None:
    # Boş bir liste "yapacak iş yok" diye okunur; bağlantı yokken bu bir yalan.
    assert "BOŞ DEĞİL, BİLİNMİYOR" in PANEL


def test_hedefi_olmayan_satira_atlama_dugmesi_konmaz() -> None:
    # Hiçbir yere gitmeyen bir düğme, bozuk bir düğmedir: kabuk tanımadığı bir
    # panel kimliğini sessizce yok sayar.
    assert "if (task.panel) {" in PANEL
    assert "'bd-link'" in PANEL


def test_bekleyen_is_cumlesi_panelde_yeniden_yazilmaz() -> None:
    # `title`/`detail` sunucudan gelir ve olduğu gibi basılır. Panel kendi
    # cümlesini kursaydı aynı durum iki ekranda iki farklı cümleyle anlatılırdı.
    assert "task.title || task.code" in PANEL
    assert "h('div', 'bd-task-detail', task.detail)" in PANEL
