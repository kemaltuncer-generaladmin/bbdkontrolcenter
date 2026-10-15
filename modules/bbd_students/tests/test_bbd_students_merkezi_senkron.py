"""Merkezi senkron — önce KM, sonra kantin; olaylar; yaşam döngüsü; uzlaştırma.

Sözleşme: bbd-akademi `docs/integrations/km-merkezi-senkron.md` §2, §5, §6.3, §7.
Depo GERÇEK SQLite'tır (çekirdek şeması + outbox + modülün bütün göçleri), olay
outbox'u gerçektir; kantin, tablet ve Akademi finans yeteneği sahtedir.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from bbd_students_backend.api import routes
from bbd_students_backend.canteen_events import CanteenSync
from bbd_students_backend.directory import StudentDirectory
from bbd_students_backend.groups import GroupService
from bbd_students_backend.lifecycle import Lifecycle
from bbd_students_backend.service import StudentService
from bbd_students_backend.sync import StudentSync, SyncError
from bbd_students_fakes import FakeLog
from fastapi import HTTPException

from km_core.contracts.module import ScopedStore
from km_core.store.bootstrap import build_schema
from km_core.store.db import Store
from km_platform.events.outbox import EventOutbox

MIGRATIONS = Path(__file__).resolve().parents[1] / "backend" / "migrations"
TODAY = date(2026, 10, 12)
PII = ("Elif", "Kaya", "532", "505", "0532")


class Refused(RuntimeError):
    def __init__(self, status: int | None, reason: str | None, text: str = "ret") -> None:
        super().__init__(text)
        self.status = status
        self.reason = reason


class Canteen:
    """`canteen.api` sahtesi: öğrenci listesi + yeni senkron uçları."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.down = False
        self.reject: Refused | None = None
        self.calls: list[tuple[str, str, Any]] = []
        self.requests: list[dict[str, Any]] = []

    def add(self, opaque_id: str, name: str, **extra: Any) -> None:
        self.rows[opaque_id] = {"id": opaque_id, "displayName": name, "parentPhone": "",
                                "balance": 0, "spendingLimit": None, "isBlocked": False,
                                "updatedAt": 1791786600000, **extra}

    def _gate(self) -> None:
        if self.down:
            raise RuntimeError("bağlantı hatası")
        if self.reject is not None:
            raise self.reject

    async def students(self) -> list[dict[str, Any]]:
        if self.down:
            raise RuntimeError("bağlantı hatası")
        return [dict(row) for row in self.rows.values()]

    async def student(self, opaque_id: str) -> dict[str, Any]:
        if self.down:
            raise RuntimeError("bağlantı hatası")
        if opaque_id not in self.rows:
            raise Refused(404, "student_not_found")
        return dict(self.rows[opaque_id])

    async def get_student(self, opaque_id: str) -> dict[str, Any]:
        return await self.student(opaque_id)

    async def payment_requests(self, student_id: str | None = None) -> list[dict[str, Any]]:
        return [row for row in self.requests if row.get("studentId") == student_id]

    async def upsert_student(self, opaque_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        self._gate()
        self.calls.append(("upsert", opaque_id, dict(changes)))
        row = self.rows.setdefault(opaque_id, {"id": opaque_id, "balance": 0, "parentPhone": "",
                                               "updatedAt": 1791786600000})
        row.update(changes)
        return dict(row)

    async def set_student_status(self, opaque_id: str, status: str, reason: str | None = None,
                                 at: str | None = None) -> dict[str, Any]:
        self._gate()
        self.calls.append(("status", opaque_id, status))
        self.rows[opaque_id]["status"] = status
        return {"id": opaque_id, "status": status}

    async def delete_student(self, opaque_id: str) -> dict[str, Any]:
        self._gate()
        self.calls.append(("delete", opaque_id, None))
        self.rows.pop(opaque_id, None)
        return {}

    async def dashboard(self) -> dict[str, Any]:
        return {}


class Tablet:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.only_reasons: list[str | None] = []

    async def block(self, student_id: str, until: Any, reason: str, actor: str) -> None:
        self.calls.append(("block", student_id))

    async def unblock(self, student_id: str, reason: str, actor: str, *,
                      only_reason: str | None = None) -> None:
        self.calls.append(("unblock", student_id))
        self.only_reasons.append(only_reason)


class Finance:
    def __init__(self, debt: bool = False, fail: bool = False) -> None:
        self.debt, self.fail = debt, fail

    async def has_institution_debt(self, student_id: str) -> bool:
        if self.fail:
            raise RuntimeError("Akademi yok")
        return self.debt


@dataclass
class World:
    raw: Store
    store: ScopedStore
    outbox: EventOutbox
    canteen: Canteen
    directory: StudentDirectory
    sync: StudentSync
    service: StudentService
    groups: GroupService
    lifecycle: Lifecycle
    caps: dict[str, Any]
    clock: list[datetime]

    async def events(self) -> list[dict[str, Any]]:
        return await self.outbox.read_after(0, 500)

    async def types(self) -> list[str]:
        return [event["type"] for event in await self.events()]


@pytest.fixture
async def world(tmp_path: Path) -> AsyncIterator[World]:
    raw = Store(tmp_path / "km.sqlite")
    await raw.open()
    await build_schema(raw)
    for path in sorted(MIGRATIONS.glob("*.sql")):
        await raw.apply_migration("bbd_students", path.name, path.read_text(encoding="utf-8"))
    store = ScopedStore(raw, "bbd_students")
    outbox = EventOutbox(raw)
    await outbox.open(prune_loop=False)
    canteen = Canteen()
    clock = [datetime(2026, 10, 12, 9, 0, tzinfo=UTC)]
    caps: dict[str, Any] = {"bbd_tablet.control": Tablet()}
    directory = StudentDirectory(canteen=canteen, store=store, log=FakeLog())
    sync = StudentSync(store=store, canteen=canteen, log=FakeLog(), outbox=lambda: outbox,
                       on_change=directory.invalidate, clock=lambda: clock[0],
                       today=lambda: clock[0].date(), delay=0)
    service = StudentService(canteen=canteen, store=store, log=FakeLog(),
                             on_change=directory.invalidate, sync=sync, directory=directory)
    groups = GroupService(store=store, log=FakeLog(), outbox=lambda: outbox,
                          today=lambda: clock[0].date())
    lifecycle = Lifecycle(sync=sync, groups=groups, canteen=canteen, log=FakeLog(),
                          directory=directory, capability=caps.get)
    yield World(raw, store, outbox, canteen, directory, sync, service, groups, lifecycle,
                caps, clock)
    await outbox.close()
    await raw.close()


def no_pii(events: list[dict[str, Any]]) -> None:
    text = json.dumps(events, ensure_ascii=False)
    for needle in PII:
        assert needle not in text, needle


# ================================================================ ekleme


async def test_ekleme_once_km_sonra_kantin_tek_olay(world: World) -> None:
    result = await world.service.create_student(
        {"firstName": "Elif", "lastName": "Kaya", "className": "8-A",
         "parentPhone": "0532 111 22 33"}, actor="Ayşe")
    kantin_id = result["kantinId"]
    assert result["canteenSync"] == "ok"
    assert world.canteen.calls == [("upsert", kantin_id, {"displayName": "Elif Kaya",
                                                          "parentPhone": "0532 111 22 33"})]
    [event] = await world.events()
    assert event["type"] == "student.created" and event["subject"]["id"] == kantin_id
    assert event["revision"] == 1 and event["origin"] == "km"
    no_pii(await world.events())
    record = await world.directory.get(kantin_id)
    assert record is not None and record["canteenSync"] == "ok"
    assert record["revision"] == 1 and record["status"] == "active"


async def test_kantin_yokken_kayit_bekler_dizinde_gorunur_uzlastirma_tamamlar(
        world: World) -> None:
    world.canteen.down = True
    result = await world.service.create_student({"firstName": "Elif", "lastName": "Kaya"})
    kantin_id = result["kantinId"]
    assert result["canteenSync"] == "pending" and "warning" in result

    record = await world.directory.get(kantin_id)
    assert record is not None and record["canteen"] is None
    assert record["canteenSync"] == "pending" and record["displayName"] == "Elif Kaya"
    listed = await world.service.list_students()
    row = next(item for item in listed["students"] if item["kantinId"] == kantin_id)
    assert row["canteenSync"] == "pending" and row["canteenMissing"] is True

    await world.sync.reconcile_once()          # kantin hâlâ yok
    assert (await world.sync.profile(kantin_id))["canteen_attempts"] == 2  # type: ignore[index]

    world.canteen.down = False
    await world.sync.reconcile_once()
    profile = await world.sync.profile(kantin_id)
    assert profile is not None and profile["canteen_sync"] == "ok"
    assert profile["canteen_payload"] is None
    types = await world.types()
    assert types == ["student.created", "student.updated"]
    last = (await world.events())[-1]
    assert last["origin"] == "system" and last["fields"] == ["canteenSync"]
    record = await world.directory.get(kantin_id)
    assert record is not None and record["canteen"] is not None


async def test_kantin_reddederse_yeni_kayit_geri_alinir(world: World) -> None:
    world.canteen.reject = Refused(422, "validation_error", "ad geçersiz")
    with pytest.raises(ValueError, match="kabul etmedi"):
        await world.service.create_student({"firstName": "Elif", "lastName": "Kaya"})
    rows = await world.store.fetch_all("SELECT * FROM mod_bbd_students_profile")
    assert rows == []
    events = await world.events()
    assert [event["type"] for event in events] == ["student.created", "student.deleted"]
    assert events[1]["origin"] == "system" and "erasedAt" in events[1]["data"]


# ============================================================ güncelleme


async def test_guncelleme_alanlar_ve_veli_olaylari(world: World) -> None:
    world.canteen.add("STU-1", "Elif Kaya", parentPhone="05321112233")
    await world.service.update_student("STU-1", {"className": "8-A"})
    await world.service.update_student("STU-1", {
        "className": "8-B", "gradeLevel": 8, "parentPhone": "05055556677",
        "parentPhone2": "05321234567", "note": "not"})
    events = await world.events()
    assert [event["type"] for event in events] == [
        "student.updated", "student.updated", "guardian.phone_changed", "guardian.linked"]
    assert events[0]["fields"] == ["className"]
    assert sorted(events[1]["fields"]) == ["className", "gradeLevel"]
    assert events[2]["data"] == {"slot": 1} and events[3]["data"] == {"slot": 2}
    assert events[1]["revision"] == events[2]["revision"] == 2
    no_pii(events)
    assert world.canteen.calls[-1] == ("upsert", "STU-1", {"displayName": "Elif Kaya",
                                                          "parentPhone": "05055556677"})

    await world.service.update_student("STU-1", {"parentPhone": ""})
    assert (await world.events())[-1]["type"] == "guardian.unlinked"


async def test_kantin_reddi_guncellemeyi_telafi_eder(world: World) -> None:
    world.canteen.add("STU-1", "Elif Kaya")
    await world.service.update_student("STU-1", {"className": "8-A"})
    world.canteen.reject = Refused(422, "validation_error")
    with pytest.raises(ValueError):
        await world.service.update_student("STU-1", {"className": "9-A", "firstName": "Ela"})
    profile = await world.sync.profile("STU-1")
    assert profile is not None and profile["class_name"] == "8-A"
    assert profile["canteen_sync"] == "ok"
    last = (await world.events())[-1]
    assert last["type"] == "student.updated" and last["origin"] == "system"


async def test_bekleyen_ad_dizinde_hemen_gorunur(world: World) -> None:
    world.canteen.add("STU-1", "Elif Kaya")
    await world.directory.list()               # kantin listesi önbellekte
    world.canteen.down = True
    await world.service.update_student("STU-1", {"firstName": "Ela"})
    world.canteen.down = False
    record = await world.directory.get("STU-1")
    assert record is not None and record["displayName"] == "Ela Kaya"
    assert record["canteenSync"] == "pending"


async def test_toplu_mufredat_ogrenci_basina_olay(world: World) -> None:
    for index in range(3):
        world.canteen.add(f"STU-{index}", f"Öğrenci {index}")
    await world.service.set_curriculum(["STU-0", "STU-1", "STU-2"], {"gradeLevel": 8})
    events = await world.events()
    assert [event["subject"]["id"] for event in events] == ["STU-0", "STU-1", "STU-2"]
    assert all(event["fields"] == ["gradeLevel"] for event in events)


async def test_kantin_kontrolleri_olayi(world: World) -> None:
    world.canteen.add("STU-1", "Elif Kaya")

    async def update_controls(student_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        return {"id": student_id, **patch}

    world.canteen.update_controls = update_controls  # type: ignore[attr-defined]
    await world.service.update_canteen_controls("STU-1", {"dailyLimit": 5000, "isBlocked": True},
                                                actor="Ayşe")
    [event] = await world.events()
    assert event["type"] == "canteen.controls_changed"
    assert event["fields"] == ["dailyLimit", "isBlocked"]


# ========================================================= yaşam döngüsü


async def test_pasiflestir_hepsi_tek_islemde_sonra_kantin_ve_tablet(world: World) -> None:
    world.canteen.add("STU-1", "Elif Kaya", balance=1500)
    group = await world.groups.create({"name": "LGS-1", "studentIds": ["STU-1"],
                                       "validFrom": "2026-10-01", "reason": "geçmiş"},
                                      actor="Ayşe")
    result = await world.lifecycle.deactivate("STU-1", reason="left", actor="Ayşe")
    assert result["status"] == "inactive" and result["canteenSync"] == "ok"
    assert result["tablet"] == "blocked" and result["groupsClosed"] is True
    assert ("status", "STU-1", "inactive") in world.canteen.calls
    types = await world.types()
    assert types == ["group.changed", "student.deactivated", "group.changed",
                     "tablet.policy_changed"]
    deactivated = (await world.events())[1]
    assert deactivated["data"]["status"] == "inactive"
    assert deactivated["data"]["reason"] == "left" and deactivated["fields"] == ["status"]
    detail = await world.groups.detail(group["id"])
    member = detail["members"][0] if "members" in detail else None
    assert member is None or member.get("validTo") == "2026-10-12"
    record = await world.directory.get("STU-1")
    assert record is not None and record["status"] == "inactive"
    assert record["statusReason"] == "left" and record["statusSince"]
    with pytest.raises(SyncError, match="zaten pasif"):
        await world.lifecycle.deactivate("STU-1", reason="left", actor="Ayşe")
    history = await world.sync.history("STU-1")
    assert history[0]["action"] == "deactivated"


async def test_ileri_tarihli_pasiflestirme_gunu_gelince(world: World) -> None:
    world.canteen.add("STU-1", "Elif Kaya")
    result = await world.lifecycle.deactivate("STU-1", reason="graduated",
                                              effective_at="2026-10-20", actor="Ayşe")
    assert result["scheduledFor"] == "2026-10-20"
    assert await world.types() == []
    with pytest.raises(SyncError):
        await world.lifecycle.deactivate("STU-2", reason="left", effective_at="2026-10-01",
                                         actor="Ayşe")
    world.clock[0] = datetime(2026, 10, 19, 21, 30, tzinfo=UTC)   # 20 Ekim 00:30 yerel değil
    world.clock[0] = datetime(2026, 10, 20, 0, 30, tzinfo=UTC)
    await world.sync.reconcile_once()
    assert "student.deactivated" in await world.types()
    profile = await world.sync.profile("STU-1")
    assert profile is not None and profile["status"] == "inactive"
    assert profile["status_effective_at"] is None


async def test_yeniden_etkinlestir(world: World) -> None:
    world.canteen.add("STU-1", "Elif Kaya")
    await world.lifecycle.deactivate("STU-1", reason="suspended", actor="Ayşe")
    result = await world.lifecycle.reactivate("STU-1", actor="Ayşe")
    assert result["status"] == "active" and result["groupsReopened"] is False
    assert result["tablet"] == "unblocked"
    # Yalnız pasifleştirmenin koyduğu kısıt kalkar (başka gerekçeli kısıt sürer).
    assert world.caps["bbd_tablet.control"].only_reasons[-1] == "Öğrenci pasifleştirildi (KM)"
    assert (await world.types())[-2:] == ["student.reactivated", "tablet.policy_changed"]
    assert world.canteen.calls[-1] == ("status", "STU-1", "active")
    with pytest.raises(SyncError, match="zaten etkin"):
        await world.lifecycle.reactivate("STU-1", actor="Ayşe")


async def _members(world: World, student: str) -> list[dict[str, Any]]:
    return [dict(row) for row in await world.store.fetch_all(
        "SELECT * FROM mod_bbd_students_group_member WHERE kantin_id = ? ORDER BY valid_from",
        (student,))]


async def test_aktiflestir_siniflari_ayni_gun_kesintisiz_geri_acar(world: World) -> None:
    """16.10.2026 kullanıcı: "KM de pasife alınan öğrenci aktifleştirilebilsin" — sınıf
    üyelikleri, durum ve olaylar tek işlemde; Akademi önce öğrenciyi sonra sınıfı görür."""
    world.canteen.add("STU-1", "Elif Kaya")
    group = await world.groups.create({"name": "8-A", "studentIds": ["STU-1"],
                                       "validFrom": "2026-10-01", "reason": "geçmiş"},
                                      actor="Ayşe")
    await world.lifecycle.deactivate("STU-1", reason="left", actor="Ayşe")
    [closed] = await _members(world, "STU-1")
    assert closed["valid_to"] == "2026-10-12"
    before = len(await world.types())
    result = await world.lifecycle.reactivate("STU-1", actor="Ayşe", restore_classes=True)
    assert result["status"] == "active" and result["groupsReopened"] is True
    assert result["classesRestored"] == ["8-A"] and result["canteenSync"] == "ok"
    # Aynı gün geri alındı: aynı satır kesintisiz sürer (yeni satır açılmaz).
    [member] = await _members(world, "STU-1")
    assert member["id"] == closed["id"] and member["valid_to"] == ""
    assert member["valid_from"] == "2026-10-01" and member["reason"] == "Öğrenci aktifleştirildi"
    assert (await world.types())[before:] == ["student.reactivated", "group.changed",
                                              "tablet.policy_changed"]
    no_pii(await world.events())
    assert "STU-1" in await world.groups.current_students(group["id"])
    history = await world.sync.history("STU-1")
    assert [row["action"] for row in history[:2]] == ["classes_restored", "reactivated"]
    assert "8-A" in history[0]["note"]
    record = await world.directory.get("STU-1")
    assert record is not None and record["status"] == "active"


async def test_aktiflestir_gunler_sonra_yeni_uyelik_arsivli_sinif_atlanir(world: World) -> None:
    world.canteen.add("STU-1", "Elif Kaya")
    kept = await world.groups.create({"name": "8-A", "studentIds": ["STU-1"],
                                      "validFrom": "2026-10-01", "reason": "geçmiş"}, actor="Ayşe")
    gone = await world.groups.create({"name": "Etüt", "studentIds": ["STU-1"],
                                      "validFrom": "2026-10-01", "reason": "geçmiş"}, actor="Ayşe")
    await world.lifecycle.deactivate("STU-1", reason="suspended", actor="Ayşe")
    await world.groups.archive(gone["id"], actor="Ayşe")
    world.clock[0] = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
    result = await world.lifecycle.reactivate("STU-1", actor="Ayşe", restore_classes=True)
    assert result["classesRestored"] == ["8-A"]
    rows = [row for row in await _members(world, "STU-1") if row["group_id"] == kept["id"]]
    # Pasif kalınan günler (13–19 Ekim) sınıf listesine girmez: eski satır kapalı kalır.
    assert [(row["valid_from"], row["valid_to"]) for row in rows] == [
        ("2026-10-01", "2026-10-12"), ("2026-10-20", "")]
    assert await world.groups.current_students(gone["id"]) == []
    # Sınıf açmadan aktifleştirme (dönem geçişi geri alması) üyeliğe dokunmaz.
    await world.lifecycle.deactivate("STU-1", reason="left", actor="Ayşe")
    plain = await world.lifecycle.reactivate("STU-1", actor="Ayşe")
    assert plain["groupsReopened"] is False and plain["classesRestored"] == []
    latest = [row for row in await _members(world, "STU-1") if row["group_id"] == kept["id"]][-1]
    assert latest["valid_to"] == "2026-10-20" and latest["reason"] == "Öğrenci pasifleştirildi"


async def test_toplu_aktiflestir(world: World) -> None:
    for kantin_id, name in (("STU-1", "Elif Kaya"), ("STU-2", "Can Kaya"), ("STU-3", "Ali Ak")):
        world.canteen.add(kantin_id, name)
    await world.groups.create({"name": "8-A", "studentIds": ["STU-1", "STU-2"],
                               "validFrom": "2026-10-01", "reason": "geçmiş"}, actor="Ayşe")
    await world.lifecycle.bulk_deactivate(["STU-1", "STU-2"], reason="left", actor="Ayşe")
    await asyncio.sleep(0.05)
    result = await world.lifecycle.bulk_reactivate(["STU-1", "STU-2", "STU-3", "STU-1"],
                                                   actor="Ayşe")
    assert result["reactivated"] == ["STU-1", "STU-2"] and result["classesRestored"] == 2
    assert [row["code"] for row in result["failed"]] == ["already_active"]
    for _ in range(50):
        if ("status", "STU-2", "active") in world.canteen.calls:
            break
        await asyncio.sleep(0.01)
    assert ("status", "STU-1", "active") in world.canteen.calls
    assert ("status", "STU-2", "active") in world.canteen.calls
    assert (await world.types()).count("student.reactivated") == 2
    with pytest.raises(SyncError):
        await world.lifecycle.bulk_reactivate([], actor="Ayşe")


async def test_kalici_silme_on_kosullari_ve_mezar_tasi(world: World) -> None:
    world.canteen.add("STU-1", "Elif Kaya", balance=250)
    with pytest.raises(SyncError) as error:
        await world.lifecycle.delete("STU-1", confirm_name="Elif Kaya", actor="Ayşe")
    assert error.value.code == "not_inactive"
    await world.lifecycle.deactivate("STU-1", reason="left", actor="Ayşe")

    with pytest.raises(SyncError) as error:
        await world.lifecycle.delete("STU-1", confirm_name="Başka Ad", actor="Ayşe")
    assert error.value.code == "confirm_name_mismatch"
    with pytest.raises(SyncError) as error:
        await world.lifecycle.delete("STU-1", confirm_name="elif kaya", actor="Ayşe")
    assert error.value.code == "balance_not_zero" and error.value.details == {"balance": 250}

    world.canteen.rows["STU-1"]["balance"] = 0
    world.canteen.requests.append({"studentId": "STU-1", "ref": "PR-1", "status": "PENDING"})
    with pytest.raises(SyncError) as error:
        await world.lifecycle.delete("STU-1", confirm_name="Elif Kaya", actor="Ayşe")
    assert error.value.code == "open_payment_request"

    world.canteen.requests.clear()
    with pytest.raises(SyncError) as error:
        await world.lifecycle.delete("STU-1", confirm_name="Elif Kaya", actor="Ayşe")
    assert error.value.code == "akademi_unavailable" and error.value.status == 503
    world.caps["akademi.student_finance"] = Finance(fail=True)
    with pytest.raises(SyncError) as error:
        await world.lifecycle.delete("STU-1", confirm_name="Elif Kaya", actor="Ayşe")
    assert error.value.code == "akademi_unavailable"
    world.caps["akademi.student_finance"] = Finance(debt=True)
    with pytest.raises(SyncError) as error:
        await world.lifecycle.delete("STU-1", confirm_name="Elif Kaya", actor="Ayşe")
    assert error.value.code == "institution_debt"

    world.caps["akademi.student_finance"] = Finance()
    before = world.clock[0] - timedelta(minutes=1)
    result = await world.lifecycle.delete("STU-1", confirm_name="Elif Kaya", actor="Ayşe")
    assert result["deleted"] is True and result["canteenSync"] == "ok"
    assert ("delete", "STU-1", None) in world.canteen.calls
    assert await world.sync.profile("STU-1") is None
    assert (await world.events())[-1]["type"] == "student.deleted"
    assert await world.directory.get("STU-1") is None
    listed = await world.directory.list(since=before)
    assert [item["id"] for item in listed["removed"]] == ["STU-1"]


async def test_toplu_pasiflestirme_ogrenci_basina_olay(world: World) -> None:
    for index in range(3):
        world.canteen.add(f"STU-{index}", f"Öğrenci {index}")
    result = await world.lifecycle.bulk_deactivate(["STU-0", "STU-1", "STU-2", "YOK"],
                                                   reason="graduated", actor="Ayşe")
    assert result["deactivated"] == ["STU-0", "STU-1", "STU-2"]
    assert [item["studentId"] for item in result["failed"]] == ["YOK"]
    for _ in range(50):
        if len([call for call in world.canteen.calls if call[0] == "status"]) == 3:
            break
        await asyncio.sleep(0.01)
    assert [call[1] for call in world.canteen.calls if call[0] == "status"] == [
        "STU-0", "STU-1", "STU-2"]
    assert (await world.types()).count("student.deactivated") == 3


# ================================================== kantin kaynaklı olaylar


async def test_kasadan_eklenen_ogrenci_taslak_profil(world: World) -> None:
    world.canteen.add("STU-K", "Kasa Öğrencisi Yeni")
    capability = CanteenSync(sync=world.sync, directory=world.directory,
                             canteen=world.canteen, log=FakeLog())
    kind = await capability.student_upserted("STU-K", ["displayName"], created=True,
                                             causation_id="CNT-7")
    assert kind == "student.created"
    profile = await world.sync.profile("STU-K")
    assert profile is not None and profile["source"] == "canteen"
    assert (profile["first_name"], profile["last_name"]) == ("Kasa Öğrencisi", "Yeni")
    [event] = await world.events()
    assert event["origin"] == "canteen" and event["causationId"] == "CNT-7"

    async def duplicate() -> bool:
        return False

    assert await capability.student_upserted("STU-K", ["parentPhone"],
                                             within=duplicate) is None
    kind = await capability.student_upserted("STU-K", ["parentPhone"], causation_id="CNT-8")
    assert kind == "student.updated"
    assert (await world.events())[-1]["fields"] == ["guardians"]
    listed = await world.service.list_students()
    assert listed["students"][0]["source"] == "canteen"
    await world.service.update_student("STU-K", {"className": "5-A"})
    assert (await world.sync.profile("STU-K"))["source"] == "km"  # type: ignore[index]


# ===================================================== kalıcı silme ekranları (0.5.4)
#
# Kullanıcı: "Kalıcı öğrenci silmeyi de ekleyelim ekranlara." Ön kontrol
# (`delete_check`) bütün engelleri birlikte, tutarıyla ve açık Türkçe verir ve
# HİÇBİR ŞEY YAZMAZ; kurum carisindeki ALACAK engellemez (kullanıcı kararı).
# Toplu silme her öğrenciyi ayrı denetler, ayrı sonuç + ayrı olay. Rota katmanı
# iki silmede de PIN ister (yıkıcı işlem). Ekran testleri:
# test_bbd_students_kalici_silme_ekran.py.


class AmountFinance:
    """`akademi.student_finance` sahtesi — 0.5.4 tutarlı yüzey (`institution_balance`)."""

    def __init__(self, debt: int = 0, credit: int = 0) -> None:
        self.debt, self.credit = debt, credit

    async def has_institution_debt(self, student_id: str) -> bool:
        return self.debt > 0

    async def institution_balance(self, student_id: str) -> dict[str, int]:
        return {"debtMinor": self.debt, "creditMinor": self.credit}


async def test_on_kontrol_butun_engelleri_birlikte_ve_tutariyla_verir(world: World) -> None:
    world.canteen.add("STU-1", "Elif Kaya", balance=4250)
    world.canteen.requests.append({"studentId": "STU-1", "ref": "PR-7", "status": "PENDING"})
    world.caps["akademi.student_finance"] = AmountFinance(debt=120000)
    before = await world.types()

    check = await world.lifecycle.delete_check("STU-1")

    assert check["deletable"] is False
    assert check["displayName"] == "Elif Kaya"
    codes = [item["code"] for item in check["blockers"]]
    assert codes == ["not_inactive", "balance_not_zero", "open_payment_request", "institution_debt"]
    messages = {item["code"]: item["message"] for item in check["blockers"]}
    assert "pasifleştirin" in messages["not_inactive"]
    assert "42,50 ₺" in messages["balance_not_zero"] and "tahsil" in messages["balance_not_zero"]
    assert "PR-7" in messages["open_payment_request"]
    assert "1.200,00 ₺" in messages["institution_debt"]
    assert check["willDelete"] and any("KVKK" in line for line in check["willDelete"])
    # Ön kontrol yazma yapmaz: ne olay ne kantin çağrısı.
    assert await world.types() == before
    assert not [call for call in world.canteen.calls if call[0] == "delete"]


async def test_ogrenci_lehine_kantin_bakiyesi_engeldir_kurum_alacagi_degildir(world: World) -> None:
    world.canteen.add("STU-1", "Elif Kaya", balance=-500)
    await world.lifecycle.deactivate("STU-1", reason="left", actor="Ayşe")
    world.caps["akademi.student_finance"] = AmountFinance(credit=10000)
    check = await world.lifecycle.delete_check("STU-1")
    assert [item["code"] for item in check["blockers"]] == ["balance_not_zero"]
    assert "öğrenci lehine" in check["blockers"][0]["message"]

    world.canteen.rows["STU-1"]["balance"] = 0
    check = await world.lifecycle.delete_check("STU-1")
    assert check["deletable"] is True and check["blockers"] == []
    assert [note["code"] for note in check["notes"]] == ["institution_credit"]
    assert "100,00 ₺" in check["notes"][0]["message"]
    result = await world.lifecycle.delete("STU-1", confirm_name="Elif Kaya", actor="Ayşe")
    assert result["deleted"] is True


async def test_akademiye_ulasilamazsa_on_kontrol_engel_der(world: World) -> None:
    world.canteen.add("STU-1", "Elif Kaya")
    await world.lifecycle.deactivate("STU-1", reason="left", actor="Ayşe")
    check = await world.lifecycle.delete_check("STU-1")       # yetenek yok
    assert [item["code"] for item in check["blockers"]] == ["akademi_unavailable"]


async def test_toplu_silme_karisik_secimde_ogrenci_basina_sonuc(world: World) -> None:
    world.canteen.add("STU-A", "Ali Uygun")
    world.canteen.add("STU-B", "Banu Etkin")
    world.canteen.add("STU-C", "Cem Borclu", balance=900)
    world.canteen.add("STU-D", "Deniz Uygun")
    for kantin_id in ("STU-A", "STU-C", "STU-D"):
        await world.lifecycle.deactivate(kantin_id, reason="graduated", actor="Ayşe")
    world.caps["akademi.student_finance"] = AmountFinance()
    ids = ["STU-A", "STU-B", "STU-C", "STU-D"]

    with pytest.raises(SyncError) as error:
        await world.lifecycle.bulk_delete(ids, confirm_count="3", actor="Ayşe")
    assert error.value.code == "confirm_count_mismatch"
    assert "student.deleted" not in await world.types()

    result = await world.lifecycle.bulk_delete(ids, confirm_count="4", actor="Ayşe")
    assert result["deleted"] == 2 and result["blocked"] == 2
    rows = {row["studentId"]: row for row in result["results"]}
    assert rows["STU-A"]["deleted"] is True and rows["STU-D"]["deleted"] is True
    assert rows["STU-B"]["deleted"] is False and rows["STU-B"]["code"] == "not_inactive"
    assert rows["STU-C"]["deleted"] is False and rows["STU-C"]["code"] == "balance_not_zero"
    assert rows["STU-C"]["displayName"] == "Cem Borclu" and "9,00 ₺" in rows["STU-C"]["message"]
    # Her silinen öğrenci için AYRI olay; engelliler yerinde.
    events = await world.events()
    deleted = [event["subject"]["id"] for event in events if event["type"] == "student.deleted"]
    assert deleted == ["STU-A", "STU-D"]
    assert await world.sync.profile("STU-A") is None
    assert await world.sync.profile("STU-C") is not None
    # Kantin anonimleştirmesi arka planda, sıralı.
    for _ in range(100):
        if len([call for call in world.canteen.calls if call[0] == "delete"]) == 2:
            break
        await asyncio.sleep(0.01)
    assert [call[1] for call in world.canteen.calls if call[0] == "delete"] == ["STU-A", "STU-D"]


async def test_toplu_silme_sinirlari(world: World) -> None:
    with pytest.raises(SyncError):
        await world.lifecycle.bulk_delete([], confirm_count="0", actor="Ayşe")
    with pytest.raises(SyncError, match="en çok 50"):
        await world.lifecycle.bulk_delete([f"STU-{i}" for i in range(51)], confirm_count="51", actor="Ayşe")


# ------------------------------------------------------------------ rota: PIN


class Identity:
    def __init__(self) -> None:
        self.store = self
        self.audits: list[tuple[Any, str, str]] = []

    async def fetch_one(self, sql: str, params: tuple[Any, ...]) -> dict[str, str]:
        return {"secret_lookup": "hmac:246810"}

    def secret_lookup(self, pin: str) -> str:
        return f"hmac:{pin}"

    async def audit(self, user_id: Any, action: str, *, result: str, detail: str) -> None:
        self.audits.append((user_id, action, result))


def _request() -> Any:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(identity=Identity())))


