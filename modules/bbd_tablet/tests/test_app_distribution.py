"""Sözleşme v2.1 — uygulama dağıtımı: APK okuma, parçalı yükleme, paket açma,
yayın, sync `apps`/`install`, indirme sırası ve yuvalar, dosya ucu (Range,
sıkıştırmasız), dağıtımı bitirme ve temizlik."""

from __future__ import annotations

import asyncio
import base64
import errno
import hashlib
import io
import os
import re
import struct
import threading
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
from modules.bbd_tablet.backend import distribution as distribution_module
from modules.bbd_tablet.backend.api import routes, tablet_routes
from modules.bbd_tablet.backend.apk_meta import ApkFormatError, parse_binary_manifest
from modules.bbd_tablet.backend.distribution import CHUNK_BYTES, HEAD_SYNC_SECONDS
from modules.bbd_tablet.backend.packages import (
    SELF_PACKAGE,
    ProcessingStopped,
    prepare_release,
    split_file_name,
)
from modules.bbd_tablet.backend.service import ConflictError, NotFoundError, TabletService

from km_sdk import CurrentUser

GAME = "com.example.game"
QUIZ = "com.example.quiz"
# The tablet's rule for a stored file name.
TABLET_FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")

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
    # An APK without app distribution sends no `appsVersion` key: no catalog.
    assert (await sync(service, token))["apps"] is None
    empty = await sync(service, token, appsVersion=None)
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
    # Positions count tablets, not rows: only the fifth tablet (pushed) is
    # ahead of this one for the game.
    assert third["install"]["queue"] == [{"releaseId": own, "position": 1},
                                         {"releaseId": game, "position": 2}]

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
    apps = (await sync(service, token, appsVersion=None))["apps"]
    assert apps["remove"] == [GAME]
    assert [item["packageName"] for item in apps["items"]] == [SELF_PACKAGE]
    queue = service.distribution.queue
    assert await store.fetch_all(f"SELECT * FROM {queue} WHERE release_id=?", (game,)) == []

    # Distributing the package again takes it off the remove list.
    await publish(service, apk(GAME, 46))
    assert (await sync(service, token, appsVersion=None))["apps"]["remove"] == []


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
    assert await dist.cleanup() == {"uploads": 0, "orphanFiles": 2, "repaired": 0}
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
    assert (await bare.distribution.cleanup()) == {"uploads": 0, "orphanFiles": 0,
                                                   "repaired": 0}


# -------------------------------------------- gözden geçirme düzeltmeleri


async def start_and_send(service: TabletService, payload: bytes,
                         name: str = "oyun.apk") -> str:
    """An upload with every byte sent but not finished; returns its id."""
    dist = service.distribution
    started = await dist.start_upload(file_name=name, size_bytes=len(payload),
                                      fingerprint="1700000000000", actor="Ayşe")
    await dist.put_chunk(upload_id=started["uploadId"], offset=0,
                         data=base64.b64encode(payload).decode())
    return str(started["uploadId"])


async def test_lost_upload_file_fails_the_upload_instead_of_looping(
        service: TabletService) -> None:
    dist = service.distribution
    root = dist.storage_dir
    assert root is not None
    payload = apk(filler=30_000)
    started = await dist.start_upload(file_name="oyun.apk", size_bytes=len(payload),
                                      fingerprint="7", actor="Ayşe")
    upload_id = started["uploadId"]
    await dist.put_chunk(upload_id=upload_id, offset=0,
                         data=base64.b64encode(payload[:10_000]).decode())
    (root / "uploads" / f"{upload_id}.part").unlink()
    with pytest.raises(ConflictError) as lost:
        await dist.put_chunk(upload_id=upload_id, offset=10_000,
                             data=base64.b64encode(payload[10_000:]).decode())
    assert lost.value.code == "UPLOAD_LOST"
    # The panel re-reads the status after a 409: not `uploading` any more → it stops.
    status = await dist.upload_status(upload_id)
    assert status["state"] == "failed" and "baştan" in status["error"]
    with pytest.raises(ConflictError, match="parça kabul etmiyor"):
        await dist.put_chunk(upload_id=upload_id, offset=10_000, data="AA==")
    # Selecting the file again starts a new upload instead of resuming the lost one.
    again = await dist.start_upload(file_name="oyun.apk", size_bytes=len(payload),
                                    fingerprint="7", actor="Ayşe")
    assert again["uploadId"] != upload_id and again["resumed"] is False
    assert dist._upload_locks == {}


