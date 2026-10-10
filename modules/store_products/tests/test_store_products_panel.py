"""Panel dilinin sözleşmesi — ekranda YAZILIM TERİMİ kalmadığını sınar.

NEDEN BU TEST VAR. Bu ekranı kullanan kişi yazılım bilmiyor. "SEO eksik",
"URL anahtarı", "Meta başlık", "Meta açıklama", "Öznitelik ailesi", "Varyant",
"Kuru prova" — hepsi doğru terimlerdi ve hiçbiri kullanıcının sözlüğünde
yoktu. En kötüsü de zararsız görünüyordu: "SEO eksik" diye işaretlenen ürünler
hiç düzeltilmiyordu, çünkü kimse ne demek olduğunu bilmiyordu.

Terimler iş diline çevrildi. Bu dosya ÇEVİRİNİN GERİ ALINMASINI yakalar: bir
terim geri sızarsa test kırılır ve bu "yazım hatası" değil KARAR İHLALİ
demektir.

NEDEN PYTHON'DAN. Depoda JS koşucusu yok; panel metni kaynak dosyadan okunup
sınanır. Kusurlu ama hiç sınamamaktan iyi (bkz. test_store_bundles_panel.py).
"""

from __future__ import annotations

import re
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1]
PANEL = (MODULE / "ui" / "panel" / "index.js").read_text(encoding="utf-8")
CSS = (MODULE / "ui" / "panel" / "panel.css").read_text(encoding="utf-8")


def _without_comments(source: str) -> str:
    """Yorumları atar.

    NEDEN. Yorumda teknik ad GEÇEBİLİR — CLAUDE.md yorumlarda "neden böyle
    yaptık"ı anlatmayı istiyor ve o açıklama çoğu zaman `url_key`, `SEO`,
    `product_flat` gibi gerçek adları anmak zorunda. Yasak olan, kullanıcının
    EKRANDA okuduğu cümlede geçmesi.
    """
    stripped = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return "\n".join(line for line in stripped.splitlines()
                     if not line.lstrip().startswith("//"))


def _strings(text: str) -> str:
    """Kaynaktaki tırnaklı parçalar — kullanıcının GÖRDÜĞÜ yer."""
    found = re.findall(r"'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\"", text)
    return "\n".join(one or two for one, two in found)


VISIBLE = _strings(_without_comments(PANEL))


# ==================================================== yasaklı yazılım terimleri

#: EKRANDA GÖRÜNMEYECEK terimler → yerine ne kondu.
#:
#: Değer bilgi amaçlıdır: test kırıldığında geliştirici "peki ne yazacaktım"
#: diye aramasın diye burada durur.
YASAK = {
    "SEO": "Google görünümü",
    "URL anahtarı": "Sayfa adresi",
    "Meta başlık": "Google’da görünecek başlık",
    "Meta açıklama": "Google’da görünecek açıklama",
    "Öznitelik ailesi": "Bilgi alanı grubu",
    "Öznitelik": "(ekranda geçmez)",
    "Varyant": "Seçenek",
    "Kuru prova": "DENEME yapıldı",
    "Kritik stok": "Stoğu azalanlar",
    "Görselsiz": "Fotoğrafı olmayanlar",
    "Anahtar kelimeler": "Müşteri bunu ararken hangi kelimeleri yazar?",
    "product_flat": "(ekranda geçmez)",
    "OKU-DEĞİŞTİR-YAZ": "(ekranda geçmez)",
    "Fark tablosu": "Şu an / Onaylarsanız tablosu",
    "Kip": "Ne yapılsın?",
    "İşlem başarısız": "(ne yapılacağı yazılır)",
}


def test_ekranda_yazilim_terimi_gorunmez() -> None:
    # BÜYÜK/KÜÇÜK HARFE DUYARLI arıyoruz. Yasak olan, kullanıcının okuduğu
    # ETİKET; koddaki teknik anahtar (`'urlKey'`, `'seo_missing'`) kalmalı ve
    # mağazaya giden gövdeyi o taşıyor.
    kalan = [terim for terim in YASAK if terim in VISIBLE]
    assert kalan == [], (
        "Bu terimler ekranda görünüyor ve kullanıcının sözlüğünde yok: "
        + ", ".join(f"{terim} → “{YASAK[terim]}”" for terim in kalan)
    )


def test_seo_eksik_cipi_sonucu_soyler() -> None:
    """“SEO eksik” bir terimdi; kullanıcı ne yapması gerektiğini bilmiyordu.

    Yeni ad hem aynı şeyi söyler hem NEDEN önemli olduğunu anlatır.

    10.10.2026 (docs/tasarim-sade-magaza.md §6): aynı sayıyı tekrarlayan
    “Google’da zor bulunur” KPI kutusu kalktı; sayı artık çipin kendisinde.
    Çip adı EKRANDA (yorumda değil) kalmalı.
    """
    assert "Google’da zor bulunanlar" in VISIBLE


