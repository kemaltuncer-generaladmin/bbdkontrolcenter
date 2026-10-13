"""Öğrenci grupları ve tarihli üyelik — `bbd_students.groups` (sözleşme §2).

Depo GERÇEK SQLite'tır ve modülün bütün göçleri (003 grup tabloları, 004
işlemsiz indeksler dahil) uygulanır. "Bugün" sabitlenir: tarih kuralları
saatin ne gösterdiğine bağlı kalmasın.
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from bbd_students_backend.api import routes
from bbd_students_backend.groups import GroupError, GroupService, StudentGroups, fold
from bbd_students_fakes import FakeLog

from km_core.contracts.module import ScopedStore
from km_core.store.db import Store
from km_core.store.dialect import concurrent_index_plan, translate_ddl

MIGRATIONS = Path(__file__).resolve().parents[1] / "backend" / "migrations"
TODAY = date(2026, 10, 12)


class Published:
    def __init__(self, *, fail: bool = False) -> None:
        self.revisions: list[int] = []
        self.fail = fail

    async def __call__(self, revision: int) -> None:
        self.revisions.append(revision)
        if self.fail:
            raise RuntimeError("olay yolu düştü")


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[ScopedStore]:
    raw = Store(tmp_path / "km.sqlite")
    await raw.open()
    for path in sorted(MIGRATIONS.glob("*.sql")):
        await raw.apply_migration("bbd_students", path.name, path.read_text(encoding="utf-8"))
    yield ScopedStore(raw, "bbd_students")
    await raw.close()


def build(store: ScopedStore, *, publish: Published | None = None,
          today: date = TODAY) -> tuple[GroupService, Published]:
    published = publish or Published()
    return GroupService(store=store, log=FakeLog(), publish=published,
                        today=lambda: today), published


async def make(service: GroupService, name: str, students: list[str] | None = None,
               **extra: Any) -> dict[str, Any]:
    return await service.create({"name": name, "studentIds": students or [], **extra},
                                actor="Ayşe")


# --------------------------------------------------------------------- göç


def test_goc_lehce_cevirmeninden_gecer() -> None:
    """003 PostgreSQL'e çevrilebilir; 004 yalnız CONCURRENTLY indeks içerir."""
    translate_ddl((MIGRATIONS / "003_groups.sql").read_text(encoding="utf-8"))
    plan = concurrent_index_plan((MIGRATIONS / "004_group_indexes.sql").read_text(
        encoding="utf-8"))
    assert plan is not None and len(plan) == 6
    assert all("CONCURRENTLY" in statement for _, statement in plan)


# -------------------------------------------------------------------- ad


def test_fold_turkce_i() -> None:
    assert fold("İlayda") == fold("ilayda")
    assert fold("IŞIK") == fold("ışık")
    assert fold("  TYT   Grubu ") == fold("tyt grubu")


async def test_ad_tekilligi_turkce_katlamayla(store: ScopedStore) -> None:
    service, _ = build(store)
    await make(service, "İlayda", kind="individual")
    with pytest.raises(GroupError) as caught:
        await make(service, "ilayda")
    assert caught.value.code == "group_name_taken"
    # Arşivlenmiş grubun adı yeniden kullanılabilir (tekillik canlılar arasında).
    first = (await service.overview())["groups"][0]
    await service.archive(first["id"], actor="Ayşe")
    again = await make(service, "ILAYDA".replace("I", "İ", 1))
    assert again["name"] == "İLAYDA"
    # ...ama arşivden geri almak çakışırsa reddedilir.
    with pytest.raises(GroupError):
        await service.restore(first["id"], actor="Ayşe")


async def test_bos_ad_ve_yanlis_tur_reddedilir(store: ScopedStore) -> None:
    service, _ = build(store)
    with pytest.raises(GroupError):
        await make(service, "   ")
    with pytest.raises(GroupError):
        await make(service, "TYT", kind="ozel")


# -------------------------------------------------------------- üyelik


async def test_grup_ad_ve_ogrenci_secimiyle_acilir(store: ScopedStore) -> None:
    service, published = build(store)
    group = await make(service, "TYT Hızlandırma", ["STU-A", "STU-B"], callName="TYT grubu")
    assert group["kind"] == "group"
    assert [m["studentId"] for m in group["current"]] == ["STU-A", "STU-B"]
    assert all(m["validFrom"] == "2026-10-12" and m["validTo"] is None
               for m in group["members"])
    assert published.revisions == [1]


