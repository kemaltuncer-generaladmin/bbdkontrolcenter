"""Öğrenci yönetimi BBD Akademi'ye taşındı (13.10.2026) — yeni akışlar.

* Veli yönetimi (göç 007): birden çok veli (en çok 4), birincil veli = yuva 1
  (telefonu kantinde), yakınlık, telefon doğrulama (+90 5xx), aynı öğrencide
  tekrar eden telefon reddi, aynı telefonun başka öğrencide veli olabilmesi
  (kardeş), olaylar (yuva 1–4 `guardian.*` — veli-senkron-4; ad/yakınlık
  `student.updated`).
* Toplu ana sınıf ataması (`set_class`): öğrenci başına `student.updated`
  (`className`), kantine gidilmez.
* Tek öğrenci kaydı (`get_student`): Akademi öğrenci kartının "Kayıt" bölümü.
* Taşınan akışlar aynı servisle çalışır: ekle / düzenle / sınıf değiştir /
  grup / pasif / sil — her biri senkron olayı üretir.

Depo, outbox ve göçler GERÇEKTİR (`test_bbd_students_merkezi_senkron.World`).
"""

# ruff: noqa: F811 — `world` fikstürü merkezi senkron testinden alınır (aynı gerçek depo).
from __future__ import annotations

from typing import Any

import pytest
from bbd_students_backend.guardians import GuardianError, clean_items
from test_bbd_students_merkezi_senkron import World, no_pii, world  # noqa: F401 — fikstür

MOTHER, FATHER, GRANNY = "5321112233", "5054445566", "5427778899"


async def _profile(world: World, kantin_id: str) -> dict[str, Any]:
    profile = await world.sync.profile(kantin_id)
    assert profile is not None
    return profile


# ================================================================== veliler


def test_veli_kurallari() -> None:
    clean = clean_items([{"name": "  Ayşe   Kaya ", "phone": "+90 532 111 22 33", "relation": "mother"},
                         {"name": "", "phone": "0505 444 55 66", "relation": ""}])
    assert clean == [{"name": "Ayşe Kaya", "phone": MOTHER, "relation": "mother"},
                     {"name": "", "phone": FATHER, "relation": ""}]
    with pytest.raises(GuardianError, match="geçersiz") as bad:
        clean_items([{"phone": "0212 555 11 22"}])       # sabit hat SMS/OTP alamaz
    assert bad.value.index == 0 and bad.value.field == "phone"
    with pytest.raises(GuardianError, match="boş"):
        clean_items([{"name": "Ayşe", "phone": ""}])
    with pytest.raises(GuardianError, match="1. veliyle aynı"):
        clean_items([{"phone": MOTHER}, {"phone": "0" + MOTHER}])
    with pytest.raises(GuardianError, match="yakınlık"):
        clean_items([{"phone": MOTHER, "relation": "kuzen"}])
    with pytest.raises(GuardianError, match="En çok 4"):
        clean_items([{"phone": f"53200000{index:02d}"} for index in range(5)])
    assert clean_items([]) == []


async def test_veliler_yuvalara_yazilir_birincil_kantine_gider(world: World) -> None:
    world.canteen.add("STU-1", "Elif Kaya")
    result = await world.service.set_guardians("STU-1", [
        {"name": "Ayşe Kaya", "phone": "0532 111 22 33", "relation": "mother"},
        {"name": "Mehmet Kaya", "phone": FATHER, "relation": "father"},
        {"name": "Fatma Kaya", "phone": GRANNY, "relation": "grandparent"},
    ], actor="Ayşe")
    assert result["changed"] is True and result["canteenSync"] == "ok"
    # Birincil velinin telefonu kantine (normalize) gider; diğerleri KM'de.
    assert world.canteen.calls[-1] == ("upsert", "STU-1", {"displayName": "Elif Kaya",
                                                          "parentPhone": MOTHER})
    profile = await _profile(world, "STU-1")
    assert profile["parent_name"] == "Ayşe Kaya" and profile["parent_relation"] == "mother"
    assert profile["parent_phone2"] == FATHER and profile["parent_relation2"] == "father"
    assert "Fatma Kaya" in profile["guardians_extra"]

    view = result["guardians"]
    assert [(row["slot"], row["phone"], row["primary"], row["appAccess"]) for row in view] == [
        (1, MOTHER, True, True), (2, FATHER, False, True), (3, GRANNY, False, True)]
    assert view[2]["relationLabel"] == "Büyükanne / büyükbaba"

    events = await world.events()
    assert [event["type"] for event in events] == [
        "student.updated", "guardian.linked", "guardian.linked", "guardian.linked"]
    assert events[0]["fields"] == ["guardians"]
    assert [event["data"] for event in events[1:]] == [{"slot": 1}, {"slot": 2}, {"slot": 3}]
    no_pii(events)

    # Dizin (Akademi'ye giden kayıt) yakınlığı ve 3. veliyi taşır.
    record = await world.directory.get("STU-1")
    assert record is not None
    assert [(row["slot"], row["relation"]) for row in record["guardians"]] == [
        (1, "mother"), (2, "father"), (3, "grandparent")]