async def test_publish_delete_and_cleanup_of_one_draft_do_not_cross(
        service: TabletService, clock: Any) -> None:
    dist = service.distribution
    root = dist.storage_dir
    assert root is not None
    draft = await upload(service, apk(GAME, 45))
    published, refused = await asyncio.gather(
        dist.publish(release_id=draft["releaseId"], app_name=""),
        dist.delete_release(release_id=draft["releaseId"]), return_exceptions=True)
    assert isinstance(published, dict) and published["state"] == "published"
    assert isinstance(refused, ConflictError)
    assert (root / "releases" / draft["releaseId"] / "base.apk").is_file()

    other = await upload(service, apk(QUIZ, 3))
    deleted, missing = await asyncio.gather(
        dist.delete_release(release_id=other["releaseId"]),
        dist.publish(release_id=other["releaseId"], app_name=""), return_exceptions=True)
    assert isinstance(deleted, dict) and deleted["deleted"] is True
    assert isinstance(missing, NotFoundError)
    assert QUIZ not in (await dist.catalog())["packages"]

    # A two-day-old draft being published is not deleted by the cleanup.
    old = await upload(service, apk(QUIZ, 4))
    clock.advance(days=3)
    cleaned, done = await asyncio.gather(
        dist.cleanup(), dist.publish(release_id=old["releaseId"], app_name=""))
    assert cleaned["uploads"] == 0 and done["state"] == "published"
    assert (root / "releases" / old["releaseId"] / "base.apk").is_file()
    assert dist._upload_locks == {}


async def test_interrupted_publish_is_finished_never_deleted(
        service: TabletService, store: Any, clock: Any) -> None:
    dist = service.distribution
    root = dist.storage_dir
    assert root is not None
    old = await publish(service, apk(GAME, 45))
    new = await upload(service, apk(GAME, 46))
    # Crash right after publish's first write: `managed_apps` points to the draft.
    await store.execute(f"UPDATE {dist.managed} SET release_id=? WHERE package_name=?",
                        (new["releaseId"], GAME))
    dist.invalidate()
    assert (await dist.catalog())["packages"][GAME]["releaseId"] == new["releaseId"]
    clock.advance(days=3)
    cleaned = await dist.cleanup()
    assert cleaned["uploads"] == 0 and cleaned["repaired"] == 2
    assert (await dist.upload_status(new["releaseId"]))["state"] == "published"
    assert (await dist.upload_status(old["releaseId"]))["state"] == "superseded"
    assert (root / "releases" / new["releaseId"] / "base.apk").is_file()
    assert not (root / "releases" / old["releaseId"]).exists()

    # Publishing the same draft again finishes the job instead of "not newer".
    first = await publish(service, apk(QUIZ, 3))
    second = await upload(service, apk(QUIZ, 4))
    await store.execute(f"UPDATE {dist.managed} SET release_id=? WHERE package_name=?",
                        (second["releaseId"], QUIZ))
    with pytest.raises(ConflictError):
        await dist.delete_release(release_id=second["releaseId"])
    resumed = await dist.publish(release_id=second["releaseId"], app_name="")
    assert resumed["state"] == "published"
    assert (await dist.cleanup())["repaired"] == 1        # the first one is superseded
    assert (await dist.upload_status(first["releaseId"]))["state"] == "superseded"

    # A managed row whose release vanished does not break the next publish (upsert).
    stamp = clock().isoformat()
    await store.execute(
        f"INSERT INTO {dist.managed} (package_name, app_name, kind, release_id, created_at, "
        "updated_at) VALUES (?, ?, 'app', 'kayip', ?, ?)", ("com.example.lost", "Kayıp",
                                                            stamp, stamp))
    lost = await publish(service, apk("com.example.lost", 1))
    assert lost["state"] == "published"
    assert (await dist.catalog())["packages"]["com.example.lost"]["releaseId"] == \
        lost["releaseId"]


