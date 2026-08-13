"""Önbellek katmanları ve denetim izi."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from store_api_backend.audit import AuditTrail
from store_api_backend.cache import ReferenceCache, SnapshotCache
from store_api_fakes import FakeLog, FakeStore


def test_referans_onbellegi_suresi_dolunca_dusuyor() -> None:
    onbellek = ReferenceCache(ttl=900)
    onbellek.put("channels", {"items": [1]})
    assert onbellek.get("channels") == {"items": [1]}

    bayat = ReferenceCache(ttl=0)
    bayat.put("channels", {"items": [1]})
    assert bayat.get("channels") is None


def test_referans_onbellegi_onekle_dusurulur() -> None:
    onbellek = ReferenceCache()
    onbellek.put("tax_rates", 1)
    onbellek.put("tax_categories", 2)
    onbellek.put("channels", 3)
    onbellek.drop("tax")
    assert onbellek.get("tax_rates") is None
    assert onbellek.get("channels") == 3


async def test_anlik_goruntu_yazilir_ve_geri_okunur() -> None:
    depo = FakeStore()
    onbellek = SnapshotCache(depo, FakeLog(), ttl=1800)
    await onbellek.put("reference", {"parts": {"channels": [{"id": 1}]}})

    kayit = await onbellek.get("reference")
    assert kayit is not None
    assert kayit["payload"]["parts"]["channels"] == [{"id": 1}]
    assert kayit["stale"] is False


async def test_suresi_gecen_anlik_goruntu_silinmez_bayat_isaretlenir() -> None:
    depo = FakeStore()
    eski = (datetime.now(UTC) - timedelta(hours=3)).isoformat(timespec="seconds")
    depo.snapshots["reference"] = {"payload": json.dumps({"parts": {}}), "stored_at": eski}

    kayit = await SnapshotCache(depo, FakeLog(), ttl=1800).get("reference")
    assert kayit is not None
    # Mağaza erişilemezken ekranın gösterebileceği tek veri budur (K7).
    assert kayit["stale"] is True
    assert kayit["ageSeconds"] > 1800


async def test_depo_patlarsa_onbellek_gecidi_dusurmez() -> None:
    depo = FakeStore()
    depo.fail = True
    gunluk = FakeLog()
    onbellek = SnapshotCache(depo, gunluk)

    await onbellek.put("reference", {"parts": {}})
    assert await onbellek.get("reference") is None
    assert "anlık görüntü" in gunluk.text()


async def test_denetim_satiri_once_yazilir_sonuc_sonradan_islenir() -> None:
    depo = FakeStore()
    iz = AuditTrail(depo, FakeLog())
    kimlik = AuditTrail.new_request_id()

    await iz.before(request_id=kimlik, method="POST", path="/api/admin/orders/7/cancel",
                    action="cancel_order", reason="Müşteri vazgeçti, stok iade edildi",
                    actor="Kemal", dry_run=False, body={"adminToken": "12|gizli", "id": 7})

    satir = depo.audit[0]
    assert satir["result"] == ""          # sonuç henüz yok: "gönderildi mi belli değil"
    assert satir["reason"].startswith("Müşteri")
    # Gövde denetim izine MASKELİ yazılır: tablo diskte durur ve ekranda görünür.
    assert "gizli" not in satir["body"]
    assert json.loads(satir["body"])["adminToken"] == "***"

    await iz.after(kimlik, result="ok", status=200)
    assert depo.audit[0]["result"] == "ok"
    assert depo.audit[0]["status"] == 200


async def test_denetim_yazilamazsa_hata_loglanir_ama_firlatilmaz() -> None:
    depo = FakeStore()
    depo.fail = True
    gunluk = FakeLog()

    await AuditTrail(depo, gunluk).before(request_id="x", method="POST", path="/p",
                                          reason="gerekçe metni", actor="A")
    assert any(seviye == "error" for seviye, _, _ in gunluk.records)
