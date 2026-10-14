"""SINIF = GRUP (iş 23, göç 009) — birleştirme, iki görünüm, türetilmiş alanlar, seviye.

Depo GERÇEK SQLite'tır (çekirdek şeması + gerçek outbox + modülün bütün göçleri);
kantin sahtedir. "Bugün" 2026-10-14'e sabitlenir (akademik yıl başı 2026-09-01).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from bbd_students_backend.classes import ClassLink
from bbd_students_backend.classes_merge import ClassMerge, academic_year_start, class_id
from bbd_students_backend.directory import StudentDirectory
from bbd_students_backend.groups import GroupError, GroupService
from bbd_students_backend.service import StudentService
from bbd_students_backend.sync import StudentSync
from bbd_students_fakes import FakeLog

from km_core.contracts.module import ScopedStore
from km_core.store.bootstrap import build_schema
from km_core.store.db import Store
from km_core.store.dialect import concurrent_index_plan, translate_ddl
from km_platform.events.outbox import EventOutbox

MIGRATIONS = Path(__file__).resolve().parents[1] / "backend" / "migrations"
TODAY = date(2026, 10, 14)


class Canteen:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def add(self, opaque_id: str, name: str) -> None:
        self.rows[opaque_id] = {"id": opaque_id, "displayName": name, "parentPhone": "",
                                "balance": 0, "spendingLimit": None, "isBlocked": False,
                                "updatedAt": 1791786600000}

    async def students(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.rows.values()]

    async def student(self, opaque_id: str) -> dict[str, Any]:
        return dict(self.rows[opaque_id])

    async def get_student(self, opaque_id: str) -> dict[str, Any]:
        return await self.student(opaque_id)

    async def dashboard(self) -> dict[str, Any]:
        return {}


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
    link: ClassLink
    merge: ClassMerge
    published: list[int]

    async def events(self, kind: str = "student.updated") -> list[dict[str, Any]]:
        return [event for event in await self.outbox.read_after(0, 2000)
                if event["type"] == kind]

    async def profile(self, kantin_id: str) -> dict[str, Any]:
        row = await self.store.fetch_one(
            f"SELECT * FROM {self.store.table('profile')} WHERE kantin_id = ?", (kantin_id,))
        assert row is not None
        return dict(row)


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
    clock = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
    directory = StudentDirectory(canteen=canteen, store=store, log=FakeLog())
    sync = StudentSync(store=store, canteen=canteen, log=FakeLog(), outbox=lambda: outbox,
                       on_change=directory.invalidate, clock=lambda: clock,
                       today=lambda: TODAY, delay=0)
    service = StudentService(canteen=canteen, store=store, log=FakeLog(),
                             on_change=directory.invalidate, sync=sync, directory=directory)
    link = ClassLink(store=store, sync=sync, log=FakeLog(), today=lambda: TODAY)
    published: list[int] = []

    async def publish(revision: int) -> None:
        published.append(revision)

    groups = GroupService(store=store, log=FakeLog(), publish=publish, outbox=lambda: outbox,
                          today=lambda: TODAY, students_changed=link.refresh,
                          apply_level=link.apply_level)
    service.attach_groups(groups)
    merge = ClassMerge(store=store, groups=groups, log=FakeLog(), today=lambda: TODAY)
    yield World(raw, store, outbox, canteen, directory, sync, service, groups, link, merge,
                published)
    await outbox.close()
    await raw.close()


async def seed(world: World, kantin_id: str, *, class_name: str = "", status: str = "active",
               **columns: Any) -> None:
    """Birleştirme öncesi profil (doğrudan tablo; eski sürümün yazdığı gibi)."""
    world.canteen.add(kantin_id, f"Öğrenci {kantin_id}")
    values = {"kantin_id": kantin_id, "class_name": class_name, "status": status,
              "updated_at": "2026-10-01T00:00:00+00:00", **columns}
    names = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    await world.store.execute(
        f"INSERT INTO {world.store.table('profile')} ({names}) VALUES ({marks})",
        tuple(values.values()))


async def define(world: World, name: str, grade: str, track: str | None = None,
                 exam: str | None = None) -> None:
    """Göç 008 sınıf tanımı (birleştirme öncesi)."""
    await world.store.execute(
        f"INSERT INTO {world.store.table('class')} (name, grade_level, track, exam_target, "
        f"created_at, updated_at) VALUES (?, ?, ?, ?, 'x', 'x')", (name, grade, track, exam))


# ---------------------------------------------------------------------- göç


def test_goc_lehce_cevirmeninden_gecer() -> None:
    translate_ddl((MIGRATIONS / "009_class_merge.sql").read_text(encoding="utf-8"))
    plan = concurrent_index_plan((MIGRATIONS / "010_class_merge_indexes.sql").read_text(
        encoding="utf-8"))
    assert plan is not None and len(plan) == 2


def test_akademik_yil_basi() -> None:
    assert academic_year_start(date(2026, 10, 14)) == date(2026, 9, 1)
    assert academic_year_start(date(2027, 3, 1)) == date(2026, 9, 1)
    assert academic_year_start(date(2026, 9, 1)) == date(2026, 9, 1)


# ------------------------------------------------------------- birleştirme


async def test_birlestirme_her_sinif_adi_sinif_kaydi_uyelik_seviye(world: World) -> None:
    await define(world, "11-A", "11", "SAY", "YKS")
    await define(world, "Boş tanım", "8", None, "LGS")          # öğrencisiz sınıf tanımı
    await seed(world, "STU-1", class_name="11-A", grade_level="10")   # eski kendi değeri
    await seed(world, "STU-2", class_name="11-A", level_override=1, track="EA")
    await seed(world, "STU-3", class_name="8-B", grade_level="8", exam_target="LGS")
    await seed(world, "STU-4", class_name="8-B", status="inactive")
    await seed(world, "STU-5")                                   # sınıfsız

    result = await world.merge.run()
    assert result == {"skipped": False, "classes": 3, "renamed": 0, "members": 3,
                      "levels": 2, "students": 3}

    listing = await world.groups.list(view="classes")
    by_name = {group["name"]: group for group in listing["groups"]}
    assert set(by_name) == {"11-A", "8-B", "Boş tanım"}
    eleven = by_name["11-A"]
    assert eleven["id"] == class_id("11-A")
    assert eleven["legacyClassName"] == "11-A" and eleven["origin"] == "class"
    assert eleven["classLevel"] == {"gradeLevel": 11, "track": "SAY", "examTarget": "YKS"}
    assert sorted((m["studentId"], m["validFrom"], m["validTo"]) for m in eleven["members"]) == [
        ("STU-1", "2026-09-01", None), ("STU-2", "2026-09-01", None)]
    # Pasif öğrenciye üyelik açılmaz; className'i öğrencide kalır.
    assert [m["studentId"] for m in by_name["8-B"]["members"]] == ["STU-3"]
    assert (await world.profile("STU-4"))["class_name"] == "8-B"

    # SEVİYE ÖĞRENCİDE: geçerli değer (sınıftan; istisnada kendi alanı) öğrenciye yazıldı.
    one, two = await world.profile("STU-1"), await world.profile("STU-2")
    assert (one["grade_level"], one["track"], one["exam_target"]) == ("11", "SAY", "YKS")
    assert (two["grade_level"], two["track"], two["exam_target"]) == ("11", "EA", "YKS")
    assert one["class_ids"] == class_id("11-A")
    # Geçerli değer değişmediği için öğrenci olayı yok; grup olayı var.
    assert await world.events() == []
    assert len(await world.events("group.changed")) == 1
    assert world.published == [listing["revision"]]


async def test_birlestirme_idempotent_ve_es_zamanli_ikinci_kosu_atlar(world: World) -> None:
    await seed(world, "STU-1", class_name="9-A")
    first = await world.merge.run()
    revision = await world.groups.revision()
    again = await world.merge.run()
    assert again["skipped"] is True and again["classes"] == first["classes"]
    assert await world.groups.revision() == revision          # revizyon harcanmadı
    # force: eksik olanı ekler, var olana dokunmaz.
    await seed(world, "STU-2", class_name="9-A")
    forced = await world.merge.run(force=True)
    assert forced["classes"] == 0 and forced["members"] == 1
    assert (await world.merge.status())["state"] == "done"


async def test_ayni_adli_canli_grup_varsa_ayri_kayit_ayirt_edici_adla(world: World) -> None:
    old = await world.groups.create({"name": "tyt", "studentIds": ["STU-9"]}, actor="Ayşe")
    await seed(world, "STU-1", class_name="TYT")
    result = await world.merge.run()
    assert result["renamed"] == 1
    listing = (await world.groups.list(view="classes"))["groups"]
    names = sorted(group["name"] for group in listing)
    assert names == ["TYT (sınıf)", "tyt"]
    renamed = next(group for group in listing if group["name"] == "TYT (sınıf)")
    assert renamed["legacyClassName"] == "TYT"
    # Eski grubun kimliği ve üyeleri değişmedi.
    kept = next(group for group in listing if group["id"] == old["id"])
    assert kept["legacyClassName"] is None and kept["origin"] == "group"


async def test_eski_gorunum_donusen_siniflari_gizler(world: World) -> None:
    await seed(world, "STU-1", class_name="12-A")
    group = await world.groups.create({"name": "TYT Hızlandırma", "studentIds": ["STU-1"]},
                                      actor="Ayşe")
    await world.merge.run()
    legacy = await world.groups.list()
    assert [item["id"] for item in legacy["groups"]] == [group["id"]]
    assert set(legacy["groups"][0]) == {"id", "name", "callName", "kind", "archived",
                                        "archivedAt", "updatedAt", "revision", "members"}
    full = await world.groups.list(view="classes")
    assert {item["name"] for item in full["groups"]} == {"TYT Hızlandırma", "12-A"}
    # Artımlı okuma da süzülür.
    since = await world.groups.list(0)
    assert [item["id"] for item in since["groups"]] == [group["id"]]
    with pytest.raises(GroupError):
        await world.groups.list(view="hepsi")


async def test_geri_alma_donusumu_kaldirir_seviyeyi_dondurur(world: World) -> None:
    await define(world, "11-A", "11", "SAY", "YKS")
    await seed(world, "STU-1", class_name="11-A", grade_level="10")
    group = await world.groups.create({"name": "Etüt", "studentIds": ["STU-1"]}, actor="Ayşe")
    await world.merge.run()
    summary = await world.merge.rollback(actor="Yönetici")
    assert summary == {"classes": 1, "members": 1, "levels": 1}
    listing = (await world.groups.list(view="classes"))["groups"]
    assert [item["id"] for item in listing] == [group["id"]]
    profile = await world.profile("STU-1")
    assert (profile["grade_level"], profile["track"], profile["class_ids"]) == ("10", None, "")
    assert profile["class_name"] == "11-A"
    assert (await world.merge.status())["state"] == "rolled_back"
    # Geri alınmış dönüşüm kendiliğinden yeniden koşmaz.
    assert (await world.merge.run())["skipped"] is True


# --------------------------------------------- türetilmiş alanlar + olaylar


async def test_uyelik_degisince_class_ids_eski_ad_ve_olay(world: World) -> None:
    await seed(world, "STU-1", class_name="8-A")
    await seed(world, "STU-2", class_name="8-B")
    await world.merge.run()
    a, b = class_id("8-A"), class_id("8-B")

    # 8-A'dan 8-B'ye taşı (bugünden): eski ad değişir, sınıflar değişir.
    await world.groups.move(["STU-1"], from_group=a, to_group=b, actor="Ayşe",
                            reason="Şube değişti")
    profile = await world.profile("STU-1")
    assert profile["class_ids"] == b and profile["class_name"] == "8-B"
    events = await world.events()
    assert [(e["subject"]["id"], sorted(e["fields"])) for e in events] == [
        ("STU-1", ["className", "classes"])]

    # Sonradan açılan sınıfa ekleme: sınıflar çoğalır, eski ad (legacy) aynı kalır.
    tyt = await world.groups.create({"name": "TYT", "studentIds": ["STU-1"]}, actor="Ayşe")
    profile = await world.profile("STU-1")
    assert profile["class_ids"] == ",".join(sorted([b, tyt["id"]]))
    assert profile["class_name"] == "8-B"
    assert sorted((await world.events())[-1]["fields"]) == ["classes"]

    # Dizin: classes ada göre; className eski tekil ad.
    record = await world.directory.get("STU-1")
    assert record is not None
    assert record["classes"] == [{"id": b, "name": "8-B"}, {"id": tyt["id"], "name": "TYT"}]
    assert record["className"] == "8-B"

    # Ad değişince üyeler yine olay alır (dizindeki ad değişti).
    before = len(await world.events())
    await world.groups.update(tyt["id"], {"name": "TYT Mat"}, actor="Ayşe")
    assert len(await world.events()) == before + 1

    # Arşiv: sınıf öğrencinin bugünkü sınıflarından düşer.
    await world.groups.archive(tyt["id"], actor="Ayşe")
    assert (await world.profile("STU-1"))["class_ids"] == b


async def test_eski_ad_ad_sirasiyla_ilk_dönusen_sinif(world: World) -> None:
    await seed(world, "STU-1", class_name="9-B")
    await seed(world, "STU-2", class_name="9-A")
    await world.merge.run()
    await world.groups.add_members(class_id("9-A"), ["STU-1"], actor="Ayşe")
    profile = await world.profile("STU-1")
    assert profile["class_name"] == "9-A"            # "9-A" < "9-B"


async def test_gunluk_uzlastirma_ileri_tarihli_uyelik(world: World) -> None:
    await seed(world, "STU-1")
    group = await world.groups.create({"name": "LGS", "studentIds": ["STU-1"],
                                       "validFrom": "2026-10-20"}, actor="Ayşe")
    assert (await world.profile("STU-1"))["class_ids"] == ""
    later = ClassLink(store=world.store, sync=world.sync, log=FakeLog(),
                      today=lambda: date(2026, 10, 20))
    assert await later.reconcile_all() == 1
    assert (await world.profile("STU-1"))["class_ids"] == group["id"]
    assert await later.reconcile_all() == 0               # aynı gün ikinci kez koşmaz


# ------------------------------------------------------------------ seviye


async def test_sinif_seviyesi_uyeye_yazilir_istisna_korunur(world: World) -> None:
    await seed(world, "STU-1")
    await seed(world, "STU-2", level_override=1, track="EA", exam_target="YKS")
    await world.merge.run()
    group = await world.groups.create(
        {"name": "11-SAY", "level": {"gradeLevel": 11, "track": "SAY", "examTarget": "YKS"},
         "studentIds": ["STU-1", "STU-2"]}, actor="Ayşe")
    one, two = await world.profile("STU-1"), await world.profile("STU-2")
    assert (one["grade_level"], one["track"], one["exam_target"]) == ("11", "SAY", "YKS")
    assert (two["grade_level"], two["track"], two["exam_target"]) == ("11", "EA", "YKS")

    # Seviye değişince bugünkü üyeler güncellenir; applyLevel=False dokunmaz.
    await world.groups.update(group["id"], {"level": {"gradeLevel": 12, "track": "SAY",
                                                      "examTarget": "YKS"}}, actor="Ayşe")
    assert (await world.profile("STU-1"))["grade_level"] == "12"
    await world.groups.update(group["id"], {"level": {"gradeLevel": 9}, "applyLevel": False},
                              actor="Ayşe")
    assert (await world.profile("STU-1"))["grade_level"] == "12"
    applied = await world.groups.apply_level_now(group["id"], actor="Ayşe")
    assert applied["updatedStudents"] == 2
    one = await world.profile("STU-1")
    assert (one["grade_level"], one["track"]) == ("9", None)       # 10 altında alan yok

    with pytest.raises(GroupError):
        await world.groups.update(group["id"], {"level": {"track": "SAY"}}, actor="Ayşe")


async def test_birlesmeden_sonra_ogrenci_seviyesi_kartta_yazilir(world: World) -> None:
    await define(world, "11-A", "11", "SAY", "YKS")
    await seed(world, "STU-1", class_name="11-A")
    await world.merge.run()
    # Eskiden "Seviye sınıftan gelir" diye reddedilirdi; artık öğrencinin kendi alanı.
    await world.service.update_student("STU-1", {"gradeLevel": 12}, actor="Ayşe")
    record = await world.directory.get("STU-1")
    assert record is not None and record["gradeLevel"] == 12
    assert record["classLevel"] == {"gradeLevel": 11, "track": "SAY", "examTarget": "YKS"}
    student = await world.service.get_student("STU-1")
    assert student is not None
    assert student["gradeLevel"] == 12 and student["levelSource"] == "student"
    assert student["classNames"] == ["11-A"] and student["className"] == "11-A"


async def test_toplu_sinifa_ata_birlesmeden_sonra_uyelik_ekler(world: World) -> None:
    await seed(world, "STU-1", class_name="8-A")
    await seed(world, "STU-2")
    await world.merge.run()
    result = await world.service.set_class(["STU-1", "STU-2"], "tyt hızlandırma", actor="Ayşe")
    assert result["updated"] == ["STU-1", "STU-2"]
    created = next(group for group in (await world.groups.overview())["groups"]
                   if group["name"] == "tyt hızlandırma")
    assert created["memberCount"] == 2
    # Ana sınıf yok: 8-A üyeliği sürer.
    assert set((await world.profile("STU-1"))["class_ids"].split(",")) == {
        class_id("8-A"), created["id"]}
    # Var olan sınıfa ikinci ekleme atlanır, yeni sınıf açılmaz.
    again = await world.service.set_class(["STU-1"], "TYT HIZLANDIRMA", actor="Ayşe")
    assert again["skipped"] == ["STU-1"]
    with pytest.raises(ValueError):
        await world.service.set_class(["STU-1"], "  ", actor="Ayşe")


async def test_birlesmeden_once_eski_davranis_surer(world: World) -> None:
    await define(world, "11-A", "11", "SAY", "YKS")
    await seed(world, "STU-1", class_name="11-A", grade_level="10")
    record = await world.directory.get("STU-1")
    assert record is not None
    assert record["gradeLevel"] == 11 and record["className"] == "11-A"
    assert record["classes"] == []
    listing = await world.service.list_classes()
    assert [item["name"] for item in listing["items"]] == ["11-A"]

