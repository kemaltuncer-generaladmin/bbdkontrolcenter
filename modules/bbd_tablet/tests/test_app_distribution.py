"""Sözleşme v2.1 — uygulama dağıtımı: APK okuma, parçalı yükleme, paket açma,
yayın, sync `apps`/`install`, indirme sırası ve yuvalar, dosya ucu (Range,
sıkıştırmasız), dağıtımı bitirme ve temizlik."""

from __future__ import annotations

import base64
import hashlib
import io
import os
import struct
import time
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from modules.bbd_tablet.backend.api import routes, tablet_routes
from modules.bbd_tablet.backend.apk_meta import ApkFormatError, parse_binary_manifest
from modules.bbd_tablet.backend.distribution import CHUNK_BYTES, HEAD_SYNC_SECONDS
from modules.bbd_tablet.backend.packages import SELF_PACKAGE
from modules.bbd_tablet.backend.service import ConflictError, NotFoundError, TabletService

from km_sdk import CurrentUser

GAME = "com.example.game"

# ------------------------------------------------------------ AXML üretici

_FRAMEWORK = (("versionCode", 0x0101021B), ("versionName", 0x0101021C),
              ("minSdkVersion", 0x0101020C), ("label", 0x01010001))


def axml(package: str, version_code: int, *, version_name: str = "1.0",
         split: str | None = None, label: str | None = None, min_sdk: int = 24) -> bytes:
    """Smallest binary AndroidManifest.xml the reader accepts (UTF-8 pool)."""
    strings: list[str] = [name for name, _ in _FRAMEWORK]

    def index(text: str) -> int:
        if text not in strings:
            strings.append(text)
        return strings.index(text)

    def string_attr(name: str, value: str) -> bytes:
        return struct.pack("<IIIHBBI", 0xFFFFFFFF, index(name), index(value), 8, 0, 0x03,
                           index(value))

    def int_attr(name: str, value: int) -> bytes:
        return struct.pack("<IIIHBBI", 0xFFFFFFFF, index(name), 0xFFFFFFFF, 8, 0, 0x10, value)

    elements = [("manifest", [string_attr("package", package),
                              int_attr("versionCode", version_code),
                              string_attr("versionName", version_name)]
                 + ([string_attr("split", split)] if split else [])),
                ("uses-sdk", [int_attr("minSdkVersion", min_sdk)]),
                ("application", [string_attr("label", label)] if label else [])]
    body = b""
    for name, attrs in elements:
        ext = struct.pack("<IIHHHHHH", 0xFFFFFFFF, index(name), 20, 20, len(attrs), 0, 0, 0)
        payload = ext + b"".join(attrs)
        body += struct.pack("<HHIII", 0x0102, 16, 16 + len(payload), 1, 0xFFFFFFFF) + payload

    data = b""
    offsets = []
    for text in strings:
        encoded = text.encode("utf-8")
        offsets.append(len(data))
        data += bytes([len(text), len(encoded)]) + encoded + b"\0"
    data += b"\0" * (-len(data) % 4)
    start = 28 + 4 * len(strings)
    pool = struct.pack("<HHIIIIII", 0x0001, 28, start + len(data), len(strings), 0, 0x100,
                       start, 0) + struct.pack(f"<{len(strings)}I", *offsets) + data
    ids = struct.pack("<HHI", 0x0180, 8, 8 + 4 * len(_FRAMEWORK)) + \
        struct.pack(f"<{len(_FRAMEWORK)}I", *(rid for _, rid in _FRAMEWORK))
    chunks = pool + ids + body
    return struct.pack("<HHI", 0x0003, 8, 8 + len(chunks)) + chunks


def apk(package: str = GAME, version_code: int = 45, *, filler: int = 4096,
        padding: int = 0, **manifest: Any) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("AndroidManifest.xml", axml(package, version_code, **manifest))
        archive.writestr("classes.dex", os.urandom(filler))
        if padding:
            # Stored zeros: a body any gzip would shrink to almost nothing.
            archive.writestr("assets/padding.bin", b"\0" * padding,
                             compress_type=zipfile.ZIP_STORED)
    return buffer.getvalue()


