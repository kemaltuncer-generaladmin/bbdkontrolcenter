"""Dosya yükleme: multipart gövde kurulumu, boyut freni, acil fren.

Hiçbir test ağa çıkmaz — `httpx.MockTransport` ile sahte sunucu kullanılır.
Buradaki asıl soru şu: yüklemede de BEŞ POLİTİKA işliyor mu, yoksa multipart
yolu politikaların yanından mı dolanıyor?
"""

from __future__ import annotations

import base64
from typing import Any

import httpx
import pytest
from store_api_backend.client import StoreApi
from store_api_backend.errors import StoreApiError
from store_api_backend.upload import (
    PRODUCT_IMAGE_MAX_BYTES,
    decode_content,
    max_upload_bytes,
    prepare_upload,
    safe_filename,
)
from store_api_fakes import FakeLog, FakeSecrets, FakeStore

TOKEN = "12|cokGizliBelirtec"
PNG = b"\x89PNG\r\n\x1a\n" + b"veri" * 8
GEREKCE = "Kapak görseli güncellendi, yayıncı yeni kapak gönderdi"


def gateway(handler: Any, **options: Any) -> tuple[StoreApi, FakeStore, FakeLog]:
    depo, gunluk = FakeStore(), FakeLog()
    ayar: dict[str, Any] = {"read_only": False, "dry_run_default": False}
    ayar.update(options)
    api = StoreApi(base_url="https://ornek.test", secrets=FakeSecrets({"store.admin_token": TOKEN}),
                   log=gunluk, store=depo, transport=httpx.MockTransport(handler), **ayar)
    api._sleep = _uyuma
    return api, depo, gunluk


async def _uyuma(_seconds: float) -> None:
    return None


# ---------------------------------------------------- saf hazırlık mantığı

def test_base64_metin_bayta_cozulur_data_uri_onegi_atilir() -> None:
    metin = "data:image/png;base64," + base64.b64encode(PNG).decode()
    assert decode_content(metin) == PNG
    assert decode_content(PNG) == PNG


def test_bozuk_base64_anlasilir_hataya_cevrilir() -> None:
    with pytest.raises(StoreApiError) as hata:
        decode_content("bu base64 değil!!!")
    assert hata.value.code == "payload"
    # Kullanıcı "Invalid base64-encoded string" görmemeli.
    assert "base64 bozuk" in hata.value.message


def test_dosya_adindaki_yol_ve_tirnak_temizlenir() -> None:
    # Ad doğrudan Content-Disposition başlığına giriyor.
    assert safe_filename('../../etc/"kapak".png') == "kapak_.png"
    assert safe_filename("") == "yukleme"


def test_uzantisiz_ad_mime_biliniyorsa_tamamlanir() -> None:
    parca = prepare_upload(PNG, filename="kapak", mime="image/png", max_bytes=1024)
    # Sunucu dosyayı uzantıyla kaydediyor; uzantısız ad 'jpg' varsayımına düşerdi.
    assert parca["filename"] == "kapak.png"


def test_desteklenmeyen_tur_reddedilir() -> None:
    with pytest.raises(StoreApiError) as hata:
        prepare_upload(b"%PDF-1.4", filename="katalog.pdf", mime="application/pdf",
                       max_bytes=1024)
    assert hata.value.code == "payload"
    assert "webp" in hata.value.message


def test_uzanti_uygunsa_mime_bos_olsa_da_kabul_edilir() -> None:
    # Sunucunun kuralı "mime YA DA uzantı"; daha katı davranıp doğru dosyayı
    # reddetmek olmaz.
    parca = prepare_upload(PNG, filename="kapak.webp", mime="", max_bytes=1024)
    assert parca["mime"] == "image/webp"


def test_ayar_sinirinin_gecersiz_degeri_varsayilana_duser() -> None:
    assert max_upload_bytes(0) == 24 * 1024 * 1024
    assert max_upload_bytes("abc") == 24 * 1024 * 1024
    assert max_upload_bytes(2) == 2 * 1024 * 1024


def test_base64_metin_cozulmeden_once_kaba_olcuyle_reddedilir() -> None:
    # 200 MB'lık metni belleğe açmadan durdurulmalı.
    with pytest.raises(StoreApiError) as hata:
        prepare_upload("A" * 5_000, filename="kapak.png", mime="image/png", max_bytes=1_000)
    assert "büyük" in hata.value.message


