"""Öğrenci ekranının veli kantin kontrolleri — `StudentService` iş kuralları.

Kantin sahtedir; ağa çıkılmaz. Kontroller kantinde durur, burada yalnız
okunur ve kantine yazılır.
"""

from __future__ import annotations

from typing import Any

import pytest
from bbd_students_backend.service import ControlsError, StudentService
from bbd_students_fakes import FakeLog, FakeStore


class CanteenError(RuntimeError):
    def __init__(self, message: str, *, status: int, reason: str) -> None:
        super().__init__(message)
        self.status = status
        self.reason = reason


class Canteen:
    def __init__(self) -> None:
        self.student_row: dict[str, Any] = {
            "id": "STU-1", "displayName": "Elif Kaya", "balance": -500,
            "spendingLimit": 20000, "isBlocked": False, "dailyLimit": None,
            "guardianBlocked": True, "restrictedProductIds": [17, 3],
            "spentToday": 1200, "controlsUpdatedAt": "2026-10-12T09:30:00+03:00",
            "accessCode": "483920",
        }
        self.patches: list[dict[str, Any]] = []
        self.product_calls = 0
        self.fail: str = ""

    async def get_student(self, student_id: str) -> dict[str, Any]:
        if self.fail == "down":
            raise CanteenError("bağlantı", status=503, reason="")
        if student_id != "STU-1":
            raise CanteenError("yok", status=404, reason="student_not_found")
        return dict(self.student_row)

    async def update_controls(self, student_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        if self.fail == "invalid":
            raise CanteenError("Bilinmeyen ürün: 99.", status=422, reason="validation_error")
        self.patches.append(dict(patch))
        self.student_row.update(patch)
        return dict(self.student_row)

    async def products(self) -> list[dict[str, Any]]:
        self.product_calls += 1
        return [{"id": 3, "name": "Tost", "price": 2500, "isActive": True},
                {"id": 17, "name": "Ayran", "price": 1000, "isActive": False},
                {"id": 5, "name": "Ayva", "price": 900, "isActive": True}]


def make() -> tuple[StudentService, Canteen]:
    canteen = Canteen()
    return StudentService(canteen=canteen, store=FakeStore(), log=FakeLog(), config={}), canteen


async def test_kontroller_okunur_kod_sizmaz_urunler_onbellekte() -> None:
    service, canteen = make()
    result = await service.canteen_controls("STU-1")
    assert result["available"] is True
    assert result["controls"] == {
        "kantinId": "STU-1", "balance": -500, "spendingLimit": 20000, "dailyLimit": None,
        "isBlocked": False, "guardianBlocked": True, "restrictedProductIds": [3, 17],
        "spentToday": 1200, "controlsUpdatedAt": "2026-10-12T09:30:00+03:00",
    }
    assert "483920" not in str(result)
    assert [item["name"] for item in result["products"]] == ["Ayva", "Tost", "Ayran"]
    await service.canteen_controls("STU-1")
    assert canteen.product_calls == 1


async def test_kantin_yoksa_ekran_ayakta() -> None:
    service, canteen = make()
    assert (await service.canteen_controls("STU-YOK"))["notFound"] is True
    canteen.fail = "down"
    result = await service.canteen_controls("STU-1")
    assert result["available"] is False and result["error"]


async def test_yalniz_gonderilen_kontrol_yazilir() -> None:
    service, canteen = make()
    result = await service.update_canteen_controls(
        "STU-1", {"dailyLimit": 15000, "guardianBlocked": False}, actor="Ayşe")
    assert canteen.patches == [{"dailyLimit": 15000, "guardianBlocked": False}]
    assert result["controls"]["guardianBlocked"] is False
    assert result["controls"]["dailyLimit"] == 15000


async def test_veli_engeli_personelce_konmaz() -> None:
    service, canteen = make()
    with pytest.raises(ControlsError):
        await service.update_canteen_controls("STU-1", {"guardianBlocked": True}, actor="x")
    with pytest.raises(ControlsError):
        await service.update_canteen_controls("STU-1", {}, actor="x")
    assert canteen.patches == []


async def test_kantin_reddi_422() -> None:
    service, canteen = make()
    canteen.fail = "invalid"
    with pytest.raises(ControlsError) as error:
        await service.update_canteen_controls("STU-1", {"restrictedProductIds": [99]}, actor="x")
    assert error.value.status == 422
