"""App distribution: chunked uploads, releases, the download queue and leases.

Contract: TabletKontrol `docs/CONTROL_CENTER_INTEGRATION.md` → "Uygulama
dağıtımı ve uzaktan güncelleme (v2.1)". The server shares its host and its
Cloudflare zone with bbdstore, which shapes everything here:

- **Uploads are chunked** (`chunkBytes` = 8 MiB, base64 in JSON): Cloudflare
  refuses bodies over 100 MB and the panel's HTTP bridge only carries JSON.
  Each chunk is written at its offset, never the whole file in memory. Same
  name + size + fingerprint resumes an unfinished upload.
- **`finish` does not block**: unpacking and hashing 4 GB takes longer than
  Cloudflare's 100 s answer limit, so it starts a background task and answers
  `processing`; the panel polls `GET /apps/uploads/{id}`.
- **Downloads are rationed**: at most `download_slots` tablets hold a lease at
  a time. The order is kept here: TabletKontrol itself first, then tablets a
  student (or the admin) pushed forward, then the time the tablet first asked.
  A tablet that has not synced within `queue_stale_seconds` is skipped.
- **Sync stays cheap**: the catalog (current releases + remove list) and its
  opaque version live in memory and are rebuilt only after a publish or a
  removal (single uvicorn process). A sync without `installs` never touches
  the queue table.
- **No transactions**: the module store has none, so multi-row changes are
  ordered so that a crash between two writes leaves a state the daily
  cleanup repairs (see `publish` and `cleanup`). Changes to one release are
  serialized by its own lock; the queue lock comes second when both are held.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import errno
import hashlib
import json
import math
import os
import shutil
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from km_sdk import ensure_private_dir

from .errors import ConflictError, NotFoundError, StorageError, TabletError
from .packages import (
    SELF_PACKAGE,
    PackageError,
    PreparedRelease,
    free_bytes,
    prepare_release,
    recover_single,
)

CHUNK_BYTES = 8 * 1024 * 1024
# Base64 of one chunk plus slack; the JSON body stays far below Cloudflare's 100 MB.
CHUNK_BASE64_MAX = 4 * (CHUNK_BYTES // 3 + 1) + 16
# Kept free on the storage disk next to every upload and unpacked bundle.
RESERVE_BYTES = 512 * 1024 * 1024
STALE_UPLOAD = timedelta(days=2)
ORPHAN_GRACE_SECONDS = 60 * 60
HEAD_SYNC_SECONDS = 30
MAX_RESULTS = 50
MAX_WANTED = 200
MAX_APP_NAME = 120
IN_CHUNK = 500
# Stored release state → what the panel reads.
PUBLIC_STATE = {"uploading": "uploading", "processing": "processing", "draft": "ready",
                "failed": "failed", "published": "published",
                "superseded": "superseded", "withdrawn": "withdrawn"}
OPEN_STATES = ("uploading", "processing", "draft", "failed")
LIVE_STATES = ("uploading", "processing", "draft", "published")
# Only these still need their `.part` upload file.
PART_STATES = ("uploading", "processing")
# How long discarding a draft waits for its unpacking to notice the stop.
STOP_WAIT_SECONDS = 30
DISK_FULL = {errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)}
UPLOAD_LOST = "Yükleme dosyası sunucuda yok. Dosyayı yeniden seçip baştan yükleyin."


def _storage_error(exc: OSError, what: str) -> StorageError:
    if exc.errno in DISK_FULL:
        return StorageError("Sunucuda yer kalmadı. Yer açılınca aynı dosyayı yeniden seçin; "
                            "yükleme kaldığı yerden sürer.", status=507)
    return StorageError(f"{what} sunucuda diske yazılamadı.", status=500)


def _marks(count: int) -> str:
    return ", ".join("?" * count)


def _write_at(path: Path, offset: int, data: bytes) -> None:
    with path.open("r+b") as sink:
        sink.seek(offset)
        sink.write(data)


def _truncate(path: Path, size: int) -> None:
    with path.open("r+b") as sink:
        sink.truncate(size)


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def _remove_orphans(root: Path, parts: set[str], releases: set[str],
                    older_than: float) -> int:
    """Delete upload/release files no live record points to (after a grace).

    An upload file is live only while its record is still uploading or
    processing; a release folder while the record is not finished with.
    """
    removed = 0
    for folder, suffix, live in (("uploads", ".part", parts), ("releases", "", releases)):
        base = root / folder
        if not base.is_dir():
            continue
        for entry in base.iterdir():
            name = entry.name[:-len(suffix)] if suffix and entry.name.endswith(suffix) \
                else entry.name
            if name in live:
                continue
            try:
                if entry.stat().st_mtime > older_than:
                    continue
            except OSError:
                continue
            _remove(entry)
            removed += 1
    return removed


@dataclass
class _LockEntry:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


class AppDistribution:
    def __init__(self, *, store: Any, config: dict[str, Any],
                 clock: Callable[[], datetime], storage_dir: Path | None) -> None:
        self.store = store
        self.config = config
        self._clock = clock
        self.storage_dir = storage_dir
        self.releases = store.table("app_releases")
        self.managed = store.table("managed_apps")
        self.removals = store.table("app_removals")
        self.queue = store.table("install_queue")
        self.devices = store.table("devices")
        self.inventory = store.table("inventory")
        self._catalog: dict[str, Any] | None = None
        self._generation = 0
        self._catalog_lock = asyncio.Lock()
        self._queue_lock = asyncio.Lock()
        # Per-release locks; an entry lives only while someone holds or waits.
        self._upload_locks: dict[str, _LockEntry] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._stops: dict[str, threading.Event] = {}

    # ------------------------------------------------------------ ayarlar

    def _int_setting(self, key: str, default: int, low: int, high: int) -> int:
        try:
            value = int(self.config.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(low, min(high, value))

    @property
    def download_slots(self) -> int:
        return self._int_setting("download_slots", 2, 1, 20)

    @property
    def lease_seconds(self) -> int:
        return self._int_setting("download_lease_seconds", 900, 300, 3600)

    @property
    def queue_stale_seconds(self) -> int:
        return self._int_setting("queue_stale_seconds", 900, 300, 7200)

    @property
    def max_upload_bytes(self) -> int:
        return self._int_setting("max_upload_mb", 6144, 1, 65536) * 1024 * 1024

    def _now(self) -> datetime:
        return self._clock()

    def _storage(self) -> Path:
        if self.storage_dir is None:
            raise ConflictError("Uygulama dosyaları için depolama yeri tanımlı değil.")
        return self.storage_dir

    def _part(self, upload_id: str) -> Path:
        return self._storage() / "uploads" / f"{upload_id}.part"

    def _release_dir(self, release_id: str) -> Path:
        return self._storage() / "releases" / release_id

    async def _drop_files(self, release_id: str) -> None:
        if self.storage_dir is None:
            return
        await asyncio.to_thread(_remove, self._part(release_id))
        await asyncio.to_thread(_remove, self._release_dir(release_id))

    # ------------------------------------------------------------ katalog

    def invalidate(self) -> None:
        self._generation += 1
        self._catalog = None

    @staticmethod
    def _files(row: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            files = json.loads(row.get("files_json") or "[]")
        except ValueError:
            files = []
        return files if isinstance(files, list) else []

    @classmethod
    def _item(cls, row: dict[str, Any], app_name: str) -> dict[str, Any]:
        return {
            "releaseId": row["id"], "packageName": row["package_name"], "appName": app_name,
            "kind": row["kind"], "versionCode": int(row["version_code"]),
            "versionName": row["version_name"], "totalBytes": int(row["total_bytes"]),
            "files": [{"name": item["name"], "sizeBytes": int(item["sizeBytes"]),
                       "sha256": item["sha256"]} for item in cls._files(row)],
        }

    async def catalog(self) -> dict[str, Any]:
        """Current releases and the remove list; in memory until the next change."""
        cached = self._catalog
        if cached is not None:
            return cached
        async with self._catalog_lock:
            if self._catalog is not None:
                return self._catalog
            generation = self._generation
            rows = await self.store.fetch_all(
                f"SELECT m.app_name AS managed_name, m.created_at AS managed_at, r.* "
                f"FROM {self.managed} m JOIN {self.releases} r ON r.id = m.release_id"
            )
            removals = await self.store.fetch_all(
                f"SELECT package_name FROM {self.removals} ORDER BY package_name"
            )
            items = sorted((self._item(row, row["managed_name"]) for row in rows),
                           key=lambda item: (item["kind"] != "self", item["appName"].casefold(),
                                             item["packageName"]))
            remove = [row["package_name"] for row in removals]
            digest = hashlib.sha256(json.dumps({"items": items, "remove": remove},
                                               sort_keys=True).encode("utf-8")).hexdigest()
            catalog = {
                "version": digest[:24],
                "items": items,
                "remove": remove,
                "releases": {item["releaseId"]: item for item in items},
                "packages": {item["packageName"]: item for item in items},
                "publishedAt": {row["id"]: row.get("published_at") for row in rows},
            }
            if generation == self._generation:
                self._catalog = catalog
            return catalog

    async def apps_payload(self, apps_version: Any) -> dict[str, Any] | None:
        """Sync `apps`: `null` when the tablet already has this version."""
        catalog = await self.catalog()
        if apps_version == catalog["version"]:
            return None
        return {"version": catalog["version"], "items": catalog["items"],
                "remove": catalog["remove"]}

    # ------------------------------------------------- kendini güncelleme

    @staticmethod
    def self_item(catalog: dict[str, Any]) -> dict[str, Any] | None:
        """The published TabletKontrol release, if any (sözleşme v2.2)."""
        item = catalog["packages"].get(SELF_PACKAGE)
        return item if item is not None and item["kind"] == "self" else None

    async def self_update_offer(self, *, device_id: str, app_version_code: Any,
                                base_url: str) -> dict[str, Any] | None:
        """Sync `selfUpdate`: the current single-file self release when it is newer.

        In-memory catalog and one comparison, so an up-to-date tablet costs no
        query. A split self release is never offered: the self path installs a
        single `base.apk`.
        """
        item = self.self_item(await self.catalog())
        if item is None or len(item["files"]) != 1:
            return None
        if not isinstance(app_version_code, int) or isinstance(app_version_code, bool) \
                or item["versionCode"] <= app_version_code:
            return None
        file = item["files"][0]
        return {"releaseId": item["releaseId"], "versionCode": item["versionCode"],
                "versionName": item["versionName"], "sizeBytes": file["sizeBytes"],
                "sha256": file["sha256"],
                "url": f"{base_url}/devices/{device_id}/self-update/{item['releaseId']}"}

    async def self_update_path(self, *, release_id: str) -> Path:
        """File behind a `selfUpdate.url`; no lease, only the current self release."""
        item = self.self_item(await self.catalog())
        if item is None:
            raise NotFoundError("Yayında tablet uygulaması sürümü yok.")
        if item["releaseId"] != release_id:
            raise TabletError("RELEASE_GONE", "Bu sürüm artık dağıtılmıyor.")
        if len(item["files"]) != 1:
            raise NotFoundError("Dosya bulunamadı.")
        path = self._release_dir(release_id) / item["files"][0]["name"]
        if not await asyncio.to_thread(path.is_file):
            raise TabletError("RELEASE_GONE", "Bu sürümün dosyası artık sunucuda yok.")
        return path

    @staticmethod
    def self_report(device: dict[str, Any]) -> dict[str, Any] | None:
        """The tablet's last `selfUpdate` report stored on its row (v2.2+)."""
        try:
            report = json.loads(device.get("self_update") or "null")
        except ValueError:
            return None
        return report if isinstance(report, dict) else None

    @classmethod
    def _self_status(cls, device: dict[str, Any],
                     release: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        """Panel status of the self release from a v2.2 report; None for older tablets.

        The buckets are the queue's: `ready` (waits for the student session to
        end) counts as waiting, `installing` as downloading; `selfState` tells
        the panel which one it really is.
        """
        report = cls.self_report(device)
        if report is None:
            return None
        code = device.get("app_version_code")
        state = report.get("state")
        if isinstance(code, int) and code >= release["versionCode"]:
            status = "installed"
        elif state in ("downloading", "installing"):
            status = "downloading"
        elif state == "failed":
            status = "failed"
        else:
            # idle, ready, or an older update reported as installed: the
            # tablet takes this one on its next sync or after the session.
            status = "waiting"
        return status, report

    # --------------------------------------------------------- yüklemeler

    def _status(self, row: dict[str, Any],
                current: dict[str, Any] | None = None) -> dict[str, Any]:
        state = row["state"]
        view: dict[str, Any] = {
            "uploadId": row["id"], "releaseId": row["id"],
            "state": PUBLIC_STATE.get(state, state), "fileName": row["file_name"],
            "sizeBytes": int(row["size_bytes"]), "receivedBytes": int(row["received_bytes"]),
            "chunkBytes": CHUNK_BYTES, "error": row["error"] or "",
            "createdAt": row["created_at"], "updatedAt": row["updated_at"],
            "createdBy": row["created_by"] or "", "release": None,
        }
        if state in ("draft", "published", "superseded", "withdrawn") and row["package_name"]:
            release = self._item(row, row["app_name"] or row["package_name"])
            release["minSdk"] = row.get("min_sdk")
            release["current"] = None
            if current and current["releaseId"] != row["id"]:
                release["current"] = {"releaseId": current["releaseId"],
                                      "appName": current["appName"],
                                      "versionCode": current["versionCode"],
                                      "versionName": current["versionName"]}
            view["release"] = release
        return view

    async def _row(self, release_id: str) -> dict[str, Any]:
        row = await self.store.fetch_one(
            f"SELECT * FROM {self.releases} WHERE id=?", (release_id,)
        )
        if not row:
            raise NotFoundError("Yükleme bulunamadı.")
        return dict(row)

    async def upload_status(self, upload_id: str) -> dict[str, Any]:
        row = await self._row(upload_id)
        if row["state"] == "processing" and upload_id not in self._tasks:
            # The process restarted while unpacking; pick the work up again.
            self._start_processing(upload_id)
        current = (await self.catalog())["packages"].get(row["package_name"])
        return self._status(row, current)

    async def start_upload(self, *, file_name: str, size_bytes: int, fingerprint: str,
                           actor: str) -> dict[str, Any]:
        storage = self._storage()
        name = str(file_name).replace("\\", "/").rsplit("/", 1)[-1].strip()[:255]
        if not name:
            raise ValueError("Dosya adı boş olamaz.")
        size = int(size_bytes)
        if size <= 0:
            raise ValueError("Dosya boş.")
        if size > self.max_upload_bytes:
            raise ValueError(f"Dosya en fazla {self.max_upload_bytes // (1024 * 1024)} MB olabilir.")
        fingerprint = str(fingerprint or "")[:100]
        existing = await self.store.fetch_one(
            f"SELECT * FROM {self.releases} WHERE state='uploading' AND file_name=? "
            "AND size_bytes=? AND fingerprint=? ORDER BY updated_at DESC LIMIT 1",
            (name, size, fingerprint),
        )
        if existing and await asyncio.to_thread(self._part(existing["id"]).is_file):
            return {**self._status(dict(existing)), "resumed": True}
        available = await asyncio.to_thread(free_bytes, storage)
        if available - size < RESERVE_BYTES:
            raise ConflictError(
                f"Sunucuda yer yok: dosya {size // (1024 * 1024)} MB, boş alan "
                f"{max(0, available - RESERVE_BYTES) // (1024 * 1024)} MB."
            )
        upload_id = str(uuid4())
        part = self._part(upload_id)

        def create() -> None:
            ensure_private_dir(storage)
            ensure_private_dir(part.parent)
            os.close(os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600))

        try:
            await asyncio.to_thread(create)
        except OSError as exc:
            raise _storage_error(exc, "Yükleme dosyası") from exc
        stamp = self._now().isoformat()
        await self.store.execute(
            f"INSERT INTO {self.releases} (id, state, file_name, fingerprint, size_bytes, "
            "received_bytes, created_by, created_at, updated_at) "
            "VALUES (?, 'uploading', ?, ?, ?, 0, ?, ?, ?)",
            (upload_id, name, fingerprint, size, actor, stamp, stamp),
        )
        return {**self._status(await self._row(upload_id)), "resumed": False}

    @asynccontextmanager
    async def _guard(self, release_id: str) -> AsyncIterator[None]:
        """The release's own lock; the entry is dropped once nobody uses it."""
        entry = self._upload_locks.get(release_id)
        if entry is None:
            entry = self._upload_locks[release_id] = _LockEntry()
        entry.users += 1
        try:
            async with entry.lock:
                yield
        finally:
            entry.users -= 1
            if entry.users == 0 and self._upload_locks.get(release_id) is entry:
                del self._upload_locks[release_id]

    async def put_chunk(self, *, upload_id: str, offset: int, data: str) -> dict[str, Any]:
        async with self._guard(upload_id):
            row = await self._row(upload_id)
            if row["state"] != "uploading":
                raise ConflictError("Bu yükleme artık parça kabul etmiyor.")
            received, size = int(row["received_bytes"]), int(row["size_bytes"])
            if int(offset) != received:
                raise ConflictError(f"Parça {received}. bayttan başlamalı.",
                                    code="UPLOAD_OFFSET", receivedBytes=received)
            if len(data) > CHUNK_BASE64_MAX:
                raise ValueError(f"Parça en fazla {CHUNK_BYTES} bayt olabilir.")
            try:
                chunk = base64.b64decode(data, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError("Parça base64 biçiminde değil.") from exc
            if not chunk:
                raise ValueError("Parça boş.")
            if len(chunk) > CHUNK_BYTES:
                raise ValueError(f"Parça en fazla {CHUNK_BYTES} bayt olabilir.")
            if received + len(chunk) > size:
                raise ValueError("Parça dosya boyutunu aşıyor.")
            part = self._part(upload_id)
            if not await asyncio.to_thread(part.is_file):
                # No retry can help: the upload is failed, so the panel stops
                # and re-selecting the file starts a new one.
                await self.store.execute(
                    f"UPDATE {self.releases} SET state='failed', error=?, updated_at=? "
                    "WHERE id=? AND state='uploading'",
                    (UPLOAD_LOST, self._now().isoformat(), upload_id),
                )
                raise ConflictError(UPLOAD_LOST, code="UPLOAD_LOST")
            try:
                await asyncio.to_thread(_write_at, part, received, chunk)
            except OSError as exc:
                # `received_bytes` is unchanged: the same chunk may be sent again.
                raise _storage_error(exc, "Parça") from exc
            await self.store.execute(
                f"UPDATE {self.releases} SET received_bytes=?, updated_at=? "
                "WHERE id=? AND received_bytes=?",
                (received + len(chunk), self._now().isoformat(), upload_id, received),
            )
            return self._status(await self._row(upload_id))

    async def finish_upload(self, *, upload_id: str) -> dict[str, Any]:
        async with self._guard(upload_id):
            row = await self._row(upload_id)
            if row["state"] == "uploading":
                if int(row["received_bytes"]) != int(row["size_bytes"]):
                    raise ConflictError(
                        f"Yükleme tamamlanmadı: {row['received_bytes']}/{row['size_bytes']} bayt."
                    )
                await self.store.execute(
                    f"UPDATE {self.releases} SET state='processing', error='', updated_at=? "
                    "WHERE id=? AND state='uploading'", (self._now().isoformat(), upload_id),
                )
                self._start_processing(upload_id)
        return await self.upload_status(upload_id)

    def _start_processing(self, upload_id: str) -> None:
        if upload_id in self._tasks:
            return
        stop = self._stops[upload_id] = threading.Event()
        task = asyncio.create_task(self._process(upload_id, stop))
        self._tasks[upload_id] = task

        def done(_: asyncio.Task[None]) -> None:
            if self._tasks.get(upload_id) is task:
                del self._tasks[upload_id]
            if self._stops.get(upload_id) is stop:
                del self._stops[upload_id]

        task.add_done_callback(done)

    def resume_processing(self, rows: list[dict[str, Any]]) -> None:
        """Restart unpacking of `processing` records no task works on (restart)."""
        for row in rows:
            if row["state"] == "processing" and row["id"] not in self._tasks:
                self._start_processing(row["id"])

    async def wait_idle(self) -> None:
        """Wait for background package processing (tests, shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks.values()), return_exceptions=True)
            # Let the done callbacks drop the finished tasks.
            await asyncio.sleep(0)

    async def _process(self, upload_id: str, stop: threading.Event) -> None:
        try:
            row = await self._row(upload_id)
            if row["state"] != "processing":
                return
            part, target = self._part(upload_id), self._release_dir(upload_id)
            # A single APK moved to `base.apk` just before a restart goes back.
            await asyncio.to_thread(recover_single, part, target)
            if not await asyncio.to_thread(part.is_file):
                raise PackageError("Yüklenen dosya sunucuda yok. Dosyayı yeniden yükleyin.")
            await asyncio.to_thread(_truncate, part, int(row["size_bytes"]))
            prepared = await asyncio.to_thread(prepare_release, part, target,
                                               reserve_bytes=RESERVE_BYTES, stop=stop.is_set)
            app_name = await self._suggest_name(prepared)
            await self.store.execute(
                f"UPDATE {self.releases} SET state='draft', package_name=?, app_name=?, kind=?, "
                "version_code=?, version_name=?, min_sdk=?, total_bytes=?, files_json=?, "
                "error='', updated_at=? WHERE id=? AND state='processing'",
                (prepared.package_name, app_name, prepared.kind, prepared.version_code,
                 prepared.version_name[:100], prepared.min_sdk, prepared.total_bytes,
                 json.dumps([{"name": item.name, "sizeBytes": item.size_bytes,
                              "sha256": item.sha256, "splitName": item.split_name}
                             for item in prepared.files], separators=(",", ":")),
                 self._now().isoformat(), upload_id),
            )
            still = await self.store.fetch_one(
                f"SELECT state FROM {self.releases} WHERE id=?", (upload_id,)
            )
            if not still or still["state"] != "draft":
                # Thrown away while unpacking: leave nothing behind.
                await self._drop_files(upload_id)
                return
            # A bundle's upload goes only now: a restart before the draft was
            # recorded unpacks it again (a single APK was already moved).
            await asyncio.to_thread(_remove, part)
        except NotFoundError:
            await self._drop_files(upload_id)
        except PackageError as exc:
            await self._fail(upload_id, str(exc))
        except Exception as exc:  # noqa: BLE001 — the panel must see a failure, not "processing" forever
            await self._fail(upload_id, f"Paket işlenemedi ({type(exc).__name__}).")

    async def _fail(self, upload_id: str, message: str) -> None:
        await self._drop_files(upload_id)
        await self.store.execute(
            f"UPDATE {self.releases} SET state='failed', error=?, updated_at=? "
            "WHERE id=? AND state='processing'",
            (message[:500], self._now().isoformat(), upload_id),
        )

    async def _suggest_name(self, prepared: PreparedRelease) -> str:
        """Managed name → manifest label → a tablet's inventory label → package."""
        managed = await self.store.fetch_one(
            f"SELECT app_name FROM {self.managed} WHERE package_name=?", (prepared.package_name,)
        )
        if managed and managed["app_name"]:
            return str(managed["app_name"])[:MAX_APP_NAME]
        if prepared.label:
            return prepared.label[:MAX_APP_NAME]
        known = await self.store.fetch_one(
            f"SELECT app_label FROM {self.inventory} WHERE package_name=? AND app_label<>'' "
            "LIMIT 1", (prepared.package_name,),
        )
        if known and known["app_label"]:
            return str(known["app_label"])[:MAX_APP_NAME]
        return prepared.package_name[:MAX_APP_NAME]

    async def _referenced(self, release_id: str) -> bool:
        return await self.store.fetch_one(
            f"SELECT package_name FROM {self.managed} WHERE release_id=?", (release_id,)
        ) is not None

    async def delete_release(self, *, release_id: str) -> dict[str, Any]:
        """Throw away an upload or draft; a processing one stops unpacking first."""
        async with self._guard(release_id):
            row = await self._row(release_id)
            if row["state"] == "published" or await self._referenced(release_id):
                raise ConflictError("Dağıtımdaki sürüm atılamaz. Önce dağıtımı bitirin.")
            task = self._tasks.get(release_id)
            stop = self._stops.get(release_id)
            if stop is not None:
                stop.set()
            # The record goes first: whatever the task still does finds no
            # record and removes its own files.
            await self.store.execute(f"DELETE FROM {self.releases} WHERE id=?", (release_id,))
            if task is not None:
                await asyncio.wait({task}, timeout=STOP_WAIT_SECONDS)
            await self._drop_files(release_id)
        return {"deleted": True, "releaseId": release_id}

    # ------------------------------------------------------------ yayın

    async def publish(self, *, release_id: str, app_name: str) -> dict[str, Any]:
        """Distribute a draft.

        Lock order: the release's own lock (excludes `delete_release` and the
        cleanup of the same draft), then the queue lock. Write order, without
        a transaction: `managed_apps` first, then the release's state, then
        the previous release. A crash after the first write leaves a draft the
        catalog already serves; cleanup never deletes a release `managed_apps`
        points to and repairs both halves (see `cleanup`), and publishing the
        same draft again finishes the job.
        """
        async with self._guard(release_id), self._queue_lock:
            row = await self._row(release_id)
            if row["state"] != "draft":
                raise ConflictError("Yalnız işlenmiş bir taslak dağıtılabilir.")
            package = row["package_name"]
            current = await self.store.fetch_one(
                f"SELECT m.release_id, m.app_name, r.version_code FROM {self.managed} m "
                f"JOIN {self.releases} r ON r.id = m.release_id WHERE m.package_name=?",
                (package,),
            )
            if current and current["release_id"] == release_id:
                current = None          # an interrupted publish of this draft
            if current and int(current["version_code"]) >= int(row["version_code"]):
                raise ConflictError(
                    f"Bu sürüm ({row['version_code']}) dağıtılan sürümden "
                    f"({current['version_code']}) yeni değil. Android eski ya da aynı "
                    "sürümün üstüne kuramaz; versionCode'u daha büyük bir paket yükleyin."
                )
            name = str(app_name or "").strip()[:MAX_APP_NAME] or row["app_name"] or package
            stamp = self._now().isoformat()
            await self.store.execute(
                f"INSERT INTO {self.managed} (package_name, app_name, kind, release_id, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(package_name) DO UPDATE SET app_name=excluded.app_name, "
                "kind=excluded.kind, release_id=excluded.release_id, "
                "updated_at=excluded.updated_at",
                (package, name, row["kind"], release_id, stamp, stamp),
            )
            await self.store.execute(
                f"UPDATE {self.releases} SET state='published', app_name=?, published_at=?, "
                "updated_at=? WHERE id=?", (name, stamp, stamp, release_id),
            )
            previous = current["release_id"] if current else None
            if previous:
                # Tablets still downloading the old release now get 410 RELEASE_GONE.
                await self.store.execute(
                    f"UPDATE {self.releases} SET state='superseded', updated_at=? WHERE id=?",
                    (stamp, previous),
                )
                await self.store.execute(
                    f"DELETE FROM {self.queue} WHERE release_id=?", (previous,)
                )
            await self.store.execute(
                f"DELETE FROM {self.removals} WHERE package_name=?", (package,)
            )
            self.invalidate()
        if previous:
            await self._drop_files(previous)
        return {"published": True, "previousReleaseId": previous,
                **self._status(await self._row(release_id))}

    async def remove_app(self, *, package_name: str, uninstall: bool,
                         actor: str) -> dict[str, Any]:
        async with self._queue_lock:
            row = await self.store.fetch_one(
                f"SELECT * FROM {self.managed} WHERE package_name=?", (package_name,)
            )
            if not row:
                raise NotFoundError("Dağıtılan uygulama bulunamadı.")
            if row["kind"] == "self" or package_name == SELF_PACKAGE:
                raise ConflictError("Tablet uygulamasının (TabletKontrol) dağıtımı bitirilemez; "
                                    "yalnız yeni sürümü yüklenebilir.")
            stamp = self._now().isoformat()
            await self.store.execute(
                f"DELETE FROM {self.managed} WHERE package_name=?", (package_name,)
            )
            await self.store.execute(
                f"UPDATE {self.releases} SET state='withdrawn', updated_at=? WHERE id=?",
                (stamp, row["release_id"]),
            )
            await self.store.execute(
                f"DELETE FROM {self.queue} WHERE release_id=?", (row["release_id"],)
            )
            if uninstall:
                await self.store.execute(
                    f"INSERT INTO {self.removals} (package_name, removed_at, removed_by) "
                    "VALUES (?, ?, ?) ON CONFLICT(package_name) DO UPDATE SET "
                    "removed_at=excluded.removed_at, removed_by=excluded.removed_by",
                    (package_name, stamp, actor),
                )
            self.invalidate()
        await self._drop_files(row["release_id"])
        return {"removed": True, "packageName": package_name, "uninstall": bool(uninstall)}

    async def prioritize(self, *, package_name: str, device_id: str) -> dict[str, Any]:
        item = (await self.catalog())["packages"].get(package_name)
        if not item:
            raise NotFoundError("Dağıtılan uygulama bulunamadı.")
        device = await self.store.fetch_one(
            f"SELECT id, token_hash, revoked_at FROM {self.devices} WHERE id=?", (device_id,)
        )
        if not device:
            raise NotFoundError("Tablet bulunamadı.")
        if not device["token_hash"] or device["revoked_at"]:
            raise ConflictError("Tablet eşli değil.")
        release_id = item["releaseId"]
        stamp = self._now().isoformat()
        async with self._queue_lock:
            row = await self.store.fetch_one(
                f"SELECT state FROM {self.queue} WHERE device_id=? AND release_id=?",
                (device_id, release_id),
            )
            if row and row["state"] == "installed":
                raise ConflictError("Uygulama bu tablette zaten kurulu.")
            if row:
                await self.store.execute(
                    f"UPDATE {self.queue} SET priority_at=?, state='waiting', "
                    "wanted_at=CASE WHEN state='failed' THEN ? ELSE wanted_at END, updated_at=? "
                    "WHERE device_id=? AND release_id=?",
                    (stamp, stamp, stamp, device_id, release_id),
                )
            else:
                # Counted in the queue once the tablet asks for it in a sync.
                await self.store.execute(
                    f"INSERT INTO {self.queue} (device_id, release_id, package_name, state, "
                    "wanted_at, priority_at, updated_at) VALUES (?, ?, ?, 'waiting', ?, ?, ?)",
                    (device_id, release_id, package_name, stamp, stamp, stamp),
                )
        return {"prioritized": True, "packageName": package_name, "deviceId": device_id}

    async def forget_device(self, device_id: str) -> None:
        await self.store.execute(f"DELETE FROM {self.queue} WHERE device_id=?", (device_id,))

    # -------------------------------------------------------------- sıra

    def _ordered(self, rows: list[dict[str, Any]], releases: dict[str, Any],
                 current: datetime) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        """(device → its valid lease row, waiting rows in queue order).

        The waiting rows still include other wanted releases of devices that
        hold a lease; `_positions` counts devices, not rows.
        """
        stamp = current.isoformat()
        cutoff = (current - timedelta(seconds=self.queue_stale_seconds)).isoformat()
        leased: dict[str, dict[str, Any]] = {}
        waiting = []
        for row in rows:
            if row["state"] != "waiting" or row["release_id"] not in releases:
                continue
            if row.get("lease_until") and row["lease_until"] > stamp:
                leased.setdefault(row["device_id"], row)
            elif row.get("seen_at") and row["seen_at"] >= cutoff:
                waiting.append(row)
        waiting.sort(key=lambda row: (
            releases[row["release_id"]]["kind"] != "self",
            row.get("priority_at") is None, row.get("priority_at") or "",
            row["wanted_at"], row["device_id"], row["release_id"],
        ))
        return leased, waiting

    @staticmethod
    def _positions(leased: dict[str, Any], waiting: list[dict[str, Any]]
                   ) -> tuple[dict[str, int], dict[tuple[str, str], int]]:
        """(device → rank among devices without a lease, row → queue position).

        A device without a lease stands in the queue once, at its best row;
        its rank is its place among those devices (1 = takes the next slot).
        A row's position is 1 + the number of other such devices standing
        before it, so a device's best row has its rank and a further wanted
        release (or one of a device downloading something else) shows how
        many tablets go first.
        """
        ranks: dict[str, int] = {}
        positions: dict[tuple[str, str], int] = {}
        for row in waiting:
            device = row["device_id"]
            ahead = len(ranks) - (1 if device in ranks else 0)
            positions[(device, row["release_id"])] = ahead + 1
            if device not in leased and device not in ranks:
                ranks[device] = len(ranks) + 1
        return ranks, positions

    async def _waiting_rows(self, current: datetime) -> list[dict[str, Any]]:
        """Every waiting row; expired leases go to the back of the queue first.

        "Süresi dolan yuva başka tablete geçer": a lease that ran out without
        an `active` report must not win the slot back on order alone.
        """
        stamp = current.isoformat()
        rows = [dict(row) for row in await self.store.fetch_all(
            f"SELECT device_id, release_id, package_name, state, wanted_at, seen_at, "
            f"priority_at, lease_until FROM {self.queue} WHERE state='waiting'"
        )]
        for row in rows:
            if row["lease_until"] and row["lease_until"] <= stamp:
                await self.store.execute(
                    f"UPDATE {self.queue} SET lease_until=NULL, wanted_at=?, "
                    "priority_at=CASE WHEN priority_at IS NULL THEN NULL ELSE ? END, "
                    "updated_at=? WHERE device_id=? AND release_id=? AND lease_until=?",
                    (stamp, stamp, stamp, row["device_id"], row["release_id"],
                     row["lease_until"]),
                )
                row["lease_until"] = None
                row["wanted_at"] = stamp
                if row["priority_at"]:
                    row["priority_at"] = stamp
        return rows

    def _grant(self, device_id: str, base_url: str, row: dict[str, Any],
               release: dict[str, Any], current: datetime) -> dict[str, Any]:
        until = datetime.fromisoformat(row["lease_until"])
        prefix = f"{base_url}/devices/{device_id}/releases/{release['releaseId']}/files"
        return {
            "releaseId": release["releaseId"],
            "leaseSeconds": max(1, math.ceil((until - current).total_seconds())),
            "files": [{"name": item["name"], "url": f"{prefix}/{index}"}
                      for index, item in enumerate(release["files"])],
        }

    async def sync_installs(self, *, device_id: str, installs: dict[str, Any],
                            base_url: str) -> tuple[dict[str, Any], bool]:
        """Apply the tablet's `installs` report and answer `install`.

        Returns the `install` object and whether the tablet is at the head of
        the queue (its next sync is then at most `HEAD_SYNC_SECONDS` away).
        """
        async with self._queue_lock:
            catalog = await self.catalog()
            releases: dict[str, Any] = catalog["releases"]
            current = self._now()
            stamp = current.isoformat()
            renewed = (current + timedelta(seconds=self.lease_seconds)).isoformat()
            stored = {row["release_id"]: dict(row) for row in await self.store.fetch_all(
                f"SELECT * FROM {self.queue} WHERE device_id=?", (device_id,)
            )}
            changed: dict[str, dict[str, Any]] = {}
            created: set[str] = set()

            def touch(release_id: str) -> dict[str, Any] | None:
                if release_id not in releases:
                    return None
                if release_id in changed:
                    return changed[release_id]
                row = stored.get(release_id)
                if row is None:
                    created.add(release_id)
                    row = {"device_id": device_id, "release_id": release_id,
                           "package_name": releases[release_id]["packageName"],
                           "state": "waiting", "wanted_at": stamp, "seen_at": stamp,
                           "priority_at": None, "lease_until": None, "received_bytes": 0,
                           "last_error": "", "error_at": None, "installed_version_code": None}
                else:
                    row = dict(row)
                changed[release_id] = row
                return row

            reported: set[str] = set()
            for result in (installs.get("results") or [])[:MAX_RESULTS]:
                release_id = str(result.get("releaseId") or "")
                row = touch(release_id)
                if row is None:
                    continue
                reported.add(release_id)
                row.update(lease_until=None, priority_at=None, seen_at=stamp)
                if result.get("ok"):
                    code = result.get("versionCode")
                    row.update(state="installed", last_error="", error_at=None,
                               installed_version_code=code if isinstance(code, int) else None,
                               received_bytes=releases[release_id]["totalBytes"])
                else:
                    message = str(result.get("message") or "").strip()[:300]
                    row.update(state="failed", last_error=message or "install_failed",
                               error_at=stamp)

            mentioned = set(reported)
            active = installs.get("active") or None
            if active:
                release_id = str(active.get("releaseId") or "")
                row = touch(release_id) if release_id not in reported else None
                if row is not None:
                    mentioned.add(release_id)
                    total = releases[release_id]["totalBytes"]
                    received = active.get("receivedBytes")
                    row["received_bytes"] = max(0, min(total, int(received or 0)))
                    row["seen_at"] = stamp
                    if row["state"] != "waiting":
                        row.update(state="waiting", wanted_at=stamp)
                    if row["lease_until"] and row["lease_until"] > stamp:
                        row["lease_until"] = renewed
            for release_id in dict.fromkeys(str(item) for item in
                                            (installs.get("wanted") or [])[:MAX_WANTED]):
                if release_id in reported:
                    continue
                row = touch(release_id)
                if row is None:
                    continue
                mentioned.add(release_id)
                row["seen_at"] = stamp
                if row["state"] != "waiting":
                    # A failed (or lost) install asked for again joins at the back.
                    row.update(state="waiting", wanted_at=stamp)
            priority = str(installs.get("priority") or "")
            if priority and priority not in reported:
                row = touch(priority)
                if row is not None:
                    mentioned.add(priority)
                    if row["state"] != "waiting":
                        row.update(state="waiting", wanted_at=stamp)
                    row["priority_at"] = row["priority_at"] or stamp
                    row["seen_at"] = stamp
            for release_id, row in stored.items():
                # The tablet no longer asks for it (installed meanwhile, no room,
                # waiting out a failure): it leaves the queue and frees its lease.
                if release_id in mentioned or row["state"] != "waiting":
                    continue
                if row["seen_at"] or row["lease_until"]:
                    target = touch(release_id)
                    if target is not None:
                        target.update(seen_at=None, lease_until=None)

            for release_id, row in changed.items():
                if release_id in created:
                    await self.store.execute(
                        f"INSERT INTO {self.queue} (device_id, release_id, package_name, state, "
                        "wanted_at, seen_at, priority_at, lease_until, received_bytes, "
                        "last_error, error_at, installed_version_code, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (device_id, release_id, row["package_name"], row["state"],
                         row["wanted_at"], row["seen_at"], row["priority_at"],
                         row["lease_until"], row["received_bytes"], row["last_error"],
                         row["error_at"], row["installed_version_code"], stamp),
                    )
                else:
                    await self.store.execute(
                        f"UPDATE {self.queue} SET state=?, wanted_at=?, seen_at=?, "
                        "priority_at=?, lease_until=?, received_bytes=?, last_error=?, "
                        "error_at=?, installed_version_code=?, updated_at=? "
                        "WHERE device_id=? AND release_id=?",
                        (row["state"], row["wanted_at"], row["seen_at"], row["priority_at"],
                         row["lease_until"], row["received_bytes"], row["last_error"],
                         row["error_at"], row["installed_version_code"], stamp,
                         device_id, release_id),
                    )

            leased, waiting = self._ordered(await self._waiting_rows(current), releases,
                                            current)
            slots = self.download_slots
            grant_row = leased.get(device_id)
            if grant_row is None and len(leased) < slots:
                ranks, _ = self._positions(leased, waiting)
                if ranks.get(device_id, slots + 1) <= slots - len(leased):
                    grant_row = next(row for row in waiting if row["device_id"] == device_id)
                    await self.store.execute(
                        f"UPDATE {self.queue} SET lease_until=?, updated_at=? "
                        "WHERE device_id=? AND release_id=?",
                        (renewed, stamp, device_id, grant_row["release_id"]),
                    )
                    grant_row["lease_until"] = renewed
                    waiting.remove(grant_row)
                    leased[device_id] = grant_row
            ranks, positions = self._positions(leased, waiting)
            queue = [{"releaseId": row["release_id"],
                      "position": positions[(device_id, row["release_id"])]}
                     for row in waiting if row["device_id"] == device_id]
            # The first `slots` devices without a lease take the next free slots;
            # they sync often so a slot that frees up does not stay idle.
            head = grant_row is None and ranks.get(device_id, slots + 1) <= slots
            grant = None
            if grant_row is not None:
                grant = self._grant(device_id, base_url, grant_row,
                                    releases[grant_row["release_id"]], current)
            return {"grant": grant, "queue": queue}, head

    async def download_path(self, *, device_id: str, release_id: str, index: int) -> Path:
        """File behind a grant URL; the lease decides, not the file's existence."""
        release = (await self.catalog())["releases"].get(release_id)
        if release is None:
            raise TabletError("RELEASE_GONE", "Bu sürüm artık dağıtılmıyor.")
        row = await self.store.fetch_one(
            f"SELECT lease_until FROM {self.queue} WHERE device_id=? AND release_id=? "
            "AND state='waiting'", (device_id, release_id),
        )
        if not row or not row["lease_until"] or row["lease_until"] <= self._now().isoformat():
            raise TabletError("DOWNLOAD_NOT_GRANTED",
                              "Bu tabletin bu sürüm için indirme sırası yok.")
        if not 0 <= index < len(release["files"]):
            raise NotFoundError("Dosya bulunamadı.")
        path = self._release_dir(release_id) / release["files"][index]["name"]
        if not await asyncio.to_thread(path.is_file):
            raise TabletError("RELEASE_GONE", "Bu sürümün dosyası artık sunucuda yok.")
        return path

    # -------------------------------------------------------------- panel

    @staticmethod
    def _version(value: Any) -> int | None:
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    def _device_status(self, row: dict[str, Any] | None, installed_code: int | None,
                       release: dict[str, Any], stamp: str) -> str:
        if row is not None:
            if row["state"] == "installed":
                return "installed"
            if row["state"] == "failed":
                return "failed"
            if row["lease_until"] and row["lease_until"] > stamp:
                return "downloading"
            return "waiting"
        if installed_code is not None and installed_code >= release["versionCode"]:
            return "installed"
        return "unknown"

    async def _paired_devices(self) -> list[dict[str, Any]]:
        return [dict(row) for row in await self.store.fetch_all(
            f"SELECT id, name, app_version_code, self_update FROM {self.devices} "
            "WHERE token_hash IS NOT NULL "
            "AND revoked_at IS NULL ORDER BY name"
        )]

    async def _installed_codes(self, packages: list[str]) -> dict[tuple[str, str], int]:
        codes: dict[tuple[str, str], int] = {}
        for start in range(0, len(packages), IN_CHUNK):
            part = packages[start:start + IN_CHUNK]
            for row in await self.store.fetch_all(
                f"SELECT device_id, package_name, version_code FROM {self.inventory} "
                f"WHERE package_name IN ({_marks(len(part))})", tuple(part),
            ):
                code = self._version(row["version_code"])
                if code is not None:
                    codes[(row["device_id"], row["package_name"])] = code
        return codes

    async def _queue_rows(self, release_ids: list[str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for start in range(0, len(release_ids), IN_CHUNK):
            part = release_ids[start:start + IN_CHUNK]
            rows.extend(dict(row) for row in await self.store.fetch_all(
                f"SELECT * FROM {self.queue} WHERE release_id IN ({_marks(len(part))})",
                tuple(part),
            ))
        return rows

    async def overview(self) -> dict[str, Any]:
        """`GET /apps`: managed apps with per-tablet counts, drafts and slot use."""
        catalog = await self.catalog()
        current = self._now()
        stamp = current.isoformat()
        devices = await self._paired_devices()
        releases: dict[str, Any] = catalog["releases"]
        rows = await self._queue_rows(list(releases))
        by_key = {(row["device_id"], row["release_id"]): row for row in rows}
        codes = await self._installed_codes(list(catalog["packages"]))
        leased, _ = self._ordered(rows, releases, current)
        apps = []
        for item in catalog["items"]:
            counts = {"installed": 0, "downloading": 0, "waiting": 0, "failed": 0, "unknown": 0}
            for device in devices:
                reported = self._self_status(device, item) if item["kind"] == "self" else None
                status = reported[0] if reported else self._device_status(
                    by_key.get((device["id"], item["releaseId"])),
                    codes.get((device["id"], item["packageName"])), item, stamp,
                )
                counts[status] += 1
            apps.append({**item, "publishedAt": catalog["publishedAt"].get(item["releaseId"]),
                         "counts": {**counts, "total": len(devices)}})
        drafts = await self.store.fetch_all(
            f"SELECT * FROM {self.releases} WHERE state IN ({_marks(len(OPEN_STATES))}) "
            "ORDER BY created_at DESC", OPEN_STATES,
        )
        # After a restart nothing unpacks a `processing` draft; the panel's
        # list is the natural place to notice (in-memory check, no query).
        self.resume_processing([dict(row) for row in drafts])
        return {
            "serverTime": stamp, "version": catalog["version"], "apps": apps,
            "drafts": [self._status(dict(row), catalog["packages"].get(row["package_name"]))
                       for row in drafts],
            "removed": catalog["remove"],
            "slots": {"total": self.download_slots, "used": len(leased)},
            "chunkBytes": CHUNK_BYTES, "maxUploadBytes": self.max_upload_bytes,
            "storageReady": self.storage_dir is not None,
        }

    async def app_devices(self, *, package_name: str) -> dict[str, Any]:
        """`GET /apps/{package}/devices`: status, queue place, progress, last error."""
        catalog = await self.catalog()
        item = catalog["packages"].get(package_name)
        if not item:
            raise NotFoundError("Dağıtılan uygulama bulunamadı.")
        current = self._now()
        stamp = current.isoformat()
        releases: dict[str, Any] = catalog["releases"]
        rows = await self._queue_rows(list(releases))
        leased, waiting = self._ordered(rows, releases, current)
        _, positions = self._positions(leased, waiting)
        mine = {row["device_id"]: row for row in rows if row["release_id"] == item["releaseId"]}
        codes = await self._installed_codes([package_name])
        result = []
        for device in await self._paired_devices():
            reported = self._self_status(device, item) if item["kind"] == "self" else None
            if reported is not None:
                # v2.2 tablets update themselves outside the queue (sözleşme v2.2).
                result.append(self._self_row(device, item, *reported))
                continue
            row = mine.get(device["id"])
            status = self._device_status(row, codes.get((device["id"], package_name)), item,
                                         stamp)
            received = int(row["received_bytes"]) if row else 0
            total = item["totalBytes"]
            result.append({
                "deviceId": device["id"], "deviceName": device["name"], "status": status,
                "position": positions.get((device["id"], item["releaseId"])),
                "receivedBytes": received,
                "progressPercent": (min(100, math.floor(received * 100 / total))
                                    if total and status in ("downloading", "waiting")
                                    and received else None),
                "priority": bool(row and row.get("priority_at")),
                "lastError": (row or {}).get("last_error") or "",
                "errorAt": (row or {}).get("error_at"),
                "installedVersionCode": (row or {}).get("installed_version_code")
                or codes.get((device["id"], package_name)),
                "seenAt": (row or {}).get("seen_at"),
                "leaseUntil": (row or {}).get("lease_until") if status == "downloading" else None,
                "selfState": None, "nextRetryAt": None,
            })
        return {"serverTime": stamp, "app": item,
                "slots": {"total": self.download_slots, "used": len(leased)},
                "devices": result}

    @staticmethod
    def _self_row(device: dict[str, Any], item: dict[str, Any], status: str,
                  report: dict[str, Any]) -> dict[str, Any]:
        received = report.get("receivedBytes") or 0
        total = item["totalBytes"]
        downloading = report.get("state") == "downloading" and status == "downloading"
        return {
            "deviceId": device["id"], "deviceName": device["name"], "status": status,
            "position": None, "receivedBytes": received,
            "progressPercent": (min(100, math.floor(received * 100 / total))
                                if downloading and total else None),
            "priority": False,
            "lastError": (report.get("error") or "") if status == "failed" else "",
            "errorAt": report.get("at") if status == "failed" else None,
            "installedVersionCode": device.get("app_version_code"),
            "seenAt": report.get("at"), "leaseUntil": None,
            "selfState": report.get("state"),
            "nextRetryAt": report.get("nextRetryAt") if status == "failed" else None,
        }

    # ------------------------------------------------------------ temizlik

    async def _repair(self) -> int:
        """Finish what a crash in the middle of `publish`/`remove_app` left.

        - A draft `managed_apps` points to is served already: it is published.
        - A published release nothing points to lost its place: it is
          superseded, its queue rows and files go.
        Under the queue lock, so a publish in progress is never seen halfway.
        """
        repaired = 0
        dropped: list[str] = []
        async with self._queue_lock:
            stamp = self._now().isoformat()
            for row in await self.store.fetch_all(
                f"SELECT r.id FROM {self.releases} r JOIN {self.managed} m "
                "ON m.release_id = r.id WHERE r.state='draft'"
            ):
                await self.store.execute(
                    f"UPDATE {self.releases} SET state='published', published_at=?, "
                    "updated_at=? WHERE id=? AND state='draft'", (stamp, stamp, row["id"]),
                )
                repaired += 1
            for row in await self.store.fetch_all(
                f"SELECT id FROM {self.releases} WHERE state='published' "
                f"AND id NOT IN (SELECT release_id FROM {self.managed})"
            ):
                await self.store.execute(
                    f"UPDATE {self.releases} SET state='superseded', updated_at=? "
                    "WHERE id=? AND state='published'", (stamp, row["id"]),
                )
                await self.store.execute(
                    f"DELETE FROM {self.queue} WHERE release_id=?", (row["id"],)
                )
                dropped.append(row["id"])
                repaired += 1
            if repaired:
                self.invalidate()
        for release_id in dropped:
            await self._drop_files(release_id)
        return repaired

    async def cleanup(self) -> dict[str, int]:
        """Daily: unfinished uploads, unpublished drafts, failed rows, orphan files.

        Records in `uploading`, `processing` (with no task working on it),
        `draft` (processed but never published) or `failed` state untouched
        for two days go with their files; a release `managed_apps` points to
        never does. Each deletion re-checks the record under the release's
        own lock, so it cannot cross a publish or a discard of the same
        release. Files no live record points to go after an hour (a
        just-started upload is safe).
        """
        repaired = await self._repair()
        cutoff = (self._now() - STALE_UPLOAD).isoformat()
        stale_sql = (f"SELECT id FROM {self.releases} WHERE id=? AND "
                     f"state IN ({_marks(len(OPEN_STATES))}) AND updated_at<? "
                     f"AND id NOT IN (SELECT release_id FROM {self.managed})")
        stale = await self.store.fetch_all(
            f"SELECT id FROM {self.releases} WHERE state IN ({_marks(len(OPEN_STATES))}) "
            f"AND updated_at<? AND id NOT IN (SELECT release_id FROM {self.managed})",
            (*OPEN_STATES, cutoff),
        )
        removed = 0
        for row in stale:
            release_id = row["id"]
            async with self._guard(release_id):
                if release_id in self._tasks or not await self.store.fetch_one(
                        stale_sql, (release_id, *OPEN_STATES, cutoff)):
                    continue
                await self.store.execute(f"DELETE FROM {self.releases} WHERE id=?",
                                         (release_id,))
                await self._drop_files(release_id)
            removed += 1
        orphans = 0
        if self.storage_dir is not None:
            states = await self.store.fetch_all(
                f"SELECT id, state FROM {self.releases} "
                f"WHERE state IN ({_marks(len(LIVE_STATES))})", LIVE_STATES,
            )
            parts = {row["id"] for row in states if row["state"] in PART_STATES}
            live = {row["id"] for row in states}
            orphans = await asyncio.to_thread(_remove_orphans, self.storage_dir, parts, live,
                                              time.time() - ORPHAN_GRACE_SECONDS)
        return {"uploads": removed, "orphanFiles": orphans, "repaired": repaired}
