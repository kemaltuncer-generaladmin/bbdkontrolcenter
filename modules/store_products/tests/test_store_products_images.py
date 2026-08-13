"""Görsel incelemesi — saf mantık, ağa çıkmaz.

Gerçek dosya OKUNMAZ: her biçimin başlığı elle kurulur. Böylece test ne
ikili varlığa ne de ortamdaki bir kütüphaneye bağlı olur.
"""

from __future__ import annotations

import base64
import struct
import zlib

from store_products_backend import images

# ================================================================ yardımcı

def _png(width: int, height: int) -> bytes:
    """En küçük geçerli PNG başlığı: imza + IHDR."""
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    chunk = struct.pack(">I", len(header)) + b"IHDR" + header
    chunk += struct.pack(">I", zlib.crc32(b"IHDR" + header))
    return b"\x89PNG\r\n\x1a\n" + chunk


def _jpeg(width: int, height: int, *, exif: int = 0) -> bytes:
    """SOF0 taşıyan JPEG. `exif` kadar baytlık bir APP1 bloğu ÖNE konur.

    Boyutun dosyanın başında olmadığını kanıtlamak için: gerçek fotoğraflarda
    SOF bloğundan önce kilobaytlarca EXIF ve ICC verisi durur.
    """
    out = b"\xff\xd8"
    if exif:
        out += b"\xff\xe1" + struct.pack(">H", exif + 2) + b"\x00" * exif
    out += b"\xff\xc0" + struct.pack(">HBHHB", 17, 8, height, width, 3) + b"\x00" * 9
    return out + b"\xff\xd9"


def _webp_lossy(width: int, height: int) -> bytes:
    body = b"VP8 " + struct.pack("<I", 10) + b"\x00" * 3 + b"\x9d\x01\x2a"
    body += struct.pack("<HH", width, height)
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WEBP" + body


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


MB = 1024 * 1024


# ============================================================ tür tanıma

def test_tur_uzantidan_degil_icerikten_okunur() -> None:
    # `.jpg` adlı bir PNG: uzantıya güvenen kod yanlış türle yükler.
    assert images.sniff(_png(10, 10)) == "image/png"
    assert images.sniff(_jpeg(10, 10)) == "image/jpeg"
    assert images.sniff(_webp_lossy(10, 10)) == "image/webp"
    assert images.sniff(b"%PDF-1.7 ...") == "application/pdf"
    assert images.sniff(b"rastgele") == ""


def test_pdf_gorsel_diye_yuklenemez_ve_neden_soylenir() -> None:
    try:
        images.inspect_upload(_b64(b"%PDF-1.7 " + b"0" * 100), filename="katalog.jpg",
                              max_bytes=4 * MB)
    except images.ImageError as failure:
        assert "PDF" in str(failure)
        assert "JPEG" in str(failure)
    else:
        raise AssertionError("PDF kabul edilmemeliydi")


def test_gif_reddedilir_okunur_bir_adla() -> None:
    try:
        images.inspect_upload(_b64(b"GIF89a" + b"\x0a\x00\x0a\x00" + b"0" * 40),
                              filename="hareketli.gif", max_bytes=4 * MB)
    except images.ImageError as failure:
        assert "GIF" in str(failure)
    else:
        raise AssertionError("GIF kabul edilmemeliydi")


# =============================================================== ölçü okuma

def test_png_olcusu_okunur() -> None:
    assert images.dimensions(_png(1200, 800)) == (1200, 800)


def test_jpeg_olcusu_exif_blogunun_ARKASINDAN_okunur() -> None:
    # Boyut dosyanın başında olsaydı bu test 0 döndürürdü.
    assert images.dimensions(_jpeg(1024, 768, exif=4096)) == (1024, 768)


def test_jpeg_genislik_ve_yukseklik_ters_okunmaz() -> None:
    # SOF bloğunda YÜKSEKLİK önce yazılıdır; ters okuyan kod 800x600'ü
    # 600x800 gösterir ve "kare değil" uyarısını yanlış tarafa çevirir.
    assert images.dimensions(_jpeg(800, 600)) == (800, 600)


def test_webp_olcusu_okunur() -> None:
    assert images.dimensions(_webp_lossy(640, 480)) == (640, 480)


def test_bozuk_baslik_sifir_uydurmaz() -> None:
    assert images.dimensions(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4) is None


