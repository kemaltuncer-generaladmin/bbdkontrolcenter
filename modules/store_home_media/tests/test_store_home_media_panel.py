"""Panel dilinin sözleşmesi — ekranda YAZILIM TERİMİ kalmadığını sınar.

NEDEN BU TEST VAR. Bu ekranı kullanan kişi yazılım bilmiyor. "Slot", "Slider",
"Banner alanları", "CMS sayfası", "Serbest bağlantı", "Alt metni yok" — hepsi
doğru terimlerdi ve hiçbiri kullanıcının sözlüğünde yoktu. Terimler iş diline
çevrildi; bu dosya ÇEVİRİNİN GERİ ALINMASINI yakalar.

Bir terim geri sızarsa buradaki test kırılır. Kırılan test "yazım hatası"
değil, KARAR İHLALİ demektir: ekranın dili kullanıcının dilidir.

NEDEN PYTHON'DAN. Depoda JS koşucusu yok; panel metni kaynak dosyadan okunup
sınanır. Kusurlu ama hiç sınamamaktan iyi (bkz. test_store_bundles_panel.py).
"""

from __future__ import annotations

import re
from pathlib import Path

from modules.store_home_media.backend import slots

MODULE = Path(__file__).resolve().parents[1]
PANEL = (MODULE / "ui" / "panel" / "index.js").read_text(encoding="utf-8")
CSS = (MODULE / "ui" / "panel" / "panel.css").read_text(encoding="utf-8")