def test_sayfa_adresi_alani_ne_oldugunu_ornekle_anlatir() -> None:
    """“URL anahtarı” kutusuna ne yazılacağı EKRANDA cevaplanmalı."""
    assert "Sayfa adresi" in PANEL
    assert "bbdstore.com.tr/BURASI" in PANEL
    # Değiştirmenin sonucu da söylenir: eski bağlantılar kırılır.
    assert "eski adres çalışmaz" in PANEL


def test_google_sekmesi_alanlari_nerede_gorunecegini_soyler() -> None:
    seo = PANEL[PANEL.index("function paintSeo("):PANEL.index("async function paintHistory(")]
    assert "Google’da görünecek başlık" in seo
    assert "Google’da görünecek açıklama" in seo
    assert "Google’da böyle görünecek" in seo          # önizleme kartının başlığı
    assert seo.count("hint:") >= 3                     # her alanın ipucu var


# ========================================== engel = neden + SIRADAKİ ADIM

def test_her_engel_hem_neden_hem_siradaki_adim_soyler() -> None:
    """`store_shipping/backend/geliver.py` → `BLOCKER_ACTIONS` deseni.

    Bir işlem yapılamıyorsa ekran iki şey söyler: neden yapılamadığı VE
    kullanıcının şimdi ne yapacağı. Yalnız neden söylemek ("Varyantlı ürünün
    fiyatı varyantlarındadır") kullanıcıyı ekranda çaresiz bırakıyordu.
    """
    blok = PANEL[PANEL.index("const BLOCKERS = {"):PANEL.index("/** Engelin iki cümlesini")]
    anahtarlar = re.findall(r"^  ([A-Z_]+): \{", blok, flags=re.MULTILINE)
    assert len(anahtarlar) >= 6, anahtarlar
    assert blok.count("why:") == len(anahtarlar)
    assert blok.count("next:") == len(anahtarlar)
    for cumle in re.findall(r"next: '([^']*)'", blok):
        assert "Sıradaki adım" in cumle, cumle


def test_kapali_dugme_nedenini_soyler() -> None:
    """Kapalı düğme sessiz kalmaz (kit README · `blockedButton`)."""
    assert "function blockedReason(" in PANEL
    assert "blockedReason(previewBtn, 'NO_BOOK_FIELDS')" in PANEL
    assert "aria-label" in PANEL[PANEL.index("function blockedReason("):]


def test_varyantli_uruntte_fiyat_kapaliysa_nereye_gidilecegi_yazilir() -> None:
    # Eskiden "alanlar kapalıdır" deyip bırakıyordu.
    assert "blockerBox('PRICE_ON_VARIANTS')" in PANEL
    assert "“Seçenekler” sekmesine geçip" in PANEL


# ============================================ silme ile vitrinden kaldırma ayrı

def test_silme_ile_vitrinden_kaldirma_ekranda_ayirt_edilir() -> None:
    """İkisi ayrı işlemdir ve biri geri alınamaz.

    "Pasifleştir" ile "Sil" yan yana duruyordu ve ikisinin farkı yalnız
    düğmenin rengiydi. Artık her iki metin de ötekine yol gösteriyor.
    """
    assert "Vitrinden kaldır" in PANEL
    assert "GERİ ALINAMAZ" in PANEL
    assert "Yalnız vitrinden kaldırmak" in PANEL


# ================================================= alanın yanında ipucu var mı

def test_yeni_urun_formundaki_her_alan_ne_yazilacagini_soyler() -> None:
    """“Bu kutuya ne yazarım” sorusu EKRANDA cevaplanır."""
    form = PANEL[PANEL.index("      { key: 'sku', label: 'Stok kodu (SKU)'"):
                 PANEL.index("    // Gizlenen alanın değeri BOŞ gider")]
    alanlar = re.findall(r"\{ key: '(\w+)',", form)
    assert len(alanlar) >= 9, alanlar
    assert form.count("hint:") >= len(alanlar), (alanlar, form.count("hint:"))


def test_toplu_islem_kutularinda_ipucu_var() -> None:
    """Toplu fiyat kutusunda "-10" mu "%10" mu yazılacağı ekranda yazmalı."""
    assert "function labelled(label, control, hint)" in PANEL
    assert "10 = %10 zam, -10 = %10 indirim" in PANEL


