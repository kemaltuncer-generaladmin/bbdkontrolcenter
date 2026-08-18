"""Döküm hesabı — saf fonksiyonlar (`analytics.ledger` / `collection_entries`).

Buradaki ayrım kritiktir: `live()` CİRO hesaplarının kapısıdır, `ledger()` ise
OLAY listesidir. Biri diğerinin yerine geçerse ya ciro şişer ya kullanıcının
aradığı hareket kaybolur.
"""

from __future__ import annotations

from bbd_canteen_reports_backend import analytics
from canteen_reports_fakes import at, sale


def test_ledger_iptalleri_dusurmez_damgalar() -> None:
    rows = [
        sale("2026-05-04", hour=9),
        sale("2026-05-04", hour=10, reversed_at=at("2026-05-04", 11)),
    ]

    entries = analytics.ledger(rows)

    assert len(entries) == 2
    assert {row["kind"] for row in entries} == {analytics.ENTRY_SALE, analytics.ENTRY_REVERSED}


def test_live_iptalleri_dusurur() -> None:
    """Aynı girdi, ciro tarafında tek satır — iki hesabın ayrı kaldığının kanıtı."""
    rows = [
        sale("2026-05-04", hour=9),
        sale("2026-05-04", hour=10, reversed_at=at("2026-05-04", 11)),
    ]

    assert len(analytics.live(rows)) == 1
    assert len(analytics.ledger(rows)) == 2


def test_ledger_tahsilati_araya_zamanina_gore_koyar() -> None:
    rows = [sale("2026-05-04", hour=9), sale("2026-05-04", hour=17)]
    collections = analytics.collection_entries(
        {"entries": [{"studentId": "s1", "amount": 5000,
                      "createdAt": at("2026-05-04", 12), "method": "cash"}]},
    )

    entries = analytics.ledger(rows, collections)
    kinds = [row["kind"] for row in entries]

    assert kinds == [analytics.ENTRY_SALE, analytics.ENTRY_COLLECTION, analytics.ENTRY_SALE]


def test_collection_entries_alternatif_alan_adlarini_tanir() -> None:
    """Uç listeyi `items` ile de verse, tutarı `total` ile de yazsa döküm dolar."""
    rows = analytics.collection_entries(
        {"items": [{"student_id": "s1", "total": 2500, "at": at("2026-05-04", 12),
                    "source": "LINK"}]},
    )

    assert len(rows) == 1
    assert rows[0]["total"] == 2500
    assert rows[0]["method"] == "LINK"
    assert rows[0]["kind"] == analytics.ENTRY_COLLECTION


def test_collection_entries_baskasinin_tahsilatini_almaz() -> None:
    rows = analytics.collection_entries(
        {"entries": [{"studentId": "s1", "amount": 100, "createdAt": 1},
                     {"studentId": "s2", "amount": 200, "createdAt": 2}]},
        student_id="s1",
    )

    assert [row["total"] for row in rows] == [100]


def test_collection_entries_bos_yaniti_yutar() -> None:
    """Uç hiç liste vermezse döküm yalnız satışlardan oluşur; patlamaz."""
    assert analytics.collection_entries({"totalCollected": 5000, "count": 2}) == []
    assert analytics.collection_entries(None) == []
    assert analytics.collection_entries([]) == []


def test_entry_counts_turlere_gore_sayar() -> None:
    entries = analytics.ledger(
        [sale("2026-05-04", hour=9),
         sale("2026-05-04", hour=10, reversed_at=at("2026-05-04", 11))],
        analytics.collection_entries(
            {"entries": [{"studentId": "s1", "amount": 5000, "createdAt": at("2026-05-04", 12)}]},
        ),
    )

    assert analytics.entry_counts(entries) == {"sale": 1, "reversed": 1, "collection": 1}
