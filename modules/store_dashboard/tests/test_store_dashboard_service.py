"""Kontrol Paneli servisi — iş kuralları. Ağa çıkmaz; `store.api` taklit edilir."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from store_dashboard_backend.service import DashboardService
from store_dashboard_fakes import FakeApi, FakeLog, FakePrinter, FakeStore, order

RANGE = {"start": "2026-08-10", "end": "2026-08-12"}


def _service(api: FakeApi | None = None, store: FakeStore | None = None,
             printer: Any = None, **config: Any) -> tuple[DashboardService, FakeApi, FakeStore]:
    api = api or FakeApi([
        order(1, created="2026-08-10 09:00:00", total="100.00", status="completed"),
        order(2, created="2026-08-11 10:00:00", total="50.00", status="pending"),
        order(3, created="2026-08-12 11:00:00", total="900.00", status="canceled"),
    ])
    store = store or FakeStore()
    service = DashboardService(
        api=api, store=store, log=FakeLog(), printer=printer,
        config={"channel": "default", "locale": "tr", "compare": "none", **config},
        fallback_dir=Path("/tmp/km-test-raporlar"),
    )
    return service, api, store


# ============================================================ K7 — ayakta kalma

async def test_magaza_dusunce_pano_ayakta_kalir() -> None:
    service, api, _ = _service()
    api.fail.add("orders")
    result = await service.summary(**RANGE)
    assert result["ok"] is True              # uç patlamaz
    assert result["connected"] is False
    assert result["kpis"] == []
    assert "patladı" in result["error"]


async def test_bir_kart_patlarsa_digerleri_dolar() -> None:
    service, api, _ = _service()
    api.fail.add("bbd_catalog_health")       # tükenen ürün sayısı gelmiyor
    api.fail.add("refunds")
    result = await service.summary(**RANGE)
    assert result["connected"] is True
    tiles = {tile["key"]: tile for tile in result["kpis"]}
    assert tiles["revenue"]["value"] == 15000
    assert tiles["outOfStock"]["value"] is None
    assert tiles["outOfStock"]["note"]       # neden boş olduğu yazıyor
    assert tiles["refunds"]["value"] is None


async def test_bekleyen_isler_satir_satir_hata_verir() -> None:
    service, api, _ = _service()
    api.fail.add("reviews")
    api.counts["returns"] = 2
    result = await service.pending_work()
    rows = {row["key"]: row for row in result["items"]}
    assert rows["reviews"]["count"] is None and rows["reviews"]["error"]
    assert rows["returns"]["count"] == 2
    assert rows["returns"]["target"] == "store_requests"


async def test_kritik_stok_ucu_patlarsa_kart_gizlenmez_durumu_anlatir() -> None:
    # Kural: okunamayan kart YOK OLMAZ, neden okunamadığını söyler. Boş kart
    # ile "kritik stokta ürün yok" ekranda aynı görünür — biri sorun, diğeri
    # iyi haber.
    service, api, _ = _service()
    api.fail.add("dashboard_stats")
    result = await service.critical_stock()
    assert result["ok"] is True
    assert result["available"] is False
    assert result["error"]


async def test_kritik_stok_katalog_saglik_ucuna_SORULMAZ() -> None:
    """BULUNAN HATA (2026-08-14). Kart `bbd_catalog_issues`'a `low_stock` diye
    bir sorun tipi soruyordu. ÖYLE BİR TİP YOK — canlıdaki servis yalnız
    no_image · no_description · no_meta · zero_price · no_category ·
    not_indexed tanıyor, hiçbiri stokla ilgili değil.

    Kart her açılışta boş geliyordu ve kullanıcı bunu "kritik stokta ürün yok"
    diye okuyordu; oysa canlıda eşiğin altında 5 ürün vardı. Uç yayındaydı,
    SORU yanlıştı — sessiz yanlış cevabın ta kendisi.
    """
    service, api, _ = _service()
    api.stock_threshold_rows = [
        {"id": 9, "sku": "SKU-9", "name": "Matematik Soru Bankası", "total_qty": "20"},
    ]
    await service.critical_stock()
    assert api.used("bbd_catalog_issues") == []
    assert api.used("dashboard_stats") == [
        {"kind": "stock-threshold-products", "start": "", "end": ""}
    ]


async def test_kritik_stok_metin_adedi_sayiya_cevirir_ve_azdan_coga_sirlar() -> None:
    # `total_qty` canlıda METİN geliyor ("18"). Çevrilmezse sıralama alfabetik
    # olur ve "9" > "18" çıkar — en kritik ürün listenin dibine düşer, yani
    # kartın tek işi ters çalışır.
    service, api, _ = _service()
    api.stock_threshold_rows = [
        {"id": 1, "sku": "SKU-1", "name": "On sekiz", "total_qty": "18"},
        {"id": 2, "sku": "SKU-2", "name": "Dokuz", "total_qty": "9"},
        {"id": 3, "sku": "SKU-3", "name": "Yirmi", "total_qty": "20"},
    ]
    result = await service.critical_stock()
    assert [row["stock"] for row in result["items"]] == [9, 18, 20]
    assert result["total"] == 3


async def test_kritik_stok_kart_sayisi_kadar_gosterir_toplami_saklar() -> None:
    # Uç sayfalanmıyor; tamamı gelir, kart yalnız ilk N'i çizer. "5 üründen
    # 2'sini görüyorsun" diyebilmek için toplam korunmalı.
    service, api, _ = _service()
    api.stock_threshold_rows = [
        {"id": i, "sku": f"SKU-{i}", "name": f"Ürün {i}", "total_qty": str(i)}
        for i in range(1, 6)
    ]
    result = await service.critical_stock(limit=2)
    assert len(result["items"]) == 2
    assert result["total"] == 5


# ================================================================ KPI hesabı

async def test_kpilar_siparis_listesinden_hesaplanir_dashboard_stats_cagrilmaz() -> None:
    # Ekranda rakamla birlikte o rakamı üreten siparişler duruyor; iki ayrı
    # kaynak birbirini tutmazsa kullanıcı ikisine de güvenmez.
    service, api, _ = _service()
    result = await service.summary(**RANGE)
    tiles = {tile["key"]: tile for tile in result["kpis"]}
    assert tiles["revenue"]["value"] == 15000       # iptal edilen 900 TL hariç
    assert tiles["orders"]["value"] == 2
    assert tiles["basket"]["value"] == 7500
    assert not any(call == "dashboard_stats" for call, _, _ in api.calls)


async def test_karsilastirma_ikinci_donemi_tarar_ve_yuzde_uretir() -> None:
    service, api, _ = _service()
    result = await service.summary(start="2026-08-11", end="2026-08-12", compare="previous")
    assert result["previousRange"] == {"start": "2026-08-09", "end": "2026-08-10"}
    tiles = {tile["key"]: tile for tile in result["kpis"]}
    # Dönem: 50 TL · önceki dönem: 100 TL → %50 düşüş.
    assert tiles["revenue"]["delta"]["percent"] == -50.0
    assert len(api.used("orders")) == 2


async def test_karsilastirma_kapaliyken_ikinci_tarama_yapilmaz() -> None:
    service, api, _ = _service()
    await service.summary(**RANGE, compare="none")
    assert len(api.used("orders")) == 1


async def test_kanal_her_siparis_isteginde_gider() -> None:
    service, api, _ = _service()
    await service.summary(**RANGE)
    assert api.args("orders")[0][0]["channel"] == "default"


# ================================================= süzgeç yok sayılırsa (tuzak)

async def test_magaza_tarih_suzgecini_yok_sayarsa_rakam_yerelde_duzeltilir() -> None:
    api = FakeApi([
        order(1, created="2026-08-10 09:00:00", total="100.00", status="completed"),
        order(9, created="2025-01-01 09:00:00", total="999.00", status="completed"),
    ])
    api.honor_dates = False                  # Laravel bilmediği parametreyi yutar
    service, _, _ = _service(api)
    result = await service.summary(**RANGE)
    tiles = {tile["key"]: tile for tile in result["kpis"]}
    assert tiles["revenue"]["value"] == 10000
    assert any("yerelde süzüldü" in note for note in result["notes"])


async def test_tarama_tavana_dayanirsa_rakamlarin_eksik_oldugu_soylenir() -> None:
    api = FakeApi([order(1, created="2026-08-10 09:00:00", total="100.00")])
    api.truncate_orders = True
    service, _, _ = _service(api)
    result = await service.summary(**RANGE)
    assert result["truncated"] is True
    assert any("EKSİK" in note for note in result["notes"])


async def test_kalem_yoksa_en_cok_satan_urun_raporuna_duser() -> None:
    api = FakeApi([order(1, created="2026-08-10 09:00:00", total="100.00", items=[])])
    api.reporting_payload = {"products": [{"name": "Kalem", "total_qty_ordered": 12}]}
    service, _, _ = _service(api)
    result = await service.summary(**RANGE)
    assert result["topSource"] == "report"
    assert result["topProducts"][0]["name"] == "Kalem"


# =============================================================== bakım modu

async def test_bakim_modu_gerekcesiz_yazilmaz() -> None:
    service, api, _ = _service()
    result = await service.set_maintenance(enabled=True, reason="kısa", actor="Ali")
    assert result["ok"] is False
    assert not api.used("update_configuration")


async def test_bulunmayan_anahtara_bakim_modu_yazilmaz() -> None:
    # Bulunmayan anahtara yazmak vitrini kapatmaz; kullanıcı kapattığını sanır.
    service, api, _ = _service()
    api.config_payload = {"general.content.shop_information.shop_name": "BBD"}
    result = await service.set_maintenance(enabled=True, reason="bakım için kapatılıyor",
                                           actor="Ali")
    assert result["ok"] is False
    assert "bulunamadı" in result["error"]
    assert not api.used("update_configuration")


async def test_bakim_modu_bulunan_anahtara_gerekceyle_yazilir() -> None:
    service, api, store = _service()
    api.config_payload = {
        "general.content.maintenance_mode.status": 0,
        "general.content.maintenance_mode.allowed_ips": "",
    }
    result = await service.set_maintenance(enabled=True, allowed_ips="1.2.3.4",
                                           reason="sürüm geçişi için kapatıldı", actor="Ali",
                                           dry_run=False)
    assert result["ok"] is True
    written = api.used("update_configuration")[0]
    assert written["values"]["general.content.maintenance_mode.status"] == 1
    assert written["values"]["general.content.maintenance_mode.allowed_ips"] == "1.2.3.4"
    assert written["reason"] == "sürüm geçişi için kapatıldı"
    # Gerekçe YEREL denetim izine de yazılır: Bagisto "neden" alanı tutmuyor.
    assert [row["result"] for row in store.audit] == ["denendi", "ok"]


# =================================================================== ayarlar

async def test_yerel_tercih_kaydedilir_ve_sonraki_okumada_kullanilir() -> None:
    service, api, store = _service()
    result = await service.save_settings(local={"channel": "mobil", "compare": "lastYear"},
                                         reason="mobil kanala geçildi", actor="Ali",
                                         dry_run=False)
    assert result["ok"] is True
    assert store.prefs["channel"] == "mobil"
    api.calls.clear()
    await service.summary(**RANGE)
    assert api.args("orders")[0][0]["channel"] == "mobil"


async def test_bulunmayan_magaza_ayari_yazilmaz_atlandi_diye_doner() -> None:
    service, api, _ = _service()
    api.config_payload = {"general.content.shop_information.shop_name": "BBD"}
    result = await service.save_settings(
        identity={"name": "BBD Store", "email": "info@example.com"},
        reason="mağaza adı güncellendi", actor="Ali", dry_run=False)
    assert result["ok"] is True
    assert result["skipped"] == ["E-posta"]
    values = api.used("update_configuration")[0]["values"]
    assert values == {"general.content.shop_information.shop_name": "BBD Store"}


async def test_ayar_ekrani_kanal_para_ve_dil_listelerini_tasir() -> None:
    service, api, _ = _service()
    api.snapshot_payload = {"parts": {
        "channels": [{"code": "default", "name": "Varsayılan",
                      "base_currency": {"code": "TRY"}}],
        "currencies": [{"code": "TRY", "name": "Türk Lirası"}],
        "locales": [{"code": "tr", "name": "Türkçe"}],
    }, "errors": [], "stale": False}
    result = await service.settings()
    assert result["channels"][0]["currency"] == "TRY"
    assert result["locales"][0]["code"] == "tr"
    assert result["reportDir"]["path"]


async def test_ayar_bolumleri_slug_basina_bir_kez_okunur() -> None:
    # Üç grup da aynı slug altında; alan başına istek atmak kovayı harcardı.
    service, api, _ = _service()
    await service.settings()
    assert len(api.used("configuration")) == 1


# ==================================================================== rapor

async def test_rapor_klasoru_disindaki_dosya_basilmaz() -> None:
    # Serbest yol kabul etmek, `lp` ile makinedeki herhangi bir dosyayı kâğıda
    # döktürmeye açık kapı bırakırdı.
    printer = FakePrinter()
    service, _, _ = _service(printer=printer, export_path="/tmp/km-test-raporlar/pano")
    result = await service.print_report("/etc/hostname")
    assert result["ok"] is False
    assert "rapor klasöründe değil" in result["error"]
    assert printer.printed == []


async def test_yazici_yoksa_basma_ucu_anlasilir_hata_doner() -> None:
    service, _, _ = _service()
    result = await service.print_report("/etc/hostname")
    assert result["ok"] is False
    assert "Yazıcı yeteneği" in result["error"]
    assert (await service.printer_status())["ready"] is False


async def test_bilinmeyen_rapor_turu_uretilmez() -> None:
    service, _, _ = _service()
    assert (await service.build_report("yillik", {}))["ok"] is False
