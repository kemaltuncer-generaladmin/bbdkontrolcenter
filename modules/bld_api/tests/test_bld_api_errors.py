"""Hata eşlemesi ve sır maskeleme.

İmza sırrı hiçbir metne sızmamalı: ne log'a, ne hata mesajına, ne ekrana.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from bld_api_backend.client import SECRET_KEY
from bld_api_backend.errors import BldApiError, mask_mapping, mask_text
from bld_api_fakes import TEST_SECRET, gateway


def json_response(payload: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


def control_error(code: str, message: str, status: int) -> httpx.Response:
    """Sözleşme §1'deki hata zarfı."""
    return json_response({"error": {"code": code, "message": message, "details": {}}},
                         status=status)


# ------------------------------------------------------------- maskeleme

def test_sir_alan_adiyla_birlikte_gecerse_maskelenir() -> None:
    metin = f'{{"control_secret": "{TEST_SECRET}", "name": "Mutfak"}}'
    sonuc = mask_text(metin)

    assert TEST_SECRET not in sonuc
    # Alan ADI kalır: hangi alan yüzünden patladığı teşhis için gerekli.
    assert "control_secret" in sonuc
    assert "Mutfak" in sonuc


def test_sozluk_maskelemesi_ic_ice_iner() -> None:
    veri = {"device": {"id": 3, "auth": {"control_secret": "gizli", "name": "KDS"}}}
    sonuc = mask_mapping(veri)

    assert sonuc["device"]["auth"]["control_secret"] == "***"
    assert sonuc["device"]["auth"]["name"] == "KDS"


def test_hata_kendi_metnini_maskeler() -> None:
    hata = BldApiError('Reddedildi: {"secret": "acikSir123"}', status=422, code="validation")

    assert "acikSir123" not in str(hata)
    assert hata.status == 422
    assert hata.code == "validation"


async def test_ciplak_sir_yankilanirsa_da_hata_metnine_dusmez() -> None:
    """Sır rastgele bir dizedir; sunucu onu alan adı olmadan yankılarsa ad
    tabanlı maskeleme yakalayamaz. İstemci bilinen değeri ayrıca siler."""
    def handler(_request: httpx.Request) -> httpx.Response:
        return control_error("VALIDATION", f"Beklenmeyen değer: {TEST_SECRET}", 422)

    api, _, _, _ = gateway(handler)
    with pytest.raises(BldApiError) as hata:
        await api.revoke_device(3, reason="Kasa mutfaktan çıkarıldı", actor="Ayşe Yılmaz")

    assert hata.value.code == "validation"
    assert TEST_SECRET not in hata.value.message
    assert "***" in hata.value.message


# ---------------------------------------------------------- hata eşlemesi

async def test_dortyuzbir_sirri_dusurur_ve_uc_sebebi_de_soyler() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return control_error("UNAUTHENTICATED", "İstek zaman penceresinin dışında.", 401)

    api, _, _, kasa = gateway(handler)
    with pytest.raises(BldApiError) as hata:
        await api.devices()

    assert hata.value.code == "unauthorized"
    assert "zaman penceresinin dışında" in hata.value.message
    assert "saat" in hata.value.message          # sahada en sık sebep
    # Yönetici local.yaml'ı düzeltince yeniden başlatma gerekmesin.
    assert api._secret is None
    assert kasa.reads == [SECRET_KEY]


async def test_zarfli_dortyuzdort_kayit_yok_demektir() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return control_error("NOT_FOUND", "Cihaz bulunamadı.", 404)

    api, _, _, _ = gateway(handler)
    with pytest.raises(BldApiError) as hata:
        await api.order(99)

    assert hata.value.code == "not_found"


async def test_zarfsiz_dortyuzdort_uc_yayinda_degil_demektir() -> None:
    """Ayrım kozmetik değil: "kayıt yok" ekranı çalışır tutar, "uç yayında
    değil" ekranı bekleyen bir dağıtıma yönlendirir."""
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="<!DOCTYPE html>404 Not Found")

    api, _, _, _ = gateway(handler)
    with pytest.raises(BldApiError) as hata:
        await api.devices()

    assert hata.value.code == "control_endpoint_missing"
    assert "yayında değil" in hata.value.message


async def test_dortyuzbes_de_uc_yayinda_degil_demektir() -> None:
    """405, "uç yok"un sessiz hâlidir: Laravel yolu tanıyıp metodu tanımazsa
    404 değil 405 döner."""
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(405, text="Method Not Allowed")

    api, _, _, _ = gateway(handler)
    with pytest.raises(BldApiError) as hata:
        await api.revoke_device(3, reason="Kasa mutfaktan çıkarıldı", actor="Ayşe Yılmaz")

    assert hata.value.code == "control_endpoint_missing"


async def test_dogrulama_hatasinin_mesaji_tasinir_ve_denetime_islenir() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return control_error("VALIDATION", "reason alanı en az 10 karakter olmalı.", 422)

    api, depo, _, _ = gateway(handler)
    with pytest.raises(BldApiError) as hata:
        await api.set_order_status(12, status="hazir", reason="Mutfak hazırladı bildirdi",
                                   actor="Ayşe Yılmaz")

    assert hata.value.code == "validation"
    assert "en az 10 karakter" in hata.value.message
    assert depo.audit[0]["result"] == "error:validation"
    assert depo.audit[0]["status"] == 422


async def test_sunucu_hatasi_okumada_yinelenir_sonra_server_koduyla_doner() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Server Error")

    api, _, gunluk, _ = gateway(handler)
    with pytest.raises(BldApiError) as hata:
        await api.overview()

    assert hata.value.code == "server"
    assert "yineleniyor" in gunluk.text()
