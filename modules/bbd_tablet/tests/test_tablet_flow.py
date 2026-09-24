"""Focused server flow: one canteen code, student usage, idempotent batch and grant."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from modules.bbd_tablet.backend.service import TabletService


class Store:
    def __init__(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        migration = Path(__file__).parents[1] / "backend/migrations/001_tablet.sql"
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
    first = await service.usage_batch(session=session, data=batch)
    second = await service.usage_batch(session=session, data=batch)
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["usage"][0]["usedSeconds"] == 120

    await service.grant_time(student_id="opaque-A", package_name="com.example.video",
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
    await service.store.execute(
        f"UPDATE {service.sessions} SET last_heartbeat_at=? WHERE id=?",
        (too_late.isoformat(), session["id"]),
    )
    assert await service.session_for_usage_batch_token(logged["sessionToken"]) is None

    await service.logout(session=offline_session)
    assert await service.session_for_token(logged["sessionToken"]) is None
    assert await service.session_for_usage_batch_token(logged["sessionToken"]) is None
