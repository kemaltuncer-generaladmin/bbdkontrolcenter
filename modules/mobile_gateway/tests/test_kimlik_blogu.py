"""Sözleşme kimliği `identity` bloğu (iş 80-km-kimlik, bbd-akademi 80-ortak K2).

Blok YALNIZ `mobile_gateway.identity.read` taşıyan anahtara, `GET /directory` (tam,
`since`, `ids`) ve `GET /students/{id}` yanıtına EKLEMELİ gelir; taşımayanın yanıtı
birebir eskisidir. Bilinmeyen alan düşer; bloğu gelmeyen öğrencide anahtar YOK
(Akademi aynası değişmez). Kişiler uydurmadır.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from mobile_gateway_fakes import CAN, ELIF, MOTHER, FakeDirectory

from km_core.security.identity import CurrentUser
from modules.mobile_gateway.backend.api import routes

World = Any  # conftest.World — `conftest`ten import edilmez (test_contract_v1 notu)
TC = "10000000146"


class IdentityDirectory(FakeDirectory):
    def __init__(self) -> None:
        super().__init__()
        self.identity_calls: list[list[str]] = []

    async def get_many(self, ids: list[str]) -> dict[str, Any]:
        found = [deepcopy(self.records[item]) for item in ids if item in self.records]
        return {"students": found, "missingIds": [item for item in ids
                                                  if item not in self.records]}

    async def identities(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        self.identity_calls.append([str(record["id"]) for record in records])
        if not any(record["id"] == ELIF for record in records):
            return {}
        return {ELIF: {
            "updatedAt": "2026-10-28T12:00:00Z",
            "student": {"nationalId": TC, "foreignId": None, "birthDate": "2009-05-01",
                        "gender": "female", "registeredOn": "2026-01-08", "schoolName": None,
                        "homePhone": None, "motherName": "Ayşe Kaya", "fatherName": None,
                        "address": {"line": "Örnek Mah.", "district": "Selçuklu",
                                    "city": "Konya"},
                        "secretNote": "düşmeli"},
            "guardians": [
                {"slot": 1, "fullName": "Ayşe Kaya", "nationalId": "10000000078",
                 "foreignId": None, "relation": "mother", "phone": MOTHER,
                 "occupation": None, "workPhone": None, "email": None,
                 "homeAddress": {"line": "Örnek Mah.", "district": "", "city": "Konya"},
                 "workAddress": {"line": "", "district": "", "city": ""}},
                {"slot": 9, "fullName": "Fazla"},
            ],
        }}


def use(world: World) -> IdentityDirectory:
    directory = IdentityDirectory()
    world.replace("bbd_students.directory", directory)
    return directory


async def test_yetkisiz_anahtarin_yaniti_birebir_eskisi(service: Any, world: World) -> None:
    directory = use(world)
    reply = await service.directory(None, None)
    body = json.loads(reply.raw)
    assert all("identity" not in student for student in body["students"])
    one = await service.student_one(ELIF)
    assert "identity" not in one and directory.identity_calls == []


async def test_yetkili_anahtara_k2_blogu(service: Any, world: World) -> None:
    directory = use(world)
    plain = json.loads((await service.directory(None, None)).raw)
    reply = await service.directory(None, None, identity=True)
    body = json.loads(reply.raw)
    by_id = {student["id"]: student for student in body["students"]}
    block = by_id[ELIF]["identity"]
    assert block["updatedAt"] == "2026-10-28T12:00:00Z"
    assert block["student"]["nationalId"] == TC and "secretNote" not in block["student"]
    assert block["student"]["address"] == {"line": "Örnek Mah.", "district": "Selçuklu",
                                           "city": "Konya"}
    [mother] = block["guardians"]                      # yuva 9 düşer
    assert mother["slot"] == 1 and mother["phone"] == MOTHER and mother["relation"] == "mother"
    assert mother["homeAddress"] == {"line": "Örnek Mah.", "district": None, "city": "Konya"}
    assert mother["workAddress"] is None
    # Bloğu gelmeyen öğrencide anahtar YOK; eski alanlar birebir aynı.
    assert "identity" not in by_id[CAN]
    without = {key: value for key, value in by_id[ELIF].items() if key != "identity"}
    assert without == {s["id"]: s for s in plain["students"]}[ELIF]
    # Tek yetenek çağrısı (N+1 yok); iki görünümün önbelleği/ETag'i ayrı.
    assert len(directory.identity_calls) == 1 and len(directory.identity_calls[0]) >= 2
    assert body["etag"] != plain["etag"]

    one = await service.student_one(ELIF, identity=True)
    assert one["identity"]["student"]["nationalId"] == TC
    ids = await service.directory(None, None, ids_text=f"{ELIF},{CAN}", identity=True)
    assert [("identity" in item) for item in ids.body["students"]] == [True, False]


async def test_rota_izni_sorar(service: Any, world: World) -> None:
    use(world)
    routes.bind(service)
    base = {"id": "svc", "first_name": "Akademi", "last_name": "Servis", "org_scope": "org",
            "roles": ["mobile_service"]}
    old = CurrentUser(**base, permissions={"mobile_gateway.access"})
    new = CurrentUser(**base, permissions={"mobile_gateway.access",
                                           "mobile_gateway.identity.read"})
    assert "identity" not in await routes.student(ELIF, balance=None, user=old)
    assert (await routes.student(ELIF, balance=None, user=new))["identity"]["student"][
        "nationalId"] == TC