async def test_birincil_degisince_telefonlar_yuva_degistirir(world: World) -> None:
    world.canteen.add("STU-1", "Elif Kaya", parentPhone=MOTHER)
    await world.service.set_guardians("STU-1", [{"name": "Ayşe", "phone": MOTHER},
                                                {"name": "Mehmet", "phone": FATHER}])
    before = len(await world.events())
    await world.service.set_guardians("STU-1", [{"name": "Mehmet", "phone": FATHER},
                                                {"name": "Ayşe", "phone": MOTHER}])
    events = (await world.events())[before:]
    assert {event["type"] for event in events} == {"student.updated", "guardian.phone_changed"}
    slots = sorted(event["data"]["slot"] for event in events if event["type"].startswith("guardian."))
    assert slots == [1, 2]
    assert world.canteen.rows["STU-1"]["parentPhone"] == FATHER


async def test_veli_silme_ve_degismeyen_liste(world: World) -> None:
    world.canteen.add("STU-1", "Elif Kaya", parentPhone=MOTHER)
    await world.service.set_guardians("STU-1", [{"name": "Ayşe", "phone": MOTHER},
                                                {"name": "Mehmet", "phone": FATHER}])
    calls = len(world.canteen.calls)
    same = await world.service.set_guardians("STU-1", [{"name": "Ayşe", "phone": "0532 111 22 33"},
                                                       {"name": "Mehmet", "phone": FATHER}])
    assert same["changed"] is False and len(world.canteen.calls) == calls

    before = len(await world.events())
    await world.service.set_guardians("STU-1", [{"name": "Ayşe", "phone": MOTHER}])
    events = (await world.events())[before:]
    assert [event["type"] for event in events] == ["student.updated", "guardian.unlinked"]
    assert events[1]["data"] == {"slot": 2}
    assert len(world.canteen.calls) == calls, "1. veli değişmedi: kantine gidilmemeli"

    await world.service.set_guardians("STU-1", [])
    assert (await world.events())[-1]["type"] == "guardian.unlinked"
    assert world.canteen.rows["STU-1"]["parentPhone"] == ""


async def test_yuva_3_4_telefonu_veli_olayi_ad_yakinlik_student_updated(world: World) -> None:
    """Yuva 3–4 (göç 007) de `guardian.linked|unlinked|phone_changed` (`data.slot` 3|4) üretir;
    yalnız telefon değişince `student.updated` çıkmaz, ad / yakınlık değişince çıkar."""
    aunt, uncle = "5331234567", "5449876543"
    world.canteen.add("STU-1", "Elif Kaya", parentPhone=MOTHER)
    base = [{"name": "Ayşe", "phone": MOTHER, "relation": "mother"},
            {"name": "Mehmet", "phone": FATHER, "relation": "father"}]
    await world.service.set_guardians("STU-1", base)
    calls = len(world.canteen.calls)

    def tail(events: list[dict[str, Any]], start: int) -> list[tuple[str, Any]]:
        return [(event["type"], event["data"]) for event in events[start:]]

    start = len(await world.events())
    await world.service.set_guardians("STU-1", [*base,
                                                {"name": "Fatma", "phone": GRANNY, "relation": "grandparent"},
                                                {"name": "Zehra", "phone": aunt, "relation": "other"}])
    events = await world.events()
    assert tail(events, start) == [("student.updated", None), ("guardian.linked", {"slot": 3}),
                                   ("guardian.linked", {"slot": 4})]
    assert events[start]["fields"] == ["guardians"]
    no_pii(events)

    start = len(events)
    await world.service.set_guardians("STU-1", [*base,
                                                {"name": "Fatma", "phone": GRANNY, "relation": "grandparent"},
                                                {"name": "Zehra", "phone": uncle, "relation": "other"}])
    events = await world.events()
    assert tail(events, start) == [("guardian.phone_changed", {"slot": 4})], \
        "yalnız telefon: student.updated çıkmaz"

    start = len(events)
    await world.service.set_guardians("STU-1", [*base,
                                                {"name": "Fatma Kaya", "phone": GRANNY, "relation": "grandparent"},
                                                {"name": "Zehra", "phone": uncle, "relation": "guardian"}])
    events = await world.events()
    assert tail(events, start) == [("student.updated", None)]
    assert events[start]["fields"] == ["guardians"]

    start = len(events)
    await world.service.set_guardians("STU-1", base)
    events = await world.events()
    assert tail(events, start) == [("student.updated", None), ("guardian.unlinked", {"slot": 3}),
                                   ("guardian.unlinked", {"slot": 4})]
    assert len(world.canteen.calls) == calls, "1. veli değişmedi: kantine gidilmemeli"

    record = await world.directory.get("STU-1")
    assert record is not None and [row["slot"] for row in record["guardians"]] == [1, 2]


