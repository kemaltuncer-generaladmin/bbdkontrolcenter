"""Personel (mobil yönetici erişimi): KM otoritedir, liste Akademi'ye itilir."""

from __future__ import annotations

import json

import httpx
from modules.bbd_akademi.backend import module as module_entry
from modules.bbd_akademi.backend import tasks
from modules.bbd_akademi.backend.staff import StaffService
from modules.bbd_akademi.tests.akademi_fakes import Akademi, auth

STAFF = "/api/bbd_akademi/km/staff"


def _ok(created: int = 1, updated: int = 0, deactivated: int = 0,
        errors: list[dict[str, str]] | None = None) -> httpx.Response:
    return httpx.Response(200, json={"created": created, "updated": updated,
                                     "deactivated": deactivated, "unchanged": 0,
                                     "errors": errors or []})


async def test_ekleme_tam_listeyi_iter(client: httpx.AsyncClient, akademi: Akademi) -> None:
    akademi.reply(_ok())
    response = await client.post(STAFF, headers=auth(), json={
        "displayName": "Zeynep  Arslan", "phone": "0532 123 45 67", "roles": ["admin"],
        "kmUserId": "km-42"})
    assert response.status_code == 201, response.text
    item = response.json()["item"]
    assert item["phone"] == "5321234567" and item["displayName"] == "Zeynep Arslan"
    assert response.json()["sync"]["status"] == "ok"

    sent = akademi.last
    assert sent.method == "PUT" and sent.url.path == "/v1/admin/staff/sync"
    assert sent.headers["x-akademi-on-behalf-of"].startswith("km:user:u-1:")
    body = json.loads(akademi.bodies[-1])
    assert body == {"items": [{"kmUserId": "km-42", "displayName": "Zeynep Arslan",
                               "phone": "5321234567", "roleKeys": ["admin"], "active": True}]}

    await client.post(STAFF, headers=auth(), json={
        "displayName": "Mert Can", "phone": "905551112233", "roles": ["teacher", "admin"]})
    body = json.loads(akademi.bodies[-1])
    assert len(body["items"]) == 2
    free = next(item for item in body["items"] if item["displayName"] == "Mert Can")
    assert free["kmUserId"].startswith("staff:")
    assert free["roleKeys"] == ["admin", "teacher"]


async def test_dogrulama_ve_tekil_telefon(client: httpx.AsyncClient, akademi: Akademi) -> None:
    bad = await client.post(STAFF, headers=auth(), json={
        "displayName": "Ali Veli", "phone": "4321234567", "roles": ["admin"]})
    assert bad.status_code == 422
    assert bad.json()["error"]["details"][0]["field"] == "phone"
    assert akademi.calls == []

    akademi.reply(_ok())
    await client.post(STAFF, headers=auth(), json={
        "displayName": "Ali Veli", "phone": "5321234567", "roles": ["teacher"]})
    twin = await client.post(STAFF, headers=auth(), json={
        "displayName": "Başka Kişi", "phone": "05321234567", "roles": ["admin"]})
    assert twin.status_code == 409
    assert twin.json()["error"]["code"] == "phone_conflict"


async def test_pasiflestirme_ve_iyimser_kilit(client: httpx.AsyncClient,
                                              akademi: Akademi) -> None:
    akademi.reply(_ok())
    item = (await client.post(STAFF, headers=auth(), json={
        "displayName": "Ayla Kurt", "phone": "5551234567", "roles": ["admin"]})).json()["item"]

    changed = await client.patch(f"{STAFF}/{item['id']}", headers=auth(),
                                 json={"expectedRevision": 1, "active": False})
    assert changed.status_code == 200
    assert changed.json()["item"]["active"] is False
    assert changed.json()["item"]["revision"] == 2
    assert json.loads(akademi.bodies[-1])["items"][0]["active"] is False

    stale = await client.patch(f"{STAFF}/{item['id']}", headers=auth(),
                               json={"expectedRevision": 1, "displayName": "Ayla K."})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_conflict"


async def test_akademi_kapaliyken_kayit_yazilir_durum_basarisiz(
        client: httpx.AsyncClient, akademi: Akademi) -> None:
    akademi.down = httpx.ConnectError("refused")
    response = await client.post(STAFF, headers=auth(), json={
        "displayName": "Deniz Ak", "phone": "5329998877", "roles": ["teacher"]})
    assert response.status_code == 201
    assert response.json()["sync"]["status"] == "failed"
    assert "ulaşılamıyor" in response.json()["sync"]["error"]
    listing = (await client.get(STAFF, headers=auth())).json()
    assert len(listing["items"]) == 1
    assert listing["sync"]["status"] == "failed"

    akademi.down = None
    akademi.reply(_ok(errors=[{"kmUserId": f"staff:{listing['items'][0]['id']}",
                               "code": "phone_conflict", "message": "Telefon başka kurumda"}]))
    manual = (await client.post(f"{STAFF}/sync", headers=auth())).json()
    assert manual["sync"]["status"] == "partial"
    assert manual["sync"]["source"] == "manual"
    assert manual["items"][0]["syncError"] == {"code": "phone_conflict",
                                               "message": "Telefon başka kurumda"}


async def test_personel_izni_gerekir(client: httpx.AsyncClient, akademi: Akademi) -> None:
    assert (await client.get(STAFF, headers=auth("viewer"))).status_code == 403
    response = await client.post(STAFF, headers=auth("cashier"), json={
        "displayName": "Ali Veli", "phone": "5321234567", "roles": ["admin"]})
    assert response.status_code == 403
    assert akademi.calls == []


async def test_zamanlanmis_gorev(staff_service: StaffService, akademi: Akademi) -> None:
    module_entry._LIVE.clear()
    assert (await tasks.run_staff_sync())["ok"] is False
    module_entry._LIVE["staff"] = staff_service
    try:
        await staff_service.create("test", {"displayName": "Sena Ay", "phone": "5321112233",
                                            "roles": ["admin"]})
        akademi.reply(_ok())
        result = await tasks.run_staff_sync()
        assert result["ok"] is True
        assert result["state"]["source"] == "schedule"
        assert akademi.last.headers["x-akademi-on-behalf-of"].startswith("km:system:")
    finally:
        module_entry._LIVE.clear()
