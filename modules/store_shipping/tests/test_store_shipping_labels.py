"""Etiket sayfa yerleşimi — saf aritmetik, `pypdf` gerektirmez."""

from __future__ import annotations

import importlib.util

import pytest
from store_shipping_backend import labels

HAS_PYPDF = importlib.util.find_spec("pypdf") is not None


def test_termal_sayfa_100x150_mm_dir() -> None:
    width, height = labels.page_size(labels.THERMAL)
    assert round(width / labels.MM) == 100
    assert round(height / labels.MM) == 150


def test_a4_sayfada_dort_goz_okuma_sirasindadir() -> None:
    boxes = labels.slots(labels.A4_4UP)
    assert len(boxes) == 4
    # Sol üst → sağ üst → sol alt → sağ alt. PDF'te başlangıç SOL ALTTIR, bu
    # yüzden ilk iki gözün y'si daha büyüktür.
    assert boxes[0][1] > boxes[2][1]
    assert boxes[0][0] < boxes[1][0]
    assert boxes[2][0] < boxes[3][0]


def test_bes_etiket_a4te_iki_sayfa_eder_bos_goz_bos_kalir() -> None:
    pages = labels.plan(5, labels.A4_4UP)
    assert len(pages) == 2
    assert [cell["index"] for cell in pages[0]] == [0, 1, 2, 3]
    # Son sayfada TEK göz dolu: eksik gözü tekrarla doldurmak aynı gönderiyi
    # iki kez kargoya vermek olurdu.
    assert [cell["index"] for cell in pages[1]] == [4]


def test_termalde_her_etiket_kendi_sayfasindadir() -> None:
    pages = labels.plan(3, labels.THERMAL)
    assert len(pages) == 3
    assert all(len(page) == 1 for page in pages)


def test_hic_etiket_yoksa_sayfa_uretilmez() -> None:
    assert labels.plan(0, labels.THERMAL) == []


def test_kaynak_kutuya_orani_bozulmadan_ortalanir() -> None:
    box = (10.0, 20.0, 100.0, 200.0)
    scale, dx, dy = labels.fit(50.0, 50.0, box)
    assert scale == 2.0                       # dar kenar belirler
    assert dx == 10.0                         # yatayda tam oturur
    assert dy == 20.0 + (200.0 - 100.0) / 2   # dikeyde ortalanır


def test_kucuk_etiket_de_buyutulur() -> None:
    # Bazı taşıyıcılar etiketi A4'ün köşesine basıyor; olduğu gibi
    # yerleştirilirse termal kâğıtta okunamayacak kadar küçük kalır.
    scale, _, _ = labels.fit(100.0, 150.0, (0.0, 0.0, 200.0, 300.0))
    assert scale == 2.0


def test_olcusu_okunamayan_etiket_sessizce_gecilmez() -> None:
    with pytest.raises(labels.LabelError):
        labels.fit(0.0, 150.0, (0.0, 0.0, 200.0, 300.0))


def test_bilinmeyen_bicim_reddedilir() -> None:
    with pytest.raises(labels.LabelError):
        labels.page_size("a3-9up")
    with pytest.raises(labels.LabelError):
        labels.compose([b"%PDF-1.4"], "a3-9up")


def test_bos_liste_birlestirilmez() -> None:
    with pytest.raises(labels.LabelError):
        labels.compose([], labels.THERMAL)


@pytest.mark.skipif(HAS_PYPDF, reason="pypdf kurulu; eksiklik yolu sınanamaz")
def test_pypdf_yoksa_ne_yapilacagi_yaziyla_soylenir() -> None:
    # K7: paket eksikse modül düşmez, uç anlaşılır bir hata döner.
    with pytest.raises(labels.LabelError) as error:
        labels.compose([b"%PDF-1.4 sahte"], labels.THERMAL)
    assert "pypdf" in str(error.value)
    assert "install-deps" in str(error.value)


@pytest.mark.skipif(not HAS_PYPDF, reason="pypdf kurulu değil")
def test_bozuk_pdf_kacinci_etiket_oldugunu_soyler() -> None:
    with pytest.raises(labels.LabelError) as error:
        labels.compose([b"bu bir PDF degil"], labels.THERMAL)
    assert "1. etiket" in str(error.value)
