"""Personel (mobil yönetici erişimi): KM otoritedir, liste Akademi'ye itilir."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from modules.bbd_akademi.backend import contract_features, tasks
from modules.bbd_akademi.backend import module as module_entry
from modules.bbd_akademi.backend.staff import StaffService
from modules.bbd_akademi.tests.akademi_fakes import MIGRATIONS, Akademi, auth

from km_core.store.db import Store

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
                               "phone": "5321234567", "roleKeys": ["admin"], "active": True,
                               "announceName": None}]}

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


# ------------------------------------------------------------- anons adı


async def test_anons_adi_saklanir_kirpilir_ve_yuke_girer(
        client: httpx.AsyncClient, akademi: Akademi) -> None:
    akademi.reply(_ok())
    created = await client.post(STAFF, headers=auth(), json={
        "displayName": "Hüseyin Yılmaz", "phone": "5321234567", "roles": ["teacher"],
        "announceName": "  Hüseyin   hoca "})
    assert created.status_code == 201, created.text
    item = created.json()["item"]
    assert item["announceName"] == "Hüseyin hoca"

    # Şalter açık (sözleşme 2d91b96: StaffSyncRequest.items[].announceName).
    assert contract_features.STAFF_ANNOUNCE_NAME is True
    sent = json.loads(akademi.bodies[-1])["items"][0]
    assert sent["announceName"] == "Hüseyin hoca"
    assert set(sent) == {"kmUserId", "displayName", "phone", "roleKeys", "active", "announceName"}

    listing = (await client.get(STAFF, headers=auth())).json()
    assert listing["announceNameSync"] is True
    assert listing["items"][0]["announceName"] == "Hüseyin hoca"

    changed = await client.patch(f"{STAFF}/{item['id']}", headers=auth(), json={
        "expectedRevision": 1, "announceName": "Hüseyin Bey"})
    assert changed.status_code == 200
    assert changed.json()["item"]["announceName"] == "Hüseyin Bey"
    # Başka alan değişince anons adı korunur.
    kept = await client.patch(f"{STAFF}/{item['id']}", headers=auth(), json={
        "expectedRevision": 2, "note": "Matematik"})
    assert kept.json()["item"]["announceName"] == "Hüseyin Bey"
    # Boş dize anons adını temizler.
    cleared = await client.patch(f"{STAFF}/{item['id']}", headers=auth(), json={
        "expectedRevision": 3, "announceName": "   "})
    assert cleared.json()["item"]["announceName"] == ""


async def test_anons_adi_en_cok_60_karakter(client: httpx.AsyncClient,
                                            akademi: Akademi) -> None:
    exact = "a" * 60
    akademi.reply(_ok())
    ok = await client.post(STAFF, headers=auth(), json={
        "displayName": "Ali Veli", "phone": "5321234567", "roles": ["teacher"],
        "announceName": f"  {exact}  "})
    assert ok.status_code == 201
    assert ok.json()["item"]["announceName"] == exact

    calls = len(akademi.calls)
    long = await client.post(STAFF, headers=auth(), json={
        "displayName": "Can Kaya", "phone": "5329876543", "roles": ["teacher"],
        "announceName": "a" * 61})
    assert long.status_code == 422
    error = long.json()["error"]
    assert error["details"][0]["field"] == "announceName"
    assert "60" in error["message"]
    assert len(akademi.calls) == calls, "reddedilen kayıt Akademi'ye itilmemeli"

    row = ok.json()["item"]
    patched = await client.patch(f"{STAFF}/{row['id']}", headers=auth(), json={
        "expectedRevision": 1, "announceName": "b" * 61})
    assert patched.status_code == 422


async def test_salter_kapaliyken_anons_adi_yuke_girmez(
        staff_service: StaffService, monkeypatch: pytest.MonkeyPatch) -> None:
    await staff_service.create("test", {"displayName": "Zeynep Ak", "phone": "5321112233",
                                        "roles": ["teacher"], "announceName": "Zeynep hoca"})
    monkeypatch.setattr(contract_features, "STAFF_ANNOUNCE_NAME", False)
    sent = (await staff_service.payload())["items"][0]
    assert "announceName" not in sent
    assert set(sent) == {"kmUserId", "displayName", "phone", "roleKeys", "active"}


async def test_salter_acikken_anons_adi_yuke_girer(
        staff_service: StaffService, monkeypatch: pytest.MonkeyPatch) -> None:
    await staff_service.create("test", {"displayName": "Zeynep Ak", "phone": "5321112233",
                                        "roles": ["teacher"], "announceName": "Zeynep hoca"})
    await staff_service.create("test", {"displayName": "Mert Can", "phone": "5321112244",
                                        "roles": ["admin"]})
    monkeypatch.setattr(contract_features, "STAFF_ANNOUNCE_NAME", True)
    items = {item["displayName"]: item for item in (await staff_service.payload())["items"]}
    assert items["Zeynep Ak"]["announceName"] == "Zeynep hoca"
    # Boş anons adı `null` gider: Akademi çağrıda `teacherName: null` yazar.
    assert items["Mert Can"]["announceName"] is None


async def test_goc_mevcut_satirlari_bos_anons_adiyla_korur(tmp_path: Path) -> None:
    """002 ekleyicidir: 001 ile yazılmış satır göçten sonra aynen okunur."""
    database = Store(tmp_path / "eski.sqlite")
    await database.open()
    try:
        first, *rest = sorted(MIGRATIONS.glob("*.sql"))
        assert first.name.startswith("001_") and rest[0].name == "002_staff_announce_name.sql"
        await database.apply_migration("bbd_akademi", first.name,
                                       first.read_text(encoding="utf-8"))
        await database.execute(
            "INSERT INTO mod_bbd_akademi_staff (id, display_name, phone, roles, created_at, "
            "updated_at) VALUES ('s1', 'Eski Kayıt', '5320000000', 'admin', 'x', 'x')")
        for migration in rest:
            await database.apply_migration("bbd_akademi", migration.name,
                                           migration.read_text(encoding="utf-8"))
        row = await database.fetch_one("SELECT * FROM mod_bbd_akademi_staff WHERE id = 's1'")
        assert row is not None
        assert row["announce_name"] == "" and row["display_name"] == "Eski Kayıt"
    finally:
        await database.close()
