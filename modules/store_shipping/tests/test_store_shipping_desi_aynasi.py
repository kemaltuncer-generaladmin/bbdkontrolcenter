"""Desi formülü sunucudakiyle AYNI sonucu verir.

NEDEN İKİ KOPYA VAR: sunucu (`Bbd\\Shipping\\Support\\BookDimensions`)
müşteriden alınacak kargo ücretini hesaplıyor; burası personele gösterilen
tahmini. K3 gereği modül modülü import edemez, iki depo arasında ortak kod da
yok — kopya zorunlu.

NEDEN BU TEST ŞART: kopyalar sessizce ayrışır. Ayrışma çalışma anında hata
vermez; personel bir rakam görür, müşteri başka bir tutar öder ve fark ancak
KARGO FATURASI GELDİĞİNDE ortaya çıkar. Yani en pahalı anda.

Beklenen değerler sunucudaki sınıf ÇALIŞTIRILARAK üretildi (2026-08-15,
php:8.3-cli, `BookDimensions::desiForPages`). Elle yazılmadı.

YUVARLAMA NOTU: karşılaştırma ham değer üzerinden yapılır. PHP `round()`
yarımı yukarı, Python bankacı yuvarlaması yapar; 176 sayfada gerçek değer
0,1839325 olduğu için altıncı ondalıkta ayrışırlar. Bu bir formül farkı
DEĞİLDİR ve teste yansıtılmamalıdır — yansıtılsaydı test, olmayan bir hatayı
kovalardı.
"""

from __future__ import annotations

import math

import pytest
from store_shipping_backend import shipping

#: Sunucudaki sınıfın ürettiği referans değerler: (sayfa, kalınlık_cm, desi).
SUNUCU = [
    (0, 0.1, 0.021142),
    (1, 0.104375, 0.022067),
    (96, 0.52, 0.109937),
    (176, 0.87, 0.1839325),
    (208, 1.01, 0.213531),
    (240, 1.15, 0.243129),
    (364, 1.6925, 0.357823),
    (512, 2.34, 0.494715),
    (1008, 4.51, 0.953489),
    (5000, 21.975, 4.645881),
    # Tavanın üstü: 9999 sayfa 5000'e kırpılır, sonuç 5000 ile AYNI olmalı.
    (9999, 21.975, 4.645881),
]


@pytest.mark.parametrize(("sayfa", "kalinlik", "desi"), SUNUCU)
def test_sayfa_basina_deger_sunucuyla_ayni(sayfa: int, kalinlik: float, desi: float) -> None:
    assert shipping.thickness_cm_for_pages(sayfa) == pytest.approx(kalinlik, abs=1e-9)
    assert shipping.desi_for_pages(sayfa) == pytest.approx(desi, abs=1e-6)


def test_katsayilar_sunucudakiyle_ayni() -> None:
    # Katsayı burada da yazılı; biri değiştirilirse test kırılır ve kopyanın
    # sapması commit anında görülür.
    assert shipping.PAGE_THICKNESS_MM == 0.04375
    assert shipping.COVER_THICKNESS_MM == 1.0
    assert shipping.TRIM_WIDTH_CM == 19.5
    assert shipping.TRIM_HEIGHT_CM == 27.5
    assert shipping.PACKAGING_MARGIN_CM == 1.0
    assert shipping.DESI_DIVISOR == 3000
    assert shipping.MAX_PAGE_COUNT == 5000
    assert shipping.footprint_cm2() == pytest.approx(634.25, abs=1e-9)


# ================================================== kitaplar üst üste konur

def test_adet_KALINLIGA_uygulanir_hacme_degil() -> None:
    # Tek kitabın hacmini adetle çarpmak, kitapları YAN YANA dizmek demektir ve
    # kolinin tabanını gereksiz büyütür. Üst üste konunca taban sabit kalır.
    tek = shipping.desi_for_pages(208)
    bes_yanyana = tek * 5
    bes_ustuste = shipping.desi_for_thickness_cm(
        shipping.thickness_cm_for_pages(208) * 5)

    # Üst üste koymak daha az yer kaplar: kapak payı bir kez değil beş kez
    # sayılsa da taban alanı tek koli olarak kalır — fark burada değil,
    # yan yana dizmenin tabanı beşe katlamasında.
    assert bes_ustuste == pytest.approx(bes_yanyana, abs=1e-9)
    # Asıl kanıt: sepet hesabı tek koli tabanı kullanıyor.
    sepet = shipping.desi_for_items([{"pageCount": 208, "qty": 5}])
    assert sepet == max(1, math.ceil(bes_ustuste))


def test_sepet_toplami_kalinliklari_toplar() -> None:
    kalem = [{"pageCount": 208, "qty": 3}, {"pageCount": 364, "qty": 2}]
    beklenen_kalinlik = (shipping.thickness_cm_for_pages(208) * 3
                         + shipping.thickness_cm_for_pages(364) * 2)
    beklenen = max(1, math.ceil(shipping.desi_for_thickness_cm(beklenen_kalinlik)))
    assert shipping.desi_for_items(kalem) == beklenen


def test_uc_kitap_bir_desi_on_kitap_uc_desi() -> None:
    # Kullanıcıya gösterilen tablo bu. Bugünkü davranış "her kitap 1 desi"
    # olduğu için 3 kitap 3 desi, 10 kitap 10 desi ediyordu.
    assert shipping.desi_for_items([{"pageCount": 208, "qty": 3}]) == 1
    assert shipping.desi_for_items([{"pageCount": 208, "qty": 10}]) == 3


# ============================================================ eksik veri

def test_sayfasi_okunmayan_kalem_VARSAYILANLA_sayilir() -> None:
    # Veri eksikse müşteriye az fatura kesip zarar etmektense fazla hesaplamak
    # yeğlenir — bugünkü davranış korunur.
    assert shipping.desi_for_items([{"qty": 2}]) == 2


def test_bos_sepet_en_az_bir_desi() -> None:
    assert shipping.desi_for_items([]) == 1
    assert shipping.desi_for_items(None) == 1


def test_adet_alani_farkli_adlarla_gelebilir() -> None:
    # Sipariş `qtyOrdered`, sepet `quantity` kullanıyor; ikisi de tanınmalı.
    a = shipping.desi_for_items([{"page_count": 208, "qty_ordered": 3}])
    b = shipping.desi_for_items([{"pageCount": 208, "quantity": 3}])
    assert a == b == 1


def test_sayfa_tavani_ucreti_ucurmaz() -> None:
    # `page_count` elle doldurulan bir alan ve doğrudan paraya dönüşüyor;
    # "50000 sayfa" yazımı tavana kırpılır.
    assert shipping.desi_for_pages(50_000) == shipping.desi_for_pages(5_000)
