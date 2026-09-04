"""ELLE TAKİP GİRİŞİ — etiket dışarıdan alınmışken siparişi kapatan üçüncü yol.

KULLANICININ DURUMU (04.09.2026, canlı): Geliver taslağı açıldı, teklifler
geldi, etiket SATIN ALINAMADI. Personel Geliver'ın kendi panelinden HepsiJET
etiketi aldı ve elinde gerçek bir barkod kaldı (`GLV31161794989`). Kontrol
Merkezi'nde bu numarayı siparişe yazacak bir yol yoktu; geriye iki kötü
seçenek kalıyordu — Bagisto paneline elle girmek (denetim defteri boş kalır,
ekran siparişi hâlâ "kargoya hazır" gösterir) ya da sistemden İKİNCİ bir
etiket satın almak (ödenmiş etiketin üstüne para harcamak).

Bu testlerin koruduğu şey ÜÇ YOLUN BİRBİRİNE KARIŞMAMASI ve elle girilen
numaranın uydurulmuş bir numaraya dönüşmemesi.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from store_shipping_backend.service import ShippingService
from store_shipping_fakes import FakeApi, FakeLog, FakeStore

#: Canlıdaki gerçek numara — etiketin üstündeki barkod.
TAKIP = "GLV31161794989"

SIPARIS: dict[str, Any] = {
    "id": 26, "increment_id": "26", "status": "processing", "grand_total": "1869.00",
    "total_qty_ordered": 3, "total_qty_invoiced": 3, "total_qty_shipped": 0,
    "customer_full_name": "İpek Genç",
    "shippingTitle": "Kargo - Standart Kargo",
    "shipping_address": {"city": "Kocaeli", "district": "Başiskele", "phone": "5325459459"},
    "items": [
        {"id": 71, "sku": "A", "qty_ordered": 2, "qty_shipped": 0},
        {"id": 72, "sku": "B", "qty_ordered": 1, "qty_shipped": 0},
    ],
}


def _service(**config: Any) -> tuple[ShippingService, FakeApi, FakeStore]:
    api, store = FakeApi(), FakeStore()
    service = ShippingService(
        api=api, store=store, log=FakeLog(), notifier=None, publish=None, printer=None,
        config={"channel": "default", "locale": "tr", "idle_days": 3, **config},
        fallback_dir=Path("/tmp/km-test-kargo"),
    )
    api.order_by_id = {26: dict(SIPARIS)}
    return service, api, store


# ============================================================ para harcamaz

async def test_elle_giris_geliver_ucuna_HIC_gitmez() -> None:
    """Bu yolun tek satırlık sözü: etiket zaten alınmış, PARA HARCANMAZ."""
    service, api, _ = _service()
    result = await service.manual_shipment(
        26, carrier="hepsijet", tracking_no=TAKIP, reason="", actor="Ali", dry_run=False)

    assert result["ok"] is True
    assert api.used("bbd_create_shipment") == []      # Geliver taslağı açılmadı
    assert api.used("bbd_dispatch_order") == []       # zincir çalışmadı
    assert api.used("bbd_purchase_shipment") == []    # etiket satın alınmadı
    assert api.used("create_shipment")                # yalnız Bagisto kaydı


async def test_numara_uydurulmaz_kullanicinin_girdigi_yazilir() -> None:
    """Test yolu numarayı ÜRETİR; bu yol kullanıcınınkini AYNEN yazar."""
    service, api, _ = _service()
    result = await service.manual_shipment(
        26, carrier="hepsijet", tracking_no=f"  {TAKIP} ", reason="", actor="Ali",
        dry_run=False)

    assert result["trackNumber"] == TAKIP
    gonderilen = api.used("create_shipment")[0]["payload"]
    assert gonderilen["trackNumber"] == TAKIP
    assert gonderilen["carrierTitle"] == "HepsiJET"


async def test_tasiyici_bos_birakilirsa_siparisteki_firma_yazilir() -> None:
    # Uydurulmuş bir firma adı müşteriyi yanlış şubeye yollar; boş bırakmak
    # "siparişte ne yazıyorsa o" demektir.
    service, api, _ = _service()
    await service.manual_shipment(26, carrier="", tracking_no=TAKIP, reason="",
                                  actor="Ali", dry_run=False)

    assert api.used("create_shipment")[0]["payload"]["carrierTitle"] == "Kargo - Standart Kargo"


# =============================================================== kapılar

async def test_kisa_numara_reddedilir() -> None:
    service, api, _ = _service()
    result = await service.manual_shipment(26, carrier="hepsijet", tracking_no="GLV1",
                                           reason="", actor="Ali", dry_run=False)

    assert result["ok"] is False
    assert "en az" in result["error"]
    assert api.used("create_shipment") == []          # mağazaya HİÇ çıkılmadı


async def test_TEST_onekli_numara_elle_girilemez() -> None:
    """`TEST-` öneki "bu deneme" demektir ve müşteri de öyle okur.

    Gerçek bir gönderiye o öneki yazmak, kargolanmış bir paketi günün birinde
    "deneme" sanıp aramaya yol açardı.
    """
    service, api, _ = _service()
    result = await service.manual_shipment(26, carrier="hepsijet",
                                           tracking_no="TEST-26-20260904-1",
                                           reason="", actor="Ali", dry_run=False)

    assert result["ok"] is False
    assert "TEST-" in result["error"]
    assert api.used("create_shipment") == []


async def test_ayni_numara_ikinci_kez_yazilmaz() -> None:
    """Mükerrer koruması SİPARİŞTEN okunur, yerel defterden değil.

    Numara Bagisto paneline elle de girilmiş olabilir ve o kayıt bizim
    defterimizde yoktur. İkinci kez yazmak tek paketi iki gönderi gösterir ve
    stoğu ikinci kez düşerdi.
    """
    service, api, _ = _service()
    api.order_by_id[26]["shipments"] = [{"id": 17, "trackNumber": TAKIP}]

    result = await service.manual_shipment(26, carrier="hepsijet", tracking_no=TAKIP,
                                           reason="", actor="Ali", dry_run=False)

    assert result["ok"] is False
    assert result["already"] is True
    assert result["shipmentId"] == 17
    assert api.used("create_shipment") == []


async def test_kargolanacak_kalem_kalmadiysa_yazilmaz() -> None:
    service, api, _ = _service()
    api.order_by_id[26] = {**SIPARIS, "total_qty_shipped": 3,
                           "items": [{"id": 71, "qty_ordered": 2, "qty_shipped": 2},
                                     {"id": 72, "qty_ordered": 1, "qty_shipped": 1}]}

    result = await service.manual_shipment(26, carrier="hepsijet", tracking_no=TAKIP,
                                           reason="", actor="Ali", dry_run=False)

    assert result["ok"] is False
    assert api.used("create_shipment") == []


# ============================================================ denetim izi

async def test_gerekce_bos_birakilsa_da_defter_dolu_kalir() -> None:
    """Ekran bu yolda gerekçe SORMUYOR; boşluğu servis dolduruyor.

    Mağazanın yazma kapısı gerekçesiz isteği reddediyor (`require_reason`);
    ekrana soru sordurmak yerine metin burada üretilir.
    """
    service, api, store = _service()
    await service.manual_shipment(26, carrier="hepsijet", tracking_no=TAKIP,
                                  reason="", actor="Ali", dry_run=False)

    gonderilen = api.used("create_shipment")[0]
    assert TAKIP in gonderilen["reason"]
    assert len(gonderilen["reason"]) >= 10

    satirlar = [row for row in store.audit if row["action"] == "create_manual_shipment"]
    # İSTEK GİTMEDEN ÖNCE "denendi" yazılır: zaman aşımına uğrayan bir yazma
    # uzakta uygulanmış olabilir.
    assert [row["result"] for row in satirlar] == ["denendi", "ok"]