async def test_silme_rotalari_pin_ister(world: World) -> None:
    world.canteen.add("STU-1", "Elif Kaya")
    await world.lifecycle.deactivate("STU-1", reason="left", actor="Ayşe")
    world.caps["akademi.student_finance"] = AmountFinance()
    routes._lifecycle = world.lifecycle
    user = SimpleNamespace(id=7, full_name="Ayşe Yılmaz")

    old_shell = await routes.delete_student(_request(), "STU-1", routes.DeleteBody(confirmName="Elif Kaya"), user)
    assert old_shell.status_code == 422 and b"pin_required" in old_shell.body
    request = _request()
    with pytest.raises(HTTPException) as denied:
        await routes.delete_student(request, "STU-1",
                                    routes.DeleteBody(confirmName="Elif Kaya", pin="000000"), user)
    assert denied.value.status_code == 403
    assert request.app.state.identity.audits == [(7, "bbd_students.delete", "denied")]
    assert "student.deleted" not in await world.types()

    done = await routes.delete_student(_request(), "STU-1",
                                       routes.DeleteBody(confirmName="Elif Kaya", pin="246810"), user)
    assert done["deleted"] is True
    assert (await world.types())[-1] == "student.deleted"

    bulk = await routes.bulk_delete_students(_request(), routes.BulkDeleteBody(students=["X"], confirmCount="1"),
                                             user)
    assert bulk.status_code == 422 and b"pin_required" in bulk.body
    check = await routes.delete_check("STU-1", user)
    assert check.status_code == 404                     # silinmiş öğrenci


def test_silme_uclarinin_izni() -> None:
    wanted = {("GET", "/students/{kantin_id}/delete-check"), ("POST", "/students/delete"),
              ("DELETE", "/students/{kantin_id}")}
    found = {(method, route.path): route for route in routes.router.routes for method in route.methods
             if (method, route.path) in wanted}
    assert set(found) == wanted
    for route in found.values():
        declared: set[str] = set()
        for param in route.dependant.dependencies:
            for cell in getattr(param.call, "__closure__", None) or ():
                if isinstance(cell.cell_contents, tuple):
                    declared.update(cell.cell_contents)
        assert declared == {"bbd_students.delete"}