# ----------------------------------------------------------- multipart gövde

async def test_urun_gorseli_multipart_olarak_image_alaninda_gider() -> None:
    istekler: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        istekler.append(request)
        return httpx.Response(201, json={"id": 47, "productId": 12, "position": 3})

    api, depo, _ = gateway(handler)
    sonuc = await api.upload_product_image(12, content=base64.b64encode(PNG).decode(),
                                           filename="kapak.png", mime="image/png", position=3,
                                           reason=GEREKCE, actor="Ayşe Yılmaz")

    istek = istekler[0]
    govde = istek.content
    assert istek.url.path == "/api/admin/catalog/products/12/images"
    assert istek.headers["Content-Type"].startswith("multipart/form-data; boundary=")
    # Alan adı vendor kaynağından: 'image' — 'images[]' ya da 'file' DEĞİL.
    assert b'name="image"' in govde
    assert b'filename="kapak.png"' in govde
    assert PNG in govde
    assert b'name="position"' in govde
    assert sonuc["id"] == 47
    # Gerekçe başlığı ve denetim izi yüklemede de var.
    assert istek.headers["X-Bbd-Request-Id"] == depo.audit[0]["request_id"]
    assert depo.audit[0]["result"] == "ok"


async def test_denetim_izine_ham_bayt_degil_dosya_ozeti_yazilir() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"id": 1})

    api, depo, _ = gateway(handler)
    await api.upload_product_image(12, content=PNG, filename="kapak.png", mime="image/png",
                                   reason=GEREKCE)

    govde = depo.audit[0]["body"]
    # Ham bayt JSON'a çevrilemez; özet yazılır.
    assert '"kapak.png"' in govde
    assert '"bytes": 40' in govde
    assert "PNG" not in govde


async def test_yuklemede_kuru_prova_istek_gondermez() -> None:
    gonderilen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        gonderilen.append(request)
        return httpx.Response(201, json={})

    api, depo, _ = gateway(handler, dry_run_default=True)
    sonuc = await api.upload_product_image(12, content=PNG, filename="kapak.png",
                                           mime="image/png", reason=GEREKCE)

    assert gonderilen == []
    assert sonuc["sent"] is False
    assert sonuc["body"]["filename"] == "kapak.png"
    assert depo.audit[0]["result"] == "dry_run"


# ------------------------------------------------------------ boyut freni

async def test_ayar_sinirini_asan_dosya_icin_istek_hic_gitmez() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    api, depo, _ = gateway(handler, max_upload_mb=1)
    with pytest.raises(StoreApiError) as hata:
        await api.upload_product_image(12, content=b"x" * (2 * 1024 * 1024),
                                       filename="kapak.png", mime="image/png", reason=GEREKCE)

    assert hata.value.code == "payload"
    assert "MB" in hata.value.message
    # Denetim satırı da açılmaz: istek hiç kurulmadı.
    assert depo.audit == []


async def test_ucun_dort_megabaytlik_sinirini_asan_dosya_gonderilmez() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    # Ayar 24 MB'a izin veriyor ama ürün görseli ucu 4 MB'ın üstünü 422 ile
    # reddediyor; sunucudan ret beklemek hız kovasından pay yer.
    api, _, _ = gateway(handler, max_upload_mb=24)
    with pytest.raises(StoreApiError) as hata:
        await api.upload_product_image(12, content=b"x" * (PRODUCT_IMAGE_MAX_BYTES + 1),
                                       filename="kapak.png", mime="image/png", reason=GEREKCE)
    assert hata.value.code == "payload"


# --------------------------------------------- acil fren ve gerekçe freni

async def test_acil_fren_acikken_yukleme_de_gonderilmez() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    api, depo, _ = gateway(handler, read_only=True)
    with pytest.raises(StoreApiError) as hata:
        await api.upload_product_image(12, content=PNG, filename="kapak.png",
                                       mime="image/png", reason=GEREKCE)

    assert hata.value.code == "read_only"
    assert depo.audit[0]["result"] == "blocked"
    assert depo.audit[0]["path"].endswith("/products/12/images")