async def test_restart_while_processing_is_recovered_by_the_app_list(
        service: TabletService, store: Any) -> None:
    dist = service.distribution
    root = dist.storage_dir
    assert root is not None
    payload = apk(GAME, 45, filler=20_000)
    single = await start_and_send(service, payload)
    # The process stopped right after the APK was moved to `base.apk`.
    await store.execute(f"UPDATE {dist.releases} SET state='processing' WHERE id=?", (single,))
    target = root / "releases" / single
    target.mkdir(parents=True)
    os.replace(root / "uploads" / f"{single}.part", target / "base.apk")

    base = apk(QUIZ, 3)
    split = apk(QUIZ, 3, split="config.tr")
    packed = await start_and_send(service, bundle({"base.apk": base, "split.apk": split}),
                                  name="quiz.apks")
    # Stopped half-way through unpacking: the upload file is still there.
    await store.execute(f"UPDATE {dist.releases} SET state='processing' WHERE id=?", (packed,))
    (root / "releases" / packed).mkdir(parents=True)
    (root / "releases" / packed / "part-0.tmp").write_bytes(b"yarim")

    listing = await dist.overview()      # GET /apps notices and restarts the work
    assert {item["state"] for item in listing["drafts"]} == {"processing"}
    await dist.wait_idle()
    first = await dist.upload_status(single)
    assert first["state"] == "ready", first["error"]
    assert first["release"]["files"][0]["sha256"] == hashlib.sha256(payload).hexdigest()
    assert (target / "base.apk").read_bytes() == payload
    second = await dist.upload_status(packed)
    assert second["state"] == "ready", second["error"]
    assert sorted(path.name for path in (root / "releases" / packed).iterdir()) == \
        ["base.apk", "split_config.tr.apk"]
    assert not (root / "uploads" / f"{packed}.part").exists()


async def test_processing_draft_can_be_discarded(
        service: TabletService, monkeypatch: pytest.MonkeyPatch) -> None:
    dist = service.distribution
    root = dist.storage_dir
    assert root is not None
    running = threading.Event()

    def slow(upload: Path, target: Path, *, reserve_bytes: int, stop: Any) -> Any:
        target.mkdir(parents=True, exist_ok=True)
        (target / "part-0.tmp").write_bytes(b"x")
        running.set()
        while not stop():
            time.sleep(0.01)
        raise ProcessingStopped("Paket işlenirken taslak atıldı.")

    monkeypatch.setattr(distribution_module, "prepare_release", slow)
    upload_id = await start_and_send(service, apk(GAME, 45))
    await dist.finish_upload(upload_id=upload_id)
    assert await asyncio.to_thread(running.wait, 5)
    assert upload_id in dist._tasks
    assert (await dist.delete_release(release_id=upload_id))["deleted"] is True
    await dist.wait_idle()
    with pytest.raises(NotFoundError):
        await dist.upload_status(upload_id)
    assert not (root / "releases" / upload_id).exists()
    assert not (root / "uploads" / f"{upload_id}.part").exists()
    assert dist._upload_locks == {} and dist._stops == {}


def test_stop_interrupts_unpacking(tmp_path: Path) -> None:
    for name, payload in (("tek.apk", apk(GAME, 45)),
                          ("paket.apks", bundle({"base.apk": apk(GAME, 45)}))):
        source = tmp_path / name
        source.write_bytes(payload)
        with pytest.raises(ProcessingStopped):
            prepare_release(source, tmp_path / f"out-{name}", stop=lambda: True)
        assert source.is_file()             # nothing was moved


