"""notify/SMS katmanı testleri.

Ağa çıkılmaz: sağlayıcı istemcisi taklit edilir. Amaç, sarmalayıcının SDK'nın
tuzaklarını gerçekten kapattığını doğrulamak (docs/netgsm-integration.md).
"""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from km_platform.notify import (
    SmsInvalidRecipient,
    SmsMessage,
    normalize_msisdn,
    plan_text,
)
from km_platform.notify.errors import (
    SmsAuthError,
    SmsRateLimited,
    SmsRejected,
    SmsTransportError,
)
from km_platform.notify.providers.netgsm import NetgsmConfig, NetgsmSmsProvider, codes

# --------------------------------------------------------------- numara


@pytest.mark.parametrize(
    "raw",
    [
        "5321234567",
        "05321234567",
        "0532 123 45 67",
        "+90 532 123 45 67",
        "90-532-123-45-67",
        "(0532) 123 45 67",
    ],
)
def test_numara_normallestirme(raw):
    assert normalize_msisdn(raw) == "5321234567"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "123",
        "4321234567",        # 5 ile başlamıyor
        "532123456",         # eksik hane
        "53212345678",       # fazla hane
        "+1 555 123 4567",   # yabancı numara
    ],
)
def test_gecersiz_numara_reddedilir(raw):
    with pytest.raises(ValueError):
        normalize_msisdn(raw)


# -------------------------------------------------------------- kodlama


def test_duz_metin_gsm7_tek_parca():
    plan = plan_text("Sunucu ayakta.")
    assert plan.encoding is None      # Türkçe karakter yok → parametre gönderilmez
    assert plan.parts == 1
    assert plan.unicode is False


def test_turkce_karakter_tr_kodlamasi_tetikler():
    plan = plan_text("Yedekleme başarısız oldu")   # 'ş' Türkçe kaydırma tablosunda
    assert plan.encoding == "tr"
    assert plan.unicode is False


def test_turkce_karakter_iki_septet_sayilir():
    assert plan_text("a" * 10).units == 10
    assert plan_text("ş" * 10).units == 20


def test_uzun_metin_cok_parcaya_bolunur():
    assert plan_text("a" * 160).parts == 1
    assert plan_text("a" * 161).parts == 2      # 153'lük parçalara geçer
    assert plan_text("a" * 306).parts == 2
    assert plan_text("a" * 307).parts == 3


def test_emoji_ucs2ye_duser():
    plan = plan_text("Uyarı 🔥")
    assert plan.unicode is True
    assert plan.parts == 1


def test_ucs2_parca_siniri_70():
    assert plan_text("🔥" + "a" * 69).parts == 1
    assert plan_text("🔥" + "a" * 70).parts == 2


# ------------------------------------------------------- tarih biçimleri


def test_saat_dilimli_tarih_turkiye_saatine_cevrilir():
    """UTC verilen bir zamanlama yerel saate çevrilmezse gönderim kayar."""
    from datetime import timedelta, timezone

    utc_noon = datetime(2023, 12, 1, 12, 0, tzinfo=timezone.utc)
    assert codes.fmt_send(utc_noon) == "011220231500"      # UTC+3

    already_local = datetime(2023, 12, 1, 15, 0, tzinfo=timezone(timedelta(hours=3)))
    assert codes.fmt_send(already_local) == "011220231500"


def test_uc_ayri_tarih_bicimi():
    dt = datetime(2023, 12, 1, 14, 30, 0)
    assert codes.fmt_send(dt) == "011220231430"
    assert codes.fmt_report(dt) == "01.12.2023 14:30:00"
    assert codes.fmt_inbox(dt) == "01122023143000"


# ------------------------------------------------------------ kod denetimi


def test_basari_kodu_gecer():
    codes.raise_for_code("00", context="test")
    codes.raise_for_code(None, context="test")


@pytest.mark.parametrize(
    "code,expected",
    [
        ("30", SmsAuthError),
        ("40", SmsRejected),
        ("20", SmsRejected),
        ("70", SmsRejected),
        ("80", SmsRateLimited),
        ("85", SmsRateLimited),
    ],
)
def test_hata_kodu_tipli_hataya_cevrilir(code, expected):
    with pytest.raises(expected) as exc:
        codes.raise_for_code(code, context="test")
    assert exc.value.provider_code == code


