"""Sözleşme v2.2 — kendini güncelleme ve uzaktan tanılama: sync `selfUpdate`
(yalnız üç koşulla), bildirilen alanların yalnız değişince yazılması, dosya ucu
(Range, 410), tanı raporu yükleme (413, son 5), yönetim uçları ve izinleri,
yeni komutlar, panel özeti ve Uygulamalar kartının durumu."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx
import test_app_distribution as distribution_tests
from modules.bbd_tablet.backend.service import MAX_DIAGNOSTICS_TEXT, TabletService
from test_app_distribution import (
    ADMIN_AUTH,
    GAME,
    SELF_PACKAGE,
    apk,
    bundle,
    pair,
    publish,
    sync,
    upload,
)

# Fikstürler test_app_distribution'dan: depolu servis ve tel istemcisi.
service = distribution_tests.service
client = distribution_tests.client

BASE = "https://km.test/api/tablet/v1"
VIEWER = {"Authorization": "Bearer viewer"}
MANAGER = {"Authorization": "Bearer manager"}


def spy(store: Any) -> list[str]:
    """Record every write statement the store runs from now on."""
    statements: list[str] = []
    original = store.execute

    async def execute(sql: str, params: tuple[Any, ...] = ()) -> None:
        statements.append(sql)
        await original(sql, params)

    store.execute = execute
    return statements


async def row(service: TabletService, device_id: str) -> dict[str, Any]:
    found = await service.store.fetch_one(
        f"SELECT app_version_code, self_update, policy_detail FROM {service.devices} "
        "WHERE id=?", (device_id,),
    )
    assert found is not None
    return dict(found)


# ------------------------------------------------------------ sync teklifi


async def test_self_update_is_offered_only_under_the_three_conditions(
        service: TabletService, store: Any) -> None:
    device, token = await pair(service)
    idle = {"state": "idle"}
    # No self release yet: a v2.2 tablet gets the key with null.
    assert (await sync(service, token, appVersionCode=6, selfUpdate=idle))["selfUpdate"] is None
    await publish(service, apk(GAME, 45))
    assert (await sync(service, token, appVersionCode=6, selfUpdate=idle))["selfUpdate"] is None

    payload = apk(SELF_PACKAGE, 7, version_name="0.7.3")
    release = (await publish(service, payload, app_name="TabletKontrol"))["releaseId"]
    offer = (await sync(service, token, appVersionCode=6, selfUpdate=idle))["selfUpdate"]
    assert offer == {
        "releaseId": release, "versionCode": 7, "versionName": "0.7.3",
        "sizeBytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
        "url": f"{BASE}/devices/{device['id']}/self-update/{release}",
    }
    # Same or newer installed, or no installed version reported: nothing offered.
    assert (await sync(service, token, appVersionCode=7, selfUpdate=idle))["selfUpdate"] is None
    assert (await sync(service, token, appVersionCode=9, selfUpdate=idle))["selfUpdate"] is None
    assert (await sync(service, token, selfUpdate=idle))["selfUpdate"] is None
    # A tablet without the `selfUpdate` key (0.7.0/0.7.1) gets the old answer.
    old = await sync(service, token, appVersionCode=6, appsVersion=None)
    assert "selfUpdate" not in old
    assert [item["kind"] for item in old["apps"]["items"]] == ["self", "app"]

    # The catalog is in memory: the offer costs no query and the unchanged
    # report no write beyond the sync's own device update.
    report = {"appVersionCode": 6, "selfUpdate": {"state": "downloading", "releaseId": release,
                                                  "receivedBytes": 100}}
    await sync(service, token, **report)
    current = await service.device_by_token(token)
    assert current is not None
    store.reset()
    quiet = await service.sync(device=current, data=dict(report), base_url=BASE)
    assert quiet["selfUpdate"]["releaseId"] == release
    assert store.reads <= 3 and store.writes <= 1, (store.reads, store.writes)


async def test_split_self_release_is_never_offered(service: TabletService) -> None:
    _, token = await pair(service)
    base = apk(SELF_PACKAGE, 8)
    split = apk(SELF_PACKAGE, 8, split="config.tr")
    status = await upload(service, bundle({"base.apk": base, "split_config.tr.apk": split}),
                          name="tabletkontrol.apks")
    assert status["state"] == "ready", status["error"]
    await service.distribution.publish(release_id=status["releaseId"], app_name="")
    answer = await sync(service, token, appVersionCode=6, selfUpdate={"state": "idle"})
    assert answer["selfUpdate"] is None


# ------------------------------------------------------- bildirilen alanlar


async def test_reported_fields_are_stored_only_when_they_change(
        service: TabletService, store: Any) -> None:
    device, token = await pair(service)
    report = {"state": "failed", "releaseId": " r1 ", "versionCode": 23, "attempt": 2,
              "error": "install_no_result", "detail": "x" * 400,
              "nextRetryAt": "2026-10-14T13:30:00Z", "at": "2026-10-14T13:20:00Z",
              "unknown": "atılır", "receivedBytes": -5}
    await sync(service, token, appVersionCode=22, selfUpdate=report,
               policyHealth="degraded", policyDetail="  " + "p" * 400)
    stored = await row(service, device["id"])
    assert stored["app_version_code"] == 22 and stored["policy_detail"] == "p" * 300
    kept = json.loads(stored["self_update"])
    assert kept == {"state": "failed", "releaseId": "r1", "versionCode": 23, "attempt": 2,
                    "error": "install_no_result", "detail": "x" * 300,
                    "nextRetryAt": "2026-10-14T13:30:00Z", "at": "2026-10-14T13:20:00Z"}

    writes = spy(store)
    await sync(service, token, appVersionCode=22, selfUpdate=report,
               policyHealth="degraded", policyDetail="p" * 400)
    [update] = [sql for sql in writes if "UPDATE" in sql and service.devices in sql]
    assert "self_update" not in update and "app_version_code" not in update
    assert "policy_detail" not in update

    writes.clear()
    await sync(service, token, appVersionCode=23, selfUpdate={"state": "installed",
                                                              "versionCode": 23})
    [update] = [sql for sql in writes if "UPDATE" in sql and service.devices in sql]
    assert "self_update=?" in update and "app_version_code=?" in update
    # A v2.2 tablet omits `policyDetail` while healthy: the old text is cleared.
    assert "policy_detail=?" in update
    stored = await row(service, device["id"])
    assert (stored["app_version_code"], stored["policy_detail"]) == (23, None)
    assert json.loads(stored["self_update"]) == {"state": "installed", "versionCode": 23}

    # An older APK sends none of the keys: nothing is touched.
    await sync(service, token, appVersion="0.7.1", policyHealth="degraded")
    assert await row(service, device["id"]) == stored
    # An unknown state does not fail the sync; the report is dropped.
    answer = await sync(service, token, appVersionCode=23, selfUpdate={"state": "dans"})
    assert answer["selfUpdate"] is None
    assert (await row(service, device["id"]))["self_update"] is None


async def test_overview_carries_version_code_report_and_policy_detail(
        service: TabletService) -> None:
    device, token = await pair(service)
    other, _ = await pair(service, "Etüt-02")
    await sync(service, token, appVersionCode=22, policyHealth="degraded",
               policyDetail="set_user_restriction_failed:no_config_vpn",
               selfUpdate={"state": "ready", "releaseId": "r1", "versionCode": 23})
    views = {item["id"]: item for item in (await service.overview())["devices"]}
    mine = views[device["id"]]
    assert mine["app_version_code"] == 22
    assert mine["self_update"] == {"state": "ready", "releaseId": "r1", "versionCode": 23}
    assert mine["policy_detail"] == "set_user_restriction_failed:no_config_vpn"
    assert views[other["id"]]["self_update"] is None
    assert views[other["id"]]["app_version_code"] is None

    await service.revoke_device(device_id=device["id"])
    revoked = next(item for item in (await service.overview())["devices"]
                   if item["id"] == device["id"])
    assert (revoked["app_version_code"], revoked["self_update"], revoked["policy_detail"]) \
        == (None, None, None)


# ------------------------------------------------------------- komutlar


async def test_self_update_and_diagnostics_commands_are_accepted(
        service: TabletService, client: httpx.AsyncClient) -> None:
    device, token = await pair(service)
    for kind in ("self_update", "diagnostics"):
        command = await service.create_command(device_id=device["id"], command_type=kind,
                                               payload={"ignored": True}, actor="Ayşe")
        assert command["type"] == kind and command["payload"] == {}
    bulk = await service.bulk(device_ids=[device["id"]], action="command", actor="Ayşe",
                              command={"type": "diagnostics", "payload": {}})
    assert bulk["okCount"] == 1
    sent = await client.post(f"/api/bbd_tablet/devices/{device['id']}/commands",
                             headers=ADMIN_AUTH, json={"type": "self_update"})
    assert sent.status_code == 200 and sent.json()["type"] == "self_update"
    wire_bulk = await client.post("/api/bbd_tablet/devices/bulk", headers=ADMIN_AUTH, json={
        "deviceIds": [device["id"]], "action": "command",
        "command": {"type": "self_update", "payload": {}}})
    assert wire_bulk.status_code == 200 and wire_bulk.json()["okCount"] == 1
    commands = (await sync(service, token))["commands"]
    assert sorted(item["type"] for item in commands) == \
        ["diagnostics", "diagnostics", "self_update", "self_update", "self_update"]


# ------------------------------------------------------- Uygulamalar kartı


async def test_self_card_reads_v22_reports_and_old_tablets_from_the_queue(
        service: TabletService) -> None:
    payload = apk(SELF_PACKAGE, 7)
    release = (await publish(service, payload, app_name="TabletKontrol"))["releaseId"]
    tokens = {name: (await pair(service, name))[1] for name in ("A", "B", "C", "D", "E")}
    half = len(payload) // 2
    await sync(service, tokens["A"], appVersionCode=6, selfUpdate={
        "state": "downloading", "releaseId": release, "receivedBytes": half})
    await sync(service, tokens["B"], appVersionCode=6, selfUpdate={
        "state": "failed", "releaseId": release, "error": "incompatible_signature",
        "at": "2026-10-14T07:00:00Z"})
    await sync(service, tokens["C"], appVersionCode=7, selfUpdate={
        "state": "installed", "versionCode": 7})
    await sync(service, tokens["D"], appVersionCode=6, selfUpdate={
        "state": "ready", "releaseId": release})
    # E is an older APK: no report, its status comes from the queue (none yet).
    await sync(service, tokens["E"], appVersion="0.7.1")

    item = (await service.distribution.overview())["apps"][0]
    assert item["kind"] == "self"
    assert item["counts"] == {"installed": 1, "downloading": 1, "waiting": 1, "failed": 1,
                              "unknown": 1, "total": 5}
    rows = {entry["deviceName"]: entry for entry in (
        await service.distribution.app_devices(package_name=SELF_PACKAGE))["devices"]}
    assert rows["A"]["status"] == "downloading" and rows["A"]["selfState"] == "downloading"
    assert rows["A"]["progressPercent"] == half * 100 // len(payload)
    assert rows["B"]["status"] == "failed"
    assert rows["B"]["lastError"] == "incompatible_signature"
    assert rows["B"]["nextRetryAt"] is None
    assert rows["C"]["status"] == "installed" and rows["C"]["installedVersionCode"] == 7
    assert rows["D"]["status"] == "waiting" and rows["D"]["selfState"] == "ready"
    assert rows["E"]["status"] == "unknown" and rows["E"]["selfState"] is None
    # The panel's overview names the published self release for the progress.
    assert (await service.overview())["selfRelease"] == {
        "releaseId": release, "versionCode": 7, "versionName": "1.0",
        "totalBytes": len(payload)}


# ----------------------------------------------------------------- tel


async def test_wire_self_update_file_range_and_gone(
        client: httpx.AsyncClient, service: TabletService) -> None:
    created = (await client.post("/api/bbd_tablet/devices", headers=ADMIN_AUTH,
                                 json={"name": "Etüt-01"})).json()
    enrolled = (await client.post("/api/tablet/v1/devices/enroll", json={
        "enrollmentCode": created["enrollmentCode"], "deviceUuid": "uuid-0000-0001",
        "deviceName": "Etüt-01"})).json()
    device_id = enrolled["deviceId"]
    auth = {"Authorization": f"Bearer {enrolled['deviceToken']}"}
    sync_path = f"/api/tablet/v1/devices/{device_id}/sync"
    none_yet = await client.get(f"/api/tablet/v1/devices/{device_id}/self-update/yok",
                                headers=auth)
    assert none_yet.status_code == 404

    payload = apk(SELF_PACKAGE, 7, padding=100_000)
    release = (await publish(service, payload))["releaseId"]
    # 0.7.1 sends neither key: the answer has no `selfUpdate` at all.
    old = (await client.post(sync_path, headers=auth, json={"appVersion": "0.7.1"})).json()
    assert "selfUpdate" not in old
    offer = (await client.post(sync_path, headers={**auth, "CF-Visitor": '{"scheme":"https"}'},
                               json={"appVersionCode": 6,
                                     "selfUpdate": {"state": "idle"}})).json()["selfUpdate"]
    file_path = f"/api/tablet/v1/devices/{device_id}/self-update/{release}"
    assert offer["url"] == f"https://test{file_path}"

    full = await client.get(file_path, headers={**auth, "Accept-Encoding": "gzip"})
    assert full.status_code == 200 and full.content == payload
    assert full.headers["content-type"] == "application/zip"
    assert full.headers["cache-control"] == "no-store"
    assert "content-encoding" not in full.headers
    resumed = await client.get(file_path, headers={**auth, "Range": "bytes=500-"})
    assert resumed.status_code == 206 and resumed.content == payload[500:]
    foreign = await client.get(file_path.replace(device_id, "baska"), headers=auth)
    assert foreign.json()["detail"]["code"] == "FORBIDDEN_DEVICE"
    other = await client.get(file_path.replace(release, "baska-yayin"), headers=auth)
    assert other.status_code == 410 and other.json()["detail"]["code"] == "RELEASE_GONE"

    await publish(service, apk(SELF_PACKAGE, 8))
    gone = await client.get(file_path, headers=auth)
    assert gone.status_code == 410 and gone.json()["detail"]["code"] == "RELEASE_GONE"


async def test_wire_diagnostics_upload_keep_five_permissions_and_cleanup(
        client: httpx.AsyncClient, service: TabletService, clock: Any) -> None:
    device, token = await pair(service)
    auth = {"Authorization": f"Bearer {token}"}
    path = f"/api/tablet/v1/devices/{device['id']}/diagnostics"
    first = await client.post(path, headers=auth, json={
        "commandId": "c1", "appVersion": "0.7.2", "appVersionCode": 22, "text": "Rapor ğüş"})
    assert first.status_code == 200
    assert set(first.json()) == {"id", "receivedAt"}
    ids = [first.json()["id"]]
    for index in range(6):
        clock.advance(minutes=1)
        ids.append((await client.post(path, headers=auth,
                                      json={"text": f"rapor {index}"})).json()["id"])

    too_big = await client.post(path, headers=auth, json={"text": "x" * (MAX_DIAGNOSTICS_TEXT + 1)})
    assert too_big.status_code == 413
    assert "262144" in too_big.json()["error"]["message"]
    assert (await client.post(path, headers=auth, json={"commandId": "c"})).status_code == 422
    unknown = await client.post(path, headers={"Authorization": "Bearer yok"}, json={"text": "x"})
    assert unknown.json()["detail"]["code"] == "DEVICE_AUTH_INVALID"
    exact = await client.post(path, headers=auth, json={"text": "y" * MAX_DIAGNOSTICS_TEXT})
    assert exact.status_code == 200
    ids.append(exact.json()["id"])

    listing = await client.get(f"/api/bbd_tablet/devices/{device['id']}/diagnostics",
                               headers=VIEWER)
    assert listing.status_code == 200
    reports = listing.json()
    assert [item["id"] for item in reports] == list(reversed(ids[-5:]))
    assert "text" not in reports[0]
    assert reports[0]["sizeBytes"] == MAX_DIAGNOSTICS_TEXT
    assert set(reports[0]) == {"id", "deviceId", "commandId", "createdAt", "appVersion",
                               "appVersionCode", "sizeBytes"}

    text_path = f"/api/bbd_tablet/devices/{device['id']}/diagnostics/{ids[-2]}"
    assert (await client.get(text_path, headers=VIEWER)).status_code == 403
    opened = await client.get(text_path, headers=MANAGER)
    assert opened.status_code == 200 and opened.json()["text"] == "rapor 5"
    assert (await client.get(text_path[:-3] + "yok", headers=MANAGER)).status_code == 404

    # Older than 30 days: the daily cleanup in the panel's overview drops it.
    clock.advance(days=31)
    fresh = (await client.post(path, headers=auth, json={"text": "yeni"})).json()["id"]
    await service.overview()
    assert [item["id"] for item in await service.list_diagnostics(device_id=device["id"])] \
        == [fresh]
    await service.delete_device(device_id=device["id"])
    assert await service.list_diagnostics(device_id=device["id"]) == []
