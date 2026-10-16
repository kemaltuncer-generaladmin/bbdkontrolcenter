"""Sınıf / GRUP müfredat seviyesi (Akademi README §14 madde 102; canlı: öğrencilerin çoğu KM gruplarında).

Grupta seviye isteğe bağlıdır: karışık bir hazırlık grubuna ("TYT GRUBU") yalnız hedef sınav (+ alan) girilebilir;
kayıt bunu `curriculumLevel` ile Akademi'ye taşır (Akademi `level_source='km'` yazar, addan tahminden önce kullanır).
Seviyesiz değer öğrencinin seviyesine YAZILMAZ (`classLevel` / "üyelere uygula" yalnız seviyeli tanımda).
"""

from __future__ import annotations

import pytest
from bbd_students_backend import curriculum
from bbd_students_backend.groups import GroupError, StudentGroups
from test_bbd_students_groups import build, make, store  # noqa: F401 — fikstür

from km_core.contracts.module import ScopedStore


def test_grup_seviyesi_kurali() -> None:
    assert curriculum.resolve_group({"gradeLevel": None, "examTarget": "YKS"}) == {
        "grade_level": None, "track": None, "exam_target": "YKS"}
    assert curriculum.resolve_group({"track": "EA", "examTarget": "YKS"})["track"] == "EA"
    assert curriculum.resolve_group({"gradeLevel": 8, "examTarget": "LGS"})["grade_level"] == "8"
    with pytest.raises(curriculum.CurriculumError):
        curriculum.resolve_group({})
    with pytest.raises(curriculum.CurriculumError):
        curriculum.resolve_group({"track": "SAY", "examTarget": "LGS"})
    assert curriculum.group_from_row({"grade_level": None, "track": None, "exam_target": "YKS"}) == {
        "gradeLevel": None, "track": None, "examTarget": "YKS"}
    assert curriculum.group_from_row({"grade_level": None, "track": None, "exam_target": None}) is None


async def test_tyt_grubu_yalniz_hedefle_kaydedilir(store: ScopedStore) -> None:  # noqa: F811
    service, _ = build(store)
    group = await make(service, "TYT GRUBU", ["STU-A"], level={"gradeLevel": None, "examTarget": "YKS"})
    record = await StudentGroups(service).get(group["id"])
    assert record is not None
    assert record["curriculumLevel"] == {"gradeLevel": None, "track": None, "examTarget": "YKS"}
    assert record["classLevel"] is None  # öğrenciye yazılan şablon değil
    mezun = await make(service, "MEZUN GRUBU", [], level={"gradeLevel": "graduate", "examTarget": "YKS"})
    record = await StudentGroups(service).get(mezun["id"])
    assert record is not None
    assert record["curriculumLevel"] == record["classLevel"] == {
        "gradeLevel": "graduate", "track": None, "examTarget": "YKS"}
    with pytest.raises(GroupError):
        await make(service, "Boş", [], level={"gradeLevel": None})
    plain = await make(service, "8. Sınıflar", [])
    record = await StudentGroups(service).get(plain["id"])
    assert record is not None and record["curriculumLevel"] is None  # Akademi addan tahmin eder


def test_gecit_curriculum_level() -> None:
    from modules.mobile_gateway.backend.service import GatewayService

    rec = {"id": "g1", "name": "TYT GRUBU", "members": [], "curriculumLevel": {"gradeLevel": None, "examTarget": "YKS"}}
    out = GatewayService.group(rec, classes=True)
    assert out["curriculumLevel"] == {"gradeLevel": None, "track": None, "examTarget": "YKS"}
    assert out["classLevel"] is None
    assert "curriculumLevel" not in GatewayService.group({"id": "g2", "name": "x", "members": []}, classes=True)
    assert "curriculumLevel" not in GatewayService.group(rec)  # eski görünüm
