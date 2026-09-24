"""Profiles, devices, usage and student extra time owned by Control Center."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo


def now() -> str:
    return datetime.now(UTC).isoformat()


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class TabletService:
    def __init__(self, *, store: Any, canteen: Any, config: dict[str, Any]) -> None:
        self.store = store
        self.canteen = canteen
        self.config = config or {}
        self._profile_lock = asyncio.Lock()
        self.profiles = store.table("profiles")
        self.apps = store.table("profile_apps")
        self.devices = store.table("devices")
        self.inventory = store.table("inventory")
        self.sessions = store.table("sessions")
        self.login_failures = store.table("login_failures")
        self.usage = store.table("daily_usage")
        self.batches = store.table("usage_batches")
        self.batch_items = store.table("usage_batch_items")
        self.grants = store.table("grants")

    async def profile(self, profile_id: str) -> dict[str, Any] | None:
        row = await self.store.fetch_one(
            f"SELECT * FROM {self.profiles} WHERE id = ?", (profile_id,)
        )
        if not row or row["revision"] < 1:
            return None
        apps = await self.store.fetch_all(
            f"SELECT * FROM {self.apps} WHERE profile_id = ? AND revision = ? "
            "ORDER BY sort_order, app_name",
            (profile_id, row["revision"]),
        )
        return {
            "id": row["id"], "name": row["name"], "revision": row["revision"],
            "timezone": row["timezone"], "updatedAt": row["updated_at"],
            "apps": [self._app_view(app) for app in apps],
        }

    @staticmethod
    def _app_view(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "packageName": row["package_name"], "appName": row["app_name"],
            "allowed": bool(row["allowed"]), "unlimited": bool(row["unlimited"]),
            "dailyLimitSeconds": row["daily_limit_seconds"],
            "sortOrder": row["sort_order"],
        }

    async def overview(self) -> dict[str, Any]:
        profile_rows = await self.store.fetch_all(
            f"SELECT id FROM {self.profiles} WHERE revision > 0 ORDER BY name"
        )
        profiles = [await self.profile(row["id"]) for row in profile_rows]
        devices = await self.store.fetch_all(
            f"SELECT id, uuid, name, profile_id, manufacturer, model, android_version, "
            f"app_version, last_seen_at, policy_health, revoked_at FROM {self.devices} "
            "ORDER BY name"
        )
        sessions = await self.store.fetch_all(
            f"SELECT id, student_id, student_name, device_id, login_at, "
            f"last_heartbeat_at, state FROM {self.sessions} "
            "WHERE state = 'active' ORDER BY login_at DESC"
        )
        return {"profiles": profiles, "devices": [dict(row) for row in devices],
                "sessions": [dict(row) for row in sessions]}

    async def save_profile(self, *, profile_id: str | None, name: str,
                           timezone: str, apps: list[dict[str, Any]]) -> dict[str, Any]:
        async with self._profile_lock:
            return await self._save_profile(profile_id=profile_id, name=name,
                                            timezone=timezone, apps=apps)

    async def _save_profile(self, *, profile_id: str | None, name: str,
                            timezone: str, apps: list[dict[str, Any]]) -> dict[str, Any]:
        ZoneInfo(timezone)
        packages = [app["packageName"] for app in apps]
        if len(packages) != len(set(packages)):
            raise ValueError("Aynı uygulama profilde bir kez bulunabilir.")
        stamp = now()
        if profile_id:
            existing = await self.profile(profile_id)
            if existing is None:
                raise ValueError("Profil bulunamadı.")
            revision = int(existing["revision"]) + 1
            await self.store.execute(
                f"DELETE FROM {self.apps} WHERE profile_id=? AND revision=?",
                (profile_id, revision),
            )
        else:
            profile_id, revision = str(uuid4()), 1
            await self.store.execute(
                f"INSERT INTO {self.profiles} (id, name, revision, timezone, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (profile_id, name, 0, timezone, stamp, stamp),
            )
        for order, app in enumerate(apps):
            await self.store.execute(
                f"INSERT INTO {self.apps} (profile_id, revision, package_name, app_name, allowed, "
                "unlimited, daily_limit_seconds, sort_order) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (profile_id, revision, app["packageName"], app.get("appName", ""),
                 int(app.get("allowed", False)), int(app.get("unlimited", False)),
                 int(app.get("dailyLimitSeconds", 0)), order),
            )
        # Yeni satırlar tamamlandıktan sonra tek satırlık revision geçişi;
        # yarıda kesilirse tabletler son tamamlanmış sürümü okumaya devam eder.
        await self.store.execute(
            f"UPDATE {self.profiles} SET name=?, timezone=?, revision=?, updated_at=? WHERE id=?",
            (name, timezone, revision, stamp, profile_id),
        )
        await self.store.execute(
            f"DELETE FROM {self.apps} WHERE profile_id=? AND revision<?",
            (profile_id, revision),
        )
        return (await self.profile(profile_id)) or {}

    async def create_device(self, *, name: str, profile_id: str) -> dict[str, Any]:
        if not await self.profile(profile_id):
            raise ValueError("Profil bulunamadı.")
        device_id = str(uuid4())
        code = secrets.token_urlsafe(9)
        ttl = max(1, min(60, int(self.config.get("enrollment_code_minutes", 10))))
        expiry = (datetime.now(UTC) + timedelta(minutes=ttl)).isoformat()
        await self.store.execute(
            f"INSERT INTO {self.devices} (id, uuid, name, profile_id, enrollment_code_hash, "
            "enrollment_expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (device_id, f"pending:{device_id}", name, profile_id, token_hash(code), expiry, now()),
        )
        return {"id": device_id, "name": name, "profileId": profile_id,
                "enrollmentCode": code, "expiresAt": expiry}

    async def assign_profile(self, *, device_id: str, profile_id: str) -> dict[str, Any]:
        if not await self.profile(profile_id):
            raise ValueError("Profil bulunamadı.")
        device = await self.store.fetch_one(f"SELECT id FROM {self.devices} WHERE id=?", (device_id,))
        if not device:
            raise ValueError("Tablet bulunamadı.")
        await self.store.execute(
            f"UPDATE {self.devices} SET profile_id=? WHERE id=?", (profile_id, device_id)
        )
        return {"deviceId": device_id, "profileId": profile_id}

    async def student_usage(self, student_id: str, local_date: str) -> dict[str, Any]:
        usage = await self.store.fetch_all(
            f"SELECT i.package_name, i.local_date, SUM(i.used_seconds) AS used_seconds "
            f"FROM {self.batch_items} i JOIN {self.batches} b ON b.id=i.batch_id "
            "WHERE b.student_id=? AND i.local_date=? "
            "GROUP BY i.package_name, i.local_date", (student_id, local_date),
        )
        grants = await self.store.fetch_all(
            f"SELECT id, package_name, extra_seconds, reason, created_at, created_by "
            f"FROM {self.grants} WHERE student_id=? AND local_date=? "
            "AND revoked_at IS NULL ORDER BY created_at DESC", (student_id, local_date),
        )
        return {"studentId": student_id, "localDate": local_date,
                "usage": [{"packageName": row["package_name"],
                           "localDate": row["local_date"],
                           "usedSeconds": row["used_seconds"]} for row in usage],
                "grants": [{"id": row["id"], "packageName": row["package_name"],
                            "localDate": local_date, "extraSeconds": row["extra_seconds"],
                            "reason": row["reason"], "createdAt": row["created_at"]}
                           for row in grants]}

    async def grant_time(self, *, student_id: str, package_name: str,
                         local_date: str, extra_seconds: int, reason: str,
                         actor: str) -> dict[str, Any]:
        students = await self.canteen.students()
        student = next((item for item in students if str(item.get("id")) == student_id), None)
        if not student:
            raise ValueError("Öğrenci kantinde bulunamadı.")
        if not await self.store.fetch_one(
            f"SELECT 1 FROM {self.apps} a JOIN {self.profiles} p "
            "ON p.id=a.profile_id AND p.revision=a.revision "
            "WHERE a.package_name=? AND a.allowed=1",
            (package_name,),
        ):
            raise ValueError("Uygulama hiçbir profilde izinli değil.")
        grant_id = str(uuid4())
        stamp = now()
        await self.store.execute(
            f"INSERT INTO {self.grants} (id, student_id, package_name, local_date, "
            "extra_seconds, reason, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (grant_id, student_id, package_name, local_date, extra_seconds, reason, actor, stamp),
        )
        return {"id": grant_id, "studentId": student_id, "packageName": package_name,
                "localDate": local_date, "extraSeconds": extra_seconds, "createdAt": stamp}

    async def students(self) -> list[dict[str, str]]:
        rows = await self.canteen.students()
        return [{"id": str(row.get("id", "")), "name": str(row.get("displayName", ""))}
                for row in rows]

    async def device_for_token(self, bearer: str) -> dict[str, Any] | None:
        if not bearer:
            return None
        row = await self.store.fetch_one(
            f"SELECT * FROM {self.devices} WHERE token_hash=? AND revoked_at IS NULL",
            (token_hash(bearer),),
        )
        return dict(row) if row else None

    async def session_for_token(self, bearer: str) -> dict[str, Any] | None:
        if not bearer:
            return None
        row = await self.store.fetch_one(
            f"SELECT * FROM {self.sessions} WHERE token_hash=? AND state='active'",
            (token_hash(bearer),),
        )
        if not row:
            return None
        last = datetime.fromisoformat(row["last_heartbeat_at"])
        if datetime.now(UTC) - last > timedelta(minutes=30):
            await self.store.execute(
                f"UPDATE {self.sessions} SET state='expired', ended_at=? WHERE id=? AND state='active'",
                (now(), row["id"]),
            )
            return None
        return dict(row)

    async def session_for_usage_batch_token(self, bearer: str) -> dict[str, Any] | None:
        """Resolve a session bearer for usage sync, including bounded offline grace.

        Expired sessions remain authorized for this write-only endpoint for up to
        24 hours after their last server heartbeat. Explicit logout clears the
        token hash, so it cannot use this path. This never reactivates a session.
        """
        if not bearer:
            return None
        row = await self.store.fetch_one(
            f"SELECT * FROM {self.sessions} WHERE token_hash=? "
            "AND state IN ('active', 'expired')", (token_hash(bearer),),
        )
        if not row:
            return None
        last_heartbeat = datetime.fromisoformat(row["last_heartbeat_at"])
        current = datetime.now(UTC)
        if row["state"] == "active" and current - last_heartbeat > timedelta(minutes=30):
            ended = now()
            await self.store.execute(
                f"UPDATE {self.sessions} SET state='expired', ended_at=? "
                "WHERE id=? AND state='active'", (ended, row["id"]),
            )
            row["state"] = "expired"
            row["ended_at"] = ended
        if row["state"] == "expired" and current > last_heartbeat + timedelta(hours=24):
            return None
        return dict(row)

    async def expire_stale_sessions(self) -> None:
        cutoff = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
        await self.store.execute(
            f"UPDATE {self.sessions} SET state='expired', ended_at=? "
            "WHERE state='active' AND last_heartbeat_at<?", (now(), cutoff),
        )

    async def enroll(self, data: dict[str, Any]) -> dict[str, Any]:
        code_hash = token_hash(data["enrollmentCode"])
        row = await self.store.fetch_one(
            f"SELECT * FROM {self.devices} WHERE enrollment_code_hash=? "
            "AND token_hash IS NULL AND revoked_at IS NULL", (code_hash,),
        )
        if not row or not row["enrollment_expires_at"] or datetime.fromisoformat(
            row["enrollment_expires_at"]
        ) < datetime.now(UTC):
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
            "enrollment_expires_at=NULL, last_seen_at=? "
            "WHERE id=? AND enrollment_code_hash=? AND token_hash IS NULL",
            (uuid, data["deviceName"], data.get("manufacturer", ""), data.get("model", ""),
             data.get("androidVersion", ""), data.get("appVersion", ""), token_hash(token),
             now(), row["id"], code_hash),
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
        await self.expire_stale_sessions()
        failure = await self.store.fetch_one(
            f"SELECT * FROM {self.login_failures} WHERE device_id=?", (device["id"],)
        )
        if failure and failure["blocked_until"] and datetime.fromisoformat(
            failure["blocked_until"]
        ) > datetime.now(UTC):
            raise ValueError("Çok fazla başarısız giriş. Beş dakika sonra tekrar deneyin.")
        try:
            student = await self.canteen.lookup_student_by_code(student_code)
        except Exception as exc:
            if getattr(exc, "status", None) in (404, 422):
                await self._record_login_failure(device["id"], failure)
                raise ValueError("Öğrenci şifresi geçersiz.") from exc
            raise RuntimeError("Kantin doğrulaması şu anda yapılamıyor.") from exc
        if not student or not student.get("id"):
            await self._record_login_failure(device["id"], failure)
            raise ValueError("Öğrenci şifresi geçersiz.")
        await self.store.execute(
            f"DELETE FROM {self.login_failures} WHERE device_id=?", (device["id"],)
        )
        student_id = str(student["id"])
        occupied = await self.store.fetch_one(
            f"SELECT id FROM {self.sessions} WHERE student_id=? AND state='active'",
            (student_id,),
        )
        if occupied:
            raise ValueError("Bu hesap başka bir tablette açık.")
        occupied_device = await self.store.fetch_one(
            f"SELECT id FROM {self.sessions} WHERE device_id=? AND state='active'",
            (device["id"],),
        )
        if occupied_device:
            raise ValueError("Bu tablette önceki oturum açık.")
        profile = await self.profile(device["profile_id"])
        if not profile:
            raise ValueError("Tablete profil atanmadı.")
        session_id, session_token = str(uuid4()), secrets.token_urlsafe(48)
        stamp = now()
        try:
            await self.store.execute(
                f"INSERT INTO {self.sessions} (id, student_id, student_name, device_id, "
                "token_hash, login_at, last_heartbeat_at, policy_revision) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, student_id, str(student.get("displayName") or ""), device["id"],
                 token_hash(session_token), stamp, stamp, profile["revision"]),
            )
        except Exception as exc:
            # Unique active-session indexes resolve concurrent logins too.
            conflict = await self.store.fetch_one(
                f"SELECT id FROM {self.sessions} WHERE state='active' "
                "AND (student_id=? OR device_id=?)", (student_id, device["id"]),
            )
            if conflict:
                raise ValueError("Bu hesap veya tablet başka bir oturumda açık.") from exc
            raise
        today = datetime.now(ZoneInfo(profile["timezone"])).date().isoformat()
        totals = await self.student_usage(student_id, today)
        return {"sessionId": session_id, "sessionToken": session_token,
                "sessionExpiresAt": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
                "student": {"id": student_id, "name": str(student.get("displayName") or "")},
                "profile": profile, "usage": totals["usage"],
                "extraGrants": totals["grants"], "policyRevision": profile["revision"]}

    async def _record_login_failure(self, device_id: str, previous: dict[str, Any] | None) -> None:
        stamp = datetime.now(UTC)
        recent = previous and stamp - datetime.fromisoformat(previous["last_attempt_at"]) \
            < timedelta(minutes=5)
        attempts = int(previous["attempts"]) + 1 if recent else 1
        blocked = (stamp + timedelta(minutes=5)).isoformat() if attempts >= 5 else None
        await self.store.execute(
            f"INSERT INTO {self.login_failures} (device_id, attempts, last_attempt_at, blocked_until) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(device_id) DO UPDATE SET "
            "attempts=excluded.attempts, last_attempt_at=excluded.last_attempt_at, "
            "blocked_until=excluded.blocked_until",
            (device_id, attempts, stamp.isoformat(), blocked),
        )

    async def policy(self, *, device: dict[str, Any], revision: int,
                     session: dict[str, Any] | None = None) -> dict[str, Any]:
        await self.expire_stale_sessions()
        profile = await self.profile(device["profile_id"])
        if not profile:
            raise ValueError("Tablete profil atanmadı.")
        if session and session["device_id"] != device["id"]:
            raise ValueError("Oturum bu tablete ait değil.")
        result: dict[str, Any] = {"changed": revision != profile["revision"],
                                  "revision": profile["revision"], "serverTime": now()}
        if result["changed"]:
            result["profile"] = profile
        if session:
            today = datetime.now(ZoneInfo(profile["timezone"])).date().isoformat()
            totals = await self.student_usage(session["student_id"], today)
            result["usage"] = totals["usage"]
            result["extraGrants"] = totals["grants"]
        return result

    async def heartbeat(self, *, device: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        stamp = now()
        await self.store.execute(
            f"UPDATE {self.devices} SET last_seen_at=?, app_version=?, "
            "last_policy_applied_at=?, policy_health=? WHERE id=?",
            (stamp, data.get("appVersion", ""), data.get("lastPolicyAppliedAt"),
             data.get("policyHealth", ""), device["id"]),
        )
        session_id = data.get("sessionId")
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
                f"UPDATE {self.sessions} SET last_heartbeat_at=? "
                "WHERE id=? AND device_id=? AND state='active'",
                (stamp, session_id, device["id"]),
            )
        profile = await self.profile(device["profile_id"])
        return {"ok": True, "serverTime": stamp,
                "sessionExpiresAt": (datetime.now(UTC) + timedelta(minutes=30)).isoformat()
                if session_id else None,
                "policyRevision": profile["revision"] if profile else 0}

    async def replace_inventory(self, *, device: dict[str, Any],
                                apps: list[dict[str, Any]]) -> dict[str, Any]:
        await self.store.execute(f"DELETE FROM {self.inventory} WHERE device_id=?", (device["id"],))
        for app in apps:
            await self.store.execute(
                f"INSERT INTO {self.inventory} (device_id, package_name, app_label, "
                "version_name, version_code, is_system_app, is_launchable, installed_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (device["id"], app["packageName"], app.get("appLabel", ""),
                 app.get("versionName", ""), str(app.get("versionCode", "")),
                 int(app.get("isSystemApp", False)), int(app.get("isLaunchable", False)),
                 app.get("installedAt"), app.get("updatedAt")),
            )
        return {"ok": True, "count": len(apps)}

    async def logout(self, *, session: dict[str, Any]) -> dict[str, Any]:
        stamp = now()
        await self.store.execute(
            f"UPDATE {self.sessions} SET state='ended', ended_at=?, token_hash='' "
            "WHERE id=? AND state='active'", (stamp, session["id"]),
        )
        return {"ok": True, "endedAt": stamp}

    async def usage_batch(self, *, session: dict[str, Any],
                          data: dict[str, Any]) -> dict[str, Any]:
        if data["sessionId"] != session["id"] or data["deviceId"] != session["device_id"] \
                or data["studentId"] != session["student_id"]:
            raise ValueError("Kullanım paketi oturumla eşleşmiyor.")
        profile_row = await self.store.fetch_one(
            f"SELECT profile_id FROM {self.devices} WHERE id=?", (session["device_id"],)
        )
        profile = await self.profile(profile_row["profile_id"]) if profile_row else None
        if not profile:
            raise ValueError("Tablete profil atanmadı.")
        started = datetime.fromisoformat(data["startedAt"])
        ended = datetime.fromisoformat(data["endedAt"])
        if started.tzinfo is None or ended.tzinfo is None or ended < started:
            raise ValueError("Kullanım zaman aralığı geçersiz.")
        if session.get("state") == "expired":
            last_heartbeat = datetime.fromisoformat(session["last_heartbeat_at"])
            if datetime.now(UTC) > last_heartbeat + timedelta(hours=24) or \
                    ended > last_heartbeat + timedelta(hours=24):
                raise ValueError("Çevrimdışı kullanım eşitleme süresi dolmuş.")
        if started < datetime.fromisoformat(session["login_at"]) or ended > datetime.now(UTC) + timedelta(minutes=2):
            raise ValueError("Kullanım oturum sınırları dışında.")
        allowed_dates = {started.astimezone(ZoneInfo(profile["timezone"])).date().isoformat(),
                         ended.astimezone(ZoneInfo(profile["timezone"])).date().isoformat()}
        total = 0
        for item in data["usages"]:
            if item["localDate"] not in allowed_dates:
                raise ValueError("Kullanım tarihi profil saat dilimiyle eşleşmiyor.")
            total += int(item["usedSeconds"])
        if total > int((ended - started).total_seconds()) + 5:
            raise ValueError("Kullanım süresi zaman aralığını aşıyor.")
        previous = await self.store.fetch_one(
            f"SELECT id FROM {self.batches} WHERE id=?", (data["batchId"],)
        )
        if not previous:
            for item in data["usages"]:
                await self.store.execute(
                    f"INSERT INTO {self.batch_items} (batch_id, package_name, local_date, "
                    "used_seconds) VALUES (?, ?, ?, ?) ON CONFLICT(batch_id, package_name, local_date) "
                    "DO UPDATE SET used_seconds=excluded.used_seconds",
                    (data["batchId"], item["packageName"], item["localDate"], item["usedSeconds"]),
                )
            await self.store.execute(
                f"INSERT INTO {self.batches} (id, device_id, session_id, student_id, "
                "started_at, ended_at, accepted_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO NOTHING",
                (data["batchId"], data["deviceId"], data["sessionId"], data["studentId"],
                 data["startedAt"], data["endedAt"], now()),
            )
        dates = sorted({item["localDate"] for item in data["usages"]})
        totals = []
        for local_date in dates:
            totals.extend((await self.student_usage(session["student_id"], local_date))["usage"])
        return {"accepted": True, "duplicate": bool(previous), "usage": totals,
                "serverTime": now()}
