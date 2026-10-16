"""Akademi "Tablet sınırları" yetenekleri (iş tablet-sinir).

`read.student_limits`, `control.update_profile_limits`, `control.revoke_grant`:
öğrencinin bugünkü sınırları, profil kuralının revizyon denetimli düzenlenmesi ve
ek sürenin geri alınması. Değişiklik tablete yeni profil revizyonu / `grantsVersion`
olarak sonraki sync'te iner.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from modules.bbd_tablet.backend.capabilities import (
    TabletCapabilityError,
    TabletControl,
    TabletRead,
)
from modules.bbd_tablet.backend.service import TabletService
from modules.bbd_tablet.tests.test_capabilities import TODAY, VIDEO, Log, login, pair

GAME = {"packageName": "com.example.game", "appName": "Oyun", "allowed": False,
        "unlimited": False, "dailyLimitSeconds": 0}
BOOK = {"packageName": "com.example.book", "appName": "Kitap", "allowed": True,
        "unlimited": True, "dailyLimitSeconds": 0}


@pytest.fixture
def service(store: Any, canteen: Any, clock: Any) -> TabletService:
    return TabletService(store=store, canteen=canteen, config={}, clock=clock)


async def _setup(service: TabletService, clock: Any) -> tuple[dict[str, Any], str]:
    profile = await service.save_profile(
        profile_id=None, name="Ortaokul", timezone="Europe/Istanbul", apps=[VIDEO, GAME, BOOK],
        usage_windows=[{"days": [1, 2, 3, 4, 5], "start": "08:00", "end": "18:00"}],
    )
    device_id, token = await pair(service, profile["id"], "Tablet 07")
    await pair(service, profile["id"], "Tablet 08")
    logged = await login(service, token)
    session = await service.session_for_usage_batch_token(logged["sessionToken"])
    assert session is not None
    clock.advance(minutes=20)
    await service.usage_batch(session=session, data={
        "batchId": str(uuid4()), "deviceId": device_id, "sessionId": session["id"],
        "studentId": "opaque-A", "startedAt": session["login_at"],
        "endedAt": clock().isoformat(),
        "usages": [{"packageName": VIDEO["packageName"], "localDate": TODAY, "usedSeconds": 600},
                   {"packageName": "com.other.app", "localDate": TODAY, "usedSeconds": 120}],
    })
    return profile, device_id


async def test_ogrencinin_bugunku_sinirlari(service: TabletService, clock: Any) -> None:
    profile, _ = await _setup(service, clock)
    control = TabletControl(service, Log())
    grant_id = await control.grant("opaque-A", VIDEO["packageName"], 300, "Ödev için", "Ayşe")

    limits = await TabletRead(service).student_limits("opaque-A")

    assert limits["localDate"] == TODAY
    assert limits["profile"]["id"] == profile["id"]
    assert limits["profile"]["revision"] == 1
    assert limits["profile"]["deviceCount"] == 2
    assert limits["profile"]["usageWindows"] == [
        {"days": [1, 2, 3, 4, 5], "start": "08:00", "end": "18:00"}]
    apps = {app["packageName"]: app for app in limits["apps"]}
    assert apps[VIDEO["packageName"]] == {
        "packageName": VIDEO["packageName"], "appName": "Video", "mode": "limited",
        "dailyLimitSeconds": 1800, "generalLimitSeconds": 1800, "studentLimitSeconds": None,
        "source": "general", "usedSeconds": 600, "extraSeconds": 300,
        "remainingSeconds": 1500, "inProfile": True}
    assert apps[GAME["packageName"]]["mode"] == "blocked"
    assert apps[GAME["packageName"]]["remainingSeconds"] is None
    assert apps[BOOK["packageName"]]["mode"] == "unlimited"
    assert apps["com.other.app"]["inProfile"] is False
    assert [grant["id"] for grant in limits["grants"]] == [grant_id]
    assert limits["grants"][0]["createdAt"].endswith("+03:00")
    assert limits["blockedUntil"] is None
    assert limits["sync"] == {"activeSeconds": 60, "idleSeconds": 300}


async def test_hic_giris_yapmayan_ogrencinin_profili_yok(service: TabletService) -> None:
    limits = await TabletRead(service).student_limits("opaque-B")
    assert limits["profile"] is None and limits["apps"] == [] and limits["grants"] == []


async def test_profil_sinirlari_revizyon_denetimli_duzenlenir(service: TabletService,
                                                             clock: Any) -> None:
    profile, _ = await _setup(service, clock)
    log = Log()
    control = TabletControl(service, log)

    saved = await control.update_profile_limits(
        profile["id"], 1,
        [{"packageName": VIDEO["packageName"], "mode": "limited", "dailyLimitSeconds": 2700},
         {"packageName": GAME["packageName"], "mode": "limited", "dailyLimitSeconds": 600}],
        None, "Sınav haftası", "akademi:staff:1:Ayşe",
    )
    assert saved["revision"] == 2
    current = await service.profile(profile["id"])
    assert current is not None
    rules = {app["packageName"]: app for app in current["apps"]}
    assert rules[VIDEO["packageName"]]["dailyLimitSeconds"] == 2700
    assert rules[GAME["packageName"]]["allowed"] is True
    assert rules[GAME["packageName"]]["dailyLimitSeconds"] == 600
    assert rules[BOOK["packageName"]]["unlimited"] is True  # anılmayan korunur
    assert current["usageWindows"] == [{"days": [1, 2, 3, 4, 5], "start": "08:00", "end": "18:00"}]
    assert log.records[-1][1]["reason"] == "Sınav haftası"

    # Eski revizyonla yazma reddedilir (bu arada değişti).
    with pytest.raises(TabletCapabilityError) as error:
        await control.update_profile_limits(profile["id"], 1, None, [], "Yeniden", "Ayşe")
    assert error.value.reason == "profile_conflict"

    # Yalnız saatler: boş liste = her zaman açık.
    again = await control.update_profile_limits(profile["id"], 2, None, [], "Hafta sonu", "Ayşe")
    assert again["revision"] == 3
    assert (await service.profile(profile["id"]) or {})["usageWindows"] == []


@pytest.mark.parametrize("apps,windows", [
    ([{"packageName": "com.not.in.profile", "mode": "blocked"}], None),
    ([{"packageName": VIDEO["packageName"], "mode": "limited", "dailyLimitSeconds": 30}], None),
    ([{"packageName": VIDEO["packageName"], "mode": "bilinmeyen"}], None),
    (None, [{"days": [1], "start": "18:00", "end": "08:00"}]),
    (None, None),
])
async def test_gecersiz_sinir_reddedilir(service: TabletService, clock: Any, apps: Any,
                                         windows: Any) -> None:
    profile, _ = await _setup(service, clock)
    with pytest.raises(TabletCapabilityError) as error:
        await TabletControl(service, Log()).update_profile_limits(
            profile["id"], 1, apps, windows, "Gerekçe", "Ayşe")
    assert error.value.reason == "validation_error"
    assert (await service.profile(profile["id"]) or {})["revision"] == 1


async def test_bilinmeyen_profil(service: TabletService) -> None:
    with pytest.raises(TabletCapabilityError) as error:
        await TabletControl(service, Log()).update_profile_limits(
            "yok", 1, None, [], "Gerekçe", "Ayşe")
    assert error.value.reason == "profile_not_found"


async def test_ek_sure_geri_alinir_ve_tablete_iner(service: TabletService, clock: Any) -> None:
    await _setup(service, clock)
    control = TabletControl(service, Log())
    grant_id = await control.grant("opaque-A", VIDEO["packageName"], 600, "Ödev", "Ayşe")
    before = await service.grants_version("opaque-A", TODAY)

    result = await control.revoke_grant("opaque-A", grant_id, "Yanlış verildi", "Ayşe")
    assert result == {"id": grant_id, "revoked": True, "alreadyRevoked": False}
    assert await service.grants_version("opaque-A", TODAY) != before
    assert (await TabletRead(service).student_limits("opaque-A"))["grants"] == []

    again = await control.revoke_grant("opaque-A", grant_id, "Yanlış verildi", "Ayşe")
    assert again["alreadyRevoked"] is True
    with pytest.raises(TabletCapabilityError) as error:
        await control.revoke_grant("opaque-B", grant_id, "Başkasının", "Ayşe")
    assert error.value.reason == "grant_not_found"


async def test_gerekce_istege_bagli(service: TabletService, clock: Any) -> None:
    profile, _ = await _setup(service, clock)
    log = Log()
    saved = await TabletControl(service, log).update_profile_limits(
        profile["id"], 1, [{"packageName": GAME["packageName"], "mode": "unlimited"}], None,
        None, "Ayşe")
    assert saved["revision"] == 2
    assert log.records[-1][1]["reason"] == "Akademi yöneticisi düzeltti"


async def test_genel_ayarlar_profiller(service: TabletService, clock: Any) -> None:
    profile, _ = await _setup(service, clock)
    await service.save_profile(profile_id=None, name="Lise", timezone="Europe/Istanbul",
                               apps=[VIDEO])
    profiles = {item["name"]: item for item in await TabletRead(service).profiles()}
    assert profiles["Ortaokul"]["id"] == profile["id"]
    assert profiles["Ortaokul"]["deviceCount"] == 2
    assert profiles["Lise"]["deviceCount"] == 0
    modes = {app["packageName"]: (app["mode"], app["dailyLimitSeconds"])
             for app in profiles["Ortaokul"]["apps"]}
    assert modes == {VIDEO["packageName"]: ("limited", 1800),
                     GAME["packageName"]: ("blocked", None),
                     BOOK["packageName"]: ("unlimited", None),
                     "com.veykemtu.benimdunyam": ("unlimited", None)}


async def test_ogrenciye_ozel_sure_tablete_ek_sure_olarak_iner(service: TabletService,
                                                              clock: Any) -> None:
    _, device_id = await _setup(service, clock)
    control = TabletControl(service, Log())
    device = await service.store.fetch_one(
        f"SELECT * FROM {service.devices} WHERE id=?", (device_id,))
    assert device is not None
    session = await service.store.fetch_one(
        f"SELECT id FROM {service.sessions} WHERE device_id=? AND state='active'", (device_id,))
    assert session is not None
    first = await service.sync(device=device, data={"sessionId": session["id"]})
    version = first["grants"]["version"]
    assert first["grants"]["items"] == []

    result = await control.set_student_app_limit("opaque-A", VIDEO["packageName"], 2700, "Ayşe")
    assert result == {"packageName": VIDEO["packageName"], "studentLimitSeconds": 2700,
                      "previousSeconds": None}
    synced = await service.sync(device=device, data={"sessionId": session["id"],
                                                     "grantsVersion": version})
    assert synced["grants"]["items"] == [{
        "id": f"ovr:{VIDEO['packageName']}", "packageName": VIDEO["packageName"],
        "localDate": TODAY, "extraSeconds": 900}]
    # Değişiklik yoksa liste yeniden gitmez.
    again = await service.sync(device=device, data={"sessionId": session["id"],
                                                    "grantsVersion": synced["grants"]["version"]})
    assert again["grants"] is None

    limits = {app["packageName"]: app
              for app in (await TabletRead(service).student_limits("opaque-A"))["apps"]}
    video = limits[VIDEO["packageName"]]
    assert (video["source"], video["generalLimitSeconds"], video["studentLimitSeconds"],
            video["dailyLimitSeconds"], video["remainingSeconds"]) == (
        "student", 1800, 2700, 2700, 2100)

    # Genelden kısa, yasak ya da profilde olmayan uygulamaya özel süre verilemez.
    for package, seconds in ((VIDEO["packageName"], 1200), (GAME["packageName"], 1200),
                             ("com.not.in.profile", 1200), (VIDEO["packageName"], 30)):
        with pytest.raises(TabletCapabilityError) as error:
            await control.set_student_app_limit("opaque-A", package, seconds, "Ayşe")
        assert error.value.reason == "validation_error"

    # Genele döndür: satır silinir, tablete boş liste iner.
    back = await control.set_student_app_limit("opaque-A", VIDEO["packageName"], None, "Ayşe")
    assert back["previousSeconds"] == 2700
    reset = await service.sync(device=device, data={"sessionId": session["id"],
                                                    "grantsVersion": synced["grants"]["version"]})
    assert reset["grants"]["items"] == []
    assert (await TabletRead(service).student_limits("opaque-A"))["apps"][0]["source"] == "general"


async def test_ozel_sure_girisle_gelir(service: TabletService, clock: Any) -> None:
    _, device_id = await _setup(service, clock)
    await TabletControl(service, Log()).set_student_app_limit(
        "opaque-A", VIDEO["packageName"], 3600, "Ayşe")
    sessions = await service.list_sessions(limit=5, student_id="opaque-A")
    await service.end_session(session_id=sessions[0]["id"])
    device = await service.store.fetch_one(
        f"SELECT * FROM {service.devices} WHERE id=?", (device_id,))
    assert device is not None
    logged = await service.login(device=device, student_code="123456")
    assert {"id": f"ovr:{VIDEO['packageName']}", "packageName": VIDEO["packageName"],
            "localDate": TODAY, "extraSeconds": 1800} in logged["extraGrants"]
    assert ":o1:" in logged["grantsVersion"]


async def test_km_paneli_ozel_sureyi_gorur_ve_kaldirir(service: TabletService, clock: Any) -> None:
    await _setup(service, clock)
    await TabletControl(service, Log()).set_student_app_limit(
        "opaque-A", VIDEO["packageName"], 2700, "Ayşe")
    usage = await service.student_usage("opaque-A", TODAY)
    video = next(item for item in usage["usage"] if item["packageName"] == VIDEO["packageName"])
    assert video["limitSeconds"] == 2700
    assert video["remainingSeconds"] == 2700 - 600

    from modules.bbd_tablet.backend.api import routes

    routes.bind(service)

    class User:
        full_name = "KM Yöneticisi"

    listed = await routes.student_app_limits("opaque-A", user=User())  # type: ignore[arg-type]
    assert [item["dailyLimitSeconds"] for item in listed] == [2700]
    removed = await routes.reset_student_app_limit("opaque-A", VIDEO["packageName"],
                                                    user=User())  # type: ignore[arg-type]
    assert removed == {"packageName": VIDEO["packageName"], "removed": True}
    assert await routes.student_app_limits("opaque-A", user=User()) == []  # type: ignore[arg-type]
