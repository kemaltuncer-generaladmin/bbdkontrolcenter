"""Sözleşme v2: sync, uzak durum, komutlar, kısıtlama, kullanım saatleri,
ek süre sürümü, eşleme kodu, giriş kilidi, silme eylemleri, geçmiş saklama,
günlük özet ve hata kodları."""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from modules.bbd_tablet.backend.api import routes, tablet_routes
from modules.bbd_tablet.backend.rules import (
    ENROLLMENT_ALPHABET,
    allow_always_allowed,
    inside_usage_window,
    next_window_start,
    normalize_usage_windows,
    with_always_allowed,
)
from modules.bbd_tablet.backend.service import (
    ConflictError,
    NotFoundError,
    TabletError,
    TabletService,
)
from pydantic import ValidationError

VIDEO = {"packageName": "com.example.video", "appName": "Video", "allowed": True,
         "unlimited": False, "dailyLimitSeconds": 1800}
BOOKS = {"packageName": "com.example.books", "appName": "Kitaplık", "allowed": True,
         "unlimited": True, "dailyLimitSeconds": 0}
GAME = {"packageName": "com.example.game", "appName": "Oyun", "allowed": False,
        "unlimited": False, "dailyLimitSeconds": 0}
TODAY = "2026-10-14"
AKADEMI = "com.veykemtu.benimdunyam"
# Auth fails before the sync route reads its request.
NO_REQUEST: Any = SimpleNamespace(headers={})


@pytest.fixture
def service(store: Any, canteen: Any, clock: Any) -> TabletService:
    return TabletService(store=store, canteen=canteen, config={}, clock=clock)


