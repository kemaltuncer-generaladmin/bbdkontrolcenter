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
