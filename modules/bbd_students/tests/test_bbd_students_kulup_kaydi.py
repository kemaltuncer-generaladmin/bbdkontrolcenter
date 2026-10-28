"""Deneme Kulübü öğrenci açılışı — `bbd_students.admission` (iş 81-km-kulup, göç 015).

Kulüp üyesi KM'de NORMAL öğrencidir: "Deneme Kulübü" sınıfına üye olur (yoksa açılır), kantine
yazılır, `student.created` olayı çıkar, kimlik bloğu (TC şifreli) aynı işlemde yazılır. Aynı
davet (externalRef) ikinci öğrenci açmaz; mevcut öğrenci ek sınıf üyeliği alır; belirsiz eşleşme
reddedilir. Depo GERÇEK SQLite (çekirdek şeması + outbox + modülün göçleri). Kişiler UYDURMA.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from bbd_students_backend.admission import AdmissionError, StudentAdmission
from bbd_students_backend.classes import ClassLink
from bbd_students_backend.directory import StudentDirectory
from bbd_students_backend.groups import GroupService
from bbd_students_backend.identity import IdentityCipher
from bbd_students_backend.service import StudentService
from bbd_students_backend.sync import StudentSync
from bbd_students_fakes import FakeLog

from km_core.contracts.module import ScopedStore
from km_core.store.bootstrap import build_schema
from km_core.store.db import Store
from km_platform.events.outbox import EventOutbox

MIGRATIONS = Path(__file__).resolve().parents[1] / "backend" / "migrations"
TC = "10000000146"


class Vault:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value


class Canteen:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.upserts = 0

    def add(self, opaque_id: str, name: str, phone: str = "") -> None:
        self.rows[opaque_id] = {"id": opaque_id, "displayName": name, "parentPhone": phone,
                                "balance": 0, "spendingLimit": None, "isBlocked": False,
                                "updatedAt": 1791786600000}

    async def students(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.rows.values()]

    async def get_student(self, opaque_id: str) -> dict[str, Any]:
        return dict(self.rows[opaque_id])

    async def student(self, opaque_id: str) -> dict[str, Any]:
        return dict(self.rows[opaque_id])

    async def upsert_student(self, opaque_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        self.upserts += 1
        row = self.rows.setdefault(opaque_id, {"id": opaque_id, "balance": 0, "parentPhone": "",
                                               "updatedAt": 1791786600000})
        row.update(changes)
        return dict(row)

    async def dashboard(self) -> dict[str, Any]:
        return {}


@dataclass
class World:
    raw: Store
    outbox: EventOutbox
    canteen: Canteen
    directory: StudentDirectory
    service: StudentService
    admission: StudentAdmission

    async def events(self) -> list[dict[str, Any]]:
        return await self.outbox.read_after(0, 500)

    async def classes_of(self, kantin_id: str) -> list[str]:
        student = await self.service.get_student(kantin_id)
        return sorted(item["name"] for item in (student or {}).get("classes") or [])


@pytest.fixture
async def world(tmp_path: Path) -> AsyncIterator[World]:
    raw = Store(tmp_path / "km.sqlite")
    await raw.open()
    await build_schema(raw)
    for path in sorted(MIGRATIONS.glob("*.sql")):
        await raw.apply_migration("bbd_students", path.name, path.read_text(encoding="utf-8"))
    store = ScopedStore(raw, "bbd_students")
    # Sınıf = grup (göç 009): birleştirme tamam.
    await store.execute(f"INSERT INTO {store.table('class_merge')} (id, state) VALUES (1, 'done')")
    outbox = EventOutbox(raw)
    await outbox.open(prune_loop=False)
    canteen = Canteen()
    vault = Vault()
    cipher = IdentityCipher(lambda: vault)
    directory = StudentDirectory(canteen=canteen, store=store, log=FakeLog(), cipher=cipher)
    clock = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
    sync = StudentSync(store=store, canteen=canteen, log=FakeLog(), outbox=lambda: outbox,
                       on_change=directory.invalidate, clock=lambda: clock,
                       today=lambda: clock.date(), delay=0)
    service = StudentService(canteen=canteen, store=store, log=FakeLog(),
                             on_change=directory.invalidate, sync=sync, directory=directory,
                             cipher=cipher)

    async def published(revision: int) -> None:
        directory.invalidate(canteen=False)

    link = ClassLink(store=store, sync=sync, log=FakeLog())
    service.attach_groups(GroupService(store=store, log=FakeLog(), publish=published,
                                       outbox=lambda: outbox, today=lambda: clock.date(),
                                       students_changed=link.refresh,
                                       apply_level=link.apply_level))
    admission = StudentAdmission(service=service, store=store, directory=directory,
                                 log=FakeLog())
    yield World(raw, outbox, canteen, directory, service, admission)
    await outbox.close()
    await raw.close()


PAYLOAD: dict[str, Any] = {
    "firstName": "Deniz", "lastName": "Örnek", "className": "Deneme Kulübü",
    "guardians": [{"slot": 1, "name": "Ayla Örnek", "phone": "0532 111 00 00",
                   "relation": "mother"},
                  {"slot": 2, "name": "Kaan Örnek", "phone": "5052220000",
                   "relation": "father"}],
    "identity": {"student": {"nationalId": TC, "birthDate": "2010-03-04", "gender": "female"},
                 "guardians": [{"slot": 1, "phone": "5321110000", "occupation": "Mimar"}]},
}


async def test_yeni_ogrenci_kulup_sinifinda_kantinde_olayli(world: World) -> None:
    result = await world.admission.admit(PAYLOAD, external_ref="inv_1", causation_id="idem-1")
    assert result["created"] is True and result["studentId"].startswith("STU-")
    assert "warnings" not in result
    kantin_id = result["studentId"]
    assert world.canteen.rows[kantin_id]["displayName"] == "Deniz Örnek"
    assert world.canteen.rows[kantin_id]["parentPhone"] == "5321110000"
    assert await world.classes_of(kantin_id) == ["Deneme Kulübü"]
    student = await world.service.get_student(kantin_id)
    assert [g["phone"] for g in student["guardians"]] == ["5321110000", "5052220000"]
    # Kimlik: TC şifreli (düz metin profilde yok), tam hali yetkili okumada.
    row = await world.raw.fetch_one(
        "SELECT national_id FROM mod_bbd_students_profile WHERE kantin_id = ?", (kantin_id,))
    assert row["national_id"].startswith("v1:") and TC not in row["national_id"]
    view = await world.service.get_identity(kantin_id, reveal=True)
    assert view["student"]["nationalId"] == TC
    created = [event for event in await world.events() if event["type"] == "student.created"]
    assert len(created) == 1 and created[0]["subject"]["id"] == kantin_id
    assert created[0]["causationId"] == "idem-1"


async def test_ayni_davet_ikinci_ogrenci_acmaz(world: World) -> None:
    first = await world.admission.admit(PAYLOAD, external_ref="inv_1")
    again = await world.admission.admit(PAYLOAD, external_ref="inv_1")
    assert again == {"studentId": first["studentId"], "created": True,
                     "className": "Deneme Kulübü"}
    assert len(world.canteen.rows) == 1


async def test_mevcut_ogrenci_ek_sinif_alir(world: World) -> None:
    world.canteen.add("STU-old0000001", "Deniz Örnek", "5321110000")
    await world.service.set_class(["STU-old0000001"], "8-A")
    result = await world.admission.admit(PAYLOAD, external_ref="inv_2")
    assert result == {"studentId": "STU-old0000001", "created": False,
                      "className": "Deneme Kulübü"}
    assert await world.classes_of("STU-old0000001") == ["8-A", "Deneme Kulübü"]
    assert len(world.canteen.rows) == 1
    # Açık eşleştirme (Akademi kesin eşleşme verdi) de aynı yoldan.
    linked = await world.admission.admit({**PAYLOAD, "firstName": "Başka"},
                                         external_ref="inv_3", student_id="STU-old0000001")
    assert linked["created"] is False and linked["studentId"] == "STU-old0000001"


async def test_belirsiz_eslesme_reddedilir(world: World) -> None:
    world.canteen.add("STU-twin000001", "Deniz Örnek", "5321110000")
    world.canteen.add("STU-twin000002", "Deniz Örnek", "5321110000")
    with pytest.raises(AdmissionError) as caught:
        await world.admission.admit(PAYLOAD, external_ref="inv_4")
    assert (caught.value.status, caught.value.code) == (409, "ambiguous_match")
    # Sahiplenme bırakıldı: eşleştirme verilince aynı davet işlenir.
    result = await world.admission.admit(PAYLOAD, external_ref="inv_4",
                                         student_id="STU-twin000002")
    assert result["studentId"] == "STU-twin000002"


async def test_ad_ayni_telefon_farkli_yeni_ogrenci(world: World) -> None:
    world.canteen.add("STU-other00001", "Deniz Örnek", "5449990000")
    result = await world.admission.admit(PAYLOAD, external_ref="inv_5")
    assert result["created"] is True and result["studentId"] != "STU-other00001"


async def test_dogrulama(world: World) -> None:
    with pytest.raises(AdmissionError):
        await world.admission.admit({**PAYLOAD, "guardians": []}, external_ref="x")
    with pytest.raises(AdmissionError) as caught:
        await world.admission.admit({**PAYLOAD, "guardians": [{"slot": 1, "phone": "123"}]},
                                    external_ref="x")
    assert caught.value.details == {"path": "guardians[0].phone"}
    with pytest.raises(AdmissionError):
        await world.admission.admit({**PAYLOAD, "lastName": " "}, external_ref="x")
    assert world.canteen.rows == {}
