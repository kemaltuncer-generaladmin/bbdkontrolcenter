"""Pano hesabı — saf mantık. Ağ yok, DB yok."""

from __future__ import annotations

from typing import Any

from store_dashboard_backend import metrics

RAW = {
    "id": 7, "increment_id": "BBD-7", "created_at": "2026-08-12 14:35:00",
    "status": "processing", "grand_total": "1.234,50", "customer_first_name": "Ali",
    "customer_last_name": "Veli", "total_qty_ordered": 3,
    "items": [{"name": "Kalem", "sku": "KLM-1", "qty_ordered": 2, "total": "20.00"}],
}


def _row(**extra: Any) -> dict[str, Any]:
    return metrics.order_row({**RAW, **extra})


# ============================================================== para ve alan

def test_ondalik_para_kurusa_cevrilirken_bir_kurus_kaybolmaz() -> None:
    # float("1234.35") * 100 → 123434.99999; int() bir kuruş aşağı yuvarlardı.
    assert metrics.to_kurus("1234.35") == 123435
    assert metrics.to_kurus("1.234,50") == 123450
    assert metrics.to_kurus("0") == 0
    assert metrics.to_kurus("") is None
    assert metrics.to_kurus(None) is None


def test_siparis_satiri_ad_soyad_ve_saat_dahil_okunur() -> None:
    row = _row()
    assert row["number"] == "BBD-7"
    assert row["customer"] == "Ali Veli"
    assert row["total"] == 123450
    assert row["date"] == "2026-08-12"
    assert row["hour"] == 14
    assert row["statusLabel"] == "Hazırlanıyor"


def test_bilinmeyen_durum_ingilizce_kalmaz_ham_deger_gosterilir() -> None:
    assert _row(status="on_hold")["statusLabel"] == "on_hold"


def test_kalem_tasimayan_siparis_bos_kalem_listesi_verir() -> None:
    # Sıfır uydurulmaz: "en çok satan" kartı kaynağının olmadığını söyleyecek.
    assert _row(items=None)["items"] == []


# =================================================================== aralık

def test_ters_verilen_aralik_reddedilmez_cevrilir() -> None:
    span = metrics.normalize_range("2026-08-20", "2026-08-01")
    assert (span["start"], span["end"]) == ("2026-08-01", "2026-08-20")
    assert "ters" in span["note"]


def test_bozuk_tarih_bugune_duser_ekran_bos_kalmaz() -> None:
    span = metrics.normalize_range("dün", "", today="2026-08-13")
    assert span["start"] == span["end"] == "2026-08-13"
    assert span["note"]


def test_cok_uzun_aralik_tavana_kirpilir() -> None:
    span = metrics.normalize_range("2000-01-01", "2026-08-13")
    assert span["days"] == metrics.MAX_RANGE_DAYS
    assert span["end"] == "2026-08-13"


def test_onceki_donem_ayni_uzunlukta_ve_bitisik_gelir() -> None:
    previous = metrics.previous_range("2026-08-08", "2026-08-14", "previous")
    assert previous == {"start": "2026-08-01", "end": "2026-08-07"}


def test_gecen_yil_ayni_gunler_29_subatta_patlamaz() -> None:
    previous = metrics.previous_range("2024-02-29", "2024-02-29", "lastYear")
    assert previous == {"start": "2023-02-28", "end": "2023-02-28"}


def test_karsilastirma_kapaliysa_onceki_donem_bos_doner() -> None:
    assert metrics.previous_range("2026-08-01", "2026-08-07", "none") == {"start": "", "end": ""}


# =========================================== iki dönemi tek sorguya indirme

def test_bitisik_donemler_tek_araliga_birlesir() -> None:
    span = {"start": "2026-08-08", "end": "2026-08-14"}
    previous = metrics.previous_range(span["start"], span["end"], "previous")
    assert metrics.merge_ranges(span, previous) == {
        "start": "2026-08-01", "end": "2026-08-14", "days": 14}