async def test_gerekcesiz_yukleme_reddedilir() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    api, _, _ = gateway(handler)
    with pytest.raises(StoreApiError) as hata:
        await api.upload_product_image(12, content=PNG, filename="kapak.png",
                                       mime="image/png", reason="kısa")
    assert hata.value.code == "reason_required"


# --------------------------------------------------------- yayında olmayan uç

async def test_ana_ekran_gorseli_ucu_yoksa_anlasilir_hata_doner() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="<!DOCTYPE html>404 Not Found")

    api, _, _ = gateway(handler)
    with pytest.raises(StoreApiError) as hata:
        await api.upload_media(content=PNG, filename="afis.png", mime="image/png",
                               slot="hero", reason="Yeni dönem afişi yüklendi")

    # Ekran çökmez, durumu anlatır (K7).
    assert hata.value.code == "bbd_endpoint_missing"
    assert "henüz yayında değil" in hata.value.message


async def test_bbd_ucunda_yuklemede_gerekce_ve_kuru_prova_govdeye_konur() -> None:
    istekler: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        istekler.append(request)
        return httpx.Response(201, json={"ok": True})

    api, _, _ = gateway(handler, dry_run_default=True)
    await api.upload_media(content=PNG, filename="afis.png", mime="image/png",
                           reason="Yeni dönem afişi yüklendi")

    # BBD uçları `dryRun` biliyor: istek GERÇEKTEN gider, bayrak gövdededir.
    govde = istekler[0].content
    assert b'name="dryRun"' in govde
    assert b'name="reason"' in govde


# ------------------------------------------------------------------- silme

async def test_gorsel_silme_ucu_ve_eski_ad_ayni_yere_gider() -> None:
    yollar: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        yollar.append((request.method, request.url.path))
        return httpx.Response(200, json={"success": True})

    api, _, _ = gateway(handler)
    await api.delete_product_image(12, 47, reason="Yanlış kapak yüklenmişti, kaldırıldı")
    await api.remove_product_image(12, 47, reason="Yanlış kapak yüklenmişti, kaldırıldı")

    assert yollar == [("DELETE", "/api/admin/catalog/products/12/images/47")] * 2


async def test_eski_sozluk_imzasi_gercek_yukleme_yoluna_verilir() -> None:
    istekler: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        istekler.append(request)
        return httpx.Response(201, json={"id": 9})

    api, _, _ = gateway(handler)
    await api.add_product_image(12, payload={"content": PNG, "filename": "kapak.png",
                                             "mime": "image/png"}, reason=GEREKCE)

    # Eski gövde JSON olarak gitseydi sunucu "görsel zorunlu" diyen 422 verirdi.
    assert istekler[0].headers["Content-Type"].startswith("multipart/form-data")
    assert b'name="image"' in istekler[0].content


async def test_dosyasiz_eski_cagri_anlasilir_hata_verir() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    api, _, _ = gateway(handler)
    with pytest.raises(StoreApiError) as hata:
        await api.add_product_image(12, payload={"position": 1}, reason=GEREKCE)
    assert hata.value.code == "payload"
    assert "upload_product_image" in hata.value.message


# ------------------------------------------------------------ görsel sırası

async def test_gorsel_sirasi_duz_kimlik_listesi_degil_satir_sozlugu_gonderir() -> None:
    # Vendor işlemcisi her satırı `is_array($row) && isset($row['id'])` ile
    # denetliyor ve konumu `$row['position']` alanından okuyor
    # (AdminCatalogProductImageProcessor). Düz `[47, 48]` gönderilirse uç HER
    # ZAMAN 422 döner — istek biçimsel olarak geçerli göründüğü için bu hata
    # ancak gerçek çağrıda ortaya çıkar, testsiz fark edilmez.
    import json

    istekler: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        istekler.append(request)
        return httpx.Response(200, json={"id": 12, "success": True})

    api, _, _ = gateway(handler)
    await api.reorder_product_images(12, order=[47, 48, 49], reason=GEREKCE, actor="Ayşe")

    govde = json.loads(istekler[0].content)
    assert govde == {"order": [
        {"id": 47, "position": 1},
        {"id": 48, "position": 2},
        {"id": 49, "position": 3},
    ]}
    # Listenin ilk elemanı kapaktır: konum 1'den başlar, 0'dan değil.
    assert govde["order"][0]["position"] == 1
