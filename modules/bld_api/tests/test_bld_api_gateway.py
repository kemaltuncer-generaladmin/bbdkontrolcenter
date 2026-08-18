"""Geçit politikaları: acil fren, kuru prova, gerekçe, yineleme, hız kovası.

Hiçbir test ağa çıkmaz — `httpx.MockTransport` ile sahte sunucu kullanılır.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from bld_api_backend.client import CONTROL
from bld_api_backend.errors import BldApiError
from bld_api_fakes import gateway


def json_response(payload: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


# ------------------------------------------------------------------ okuma

async def test_liste_zarfi_iki_bicimde_de_acilir() -> None:
    def duz(_request: httpx.Request) -> httpx.Response:
        return json_response([{"id": 3, "name": "Mutfak Kasası"}])

    api, _, _, _ = gateway(duz)
    assert (await api.devices()) == [{"id": 3, "name": "Mutfak Kasası"}]

    def zarfli(_request: httpx.Request) -> httpx.Response:
        return json_response({"data": [{"id": 4}]})

    api, _, _, _ = gateway(zarfli)
    assert (await api.devices()) == [{"id": 4}]


async def test_get_besyuzde_uc_kez_denenir() -> None:
    denemeler: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        denemeler.append(1)
        return httpx.Response(503, text="gateway down")

    api, _, _, _ = gateway(handler)
    with pytest.raises(BldApiError) as hata:
        await api.devices()

    assert len(denemeler) == 3
    assert hata.value.code == "server"


async def test_get_baglanti_hatasinda_da_uc_kez_denenir() -> None:
    denemeler: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        denemeler.append(1)
        raise httpx.ConnectTimeout("zaman aşımı")

    api, _, _, _ = gateway(handler)
    with pytest.raises(BldApiError) as hata:
        await api.devices()

    assert len(denemeler) == 3
    assert hata.value.code == "transport"


async def test_yazma_baglanti_hatasinda_yinelenmez() -> None:
    denemeler: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        denemeler.append(1)
        raise httpx.ConnectTimeout("zaman aşımı")

    api, depo, _, _ = gateway(handler)
    with pytest.raises(BldApiError) as hata:
        await api.revoke_device(3, reason="Kasa mutfaktan çıkarıldı", actor="Ayşe Yılmaz")

    # Tekrarlamak ikinci iptal demek olurdu; sözleşmede idempotency
    # anahtarı taşıyan bir başlık da yok.
    assert len(denemeler) == 1
    assert hata.value.code == "transport"
    assert depo.audit[0]["result"] == "error:transport"


# ------------------------------------------------------------- acil fren

async def test_acil_fren_acikken_yazma_uzaga_hic_gitmez() -> None:
    gonderilen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        gonderilen.append(request)
        return json_response({})

    api, depo, _, _ = gateway(handler, read_only=True)
    with pytest.raises(BldApiError) as hata:
        await api.revoke_device(3, reason="Kasa mutfaktan çıkarıldı", actor="Ayşe Yılmaz")

    assert hata.value.code == "read_only"
    assert gonderilen == []
    # Denemenin izi yerelde kalır: "ne yapmaya çalıştık" kaybolmaz.
    assert depo.audit[0]["result"] == "blocked"
    assert depo.audit[0]["path"].endswith("/devices/3/revoke")
    assert depo.audit[0]["actor"] == "Ayşe Yılmaz"


async def test_acil_fren_okumayi_engellemez() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return json_response({"data": []})

    api, _, _, _ = gateway(handler, read_only=True)
    assert (await api.devices()) == []


# ------------------------------------------------------------ kuru prova

async def test_sozlesmedeki_ucta_kuru_prova_bayrakla_gonderilir() -> None:
    govdeler: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        govdeler.append(json.loads(request.content))
        return json_response({"ok": True, "dry_run": True, "would": {"revoked": 3}})

    api, depo, _, _ = gateway(handler, dry_run_default=True)
    sonuc = await api.revoke_device(3, reason="Kasa mutfaktan çıkarıldı", actor="Ayşe Yılmaz")

    # Sözleşme §4: uç bayrağı anlar, istek gerçekten gider.
    assert govdeler[0]["dry_run"] is True
    assert govdeler[0]["reason"] == "Kasa mutfaktan çıkarıldı"
    assert govdeler[0]["actor"] == "Ayşe Yılmaz"
    assert sonuc["would"] == {"revoked": 3}
    assert depo.audit[0]["result"] == "ok"


async def test_bilinmeyen_ucta_kuru_prova_istegi_hic_gondermez() -> None:
    """Laravel tanımadığı alanı yok sayar: bayrak sessizce düşer ve "prova"
    gerçek yazmaya dönüşürdü."""
    gonderilen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        gonderilen.append(request)
        return json_response({})

    api, depo, _, _ = gateway(handler, dry_run_default=True)
    sonuc = await api._request(
        "POST", f"{CONTROL}/devices/3/sozlesmede-olmayan", body={"x": 1},
        reason="Sözleşme dışı uç denemesi", actor="Ayşe Yılmaz", action="deneme",
    )

    assert gonderilen == []
    assert sonuc["sent"] is False
    assert sonuc["dry_run"] is True
    assert depo.audit[0]["result"] == "dry_run"


async def test_kuru_prova_kapatilinca_bayrak_govdeye_konmaz() -> None:
    govdeler: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        govdeler.append(json.loads(request.content))
        return json_response({"id": 9})

    api, depo, _, _ = gateway(handler)
    sonuc = await api.create_device(name="Mutfak Kasası", reason="Yeni kasa kuruldu",
                                    actor="Ayşe Yılmaz", dry_run=False)

    assert "dry_run" not in govdeler[0]
    assert sonuc["id"] == 9
    assert depo.audit[0]["result"] == "ok"
    assert depo.audit[0]["dry_run"] == 0


# --------------------------------------------------------------- gerekçe

async def test_kisa_gerekce_reddedilir() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    api, _, _, _ = gateway(handler)
    with pytest.raises(BldApiError) as hata:
        await api.revoke_device(3, reason="kısa", actor="Ayşe Yılmaz")

    assert hata.value.code == "reason_required"


async def test_aktorsuz_yazma_reddedilir() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    api, _, _, _ = gateway(handler)
    with pytest.raises(BldApiError) as hata:
        await api.revoke_device(3, reason="Kasa mutfaktan çıkarıldı", actor="   ")

    # Sözleşme §3 aktörü de zorunlu sayıyor: revizyon kaydında
    # `created_by_staff` = "Kontrol Merkezi" + aktör yazılıyor.
    assert hata.value.code == "actor_required"


async def test_revizyon_gerekcesi_yuz_altmis_karakteri_asamaz() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    api, _, _, _ = gateway(handler)
    with pytest.raises(BldApiError) as hata:
        await api.create_order_revision(12, items=[{"menu_id": 1, "quantity": 2}],
                                        reason="A" * 161, actor="Ayşe Yılmaz")

    assert hata.value.code == "reason_required"


async def test_gerekce_ve_aktor_govdeye_konur_baslikla_tasinmaz() -> None:
    istekler: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        istekler.append(request)
        return json_response({"ok": True})

    api, depo, _, _ = gateway(handler)
    await api.revoke_device(3, reason="Müşteri şikâyeti sonrası kasa değişti",
                            actor="Ayşe Yılmaz")

    govde = json.loads(istekler[0].content)
    assert govde["reason"] == "Müşteri şikâyeti sonrası kasa değişti"
    assert govde["actor"] == "Ayşe Yılmaz"
    # Sözleşme §1 yalnız üç başlık tanımlıyor; dördüncüsü uydurulmaz.
    assert sorted(k for k in istekler[0].headers if k.lower().startswith("x-")) == [
        "x-control-nonce", "x-control-signature", "x-control-timestamp",
    ]
    # Yerel denetim izinde gerekçe HAM hâliyle durur.
    assert depo.audit[0]["reason"] == "Müşteri şikâyeti sonrası kasa değişti"


# ------------------------------------------------------------ hız sınırı

async def test_dortyuzyirmidokuz_retry_after_kadar_bekleyip_bir_kez_yineler() -> None:
    denemeler: list[int] = []
    beklemeler: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        denemeler.append(1)
        if len(denemeler) == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, text="Too Many Requests")
        return json_response({"data": []})

    api, _, _, _ = gateway(handler)

    async def kaydeden_uyku(seconds: float) -> None:
        beklemeler.append(seconds)

    api._sleep = kaydeden_uyku
    await api.devices()

    assert len(denemeler) == 2
    assert beklemeler == [2.0]


async def test_yazma_bile_dortyuzyirmidokuzda_bir_kez_yinelenir() -> None:
    """429 istisnadır: hız sınırı isteği denetleyiciye ULAŞTIRMADAN reddeder,
    yan etkisi yoktur."""
    denemeler: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        denemeler.append(1)
        if len(denemeler) == 1:
            return httpx.Response(429, headers={"Retry-After": "1"}, text="rate")
        return json_response({"ok": True})

    api, _, _, _ = gateway(handler)
    await api.revoke_device(3, reason="Kasa mutfaktan çıkarıldı", actor="Ayşe Yılmaz")

    assert len(denemeler) == 2


async def test_ikinci_dortyuzyirmidokuz_hata_olur() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "1"}, text="rate")

    api, _, _, _ = gateway(handler)
    with pytest.raises(BldApiError) as hata:
        await api.devices()

    assert hata.value.code == "rate_limited"


async def test_hiz_kovasi_dakikalik_sinira_ulasinca_bekletir() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return json_response({"data": []})

    api, _, _, _ = gateway(handler, requests_per_minute=2)
    beklemeler: list[float] = []

    async def kaydeden_uyku(seconds: float) -> None:
        beklemeler.append(seconds)
        # Gerçekte 60 sn beklenirdi; testte pencereyi bekletmeden geçiyoruz.
        api._bucket._stamps.clear()

    api._sleep = kaydeden_uyku
    for _ in range(3):
        await api.devices()

    assert len(beklemeler) == 1
    # `+ 0.01` pay: pencerenin TAM sınırında uyanmak ikinci bir kısa uykuya
    # yol açıyordu (`_RateBucket.take`).
    assert 0 < beklemeler[0] <= 61


# ── İKİ PENCERELİ KOVA ────────────────────────────────────────────────────
#
# Tek dakikalık pencere etkileşimli kullanımı cezalandırıyordu: bir panel
# açılışı 4-6 istek atıyor, 18'lik tavan saniyeler içinde doluyor ve sonraki
# istek `60 - yaş` kadar UYUYORDU. Aşağıdaki iki test yeni davranışı
# sabitliyor — patlama geçer, saatlik tavan tutar.

async def test_patlama_kisa_pencerede_beklemeden_gecer() -> None:
    """Bir panel açılışı kadar istek arka arkaya, HİÇ UYUMADAN gitmeli."""
    def handler(_request: httpx.Request) -> httpx.Response:
        return json_response({"data": []})

    api, _, _, _ = gateway(handler, requests_per_minute=45, requests_per_hour=1100)
    beklemeler: list[float] = []

    async def kaydeden_uyku(seconds: float) -> None:  # pragma: no cover — çağrılmamalı
        beklemeler.append(seconds)

    api._sleep = kaydeden_uyku
    # Üç ekranın açılışı: her biri ~6 istek.
    for _ in range(18):
        await api.devices()

    assert beklemeler == [], "etkileşimli patlama uyutulmamalı"


async def test_saatlik_tavan_kacak_dongude_tutar() -> None:
    """Kısa pencere doldukça açılır ama SAATLİK tavan sunucu sınırını korur.

    Kaçak bir yoklama döngüsü dakikalık tavanı defalarca doldurup açabilir;
    sert tavan olmasaydı saatte binlerce istek çıkar, sunucu 429 verir ve o
    hata ekranda "bağlantı koptu" diye görünürdü.

    UYKU SAHTE SAATİ İLERLETİYOR: damgalar uyunan süre kadar geriye kaydırılır.
    Deftere dokunmayan bir taklit, döngüyü sonsuza kadar uyutur; hepsini
    silen bir taklit ise saatlik sayacı da sıfırlar ve testin sınadığı şeyi
    ortadan kaldırırdı.
    """
    def handler(_request: httpx.Request) -> httpx.Response:
        return json_response({"data": []})

    # Kısa pencere DAR (3), saatlik tavan da dar (6): kısa pencere iki kez
    # dolup açılacak, sonra saatlik tavan devreye girecek.
    api, _, _, _ = gateway(handler, requests_per_minute=3, requests_per_hour=6)
    beklemeler: list[float] = []

    async def kaydeden_uyku(seconds: float) -> None:
        beklemeler.append(seconds)
        kova = api._bucket
        kova._stamps = type(kova._stamps)(stamp - seconds for stamp in kova._stamps)

    api._sleep = kaydeden_uyku
    for _ in range(7):
        await api.devices()

    # İlk bekleme KISA pencere yüzünden (4. istek), ikincisi SAATLİK tavan
    # yüzünden (7. istek).
    assert len(beklemeler) == 2
    assert 0 < beklemeler[0] <= 61
    assert 60 < beklemeler[1] <= 3601


# ------------------------------------------------------------- denetim izi

async def test_denetim_izi_en_yeniden_eskiye_okunur() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return json_response({"ok": True})

    api, _, _, _ = gateway(handler)
    await api.revoke_device(3, reason="Birinci deneme kaydı", actor="Ayşe Yılmaz")
    await api.revoke_device(4, reason="İkinci deneme kaydı", actor="Ayşe Yılmaz")

    satirlar = await api.audit_trail(limit=10)
    assert satirlar[0]["reason"] == "İkinci deneme kaydı"
    assert satirlar[0]["result"] == "ok"


async def test_depo_yazamazsa_istek_yine_de_gider() -> None:
    """Yerel SQLite hıçkırığı mutfak işlemini durdurmaz; hata loglanır."""
    def handler(_request: httpx.Request) -> httpx.Response:
        return json_response({"ok": True})

    api, depo, gunluk, _ = gateway(handler)
    depo.fail = True
    sonuc = await api.revoke_device(3, reason="Kasa mutfaktan çıkarıldı", actor="Ayşe Yılmaz")

    assert sonuc["ok"] is True
    assert "denetim izi yazılamadı" in gunluk.text()
