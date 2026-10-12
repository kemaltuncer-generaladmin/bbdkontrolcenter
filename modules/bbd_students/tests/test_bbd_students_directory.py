"""`bbd_students.directory` yeteneği, telefon normalizasyonu ve seviye/alan/hedef sınav.

Depo GERÇEK SQLite'tır ve modülün kendi göçleri (001 + 002) uygulanır — 002
göçü de böylece sınanır. Kantin sahtedir; ağa çıkılmaz.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from bbd_students_backend import curriculum
from bbd_students_backend import qr as qr_codec
from bbd_students_backend.directory import (
    RECORD_KEYS,
    DirectoryUnavailable,
    StudentDirectory,
    student_record,
)
from bbd_students_backend.phones import normalize_phone
from bbd_students_backend.service import StudentService
from bbd_students_fakes import FakeLog

from km_core.contracts.module import ScopedStore
from km_core.store.db import Store

MIGRATIONS = Path(__file__).resolve().parents[1] / "backend" / "migrations"
QR_KEY = bytes(range(32))


class Canteen:
    def __init__(self, students: list[dict[str, Any]]) -> None:
        self.rows = students
        self.down = False
        self.list_calls = 0

    async def students(self) -> list[dict[str, Any]]:
        self.list_calls += 1
        if self.down:
            raise RuntimeError("bağlantı hatası")
        return list(self.rows)

    async def student(self, opaque_id: str) -> dict[str, Any]:
        if self.down:
            raise RuntimeError("bağlantı hatası")
        for row in self.rows:
            if row["id"] == opaque_id:
                return dict(row)
        error = RuntimeError("404")
        error.status = 404  # type: ignore[attr-defined]
        raise error

    async def qr_key(self) -> bytes:
        return QR_KEY

    async def upsert_student(self, opaque_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        return {"id": opaque_id, **changes}

    async def dashboard(self) -> dict[str, Any]:
        return {}


def kantin(opaque_id: str, name: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": opaque_id, "displayName": name, "parentPhone": "", "balance": 0,
        "spendingLimit": None, "isBlocked": False,
        "updatedAt": 1791786600000,          # 2026-10-12T09:30:00+03:00
        "accessCode": "483920", "accessCodeIssuedAt": 1791786600000, **extra,
    }


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[ScopedStore]:
    raw = Store(tmp_path / "km.sqlite")
    await raw.open()
    for path in sorted(MIGRATIONS.glob("*.sql")):
        await raw.apply_migration("bbd_students", path.name, path.read_text(encoding="utf-8"))
    yield ScopedStore(raw, "bbd_students")
    await raw.close()


async def profile(store: ScopedStore, kantin_id: str, **columns: Any) -> None:
    columns.setdefault("updated_at", "2026-10-11T15:02:11+00:00")
    names = ", ".join(columns)
    marks = ", ".join("?" for _ in columns)
    await store.execute(
        f"INSERT INTO mod_bbd_students_profile (kantin_id, {names}) VALUES (?, {marks})",
        (kantin_id, *columns.values()),
    )


# ------------------------------------------------------------------ telefon


@pytest.mark.parametrize(("raw", "expected"), [
    ("5321234567", "5321234567"),
    ("05321234567", "5321234567"),
    ("+90 532 123 45 67", "5321234567"),
    ("0090 (532) 123-45-67", "5321234567"),
    ("90 532 1234567", "5321234567"),
    ("0532 123 45 6", None),
    ("02121234567", None),           # sabit hat
    ("532123456789", None),
    ("abc", None),
    ("", None),
    (None, None),
])
def test_telefon_normalizasyonu(raw: Any, expected: str | None) -> None:
    assert normalize_phone(raw) == expected


# ------------------------------------------------------------- seviye/alan


@pytest.mark.parametrize(("class_name", "expected"), [
    ("8-A", {"gradeLevel": 8, "track": None, "examTarget": "LGS"}),
    ("12-SAY", {"gradeLevel": 12, "track": "SAY", "examTarget": "YKS"}),
    ("11 EA", {"gradeLevel": 11, "track": "EA", "examTarget": "YKS"}),
    ("10-SÖZ", {"gradeLevel": 10, "track": "SOZ", "examTarget": None}),
    ("Mezun DİL", {"gradeLevel": "graduate", "track": "DIL", "examTarget": "YKS"}),
    ("9-SAY", {"gradeLevel": 9, "track": None, "examTarget": None}),
    ("12-TM", {"gradeLevel": 12, "track": "EA", "examTarget": "YKS"}),
    ("A şubesi", None),
    ("", None),
    ("14-A", None),
])
def test_sinif_adindan_oneri(class_name: str, expected: dict[str, Any] | None) -> None:
    assert curriculum.suggest(class_name) == expected


def test_seviye_kurallari() -> None:
    assert curriculum.resolve({}, {"gradeLevel": "12", "track": "say"}) == {
        "grade_level": "12", "track": "SAY"}
    assert curriculum.resolve({}, {"gradeLevel": "graduate"}) == {"grade_level": "graduate"}
    # Seviye 10 altına çekilince alan temizlenir.
    assert curriculum.resolve({"gradeLevel": 11, "track": "EA"}, {"gradeLevel": 9}) == {
        "grade_level": "9", "track": None}
    # 10 altına açıkça alan verilemez.
    with pytest.raises(curriculum.CurriculumError):
        curriculum.resolve({}, {"gradeLevel": 8, "track": "SAY"})
    for bad in ({"gradeLevel": 4}, {"gradeLevel": 13}, {"gradeLevel": "mezun"},
                {"gradeLevel": True}, {"track": "FEN"}, {"examTarget": "KPSS"}):
        with pytest.raises(curriculum.CurriculumError):
            curriculum.resolve({}, bad)
    # Temizleme: None yazılır.
    assert curriculum.resolve({"examTarget": "LGS"}, {"examTarget": None}) == {
        "exam_target": None}


# ------------------------------------------------------------------- dizin


async def test_kayit_sozlesme_anahtarlarini_tasir_kod_sizmaz(store: ScopedStore) -> None:
    await profile(store, "STU-1", first_name="Elif", last_name="Kaya", class_name="8-A",
                  school_no="1234", student_phone="0532 123 45 67", parent_name="Ayşe Kaya",
                  parent_name2="Mehmet Kaya", parent_phone2="+90 505 111 22 33",
                  grade_level="8", exam_target="LGS")
    canteen = Canteen([kantin("STU-1", "Elif Kaya", parentPhone="05329876543", balance=4250,
                              spendingLimit=20000, dailyLimit=15000)])
    directory = StudentDirectory(canteen=canteen, store=store, log=FakeLog())

    result = await directory.list()
    assert result["complete"] is True
    assert result["classes"] == ["8-A"]
    record = result["students"][0]
    assert tuple(record) == RECORD_KEYS
    assert "accessCode" not in str(record)
    assert record == {
        "id": "STU-1",
        "firstName": "Elif",
        "lastName": "Kaya",
        "displayName": "Elif Kaya",
        "className": "8-A",
        "schoolNo": "1234",
        "studentPhone": "5321234567",
        "guardians": [
            {"slot": 1, "name": "Ayşe Kaya", "phone": "5329876543"},
            {"slot": 2, "name": "Mehmet Kaya", "phone": "5051112233"},
        ],
        "canteen": {"balance": 4250, "spendingLimit": 20000, "isBlocked": False,
                    "dailyLimit": 15000, "guardianBlocked": False},
        "updatedAt": "2026-10-12T09:30:00+03:00",
        "phoneIssues": [],
        "gradeLevel": 8,
        "track": None,
        "examTarget": "LGS",
    }


async def test_bozuk_telefon_null_ve_phone_issues(store: ScopedStore) -> None:
    await profile(store, "STU-1", student_phone="0212 123 45 67", parent_phone2="12")
    record = student_record(kantin("STU-1", "Ali Veli", parentPhone="yok"),
                            await store.fetch_one(
                                "SELECT * FROM mod_bbd_students_profile WHERE kantin_id = ?",
                                ("STU-1",)))
    assert record["studentPhone"] is None
    assert [g["phone"] for g in record["guardians"]] == [None, None]
    assert record["phoneIssues"] == ["studentPhone", "parentPhone", "parentPhone2"]
    # Profil yoksa ad kantin adından bölünür; telefonu olmayan veli listelenmez.
    bare = student_record(kantin("STU-2", "Can Ali Demir"), None)
    assert (bare["firstName"], bare["lastName"]) == ("Can Ali", "Demir")
    assert bare["guardians"] == []
    assert bare["gradeLevel"] is None


async def test_since_suzer_siniflar_tam_kalir(store: ScopedStore) -> None:
    await profile(store, "STU-1", class_name="12-SAY", updated_at="2026-10-12T08:00:00+00:00")
    await profile(store, "STU-2", class_name="8-A", updated_at="2026-10-01T08:00:00+00:00")
    eski = 1759305600000  # 2025 — kantin kaydı eski
    canteen = Canteen([kantin("STU-1", "A B", updatedAt=eski),
                       kantin("STU-2", "C D", updatedAt=eski)])
    directory = StudentDirectory(canteen=canteen, store=store, log=FakeLog())
    result = await directory.list(since=datetime(2026, 10, 5, tzinfo=UTC))
    assert [row["id"] for row in result["students"]] == ["STU-1"]
    assert result["classes"] == ["8-A", "12-SAY"]


async def test_kantin_yoksa_complete_false(store: ScopedStore) -> None:
    canteen = Canteen([])
    canteen.down = True
    directory = StudentDirectory(canteen=canteen, store=store, log=FakeLog())
    assert await directory.list() == {"students": [], "classes": [], "complete": False}
    with pytest.raises(DirectoryUnavailable) as error:
        await directory.get("STU-1")
    assert error.value.reason == "canteen_unavailable"


async def test_get_ve_find_by_phone(store: ScopedStore) -> None:
    await profile(store, "STU-1", student_phone="5551112233", parent_phone2="5051112233")
    canteen = Canteen([kantin("STU-1", "Elif Kaya", parentPhone="5329876543"),
                       kantin("STU-2", "Ekin Kaya", parentPhone="0532 987 65 43")])
    directory = StudentDirectory(canteen=canteen, store=store, log=FakeLog())

    assert (await directory.get("STU-1"))["studentPhone"] == "5551112233"  # type: ignore[index]
    assert await directory.get("STU-YOK") is None
    assert await directory.get("") is None

    kardesler = await directory.find_by_phone("05329876543")
    assert sorted(row["id"] for row in kardesler) == ["STU-1", "STU-2"]
    assert [row["id"] for row in await directory.find_by_phone("5051112233")] == ["STU-1"]
    assert await directory.find_by_phone("5559999999") == []
    assert await directory.find_by_phone("geçersiz") == []
    # Kantin listesi kısa süre önbellekte: iki arama tek liste çağrısı.
    assert canteen.list_calls == 1


async def test_kart_qr_metni_kartla_ayni(store: ScopedStore) -> None:
    directory = StudentDirectory(canteen=Canteen([]), store=store, log=FakeLog())
    text = await directory.card_qr_text("STU-1")
    decoded = qr_codec.decode(text, QR_KEY)
    assert decoded is not None and decoded["sid"] == "STU-1" and decoded["v"] == 1


# ------------------------------------------------------ servis (profil yazımı)


async def test_profil_seviye_yazimi_ve_toplu_islem(store: ScopedStore) -> None:
    canteen = Canteen([kantin("STU-1", "A B"), kantin("STU-2", "C D"), kantin("STU-3", "E F")])
    service = StudentService(canteen=canteen, store=store, log=FakeLog(), config={})

    await service.update_student("STU-1", {"className": "11-EA", "gradeLevel": 11, "track": "EA"})
    await service.update_student("STU-2", {"className": "8-A"})
    rows = {row["kantinId"]: row for row in (await service.list_students())["students"]}
    assert (rows["STU-1"]["gradeLevel"], rows["STU-1"]["track"]) == (11, "EA")
    assert rows["STU-2"]["gradeLevel"] is None                 # öneri YAZILMADI
    assert rows["STU-2"]["curriculumSuggestion"] == {
        "gradeLevel": 8, "track": None, "examTarget": "LGS"}

    with pytest.raises(ValueError):
        await service.update_student("STU-2", {"gradeLevel": 8, "track": "SAY"})

    result = await service.set_curriculum(["STU-1", "STU-2", "STU-3"], {"track": "SAY"})
    # STU-2'nin seviyesi henüz yok → alan girilebilir; hepsi yazılır.
    assert result == {"updated": ["STU-1", "STU-2", "STU-3"], "failed": []}

    await service.set_curriculum(["STU-2"], {"gradeLevel": 9})  # alan temizlenir
    result = await service.set_curriculum(["STU-1", "STU-2"], {"track": "EA"})
    assert result["updated"] == ["STU-1"]
    assert result["failed"][0]["studentId"] == "STU-2"

    rows = {row["kantinId"]: row for row in (await service.list_students())["students"]}
    assert (rows["STU-2"]["gradeLevel"], rows["STU-2"]["track"]) == (9, None)
    assert rows["STU-1"]["track"] == "EA"

    with pytest.raises(ValueError):
        await service.set_curriculum([], {"gradeLevel": 9})
    with pytest.raises(ValueError):
        await service.set_curriculum(["STU-1"], {"gradeLevel": 13})
