"""Çekirdek rapor üreteci — biçim sözleşmeleri.

Bu üreteç 20 mağaza ekranının ortak çıktısını basacak. Bir biçim hatası tek
ekranda değil hepsinde görünür; bu yüzden sözleşme testle sabitlenir.
"""

import pytest

from km_sdk import ExportError, build_pdf, csv_bytes, money, number, percent


def test_csv_excelin_turkce_yerelinde_acilir() -> None:
    # BOM olmadan Excel UTF-8'i tanımıyor ve 'ğ' bozuluyor; ayraç virgül olursa
    # ondalıklı sayılar sütun kaydırıyor. İkisi de sahada yaşanmış hatalar.
    data = csv_bytes(["Ürün", "Tutar"], [["Kağıt", "12,50"]])
    assert data.startswith(b"\xef\xbb\xbf")
    text = data.decode("utf-8-sig")
    assert text.split("\r\n")[0] == "Ürün;Tutar"
    assert "\r\n" in text


def test_para_kurustan_turkce_bicime_donusur() -> None:
    assert money(1234567) == "12.345,67 ₺"
    assert money(0) == "0,00 ₺"
    assert money(None) == "0,00 ₺"
    assert money(5) == "0,05 ₺"


def test_sayi_ve_yuzde_turkce_ayraclarla_yazilir() -> None:
    assert number(1419) == "1.419"
    assert number(1234.5, 1) == "1.234,5"
    # Yüzde işareti Türkçede önde durur.
    assert percent(12.5) == "%12,5"


def test_pdf_uretilir_ve_bolumleri_kabul_eder() -> None:
    pdf = build_pdf(
        title="Mağaza Satış Raporu",
        subtitle="1–13 Ağustos 2026",
        sections=[
            {"kind": "tiles", "title": "Özet",
             "tiles": [("Ciro", money(125000)), ("Sipariş", "11")]},
            {"kind": "table", "title": "Ürünler",
             "headers": ["Ürün", "Adet", "Tutar"],
             "rows": [["Kağıt Ürünü ğüşİÖÇ", "3", money(37500)]],
             "align": "LRR", "widths": [3, 1, 1]},
            {"kind": "bars", "title": "Dağılım",
             "bars": [("Kitap", 8, "8 adet"), ("Set", 3, "3 adet")]},
            {"kind": "break"},
            {"kind": "note", "text": "Bu belge yasal e-Arşiv faturası değildir."},
        ],
        footer="Kontrol Merkezi · Mağaza",
    )
    assert pdf.startswith(b"%PDF")
    # Sayfa sonu bölümü gerçekten ikinci sayfayı açmalı.
    assert pdf.count(b"/Type /Page") >= 2 or pdf.count(b"/Type/Page") >= 2


def test_bos_bolum_listesi_de_gecerli_pdf_uretir() -> None:
    # Boş rapor "hata" değildir: süzgece uyan kayıt olmayabilir. Ekran boş
    # tablo göstermek yerine boş PDF üretebilmeli.
    pdf = build_pdf(title="Boş", subtitle="—", sections=[])
    assert pdf.startswith(b"%PDF")


def test_reportlab_yoksa_anlasilir_hata(monkeypatch: pytest.MonkeyPatch) -> None:
    # K7: kütüphane yoksa çekirdek düşmez, yalnız bu uç anlatarak reddeder.
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("reportlab"):
            raise ImportError("yok")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ExportError) as caught:
        build_pdf(title="x", subtitle="y", sections=[])
    assert "reportlab" in str(caught.value)


# ------------------------------------------------------------------
# Ortak finansal rapor şablonu (63-finans-pdf): süzgeç başlığı, hazırlayan,
# "Sayfa X / Y", tekrar eden tablo başlığı, ara/genel toplam, Türkçe yazı
# tipi, KVKK maskelemesi, yatay sayfa ve parça parça önizleme.

import asyncio  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
from pathlib import Path  # noqa: E402

from km_sdk import (  # noqa: E402
    filters_line,
    mask_phones,
    pdf_page_count,
    pdf_preview,
    pdf_preview_pages,
)


def _text(pdf: bytes, tmp_path: Path) -> str:
    binary = shutil.which("pdftotext")
    if not binary:
        pytest.skip("pdftotext yok")
    source = tmp_path / "rapor.pdf"
    source.write_bytes(pdf)
    out = subprocess.run([binary, "-layout", str(source), "-"], capture_output=True, check=True)
    return out.stdout.decode("utf-8")


