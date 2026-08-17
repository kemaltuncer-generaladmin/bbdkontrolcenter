"""Panelin GÖRSEL ve KAYNAK sözleşmesi — kaynak dosya üzerinden.

NEDEN PYTHON'DAN. Depoda JS koşucusu yok (`bld_dashboard` ve `store_dashboard`
aynı yolu izliyor). Buradaki kuralların çoğu çalışma zamanında hata VERMEZ:
panel yine açılır, kutular yine çizilir. Kırıldığında görünen şey ya sızan bir
zamanlayıcı, ya kapanmayan bir takvim, ya da başka panelin görünümünü bozan bir
CSS kuralıdır — hepsi de sahada "arada bir garipleşiyor" diye tarif edilen
türden.

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
    YASAK, `innerHTML` ASLA). Ham metinde arama yapan bir test, kuralı
    AÇIKLAYAN satırı kuralın İHLALİ sanıp düşerdi — ve düzeltmenin tek yolu
    açıklamayı silmek olurdu. Gerekçesini yazamadığın kural, korunmayan
    kuraldır.
    """
    govde = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    satirlar = []
    for satir in govde.splitlines():
        kesik = "" if satir.lstrip().startswith("//") else satir.split("//", 1)[0]
        satirlar.append(kesik)
    return "\n".join(satirlar)


CODE = _code(PANEL)


# ==================================================== tek kopya: kit kuralları

def test_panel_kit_bilesenlerini_yeniden_tanimlamaz() -> None:
    # Tek kopya kuralı (ADR 0011) yalnız yeni kod için değil: `store_shipping`
    # kendi zaman çizelgesini yazdığı için kitteki düzeltmeleri hiç almadı.
    for ad in ("timeline", "kpiRow", "statusLine", "dataTable", "filterBar", "badge"):
        assert f"function {ad}(" not in CODE, f"{ad} panelde yeniden tanımlanmış"


def test_tarih_alani_native_input_kullanmaz() -> None:
    # WebKitGTK'da `<input type="date">` açılır takvimi kapanmıyor (kit kuralı 1).
    assert 'type="date"' not in CODE
    assert "type: 'date'" not in CODE
    assert "input.type = 'date'" not in CODE


def test_kok_kit_panel_sinifini_ve_kendi_onekini_tasir() -> None:
    # Yoksa toast ve overlay tüm pencereye taşar (kit kuralı 2).
    assert "'kit-panel sm'" in CODE


def test_overlay_panel_kokune_eklenir_body_ye_degil() -> None:
    # Panel değişince kabuk `root.replaceChildren()` yapıyor; body'deki bir
    # overlay orada asılı kalır (kit kuralı 3).
    assert "document.body" not in CODE
    assert "drawer(nodes.root" in CODE
    assert "confirmWithReason(nodes.root" in CODE


def test_panel_css_mount_icinde_yuklenir() -> None:
    # Kabuk, yetenek çözümü sırasında hiç açılmayan panelleri de import ediyor;
    # dosya tepesindeki `loadStyles()` kullanılmayan stilleri `document.head`'e
    # sızdırır.
    tepe = CODE.split("export function mount(", 1)[0]
    assert "loadStyles(" not in tepe, "panel.css dosya tepesinde yükleniyor"
    assert "loadStyles(import.meta.url)" in CODE


def test_temizlik_gercek_kaynak_birakir() -> None:
    # `pollLoop` hem zamanlayıcıyı hem `visibilitychange` dinleyicisini,
    # `filterBar` ise arama için tuttuğu global dinleyiciyi bırakır (kit
    # kuralı 4). Bırakılmazsa kapalı bir ekran yoklamaya devam eder.
    kapanis = CODE.rsplit("return () => {", 1)[-1]
    assert "stopLive()" in kapanis
    assert "eventFilters?.destroy()" in kapanis
    assert "localFilters?.destroy()" in kapanis


def test_gun_hesabi_todayIso_ile_yapilir() -> None:
    # `toISOString()` UTC'ye kayar ve "bugün" dün olabilir (kit kuralı 6).
    assert "toISOString(" not in CODE
    assert "todayIso(" in CODE