async def test_split_file_names_fit_the_tablet_rule(service: TabletService) -> None:
    long_a = "config." + "a" * 120
    long_b = "config." + "a" * 119 + "b"
    for split in (long_a, long_b, "config.arm64_v8a", "../evil name", "-x"):
        name = split_file_name(split)
        assert TABLET_FILE_NAME.match(name) and len(name) <= 100, name
    assert split_file_name(long_a) != split_file_name(long_b)
    assert split_file_name("config.arm64_v8a") == "split_config.arm64_v8a.apk"

    base = apk(GAME, 50)
    status = await upload(service, bundle({
        "base.apk": base, "a.apk": apk(GAME, 50, split=long_a),
        "b.apk": apk(GAME, 50, split=long_b),
        "c.apk": apk(GAME, 50, split="feature one"), "d.apk": apk(GAME, 50, split="feature_one"),
    }), name="uzun.apks")
    assert status["state"] == "ready", status["error"]
    names = [item["name"] for item in status["release"]["files"]]
    assert len(set(names)) == 5
    assert all(TABLET_FILE_NAME.match(name) for name in names), names


async def test_positions_count_tablets_without_a_slot(
        service: TabletService, clock: Any) -> None:
    game = (await publish(service, apk(GAME, 45)))["releaseId"]
    quiz = (await publish(service, apk(QUIZ, 3)))["releaseId"]
    tokens = [(await pair(service, f"T{index}"))[1] for index in range(5)]
    # Two slots, both taken by tablets that want a second app as well. The game
    # is asked for first, so the slot is the game's whatever the release ids are.
    for token in tokens[:2]:
        held = await sync(service, token, **installs(wanted=[game]))
        assert held["install"]["grant"]["releaseId"] == game
        clock.advance(seconds=1)
        again = await sync(service, token, **installs(
            wanted=[quiz], active={"releaseId": game, "receivedBytes": 0}))
        assert again["install"]["grant"]["releaseId"] == game
        clock.advance(seconds=1)
    first = await sync(service, tokens[2], **installs(wanted=[game]))
    assert first["install"]["queue"] == [{"releaseId": game, "position": 1}]
    assert first["nextSyncSeconds"] <= HEAD_SYNC_SECONDS
    clock.advance(seconds=1)
    second = await sync(service, tokens[3], **installs(wanted=[game]))
    assert second["install"]["queue"] == [{"releaseId": game, "position": 2}]
    assert second["nextSyncSeconds"] <= HEAD_SYNC_SECONDS
    clock.advance(seconds=1)
    third = await sync(service, tokens[4], **installs(wanted=[game]))
    assert third["install"]["queue"] == [{"releaseId": game, "position": 3}]
    assert third["nextSyncSeconds"] > HEAD_SYNC_SECONDS
    view = await service.distribution.app_devices(package_name=GAME)
    assert {item["deviceName"]: item["position"] for item in view["devices"]} == \
        {"T0": None, "T1": None, "T2": 1, "T3": 2, "T4": 3}

    # T0 finishes the game; its quiz was asked for before anyone else's game.
    next_one = await sync(service, tokens[0], **installs(
        wanted=[quiz], results=[{"releaseId": game, "ok": True, "versionCode": 45}]))
    assert next_one["install"]["grant"]["releaseId"] == quiz
    # T1 finishes and wants nothing more: the first tablet without a slot takes it.
    await sync(service, tokens[1], **installs(
        results=[{"releaseId": game, "ok": True, "versionCode": 45}]))
    taken = await sync(service, tokens[2], **installs(wanted=[game]))
    assert taken["install"]["grant"]["releaseId"] == game


async def test_a_tablet_that_stops_asking_frees_its_slot(
        service: TabletService, clock: Any) -> None:
    service.config["download_slots"] = 1
    game = (await publish(service, apk(GAME, 45)))["releaseId"]
    quiz = (await publish(service, apk(QUIZ, 3)))["releaseId"]
    token_a, token_b = [(await pair(service, f"T{index}"))[1] for index in range(2)]
    assert (await sync(service, token_a, **installs(wanted=[game])))["install"]["grant"]
    clock.advance(seconds=1)
    assert (await sync(service, token_b, **installs(wanted=[game])))["install"]["queue"] == \
        [{"releaseId": game, "position": 1}]
    # A no longer asks for the game (e.g. no room left) but still wants another app.
    clock.advance(seconds=1)
    moved = await sync(service, token_a, **installs(wanted=[quiz]))
    assert moved["install"] == {"grant": None, "queue": [{"releaseId": quiz, "position": 2}]}
    assert (await sync(service, token_b, **installs(wanted=[game])))["install"]["grant"][
        "releaseId"] == game


