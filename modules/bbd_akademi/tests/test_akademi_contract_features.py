"""Sözleşme şalterleri (`backend/core/contract_features.py`).

Şalter AÇIKSA alanın sözleşmede karşılığı olmalı: vekil gövdeyi doğrulamadan
geçirir, sözleşmede olmayan alan Akademi'de 422 olurdu. Şalterin KAPALI olması
her zaman güvenlidir (alan gönderilmez); bu yüzden sözleşmeye alan girdiğinde
test kırılmaz — şalteri açan commit bu testten geçer.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
from modules.bbd_akademi.backend.core import contract_features
from modules.bbd_akademi.tests.akademi_fakes import auth

MODULE = Path(__file__).resolve().parents[1]
SPEC = Path(os.environ.get("AKADEMI_OPENAPI")
            or MODULE.parents[3] / "bbd-akademi" / "docs" / "api" / "openapi.yaml")


def _spec() -> dict[str, Any]:
    loader = importlib.util.spec_from_file_location("gen_allowlist_cf",
                                                    MODULE / "tools" / "gen_allowlist.py")
    assert loader and loader.loader
    module = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(module)
    spec: dict[str, Any] = module.load_spec(Path(os.environ["AKADEMI_OPENAPI"])
                                            if os.environ.get("AKADEMI_OPENAPI") else None)
    return spec


def _resolve(spec: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in node:
        name = str(node["$ref"]).rsplit("/", 1)[-1]
        node = spec["components"]["schemas"][name]
    return node


def _properties(spec: dict[str, Any], node: dict[str, Any]) -> set[str]:
    node = _resolve(spec, node)
    names = set((node.get("properties") or {}).keys())
    for part in node.get("allOf") or []:
        names |= _properties(spec, part)
    return names


def _request_schema(spec: dict[str, Any], operation_id: str) -> dict[str, Any]:
    for item in spec["paths"].values():
        for operation in item.values():
            if isinstance(operation, dict) and operation.get("operationId") == operation_id:
                content = operation["requestBody"]["content"]["application/json"]
                return _resolve(spec, content["schema"])
    raise AssertionError(f"{operation_id} sözleşmede yok")


@pytest.mark.skipif(not SPEC.exists(), reason="Akademi sözleşmesi bu makinede yok")
def test_acik_salterin_sozlesmede_karsiligi_var() -> None:
    spec = _spec()
    body = _request_schema(spec, "admin_syncStaff")
    row = _properties(spec, _resolve(spec, _resolve(spec, body["properties"]["items"])["items"]))
    # Çözücü boşa dönmüyor: bugünkü alanlar bulunuyor.
    assert {"kmUserId", "displayName", "phone", "roleKeys"} <= row
    calendar = {operation_id: _properties(spec, _request_schema(spec, operation_id))
                for operation_id in ("admin_createCalendarEvent", "admin_updateCalendarEvent")}
    assert all({"kind", "title", "startDate"} <= fields for fields in calendar.values())

    if contract_features.STAFF_ANNOUNCE_NAME:
        assert "announceName" in row, (
            "STAFF_ANNOUNCE_NAME açık ama StaffSyncRequest.items[] announceName taşımıyor")
    if contract_features.CALENDAR_EVENT_BELL:
        for operation_id, fields in calendar.items():
            assert "bell" in fields, (
                f"CALENDAR_EVENT_BELL açık ama {operation_id} gövdesi `bell` taşımıyor")


async def test_durum_ucu_salterleri_bildirir(client: httpx.AsyncClient) -> None:
    data = (await client.get("/api/bbd_akademi/km/status", headers=auth("viewer"))).json()
    assert data["contractFeatures"] == {
        "staffAnnounceName": contract_features.STAFF_ANNOUNCE_NAME,
        "calendarEventBell": contract_features.CALENDAR_EVENT_BELL,
    }