def _finance_pdf(rows: int, **extra: object) -> bytes:
    body = [[f"Öğrenci Şükrü Çağlayan {index}", "8-A", money(index * 1250)]
            for index in range(rows)]
    return build_pdf(
        title="Nakit Tahsilatlar",
        subtitle="Kantin parası — kurum carisiyle birleşmez",
        sections=[{"kind": "table", "title": "Tahsilatlar",
                   "headers": ["Öğrenci", "Sınıf", "Tutar"], "align": "LLR",
                   "rows": [*body, {"cells": ["8-A ara toplam", "", money(99900)],
                                    "kind": "subtotal"}],
                   "footer": ["Genel toplam", f"{rows} kayıt", money(123456789)]}],
        filters=[("Dönem", "01.10.2026 – 16.10.2026"), ("Sınıf", "8-A"), ("Durum", "")],
        author="Ayşe Öğretmen", organization="BBD Akademi", **extra)  # type: ignore[arg-type]


def test_suzgec_ozeti_bos_degeri_yazmaz() -> None:
    assert filters_line([("Dönem", "1–16 Ekim"), ("Sınıf", ""), ("Durum", "Tümü")]) == \
        "Dönem: 1–16 Ekim · Durum: Tümü"
    assert filters_line(None) == ""


def test_telefon_maskelenir_son_iki_hane_kalir() -> None:
    assert mask_phones("Veli: 0532 123 45 67") == "Veli: 0*** *** ** 67"
    assert mask_phones("+90 532 123 45 67") == "0*** *** ** 67"
    # Tutar ve tarih telefon sanılmaz.
    assert mask_phones("12.345,67 ₺ · 16.10.2026") == "12.345,67 ₺ · 16.10.2026"


def test_finansal_rapor_basligi_suzgec_hazirlayan_ve_sayfa_x_y(tmp_path: Path) -> None:
    pdf = _finance_pdf(160)
    text = _text(pdf, tmp_path)
    # Süzgeçler başlıkta yazar (boş olan yazılmaz); hazırlayan ve kurum görünür.
    assert "Dönem: 01.10.2026 – 16.10.2026 · Sınıf: 8-A" in text
    assert "Durum:" not in text
    assert "Ayşe Öğretmen" in text and "BBD Akademi" in text
    # Türkçe karakterler gömülü yazı tipiyle doğru çıkar.
    assert "Öğrenci Şükrü Çağlayan 7" in text
    pages = text.count("\f") or 1
    assert pages >= 3
    assert f"Sayfa 1 / {pages}" in text and f"Sayfa {pages} / {pages}" in text
    # Tablo başlığı HER sayfada tekrar eder.
    assert text.count("Öğrenci") >= pages
    # Ara ve genel toplam satırları ile para biçimi.
    assert "8-A ara toplam" in text and "Genel toplam" in text
    # ₺ işaretini taşımayan yazı tipinde (geliştirme makinesi) kâğıda "TL" yazılır.
    assert "1.234.567,89 ₺" in text or "1.234.567,89 TL" in text


def test_yatay_sayfa_ve_eski_cagri_uyumlu(tmp_path: Path) -> None:
    wide = _finance_pdf(5, orientation="landscape")
    assert b"/MediaBox [ 0 0 841.8898 595.2756 ]" in wide
    # Eski imza (yalnız title/subtitle/sections) aynen çalışır.
    legacy = build_pdf(title="Eski", subtitle="—", sections=[
        {"kind": "table", "headers": ["A"], "rows": [["<b>kalın</b>"], ["Ali & Veli <x>"]]}])
    text = _text(legacy, tmp_path)
    assert "kalın" in text and "Ali & Veli <x>" in text


def test_onizleme_ilk_sayfalari_verir_kalani_parca_parca(tmp_path: Path) -> None:
    if not shutil.which("pdftoppm"):
        pytest.skip("pdftoppm yok")
    path = tmp_path / "buyuk.pdf"
    path.write_bytes(_finance_pdf(400))
    preview = asyncio.run(pdf_preview(path))
    assert preview["previewError"] == ""
    assert preview["shown"] == 4 and preview["pageCount"] > 4
    assert preview["pages"][0].startswith("data:image/png;base64,")
    assert asyncio.run(pdf_page_count(path)) == preview["pageCount"]
    rest = asyncio.run(pdf_preview_pages(path, first=5, last=6))
    assert len(rest) == 2