async def make_profile(service: TabletService, *, name: str = "Ortaokul",
                       apps: list[dict[str, Any]] | None = None,
                       windows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return await service.save_profile(
        profile_id=None, name=name, timezone="Europe/Istanbul",
        apps=apps if apps is not None else [VIDEO, BOOKS, GAME], usage_windows=windows,
    )


async def pair(service: TabletService, profile_id: str | None = None,
               name: str = "Etüt-01") -> tuple[dict[str, Any], str]:
    pending = await service.create_device(name=name, profile_id=profile_id)
    enrolled = await service.enroll({"enrollmentCode": pending["enrollmentCode"],
                                     "deviceUuid": str(uuid4()), "deviceName": name})
    device = await service.device_for_token(enrolled["deviceToken"])
    assert device is not None
    return device, enrolled["deviceToken"]


async def fresh(service: TabletService, token: str) -> dict[str, Any]:
    device = await service.device_by_token(token)
    assert device is not None
    return device


async def login(service: TabletService, token: str, code: str = "123456") -> dict[str, Any]:
    return await service.login(device=await fresh(service, token), student_code=code)


async def sync(service: TabletService, token: str, **data: Any) -> dict[str, Any]:
    return await service.sync(device=await fresh(service, token), data=data)


async def send_usage(service: TabletService, logged: dict[str, Any], device_id: str,
                     clock: Any, seconds: int, batch_id: str | None = None,
                     local_date: str = TODAY) -> dict[str, Any]:
    session = await service.session_for_usage_batch_token(logged["sessionToken"])
    assert session is not None
    started = datetime.fromisoformat(session["login_at"])
    return await service.usage_batch(session=session, data={
        "batchId": batch_id or str(uuid4()), "deviceId": device_id,
        "sessionId": session["id"], "studentId": session["student_id"],
        "startedAt": started.isoformat(), "endedAt": clock().isoformat(),
        "usages": [{"packageName": VIDEO["packageName"], "localDate": local_date,
                    "usedSeconds": seconds}],
    })


def expect_code(error: pytest.ExceptionInfo[HTTPException], code: str, status: int) -> dict[str, Any]:
    assert error.value.status_code == status
    detail = error.value.detail
    assert isinstance(detail, dict) and detail["code"] == code and detail["message"]
    return detail


# ------------------------------------------------------------- eşleme kodu


async def test_enrollment_code_is_short_readable_and_normalized(service: TabletService) -> None:
    pending = await service.create_device(name="Etüt-01")
    code = pending["enrollmentCode"]
    assert len(code) == 8 and set(code) <= set(ENROLLMENT_ALPHABET)
    assert pending["enrollmentCodeDisplay"] == f"{code[:4]}-{code[4:]}"
    typed = f" {code[:4].lower()} - {code[4:].lower()}\t"
    enrolled = await service.enroll({"enrollmentCode": typed, "deviceUuid": str(uuid4()),
                                     "deviceName": "Etüt-01"})
    assert enrolled["deviceId"] == pending["id"]
    with pytest.raises(ValueError, match="geçersiz"):
        await service.enroll({"enrollmentCode": code, "deviceUuid": str(uuid4()),
                              "deviceName": "Etüt-01"})


async def test_new_code_after_revoke_never_revives_old_token(
        service: TabletService, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "_service", service)
    profile = await make_profile(service)
    device, old_token = await pair(service, profile["id"])
    with pytest.raises(ConflictError):
        await service.issue_enrollment_code(device_id=device["id"])

    await service.revoke_device(device_id=device["id"])
    with pytest.raises(HTTPException) as revoked:
        await tablet_routes.sync(device["id"], tablet_routes.SyncBody(), NO_REQUEST,
                                 authorization=f"Bearer {old_token}")
    expect_code(revoked, "DEVICE_REVOKED", 401)

    issued = await service.issue_enrollment_code(device_id=device["id"],
                                                 profile_id=profile["id"])
    assert issued["profileId"] == profile["id"]
    for call in (
        tablet_routes.sync(device["id"], tablet_routes.SyncBody(), NO_REQUEST,
                           authorization=f"Bearer {old_token}"),
        tablet_routes.unpair(device["id"], authorization=f"Bearer {old_token}"),
    ):
        with pytest.raises(HTTPException) as dead:
            await call
        expect_code(dead, "DEVICE_AUTH_INVALID", 401)

    enrolled = await service.enroll({"enrollmentCode": issued["enrollmentCodeDisplay"],
                                     "deviceUuid": str(uuid4()), "deviceName": "Etüt-01"})
    assert enrolled["deviceId"] == device["id"]
    assert await service.device_for_token(enrolled["deviceToken"]) is not None
    assert await service.device_by_token(old_token) is None


async def test_duplicate_profile_copies_rules_hours_and_controls(service: TabletService) -> None:
    windows = [{"days": [1, 2, 3, 4, 5], "start": "08:30", "end": "16:00"}]
    source = await service.save_profile(
        profile_id=None, name="5. sınıflar", timezone="Europe/Istanbul",
        apps=[VIDEO, BOOKS], system_controls={"factory_reset": True}, usage_windows=windows,
    )
    copy = await service.duplicate_profile(profile_id=source["id"], name=None)
    assert copy["id"] != source["id"]
    assert copy["name"] == "5. sınıflar (kopya)"
    # BBD Akademi kaynağın görünümüne eklenir, kopyada saklanır: sıra numarası değişebilir.
    def rules(profile: dict[str, Any]) -> list[dict[str, Any]]:
        return [{k: v for k, v in app.items() if k != "sortOrder"} for app in profile["apps"]]
    assert rules(copy) == rules(source)
    assert copy["usageWindows"] == windows
    assert copy["systemControls"] == {"factory_reset": True}


# --------------------------------------------------------------- profil


async def test_app_modes_and_usage_windows_are_validated(service: TabletService) -> None:
    for limit in (0, 59, 86401):
        with pytest.raises(ValueError, match="1 dakika ile 24 saat"):
            await make_profile(service, apps=[{**VIDEO, "dailyLimitSeconds": limit}])
    profile = await make_profile(service, apps=[{**VIDEO, "dailyLimitSeconds": 60},
                                                {**BOOKS, "dailyLimitSeconds": 999},
                                                {**GAME, "dailyLimitSeconds": 999}])
    limits = {app["packageName"]: app["dailyLimitSeconds"] for app in profile["apps"]}
    assert limits == {VIDEO["packageName"]: 60, BOOKS["packageName"]: 0,
                      GAME["packageName"]: 0, AKADEMI: 0}

    for windows, message in (
        ([{"days": [8], "start": "08:00", "end": "09:00"}], "1 \\(Pazartesi\\)"),
        ([{"days": [1], "start": "8:00", "end": "09:00"}], "SS:DD"),
        ([{"days": [1], "start": "10:00", "end": "09:00"}], "önce olmalı"),
        ([{"days": [1], "start": "08:00", "end": "09:00"}] * 22, "En fazla"),
    ):
        with pytest.raises(ValueError, match=message):
            await make_profile(service, windows=windows)

    hours = await make_profile(service, windows=[{"days": [3, 1, 3], "start": "08:00",
                                                  "end": "09:00"}])
    assert hours["usageWindows"] == [{"days": [1, 3], "start": "08:00", "end": "09:00"}]
    # Omitted windows keep the previous revision's hours.
    kept = await service.save_profile(profile_id=hours["id"], name="Ortaokul",
                                      timezone="Europe/Istanbul", apps=[VIDEO])
    assert kept["usageWindows"] == hours["usageWindows"]


async def test_daily_total_is_validated_kept_copied_and_sent(service: TabletService) -> None:
    for total in (59, 86401):
        with pytest.raises(ValueError, match="toplam süre"):
            await service.save_profile(profile_id=None, name="Ortaokul", timezone="Europe/Istanbul",
                                       apps=[VIDEO], daily_total_limit_seconds=total)
    plain = await make_profile(service)
    assert plain["dailyTotalLimitSeconds"] == 0
    profile = await service.save_profile(profile_id=None, name="Ortaokul",
                                         timezone="Europe/Istanbul", apps=[VIDEO, BOOKS],
                                         daily_total_limit_seconds=5400)
    assert profile["dailyTotalLimitSeconds"] == 5400
    # Omitted keeps the previous revision's total; 0 clears it.
    kept = await service.save_profile(profile_id=profile["id"], name="Ortaokul",
                                      timezone="Europe/Istanbul", apps=[VIDEO])
    assert kept["dailyTotalLimitSeconds"] == 5400
    copy = await service.duplicate_profile(profile_id=profile["id"], name=None)
    assert copy["dailyTotalLimitSeconds"] == 5400

    _, token = await pair(service, kept["id"])
    logged = await login(service, token)
    assert logged["profile"]["dailyTotalLimitSeconds"] == 5400
    usage = await service.student_usage(logged["student"]["id"], TODAY)
    assert usage["dailyTotal"] == {"limitSeconds": 5400, "usedSeconds": 0,
                                   "remainingSeconds": 5400}

    cleared = await service.save_profile(profile_id=profile["id"], name="Ortaokul",
                                         timezone="Europe/Istanbul", apps=[VIDEO],
                                         daily_total_limit_seconds=0)
    assert cleared["dailyTotalLimitSeconds"] == 0
    assert (await service.student_usage(logged["student"]["id"], TODAY))["dailyTotal"] is None


def test_next_window_start_is_in_profile_time_zone() -> None:
    windows = normalize_usage_windows([{"days": [4], "start": "08:00", "end": "09:00"},
                                       {"days": [3], "start": "14:00", "end": "15:00"}])
    wednesday = datetime.fromisoformat("2026-10-14T10:00:00+03:00")
    assert not inside_usage_window(windows, wednesday)
    assert next_window_start(windows, wednesday).isoformat() == "2026-10-14T14:00:00+03:00"
    late = datetime.fromisoformat("2026-10-14T15:00:00+03:00")
    assert not inside_usage_window(windows, late)
    assert next_window_start(windows, late).isoformat() == "2026-10-15T08:00:00+03:00"
    assert inside_usage_window(windows, datetime.fromisoformat("2026-10-14T14:59:00+03:00"))
    assert inside_usage_window([], wednesday)


async def test_login_outside_hours_and_sync_closes_session_at_window_end(
        service: TabletService, clock: Any) -> None:
    closed = await make_profile(service, windows=[{"days": [4], "start": "08:00",
                                                   "end": "09:00"}])
    device, token = await pair(service, closed["id"])
    with pytest.raises(TabletError) as outside:
        await login(service, token)
    assert outside.value.status == 403
    assert outside.value.detail() == {"code": "OUTSIDE_USAGE_HOURS",
                                      "message": "Tabletler şu anda kapalı.",
                                      "nextWindowStart": "2026-10-15T08:00:00+03:00"}

    open_now = await make_profile(service, name="Açık", windows=[
        {"days": [3], "start": "09:00", "end": "10:30"}])
    await service.assign_profile(device_id=device["id"], profile_id=open_now["id"])
    logged = await login(service, token)
    clock.advance(minutes=20)
    assert (await sync(service, token, sessionId=logged["sessionId"]))["session"]["state"] \
        == "active"
    clock.advance(minutes=20)
    ended = await sync(service, token, sessionId=logged["sessionId"])
    assert ended["session"]["state"] == "ended"
    assert ended["session"]["endReason"] == "outside_hours"
    assert ended["nextSyncSeconds"] == 300


# ------------------------------------------------------------------ sync


async def test_sync_sends_only_changes_and_no_change_path_is_cheap(
        service: TabletService, store: Any) -> None:
    profile = await make_profile(service)
    device, token = await pair(service, profile["id"])
    logged = await login(service, token)
    assert logged["serverTime"] and logged["nextSyncSeconds"] == 60
    assert logged["usageFlushSeconds"] == 300
    assert logged["deviceState"] == {"version": 1, "locked": False, "lockMessage": "",
                                     "offlineMode": "local", "announcement": "",
                                     "loginLockedUntil": None}
    first = await sync(service, token, sessionId=logged["sessionId"],
                       activeStudentId="opaque-A")
    assert first["profile"]["id"] == profile["id"]
    assert first["deviceState"]["version"] == 1
    assert first["grants"]["version"] == logged["grantsVersion"]
    assert first["session"]["state"] == "active" and first["session"]["expiresAt"]

    current = await fresh(service, token)
    store.reset()
    quiet = await service.sync(device=current, data={
        "sessionId": logged["sessionId"], "profileId": profile["id"],
        "profileRevision": profile["revision"], "deviceStateVersion": 1,
        "grantsVersion": logged["grantsVersion"], "batteryPercent": 81, "charging": False,
        "foregroundPackage": VIDEO["packageName"], "pendingUsageBatches": 0,
        "appVersion": "0.6.0", "policyHealth": "ok",
    })
    assert quiet["profile"] is None and quiet["deviceState"] is None
    assert quiet["grants"] is None and quiet["commands"] == []
    assert quiet["assignedProfileId"] == profile["id"]
    assert quiet["profileRevision"] == profile["revision"]
    assert quiet["nextSyncSeconds"] == 60
    assert store.reads <= 4 and store.writes <= 2, (store.reads, store.writes)

    await service.logout(session=await service.session_for_logout_token(logged["sessionToken"]))
    current = await fresh(service, token)
    store.reset()
    idle = await service.sync(device=current, data={
        "profileId": profile["id"], "profileRevision": profile["revision"],
        "deviceStateVersion": 1,
    })
    assert idle["session"] is None and idle["nextSyncSeconds"] == 300
    assert store.reads <= 3 and store.writes <= 1, (store.reads, store.writes)

    revised = await service.save_profile(profile_id=profile["id"], name="Ortaokul",
                                         timezone="Europe/Istanbul", apps=[VIDEO])
    changed = await sync(service, token, profileId=profile["id"],
                         profileRevision=profile["revision"], deviceStateVersion=1)
    assert changed["profile"]["revision"] == revised["revision"]
    view = next(item for item in (await service.overview())["devices"]
                if item["id"] == device["id"])
    assert view["sync_state"] == "behind"
    await sync(service, token, profileId=profile["id"], profileRevision=revised["revision"])
    view = next(item for item in (await service.overview())["devices"]
                if item["id"] == device["id"])
    assert view["sync_state"] == "current"


async def test_device_state_version_lock_and_unlock(service: TabletService) -> None:
    profile = await make_profile(service)
    device, token = await pair(service, profile["id"])
    logged = await login(service, token)

    patched = await service.update_device(device_id=device["id"], changes={
        "locked": True, "lockMessage": "Sınav başladı"})
    assert patched["stateVersion"] == 2 and patched["endedSessions"] == 1
    result = await sync(service, token, sessionId=logged["sessionId"], deviceStateVersion=1)
    assert result["deviceState"] == {"version": 2, "locked": True,
                                     "lockMessage": "Sınav başladı", "offlineMode": "local",
                                     "announcement": "", "loginLockedUntil": None}
    assert result["session"] == {"id": logged["sessionId"], "state": "ended",
                                 "endReason": "admin", "message": "Sınav başladı",
                                 "expiresAt": result["session"]["expiresAt"]}
    with pytest.raises(TabletError) as locked:
        await login(service, token)
    assert locked.value.code == "DEVICE_LOCKED" and locked.value.status == 423

    renamed = await service.update_device(device_id=device["id"], changes={"name": "Etüt-9"})
    assert renamed["stateVersion"] == 2
    unlocked = await service.update_device(device_id=device["id"], changes={
        "locked": False, "offlineMode": "denied", "announcement": "Teneffüs 10:40"})
    assert unlocked["stateVersion"] == 3
    state = (await sync(service, token, deviceStateVersion=2))["deviceState"]
    assert state["offlineMode"] == "denied" and state["announcement"] == "Teneffüs 10:40"
    assert (await sync(service, token, deviceStateVersion=3))["deviceState"] is None
    with pytest.raises(ValueError, match="Çevrimdışı kip"):
        await service.update_device(device_id=device["id"], changes={"offlineMode": "always"})
    assert (await login(service, token))["deviceState"]["version"] == 3


async def test_command_lifecycle_and_other_device_cannot_ack(
        service: TabletService, clock: Any) -> None:
    profile = await make_profile(service)
    device, token = await pair(service, profile["id"])
    other, other_token = await pair(service, profile["id"], name="Etüt-02")
    with pytest.raises(ValueError, match="Mesaj metni"):
        await service.create_command(device_id=device["id"], command_type="message",
                                     payload={}, actor="Yönetici")
    message = await service.create_command(
        device_id=device["id"], command_type="message", payload={"text": "Tabletleri topla"},
        actor="Yönetici")
    assert message["status"] == "pending"
    await service.create_command(device_id=device["id"], command_type="refresh_inventory",
                                 payload={"ignored": True}, actor="Yönetici")

    delivered = await sync(service, token)
    assert [item["type"] for item in delivered["commands"]] == ["message", "refresh_inventory"]
    assert delivered["commands"][0]["payload"] == {"text": "Tabletleri topla"}
    assert delivered["commands"][1]["payload"] == {}

    await sync(service, other_token, commandResults=[{"id": message["id"], "ok": True}])
    listed = {item["id"]: item for item in await service.list_commands(device_id=device["id"])}
    assert listed[message["id"]]["status"] == "delivered"
    assert listed[message["id"]]["deliveryCount"] == 1

    acked = await sync(service, token, commandResults=[
        {"id": message["id"], "ok": True, "message": ""},
        {"id": delivered["commands"][1]["id"], "ok": False, "message": "unsupported"}])
    assert acked["commands"] == []
    listed = {item["id"]: item for item in await service.list_commands(device_id=device["id"])}
    assert listed[message["id"]]["status"] == "done"
    assert listed[delivered["commands"][1]["id"]]["status"] == "failed"
    assert listed[delivered["commands"][1]["id"]]["resultMessage"] == "unsupported"

    reboot = await service.create_command(device_id=other["id"], command_type="reboot",
                                          payload={}, actor="Yönetici")
    clock.advance(hours=25)
    assert (await sync(service, other_token))["commands"] == []
    assert (await service.list_commands(device_id=other["id"]))[0]["id"] == reboot["id"]
    assert (await service.list_commands(device_id=other["id"]))[0]["status"] == "expired"

    await service.revoke_device(device_id=other["id"])
    with pytest.raises(ConflictError):
        await service.create_command(device_id=other["id"], command_type="flush_usage",
                                     payload={}, actor="Yönetici")


async def test_command_backlog_is_delivered_in_pages_of_one_hundred(
        service: TabletService) -> None:
    profile = await make_profile(service)
    device, token = await pair(service, profile["id"])
    for index in range(120):
        await service.create_command(device_id=device["id"], command_type="message",
                                     payload={"text": f"Duyuru {index}"}, actor="Yönetici")
    first = await sync(service, token)
    assert len(first["commands"]) == 100
    undelivered = await service.store.fetch_one(
        f"SELECT COUNT(*) AS total FROM {service.commands} WHERE delivered_at IS NULL")
    assert undelivered is not None and undelivered["total"] == 20

    # The tablet may report more results than one request reads; nothing is refused.
    body = tablet_routes.SyncBody(commandResults=[
        {"id": item["id"], "ok": True} for item in first["commands"]] * 2)
    second = await service.sync(device=await fresh(service, token), data=body.model_dump())
    assert len(second["commands"]) == 20
    assert {item["payload"]["text"] for item in second["commands"]} == {
        f"Duyuru {index}" for index in range(100, 120)}


async def test_bulk_reports_each_tablet(service: TabletService) -> None:
    profile = await make_profile(service)
    device, token = await pair(service, profile["id"])
    second, _ = await pair(service, profile["id"], name="Etüt-02")
    result = await service.bulk(device_ids=[device["id"], "missing", device["id"]],
                                action="lock", actor="Yönetici", lock_message="Sınav")
    assert result["okCount"] == 1 and result["failedCount"] == 1
    assert result["results"][1] == {"deviceId": "missing", "ok": False,
                                    "message": "Tablet bulunamadı."}
    assert (await fresh(service, token))["locked"] == 1

    with pytest.raises(ValueError, match="Profil bulunamadı"):
        await service.bulk(device_ids=[device["id"]], action="assign_profile",
                           actor="Yönetici", profile_id="missing")
    other = await make_profile(service, name="Lise")
    moved = await service.bulk(device_ids=[device["id"], second["id"]],
                               action="assign_profile", actor="Yönetici",
                               profile_id=other["id"])
    assert moved["okCount"] == 2
    sent = await service.bulk(device_ids=[second["id"]], action="command", actor="Yönetici",
                              command={"type": "message", "payload": {"text": "Merhaba"}})
    assert sent["okCount"] == 1
    assert (await service.list_commands(device_id=second["id"]))[0]["type"] == "message"


# --------------------------------------------------------------- oturumlar


async def test_admin_end_keeps_batch_open_until_logout(
        service: TabletService, clock: Any) -> None:
    profile = await make_profile(service)
    device, token = await pair(service, profile["id"])
    _, other_token = await pair(service, profile["id"], name="Etüt-02")
    logged = await login(service, token)
    clock.advance(minutes=5)

    ended = await service.end_session(session_id=logged["sessionId"], message="Derse dön")
    assert ended["ended"] is True
    again = await service.end_session(session_id=logged["sessionId"])
    assert again["alreadyEnded"] is True
    session = (await sync(service, token, sessionId=logged["sessionId"]))["session"]
    assert (session["state"], session["endReason"], session["message"]) == \
        ("ended", "admin", "Derse dön")

    accepted = await send_usage(service, logged, device["id"], clock, 120)
    assert accepted["accepted"] is True
    assert (await login(service, other_token))["student"]["id"] == "opaque-A"

    terminal = await service.session_for_logout_token(logged["sessionToken"])
    assert (await service.logout(session=terminal))["ok"] is True
    assert await service.session_for_usage_batch_token(logged["sessionToken"]) is None
    history = await service.list_sessions(device_id=device["id"])
    assert history[0]["endReason"] == "admin" and history[0]["message"] == "Derse dön"
    assert history[0]["deviceName"] == "Etüt-01"
    assert {item["studentId"] for item in await service.list_sessions(student_id="opaque-A")} \
        == {"opaque-A"}


async def test_orphan_server_session_is_closed_by_sessionless_sync(
        service: TabletService, clock: Any) -> None:
    profile = await make_profile(service)
    _, token = await pair(service, profile["id"])
    logged = await login(service, token)
    clock.advance(minutes=1)
    await sync(service, token)
    assert (await service.list_sessions())[0]["state"] == "active"
    clock.advance(minutes=2)
    await sync(service, token)
    session = (await service.list_sessions())[0]
    assert session["id"] == logged["sessionId"]
    assert (session["state"], session["endReason"]) == ("ended", "expired")


async def test_logout_after_orphan_sweep_records_the_real_reason(
        service: TabletService, clock: Any) -> None:
    # The tablet closed both sessions itself; a sessionless sync reached the
    # server before each queued logout did.
    plain = await make_profile(service)
    hours = await make_profile(service, name="Sabah", windows=[
        {"days": [3], "start": "09:00", "end": "10:30"}])
    _, token = await pair(service, plain["id"])
    hours_device, hours_token = await pair(service, hours["id"], name="Etüt-02")
    logged = await login(service, token)
    other = await login(service, hours_token, code="654321")
    clock.advance(minutes=20)
    await sync(service, token, sessionId=logged["sessionId"])
    await sync(service, hours_token, sessionId=other["sessionId"])
    clock.advance(minutes=15)  # 10:35, after the morning window
    await sync(service, token)
    await sync(service, hours_token)
    assert {(item["state"], item["endReason"]) for item in await service.list_sessions()} \
        == {("ended", "expired")}

    for session in (logged, other):
        terminal = await service.session_for_logout_token(session["sessionToken"])
        assert (await service.logout(session=terminal))["ok"] is True
    reasons = {item["deviceId"]: item["endReason"] for item in await service.list_sessions()}
    assert reasons[hours_device["id"]] == "outside_hours"
    assert set(reasons.values()) == {"logout", "outside_hours"}


async def test_stale_sweep_runs_at_most_once_a_minute(
        service: TabletService, store: Any) -> None:
    await service.expire_stale_sessions()
    store.reset()
    await service.expire_stale_sessions()
    assert store.writes == 0
    await service.expire_stale_sessions(force=True)
    assert store.writes == 1


# ---------------------------------------------------------- kısıtlama


async def test_student_block_ends_session_and_rejects_login(
        service: TabletService, clock: Any) -> None:
    profile = await make_profile(service)
    _, token = await pair(service, profile["id"])
    logged = await login(service, token)
    with pytest.raises(ValueError, match="gelecekte"):
        await service.block_student(student_id="opaque-A", until=clock() - timedelta(minutes=1),
                                    reason="", actor="Yönetici")
    with pytest.raises(ValueError, match="kantinde bulunamadı"):
        await service.block_student(student_id="ghost", until=clock() + timedelta(hours=1),
                                    reason="", actor="Yönetici")

    # A naive time from the panel is read in the module's time zone.
    block = await service.block_student(student_id="opaque-A",
                                        until=datetime.fromisoformat("2026-10-14T11:00"),
                                        reason="Ders saatinde oyun", actor="Yönetici")
    assert block["endedSessions"] == 1 and block["active"] is True
    assert block["until"] == "2026-10-14T08:00:00+00:00"
    session = (await sync(service, token, sessionId=logged["sessionId"]))["session"]
    assert (session["state"], session["endReason"]) == ("ended", "blocked")
    with pytest.raises(TabletError) as blocked:
        await login(service, token)
    assert blocked.value.status == 403
    assert blocked.value.detail()["until"] == "2026-10-14T11:00:00+03:00"
    assert (await service.students())[0]["blockedUntil"] == block["until"]

    assert (await service.unblock_student(student_id="opaque-A"))["lifted"] == 1
    assert (await service.list_blocks(student_id="opaque-A"))[0]["active"] is False
    assert (await login(service, token))["student"]["id"] == "opaque-A"


# ------------------------------------------------------------- ek süre


async def test_grants_version_follows_grant_and_revoke(
        service: TabletService, clock: Any) -> None:
    profile = await make_profile(service)
    _, token = await pair(service, profile["id"])
    logged = await login(service, token)
    version = logged["grantsVersion"]
    assert logged["extraGrants"] == []
    assert (await sync(service, token, sessionId=logged["sessionId"],
                       grantsVersion=version))["grants"] is None

    clock.advance(minutes=1)
    grant = await service.grant_time(student_id="opaque-A", package_name=VIDEO["packageName"],
                                     local_date=TODAY, extra_seconds=600, reason="Ödev",
                                     actor="Yönetici")
    granted = (await sync(service, token, sessionId=logged["sessionId"],
                          grantsVersion=version))["grants"]
    assert granted["version"] != version
    assert granted["items"][0]["id"] == grant["id"]
    assert granted["items"][0]["extraSeconds"] == 600

    clock.advance(minutes=1)
    revoked = await service.revoke_grant(student_id="opaque-A", grant_id=grant["id"],
                                         actor="Yönetici")
    assert revoked["alreadyRevoked"] is False
    after = (await sync(service, token, sessionId=logged["sessionId"],
                        grantsVersion=granted["version"]))["grants"]
    assert after["version"] not in (version, granted["version"])
    assert after["items"] == []
    repeat = await service.revoke_grant(student_id="opaque-A", grant_id=grant["id"],
                                        actor="Yönetici")
    assert repeat["alreadyRevoked"] is True
    with pytest.raises(NotFoundError):
        await service.revoke_grant(student_id="opaque-B", grant_id=grant["id"], actor="Y")
    with pytest.raises(ValueError, match="hiçbir profilde izinli değil"):
        await service.grant_time(student_id="opaque-A", package_name=GAME["packageName"],
                                 local_date=TODAY, extra_seconds=60, reason="", actor="Y")
    # No session needed: a student who never logged in can get extra time.
    assert (await service.grant_time(student_id="opaque-B", package_name=VIDEO["packageName"],
                                     local_date=TODAY, extra_seconds=60, reason="",
                                     actor="Y"))["studentId"] == "opaque-B"


async def test_canteen_student_list_is_cached(service: TabletService, canteen: Any) -> None:
    await make_profile(service)
    await service.students()
    await service.grant_time(student_id="opaque-A", package_name=VIDEO["packageName"],
                             local_date=TODAY, extra_seconds=60, reason="", actor="Y")
    await service.students()
    assert canteen.student_calls == 1


# ------------------------------------------------------ kullanım özeti


async def test_daily_usage_summary_reports_and_student_view(
        service: TabletService, clock: Any) -> None:
    profile = await make_profile(service)
    device, token = await pair(service, profile["id"])
    logged = await login(service, token)
    clock.advance(minutes=5)
    batch_id = str(uuid4())
    await send_usage(service, logged, device["id"], clock, 60, batch_id)
    duplicate = await send_usage(service, logged, device["id"], clock, 60, batch_id)
    assert duplicate["duplicate"] is True
    clock.advance(minutes=5)
    second = await send_usage(service, logged, device["id"], clock, 120)
    assert second["usage"] == [{"packageName": VIDEO["packageName"], "localDate": TODAY,
                                "usedSeconds": 180}]
    await service.grant_time(student_id="opaque-A", package_name=VIDEO["packageName"],
                             local_date=TODAY, extra_seconds=600, reason="", actor="Y")

    view = await service.student_usage("opaque-A", TODAY)
    assert view["profile"] == {"id": profile["id"], "name": "Ortaokul"}
    apps = {item["packageName"]: item for item in view["usage"]}
    assert apps[VIDEO["packageName"]] == {
        "packageName": VIDEO["packageName"], "appName": "Video", "localDate": TODAY,
        "usedSeconds": 180, "allowed": True, "unlimited": False, "limitSeconds": 1800,
        "extraSeconds": 600, "remainingSeconds": 1800 + 600 - 180}
    assert apps[BOOKS["packageName"]]["unlimited"] is True
    assert apps[BOOKS["packageName"]]["limitSeconds"] is None
    assert GAME["packageName"] not in apps
    assert view["grants"][0]["appName"] == "Video"

    report = await service.daily_report(TODAY)
    assert report["rows"] == [{"studentId": "opaque-A", "studentName": "Ahmet",
                               "packageName": VIDEO["packageName"], "appName": "Video",
                               "usedSeconds": 180}]
    assert report["apps"] == [{"packageName": VIDEO["packageName"], "appName": "Video",
                               "usedSeconds": 180, "studentCount": 1}]
    assert report["totalSeconds"] == 180


async def test_batch_with_an_app_banned_mid_session_is_counted_whole(
        service: TabletService, clock: Any) -> None:
    profile = await make_profile(service)
    device, token = await pair(service, profile["id"])
    logged = await login(service, token)
    clock.advance(minutes=5)
    # The rule changes while the queued batch still holds the app's minutes.
    await service.save_profile(profile_id=profile["id"], name="Ortaokul",
                               timezone="Europe/Istanbul",
                               apps=[{**VIDEO, "allowed": False}, BOOKS])
    session = await service.session_for_usage_batch_token(logged["sessionToken"])
    assert session is not None
    accepted = await service.usage_batch(session=session, data={
        "batchId": str(uuid4()), "deviceId": device["id"], "sessionId": session["id"],
        "studentId": session["student_id"], "startedAt": session["login_at"],
        "endedAt": clock().isoformat(),
        "usages": [{"packageName": VIDEO["packageName"], "localDate": TODAY, "usedSeconds": 90},
                   {"packageName": BOOKS["packageName"], "localDate": TODAY, "usedSeconds": 120}],
    })
    assert accepted["accepted"] is True
    assert {(row["packageName"], row["usedSeconds"]) for row in accepted["usage"]} == {
        (VIDEO["packageName"], 90), (BOOKS["packageName"], 120)}


async def test_unnamed_apps_and_padded_profile_names_are_normalized(
        service: TabletService) -> None:
    profile = await make_profile(service, name="  5-A  ", apps=[
        {**VIDEO, "appName": "  "}, {**BOOKS, "appName": ""}])
    assert profile["name"] == "5-A"
    assert {app["packageName"]: app["appName"] for app in profile["apps"]} == {
        VIDEO["packageName"]: VIDEO["packageName"], BOOKS["packageName"]: BOOKS["packageName"],
        AKADEMI: "BBD Akademi"}
    with pytest.raises(ValueError, match="Profil adı"):
        await make_profile(service, name="   ")


async def test_name_migration_repairs_stored_rows(store: Any) -> None:
    profiles, apps = store.table("profiles"), store.table("profile_apps")
    await store.execute(
        f"INSERT INTO {profiles} (id, name, revision, timezone, created_at, updated_at) "
        "VALUES ('p1', '   ', 1, 'Europe/Istanbul', 'a', 'a'), "
        "('p2', ' Lise ', 1, 'Europe/Istanbul', 'a', 'a')"
    )
    await store.execute(
        f"INSERT INTO {apps} (profile_id, revision, package_name, app_name, allowed, "
        "unlimited, daily_limit_seconds, sort_order) "
        "VALUES ('p1', 1, 'com.example.video', ' ', 1, 1, 0, 0)"
    )
    migration = Path(__file__).parents[1] / "backend/migrations/006_profile_names.sql"
    await store.database.execute_script(migration.read_text(encoding="utf-8"))
    names = {row["id"]: row["name"] for row in await store.fetch_all(
        f"SELECT id, name FROM {profiles}")}
    assert names == {"p1": "Adsız profil", "p2": "Lise"}
    assert (await store.fetch_one(f"SELECT app_name FROM {apps}"))["app_name"] \
        == "com.example.video"


async def test_backfill_migration_rebuilds_daily_usage(store: Any) -> None:
    batches, items = store.table("usage_batches"), store.table("usage_batch_items")
    for batch_id, seconds in (("legacy-1", 60), ("legacy-2", 90)):
        await store.execute(
            f"INSERT INTO {batches} (id, device_id, session_id, student_id, started_at, "
            "ended_at, accepted_at) VALUES (?, 'd', 's', 'opaque-A', 'a', 'b', 'c')", (batch_id,),
        )
        await store.execute(
            f"INSERT INTO {items} (batch_id, package_name, local_date, used_seconds) "
            "VALUES (?, 'com.example.video', ?, ?)", (batch_id, TODAY, seconds),
        )
    migration = Path(__file__).parents[1] / "backend/migrations/005_daily_usage_backfill.sql"
    await store.database.execute_script(migration.read_text(encoding="utf-8"))
    rows = await store.fetch_all(f"SELECT * FROM {store.table('daily_usage')}")
    assert rows == [{"student_id": "opaque-A", "package_name": "com.example.video",
                     "local_date": TODAY, "used_seconds": 150}]


async def test_inventory_unchanged_costs_no_writes(service: TabletService, store: Any) -> None:
    profile = await make_profile(service)
    _, token = await pair(service, profile["id"])
    apps = [{"packageName": "com.example.video", "appLabel": "Video", "isLaunchable": True}]
    assert (await service.replace_inventory(device=await fresh(service, token),
                                            apps=apps))["unchanged"] is False
    current = await fresh(service, token)
    store.reset()
    assert (await service.replace_inventory(device=current, apps=apps))["unchanged"] is True
    assert store.writes == 0


# --------------------------------------------------------------- özet


async def test_overview_summary_statuses_and_no_per_profile_queries(
        service: TabletService, store: Any, clock: Any) -> None:
    profile = await make_profile(service)
    active, active_token = await pair(service, profile["id"])
    idle, idle_token = await pair(service, profile["id"], name="Etüt-02")
    pending = await service.create_device(name="Etüt-03", profile_id=profile["id"])
    await service.replace_inventory(device=await fresh(service, active_token), apps=[
        {"packageName": VIDEO["packageName"], "appLabel": "Video uygulaması"}])
    await service.update_device(device_id=idle["id"], changes={"locked": True})
    await sync(service, idle_token)
    logged = await login(service, active_token)
    clock.advance(minutes=15)
    await sync(service, active_token, sessionId=logged["sessionId"],
               profileId=profile["id"], profileRevision=profile["revision"],
               batteryPercent=64, charging=True, foregroundPackage=VIDEO["packageName"])
    await service.create_command(device_id=active["id"], command_type="flush_usage",
                                 payload={}, actor="Y")

    result = await service.overview()
    assert result["summary"] == {"tabletsTotal": 3, "online": 1, "offline": 1,
                                 "activeSessions": 1, "pendingPairing": 1,
                                 "behindPolicy": 1, "locked": 1, "loginLocked": 0,
                                 "revoked": 0}
    views = {item["id"]: item for item in result["devices"]}
    live = views[active["id"]]
    assert (live["status"], live["sync_state"], live["profile_name"]) == \
        ("online", "current", "Ortaokul")
    assert live["foreground_app_name"] == "Video"
    assert (live["battery_percent"], live["charging"]) == (64, True)
    assert live["active_student_name"] == "Ahmet" and live["pending_commands"] == 1
    assert "token_hash" not in live
    assert (views[idle["id"]]["status"], views[idle["id"]]["sync_state"]) == \
        ("offline", "behind")
    assert views[idle["id"]]["locked"] is True
    assert views[pending["id"]]["status"] == "pending"
    assert views[pending["id"]]["enrollment_code_expired"] is True
    assert result["profiles"][0]["deviceCount"] == 3
    assert result["sessions"][0]["device_name"] == "Etüt-01"

    store.reset()
    await service.overview()
    baseline = store.reads
    for index in range(5):
        await make_profile(service, name=f"Profil {index}")
    await service.overview()
    store.reset()
    await service.overview()
    assert store.reads == baseline


# ------------------------------------------------------------ giriş kilidi


async def wrong_codes(service: TabletService, token: str, times: int) -> TabletError:
    error: TabletError | None = None
    for _ in range(times):
        with pytest.raises(TabletError) as failed:
            await login(service, token, code="000000")
        error = failed.value
    assert error is not None
    return error


async def test_login_lock_is_device_state_and_panel_can_lift_it(
        service: TabletService, clock: Any) -> None:
    profile = await make_profile(service)
    device, token = await pair(service, profile["id"])
    assert (await wrong_codes(service, token, 4)).code == "INVALID_STUDENT_CODE"
    assert (await fresh(service, token))["state_version"] == 1
    assert (await wrong_codes(service, token, 1)).code == "LOGIN_RATE_LIMITED"
    assert (await fresh(service, token))["state_version"] == 2
    until = (clock() + timedelta(minutes=5)).isoformat()
    state = (await sync(service, token, deviceStateVersion=1))["deviceState"]
    assert (state["version"], state["loginLockedUntil"]) == (2, until)
    # A try during the lock is refused before the canteen and changes nothing.
    assert (await wrong_codes(service, token, 1)).code == "LOGIN_RATE_LIMITED"
    assert (await sync(service, token, deviceStateVersion=2))["deviceState"] is None

    overview = await service.overview()
    view = next(item for item in overview["devices"] if item["id"] == device["id"])
    assert view["login_locked_until"] == until
    assert overview["summary"]["loginLocked"] == 1

    assert await service.reset_login_lock(device_id=device["id"]) == {
        "deviceId": device["id"], "cleared": True}
    state = (await sync(service, token, deviceStateVersion=2))["deviceState"]
    assert (state["version"], state["loginLockedUntil"]) == (3, None)
    assert (await login(service, token))["deviceState"]["loginLockedUntil"] is None
    assert (await service.reset_login_lock(device_id=device["id"]))["cleared"] is False
    assert (await fresh(service, token))["state_version"] == 3
    with pytest.raises(NotFoundError):
        await service.reset_login_lock(device_id="missing")


async def test_login_lock_running_out_does_not_change_the_state_version(
        service: TabletService, clock: Any) -> None:
    profile = await make_profile(service)
    device, token = await pair(service, profile["id"])
    await wrong_codes(service, token, 5)
    clock.advance(minutes=6)
    assert (await sync(service, token, deviceStateVersion=2))["deviceState"] is None
    view = next(item for item in (await service.overview())["devices"]
                if item["id"] == device["id"])
    assert view["login_locked_until"] is None
    assert (await service.reset_login_lock(device_id=device["id"]))["cleared"] is False
    # Another change sends the state; a lock that ran out reads as none.
    await service.update_device(device_id=device["id"], changes={"announcement": "Merhaba"})
    state = (await sync(service, token, deviceStateVersion=2))["deviceState"]
    assert (state["version"], state["loginLockedUntil"]) == (3, None)


# ------------------------------------------------------------------ silme


async def test_deleting_a_paired_tablet_revokes_it_and_keeps_history(
        service: TabletService, clock: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "_service", service)
    profile = await make_profile(service)
    device, token = await pair(service, profile["id"])
    await service.replace_inventory(device=await fresh(service, token), apps=[
        {"packageName": VIDEO["packageName"], "appLabel": "Video"}])
    logged = await login(service, token)
    clock.advance(minutes=5)
    await send_usage(service, logged, device["id"], clock, 120)
    await service.create_command(device_id=device["id"], command_type="flush_usage",
                                 payload={}, actor="Y")
    await wrong_codes(service, token, 2)

    deleted = await service.delete_device(device_id=device["id"])
    assert deleted == {"deleted": True, "deviceId": device["id"], "revoked": True}
    for table, key in ((service.devices, "id"), (service.inventory, "device_id"),
                       (service.commands, "device_id"), (service.login_failures, "device_id")):
        assert await service.store.fetch_one(
            f"SELECT 1 AS found FROM {table} WHERE {key}=?", (device["id"],)) is None

    auth = f"Bearer {token}"
    for call in (
        lambda: tablet_routes.sync(device["id"], tablet_routes.SyncBody(), NO_REQUEST,
                                   authorization=auth),
        lambda: tablet_routes.unpair(device["id"], authorization=auth),
        lambda: tablet_routes.inventory(device["id"], tablet_routes.InventoryBody(),
                                        authorization=auth),
        lambda: tablet_routes.profiles(authorization=auth),
        lambda: tablet_routes.login(tablet_routes.LoginBody(deviceId=device["id"],
                                                            studentCode="123456"),
                                    authorization=auth),
    ):
        with pytest.raises(HTTPException) as dead:
            await call()
        expect_code(dead, "DEVICE_AUTH_INVALID", 401)
    assert await service.session_for_usage_batch_token(logged["sessionToken"]) is None

    history = await service.list_sessions(student_id="opaque-A")
    assert (history[0]["deviceName"], history[0]["deviceDeleted"]) == ("Silinmiş tablet", True)
    assert (history[0]["state"], history[0]["endReason"]) == ("ended", "revoked")
    assert (await service.student_usage("opaque-A", TODAY))["usage"][0]["usedSeconds"] == 120
    _, other_token = await pair(service, profile["id"], name="Etüt-02")
    assert (await login(service, other_token))["student"]["id"] == "opaque-A"
    assert (await service.list_sessions(student_id="opaque-A"))[0]["deviceDeleted"] is False
    with pytest.raises(NotFoundError):
        await service.delete_device(device_id=device["id"])


async def test_bulk_lifts_login_locks_and_deletes_per_tablet(service: TabletService) -> None:
    profile = await make_profile(service)
    first, first_token = await pair(service, profile["id"])
    second, _ = await pair(service, profile["id"], name="Etüt-02")
    await wrong_codes(service, first_token, 5)
    lifted = await service.bulk(device_ids=[first["id"], second["id"]],
                                action="reset_login_lock", actor="Y")
    assert (lifted["okCount"], lifted["failedCount"]) == (2, 0)
    assert (await fresh(service, first_token))["state_version"] == 3

    removed = await service.bulk(device_ids=[first["id"], "missing"], action="delete",
                                 actor="Y")
    assert removed["results"] == [
        {"deviceId": first["id"], "ok": True, "message": ""},
        {"deviceId": "missing", "ok": False, "message": "Tablet bulunamadı."}]
    assert await service.device_by_token(first_token) is None
    assert [item["id"] for item in (await service.overview())["devices"]] == [second["id"]]
    with pytest.raises(ValueError, match="Desteklenmeyen"):
        await service.bulk(device_ids=[second["id"]], action="wipe", actor="Y")


async def test_cancelled_command_is_never_sent_and_its_late_result_is_kept(
        service: TabletService) -> None:
    profile = await make_profile(service)
    device, token = await pair(service, profile["id"])
    other, _ = await pair(service, profile["id"], name="Etüt-02")
    reboot = await service.create_command(device_id=device["id"], command_type="reboot",
                                          payload={}, actor="Y")
    message = await service.create_command(device_id=device["id"], command_type="message",
                                           payload={"text": "Topla"}, actor="Y")
    assert len((await sync(service, token))["commands"]) == 2
    await sync(service, token, commandResults=[{"id": message["id"], "ok": True}])
    flush = await service.create_command(device_id=device["id"], command_type="flush_usage",
                                         payload={}, actor="Y")

    for command in (reboot, flush):
        cancelled = await service.cancel_command(device_id=device["id"],
                                                 command_id=command["id"], actor="Ayşe")
        assert (cancelled["status"], cancelled["cancelledBy"]) == ("cancelled", "Ayşe")
        assert cancelled["alreadyCancelled"] is False and cancelled["cancelledAt"]
    assert (await sync(service, token))["commands"] == []
    listed = {item["id"]: item for item in await service.list_commands(device_id=device["id"])}
    assert listed[flush["id"]]["status"] == "cancelled"
    view = next(item for item in (await service.overview())["devices"]
                if item["id"] == device["id"])
    assert view["pending_commands"] == 0
    again = await service.cancel_command(device_id=device["id"], command_id=reboot["id"],
                                         actor="Y")
    assert again["alreadyCancelled"] is True

    # The tablet ran the reboot before it heard of the cancel; the real result stays.
    await sync(service, token, commandResults=[{"id": reboot["id"], "ok": True}])
    listed = {item["id"]: item for item in await service.list_commands(device_id=device["id"])}
    assert listed[reboot["id"]]["status"] == "done" and listed[reboot["id"]]["ok"] is True
    assert listed[reboot["id"]]["cancelledBy"] == "Ayşe"
    assert listed[message["id"]]["status"] == "done"
    assert (await sync(service, token))["commands"] == []
    for acked in (reboot, message):
        with pytest.raises(ConflictError, match="zaten uyguladı"):
            await service.cancel_command(device_id=device["id"], command_id=acked["id"],
                                         actor="Y")
    for device_id, command_id in ((other["id"], reboot["id"]), (device["id"], "missing")):
        with pytest.raises(NotFoundError):
            await service.cancel_command(device_id=device_id, command_id=command_id, actor="Y")


async def test_profile_in_use_is_moved_or_unassigned_before_delete(
        service: TabletService) -> None:
    profile = await service.save_profile(
        profile_id=None, name="Ortaokul", timezone="Europe/Istanbul", apps=[VIDEO],
        system_controls={"factory_reset": True})
    other = await make_profile(service, name="Lise")
    _, first_token = await pair(service, profile["id"], name="Etüt-01")
    pending = await service.create_device(name="Etüt-02", profile_id=profile["id"])
    revoked, _ = await pair(service, profile["id"], name="Etüt-03")
    await service.revoke_device(device_id=revoked["id"])

    with pytest.raises(ConflictError) as in_use:
        await service.delete_profile(profile_id=profile["id"])
    detail = in_use.value.detail()
    assert isinstance(detail, dict)
    assert (detail["code"], detail["devices"]) == ("PROFILE_IN_USE", ["Etüt-01", "Etüt-02"])
    for kwargs, message in (
        ({"replacement_profile_id": other["id"], "unassign": True}, "ya başka profile"),
        ({"replacement_profile_id": profile["id"]}, "silinen profile"),
        ({"replacement_profile_id": "missing"}, "Taşınacak profil"),
    ):
        with pytest.raises(ValueError, match=message):
            await service.delete_profile(profile_id=profile["id"], **kwargs)
    assert await service.profile(profile["id"]) is not None

    moved = await service.delete_profile(profile_id=profile["id"],
                                         replacement_profile_id=other["id"])
    assert moved == {"deleted": True, "profileId": profile["id"], "movedDevices": 2,
                     "unassignedDevices": 0}
    assert await service.profile(profile["id"]) is None
    for table in (service.apps, service.system_controls):
        assert await service.store.fetch_one(
            f"SELECT 1 AS found FROM {table} WHERE profile_id=?", (profile["id"],)) is None
    synced = await sync(service, first_token, profileId=profile["id"],
                        profileRevision=profile["revision"])
    assert synced["assignedProfileId"] == other["id"]
    assert synced["profile"]["id"] == other["id"]
    stored = await service.store.fetch_one(
        f"SELECT profile_id FROM {service.devices} WHERE id=?", (pending["id"],))
    assert stored == {"profile_id": other["id"]}

    left = await service.delete_profile(profile_id=other["id"], unassign=True)
    assert (left["movedDevices"], left["unassignedDevices"]) == (0, 2)
    synced = await sync(service, first_token, profileId=other["id"],
                        profileRevision=other["revision"])
    assert (synced["assignedProfileId"], synced["profile"]) == (None, None)
    with pytest.raises(TabletError) as no_profile:
        await login(service, first_token)
    assert no_profile.value.code == "NO_PROFILE"
    with pytest.raises(NotFoundError):
        await service.delete_profile(profile_id=other["id"], unassign=True)


async def test_student_history_delete_waits_for_logout_and_keeps_other_students(
        service: TabletService, store: Any, clock: Any) -> None:
    profile = await make_profile(service)
    device, token = await pair(service, profile["id"])
    other_device, other_token = await pair(service, profile["id"], name="Etüt-02")
    logged = await login(service, token)
    clock.advance(minutes=5)
    await send_usage(service, logged, device["id"], clock, 120)
    await service.grant_time(student_id="opaque-A", package_name=VIDEO["packageName"],
                             local_date=TODAY, extra_seconds=600, reason="", actor="Y")
    others = await login(service, other_token, code="654321")
    clock.advance(minutes=2)
    await send_usage(service, others, other_device["id"], clock, 60)

    with pytest.raises(ConflictError, match="açık oturumu"):
        await service.delete_student_history(student_id="opaque-A")
    await service.logout(session=await service.session_for_logout_token(logged["sessionToken"]))
    await service.block_student(student_id="opaque-A", until=clock() + timedelta(days=1),
                                reason="", actor="Y")
    removed = await service.delete_student_history(student_id="opaque-A")
    assert removed == {"studentId": "opaque-A", "sessions": 1, "usageBatches": 1,
                       "dailyUsage": 1, "grants": 1, "blocks": 1}
    assert await service.list_sessions(student_id="opaque-A") == []
    view = await service.student_usage("opaque-A", TODAY)
    assert (view["usage"], view["grants"], view["blockedUntil"]) == ([], [], None)
    items = await store.fetch_one(
        f"SELECT COUNT(*) AS total FROM {service.batch_items}")
    assert items == {"total": 1}
    assert len(await service.list_sessions(student_id="opaque-B")) == 1
    assert (await service.student_usage("opaque-B", TODAY))["usage"][0]["usedSeconds"] == 60
    # The lifted restriction went with the data: the student may log in again.
    await login(service, token)
    # A session that went quiet does not hold the delete back.
    clock.advance(minutes=31)
    assert (await service.delete_student_history(student_id="opaque-A"))["sessions"] == 1
    assert (await service.delete_student_history(student_id="nobody"))["sessions"] == 0


# ----------------------------------------------------------------- saklama


async def test_old_history_is_purged_once_a_day_from_overview_not_sync(
        service: TabletService, store: Any, clock: Any) -> None:
    assert service.history_retention_days == 180
    profile = await make_profile(service)
    device, token = await pair(service, profile["id"])
    old = await login(service, token)
    clock.advance(minutes=5)
    await send_usage(service, old, device["id"], clock, 120)
    await service.grant_time(student_id="opaque-A", package_name=VIDEO["packageName"],
                             local_date=TODAY, extra_seconds=600, reason="", actor="Y")
    await service.block_student(student_id="opaque-B", until=clock() + timedelta(hours=1),
                                reason="", actor="Y")
    await service.create_command(device_id=device["id"], command_type="flush_usage",
                                 payload={}, actor="Y")
    await service.logout(session=await service.session_for_logout_token(old["sessionToken"]))

    clock.advance(days=181)
    today = clock().astimezone(ZoneInfo("Europe/Istanbul")).date().isoformat()
    recent = await login(service, token)
    clock.advance(minutes=5)
    await send_usage(service, recent, device["id"], clock, 60, local_date=today)
    await sync(service, token, sessionId=recent["sessionId"])

    async def count(table: str) -> int:
        row = await store.fetch_one(f"SELECT COUNT(*) AS total FROM {table}")
        return int((row or {}).get("total") or 0)

    tables = (service.sessions, service.batches, service.batch_items, service.usage,
              service.grants, service.blocks, service.commands)
    # Neither login, usage nor sync purges.
    assert [await count(table) for table in tables] == [2, 2, 2, 2, 1, 1, 1]
    await service.overview()
    assert [await count(table) for table in tables] == [1, 1, 1, 1, 0, 0, 0]
    assert (await service.list_sessions())[0]["id"] == recent["sessionId"]
    assert await service.purge_history() is False
    assert await service.purge_history(force=True) is True

    short = TabletService(store=store, canteen=None, config={"history_retention_days": 1},
                          clock=clock)
    assert short.history_retention_days == 7


# ------------------------------------------------------------ hata kodları


async def test_tablet_error_codes(service: TabletService, canteen: Any, clock: Any,
                                  monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "_service", service)
    profile = await make_profile(service)
    device, token = await pair(service, profile["id"])
    other, other_token = await pair(service, profile["id"], name="Etüt-02")
    bare, bare_token = await pair(service, None, name="Etüt-03")
    auth = f"Bearer {token}"

    with pytest.raises(HTTPException) as unknown:
        await tablet_routes.sync(device["id"], tablet_routes.SyncBody(), NO_REQUEST,
                                 authorization="Bearer yanlis")
    expect_code(unknown, "DEVICE_AUTH_INVALID", 401)
    with pytest.raises(HTTPException) as missing:
        await tablet_routes.sync(device["id"], tablet_routes.SyncBody(), NO_REQUEST, authorization="")
    expect_code(missing, "DEVICE_AUTH_INVALID", 401)
    with pytest.raises(HTTPException) as foreign:
        await tablet_routes.sync(other["id"], tablet_routes.SyncBody(), NO_REQUEST,
                                 authorization=auth)
    expect_code(foreign, "FORBIDDEN_DEVICE", 403)
    with pytest.raises(HTTPException) as no_profile:
        await tablet_routes.login(tablet_routes.LoginBody(deviceId=bare["id"],
                                                          studentCode="123456"),
                                  authorization=f"Bearer {bare_token}")
    expect_code(no_profile, "NO_PROFILE", 409)

    wrong = tablet_routes.LoginBody(deviceId=device["id"], studentCode="000000")
    for _ in range(4):
        with pytest.raises(HTTPException) as invalid:
            await tablet_routes.login(wrong, authorization=auth)
        expect_code(invalid, "INVALID_STUDENT_CODE", 401)
    for _ in range(2):
        with pytest.raises(HTTPException) as limited:
            await tablet_routes.login(wrong, authorization=auth)
        detail = expect_code(limited, "LOGIN_RATE_LIMITED", 429)
        assert detail["retryAfterSeconds"] == 300
        assert limited.value.headers == {"Retry-After": "300"}
    clock.advance(minutes=6)

    canteen.down = True
    right = tablet_routes.LoginBody(deviceId=device["id"], studentCode="123456")
    with pytest.raises(HTTPException) as down:
        await tablet_routes.login(right, authorization=auth)
    expect_code(down, "CANTEEN_UNAVAILABLE", 503)
    canteen.down = False

    first = await tablet_routes.login(right, authorization=auth)
    with pytest.raises(HTTPException) as logout_foreign:
        await tablet_routes.logout(tablet_routes.LogoutBody(deviceId=other["id"],
                                                            sessionId=first["sessionId"]),
                                   authorization=f"Bearer {first['sessionToken']}")
    expect_code(logout_foreign, "FORBIDDEN_DEVICE", 403)

    async def ended(session_id: str) -> tuple[str, str | None]:
        row = await service.store.fetch_one(
            f"SELECT state, end_reason FROM {service.sessions} WHERE id=?", (session_id,))
        return row["state"], row["end_reason"]

    # Yeni giriş önceki oturumu kapatır: bağlantısı kopan ya da açık kalan tablet
    # girişi engellemez; eski tablet bir sonraki bağlantıda `replaced` alır.
    moved = await tablet_routes.login(tablet_routes.LoginBody(deviceId=other["id"],
                                                              studentCode="123456"),
                                      authorization=f"Bearer {other_token}")
    assert moved["student"]["id"] == "opaque-A"
    assert await ended(first["sessionId"]) == ("expired", "replaced")
    # Aynı tablette kapanmamış oturum da yeni girişle kapanır.
    again = await tablet_routes.login(tablet_routes.LoginBody(deviceId=other["id"],
                                                              studentCode="654321"),
                                      authorization=f"Bearer {other_token}")
    assert again["sessionId"] != moved["sessionId"]
    assert await ended(moved["sessionId"]) == ("expired", "replaced")
    # Geç gelen çıkış kapanmış oturum için de kabul edilir.
    late = await tablet_routes.logout(tablet_routes.LogoutBody(deviceId=device["id"],
                                                               sessionId=first["sessionId"]),
                                      authorization=f"Bearer {first['sessionToken']}")
    assert late["ok"] is True


def test_sync_body_accepts_empty_and_rejects_bad_values() -> None:
    assert tablet_routes.SyncBody().model_dump()["commandResults"] == []
    assert tablet_routes.SyncBody(futureField=1).sessionId is None  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        tablet_routes.SyncBody(batteryPercent=101)
    with pytest.raises(ValidationError):
        tablet_routes.SyncBody(commandResults=[{"ok": True}])


# ------------------------------------------------------ izin ve PIN kapıları

VIEW, MANAGE, GRANT = "bbd_tablet.view", "bbd_tablet.manage", "bbd_tablet.grant"
EXPECTED_PERMISSIONS = {
    ("GET", "/overview"): {VIEW},
    ("GET", "/students"): {VIEW},
    ("GET", "/students/{student_id}/usage"): {VIEW},
    ("GET", "/students/{student_id}/blocks"): {VIEW},
    ("GET", "/reports/daily"): {VIEW},
    ("GET", "/sessions"): {VIEW},
    ("GET", "/devices/{device_id}/apps"): {VIEW},
    ("GET", "/devices/{device_id}/commands"): {VIEW},
    ("GET", "/devices/{device_id}/diagnostics"): {VIEW},
    ("GET", "/devices/{device_id}/diagnostics/{diagnostics_id}"): {MANAGE},
    ("POST", "/profiles"): {MANAGE},
    ("PUT", "/profiles/{profile_id}"): {MANAGE},
    ("POST", "/profiles/{profile_id}/duplicate"): {MANAGE},
    ("DELETE", "/profiles/{profile_id}"): {MANAGE},
    ("POST", "/devices"): {MANAGE},
    ("POST", "/devices/bulk"): {MANAGE},
    ("PATCH", "/devices/{device_id}"): {MANAGE},
    ("POST", "/devices/{device_id}/assign-profile"): {MANAGE},
    ("POST", "/devices/{device_id}/enrollment-code"): {MANAGE},
    ("POST", "/devices/{device_id}/revoke"): {MANAGE},
    ("DELETE", "/devices/{device_id}"): {MANAGE},
    ("POST", "/devices/{device_id}/login-lock/reset"): {GRANT},
    ("POST", "/devices/{device_id}/commands"): {MANAGE},
    ("DELETE", "/devices/{device_id}/commands/{command_id}"): {MANAGE},
    ("DELETE", "/students/{student_id}/history"): {MANAGE},
    ("POST", "/sessions/{session_id}/end"): {MANAGE},
    ("POST", "/students/{student_id}/apps/{package_name}/grants"): {GRANT},
    ("POST", "/students/{student_id}/grants/{grant_id}/revoke"): {GRANT},
    ("POST", "/students/{student_id}/block"): {GRANT},
    ("POST", "/students/{student_id}/unblock"): {GRANT},
    # tablet-sinir: öğrenciye özel süreleri görme / genele döndürme.
    ("GET", "/students/{student_id}/app-limits"): {VIEW},
    ("DELETE", "/students/{student_id}/app-limits/{package_name}"): {GRANT},
    ("GET", "/apps"): {VIEW},
    ("GET", "/apps/{package_name}/devices"): {VIEW},
    ("POST", "/apps/uploads"): {MANAGE},
    ("GET", "/apps/uploads/{upload_id}"): {MANAGE},
    ("PUT", "/apps/uploads/{upload_id}/chunk"): {MANAGE},
    ("POST", "/apps/uploads/{upload_id}/finish"): {MANAGE},
    ("POST", "/apps/releases/{release_id}/publish"): {MANAGE},
    ("DELETE", "/apps/releases/{release_id}"): {MANAGE},
    ("POST", "/apps/{package_name}/devices/{device_id}/prioritize"): {MANAGE},
    ("DELETE", "/apps/{package_name}"): {MANAGE},
}


def _declared(route: Any) -> set[str]:
    found: set[str] = set()
    for param in route.dependant.dependencies:
        for cell in getattr(param.call, "__closure__", None) or ():
            value = cell.cell_contents
            if isinstance(value, tuple) and all(isinstance(item, str) for item in value):
                found.update(value)
    return found


def test_every_management_route_declares_its_permission() -> None:
    found = {(method, route.path): route for route in routes.router.routes
             for method in route.methods}
    assert set(found) == set(EXPECTED_PERMISSIONS)
    for key, route in found.items():
        assert _declared(route) == EXPECTED_PERMISSIONS[key], key


def test_every_tablet_route_except_enroll_takes_a_bearer() -> None:
    for route in tablet_routes.router.routes:
        parameters = inspect.signature(route.endpoint).parameters
        assert ("authorization" in parameters) == (route.path != "/devices/enroll"), route.path


def test_destructive_routes_require_pin_in_body() -> None:
    with pytest.raises(ValidationError):
        routes.PinBody()
    for endpoint in (routes.revoke_device, routes.delete_device, routes.delete_profile,
                     routes.delete_student_history, routes.bulk_devices, routes.remove_app):
        assert "confirm_pin(" in inspect.getsource(endpoint)
        assert "pin" not in inspect.signature(endpoint).parameters
    for endpoint in (routes.create_device, routes.enrollment_code, routes.update_device):
        assert "confirm_pin(" not in inspect.getsource(endpoint)


class FakeIdentity:
    def __init__(self) -> None:
        self.store = self
        self.audits: list[tuple[Any, ...]] = []

    @staticmethod
    def secret_lookup(pin: str) -> str:
        return f"h:{pin}"

    async def fetch_one(self, sql: str, params: tuple[Any, ...]) -> dict[str, str]:
        return {"secret_lookup": "h:482913"}

    async def audit(self, *args: Any, **kwargs: Any) -> None:
        self.audits.append(args)


async def test_revoke_with_wrong_pin_changes_nothing(
        service: TabletService, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "_service", service)
    profile = await make_profile(service)
    device, token = await pair(service, profile["id"])
    identity = FakeIdentity()
    request: Any = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(identity=identity)))
    user: Any = SimpleNamespace(id="u1", full_name="Yönetici")

    with pytest.raises(HTTPException) as denied:
        await routes.revoke_device(device["id"], routes.PinBody(pin="000000"), request, user)
    assert denied.value.status_code == 403
    assert identity.audits[0][1] == "bbd_tablet.device.revoke"
    assert await service.device_for_token(token) is not None

    revoked = await routes.revoke_device(device["id"], routes.PinBody(pin="482913"),
                                         request, user)
    assert revoked["revoked"] is True
    with pytest.raises(HTTPException) as missing:
        await routes.delete_profile("missing", routes.ProfileDeleteBody(pin="482913"),
                                    request, user)
    assert missing.value.status_code == 404