# ========================================================== kalite uyarısı

def test_kucuk_gorsel_somut_uyarir_kac_piksel_oldugunu_soyler() -> None:
    notes = images.quality_notes(320, 240, min_width=800, min_height=800)
    assert any("800×800" in note and "320×240" in note for note in notes)
    assert any("bulanık" in note for note in notes)


def test_onerilen_olcuyu_saglayan_kare_gorsel_uyarilmaz() -> None:
    assert images.quality_notes(1000, 1000, min_width=800, min_height=800) == []


def test_asiri_oran_uyarilir_ve_oran_sadelestirilir() -> None:
    notes = images.quality_notes(1200, 400, min_width=100, min_height=100, max_ratio=1.6)
    assert len(notes) == 1
    assert "3:1" in notes[0]
    assert "kırpılır" in notes[0]


def test_sadelesmeyen_oran_ondalikla_yazilir() -> None:
    assert images.ratio_text(1000, 543) == "1,8:1"
    assert images.ratio_text(800, 800) == "1:1"


# =============================================================== inceleme

def test_sinir_asan_dosya_hic_gonderilmez_ve_MB_ile_soylenir() -> None:
    try:
        images.inspect_upload(_b64(_png(10, 10) + b"0" * (5 * MB)), filename="buyuk.png",
                              max_bytes=4 * MB)
    except images.ImageError as failure:
        assert "MB" in str(failure)
        assert "gönderilmedi" in str(failure)
    else:
        raise AssertionError("sınır aşıldı, reddedilmeliydi")


def test_base64_uzunlugu_bile_sinirin_ustundeyse_bellege_acilmaz() -> None:
    # Çözülmüş hâli değil, METNİN uzunluğu bakılır: 40 MB'lık bir metni
    # ölçmek için belleğe açmanın anlamı yok.
    try:
        images.inspect_upload("A" * (6 * MB), filename="dev.png", max_bytes=4 * MB)
    except images.ImageError as failure:
        assert "MB" in str(failure)
    else:
        raise AssertionError("kaba sınır çalışmadı")


def test_bozuk_base64_anlasilir_hataya_cevrilir() -> None:
    try:
        images.decode("bu base64 değil!!!")
    except images.ImageError as failure:
        assert "base64" not in str(failure).lower()   # kullanıcı bu kelimeyi görmemeli
        assert "yeniden seçip deneyin" in str(failure)
    else:
        raise AssertionError("bozuk içerik kabul edildi")


def test_data_uri_onegi_ve_satir_sonlari_temizlenir() -> None:
    raw = _png(800, 800)
    content = "data:image/png;base64," + "\n".join(
        [_b64(raw)[i:i + 40] for i in range(0, len(_b64(raw)), 40)])
    assert images.decode(content) == raw


def test_yanlis_uzanti_duzeltilir_ve_kullaniciya_soylenir() -> None:
    report = images.inspect_upload(_b64(_png(900, 900)), filename="kapak.jpg",
                                   mime="image/jpeg", max_bytes=4 * MB)
    # Sunucu dosyayı uzantısıyla kaydediyor; yanlış ad vitrinde açılmayan
    # görsel demektir.
    assert report["filename"] == "kapak.png"
    assert report["mime"] == "image/png"
    assert any("PNG" in note for note in report["warnings"])


def test_yol_iceren_dosya_adi_temizlenir() -> None:
    report = images.inspect_upload(_b64(_png(900, 900)),
                                   filename="../../etc/kapak\".png", max_bytes=4 * MB)
    assert "/" not in report["filename"]
    assert '"' not in report["filename"]


def test_kucuk_gorsel_ENGELLENMEZ_uyarilir() -> None:
    report = images.inspect_upload(_b64(_png(320, 240)), filename="k.png", max_bytes=4 * MB,
                                   min_width=800, min_height=800)
    assert report["width"] == 320
    assert report["height"] == 240
    assert any("320×240" in note for note in report["warnings"])


def test_uygun_gorsel_uyarisiz_gecer() -> None:
    report = images.inspect_upload(_b64(_jpeg(1000, 1000)), filename="urun.jpg",
                                   mime="image/jpeg", max_bytes=4 * MB)
    assert report["warnings"] == []
    assert report["mime"] == "image/jpeg"
    assert report["bytes"] > 0
