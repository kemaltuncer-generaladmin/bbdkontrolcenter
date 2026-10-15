"""tablet-sinir: Akademi yöneticisinin uzaktan tablet sınırları (geçit uçları).

`GET /students/{id}/tablet/limits`, `PUT /tablet-profiles/{id}/limits`,
`POST /students/{id}/tablet/grants/{grantId}/revoke` — servis anahtarı
(`mobile_gateway.access`), denetim izi, olay; KM tek gerçek kaynaktır.
"""

from __future__ import annotations

from typing import Any

import httpx
from mobile_gateway_fakes import CAN, ELIF
from test_contract_v1 import expect_error, on_behalf, table


async def test_ogrencinin_sinirlari(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/students/{ELIF}/tablet/limits")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"localDate", "profile", "apps", "grants", "blockedUntil", "sync"}
    assert body["profile"] == {
        "id": "p-1", "name": "Ortaokul", "revision": 4, "updatedAt": "2026-10-12T08:00:00+03:00",
        "usageWindows": [{"days": [1, 2, 3, 4, 5], "start": "08:00", "end": "18:00"}],
        "deviceCount": 12}
    assert body["apps"][0] == {
        "packageName": "com.example.video", "appName": "Video", "mode": "limited",
        "dailyLimitSeconds": 2700, "generalLimitSeconds": 1800, "studentLimitSeconds": 2700,
        "source": "student", "usedSeconds": 600, "extraSeconds": 300,
        "remainingSeconds": 2400, "inProfile": True}
    assert body["apps"][1]["source"] == "general"
    assert body["apps"][1]["generalLimitSeconds"] is None
    assert body["grants"][0]["createdAt"] == "2026-10-12T09:00:00+03:00"
    assert body["sync"] == {"activeSeconds": 60, "idleSeconds": 300}

    never = (await client.get(f"/students/{CAN}/tablet/limits")).json()
    assert never["profile"] is None and never["apps"] == []
    expect_error(await client.get("/students/STU-yok/tablet/limits"), 404, "student_not_found")


async def test_profil_sinirlarini_duzenleme(client: httpx.AsyncClient, world: Any,
                                           store: Any) -> None:
    who = on_behalf("akademi:staff:42:Ayşe Yılmaz")
    body = {"expectedRevision": 4, "reason": "Sınav haftası",
            "apps": [{"packageName": "com.example.video", "mode": "limited",
                      "dailyLimitSeconds": 2700},
                     {"packageName": "com.example.game", "mode": "blocked"}],
            "usageWindows": [{"days": [1, 2, 3, 4, 5], "start": "08:00", "end": "17:00"}]}
    saved = await client.put("/tablet-profiles/p-1/limits", json=body,
                             headers={**who, "Idempotency-Key": "k-1"})
    assert saved.status_code == 200, saved.text
    assert saved.json() == {"id": "p-1", "name": "Ortaokul", "revision": 5,
                            "updatedAt": "2026-10-12T10:00:00+03:00"}
    call = world.tablet_control.calls[-1]
    assert call[0] == "update_profile_limits"
    assert call[1]["apps"][1] == {"packageName": "com.example.game", "mode": "blocked",
                                  "dailyLimitSeconds": None}
    assert call[1]["usage_windows"] == [{"days": [1, 2, 3, 4, 5], "start": "08:00",
                                         "end": "17:00"}]
    assert "Ayşe Yılmaz" in call[1]["actor"]

    # Aynı anahtar + gövde → saklı yanıt; KM'ye ikinci yazma yok.
    replay = await client.put("/tablet-profiles/p-1/limits", json=body,
                              headers={**who, "Idempotency-Key": "k-1"})
    assert replay.json()["revision"] == 5
    assert sum(1 for name, _ in world.tablet_control.calls
               if name == "update_profile_limits") == 1

    # Eski revizyon → 409 profile_conflict.
    expect_error(await client.put("/tablet-profiles/p-1/limits", json=body), 409,
                 "profile_conflict")
    expect_error(await client.put("/tablet-profiles/yok/limits",
                                  json={**body, "expectedRevision": 5}), 404,
                 "profile_not_found")
    # Gerekçe isteğe bağlı (kullanıcı kararı): verilmezse varsayılan metin denetim izine gider.
    plain = await client.put("/tablet-profiles/p-1/limits",
                             json={"expectedRevision": 5, "usageWindows": []})
    assert plain.status_code == 200 and plain.json()["revision"] == 6
    assert world.tablet_control.calls[-1][1]["reason"] == "Akademi yöneticisi düzeltti"
    for bad in ({"expectedRevision": 6, "reason": "Boş"},
                {"expectedRevision": 6, "reason": "Süre yok",
                 "apps": [{"packageName": "com.example.video", "mode": "limited"}]},
                {"expectedRevision": 6, "reason": "Kısa",
                 "apps": [{"packageName": "com.example.video", "mode": "limited",
                           "dailyLimitSeconds": 30}]},
                {"expectedRevision": 6, "reason": "Saat",
                 "usageWindows": [{"days": [1], "start": "8:00", "end": "17:00"}]},
                {"expectedRevision": 6, "reason": "Yabancı",
                 "apps": [{"packageName": "com.not.in.profile", "mode": "blocked"}]}):
        expect_error(await client.put("/tablet-profiles/p-1/limits", json=bad), 422,
                     "validation_error")
    audit = [row for row in await table(store, "audit")
             if row["action"] == "tablet.profile.limits"]
    assert audit and "Sınav haftası" in str(audit[0])


async def test_ek_sure_geri_alma(client: httpx.AsyncClient, world: Any, store: Any) -> None:
    done = await client.post(f"/students/{ELIF}/tablet/grants/g-1/revoke",
                             json={"reason": "Yanlış verildi"})
    assert done.status_code == 200, done.text
    assert done.json() == {"grantId": "g-1", "revoked": True, "alreadyRevoked": False}
    assert world.tablet_control.calls[-1][1]["grant_id"] == "g-1"
    expect_error(await client.post(f"/students/{ELIF}/tablet/grants/g-x/revoke",
                                   json={"reason": "Yok"}), 404, "grant_not_found")
    plain = await client.post(f"/students/{ELIF}/tablet/grants/g-1/revoke", json={})
    assert plain.status_code == 200
    actions = [row["action"] for row in await table(store, "audit")]
    assert "tablet.grant.revoke" in actions


async def test_eski_tablet_modulunde_503(client: httpx.AsyncClient, world: Any) -> None:
    from mobile_gateway_fakes import FakeTabletDailyRead

    world.replace("bbd_tablet.read", FakeTabletDailyRead())
    error = expect_error(await client.get(f"/students/{ELIF}/tablet/limits"), 503,
                         "capability_missing")
    assert error["capability"] == "bbd_tablet.read.student_limits"


async def test_genel_ayarlar(client: httpx.AsyncClient) -> None:
    response = await client.get("/tablet-profiles")
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["deviceCount"] == 12 and items[0]["updatedAt"] == "2026-10-12T08:00:00+03:00"
    assert items[0]["apps"][1] == {"packageName": "com.example.game", "appName": "Oyun",
                                   "mode": "blocked", "dailyLimitSeconds": None}


async def test_ogrenciye_ozel_sure(client: httpx.AsyncClient, world: Any, store: Any) -> None:
    done = await client.put(f"/students/{ELIF}/tablet/app-limits/com.example.video",
                            json={"dailyLimitSeconds": 2700})
    assert done.status_code == 200, done.text
    assert done.json() == {"packageName": "com.example.video", "studentLimitSeconds": 2700,
                           "previousSeconds": None}
    back = await client.put(f"/students/{ELIF}/tablet/app-limits/com.example.video",
                            json={"dailyLimitSeconds": None})
    assert back.json()["studentLimitSeconds"] is None
    assert world.tablet_control.calls[-1][1]["daily_limit_seconds"] is None
    expect_error(await client.put(f"/students/{ELIF}/tablet/app-limits/com.example.video",
                                  json={"dailyLimitSeconds": 1200}), 422, "validation_error")
    expect_error(await client.put(f"/students/{ELIF}/tablet/app-limits/com.example.video",
                                  json={}), 422, "validation_error")
    expect_error(await client.put("/students/STU-yok/tablet/app-limits/com.example.video",
                                  json={"dailyLimitSeconds": 2700}), 404, "student_not_found")
    actions = [row["action"] for row in await table(store, "audit")]
    assert actions.count("tablet.student_limit") >= 2
