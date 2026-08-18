"""Panel dilinin ve yapısının sözleşmesi.

NEDEN BU TEST VAR. Bu ekranı kullanan kişi yazılım bilmiyor. "Slot", "Slider",
"Banner alanları", "CMS sayfası", "Serbest bağlantı", "Alt metni" — hepsi
doğru terimlerdi ve hiçbiri kullanıcının sözlüğünde yoktu. Terimler iş diline
çevrildi; bu dosya ÇEVİRİNİN GERİ ALINMASINI yakalar.

Bir terim geri sızarsa buradaki test kırılır. Kırılan test "yazım hatası"
değil, KARAR İHLALİ demektir: ekranın dili kullanıcının dilidir.

DOSYA 18.08.2026'DA DARALDI. Sekme adlarını, durum rozetlerini ve "bu bölüm ne
işe yarar" cümlelerini sınayan üç test kaldırıldı: ekranda ne sekme kaldı ne
durum rozeti. Yerlerine TEK İŞ kuralını koruyan testler geldi — kaldırılan
sekmeler ve alanlar geri sızarsa bu dosya söyler.

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
    yaptık"ı anlatmayı istiyor ve o açıklama çoğu zaman `slide`, `carousel`
    gibi gerçek adları anmak zorunda. Yasak olan, kullanıcının EKRANDA
    okuduğu cümlede geçmesi. Bu yüzden hem `//` satırları hem `/* … */`
    blokları düşürülür.
    """
    stripped = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return "\n".join(line for line in stripped.splitlines()
                     if not line.lstrip().startswith("//"))


CODE = _without_comments(PANEL)


def _strings(text: str) -> str:
    """Kaynaktaki tek/çift tırnaklı metin parçaları — kullanıcının GÖRDÜĞÜ yer."""
    found = re.findall(r"'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\"", text)
    return "\n".join(one or two for one, two in found)


VISIBLE = _strings(CODE)


# ==================================================== yasaklı yazılım terimleri

#: EKRANDA GÖRÜNMEYECEK terimler → yerine ne konduğu.
#:
#: Anahtar terim, değer ise KULLANICININ ANLADIĞI karşılığı. Değer bilgi
#: amaçlıdır: test kırıldığında geliştirici "peki ne yazacaktım" diye
#: aramasın diye burada durur.
YASAK = {
    "Slider": "Ana ekranda dönen görseller",
    "Banner": "(bu ekranda hiç geçmez)",
    "Karusel": "(bu ekranda hiç geçmez)",
    "CMS sayfası": "adresi doğrudan yazın",
    "Serbest bağlantı": "Tıklayınca nereye gitsin?",
    "Alt metni": "Görselde ne var? (kısa açıklama)",
    "Hedef türü": "Tıklayınca nereye gitsin?",
    "Hedef bağlantı": "Gideceği sayfanın adresi",
    "Yerleşim": "(bu ekranda hiç geçmez)",
    "Öznitelik": "(bu ekranda hiç geçmez)",
    "Salt okunur": "Şu an yalnız bakabilirsiniz",
    "Kuru prova": "Deneme — mağazaya yazılmadı",
    "düşük çözünürlük": "görsel küçük, bulanık çıkar",
    "oran farklı": "ölçü tutmuyor, kenarları kesilir",
}


def test_ekranda_yazilim_terimi_gorunmez() -> None:
    # BÜYÜK/KÜÇÜK HARFE DUYARLI arıyoruz. Yasak olan, kullanıcının okuduğu
    # ETİKET; koddaki teknik anahtar kalmalı ve mağazaya giden gövdeyi o taşıyor.
    kalan = [terim for terim in YASAK if terim in VISIBLE]
    assert kalan == [], (
        "Bu terimler ekranda görünüyor ve kullanıcının sözlüğünde yok: "
        + ", ".join(f"{terim} → “{YASAK[terim]}”" for terim in kalan)
    )


def test_slot_kelimesi_kullaniciya_gosterilmez() -> None:
    """“Slot” bu ekranın en sık geçen yazılım terimiydi.

    Kod içindeki `slideRow`, `hm-slot` gibi TANIMLAYICILAR kalır (İngilizce ve
    ASCII olmaları gerekiyor); yasak olan, kullanıcının okuduğu Türkçe cümlede
    geçmesi.
    """
    kotu = [parca for parca in VISIBLE.splitlines()
            if re.search(r"\bslot|\bslayt|\bslide", parca, flags=re.IGNORECASE)
            and re.search(r"[çğıöşüÇĞİÖŞÜ]| ve | için | bir ", parca)]
    assert kotu == [], "Kullanıcıya gösterilen metinde teknik ad geçiyor: " + " | ".join(kotu)


# ================================================== TEK İŞ — geri sızmasın

