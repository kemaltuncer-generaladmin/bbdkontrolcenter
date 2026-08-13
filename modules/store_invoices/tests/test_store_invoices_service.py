"""Fatura servisi — iş kuralları. Ağa çıkmaz; `store.api` taklit edilir."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from store_invoices_backend import ledger
from store_invoices_backend.service import BULK_ISSUE_CAP, InvoiceByOrder, InvoicesService
from store_invoices_fakes import (
    FakeApi,
    FakeBus,
    FakeLog,
    FakeStore,
    invoice_raw,
    order_raw,
    shipment_raw,
)

GEREKCE = "mali müşavir talebiyle düzeltildi"


def _service(api: FakeApi | None = None, store: FakeStore | None = None,
             bus: FakeBus | None = None,
             **config: Any) -> tuple[InvoicesService, FakeApi, FakeStore]:
    api = api or FakeApi()
    store = store or FakeStore()
    service = InvoicesService(
        api=api, store=store, log=FakeLog(), config={"page_size": 50, **config},
        publish=bus, fallback_dir=Path("/tmp/km-test-raporlar"),
    )
    return service, api, store


# ============================================================ K7 — ayakta kalma

async def test_magaza_dusunce_ekran_ayakta_kalir() -> None:
    service, api, _ = _service()
    api.fail.add("invoices")
    result = await service.invoices()
    assert result["ok"] is True             # uç patlamaz
    assert result["connected"] is False
    assert result["items"] == []
    assert "patladı" in result["error"]
    # Yasal uyarı bağlantı olmasa da gider: ekranın bandı her zaman dolu.
    assert result["notice"] == ledger.LEGAL_NOTICE


async def test_yerel_esleme_okunamazsa_liste_yine_gelir() -> None:
    # Yerel tablo çökse bile mağaza verisi ekranda kalmalı; yalnız "eşlenmedi"
    # görünür. Eşleme uğruna listeyi kaybetmek K7 ihlali olurdu.
    service, api, store = _service()
    api.invoice_payload = {"items": [invoice_raw()], "meta": {"total": 1}}
    store.read_broken = True
    result = await service.invoices()
    assert result["ok"] is True
    assert len(result["items"]) == 1
    assert result["items"][0]["legalMatched"] is False


async def test_denetim_izi_yazilamazsa_is_durmaz() -> None:
    service, _, store = _service()
    store.broken = True
    result = await service.send_copy(7, reason=GEREKCE, actor="Kemal", dry_run=True)
    assert result["ok"] is True


# ================================================= sunucu tarafı sayfalama

async def test_liste_sunucudan_sayfali_gelir_tam_liste_cekilmez() -> None:
    service, api, _ = _service()
    api.invoice_payload = {"items": [invoice_raw()],
                           "meta": {"total": 6120, "currentPage": 4, "perPage": 50,
                                    "lastPage": 123}}
    result = await service.invoices(page=4)
    assert result["total"] == 6120
    assert result["pages"] == 123
    assert api.used("invoices")[0]["all_pages"] is False
    assert api.used("invoices")[0]["page"] == 4


async def test_yok_sayilan_suzgecler_magazaya_hic_gonderilmez() -> None:
    # KUSUR KAYDI: `search` ve `grand_total_from/to` geçide gönderiliyordu.
    # Canlıda denendi: 16 faturanın 16'sı geri geliyor, yani Laravel bu
    # parametreleri sessizce yok sayıyor. Gönderip "süzdüm" saymak, tüm
    # dönemi gösteren listeyi süzülmüş sanmak demekti.
    service, api, _ = _service()
    await service.invoices(q="ayşe", min_total=12_345, max_total=100_000,
                           state="paid", start="2026-08-01", end="2026-08-31")
    filters = api.args_of("invoices")[0][0]
    assert set(filters) == {"state", "date_from", "date_to"}
    for ignored in ledger.IGNORED_FILTERS:
        assert ignored not in filters


async def test_arama_ve_tutar_yerel_uygulanir_ve_ekrana_soylenir() -> None:
    service, api, _ = _service()
    api.invoice_payload = {"items": [invoice_raw(1, total="120.00"),
                                     invoice_raw(2, total="900.00")],
                           "meta": {"total": 2}}
    result = await service.invoices(min_total=50_000)
    assert [row["id"] for row in result["items"]] == [2]
    assert result["hiddenByLocal"] == 1
    assert "tutar aralığı" in result["localFilters"]
    # Toplam MAĞAZANIN rakamıdır; yerel eleme onu değiştirmez.
    assert result["total"] == 2


async def test_arama_kimse_uymadiginda_bos_liste_dondurur() -> None:
    service, api, _ = _service()
    api.invoice_payload = {"items": [invoice_raw(1)], "meta": {"total": 1}}
    result = await service.invoices(q="bulunmayan müşteri")
    assert result["items"] == []
    assert result["localFilters"] == ["arama"]


async def test_uygulanmayan_durum_suzgeci_ekrana_bildirilir() -> None:
    service, api, _ = _service()
    api.invoice_payload = {"items": [invoice_raw(1, state="paid"),
                                     invoice_raw(2, state="pending")], "meta": {}}
    result = await service.invoices(state="paid")
    assert result["stateFilter"] is False        # panel uyarı çizer


# ============================================== yerel süzgeç dürüstlüğü

async def test_yasal_no_suzgeci_yerel_ve_kac_satir_elendigi_soylenir() -> None:
    service, api, store = _service()
    api.invoice_payload = {"items": [invoice_raw(1), invoice_raw(2)], "meta": {"total": 2}}
    store.legal[1] = {"invoice_id": 1, "series": "A2026", "number": 1,
                      "legal_no": "A2026000000001", "issued_at": "2026-08-01", "note": ""}
    result = await service.invoices(unmatched=True)
    assert [row["id"] for row in result["items"]] == [2]
    assert result["hiddenByLocal"] == 1


# ============================================================ yasal numara

async def test_yasal_numara_eslemesi_magazaya_yazmaz() -> None:
    service, api, store = _service()
    result = await service.save_legal_no(7, series="a2026", number=145, reason=GEREKCE,
                                         actor="Kemal")
    assert result["ok"] is True
    assert result["legalNo"] == "A2026000000145"   # seri kodu büyük harfe çekilir
    assert store.legal[7]["series"] == "A2026"
    # Mağazaya TEK BİR istek bile gitmez: bu eşleme tamamen yereldir.
    assert api.calls == []


async def test_ayni_numara_iki_faturaya_eslenemez() -> None:
    service, _, _ = _service()
    await service.save_legal_no(7, series="A2026", number=145, reason=GEREKCE, actor="Kemal")
    result = await service.save_legal_no(8, series="A2026", number=145, reason=GEREKCE,
                                         actor="Kemal")
    assert result["ok"] is False
    assert "zaten eşlenmiş" in result["error"]


async def test_ayni_faturanin_numarasi_guncellenebilir() -> None:
    service, _, store = _service()
    await service.save_legal_no(7, series="A2026", number=145, reason=GEREKCE, actor="Kemal")
    result = await service.save_legal_no(7, series="A2026", number=145,
                                         legal_no="A2026/145", reason=GEREKCE, actor="Kemal")
    assert result["ok"] is True
    assert store.legal[7]["legal_no"] == "A2026/145"


async def test_yasal_numara_gerekce_ister_ve_denetime_yazilir() -> None:
    service, _, store = _service()
    kisa = await service.save_legal_no(7, series="A2026", number=1, reason="kısa", actor="K")
    assert kisa["ok"] is False
    await service.save_legal_no(7, series="A2026", number=1, reason=GEREKCE, actor="Kemal")
    assert store.audit[-1]["action"] == "save_legal_no"
    assert store.audit[-1]["reason"] == GEREKCE


async def test_seri_basamak_sayisi_numara_biciminde_kullanilir() -> None:
    service, _, store = _service()
    await service.save_series(code="A2026", label="", start_no=1, pad=4, year_reset=True,
                              is_default=True, note="", actor="Kemal", reason=GEREKCE)
    result = await service.save_legal_no(7, series="A2026", number=145, reason=GEREKCE,
                                         actor="Kemal")
    assert result["legalNo"] == "A20260145"
    assert store.series["A2026"]["pad"] == 4


# ================================================================== seriler

async def test_seri_ekrani_numara_bosluklarini_bildirir() -> None:
    service, _, _store = _service()
    await service.save_series(code="A2026", label="Satış", start_no=1, pad=9, year_reset=True,
                              is_default=True, note="", actor="Kemal", reason=GEREKCE)
    for number in (143, 144, 148):
        await service.save_legal_no(number, series="A2026", number=number, reason=GEREKCE,
                                    actor="Kemal")
    result = await service.series()
    seri = result["items"][0]
    assert seri["gaps"] == [{"from": 145, "to": 147, "count": 3}]
    assert seri["gapMessage"] == "A2026 serisinde 145-147 eksik."
    assert seri["nextNumber"] == 149


async def test_tanimsiz_serideki_kayit_gizlenmez() -> None:
    service, _, _ = _service()
    await service.save_legal_no(1, series="YANLIS", number=5, reason=GEREKCE, actor="Kemal")
    result = await service.series()
    assert result["items"] == []
    assert result["orphans"][0]["code"] == "YANLIS"


async def test_varsayilan_seri_tek_kalir() -> None:
    service, _, store = _service()
    await service.save_series(code="A2026", label="", start_no=1, pad=9, year_reset=True,
                              is_default=True, note="", actor="K", reason=GEREKCE)
    await service.save_series(code="B2026", label="", start_no=1, pad=9, year_reset=True,
                              is_default=True, note="", actor="K", reason=GEREKCE)
    assert store.series["A2026"]["is_default"] == 0
    assert store.series["B2026"]["is_default"] == 1


async def test_seri_yokken_ayardaki_kod_onerilir() -> None:
    service, _, _ = _service(default_series="A2026")
    result = await service.series()
    assert result["defaultSeries"] == "A2026"


# =========================================================== toplu fatura

async def test_toplu_fatura_gerekce_ister() -> None:
    service, api, _ = _service()
    result = await service.issue([1, 2], reason="kısa", actor="Kemal")
    assert result["ok"] is False
    assert api.calls == []


async def test_toplu_fatura_tavani_asilamaz() -> None:
    service, api, _ = _service()
    result = await service.issue(list(range(1, BULK_ISSUE_CAP + 2)), reason=GEREKCE,
                                 actor="Kemal")
    assert result["ok"] is False
    assert "en çok" in result["error"]
    assert api.calls == []


async def test_bir_siparis_patlarsa_gerisi_surer_ve_sonuc_satir_satir_doner() -> None:
    service, api, store = _service()

    original = api.create_invoice
    calls: list[int] = []

    async def flaky(order_id: int, **kwargs: Any) -> dict[str, Any]:
        calls.append(order_id)
        if order_id == 2:
            raise RuntimeError("sipariş kilitli")
        return await original(order_id, **kwargs)

    api.create_invoice = flaky  # type: ignore[method-assign]
    result = await service.issue([1, 2, 3], reason=GEREKCE, actor="Kemal", dry_run=False)
    assert calls == [1, 2, 3]                 # biri patladı, gerisi sürdü
    assert result["created"] == 2
    assert result["failed"] == 1
    assert result["ok"] is False
    hata = next(item for item in result["results"] if not item["ok"])
    assert hata["orderId"] == 2
    assert "kilitli" in hata["error"]
    assert any(row["result"] == "hata" for row in store.audit)


async def test_kuru_provada_olay_yayinlanmaz() -> None:
    bus = FakeBus()
    service, _, _ = _service(bus=bus)
    await service.issue([1], reason=GEREKCE, actor="Kemal", dry_run=True)
    assert bus.events == []

    await service.issue([1], reason=GEREKCE, actor="Kemal", dry_run=False)
    assert bus.events[0][0] == "store.invoice.issued"
    assert bus.events[0][1]["orderId"] == 1


async def test_olay_yayinlanamazsa_fatura_kesme_bozulmaz() -> None:
    bus = FakeBus(broken=True)
    service, _, _ = _service(bus=bus)
    result = await service.issue([1], reason=GEREKCE, actor="Kemal", dry_run=False)
    assert result["ok"] is True


# ============================================================ durum yazma

async def test_yazilamayan_durum_reddedilir() -> None:
    service, api, _ = _service()
    result = await service.set_state([1], state="refunded", reason=GEREKCE, actor="Kemal")
    assert result["ok"] is False
    assert "yazılamaz" in result["error"]
    assert api.calls == []


async def test_durum_yazmasi_gecide_kuru_prova_ile_gider() -> None:
    service, api, _ = _service()
    result = await service.set_state([1, 2], state="paid", reason=GEREKCE, actor="Kemal")
    assert result["ok"] is True
    assert api.used("update_invoice_status")[0]["dry_run"] is True
    assert api.used("update_invoice_status")[0]["reason"] == GEREKCE


async def test_fatura_secilmezse_hicbir_istek_cikmaz() -> None:
    service, api, _ = _service()
    assert (await service.set_state([], state="paid", reason=GEREKCE, actor="K"))["ok"] is False
    assert api.calls == []


# ============================================================= byOrder yeteneği

async def test_by_order_yanlis_siparisin_faturasini_dondurmez() -> None:
    # Sipariş süzgeci yok sayılırsa mağaza tüm faturaları döner; yerel eleme
    # yapılmasaydı sipariş ekranı başkasının faturasını gösterirdi.
    service, api, _ = _service()
    api.invoice_payload = {"items": [invoice_raw(1, order_id=3), invoice_raw(2, order_id=9)],
                           "meta": {}}
    result = await service.by_order(3)
    assert [row["id"] for row in result["items"]] == [1]
    assert result["count"] == 1


async def test_by_order_siparis_numarasi_yoksa_istek_cikmaz() -> None:
    service, api, _ = _service()
    result = await service.by_order(0)
    assert result["ok"] is False
    assert api.calls == []


async def test_yetenek_yalniz_okuma_yuzeyi_acar() -> None:
    service, _, _ = _service()
    provider = InvoiceByOrder(service)
    assert hasattr(provider, "by_order")
    # Yazma metotları yeteneğe SIZMAZ (K3).
    assert not hasattr(provider, "issue")
    assert not hasattr(provider, "save_legal_no")
    assert not hasattr(provider, "set_state")


# ============================================================== faturasızlar

async def test_faturasi_olan_siparis_fatura_listesinden_capraz_elenir() -> None:
    # EN CİDDİ KUSUR BUYDU. Canlı sipariş LİSTESİ `invoices` dizisini hiç
    # taşımıyor; satırdaki diziye bakan kod faturası kesilmiş 6 siparişin
    # 6'sını da "faturasız" gösteriyordu ve kullanıcı hepsine ikinci,
    # GERİ ALINAMAZ fatura keserdi. Çapraz denetim fatura listesinden yapılır.
    service, api, _ = _service()
    api.order_payload = {"items": [order_raw(1), order_raw(2)], "meta": {"total": 2}}
    api.invoice_payload = {"items": [invoice_raw(50, order_id=2)], "meta": {"total": 1}}
    result = await service.uninvoiced()
    assert [row["id"] for row in result["items"]] == [1]
    assert result["scanned"] == 2
    assert result["verified"] is True
    assert result["invoicedSkipped"] == 1
    # Tarama en eski siparişin gününden başlar: fatura siparişten önce kesilemez.
    assert api.used("invoices")[0]["all_pages"] is True
    assert api.args_of("invoices")[0][0]["date_from"] == "2026-08-01"


async def test_faturalanamaz_durumdaki_siparis_aday_gosterilmez() -> None:
    service, api, _ = _service()
    api.order_payload = {"items": [order_raw(1, status="canceled"),
                                   order_raw(2, status="closed"),
                                   order_raw(3, status="pending")], "meta": {"total": 3}}
    result = await service.uninvoiced()
    assert [row["id"] for row in result["items"]] == [3]


async def test_capraz_denetim_yapilamazsa_liste_dogrulanmamis_isaretlenir() -> None:
    # Sessizce "hepsi faturasız" demektense uyarmak doğrudur.
    service, api, _ = _service()
    api.order_payload = {"items": [order_raw(1)], "meta": {"total": 1}}
    api.fail.add("invoices")
    result = await service.uninvoiced()
    assert result["ok"] is True
    assert result["verified"] is False
    assert result["verifyError"] != ""


async def test_kesme_aninda_faturasi_cikan_siparis_atlanir() -> None:
    # SON KAPI: aday listesi bayat olabilir, kesilen fatura silinemez.
    service, api, store = _service()
    api.orders_by_id[2] = {"id": 2, "invoices": [{"id": 9, "incrementId": "9"}]}
    result = await service.issue([1, 2], reason=GEREKCE, actor="Kemal", dry_run=False)
    assert [args[0] for args in api.args_of("create_invoice")] == [1]
    atlanan = next(item for item in result["results"] if item["orderId"] == 2)
    assert atlanan["ok"] is False
    assert "zaten var" in atlanan["error"]
    assert result["created"] == 1
    assert result["failed"] == 1
    assert any(row["result"] == "atlandı" for row in store.audit)


async def test_siparis_detayi_okunamazsa_kesme_engellenmez() -> None:
    # Mağaza yavaşlarsa işi tamamen durdurmak, koruma değil arıza olurdu.
    service, api, _ = _service()
    api.fail.add("order")
    result = await service.issue([1], reason=GEREKCE, actor="Kemal", dry_run=True)
    assert result["created"] == 1


# ============================================================== irsaliyeler

async def test_irsaliye_araması_yerel_yapilir_ve_soylenir() -> None:
    service, api, _ = _service()
    api.shipment_payload = {"items": [shipment_raw(8, order_id=12),
                                      shipment_raw(9, order_id=13)],
                            "meta": {"total": 2}}
    api.shipment_payload["items"][1]["carrierTitle"] = "Yurtiçi"
    result = await service.shipments(q="aras")
    assert [row["id"] for row in result["items"]] == [8]
    assert result["localFilters"] == ["arama"]
    assert "search" not in api.args_of("shipments")[0][0]


# ================================================================== rapor

async def test_bilinmeyen_rapor_reddedilir() -> None:
    service, _, _ = _service()
    assert (await service.build_report("uydurma", {}))["ok"] is False


async def test_donem_icmali_tarih_araligi_zorunlu() -> None:
    service, api, _ = _service()
    result = await service.build_report("period", {"start": "", "end": "2026-08-31"})
    assert result["ok"] is False
    assert api.calls == []


async def test_birlesik_pdf_tavani_asilamaz() -> None:
    service, api, _ = _service()
    result = await service.build_report("combined", {"invoiceIds": list(range(200))})
    assert result["ok"] is False
    assert api.calls == []


async def test_bos_secimle_pdf_uretilmez() -> None:
    service, api, _ = _service()
    assert (await service.build_report("combined", {"invoiceIds": []}))["ok"] is False
    assert (await service.build_report("delivery", {"shipmentIds": []}))["ok"] is False
    assert api.calls == []


async def test_rapor_klasoru_disindaki_dosya_basilmaz(tmp_path: Path) -> None:
    class Printer:
        async def print_file(self, path: Path, **kwargs: Any) -> dict[str, Any]:
            raise AssertionError("bu dosya asla basılmamalıydı")

        async def status(self) -> dict[str, Any]:
            return {"ready": True}

    api, store = FakeApi(), FakeStore()
    service = InvoicesService(api=api, store=store, log=FakeLog(), config={},
                              printer=Printer(), fallback_dir=tmp_path / "raporlar")
    kotu = tmp_path / "gizli.pdf"
    kotu.write_bytes(b"%PDF")
    result = await service.print_report(str(kotu))
    assert result["ok"] is False
    assert "rapor klasöründe değil" in result["error"]


async def test_yazici_yoksa_ekran_bunu_soyler() -> None:
    service, _, _ = _service()
    assert (await service.printer_status())["ready"] is False
    assert (await service.print_report("/tmp/x.pdf"))["ok"] is False


# ============================================================== denetim izi

async def test_denetim_izi_gerekceyi_ve_ayrintiyi_saklar() -> None:
    service, _, store = _service()
    await service.issue([5], reason=GEREKCE, actor="Kemal", dry_run=True)
    kayit = next(row for row in store.audit if row["action"] == "issue_invoices")
    assert kayit["reason"] == GEREKCE
    assert kayit["actor"] == "Kemal"
    assert json.loads(kayit["detail"])["orders"] == [5]

    okunan = await service.audit(limit=10)
    assert okunan["ok"] is True
    assert any(item["action"] == "issue_invoice" for item in okunan["items"])
