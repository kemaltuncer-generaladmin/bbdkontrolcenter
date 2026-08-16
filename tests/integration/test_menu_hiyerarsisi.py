"""Kenar çubuğu iki seviyelidir ve hiyerarşi VERİDEN gelir.

NEDEN TEST. Hiyerarşi `ui.nav.group` adının içindeki ` / ` ayracından
türetiliyor; kabukta bir üst-alt tablosu YOK (K1 — çekirdek hangi başlıkların
var olduğunu bilmez) ve şemada da ayrı bir alan yok. Bu tasarım doğru ama
SESSİZ: bir modülün grubuna ayraç koymayı unutmak, o ekranı üst başlığın
yanına düz bir grup olarak düşürür ve kimse hata görmez.

Ayrıca grup SIRASI da veriden geliyor (bir grubun sırası, içindeki en küçük
`order`). Eskiden bu sıra kabukta ve üreticide İKİ AYRI sabit listede
yazılıydı; yalnız biri güncellenince çalışma zamanı ile build çıktısı sessizce
ayrışıyordu. Bantların çakışmadığını burada sınıyoruz.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

LEVEL = " / "


def _navs() -> list[dict]:
    """Her modülün `ui.nav` bloğu (menüde görünenler)."""
    out = []
    for manifest in sorted((ROOT / "modules").glob("*/module.yaml")):
        data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        nav = ((data.get("ui") or {}).get("nav")) or {}
        if not nav:
            continue
        out.append({"id": data.get("id", manifest.parent.name), **nav})
    return out


def test_magaza_ekranlari_TEK_ust_baslik_altinda() -> None:
    """Kullanıcının kararı: "bbdstore'u sidebarda tek yere toplasak".

    Mağazanın her ekranı `BBD Store` başlığının altındadır — bölümler onun
    çocuğudur, kardeşi değil. Bir modülün grubuna ayraç konmazsa burada
    kırılır.
    """
    store = [nav for nav in _navs() if nav["id"].startswith("store_")]
    assert store, "mağaza modülü bulunamadı"
    for nav in store:
        group = nav["group"]
        top = group.split(LEVEL)[0]
        assert top == "BBD Store", f"{nav['id']} → “{group}” BBD Store altında değil"


def test_bolum_adlari_bilinen_kumeden() -> None:
    """Yeni bölüm açmak serbesttir ama KAZA ESERİ açılmamalı: bir yazım hatası
    (“Katalog ” / “katalog”) menüde ikinci bir bölüm doğururdu."""
    beklenen = {"Satış ve Kargo", "Katalog", "Vitrin ve İçerik",
                "Müşteri ve İletişim", "Mali", "Sistem ve Kayıt"}
    bulunan = set()
    for nav in _navs():
        if nav["group"].startswith("BBD Store" + LEVEL):
            bulunan.add(nav["group"].split(LEVEL, 1)[1])
    assert bulunan == beklenen, f"beklenmeyen bölüm(ler): {bulunan ^ beklenen}"


def test_grup_bantlari_CAKISMAZ() -> None:
    """Grup sırası içindeki en küçük `order`dan türer; bantlar çakışırsa
    başlıklar birbirinin arasına girer.

    Bu bir kez oldu: `BBD Store` (10) ile `BLD` (10) aynı değeri taşıyordu ve
    mağazanın bölümleri BLD ile Kurumsal'ın ARKASINA düşüyordu.
    """
    en_kucuk: dict[str, int] = {}
    for nav in _navs():
        top = nav["group"].split(LEVEL)[0]
        order = int(nav.get("order", 1000))
        if top not in en_kucuk or order < en_kucuk[top]:
            en_kucuk[top] = order
    # Aynı en-küçük değeri iki üst başlık paylaşamaz.
    degerler = list(en_kucuk.values())
    assert len(degerler) == len(set(degerler)), f"bant çakışması: {en_kucuk}"


def test_kontrol_paneli_BOLUM_ICINE_gomulmemis() -> None:
    """Günde onlarca kez açılan ekran, bir bölümü açmaya zorlamamalı: her
    gidişte fazladan bir tık demektir. Üst başlığın doğrudan çocuğudur."""
    dashboard = [nav for nav in _navs() if nav["id"] == "store_dashboard"]
    assert dashboard, "store_dashboard bulunamadı"
    assert dashboard[0]["group"] == "BBD Store"


def test_uretici_ile_manifestler_AYNI_gruplari_soyluyor() -> None:
    """Kayıt defteri elle düzenlenebilir bir dosya değildir; üretici çıktısıyla
    manifestler ayrışırsa çalışma zamanı ile build çıktısı farklı menü çizer."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_ui_registry", ROOT / "tools" / "build-ui-registry.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    registry = module.build(copy_panels=False)
    uretilen = {p["id"]: p["group"] for p in registry["panels"]}
    for nav in _navs():
        if nav["id"] in uretilen:
            assert uretilen[nav["id"]] == nav["group"], nav["id"]