async def test_ayni_telefon_kardeslerde_veli_olabilir(world: World) -> None:
    world.canteen.add("STU-1", "Elif Kaya")
    world.canteen.add("STU-2", "Can Kaya")
    await world.service.set_guardians("STU-1", [{"name": "Ayşe", "phone": MOTHER}])
    await world.service.set_guardians("STU-2", [{"name": "Ayşe", "phone": MOTHER}])
    view = await world.service.guardians("STU-1")
    assert view["items"][0]["siblings"] == [{"id": "STU-2", "displayName": "Can Kaya",
                                             "className": ""}]
    assert view["max"] == 4 and view["appSlots"] == [1, 2, 3, 4]
    assert {item["value"] for item in view["relations"]} >= {"mother", "father", "guardian"}


async def test_kantin_yokken_veli_bekler_uzlastirma_tamamlar(world: World) -> None:
    world.canteen.add("STU-1", "Elif Kaya")
    await world.directory.list()
    world.canteen.down = True
    result = await world.service.set_guardians("STU-1", [{"name": "Ayşe", "phone": MOTHER}])
    assert result["canteenSync"] == "pending"
    world.canteen.down = False
    await world.sync.reconcile_once()
    assert world.canteen.rows["STU-1"]["parentPhone"] == MOTHER


async def test_patch_yakinlik_dogrulanir(world: World) -> None:
    world.canteen.add("STU-1", "Elif Kaya")
    with pytest.raises(ValueError, match="yakınlık"):
        await world.service.update_student("STU-1", {"parentRelation": "kuzen"})
    await world.service.update_student("STU-1", {"parentRelation": "guardian"})
    assert (await _profile(world, "STU-1"))["parent_relation"] == "guardian"


# ============================================================ toplu sınıf


async def test_toplu_ana_sinif_ogrenci_basina_olay(world: World) -> None:
    for index in range(3):
        world.canteen.add(f"STU-{index}", f"Öğrenci {index}")
    result = await world.service.set_class(["STU-0", "STU-1", "STU-2", "STU-1"], " 9 / b ")
    assert result == {"updated": ["STU-0", "STU-1", "STU-2"], "failed": [], "className": "9-B"}
    events = await world.events()
    assert [event["type"] for event in events] == ["student.updated"] * 3
    assert all(event["fields"] == ["className"] for event in events)
    assert world.canteen.calls == [], "sınıf kantin alanı değil"
    listed = (await world.service.list_students())["students"]
    assert {row["className"] for row in listed} == {"9-B"}

    with pytest.raises(ValueError, match="seçilmedi"):
        await world.service.set_class([], "9-A")


# ====================================================== tek öğrenci kaydı


async def test_tek_ogrenci_kaydi(world: World) -> None:
    world.canteen.add("STU-1", "Elif Kaya", parentPhone=MOTHER, balance=1250)
    await world.service.update_student("STU-1", {"className": "8-A", "parentName": "Ayşe"})
    student = await world.service.get_student("STU-1")
    assert student is not None
    assert student["kantinId"] == "STU-1" and student["className"] == "8-A"
    assert student["balance"] == 1250
    assert student["guardians"][0]["name"] == "Ayşe" and student["guardians"][0]["primary"]
    assert await world.service.get_student("STU-YOK") is None


async def test_tek_ogrenci_kaydi_kantine_yazilmamis(world: World) -> None:
    world.canteen.down = True
    created = await world.service.create_student({"firstName": "Elif", "lastName": "Kaya"})
    student = await world.service.get_student(created["kantinId"])
    assert student is not None and student["canteenMissing"] is True
    assert student["displayName"] == "Elif Kaya"


# ============================================= taşınan akışlar: olay üretimi


async def test_tasinan_akislar_senkron_olayi_uretir(world: World) -> None:
    created = await world.service.create_student({"firstName": "Elif", "lastName": "Kaya",
                                                  "className": "8-A"})
    kantin_id = created["kantinId"]
    await world.service.update_student(kantin_id, {"schoolNo": "123"})
    await world.service.set_class([kantin_id], "8-B")
    group = await world.groups.create({"name": "LGS Hazırlık"}, actor="Ayşe")
    await world.groups.add_members(group["id"], [kantin_id], valid_from=None, reason="",
                                   actor="Ayşe")
    await world.lifecycle.deactivate(kantin_id, reason="left", actor="Ayşe")
    types = await world.types()
    for expected in ("student.created", "student.updated", "group.changed", "student.deactivated"):
        assert expected in types, expected
    no_pii(await world.events())