async def test_ayni_ogrenci_kesisen_iki_uyelik_tasiyamaz(store: ScopedStore) -> None:
    service, _ = build(store)
    group = await make(service, "TYT", ["STU-A"])
    # Zaten üye: toplu eklemede atlanır, hata değil.
    result = await service.add_members(group["id"], ["STU-A", "STU-B"], actor="Ayşe")
    assert result["added"] == 1 and result["skipped"] == ["STU-A"]

    # Çıkarılıp sonra yeniden eklenebilir — aralıklar kesişmediği sürece.
    await service.end_members(group["id"], ["STU-A"], valid_to="2026-10-20", actor="Ayşe")
    again = await service.add_members(group["id"], ["STU-A"], valid_from="2026-10-21",
                                      actor="Ayşe")
    assert again["added"] == 1
    rows = [m for m in again["group"]["members"] if m["studentId"] == "STU-A"]
    assert [(m["validFrom"], m["validTo"]) for m in rows] == [
        ("2026-10-12", "2026-10-20"), ("2026-10-21", None)]

    # Düzeltme kesişme doğurursa reddedilir.
    first = next(m for m in rows if m["validTo"] == "2026-10-20")
    member_id = next(m["id"] for m in again["group"]["members"]
                     if m["studentId"] == "STU-A" and m["validTo"] == first["validTo"])
    with pytest.raises(GroupError) as caught:
        await service.patch_member(group["id"], member_id, {"validTo": "2026-10-25"},
                                   actor="Ayşe")
    assert caught.value.code == "membership_overlap"


async def test_bireysel_grupta_tek_uye(store: ScopedStore) -> None:
    service, _ = build(store)
    with pytest.raises(GroupError) as caught:
        await make(service, "İlayda", ["STU-A", "STU-B"], kind="individual")
    assert caught.value.code == "individual_single_member"

    solo = await make(service, "İlayda", ["STU-A"], kind="individual")
    with pytest.raises(GroupError):
        await service.add_members(solo["id"], ["STU-B"], actor="Ayşe")
    # Önceki öğrenci bittikten sonra başka öğrenci başlayabilir.
    await service.end_members(solo["id"], ["STU-A"], valid_to="2026-10-15", actor="Ayşe")
    ok = await service.add_members(solo["id"], ["STU-B"], valid_from="2026-10-16",
                                   actor="Ayşe")
    assert ok["added"] == 1

    # Çok üyeli toplu grup bireysele çevrilemez.
    many = await make(service, "TYT", ["STU-C", "STU-D"])
    with pytest.raises(GroupError):
        await service.update(many["id"], {"kind": "individual"}, actor="Ayşe")


async def test_gecmis_tarihli_degisiklik_gerekce_ister(store: ScopedStore) -> None:
    service, _ = build(store)
    group = await make(service, "TYT", ["STU-A"])
    with pytest.raises(GroupError) as caught:
        await service.add_members(group["id"], ["STU-B"], valid_from="2026-10-01",
                                  actor="Ayşe")
    assert caught.value.code == "reason_required"
    ok = await service.add_members(group["id"], ["STU-B"], valid_from="2026-10-01",
                                   reason="kayıt geç girildi", actor="Ayşe")
    assert ok["added"] == 1

    member = next(m for m in ok["group"]["members"] if m["studentId"] == "STU-A")
    with pytest.raises(GroupError):
        await service.patch_member(group["id"], member["id"], {"validFrom": "2026-09-15"},
                                   actor="Ayşe")
    fixed = await service.patch_member(group["id"], member["id"],
                                       {"validFrom": "2026-09-15", "reason": "yanlış gün"},
                                       actor="Ayşe")
    row = next(m for m in fixed["members"] if m["id"] == member["id"])
    assert row["validFrom"] == "2026-09-15" and row["reason"] == "yanlış gün"
    history = await service.history(group["id"])
    assert history[0]["action"] == "member_fix"
    assert history[0]["detail"]["reason"] == "yanlış gün"


async def test_cikarma_varsayilani_bugun_son_gun(store: ScopedStore) -> None:
    service, _ = build(store)
    group = await make(service, "TYT", ["STU-A", "STU-B"])
    result = await service.end_members(group["id"], ["STU-A", "STU-X"], actor="Ayşe")
    assert result["ended"] == 1 and result["skipped"] == ["STU-X"]
    row = next(m for m in result["group"]["members"] if m["studentId"] == "STU-A")
    assert row["validTo"] == "2026-10-12"
    # Bugün hâlâ üye (son gün dahil); yarın değil.
    tomorrow = await service.detail(group["id"], on=date(2026, 10, 13))
    assert [m["studentId"] for m in tomorrow["current"]] == ["STU-B"]


