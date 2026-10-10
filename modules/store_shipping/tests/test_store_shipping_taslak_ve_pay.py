"""Taslak ölçüsü, etiket ücreti ve geçit payı — 10.10.2026 düzeltmeleri.

Bu dosyanın koruduğu ÜÇ şey — her biri canlıda sessiz bir yanlıştı:

 1. ESKİ ÖLÇÜLÜ TASLAK. Mağaza aynı sipariş için ikinci taslak açmaz, var
    olanı döndürür (`ShipmentManager::createFor`) ve taslağın ölçüsünü
    güncelleyen bir uç yoktur. Sihirbazda koliyi yeniden ölçen kişi onay
    penceresinde YENİ ölçüyü görüyor, etiket ESKİ ölçüyle satın alınıyordu.
 2. ÜCRET 0. "Kargoya ver" olayı `feeKurus: 0` yayınlıyordu; kâr/zarar
    hesabı her gönderiyi bedava sayıyordu.
 3. GEÇİT PAYI. Dakikada 55 istek bütün ekranlara yetmek zorunda; sihirbazın
    ücret dökümü, performans sekmesi, toplu etiket ve manifesto bu payı
    gereksiz yere tüketiyordu.

Ağa çıkmaz, gerçek DB kullanmaz, canlıya yazmaz.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from store_shipping_backend import service as service_module
from store_shipping_backend import shipping
from store_shipping_backend.service import ShippingService
from store_shipping_fakes import Events, FakeApi, FakeLog, FakeStore, _bos_pdf

SIPARIS: dict[str, Any] = {
    "id": 91, "increment_id": "S-91", "status": "processing", "grand_total": "500.00",
    "total_qty_ordered": 3, "total_qty_invoiced": 3, "total_qty_shipped": 0,
    "customer_full_name": "Ayşe Yılmaz",
    "shipping_address": {"city": "İstanbul", "district": "Adalar", "phone": "5321234567"},
    "items": [{"id": 42, "sku": "A", "qty_ordered": 3, "qty_shipped": 0}],
}

#: Mağazanın gönderi künyesi (`ShipmentController::present`) — snake_case,
#: beyaz listeli. Etiketi ALINMAMIŞ taslak.
TASLAK: dict[str, Any] = {
    "id": 70, "order_id": 91, "geliver_shipment_id": "g-70",
    "geliver_transaction_id": None, "status": "INFO_RECEIVED", "is_return": False,
    "tracking_number": None, "barcode": None, "amount": None,
    "isPurchased": False, "canPurchase": True, "canCancel": True,
}

OLCU = {"length": 30.0, "width": 20.0, "height": 10.0}      # 2 desi


def _service(tmp_path: Path, *, events: Any = None, stage_notify: Any = None,
             api: FakeApi | None = None,
             **config: Any) -> tuple[ShippingService, FakeApi, FakeStore]:
    api = api or FakeApi()
    store = FakeStore()
    service = ShippingService(
        api=api, store=store, log=FakeLog(), publish=events, stage_notify=stage_notify,
        config={"channel": "default", "locale": "tr", "idle_days": 3, **config},
        fallback_dir=tmp_path,
    )
    api.order_by_id = {91: dict(SIPARIS)}
    api.invoice_rows = [{"id": 19, "orderIncrementId": "S-91"}]
    return service, api, store


def _cleanup(result: dict[str, Any]) -> None:
    for item in result.get("documents") or []:
        if item.get("path"):
            Path(item["path"]).unlink(missing_ok=True)


def _order_of_calls(api: FakeApi) -> list[str]:
    return [name for name, _, _ in api.calls]


# ===================================================== 1 · eski ölçülü taslak

async def test_olcu_degisince_eski_taslak_IPTAL_edilir_sonra_kargoya_verilir(
        tmp_path: Path) -> None:
    service, api, store = _service(tmp_path)
    api.shipments_payload = {"items": [dict(TASLAK)], "meta": {"total": 1}}

    result = await service.dispatch(91, actor="Ali", **OLCU)

    assert result["ok"] is True
    iptal = api.used("bbd_cancel_shipment")
    assert len(iptal) == 1 and iptal[0]["dry_run"] is False
    assert api.args_of("bbd_cancel_shipment")[0] == (70,)
    # SIRA ÖNEMLİ: iptal satın almadan ÖNCE. Tersi eski ölçüyle etiket alırdı.
    sira = _order_of_calls(api)
    assert sira.index("bbd_cancel_shipment") < sira.index("bbd_dispatch_order")
    assert result["replacedDraft"] == 70
    assert any("ücretsiz iptal" in line for line in result["warnings"])
    assert [row["result"] for row in store.audit if row["action"] == "replace_draft"] \
        == ["denendi", "ok"]
    _cleanup(result)


async def test_AYNI_olcuyle_acilmis_taslaga_dokunulmaz(tmp_path: Path) -> None:
    # Sihirbazda "Taslak aç ve teklifleri getir" sonra "Kargoya ver": taslak
    # zaten bu ölçüyle açıldı; iptal etmek teklifleri boşa düşürürdü.
    service, api, store = _service(tmp_path)
    api.shipments_payload = {"items": [dict(TASLAK)], "meta": {"total": 1}}
    store.audit.append({"shipment_id": 70, "order_id": 91, "action": "create_shipment",
                        "reason": "", "actor": "Ali", "result": "ok",
                        "detail": json.dumps({"desi": 2.0, **OLCU}), "created_at": ""})

    result = await service.dispatch(91, actor="Ali", **OLCU)

    assert result["ok"] is True
    assert api.used("bbd_cancel_shipment") == []
    assert result["replacedDraft"] == 0
    _cleanup(result)


async def test_etiketi_ALINMIS_gonderide_yeni_olcu_reddedilir_para_harcanmaz(
        tmp_path: Path) -> None:
    service, api, _ = _service(tmp_path)
    api.shipments_payload = {"items": [{**TASLAK, "geliver_transaction_id": "tx-1",
                                        "isPurchased": True, "canPurchase": False,
                                        "tracking_number": "1234567890"}],
                             "meta": {"total": 1}}

    result = await service.dispatch(91, actor="Ali", **OLCU)

    assert result["ok"] is False
    assert result["alreadyPurchased"] is True
    assert "zaten alınmış" in result["error"]
    assert api.used("bbd_dispatch_order") == []
    assert api.used("bbd_cancel_shipment") == []       # ödenmiş etiket YAKILMAZ


async def test_olcusuz_kargoya_ver_taslaga_HIC_bakmaz(tmp_path: Path) -> None:
    # Satırdaki tek tık ölçü göndermez; mağaza siparişin kendi desisini ya da
    # var olan taslağı kullanır. Fazladan istek atmanın sebebi yok.
    service, api, _ = _service(tmp_path)
    api.shipments_payload = {"items": [dict(TASLAK)], "meta": {"total": 1}}

    result = await service.dispatch(91, actor="Ali")

    assert result["ok"] is True
    assert api.used("bbd_shipments") == []
    assert api.used("bbd_cancel_shipment") == []
    _cleanup(result)


async def test_taslak_okunamazsa_etiket_ALINMAZ(tmp_path: Path) -> None:
    service, api, _ = _service(tmp_path)
    api.fail.add("bbd_shipments")

    result = await service.dispatch(91, actor="Ali", **OLCU)

    assert result["ok"] is False
    assert "satın ALINMADI" in result["error"]
    assert api.used("bbd_dispatch_order") == []


async def test_taslak_iptal_edilemezse_etiket_ALINMAZ(tmp_path: Path) -> None:
    service, api, _ = _service(tmp_path)
    api.shipments_payload = {"items": [dict(TASLAK)], "meta": {"total": 1}}
    api.fail.add("bbd_cancel_shipment")

    result = await service.dispatch(91, actor="Ali", **OLCU)

    assert result["ok"] is False
    assert "#70" in result["error"]
    assert api.used("bbd_dispatch_order") == []


async def test_kuru_provada_taslaga_dokunulmaz(tmp_path: Path) -> None:
    service, api, _ = _service(tmp_path)
    api.shipments_payload = {"items": [dict(TASLAK)], "meta": {"total": 1}}
    result = await service.dispatch(91, actor="Ali", dry_run=True, **OLCU)
    assert api.used("bbd_cancel_shipment") == []
    _cleanup(result)


async def test_yarim_kalan_zincirin_taslagi_HATIRLANIR_ikinci_denemede_silinmez(
        tmp_path: Path) -> None:
    """En sık yarım kalış: "teklifler henüz hazır değil, birkaç saniye sonra
    deneyin". Zincir taslağı AÇTI ama etiket alamadı. Taslağın ölçüsü
    hatırlanmasaydı ikinci deneme onu "bilinmiyor" sayıp iptal eder, yeniden
    açar ve teklifler yine hazır olmazdı — sonsuz döngü."""

    class TekliflerHazirDegil(FakeApi):
        def __init__(self) -> None:
            super().__init__()
            self.patlat = True

        async def bbd_dispatch_order(self, order_id: int, **kwargs: Any) -> dict[str, Any]:
            if self.patlat:
                self._record("bbd_dispatch_order", order_id, **kwargs)
                # Zincir taslağı açtı ve teklif aşamasında düştü.
                self.shipments_payload = {"items": [{**TASLAK, "id": 81}],
                                          "meta": {"total": 1}}
                raise RuntimeError("Kargo firması henüz teklif üretmedi.")
            return await super().bbd_dispatch_order(order_id, **kwargs)

    api = TekliflerHazirDegil()
    service, _, store = _service(tmp_path, api=api)

    ilk = await service.dispatch(91, actor="Ali", **OLCU)
    assert ilk["ok"] is False
    hatira = [row for row in store.audit if row["action"] == "draft_measures"]
    assert [row["shipment_id"] for row in hatira] == [81]

    api.patlat = False
    ikinci = await service.dispatch(91, actor="Ali", **OLCU)
    assert ikinci["ok"] is True
    assert api.used("bbd_cancel_shipment") == []        # taslak korunur
    _cleanup(ikinci)


async def test_sihirbaz_taslagi_da_eski_olcuyle_KALMAZ(tmp_path: Path) -> None:
    service, api, _ = _service(tmp_path)
    api.shipments_payload = {"items": [dict(TASLAK)], "meta": {"total": 1}}

    result = await service.create_shipment(
        91, carrier="hepsijet", packages=1, desi_value=0, weight=1, payer="sender", cod=0,
        note="", reason="", actor="Ali", dry_run=False, provider="geliver", **OLCU)

    assert result["ok"] is True
    assert api.args_of("bbd_cancel_shipment")[0] == (70,)
    sira = _order_of_calls(api)
    assert sira.index("bbd_cancel_shipment") < sira.index("bbd_create_shipment")


# ========================================================= 2 · etiket ücreti

async def test_etiket_ucreti_gonderi_kunyesinden_okunur_ve_olaya_yazilir(
        tmp_path: Path) -> None:
    olaylar = Events()
    service, api, _ = _service(tmp_path, events=olaylar)
    api.shipment_by_id = {77: {"id": 77, "order_id": 91, "amount": "54.90",
                               "tracking_url": "https://takip.example/1"}}

    result = await service.dispatch(91, actor="Ali")

    assert result["fee"] == 5490
    assert result["trackingUrl"] == "https://takip.example/1"
    [satin] = [payload for name, payload in olaylar.published
               if name == "store.shipment.purchased"]
    assert satin["feeKurus"] == 5490 and satin["feeKnown"] is True
    _cleanup(result)


async def test_ucret_okunamazsa_SIFIR_uydurulmaz(tmp_path: Path) -> None:
    olaylar = Events()
    service, _, _ = _service(tmp_path, events=olaylar)      # künye yok

    result = await service.dispatch(91, actor="Ali")

    assert result["ok"] is True
    assert result["fee"] is None
    [satin] = [payload for name, payload in olaylar.published
               if name == "store.shipment.purchased"]
    assert satin["feeKnown"] is False
    _cleanup(result)


def test_gonderi_satirinin_ucreti_magazanin_amount_alanindan_okunur() -> None:
    row = shipping.shipment_row({"id": 1, "status": "in_transit", "amount": "54.90"})
    assert row["fee"] == 5490


# ============================================================ 3 · geçit payı

class _Sms:
    async def notify(self, **_: Any) -> dict[str, Any]:
        return {"ok": True, "sent": True}


async def test_kargoya_ver_siparisi_ve_kunyeyi_BIR_KEZ_okur(tmp_path: Path) -> None:
    # Fatura ve SMS aynı sipariş künyesini ayrı ayrı okuyordu; SMS takip
    # bağlantısı için gönderiyi de ayrıca okuyordu.
    service, api, _ = _service(tmp_path, stage_notify=_Sms())
    api.shipment_by_id = {77: {"id": 77, "order_id": 91, "tracking_url": "https://t/1"}}

    result = await service.dispatch(91, actor="Ali")

    assert result["sms"]["sent"] is True
    assert len(api.args_of("order")) == 1
    assert len(api.args_of("bbd_shipment")) == 1
    _cleanup(result)


async def test_ucret_dokumu_matrisi_ve_siparisi_her_degisimde_SORMAZ(tmp_path: Path) -> None:
    service, api, _ = _service(tmp_path)
    for desi in (1, 2, 3):
        await service.quote(order_id=91, carrier="yurtici", desi_value=desi, weight=0.5)
    assert len(api.used("bbd_shipping_rates")) == 1
    assert len(api.args_of("order")) == 1


async def test_matris_kaydedilince_bellek_DUSER(tmp_path: Path) -> None:
    service, api, _ = _service(tmp_path)
    await service.rates()
    await service.save_rates(carriers=[{"code": "yurtici", "tiers": [
        {"min": 0, "max": 0, "price": 4500}]}], free_threshold=0, cod_fee=0, promise="",
        reason="Tarife güncellendi", actor="Ali", dry_run=False)
    await service.rates()
    assert len(api.used("bbd_shipping_rates")) == 2


async def test_tasiyici_listesi_bellekten_verilir(tmp_path: Path) -> None:
    service, api, _ = _service(tmp_path)
    await service.carriers()
    await service.carriers()
    assert len(api.used("bbd_carriers")) == 1


async def test_tahmin_desi_matrisinden_ve_UYDURMADAN(tmp_path: Path) -> None:
    service, api, _ = _service(tmp_path)
    api.rates_payload = {"carriers": [{"code": "hepsijet", "tiers": [
        {"min": 0, "max": 2, "price": "45.00"}, {"min": 2, "max": 0, "price": "65.00"}]}]}

    result = await service.estimate([
        {"orderId": 1, "carrier": "hepsijet_hepsijet", "units": 2},
        {"orderId": 2, "carrier": "", "units": 3},          # tek taşıyıcı → o
        {"orderId": 3, "carrier": "hepsijet", "units": 0},  # desi bilinmiyor
    ])

    assert result["total"] == 4500 + 6500
    assert result["known"] == 2 and result["unknown"] == 1
    assert result["lines"][2]["price"] is None
    await service.estimate([{"orderId": 1, "carrier": "hepsijet", "units": 1}])
    assert len(api.used("bbd_shipping_rates")) == 1


async def test_performans_pencere_disina_cikinca_TARAMAYI_BIRAKIR(tmp_path: Path) -> None:
    eski = service_module._days_ago(400)
    yeni = service_module._days_ago(2)

    class Sayfali(FakeApi):
        async def bbd_shipments(self, filters: Any = None, *, page: int = 1,
                                per_page: int | None = None,
                                all_pages: bool = False) -> dict[str, Any]:
            self._record("bbd_shipments", filters, page=page, per_page=per_page,
                         all_pages=all_pages)
            gun = yeni if page == 1 else eski
            return {"items": [{"id": page * 10 + index, "status": "delivered",
                               "carrier": "hepsijet", "created_at": f"{gun}T10:00:00",
                               "delivered_at": f"{gun}T18:00:00"}
                              for index in range(2)],
                    "meta": {"currentPage": page, "lastPage": 50, "total": 100}}

    api = Sayfali()
    service, _, _ = _service(tmp_path, api=api)
    result = await service.performance(days=30)

    assert result["totals"]["total"] == 2                  # yalnız pencere içi
    sayfalar = [kwargs["page"] for kwargs in api.used("bbd_shipments")]
    assert sayfalar == [1, 2]                              # 50 sayfa taranmadı
    assert all(not kwargs["all_pages"] for kwargs in api.used("bbd_shipments"))

    await service.performance(days=30)                     # bellekten
    assert len(api.used("bbd_shipments")) == 2
    taze = await service.performance(days=30, fresh=True)
    assert len(api.used("bbd_shipments")) == 4 and taze["cached"] is False


async def test_kargoya_verilen_etiket_yeniden_basilirken_magazaya_SORULMAZ(
        tmp_path: Path) -> None:
    service, api, _ = _service(tmp_path)
    result = await service.dispatch(91, actor="Ali")
    _cleanup(result)

    sayfa = await service.build_report("labels", {"shipmentIds": [77]})
    assert sayfa["ok"] is True
    assert api.used("bbd_shipment_label") == []
    Path(sayfa["path"]).unlink(missing_ok=True)


async def test_etiket_ikinci_kez_istenince_bellekten_gelir(tmp_path: Path) -> None:
    service, api, _ = _service(tmp_path)
    api.label_bytes = {5: _bos_pdf()}
    for _ in range(2):
        sayfa = await service.build_report("labels", {"shipmentIds": [5]})
        assert sayfa["ok"] is True
        Path(sayfa["path"]).unlink(missing_ok=True)
    assert len(api.used("bbd_shipment_label")) == 1


async def test_manifesto_satirlari_SAYFADAN_tek_istekle_okunur(tmp_path: Path) -> None:
    service, api, _ = _service(tmp_path)
    satir = {"id": 5, "order_id": 91, "order_number": "S-91",
             "tracking_number": "1234567890", "status": "in_transit", "carrier": "yurtici"}
    api.shipments_payload = {"items": [satir, {**satir, "id": 6, "tracking_number": "2"}],
                             "meta": {"total": 2}}

    result = await service.build_report("manifest", {"shipmentIds": [6, 5], "page": 1,
                                                     "size": 50})

    assert result["ok"] is True and result["count"] == 2
    assert len(api.used("bbd_shipments")) == 1
    assert api.used("bbd_shipment") == []                  # tek tek okunmadı
    Path(result["path"]).unlink(missing_ok=True)


def test_tasiyici_anahtari_magazanin_kuraliyla_cozulur() -> None:
    assert shipping.carrier_key("hepsijet_hepsijet") == "hepsijet"
    assert shipping.carrier_key("geliver_SURAT_STANDART") == "surat"
    assert shipping.carrier_key("HEPSIJET") == "hepsijet"