def test_toplu_islem_once_ne_olacagini_gosterir() -> None:
    # "Önizle" düğmesi ne yaptığını söylemiyordu.
    assert "Önce ne olacağını göster" in PANEL
    assert "Onaylarsanız" in PANEL              # fark tablosunun sütun başlığı
    assert "Onaylamadan hiçbir şey değişmez" in PANEL


# ============================== sade ekran (docs/tasarim-sade-magaza.md, 10.10.2026)
#
# Kullanıcı kararı: mağaza ekranları "karışık değil, sade, tık tık tık". Bu
# testler kuralın GERİ SIZMASINI yakalar — kırılırsa bir düğme/kutu yeniden
# varsayılan görünüme dönmüş demektir. Özellik silinmedi; yalnız taşındı.

MOUNT = PANEL[PANEL.index("export function mount("):]
FILTER_BAR = MOUNT[MOUNT.index("nodes.filters = filterBar("):MOUNT.index("nodes.chips = chipRow(")]


def test_suzgec_seridi_sade_kipte_ve_en_cok_iki_alan_gorunur() -> None:
    """§1 — Kategori/Tür/Fiyat "Filtreler" düğmesinin arkasına katlanır."""
    assert "compact: true" in FILTER_BAR
    assert 1 <= FILTER_BAR.count("primary: true") <= 2


def test_ikincil_eylemler_tek_menude_tek_birincil_dugme() -> None:
    """§2 — dört dışa aktarma yan yana dizilmez; tek "Dışa aktar" menüsü."""
    kod = _without_comments(FILTER_BAR)
    assert "menuButton('Dışa aktar'" in kod
    assert kod.count("variant: 'primary'") == 1
    for etiket in ("Ekrandaki ürünler", "Tüm ürünler", "Stok raporu", "Fiyat listesi"):
        assert etiket in kod, etiket
    # Menüdekiler hâlâ AYNI işlere bağlı — taşındı, silinmedi.
    for is_ in ("exportVisible", "exportAll", "report.run('stock'", "report.run('pricelist'"):
        assert is_ in kod, is_


def test_tablo_en_cok_yedi_sutun_ve_satirda_sil_yok() -> None:
    """§5 — yıkıcı eylem satırda durmaz; seçim kutusu da sütun sayılır."""
    sutunlar = _without_comments(PANEL[PANEL.index("const COLUMNS = ["):
                                       PANEL.index("function emptyNode(")])
    anahtarlar = re.findall(r"key: '(\w+)'", sutunlar)
    assert len(anahtarlar) + 1 <= 7, anahtarlar          # +1: `selectable` kutusu
    assert "deleteDialog" not in sutunlar
    assert "'Sil'" not in sutunlar


def test_silme_cekmecede_ve_coklu_secimde_duruyor() -> None:
    """Satırdan kalkan "Sil" kaybolmadı: çekmecede ve seçim çubuğunda."""
    genel = PANEL[PANEL.index("function paintGeneral("):PANEL.index("function desiFromPages(")]
    assert "button('Ürünü sil'" in genel
    assert "deleteDialog([product.id]" in genel
    secim = PANEL[PANEL.index("function renderSelectionBar("):PANEL.index("function applyChip(")]
    assert "deleteDialog(state.selection" in secim


def test_kpi_seridi_yok_sayilar_ciplerde() -> None:
    """§6 — çipte sayısı yazan değer KPI olarak tekrarlanmaz; en çok beş çip."""
    assert "nodes.kpi" not in PANEL
    cipler = PANEL[PANEL.index("const CHIPS = ["):PANEL.index("// ENGELLER")]
    assert len(re.findall(r"\{ key: '", cipler)) <= 5
    assert "nodes.chips.counts(" in PANEL


def test_varsayilan_gorunumde_ve_cekmecede_aciklama_kutusu_yok() -> None:
    """§3 — `hintBox` yalnız menüde kapalı duran Nitelikler sekmesinde kalabilir."""
    acik_ekranlar = _without_comments(PANEL[:PANEL.index("const SCHEMA_SCOPES = [")])
    assert "hintBox(" not in acik_ekranlar


# ===================================================== kit kuralları (bozulmasın)

def test_panel_kokunun_kit_panel_sinifi_var() -> None:
    assert "'kit-panel sp'" in PANEL


def test_panel_css_yalnizca_mount_icinde_yuklenir() -> None:
    assert PANEL.count("loadStyles(") == 1
    assert "loadStyles(import.meta.url)" in PANEL[PANEL.index("export function mount("):]


def test_css_oneki_yalniz_kendi_ad_alanindadir() -> None:
    for line in CSS.splitlines():
        stripped = line.strip()
        if not stripped.startswith("."):
            continue
        assert stripped.startswith(".sp"), stripped
