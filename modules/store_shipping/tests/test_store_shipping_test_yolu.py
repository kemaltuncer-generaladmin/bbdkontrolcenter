"""İki kargo yolu: Geliver (gerçek) ve Bagisto (test).

KULLANICININ KARARI: "kargo yöntemi olarak Bagisto test amaçlı olsun, otomatik
takip numarası ve kargo fişi yazdırması olsun; Geliver de gerçeği olsun."

Bu testlerin koruduğu şey iki yolun BİRBİRİNE KARIŞMAMASI. Karışma sessiz
olurdu: test yolu Geliver'a giderse para harcanır, gerçek yol Bagisto'ya
giderse gönderi hiç yola çıkmaz ama ekran "kargolandı" der. İkisi de ancak
müşteri aradığında fark edilir.

Sözleşme canlı OpenAPI şemasından alındı (2026-08-15):

    POST /api/admin/orders/{orderId}/shipments
    {"source": 1,
     "items": [{"orderItemId": 42, "inventorySourceId": 1, "quantity": 3}],
     "carrierTitle": "UPS", "trackNumber": "1Z999AA1"}
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from store_shipping_backend import shipping
from store_shipping_backend.service import ShippingService
from store_shipping_fakes import FakeApi, FakeLog, FakeStore

GEREKCE = "Test gönderisi açılıyor, akış denemesi"

#: Kalemleri KISMİ kargolanmış sipariş: 3 adetten 1'i gitmiş.
SIPARIS: dict[str, Any] = {
    "id": 91, "increment_id": "S-91", "status": "processing", "grand_total": "500.00",
    "total_qty_ordered": 3, "total_qty_invoiced": 3, "total_qty_shipped": 1,
    "customer_full_name": "Ayşe Yılmaz",
    "shipping_address": {"city": "İstanbul", "district": "Adalar", "phone": "5321234567"},
    "items": [
        {"id": 42, "sku": "A", "qty_ordered": 3, "qty_shipped": 1},
        {"id": 43, "sku": "B", "qty_ordered": 2, "qty_shipped": 2},
    ],
}


def _service(**config: Any) -> tuple[ShippingService, FakeApi, FakeStore]:
    api, store = FakeApi(), FakeStore()
    service = ShippingService(
        api=api, store=store, log=FakeLog(), notifier=None, publish=None, printer=None,
        config={"channel": "default", "locale": "tr", "idle_days": 3, **config},
        fallback_dir=Path("/tmp/km-test-kargo"),
    )
    api.order_by_id = {91: dict(SIPARIS)}
    return service, api, store


# ==================================================== iki yol birbirine karışmaz

async def test_test_yolu_geliver_ucuna_HIC_gitmez() -> None:
    # Karışma sessiz olurdu: "test" diye basılan düğme para harcardı.
    service, api, _ = _service()
    result = await service.create_shipment(
        91, carrier="", packages=1, desi_value=0, weight=0, payer="sender", cod=0,
        note="", reason=GEREKCE, actor="Ali", dry_run=True, provider="bagisto")

    assert result["ok"] is True
    assert result["provider"] == "bagisto" and result["test"] is True
    assert api.used("create_shipment")            # Bagisto ucu
    assert api.used("bbd_create_shipment") == []  # Geliver ucu HİÇ çağrılmadı


async def test_gercek_yol_bagisto_ucuna_gitmez() -> None:
    service, api, _ = _service()
    await service.create_shipment(
        91, carrier="yurtici", packages=1, desi_value=2, weight=1, payer="sender", cod=0,
        note="", reason=GEREKCE, actor="Ali", dry_run=True, provider="geliver")

    assert api.used("bbd_create_shipment")
    assert api.used("create_shipment") == []


async def test_varsayilan_yol_GERCEK_olandir() -> None:
    # Test yolunu varsayılan yapmak, bir gün birinin gerçek sandığı gönderinin
    # hiç yola çıkmamasına yol açardı. Sessiz yanlış > gürültülü yanlış.
    service, api, _ = _service()
    await service.create_shipment(
        91, carrier="yurtici", packages=1, desi_value=2, weight=1, payer="sender", cod=0,
        note="", reason=GEREKCE, actor="Ali", dry_run=True)

    assert api.used("bbd_create_shipment")
    assert api.used("create_shipment") == []


async def test_ayardaki_yol_kullanilir_istek_belirtmezse() -> None:
    service, api, _ = _service(provider="bagisto")
    await service.create_shipment(
        91, carrier="", packages=1, desi_value=0, weight=0, payer="sender", cod=0,
        note="", reason=GEREKCE, actor="Ali", dry_run=True)

    assert api.used("create_shipment")


async def test_taninmayan_yol_reddedilir_sessizce_gercege_dusmez() -> None:
    # Yazım hatası olan bir yol adı gerçek yola düşerse PARA HARCAR.
    service, api, _ = _service()
    result = await service.create_shipment(
        91, carrier="", packages=1, desi_value=0, weight=0, payer="sender", cod=0,
        note="", reason=GEREKCE, actor="Ali", dry_run=True, provider="bagsto")

    assert result["ok"] is False
    assert api.used("create_shipment") == [] and api.used("bbd_create_shipment") == []


# ============================================================ gövde ve adetler

async def test_govde_canli_sozlesmeye_uyar() -> None:
    service, api, _ = _service()
    await service.create_shipment(
        91, carrier="", packages=1, desi_value=0, weight=0, payer="sender", cod=0,
        note="", reason=GEREKCE, actor="Ali", dry_run=True, provider="bagisto")

    body = api.used("create_shipment")[0]["payload"]
    assert set(body) == {"source", "items", "carrierTitle", "trackNumber"}
    assert body["source"] == 1
    assert body["items"] == [{"orderItemId": 42, "inventorySourceId": 1, "quantity": 2}]


async def test_KALAN_adet_gonderilir_siparis_edilen_degil() -> None:
    # 3 sipariş edilmiş, 1'i kargolanmış → 2 kalmış. Sipariş edilen adedi
    # göndermek aynı kalemi ikinci kez kargolardı; müşteriye iki koli gider.
    service, api, _ = _service()
    await service.create_shipment(
        91, carrier="", packages=1, desi_value=0, weight=0, payer="sender", cod=0,
        note="", reason=GEREKCE, actor="Ali", dry_run=True, provider="bagisto")

    kalemler = api.used("create_shipment")[0]["payload"]["items"]
    assert [k["quantity"] for k in kalemler] == [2]
    # Tamamı kargolanmış kalem (43) hiç gitmez.
    assert [k["orderItemId"] for k in kalemler] == [42]


async def test_kaynak_ile_kalem_kaynagi_AYNI_olur() -> None:
    # Ayrışırsa Bagisto stoğu bir depodan düşüp gönderiyi başka depoya yazar.
    service, api, _ = _service(inventory_source_id=3)
    await service.create_shipment(
        91, carrier="", packages=1, desi_value=0, weight=0, payer="sender", cod=0,
        note="", reason=GEREKCE, actor="Ali", dry_run=True, provider="bagisto")

    body = api.used("create_shipment")[0]["payload"]
    assert body["source"] == 3
    assert all(k["inventorySourceId"] == 3 for k in body["items"])


async def test_kargolanacak_kalem_kalmamissa_istek_HIC_gitmez() -> None:
    service, api, _ = _service()
    api.order_by_id = {91: {**SIPARIS, "total_qty_shipped": 5,
                            "items": [{"id": 42, "qty_ordered": 3, "qty_shipped": 3}]}}
    result = await service.create_shipment(
        91, carrier="", packages=1, desi_value=0, weight=0, payer="sender", cod=0,
        note="", reason=GEREKCE, actor="Ali", dry_run=True, provider="bagisto")

    assert result["ok"] is False
    assert api.used("create_shipment") == []


# =========================================================== takip numarası

async def test_takip_numarasi_TEST_onekiyle_baslar() -> None:
    # Gören herkes (müşteri dâhil) bunun deneme olduğunu anlamalı. Sessiz bir
    # sahte numara, günün birinde gerçek sanılıp kargo aranmasına yol açar.
    service, api, _ = _service()
    result = await service.create_shipment(
        91, carrier="", packages=1, desi_value=0, weight=0, payer="sender", cod=0,
        note="", reason=GEREKCE, actor="Ali", dry_run=True, provider="bagisto")

    assert result["trackNumber"].startswith("TEST-")
    assert shipping.is_test_track(result["trackNumber"])
    assert api.used("create_shipment")[0]["payload"]["trackNumber"] == result["trackNumber"]


def test_takip_numarasi_RASTGELE_DEGIL() -> None:
    # Rastgele numara, aynı isteğin iki kez gönderildiğini GİZLERDİ. Sabit
    # numarayla çift kayıt gözle görülür.
    a = shipping.test_track_number(20, when="2026-08-15", suffix=1)
    b = shipping.test_track_number(20, when="2026-08-15", suffix=1)
    assert a == b == "TEST-20-20260815-1"
    assert shipping.test_track_number(20, when="2026-08-15", suffix=2) != a


def test_gercek_takip_numarasi_test_sayilmaz() -> None:
    assert shipping.is_test_track("1Z999AA1") is False
    assert shipping.is_test_track("") is False


# ================================================================== ayakta kalma

async def test_test_yolunda_magaza_dusunce_ekran_cokmez() -> None:
    service, api, _ = _service()
    api.fail.add("create_shipment")
    result = await service.create_shipment(
        91, carrier="", packages=1, desi_value=0, weight=0, payer="sender", cod=0,
        note="", reason=GEREKCE, actor="Ali", dry_run=True, provider="bagisto")

    assert result["ok"] is False and result["error"]


async def test_test_gonderisi_denetime_AYRI_eylemle_yazilir() -> None:
    # Gerçek ve test gönderisi denetim defterinde ayrışmalı; yoksa "bu gönderi
    # gerçekten yola çıktı mı" sorusu geriye dönük cevaplanamaz.
    service, _, store = _service()
    await service.create_shipment(
        91, carrier="", packages=1, desi_value=0, weight=0, payer="sender", cod=0,
        note="", reason=GEREKCE, actor="Ali", dry_run=True, provider="bagisto")

    eylemler = [row["action"] for row in store.audit]
    assert "create_test_shipment" in eylemler
    assert "create_shipment" not in eylemler


async def test_test_gonderisi_para_harcanmadigini_SOYLER() -> None:
    service, _, _ = _service()
    result = await service.create_shipment(
        91, carrier="", packages=1, desi_value=0, weight=0, payer="sender", cod=0,
        note="", reason=GEREKCE, actor="Ali", dry_run=True, provider="bagisto")

    assert any("para harcanmadı" in line for line in result["warnings"])


# ==================================================================== fiş

async def test_fis_uretilir_ve_TEST_oldugunu_soyler() -> None:
    # Fiş ETİKET DEĞİLDİR. Gerçek etiketin taklidi basılsaydı, bir gün biri
    # onu koliye yapıştırıp kargoya verirdi.
    service, _, _ = _service()
    result = await service.build_report("receipt", {
        "orderId": 91, "trackNumber": "TEST-91-20260815-1", "carrier": "BBD Test Kargo"})

    assert result["ok"] is True, result.get("error")
    assert result["test"] is True
    assert result["trackNumber"] == "TEST-91-20260815-1"
    assert result["name"].startswith("magaza-kargo-fis-")
    assert Path(result["path"]).exists()
    assert Path(result["path"]).stat().st_size > 0


async def test_fis_dosyasi_0600_yazilir() -> None:
    # Müşteri adı ve telefonu taşıyor; rapor klasöründeki her belge gibi
    # yalnız kullanıcıya okunur olmalı.
    service, _, _ = _service()
    result = await service.build_report("receipt", {"orderId": 91, "trackNumber": "T-1"})
    assert oct(Path(result["path"]).stat().st_mode)[-3:] == "600"


async def test_gonderi_belirtilmezse_fis_basilmaz() -> None:
    service, _, _ = _service()
    result = await service.build_report("receipt", {})
    assert result["ok"] is False


async def test_bilinmeyen_belge_turu_reddedilir() -> None:
    service, _, _ = _service()
    assert (await service.build_report("etiketcik", {}))["ok"] is False
