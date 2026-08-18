"""Öğrenci dökümü — ARALIKTAKİ TÜM İŞLEMLER.

Bu dosyanın sorduğu tek soru şu: kullanıcı bir aralık seçtiğinde o öğrencinin
o aralıktaki hareketlerinin TAMAMI dönüyor mu? Beş ayrı yerde sessiz kırpma
vardı; her biri buradaki bir testle kapatılıyor:

* satır sayısı sınırı yok (ekran 200, karne PDF'i 120 satır kırpıyordu),
* iptaller dökümde damgalı görünür ama hiçbir ÖZETE girmez,
* tahsilatlar da dökümdedir (ayrı uçtan gelir; yoksa "tüm işlemler" eksiktir),
* aralık 400 günde kırpılırsa kullanıcıya söylenir,
* bir gün kantinin satır sınırını doldurursa kullanıcıya söylenir.
"""

from __future__ import annotations

from typing import Any

from bbd_canteen_reports_backend import analytics
from bbd_canteen_reports_backend.service import ReportService
from canteen_reports_fakes import FakeCanteen, FakeLog, FakeStore, at, sale


def build(canteen: FakeCanteen) -> ReportService:
    return ReportService(canteen=canteen, store=FakeStore(), log=FakeLog(), config={})


# ------------------------------------------------- aralıktaki tüm işlemler

async def test_aralikta_tum_islemler_doner_kirpma_yok() -> None:
    """Üç yüz işlem varsa üç yüzü de gelir; 200'de kesilmez."""
    rows = [sale("2026-05-04", hour=8, minute=index % 60, total=100 + index)
            for index in range(300)]
    service = build(FakeCanteen(rows))

    detail = await service.student_detail("s1", "2026-05-04", "2026-05-04")

    assert len(detail["transactions"]) == 300
    assert detail["counts"]["sale"] == 300


async def test_dokum_yeniden_eskiye_siralanir() -> None:
    service = build(FakeCanteen([
        sale("2026-05-04", hour=9), sale("2026-05-04", hour=15), sale("2026-05-04", hour=12),
    ]))

    detail = await service.student_detail("s1", "2026-05-04", "2026-05-04")

    stamps = [row["createdAt"] for row in detail["transactions"]]
    assert stamps == sorted(stamps, reverse=True)


async def test_baska_ogrencinin_islemi_dokume_sizmaz() -> None:
    service = build(FakeCanteen([
        sale("2026-05-04", student="s1"), sale("2026-05-04", student="s2"),
    ]))

    detail = await service.student_detail("s1", "2026-05-04", "2026-05-04")

    assert {row["studentId"] for row in detail["transactions"]} == {"s1"}


# ------------------------------------------------------------- iptaller

async def test_iptaller_damgali_gelir_ama_ozete_girmez() -> None:
    """İptal dökümde görünür (olay yaşandı) ama harcamaya YAZILMAZ."""
    service = build(FakeCanteen([
        sale("2026-05-04", hour=9, total=1000),
        sale("2026-05-04", hour=10, total=2500, reversed_at=at("2026-05-04", 11),
             reason="yanlış ürün"),
    ]))

    detail = await service.student_detail("s1", "2026-05-04", "2026-05-04")
    kinds = [row["kind"] for row in detail["transactions"]]

    # Döküm ham listedir: iki satır da var, biri iptal damgalı.
    assert len(detail["transactions"]) == 2
    assert kinds.count(analytics.ENTRY_REVERSED) == 1
    assert kinds.count(analytics.ENTRY_SALE) == 1
    assert detail["counts"]["reversed"] == 1

    # Özet ve ürün kırılımı yalnız geçerli satıştan hesaplanır.
    assert detail["summary"]["total"] == 1000
    assert detail["summary"]["count"] == 1
    assert sum(row["total"] for row in detail["products"]) == 1000
    assert sum(row["total"] for row in detail["byDay"]) == 1000


async def test_iptal_gerekcesi_dokum_satirinda_kalir() -> None:
    service = build(FakeCanteen([
        sale("2026-05-04", total=2500, reversed_at=at("2026-05-04", 11), reason="yanlış ürün"),
    ]))

    detail = await service.student_detail("s1", "2026-05-04", "2026-05-04")

    assert detail["transactions"][0]["reversedReason"] == "yanlış ürün"