def bundle(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


# ----------------------------------------------------------------- düzen


@pytest.fixture
def service(store: Any, canteen: Any, clock: Any, tmp_path: Path) -> TabletService:
    return TabletService(store=store, canteen=canteen, clock=clock,
                         config={"download_slots": 2}, storage_dir=tmp_path / "apps")


async def pair(service: TabletService, name: str = "Etüt-01") -> tuple[dict[str, Any], str]:
    pending = await service.create_device(name=name)
    enrolled = await service.enroll({"enrollmentCode": pending["enrollmentCode"],
                                     "deviceUuid": str(uuid4()), "deviceName": name})
    device = await service.device_for_token(enrolled["deviceToken"])
    assert device is not None
    return device, enrolled["deviceToken"]


async def sync(service: TabletService, token: str, **data: Any) -> dict[str, Any]:
    device = await service.device_by_token(token)
    assert device is not None
    return await service.sync(device=device, data=data, base_url="https://km.test/api/tablet/v1")


async def upload(service: TabletService, payload: bytes, name: str = "oyun.apk",
                 step: int = CHUNK_BYTES) -> dict[str, Any]:
    dist = service.distribution
    started = await dist.start_upload(file_name=name, size_bytes=len(payload),
                                      fingerprint="1700000000000", actor="Ayşe")
    offset = started["receivedBytes"]
    while offset < len(payload):
        part = payload[offset:offset + step]
        await dist.put_chunk(upload_id=started["uploadId"], offset=offset,
                             data=base64.b64encode(part).decode())
        offset += len(part)
    finished = await dist.finish_upload(upload_id=started["uploadId"])
    assert finished["state"] in ("processing", "ready", "failed")
    await dist.wait_idle()
    return await dist.upload_status(started["uploadId"])


async def publish(service: TabletService, payload: bytes, app_name: str = "") -> dict[str, Any]:
    status = await upload(service, payload)
    assert status["state"] == "ready", status["error"]
    return await service.distribution.publish(release_id=status["releaseId"], app_name=app_name)


def installs(**fields: Any) -> dict[str, Any]:
    return {"installs": {"wanted": [], "priority": None, "active": None, "results": [],
                         **fields}}


# ------------------------------------------------------------- APK okuma


def test_manifest_reader_reads_package_version_split_and_label() -> None:
    parsed = parse_binary_manifest(axml("com.example.game", 45, version_name="2.3.1",
                                        label="Örnek Oyun", min_sdk=26))
    assert (parsed.package_name, parsed.version_code, parsed.version_name) == \
        ("com.example.game", 45, "2.3.1")
    assert (parsed.split_name, parsed.label, parsed.min_sdk) == ("", "Örnek Oyun", 26)
    split = parse_binary_manifest(axml("com.example.game", 45, split="config.arm64_v8a"))
    assert split.split_name == "config.arm64_v8a" and split.label == ""
    with pytest.raises(ApkFormatError):
        parse_binary_manifest(b"\x03\x00\x08\x00" + b"\xff" * 20)
    with pytest.raises(ApkFormatError):
        parse_binary_manifest(b"not a manifest")


# -------------------------------------------------------- parçalı yükleme


async def test_chunked_upload_offsets_limit_and_resume(service: TabletService) -> None:
    dist = service.distribution
    payload = apk(filler=50_000)
    started = await dist.start_upload(file_name="C:\\İndirilenler\\oyun.apk",
                                      size_bytes=len(payload), fingerprint="42", actor="Ayşe")
    assert started["fileName"] == "oyun.apk" and started["chunkBytes"] == CHUNK_BYTES
    assert started["receivedBytes"] == 0 and started["state"] == "uploading"
    upload_id = started["uploadId"]

    first = payload[:20_000]
    done = await dist.put_chunk(upload_id=upload_id, offset=0,
                                data=base64.b64encode(first).decode())
    assert done["receivedBytes"] == 20_000
    with pytest.raises(ConflictError) as wrong:
        await dist.put_chunk(upload_id=upload_id, offset=0, data=base64.b64encode(first).decode())
    assert wrong.value.extra["receivedBytes"] == 20_000
    with pytest.raises(ValueError, match="base64"):
        await dist.put_chunk(upload_id=upload_id, offset=20_000, data="***")
    with pytest.raises(ValueError, match="boyutunu aşıyor"):
        await dist.put_chunk(upload_id=upload_id, offset=20_000,
                             data=base64.b64encode(b"x" * len(payload)).decode())
    with pytest.raises(ConflictError, match="tamamlanmadı"):
        await dist.finish_upload(upload_id=upload_id)

    # Same name + size + fingerprint resumes; another fingerprint starts anew.
    again = await dist.start_upload(file_name="oyun.apk", size_bytes=len(payload),
                                    fingerprint="42", actor="Ayşe")
    assert again["uploadId"] == upload_id and again["resumed"] is True
    assert again["receivedBytes"] == 20_000
    other = await dist.start_upload(file_name="oyun.apk", size_bytes=len(payload),
                                    fingerprint="43", actor="Ayşe")
    assert other["uploadId"] != upload_id
    await dist.put_chunk(upload_id=upload_id, offset=20_000,
                         data=base64.b64encode(payload[20_000:]).decode())
    await dist.finish_upload(upload_id=upload_id)
    await dist.wait_idle()
    status = await dist.upload_status(upload_id)
    assert status["state"] == "ready"
    stored = service.distribution.storage_dir / "releases" / upload_id / "base.apk"  # type: ignore[operator]
    assert stored.read_bytes() == payload

    with pytest.raises(ValueError, match="en fazla 1 MB"):
        service.config["max_upload_mb"] = 1
        await dist.start_upload(file_name="dev.apk", size_bytes=2 * 1024 * 1024,
                                fingerprint="", actor="Ayşe")
    with pytest.raises(NotFoundError):
        await dist.put_chunk(upload_id="yok", offset=0, data="AA==")


async def test_finish_reads_package_hashes_and_detects_self(service: TabletService) -> None:
    payload = apk(GAME, 45, version_name="2.3.1", label="Örnek Oyun")
    status = await upload(service, payload)
    release = status["release"]
    assert status["state"] == "ready" and status["error"] == ""
    assert (release["packageName"], release["versionCode"], release["versionName"]) == \
        (GAME, 45, "2.3.1")
    assert release["kind"] == "app" and release["appName"] == "Örnek Oyun"
    assert release["files"] == [{"name": "base.apk", "sizeBytes": len(payload),
                                 "sha256": hashlib.sha256(payload).hexdigest()}]
    assert release["totalBytes"] == len(payload) and release["current"] is None

    own = await upload(service, apk(SELF_PACKAGE, 7), name="tabletkontrol.apk")
    assert own["release"]["kind"] == "self"
    # No label in the manifest: the package name until a tablet reported a label.
    unnamed = await upload(service, apk("com.example.quiz", 3), name="quiz.apk")
    assert unnamed["release"]["appName"] == "com.example.quiz"

    broken = await upload(service, b"PK\x03\x04 bu bir zip degil" * 10, name="bozuk.apk")
    assert broken["state"] == "failed" and "APK" in broken["error"]
    lone_split = await upload(service, apk(GAME, 46, split="config.tr"), name="parca.apk")
    assert lone_split["state"] == "failed" and "parçası" in lone_split["error"]


async def test_bundles_are_unpacked_and_obb_is_rejected(service: TabletService) -> None:
    base = apk(GAME, 50, label="Oyun")
    split = apk(GAME, 50, split="config.arm64_v8a")
    status = await upload(service, bundle({"base.apk": base,
                                           "split_config.arm64_v8a.apk": split,
                                           "manifest.json": b"{}", "icon.png": b"x"}),
                          name="oyun.xapk")
    assert status["state"] == "ready", status["error"]
    files = status["release"]["files"]
    assert [item["name"] for item in files] == ["base.apk", "split_config.arm64_v8a.apk"]
    assert files[1]["sha256"] == hashlib.sha256(split).hexdigest()
    assert status["release"]["totalBytes"] == len(base) + len(split)

    # bundletool layout, detected by content even with an .apk name.
    apks = await upload(service, bundle({"toc.pb": b"", "splits/base-master.apk": base,
                                         "standalones/standalone-x.apk": base}),
                        name="oyun-bundletool.apk")
    assert apks["state"] == "ready" and len(apks["release"]["files"]) == 1

    obb = await upload(service, bundle({"base.apk": base,
                                        "Android/obb/com.example.game/main.50.obb": b"o"}),
                       name="obb.xapk")
    assert obb["state"] == "failed" and "OBB" in obb["error"]
    two_bases = await upload(service, bundle({"a.apk": base, "b.apk": apk(GAME, 50)}),
                             name="iki.apks")
    assert two_bases["state"] == "failed" and "tek bir temel" in two_bases["error"]
    mixed = await upload(service, bundle({"base.apk": base,
                                          "split.apk": apk(GAME, 51, split="config.tr")}),
                         name="karisik.apkm")
    assert mixed["state"] == "failed" and "versionCode" in mixed["error"]
    empty = await upload(service, bundle({"readme.txt": b"apk yok"}), name="bos.apks")
    assert empty["state"] == "failed"
    # Failed packages leave no files behind.
    root = service.distribution.storage_dir
    assert root is not None
    assert not (root / "releases" / obb["releaseId"]).exists()
    assert not (root / "uploads" / f"{obb['releaseId']}.part").exists()


# ------------------------------------------------------------------- yayın


async def test_publish_refuses_downgrade_and_drops_old_files(service: TabletService) -> None:
    first = await publish(service, apk(GAME, 45, label="Oyun"), app_name="  Örnek Oyun ")
    assert first["state"] == "published" and first["release"]["appName"] == "Örnek Oyun"
    root = service.distribution.storage_dir
    assert root is not None
    old_dir = root / "releases" / first["releaseId"]
    assert old_dir.is_dir()

    same = await upload(service, apk(GAME, 45))
    assert same["release"]["current"]["versionCode"] == 45
    with pytest.raises(ConflictError, match="yeni değil"):
        await service.distribution.publish(release_id=same["releaseId"], app_name="")
    with pytest.raises(ConflictError):
        await service.distribution.publish(release_id=first["releaseId"], app_name="")

    newer = await upload(service, apk(GAME, 46))
    assert newer["release"]["appName"] == "Örnek Oyun"      # managed name is kept
    published = await service.distribution.publish(release_id=newer["releaseId"], app_name="")
    assert published["previousReleaseId"] == first["releaseId"]
    assert not old_dir.exists()
    catalog = await service.distribution.catalog()
    assert [item["versionCode"] for item in catalog["items"]] == [46]

    # A draft may be thrown away, a published release may not.
    await service.distribution.delete_release(release_id=same["releaseId"])
    with pytest.raises(ConflictError):
        await service.distribution.delete_release(release_id=newer["releaseId"])


async def test_sync_apps_is_null_when_the_version_is_known(
        service: TabletService, store: Any) -> None:
    _, token = await pair(service)
    empty = await sync(service, token)
    assert empty["apps"]["items"] == [] and empty["install"] is None
    assert (await sync(service, token, appsVersion=empty["apps"]["version"]))["apps"] is None

    await publish(service, apk(SELF_PACKAGE, 7), app_name="TabletKontrol")
    await publish(service, apk(GAME, 45, version_name="2.3.1"), app_name="Örnek Oyun")
    changed = await sync(service, token, appsVersion=empty["apps"]["version"])
    apps = changed["apps"]
    assert [item["kind"] for item in apps["items"]] == ["self", "app"]
    item = apps["items"][1]
    assert set(item) == {"releaseId", "packageName", "appName", "kind", "versionCode",
                         "versionName", "totalBytes", "files"}
    assert item["files"][0]["name"] == "base.apk" and len(item["files"][0]["sha256"]) == 64
    assert apps["remove"] == []

    # The catalog is in memory: a known version costs no query.
    current = await service.device_by_token(token)
    assert current is not None
    store.reset()
    quiet = await service.sync(device=current, data={"appsVersion": apps["version"]})
    assert quiet["apps"] is None and quiet["install"] is None
    assert store.reads <= 3 and store.writes <= 1, (store.reads, store.writes)


# -------------------------------------------------------------------- sıra


async def test_queue_order_slots_and_head_sync(service: TabletService, clock: Any) -> None:
    game = (await publish(service, apk(GAME, 45)))["releaseId"]
    tablets = [await pair(service, f"Etüt-{index}") for index in range(1, 6)]
    tokens = [token for _, token in tablets]

    first = await sync(service, tokens[0], **installs(wanted=[game]))
    assert first["install"]["grant"]["releaseId"] == game
    grant = first["install"]["grant"]
    assert grant["leaseSeconds"] == 900
    device_id = tablets[0][0]["id"]
    assert grant["files"] == [{"name": "base.apk", "url":
                               f"https://km.test/api/tablet/v1/devices/{device_id}/releases/"
                               f"{game}/files/0"}]
    clock.advance(seconds=1)
    second = await sync(service, tokens[1], **installs(wanted=[game]))
    assert second["install"]["grant"]["releaseId"] == game
    clock.advance(seconds=1)
    third = await sync(service, tokens[2], **installs(wanted=[game]))
    assert third["install"] == {"grant": None, "queue": [{"releaseId": game, "position": 1}]}
    assert third["nextSyncSeconds"] <= HEAD_SYNC_SECONDS
    clock.advance(seconds=1)
    fourth = await sync(service, tokens[3], **installs(wanted=[game]))
    assert fourth["install"]["queue"] == [{"releaseId": game, "position": 2}]
    assert fourth["nextSyncSeconds"] <= HEAD_SYNC_SECONDS     # 2 slots: positions 1–2 are head
    clock.advance(seconds=1)
    fifth = await sync(service, tokens[4], **installs(wanted=[game]))
    assert fifth["install"]["queue"] == [{"releaseId": game, "position": 3}]
    assert fifth["nextSyncSeconds"] == 300

    # A student's tap moves the fifth tablet ahead of the waiting ones.
    pushed = await sync(service, tokens[4], **installs(wanted=[game], priority=game))
    assert pushed["install"]["queue"] == [{"releaseId": game, "position": 1}]
    # A TabletKontrol update goes before everything, priority included.
    own = (await publish(service, apk(SELF_PACKAGE, 7)))["releaseId"]
    behind = await sync(service, tokens[4], **installs(wanted=[game], priority=game))
    assert behind["install"]["queue"] == [{"releaseId": game, "position": 1}]
    third = await sync(service, tokens[2], **installs(wanted=[own, game]))
    assert third["install"]["queue"] == [{"releaseId": own, "position": 1},
                                         {"releaseId": game, "position": 3}]

    # The first tablet finishes; its slot goes to the head of the queue only.
    done = await sync(service, tokens[0], **installs(
        results=[{"releaseId": game, "ok": True, "message": "", "versionCode": 45}]))
    assert done["install"] == {"grant": None, "queue": []}
    not_yet = await sync(service, tokens[4], **installs(wanted=[game], priority=game))
    assert not_yet["install"]["grant"] is None
    head = await sync(service, tokens[2], **installs(wanted=[own, game]))
    assert head["install"]["grant"]["releaseId"] == own
    assert head["install"]["queue"] == [{"releaseId": game, "position": 2}]


async def test_stale_tablets_and_expired_leases_free_the_slot(
        service: TabletService, clock: Any) -> None:
    service.config["download_slots"] = 1
    game = (await publish(service, apk(GAME, 45)))["releaseId"]
    token_a, token_b, token_c = [(await pair(service, f"T{index}"))[1] for index in range(3)]
    assert (await sync(service, token_a, **installs(wanted=[game])))["install"]["grant"]
    clock.advance(seconds=1)
    assert (await sync(service, token_b, **installs(wanted=[game])))["install"]["queue"] == \
        [{"releaseId": game, "position": 1}]
    clock.advance(seconds=1)
    assert (await sync(service, token_c, **installs(wanted=[game])))["install"]["queue"] == \
        [{"releaseId": game, "position": 2}]

    # `active` renews the lease.
    clock.advance(seconds=800)
    renewed = await sync(service, token_a, **installs(
        active={"releaseId": game, "receivedBytes": 1024}))
    assert renewed["install"]["grant"]["leaseSeconds"] == 900
    # B went quiet: after `queue_stale_seconds` C is first.
    clock.advance(seconds=150)
    assert (await sync(service, token_c, **installs(wanted=[game])))["install"]["queue"] == \
        [{"releaseId": game, "position": 1}]
    # A stops reporting: the lease runs out and the slot passes to C.
    clock.advance(seconds=800)
    taken = await sync(service, token_c, **installs(wanted=[game]))
    assert taken["install"]["grant"]["releaseId"] == game
    late = await sync(service, token_a, **installs(
        active={"releaseId": game, "receivedBytes": 2048}))
    assert late["install"] == {"grant": None, "queue": [{"releaseId": game, "position": 1}]}

    view = await service.distribution.app_devices(package_name=GAME)
    by_name = {item["deviceName"]: item for item in view["devices"]}
    assert by_name["T2"]["status"] == "downloading"
    assert by_name["T0"]["status"] == "waiting" and by_name["T0"]["position"] == 1
    assert by_name["T0"]["progressPercent"] is not None
    assert by_name["T1"]["position"] is None          # stale, not counted
    assert view["slots"] == {"total": 1, "used": 1}


async def test_results_failures_and_already_installed(service: TabletService) -> None:
    game = (await publish(service, apk(GAME, 45)))["releaseId"]
    token_a, token_b, token_c = [(await pair(service, f"T{index}"))[1] for index in range(3)]
    await sync(service, token_a, **installs(results=[
        {"releaseId": game, "ok": True, "message": "already_installed", "versionCode": 47}]))
    await sync(service, token_b, **installs(wanted=[game]))
    failed = await sync(service, token_b, **installs(results=[
        {"releaseId": game, "ok": False, "message": "insufficient_storage"}]))
    assert failed["install"] == {"grant": None, "queue": []}

    overview = await service.distribution.overview()
    counts = overview["apps"][0]["counts"]
    assert counts == {"installed": 1, "downloading": 0, "waiting": 0, "failed": 1,
                      "unknown": 1, "total": 3}
    rows = {item["deviceName"]: item
            for item in (await service.distribution.app_devices(package_name=GAME))["devices"]}
    assert rows["T0"]["installedVersionCode"] == 47
    assert rows["T1"]["lastError"] == "insufficient_storage"

    # A failed install asked for again re-enters at the back of the queue.
    await sync(service, token_c, **installs(wanted=[game]))
    back = await sync(service, token_b, **installs(wanted=[game]))
    assert back["install"]["grant"] is not None   # two slots, C holds one
    assert (await service.distribution.overview())["slots"] == {"total": 2, "used": 2}


async def test_admin_prioritize_and_device_delete_cleans_queue(
        service: TabletService, store: Any) -> None:
    service.config["download_slots"] = 1
    game = (await publish(service, apk(GAME, 45)))["releaseId"]
    (a, token_a), (b, token_b) = [await pair(service, f"T{index}") for index in range(2)]
    await sync(service, token_a, **installs(wanted=[game]))
    await sync(service, token_b, **installs(wanted=[game]))
    other, _ = await pair(service, "T2")
    await service.distribution.prioritize(package_name=GAME, device_id=other["id"])
    with pytest.raises(NotFoundError):
        await service.distribution.prioritize(package_name="com.yok", device_id=b["id"])
    queue = service.distribution.queue

    await service.distribution.prioritize(package_name=GAME, device_id=b["id"])
    pushed = await sync(service, token_b, **installs(wanted=[game]))
    assert pushed["install"]["queue"] == [{"releaseId": game, "position": 1}]

    await service.delete_device(device_id=b["id"])
    assert await store.fetch_all(f"SELECT * FROM {queue} WHERE device_id=?", (b["id"],)) == []
    await service.revoke_device(device_id=a["id"])
    assert await store.fetch_all(f"SELECT * FROM {queue} WHERE device_id=?", (a["id"],)) == []
    assert (await service.distribution.overview())["slots"]["used"] == 0


async def test_remove_with_uninstall_reaches_tablets(service: TabletService, store: Any) -> None:
    _, token = await pair(service)
    game = (await publish(service, apk(GAME, 45)))["releaseId"]
    await publish(service, apk(SELF_PACKAGE, 7))
    await sync(service, token, **installs(wanted=[game]))
    root = service.distribution.storage_dir
    assert root is not None

    with pytest.raises(ConflictError):
        await service.distribution.remove_app(package_name=SELF_PACKAGE, uninstall=False,
                                              actor="Ayşe")
    removed = await service.distribution.remove_app(package_name=GAME, uninstall=True,
                                                    actor="Ayşe")
    assert removed == {"removed": True, "packageName": GAME, "uninstall": True}
    assert not (root / "releases" / game).exists()
    apps = (await sync(service, token))["apps"]
    assert apps["remove"] == [GAME]
    assert [item["packageName"] for item in apps["items"]] == [SELF_PACKAGE]
    queue = service.distribution.queue
    assert await store.fetch_all(f"SELECT * FROM {queue} WHERE release_id=?", (game,)) == []

    # Distributing the package again takes it off the remove list.
    await publish(service, apk(GAME, 46))
    assert (await sync(service, token))["apps"]["remove"] == []


async def test_cleanup_drops_stale_uploads_drafts_and_orphans(
        service: TabletService, clock: Any) -> None:
    dist = service.distribution
    root = dist.storage_dir
    assert root is not None
    half = await dist.start_upload(file_name="yarim.apk", size_bytes=10_000, fingerprint="",
                                   actor="Ayşe")
    draft = await upload(service, apk(GAME, 45))
    live = await publish(service, apk("com.example.quiz", 3))
    orphan = root / "releases" / "eski-yayin"
    orphan.mkdir(parents=True)
    (orphan / "base.apk").write_bytes(b"x")
    stray = root / "uploads" / "kayip.part"
    stray.write_bytes(b"x")
    old = time.time() - 2 * 60 * 60
    for path in (orphan, stray):
        os.utime(path, (old, old))

    clock.advance(days=1)
    assert await dist.cleanup() == {"uploads": 0, "orphanFiles": 2}
    clock.advance(days=1, seconds=1)
    assert await service.purge_history(force=True)
    with pytest.raises(NotFoundError):
        await dist.upload_status(half["uploadId"])
    with pytest.raises(NotFoundError):
        await dist.upload_status(draft["releaseId"])
    assert not (root / "uploads" / f"{half['uploadId']}.part").exists()
    assert not (root / "releases" / draft["releaseId"]).exists()
    assert (root / "releases" / live["releaseId"] / "base.apk").is_file()


async def test_storage_is_required_for_uploads(store: Any, canteen: Any, clock: Any) -> None:
    bare = TabletService(store=store, canteen=canteen, config={}, clock=clock)
    with pytest.raises(ConflictError, match="depolama"):
        await bare.distribution.start_upload(file_name="a.apk", size_bytes=10, fingerprint="",
                                             actor="Ayşe")
    assert (await bare.distribution.cleanup()) == {"uploads": 0, "orphanFiles": 0}


# --------------------------------------------------------------- tel biçimi

ADMIN_AUTH = {"Authorization": "Bearer admin"}
_USERS = {
    "admin": CurrentUser(id="u1", first_name="Ayşe", last_name="Yılmaz", org_scope="bbd",
                         roles=["admin"], permissions={"bbd_tablet.view", "bbd_tablet.manage",
                                                       "bbd_tablet.grant"}),
    "viewer": CurrentUser(id="u2", first_name="Can", last_name="Demir", org_scope="bbd",
                          roles=["org_staff"], permissions={"bbd_tablet.view"}),
    "manager": CurrentUser(id="u3", first_name="Ece", last_name="Kaya", org_scope="bbd",
                           roles=["bbd_staff"],
                           permissions={"bbd_tablet.view", "bbd_tablet.manage"}),
}


class Identity:
    """Same stand-in as test_http_wire: PIN 482913 for everyone."""

    def __init__(self) -> None:
        self.store = self

    async def resolve_session(self, token: str) -> CurrentUser | None:
        return _USERS.get(token)

    @staticmethod
    def secret_lookup(pin: str) -> str:
        return f"h:{pin}"

    async def fetch_one(self, sql: str, params: tuple[Any, ...]) -> dict[str, str]:
        return {"secret_lookup": "h:482913"}

    async def audit(self, *args: Any, **kwargs: Any) -> None:
        return None


def core_envelope(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "İstek reddedildi."
        return JSONResponse(status_code=exc.status_code,
                            content={"error": {"status": exc.status_code, "message": detail}},
                            headers=exc.headers)



@pytest.fixture
async def client(service: TabletService) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    core_envelope(app)
    # Same middleware as km_core/http/app.py: package files must pass untouched.
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.state.identity = Identity()
    app.include_router(routes.bind(service), prefix="/api/bbd_tablet")
    app.include_router(tablet_routes.router, prefix="/api/tablet/v1")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    routes._service = None


async def test_wire_upload_publish_download_and_remove(
        client: httpx.AsyncClient, service: TabletService) -> None:
    payload = apk(GAME, 45, padding=200_000)
    started = (await client.post("/api/bbd_tablet/apps/uploads", headers=ADMIN_AUTH, json={
        "fileName": "oyun.apk", "sizeBytes": len(payload), "fingerprint": "1"})).json()
    upload_id = started["uploadId"]
    path = f"/api/bbd_tablet/apps/uploads/{upload_id}"
    viewer = {"Authorization": "Bearer viewer"}
    assert (await client.put(f"{path}/chunk", headers=viewer,
                             json={"offset": 0, "data": "AA=="})).status_code == 403
    half = len(payload) // 2
    put = await client.put(f"{path}/chunk", headers=ADMIN_AUTH, json={
        "offset": 0, "data": base64.b64encode(payload[:half]).decode()})
    assert put.status_code == 200 and put.json()["receivedBytes"] == half
    wrong = await client.put(f"{path}/chunk", headers=ADMIN_AUTH, json={
        "offset": 0, "data": base64.b64encode(payload[:10]).decode()})
    assert wrong.status_code == 409 and wrong.headers["x-received-bytes"] == str(half)
    assert str(half) in wrong.json()["error"]["message"]
    await client.put(f"{path}/chunk", headers=ADMIN_AUTH, json={
        "offset": half, "data": base64.b64encode(payload[half:]).decode()})
    finished = await client.post(f"{path}/finish", headers=ADMIN_AUTH)
    assert finished.json()["state"] in ("processing", "ready")
    await service.distribution.wait_idle()
    status = (await client.get(path, headers=ADMIN_AUTH)).json()
    assert status["state"] == "ready" and status["release"]["packageName"] == GAME
    published = await client.post(f"/api/bbd_tablet/apps/releases/{upload_id}/publish",
                                  headers=ADMIN_AUTH, json={"appName": "Örnek Oyun"})
    assert published.status_code == 200
    listing = (await client.get("/api/bbd_tablet/apps", headers=viewer)).json()
    assert listing["apps"][0]["appName"] == "Örnek Oyun"
    assert listing["slots"] == {"total": 2, "used": 0}

    created = (await client.post("/api/bbd_tablet/devices", headers=ADMIN_AUTH,
                                 json={"name": "Etüt-01"})).json()
    enrolled = (await client.post("/api/tablet/v1/devices/enroll", json={
        "enrollmentCode": created["enrollmentCode"], "deviceUuid": "uuid-0000-0001",
        "deviceName": "Etüt-01"})).json()
    device_id = enrolled["deviceId"]
    auth = {"Authorization": f"Bearer {enrolled['deviceToken']}"}
    file_path = f"/api/tablet/v1/devices/{device_id}/releases/{upload_id}/files/0"
    denied = await client.get(file_path, headers=auth)
    assert denied.status_code == 409
    assert denied.json()["detail"]["code"] == "DOWNLOAD_NOT_GRANTED"

    synced = (await client.post(f"/api/tablet/v1/devices/{device_id}/sync", headers={
        **auth, "CF-Visitor": '{"scheme":"https"}'}, json={
        "installs": {"wanted": [upload_id]}})).json()
    url = synced["install"]["grant"]["files"][0]["url"]
    assert url == f"https://test{file_path}"
    assert synced["apps"]["items"][0]["releaseId"] == upload_id

    full = await client.get(file_path, headers={**auth, "Accept-Encoding": "gzip"})
    assert full.status_code == 200 and full.content == payload
    assert "content-encoding" not in full.headers
    assert full.headers["content-length"] == str(len(payload))
    assert full.headers["cache-control"] == "no-store"
    assert full.headers["accept-ranges"] == "bytes"
    resumed = await client.get(file_path, headers={**auth, "Range": "bytes=1000-",
                                                   "Accept-Encoding": "gzip"})
    assert resumed.status_code == 206 and resumed.content == payload[1000:]
    assert resumed.headers["content-range"] == f"bytes 1000-{len(payload) - 1}/{len(payload)}"
    assert "content-encoding" not in resumed.headers
    foreign = await client.get(file_path.replace(device_id, "baska"), headers=auth)
    assert foreign.json()["detail"]["code"] == "FORBIDDEN_DEVICE"
    assert (await client.get(file_path[:-1] + "5", headers=auth)).status_code == 404

    # A newer version supersedes the old release: its file answers 410.
    newer = await upload(service, apk(GAME, 46))
    await service.distribution.publish(release_id=newer["releaseId"], app_name="")
    gone = await client.get(file_path, headers=auth)
    assert gone.status_code == 410 and gone.json()["detail"]["code"] == "RELEASE_GONE"

    remove = f"/api/bbd_tablet/apps/{GAME}"
    assert (await client.request("DELETE", remove, headers=ADMIN_AUTH,
                                 json={"uninstall": True})).status_code == 422
    assert (await client.request("DELETE", remove, headers=ADMIN_AUTH,
                                 json={"pin": "000000", "uninstall": True})).status_code == 403
    manager = {"Authorization": "Bearer manager"}
    removed = await client.request("DELETE", remove, headers=manager,
                                   json={"pin": "482913", "uninstall": True})
    assert removed.status_code == 200 and removed.json()["uninstall"] is True
    after = (await client.post(f"/api/tablet/v1/devices/{device_id}/sync", headers=auth,
                               json={"appsVersion": synced["apps"]["version"]})).json()
    assert after["apps"]["remove"] == [GAME] and after["apps"]["items"] == []