async def test_tasi_tek_islemde_iki_tarafi_yazar(store: ScopedStore) -> None:
    service, published = build(store)
    tyt = await make(service, "TYT", ["STU-A", "STU-B"], validFrom="2026-09-01",
                     reason="dönem başı")
    ayt = await make(service, "AYT", ["STU-C"])
    before = list(published.revisions)
    result = await service.move(["STU-A"], from_group=tyt["id"], to_group=ayt["id"],
                                effective_from="2026-10-20", actor="Ayşe")
    source = next(m for m in result["from"]["members"] if m["studentId"] == "STU-A")
    target = next(m for m in result["to"]["members"] if m["studentId"] == "STU-A")
    assert source["validTo"] == "2026-10-19"
    assert target["validFrom"] == "2026-10-20" and target["validTo"] is None
    # Tek revizyon, tek olay.
    assert published.revisions == [*before, before[-1] + 1]
    assert result["from"]["revision"] == result["to"]["revision"] == before[-1] + 1

    # Hedef kuralı tutmazsa kaynak da DEĞİŞMEZ.
    solo = await make(service, "İlayda", ["STU-Z"], kind="individual")
    with pytest.raises(GroupError):
        await service.move(["STU-B"], from_group=tyt["id"], to_group=solo["id"],
                           effective_from="2026-10-20", actor="Ayşe")
    still = await service.detail(tyt["id"], on=date(2026, 10, 25))
    assert "STU-B" in [m["studentId"] for m in still["current"]]


async def test_arsiv_acik_uyelikleri_bugun_kapatir(store: ScopedStore) -> None:
    service, _ = build(store)
    group = await make(service, "LGS", ["STU-A"])
    await service.add_members(group["id"], ["STU-B"], valid_from="2026-11-01", actor="Ayşe")
    await service.end_members(group["id"], ["STU-A"], valid_to="2026-10-30", actor="Ayşe")
    archived = await service.archive(group["id"], actor="Ayşe")
    assert archived["archived"] is True and archived["archivedAt"]
    rows = {m["studentId"]: m for m in archived["members"]}
    assert rows["STU-A"]["validTo"] == "2026-10-12"
    # Başlamamış üyelik hiç yürürlüğe girmedi: kaldırılır, denetimde yazar.
    assert "STU-B" not in rows
    assert (await service.history(group["id"]))[0]["detail"]["droppedFuture"] == 1
    # Arşivlenmiş gruba üye eklenmez; geri alınca eklenir.
    with pytest.raises(GroupError) as caught:
        await service.add_members(group["id"], ["STU-C"], actor="Ayşe")
    assert caught.value.code == "group_archived"
    restored = await service.restore(group["id"], actor="Ayşe")
    assert restored["archived"] is False


async def test_siniftan_grup_kopya_olusturur(store: ScopedStore) -> None:
    service, _ = build(store)
    for kantin_id, class_name in (("STU-A", "12-SAY"), ("STU-B", "12-SAY"), ("STU-C", "11-EA")):
        await store.execute(
            "INSERT INTO mod_bbd_students_profile (kantin_id, class_name, updated_at) "
            "VALUES (?, ?, ?)", (kantin_id, class_name, "2026-10-11T00:00:00+00:00"))
    group = await service.from_class("12-SAY", {}, actor="Ayşe")
    assert group["name"] == "12-SAY"
    assert sorted(m["studentId"] for m in group["current"]) == ["STU-A", "STU-B"]
    with pytest.raises(GroupError):
        await service.from_class("9-Z", {"name": "Boş"}, actor="Ayşe")


# ------------------------------------------------------- revizyon ve olay


async def test_revizyon_tekduze_ve_since_artimli(store: ScopedStore) -> None:
    service, published = build(store)
    tyt = await make(service, "TYT", ["STU-A"])
    lgs = await make(service, "LGS")
    full = await service.list()
    assert full["full"] is True and full["complete"] is True
    assert full["revision"] == 2
    assert {g["name"] for g in full["groups"]} == {"TYT", "LGS"}

    cursor = full["revision"]
    assert (await service.list(cursor))["groups"] == []
    # Üyelik değişince grubun revizyonu da yükselir.
    await service.add_members(lgs["id"], ["STU-B"], actor="Ayşe")
    delta = await service.list(cursor)
    assert delta["full"] is False
    assert [g["id"] for g in delta["groups"]] == [lgs["id"]]
    assert delta["groups"][0]["members"] == [
        {"studentId": "STU-B", "validFrom": "2026-10-12", "validTo": None}]
    assert delta["revision"] == 3
    # Değişmeyen yazma revizyon tüketmez.
    await service.update(tyt["id"], {"name": "TYT"}, actor="Ayşe")
    assert await service.revision() == 3
    assert published.revisions == [1, 2, 3]


async def test_revizyon_yeniden_baslatinca_korunur(store: ScopedStore) -> None:
    service, _ = build(store)
    await make(service, "TYT")
    await make(service, "AYT")
    fresh, _ = build(store)          # süreç yeniden başladı
    assert await fresh.revision() == 2
    await make(fresh, "LGS")
    assert await fresh.revision() == 3