async def test_iptal_donem_cirosuna_yazilmaz() -> None:
    """Genel rapor `live()` üzerinden gider — bu ayrım korunmalı."""
    canteen = FakeCanteen([
        sale("2026-05-04", total=1000),
        sale("2026-05-04", hour=11, total=4000, reversed_at=at("2026-05-04", 12)),
    ])
    service = build(canteen)

    report = await service.report("2026-05-04", "2026-05-04", compare_previous=False)

    assert report["overview"]["total"] == 1000
    assert report["overview"]["reversedCount"] == 1
    assert report["overview"]["reversedTotal"] == 4000


# ------------------------------------------------------------- tahsilat

async def test_tahsilat_satirlari_dokumde_gorunur() -> None:
    """CREDIT satırları ayrı uçtan gelir; birleşmezse 'tüm işlemler' eksiktir."""
    canteen = FakeCanteen(
        [sale("2026-05-04", total=1000)],
        collections={"totalCollected": 5000, "count": 1, "entries": [
            {"studentId": "s1", "amount": 5000, "createdAt": at("2026-05-04", 16),
             "method": "cash", "reference": "t-1"},
        ]},
    )
    service = build(canteen)

    detail = await service.student_detail("s1", "2026-05-04", "2026-05-04")
    collected = [row for row in detail["transactions"]
                 if row["kind"] == analytics.ENTRY_COLLECTION]

    assert len(detail["transactions"]) == 2
    assert len(collected) == 1
    assert collected[0]["total"] == 5000
    assert collected[0]["method"] == "cash"
    assert detail["counts"]["collection"] == 1

    # Tahsilat harcama DEĞİLDİR: özete ve ürün kırılımına girmez.
    assert detail["summary"]["total"] == 1000
    assert sum(row["total"] for row in detail["products"]) == 1000


async def test_tahsilat_yalniz_o_ogrenci_icin_istenir() -> None:
    canteen = FakeCanteen([sale("2026-05-04")], collections={"entries": []})
    service = build(canteen)

    await service.student_detail("s1", "2026-05-04", "2026-05-04")
    asked = [params for name, params in canteen.calls if name == "collections"]

    assert asked and asked[0]["studentId"] == "s1"


async def test_tahsilat_ucu_dusse_de_dokum_gelir() -> None:
    """K7: tahsilat okunamazsa satışlar yine gösterilir, ama SESSİZ KALINMAZ."""
    canteen = FakeCanteen([sale("2026-05-04")])
    canteen.fail.add("collections")
    service = build(canteen)

    detail = await service.student_detail("s1", "2026-05-04", "2026-05-04")

    assert len(detail["transactions"]) == 1
    assert any("Tahsilat" in warning for warning in detail["meta"]["warnings"])


# ------------------------------------------------- gereksiz ikinci çekim

async def test_ogrenci_dokumu_araligi_yalnizca_bir_kez_ceker() -> None:
    """`compare_previous` bu yolda çalışmaz: önceki dönem boşuna okunmaz."""
    canteen = FakeCanteen([sale("2026-05-04")], collections={"entries": []})
    service = build(canteen)

    await service.student_detail("s1", "2026-05-01", "2026-05-07")

    assert canteen.count("transactions") == 1


async def test_genel_rapor_karsilastirma_kapaliyken_tek_cekim_yapar() -> None:
    canteen = FakeCanteen([sale("2026-05-04")])
    service = build(canteen)

    await service.report("2026-05-01", "2026-05-07", compare_previous=False)
    assert canteen.count("transactions") == 1

    await service.report("2026-05-01", "2026-05-07", compare_previous=True)
    assert canteen.count("transactions") == 3  # 1 + (güncel + önceki dönem)


# ------------------------------------------------------ eksik veri uyarısı

