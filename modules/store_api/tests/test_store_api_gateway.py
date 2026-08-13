"""Geçit politikaları: acil fren, kuru prova, gerekçe, yineleme, hız sınırı.

Hiçbir test ağa çıkmaz — `httpx.MockTransport` ile sahte sunucu kullanılır.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from store_api_backend.client import StoreApi
from store_api_backend.errors import StoreApiError
from store_api_fakes import FakeLog, FakeSecrets, FakeStore

TOKEN = "12|cokGizliBelirtec"


def gateway(handler: Any, **options: Any) -> tuple[StoreApi, FakeStore, FakeLog, FakeSecrets]:
    """Sahte taşıyıcıyla geçit. Varsayılanlar testler için AÇIK yazma kipinde."""
    depo, gunluk = FakeStore(), FakeLog()
    kasa = FakeSecrets({"store.admin_token": TOKEN})
    ayar: dict[str, Any] = {"read_only": False, "dry_run_default": False}
    ayar.update(options)
    api = StoreApi(base_url="https://ornek.test", secrets=kasa, log=gunluk, store=depo,
                   transport=httpx.MockTransport(handler), **ayar)
    api._sleep = _uyuma  # testler beklemesin
    return api, depo, gunluk, kasa


async def _uyuma(_seconds: float) -> None:
    return None


def json_response(payload: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


# ------------------------------------------------------------------ okuma

async def test_liste_zarfi_acilir_ve_sayfa_boyu_kirpilir() -> None:
    istekler: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        istekler.append(request)
        return json_response({"data": [{"id": 2392}], "meta": {"total": 1, "lastPage": 1}})

    api, _, _, _ = gateway(handler)
    sonuc = await api.orders({"status": "processing"}, page=2, per_page=500)

    assert sonuc["items"] == [{"id": 2392}]
    assert sonuc["meta"]["total"] == 1
    assert istekler[0].url.params["per_page"] == "50"   # sunucu sınırı
    assert istekler[0].url.params["page"] == "2"
    assert istekler[0].url.params["status"] == "processing"
    assert istekler[0].headers["Authorization"] == f"Bearer {TOKEN}"


async def test_tam_katalog_taramasi_sayfalari_sirayla_ister() -> None:
    sayfalar: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sayfa = request.url.params["page"]
        sayfalar.append(sayfa)
        return json_response({"data": [{"id": int(sayfa)}], "meta": {"total": 3, "lastPage": 3}})

    api, _, _, _ = gateway(handler)
    sonuc = await api.products(all_pages=True)

    assert sayfalar == ["1", "2", "3"]      # paralel değil, SIRAYLA
    assert len(sonuc["items"]) == 3


async def test_get_besyuzde_uc_kez_denenir() -> None:
    denemeler: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        denemeler.append(1)
        return httpx.Response(503, text="gateway down")

    api, _, _, _ = gateway(handler)
    with pytest.raises(StoreApiError) as hata:
        await api.orders()

    assert len(denemeler) == 3
    assert hata.value.code == "server"


async def test_yazma_bagalanti_hatasinda_yinelenmez() -> None:
    denemeler: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        denemeler.append(1)
        raise httpx.ConnectTimeout("zaman aşımı")

    api, _, _, _ = gateway(handler)
    with pytest.raises(StoreApiError) as hata:
        await api.cancel_order(7, reason="Müşteri iptal istedi, stok geri alındı")

    # Tekrarlamak ikinci iptal/ikinci fatura demek olurdu.
    assert len(denemeler) == 1
    assert hata.value.code == "transport"


# ------------------------------------------------------------- acil fren

async def test_acil_fren_acikken_yazma_uzaga_hic_gitmez() -> None:
    gonderilen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        gonderilen.append(request)
        return json_response({})

    api, depo, _, _ = gateway(handler, read_only=True)
    with pytest.raises(StoreApiError) as hata:
        await api.cancel_order(7, reason="Müşteri iptal istedi, stok geri alındı")

    assert hata.value.code == "read_only"
    assert gonderilen == []
    # Denemenin izi yerelde kalır: "ne yapmaya çalıştık" kaybolmaz.
    assert depo.audit[0]["result"] == "blocked"
    assert depo.audit[0]["path"].endswith("/orders/7/cancel")


async def test_acil_fren_okumayi_engellemez() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return json_response({"data": [], "meta": {"total": 0}})

    api, _, _, _ = gateway(handler, read_only=True)
    assert (await api.orders())["items"] == []


# ------------------------------------------------------------ kuru prova

async def test_cekirdek_ucunda_kuru_prova_istek_gondermez() -> None:
    gonderilen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        gonderilen.append(request)
        return json_response({})

    api, depo, _, _ = gateway(handler, dry_run_default=True)
    sonuc = await api.update_product(42, payload={"sku": "ABC"},
                                     reason="Fiyat listesi güncellemesi 2026-08")

    # Bagisto `dryRun` bilmez: göndermek GERÇEK yazma olurdu.
    assert gonderilen == []
    assert sonuc["sent"] is False
    assert sonuc["dryRun"] is True
    assert depo.audit[0]["result"] == "dry_run"


async def test_bbd_ucunda_kuru_prova_bayrakla_gonderilir() -> None:
    govdeler: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        govdeler.append(_json.loads(request.content))
        return json_response({"ok": True})

    api, _, _, _ = gateway(handler, dry_run_default=True)
    await api.bbd_create_payment_link(order_id=7, amount=12_500,
                                      reason="Telefonla sipariş, link istendi")

    assert govdeler[0]["dryRun"] is True
    assert govdeler[0]["amount"] == 12_500
    # Gerekçe BBD uçlarında gövdeye de konur; çekirdek uçlarında konmaz.
    assert govdeler[0]["reason"].startswith("Telefonla")


async def test_kuru_prova_kapatilinca_istek_gercekten_gider() -> None:
    gonderilen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        gonderilen.append(request)
        return json_response({"id": 42})

    api, depo, _, _ = gateway(handler)
    sonuc = await api.update_product(42, payload={"sku": "ABC"},
                                     reason="Fiyat listesi güncellemesi 2026-08",
                                     dry_run=False)

    assert len(gonderilen) == 1
    assert sonuc["id"] == 42
    assert depo.audit[0]["result"] == "ok"


# --------------------------------------------------------------- gerekçe

async def test_gerekcesiz_yazma_reddedilir() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    api, _, _, _ = gateway(handler)
    with pytest.raises(StoreApiError) as hata:
        await api.cancel_order(7, reason="kısa")
    assert hata.value.code == "reason_required"


async def test_gerekce_ve_kimlik_basliklari_yuzde_kodlu_gider() -> None:
    istekler: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        istekler.append(request)
        return json_response({})

    api, depo, _, _ = gateway(handler)
    await api.cancel_order(7, reason="Müşteri vazgeçti, ürün gönderilmedi", actor="Ayşe Yılmaz")

    baslik = istekler[0].headers
    # HTTP başlığı latin-1'dir: ş/ğ ham gitseydi httpx patlardı.
    assert "%C3%BC" in baslik["X-Bbd-Reason"]
    assert "%C5%9F" in baslik["X-Bbd-Actor"]
    assert baslik["X-Bbd-Request-Id"] == depo.audit[0]["request_id"]
    # Yerel denetim izinde gerekçe HAM hâliyle durur.
    assert depo.audit[0]["reason"] == "Müşteri vazgeçti, ürün gönderilmedi"


# ---------------------------------------------------------------- hatalar

async def test_belirtec_yoksa_anlasilir_hata_doner() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    depo, gunluk = FakeStore(), FakeLog()
    api = StoreApi(base_url="https://ornek.test", secrets=FakeSecrets({}), log=gunluk,
                   store=depo, read_only=False, transport=httpx.MockTransport(handler))
    with pytest.raises(StoreApiError) as hata:
        await api.orders()

    assert hata.value.code == "config_missing"
    assert "store.admin_token" in hata.value.message


async def test_dortyuzbir_belirteci_dusurur_ve_metne_sizdirmaz() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return json_response({"message": f"Unauthenticated. token={TOKEN}"}, status=401)

    api, _, _, kasa = gateway(handler)
    with pytest.raises(StoreApiError) as hata:
        await api.orders()

    assert hata.value.code == "unauthorized"
    assert TOKEN not in str(hata.value)
    assert "cokGizliBelirtec" not in str(hata.value)
    # Bir sonraki çağrı kasadan yeniden okusun (belirteç döndürülmüş olabilir).
    assert api._token is None
    assert kasa.reads == ["store.admin_token"]


async def test_yayinda_olmayan_bbd_ucu_anlasilir_hata_doner() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="<!DOCTYPE html>404 Not Found")

    api, _, _, _ = gateway(handler)
    with pytest.raises(StoreApiError) as hata:
        await api.bbd_shipments()

    assert hata.value.code == "bbd_endpoint_missing"
    assert "henüz yayında değil" in hata.value.message


async def test_dogrulama_hatasinin_mesaji_tasinir_sirlar_maskelenir() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return json_response({"message": 'sku zorunlu (token: "12|acik")'}, status=422)

    api, depo, _, _ = gateway(handler)
    with pytest.raises(StoreApiError) as hata:
        await api.update_product(1, payload={}, reason="SKU düzeltmesi yapıldı", dry_run=False)

    assert hata.value.code == "validation"
    assert "sku zorunlu" in hata.value.message
    assert "12|acik" not in hata.value.message
    assert depo.audit[0]["result"] == "error:validation"


# ------------------------------------------------------------ hız sınırı

async def test_dorttyuzyirmidokuz_retry_after_kadar_bekleyip_bir_kez_yineler() -> None:
    denemeler: list[int] = []
    beklemeler: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        denemeler.append(1)
        if len(denemeler) == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, text="Too Many Requests")
        return json_response({"data": [], "meta": {}})

    api, _, _, _ = gateway(handler)

    async def kaydeden_uyku(seconds: float) -> None:
        beklemeler.append(seconds)

    api._sleep = kaydeden_uyku
    await api.orders()

    assert len(denemeler) == 2
    assert beklemeler == [2.0]


async def test_hiz_kovasi_dakikalik_sinira_ulasinca_bekletir() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return json_response({"data": [], "meta": {}})

    api, _, _, _ = gateway(handler, requests_per_minute=2)
    beklemeler: list[float] = []

    async def kaydeden_uyku(seconds: float) -> None:
        beklemeler.append(seconds)
        # Gerçekte 60 sn beklenirdi; testte pencereyi bekletmeden geçiyoruz.
        api._bucket._stamps.clear()

    api._sleep = kaydeden_uyku
    for _ in range(3):
        await api.orders()

    assert len(beklemeler) == 1
    assert 0 < beklemeler[0] <= 60


# ------------------------------------------------------- anlık görüntü

async def test_anlik_goruntu_referanslari_sirayla_toplar_ve_onbellege_yazar() -> None:
    yollar: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        yollar.append(request.url.path)
        return json_response({"data": [{"id": 1}], "meta": {"total": 1, "lastPage": 1}})

    api, depo, _, _ = gateway(handler)
    sonuc = await api.snapshot()

    assert list(sonuc["parts"]) == list(StoreApi.SNAPSHOT_PARTS)
    assert sonuc["errors"] == []
    assert "reference" in depo.snapshots
    # Yedi referans listesi, yedi istek — hepsi sırayla.
    assert len(yollar) == len(StoreApi.SNAPSHOT_PARTS)


async def test_magaza_dusukken_bayat_anlik_goruntu_doner() -> None:
    def calisan(_request: httpx.Request) -> httpx.Response:
        return json_response({"data": [{"id": 1}], "meta": {"total": 1, "lastPage": 1}})

    api, _, _, _ = gateway(calisan)
    await api.snapshot()

    def dusuk(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="down")

    api._transport = httpx.MockTransport(dusuk)
    api._reference.drop()
    sonuc = await api.snapshot(refresh=True)

    # Ekran ayakta kalır (K7) ama verinin bayat olduğunu SÖYLER.
    assert sonuc["stale"] is True
    assert sonuc["errors"]
    assert sonuc["parts"]["channels"] == [{"id": 1}]


async def test_referans_listeleri_onbellekten_gelir() -> None:
    istekler: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        istekler.append(1)
        return json_response({"data": [{"code": "TRY"}], "meta": {"total": 1, "lastPage": 1}})

    api, _, _, _ = gateway(handler)
    await api.currencies()
    await api.currencies()

    assert len(istekler) == 1     # ikinci çağrı mağazaya gitmez