def _without_comments(source: str) -> str:
    """Yorumları atar.

    NEDEN. Yorumda teknik ad GEÇEBİLİR — CLAUDE.md yorumlarda "neden böyle
    yaptık"ı anlatmayı istiyor ve o açıklama çoğu zaman `slot`, `carousel`
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
    "Slider": "Ana ekran kayan görseller",
    "Banner alanları": "Tanıtım görselleri",
    "Öne çıkan koleksiyonlar": "Öne çıkan ürün grupları",
    "Duyuru şeridi": "Üst duyuru yazısı",
    "CMS sayfası": "Site içi bilgi sayfası (Hakkımızda, İletişim…)",
    "Serbest bağlantı": "Adresi ben yazacağım",
    "Alt metni": "Görselde ne var? (kısa açıklama)",
    "alt metni yok": "görsel açıklaması yok",
    "Hedef türü": "Tıklayınca nereye gitsin?",
    "Hedef bağlantı": "Gideceği sayfanın adresi",
    "Yerleşim": "Sayfanın neresinde dursun?",
    "Öznitelik": "(bu ekranda hiç geçmez)",
    "Salt okunur": "Şu an yalnız bakabilirsiniz",
    "Kuru prova": "Deneme — mağazaya yazılmadı",
    "düşük çözünürlük": "görsel küçük, bulanık çıkar",
    "oran farklı": "ölçü tutmuyor, kenarları kesilir",
}


def test_ekranda_yazilim_terimi_gorunmez() -> None:
    # BÜYÜK/KÜÇÜK HARFE DUYARLI arıyoruz. Yasak olan, kullanıcının okuduğu
    # ETİKET ("Slider"); koddaki teknik anahtar (`'slider'`) kalmalı ve
    # mağazaya giden gövdeyi o taşıyor.
    kalan = [terim for terim in YASAK if terim in VISIBLE]
    assert kalan == [], (
        "Bu terimler ekranda görünüyor ve kullanıcının sözlüğünde yok: "
        + ", ".join(f"{terim} → “{YASAK[terim]}”" for terim in kalan)
    )


def test_slot_kelimesi_kullaniciya_gosterilmez() -> None:
    """“Slot” bu ekranın en sık geçen yazılım terimiydi.

    Kod içindeki `slotRow`, `slotId`, `/slots` gibi TANIMLAYICILAR kalır
    (İngilizce ve ASCII olmaları gerekiyor); yasak olan, kullanıcının okuduğu
    Türkçe cümlede geçmesi.
    """
    kotu = [parca for parca in VISIBLE.splitlines()
            if re.search(r"\bslot", parca, flags=re.IGNORECASE)
            and re.search(r"[çğıöşüÇĞİÖŞÜ]| ve | için | bir ", parca)]
    assert kotu == [], "Kullanıcıya gösterilen metinde “slot” geçiyor: " + " | ".join(kotu)


# ================================================= sekme adları backend ile aynı

def test_sekme_adlari_backend_ile_ayni() -> None:
    """Panel ve backend AYNI kelimeyi kullanır.

    İkisi ayrı yerde duruyor (panel sekmeyi mount anında çiziyor, referans
    isteği sonradan geliyor). Ayrışırlarsa kullanıcı listede bir ad, raporda
    başka bir ad görür ve ikisinin aynı şey olduğunu anlayamaz.
    """
    for key, label in slots.AREA_LABELS.items():
        assert f"key: '{key}', label: '{label}'" in PANEL, f"{key} → {label}"
    for key, one in slots.AREA_ONE.items():
        assert f"one: '{one}'" in PANEL, f"{key} → {one}"


def test_durum_rozetleri_backend_ile_ayni() -> None:
    for label in slots.STATE_LABELS.values():
        assert f"'{label}'" in PANEL, label
    for what in slots.STATE_WHAT.values():
        assert what in PANEL, what


# ========================================== engel = neden + SIRADAKİ ADIM

def test_her_engel_hem_neden_hem_siradaki_adim_soyler() -> None:
    """`store_shipping/backend/geliver.py` → `BLOCKER_ACTIONS` deseni.

    Bir işlem yapılamıyorsa ekran iki şey söyler: neden yapılamadığı VE
    kullanıcının şimdi ne yapacağı. Yalnız neden söylemek, kullanıcıyı
    ekranda çaresiz bırakıyordu.
    """
    blok = PANEL[PANEL.index("const BLOCKERS = {"):PANEL.index("/** Engelin iki cümlesini")]
    anahtarlar = re.findall(r"^  ([A-Z_]+): \{", blok, flags=re.MULTILINE)
    assert len(anahtarlar) >= 6, anahtarlar
    assert blok.count("why:") == len(anahtarlar)
    assert blok.count("next:") == len(anahtarlar)
    # Her "sıradaki adım" gerçekten bir ADIM anlatır.
    for cumle in re.findall(r"next: '([^']*)'", blok):
        assert "Sıradaki adım" in cumle, cumle


def test_kapali_dugme_nedenini_soyler() -> None:
    """Kapalı düğme sessiz kalmaz (kit README · `blockedButton`).

    Eskiden `save.disabled = true` ve `upload.disabled = …` vardı: düğme
    soluklaşıyor, kullanıcı tıklıyor, hiçbir şey olmuyordu.
    """
    assert "function blockedReason(" in PANEL
    assert PANEL.count("blockedReason(") >= 5
    assert "aria-label" in PANEL[PANEL.index("function blockedReason("):]


# ================================================= alanın yanında ipucu var mı

def test_duzenleyicideki_her_alan_ne_yazilacagini_soyler() -> None:
    """“Bu kutuya ne yazarım” sorusu EKRANDA cevaplanır.

    Alan adı ne kadar iyi olursa olsun, örnek vermeden bir kutu boş kalır.
    """
    form = PANEL[PANEL.index("  const form = formGrid({"):PANEL.index("hedef seçici")]
    alanlar = re.findall(r"\{ key: '(\w+)',", form)
    ipuclu = re.findall(r"hint:", form)
    # Kanal alanı `choiceField` üretiyor (tek seçenekte hiç çizilmiyor);
    # kalan her alanın ipucu olmalı.
    assert len(ipuclu) >= len(alanlar) - 1, (alanlar, len(ipuclu))


def test_bolum_ne_ise_yarar_cumlesi_ekranda_durur() -> None:
    # Sekme adı "Tanıtım görselleri" doğrudur ama müşterinin bunu NEREDE
    # göreceğini söylemez; o cümle sekmenin altında durur.
    assert "function areaWhat(" in PANEL
    assert "nodes.areaWhat" in PANEL
    for what in slots.AREA_WHAT.values():
        head = what.split("—")[0].split(".")[0][:40]
        assert head in PANEL, head


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
