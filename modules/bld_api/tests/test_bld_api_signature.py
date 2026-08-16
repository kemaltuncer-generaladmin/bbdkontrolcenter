"""Kontrol imzası — sözleşme §1.

İmza yanlış üretilirse sunucu "İmza doğrulanamadı" der ve HANGİ parçanın
bozuk olduğunu SÖYLEMEZ (söyleyemez de: doğrulama tek bir `hash_equals`
karşılaştırmasıdır). Bu yüzden kanonik biçim burada sabit vektörle çakılır —
bir gün ayraç, sıra ya da kodlama değişirse hata sahada değil burada çıkar.

Hiçbir test ağa çıkmaz: `httpx.MockTransport`.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

import httpx
import pytest
from bld_api_backend.client import (
    EMPTY_BODY_SHA,
    SECRET_KEY,
    BldApi,
    canonical_payload,
    sign,
)
from bld_api_backend.errors import BldApiError
from bld_api_fakes import TEST_SECRET, FakeLog, FakeSecrets, FakeStore, gateway

#: Sabit vektör. Değerler elle hesaplandı; kodun kendisinden ÜRETİLMEDİ.
VEKTOR_ZAMAN = 1755100000
VEKTOR_NONCE = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
VEKTOR_GOVDE = '{"reason":"Kasa mutfaktan çıkarıldı","actor":"Ayşe Yılmaz","dry_run":false}'


def json_response(payload: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


# ---------------------------------------------------------- kanonik biçim

def test_kanonik_yuk_bes_satirdir_ve_govde_ozetiyle_biter() -> None:
    kanonik = canonical_payload("post", "/api/control/kds/devices", VEKTOR_ZAMAN,
                                VEKTOR_NONCE, b"")
    satirlar = kanonik.split("\n")

    assert satirlar == [
        "POST",                                  # metot BÜYÜK harf
        "/api/control/kds/devices",              # yol, sorgu dizesi HARİÇ
        "1755100000",
        VEKTOR_NONCE,
        EMPTY_BODY_SHA,
    ]
    # Sözleşme §1 bu sabiti açıkça yazıyor: gövdesiz istekte sha256("").
    assert EMPTY_BODY_SHA == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_bilinen_sir_ve_govde_icin_beklenen_hex_uretilir() -> None:
    kanonik = canonical_payload("POST", "/api/control/kds/devices/3/revoke", VEKTOR_ZAMAN,
                                VEKTOR_NONCE, VEKTOR_GOVDE.encode("utf-8"))

    assert sign(TEST_SECRET, kanonik) == (
        "sha256=99464d636dfd64b14e09ed4245c01daded281d8975cdc2ed88690211575247c0"
    )


def test_govdesiz_istegin_bilinen_hexi() -> None:
    kanonik = canonical_payload("GET", "/api/control/kds/devices", VEKTOR_ZAMAN,
                                VEKTOR_NONCE, b"")

    assert sign(TEST_SECRET, kanonik) == (
        "sha256=e3a4f597d301393fe2fd836823c0c0156eb866a4157e8b9ae931cc302261d817"
    )


# ------------------------------------------------------- istek üzerindeki

async def test_istek_uc_imza_basligini_tasir_ve_imza_gonderilen_baytla_uyusur() -> None:
    istekler: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        istekler.append(request)
        return json_response({"ok": True})

    api, _, _, _ = gateway(handler)
    await api.revoke_device(3, reason="Kasa mutfaktan çıkarıldı", actor="Ayşe Yılmaz")

    istek = istekler[0]
    zaman = istek.headers["X-Control-Timestamp"]
    nonce = istek.headers["X-Control-Nonce"]

    assert zaman.isdigit()
    assert 16 <= len(nonce) <= 128          # sunucunun kabul ettiği aralık
    assert istek.headers["X-Control-Signature"].startswith("sha256=")
    assert len(istek.headers["X-Control-Signature"]) == len("sha256=") + 64

    # İmza, GERÇEKTEN GÖNDERİLEN baytların üzerinden doğrulanabilmeli.
    kanonik = "\n".join([
        "POST", "/api/control/kds/devices/3/revoke", zaman, nonce,
        hashlib.sha256(istek.content).hexdigest(),
    ])
    beklenen = "sha256=" + hmac.new(TEST_SECRET.encode("utf-8"), kanonik.encode("utf-8"),
                                    hashlib.sha256).hexdigest()
    assert istek.headers["X-Control-Signature"] == beklenen


async def test_gonderilen_govde_imzalanan_baytla_birebir_aynidir() -> None:
    """httpx gövdeyi YENİDEN SERİLEŞTİRMEZ — `content=` ham bayt gönderir.

    Ayraç boşluğu ya da `\\uXXXX` kaçışı gibi en küçük fark, sunucuda
    `hash('sha256', $request->getContent())` başka bir özet üretir ve hata
    "gövde bozuk" değil "imza doğrulanamadı" olarak görünür.
    """
    istekler: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        istekler.append(request)
        return json_response({"ok": True})

    api, _, _, _ = gateway(handler)
    await api.revoke_device(3, reason="Kasa mutfaktan çıkarıldı", actor="Ayşe Yılmaz")

    ham = istekler[0].content
    # Türkçe karakterler UTF-8 gider, kaçış dizisiyle değil.
    assert "çıkarıldı".encode() in ham
    assert b"\\u" not in ham
    # Ayraçlarda boşluk yok: biçim `_encode` içinde sabitlendi.
    assert b'", "' not in ham
    assert istekler[0].headers["Content-Type"] == "application/json"


async def test_sorgu_dizesi_imzaya_girmez_ama_istege_girer() -> None:
    istekler: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        istekler.append(request)
        return json_response({"data": []})

    api, _, _, _ = gateway(handler)
    await api.print_jobs(device_id=3, limit=10)

    istek = istekler[0]
    assert istek.url.params["device_id"] == "3"
    assert istek.url.params["limit"] == "10"

    # Sözleşme §1: yol sorgu dizesi HARİÇ imzalanır.
    kanonik = "\n".join([
        "GET", "/api/control/kds/print-jobs",
        istek.headers["X-Control-Timestamp"], istek.headers["X-Control-Nonce"],
        EMPTY_BODY_SHA,
    ])
    assert istek.headers["X-Control-Signature"] == sign(TEST_SECRET, kanonik)


async def test_her_deneme_yeni_nonce_ve_zamanla_imzalanir() -> None:
    """Nonce sunucuda 600 sn hatırlanıyor; aynısını yinelemek 401 üretirdi."""
    istekler: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        istekler.append(request)
        if len(istekler) == 1:
            return httpx.Response(429, headers={"Retry-After": "1"}, text="Too Many Requests")
        return json_response({"data": []})

    api, _, _, _ = gateway(handler)
    await api.devices()

    assert len(istekler) == 2
    nonce_bir = istekler[0].headers["X-Control-Nonce"]
    nonce_iki = istekler[1].headers["X-Control-Nonce"]
    assert nonce_bir != nonce_iki
    assert istekler[0].headers["X-Control-Signature"] != istekler[1].headers["X-Control-Signature"]


# ------------------------------------------------------------ yapılandırma

async def test_sir_yoksa_istek_hic_gonderilmez() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    depo, gunluk = FakeStore(), FakeLog()
    kasa = FakeSecrets({})
    api = BldApi(base_url="https://ornek.test", secrets=kasa, log=gunluk, store=depo,
                 read_only=False, transport=httpx.MockTransport(handler))

    with pytest.raises(BldApiError) as hata:
        await api.devices()

    assert hata.value.code == "config_missing"
    assert SECRET_KEY in hata.value.message
    assert kasa.reads == [SECRET_KEY]


async def test_taban_adres_yoksa_istek_hic_gonderilmez() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    depo, gunluk = FakeStore(), FakeLog()
    api = BldApi(base_url="", secrets=FakeSecrets({SECRET_KEY: TEST_SECRET}), log=gunluk,
                 store=depo, read_only=False, transport=httpx.MockTransport(handler))

    with pytest.raises(BldApiError) as hata:
        await api.devices()

    assert hata.value.code == "config_missing"
    assert "base_url" in hata.value.message
