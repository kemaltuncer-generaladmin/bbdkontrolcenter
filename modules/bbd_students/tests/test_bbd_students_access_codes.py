"""Toplu şifre listesi ve toplu sıfırlama — `StudentService` iş kuralları.

Kantin sahte (`FakeCanteen`): ağa çıkılmaz, gerçek kod üretilmez. Amaç
servisin doğru öğrenciyi doğru listeye koyduğunu ve kısmi hatada döngünün
durmadığını görmek.
"""

from __future__ import annotations

import pytest
from bbd_students_backend import service as service_module
from bbd_students_backend.service import StudentService
from bbd_students_fakes import FakeCanteen, FakeLog, FakeStore, profile_row, student


@pytest.fixture(autouse=True)
def _no_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    # Üretimde toplu sıfırlama çağrıları arasına kantin throttle'ına
    # değmemek için bekleme konur; testte bu yalnız yavaşlatır.
    monkeypatch.setattr(service_module, "RESET_DELAY_SECONDS", 0)


def make_service(students: list[dict], profiles: list[dict] | None = None) -> StudentService:
    canteen = FakeCanteen(students)
    store = FakeStore(profiles)
    return StudentService(canteen=canteen, store=store, log=FakeLog(), config={})


async def test_kodu_olan_ve_olmayan_ogrenci_listede_ayrisir() -> None:
    students = [
        student("STU-1", "Ayşe Kaya", access_code="123456"),
        student("STU-2", "Bora Demir", access_code=""),
    ]
    service = make_service(students)

    result = await service.access_code_list(["STU-1", "STU-2"])

    assert [row["studentId"] for row in result["codes"]] == ["STU-1"]
    assert result["codes"][0]["accessCode"] == "123456"
    assert [row["studentId"] for row in result["missing"]] == ["STU-2"]


async def test_secilmeyen_ogrenci_listede_gorunmez() -> None:
    students = [
        student("STU-1", "Ayşe Kaya", access_code="111111"),
        student("STU-2", "Bora Demir", access_code="222222"),
    ]
    service = make_service(students)

    result = await service.access_code_list(["STU-1"])

    assert len(result["codes"]) == 1
    assert result["codes"][0]["studentId"] == "STU-1"


async def test_liste_akisi_hicbir_kod_uretmez() -> None:
    canteen = FakeCanteen([student("STU-1", "Ayşe Kaya", access_code="111111")])
    service = StudentService(canteen=canteen, store=FakeStore(), log=FakeLog(), config={})

    await service.access_code_list(["STU-1"])

    assert canteen.issued == []  # kod üretim ucu hiç çağrılmadı


async def test_bos_secimle_toplu_sifirlama_reddedilir() -> None:
    service = make_service([])
    with pytest.raises(ValueError):
        await service.reset_access_codes([])


async def test_toplu_sifirlama_hepsine_yeni_kod_uretir_ve_sinif_ekler() -> None:
    students = [
        student("STU-1", "Ayşe Kaya", access_code="111111"),
        student("STU-2", "Bora Demir", access_code=""),
    ]
    profiles = [profile_row("STU-1", class_name="9-A"), profile_row("STU-2", class_name="9-B")]
    service = make_service(students, profiles)

    result = await service.reset_access_codes(["STU-1", "STU-2"])

    assert result["failed"] == []
    by_id = {row["studentId"]: row for row in result["issued"]}
    assert by_id["STU-1"]["className"] == "9-A"
    assert by_id["STU-2"]["className"] == "9-B"
    # Yeni kod üretildi — eskisiyle aynı değer değil.
    assert by_id["STU-1"]["accessCode"] != "111111"


async def test_toplu_sifirlamada_kismi_hata_digerlerini_durdurmaz() -> None:
    students = [
        student("STU-1", "Ayşe Kaya"),
        student("STU-2", "Bora Demir"),
        student("STU-3", "Cem Yıldız"),
    ]
    canteen = FakeCanteen(students)
    canteen.fail_for = {"STU-2"}
    service = StudentService(canteen=canteen, store=FakeStore(), log=FakeLog(), config={})

    result = await service.reset_access_codes(["STU-1", "STU-2", "STU-3"])

    assert {row["studentId"] for row in result["issued"]} == {"STU-1", "STU-3"}
    assert [row["studentId"] for row in result["failed"]] == ["STU-2"]
    # Başarısız öğrenci için de deneme yapıldı (döngü onu atlamadı).
    assert "STU-2" in canteen.issued


async def test_sifirlanan_kod_yerel_tabloya_yazilmaz() -> None:
    store = FakeStore()
    canteen = FakeCanteen([student("STU-1", "Ayşe Kaya")])
    service = StudentService(canteen=canteen, store=store, log=FakeLog(), config={})

    await service.reset_access_codes(["STU-1"])

    assert store.written == []
