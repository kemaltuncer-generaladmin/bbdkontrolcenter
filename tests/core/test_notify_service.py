"""`notify` yeteneğinin kapıları ve SMS metin yardımcıları.

Buradaki asıl sınama GÜVENLİK AĞIDIR: ödeme bağlantısı SMS'i gerçek para
harcatan bir işlemi tetikler ve yanlış numaraya gitmesi geri alınamaz. Bu
yüzden "kapalı" ve "kuru prova" varsayılanları testle sabitlenir — bir gün
biri ayar dosyasında varsayılanı değiştirirse burada yakalanır.

Hiçbir test ağa çıkmaz; sağlayıcı hiç kurulmaz.
"""

import pytest

from km_platform.notify.service import NotifyService
from km_sdk import (
    SmsConfigError,
    normalize_msisdn,
    offending,
    plan_text,
    simplify,
)


class FakeVault:
    def __init__(self, **values: str) -> None:
        self._values = values

    async def get(self, key: str) -> str | None:
        return self._values.get(key)


class FakeConfig:
    def __init__(self, sms: dict | None = None) -> None:
        self._sms = sms if sms is not None else {}

    def section(self, path: str) -> dict:
        assert path == "platform.notify"
        return {"sms": self._sms}


class FakeLog:
    def info(self, *args: object, **kwargs: object) -> None:
        pass

    def warning(self, *args: object, **kwargs: object) -> None:
        pass


def service(sms: dict | None = None, **secrets: str) -> NotifyService:
    return NotifyService(FakeVault(**secrets), FakeConfig(sms), FakeLog())


# ------------------------------------------------------------ varsayılanlar

def test_sms_varsayilan_olarak_kapalidir() -> None:
    assert service().enabled is False


def test_kuru_prova_varsayilan_olarak_aciktir() -> None:
    # Ayar hiç yazılmamışken bile gerçek SMS gitmemeli.
    assert service().dry_run is True
    # Yalnızca açıkça false yazılırsa kapanır.
    assert service({"dry_run": False}).dry_run is False


async def test_kapaliyken_gonderim_denemesi_anlatarak_reddedilir() -> None:
    with pytest.raises(SmsConfigError) as caught:
        await service().sms()
    assert "enabled" in str(caught.value)


async def test_kimlik_bilgisi_yokken_hangi_anahtarin_eksik_oldugu_soylenir() -> None:
    with pytest.raises(SmsConfigError) as caught:
        await service({"enabled": True, "header": "BBDUNYAM"}).sms()
    assert "notify.netgsm.username" in str(caught.value)


async def test_bilinmeyen_saglayici_reddedilir() -> None:
    with pytest.raises(SmsConfigError) as caught:
        await service({"enabled": True, "provider": "twilio"}).sms()
    assert "twilio" in str(caught.value)


# ------------------------------------------------------------------ durum

async def test_ready_kurulmamis_sistemde_bile_patlamaz() -> None:
    # K7: ekran durumu gösterebilmeli; beyaz sayfa hatanın kendisinden pahalı.
    state = await service().ready()
    assert state["configured"] is False
    assert state["enabled"] is False
    assert state["dryRun"] is True
    assert state["error"]          # sebep yazılı olmalı
    assert state["provider"] == "netgsm"


# ------------------------------------------------------- metin yardımcıları

def test_numara_serbest_bicimden_netgsm_bicimine_iner() -> None:
    for raw in ("0532 123 45 67", "+90 532 123 45 67", "90-532-123-45-67", "5321234567"):
        assert normalize_msisdn(raw) == "5321234567"


def test_gecersiz_numara_erkenden_reddedilir() -> None:
    with pytest.raises(ValueError):
        normalize_msisdn("0212 555 44 33")   # sabit hat


def test_turkce_harf_mesaji_pahalilastirir_sadelestirme_ucuzlatir() -> None:
    turkish = "Sayın Ahmet Yılmaz, ödemeniz için bağlantı: " + "x" * 80
    plain = simplify(turkish)

    assert plan_text(plain).parts < plan_text(turkish).parts
    assert "ı" not in plain and "ğ" not in plain and "İ" not in plain
    # Sadeleştirme okunabilirliği korumalı, harfleri yok etmemeli.
    assert plain.startswith("Sayin Ahmet Yilmaz")


def test_pahali_karakterler_kullaniciya_gosterilebilir() -> None:
    found = offending("Sayın Ali")
    assert "ı" in found
    # Tekrar etmez, sırayı korur.
    assert found == list(dict.fromkeys(found))
    # GSM-7'de zaten ucuz olan harf listeye girmez.
    assert "A" not in found and "S" not in found


def test_kalan_karakter_sayisi_bir_sonraki_parcaya_gore_hesaplanir() -> None:
    plan = plan_text("x" * 150)
    assert plan.parts == 1
    assert plan.remaining == 10           # 160 - 150

    tasan = plan_text("x" * 161)
    assert tasan.parts == 2
    assert tasan.remaining == 306 - 161   # 2 × 153

    assert plan_text("").remaining == 160