async def test_bbd_akademi_is_in_every_profile_and_never_blocked(
        service: TabletService) -> None:
    # Profilde yoksa görünümde (panel, tablet, geçit) sınırsız olarak bulunur.
    plain = await make_profile(service, apps=[VIDEO])
    akademi = next(app for app in plain["apps"] if app["packageName"] == AKADEMI)
    assert (akademi["allowed"], akademi["unlimited"], akademi["dailyLimitSeconds"]) == (True, True, 0)
    # Süreli kaydedilir ve tablete süreli gider (74-tablet).
    timed = await make_profile(service, apps=[
        {"packageName": AKADEMI, "appName": "BBD Akademi", "allowed": True, "unlimited": False,
         "dailyLimitSeconds": 2700}, VIDEO])
    rows = [app for app in timed["apps"] if app["packageName"] == AKADEMI]
    assert [(app["allowed"], app["unlimited"], app["dailyLimitSeconds"], app["sortOrder"])
            for app in rows] == [(True, False, 2700, 0)]
    _, token = await pair(service, timed["id"])
    logged = await login(service, token)
    sent = next(app for app in logged["profile"]["apps"] if app["packageName"] == AKADEMI)
    assert (sent["allowed"], sent["unlimited"], sent["dailyLimitSeconds"]) == (True, False, 2700)
    # Süre de diğer uygulamalar gibi 1 dakika – 24 saat.
    with pytest.raises(ValueError, match="1 dakika ile 24 saat"):
        await make_profile(service, apps=[{"packageName": AKADEMI, "allowed": True,
                                           "dailyLimitSeconds": 30}])
    # Yasak reddedilir; panel 400 alır, profil değişmez.
    with pytest.raises(ValueError, match="yasaklanamaz"):
        await make_profile(service, apps=[VIDEO, {"packageName": AKADEMI, "allowed": False}])
    with pytest.raises(HTTPException) as refused:
        await routes.run(service.save_profile(
            profile_id=timed["id"], name="Ortaokul", timezone="Europe/Istanbul",
            apps=[{"packageName": AKADEMI, "allowed": False}]))
    assert refused.value.status_code == 400
    assert (await service.profile(timed["id"]) or {})["revision"] == timed["revision"]
    # Sınırsıza geri alınabilir; diğer uygulamalar sınırsız eklenebilmeye devam eder.
    back = await service.save_profile(profile_id=timed["id"], name="Ortaokul",
                                      timezone="Europe/Istanbul",
                                      apps=[{"packageName": AKADEMI, "allowed": True,
                                             "unlimited": True}, BOOKS])
    assert [(app["packageName"], app["unlimited"]) for app in back["apps"]] == [
        (AKADEMI, True), (BOOKS["packageName"], True)]