def test_uzak_donemler_birlestirilmez() -> None:
    # Birleştirmek aradaki 11 ayı da çekerdi; tarama tavanı boşa giderdi.
    span = {"start": "2026-08-08", "end": "2026-08-14"}
    previous = metrics.previous_range(span["start"], span["end"], "lastYear")
    assert metrics.merge_ranges(span, previous) is None


def test_cok_uzun_birlesik_aralik_birlestirilmez() -> None:
    # Tavan SATIR sayısına göre işler: iki uzun dönemi tek sorguya koymak,
    # ayrı ayrı sığan satırları tek tavanın altına toplar ve eskiden
    # kesilmeyen bir taramayı keserdi.
    span = {"start": "2026-01-01", "end": "2026-12-31"}
    previous = metrics.previous_range(span["start"], span["end"], "previous")
    assert metrics.merge_ranges(span, previous) is None


def test_bos_karsilastirma_donemi_birlestirilmez() -> None:
    assert metrics.merge_ranges({"start": "2026-08-01", "end": "2026-08-07"},
                                {"start": "", "end": ""}) is None


def test_bozuk_tarih_birlestirmeyi_patlatmaz() -> None:
    assert metrics.merge_ranges({"start": "2026-08-01", "end": "2026-08-07"},
                                {"start": "dün", "end": "bugün"}) is None


# ============================================================ toplulaştırma

def _rows() -> list[dict[str, Any]]:
    return [
        _row(id=1, created_at="2026-08-10 09:00:00", grand_total="100.00", status="completed"),
        _row(id=2, created_at="2026-08-10 15:00:00", grand_total="50.00", status="pending"),
        _row(id=3, created_at="2026-08-12 15:00:00", grand_total="900.00", status="canceled"),
    ]


def test_iptal_edilen_siparis_ciroya_da_sayiya_da_girmez() -> None:
    rows = _rows()
    assert metrics.revenue(rows) == 15000
    assert len(metrics.counted(rows)) == 2
    assert metrics.average_basket(rows) == 7500


def test_gunluk_seri_yalnizca_verisi_olan_gunleri_tasir() -> None:
    # Boş günleri sunucu doldurmaz; ekran `fillDays()` ile aralığı tamamlar.
    assert metrics.daily_series(_rows()) == [{"date": "2026-08-10", "value": 15000}]


def test_durum_dagilimi_sabit_sirada_ve_sayilariyla_gelir() -> None:
    parts = metrics.status_counts(_rows())
    assert [part["key"] for part in parts] == ["pending", "completed", "canceled"]
    assert all(part["value"] == 1 for part in parts)
    assert parts[0]["label"] == "Bekliyor"


def test_saat_seridi_yirmi_dort_kutu_doner_saatsiz_siparis_sayilmaz() -> None:
    hours = metrics.hour_counts([*_rows(), _row(created_at="")])
    assert len(hours) == 24
    assert hours[9]["count"] == 1
    assert hours[15]["count"] == 2
    assert sum(hour["count"] for hour in hours) == 3


def test_kargolanmayan_gonderisi_olan_siparisi_saymaz() -> None:
    rows = [
        _row(id=1, status="processing"),
        _row(id=2, status="processing", shipments=[{"id": 9}]),
        _row(id=3, status="completed"),
    ]
    assert [row["id"] for row in metrics.awaiting_shipment(rows)] == [1]


def test_en_cok_satan_kalemlerden_adede_gore_toplanir() -> None:
    rows = [
        _row(id=1, items=[{"name": "Kalem", "sku": "KLM-1", "qty_ordered": 2, "total": "20.00"},
                          {"name": "Defter", "sku": "DFT-1", "qty_ordered": 1, "total": "30.00"}]),
        _row(id=2, items=[{"name": "Kalem", "sku": "KLM-1", "qty_ordered": 5, "total": "50.00"}]),
        # İptal edilen sipariş kalemi sayılmaz: ciroyla tutarlı olmalı.
        _row(id=3, status="canceled",
             items=[{"name": "Defter", "sku": "DFT-1", "qty_ordered": 99, "total": "990.00"}]),
    ]
    top = metrics.top_products(rows)
    assert [item["sku"] for item in top] == ["KLM-1", "DFT-1"]
    assert top[0]["qty"] == 7
    assert top[0]["total"] == 7000


