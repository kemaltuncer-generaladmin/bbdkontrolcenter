"""Kargo performansı — saf hesap. Üç kararın da testi burada."""

from __future__ import annotations

from typing import Any

from store_shipping_backend import analytics


def row(**over: Any) -> dict[str, Any]:
    base = {"carrier": "yurtici", "status": "delivered", "deliveryDays": 2, "fee": 4500,
            "collectedFee": 5000, "payer": "sender", "cod": 0, "flags": []}
    base.update(over)
    return base


def test_ortalama_teslim_suresi_yalniz_teslim_edilenden_hesaplanir() -> None:
    # KARAR 1: yolda olanı "şu ana kadar" süresiyle katmak ortalamayı her gün
    # aşağı çeker ve rakam anlamsızlaşır.
    rows = [row(deliveryDays=2), row(deliveryDays=4),
            row(status="in_transit", deliveryDays=None)]
    [summary] = analytics.carrier_performance(rows)
    assert summary["avgDays"] == 3.0
    assert summary["count"] == 3
    assert summary["inTransit"] == 1


def test_hic_teslim_edilmemis_tasiyicida_ortalama_sifir_degil_none_olur() -> None:
    [summary] = analytics.carrier_performance([row(status="in_transit", deliveryDays=None)])
    assert summary["avgDays"] is None
    assert summary["failRate"] is None


def test_teslim_edilemeyen_orani_yoldakileri_paydaya_koymaz() -> None:
    # KARAR 2: yoğun bir günün ardından oran kendiliğinden düşmemeli.
    rows = [row(), row(status="undelivered", deliveryDays=None),
            row(status="in_transit", deliveryDays=None),
            row(status="in_transit", deliveryDays=None)]
    [summary] = analytics.carrier_performance(rows)
    assert summary["settled"] == 2
    assert summary["failRate"] == 50.0


def test_alici_odemeli_gonderi_maliyete_de_tahsilata_da_girmez() -> None:
    # KARAR 3: mağaza o gönderide ne öder ne tahsil eder.
    rows = [row(fee=4500, collectedFee=5000),
            row(payer="receiver", fee=9900, collectedFee=None)]
    summary = analytics.money_summary(rows)
    assert summary["cost"] == 4500
    assert summary["collected"] == 5000
    assert summary["margin"] == 500
    assert summary["receiverPaid"] == 1


def test_tahsilat_bilgisi_olmayan_gonderi_sayilir_ve_soylenir() -> None:
    summary = analytics.money_summary([row(collectedFee=None)])
    assert summary["missingCollected"] == 1
    assert summary["margin"] == -4500          # ödendi, tahsilat bilinmiyor


def test_tahsil_edilmemis_kapida_odeme_toplami_ayri_tutulur() -> None:
    summary = analytics.money_summary([row(cod=25000, flags=["cod"]), row(cod=0)])
    assert summary["codOutstanding"] == 25000


def test_gecikme_dagilimi_yalniz_teslim_edilenleri_kovalara_atar() -> None:
    rows = [row(deliveryDays=0), row(deliveryDays=3), row(deliveryDays=9),
            row(status="in_transit", deliveryDays=None)]
    buckets = {item["label"]: item["count"] for item in analytics.delay_distribution(rows)}
    assert buckets["0–1 gün"] == 1
    assert buckets["2–3 gün"] == 1
    assert buckets["8+ gün"] == 1


def test_kpi_sayaclari_tek_gecişte_cikar() -> None:
    rows = [row(), row(status="in_transit", deliveryDays=None, flags=["late"]),
            row(status="undelivered", deliveryDays=None, flags=["address"])]
    counts = analytics.totals(rows)
    assert counts["total"] == 3
    assert counts["delivered"] == 1
    assert counts["late"] == 1
    assert counts["address"] == 1
    assert counts["deliveredRate"] == 33.3


def test_geciken_liste_en_uzun_bekleyenden_baslar() -> None:
    rows = [row(status="in_transit", deliveryDays=None, flags=["late"], idleDays=4),
            row(status="in_transit", deliveryDays=None, flags=["late"], idleDays=11),
            row()]
    late = analytics.late_rows(rows)
    assert [item["idleDays"] for item in late] == [11, 4]


def test_bos_liste_bolme_hatasi_uretmez() -> None:
    assert analytics.carrier_performance([]) == []
    assert analytics.totals([])["deliveredRate"] is None
    assert analytics.money_summary([])["margin"] == 0
