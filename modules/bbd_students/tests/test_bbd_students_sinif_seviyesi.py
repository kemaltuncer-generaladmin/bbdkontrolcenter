"""Sınıf seviyeyi taşır (kullanıcı kararı 2026-10-13, göç 008).

Seviye / alan / hedef sınav ANA SINIF tanımında girilir; öğrencinin geçerli değeri
sınıftan türetilir (öğrenciye özel yalnız alan ve hedef sınav istisnası). Dizin
(`classLevel` + türetilmiş `gradeLevel/track/examTarget`) ve olaylar
(`student.updated`, sınıftaki her öğrenci için) Akademi'ye bunu taşır.
"""

from __future__ import annotations

import pytest
from bbd_students_backend import curriculum
from test_bbd_students_merkezi_senkron import World, world  # noqa: F401 — fikstür

SAY11 = {"gradeLevel": 11, "track": "SAY", "examTarget": "YKS"}


def test_gecerli_seviye_kurali() -> None:
    cls = curriculum.class_from_row({"grade_level": "11", "track": "SAY", "exam_target": "YKS"})
    assert cls == SAY11
    # sınıf tanımlıysa öğrencinin eski seviyesi geçmez
    own = {"grade_level": "9", "track": None, "exam_target": None}
    assert curriculum.effective(own, cls) == {**SAY11, "levelSource": "class"}
    # istisna: öğrencinin alanı / hedefi (seviye sınıftan)
    over = {"grade_level": "9", "track": "EA", "exam_target": None, "level_override": 1}
    assert curriculum.effective(over, cls) == {"gradeLevel": 11, "track": "EA", "examTarget": "YKS",
                                               "levelSource": "override"}
    # sınıfa seviye girilmemiş: öğrencinin kendi (eski) değerleri; hiçbiri yoksa kaynak yok
    assert curriculum.effective(own, None)["levelSource"] == "student"
    assert curriculum.effective({}, None) == {"gradeLevel": None, "track": None, "examTarget": None,
                                              "levelSource": None}
    # 9. sınıfta alan tutulmaz; mezun sınıfta tutulur
    assert curriculum.class_from_row({"grade_level": "9", "track": "SAY"})["track"] is None
    assert curriculum.resolve_class(None, {"gradeLevel": "graduate", "track": "say"}) == {
        "grade_level": "graduate", "track": "SAY", "exam_target": None}
    with pytest.raises(curriculum.CurriculumError):
        curriculum.resolve_class(None, {"track": "SAY"})          # seviye zorunlu
    with pytest.raises(curriculum.CurriculumError):
        curriculum.resolve_class(None, {"gradeLevel": 8, "track": "SAY"})
    assert curriculum.class_label("Mezun", None) == "Mezun · seviye seçin"
    assert curriculum.class_label("11-A", SAY11) == "11-A · 11. sınıf · SAY · YKS"


async def test_sinif_tanimi_ogrencilere_ve_dizine_gecer(world: World) -> None:  # noqa: F811
    w = world
    w.canteen.add("STU-1", "Elif Kaya")
    w.canteen.add("STU-2", "Can Demir")
    w.canteen.add("STU-3", "Ece Ak")
    await w.service.set_class(["STU-1", "STU-2"], "11a")      # → "11-A"
    await w.service.update_student("STU-3", {"className": "Mezun", "gradeLevel": "graduate"})

    classes = {item["name"]: item for item in (await w.service.list_classes())["items"]}
    assert classes["11-A"]["defined"] is False and classes["11-A"]["studentCount"] == 2
    assert classes["11-A"]["suggestion"]["gradeLevel"] == 11
    assert classes["11-A"]["label"] == "11-A · seviye seçin"
    assert (await w.service.list_classes())["undefinedCount"] == 2

    before = len(await w.events())
    result = await w.service.set_class_level("11-A", SAY11, actor="Ayşe")
    assert result["updatedStudents"] == 2 and result["label"] == "11-A · 11. sınıf · SAY · YKS"
    new = (await w.events())[before:]
    assert [(e["type"], sorted(e["fields"])) for e in new] == [
        ("student.updated", ["examTarget", "gradeLevel", "track"])] * 2

    record = await w.directory.get("STU-1")
    assert record is not None
    assert (record["gradeLevel"], record["track"], record["examTarget"]) == (11, "SAY", "YKS")
    assert record["classLevel"] == SAY11
    listed = {row["id"]: row for row in (await w.directory.list())["students"]}
    assert listed["STU-2"]["classLevel"] == SAY11
    assert listed["STU-3"]["classLevel"] is None and listed["STU-3"]["gradeLevel"] == "graduate"

    # öğrencide seviye yazılmaz (sınıftan gelir); istisna yalnız alan / hedef sınav
    with pytest.raises(ValueError, match="sınıftan gelir"):
        await w.service.update_student("STU-1", {"gradeLevel": 9})
    before = len(await w.events())
    await w.service.update_student("STU-1", {"levelOverride": True, "track": "EA"})
    student = await w.service.get_student("STU-1")
    assert student is not None
    assert (student["gradeLevel"], student["track"], student["levelSource"]) == (11, "EA", "override")
    assert student["classLevel"] == SAY11 and student["levelOverride"] is True
    [event] = (await w.events())[before:]
    assert event["fields"] == ["track"]

    # sınıfın seviyesi değişince istisnalı öğrencinin yalnız seviyesi değişir
    before = len(await w.events())
    await w.service.set_class_level("11-A", {"gradeLevel": 12})
    assert [sorted(e["fields"]) for e in (await w.events())[before:]] == [["gradeLevel"], ["gradeLevel"]]
    rows = {row["kantinId"]: row for row in (await w.service.list_students())["students"]}
    assert (rows["STU-1"]["gradeLevel"], rows["STU-1"]["track"]) == (12, "EA")
    assert (rows["STU-2"]["gradeLevel"], rows["STU-2"]["track"]) == (12, "SAY")

    # canlıdaki gibi elle yazılmış "Mezun" adı olduğu gibi kalır
    await w.service.set_class_level("Mezun", {"gradeLevel": "graduate", "track": "SAY", "examTarget": "YKS"})
    record = await w.directory.get("STU-3")
    assert record is not None and record["classLevel"]["gradeLevel"] == "graduate"
    assert record["track"] == "SAY"

    # başka sınıfa geçen öğrenci o sınıfın seviyesini alır (tek olay, sınıf + seviye alanları)
    before = len(await w.events())
    await w.service.set_class(["STU-2"], "Mezun")
    [event] = (await w.events())[before:]
    assert sorted(event["fields"]) == ["className", "gradeLevel"]

    # toplu alan ataması sınıfı seviyeli öğrencide istisna olur
    result = await w.service.set_curriculum(["STU-2"], {"examTarget": "LGS"})
    assert result["updated"] == ["STU-2"]
    student = await w.service.get_student("STU-2")
    assert student is not None and student["levelOverride"] is True and student["examTarget"] == "LGS"

    # tanım kaldırılınca öğrenciler kendi (eski) değerlerine döner
    await w.service.clear_class_level("11-A")
    record = await w.directory.get("STU-1")
    assert record is not None and record["classLevel"] is None and record["gradeLevel"] is None
    with pytest.raises(LookupError):
        await w.service.clear_class_level("11-A")
    with pytest.raises(ValueError):
        await w.service.set_class_level("  ", {"gradeLevel": 9})
