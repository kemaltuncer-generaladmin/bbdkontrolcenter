"""SINIFIN DÖNEMİ + arşivden KALICI SİLME (iş 30, göç 011/012).

Kullanıcı (2026-10-14): "tüm mevcut sınıflara da 2026-2027 dönemini ata. ayrıca arşive
ek sil fonksiyonu olsun." Depo GERÇEK SQLite'tır (çekirdek şeması + gerçek outbox +
modülün bütün göçleri; dünya `test_bbd_students_sinif_birlesim`'den); "bugün" 2026-10-14.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import test_bbd_students_sinif_birlesim as birlesim
from bbd_students_backend.academic_year import (
    BACKFILL_YEAR,
    YearBackfill,
    current_year,
    parse_year,
    year_choices,
)
from bbd_students_backend.api import routes
from bbd_students_backend.classes_merge import class_id
from bbd_students_backend.groups import GroupError
from bbd_students_fakes import FakeLog
from test_bbd_students_groups import _declared
from test_bbd_students_sinif_birlesim import World, seed

from km_core.store.dialect import concurrent_index_plan, translate_ddl

MIGRATIONS = Path(__file__).resolve().parents[1] / "backend" / "migrations"
#: Birleşim testlerinin gerçek dünyası (SQLite + outbox) — aynı fikstür.
world = birlesim.world


async def make(world: World, name: str, students: list[str] | None = None, **extra: object) -> dict:
    return await world.groups.create({"name": name, "studentIds": students or [], **extra},
                                     actor="Ayşe")


# --------------------------------------------------------------------- göç


def test_goc_011_012_lehce_cevirmeninden_gecer() -> None:
    translate_ddl((MIGRATIONS / "011_class_year.sql").read_text(encoding="utf-8"))
    plan = concurrent_index_plan((MIGRATIONS / "012_class_year_indexes.sql").read_text(
        encoding="utf-8"))
    assert plan is not None and len(plan) == 2
    assert all("CONCURRENTLY" in statement for _, statement in plan)


# -------------------------------------------------------------------- kural


def test_donem_eylulde_doner_ve_bicim() -> None:
    assert current_year(date(2026, 10, 14)) == "2026-2027"
    assert current_year(date(2027, 8, 31)) == "2026-2027"
    assert current_year(date(2027, 9, 1)) == "2027-2028"
    assert parse_year(" 2026-2027 ") == "2026-2027"
    for bad in ("", "2026", "2026-2028", "2026/2027", "26-27", None):
        with pytest.raises(ValueError):
            parse_year(bad)
    assert year_choices(date(2026, 10, 14), extra=["2019-2020", "bozuk"]) == [
        "2027-2028", "2026-2027", "2025-2026", "2024-2025", "2019-2020"]


# ----------------------------------------------------------- oluştur/düzenle


async def test_yeni_sinif_varsayilan_guncel_donem_ve_secilen_donem(world: World) -> None:
    plain = await make(world, "TYT Matematik")
    chosen = await make(world, "LGS Fen", academicYear="2027-2028")
    assert plain["academicYear"] == "2026-2027"
    assert chosen["academicYear"] == "2027-2028"
    with pytest.raises(GroupError, match="2026-2027"):
        await make(world, "Bozuk", academicYear="2026-2028")


async def test_duzenlemede_donem_degisir_bos_birakilamaz(world: World) -> None:
    group = await make(world, "11-A")
    revision = await world.groups.revision()
    after = await world.groups.update(group["id"], {"academicYear": "2025-2026"}, actor="Ayşe")
    assert after["academicYear"] == "2025-2026"
    assert await world.groups.revision() == revision + 1
    # Aynı dönem: değişiklik yok, revizyon harcanmaz.
    await world.groups.update(group["id"], {"academicYear": "2025-2026"}, actor="Ayşe")
    assert await world.groups.revision() == revision + 1
    with pytest.raises(GroupError):
        await world.groups.update(group["id"], {"academicYear": None}, actor="Ayşe")


async def test_toplu_donem_ata_tek_islem(world: World) -> None:
    first = await make(world, "9-A")
    second = await make(world, "9-B", academicYear="2025-2026")
    third = await make(world, "9-C", academicYear="2025-2026")
    await world.groups.archive(third["id"], actor="Ayşe")
    revision = await world.groups.revision()
    result = await world.groups.set_year([first["id"], second["id"], third["id"], first["id"]],
                                         "2025-2026", actor="Ayşe")
    assert result == {"updated": 1, "skipped": [second["id"], third["id"]],
                      "academicYear": "2025-2026"}
    assert await world.groups.revision() == revision + 1
    # Artımlı akışta yalnız değişen sınıf.
    delta = await world.groups.list(revision, view="classes")
    assert [(item["id"], item["academicYear"]) for item in delta["groups"]] == [
        (first["id"], "2025-2026")]
    history = await world.groups.history(first["id"])
    assert history[0]["action"] == "year_set"
    assert history[0]["detail"] == {"academicYear": "2025-2026", "before": "2026-2027"}
    # Değişmeyen toplu atama revizyon harcamaz; bilinmeyen sınıf 404.
    again = await world.groups.set_year([first["id"]], "2025-2026", actor="Ayşe")
    assert again["updated"] == 0 and await world.groups.revision() == revision + 1
    with pytest.raises(GroupError) as missing:
        await world.groups.set_year(["yok"], "2025-2026", actor="Ayşe")
    assert missing.value.status == 404


async def test_liste_donem_suzgeci_ve_secenekler(world: World) -> None:
    await make(world, "11-A")
    await make(world, "Geçen yıl", academicYear="2025-2026")
    every = await world.groups.overview()
    assert every["currentYear"] == "2026-2027"
    assert every["years"][:4] == ["2027-2028", "2026-2027", "2025-2026", "2024-2025"]
    assert {group["name"]: group["academicYear"] for group in every["groups"]} == {
        "11-A": "2026-2027", "Geçen yıl": "2025-2026"}
    current = await world.groups.overview(year="2026-2027")
    assert [group["name"] for group in current["groups"]] == ["11-A"]
    assert (await world.groups.overview(year="none"))["groups"] == []


async def test_eski_gorunum_donem_tasimaz(world: World) -> None:
    await make(world, "TYT")
    legacy = (await world.groups.list())["groups"][0]
    assert "academicYear" not in legacy
    classes = (await world.groups.list(view="classes"))["groups"][0]
    assert classes["academicYear"] == "2026-2027"


# ------------------------------------------------------- mevcutlara 2026-2027


async def test_mevcut_butun_siniflar_2026_2027_alir_bir_kez(world: World) -> None:
    await seed(world, "STU-1", class_name="8-A")
    await world.merge.run()
    live = await make(world, "TYT")
    archived = await make(world, "Eski", kind="individual")
    await world.groups.archive(archived["id"], actor="Ayşe")
    # Eski sürümün (0.5.13) yazdığı gibi: dönem boş.
    await world.store.execute(f"UPDATE {world.store.table('group')} SET academic_year = NULL")
    before = await world.groups.revision()
    events = len(await world.events("group.changed"))
    backfill = YearBackfill(store=world.store, groups=world.groups, log=FakeLog())
    assert await backfill.run() == 3
    listing = (await world.groups.list(view="classes"))["groups"]
    assert {item["academicYear"] for item in listing} == {BACKFILL_YEAR}
    assert await world.groups.revision() == before + 1
    assert len(await world.events("group.changed")) == events + 1
    # Artımlı akış (Akademi) hepsini görür; arşivli ve dönüşen sınıf dahil.
    delta = await world.groups.list(before, view="classes")
    assert {item["id"] for item in delta["groups"]} == {
        live["id"], archived["id"], class_id("8-A")}
    # İkinci koşu: yapılacak yok, revizyon harcanmaz.
    assert await backfill.run() == 0
    assert await world.groups.revision() == before + 1


# ------------------------------------------------------- arşivden kalıcı sil


async def test_kalici_silme_yalniz_arsivde_ad_ve_gerekce_ister(world: World) -> None:
    group = await make(world, "Deneme Sınıfı", ["STU-1"])
    revision = await world.groups.revision()
    with pytest.raises(GroupError) as live:
        await world.groups.delete(group["id"], confirm_name="Deneme Sınıfı", reason="yanlış açıldı",
                                  actor="Yönetici")
    assert live.value.status == 409 and live.value.code == "group_not_archived"
    await world.groups.archive(group["id"], actor="Ayşe")
    revision = await world.groups.revision()
    with pytest.raises(GroupError) as wrong:
        await world.groups.delete(group["id"], confirm_name="Deneme", reason="yanlış açıldı",
                                  actor="Yönetici")
    assert wrong.value.code == "confirm_mismatch"
    with pytest.raises(GroupError) as why:
        await world.groups.delete(group["id"], confirm_name="deneme sınıfı", reason=" ",
                                  actor="Yönetici")
    assert why.value.code == "reason_required"
    assert await world.groups.revision() == revision        # reddedilen silme revizyon harcamaz
    assert await world.groups.get(group["id"]) is not None


async def test_kalici_silme_uyelik_mezar_tasi_denetim_ve_olay(world: World) -> None:
    deleted: list[tuple[str, int]] = []

    async def on_deleted(group_id: str, revision: int) -> None:
        deleted.append((group_id, revision))

    world.groups._deleted = on_deleted
    keep = await make(world, "Kalan", ["STU-1"])
    group = await make(world, "İptal Sınıf", ["STU-1", "STU-2"])
    await world.groups.archive(group["id"], actor="Ayşe")
    before = await world.groups.revision()
    events = len(await world.events("group.changed"))
    result = await world.groups.delete(group["id"], confirm_name="iptal sınıf",
                                       reason="Yanlış açılmıştı", actor="Yönetici")
    assert result["deleted"] is True and result["removedMemberships"] == 2
    assert result["students"] == 2 and result["revision"] == before + 1
    assert deleted == [(group["id"], before + 1)]
    assert await world.groups.get(group["id"]) is None
    with pytest.raises(GroupError):
        await world.groups.detail(group["id"])
    members = await world.store.fetch_all(
        f"SELECT * FROM {world.store.table('group_member')} WHERE group_id = ?", (group["id"],))
    assert members == []
    assert [item["group"]["id"] for item in await world.groups.for_student("STU-1")] == [keep["id"]]
    # Denetim izi kalır (geçmiş satırlar + silme kaydı).
    log = await world.store.fetch_all(
        f"SELECT action, detail FROM {world.store.table('group_log')} WHERE group_id = ? "
        f"ORDER BY id", (group["id"],))
    assert [row["action"] for row in log] == ["create", "archive", "delete"]
    assert "Yanlış açılmıştı" in str(log[-1]["detail"])
    assert len(await world.events("group.changed")) == events + 1
    # Artımlı akış: yalnız `classes` görünümünde mezar taşı; tam listede ve eski görünümde yok.
    delta = await world.groups.list(before, view="classes")
    assert delta["groups"] == [{"id": group["id"], "deleted": True,
                                "deletedAt": delta["groups"][0]["deletedAt"],
                                "revision": before + 1}]
    assert delta["groups"][0]["deletedAt"].endswith("+03:00")
    assert (await world.groups.list(before))["groups"] == []
    full = await world.groups.list(view="classes")
    assert [item["id"] for item in full["groups"]] == [keep["id"]]
    # Ad yeniden kullanılabilir.
    again = await make(world, "İptal Sınıf")
    assert again["id"] != group["id"]


async def test_silinen_donusmus_sinif_zorla_birlestirmede_geri_gelmez(world: World) -> None:
    await seed(world, "STU-1", class_name="7-C")
    await world.merge.run()
    cid = class_id("7-C")
    await world.groups.archive(cid, actor="Ayşe")
    await world.groups.delete(cid, confirm_name="7-C", reason="kapandı", actor="Yönetici")
    forced = await world.merge.run(force=True)
    assert forced["classes"] == 0
    assert await world.groups.get(cid) is None


# ------------------------------------------------------------- izin kapıları


def test_yeni_uclar_izin_ilan_eder() -> None:
    found = {}
    for route in routes.router.routes:
        for method in route.methods:  # type: ignore[attr-defined]
            found[(method, route.path)] = route  # type: ignore[attr-defined]
    assert _declared(found[("DELETE", "/groups/{group_id}")]) == {"bbd_students.groups_delete"}
    assert _declared(found[("POST", "/groups/academic-year")]) == {"bbd_students.groups"}