# ------------------------------------------------------------- sağlayıcı


def _provider(**overrides):
    cfg = NetgsmConfig(
        username="u", password="p", header="KURUM", **overrides
    )
    return NetgsmSmsProvider(cfg)


async def test_kuru_calisma_gondermez():
    p = _provider(dry_run=True)
    p._client = MagicMock()   # çağrılırsa test düşer

    result = await p.send([SmsMessage(to="0532 123 45 67", text="Deneme")])

    assert result.dry_run is True
    assert result.accepted is True
    assert result.recipients == 1
    assert result.parts == 1
    p._client.sms.send.assert_not_called()


async def test_gecersiz_numara_saglayiciya_gitmez():
    p = _provider()
    p._client = MagicMock()

    with pytest.raises(SmsInvalidRecipient) as exc:
        await p.send([SmsMessage(to="123", text="Deneme")])

    assert exc.value.raw == "123"
    p._client.sms.send.assert_not_called()


async def test_govdedeki_hata_kodu_yakalanir():
    """SDK'nın atladığı denetim: HTTP 200 ama gövdede hata kodu.

    Bu testin varlık nedeni budur — SDK burada istisna atmaz ve
    gönderilmemiş SMS başarılı sayılırdı.
    """
    p = _provider()
    p._client = MagicMock()
    p._client.sms.send.return_value = {"code": "40", "description": "header yok"}

    with pytest.raises(SmsRejected) as exc:
        await p.send([SmsMessage(to="5321234567", text="Deneme")])

    assert exc.value.provider_code == "40"
    assert "başlığı" in str(exc.value)


async def test_basarili_gonderim_jobid_dondurur():
    p = _provider()
    p._client = MagicMock()
    p._client.sms.send.return_value = {"code": "00", "jobid": "123456789"}

    result = await p.send(
        [
            SmsMessage(to="0532 123 45 67", text="Sunucu ayakta"),
            SmsMessage(to="+90 533 000 00 00", text="Sunucu ayakta"),
        ]
    )

    assert result.accepted is True
    assert result.job_id == "123456789"
    assert result.recipients == 2
    assert result.parts == 2

    # numaralar normalleştirilmiş, İYS filtresi eklenmiş olmalı
    kwargs = p._client.sms.send.call_args.kwargs
    assert [m["no"] for m in kwargs["messages"]] == ["5321234567", "5330000000"]
    assert kwargs["iysfilter"] == codes.IysFilter.INFORMATIONAL
    assert "encoding" not in kwargs        # Türkçe karakter yok


async def test_partide_tek_turkce_mesaj_tumunu_tr_yapar():
    p = _provider()
    p._client = MagicMock()
    p._client.sms.send.return_value = {"code": "00", "jobid": "1"}

    await p.send(
        [
            SmsMessage(to="5321234567", text="Server up"),
            SmsMessage(to="5331234567", text="Yedekleme başarısız"),
        ]
    )

    assert p._client.sms.send.call_args.kwargs["encoding"] == "tr"


async def test_zamanlanmis_gonderim_tarihi_bicimlendirilir():
    p = _provider()
    p._client = MagicMock()
    p._client.sms.send.return_value = {"code": "00", "jobid": "1"}

    await p.send(
        [SmsMessage(to="5321234567", text="Deneme")],
        scheduled_at=datetime(2023, 12, 1, 14, 30),
    )

    assert p._client.sms.send.call_args.kwargs["startdate"] == "011220231430"


async def test_zaman_asimi_transport_hatasina_cevrilir():
    import time

    p = _provider(timeout=0.05)
    p._client = MagicMock()
    p._client.sms.send.side_effect = lambda **kw: time.sleep(1)

    with pytest.raises(SmsTransportError) as exc:
        await p.send([SmsMessage(to="5321234567", text="Deneme")])

    assert "doğrulayın" in str(exc.value)


async def test_rapor_kayit_yoksa_bos_liste():
    p = _provider()
    p._client = MagicMock()
    p._client.sms.get_report.return_value = {"code": "60"}

    result = await p.report(datetime(2026, 8, 1), datetime(2026, 8, 2))
    assert result == []


def test_baslik_zorunlu():
    from km_platform.notify.errors import SmsConfigError

    with pytest.raises(SmsConfigError):
        NetgsmConfig(username="u", password="p", header="")