def test_urun_raporu_tanimadigi_bicimde_satir_uydurmaz() -> None:
    assert metrics.report_products({"bilinmeyen": [{"x": 1}]}) == []
    rows = metrics.report_products({"products": [{"name": "Kalem", "total_qty_ordered": 4}]})
    assert rows == [{"name": "Kalem", "sku": "", "qty": 4, "total": 0}]


# ============================================================== karşılaştırma

def test_onceki_donem_sifirsa_yuzde_uydurulmaz() -> None:
    assert metrics.delta(500, 0)["percent"] is None
    assert metrics.delta(500, 0)["direction"] == "up"


def test_yuzde_farki_bir_ondalikla_hesaplanir() -> None:
    assert metrics.delta(150, 100) == {"percent": 50.0, "direction": "up", "delta": 50.0}
    assert metrics.delta(80, 100)["direction"] == "down"


def test_bilinmeyen_kpi_sifir_degil_bos_gosterilir() -> None:
    numbers = metrics.snapshot_numbers(_rows(), refunds=None, new_customers=4, out_of_stock=None)
    tiles = {tile["key"]: tile for tile in metrics.kpi_tiles(numbers, None)}
    assert tiles["outOfStock"]["value"] is None
    assert tiles["refunds"]["value"] is None
    assert tiles["customers"]["value"] == 4
    assert len(tiles) == 8


def test_delta_yalnizca_karsilastirilabilir_kutulara_konur() -> None:
    current = metrics.snapshot_numbers(_rows(), refunds=0, new_customers=2, out_of_stock=1)
    previous = metrics.snapshot_numbers(_rows()[:1], refunds=0, new_customers=1, out_of_stock=9)
    tiles = {tile["key"]: tile for tile in metrics.kpi_tiles(current, previous)}
    assert tiles["revenue"]["delta"]["percent"] == 50.0
    # Anlık sayımların (bekleyen, kargolanmayan, tükenen) geçmiş dönemi yoktur.
    assert "delta" not in tiles["pending"]
    assert "delta" not in tiles["outOfStock"]


# ====================================================================== ayar

def test_bulunmayan_ayar_anahtari_bulundu_sayilmaz() -> None:
    values = {"general.content.shop_information.shop_name": "BBD"}
    assert metrics.config_value(values, "general.content.shop_information.shop_name")["found"]
    assert not metrics.config_value(values, "general.content.seo.meta_title")["found"]


def test_anahtar_adi_degismisse_son_parcadan_bulunur() -> None:
    values = {"general.settings.shop_name": "BBD"}
    found = metrics.config_value(values, "general.content.shop_information.shop_name")
    assert found["found"] and found["key"] == "general.settings.shop_name"


def test_gerekce_on_karakterden_kisa_olamaz() -> None:
    assert metrics.reason_error("kısa")
    assert metrics.reason_error("bakım için kapatıldı") == ""


def test_tarihsiz_yedek_en_yeni_sayilmaz() -> None:
    newest = metrics.newest_backup([
        {"name": "eski.tar", "created_at": "2026-08-01 03:00:00"},
        {"name": "tarihsiz.tar"},
        {"name": "yeni.tar", "created_at": "2026-08-12 03:00:00"},
    ])
    assert newest is not None
    assert newest["name"] == "yeni.tar"
    assert metrics.backup_age_days("2026-08-12 03:00:00", today="2026-08-13") == 1
    assert metrics.backup_age_days("bilinmiyor") is None
