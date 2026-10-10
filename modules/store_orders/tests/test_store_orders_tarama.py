"""ORTAK TARAMA ve İSTEK BÜTÇESİ — ağa çıkmaz; `store.api` ve `store.scan` taklit edilir.

NEDEN AYRI DOSYA. Geçit mağazaya dakikada 55 istekte tutuyor ve bu pay BÜTÜN
mağaza ekranlarının ortak payı. Sipariş ekranı her yenilemede siparişlerin
tamamını tarıyor, ardından en çok 300 siparişi TEK TEK okuyordu; sayfa
değiştirmek bile taramayı tekrarlıyordu ve o sırada öteki ekranlar sırada
bekliyordu. Bu dosya o davranışın geri gelmesini engeller: sayılan şey
doğruluk kadar İSTEK SAYISIDIR.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import store_orders_backend.service as service_module
from store_orders_backend import orders as ord_
from store_orders_backend.service import DETAIL_KEY, SCAN_KEY, OrdersService
from store_orders_fakes import FakeApi, FakeLog, FakeScan, FakeStore

GEREKCE = "Tahsilat bankadan teyit edildi, fatura kesiliyor."


def _sig(order_id: int, *, day: str = "2026-08-10", status: str = "processing",
         qty: int = 2, total: int = 100) -> dict[str, Any]:
    """`GET /orders` satırı — SIĞ (canlı biçim: camelCase, fatura/gönderi yok)."""
    return {"id": order_id, "incrementId": str(order_id), "status": status,
            "customerName": f"Müşteri {order_id}", "customerEmail": f"m{order_id}@ornek.com",
            "totalQtyOrdered": qty, "grandTotal": total,
            "createdAt": f"{day} 09:30:00", "updatedAt": f"{day} 09:31:00"}


def _detay(order_id: int, **extra: Any) -> dict[str, Any]:
    """`GET /orders/{id}` — detay: koleksiyonlar BOŞ DA OLSA gelir."""
    return {**_sig(order_id), "invoices": [], "shipments": [], "refunds": [],
            "comments": [], **extra}


def _service(api: FakeApi, *, scan: FakeScan | None = None,
             **config: Any) -> OrdersService:
    service = OrdersService(api=api, store=FakeStore(), log=FakeLog(), scan=scan,
                            config={"channel": "default", "page_size": 50, **config},
                            fallback_dir=Path("/tmp/km-test-raporlar"))
    service._detail_pace = 0          # testte bekleme yok; üretimde istekler arası 2 sn
    return service


def _tam_liste_cagrilari(api: FakeApi, name: str) -> int:
    return len([kw for kw in api.used(name) if kw.get("all_pages")])


# ===================================================== bir tarama, iki uç

async def test_liste_ve_sayac_TEK_taramayi_paylasir_siparis_basina_istek_yok() -> None:
    api = FakeApi({}, shallow=[_sig(1), _sig(2, status="pending"), _sig(3)])
    scan = FakeScan()
    service = _service(api, scan=scan)

    liste = await service.orders(filters={"chip": "processing"})
    sayac = await service.overview(filters={"chip": "processing"})

    assert [row["id"] for row in liste["items"]] == [1, 3]
    assert sayac["counts"]["processing"] == 2
    assert sayac["counts"]["pending"] == 1
    assert scan.loads == [SCAN_KEY]                     # ikinci uç taramayı TEKRARLAMAZ
    assert _tam_liste_cagrilari(api, "orders") == 1
    assert api.args_of("order") == []                    # sipariş başına istek YOK


async def test_sayfa_ve_cip_degisimi_taramayi_tekrarlamaz() -> None:
    api = FakeApi({}, shallow=[_sig(index) for index in range(1, 8)])
    scan = FakeScan()
    service = _service(api, scan=scan)
    await service.orders(filters={"chip": "processing"}, page=1, size=3)
    await service.orders(filters={"chip": "processing"}, page=2, size=3)
    await service.orders(filters={"chip": "today", "q": "Müşteri 4"})
    await service.overview(filters={"status": "pending"})
    assert scan.loads == [SCAN_KEY]
    assert _tam_liste_cagrilari(api, "orders") == 1


async def test_tarama_bes_LISTE_okur_ve_yeniden_eskiye_ister() -> None:
    api = FakeApi({}, shallow=[_sig(1)])
    await _service(api, scan=FakeScan()).overview()
    for name in ("orders", "invoices", "shipments", "refunds", "bbd_orders"):
        assert _tam_liste_cagrilari(api, name) == 1, name
    assert api.used("orders")[0]["all_pages"] is True
    assert api.args_of("orders")[0][0] == {"sort": "id", "order": "desc"}


# ============================================= çipler: gönderi listesinden

async def test_kargoda_ve_geciken_GONDERI_LISTESINDEN_sayilir() -> None:
    api = FakeApi({}, shallow=[_sig(1), _sig(2), _sig(3, status="canceled")])
    # Bagisto'nun kendi gönderi kaydı (`GET /shipments`): sipariş 1 tamamen kargolu.
    api.core_shipment_payload = {"items": [
        {"id": 8, "orderId": 1, "totalQty": 2, "carrierTitle": "Aras",
         "trackNumber": "AR123", "createdAt": "2026-08-11 10:00:00"},
    ], "meta": {"total": 1}}
    service = _service(api, scan=FakeScan(), late_days=3)
    sayac = await service.overview(filters={"today": "2026-08-20"})
    assert sayac["counts"]["shipped"] == 1
    assert sayac["counts"]["late"] == 1                  # yalnız sipariş 2 (iptal gecikmez)
    assert api.args_of("order") == []

    # Takip no listeden gelir: arama artık detay istemeden takip no'yu bulur.
    bulunan = await service.orders(filters={"q": "AR123", "today": "2026-08-20"})
    assert [row["id"] for row in bulunan["items"]] == [1]
    assert bulunan["items"][0]["carrier"] == "Aras"
    assert api.args_of("order") == []


async def test_gonderi_listesi_okunamazsa_kargoda_sayaci_BOS_kalir() -> None:
    api = FakeApi({}, shallow=[_sig(1), _sig(2)])
    api.fail.add("shipments")
    sayac = await _service(api, scan=FakeScan()).overview()
    assert sayac["connected"] is True
    assert sayac["counts"]["shipped"] is None            # "0" bir yalan olurdu
    assert sayac["counts"]["late"] is None
    assert sayac["counts"]["processing"] == 2            # diğer sayaçlar yerinde
    assert any("gönderi listesi" in item for item in sayac["warnings"])


async def test_baglanamayan_gonderi_yani_bilinmiyor_yapar() -> None:
    # Hangi siparişin olduğu okunamayan TEK kayıt bile, herhangi bir siparişi
    # "kargolanmamış" göstermeye yeter; yan bilinmiyor sayılır.
    api = FakeApi({}, shallow=[_sig(1)])
    api.core_shipment_payload = {"items": [{"id": 8, "totalQty": 2}], "meta": {"total": 1}}
    liste = await _service(api, scan=FakeScan()).orders(filters={"noShipment": True})
    assert liste["items"] == []
    assert any("bağlanamadı" in item for item in liste["warnings"])


async def test_tarama_bitmeden_sayac_ve_liste_UYDURULMAZ() -> None:
    api = FakeApi({}, shallow=[_sig(1)])
    scan = FakeScan()
    scan.running.add(SCAN_KEY)
    service = _service(api, scan=scan)

    sayac = await service.overview()
    assert sayac["ready"] is False
    assert sayac["counts"] == {}
    assert sayac["connected"] is True
    assert sayac["scan"]["state"] == "running"

    liste = await service.orders(filters={"chip": "processing"})
    assert liste["ready"] is False
    assert liste["items"] == []
    assert liste["connected"] is True
    assert api.calls == []                               # istek beklemedi, yük bindirmedi


async def test_siparis_listesi_patlarsa_ekran_ayakta_kalir() -> None:
    api = FakeApi({}, shallow=[_sig(1)])
    api.fail.add("orders")
    sayac = await _service(api, scan=FakeScan()).overview()
    assert sayac["ok"] is True
    assert sayac["connected"] is False
    assert "patladı" in sayac["error"]


# ================================================= tazelik ve bayatlatma

async def test_gercek_yazma_taramayi_bayatlatir_kuru_prova_bayatlatmaz() -> None:
    api = FakeApi({1: _detay(1)}, shallow=[_sig(1)])
    scan = FakeScan()
    service = _service(api, scan=scan)
    await service.overview()

    await service.invoice(1, items=None, reason=GEREKCE, actor="Ali", dry_run=True)
    await service.overview()
    assert scan.loads == [SCAN_KEY]

    await service.invoice(1, items=None, reason=GEREKCE, actor="Ali", dry_run=False)
    assert scan.entries[SCAN_KEY]["stale"] is True
    await service.overview()
    assert scan.loads == [SCAN_KEY, SCAN_KEY]


async def test_yenile_taramayi_tazelik_suresini_beklemeden_yeniler() -> None:
    api = FakeApi({}, shallow=[_sig(1)])
    scan = FakeScan()
    service = _service(api, scan=scan)
    await service.overview()
    await service.overview(refresh=True)
    assert scan.loads == [SCAN_KEY, SCAN_KEY]


async def test_sicak_tarama_secilen_kargoyu_ISTEK_ATMADAN_verir() -> None:
    api = FakeApi({}, shallow=[_sig(1)])
    api.bbd_order_rows = [{"increment_id": "1", "id": 1,
                           "shipping_title": "Hepsijet - Hepsijet"}]
    api.list_payload = {"items": [_sig(1)], "meta": {"total": 1}}
    service = _service(api, scan=FakeScan())
    await service.overview()                              # tarama `bbd/orders`u okudu
    api.calls.clear()

    liste = await service.orders()                        # sunucu sayfalı liste
    assert liste["items"][0]["carrier"] == "Hepsijet - Hepsijet"
    assert api.used("bbd_orders") == []


# =========================================== not / müşteri grubu: arka plan

async def test_not_suzgeci_detayi_ARKA_PLANDA_okur() -> None:
    notlu = _detay(1, comments=[{"id": 5, "comment": "Kapıda arayın"}])
    api = FakeApi({1: notlu, 2: _detay(2)}, shallow=[_sig(1), _sig(2)])
    scan = FakeScan()
    service = _service(api, scan=scan)
    liste = await service.orders(filters={"hasNote": True})
    assert DETAIL_KEY in scan.loads
    assert [row["id"] for row in liste["items"]] == [1]
    assert liste["partial"] is False

    # İkinci okuma önbellekten: aynı `updatedAt` yeniden istenmez.
    await service.orders(filters={"hasNote": True})
    assert len(api.args_of("order")) == 2


async def test_detay_okumasi_surerken_liste_beklemez_ve_eksigini_soyler() -> None:
    api = FakeApi({1: _detay(1)}, shallow=[_sig(1)])
    scan = FakeScan()
    scan.running.add(DETAIL_KEY)
    liste = await _service(api, scan=scan).orders(filters={"customerGroup": "Genel"})
    assert liste["pending"] is True
    assert liste["partial"] is True
    assert api.args_of("order") == []                    # istek yolunda detay YOK


# ========================================================= ödeme tutarı

def test_fatura_ve_iade_listesi_KESKIN_odeme_durumu_verir() -> None:
    sig = _sig(1, total=100)
    kismi = ord_.order_row(sig, sides={
        "invoices": [{"orderIncrementId": "1", "totalQty": 1, "grandTotal": 40}],
        "shipments": [], "refunds": []})
    assert kismi["paymentState"] == "partial"
    assert kismi["invoiceState"] == "partial"
    assert kismi["paymentSource"] == ord_.PAYMENT_SOURCES["lists"]

    iade = ord_.order_row(sig, sides={
        "invoices": [{"orderIncrementId": "1", "totalQty": 2, "grandTotal": 100}],
        "shipments": [], "refunds": [{"orderId": 1, "grandTotal": 100}]})
    assert iade["paymentState"] == "refunded"


def test_faturasiz_siparis_listeden_ODENMEDI_damgasi_almaz() -> None:
    # "Faturası yok" kesin; ama POS denemesi parayı çekmiş olabilir. O karar
    # kanıt katmanınındır — listeden "Ödenmedi" yazılmaz.
    row = ord_.order_row(_sig(1), sides={"invoices": [], "shipments": [], "refunds": []})
    assert row["invoiceKnown"] is True
    assert row["invoiceState"] == "none"
    assert row["paymentState"] == "unknown"


def test_adedi_okunamayan_yan_kayit_bilinmiyor_sayilir() -> None:
    row = ord_.order_row(_sig(1), sides={"invoices": None, "refunds": None,
                                         "shipments": [{"orderId": 1, "id": 3}]})
    assert row["shipKnown"] is False
    assert ord_.matches(row, {"noShipment": True}) is False


def test_tutari_okunamayan_iade_odendi_gostermez() -> None:
    # Tutarsız iade kaydı 0 sayılsaydı, parası geri verilmiş sipariş "Ödendi" kalırdı.
    row = ord_.order_row(_sig(1, total=100), sides={
        "invoices": [{"orderIncrementId": "1", "totalQty": 2, "grandTotal": 100}],
        "shipments": [], "refunds": [{"orderId": 1, "id": 7}]})
    assert row["refundKnown"] is False
    assert row["paymentState"] == "unknown"
    assert ord_.matches(row, {"partialRefund": True}) is False


# ================================================================ etiket

def test_etiket_YALNIZ_siparisin_kendi_gonderisinden_secilir() -> None:
    kayitlar = [
        {"id": 50, "order_id": 99, "label_url": "https://x/50.pdf"},   # BAŞKA sipariş
        {"id": 40, "order_id": 12, "status": "cancelled", "label_url": "https://x/40.pdf"},
        {"id": 41, "order_id": 12, "status": "created"},
        {"id": 42, "orderId": 12, "status": "created", "has_label": "1"},
        {"id": 43, "order_id": 12, "status": "created", "has_label": "0"},
    ]
    secilen, neden = ord_.label_shipment(kayitlar, 12)
    assert secilen is not None and secilen["id"] == 42
    assert neden == ""

    assert ord_.label_shipment([kayitlar[0]], 12) == (None, "Bu siparişin kargo kaydı yok.")
    yok, neden = ord_.label_shipment([kayitlar[1]], 12)
    assert yok is None and "iptal" in neden


async def test_toplu_etiket_baska_siparisin_etiketini_INDIRMEZ(tmp_path: Path) -> None:
    api = FakeApi({})
    # Süzgeç yok sayılmış gibi: mağazanın EN YENİ gönderisi başka siparişin.
    api.shipment_payload = {"items": [
        {"id": 50, "order_id": 99, "label_url": "https://x/50.pdf"},
        {"id": 42, "order_id": 12, "label_url": "https://x/42.pdf"},
    ], "meta": {}}
    service = _service(api, export_path=str(tmp_path))
    result = await service.labels([12, 77])
    assert api.args_of("bbd_shipment_label") == [(42,)]
    assert result["files"][0]["error"] == ""
    assert result["files"][1]["error"] == "Bu siparişin kargo kaydı yok."


# ============================================================ PDF iş parçacığı

async def test_pdf_olay_dongusunu_TIKAMAZ(monkeypatch: Any) -> None:
    gorulen: list[bool] = []

    def sahte_pdf(**_: Any) -> bytes:
        gorulen.append(threading.current_thread() is threading.main_thread())
        return b"%PDF-1.4"

    monkeypatch.setattr(service_module, "build_pdf", sahte_pdf)
    service = _service(FakeApi({12: _detay(12)}))
    sonuc = await service._slip_pdf(12)
    assert sonuc["ok"] is True
    assert gorulen == [False]                            # ayrı iş parçacığında üretildi


# ============================================== seçim penceresi (önizleme)

async def test_buyuk_secim_siparis_basina_istek_ATMAZ() -> None:
    sig = [_sig(index) for index in range(1, 11)]
    api = FakeApi({}, shallow=sig)
    scan = FakeScan()
    service = _service(api, scan=scan)
    await service.overview()                              # günleri tarama söyler
    api.calls.clear()

    onizleme = await service.batch_preview(kind="invoice", order_ids=list(range(1, 11)))
    assert onizleme["ok"] is True
    assert onizleme["summary"]["total"] == 10
    assert api.args_of("order") == []
    # Pencere seçimin en eskisinden BİR GÜN önce başlar (saat dilimi payı).
    assert api.args_of("orders")[0][0]["date_from"] == "2026-08-09"


async def test_kucuk_secim_tek_tek_okunur_dort_liste_cekilmez() -> None:
    kayitlar = {index: _detay(index) for index in (1, 2)}
    api = FakeApi(kayitlar, shallow=[_sig(1), _sig(2)])
    service = _service(api, scan=FakeScan())
    await service.overview()
    api.calls.clear()
    onizleme = await service.batch_preview(kind="invoice", order_ids=[1, 2])
    assert onizleme["ok"] is True
    assert len(api.args_of("order")) == 2
    assert _tam_liste_cagrilari(api, "orders") == 0


# ============================================================== CSV

async def test_csv_yalniz_SUZGECE_UYANLARIN_detayini_okur(tmp_path: Path) -> None:
    sig = [_sig(1, day="2026-08-20"), _sig(2, day="2026-08-01"), _sig(3, day="2026-08-01")]
    detay = {row["id"]: {**row, "subTotal": 80, "taxAmount": 20, "invoices": [],
                         "shipments": [], "refunds": [], "comments": []} for row in sig}
    api = FakeApi(detay, shallow=sig)
    service = _service(api, scan=FakeScan(), export_path=str(tmp_path))
    sonuc = await service.export_csv(filters={"chip": "today", "today": "2026-08-20"})
    assert sonuc["ok"] is True
    assert sonuc["rows"] == 1
    assert api.args_of("order") == [(1,)]                 # eskiden üçü de okunurdu
    assert sonuc["partial"] is False


# ============================================================== panel

def test_panel_olmayan_ekrana_yollamaz_ve_sayfalamada_sayaci_yeniden_istemez() -> None:
    panel = (Path(__file__).resolve().parents[1] / "ui" / "panel" / "index.js").read_text(
        encoding="utf-8")
    # Kontrol Merkezi'nde sanal POS / provizyon ekranı YOK; kullanıcı oraya yollanmaz.
    assert "Sanal POS ekran" not in panel
    # Sayfa değiştirmek sayaç şeridini (ve sunucudaki taramayı) yeniden istemez.
    assert "refresh({ page, size, paging: true })" in panel
    # "Yenile" taramayı tazelik süresini beklemeden yeniler.
    assert "refresh({ fresh: true })" in panel
