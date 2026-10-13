"""`bbd_tablet.read` / `bbd_tablet.control` — mobil geçit sözleşmesi §4.

Gerçek SQLite depo + modül göçleri (conftest). Saat Çarşamba 2026-10-14
10:00 Europe/Istanbul'dan başlar.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from modules.bbd_tablet.backend.capabilities import (
    TabletCapabilityError,
    TabletControl,
    TabletRead,
)
from modules.bbd_tablet.backend.service import TabletService

VIDEO = {"packageName": "com.example.video", "appName": "Video", "allowed": True,
         "unlimited": False, "dailyLimitSeconds": 1800}
TODAY = "2026-10-14"


class Log:
    def __init__(self) -> None:
        self.records: list[tuple[str, dict[str, Any]]] = []

    def info(self, message: str, **fields: Any) -> None:
        self.records.append((message, fields))

    def warning(self, message: str, **fields: Any) -> None:
        self.records.append((message, fields))


@pytest.fixture
def service(store: Any, canteen: Any, clock: Any) -> TabletService:
    return TabletService(store=store, canteen=canteen, config={}, clock=clock)


async def pair(service: TabletService, profile_id: str | None, name: str) -> tuple[str, str]:
    pending = await service.create_device(name=name, profile_id=profile_id)
    enrolled = await service.enroll({"enrollmentCode": pending["enrollmentCode"],
                                     "deviceUuid": str(uuid4()), "deviceName": name})
    device = await service.device_for_token(enrolled["deviceToken"])
    assert device is not None
    await service.sync(device=device, data={"batteryPercent": 64, "charging": False})
    return str(device["id"]), str(enrolled["deviceToken"])


async def login(service: TabletService, token: str, code: str = "123456") -> dict[str, Any]:
    device = await service.device_by_token(token)
    assert device is not None
    return await service.login(device=device, student_code=code)


async def test_pano_durumlari_ve_ozet(service: TabletService, clock: Any) -> None:
    profile = await service.save_profile(profile_id=None, name="Ortaokul",
                                         timezone="Europe/Istanbul", apps=[VIDEO])
    evening = await service.save_profile(
        profile_id=None, name="Akşam", timezone="Europe/Istanbul", apps=[VIDEO],
        usage_windows=[{"days": [1, 2, 3, 4, 5], "start": "18:00", "end": "20:00"}],
    )
    free_id, _ = await pair(service, profile["id"], "Tablet 01")
    busy_id, busy_token = await pair(service, profile["id"], "Tablet 02")
    locked_id, _ = await pair(service, profile["id"], "Tablet 03")
    hours_id, _ = await pair(service, evening["id"], "Tablet 04")
    bare_id, _ = await pair(service, None, "Tablet 05")
    pending = await service.create_device(name="Tablet 06", profile_id=profile["id"])

    logged = await login(service, busy_token)
    await service.update_device(device_id=locked_id, changes={"locked": True})

    board = await TabletRead(service).board()
    states = {item["id"]: item for item in board["devices"]}

    assert states[free_id]["state"] == "free"
    assert states[free_id]["battery"] == 64 and states[free_id]["charging"] is False
    assert states[free_id]["lastSeenAt"].endswith("+03:00")
    assert states[busy_id]["state"] == "in_use"
    assert states[busy_id]["activeSession"] == {
        "id": logged["sessionId"], "studentId": "opaque-A", "studentName": "Ahmet",
        "loginAt": "2026-10-14T10:00:00+03:00",
    }
    assert (states[locked_id]["state"], states[locked_id]["reason"]) == ("locked", "device_locked")
    assert (states[hours_id]["state"], states[hours_id]["reason"]) == ("locked", "outside_hours")
    assert (states[bare_id]["state"], states[bare_id]["reason"]) == ("locked", "no_profile")
    assert states[pending["id"]]["state"] == "pending"
    assert board["summary"] == {"free": 1, "inUse": 1, "offline": 0, "locked": 3}

    clock.advance(hours=2)
    board = await TabletRead(service).board()
    assert {item["state"] for item in board["devices"] if item["id"] != pending["id"]} \
        == {"offline"}


async def test_ogrenci_kullanimi(service: TabletService, clock: Any) -> None:
    profile = await service.save_profile(profile_id=None, name="Ortaokul",
                                         timezone="Europe/Istanbul", apps=[VIDEO])
    device_id, token = await pair(service, profile["id"], "Tablet 07")
    logged = await login(service, token)
    session = await service.session_for_usage_batch_token(logged["sessionToken"])
    assert session is not None
    clock.advance(minutes=30)
    await service.usage_batch(session=session, data={
        "batchId": str(uuid4()), "deviceId": device_id, "sessionId": session["id"],
        "studentId": "opaque-A", "startedAt": session["login_at"],
        "endedAt": clock().isoformat(),
        "usages": [{"packageName": VIDEO["packageName"], "localDate": TODAY,
                    "usedSeconds": 900},
                   {"packageName": "com.unknown.app", "localDate": TODAY,
                    "usedSeconds": 60}],
    })

    usage = await TabletRead(service).student_usage("opaque-A", "2026-10-13", TODAY)
    assert [day["date"] for day in usage["days"]] == ["2026-10-13", TODAY]
    assert usage["days"][0] == {"date": "2026-10-13", "totalSeconds": 0, "apps": []}
    assert usage["days"][1] == {"date": TODAY, "totalSeconds": 960, "apps": [
        {"package": "com.example.video", "label": "Video", "seconds": 900},
        {"package": "com.unknown.app", "label": None, "seconds": 60},
    ]}
    assert usage["activeSession"] == {"id": logged["sessionId"], "deviceName": "Tablet 07",
                                      "loginAt": "2026-10-14T10:00:00+03:00"}
    assert usage["sessions"] == [{"id": logged["sessionId"], "deviceName": "Tablet 07",
                                  "loginAt": "2026-10-14T10:00:00+03:00",
                                  "endedAt": None, "endReason": None}]
    assert usage["blockedUntil"] is None

    with pytest.raises(TabletCapabilityError) as error:
        await TabletRead(service).student_usage("opaque-A", "2026-08-01", TODAY)
    assert error.value.reason == "validation_error"
    with pytest.raises(TabletCapabilityError):
        await TabletRead(service).student_usage("opaque-A", TODAY, "2026-10-13")


async def test_kontrol_ek_sure_kisit_oturum(service: TabletService, clock: Any) -> None:
    log = Log()
    control = TabletControl(service, log)
    profile = await service.save_profile(profile_id=None, name="Ortaokul",
                                         timezone="Europe/Istanbul", apps=[VIDEO])
    _, token = await pair(service, profile["id"], "Tablet 08")
    logged = await login(service, token)

    grant_id = await control.grant("opaque-A", VIDEO["packageName"], 1800,
                                   "Ödev için", "akademi:staff:42:Ayşe")
    usage = await service.student_usage("opaque-A", TODAY)
    assert usage["grants"][0]["id"] == grant_id
    assert usage["grants"][0]["createdBy"] == "akademi:staff:42:Ayşe"

    for bad in (
        control.grant("opaque-A", VIDEO["packageName"], 0, "Ödev için", "x"),
        control.grant("opaque-A", VIDEO["packageName"], 60, "x", "x"),
        control.grant("opaque-A", "com.not.allowed", 60, "Ödev için", "x"),
    ):
        with pytest.raises(TabletCapabilityError) as error:
            await bad
        assert error.value.reason == "validation_error"
    with pytest.raises(TabletCapabilityError) as missing:
        await control.grant("opaque-YOK", VIDEO["packageName"], 60, "Ödev için", "x")
    assert missing.value.reason == "student_not_found"

    until = clock() + timedelta(days=1)
    blocked = await control.block("opaque-A", until, "veli: ders saati", "akademi:veli:7")
    assert blocked == "2026-10-15T10:00:00+03:00"
    # Kısıt açık oturumu kapattı.
    assert await control.end_session(logged["sessionId"], "Panelden", "x") is False
    with pytest.raises(TabletCapabilityError) as nosession:
        await control.end_session("yok", "Panelden", "x")
    assert nosession.value.reason == "session_not_found"

    open_ended = await control.block("opaque-A", None, "veli: süresiz", "akademi:veli:7")
    assert open_ended is not None and open_ended.startswith("2027-10-14")
    # Gerekçe süzgeçli kaldırma: yalnız o gerekçeli kısıt kalkar (KM yeniden
    # etkinleştirmesi öğretmenin / velinin kısıtına dokunmaz).
    await control.block("opaque-A", None, "Öğrenci  pasifleştirildi (KM)", "km")
    await control.unblock("opaque-A", "yeniden etkin", "km",
                          only_reason="Öğrenci pasifleştirildi (KM)")
    still = (await TabletRead(service).student_usage("opaque-A", TODAY, TODAY))["blockedUntil"]
    assert still is not None and still.startswith("2027-10-14")
    blocks = await service.list_blocks(student_id="opaque-A")
    lifted = [row for row in blocks if row.get("reason") == "Öğrenci pasifleştirildi (KM)"]
    assert lifted and all(row.get("liftedAt") or row.get("lifted_at") for row in lifted)
    await control.unblock("opaque-A", "veli kaldırdı", "akademi:veli:7")
    assert (await TabletRead(service).student_usage("opaque-A", TODAY, TODAY))[
        "blockedUntil"] is None
    with pytest.raises(TabletCapabilityError) as past:
        await control.block("opaque-A", datetime(2020, 1, 1).isoformat(), "geçmiş", "x")
    assert past.value.reason == "validation_error"


async def test_kontrol_oturum_kapatir_ve_kantin_dusukse(service: TabletService,
                                                       canteen: Any) -> None:
    control = TabletControl(service, Log())
    profile = await service.save_profile(profile_id=None, name="Ortaokul",
                                         timezone="Europe/Istanbul", apps=[VIDEO])
    _, token = await pair(service, profile["id"], "Tablet 09")
    logged = await login(service, token)
    assert await control.session_student(logged["sessionId"]) == "opaque-A"
    assert await control.session_student("yok") is None
    assert await control.end_session(logged["sessionId"], "Panelden kapatıldı", "x") is True
    assert await control.end_session(logged["sessionId"], "Panelden kapatıldı", "x") is False

    canteen.down = True
    service._students_cache = None
    with pytest.raises(TabletCapabilityError) as error:
        await control.grant("opaque-A", VIDEO["packageName"], 60, "Ödev için", "x")
    assert error.value.reason == "canteen_unavailable"


async def test_tum_tablete_ek_sure_sure_sinirli_her_uygulamaya(service: TabletService) -> None:
    control = TabletControl(service, Log())
    games = {"packageName": "com.example.games", "appName": "Oyun", "allowed": True,
             "unlimited": False, "dailyLimitSeconds": 600}
    books = {"packageName": "com.example.books", "appName": "Kitap", "allowed": True,
             "unlimited": True, "dailyLimitSeconds": 0}
    banned = {"packageName": "com.example.banned", "appName": "Yasak", "allowed": False,
              "unlimited": False, "dailyLimitSeconds": 0}
    profile = await service.save_profile(profile_id=None, name="Ortaokul",
                                         timezone="Europe/Istanbul",
                                         apps=[VIDEO, games, books, banned])
    other = await service.save_profile(
        profile_id=None, name="Lise", timezone="Europe/Istanbul",
        apps=[{**VIDEO, "packageName": "com.example.other"}])

    # Hiç oturum yokken bütün profillerin süre sınırlı uygulamaları kapsanır.
    assert await service.device_grant_packages("opaque-A") == [
        "com.example.games", "com.example.other", "com.example.video"]

    _, token = await pair(service, profile["id"], "Tablet 10")
    await login(service, token)
    assert other["id"] != profile["id"]

    grant_id = await control.grant("opaque-A", None, 900, "Sınav haftası", "akademi:staff:1")
    usage = await service.student_usage("opaque-A", TODAY)
    granted = {item["packageName"]: item for item in usage["grants"]}
    # Oturumun profili: sınırsız ve yasaklı uygulama ek süre almaz.
    assert set(granted) == {"com.example.games", "com.example.video"}
    assert {item["extraSeconds"] for item in granted.values()} == {900}
    assert grant_id == granted["com.example.games"]["id"]
    extra = {item["packageName"]: item["extraSeconds"] for item in usage["usage"]}
    assert extra["com.example.video"] == 900 and extra["com.example.books"] == 0


async def test_tum_tablete_ek_sure_sinirli_uygulama_yoksa_reddedilir(
        service: TabletService) -> None:
    control = TabletControl(service, Log())
    await service.save_profile(profile_id=None, name="Serbest", timezone="Europe/Istanbul",
                               apps=[{**VIDEO, "unlimited": True}])
    with pytest.raises(TabletCapabilityError) as error:
        await control.grant("opaque-A", None, 900, "Sınav haftası", "x")
    assert error.value.reason == "validation_error"


async def test_kurum_geneli_gunluk_kullanim(service: TabletService, store: Any,
                                            clock: Any) -> None:
    """`daily_usage`: o gün kullanımı olan ya da kısıtı süren öğrenciler, iki sorgu."""
    usage = store.table("daily_usage")
    for student, package, day, seconds in (
        ("opaque-A", "com.example.video", TODAY, 900),
        ("opaque-A", "com.example.books", TODAY, 300),
        ("opaque-B", "com.example.video", TODAY, 0),          # sıfır satır sayılmaz
        ("opaque-C", "com.example.video", "2026-10-13", 600),  # başka gün
    ):
        await store.execute(
            f"INSERT INTO {usage} (student_id, package_name, local_date, used_seconds) "
            "VALUES (?, ?, ?, ?)", (student, package, day, seconds))
    control = TabletControl(service, Log())
    await control.block("opaque-B", clock() + timedelta(days=2), "veli: hafta sonu", "x")
    await control.block("opaque-A", clock() + timedelta(hours=1), "ders saati", "x")
    await control.unblock("opaque-A", "kaldırıldı", "x")

    calls: list[str] = []
    original = store.fetch_all

    async def counted(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        calls.append(sql)
        return await original(sql, params)

    store.fetch_all = counted
    report = await TabletRead(service).daily_usage(TODAY)
    store.fetch_all = original
    assert len(calls) == 2                                    # N+1 yok
    assert report == {"date": TODAY, "items": [
        {"studentId": "opaque-A", "totalSeconds": 1200, "blockedUntil": None},
        {"studentId": "opaque-B", "totalSeconds": 0,
         "blockedUntil": "2026-10-16T10:00:00+03:00"},
    ]}
    earlier = await TabletRead(service).daily_usage("2026-10-13")
    assert [item["studentId"] for item in earlier["items"]] == ["opaque-B", "opaque-C"]

    for bad in ("2026-10-15", "2026-08-13", "14.10.2026"):
        with pytest.raises(TabletCapabilityError) as error:
            await TabletRead(service).daily_usage(bad)
        assert error.value.reason == "validation_error"
    assert (await TabletRead(service).daily_usage("2026-08-14"))["items"] == [
        {"studentId": "opaque-B", "totalSeconds": 0,
         "blockedUntil": "2026-10-16T10:00:00+03:00"}]