async def test_gun_satir_sinirini_doldurunca_uyari_yuzeye_cikar() -> None:
    """Tek gün sınıra dayanırsa bölünecek daha küçük dilim yok — kayıp gerçektir."""
    canteen = FakeCanteen([sale("2026-05-04", hour=8, minute=index) for index in range(10)])
    service = build(canteen)
    service.MAX_ROWS = 3

    transactions, meta = await service.transactions("2026-05-04", "2026-05-04")

    assert meta["truncated"] is True
    assert meta["cappedDays"] == ["2026-05-04"]
    assert any("sınır" in warning for warning in meta["warnings"])
    assert len(transactions) == 3


async def test_kirpma_uyarisi_ogrenci_dokumune_tasinir() -> None:
    canteen = FakeCanteen([sale("2026-05-04", hour=8, minute=index) for index in range(10)],
                          collections={"entries": []})
    service = build(canteen)
    service.MAX_ROWS = 3

    detail = await service.student_detail("s1", "2026-05-04", "2026-05-04")

    assert detail["meta"]["truncated"] is True
    assert detail["meta"]["warnings"]


async def test_kirpma_uyarisi_genel_rapor_metasinda() -> None:
    canteen = FakeCanteen([sale("2026-05-04", hour=8, minute=index) for index in range(10)])
    service = build(canteen)
    service.MAX_ROWS = 3

    report = await service.report("2026-05-04", "2026-05-04", compare_previous=False)

    assert report["meta"]["warnings"]


async def test_sinira_dayanmayan_gun_uyari_uretmez() -> None:
    canteen = FakeCanteen([sale("2026-05-04")])
    service = build(canteen)

    _, meta = await service.transactions("2026-05-04", "2026-05-04")

    assert meta["truncated"] is False
    assert meta["warnings"] == []


async def test_uzun_aralik_kirpildigi_soylenir() -> None:
    """400 günü aşan aralık sessizce kesilmez; kaç günün dışarıda kaldığı yazılır."""
    service = build(FakeCanteen([sale("2024-01-05")]))

    _, meta = await service.transactions("2024-01-01", "2026-01-01")

    assert meta["days"] == 401
    assert meta["truncated"] is True
    assert any("gün" in warning and "DIŞARIDA" in warning for warning in meta["warnings"])


# --------------------------------------------------------------- karne PDF

def test_karne_pdfi_dokumu_kirpmaz(monkeypatch: Any) -> None:
    """PDF şablonuna kaç satır gittiğini görürüz; `reportlab` çağrılmaz."""
    from bbd_canteen_reports_backend import service as service_module

    captured: dict[str, Any] = {}

    def spy(**kwargs: Any) -> bytes:
        captured.update(kwargs)
        return b"%PDF-sahte"

    monkeypatch.setattr(service_module, "build_pdf", spy)
    service = build(FakeCanteen())
    detail = {
        "kantinId": "s1",
        "summary": {"name": "Ayşe", "total": 1000, "count": 1, "units": 1,
                    "average": 1000, "balance": 0},
        "products": [],
        "counts": {"sale": 200, "reversed": 1, "collection": 1},
        "transactions": [
            *[{"kind": "sale", "createdAt": at("2026-05-04", 8, index % 60), "total": 100,
               "items": [{"qty": 1, "name": "Süt"}]} for index in range(200)],
            {"kind": "reversed", "createdAt": at("2026-05-04", 9), "total": 500,
             "reversedReason": "yanlış ürün", "items": [{"qty": 1, "name": "Poğaça"}]},
            {"kind": "collection", "createdAt": at("2026-05-04", 16), "total": 5000,
             "method": "cash", "items": []},
        ],
        "meta": {"warnings": ["Bir gün sınıra dayandı."]},
    }

    service._student_pdf(detail, "2026-05-04", "2026-05-04")

    tables = [item for item in captured["sections"] if item.get("kind") == "table"]
    dokum = next(item for item in tables if "İşlem dökümü" in item["title"])
    assert len(dokum["rows"]) == 202
    assert {row[1] for row in dokum["rows"]} == {"Satış", "İptal", "Tahsilat"}

    notes = " ".join(item["text"] for item in captured["sections"]
                     if item.get("kind") == "note")
    assert "UYARI: Bir gün sınıra dayandı." in notes
