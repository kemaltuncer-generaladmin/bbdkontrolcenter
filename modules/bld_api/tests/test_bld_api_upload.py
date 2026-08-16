"""Görsel yükleme: base64 gövdesi, boyut freni, içerikten tür okuma.

Yüklemenin yanlış gidebileceği her şey istek ÇIKMADAN yakalanmalı. Sebep iki
katmanlı: (1) sınırı aşan bir dosyayı gönderip sunucudan ret beklemek hız
kovasından pay yer, (2) multipart yerine base64 kullanmamızın sebebi imzanın
ham gövdeyi hashlemesi — gövdeyi bir yerde yeniden kodlamak arızayı "kimlik
doğrulama hatası" kılığına sokar ve sahada teşhis edilemez.

Hiçbir test ağa çıkmaz.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest
from bld_api_backend.errors import BldApiError
from bld_api_backend.upload import (
    PRODUCT_IMAGE_MAX_BYTES,
    describe,
    extension,
    prepare_upload,
    safe_filename,
    sniff_mime,
)
from bld_api_fakes import gateway

GEREKCE = "Ürün fotoğrafı yenilendi, panelden yüklendi"
AKTOR = "Ayşe Yılmaz"

PNG = b"\x89PNG\r\n\x1a\n" + b"govde-baytlari"
JPEG = b"\xff\xd8\xff\xe0" + b"govde"
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"govde"
PDF = b"%PDF-1.7 sahte"


# ------------------------------------------------------- tür sihirli bayttan

def test_sihirli_bayt_uc_turu_tanir() -> None:
    assert sniff_mime(PNG) == "image/png"
    assert sniff_mime(JPEG) == "image/jpeg"
    assert sniff_mime(WEBP) == "image/webp"
    assert sniff_mime(PDF) == ""


def test_uzanti_yalan_soylerse_icerik_kazanir() -> None:
    """`.jpg` adlı bir PHP dosyasını yüklemenin en bilinen yolu uzantıya
    güvenmektir; sunucu da türü içerikten okuyor."""
    with pytest.raises(BldApiError) as hata:
        prepare_upload(PDF, filename="masum.jpg", max_bytes=PRODUCT_IMAGE_MAX_BYTES)

    assert hata.value.code == "payload"
    # Metin, "uzantıyı düzeltirsem geçer" sanan kullanıcıyı açıkça uyarmalı.
    assert "İÇERİKTEN" in hata.value.message


def test_dogru_uzanti_ada_eklenir() -> None:
    part = prepare_upload(PNG, filename="urun", max_bytes=PRODUCT_IMAGE_MAX_BYTES)
    assert part["filename"] == "urun.png"
    assert part["mime"] == "image/png"


# --------------------------------------------------------------- base64

def test_data_uri_oneki_ve_satir_sonlari_temizlenir() -> None:
    metin = "data:image/png;base64," + base64.b64encode(PNG).decode()
    part = prepare_upload(metin[:40] + "\n" + metin[40:], filename="urun.png",
                          max_bytes=PRODUCT_IMAGE_MAX_BYTES)

    assert part["content"] == PNG
    # Gövdeye giden metin BURADA yeniden üretilir: gövde bayt bayt imzalandığı
    # için "ne gönderdiğimizi" tek yerden bilmek gerekir.
    assert base64.b64decode(part["content_base64"]) == PNG


def test_bozuk_base64_anlasilir_hataya_cevrilir() -> None:
    with pytest.raises(BldApiError) as hata:
        prepare_upload("bu-gecerli-base64-degil!!", filename="urun.png",
                       max_bytes=PRODUCT_IMAGE_MAX_BYTES)

    assert hata.value.code == "payload"
    assert "base64" in hata.value.message


def test_bos_icerik_reddedilir() -> None:
    with pytest.raises(BldApiError):
        prepare_upload("", filename="urun.png", max_bytes=PRODUCT_IMAGE_MAX_BYTES)


# ----------------------------------------------------------------- boyut

def test_cozulmus_boyut_sinirini_asan_dosya_gonderilmez() -> None:
    buyuk = PNG + b"0" * 4096
    with pytest.raises(BldApiError) as hata:
        prepare_upload(buyuk, filename="urun.png", max_bytes=1024)

    assert hata.value.code == "payload"
    assert "MB" in hata.value.message


def test_kaba_sinir_metni_bellege_acmadan_keser() -> None:
    """Base64 uzunluğu tek başına sınırı aşıyorsa dosya HİÇ çözülmez."""
    metin = "A" * 40_000
    with pytest.raises(BldApiError):
        prepare_upload(metin, filename="urun.png", max_bytes=1024)


# ------------------------------------------------------------- dosya adı

def test_dosya_adindan_yol_ve_tirnak_temizlenir() -> None:
    assert safe_filename('../../etc/pa"sswd.png') == "pa_sswd.png"
    assert extension("URUN.JPG") == "jpg"


def test_kunye_icerik_tasimaz() -> None:
    part = prepare_upload(PNG, filename="urun.png", max_bytes=PRODUCT_IMAGE_MAX_BYTES)
    kunye = describe(part)

    assert set(kunye) == {"filename", "mime", "bytes"}
    assert "content_base64" not in kunye


# ------------------------------------------------------------- uçtan uca

async def test_gorsel_json_govdesinde_base64_olarak_gider() -> None:
    """MULTIPART DEĞİL: sınır dizesi taşıyan bir gövdeyi yeniden kodlayan
    herhangi bir vekil imzayı bozar ve hata 401 olarak görünür."""
    istekler: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        istekler.append(request)
        return httpx.Response(200, json={"ok": True, "data": {"menu_id": 27}})

    api, _, _, _ = gateway(handler)
    await api.set_product_image(27, content=PNG, filename="urun.png", reason=GEREKCE,
                                actor=AKTOR)

    assert istekler[0].headers["Content-Type"] == "application/json"
    govde = json.loads(istekler[0].content)
    assert base64.b64decode(govde["content_base64"]) == PNG
    assert govde["filename"] == "urun.png"


async def test_denetim_izine_base64_icerik_yazilmaz() -> None:
    """Sözleşme §8.2: görselde yalnız `bytes` ve `mime` yazılır. Aynı kural
    yerel iz için de geçerli — 4 KB'lik base64 parçaları izi okunamaz kılardı."""
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    api, depo, _, _ = gateway(handler)
    await api.set_product_image(27, content=PNG, filename="urun.png", reason=GEREKCE,
                                actor=AKTOR)

    satir = json.loads(depo.audit[0]["body"])
    assert satir["bytes"] == len(PNG)
    assert satir["mime"] == "image/png"
    assert "content_base64" not in satir


async def test_sinir_asiminda_istek_hic_gonderilmez() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    # Ayar sınırı 1 MB; ucun kendi sınırı 5 MB. İkisinin KÜÇÜĞÜ uygulanır.
    api, _, _, _ = gateway(handler, max_upload_mb=1)
    with pytest.raises(BldApiError) as hata:
        await api.set_product_image(27, content=PNG + b"0" * (2 * 1024 * 1024),
                                    filename="urun.png", reason=GEREKCE, actor=AKTOR)

    assert hata.value.code == "payload"


async def test_yukleme_kunyesi_yanita_eklenir() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "dry_run": True, "would": {}})

    api, _, _, _ = gateway(handler, dry_run_default=True)
    sonuc = await api.set_product_image(27, content=JPEG, filename="urun.jpg",
                                        reason=GEREKCE, actor=AKTOR)

    assert sonuc["upload"] == {"filename": "urun.jpg", "mime": "image/jpeg",
                               "bytes": len(JPEG)}
