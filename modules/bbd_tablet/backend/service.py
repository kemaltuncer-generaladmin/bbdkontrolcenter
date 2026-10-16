"""Profiles, devices, usage and student extra time owned by Control Center.

Contract: TabletKontrol `docs/CONTROL_CENTER_INTEGRATION.md` (v2). The server
shares a host with bbdstore, so tablet paths are kept to a few indexed queries:
`sync` returns only what changed, usage totals come from `daily_usage`, the
canteen student list and profile views are cached in memory, and the stale
session sweep runs at most once a minute. History older than
`history_retention_days` is purged at most once a day from the panel's
overview, never from the tablet's sync.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import secrets
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .distribution import HEAD_SYNC_SECONDS, AppDistribution
from .errors import (
    CanteenUnavailableError,
    ConflictError,
    NotFoundError,
    TabletError,
)
from .rules import (
    OFFLINE_MODES,
    display_enrollment_code,
    inside_usage_window,
    new_enrollment_code,
    next_window_start,
    normalize_app_rules,
    normalize_command,
    normalize_daily_total,
    normalize_enrollment_code,
    normalize_policy_detail,
    normalize_self_update,
    normalize_usage_windows,
    session_view,
)

# Stable storage keys from TabletKontrol's SystemPolicyControl. The debugging
# restriction is deliberately excluded because it can remove recovery access.
SYSTEM_CONTROL_KEYS = frozenset({
    "usb_file_transfer",
    "unknown_source_installs",
    "app_stores_and_installers",
    "account_modification",
    "vpn_configuration",
    "network_reset",
    "tethering",
    "wifi_configuration",
    "private_dns",
    "date_and_time",
    "safe_boot",
    "factory_reset",
    "user_and_profile_creation",
    "app_uninstall",
    "app_control_settings",
    "student_escape_hatches",
})

PROFILE_CACHE_SIZE = 256
STUDENT_CACHE_SECONDS = 300
STALE_SCAN_SECONDS = 60
PURGE_SECONDS = 24 * 60 * 60
COMMAND_TTL = timedelta(hours=24)
COMMAND_RETENTION = timedelta(days=7)
# Commands per sync answer and command results read per sync request.
MAX_SYNC_COMMANDS = 100
# A tablet that syncs without a session cannot be using one the server still
# holds open for it; the grace keeps a login racing that sync alive.
ORPHAN_SESSION_GRACE = timedelta(minutes=2)
LEGACY_ONLINE_SECONDS = 120
BULK_ACTIONS = frozenset({"lock", "unlock", "announce", "assign_profile", "command",
                          "reset_login_lock", "delete"})
ONLINE_FACTOR = 2.5
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW = timedelta(minutes=5)
MAX_BLOCK = timedelta(days=366)
#: Bellekteki öğrenci özel süre kayıtlarının üst sınırı (dolunca temizlenir).
STUDENT_LIMIT_CACHE_SIZE = 5000
INSERT_CHUNK = 200
IN_CHUNK = 500
# Shown in history views for sessions whose tablet record was deleted.
DELETED_DEVICE_NAME = "Silinmiş tablet"
# Sözleşme v2.2 diagnostics: text limit, reports kept per tablet, retention.
MAX_DIAGNOSTICS_TEXT = 262_144
DIAGNOSTICS_KEEP = 5
DIAGNOSTICS_RETENTION = timedelta(days=30)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def now() -> str:
    return _utcnow().isoformat()


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("Saat dilimi tanınmıyor.") from exc


def _marks(count: int) -> str:
    return ", ".join("?" * count)


def _chunks(items: list[Any], size: int) -> Iterable[list[Any]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


class TabletService:
    def __init__(self, *, store: Any, canteen: Any, config: dict[str, Any],
                 clock: Callable[[], datetime] | None = None,
                 storage_dir: Path | None = None) -> None:
        self.store = store
        self.canteen = canteen
        self.config = config or {}
        self._clock = clock or _utcnow
        self._profile_lock = asyncio.Lock()
        self._profile_cache: dict[tuple[str, int], dict[str, Any]] = {}
        # Öğrenciye özel süreler (tablet-sinir): sync'in "değişiklik yok" yolu sorgu eklemesin
        # diye bellekte; tek yazıcı bu servistir (tek uvicorn süreci, katalog önbelleği gibi).
        self._student_limit_cache: dict[str, dict[str, dict[str, Any]]] = {}
        self._students_cache: tuple[float, list[dict[str, Any]]] | None = None
        self._last_stale_scan = float("-inf")
        self._last_purge = float("-inf")
        self.profiles = store.table("profiles")
        self.apps = store.table("profile_apps")
        self.system_controls = store.table("profile_system_controls")
        self.devices = store.table("devices")
        self.inventory = store.table("inventory")
        self.sessions = store.table("sessions")
        self.login_failures = store.table("login_failures")
        self.usage = store.table("daily_usage")
        self.batches = store.table("usage_batches")
        self.batch_items = store.table("usage_batch_items")
        self.grants = store.table("grants")
        self.commands = store.table("commands")
        self.blocks = store.table("student_blocks")
        self.student_limits = store.table("student_app_limits")
        self.diagnostics = store.table("diagnostics")
        # App distribution keeps its files under `storage_dir`; without one
        # (tests, a misconfigured install) uploads are refused, sync still works.
        self.distribution = AppDistribution(store=store, config=self.config, clock=self._now,
                                            storage_dir=storage_dir)

    # ------------------------------------------------------------ ayarlar

    def _now(self) -> datetime:
        return self._clock()

    def _int_setting(self, key: str, default: int, low: int, high: int) -> int:
        try:
            value = int(self.config.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(low, min(high, value))

    @property
    def sync_active_seconds(self) -> int:
        return self._int_setting("sync_active_seconds", 60, 15, 3600)

    @property
    def sync_idle_seconds(self) -> int:
        return self._int_setting("sync_idle_seconds", 300, 30, 3600)

    @property
    def usage_flush_seconds(self) -> int:
        return self._int_setting("usage_flush_seconds", 300, 60, 3600)

    @property
    def session_idle_seconds(self) -> int:
        # A slowed-down sync must never make healthy sessions look idle.
        minutes = self._int_setting("session_idle_minutes", 30, 5, 240)
        return max(minutes * 60, 3 * self.sync_active_seconds)

    @property
    def history_retention_days(self) -> int:
        return self._int_setting("history_retention_days", 180, 7, 3650)

    @property
    def public_base_url(self) -> str:
        """Fixed origin for download URLs; empty means "read it from the request"."""
        return str(self.config.get("public_base_url") or "").strip().rstrip("/")

    @property
    def default_timezone(self) -> str:
        return str(self.config.get("timezone") or "Europe/Istanbul")

    # ----------------------------------------------------------- profiller

    def _remember_profile(self, view: dict[str, Any]) -> None:
        if len(self._profile_cache) >= PROFILE_CACHE_SIZE:
            self._profile_cache.clear()
        self._profile_cache[(view["id"], int(view["revision"]))] = view

    @staticmethod
    def _profile_view(row: dict[str, Any], apps: list[dict[str, Any]],
                      controls: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            windows = json.loads(row.get("usage_windows") or "[]")
        except ValueError:
            windows = []
        return {
            "id": row["id"], "name": row["name"], "revision": int(row["revision"]),
            "timezone": row["timezone"], "updatedAt": row["updated_at"],
            "usageWindows": windows,
            "dailyTotalLimitSeconds": int(row.get("daily_total_limit_seconds") or 0),
            "apps": [TabletService._app_view(app) for app in apps],
            "systemControls": {item["control_key"]: bool(item["is_enabled"])
                               for item in controls},
        }

    @staticmethod
    def _app_view(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "packageName": row["package_name"], "appName": row["app_name"],
            "allowed": bool(row["allowed"]), "unlimited": bool(row["unlimited"]),
            "dailyLimitSeconds": int(row["daily_limit_seconds"]),
            "sortOrder": int(row["sort_order"]),
        }

    async def profile(self, profile_id: str | None) -> dict[str, Any] | None:
        """Current revision of a profile. One PK query when the revision is cached."""
        if not profile_id:
            return None
        for _ in range(3):
            row = await self.store.fetch_one(
                f"SELECT * FROM {self.profiles} WHERE id = ?", (profile_id,)
            )
            if not row or int(row["revision"]) < 1:
                return None
            revision = int(row["revision"])
            cached = self._profile_cache.get((profile_id, revision))
            if cached is not None:
                return cached
            apps = await self.store.fetch_all(
                f"SELECT * FROM {self.apps} WHERE profile_id = ? AND revision = ? "
                "ORDER BY sort_order, app_name", (profile_id, revision),
            )
            controls = await self.store.fetch_all(
                f"SELECT control_key, is_enabled FROM {self.system_controls} "
                "WHERE profile_id=? AND revision=? ORDER BY control_key",
                (profile_id, revision),
            )
            check = await self.store.fetch_one(
                f"SELECT revision FROM {self.profiles} WHERE id = ?", (profile_id,)
            )
            # A save that finished between the reads replaced the rows; retry
            # instead of caching a revision with another revision's apps.
            if check and int(check["revision"]) == revision:
                view = self._profile_view(row, apps, controls)
                self._remember_profile(view)
                return view
        return None

    async def all_profiles(self) -> list[dict[str, Any]]:
        """Every published profile; cache misses load in two batched queries."""
        rows = await self.store.fetch_all(
            f"SELECT * FROM {self.profiles} WHERE revision > 0 ORDER BY name"
        )
        missing = [row for row in rows
                   if (row["id"], int(row["revision"])) not in self._profile_cache]
        if missing:
            ids = [row["id"] for row in missing]
            apps: dict[tuple[str, int], list[dict[str, Any]]] = {}
            controls: dict[tuple[str, int], list[dict[str, Any]]] = {}
            for part in _chunks(ids, IN_CHUNK):
                for app in await self.store.fetch_all(
                    f"SELECT * FROM {self.apps} WHERE profile_id IN ({_marks(len(part))}) "
                    "ORDER BY sort_order, app_name", tuple(part),
                ):
                    apps.setdefault((app["profile_id"], int(app["revision"])), []).append(app)
                for item in await self.store.fetch_all(
                    f"SELECT profile_id, revision, control_key, is_enabled "
                    f"FROM {self.system_controls} WHERE profile_id IN ({_marks(len(part))}) "
                    "ORDER BY control_key", tuple(part),
                ):
                    controls.setdefault((item["profile_id"], int(item["revision"])),
                                        []).append(item)
            current = {
                row["id"]: int(row["revision"])
                for part in _chunks(ids, IN_CHUNK)
                for row in await self.store.fetch_all(
                    f"SELECT id, revision FROM {self.profiles} "
                    f"WHERE id IN ({_marks(len(part))})", tuple(part),
                )
            }
            for row in missing:
                key = (row["id"], int(row["revision"]))
                if current.get(row["id"]) == key[1]:
                    self._remember_profile(self._profile_view(
                        row, apps.get(key, []), controls.get(key, []),
                    ))
        views = []
        for row in rows:
            view = self._profile_cache.get((row["id"], int(row["revision"])))
            if view is None:
                view = await self.profile(row["id"])
            if view is not None:
                views.append(view)
        return views

    async def save_profile(self, *, profile_id: str | None, name: str,
                           timezone: str, apps: list[dict[str, Any]],
                           system_controls: dict[str, Any] | None = None,
                           usage_windows: list[dict[str, Any]] | None = None,
                           daily_total_limit_seconds: int | None = None,
                           ) -> dict[str, Any]:
        async with self._profile_lock:
            return await self._save_profile(profile_id=profile_id, name=name,
                                            timezone=timezone, apps=apps,
                                            system_controls=system_controls,
                                            usage_windows=usage_windows,
                                            daily_total_limit_seconds=daily_total_limit_seconds)

    async def _save_profile(self, *, profile_id: str | None, name: str,
                            timezone: str, apps: list[dict[str, Any]],
                            system_controls: dict[str, Any] | None,
                            usage_windows: list[dict[str, Any]] | None,
                            daily_total_limit_seconds: int | None = None) -> dict[str, Any]:
        if system_controls is not None:
            if not isinstance(system_controls, dict):
                raise ValueError("Sistem kontrolleri anahtar-değer haritası olmalı.")
            unknown = set(system_controls) - SYSTEM_CONTROL_KEYS
            if unknown:
                raise ValueError("Desteklenmeyen sistem kontrolü: " + ", ".join(sorted(unknown)))
            if any(type(value) is not bool for value in system_controls.values()):
                raise ValueError("Sistem kontrolü değerleri true veya false olmalı.")
        name = name.strip()
        if not 2 <= len(name) <= 100:
            raise ValueError("Profil adı 2 ile 100 karakter arasında olmalı.")
        zone(timezone)
        rules = normalize_app_rules(apps)
        windows = normalize_usage_windows(usage_windows) if usage_windows is not None else None
        total = (normalize_daily_total(daily_total_limit_seconds)
                 if daily_total_limit_seconds is not None else None)
        stamp = self._now().isoformat()
        if profile_id:
            existing = await self.profile(profile_id)
            if existing is None:
                raise NotFoundError("Profil bulunamadı.")
            controls_to_save = (existing["systemControls"] if system_controls is None
                                else system_controls)
            windows_to_save = existing["usageWindows"] if windows is None else windows
            total_to_save = existing["dailyTotalLimitSeconds"] if total is None else total
            revision = int(existing["revision"]) + 1
            await self.store.execute(
                f"DELETE FROM {self.apps} WHERE profile_id=? AND revision=?",
                (profile_id, revision),
            )
            await self.store.execute(
                f"DELETE FROM {self.system_controls} WHERE profile_id=? AND revision=?",
                (profile_id, revision),
            )
        else:
            controls_to_save = system_controls or {}
            windows_to_save = windows or []
            total_to_save = total or 0
            profile_id, revision = str(uuid4()), 1
            await self.store.execute(
                f"INSERT INTO {self.profiles} (id, name, revision, timezone, created_at, "
                "updated_at, usage_windows) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (profile_id, name, 0, timezone, stamp, stamp, "[]"),
            )
        for order, app in enumerate(rules):
            await self.store.execute(
                f"INSERT INTO {self.apps} (profile_id, revision, package_name, app_name, allowed, "
                "unlimited, daily_limit_seconds, sort_order) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (profile_id, revision, app["packageName"], app["appName"],
                 int(app["allowed"]), int(app["unlimited"]), app["dailyLimitSeconds"], order),
            )
        for control_key, is_enabled in controls_to_save.items():
            await self.store.execute(
                f"INSERT INTO {self.system_controls} "
                "(profile_id, revision, control_key, is_enabled) VALUES (?, ?, ?, ?)",
                (profile_id, revision, control_key, int(is_enabled)),
            )
        # Yeni satırlar tamamlandıktan sonra tek satırlık revision geçişi;
        # yarıda kesilirse tabletler son tamamlanmış sürümü okumaya devam eder.
        await self.store.execute(
            f"UPDATE {self.profiles} SET name=?, timezone=?, usage_windows=?, "
            "daily_total_limit_seconds=?, revision=?, updated_at=? WHERE id=?",
            (name, timezone, json.dumps(windows_to_save, separators=(",", ":")),
             total_to_save, revision, stamp, profile_id),
        )
        await self.store.execute(
            f"DELETE FROM {self.apps} WHERE profile_id=? AND revision<?",
            (profile_id, revision),
        )
        await self.store.execute(
            f"DELETE FROM {self.system_controls} WHERE profile_id=? AND revision<?",
            (profile_id, revision),
        )
        return (await self.profile(profile_id)) or {}

    async def duplicate_profile(self, *, profile_id: str, name: str | None) -> dict[str, Any]:
        source = await self.profile(profile_id)
        if source is None:
            raise NotFoundError("Profil bulunamadı.")
        return await self.save_profile(
            profile_id=None, name=(name or f"{source['name']} (kopya)")[:100],
            timezone=source["timezone"], apps=source["apps"],
            system_controls=source["systemControls"], usage_windows=source["usageWindows"],
            daily_total_limit_seconds=source["dailyTotalLimitSeconds"],
        )

    async def delete_profile(self, *, profile_id: str, replacement_profile_id: str | None = None,
                             unassign: bool = False) -> dict[str, Any]:
        """Delete a profile with its app rules and system controls.

        Tablets still using it (revoked records aside) need an explicit choice:
        move them to `replacement_profile_id` or leave them without a profile.
        Without one the answer is 409 `PROFILE_IN_USE` naming those tablets.
        Sessions keep their `profile_id` as history.
        """
        row = await self.store.fetch_one(
            f"SELECT id FROM {self.profiles} WHERE id=?", (profile_id,)
        )
        if not row:
            raise NotFoundError("Profil bulunamadı.")
        if replacement_profile_id and unassign:
            raise ValueError("Tabletleri ya başka profile taşı ya da profilsiz bırak.")
        if replacement_profile_id == profile_id:
            raise ValueError("Tabletler silinen profile taşınamaz.")
        if replacement_profile_id and not await self.profile(replacement_profile_id):
            raise ValueError("Taşınacak profil bulunamadı.")
        assigned = await self.store.fetch_all(
            f"SELECT id, name FROM {self.devices} WHERE profile_id=? AND revoked_at IS NULL "
            "ORDER BY name", (profile_id,),
        )
        if assigned and not replacement_profile_id and not unassign:
            raise ConflictError(
                f"Bu profil {len(assigned)} tablete atanmış. "
                "Tabletleri başka profile taşı ya da profilsiz bırak.",
                code="PROFILE_IN_USE", devices=[device["name"] for device in assigned],
            )
        moved = 0
        if replacement_profile_id:
            for device in assigned:
                await self.assign_profile(device_id=device["id"],
                                          profile_id=replacement_profile_id)
                moved += 1
        async with self._profile_lock:
            await self.store.execute(f"DELETE FROM {self.profiles} WHERE id=?", (profile_id,))
            await self.store.execute(f"DELETE FROM {self.apps} WHERE profile_id=?", (profile_id,))
            await self.store.execute(
                f"DELETE FROM {self.system_controls} WHERE profile_id=?", (profile_id,)
            )
            # Unassigned tablets, and any record pointing here meanwhile, keep no
            # dangling profile id; they read as "no profile" (`NO_PROFILE`).
            await self.store.execute(
                f"UPDATE {self.devices} SET profile_id=NULL WHERE profile_id=?", (profile_id,)
            )
        for key in [key for key in self._profile_cache if key[0] == profile_id]:
            self._profile_cache.pop(key, None)
        return {"deleted": True, "profileId": profile_id, "movedDevices": moved,
                "unassignedDevices": len(assigned) - moved}

    # ------------------------------------------------------------ cihazlar

    def _enrollment_expiry(self) -> str:
        ttl = max(1, min(60, int(self.config.get("enrollment_code_minutes", 10))))
        return (self._now() + timedelta(minutes=ttl)).isoformat()

    async def create_device(self, *, name: str, profile_id: str | None = None) -> dict[str, Any]:
        if profile_id and not await self.profile(profile_id):
            raise ValueError("Profil bulunamadı.")
        device_id = str(uuid4())
        code = new_enrollment_code()
        expiry = self._enrollment_expiry()
        await self.store.execute(
            f"INSERT INTO {self.devices} (id, uuid, name, profile_id, enrollment_code_hash, "
            "enrollment_expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (device_id, f"pending:{device_id}", name, profile_id, token_hash(code), expiry,
             self._now().isoformat()),
        )
        return {"id": device_id, "name": name, "profileId": profile_id,
                "enrollmentCode": code, "enrollmentCodeDisplay": display_enrollment_code(code),
                "expiresAt": expiry}

    async def issue_enrollment_code(self, *, device_id: str,
                                    profile_id: str | None = None) -> dict[str, Any]:
        """New pairing code for a pending or revoked tablet.

        The old device token is dropped (`token_hash=NULL`), so it can never
        authorize again — not even the revoked-unpair retry.
        """
        device = await self.store.fetch_one(
            f"SELECT id, name, profile_id, token_hash, revoked_at FROM {self.devices} WHERE id=?",
            (device_id,),
        )
        if not device:
            raise NotFoundError("Tablet bulunamadı.")
        if device["token_hash"] and not device["revoked_at"]:
            raise ConflictError("Eşli tablete yeni kod verilemez. Önce eşlemeyi kaldır.")
        if profile_id and not await self.profile(profile_id):
            raise ValueError("Profil bulunamadı.")
        code = new_enrollment_code()
        expiry = self._enrollment_expiry()
        assigned = profile_id or device["profile_id"]
        await self.store.execute(
            f"UPDATE {self.devices} SET uuid=?, profile_id=?, token_hash=NULL, revoked_at=NULL, "
            "enrollment_code_hash=?, enrollment_expires_at=?, applied_profile_id=NULL, "
            "applied_profile_revision=0, last_seen_at=NULL, last_policy_applied_at=NULL, "
            "policy_health='', battery_percent=NULL, charging=NULL, foreground_package=NULL, "
            "pending_usage_batches=0, next_sync_seconds=NULL, inventory_hash=NULL WHERE id=?",
            (f"pending:{device_id}", assigned, token_hash(code), expiry, device_id),
        )
        await self.store.execute(
            f"DELETE FROM {self.login_failures} WHERE device_id=?", (device_id,)
        )
        return {"id": device_id, "name": device["name"], "profileId": assigned,
                "enrollmentCode": code, "enrollmentCodeDisplay": display_enrollment_code(code),
                "expiresAt": expiry}

    async def revoke_device(self, *, device_id: str) -> dict[str, Any]:
        device = await self.store.fetch_one(
            f"SELECT id, revoked_at FROM {self.devices} WHERE id=?", (device_id,)
        )
        if not device:
            raise NotFoundError("Tablet bulunamadı.")
        already_revoked = bool(device["revoked_at"])
        revoked_at = device["revoked_at"] or self._now().isoformat()
        # Revoke all general device access first. Keep the one-way token hash
        # only so a response-lost unpair can be retried through that endpoint.
        await self.store.execute(
            f"UPDATE {self.devices} SET uuid=?, enrollment_code_hash=NULL, "
            "enrollment_expires_at=NULL, profile_id=NULL, manufacturer='', model='', "
            "android_version='', app_version='', applied_profile_id=NULL, "
            "applied_profile_revision=0, last_seen_at=NULL, "
            "last_policy_applied_at=NULL, battery_percent=NULL, charging=NULL, "
            "foreground_package=NULL, pending_usage_batches=0, next_sync_seconds=NULL, "
            "inventory_hash=NULL, app_version_code=NULL, self_update=NULL, "
            "policy_detail=NULL, policy_health='revoked', revoked_at=? WHERE id=?",
            (f"pending:{device_id}", revoked_at, device_id),
        )
        await self.store.execute(
            f"UPDATE {self.sessions} SET state='logged_out', ended_at=?, "
            "end_reason=COALESCE(end_reason, 'revoked') "
            "WHERE device_id=? AND state IN ('active', 'expired')",
            (revoked_at, device_id),
        )
        await self.store.execute(
            f"DELETE FROM {self.inventory} WHERE device_id=?", (device_id,)
        )
        await self.store.execute(
            f"DELETE FROM {self.commands} WHERE device_id=? AND acked_at IS NULL", (device_id,)
        )
        # Its download queue rows and lease go too: a slot must not wait for it.
        await self.distribution.forget_device(device_id)
        return {"deviceId": device_id, "revoked": True,
                "alreadyRevoked": already_revoked, "revokedAt": revoked_at}

    async def delete_device(self, *, device_id: str) -> dict[str, Any]:
        """Delete a tablet in any state.

        A paired tablet is revoked first in the same call: its open session
        ends as `revoked` and its device token dies with the row (every tablet
        endpoint then answers `DEVICE_AUTH_INVALID`). The record, installed
        apps, commands and login lock go; session, usage and extra-time history
        stay as student data and name the tablet "Silinmiş tablet".
        """
        device = await self.store.fetch_one(
            f"SELECT id, token_hash, revoked_at FROM {self.devices} WHERE id=?", (device_id,)
        )
        if not device:
            raise NotFoundError("Tablet bulunamadı.")
        paired = bool(device["token_hash"]) and not device["revoked_at"]
        if paired:
            await self.revoke_device(device_id=device_id)
        await self.store.execute(f"DELETE FROM {self.devices} WHERE id=?", (device_id,))
        for table in (self.inventory, self.commands, self.login_failures, self.diagnostics):
            await self.store.execute(f"DELETE FROM {table} WHERE device_id=?", (device_id,))
        await self.distribution.forget_device(device_id)
        return {"deleted": True, "deviceId": device_id, "revoked": paired}

    async def _bump_state_version(self, device_id: str) -> None:
        await self.store.execute(
            f"UPDATE {self.devices} SET state_version=state_version+1 WHERE id=?", (device_id,)
        )

    async def reset_login_lock(self, *, device_id: str) -> dict[str, Any]:
        """Open a tablet's student login locked by wrong codes, and reset the count.

        `cleared` tells whether a lock was still running; only then does the
        device state version change so the tablet drops its countdown.
        """
        device = await self.store.fetch_one(
            f"SELECT id FROM {self.devices} WHERE id=?", (device_id,)
        )
        if not device:
            raise NotFoundError("Tablet bulunamadı.")
        failure = await self.store.fetch_one(
            f"SELECT blocked_until FROM {self.login_failures} WHERE device_id=?", (device_id,)
        )
        if not failure:
            return {"deviceId": device_id, "cleared": False}
        await self.store.execute(
            f"DELETE FROM {self.login_failures} WHERE device_id=?", (device_id,)
        )
        until = parse_time(failure["blocked_until"])
        cleared = until is not None and until > self._now()
        if cleared:
            await self._bump_state_version(device_id)
        return {"deviceId": device_id, "cleared": cleared}

    async def update_device(self, *, device_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        """Rename, lock/unlock, lock message, offline mode, announcement.

        Any remote-state change bumps `state_version`; locking also ends the
        tablet's open session with `endReason: admin` and the lock message.
        """
        device = await self.store.fetch_one(
            f"SELECT id, name, locked, lock_message, offline_mode, announcement, state_version "
            f"FROM {self.devices} WHERE id=?", (device_id,),
        )
        if not device:
            raise NotFoundError("Tablet bulunamadı.")
        current = {"name": device["name"], "locked": bool(device["locked"]),
                   "lockMessage": device["lock_message"], "offlineMode": device["offline_mode"],
                   "announcement": device["announcement"]}
        wanted = dict(current)
        for key, value in changes.items():
            if value is None or key not in current:
                continue
            if key == "name":
                value = str(value).strip()
                if not 2 <= len(value) <= 100:
                    raise ValueError("Tablet adı 2-100 karakter olmalı.")
            elif key == "locked":
                value = bool(value)
            elif key == "offlineMode":
                if value not in OFFLINE_MODES:
                    raise ValueError("Çevrimdışı kip geçersiz.")
            else:
                value = str(value).strip()
            wanted[key] = value
        state_changed = any(wanted[key] != current[key]
                            for key in ("locked", "lockMessage", "offlineMode", "announcement"))
        if wanted != current:
            await self.store.execute(
                f"UPDATE {self.devices} SET name=?, locked=?, lock_message=?, offline_mode=?, "
                "announcement=?, state_version=state_version+? WHERE id=?",
                (wanted["name"], int(wanted["locked"]), wanted["lockMessage"],
                 wanted["offlineMode"], wanted["announcement"], int(state_changed), device_id),
            )
        ended = 0
        if wanted["locked"]:
            ended = await self._end_active_sessions(
                "device_id", device_id, reason="admin", message=wanted["lockMessage"],
            )
        return {"id": device_id, **wanted,
                "stateVersion": int(device["state_version"]) + int(state_changed),
                "endedSessions": ended}

    async def assign_profile(self, *, device_id: str, profile_id: str) -> dict[str, Any]:
        if not await self.profile(profile_id):
            raise ValueError("Profil bulunamadı.")
        device = await self.store.fetch_one(
            f"SELECT id, revoked_at FROM {self.devices} WHERE id=?", (device_id,)
        )
        if not device:
            raise NotFoundError("Tablet bulunamadı.")
        if device["revoked_at"]:
            raise ConflictError("Eşlemesi kaldırılmış tablete profil atanamaz.")
        await self.store.execute(
            f"UPDATE {self.devices} SET profile_id=?, applied_profile_id=NULL, "
            "applied_profile_revision=0 WHERE id=?", (profile_id, device_id)
        )
        return {"deviceId": device_id, "profileId": profile_id}

    async def configure_initial_profile(self, *, device_id: str, profile_id: str | None,
                                       name: str, timezone: str,
                                       apps: list[dict[str, Any]]) -> dict[str, Any]:
        """Let a newly paired tablet select or create its first profile exactly once."""
        device = await self.store.fetch_one(
            f"SELECT profile_id FROM {self.devices} WHERE id=? AND revoked_at IS NULL",
            (device_id,),
        )
        if not device:
            raise ValueError("Tablet bulunamadı.")
        if device["profile_id"]:
            raise ValueError("Tablete zaten bir profil atanmış.")
        if profile_id:
            if not await self.profile(profile_id):
                raise ValueError("Profil bulunamadı.")
            await self.store.execute(
                f"UPDATE {self.devices} SET profile_id=? WHERE id=? AND profile_id IS NULL "
                "AND revoked_at IS NULL", (profile_id, device_id),
            )
            current = await self.store.fetch_one(
                f"SELECT profile_id FROM {self.devices} WHERE id=?", (device_id,),
            )
            if not current or current["profile_id"] != profile_id:
                raise ValueError("Tablet profili başka bir işlemde atanmış.")
            return {"deviceId": device_id, "profileId": profile_id}
        if not apps:
            raise ValueError("Yeni profil için en az bir uygulama ekleyin.")
        packages = [str(app.get("packageName", "")) for app in apps]
        if len(packages) != len(set(packages)):
            raise ValueError("Aynı uygulama profilde bir kez bulunabilir.")
        launchable = await self.store.fetch_all(
            f"SELECT package_name FROM {self.inventory} WHERE device_id=? AND is_launchable=1",
            (device_id,),
        )
        available = {row["package_name"] for row in launchable}
        if not set(packages).issubset(available):
            raise ValueError("Profil yalnızca bu tablette başlatılabilir uygulamaları içerebilir.")
        profile = await self.save_profile(
            profile_id=None, name=name, timezone=timezone, apps=apps,
        )
        await self.store.execute(
            f"UPDATE {self.devices} SET profile_id=? WHERE id=? AND profile_id IS NULL "
            "AND revoked_at IS NULL", (profile["id"], device_id),
        )
        current = await self.store.fetch_one(
            f"SELECT profile_id FROM {self.devices} WHERE id=?", (device_id,),
        )
        if not current or current["profile_id"] != profile["id"]:
            raise ValueError("Tablet profili başka bir işlemde atanmış.")
        return {"deviceId": device_id, "profileId": profile["id"]}

    # ------------------------------------------------------------ komutlar

    @staticmethod
    def _command_view(row: dict[str, Any], current: str) -> dict[str, Any]:
        if row.get("acked_at"):
            status = "done" if row.get("ok") else "failed"
        elif row.get("cancelled_at"):
            status = "cancelled"
        elif row["expires_at"] <= current:
            status = "expired"
        elif row.get("delivered_at"):
            status = "delivered"
        else:
            status = "pending"
        try:
            payload = json.loads(row.get("payload_json") or "{}")
        except ValueError:
            payload = {}
        return {"id": row["id"], "deviceId": row["device_id"], "type": row["type"],
                "payload": payload, "createdBy": row.get("created_by") or "",
                "createdAt": row["created_at"], "expiresAt": row["expires_at"],
                "deliveredAt": row.get("delivered_at"),
                "deliveryCount": int(row.get("delivery_count") or 0),
                "ackedAt": row.get("acked_at"),
                "ok": None if row.get("ok") is None else bool(row["ok"]),
                "resultMessage": row.get("result_message") or "",
                "cancelledAt": row.get("cancelled_at"),
                "cancelledBy": row.get("cancelled_by") or "", "status": status}

    async def create_command(self, *, device_id: str, command_type: str,
                             payload: Any, actor: str) -> dict[str, Any]:
        stored = normalize_command(command_type, payload)
        device = await self.store.fetch_one(
            f"SELECT id, revoked_at FROM {self.devices} WHERE id=?", (device_id,)
        )
        if not device:
            raise NotFoundError("Tablet bulunamadı.")
        if device["revoked_at"]:
            raise ConflictError("Eşlemesi kaldırılmış tablete komut gönderilemez.")
        current = self._now()
        row = {"id": str(uuid4()), "device_id": device_id, "type": command_type,
               "payload_json": json.dumps(stored, ensure_ascii=False), "created_by": actor,
               "created_at": current.isoformat(),
               "expires_at": (current + COMMAND_TTL).isoformat()}
        await self.store.execute(
            f"DELETE FROM {self.commands} WHERE device_id=? AND created_at<?",
            (device_id, (current - COMMAND_RETENTION).isoformat()),
        )
        await self.store.execute(
            f"INSERT INTO {self.commands} (id, device_id, type, payload_json, created_by, "
            "created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (row["id"], device_id, command_type, row["payload_json"], actor,
             row["created_at"], row["expires_at"]),
        )
        return self._command_view(row, current.isoformat())

    async def cancel_command(self, *, device_id: str, command_id: str,
                             actor: str) -> dict[str, Any]:
        """Cancel a command the tablet has not acknowledged, delivered or not.

        A cancelled command is never sent again. A delivered one may already
        have run on the tablet: its later result is recorded and the command
        then reads as done or failed. An acknowledged command answers 409.
        """
        row = await self.store.fetch_one(
            f"SELECT * FROM {self.commands} WHERE id=? AND device_id=?", (command_id, device_id),
        )
        if not row:
            raise NotFoundError("Komut bulunamadı.")
        already = bool(row["cancelled_at"])
        if not row["acked_at"] and not already:
            await self.store.execute(
                f"UPDATE {self.commands} SET cancelled_at=?, cancelled_by=? "
                "WHERE id=? AND acked_at IS NULL AND cancelled_at IS NULL",
                (self._now().isoformat(), actor, command_id),
            )
            row = await self.store.fetch_one(
                f"SELECT * FROM {self.commands} WHERE id=?", (command_id,)
            ) or row
        if row["acked_at"]:
            raise ConflictError("Tablet bu komutu zaten uyguladı; iptal edilemez.")
        return {**self._command_view(row, self._now().isoformat()), "alreadyCancelled": already}

    async def list_commands(self, *, device_id: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self.store.fetch_all(
            f"SELECT * FROM {self.commands} WHERE device_id=? ORDER BY created_at DESC LIMIT ?",
            (device_id, max(1, min(100, int(limit)))),
        )
        current = self._now().isoformat()
        return [self._command_view(row, current) for row in rows]

    # ------------------------------------------------------------ tanılama

    @staticmethod
    def _diagnostics_view(row: dict[str, Any], *, with_text: bool = False) -> dict[str, Any]:
        view = {"id": row["id"], "deviceId": row["device_id"],
                "commandId": row.get("command_id"), "createdAt": row["created_at"],
                "appVersion": row.get("app_version") or "",
                "appVersionCode": row.get("app_version_code"),
                "sizeBytes": int(row.get("size_bytes") or 0)}
        if with_text:
            view["text"] = row.get("text") or ""
        return view

    async def store_diagnostics(self, *, device: dict[str, Any], command_id: str | None,
                                app_version: str | None, app_version_code: int | None,
                                text: str) -> dict[str, Any]:
        """Keep a tablet's diagnostics report; only its newest DIAGNOSTICS_KEEP stay.

        The new report always survives: the older ones are trimmed by their
        own order before it is written, so equal timestamps cannot drop it.
        """
        if len(text) > MAX_DIAGNOSTICS_TEXT:
            raise ValueError(f"Tanı raporu en fazla {MAX_DIAGNOSTICS_TEXT} karakter olabilir.")
        stamp = self._now().isoformat()
        older = await self.store.fetch_all(
            f"SELECT id FROM {self.diagnostics} WHERE device_id=? ORDER BY created_at DESC",
            (device["id"],),
        )
        stale = [row["id"] for row in older[DIAGNOSTICS_KEEP - 1:]]
        for part in _chunks(stale, IN_CHUNK):
            await self.store.execute(
                f"DELETE FROM {self.diagnostics} WHERE id IN ({_marks(len(part))})", tuple(part),
            )
        report_id = str(uuid4())
        await self.store.execute(
            f"INSERT INTO {self.diagnostics} (id, device_id, command_id, created_at, "
            "app_version, app_version_code, size_bytes, text) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (report_id, device["id"], command_id or None, stamp, (app_version or "").strip(),
             app_version_code, len(text.encode("utf-8")), text),
        )
        return {"id": report_id, "receivedAt": stamp}

    async def list_diagnostics(self, *, device_id: str) -> list[dict[str, Any]]:
        rows = await self.store.fetch_all(
            f"SELECT id, device_id, command_id, created_at, app_version, app_version_code, "
            f"size_bytes FROM {self.diagnostics} WHERE device_id=? ORDER BY created_at DESC",
            (device_id,),
        )
        return [self._diagnostics_view(dict(row)) for row in rows]

    async def get_diagnostics(self, *, device_id: str, diagnostics_id: str) -> dict[str, Any]:
        row = await self.store.fetch_one(
            f"SELECT * FROM {self.diagnostics} WHERE id=? AND device_id=?",
            (diagnostics_id, device_id),
        )
        if not row:
            raise NotFoundError("Tanı raporu bulunamadı.")
        return self._diagnostics_view(dict(row), with_text=True)

    async def bulk(self, *, device_ids: list[str], action: str, actor: str,
                   lock_message: str = "", announcement: str = "",
                   profile_id: str | None = None,
                   command: dict[str, Any] | None = None) -> dict[str, Any]:
        """Apply one action to many tablets; failures are reported per tablet.

        `delete` is PIN-confirmed by the API before it reaches here.
        """
        if action not in BULK_ACTIONS:
            raise ValueError("Desteklenmeyen toplu işlem.")
        if action == "assign_profile" and not (profile_id and await self.profile(profile_id)):
            raise ValueError("Profil bulunamadı.")
        if action == "command":
            if not command:
                raise ValueError("Komut seçilmedi.")
            normalize_command(str(command.get("type")), command.get("payload"))
        results = []
        for device_id in dict.fromkeys(device_ids):
            try:
                if action == "lock":
                    await self.update_device(device_id=device_id, changes={
                        "locked": True, "lockMessage": lock_message})
                elif action == "unlock":
                    await self.update_device(device_id=device_id, changes={"locked": False})
                elif action == "announce":
                    await self.update_device(device_id=device_id,
                                             changes={"announcement": announcement})
                elif action == "assign_profile":
                    await self.assign_profile(device_id=device_id, profile_id=str(profile_id))
                elif action == "reset_login_lock":
                    await self.reset_login_lock(device_id=device_id)
                elif action == "delete":
                    await self.delete_device(device_id=device_id)
                else:
                    await self.create_command(
                        device_id=device_id, command_type=str((command or {}).get("type")),
                        payload=(command or {}).get("payload"), actor=actor,
                    )
            except ValueError as exc:
                results.append({"deviceId": device_id, "ok": False, "message": str(exc)})
            else:
                results.append({"deviceId": device_id, "ok": True, "message": ""})
        succeeded = sum(1 for item in results if item["ok"])
        return {"action": action, "results": results, "okCount": succeeded,
                "failedCount": len(results) - succeeded}

    # ----------------------------------------------------------- oturumlar

    async def _end_active_sessions(self, column: str, value: str, *, reason: str | None,
                                   message: str = "") -> int:
        """Close active sessions as `expired`: usage upload and logout stay allowed.

        A `None` reason means the server does not know why the tablet stopped
        using the session; it reads as `expired` until the tablet's logout
        records the real reason.
        """
        if column not in ("id", "device_id", "student_id"):
            raise ValueError("Geçersiz oturum anahtarı.")
        rows = await self.store.fetch_all(
            f"SELECT id FROM {self.sessions} WHERE {column}=? AND state='active'", (value,),
        )
        if not rows:
            return 0
        await self.store.execute(
            f"UPDATE {self.sessions} SET state='expired', ended_at=?, end_reason=?, "
            f"end_message=? WHERE {column}=? AND state='active'",
            (self._now().isoformat(), reason, message, value),
        )
        return len(rows)

    async def end_session(self, *, session_id: str, message: str = "") -> dict[str, Any]:
        row = await self.store.fetch_one(
            f"SELECT * FROM {self.sessions} WHERE id=?", (session_id,)
        )
        if not row:
            raise NotFoundError("Oturum bulunamadı.")
        if row["state"] != "active":
            return {"sessionId": session_id, "ended": False, "alreadyEnded": True,
                    "session": session_view(row, expires_at=None)}
        await self._end_active_sessions("id", session_id, reason="admin", message=message)
        updated = await self.store.fetch_one(
            f"SELECT * FROM {self.sessions} WHERE id=?", (session_id,)
        )
        return {"sessionId": session_id, "ended": True, "alreadyEnded": False,
                "session": session_view(updated or row, expires_at=None)}

    async def list_sessions(self, *, limit: int = 100, device_id: str | None = None,
                            student_id: str | None = None) -> list[dict[str, Any]]:
        conditions, params = [], []
        if device_id:
            conditions.append("s.device_id=?")
            params.append(device_id)
        if student_id:
            conditions.append("s.student_id=?")
            params.append(student_id)
        where = f"WHERE {' AND '.join(conditions)} " if conditions else ""
        rows = await self.store.fetch_all(
            f"SELECT s.id, s.student_id, s.student_name, s.device_id, d.name AS device_name, "
            f"s.login_at, s.last_heartbeat_at, s.ended_at, s.state, s.end_reason, "
            f"s.end_message, s.profile_id FROM {self.sessions} s "
            f"LEFT JOIN {self.devices} d ON d.id = s.device_id {where}"
            "ORDER BY s.login_at DESC LIMIT ?",
            (*params, max(1, min(500, int(limit)))),
        )
        idle = timedelta(seconds=self.session_idle_seconds)
        result = []
        for row in rows:
            last = parse_time(row["last_heartbeat_at"])
            view = session_view(row, expires_at=(last + idle).isoformat() if last else None)
            deleted = row.get("device_name") is None
            result.append({"id": row["id"], "studentId": row["student_id"],
                           "studentName": row["student_name"], "deviceId": row["device_id"],
                           "deviceName": DELETED_DEVICE_NAME if deleted else row["device_name"],
                           "deviceDeleted": deleted,
                           "profileId": row.get("profile_id"), "loginAt": row["login_at"],
                           "lastSeenAt": row["last_heartbeat_at"], "endedAt": row["ended_at"],
                           "state": view["state"], "endReason": view["endReason"],
                           "message": view["message"]})
        return result

    async def expire_stale_sessions(self, *, force: bool = False) -> None:
        """Sweep idle sessions; throttled to once a minute per process."""
        monotonic = time.monotonic()
        if not force and monotonic - self._last_stale_scan < STALE_SCAN_SECONDS:
            return
        self._last_stale_scan = monotonic
        current = self._now()
        cutoff = (current - timedelta(seconds=self.session_idle_seconds)).isoformat()
        await self.store.execute(
            f"UPDATE {self.sessions} SET state='expired', ended_at=?, end_reason='expired' "
            "WHERE state='active' AND last_heartbeat_at<?", (current.isoformat(), cutoff),
        )

    def _is_stale(self, row: dict[str, Any], current: datetime) -> bool:
        last = parse_time(row.get("last_heartbeat_at"))
        return last is None or current - last > timedelta(seconds=self.session_idle_seconds)

    # -------------------------------------------------- öğrenci ve kantin

    async def _canteen_students(self) -> list[dict[str, Any]]:
        cached = self._students_cache
        monotonic = time.monotonic()
        if cached and monotonic - cached[0] < STUDENT_CACHE_SECONDS:
            return cached[1]
        try:
            rows = list(await self.canteen.students())
        except Exception as exc:
            raise CanteenUnavailableError("Kantin şu anda yanıt vermiyor.") from exc
        self._students_cache = (monotonic, rows)
        return rows

    async def _require_student(self, student_id: str) -> dict[str, Any]:
        students = await self._canteen_students()
        student = next((item for item in students if str(item.get("id")) == student_id), None)
        if not student:
            raise ValueError("Öğrenci kantinde bulunamadı.")
        return student

    async def students(self) -> list[dict[str, Any]]:
        rows = await self._canteen_students()
        blocked = {
            row["student_id"]: row["until"]
            for row in await self.store.fetch_all(
                f"SELECT student_id, MAX(blocked_until) AS until FROM {self.blocks} "
                "WHERE lifted_at IS NULL AND blocked_until>? GROUP BY student_id",
                (self._now().isoformat(),),
            )
        }
        return [{"id": str(row.get("id", "")), "name": str(row.get("displayName", "")),
                 "blockedUntil": blocked.get(str(row.get("id", "")))} for row in rows]

    async def _app_names(self, packages: set[str]) -> dict[str, str]:
        names: dict[str, str] = {}
        if not packages:
            return names
        for profile in await self.all_profiles():
            for app in profile["apps"]:
                if app["packageName"] in packages and app["appName"]:
                    names.setdefault(app["packageName"], app["appName"])
        missing = sorted(packages - names.keys())
        for part in _chunks(missing, IN_CHUNK):
            for row in await self.store.fetch_all(
                f"SELECT package_name, app_label FROM {self.inventory} "
                f"WHERE package_name IN ({_marks(len(part))}) AND app_label<>''", tuple(part),
            ):
                names.setdefault(row["package_name"], row["app_label"])
        return names

    # ------------------------------------------------------------ kullanım

    async def _usage_rows(self, student_id: str, local_dates: list[str]) -> list[dict[str, Any]]:
        if not local_dates:
            return []
        rows = await self.store.fetch_all(
            f"SELECT package_name, local_date, used_seconds FROM {self.usage} "
            f"WHERE student_id=? AND local_date IN ({_marks(len(local_dates))}) "
            "ORDER BY local_date, package_name", (student_id, *local_dates),
        )
        return [{"packageName": row["package_name"], "localDate": row["local_date"],
                 "usedSeconds": int(row["used_seconds"])} for row in rows]

    async def _grant_items(self, student_id: str, local_date: str) -> list[dict[str, Any]]:
        rows = await self.store.fetch_all(
            f"SELECT id, package_name, extra_seconds, reason, created_at, created_by "
            f"FROM {self.grants} WHERE student_id=? AND local_date=? "
            "AND revoked_at IS NULL ORDER BY created_at DESC", (student_id, local_date),
        )
        return [{"id": row["id"], "packageName": row["package_name"], "localDate": local_date,
                 "extraSeconds": int(row["extra_seconds"]), "reason": row["reason"],
                 "createdAt": row["created_at"], "createdBy": row["created_by"]}
                for row in rows]

    async def grants_version(self, student_id: str, local_date: str) -> str:
        """Stable token of a student's grants for one day, from one aggregate query."""
        row = await self.store.fetch_one(
            f"SELECT SUM(CASE WHEN revoked_at IS NULL THEN 1 ELSE 0 END) AS active, "
            f"MAX(created_at) AS created, MAX(revoked_at) AS revoked FROM {self.grants} "
            "WHERE student_id=? AND local_date=?", (student_id, local_date),
        ) or {}
        latest = max((stamp for stamp in (row.get("created"), row.get("revoked")) if stamp),
                     default="-")
        return f"{local_date}:{int(row.get('active') or 0)}:{latest}"

    # ------------------------------------------- öğrenciye özel süre (tablet-sinir)

    async def student_app_limits(self, student_id: str) -> dict[str, dict[str, Any]]:
        """Öğrencinin özel uygulama süreleri: paket → {seconds, updatedAt, updatedBy}.

        Bellekte tutulur (ilk okumada PK önekli tek sorgu); yazma önbelleği bırakır."""
        cached = self._student_limit_cache.get(student_id)
        if cached is not None:
            return cached
        rows = await self.store.fetch_all(
            f"SELECT package_name, daily_limit_seconds, updated_at, updated_by "
            f"FROM {self.student_limits} WHERE student_id=?", (student_id,),
        )
        found = {str(row["package_name"]): {"seconds": int(row["daily_limit_seconds"]),
                                             "updatedAt": row["updated_at"],
                                             "updatedBy": row["updated_by"]} for row in rows}
        if len(self._student_limit_cache) >= STUDENT_LIMIT_CACHE_SIZE:
            self._student_limit_cache.clear()
        self._student_limit_cache[student_id] = found
        return found

    @staticmethod
    def override_grants(overrides: dict[str, dict[str, Any]], profile: dict[str, Any] | None,
                        local_date: str) -> list[dict[str, Any]]:
        """Öğrenci süresinin genel (profil) süresini aştığı kısım, o günün ek süresi olarak.

        Yalnız profilde izinli ve süreli uygulamada ve öğrenci süresi genelden büyükse
        satır üretilir; tablet ek süreyi sınıra ekler (sözleşme v2 `grants`)."""
        if not overrides or not profile:
            return []
        items = []
        for app in profile.get("apps") or []:
            override = overrides.get(app["packageName"])
            if override is None or not app["allowed"] or app["unlimited"]:
                continue
            extra = int(override["seconds"]) - int(app["dailyLimitSeconds"])
            if extra > 0:
                items.append({"id": f"ovr:{app['packageName']}", "packageName": app["packageName"],
                              "localDate": local_date, "extraSeconds": extra})
        return items

    async def session_grants(self, student_id: str, local_date: str,
                             profile: dict[str, Any] | None) -> tuple[str, list[dict[str, Any]]]:
        """Tablete giden ek süre listesi ve sürümü: verilen ek süreler + öğrenciye özel süre farkı.

        Özel süre yoksa sürüm eskisiyle aynıdır (değişmeyen tabletlere liste gitmez)."""
        version = await self.grants_version(student_id, local_date)
        overrides = await self.student_app_limits(student_id)
        virtual = self.override_grants(overrides, profile, local_date)
        if virtual:
            stamp = max(str(item["updatedAt"]) for item in overrides.values())
            version += f":o{len(virtual)}:{stamp}:{(profile or {}).get('revision')}"
        return version, virtual

    async def set_student_app_limit(self, *, student_id: str, package_name: str,
                                    daily_limit_seconds: int | None,
                                    actor: str) -> dict[str, Any] | None:
        """Öğrenciye özel günlük süreyi yazar; `None` siler (genele döner). Önceki değeri döner."""
        current = (await self.student_app_limits(student_id)).get(package_name)
        self._student_limit_cache.pop(student_id, None)
        if daily_limit_seconds is None:
            await self.store.execute(
                f"DELETE FROM {self.student_limits} WHERE student_id=? AND package_name=?",
                (student_id, package_name),
            )
            return current
        stamp = self._now().isoformat()
        if current is None:
            await self.store.execute(
                f"INSERT INTO {self.student_limits} (student_id, package_name, daily_limit_seconds, "
                "updated_at, updated_by) VALUES (?, ?, ?, ?, ?)",
                (student_id, package_name, daily_limit_seconds, stamp, actor),
            )
        else:
            await self.store.execute(
                f"UPDATE {self.student_limits} SET daily_limit_seconds=?, updated_at=?, updated_by=? "
                "WHERE student_id=? AND package_name=?",
                (daily_limit_seconds, stamp, actor, student_id, package_name),
            )
        return current

    async def _student_totals(self, student_id: str, local_date: str) -> dict[str, Any]:
        return {"usage": await self._usage_rows(student_id, [local_date]),
                "grants": await self._grant_items(student_id, local_date)}

    async def student_usage(self, student_id: str, local_date: str) -> dict[str, Any]:
        """Admin view: app name, usage, limit and extra time from the last session's profile."""
        last = await self.store.fetch_one(
            f"SELECT profile_id FROM {self.sessions} WHERE student_id=? "
            "ORDER BY login_at DESC LIMIT 1", (student_id,),
        )
        profile = await self.profile(last["profile_id"]) if last else None
        used = {row["packageName"]: row["usedSeconds"]
                for row in await self._usage_rows(student_id, [local_date])}
        grants = await self._grant_items(student_id, local_date)
        extra: dict[str, int] = {}
        for grant in grants:
            extra[grant["packageName"]] = extra.get(grant["packageName"], 0) + grant["extraSeconds"]
        rules = {app["packageName"]: app for app in (profile or {}).get("apps", [])}
        packages = set(used) | {name for name, app in rules.items() if app["allowed"]}
        names = await self._app_names(packages | set(extra))
        # Öğrenciye özel süre (tablet-sinir) genelin üstüne yazar; tablet farkı ek süre olarak alır.
        overrides = await self.student_app_limits(student_id)
        items = []
        for package in packages:
            rule = rules.get(package)
            allowed = bool(rule and rule["allowed"])
            unlimited = bool(rule and rule["unlimited"])
            limit = None if unlimited else (rule["dailyLimitSeconds"] if allowed and rule else 0)
            if limit and package in overrides:
                limit = max(limit, int(overrides[package]["seconds"]))
            spent = used.get(package, 0)
            bonus = extra.get(package, 0)
            items.append({
                "packageName": package, "appName": names.get(package, package),
                "localDate": local_date, "usedSeconds": spent, "allowed": allowed,
                "unlimited": unlimited, "limitSeconds": limit, "extraSeconds": bonus,
                "remainingSeconds": None if limit is None else max(0, limit + bonus - spent),
            })
        items.sort(key=lambda item: (-item["usedSeconds"], item["appName"].lower()))
        for grant in grants:
            grant["appName"] = names.get(grant["packageName"], grant["packageName"])
        # Toplam yalnız süreli uygulamaları sayar; sınırsızlar ne sayılır ne durur.
        total_limit = int((profile or {}).get("dailyTotalLimitSeconds") or 0)
        total = None
        if total_limit:
            total_used = sum(item["usedSeconds"] for item in items
                             if item["allowed"] and not item["unlimited"])
            total = {"limitSeconds": total_limit, "usedSeconds": total_used,
                     "remainingSeconds": max(0, total_limit - total_used)}
        return {"studentId": student_id, "localDate": local_date,
                "profile": {"id": profile["id"], "name": profile["name"]} if profile else None,
                "dailyTotal": total,
                "blockedUntil": await self._active_block_until(student_id, self._now()),
                "usage": items, "grants": grants}

    async def daily_usage_totals(self, local_date: str) -> list[dict[str, Any]]:
        """Kurum geneli bir günün öğrenci başına toplam süresi + şu an süren kısıt.

        İki sorgu, ikisi de indeksli: günün kullanımı `(local_date)` indeksiyle
        öğrenci başına toplanır; süren kısıtlar `(blocked_until)` indeksiyle
        (008 göçü) okunur. Listede o gün kullanımı OLAN ya da kısıtı SÜREN
        öğrenciler vardır; kimlik sırasıyla döner.
        """
        totals: dict[str, int] = {}
        for row in await self.store.fetch_all(
            f"SELECT student_id, SUM(used_seconds) AS total FROM {self.usage} "
            "WHERE local_date=? AND used_seconds>0 GROUP BY student_id", (local_date,),
        ):
            totals[str(row["student_id"])] = int(row["total"] or 0)
        blocks: dict[str, str] = {}
        for row in await self.store.fetch_all(
            f"SELECT student_id, MAX(blocked_until) AS until FROM {self.blocks} "
            "WHERE blocked_until>? AND lifted_at IS NULL GROUP BY student_id",
            (self._now().isoformat(),),
        ):
            if row.get("until"):
                blocks[str(row["student_id"])] = str(row["until"])
        return [{"studentId": student, "totalSeconds": totals.get(student, 0),
                 "blockedUntil": blocks.get(student)}
                for student in sorted(totals.keys() | blocks.keys())]

    async def daily_report(self, local_date: str) -> dict[str, Any]:
        rows = await self.store.fetch_all(
            f"SELECT student_id, package_name, used_seconds FROM {self.usage} "
            "WHERE local_date=? AND used_seconds>0 ORDER BY used_seconds DESC", (local_date,),
        )
        student_ids = sorted({row["student_id"] for row in rows})
        names: dict[str, str] = {}
        for part in _chunks(student_ids, IN_CHUNK):
            for row in await self.store.fetch_all(
                f"SELECT student_id, MAX(student_name) AS name FROM {self.sessions} "
                f"WHERE student_id IN ({_marks(len(part))}) GROUP BY student_id", tuple(part),
            ):
                names[row["student_id"]] = row["name"] or ""
        app_names = await self._app_names({row["package_name"] for row in rows})
        apps: dict[str, dict[str, Any]] = {}
        students: dict[str, dict[str, Any]] = {}
        items = []
        for row in rows:
            seconds = int(row["used_seconds"])
            package, student = row["package_name"], row["student_id"]
            app_name = app_names.get(package, package)
            items.append({"studentId": student, "studentName": names.get(student, ""),
                          "packageName": package, "appName": app_name, "usedSeconds": seconds})
            app = apps.setdefault(package, {"packageName": package, "appName": app_name,
                                            "usedSeconds": 0, "studentCount": 0})
            app["usedSeconds"] += seconds
            app["studentCount"] += 1
            person = students.setdefault(student, {"studentId": student,
                                                   "studentName": names.get(student, ""),
                                                   "usedSeconds": 0})
            person["usedSeconds"] += seconds
        return {"localDate": local_date, "rows": items,
                "apps": sorted(apps.values(), key=lambda item: -item["usedSeconds"]),
                "students": sorted(students.values(), key=lambda item: -item["usedSeconds"]),
                "totalSeconds": sum(item["usedSeconds"] for item in items)}

    # ----------------------------------------------------------- ek süre

    async def device_grant_packages(self, student_id: str) -> list[str]:
        """Packages a whole-device extra-time grant covers.

        Limits are per app (there is no device-wide budget), so "extra time on
        the tablet" means extra time on every allowed, time-limited app: of the
        profile of the student's latest session (the active one if any), or of
        every profile's current revision when the student never logged in.
        Unlimited apps need no grant and are left out.
        """
        last = await self.store.fetch_one(
            f"SELECT profile_id FROM {self.sessions} WHERE student_id=? "
            "ORDER BY login_at DESC LIMIT 1", (student_id,),
        )
        profile = await self.profile(last["profile_id"]) if last else None
        if profile is not None:
            return sorted({app["packageName"] for app in profile.get("apps", [])
                           if app["allowed"] and not app["unlimited"]})
        rows = await self.store.fetch_all(
            f"SELECT DISTINCT a.package_name FROM {self.apps} a JOIN {self.profiles} p "
            "ON p.id = a.profile_id AND p.revision = a.revision "
            "WHERE a.allowed=1 AND a.unlimited=0 ORDER BY a.package_name",
        )
        return [str(row["package_name"]) for row in rows]

    async def grant_time(self, *, student_id: str, package_name: str,
                         local_date: str, extra_seconds: int, reason: str,
                         actor: str) -> dict[str, Any]:
        await self._require_student(student_id)
        allowed = await self.store.fetch_one(
            f"SELECT a.package_name FROM {self.apps} a JOIN {self.profiles} p "
            "ON p.id = a.profile_id AND p.revision = a.revision "
            "WHERE a.package_name=? AND a.allowed=1 LIMIT 1", (package_name,),
        )
        if not allowed:
            raise ValueError("Uygulama hiçbir profilde izinli değil.")
        grant_id = str(uuid4())
        stamp = self._now().isoformat()
        await self.store.execute(
            f"INSERT INTO {self.grants} (id, student_id, package_name, local_date, "
            "extra_seconds, reason, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (grant_id, student_id, package_name, local_date, extra_seconds, reason, actor, stamp),
        )
        return {"id": grant_id, "studentId": student_id, "packageName": package_name,
                "localDate": local_date, "extraSeconds": extra_seconds, "reason": reason,
                "createdAt": stamp, "createdBy": actor}

    async def revoke_grant(self, *, student_id: str, grant_id: str,
                           actor: str) -> dict[str, Any]:
        row = await self.store.fetch_one(
            f"SELECT id, revoked_at FROM {self.grants} WHERE id=? AND student_id=?",
            (grant_id, student_id),
        )
        if not row:
            raise NotFoundError("Ek süre bulunamadı.")
        if row["revoked_at"]:
            return {"id": grant_id, "revoked": True, "alreadyRevoked": True,
                    "revokedAt": row["revoked_at"]}
        stamp = self._now().isoformat()
        await self.store.execute(
            f"UPDATE {self.grants} SET revoked_at=?, revoked_by=? "
            "WHERE id=? AND revoked_at IS NULL", (stamp, actor, grant_id),
        )
        return {"id": grant_id, "revoked": True, "alreadyRevoked": False, "revokedAt": stamp}

    # ------------------------------------------------------ kısıtlamalar

    async def _active_block_until(self, student_id: str, current: datetime) -> str | None:
        row = await self.store.fetch_one(
            f"SELECT MAX(blocked_until) AS until FROM {self.blocks} "
            "WHERE student_id=? AND lifted_at IS NULL AND blocked_until>?",
            (student_id, current.isoformat()),
        )
        return row["until"] if row and row.get("until") else None

    def _aware(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            value = value.replace(tzinfo=zone(self.default_timezone))
        return value.astimezone(UTC)

    @staticmethod
    def _block_view(row: dict[str, Any], current: str) -> dict[str, Any]:
        return {"id": row["id"], "studentId": row["student_id"], "until": row["blocked_until"],
                "reason": row["reason"], "createdBy": row["created_by"],
                "createdAt": row["created_at"], "liftedAt": row.get("lifted_at"),
                "active": not row.get("lifted_at") and row["blocked_until"] > current}

    async def block_student(self, *, student_id: str, until: datetime, reason: str,
                            actor: str) -> dict[str, Any]:
        current = self._now()
        ends = self._aware(until)
        if ends <= current:
            raise ValueError("Kısıtlama bitişi gelecekte olmalı.")
        if ends - current > MAX_BLOCK:
            raise ValueError("Kısıtlama en fazla bir yıl olabilir.")
        await self._require_student(student_id)
        row = {"id": str(uuid4()), "student_id": student_id, "blocked_until": ends.isoformat(),
               "reason": reason.strip(), "created_by": actor,
               "created_at": current.isoformat(), "lifted_at": None}
        await self.store.execute(
            f"INSERT INTO {self.blocks} (id, student_id, blocked_until, reason, created_by, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (row["id"], student_id, row["blocked_until"], row["reason"], actor,
             row["created_at"]),
        )
        ended = await self._end_active_sessions("student_id", student_id, reason="blocked")
        return {**self._block_view(row, current.isoformat()), "endedSessions": ended}

    async def unblock_student(self, *, student_id: str,
                              only_reason: str | None = None) -> dict[str, Any]:
        """Süren kısıtları kaldırır. `only_reason` verilirse YALNIZ o gerekçeyle
        konmuş kısıtlar kalkar (ör. KM yeniden etkinleştirmesi pasifleştirmenin
        koyduğu kısıtı kaldırır, öğretmenin koyduğu cezaya dokunmaz)."""
        current = self._now().isoformat()
        where = "student_id=? AND lifted_at IS NULL AND blocked_until>?"
        params: tuple[Any, ...] = (student_id, current)
        if only_reason is not None:
            where += " AND reason=?"
            params = (*params, only_reason)
        active = await self.store.fetch_all(f"SELECT id FROM {self.blocks} WHERE {where}", params)
        if active:
            await self.store.execute(f"UPDATE {self.blocks} SET lifted_at=? WHERE {where}",
                                     (current, *params))
        return {"studentId": student_id, "lifted": len(active)}

    async def list_blocks(self, *, student_id: str) -> list[dict[str, Any]]:
        rows = await self.store.fetch_all(
            f"SELECT * FROM {self.blocks} WHERE student_id=? ORDER BY created_at DESC LIMIT 50",
            (student_id,),
        )
        current = self._now().isoformat()
        return [self._block_view(row, current) for row in rows]

    # ------------------------------------------------- silme ve saklama

    async def delete_student_history(self, *, student_id: str) -> dict[str, Any]:
        """Erase a student's tablet data and return how many rows went.

        Ended sessions, usage batches and their items, daily totals, extra
        time and restrictions (an active one is lifted with it). Refused while
        the student has a live session, which still writes usage; a session
        that went quiet is closed first. The canteen account is not touched.
        """
        current = self._now()
        for row in await self.store.fetch_all(
            f"SELECT id, last_heartbeat_at FROM {self.sessions} "
            "WHERE student_id=? AND state='active'", (student_id,),
        ):
            if not self._is_stale(row, current):
                raise ConflictError("Öğrencinin açık oturumu var. Önce oturumu kapat.")
            await self._end_active_sessions("id", row["id"], reason="expired")
        tables = (("sessions", self.sessions, " AND state<>'active'"),
                  ("usageBatches", self.batches, ""), ("dailyUsage", self.usage, ""),
                  ("grants", self.grants, ""), ("blocks", self.blocks, ""))
        counts: dict[str, int] = {}
        for key, table, extra in tables:
            row = await self.store.fetch_one(
                f"SELECT COUNT(*) AS total FROM {table} WHERE student_id=?{extra}", (student_id,),
            )
            counts[key] = int((row or {}).get("total") or 0)
        await self.store.execute(
            f"DELETE FROM {self.batch_items} WHERE batch_id IN "
            f"(SELECT id FROM {self.batches} WHERE student_id=?)", (student_id,),
        )
        for _, table, extra in tables:
            await self.store.execute(
                f"DELETE FROM {table} WHERE student_id=?{extra}", (student_id,),
            )
        return {"studentId": student_id, **counts}

    async def purge_history(self, *, force: bool = False) -> bool:
        """Drop history older than `history_retention_days`; once a day per process.

        Ended sessions, usage batches and items, daily totals, extra time,
        long-finished restrictions and commands a week past expiry. Called from
        the panel's overview, never from the tablet's sync. Returns whether it ran.
        """
        monotonic = time.monotonic()
        if not force and monotonic - self._last_purge < PURGE_SECONDS:
            return False
        self._last_purge = monotonic
        current = self._now()
        cutoff = current - timedelta(days=self.history_retention_days)
        stamp = cutoff.isoformat()
        day = cutoff.astimezone(zone(self.default_timezone)).date().isoformat()
        await self.store.execute(
            f"DELETE FROM {self.sessions} WHERE state<>'active' AND login_at<?", (stamp,),
        )
        await self.store.execute(
            f"DELETE FROM {self.batch_items} WHERE batch_id IN "
            f"(SELECT id FROM {self.batches} WHERE accepted_at<?)", (stamp,),
        )
        await self.store.execute(f"DELETE FROM {self.batches} WHERE accepted_at<?", (stamp,))
        await self.store.execute(f"DELETE FROM {self.usage} WHERE local_date<?", (day,))
        await self.store.execute(f"DELETE FROM {self.grants} WHERE local_date<?", (day,))
        await self.store.execute(f"DELETE FROM {self.blocks} WHERE blocked_until<?", (stamp,))
        await self.store.execute(
            f"DELETE FROM {self.commands} WHERE expires_at<?",
            ((current - COMMAND_RETENTION).isoformat(),),
        )
        await self.store.execute(
            f"DELETE FROM {self.diagnostics} WHERE created_at<?",
            ((current - DIAGNOSTICS_RETENTION).isoformat(),),
        )
        # Uploads untouched for two days, drafts never published, orphan files.
        await self.distribution.cleanup()
        return True

    # ------------------------------------------------------------- özet

    @staticmethod
    def _device_state(device: dict[str, Any], login_locked_until: str | None) -> dict[str, Any]:
        return {"version": int(device.get("state_version") or 1),
                "locked": bool(device.get("locked")),
                "lockMessage": device.get("lock_message") or "",
                "offlineMode": device.get("offline_mode") or "local",
                "announcement": device.get("announcement") or "",
                "loginLockedUntil": login_locked_until}

    async def _login_locked_until(self, device_id: str, current: datetime) -> str | None:
        row = await self.store.fetch_one(
            f"SELECT blocked_until FROM {self.login_failures} WHERE device_id=?", (device_id,),
        )
        until = parse_time(row["blocked_until"]) if row else None
        return until.isoformat() if until and until > current else None

    async def overview(self) -> dict[str, Any]:
        await self.expire_stale_sessions()
        await self.purge_history()
        current = self._now()
        stamp = current.isoformat()
        profiles = await self.all_profiles()
        by_id = {profile["id"]: profile for profile in profiles}
        devices = await self.store.fetch_all(
            f"SELECT id, uuid, name, profile_id, manufacturer, model, android_version, "
            f"app_version, applied_profile_id, applied_profile_revision, last_seen_at, "
            f"last_policy_applied_at, policy_health, revoked_at, created_at, "
            f"enrollment_expires_at, locked, lock_message, offline_mode, announcement, "
            f"state_version, battery_percent, charging, foreground_package, "
            f"pending_usage_batches, next_sync_seconds, app_version_code, self_update, "
            f"policy_detail, "
            f"CASE WHEN token_hash IS NULL THEN 0 ELSE 1 END AS paired "
            f"FROM {self.devices} ORDER BY name"
        )
        sessions = await self.store.fetch_all(
            f"SELECT id, student_id, student_name, device_id, profile_id, login_at, "
            f"last_heartbeat_at, state FROM {self.sessions} "
            "WHERE state = 'active' ORDER BY login_at DESC"
        )
        pending = {
            row["device_id"]: int(row["total"])
            for row in await self.store.fetch_all(
                f"SELECT device_id, COUNT(*) AS total FROM {self.commands} "
                "WHERE acked_at IS NULL AND cancelled_at IS NULL AND expires_at>? "
                "GROUP BY device_id", (stamp,),
            )
        }
        login_locks: dict[str, str] = {}
        for row in await self.store.fetch_all(
            f"SELECT device_id, blocked_until FROM {self.login_failures} WHERE blocked_until>?",
            (stamp,),
        ):
            until = parse_time(row["blocked_until"])
            if until and until > current:
                login_locks[row["device_id"]] = until.isoformat()
        foreground = sorted({row["foreground_package"] for row in devices
                             if row["foreground_package"]})
        labels: dict[tuple[str, str], str] = {}
        for part in _chunks(foreground, IN_CHUNK):
            for row in await self.store.fetch_all(
                f"SELECT device_id, package_name, app_label FROM {self.inventory} "
                f"WHERE package_name IN ({_marks(len(part))})", tuple(part),
            ):
                labels[(row["device_id"], row["package_name"])] = row["app_label"]
        active_by_device = {row["device_id"]: row for row in sessions}
        names = {row["id"]: row["name"] for row in devices}
        assigned_counts: dict[str, int] = {}
        device_views = []
        for row in devices:
            device = dict(row)
            profile = by_id.get(device["profile_id"]) if device["profile_id"] else None
            paired = bool(device.pop("paired"))
            next_sync = device["next_sync_seconds"]
            threshold = next_sync * ONLINE_FACTOR if next_sync else LEGACY_ONLINE_SECONDS
            seen = parse_time(device["last_seen_at"])
            age = (current - seen).total_seconds() if seen else None
            if device["revoked_at"]:
                status = "revoked"
            elif not paired:
                status = "pending"
            elif age is not None and age <= threshold:
                status = "online"
            else:
                status = "offline"
            if status in ("pending", "revoked") or not profile or seen is None:
                sync_state = "unknown"
            elif device["applied_profile_id"] == profile["id"] and \
                    int(device["applied_profile_revision"] or 0) == profile["revision"]:
                sync_state = "current"
            else:
                sync_state = "behind"
            if device["profile_id"] and not device["revoked_at"]:
                assigned_counts[device["profile_id"]] = \
                    assigned_counts.get(device["profile_id"], 0) + 1
            package = device["foreground_package"]
            app_name = None
            if package:
                app_name = next((app["appName"] for app in (profile or {}).get("apps", [])
                                 if app["packageName"] == package and app["appName"]),
                                None) or labels.get((device["id"], package)) or package
            active = active_by_device.get(device["id"])
            expires = device["enrollment_expires_at"]
            device.update({
                "status": status, "online": status == "online", "sync_state": sync_state,
                "paired": paired, "profile_name": profile["name"] if profile else None,
                "profile_revision": profile["revision"] if profile else None,
                "locked": bool(device["locked"]),
                "charging": None if device["charging"] is None else bool(device["charging"]),
                "foreground_app_name": app_name,
                "pending_commands": pending.get(device["id"], 0),
                "login_locked_until": None if device["revoked_at"]
                else login_locks.get(device["id"]),
                "enrollment_code_expired": bool(
                    status == "pending" and (not expires or expires <= stamp)),
                "active_student_id": active["student_id"] if active else None,
                "active_student_name": active["student_name"] if active else None,
                "active_session_id": active["id"] if active else None,
                "self_update": self.distribution.self_report(device),
            })
            device_views.append(device)
        live = [item for item in device_views if item["status"] != "revoked"]
        summary = {
            "tabletsTotal": len(live),
            "online": sum(1 for item in live if item["status"] == "online"),
            "offline": sum(1 for item in live if item["status"] == "offline"),
            "activeSessions": len(sessions),
            "pendingPairing": sum(1 for item in live if item["status"] == "pending"),
            "behindPolicy": sum(1 for item in live if item["sync_state"] == "behind"),
            "locked": sum(1 for item in live if item["locked"]),
            "loginLocked": sum(1 for item in live if item["login_locked_until"]),
            "revoked": len(device_views) - len(live),
        }
        # The published TabletKontrol release (in-memory catalog): the panel
        # shows a v2.2 tablet's self-update download as a percentage of it.
        own = self.distribution.self_item(await self.distribution.catalog())
        return {
            "serverTime": stamp, "summary": summary,
            "selfRelease": None if own is None else {
                "releaseId": own["releaseId"], "versionCode": own["versionCode"],
                "versionName": own["versionName"], "totalBytes": own["totalBytes"]},
            "profiles": [{**profile, "deviceCount": assigned_counts.get(profile["id"], 0)}
                         for profile in profiles],
            "devices": device_views,
            "sessions": [{**dict(row),
                          "device_name": names.get(row["device_id"], DELETED_DEVICE_NAME)}
                         for row in sessions],
        }

    # ---------------------------------------------------------- tablet uçları

    async def device_by_token(self, bearer: str) -> dict[str, Any] | None:
        """Device row for a bearer, revoked rows included (auth decides the code)."""
        if not bearer:
            return None
        row = await self.store.fetch_one(
            f"SELECT * FROM {self.devices} WHERE token_hash=?", (token_hash(bearer),),
        )
        return dict(row) if row else None

    async def device_for_token(self, bearer: str) -> dict[str, Any] | None:
        row = await self.device_by_token(bearer)
        return row if row and not row["revoked_at"] else None

    async def revoked_device_for_unpair_token(self, bearer: str) -> dict[str, Any] | None:
        """Allow a revoked bearer to retry only the idempotent unpair operation."""
        row = await self.device_by_token(bearer)
        return row if row and row["revoked_at"] else None

    async def session_for_token(self, bearer: str) -> dict[str, Any] | None:
        if not bearer:
            return None
        row = await self.store.fetch_one(
            f"SELECT * FROM {self.sessions} WHERE token_hash=? AND state='active'",
            (token_hash(bearer),),
        )
        if not row:
            return None
        if self._is_stale(row, self._now()):
            await self._end_active_sessions("id", row["id"], reason="expired")
            return None
        return dict(row)

    async def session_for_usage_batch_token(self, bearer: str) -> dict[str, Any] | None:
        """Resolve a session bearer for usage sync, including offline recovery.

        An expired session keeps only this device/student-bound write capability
        until terminal logout. This lets a durable local outbox recover after a
        long outage without restoring policy, login, or heartbeat access. Explicit
        logout and device revoke move the row to logged_out, which this path rejects.
        """
        if not bearer:
            return None
        row = await self.store.fetch_one(
            f"SELECT * FROM {self.sessions} WHERE token_hash=? "
            "AND state IN ('active', 'expired')", (token_hash(bearer),),
        )
        if not row:
            return None
        if row["state"] == "active" and self._is_stale(row, self._now()):
            await self._end_active_sessions("id", row["id"], reason="expired")
            row = await self.store.fetch_one(
                f"SELECT * FROM {self.sessions} WHERE id=?", (row["id"],)
            ) or row
        return dict(row)

    async def session_for_logout_token(self, bearer: str) -> dict[str, Any] | None:
        """Resolve only the terminal logout capability, including expired/retried sessions."""
        if not bearer:
            return None
        row = await self.store.fetch_one(
            f"SELECT * FROM {self.sessions} WHERE token_hash=? "
            "AND state IN ('active', 'expired', 'logged_out')", (token_hash(bearer),),
        )
        return dict(row) if row else None

    async def enroll(self, data: dict[str, Any]) -> dict[str, Any]:
        raw = str(data["enrollmentCode"])
        normalized = normalize_enrollment_code(raw)
        # The raw hash keeps codes issued before the alphabet change valid
        # until they expire (at most 60 minutes).
        hashes = tuple(dict.fromkeys((token_hash(normalized), token_hash(raw))))
        row = await self.store.fetch_one(
            f"SELECT * FROM {self.devices} WHERE enrollment_code_hash IN ({_marks(len(hashes))}) "
            "AND token_hash IS NULL AND revoked_at IS NULL", hashes,
        )
        expires = parse_time(row["enrollment_expires_at"]) if row else None
        if not row or not expires or expires < self._now():
            raise ValueError("Eşleme kodu geçersiz veya süresi dolmuş.")
        uuid = data["deviceUuid"]
        existing = await self.store.fetch_one(
            f"SELECT id FROM {self.devices} WHERE uuid=? AND id<>?", (uuid, row["id"]),
        )
        if existing:
            raise ValueError("Cihaz UUID zaten kayıtlı.")
        token = secrets.token_urlsafe(48)
        await self.store.execute(
            f"UPDATE {self.devices} SET uuid=?, name=?, manufacturer=?, model=?, "
            "android_version=?, app_version=?, token_hash=?, enrollment_code_hash=NULL, "
            "enrollment_expires_at=NULL, last_seen_at=?, policy_health='' "
            "WHERE id=? AND enrollment_code_hash=? AND token_hash IS NULL",
            (uuid, data["deviceName"], data.get("manufacturer", ""), data.get("model", ""),
             data.get("androidVersion", ""), data.get("appVersion", ""), token_hash(token),
             self._now().isoformat(), row["id"], row["enrollment_code_hash"]),
        )
        confirmed = await self.store.fetch_one(
            f"SELECT token_hash FROM {self.devices} WHERE id=?", (row["id"],)
        )
        if not confirmed or not confirmed["token_hash"] or not hmac.compare_digest(
            confirmed["token_hash"], token_hash(token)
        ):
            raise ValueError("Eşleme kodu kullanılmış.")
        profile = await self.profile(row["profile_id"])
        return {"deviceId": row["id"], "deviceToken": token,
                "assignedProfileId": row["profile_id"],
                "policyRevision": profile["revision"] if profile else 0}

    async def login(self, *, device: dict[str, Any], student_code: str) -> dict[str, Any]:
        current = self._now()
        if device.get("locked"):
            raise TabletError("DEVICE_LOCKED",
                              device.get("lock_message") or "Tablet şu anda kilitli.")
        profile = await self.profile(device.get("profile_id"))
        if not profile:
            raise TabletError("NO_PROFILE", "Bu tablet henüz hazır değil. Yöneticiye başvur.")
        local_now = current.astimezone(zone(profile["timezone"]))
        if not inside_usage_window(profile["usageWindows"], local_now):
            upcoming = next_window_start(profile["usageWindows"], local_now)
            raise TabletError("OUTSIDE_USAGE_HOURS", "Tabletler şu anda kapalı.",
                              nextWindowStart=upcoming.isoformat() if upcoming else None)
        failure = await self.store.fetch_one(
            f"SELECT * FROM {self.login_failures} WHERE device_id=?", (device["id"],)
        )
        blocked_until = parse_time(failure["blocked_until"]) if failure else None
        if blocked_until and blocked_until > current:
            raise self._rate_limited(blocked_until, current)
        try:
            student = await self.canteen.lookup_student_by_code(student_code)
        except Exception as exc:
            if getattr(exc, "status", None) in (404, 422):
                await self._login_failed(device["id"], failure, current)
            raise TabletError("CANTEEN_UNAVAILABLE",
                              "Şu anda giriş yapılamıyor. Biraz sonra dene.") from exc
        if not student or not student.get("id"):
            await self._login_failed(device["id"], failure, current)
        if failure:
            await self.store.execute(
                f"DELETE FROM {self.login_failures} WHERE device_id=?", (device["id"],)
            )
        student_id = str(student["id"])
        student_name = str(student.get("displayName") or "")
        until = await self._active_block_until(student_id, current)
        if until:
            local_until = parse_time(until) or current
            raise TabletError("STUDENT_BLOCKED", "Tablet kullanımın kısıtlandı.",
                              until=local_until.astimezone(local_now.tzinfo).isoformat())
        await self._clear_login_conflicts(student_id, device["id"], current)
        session_id, session_token = str(uuid4()), secrets.token_urlsafe(48)
        stamp = current.isoformat()
        try:
            await self.store.execute(
                f"INSERT INTO {self.sessions} (id, student_id, student_name, device_id, "
                "token_hash, login_at, last_heartbeat_at, policy_revision, profile_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, student_id, student_name, device["id"],
                 token_hash(session_token), stamp, stamp, profile["revision"], profile["id"]),
            )
        except Exception as exc:
            # Unique active-session indexes resolve concurrent logins too.
            conflict = await self.store.fetch_one(
                f"SELECT id FROM {self.sessions} WHERE state='active' "
                "AND (student_id=? OR device_id=?)", (student_id, device["id"]),
            )
            if conflict:
                raise TabletError("SESSION_CONFLICT",
                                  "Hesabın ya da bu tablet başka bir oturumda açık.") from exc
            raise
        local_date = local_now.date().isoformat()
        grants_version, virtual = await self.session_grants(student_id, local_date, profile)
        return {"sessionId": session_id, "sessionToken": session_token,
                "sessionExpiresAt": (current + timedelta(
                    seconds=self.session_idle_seconds)).isoformat(),
                "serverTime": stamp, "nextSyncSeconds": self.sync_active_seconds,
                "usageFlushSeconds": self.usage_flush_seconds,
                "student": {"id": student_id, "name": student_name},
                "profile": profile, "policyRevision": profile["revision"],
                "usage": await self._usage_rows(student_id, [local_date]),
                "extraGrants": [*await self._grant_items(student_id, local_date), *virtual],
                "grantsVersion": grants_version,
                # A successful login means no login lock is running.
                "deviceState": self._device_state(device, None)}

    async def _clear_login_conflicts(self, student_id: str, device_id: str,
                                     current: datetime) -> None:
        rows = await self.store.fetch_all(
            f"SELECT id, student_id, device_id, last_heartbeat_at FROM {self.sessions} "
            "WHERE state='active' AND (student_id=? OR device_id=?)", (student_id, device_id),
        )
        for row in rows:
            if self._is_stale(row, current):
                await self._end_active_sessions("id", row["id"], reason="expired")
            elif row["device_id"] == device_id:
                raise TabletError("SESSION_CONFLICT", "Bu tablette açık bir oturum var.")
            else:
                raise TabletError("SESSION_CONFLICT", "Hesabın başka bir tablette açık.")

    @staticmethod
    def _rate_limited(blocked_until: datetime, current: datetime) -> TabletError:
        wait = max(1, math.ceil((blocked_until - current).total_seconds()))
        return TabletError("LOGIN_RATE_LIMITED", "Çok fazla hatalı deneme. Biraz bekle.",
                           retryAfterSeconds=wait)

    async def _login_failed(self, device_id: str, previous: dict[str, Any] | None,
                            current: datetime) -> NoReturn:
        last = parse_time(previous["last_attempt_at"]) if previous else None
        recent = previous is not None and last is not None and current - last < LOGIN_WINDOW
        attempts = int(previous["attempts"]) + 1 if previous and recent else 1
        blocked = current + LOGIN_WINDOW if attempts >= LOGIN_MAX_FAILURES else None
        await self.store.execute(
            f"INSERT INTO {self.login_failures} (device_id, attempts, last_attempt_at, blocked_until) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(device_id) DO UPDATE SET "
            "attempts=excluded.attempts, last_attempt_at=excluded.last_attempt_at, "
            "blocked_until=excluded.blocked_until",
            (device_id, attempts, current.isoformat(), blocked.isoformat() if blocked else None),
        )
        if blocked:
            # The lock is device state: the next sync carries `loginLockedUntil`.
            await self._bump_state_version(device_id)
            raise self._rate_limited(blocked, current)
        raise TabletError("INVALID_STUDENT_CODE", "Şifre hatalı.")

    async def sync(self, *, device: dict[str, Any], data: dict[str, Any],
                   base_url: str = "") -> dict[str, Any]:
        """The single periodic tablet call. Unchanged data is answered with nulls.

        `base_url` is the public tablet API root (scheme, host and the
        `/api/tablet/v1` prefix); download grants and the self-update offer
        are absolute URLs under it.

        Sözleşme v2.2: `appVersionCode`, `selfUpdate` and `policyDetail` are
        written only when they differ from the row the auth already loaded,
        as extra columns of the sync's one device update. The `selfUpdate`
        answer key exists only for a tablet that sent the `selfUpdate` key,
        so older APKs get the same answer as before.
        """
        current = self._now()
        stamp = current.isoformat()
        await self.expire_stale_sessions()
        profile = await self.profile(device.get("profile_id"))
        local_now = current.astimezone(zone(profile["timezone"] if profile
                                            else self.default_timezone))
        for result in (data.get("commandResults") or [])[:MAX_SYNC_COMMANDS]:
            await self.store.execute(
                # A cancelled command's late result is still recorded: the tablet
                # runs a command as soon as it receives it, so the result is the
                # truth even when the cancel came after delivery.
                f"UPDATE {self.commands} SET acked_at=?, ok=?, result_message=? "
                "WHERE id=? AND device_id=? AND acked_at IS NULL",
                (stamp, int(bool(result.get("ok"))), str(result.get("message") or "")[:500],
                 str(result.get("id") or ""), device["id"]),
            )
        session, active = await self._sync_session(device, data, profile, current, local_now)
        next_sync = self.sync_active_seconds if active else self.sync_idle_seconds
        # The catalog lives in memory: without a change `apps` costs no query.
        # The queue table is touched only when the tablet reports `installs`.
        # Only a tablet that sends the `appsVersion` key (null included) knows
        # app distribution; older APKs get `apps: null`.
        apps = (await self.distribution.apps_payload(data["appsVersion"])
                if "appsVersion" in data else None)
        install = None
        if data.get("installs") is not None:
            install, head = await self.distribution.sync_installs(
                device_id=device["id"], installs=data["installs"], base_url=base_url,
            )
            if head:
                next_sync = min(next_sync, HEAD_SYNC_SECONDS)
        applied_id, applied_revision = await self._applied_profile(data, profile)
        reported = self._reported_columns(device, data)
        await self.store.execute(
            f"UPDATE {self.devices} SET last_seen_at=?, next_sync_seconds=?, "
            "app_version=COALESCE(?, app_version), policy_health=COALESCE(?, policy_health), "
            "last_policy_applied_at=COALESCE(?, last_policy_applied_at), "
            "applied_profile_id=?, applied_profile_revision=?, battery_percent=?, charging=?, "
            "foreground_package=?, pending_usage_batches=?"
            + "".join(f", {column}=?" for column in reported) + " WHERE id=?",
            (stamp, next_sync, data.get("appVersion"), data.get("policyHealth"),
             data.get("lastPolicyAppliedAt"), applied_id, applied_revision,
             data.get("batteryPercent"),
             None if data.get("charging") is None else int(bool(data["charging"])),
             data.get("foregroundPackage") if active else None,
             int(data.get("pendingUsageBatches") or 0), *reported.values(), device["id"]),
        )
        grants = None
        if active:
            local_date = local_now.date().isoformat()
            version, virtual = await self.session_grants(active["student_id"], local_date,
                                                         profile)
            if version != data.get("grantsVersion"):
                grants = {"version": version,
                          "items": [*await self._grant_items(active["student_id"], local_date),
                                    *virtual]}
        changed_profile = profile is not None and (
            data.get("profileId") != profile["id"]
            or data.get("profileRevision") != profile["revision"])
        # The login lock is read only when the state is sent, so the no-change
        # path stays a few indexed queries.
        state = None
        if data.get("deviceStateVersion") != int(device.get("state_version") or 1):
            state = self._device_state(device,
                                       await self._login_locked_until(device["id"], current))
        answer = {"serverTime": stamp, "nextSyncSeconds": next_sync,
                  "usageFlushSeconds": self.usage_flush_seconds,
                  "assignedProfileId": profile["id"] if profile else None,
                  "profileRevision": profile["revision"] if profile else 0,
                  "profile": profile if changed_profile else None,
                  "deviceState": state,
                  "session": session, "grants": grants,
                  "commands": await self._pending_commands(device["id"], stamp),
                  "apps": apps, "install": install}
        if "selfUpdate" in data:
            # In-memory catalog and one comparison; no query (sözleşme v2.2).
            answer["selfUpdate"] = await self.distribution.self_update_offer(
                device_id=device["id"], app_version_code=data.get("appVersionCode"),
                base_url=base_url,
            )
        return answer

    @staticmethod
    def _reported_columns(device: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        """v2.2 report fields that differ from the stored row: column → new value.

        Only keys the tablet sent count. A v2.2 tablet (it sends `selfUpdate`)
        omits `policyDetail` while its policy is healthy, which clears it.
        """
        changes: dict[str, Any] = {}
        code = data.get("appVersionCode")
        if code is not None and code != device.get("app_version_code"):
            changes["app_version_code"] = code
        if "selfUpdate" in data:
            report = normalize_self_update(data["selfUpdate"])
            text = None if report is None else json.dumps(
                report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if text != device.get("self_update"):
                changes["self_update"] = text
        if "selfUpdate" in data or "policyDetail" in data:
            detail = normalize_policy_detail(data.get("policyDetail"))
            if detail != device.get("policy_detail"):
                changes["policy_detail"] = detail
        return changes

    async def _applied_profile(self, data: dict[str, Any],
                               assigned: dict[str, Any] | None) -> tuple[str | None, int]:
        """What the tablet applies; an unknown or impossible report is stored as unknown."""
        applied_id = data.get("profileId")
        revision = data.get("profileRevision")
        if not applied_id:
            return None, 0
        reported = assigned if assigned and assigned["id"] == applied_id \
            else await self.profile(applied_id)
        if not reported or not isinstance(revision, int) or isinstance(revision, bool) \
                or not 0 <= revision <= reported["revision"]:
            return None, 0
        return applied_id, revision

    async def _sync_session(self, device: dict[str, Any], data: dict[str, Any],
                            profile: dict[str, Any] | None, current: datetime,
                            local_now: datetime,
                            ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        session_id = data.get("sessionId")
        if not session_id:
            orphan = await self.store.fetch_one(
                f"SELECT id, login_at FROM {self.sessions} WHERE device_id=? AND state='active'",
                (device["id"],),
            )
            login_at = parse_time(orphan["login_at"]) if orphan else None
            if orphan and (login_at is None or current - login_at > ORPHAN_SESSION_GRACE):
                # Usually the tablet ended the session itself and its logout is
                # still queued; that logout fills in the reason.
                await self._end_active_sessions("id", orphan["id"], reason=None)
            return None, None
        row = await self.store.fetch_one(
            f"SELECT * FROM {self.sessions} WHERE id=?", (session_id,)
        )
        if not row or row["device_id"] != device["id"] or (
                data.get("activeStudentId") and data["activeStudentId"] != row["student_id"]):
            return {"id": session_id, "state": "ended", "endReason": "expired",
                    "message": "", "expiresAt": None}, None
        if row["state"] == "active":
            reason = message = None
            if self._is_stale(row, current):
                reason, message = "expired", ""
            elif device.get("locked"):
                reason, message = "admin", device.get("lock_message") or ""
            elif profile and not inside_usage_window(profile["usageWindows"], local_now):
                reason, message = "outside_hours", ""
            if reason is None:
                await self.store.execute(
                    f"UPDATE {self.sessions} SET last_heartbeat_at=? WHERE id=? AND state='active'",
                    (current.isoformat(), session_id),
                )
                expires = (current + timedelta(seconds=self.session_idle_seconds)).isoformat()
                return session_view(row, expires_at=expires), row
            await self._end_active_sessions("id", session_id, reason=reason,
                                            message=message or "")
            row = await self.store.fetch_one(
                f"SELECT * FROM {self.sessions} WHERE id=?", (session_id,)
            ) or row
        return session_view(row, expires_at=None), None

    async def _pending_commands(self, device_id: str, stamp: str) -> list[dict[str, Any]]:
        # At most MAX_SYNC_COMMANDS per answer, so the results the tablet sends
        # back always fit one request; the rest follow on the next sync.
        rows = await self.store.fetch_all(
            f"SELECT id, type, payload_json, created_at, expires_at FROM {self.commands} "
            "WHERE device_id=? AND acked_at IS NULL AND cancelled_at IS NULL AND expires_at>? "
            "ORDER BY created_at LIMIT ?",
            (device_id, stamp, MAX_SYNC_COMMANDS),
        )
        if not rows:
            return []
        ids = [row["id"] for row in rows]
        await self.store.execute(
            f"UPDATE {self.commands} SET delivered_at=?, delivery_count=delivery_count+1 "
            f"WHERE id IN ({_marks(len(ids))})",
            (stamp, *ids),
        )
        commands = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except ValueError:
                payload = {}
            commands.append({"id": row["id"], "type": row["type"], "payload": payload,
                             "createdAt": row["created_at"], "expiresAt": row["expires_at"]})
        return commands

    async def policy(self, *, device: dict[str, Any], revision: int,
                     session: dict[str, Any] | None = None) -> dict[str, Any]:
        """Legacy `GET /policy` for older APKs."""
        await self.expire_stale_sessions()
        profile = await self.profile(device["profile_id"])
        if not profile:
            raise ValueError("Tablete profil atanmadı.")
        if session and session["device_id"] != device["id"]:
            raise ValueError("Oturum bu tablete ait değil.")
        result: dict[str, Any] = {"changed": revision != profile["revision"],
                                  "revision": profile["revision"],
                                  "serverTime": self._now().isoformat()}
        if result["changed"]:
            result["profile"] = profile
        if session:
            today = self._now().astimezone(zone(profile["timezone"])).date().isoformat()
            totals = await self._student_totals(session["student_id"], today)
            result["usage"] = totals["usage"]
            result["extraGrants"] = totals["grants"]
        return result

    async def heartbeat(self, *, device: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        """Legacy `POST /heartbeat` for older APKs."""
        current = self._now()
        stamp = current.isoformat()
        profile = await self.profile(device["profile_id"])
        applied_profile_id = data.get("profileId")
        applied_revision = data.get("profileRevision", 0)
        reported_profile = await self.profile(applied_profile_id) if applied_profile_id else profile
        if applied_profile_id and not reported_profile:
            raise ValueError("Tablet bilinmeyen bir profil bildirdi.")
        current_revision = reported_profile["revision"] if reported_profile else 0
        if not isinstance(applied_revision, int) or isinstance(applied_revision, bool) or \
                applied_revision < 0 or applied_revision > current_revision:
            raise ValueError("Tabletin bildirdiği politika sürümü profil ile uyumlu değil.")

        session_id = data.get("sessionId")
        linked = None
        if session_id:
            linked = await self.store.fetch_one(
                f"SELECT student_id FROM {self.sessions} "
                "WHERE id=? AND device_id=? AND state='active'",
                (session_id, device["id"]),
            )
            if not linked or (data.get("activeStudentId") and
                              data["activeStudentId"] != linked["student_id"]):
                raise ValueError("Oturum bu tablete ait değil.")
        await self.store.execute(
            f"UPDATE {self.devices} SET last_seen_at=?, next_sync_seconds=NULL, app_version=?, "
            "applied_profile_id=?, applied_profile_revision=?, last_policy_applied_at=?, "
            "policy_health=? WHERE id=?",
            (stamp, data.get("appVersion", ""), applied_profile_id, applied_revision,
             data.get("lastPolicyAppliedAt"), data.get("policyHealth", ""), device["id"]),
        )
        if session_id:
            await self.store.execute(
                f"UPDATE {self.sessions} SET last_heartbeat_at=? "
                "WHERE id=? AND device_id=? AND state='active'",
                (stamp, session_id, device["id"]),
            )
        return {"ok": True, "serverTime": stamp,
                "sessionExpiresAt": (current + timedelta(
                    seconds=self.session_idle_seconds)).isoformat() if session_id else None,
                "policyRevision": profile["revision"] if profile else 0}

    async def replace_inventory(self, *, device: dict[str, Any],
                                apps: list[dict[str, Any]]) -> dict[str, Any]:
        """Replace the installed-app list; an identical list costs one read."""
        rows = sorted(
            ((app["packageName"], app.get("appLabel", ""), app.get("versionName", ""),
              str(app.get("versionCode", "")), int(bool(app.get("isSystemApp", False))),
              int(bool(app.get("isLaunchable", False))), app.get("installedAt"),
              app.get("updatedAt")) for app in apps),
            key=lambda row: row[0],
        )
        digest = token_hash(json.dumps(rows, ensure_ascii=False, separators=(",", ":")))
        if device.get("inventory_hash") == digest:
            return {"ok": True, "count": len(rows), "unchanged": True}
        await self.store.execute(f"DELETE FROM {self.inventory} WHERE device_id=?", (device["id"],))
        for part in _chunks(rows, INSERT_CHUNK):
            values = ", ".join("(?, ?, ?, ?, ?, ?, ?, ?, ?)" for _ in part)
            params: list[Any] = []
            for row in part:
                params.extend((device["id"], *row))
            await self.store.execute(
                f"INSERT INTO {self.inventory} (device_id, package_name, app_label, "
                "version_name, version_code, is_system_app, is_launchable, installed_at, "
                f"updated_at) VALUES {values} ON CONFLICT(device_id, package_name) DO NOTHING",
                tuple(params),
            )
        await self.store.execute(
            f"UPDATE {self.devices} SET inventory_hash=? WHERE id=?", (digest, device["id"]),
        )
        return {"ok": True, "count": len(rows), "unchanged": False}

    async def logout(self, *, session: dict[str, Any]) -> dict[str, Any]:
        already_ended = session["state"] == "logged_out"
        stamp = session.get("ended_at") or self._now().isoformat()
        if not already_ended:
            reason = "logout"
            if not session.get("end_reason"):
                profile = await self.profile(session.get("profile_id"))
                if profile and not inside_usage_window(
                        profile["usageWindows"],
                        self._now().astimezone(zone(profile["timezone"]))):
                    reason = "outside_hours"
            await self.store.execute(
                f"UPDATE {self.sessions} SET state='logged_out', ended_at=?, "
                "end_reason=COALESCE(end_reason, ?) "
                "WHERE id=? AND state IN ('active', 'expired')", (stamp, reason, session["id"]),
            )
        # Keep only the one-way token hash so a lost logout response can be
        # acknowledged idempotently. Other endpoints only authorize active rows.
        return {"ok": True, "endedAt": stamp, "alreadyEnded": already_ended}

    async def usage_batch(self, *, session: dict[str, Any],
                          data: dict[str, Any]) -> dict[str, Any]:
        if data["sessionId"] != session["id"] or data["deviceId"] != session["device_id"] \
                or data["studentId"] != session["student_id"]:
            raise ValueError("Kullanım paketi oturumla eşleşmiyor.")
        # Usage is not checked against the profile's app rules: the tablet has
        # already measured it, and a rule edited or a profile reassigned during
        # the session must not make the queued batch a permanently rejected one.
        # The profile only supplies the time zone the local dates are read in.
        login_profile = await self.profile(session.get("profile_id"))
        if login_profile is None:
            device_row = await self.store.fetch_one(
                f"SELECT profile_id FROM {self.devices} WHERE id=?", (session["device_id"],)
            )
            login_profile = await self.profile(device_row["profile_id"]) if device_row else None
        tz = zone(login_profile["timezone"] if login_profile else self.default_timezone)
        try:
            started = datetime.fromisoformat(data["startedAt"])
            ended = datetime.fromisoformat(data["endedAt"])
        except ValueError as exc:
            raise ValueError("Kullanım zaman aralığı geçersiz.") from exc
        if started.tzinfo is None or ended.tzinfo is None or ended < started:
            raise ValueError("Kullanım zaman aralığı geçersiz.")
        login_at = parse_time(session["login_at"])
        if (login_at and started < login_at) or ended > self._now() + timedelta(minutes=2):
            raise ValueError("Kullanım oturum sınırları dışında.")
        allowed_dates = {started.astimezone(tz).date().isoformat(),
                         ended.astimezone(tz).date().isoformat()}
        total = 0
        seen_items: set[tuple[str, str]] = set()
        for item in data["usages"]:
            key = (item["packageName"], item["localDate"])
            if key in seen_items:
                raise ValueError("Kullanım paketinde aynı uygulama ve tarih birden fazla kez var.")
            seen_items.add(key)
            if item["localDate"] not in allowed_dates:
                raise ValueError("Kullanım tarihi profil saat dilimiyle eşleşmiyor.")
            total += int(item["usedSeconds"])
        if total > int((ended - started).total_seconds()) + 5:
            raise ValueError("Kullanım süresi zaman aralığını aşıyor.")
        previous = await self.store.fetch_one(
            f"SELECT * FROM {self.batches} WHERE id=?", (data["batchId"],)
        )
        duplicate = previous is not None
        if not previous:
            for item in data["usages"]:
                await self.store.execute(
                    f"INSERT INTO {self.batch_items} (batch_id, package_name, local_date, "
                    "used_seconds) VALUES (?, ?, ?, ?) ON CONFLICT(batch_id, package_name, local_date) "
                    "DO UPDATE SET used_seconds=excluded.used_seconds",
                    (data["batchId"], item["packageName"], item["localDate"], item["usedSeconds"]),
                )
            claim = secrets.token_hex(16)
            await self.store.execute(
                f"INSERT INTO {self.batches} (id, device_id, session_id, student_id, "
                "started_at, ended_at, accepted_at, claim) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO NOTHING",
                (data["batchId"], data["deviceId"], data["sessionId"], data["studentId"],
                 data["startedAt"], data["endedAt"], self._now().isoformat(), claim),
            )
            previous = await self.store.fetch_one(
                f"SELECT * FROM {self.batches} WHERE id=?", (data["batchId"],)
            )
            if previous and previous.get("claim") == claim:
                # Only the request that created the batch row adds to the summary.
                for item in data["usages"]:
                    await self.store.execute(
                        f"INSERT INTO {self.usage} (student_id, package_name, local_date, "
                        "used_seconds) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(student_id, package_name, local_date) DO UPDATE SET "
                        f"used_seconds={self.usage}.used_seconds+excluded.used_seconds",
                        (data["studentId"], item["packageName"], item["localDate"],
                         int(item["usedSeconds"])),
                    )
                previous = None
            else:
                duplicate = True
        if previous:
            identity = (previous["device_id"], previous["session_id"], previous["student_id"],
                        previous["started_at"], previous["ended_at"])
            incoming_identity = (data["deviceId"], data["sessionId"], data["studentId"],
                                 data["startedAt"], data["endedAt"])
            if identity != incoming_identity:
                raise ValueError("Kullanım paketi kimliği farklı bir kayıtla zaten kullanılmış.")
            stored_rows = await self.store.fetch_all(
                f"SELECT package_name, local_date, used_seconds FROM {self.batch_items} "
                "WHERE batch_id=?", (data["batchId"],),
            )
            stored_items = sorted((row["package_name"], row["local_date"],
                                   int(row["used_seconds"])) for row in stored_rows)
            incoming_items = sorted((item["packageName"], item["localDate"],
                                     int(item["usedSeconds"])) for item in data["usages"])
            if stored_items != incoming_items:
                raise ValueError("Kullanım paketi kimliği farklı içerikle tekrar kullanılmış.")
        dates = sorted({item["localDate"] for item in data["usages"]})
        return {"accepted": True, "duplicate": duplicate,
                "usage": await self._usage_rows(session["student_id"], dates),
                "serverTime": self._now().isoformat()}
