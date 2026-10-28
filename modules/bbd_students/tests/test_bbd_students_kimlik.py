"""Sözleşme kimliği (göç 014, iş 80-km-kimlik) — TC sağlaması, maske, şifreli saklama,
revizyon + olay, izin, veli kimliğinin kişiyle gitmesi, dizinin `identity` bloğu.

Depo GERÇEK SQLite (çekirdek şeması + outbox + modülün bütün göçleri); kantin ve sır
kasası sahte. Bütün kişiler UYDURMADIR (TC'ler yalnız sağlaması tutan sayılar).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from bbd_students_backend import identity as rules
from bbd_students_backend.api import routes
from bbd_students_backend.directory import RECORD_KEYS, StudentDirectory
from bbd_students_backend.identity import IdentityCipher, IdentityError
from bbd_students_backend.service import StudentService
from bbd_students_backend.sync import StudentSync
from bbd_students_fakes import FakeLog

from km_core.contracts.module import ScopedStore
from km_core.security.identity import CurrentUser
from km_core.store.bootstrap import build_schema
from km_core.store.db import Store
from km_platform.events.outbox import EventOutbox

MIGRATIONS = Path(__file__).resolve().parents[1] / "backend" / "migrations"

TC_STUDENT = "10000000146"
TC_MOTHER = "10000000078"
TC_FATHER = "20000000046"
VALID = [TC_STUDENT, TC_MOTHER, TC_FATHER, "11111111110", "12345678950"]
INVALID = ["10000000147", "01234567890", "1000000014", "100000001460", "1000000014a",
           "10000000156", ""]


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

    def add(self, opaque_id: str, name: str, phone: str = "") -> None:
        self.rows[opaque_id] = {"id": opaque_id, "displayName": name, "parentPhone": phone,
                                "balance": 0, "spendingLimit": None, "isBlocked": False,
                                "updatedAt": 1791786600000}

    async def students(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.rows.values()]

    async def student(self, opaque_id: str) -> dict[str, Any]:
        return dict(self.rows[opaque_id])

    async def get_student(self, opaque_id: str) -> dict[str, Any]:
        return dict(self.rows[opaque_id])

    async def upsert_student(self, opaque_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        row = self.rows.setdefault(opaque_id, {"id": opaque_id, "balance": 0, "parentPhone": "",
                                               "updatedAt": 1791786600000})
        row.update(changes)
        return dict(row)

    async def dashboard(self) -> dict[str, Any]:
        return {}


@dataclass
class World:
    raw: Store
    store: ScopedStore
    outbox: EventOutbox
    canteen: Canteen
    vault: Vault
    directory: StudentDirectory
    service: StudentService

    async def events(self) -> list[dict[str, Any]]:
        return await self.outbox.read_after(0, 500)


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
    yield World(raw, store, outbox, canteen, vault, directory, service)
    await outbox.close()
    await raw.close()


async def elif_with_guardians(world: World) -> None:
    world.canteen.add("STU-1", "Elif Yılmaz", "05321112233")
    await world.service.set_guardians("STU-1", [
        {"name": "Ayşe Yılmaz", "phone": "05321112233", "relation": "mother"},
        {"name": "Mehmet Yılmaz", "phone": "05054445566", "relation": "father"},
    ])


FULL_BODY: dict[str, Any] = {
    "student": {"nationalId": TC_STUDENT, "birthDate": "01.05.2009", "gender": "female",
                "registeredOn": "2026-01-08", "motherName": "Ayşe Yılmaz",
                "address": {"line": "Örnek Mah. 1. Sok. No 2", "district": "Selçuklu",
                            "city": "Konya"}},
    "guardians": [{"slot": 1, "phone": "5321112233", "nationalId": TC_MOTHER,
                   "occupation": "Öğretmen", "email": "Ayse@Ornek.com",
                   "homeAddress": {"line": "Örnek Mah. 1. Sok. No 2", "district": "Selçuklu",
                                   "city": "Konya"}}],
}


# ================================================================ kurallar


@pytest.mark.parametrize("value", VALID)
def test_tc_saglamasi_gecerli(value: str) -> None:
    assert rules.valid_national_id(value)


@pytest.mark.parametrize("value", INVALID)
def test_tc_saglamasi_gecersiz(value: str) -> None:
    assert not rules.valid_national_id(value)


def test_tc_hata_metinleri_turkce_ve_degersiz() -> None:
    assert rules.national_id_problem("1000000014") == "TC kimlik no 11 hane olmalı."
    assert rules.national_id_problem("01234567890") == "TC kimlik no 0 ile başlayamaz."
    assert "doğrulama" in str(rules.national_id_problem("10000000147"))
    with pytest.raises(IdentityError) as caught:
        rules.apply("STU-1", {}, [], {"student": {"nationalId": "10000000147",
                                                   "birthDate": "2099-01-01"}},
                    seal=None, unseal=None)
    assert [item["field"] for item in caught.value.details] == ["nationalId", "birthDate"]
    assert "10000000147" not in json.dumps(caught.value.details) + str(caught.value)


def test_maske() -> None:
    assert rules.mask("0146") == "*******0146"
    assert rules.mask("AB12", national=False) == "****AB12"
    assert rules.mask("") is None


# ====================================================== yazma, olay, şifre


async def test_kimlik_yazilir_sifreli_revizyon_olay_denetim(world: World) -> None:
    await elif_with_guardians(world)
    before = (await world.service._sync.profile("STU-1"))["revision"]  # type: ignore[index]
    seen = len(await world.events())

    result = await world.service.set_identity("STU-1", FULL_BODY, actor="Kayıt Görevlisi")
    assert result["changed"] is True and result["revision"] > before
    assert "student.nationalId" in result["fields"] and "guardian1.nationalId" in result["fields"]

    [event] = (await world.events())[seen:]
    assert event["type"] == "student.updated" and event["fields"] == ["identity"]
    assert event["subject"]["id"] == "STU-1"

    # Veritabanında, olayda ve denetim izinde düz TC YOK.
    rows = await world.store.fetch_all("SELECT * FROM mod_bbd_students_profile")
    dump = json.dumps(rows, ensure_ascii=False, default=str)
    assert TC_STUDENT not in dump and TC_MOTHER not in dump
    assert rows[0]["national_id"].startswith("v1:") and rows[0]["national_id_tail"] == "0146"
    assert TC_STUDENT not in json.dumps(await world.events())
    [log] = await world.store.fetch_all("SELECT * FROM mod_bbd_students_identity_log")
    assert log["actor"] == "Kayıt Görevlisi" and "student.nationalId" in log["fields"]
    assert TC_STUDENT not in json.dumps(log, default=str)

    # Aynı gövde ikinci kez: değişiklik yok → revizyon / olay yok.
    again = await world.service.set_identity("STU-1", FULL_BODY, actor="x")
    assert again["changed"] is False
    assert len(await world.events()) == seen + 1


async def test_liste_maskeli_eksik_rozeti_ve_okuma_izni(world: World) -> None:
    await elif_with_guardians(world)
    listed = await world.service.list_students()
    [row] = listed["students"]
    assert row["nationalIdMasked"] is None
    assert row["contractMissing"] == ["Öğrenci TC kimlik no", "Veli TC kimlik no",
                                      "Veli ev adresi"]

    await world.service.set_identity("STU-1", FULL_BODY)
    [row] = (await world.service.list_students())["students"]
    assert row["nationalIdMasked"] == "*******0146" and row["contractMissing"] == []
    assert TC_STUDENT not in json.dumps(row, ensure_ascii=False, default=str)

    # Rota: `manage` taşıyan tam görür, yalnız `view` taşıyan maskeli.
    routes.bind(world.service)
    manager = CurrentUser(id="u1", first_name="A", last_name="B", org_scope="bbd",
                          roles=["bbd_staff"], permissions={"bbd_students.view",
                                                            "bbd_students.manage"})
    viewer = CurrentUser(id="u2", first_name="C", last_name="D", org_scope="bbd",
                         roles=["accountant"], permissions={"bbd_students.view"})
    full = await routes.student_identity("STU-1", user=manager)
    masked = await routes.student_identity("STU-1", user=viewer)
    assert full["student"]["nationalId"] == TC_STUDENT
    assert full["guardians"][0]["nationalId"] == TC_MOTHER
    assert masked["student"]["nationalId"] is None
    assert masked["student"]["nationalIdMasked"] == "*******0146"
    assert masked["guardians"][0]["nationalIdMasked"] == "*******0078"
    assert TC_STUDENT not in json.dumps(masked, ensure_ascii=False)
    assert full["guardians"][0]["email"] == "ayse@ornek.com"
    assert full["student"]["birthDate"] == "2009-05-01"


async def test_veli_kimligi_kisiyle_gider_ve_telefonla_tasinir(world: World) -> None:
    await elif_with_guardians(world)
    await world.service.set_identity("STU-1", {"guardians": [
        {"slot": 1, "nationalId": TC_MOTHER}, {"slot": 2, "nationalId": TC_FATHER}]})
    # Birincil veli babaya geçer: kimlikler kişiyle yer değiştirir.
    await world.service.set_guardians("STU-1", [
        {"name": "Mehmet Yılmaz", "phone": "05054445566", "relation": "father"},
        {"name": "Ayşe Yılmaz", "phone": "05321112233", "relation": "mother"},
    ])
    world.canteen.rows["STU-1"]["parentPhone"] = "5054445566"
    view = await world.service.get_identity("STU-1", reveal=True)
    assert [(row["slot"], row["nationalId"]) for row in view["guardians"]] == [
        (1, TC_FATHER), (2, TC_MOTHER)]
    # Annenin telefonu düzeltilir: kimliği yeni telefona taşınır.
    await world.service.set_guardians("STU-1", [
        {"name": "Mehmet Yılmaz", "phone": "05054445566", "relation": "father"},
        {"name": "Ayşe Yılmaz", "phone": "05329998877", "relation": "mother"},
    ])
    view = await world.service.get_identity("STU-1", reveal=True)
    assert view["guardians"][1]["nationalId"] == TC_MOTHER
    # Eski telefonla gelen kimlik yazması reddedilir (liste değişti).
    with pytest.raises(IdentityError) as caught:
        await world.service.set_identity("STU-1", {"guardians": [
            {"slot": 2, "phone": "5321112233", "occupation": "x"}]})
    assert caught.value.status == 409


async def test_yeni_ogrencide_kimlik_ayni_islemde(world: World) -> None:
    result = await world.service.create_student({
        "firstName": "Elif", "lastName": "Yılmaz", "parentPhone": "05321112233",
        "parentName": "Ayşe Yılmaz", "parentRelation": "mother",
        "identity": {"student": {"nationalId": TC_STUDENT, "gender": "female"},
                     "guardians": [{"slot": 1, "nationalId": TC_MOTHER}]},
    }, actor="Kayıt")
    kantin_id = result["kantinId"]
    assert [event["type"] for event in await world.events()] == ["student.created"]
    view = await world.service.get_identity(kantin_id, reveal=True)
    assert view["student"]["nationalId"] == TC_STUDENT
    assert view["guardians"][0]["nationalId"] == TC_MOTHER
    assert view["guardians"][0]["relation"] == "mother"
    # Geçersiz TC: hiçbir şey yazılmaz (kantine de gidilmez).
    with pytest.raises(IdentityError):
        await world.service.create_student({"firstName": "Can", "lastName": "Ak",
                                            "identity": {"student": {"nationalId": "123"}}})
    assert len(world.canteen.rows) == 1


async def test_dizin_identity_blogu_k2_sekli_ve_eski_alanlar_degismez(world: World) -> None:
    await elif_with_guardians(world)
    await world.service.set_identity("STU-1", FULL_BODY)
    listing = await world.directory.list()
    [record] = listing["students"]
    assert tuple(record) == RECORD_KEYS            # eski StudentRecord birebir
    assert TC_STUDENT not in json.dumps(record, ensure_ascii=False)
    blocks = await world.directory.identities(listing["students"])
    block = blocks["STU-1"]
    assert set(block) == {"updatedAt", "student", "guardians"}
    assert block["updatedAt"].endswith("Z")
    assert block["student"] == {
        "nationalId": TC_STUDENT, "foreignId": None, "birthDate": "2009-05-01",
        "gender": "female", "registeredOn": "2026-01-08", "schoolName": None, "homePhone": None,
        "motherName": "Ayşe Yılmaz", "fatherName": None,
        "address": {"line": "Örnek Mah. 1. Sok. No 2", "district": "Selçuklu", "city": "Konya"}}
    mother, father = block["guardians"]
    assert mother == {
        "slot": 1, "fullName": "Ayşe Yılmaz", "nationalId": TC_MOTHER, "foreignId": None,
        "relation": "mother", "phone": "5321112233", "occupation": "Öğretmen",
        "workPhone": None, "email": "ayse@ornek.com",
        "homeAddress": {"line": "Örnek Mah. 1. Sok. No 2", "district": "Selçuklu",
                        "city": "Konya"}, "workAddress": None}
    assert father["slot"] == 2 and father["nationalId"] is None
    # Kasa yoksa blok YOK (Akademi aynası değişmez), hata yükselmez.
    bare = StudentDirectory(canteen=world.canteen, store=world.store, log=FakeLog(),
                            cipher=IdentityCipher(lambda: None))
    assert await bare.identities(listing["students"]) == {}