#: Kaldırılan sekmeler ve alanlar. Biri geri gelirse ekran yeniden dört işli
#: olur ve kullanıcı kararı sessizce iptal edilmiş olur.
KALDIRILANLAR = [
    "tabBar",            # dört sekme
    "filterBar",         # süzgeçler
    "reportChain",       # yerleşim raporu
    "csvBlob",           # CSV
    "kpiRow",            # durum sayaçları
    "dateField",         # yayın tarihleri
    "/reorder",          # sıra ayrı uç değil
    "/reference",        # kanal/dil/kategori listeleri
    "/export",
    "/print",
]


def test_kaldirilan_yuzeyler_geri_gelmemis() -> None:
    kalan = [parca for parca in KALDIRILANLAR if parca in PANEL]
    assert kalan == [], (
        "Bu yüzeyler 18.08.2026'da bilerek kaldırıldı; geri gelmiş: " + ", ".join(kalan))


def test_panel_yalniz_kendi_uclarini_cagirir() -> None:
    """Ekranın tek işi var; uç listesi de o kadar olmalı."""
    cagrilar = set(re.findall(r"\$\{BASE\}(/[a-z/-]+)", PANEL))
    assert cagrilar == {"/slides", "/image/check", "/image/upload", "/link-search", "/audit"}


def test_sira_klavyeyle_de_degistirilebilir() -> None:
    """Sürükle-bırak TEK YOL OLAMAZ: fare kullanamayan personel için Ctrl+ok
    tek erişim yolu (ve en hızlısı)."""
    assert "Ctrl+↑" in PANEL
    assert "event.ctrlKey" in PANEL
    assert "ArrowUp" in PANEL and "ArrowDown" in PANEL
    # Taşıma ekran okuyucuya da duyurulur.
    assert "aria-live" in PANEL


def test_surukle_birak_duruyor() -> None:
    for olay in ("dragstart", "dragover", "drop"):
        assert olay in PANEL, olay


# ========================================== engel = neden + SIRADAKİ ADIM

def test_her_engel_hem_neden_hem_siradaki_adim_soyler() -> None:
    """`store_shipping/backend/geliver.py` → `BLOCKER_ACTIONS` deseni.

    Bir işlem yapılamıyorsa ekran iki şey söyler: neden yapılamadığı VE
    kullanıcının şimdi ne yapacağı. Yalnız neden söylemek, kullanıcıyı
    ekranda çaresiz bırakıyordu.
    """
    blok = PANEL[PANEL.index("const BLOCKERS = {"):PANEL.index("/** Engelin iki cümlesini")]
    anahtarlar = re.findall(r"^  ([A-Z_]+): \{", blok, flags=re.MULTILINE)
    assert len(anahtarlar) >= 3, anahtarlar
    assert blok.count("why:") == len(anahtarlar)
    assert blok.count("next:") == len(anahtarlar)
    # Her "sıradaki adım" gerçekten bir ADIM anlatır.
    for cumle in re.findall(r"next: '([^']*)'", blok):
        assert "Sıradaki adım" in cumle, cumle


def test_kapali_dugme_nedenini_soyler() -> None:
    """Kapalı düğme sessiz kalmaz (kit README · `blockedButton`).

    Eskiden yalnız `disabled = true` vardı: düğme soluklaşıyor, kullanıcı
    tıklıyor, hiçbir şey olmuyordu.
    """
    assert "function blockedReason(" in PANEL
    assert "aria-label" in PANEL[PANEL.index("function blockedReason("):]


# ================================================= alanın yanında ipucu var mı

def test_her_kutu_ne_yazilacagini_soyler() -> None:
    """“Bu kutuya ne yazarım” sorusu EKRANDA cevaplanır.

    Alan adı ne kadar iyi olursa olsun, örnek vermeden bir kutu boş kalır;
    liste satırındaki iki kutunun da yer tutucu metni var.
    """
    assert "Bu görsele ne ad verelim?" in PANEL
    assert "Tıklayınca nereye gitsin?" in PANEL
    # Yer tutucu tek başına yetmez: ekran okuyucu için etiket de olmalı.
    assert PANEL.count("setAttribute('aria-label'") >= 4


def test_ekranin_tek_isi_ekranda_yazar() -> None:
    # Kullanıcının cümlesi ekranın tepesinde durur: "oranın tek bi işlevi olacak".
    assert "tek işi var" in PANEL


# ===================================================== kit kuralları (bozulmasın)

def test_panel_kokunun_kit_panel_sinifi_var() -> None:
    assert "'kit-panel hm'" in PANEL


def test_panel_css_yalnizca_mount_icinde_yuklenir() -> None:
    assert PANEL.count("loadStyles(") == 1
    assert "loadStyles(import.meta.url)" in PANEL[PANEL.index("export function mount("):]


def test_css_oneki_yalniz_kendi_ad_alanindadir() -> None:
    for line in CSS.splitlines():
        stripped = line.strip()
        if not stripped.startswith("."):
            continue
        assert stripped.startswith(".hm"), stripped
