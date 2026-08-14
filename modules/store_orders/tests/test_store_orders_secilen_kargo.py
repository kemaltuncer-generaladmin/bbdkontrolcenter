"""Sipariş ekranı müşterinin seçtiği kargo firmasını gösterir.

BULUNAN TUTARSIZLIK (2026-08-15). Aynı soruya iki ekran iki cevap veriyordu:

    Sipariş ekranı   taşıyıcıyı GÖNDERİDEN okuyor  → henüz kargolanmamışsa BOŞ
    Kargo ekranı     taşıyıcıyı SİPARİŞTEN okuyor  → müşterinin seçtiği, hep DOLU

Canlıda 18 siparişin 18'inde sipariş ekranı boş, kargo ekranı doluydu. İkisi
de kendi içinde doğruydu ama farklı soruları yanıtlıyorlardı: "hangi firmayla
gönderildi" ile "müşteri hangisini seçti". Kullanıcı tek şey görmek istiyor:
bu paket hangi firmayla gidecek ya da gitti.

KURAL: gönderi varsa `carrier` ona ait kalır — gerçekte hangi firmayla gittiği,
müşterinin seçtiğinden daha doğru bir cevaptır. Gönderi yoksa müşterinin
seçtiğine düşülür. `carrierChosen` her hâlde doldurulur ki ekran ikisini
ayırt edebilsin (ör. "seçilen X, gönderilen Y" durumunu göstermek isterse).
"""

from __future__ import annotations

from store_orders_backend import orders as ord_


def test_secilen_firma_siparis_numarasiyla_eslenir() -> None:
    index = ord_.chosen_carriers([
        {"increment_id": "20", "id": 20, "shipping_title": "Hepsijet - Hepsijet"},
        {"increment_id": "19", "id": 19, "shipping_title": "Sürat Kargo"},
    ])
    assert index["20"] == "Hepsijet - Hepsijet"
    assert index["19"] == "Sürat Kargo"


def test_gonderi_yoksa_musterinin_sectigi_gosterilir() -> None:
    # Asıl düzeltme bu: canlıda 18 siparişin 18'i bu durumdaydı.
    rows = [{"orderNo": "20", "id": 20, "carrier": "", "carrierChosen": ""}]
    ord_.apply_chosen_carrier(rows, {"20": "Hepsijet - Hepsijet"})

    assert rows[0]["carrier"] == "Hepsijet - Hepsijet"
    assert rows[0]["carrierChosen"] == "Hepsijet - Hepsijet"


def test_gonderi_varsa_GERCEK_tasiyici_kazanir() -> None:
    # Müşteri Hepsijet seçmiş ama paket Sürat'la gitmişse, listede giden firma
    # görünmeli — fatura ve takip ona ait.
    rows = [{"orderNo": "20", "id": 20, "carrier": "Sürat Kargo", "carrierChosen": ""}]
    ord_.apply_chosen_carrier(rows, {"20": "Hepsijet - Hepsijet"})

    assert rows[0]["carrier"] == "Sürat Kargo"
    # Ama seçilen de kaybolmaz: ekran isterse farkı gösterebilir.
    assert rows[0]["carrierChosen"] == "Hepsijet - Hepsijet"


def test_eslesmeyen_siparis_bos_kalir_uydurulmaz() -> None:
    rows = [{"orderNo": "99", "id": 99, "carrier": "", "carrierChosen": ""}]
    ord_.apply_chosen_carrier(rows, {"20": "Hepsijet - Hepsijet"})

    assert rows[0]["carrier"] == ""
    assert rows[0]["carrierChosen"] == ""


def test_baslik_bos_gelen_satir_indekse_GIRMEZ() -> None:
    # Boş başlığı indekse koymak, eşleşen siparişin taşıyıcısını boşla
    # ezerdi — var olan bilgiyi silmek, hiç doldurmamaktan kötüdür.
    index = ord_.chosen_carriers([{"increment_id": "20", "shipping_title": ""}])
    assert index == {}


def test_bozuk_girdi_patlatmaz() -> None:
    assert ord_.chosen_carriers(None) == {}
    assert ord_.chosen_carriers([None, "metin", 5]) == {}
    ord_.apply_chosen_carrier(None, {})       # patlamamalı
    ord_.apply_chosen_carrier([None], {"20": "X"})


async def test_liste_secilen_firmayi_TEK_istekle_getirir() -> None:
    # Sipariş başına detay okumak 50 satırlık sayfada 50 istek ederdi.
    from pathlib import Path

    from store_orders_backend.service import OrdersService
    from store_orders_fakes import FakeApi, FakeLog, FakeStore

    api = FakeApi()
    api.bbd_order_rows = [
        {"increment_id": "BBD-1", "id": 1, "shipping_title": "Hepsijet - Hepsijet"},
    ]
    service = OrdersService(
        api=api, store=FakeStore(), log=FakeLog(),
        config={"channel": "default", "page_size": 50},
        fallback_dir=Path("/tmp/km-test-raporlar"),
    )
    await service.orders()
    assert len(api.used("bbd_orders")) == 1