async def test_yayin_patlasa_da_yazma_tamamdir(store: ScopedStore) -> None:
    service, published = build(store, publish=Published(fail=True))
    group = await make(service, "TYT", ["STU-A"])
    assert published.revisions == [1]
    assert (await service.get(group["id"])) is not None


async def test_grup_kaydi_sozlesme_alanlari(store: ScopedStore) -> None:
    service, _ = build(store)
    group = await make(service, "TYT", ["STU-A"], callName="TYT grubu", note="iç not")
    record = await StudentGroups(service).get(group["id"])
    assert record is not None
    assert set(record) == {"id", "name", "callName", "kind", "archived", "archivedAt",
                           "updatedAt", "revision", "members"}
    assert record["archivedAt"] is None
    assert record["updatedAt"].endswith("+03:00")
    assert record["members"] == [
        {"studentId": "STU-A", "validFrom": "2026-10-12", "validTo": None}]

    mine = await StudentGroups(service).for_student("STU-A")
    assert mine == [{"group": {"id": group["id"], "name": "TYT", "callName": "TYT grubu",
                               "kind": "group", "archived": False},
                     "validFrom": "2026-10-12", "validTo": None}]


async def test_bugunku_uyelikler_tek_cagrida(store: ScopedStore) -> None:
    service, _ = build(store)
    tyt = await make(service, "TYT", ["STU-A", "STU-B"])
    old = await make(service, "Eski", ["STU-A"])
    await service.archive(old["id"], actor="Ayşe")
    await service.end_members(tyt["id"], ["STU-B"], valid_to="2026-10-12", actor="Ayşe")
    today = await service.memberships()
    assert sorted((i["studentId"], i["groupName"]) for i in today["items"]) == [
        ("STU-A", "TYT"), ("STU-B", "TYT")]
    tomorrow = await service.memberships(on=date(2026, 10, 13))
    assert [(i["studentId"], i["groupId"]) for i in tomorrow["items"]] == [("STU-A", tyt["id"])]


async def test_import_named_idempotent(store: ScopedStore) -> None:
    service, _ = build(store)
    groups = StudentGroups(service)
    first = await groups.import_named("İlayda", kind="individual", actor="zil")
    second = await groups.import_named("ilayda", kind="individual", actor="zil")
    assert first["created"] is True and second == {"id": first["id"], "created": False}
    record = await groups.get(first["id"])
    assert record is not None and record["kind"] == "individual"
    assert record["members"] == []


# ---------------------------------------------------------------- izin kapıları


def _declared(route: Any) -> set[str]:
    found: set[str] = set()
    for param in route.dependant.dependencies:
        for cell in getattr(param.call, "__closure__", None) or ():
            content = cell.cell_contents
            if isinstance(content, tuple) and all(isinstance(x, str) for x in content):
                found.update(content)
    return found


EXPECTED = {
    ("GET", "/groups"): {"bbd_students.view"},
    ("POST", "/groups"): {"bbd_students.groups"},
    ("POST", "/groups/from-class"): {"bbd_students.groups"},
    ("POST", "/groups/move"): {"bbd_students.groups"},
    ("GET", "/groups/memberships"): {"bbd_students.view"},
    ("GET", "/groups/{group_id}"): {"bbd_students.view"},
    ("GET", "/groups/{group_id}/members"): {"bbd_students.view"},
    ("GET", "/groups/{group_id}/history"): {"bbd_students.view"},
    ("PUT", "/groups/{group_id}"): {"bbd_students.groups"},
    ("POST", "/groups/{group_id}/archive"): {"bbd_students.groups"},
    ("POST", "/groups/{group_id}/restore"): {"bbd_students.groups"},
    ("POST", "/groups/{group_id}/members"): {"bbd_students.groups"},
    ("POST", "/groups/{group_id}/members/end"): {"bbd_students.groups"},
    ("PATCH", "/groups/{group_id}/members/{member_id}"): {"bbd_students.groups"},
    ("GET", "/students/{kantin_id}/groups"): {"bbd_students.view"},
}


def test_grup_uclari_izin_ilan_eder() -> None:
    """K9: okuma `view`, her yazma `groups` — izinsiz uç yok."""
    found = {}
    for route in routes.router.routes:
        for method in route.methods:  # type: ignore[attr-defined]
            found[(method, route.path)] = route  # type: ignore[attr-defined]
    for key, permissions in EXPECTED.items():
        assert key in found, f"{key} yok"
        assert _declared(found[key]) == permissions, key
    for key, route in found.items():
        assert _declared(route), f"{key} izin ilan etmiyor (K9)"


def test_grup_yazma_ucu_view_ile_acilmaz() -> None:
    source = inspect.getsource(routes.create_group)
    assert 'requires("bbd_students.groups")' in source
