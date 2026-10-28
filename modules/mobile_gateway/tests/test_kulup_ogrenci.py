"""§11 Deneme Kulübü öğrenci açılışı — `POST /students` (iş 81-km-kulup).

Uç YALNIZ `mobile_gateway.students.create` taşıyan anahtara açılır; kural öğrenci modülünün
`bbd_students.admission` yeteneğindedir (burada sahte). Geçit: izin, gövde, idempotensi
(Idempotency-Key), kural reddinin sözleşme zarfına taşınması, denetim izi (kişi verisi yok).
Kişiler uydurmadır.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from modules.mobile_gateway.backend.api import routes
from modules.mobile_gateway.backend.errors import GatewayError
from starlette.requests import Request

from km_core.security.identity import CurrentUser

World = Any  # conftest.World — `conftest`ten import edilmez (test_contract_v1 notu)


class Refusal(ValueError):
    def __init__(self, message: str, status: int, code: str, path: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.details = {"path": path} if path else {}


class FakeAdmission:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.by_ref: dict[str, dict[str, Any]] = {}
        self.refuse: Exception | None = None

    async def admit(self, payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"payload": payload, **kwargs})
        if self.refuse is not None:
            raise self.refuse
        ref = kwargs["external_ref"]
        if ref not in self.by_ref:
            existing = kwargs.get("student_id")
            self.by_ref[ref] = {"studentId": existing or f"STU-new{len(self.by_ref):06d}",
                                "created": existing is None,
                                "className": payload.get("className") or "Deneme Kulübü"}
        return dict(self.by_ref[ref])


BODY: dict[str, Any] = {
    "externalRef": "inv_01", "firstName": "Deniz", "lastName": "Örnek",
    "className": "Deneme Kulübü", "source": "club",
    "guardians": [{"slot": 1, "name": "Ayla Örnek", "phone": "5321110000",
                   "relation": "mother"}],
    "identity": {"student": {"nationalId": "10000000146"}},
}

BASE = {"id": "svc", "first_name": "Akademi", "last_name": "Servis", "org_scope": "org",
        "roles": ["mobile_service"]}
PLAIN = CurrentUser(**BASE, permissions={"mobile_gateway.access"})
ALLOWED = CurrentUser(**BASE, permissions={"mobile_gateway.access",
                                           "mobile_gateway.students.create"})


def request(key: str | None = "k-1") -> Request:
    headers = [(b"idempotency-key", key.encode())] if key else []
    return Request({"type": "http", "method": "POST", "scheme": "http",
                    "server": ("test", 80), "path": "/api/mobile_gateway/v1/students",
                    "query_string": b"", "headers": headers})


def use(world: World) -> FakeAdmission:
    admission = FakeAdmission()
    world.replace("bbd_students.admission", admission)
    return admission


async def post(service: Any, body: dict[str, Any], user: CurrentUser = ALLOWED,
               key: str | None = "k-1") -> tuple[int, dict[str, Any], dict[str, str]]:
    routes.bind(service)
    response = await routes.create_student(routes.StudentCreateBody(**body), request(key), user)
    return response.status_code, json.loads(response.body), dict(response.headers)


async def test_izinsiz_anahtar_ogrenci_acamaz(service: Any, world: World) -> None:
    admission = use(world)
    with pytest.raises(GatewayError) as caught:
        await post(service, BODY, user=PLAIN)
    assert caught.value.status == 403 and admission.calls == []


async def test_ogrenci_acilir_ve_istek_aynen_tekrarlanir(service: Any, world: World) -> None:
    admission = use(world)
    status, body, _ = await post(service, BODY)
    assert status == 200
    assert body == {"studentId": "STU-new000000", "created": True, "className": "Deneme Kulübü"}
    [call] = admission.calls
    assert call["external_ref"] == "inv_01" and call["source"] == "club"
    assert call["student_id"] is None and call["causation_id"] == "k-1"
    assert call["payload"]["identity"]["student"]["nationalId"] == "10000000146"
    assert "externalRef" not in call["payload"] and "source" not in call["payload"]

    # Aynı Idempotency-Key → saklı yanıt; yetenek ikinci kez çağrılmaz.
    status, again, headers = await post(service, BODY)
    assert again == body and headers.get("idempotent-replayed") == "true"
    assert len(admission.calls) == 1
    # Farklı anahtar, aynı davet → yetenek idempotent (aynı öğrenci).
    _, third, _ = await post(service, BODY, key="k-2")
    assert third["studentId"] == body["studentId"]


async def test_mevcut_ogrenci_eslesmesi(service: Any, world: World) -> None:
    admission = use(world)
    _, body, _ = await post(service, {**BODY, "externalRef": "inv_02",
                                      "studentId": "STU-a1b2c3d4e5"})
    assert body["studentId"] == "STU-a1b2c3d4e5" and body["created"] is False
    assert admission.calls[0]["student_id"] == "STU-a1b2c3d4e5"


async def test_kural_reddi_zarfa_tasinir(service: Any, world: World) -> None:
    admission = use(world)
    admission.refuse = Refusal("Eşleşme belirsiz — KM'den eşleştirin.", 409, "ambiguous_match")
    with pytest.raises(GatewayError) as caught:
        await post(service, BODY)
    assert (caught.value.status, caught.value.code) == (409, "ambiguous_match")
    admission.refuse = Refusal("TC kimlik no hatalı.", 422, "validation_error",
                               "identity.student.nationalId")
    with pytest.raises(GatewayError) as caught:
        await post(service, BODY, key="k-3")
    assert caught.value.status == 422
    assert caught.value.extra["fields"] == ["identity.student.nationalId"]


async def test_yetenek_yoksa_503(service: Any, world: World) -> None:
    world.remove("bbd_students.admission")
    with pytest.raises(GatewayError) as caught:
        await post(service, BODY)
    assert (caught.value.status, caught.value.code) == (503, "capability_missing")


async def test_denetim_izinde_kisi_verisi_yok(service: Any, world: World, store: Any) -> None:
    use(world)
    await post(service, BODY)
    rows = await store.fetch_all(f"SELECT action, detail FROM {store.table('audit')}")
    text = json.dumps(rows, ensure_ascii=False)
    assert "students.create" in text and "inv_01" in text
    for secret in ("Deniz", "5321110000", "10000000146", "Ayla"):
        assert secret not in text


async def test_govde_dogrulamasi() -> None:
    with pytest.raises(ValueError):
        routes.StudentCreateBody(**{**BODY, "guardians": []})
    with pytest.raises(ValueError):
        routes.StudentCreateBody(**{**BODY, "source": "store"})