def test_old_blocked_bbd_akademi_reads_as_unlimited() -> None:
    """Kuraldan önce yasak saklanmış satır görünümde sınırsızdır; eski tabletin ilk
    profil ekranından gelen yasak Akademi reddedilmez, sınırsıza çevrilir."""
    stored = [{"packageName": AKADEMI, "appName": "Akademi", "allowed": False,
               "unlimited": False, "dailyLimitSeconds": 0, "sortOrder": 4},
              {**VIDEO, "sortOrder": 5}]
    view = with_always_allowed(stored)
    assert [(app["packageName"], app["allowed"], app["unlimited"]) for app in view] == [
        (AKADEMI, True, True), (VIDEO["packageName"], True, False)]
    assert allow_always_allowed([{"packageName": AKADEMI, "allowed": False}, GAME]) == [
        {"packageName": AKADEMI, "allowed": True, "unlimited": True, "dailyLimitSeconds": 0},
        GAME]


async def test_bbd_akademi_does_not_count_towards_the_daily_total(
        service: TabletService, clock: Any) -> None:
    profile = await service.save_profile(
        profile_id=None, name="Ortaokul", timezone="Europe/Istanbul",
        apps=[VIDEO, {"packageName": AKADEMI, "appName": "BBD Akademi", "allowed": True,
                      "unlimited": False, "dailyLimitSeconds": 3600}],
        daily_total_limit_seconds=1200)
    device, token = await pair(service, profile["id"])
    logged = await login(service, token)
    session = await service.session_for_usage_batch_token(logged["sessionToken"])
    assert session is not None
    clock.advance(minutes=30)
    await service.usage_batch(session=session, data={
        "batchId": str(uuid4()), "deviceId": device["id"], "sessionId": session["id"],
        "studentId": session["student_id"], "startedAt": session["login_at"],
        "endedAt": clock().isoformat(),
        "usages": [{"packageName": VIDEO["packageName"], "localDate": TODAY, "usedSeconds": 300},
                   {"packageName": AKADEMI, "localDate": TODAY, "usedSeconds": 900}],
    })
    usage = await service.student_usage(logged["student"]["id"], TODAY)
    assert usage["dailyTotal"] == {"limitSeconds": 1200, "usedSeconds": 300,
                                   "remainingSeconds": 900}
    akademi = next(item for item in usage["usage"] if item["packageName"] == AKADEMI)
    assert (akademi["limitSeconds"], akademi["remainingSeconds"]) == (3600, 2700)
