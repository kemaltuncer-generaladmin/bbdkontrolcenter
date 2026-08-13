"""Sır maskeleme — belirteç hiçbir metne sızmamalı."""

from __future__ import annotations

from store_api_backend.errors import StoreApiError, mask_mapping, mask_text


def test_bearer_belirteci_metinden_silinir() -> None:
    metin = "GET /api/admin/orders başlık: Authorization: Bearer 12|abcDEF123.xyz"
    sonuc = mask_text(metin)
    assert "abcDEF123" not in sonuc
    assert "12|" not in sonuc
    # Başlık adı kalır, değeri gitmiştir.
    assert sonuc.startswith("GET /api/admin/orders başlık: Authorization:")
    assert "***" in sonuc


def test_json_icindeki_belirtec_ve_parola_maskelenir() -> None:
    metin = '{"adminToken": "12|gizli", "netgsm_password": "1234", "name": "Kalem"}'
    sonuc = mask_text(metin)
    assert "gizli" not in sonuc
    assert "1234" not in sonuc
    # Alan ADI kalır: hangi alan yüzünden patladığı teşhis için gerekli.
    assert "adminToken" in sonuc
    assert "Kalem" in sonuc


def test_kargo_alanlari_yanlislikla_maskelenmez() -> None:
    # "pin" gibi kısa parçalar sözlükte olsaydı "shipping" de maskelenirdi.
    metin = '{"shipping": "Yurtiçi", "shippingAmount": 4990}'
    assert mask_text(metin) == metin


def test_sozluk_maskelemesi_ic_ice_iner() -> None:
    veri = {"order": {"id": 7, "payment": {"apiKey": "s3cret", "bank": "KT"}},
            "tokens": ["a", "b"]}
    sonuc = mask_mapping(veri)
    assert sonuc["order"]["payment"]["apiKey"] == "***"
    assert sonuc["order"]["payment"]["bank"] == "KT"
    assert sonuc["tokens"] == "***"


def test_hata_kendi_metnini_maskeler() -> None:
    hata = StoreApiError('Reddedildi: {"token": "12|acikbelirtec"}', status=422, code="validation")
    assert "acikbelirtec" not in str(hata)
    assert hata.status == 422
    assert hata.code == "validation"
