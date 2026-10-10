"""Focused server flow: one canteen code, student usage, idempotent batch and grant."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from modules.bbd_tablet.backend.api import tablet_routes
from modules.bbd_tablet.backend.service import TabletService
from pydantic import ValidationError


class Store:
    def __init__(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        migrations = Path(__file__).parents[1] / "backend/migrations"
        for migration in sorted(migrations.glob("*.sql")):
            self.db.executescript(migration.read_text())

    @staticmethod
    def table(name: str) -> str:
        return f"mod_bbd_tablet_{name}"

    async def execute(self, sql, params=()):
        self.db.execute(sql, params)
        self.db.commit()

    async def fetch_one(self, sql, params=()):
        row = self.db.execute(sql, params).fetchone()
        return dict(row) if row else None

    async def fetch_all(self, sql, params=()):
        return [dict(row) for row in self.db.execute(sql, params).fetchall()]


class Canteen:
    async def lookup_student_by_code(self, code):
        if code != "123456":
            return None
        return {"id": "opaque-A", "displayName": "Ahmet"}

    async def students(self):
        return [{"id": "opaque-A", "displayName": "Ahmet"}]


def test_heartbeat_profile_identity_is_optional_but_cannot_be_blank():
    legacy = tablet_routes.HeartbeatBody(profileRevision=1)
    assert legacy.profileId is None
    current = tablet_routes.HeartbeatBody(profileRevision=1, profileId="profile-a")
    assert current.profileId == "profile-a"
    with pytest.raises(ValidationError):
        tablet_routes.HeartbeatBody(profileRevision=1, profileId="")


@pytest.mark.asyncio
async def test_system_controls_are_allowlisted_and_follow_profile_revisions():
    service = TabletService(store=Store(), canteen=Canteen(), config={})
    original_controls = {
        "usb_file_transfer": True,
        "unknown_source_installs": False,
        "wifi_configuration": True,
    }
    profile = await service.save_profile(
        profile_id=None, name="Genel Öğrenci", timezone="Europe/Istanbul",
        apps=[], system_controls=original_controls,
    )
    assert profile["systemControls"] == original_controls
    pending = await service.create_device(name="Etüt-01", profile_id=profile["id"])
    enrolled = await service.enroll({"enrollmentCode": pending["enrollmentCode"],
                                     "deviceUuid": str(uuid4()), "deviceName": "Etüt-01"})
    device = await service.device_for_token(enrolled["deviceToken"])
    assert device is not None
    policy = await service.policy(device=device, revision=0)
    assert policy["profile"]["systemControls"] == original_controls

    # Old callers that omit the new field preserve the last revision's choices.
    revised = await service.save_profile(
        profile_id=profile["id"], name="Genel Öğrenci", timezone="Europe/Istanbul",
        apps=[],
    )
    assert revised["revision"] == profile["revision"] + 1
    assert revised["systemControls"] == original_controls

    cleared = await service.save_profile(
        profile_id=profile["id"], name="Genel Öğrenci", timezone="Europe/Istanbul",
        apps=[], system_controls={},
    )
    assert cleared["systemControls"] == {}

    with pytest.raises(ValueError, match="Desteklenmeyen sistem kontrolü"):
        await service.save_profile(
            profile_id=None, name="Hatalı", timezone="Europe/Istanbul", apps=[],
            system_controls={"developer_options_and_adb": False},
        )
    with pytest.raises(ValueError, match="true veya false"):
        await service.save_profile(
            profile_id=None, name="Hatalı", timezone="Europe/Istanbul", apps=[],
            system_controls={"usb_file_transfer": 1},
        )


@pytest.mark.asyncio
async def test_heartbeat_tracks_reported_profile_and_revision_across_reassignment():
    service = TabletService(store=Store(), canteen=Canteen(), config={})
    profile = await service.save_profile(
        profile_id=None, name="Genel Öğrenci", timezone="Europe/Istanbul", apps=[],
    )
    pending = await service.create_device(name="Etüt-01", profile_id=profile["id"])
    enrolled = await service.enroll({
        "enrollmentCode": pending["enrollmentCode"], "deviceUuid": str(uuid4()),
        "deviceName": "Etüt-01",
    })
    device = await service.device_for_token(enrolled["deviceToken"])
    assert device is not None

    await service.heartbeat(device=device, data={
        "profileId": profile["id"], "profileRevision": profile["revision"],
    })
    first_view = next(
        item for item in (await service.overview())["devices"] if item["id"] == device["id"]
    )
    assert first_view["profile_revision"] == profile["revision"]
    assert first_view["applied_profile_revision"] == profile["revision"]
    assert first_view["applied_profile_id"] == profile["id"]

    newer_profile = await service.save_profile(
        profile_id=profile["id"], name="Genel Öğrenci", timezone="Europe/Istanbul",
        apps=[{"packageName": "com.example.video", "appName": "Video", "allowed": True,
               "unlimited": False, "dailyLimitSeconds": 300}],
    )
    stale_view = next(
        item for item in (await service.overview())["devices"] if item["id"] == device["id"]
    )
    assert stale_view["profile_revision"] == newer_profile["revision"]
    assert stale_view["applied_profile_revision"] == profile["revision"]
    assert stale_view["applied_profile_id"] == profile["id"]

    await service.heartbeat(device=device, data={
        "profileId": profile["id"], "profileRevision": newer_profile["revision"],
    })
    current_view = next(
        item for item in (await service.overview())["devices"] if item["id"] == device["id"]
    )
    assert current_view["applied_profile_revision"] == newer_profile["revision"]
    assert current_view["applied_profile_id"] == profile["id"]

    replacement = await service.save_profile(
        profile_id=None, name="Başka Öğrenci", timezone="Europe/Istanbul", apps=[],
    )
    assert replacement["revision"] == profile["revision"]
    await service.assign_profile(device_id=device["id"], profile_id=replacement["id"])
    reassigned = next(
        item for item in (await service.overview())["devices"] if item["id"] == device["id"]
    )
    assert reassigned["profile_revision"] == replacement["revision"]
    assert reassigned["applied_profile_id"] is None
    assert reassigned["applied_profile_revision"] == 0
    current_device = await service.device_for_token(enrolled["deviceToken"])
    assert current_device is not None

    # A delayed heartbeat from the previous assignment cannot make this profile
    # look current, even when the two profiles share a revision number.
    await service.heartbeat(device=current_device, data={
        "profileId": profile["id"], "profileRevision": newer_profile["revision"],
    })
    delayed = next(
        item for item in (await service.overview())["devices"] if item["id"] == device["id"]
    )
    assert delayed["profile_id"] == replacement["id"]
    assert delayed["applied_profile_id"] == profile["id"]
    assert delayed["applied_profile_revision"] == newer_profile["revision"]

    await service.heartbeat(device=current_device, data={
        "profileId": replacement["id"], "profileRevision": replacement["revision"],
    })
    recovered = next(
        item for item in (await service.overview())["devices"] if item["id"] == device["id"]
    )
    assert recovered["applied_profile_id"] == replacement["id"]
    assert recovered["applied_profile_revision"] == replacement["revision"]

    with pytest.raises(ValueError, match="sürümü profil ile uyumlu değil"):
        await service.heartbeat(device=current_device, data={
            "profileId": replacement["id"],
            "profileRevision": replacement["revision"] + 1,
        })
    final_view = next(
        item for item in (await service.overview())["devices"] if item["id"] == device["id"]
    )
    assert final_view["applied_profile_revision"] == replacement["revision"]
    assert final_view["applied_profile_id"] == replacement["id"]


@pytest.mark.asyncio
async def test_student_usage_is_idempotent_and_grant_follows_student():
    service = TabletService(store=Store(), canteen=Canteen(), config={})
    profile = await service.save_profile(
        profile_id=None, name="Genel Öğrenci", timezone="Europe/Istanbul",
        apps=[{"packageName": "com.example.video", "appName": "Video", "allowed": True,
               "unlimited": False, "dailyLimitSeconds": 7200}],
    )
    pending = await service.create_device(name="Etüt-01", profile_id=profile["id"])
    enrolled = await service.enroll({"enrollmentCode": pending["enrollmentCode"],
                                     "deviceUuid": str(uuid4()), "deviceName": "Etüt-01"})
    device = await service.device_for_token(enrolled["deviceToken"])
    assert device and device["id"] == enrolled["deviceId"]
    assert await service.device_for_token("wrong-token") is None

    logged = await service.login(device=device, student_code="123456")
    session = await service.session_for_token(logged["sessionToken"])
    assert logged["student"] == {"id": "opaque-A", "name": "Ahmet"}
    assert session is not None
    overview = await service.overview()
    device_view = next(item for item in overview["devices"] if item["id"] == device["id"])
    assert device_view["online"] is True
    assert device_view["active_student_name"] == "Ahmet"
    assert device_view["profile_revision"] == profile["revision"]

    start = datetime.now(UTC) - timedelta(minutes=3)
    end = datetime.now(UTC)
    await service.store.execute(
        f"UPDATE {service.sessions} SET login_at=? WHERE id=?", (start.isoformat(), session["id"])
    )
    session["login_at"] = start.isoformat()
    local_date = start.astimezone(ZoneInfo("Europe/Istanbul")).date().isoformat()
    batch = {"batchId": str(uuid4()), "deviceId": device["id"],
             "sessionId": session["id"], "studentId": "opaque-A",
             "startedAt": start.isoformat(), "endedAt": end.isoformat(),
             "usages": [{"packageName": "com.example.video", "localDate": local_date,
                         "usedSeconds": 120}]}
    with pytest.raises(ValueError, match="birden fazla"):
        await service.usage_batch(session=session, data={
            **batch, "usages": [*batch["usages"], *batch["usages"]],
        })
    first = await service.usage_batch(session=session, data=batch)
    second = await service.usage_batch(session=session, data=batch)
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["usage"][0]["usedSeconds"] == 120
    with pytest.raises(ValueError, match="farklı içerikle"):
        await service.usage_batch(session=session, data={
            **batch, "usages": [{"packageName": "com.example.video", "localDate": local_date,
                                 "usedSeconds": 60}],
        })

    await service.grant_time(student_id="opaque-A", package_name="com.example.video",
                             local_date=local_date, extra_seconds=600,
                             reason="Ders", actor="Yönetici")
    with pytest.raises(ValueError, match="hiçbir profilde izinli değil"):
        await service.grant_time(student_id="opaque-A", package_name="com.example.other",
                                 local_date=local_date, extra_seconds=600,
                                 reason="Ders", actor="Yönetici")
    policy = await service.policy(device=device, revision=profile["revision"], session=session)
    assert policy["changed"] is False
    assert policy["extraGrants"][0]["extraSeconds"] == 600
    assert policy["usage"][0]["usedSeconds"] == 120

    revised = await service.save_profile(
        profile_id=profile["id"], name="Genel Öğrenci", timezone="Europe/Istanbul",
        apps=[{"packageName": "com.example.video", "appName": "Video", "allowed": True,
               "unlimited": False, "dailyLimitSeconds": 3600}],
    )
    newer = await service.policy(device=device, revision=profile["revision"], session=session)
    assert revised["revision"] == profile["revision"] + 1
    assert newer["changed"] is True
    assert newer["profile"]["apps"][0]["dailyLimitSeconds"] == 3600

    # A stale session bearer may reconcile owned usage for a short offline
    # window, but it does not become a general-purpose active session again.
    last_heartbeat = datetime.now(UTC) - timedelta(minutes=40)
    await service.store.execute(
        f"UPDATE {service.sessions} SET last_heartbeat_at=? WHERE id=?",
        (last_heartbeat.isoformat(), session["id"]),
    )
    offline_session = await service.session_for_usage_batch_token(logged["sessionToken"])
    assert offline_session is not None
    assert offline_session["state"] == "expired"
    assert await service.session_for_token(logged["sessionToken"]) is None
    offline_end = datetime.now(UTC)
    offline_batch = {**batch, "batchId": str(uuid4()),
                     "startedAt": (offline_end - timedelta(minutes=2)).isoformat(),
                     "endedAt": offline_end.isoformat(),
                     "usages": [{"packageName": "com.example.video", "localDate": local_date,
                                 "usedSeconds": 60}]}
    accepted = await service.usage_batch(session=offline_session, data=offline_batch)
    assert accepted["accepted"] is True
    assert accepted["duplicate"] is False
    with pytest.raises(ValueError, match="eşleşmiyor"):
        await service.usage_batch(
            session=offline_session, data={**offline_batch, "studentId": "someone-else"},
        )

    too_late = datetime.now(UTC) - timedelta(hours=25)
    late_start = too_late - timedelta(minutes=2)
    await service.store.execute(
        f"UPDATE {service.sessions} SET login_at=?, last_heartbeat_at=? WHERE id=?",
        ((late_start - timedelta(minutes=5)).isoformat(), too_late.isoformat(), session["id"]),
    )
    # A durable outbox can recover even after 24 hours offline. The expired
    # bearer still has only its own write-only batch scope until terminal logout.
    late_session = await service.session_for_usage_batch_token(logged["sessionToken"])
    assert late_session is not None and late_session["state"] == "expired"
    late_date = too_late.astimezone(ZoneInfo("Europe/Istanbul")).date().isoformat()
    late_batch = {**batch, "batchId": str(uuid4()),
                  "startedAt": late_start.isoformat(), "endedAt": too_late.isoformat(),
                  "usages": [{"packageName": "com.example.video", "localDate": late_date,
                              "usedSeconds": 60}]}
    late_accepted = await service.usage_batch(session=late_session, data=late_batch)
    assert late_accepted["accepted"] is True
    assert late_accepted["duplicate"] is False
    # Terminal logout is narrowly authorized after offline recovery; replay
    # acknowledges a response lost after the first commit.
    terminal = await service.session_for_logout_token(logged["sessionToken"])
    assert terminal is not None and terminal["state"] == "expired"
    ended = await service.logout(session=terminal)
    assert ended["ok"] is True
    assert ended["alreadyEnded"] is False
    repeated_terminal = await service.session_for_logout_token(logged["sessionToken"])
    assert repeated_terminal is not None and repeated_terminal["state"] == "logged_out"
    repeated = await service.logout(session=repeated_terminal)
    assert repeated["ok"] is True
    assert repeated["alreadyEnded"] is True
    assert repeated["endedAt"] == ended["endedAt"]
    assert await service.session_for_usage_batch_token(logged["sessionToken"]) is None
    # Ek süre oturuma bağlı değil: çıkış yapmış öğrenciye de verilebilir.
    offline_grant = await service.grant_time(
        student_id="opaque-A", package_name="com.example.video", local_date=local_date,
        extra_seconds=600, reason="Ders", actor="Yönetici",
    )
    assert offline_grant["extraSeconds"] == 600
    assert await service.session_for_token(logged["sessionToken"]) is None
    assert await service.session_for_usage_batch_token(logged["sessionToken"]) is None


@pytest.mark.asyncio
async def test_device_revoke_is_idempotent_and_releases_hardware_uuid():
    service = TabletService(store=Store(), canteen=Canteen(), config={})
    profile = await service.save_profile(
        profile_id=None, name="Genel Öğrenci", timezone="Europe/Istanbul", apps=[],
    )
    pending = await service.create_device(name="Etüt-01", profile_id=profile["id"])
    hardware_uuid = str(uuid4())
    enrolled = await service.enroll({
        "enrollmentCode": pending["enrollmentCode"], "deviceUuid": hardware_uuid,
        "deviceName": "Etüt-01",
    })
    device = await service.device_for_token(enrolled["deviceToken"])
    assert device is not None
    await service.replace_inventory(device=device, apps=[{
        "packageName": "com.example.video", "appLabel": "Video", "isLaunchable": True,
    }])
    logged = await service.login(device=device, student_code="123456")

    revoked = await service.revoke_device(device_id=device["id"])
    repeated = await service.revoke_device(device_id=device["id"])
    assert revoked["revoked"] is True
    assert revoked["alreadyRevoked"] is False
    assert repeated["alreadyRevoked"] is True
    assert repeated["revokedAt"] == revoked["revokedAt"]
    assert await service.device_for_token(enrolled["deviceToken"]) is None
    assert (await service.revoked_device_for_unpair_token(enrolled["deviceToken"]))["id"] == device["id"]
    assert await service.session_for_token(logged["sessionToken"]) is None
    assert await service.session_for_usage_batch_token(logged["sessionToken"]) is None
    stored_device = await service.store.fetch_one(
        f"SELECT uuid, token_hash, enrollment_code_hash, enrollment_expires_at, profile_id, "
        f"revoked_at FROM {service.devices} WHERE id=?", (device["id"],),
    )
    assert stored_device["uuid"] == f"pending:{device['id']}"
    assert stored_device["token_hash"]  # one-way hash remains only for terminal unpair retry
    assert stored_device["enrollment_code_hash"] is None
    assert stored_device["enrollment_expires_at"] is None
    assert stored_device["profile_id"] is None
    assert stored_device["revoked_at"] == revoked["revokedAt"]
    assert await service.store.fetch_one(
        f"SELECT package_name FROM {service.inventory} WHERE device_id=?", (device["id"],)
    ) is None

    replacement = await service.create_device(name="Etüt-01 yeni eşleme")
    re_enrolled = await service.enroll({
        "enrollmentCode": replacement["enrollmentCode"], "deviceUuid": hardware_uuid,
        "deviceName": "Etüt-01",
    })
    assert re_enrolled["deviceId"] == replacement["id"]
    assert await service.device_for_token(re_enrolled["deviceToken"]) is not None


@pytest.mark.asyncio
async def test_device_self_unpair_checks_bearer_scope_and_revokes_token(monkeypatch):
    service = TabletService(store=Store(), canteen=Canteen(), config={})
    pending = await service.create_device(name="Etüt-02")
    enrolled = await service.enroll({
        "enrollmentCode": pending["enrollmentCode"], "deviceUuid": str(uuid4()),
        "deviceName": "Etüt-02",
    })
    monkeypatch.setattr(tablet_routes, "current_service", lambda: service)
    authorization = f"Bearer {enrolled['deviceToken']}"

    with pytest.raises(HTTPException) as mismatch:
        await tablet_routes.unpair("another-device", authorization=authorization)
    assert mismatch.value.status_code == 403
    assert await service.device_for_token(enrolled["deviceToken"]) is not None

    result = await tablet_routes.unpair(enrolled["deviceId"], authorization=authorization)
    assert result["revoked"] is True
    assert await service.device_for_token(enrolled["deviceToken"]) is None
    replay = await tablet_routes.unpair(enrolled["deviceId"], authorization=authorization)
    assert replay["alreadyRevoked"] is True
    assert replay["revokedAt"] == result["revokedAt"]