async def test_revoke_frees_the_slot_and_drops_queue_rows(
        service: TabletService, store: Any) -> None:
    service.config["download_slots"] = 1
    game = (await publish(service, apk(GAME, 45)))["releaseId"]
    (a, token_a), (_, token_b) = [await pair(service, f"T{index}") for index in range(2)]
    assert (await sync(service, token_a, **installs(wanted=[game])))["install"]["grant"]
    assert (await sync(service, token_b, **installs(wanted=[game])))["install"]["grant"] is None
    await service.revoke_device(device_id=a["id"])
    queue = service.distribution.queue
    assert await store.fetch_all(f"SELECT * FROM {queue} WHERE device_id=?", (a["id"],)) == []
    assert (await sync(service, token_b, **installs(wanted=[game])))["install"]["grant"]


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
        "appsVersion": None, "installs": {"wanted": [upload_id]}})).json()
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


async def test_wire_disk_full_is_a_coded_turkish_error(
        client: httpx.AsyncClient, service: TabletService,
        monkeypatch: pytest.MonkeyPatch) -> None:
    payload = apk(GAME, 45)
    started = (await client.post("/api/bbd_tablet/apps/uploads", headers=ADMIN_AUTH, json={
        "fileName": "oyun.apk", "sizeBytes": len(payload)})).json()
    path = f"/api/bbd_tablet/apps/uploads/{started['uploadId']}"
    body = {"offset": 0, "data": base64.b64encode(payload).decode()}
    failure = {"errno": errno.ENOSPC}

    def broken(*_: Any) -> None:
        raise OSError(failure["errno"], "disk")

    monkeypatch.setattr(distribution_module, "_write_at", broken)
    full = await client.put(f"{path}/chunk", headers=ADMIN_AUTH, json=body)
    assert full.status_code == 507
    assert "yer kalmadı" in full.json()["error"]["message"]
    failure["errno"] = errno.EIO
    other = await client.put(f"{path}/chunk", headers=ADMIN_AUTH, json=body)
    assert other.status_code == 500 and "yazılamadı" in other.json()["error"]["message"]
    # Nothing was counted: the same chunk goes again once the disk is fixed.
    monkeypatch.undo()
    assert (await client.get(path, headers=ADMIN_AUTH)).json()["receivedBytes"] == 0
    assert (await client.put(f"{path}/chunk", headers=ADMIN_AUTH, json=body)).status_code == 200
    assert service.distribution._upload_locks == {}


async def test_wire_download_urls_follow_proxy_headers_and_setting(
        client: httpx.AsyncClient, service: TabletService) -> None:
    game = (await publish(service, apk(GAME, 45)))["releaseId"]
    created = (await client.post("/api/bbd_tablet/devices", headers=ADMIN_AUTH,
                                 json={"name": "Etüt-01"})).json()
    enrolled = (await client.post("/api/tablet/v1/devices/enroll", json={
        "enrollmentCode": created["enrollmentCode"], "deviceUuid": "uuid-0000-0002",
        "deviceName": "Etüt-01"})).json()
    device_id = enrolled["deviceId"]
    auth = {"Authorization": f"Bearer {enrolled['deviceToken']}"}
    path = f"/api/tablet/v1/devices/{device_id}/sync"
    tail = f"/api/tablet/v1/devices/{device_id}/releases/{game}/files/0"

    async def url(headers: dict[str, str]) -> str:
        answer = (await client.post(path, headers={**auth, **headers}, json={
            "installs": {"wanted": [game]}})).json()
        return str(answer["install"]["grant"]["files"][0]["url"])

    assert await url({}) == f"http://test{tail}"
    assert await url({"X-Forwarded-Proto": "https, http",
                      "X-Forwarded-Host": "km.example.org, proxy.local"}) == \
        f"https://km.example.org{tail}"
    service.config["public_base_url"] = "https://kontrol.example.org/"
    assert await url({"X-Forwarded-Host": "baska.example", "CF-Visitor": '{"scheme":"http"}'}) \
        == f"https://kontrol.example.org{tail}"