def test_icerik_innerHTML_ile_yazilmaz() -> None:
    # `renderHtml()` düğümleri tek tek klonlar; `innerHTML` beyaz listeyi
    # tümden atlar (kit kuralı 11). Bu ekranda gösterilen `context` bloğu
    # UZAKTAN gelen serbest JSON'dur ve HTML olarak yazılması en kötü yer.
    assert "innerHTML" not in CODE
    assert "insertAdjacentHTML" not in CODE


def test_calismayan_dugme_birakilmaz() -> None:
    # Kapalı düğmenin NEDENİ olmak zorunda (`blockedButton`); ham 404/409
    # metni gösteren ya da sessizce hiçbir şey yapmayan düğme bırakılmaz.
    assert "blockedButton(" in CODE
    assert "disabled: true" not in CODE


def test_yikici_islem_gerekce_ister() -> None:
    # PIN değil GEREKÇE (ADR 0012). Komut ve çözüm onayı `confirmWithReason`
    # üzerinden geçer; `window.confirm` kabuğun stilini de taşımıyor.
    assert "confirmWithReason(" in CODE
    assert "window.confirm" not in CODE


def test_kuru_prova_camelCase_dryRun_adiyla_gonderilir() -> None:
    # Uç `extra="forbid"` ile korunuyor: `dry_run` gönderen bir panel 422 alır,
    # `dryrun` gönderen bir panel ise SESSİZCE gerçek komut gönderirdi.
    #
    # Arama GÖVDE KURULUMLARINDA yapılır, ham metinde değil: yanıttaki
    # `result === "dry_run"` etiketi snake_case'tir ve OLMASI GEREKEN odur —
    # sunucunun denetim sözlüğünden geliyor. İkisini aynı desenle aramak,
    # doğru kodu ihlal sanardı.
    govdeler = re.findall(r"body:\s*\{[^}]*\}", CODE, flags=re.DOTALL)
    assert govdeler, "panel hiç gövde göndermiyor — desen eskimiş olabilir"
    for govde in govdeler:
        assert "dry_run" not in govde, f"gövdede snake_case bayrak: {govde[:60]}"
    assert any("dryRun" in govde for govde in govdeler), "kuru prova bayrağı hiç gitmiyor"


# ============================================================ ekranın dili

def test_baglanti_kopukken_ekran_bunu_soyler() -> None:
    # `ok:true` ile gelen `connected:false` (K7). Sessizce boş liste çizmek,
    # bir İZLEME ekranında söylenebilecek en kötü yalan: "hata yok".
    assert "connected === false" in CODE
    assert "endpoint_missing" in CODE


def test_dagitilmamis_uc_kirmizi_gosterilmez() -> None:
    # Sunucu tarafı paralel yazılıyor; "uç henüz yayında değil" beklenen bir
    # geçiş hâlidir ve her dakika bir kırmızı şerit, gerçek arızaları görünmez
    # kılardı.
    assert "'warn'" in CODE
    parca = CODE.split("state.link.missing", 1)[1][:400]
    assert "'warn'" in parca, "dağıtılmamış uç uyarı tonuyla gösterilmiyor"


def test_gizlenen_seviye_ekranda_yazar() -> None:
    # `info` varsayılan süzgeçte gizli; sessizce süzülen bir seviye "hata yok"
    # sanılmasına yol açardı.
    assert "hintBox(" in CODE
    assert "GİZLİ" in PANEL or "GİZLİ" in CODE


def test_yoklama_yalniz_acik_sekmede_calisir() -> None:
    # Kapalı sekme için istek üretmek, paylaşılan 3000/saat kovasını boşuna
    # yakar.
    assert "pollLoop(" in CODE
    assert "else stopLive();" in CODE


# ==================================================================== CSS

def test_css_yalniz_kendi_onekini_boyar() -> None:
    # Panel CSS'i `document.head`'e eklenir ve HİÇ KALDIRILMAZ: `sm-` dışında
    # bir seçici yazmak, bu panel bir kez açıldıktan sonra başka panelleri de
    # boyar (kit kuralı 9).
    govde = re.sub(r"/\*.*?\*/", "", CSS, flags=re.DOTALL)
    for satir in govde.splitlines():
        temiz = satir.strip()
        if not temiz.startswith("."):
            continue
        for secici in re.findall(r"\.[A-Za-z][\w-]*", temiz.split("{", 1)[0]):
            assert secici in {".sm"} or secici.startswith(".sm-"), \
                f"panel dışına sızan seçici: {secici}"
