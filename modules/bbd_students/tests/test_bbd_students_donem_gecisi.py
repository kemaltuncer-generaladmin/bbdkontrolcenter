"""DÖNEM GEÇİŞİ sihirbazı (iş 37 / İ4, göç 013).

Lider kararı (2026-10-15): "sınıfları yeni döneme taşı/kopyala, seviye +1, mezun et,
müfredat varsayılanları, önizleme + geri alınabilir." Depo GERÇEK SQLite'tır (çekirdek
şeması + gerçek outbox + modülün bütün göçleri); kantin ve tablet sahtedir. "Bugün"
2027-09-01 (hedef dönemin ilk günü) — ayrı testte 2026-10-15 (pencere dışı).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from bbd_students_backend import curriculum
from bbd_students_backend.academic_year import (
    YearRollover,
    default_action,
    next_level,
    next_name,
    next_year,
)
from bbd_students_backend.classes import ClassLink
from bbd_students_backend.directory import StudentDirectory
from bbd_students_backend.groups import GroupError, GroupService
from bbd_students_backend.lifecycle import Lifecycle
from bbd_students_backend.sync import StudentSync
from bbd_students_fakes import FakeLog
from test_bbd_students_merkezi_senkron import Canteen, Tablet

from km_core.contracts.module import ScopedStore
from km_core.store.bootstrap import build_schema
from km_core.store.db import Store
from km_core.store.dialect import translate_ddl
from km_platform.events.outbox import EventOutbox

MIGRATIONS = Path(__file__).resolve().parents[1] / "backend" / "migrations"
OLD, NEW = "2026-2027", "2027-2028"
L7 = {"gradeLevel": 7, "track": None, "examTarget": None}
L8 = {"gradeLevel": 8, "track": None, "examTarget": "LGS"}
L11 = {"gradeLevel": 11, "track": "SAY", "examTarget": "YKS"}
L12 = {"gradeLevel": 12, "track": "SAY", "examTarget": "YKS"}


@dataclass
class World:
    store: ScopedStore
    outbox: EventOutbox
    canteen: Canteen
    sync: StudentSync
    groups: GroupService
    lifecycle: Lifecycle
    rollover: YearRollover
    today: list[date]
    deleted: list[str]

    async def profile(self, kantin_id: str) -> dict[str, Any]:
        row = await self.store.fetch_one(
            f"SELECT * FROM {self.store.table('profile')} WHERE kantin_id = ?", (kantin_id,))
        assert row is not None
        return dict(row)

    async def level(self, kantin_id: str) -> dict[str, Any]:
        return curriculum.from_row(await self.profile(kantin_id))

    async def seed(self, kantin_id: str, grade: str | None = None, **columns: Any) -> None:
        self.canteen.add(kantin_id, f"Öğrenci {kantin_id}")
        values = {"kantin_id": kantin_id, "status": "active", "grade_level": grade,
                  "updated_at": "2026-09-01T00:00:00+00:00", **columns}
        await self.store.execute(
            f"INSERT INTO {self.store.table('profile')} ({', '.join(values)}) "
            f"VALUES ({', '.join('?' for _ in values)})", tuple(values.values()))

    async def make(self, name: str, students: list[str], level: dict[str, Any] | None = None,
                   *, year: str = OLD, kind: str = "group") -> str:
        body: dict[str, Any] = {"name": name, "studentIds": students, "academicYear": year,
                                "kind": kind, "validFrom": "2026-09-01", "reason": "kayıt",
                                "applyLevel": False}
        if level is not None:
            body["level"] = level
        if not students:
            body.pop("validFrom")
        return str((await self.groups.create(body, actor="Ayşe"))["id"])

    async def row(self, group_id: str) -> dict[str, Any] | None:
        row = await self.store.fetch_one(
            f"SELECT * FROM {self.store.table('group')} WHERE id = ?", (group_id,))
        return dict(row) if row else None


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
    today = [date(2027, 9, 1)]
    directory = StudentDirectory(canteen=canteen, store=store, log=FakeLog())
    sync = StudentSync(store=store, canteen=canteen, log=FakeLog(), outbox=lambda: outbox,
                       on_change=directory.invalidate,
                       clock=lambda: datetime.combine(today[0], datetime.min.time(), UTC),
                       today=lambda: today[0], delay=0)
    link = ClassLink(store=store, sync=sync, log=FakeLog(), today=lambda: today[0])
    deleted: list[str] = []

    async def on_deleted(group_id: str, revision: int) -> None:
        deleted.append(group_id)

    groups = GroupService(store=store, log=FakeLog(), outbox=lambda: outbox,
                          today=lambda: today[0], students_changed=link.refresh,
                          apply_level=link.apply_level, deleted=on_deleted)
    lifecycle = Lifecycle(sync=sync, groups=groups, canteen=canteen, log=FakeLog(),
                          directory=directory, capability={"bbd_tablet.control": Tablet()}.get)
    rollover = YearRollover(store=store, groups=groups, log=FakeLog(), sync=sync,
                            lifecycle=lifecycle)
    yield World(store, outbox, canteen, sync, groups, lifecycle, rollover, today, deleted)
    await asyncio.sleep(0)
    await outbox.close()
    await raw.close()


async def school(world: World) -> dict[str, str]:
    """2026-2027: 7-A (7), 8-A (8 LGS), 12-SAY (12), TYT Matematik (seviyesiz), özel ders."""
    for sid, grade in (("S1", "7"), ("S2", "7"), ("S3", "8"), ("S4", "12"), ("S5", "11"),
                       ("S6", "7")):
        await world.seed(sid, grade)
    await world.seed("S7", "7", status="inactive")
    ids = {
        "7-A": await world.make("7-A", ["S1", "S2", "S7"], L7),
        "8-A": await world.make("8-A", ["S3"], L8),
        "12-SAY": await world.make("12-SAY", ["S4"], L12),
        "TYT": await world.make("TYT Matematik", ["S4", "S5"]),
        "Özel": await world.make("Elif özel", ["S6"], kind="individual"),
    }
    # Pasif öğrencinin üyeliği pasifleşmede kapanmış olurdu: kapatılmış satır.
    await world.store.execute(
        f"UPDATE {world.store.table('group_member')} SET valid_to = '2026-12-31' "
        f"WHERE kantin_id = 'S7'")
    return ids


# --------------------------------------------------------------------- kural


def test_goc_013_lehce_cevirmeninden_gecer() -> None:
    translate_ddl((MIGRATIONS / "013_year_rollover.sql").read_text(encoding="utf-8"))


def test_seviye_ad_ve_varsayilan_islem() -> None:
    assert next_year(OLD) == NEW
    assert next_level(L7) == {"gradeLevel": 8, "track": None, "examTarget": "LGS"}
    assert next_level(L8) == {"gradeLevel": 9, "track": None, "examTarget": None}
    assert next_level({"gradeLevel": 9, "track": None, "examTarget": None}) == {
        "gradeLevel": 10, "track": None, "examTarget": None}
    assert next_level({"gradeLevel": 10, "track": "EA", "examTarget": None}) == {
        "gradeLevel": 11, "track": "EA", "examTarget": "YKS"}
    assert next_level(L12) == {"gradeLevel": "graduate", "track": "SAY", "examTarget": "YKS"}
    assert next_level({"gradeLevel": "graduate", "track": "SAY", "examTarget": "YKS"})[
        "gradeLevel"] == "graduate"
    assert next_level(None) is None
    assert next_name("7-A", L7) == "8-A"
    assert next_name("11 SAY", L11) == "12 SAY"
    assert next_name("12-SAY", L12) == "Mezun-SAY"
    assert next_name("TYT Matematik", None) == "TYT Matematik"
    assert next_name("10-A", L7) == "10-A"          # ad seviyeyle uyuşmuyor: aynen
    assert default_action({"kind": "group"}, L7) == "promote"
    assert default_action({"kind": "group"}, L8) == "graduate"
    assert default_action({"kind": "group"}, L12) == "graduate"
    assert default_action({"kind": "group"}, None) == "archive"
    assert default_action({"kind": "individual"}, L7) == "archive"


# ------------------------------------------------------------------ önizleme


async def test_onizleme_varsayilanlar_yazmaz(world: World) -> None:
    ids = await school(world)
    revision = await world.groups.revision()
    overview = await world.rollover.overview()
    assert (overview["fromYear"], overview["toYear"], overview["effectiveFrom"]) == (
        OLD, NEW, "2027-09-01")
    plan = await world.rollover.preview({})
    assert plan["blocked"] is None and plan["problems"] == []
    assert plan["closeDay"] == "2027-08-31"
    by_name = {entry["name"]: entry for entry in plan["classes"]}
    assert {name: entry["action"] for name, entry in by_name.items()} == {
        "7-A": "promote", "8-A": "graduate", "12-SAY": "graduate", "TYT Matematik": "archive",
        "Elif özel": "archive"}
    seven = by_name["7-A"]
    assert (seven["newName"], seven["newLevel"], seven["target"], seven["moving"]) == (
        "8-A", {"gradeLevel": 8, "track": None, "examTarget": "LGS"}, "create", 2)
    assert by_name["8-A"]["leaving"] == 1
    assert [item["studentId"] for item in plan["graduates"]] == ["S3", "S4"]
    changes = {item["studentId"]: (item["after"]["gradeLevel"], item["source"])
               for item in plan["levelChanges"]}
    # 7-A üyeleri sınıftan 8; yalnız seviyesiz sınıflardakiler kendi seviyesi +1; mezunlar yok.
    assert changes == {"S1": (8, "class"), "S2": (8, "class"), "S5": (12, "bump"),
                       "S6": (8, "bump")}
    assert plan["counts"] == {"classes": 5, "created": 1, "joined": 0, "archived": 5, "kept": 0,
                              "moved": 2, "graduates": 2, "levelChanges": 4}
    assert await world.groups.revision() == revision
    assert (await world.row(ids["7-A"]))["archived_at"] == ""  # type: ignore[index]
    # bumpOthers kapalı: yalnız sınıftan gelen seviye.
    narrow = await world.rollover.preview({"bumpOthers": False})
    assert {item["studentId"] for item in narrow["levelChanges"]} == {"S1", "S2"}


async def test_pencere_disinda_onizleme_var_uygulama_yok(world: World) -> None:
    world.today[0] = date(2026, 10, 15)
    await school(world)
    plan = await world.rollover.preview({})
    assert (plan["fromYear"], plan["toYear"]) == (OLD, NEW)
    assert plan["effectiveFrom"] == "2026-10-15"
    assert "01.06.2027" in plan["blocked"]
    with pytest.raises(GroupError) as refused:
        await world.rollover.apply({}, actor="Ayşe")
    assert refused.value.status == 409 and refused.value.code == "rollover_blocked"


async def test_ad_cakismasi_ve_var_olan_hedefe_katilma(world: World) -> None:
    ids = await school(world)
    # 8-A dokunulmazsa 7-A'nın önerilen adı canlı 8-A (2026-2027) ile çakışır.
    clash = await world.rollover.preview({"items": [{"groupId": ids["8-A"], "action": "keep"}]})
    assert [item["code"] for item in clash["problems"]] == ["name_taken"]
    assert clash["blocked"] == "Önce sorunları giderin."
    # Yeni dönemde 8-A zaten açılmış (yaz kaydı): 7-A ona KATILIR, yeni sınıf açılmaz.
    ready = await world.make("8-B", [], L8, year=NEW)
    joined = await world.rollover.preview({"items": [{"groupId": ids["7-A"], "action": "promote",
                                                      "name": "8-B"}]})
    entry = next(item for item in joined["classes"] if item["name"] == "7-A")
    assert (entry["target"], entry["targetId"], joined["counts"]["joined"]) == ("join", ready, 1)
    # Seviyesiz sınıf üst seviyeye taşınamaz; iki sınıf aynı ada gidemez.
    bad = await world.rollover.preview({"items": [
        {"groupId": ids["TYT"], "action": "promote"},
        {"groupId": ids["Özel"], "action": "copy", "name": "8-a"}]})
    assert sorted(item["code"] for item in bad["problems"]) == [
        "level_missing", "name_duplicate", "name_duplicate"]
    with pytest.raises(GroupError) as stray:
        await world.rollover.preview({"items": [{"groupId": "yok", "action": "keep"}]})
    assert stray.value.status == 404


# ----------------------------------------------------------- uygula + geri al


async def test_uygula_tek_revizyon_seviye_mezun_ve_geri_al(world: World) -> None:
    ids = await school(world)
    plan = await world.rollover.preview({"keepStudentIds": ["S4"]})
    assert [item["studentId"] for item in plan["graduates"]] == ["S3"]
    assert [item["studentId"] for item in plan["keptGraduates"]] == ["S4"]
    before = await world.groups.revision()
    result = await world.rollover.apply({"keepStudentIds": ["S4"],
                                         "expectedRevision": plan["revision"]}, actor="Ayşe")
    assert result["state"] == "applied" and result["canUndo"] is True
    [pair] = result["pairs"]
    assert (pair["fromName"], pair["toName"], pair["toYear"], pair["students"]) == (
        "7-A", "8-A", NEW, 2)
    new_id = pair["toId"]
    # Sınıf tarafı TEK revizyon (seviye/pasifleştirme öğrenci tarafı, ayrıca).
    created = await world.groups.detail(new_id)
    assert created["academicYear"] == NEW and created["classLevel"] == L8
    assert sorted((m["studentId"], m["validFrom"], m["validTo"]) for m in created["members"]) == [
        ("S1", "2027-09-01", None), ("S2", "2027-09-01", None)]
    old = await world.groups.detail(ids["7-A"])
    assert old["archived"] is True
    assert {m["studentId"]: m["validTo"] for m in old["members"]} == {
        "S1": "2027-08-31", "S2": "2027-08-31", "S7": "2026-12-31"}
    history = await world.groups.history(ids["7-A"])
    assert history[0]["action"] == "rollover"
    # Seviyeler: sınıftan 8 + LGS; seviyesiz sınıftaki 11 → 12; S4 mezun edilmedi ama
    # kendisi 12 → mezun (seviye +1, istisna yalnız pasifleşmeyi durdurur).
    assert await world.level("S1") == L8
    assert (await world.level("S5"))["gradeLevel"] == 12
    assert (await world.level("S4"))["gradeLevel"] == "graduate"
    s3 = await world.profile("S3")
    assert (s3["status"], s3["status_reason"]) == ("inactive", "graduated")
    assert (await world.profile("S4"))["status"] == "active"
    assert (await world.level("S3"))["gradeLevel"] == 8    # mezunun seviyesi değişmez
    overview = await world.rollover.overview()
    assert overview["latest"]["id"] == result["id"] and overview["latest"]["canUndo"] is True
    assert overview["latest"]["graduation"] == {"deactivated": 1, "scheduled": 0, "failed": 0}
    assert await world.groups.revision() > before
    # Aynı plan tekrar: eski sınıflar arşivde → uygulanacak değişiklik yok.
    again = await world.rollover.preview({"fromYear": OLD})
    assert again["classes"] == [] and again["blocked"]

    undone = await world.rollover.undo(result["id"], actor="Ayşe")
    assert undone["state"] == "undone"
    assert undone["undo"]["deletedClasses"] == 1 and undone["undo"]["restoredClasses"] == 5
    assert undone["undo"]["reactivated"] == 1
    assert await world.row(new_id) is None and world.deleted == [new_id]
    restored = await world.groups.detail(ids["7-A"])
    assert restored["archived"] is False
    assert {m["studentId"]: m["validTo"] for m in restored["members"]} == {
        "S1": None, "S2": None, "S7": "2026-12-31"}
    assert await world.level("S1") == L7
    assert (await world.level("S5"))["gradeLevel"] == 11
    assert (await world.profile("S3"))["status"] == "active"
    # Akademi'ye silinen sınıfın mezar taşı artımlı akışta gider.
    delta = await world.groups.list(before, view="classes")
    tomb = next(item for item in delta["groups"] if item["id"] == new_id)
    assert tomb["deleted"] is True
    with pytest.raises(GroupError) as twice:
        await world.rollover.undo(result["id"], actor="Ayşe")
    assert twice.value.code == "rollover_not_applied"


async def test_var_olana_katilma_geri_almada_yalniz_eklenen_uyelik_gider(world: World) -> None:
    ids = await school(world)
    ready = await world.make("8-B", [], L8, year=NEW)
    await world.groups.add_members(ready, ["S5"], actor="Ayşe")   # yaz kaydı
    result = await world.rollover.apply({"items": [
        {"groupId": ids["7-A"], "action": "promote", "name": "8-B"}]}, actor="Ayşe")
    assert result["pairs"][0]["joined"] is True and result["pairs"][0]["toId"] == ready
    joined = await world.groups.detail(ready)
    assert sorted(m["studentId"] for m in joined["members"]) == ["S1", "S2", "S5"]
    await world.rollover.undo(result["id"], actor="Ayşe")
    after = await world.groups.detail(ready)
    assert [m["studentId"] for m in after["members"]] == ["S5"]
    assert await world.row(ready) is not None


async def test_gelecek_gecis_gunu_ve_eski_onizleme(world: World) -> None:
    world.today[0] = date(2027, 8, 20)
    ids = await school(world)
    plan = await world.rollover.preview({})
    assert (plan["effectiveFrom"], plan["closeDay"]) == ("2027-09-01", "2027-08-20")
    # Önizlemeden sonra biri sınıfı değiştirdi: eski önizlemeyle uygulama reddedilir.
    await world.groups.update(ids["TYT"], {"note": "yeni not"}, actor="Ayşe")
    with pytest.raises(GroupError) as stale:
        await world.rollover.apply({"expectedRevision": plan["revision"]}, actor="Ayşe")
    assert stale.value.code == "rollover_stale"
    result = await world.rollover.apply({}, actor="Ayşe")
    new_id = result["pairs"][0]["toId"]
    created = await world.groups.detail(new_id)
    assert {m["validFrom"] for m in created["members"]} == {"2027-09-01"}
    # Mezun ileri tarihli pasifleşir (geçiş günü); bugün hâlâ etkin.
    s3 = await world.profile("S3")
    assert (s3["status"], s3["status_effective_at"]) == ("active", "2027-09-01")
    await world.rollover.undo(result["id"], actor="Ayşe")
    assert (await world.profile("S3"))["status_effective_at"] is None


async def test_yalniz_son_gecis_geri_alinir(world: World) -> None:
    ids = await school(world)
    first = await world.rollover.apply({"items": [
        {"groupId": ids["7-A"], "action": "copy", "name": "7-A 2027"},
        {"groupId": ids["8-A"], "action": "keep"},
        {"groupId": ids["12-SAY"], "action": "keep"},
        {"groupId": ids["TYT"], "action": "keep"},
        {"groupId": ids["Özel"], "action": "keep"}]}, actor="Ayşe")
    second = await world.rollover.apply({"items": [
        {"groupId": ids["8-A"], "action": "archive"},
        {"groupId": ids["12-SAY"], "action": "keep"},
        {"groupId": ids["TYT"], "action": "keep"},
        {"groupId": ids["Özel"], "action": "keep"}]}, actor="Ayşe")
    with pytest.raises(GroupError) as older:
        await world.rollover.undo(first["id"], actor="Ayşe")
    assert older.value.code == "rollover_not_latest"
    overview = await world.rollover.overview()
    assert overview["latest"]["id"] == second["id"]
    assert [item["id"] for item in overview["history"]][:2] == [second["id"], first["id"]]
