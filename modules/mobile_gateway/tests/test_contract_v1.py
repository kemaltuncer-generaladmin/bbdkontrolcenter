"""Sözleşme v1 — `km-mobile-gateway.md` (Ekler dahil),
`kantin-veli-kontrolleri.md` §2.2 ve `km-ogrenci-gruplari-ve-zil.md` §2.4, §3.1, §3.3
(öğrenci grupları, saat setleri, zil planı).

Her uç için: durum kodu, alan adları, hata kodları; ayrıca kilitler, hız
sınırları, idempotensi tekrarı/çatışması, 304 etag, eksik yetenek 503 ve
asla dönmeyecek alanlar (erişim kodu, SMS metni, akışlarda telefon).

İstekler gerçek FastAPI yığınından geçer: çekirdeğin `http.requires` kapısı
(`_permission_guard`) ve hata zarfı işleyicisi kuruludur (conftest).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import jsonschema
import pytest
import yaml
from mobile_gateway_fakes import (
    AUNT,
    CAN,
    CODES,
    DENIZ,
    ELIF,
    ELIF_PHONE,
    FATHER,
    GRANNY,
    MOTHER,
    FakeTabletRead,
    LegacyCanteen,
    RecordingLog,
)
from modules.mobile_gateway.backend.api import routes

# `conftest`ten import EDİLMEZ: başka modüllerin `conftest`i aynı adla
# yüklüyse yanlış dosya gelir. Gerekenler burada yeniden okunur.
MODULE_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = yaml.safe_load((MODULE_ROOT / "module.yaml").read_text(encoding="utf-8"))
World = Any  # conftest.World — taklitler ve registry
Clock = Any  # conftest.Clock — oynatılabilir saat

ACCESS = "mobile_gateway.access"


def on_behalf(text: str) -> dict[str, str]:
    """HTTP başlığı ASCII'dir: Türkçe ad yüzde kodlu UTF-8 gönderilir."""
    return {"X-KM-On-Behalf-Of": quote(text, safe=":")}

#: Sözleşmedeki uçların tamamı. Router'da fazlası ya da eksiği olamaz.
CONTRACT_ROUTES = {
    ("GET", "/directory"),
    # Merkezi senkron (geçit §9).
    ("GET", "/students/{student_id}"),
    ("GET", "/canteen/balances"),
    ("GET", "/events"),
    ("POST", "/auth/student-code"),
    ("POST", "/students/{student_id}/verify-code"),
    ("GET", "/students/{student_id}/canteen"),
    ("GET", "/students/{student_id}/canteen/qr"),
    ("GET", "/students/{student_id}/canteen/transactions"),
    ("GET", "/students/{student_id}/canteen/collections"),
    ("GET", "/students/{student_id}/payment-requests"),
    ("POST", "/students/{student_id}/payment-requests"),
    ("POST", "/students/{student_id}/canteen/payment-requests"),
    ("GET", "/students/{student_id}/canteen/controls"),
    ("PATCH", "/students/{student_id}/canteen/controls"),
    ("POST", "/students/{student_id}/canteen/collections"),
    ("GET", "/canteen/payment-requests"),
    ("GET", "/canteen/parent-notifications"),
    # kucuk-aciklar (geçit §3 ek): kurum geneli kantin borç toplamı (Akademi yönetici paneli).
    ("GET", "/canteen/totals"),
    ("GET", "/canteen/settings/public"),
    ("GET", "/canteen/products"),
    ("GET", "/tablets"),
    ("GET", "/tablets/usage"),
    ("GET", "/students/{student_id}/tablet-usage"),
    ("POST", "/students/{student_id}/tablet/grant"),
    ("POST", "/students/{student_id}/tablet/block"),
    ("POST", "/students/{student_id}/tablet/unblock"),
    ("POST", "/tablet-sessions/{session_id}/end"),
    # tablet-sinir: Akademi yöneticisinin uzaktan tablet sınırları.
    ("GET", "/students/{student_id}/tablet/limits"),
    ("PUT", "/tablet-profiles/{profile_id}/limits"),
    ("POST", "/students/{student_id}/tablet/grants/{grant_id}/revoke"),
    ("GET", "/tablet-profiles"),
    ("PUT", "/students/{student_id}/tablet/app-limits/{package_name}"),
    ("POST", "/announcements/call"),
    ("GET", "/schedule/bell-week"),
    ("GET", "/groups"),
    ("PUT", "/bell/plan"),
    ("GET", "/bell/plan/status"),
    ("POST", "/sms/otp"),
    # sms-kale (geçit §7 ek): teslim durumu, izleme özeti, yönetici sayaç sıfırlama.
    ("GET", "/sms/otp/{job_id}"),
    ("GET", "/sms/health"),
    ("POST", "/sms/limits/reset"),
    # Duyuru SMS'i (bbd-akademi README §14 madde 81).
    ("POST", "/sms/send"),
    ("GET", "/settings/public"),
}

STUDENT_KEYS = {"id", "firstName", "lastName", "displayName", "className", "schoolNo",
                "studentPhone", "guardians", "canteen", "gradeLevel", "track", "examTarget",
                "updatedAt", "phoneIssues",
                # Merkezi senkron (geçit §9).
                "status", "statusReason", "statusSince", "revision", "canteenSync"}
CONTROLS_KEYS = {"spendingLimit", "dailyLimit", "isBlocked", "guardianBlocked",
                 "restrictedProductIds", "spentToday", "balance", "controlsUpdatedAt"}
PR_FEED_KEYS = {"ref", "studentId", "amount", "status", "amountFixed", "linkUrl", "payToken",
                "origin", "createdAt", "updatedAt", "paidAt"}


def expect_error(response: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}
    error = body["error"]
    assert error["status"] == status and error["code"] == code
    assert isinstance(error["message"], str) and error["message"]
    return dict(error)


def leaked(text: str) -> list[str]:
    """Çıktıda görünmemesi gereken değerler: erişim kodları, OTP, SMS metni."""
    secrets = [*CODES, "583012", "Sayın veli", "Borç eşiği", "c2VjcmV0", "never"]
    return [value for value in secrets if value in text]


async def table(store: Any, name: str) -> list[dict[str, Any]]:
    return await store.fetch_all(f"SELECT * FROM {store.table(name)} ORDER BY 1")


# ---------------------------------------------------------- yüzey ve kapı


def test_router_carries_exactly_the_contract_routes() -> None:
    present = {(method, route.path) for route in routes.router.routes
               for method in route.methods}  # type: ignore[attr-defined]
    assert present == CONTRACT_ROUTES


def test_every_route_asks_the_access_permission_itself() -> None:
    """Çift kapı (K9): taban izin çekirdekte, aynı izin her ucun imzasında."""
    for route in routes.router.routes:
        found = set()
        for dependency in route.dependant.dependencies:  # type: ignore[attr-defined]
            call = dependency.call
            names = getattr(getattr(call, "__code__", None), "co_freevars", ())
            if "permissions" in names:
                found.update(call.__closure__[names.index("permissions")].cell_contents)
        assert found == {ACCESS}, route.path  # type: ignore[attr-defined]
    assert MANIFEST["http"]["requires"] == [ACCESS]
    permission = MANIFEST["permissions"][0]
    assert permission["key"] == ACCESS and "mobile_service" in permission["default_roles"]
    assert MANIFEST["http"]["prefix"] == "/api/mobile_gateway/v1"
    assert "ui" not in MANIFEST


async def test_unauthenticated_and_forbidden_use_the_contract_envelope(
        client: httpx.AsyncClient) -> None:
    anonymous = await client.get("/directory", headers={"Authorization": ""})
    expect_error(anonymous, 401, "unauthorized")
    staff = await client.get("/directory", headers={"Authorization": "Bearer staff"})
    expect_error(staff, 403, "forbidden")
    patched = await client.patch(f"/students/{ELIF}/canteen/controls",
                                 headers={"Authorization": "Bearer staff"},
                                 json={"dailyLimit": 1})
    expect_error(patched, 403, "forbidden")


def test_optional_capabilities_are_declared_optional() -> None:
    declared = {item["capability"]: bool(item.get("optional")) for item in MANIFEST["consumes"]}
    assert declared == {
        "secrets": False, "canteen.api": False, "bbd_students.directory": False,
        "bbd_tablet.read": True, "bbd_tablet.control": True,
        "bell.week": True, "bell.announce": True,
        "bell.plan": True, "bbd_students.groups": True, "events.outbox": True,
    }


def test_default_config_matches_schema_and_contract_texts() -> None:
    schema = json.loads((MODULE_ROOT / "config" / "schema.json").read_text(encoding="utf-8"))
    defaults = yaml.safe_load((MODULE_ROOT / "config" / "default.yaml").read_text("utf-8"))
    jsonschema.validate(defaults, schema)
    assert defaults["otp_template"] == \
        "BBD Akademi giris kodunuz: {code}. Kodu kimseyle paylasmayin."
    assert defaults["call_templates"]["page"] == "{ad} {soyad}, lütfen danışmaya gel."
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**defaults, "otp_template": "Kodunuz geldi."}, schema)


def test_module_imports_only_the_sdk() -> None:
    """K2/K3: modül çekirdeği ya da başka modülü import etmez."""
    for path in (MODULE_ROOT / "backend").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for forbidden in ("from km_core", "import km_core", "from km_platform",
                          "import km_platform", "from modules", "import modules"):
            assert forbidden not in source, f"{path.name}: {forbidden}"


async def test_register_resolves_capabilities_lazily(context: Any, world: World) -> None:
    from modules.mobile_gateway.backend.module import register

    world.remove("bbd_tablet.read")
    register(context)
    assert len(context.routers) == 1
    service = routes.current_service()
    assert service.caps.has("bbd_tablet.read") is False
    world.replace("bbd_tablet.read", world.tablet_read)  # sağlayıcı sonradan geldi
    assert service.caps.has("bbd_tablet.read") is True


# ================================================================ §1 dizin


async def test_directory_full_listing_shape(client: httpx.AsyncClient, world: World) -> None:
    response = await client.get("/directory")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"generatedAt", "full", "students", "classes", "complete", "etag"}
    assert body["full"] is True and body["complete"] is True
    assert body["generatedAt"] == "2026-10-12T09:30:00+03:00"
    assert body["classes"] == ["12-SAY", "8-A"]
    assert response.headers["etag"] == f'"{body["etag"]}"'
    assert len(body["etag"]) == 64
    assert world.directory.since_seen == [None]

    students = {item["id"]: item for item in body["students"]}
    assert list(students) == sorted(students)
    for item in students.values():
        assert set(item) == STUDENT_KEYS

    elif_ = students[ELIF]
    assert elif_ == {
        "id": ELIF, "firstName": "Elif", "lastName": "Kaya", "displayName": "Elif Kaya",
        "className": "8-A", "schoolNo": "1234", "studentPhone": ELIF_PHONE,
        # Dört yuva + yakınlık + birincil (veli-senkron-4); yuva 5 düşer.
        "guardians": [
            {"slot": 1, "name": "Ayşe Kaya", "phone": MOTHER, "relation": "mother",
             "primary": True},
            {"slot": 2, "name": "Mehmet Kaya", "phone": FATHER, "relation": "father",
             "primary": False},
            {"slot": 3, "name": "Fatma Kaya", "phone": GRANNY, "relation": "grandparent",
             "primary": False},
            {"slot": 4, "name": None, "phone": AUNT, "relation": None, "primary": False}],
        "canteen": {"balance": 4250, "spendingLimit": 20000, "isBlocked": False,
                    "dailyLimit": 15000, "guardianBlocked": False},
        "gradeLevel": 8, "track": None, "examTarget": "LGS",
        "updatedAt": "2026-10-11T18:02:11+03:00", "phoneIssues": [],
        # Eski dizin yeteneği bu alanları vermez: varsayılanlar (geçit §9).
        "status": "active", "statusReason": None, "statusSince": None, "revision": 0,
        "canteenSync": "ok",
    }
    # Telefonu olmayan veli listelenmez; nedeni phoneIssues'ta kalır.
    can = students[CAN]
    # Yakınlık beyaz listededir: büyük harf / boşluk onarılır, bilinmeyen null olur.
    assert can["guardians"] == [
        {"slot": 1, "name": "Ayşe Kaya", "phone": MOTHER, "relation": "mother", "primary": True},
        {"slot": 3, "name": "Komşu", "phone": "5441112233", "relation": None,
         "primary": False}]
    assert can["phoneIssues"] == ["guardian2Phone"] and can["studentPhone"] is None
    assert (can["gradeLevel"], can["track"], can["examTarget"]) == (12, "SAY", "YKS")
    assert can["canteen"]["isBlocked"] is True and can["canteen"]["guardianBlocked"] is True
    # Ham telefon normalize edilir; edilemeyen null olur ve adı yazılır.
    deniz = students[DENIZ]
    assert deniz["studentPhone"] == "5320000001"
    assert deniz["guardians"] == [] and deniz["phoneIssues"] == ["guardian1Phone"]
    assert deniz["displayName"] == "Deniz Ak" and deniz["canteen"] is None
    assert (deniz["gradeLevel"], deniz["track"], deniz["examTarget"]) == \
        ("graduate", "DIL", None)
    assert deniz["updatedAt"] == "2026-10-10T15:00:00+03:00"

    assert "accessCode" not in response.text and not leaked(response.text)


async def test_directory_student_classes_additive(client: httpx.AsyncClient,
                                                  world: World) -> None:
    """Sınıf = grup (§2.6): yeni öğrenci modülü `classes[{id, name}]` verirse geçer (beyaz
    listeli); `className` eski tekil değer olarak kalır. Eski modülde alan hiç yoktur."""
    world.directory.records[ELIF]["classes"] = [
        {"id": "c8a", "name": "8-A", "secret": "x"}, {"id": "0b9e", "name": "TYT Hızlandırma"},
        {"name": "kimliksiz"}]
    body = (await client.get("/directory")).json()
    students = {item["id"]: item for item in body["students"]}
    assert students[ELIF]["classes"] == [{"id": "c8a", "name": "8-A"},
                                         {"id": "0b9e", "name": "TYT Hızlandırma"}]
    assert students[ELIF]["className"] == "8-A"
    assert "classes" not in students[CAN]
    assert "secret" not in json.dumps(body)


async def test_directory_etag_304_and_change(client: httpx.AsyncClient, world: World) -> None:
    first = await client.get("/directory")
    etag = first.headers["etag"]
    same = await client.get("/directory", headers={"If-None-Match": etag})
    assert same.status_code == 304 and same.content == b""
    assert same.headers["etag"] == etag
    weak = await client.get("/directory", headers={"If-None-Match": f"W/{etag}, \"x\""})
    assert weak.status_code == 304

    # Müfredat alanları etag'e girer: yalnız hedef sınav değişse de liste değişir.
    world.directory.records[ELIF]["examTarget"] = "YKS"
    changed = await client.get("/directory", headers={"If-None-Match": etag})
    assert changed.status_code == 200 and changed.headers["etag"] != etag
    world.directory.records[ELIF]["gradeLevel"] = 9
    again = await client.get("/directory", headers={"If-None-Match": changed.headers["etag"]})
    assert again.status_code == 200


async def test_directory_since_incomplete_and_validation(
        client: httpx.AsyncClient, world: World) -> None:
    world.directory.complete = False
    # `+` URL'de boşluğa dönmüş olabilir; geçit onarır.
    delta = await client.get("/directory", params={"since": "2026-10-12T09:00:00 03:00"})
    assert delta.status_code == 200
    body = delta.json()
    assert body["full"] is False and body["complete"] is False
    # Dizin yeteneği `since`i saat dilimli datetime olarak alır.
    seen = world.directory.since_seen[-1]
    assert isinstance(seen, datetime) and seen.isoformat() == "2026-10-12T09:00:00+03:00"
    bad = await client.get("/directory", params={"since": "dün"})
    assert expect_error(bad, 422, "validation_error")["fields"] == ["since"]
    world.remove("bbd_students.directory")
    missing = await client.get("/directory")
    assert expect_error(missing, 503, "capability_missing")["capability"] == \
        "bbd_students.directory"


# ======================================================== §2 öğrenci şifresi


async def test_student_code_matches_student_and_guardian_phones(
        client: httpx.AsyncClient, world: World, store: Any, log: RecordingLog) -> None:
    by_mother = await client.post("/auth/student-code", json={"phone": MOTHER, "code": "483920"})
    assert by_mother.status_code == 200
    assert by_mother.json() == {"match": True, "studentId": ELIF}
    # Kardeşler aynı veli telefonunu kullanır; kod ayırt eder.
    sibling = await client.post("/auth/student-code", json={"phone": MOTHER, "code": "561207"})
    assert sibling.json() == {"match": True, "studentId": CAN}
    own = await client.post("/auth/student-code", json={"phone": ELIF_PHONE, "code": "483920"})
    assert own.json()["studentId"] == ELIF
    father_for_elif = await client.post("/auth/student-code",
                                        json={"phone": FATHER, "code": "483920"})
    assert father_for_elif.json() == {"match": True, "studentId": ELIF}
    # 3. ve 4. velinin telefonu da eşleşir (veli-senkron-4).
    for phone in (GRANNY, AUNT):
        extra = await client.post("/auth/student-code", json={"phone": phone, "code": "483920"})
        assert extra.json() == {"match": True, "studentId": ELIF}
    grandma_for_can = await client.post("/auth/student-code",
                                        json={"phone": GRANNY, "code": "561207"})
    assert grandma_for_can.json() == {"match": False}
    unknown = await client.post("/auth/student-code", json={"phone": MOTHER, "code": "000000"})
    assert unknown.json() == {"match": False}
    assert world.canteen.count("lookup_student_by_code") == 8  # istek başına bir kez

    for bad in ({"phone": "05329876543", "code": "483920"}, {"phone": MOTHER, "code": "48392"},
                {"phone": MOTHER}):
        expect_error(await client.post("/auth/student-code", json=bad), 422,
                     "validation_error")
    rejected = await client.post("/auth/student-code", json={"phone": MOTHER, "code": "12a456"})
    assert "12a456" not in rejected.text

    # Kod hiçbir yere yazılmaz: log, sayaç tablosu, idempotensi tablosu.
    assert not leaked(repr(log.records))
    for name in ("hits", "idempotency", "audit", "sms_log"):
        assert not leaked(repr(await table(store, name)))
    assert MOTHER not in repr(await table(store, "hits"))


async def test_student_code_phone_lockout_and_global_budget(
        client: httpx.AsyncClient, world: World, clock: Clock) -> None:
    for _ in range(5):
        wrong = await client.post("/auth/student-code", json={"phone": MOTHER, "code": "000000"})
        assert wrong.json() == {"match": False}
    calls = world.canteen.count("lookup_student_by_code")
    blocked = await client.post("/auth/student-code", json={"phone": MOTHER, "code": "483920"})
    error = expect_error(blocked, 423, "locked")
    assert 0 < error["retryAfterSeconds"] <= 900
    assert blocked.headers["retry-after"] == str(error["retryAfterSeconds"])
    assert world.canteen.count("lookup_student_by_code") == calls  # kantine gidilmedi
    # Kilit telefon başınadır: başka telefon etkilenmez.
    other = await client.post("/auth/student-code", json={"phone": ELIF_PHONE, "code": "483920"})
    assert other.json()["match"] is True

    clock.advance(seconds=901)
    reopened = await client.post("/auth/student-code", json={"phone": MOTHER, "code": "483920"})
    assert reopened.json()["match"] is True
    # Başarı sayacı sıfırlar.
    for _ in range(4):
        await client.post("/auth/student-code", json={"phone": MOTHER, "code": "000000"})
    assert (await client.post("/auth/student-code",
                              json={"phone": MOTHER, "code": "000000"})).status_code == 200

    # Kantinin 30/dk bütçesi tabletlerle paylaşılıyor: geçidin payı 10/dk.
    clock.advance(minutes=20)
    phones = [f"54{index:08d}" for index in range(12)]
    statuses = [(await client.post("/auth/student-code",
                                   json={"phone": phone, "code": "000000"})).status_code
                for phone in phones]
    assert statuses[:10] == [200] * 10
    limited = await client.post("/auth/student-code", json={"phone": FATHER, "code": "483920"})
    error = expect_error(limited, 429, "rate_limited")
    assert 0 < error["retryAfterSeconds"] <= 60
    clock.advance(seconds=61)
    assert (await client.post("/auth/student-code",
                              json={"phone": FATHER, "code": "483920"})).status_code == 200


async def test_student_code_canteen_down(client: httpx.AsyncClient, world: World) -> None:
    world.canteen.down = True
    down = await client.post("/auth/student-code", json={"phone": MOTHER, "code": "483920"})
    expect_error(down, 503, "canteen_unavailable")
    assert "483920" not in down.text


async def test_verify_code_and_student_lockout(client: httpx.AsyncClient, world: World,
                                               clock: Clock) -> None:
    ok = await client.post(f"/students/{ELIF}/verify-code", json={"code": "483920"})
    assert ok.status_code == 200 and ok.json() == {"valid": True}
    # Kod başka öğrenciye aitse yalnız valid:false (sızıntı yok).
    other = await client.post(f"/students/{ELIF}/verify-code", json={"code": "561207"})
    assert other.json() == {"valid": False} and CAN not in other.text
    # Kurum engelli öğrenci (kantin isBlocked) yine doğrulanır.
    blocked_student = await client.post(f"/students/{CAN}/verify-code", json={"code": "561207"})
    assert blocked_student.json() == {"valid": True}

    for _ in range(4):
        await client.post(f"/students/{ELIF}/verify-code", json={"code": "000000"})
    locked = await client.post(f"/students/{ELIF}/verify-code", json={"code": "483920"})
    error = expect_error(locked, 423, "locked")
    assert 0 < error["retryAfterSeconds"] <= 900
    # Kilit öğrenci başınadır.
    assert (await client.post(f"/students/{CAN}/verify-code",
                              json={"code": "561207"})).json() == {"valid": True}
    clock.advance(minutes=16)
    assert (await client.post(f"/students/{ELIF}/verify-code",
                              json={"code": "483920"})).json() == {"valid": True}
    expect_error(await client.post(f"/students/{ELIF}/verify-code", json={"code": "1"}),
                 422, "validation_error")


# ================================================================ §3 kantin


async def test_canteen_summary_shape(client: httpx.AsyncClient, world: World) -> None:
    response = await client.get(f"/students/{ELIF}/canteen", params={"days": 30})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"balance", "spendingLimit", "isBlocked", "transactions", "collections",
                         "pendingPaymentRequest", "dailyLimit", "guardianBlocked",
                         "restrictedProductIds", "spentToday"}
    assert (body["balance"], body["spendingLimit"], body["isBlocked"]) == (4250, 20000, False)
    assert (body["dailyLimit"], body["guardianBlocked"], body["restrictedProductIds"],
            body["spentToday"]) == (15000, False, [3, 17], 2500)
    assert body["transactions"] == [
        {"id": "8f0c-1", "at": "2026-10-11T15:41:00+03:00", "total": 3500, "reversed": False,
         "items": [{"name": "Tost", "qty": 1, "unitPrice": 2500, "lineTotal": 2500},
                   {"name": "Ayran", "qty": 2, "unitPrice": 500, "lineTotal": 1000}]},
        {"id": "8f0c-2", "at": "2026-10-10T15:41:00+03:00", "total": 1200, "reversed": True,
         "items": []},
    ]
    assert body["collections"] == [
        {"at": "2026-10-05T10:00:00+03:00", "amount": 5000, "method": "PAYMENT"},
        {"at": "2026-10-01T10:00:00+03:00", "amount": 10000, "method": "CASH"},
    ]
    assert body["pendingPaymentRequest"] == {
        "ref": "PR-01JPENDING", "amount": 4250,
        "linkUrl": "https://ode.bbdstore.com.tr/p/AB12CD",
        "createdAt": "2026-10-12T09:00:00+03:00", "payToken": "tok.pending",
        "amountFixed": False,
    }
    assert "accessCode" not in response.text and MOTHER not in response.text

    for days in (0, 181, "x"):
        expect_error(await client.get(f"/students/{ELIF}/canteen", params={"days": days}),
                     422, "validation_error")
    expect_error(await client.get("/students/STU-yok/canteen"), 404, "student_not_found")
    world.canteen.down = True
    expect_error(await client.get(f"/students/{ELIF}/canteen"), 503, "canteen_unavailable")


async def test_canteen_qr(client: httpx.AsyncClient, world: World) -> None:
    response = await client.get(f"/students/{ELIF}/canteen/qr")
    assert response.status_code == 200
    assert response.json() == {"qrText": "djEuQUJDREVGRw", "version": 1}
    expect_error(await client.get("/students/STU-yok/canteen/qr"), 404, "student_not_found")
    world.directory.down = True  # dizin kantine ulaşamıyor
    expect_error(await client.get(f"/students/{ELIF}/canteen/qr"), 503, "canteen_unavailable")


async def test_student_payment_requests_list(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/students/{ELIF}/payment-requests")
    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["ref"] for item in items] == ["PR-01JPENDING", "PR-01JPAID"]
    for item in items:
        assert set(item) == {"ref", "amount", "status", "createdAt", "paidAt", "linkUrl",
                             "payToken", "amountFixed"}
    assert items[1] == {"ref": "PR-01JPAID", "amount": 10000, "status": "PAID",
                        "createdAt": "2026-10-01T12:00:00+03:00",
                        "paidAt": "2026-10-01T12:05:00+03:00",
                        "linkUrl": "https://ode.bbdstore.com.tr/p/ZZ99", "payToken": "tok.paid",
                        "amountFixed": True}
    assert "Sayın veli" not in response.text and MOTHER not in response.text


@pytest.mark.parametrize("path", ["/students/{id}/payment-requests",
                                  "/students/{id}/canteen/payment-requests"])
async def test_create_payment_request(client: httpx.AsyncClient, world: World, store: Any,
                                      path: str) -> None:
    url = path.format(id=ELIF)
    full = await client.post(url)
    assert full.status_code == 201
    assert full.json() == {"ref": "PR-01JNEW", "amount": 4250, "status": "PENDING",
                           "linkUrl": "https://ode.bbdstore.com.tr/p/NEW1",
                           "amountFixed": False, "payToken": "tok.new"}
    partial = await client.post(url, json={"amount": 5000},
                                headers={**on_behalf("akademi:staff:42:Ayşe Yılmaz"),
                                         "X-Request-Id": "req-1"})
    assert partial.status_code == 201
    assert partial.json()["amount"] == 5000 and partial.json()["amountFixed"] is True
    assert ("create_payment_request", (ELIF, 5000)) in world.canteen.calls
    assert set(world.canteen.origins) == {"api"}  # kantin talebin kaynağını tutar
    assert MOTHER not in partial.text

    audit = await table(store, "audit")
    assert audit[-1]["action"] == "canteen.payment_request.create"
    assert audit[-1]["on_behalf_of"] == "akademi:staff:42:Ayşe Yılmaz"
    assert audit[-1]["request_id"] == "req-1" and audit[-1]["result"] == "ok"

    for reason, status in (("no_debt", 409), ("no_phone", 409), ("exceeds_debt", 422),
                           ("invalid_amount", 422)):
        world.canteen.fail_reason = reason
        error = expect_error(await client.post(url, json={"amount": 999999}), status, reason)
        if reason == "exceeds_debt":
            assert error["details"] == {"balance": 4250, "maxAmount": 4250}
        else:
            assert "details" not in error
    assert (await table(store, "audit"))[-1]["result"] == "invalid_amount"
    world.canteen.fail_reason = None
    expect_error(await client.post(url, json={"amount": 0}), 422, "validation_error")
    expect_error(await client.post(path.format(id="STU-yok")), 404, "student_not_found")


async def test_payment_request_idempotency_replay_and_conflict(
        client: httpx.AsyncClient, world: World, store: Any, clock: Clock) -> None:
    url = f"/students/{ELIF}/canteen/payment-requests"
    key = {"Idempotency-Key": "c0ffee-0001"}
    first = await client.post(url, json={"amount": 5000}, headers=key)
    replay = await client.post(url, json={"amount": 5000}, headers=key)
    assert first.status_code == replay.status_code == 201
    assert replay.json() == first.json()
    assert replay.headers["idempotent-replayed"] == "true"
    assert world.canteen.count("create_payment_request") == 1

    conflict = await client.post(url, json={"amount": 6000}, headers=key)
    expect_error(conflict, 409, "idempotency_conflict")
    other_route = await client.post(f"/students/{CAN}/canteen/payment-requests",
                                    json={"amount": 5000}, headers=key)
    expect_error(other_route, 409, "idempotency_conflict")
    expect_error(await client.post(url, headers={"Idempotency-Key": "has space"}),
                 422, "validation_error")

    # Hata saklanmaz: aynı anahtarla yeniden denenebilir.
    world.canteen.fail_reason = "no_debt"
    retry_key = {"Idempotency-Key": "retry-1"}
    expect_error(await client.post(url, headers=retry_key), 409, "no_debt")
    world.canteen.fail_reason = None
    assert (await client.post(url, headers=retry_key)).status_code == 201

    # Anahtar 24 saat saklanır; sonra yeni istek gibi işlenir.
    clock.advance(hours=24, seconds=1)
    fresh = await client.post(url, json={"amount": 6000}, headers=key)
    assert fresh.status_code == 201 and "idempotent-replayed" not in fresh.headers
    # Süresi dolan `retry-1` temizlendi; yeniden yazılan satır duruyor.
    rows = await table(store, "idempotency")
    assert {row["key"] for row in rows} == {"c0ffee-0001"}
    # Özet anahtarlıdır (HMAC): gövdenin düz sha256'sı tabloda yok.
    import hashlib

    from modules.mobile_gateway.backend.service import canonical

    plain = hashlib.sha256(canonical({
        "route": f"POST /api/mobile_gateway/v1{url}", "body": {"amount": 6000},
    }).encode()).hexdigest()
    assert rows[0]["request_hash"] != plain and len(rows[0]["request_hash"]) == 64
    assert world.vault.values["mobile_gateway.idempotency_pepper"]


async def test_legacy_canteen_client_is_adapted(client: httpx.AsyncClient,
                                                world: World) -> None:
    world.replace("canteen.api", LegacyCanteen())
    no_phone = await client.post(f"/students/{ELIF}/payment-requests")
    expect_error(no_phone, 409, "no_phone")
    amount = await client.post(f"/students/{ELIF}/payment-requests", json={"amount": 5000})
    assert expect_error(amount, 503, "capability_missing")["capability"] == \
        "canteen.api.create_payment_request(amount)"
    controls = await client.get(f"/students/{ELIF}/canteen/controls")
    assert controls.status_code == 200 and controls.json()["balance"] == 100
    feed = await client.get("/canteen/payment-requests")
    assert expect_error(feed, 503, "capability_missing")["capability"] == \
        "canteen.api.payment_requests_feed"


async def test_canteen_controls_get_and_patch(client: httpx.AsyncClient, world: World,
                                              store: Any) -> None:
    current = await client.get(f"/students/{ELIF}/canteen/controls")
    assert current.status_code == 200
    assert current.json() == {"spendingLimit": 20000, "dailyLimit": 15000, "isBlocked": False,
                              "guardianBlocked": False, "restrictedProductIds": [3, 17],
                              "spentToday": 2500, "balance": 4250,
                              "controlsUpdatedAt": "2026-10-12T09:30:00+03:00"}

    patched = await client.patch(
        f"/students/{ELIF}/canteen/controls",
        json={"dailyLimit": None, "guardianBlocked": True, "restrictedProductIds": [3]},
        headers={**on_behalf("akademi:guardian:7:Ayşe Kaya"), "Idempotency-Key": "ctl-1"},
    )
    assert patched.status_code == 200
    body = patched.json()
    assert set(body) == CONTROLS_KEYS
    assert body["dailyLimit"] is None and body["guardianBlocked"] is True
    assert body["restrictedProductIds"] == [3]
    # Gönderilmeyen alan kantine gitmez; null limit temizlemek için gider.
    assert ("update_controls", (ELIF, {"dailyLimit": None, "guardianBlocked": True,
                                       "restrictedProductIds": [3]})) in world.canteen.calls
    audit = (await table(store, "audit"))[-1]
    assert audit["action"] == "canteen.controls.update"
    assert audit["on_behalf_of"] == "akademi:guardian:7:Ayşe Kaya"
    assert json.loads(audit["detail"])["changes"]["guardianBlocked"] is True

    replay = await client.patch(f"/students/{ELIF}/canteen/controls",
                                json={"dailyLimit": None, "guardianBlocked": True,
                                      "restrictedProductIds": [3]},
                                headers={"Idempotency-Key": "ctl-1"})
    assert replay.json() == body and world.canteen.count("update_controls") == 1

    for bad in ({}, {"dailyLimit": -1}, {"spendingLimit": 100_000_001},
                {"restrictedProductIds": list(range(201))}, {"isBlocked": None}):
        expect_error(await client.patch(f"/students/{ELIF}/canteen/controls", json=bad),
                     422, "validation_error")
    expect_error(await client.patch(f"/students/{ELIF}/canteen/controls",
                                    json={"restrictedProductIds": [999999]}),
                 422, "validation_error")
    expect_error(await client.patch("/students/STU-yok/canteen/controls",
                                    json={"dailyLimit": 1}), 404, "student_not_found")
    expect_error(await client.get("/students/STU-yok/canteen/controls"), 404,
                 "student_not_found")
    world.canteen.down = True
    expect_error(await client.patch(f"/students/{ELIF}/canteen/controls",
                                    json={"dailyLimit": 1}), 503, "canteen_unavailable")


async def test_canteen_collections(client: httpx.AsyncClient, world: World,
                                   store: Any) -> None:
    body = {"amount": 1250, "localId": "0b9f3c1e-uuid", "note": "elden"}
    # Ham UTF-8 bayt da kabul edilir (Starlette latin-1 okur, geçit onarır).
    created = await client.post(f"/students/{ELIF}/canteen/collections", json=body,
                                headers={"X-KM-On-Behalf-Of":
                                         "akademi:staff:3:Müdür".encode()})
    assert created.status_code == 201
    assert created.json() == {"amount": 1250, "balance": 3000}
    assert ("record_collection", (ELIF, 1250, "0b9f3c1e-uuid")) in world.canteen.calls
    audit = (await table(store, "audit"))[-1]
    assert (audit["action"], audit["on_behalf_of"]) == \
        ("canteen.collection.create", "akademi:staff:3:Müdür")
    assert json.loads(audit["detail"]) == {"amount": 1250, "localId": "0b9f3c1e-uuid",
                                           "note": "elden"}

    for reason in ("no_debt", "exceeds_debt", "invalid_amount"):
        world.canteen.fail_reason = reason
        error = expect_error(await client.post(f"/students/{ELIF}/canteen/collections",
                                               json=body), 422, reason)
        assert error.get("details") == ({"balance": 4250, "maxAmount": 4250}
                                        if reason == "exceeds_debt" else None)
    world.canteen.fail_reason = None
    expect_error(await client.post(f"/students/{ELIF}/canteen/collections",
                                   json={"amount": 5}), 422, "validation_error")


async def test_payment_requests_feed(client: httpx.AsyncClient, world: World) -> None:
    response = await client.get("/canteen/payment-requests",
                                params={"since": "2026-10-01T00:00:00+03:00", "limit": 2})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"items", "nextSince", "hasMore"}
    assert body["hasMore"] is True
    assert [item["ref"] for item in body["items"]] == ["PR-01JPENDING", "PR-01JPAID"]
    for item in body["items"]:
        assert set(item) == PR_FEED_KEYS
    assert body["items"][0]["studentId"] == ELIF
    assert body["items"][0]["payToken"] == "tok.pending"
    assert body["items"][0]["origin"] == "admin"
    # Kantinin imleci epoch-ms gelir; geçit ISO'ya (+03:00, ms hassas) çevirir.
    assert body["nextSince"] == "2026-10-12T09:00:00.250+03:00"
    assert world.canteen.calls[-1] == ("payment_requests_feed",
                                       ("2026-10-01T00:00:00+03:00", 2))
    # Telefon ve SMS metni asla.
    assert MOTHER not in response.text and "Sayın veli" not in response.text

    # Kantin imleç vermezse satırlardan hesaplanır; satır yoksa `since` korunur.
    world.canteen.feed_cursor = {}
    computed = (await client.get("/canteen/payment-requests")).json()
    assert computed["nextSince"] == "2026-10-12T09:00:00.250+03:00"
    assert computed["hasMore"] is False
    world.canteen.requests = []
    empty = await client.get("/canteen/payment-requests",
                             params={"since": "2026-10-12T09:00:00+03:00"})
    assert empty.json() == {"items": [], "nextSince": "2026-10-12T09:00:00+03:00",
                            "hasMore": False}
    for limit in (0, 501):
        expect_error(await client.get("/canteen/payment-requests", params={"limit": limit}),
                     422, "validation_error")


async def test_parent_notifications_feed(client: httpx.AsyncClient) -> None:
    response = await client.get("/canteen/parent-notifications", params={"limit": 50})
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == [
        {"id": 71, "studentId": ELIF, "kind": "payment_request", "amount": 4250,
         "createdAt": "2026-10-12T09:00:00+03:00"},
        {"id": 72, "studentId": CAN, "kind": "debt_threshold", "amount": None,
         "createdAt": "2026-10-12T08:00:00+03:00"},
    ]
    assert body["nextSince"] == "2026-10-12T09:00:01.500+03:00"
    assert body["hasMore"] is False
    assert MOTHER not in response.text and not leaked(response.text)
    assert "Borç eşiği" not in response.text


async def test_canteen_totals_cached_and_errors(client: httpx.AsyncClient, world: World,
                                               clock: Clock) -> None:
    response = await client.get("/canteen/totals")
    assert response.status_code == 200
    body = response.json()
    assert body.pop("generatedAt")
    assert body == {"totalDebt": 4250, "spentToday": 18750, "pendingPaymentRequests": 2,
                    "blockedStudents": 1, "totalStudents": 2}
    assert not leaked(response.text)
    await client.get("/canteen/totals")
    clock.advance(seconds=29)
    await client.get("/canteen/totals")
    assert world.canteen.count("dashboard") == 1  # 30 sn önbellek
    clock.advance(seconds=2)
    world.canteen.down = True
    expect_error(await client.get("/canteen/totals"), 503, "canteen_unavailable")
    world.canteen.down = False
    world.remove("canteen.api")
    expect_error(await client.get("/canteen/totals"), 503, "capability_missing")


async def test_canteen_settings_public_and_products(client: httpx.AsyncClient, world: World,
                                                    clock: Clock) -> None:
    settings = await client.get("/canteen/settings/public")
    assert settings.status_code == 200
    assert settings.json() == {"allowPrepayment": True, "maxPrepayment": 50000}

    products = await client.get("/canteen/products")
    assert products.status_code == 200
    assert products.json() == {"items": [
        {"id": 3, "name": "Tost", "price": 2500, "active": True, "imageUrl": None},
        {"id": 17, "name": "Kola", "price": 3000, "active": False,
         "imageUrl": "https://kantin/img/17.png"},
    ]}
    await client.get("/canteen/products")
    clock.advance(seconds=299)
    await client.get("/canteen/products")
    assert world.canteen.count("products") == 1  # 5 dk önbellek
    clock.advance(seconds=2)
    await client.get("/canteen/products")
    assert world.canteen.count("products") == 2

    world.remove("canteen.api")
    clock.advance(minutes=10)
    expect_error(await client.get("/canteen/products"), 503, "capability_missing")


# ================================================================ §4 tablet


async def test_tablets_board(client: httpx.AsyncClient) -> None:
    response = await client.get("/tablets")
    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == {"free": 6, "inUse": 9, "offline": 2, "locked": 1}
    assert body["devices"][0] == {
        "id": 12, "name": "Tablet 07", "state": "in_use", "battery": 64, "charging": False,
        "lastSeenAt": "2026-10-12T09:29:40+03:00",
        "activeSession": {"id": 991, "studentId": ELIF, "studentName": "Elif Kaya",
                          "loginAt": "2026-10-12T09:00:00+03:00"},
    }
    assert body["devices"][1]["activeSession"] is None
    assert "never" not in response.text


async def test_tablet_usage(client: httpx.AsyncClient, world: World) -> None:
    response = await client.get(f"/students/{ELIF}/tablet-usage",
                                params={"from": "2026-10-01", "to": "2026-10-12"})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"days", "sessions", "activeSession", "blockedUntil"}
    assert body["days"][0]["apps"][1] == {"package": "com.unknown", "label": None,
                                          "seconds": 1800}
    assert body["sessions"][0] == {"id": 991, "deviceName": "Tablet 07",
                                   "loginAt": "2026-10-12T09:00:00+03:00", "endedAt": None,
                                   "endReason": None}
    assert body["activeSession"] == {"id": 991, "deviceName": "Tablet 07",
                                     "loginAt": "2026-10-12T09:00:00+03:00"}
    assert world.tablet_read.usage_args[-1] == (ELIF, "2026-10-01", "2026-10-12")

    span = await client.get(f"/students/{ELIF}/tablet-usage",
                            params={"from": "2026-08-01", "to": "2026-10-12"})
    expect_error(span, 422, "validation_error")
    ok_62 = await client.get(f"/students/{ELIF}/tablet-usage",
                             params={"from": "2026-08-12", "to": "2026-10-12"})
    assert ok_62.status_code == 200
    reversed_ = await client.get(f"/students/{ELIF}/tablet-usage",
                                 params={"from": "2026-10-12", "to": "2026-10-01"})
    expect_error(reversed_, 422, "validation_error")
    expect_error(await client.get("/students/STU-yok/tablet-usage"), 404, "student_not_found")


async def test_tablets_daily_usage(client: httpx.AsyncClient, world: World) -> None:
    """`GET /tablets/usage?date=` — Akademi `TabletProvider.daily_usage` (kurum geneli)."""
    response = await client.get("/tablets/usage", params={"date": "2026-10-11"})
    assert response.status_code == 200, response.text
    assert response.json() == {"date": "2026-10-11", "items": [
        {"studentId": ELIF, "totalSeconds": 5400, "blockedUntil": None},
        {"studentId": CAN, "totalSeconds": 0, "blockedUntil": "2026-10-20T18:00:00+03:00"},
    ]}
    assert world.tablet_read.daily_args == ["2026-10-11"]

    today = await client.get("/tablets/usage")              # tarih yoksa bugün (İstanbul)
    assert today.status_code == 200 and today.json()["date"] == "2026-10-12"
    oldest = await client.get("/tablets/usage", params={"date": "2026-08-12"})
    assert oldest.status_code == 200                         # bugün dahil 62. gün
    for bad in ("2026-10-13", "2026-08-11", "12.10.2026", "2026-02-30"):
        expect_error(await client.get("/tablets/usage", params={"date": bad}),
                     422, "validation_error")
    assert world.tablet_read.daily_args == ["2026-10-11", "2026-10-12", "2026-08-12"]

    world.tablet_read.items = [{"studentId": "", "totalSeconds": 5}, "bozuk",
                               {"studentId": DENIZ, "totalSeconds": -3}]
    cleaned = await client.get("/tablets/usage", params={"date": "2026-10-11"})
    assert cleaned.json()["items"] == [{"studentId": DENIZ, "totalSeconds": 0,
                                        "blockedUntil": None}]

    # Eski tablet modülü (`daily_usage` yok): yalnız bu uç 503, diğerleri çalışır.
    world.replace("bbd_tablet.read", FakeTabletRead())
    missing = expect_error(await client.get("/tablets/usage"), 503, "capability_missing")
    assert missing["capability"] == "bbd_tablet.read.daily_usage"
    assert (await client.get("/tablets")).status_code == 200


async def test_tablet_controls(client: httpx.AsyncClient, world: World, store: Any) -> None:
    who = on_behalf("akademi:staff:42:Ayşe Yılmaz")
    grant = await client.post(f"/students/{ELIF}/tablet/grant", headers=who,
                              json={"packageName": "com.x", "extraSeconds": 1800,
                                    "reason": "Ödev için"})
    assert grant.status_code == 201 and grant.json() == {"grantId": 55}
    assert world.tablet_control.calls[-1] == ("grant", {
        "student_id": ELIF, "package_name": "com.x", "extra_seconds": 1800,
        "reason": "Ödev için", "actor": "Akademi Servis (akademi:staff:42:Ayşe Yılmaz)"})

    block = await client.post(f"/students/{ELIF}/tablet/block", headers=who,
                              json={"until": "2026-10-13T08:00:00+03:00", "reason": "Ceza"})
    assert block.status_code == 200
    assert block.json() == {"blockedUntil": "2026-10-13T08:00:00+03:00"}
    forever = await client.post(f"/students/{ELIF}/tablet/block",
                                json={"until": None, "reason": "Süresiz"})
    assert forever.json() == {"blockedUntil": None}
    unblock = await client.post(f"/students/{ELIF}/tablet/unblock", json={"reason": "Bitti"})
    assert unblock.status_code == 200 and unblock.json() == {"blockedUntil": None}

    ended = await client.post("/tablet-sessions/991/end", json={"reason": "Derse dön"})
    assert ended.status_code == 200 and ended.json() == {"ended": True}
    expect_error(await client.post("/tablet-sessions/404/end", json={"reason": "Yok"}),
                 404, "session_not_found")
    expect_error(await client.post("/tablet-sessions/busy/end", json={"reason": "Meşgul"}),
                 409, "conflict")
    expect_error(await client.post(f"/students/{ELIF}/tablet/grant",
                                   json={"extraSeconds": 60, "reason": "ab"}),
                 422, "validation_error")
    expect_error(await client.post(f"/students/{ELIF}/tablet/grant",
                                   json={"extraSeconds": 60, "reason": "  a      "}),
                 422, "validation_error")
    expect_error(await client.post(f"/students/{ELIF}/tablet/unblock", json={}),
                 422, "validation_error")
    expect_error(await client.post("/students/STU-yok/tablet/grant",
                                   json={"packageName": "com.x", "extraSeconds": 60, "reason": "Deneme"}),
                 404, "student_not_found")
    package = expect_error(await client.post(f"/students/{ELIF}/tablet/grant",
                                             json={"packageName": None, "extraSeconds": 60,
                                                   "reason": "Deneme"}),
                           422, "validation_error")
    assert package["message"] == "Ek süre uygulama bazındadır; uygulama seçilmeli."
    expect_error(await client.post(f"/students/{DENIZ}/tablet/grant",
                                   json={"packageName": "com.x", "extraSeconds": 60, "reason": "Deneme"}),
                 503, "canteen_unavailable")
    actions = [row["action"] for row in await table(store, "audit")]
    assert actions[:5] == ["tablet.grant", "tablet.block", "tablet.block", "tablet.unblock",
                           "tablet.session.end"]


async def test_tablet_capability_missing(client: httpx.AsyncClient, world: World) -> None:
    world.remove("bbd_tablet.read", "bbd_tablet.control")
    for method, url, body in (
        ("GET", "/tablets", None),
        ("GET", "/tablets/usage", None),
        ("GET", f"/students/{ELIF}/tablet-usage", None),
        ("POST", f"/students/{ELIF}/tablet/grant", {"extraSeconds": 60, "reason": "Ödev"}),
        ("POST", f"/students/{ELIF}/tablet/block", {"until": None, "reason": "Ceza"}),
        ("POST", f"/students/{ELIF}/tablet/unblock", {"reason": "Bitti"}),
        ("POST", "/tablet-sessions/991/end", {"reason": "Bitti"}),
    ):
        response = await client.request(method, url, json=body)
        error = expect_error(response, 503, "capability_missing")
        assert error["capability"].startswith("bbd_tablet.")
    # Modül ayakta: tabletle ilgisiz uçlar çalışır.
    assert (await client.get("/settings/public")).json()["features"]["tablet"] is False


# ================================================================ §5 anons


async def test_call_student(client: httpx.AsyncClient, world: World, clock: Clock,
                            store: Any) -> None:
    called = await client.post("/announcements/call",
                               json={"studentId": ELIF, "template": "page", "text": None},
                               headers={"X-Request-Id": "req-9",
                                        **on_behalf("akademi:staff:1:Danışma")})
    assert called.status_code == 202
    assert called.json() == {"status": "queued", "voiceCached": True, "etaSeconds": 3}
    page = world.announce.pages[-1]
    assert page["text"] == "Elif Kaya, lütfen danışmaya gel."
    assert page["cache_key"] == f"mobile_gateway:call:page:{ELIF}"
    assert page["request_id"] == "req-9" and "Danışma" in page["actor"]

    again = await client.post("/announcements/call", json={"studentId": ELIF})
    error = expect_error(again, 429, "rate_limited")
    assert 0 < error["retryAfterSeconds"] <= 60
    custom = await client.post("/announcements/call", json={
        "studentId": CAN, "template": "custom", "text": "{sinif} sınıfından {ad} {soyad} kapıya"})
    assert custom.status_code == 202
    assert world.announce.pages[-1]["text"] == "12-SAY sınıfından Can Kaya kapıya"
    assert world.announce.pages[-1]["cache_key"] is None
    clock.advance(seconds=61)
    assert (await client.post("/announcements/call",
                              json={"studentId": ELIF})).status_code == 202

    for bad in ({"studentId": ELIF, "template": "custom"},
                {"studentId": ELIF, "template": "custom", "text": "x" * 161},
                {"studentId": ELIF, "template": "custom", "text": "{telefon} gel"},
                {"studentId": ELIF, "template": "shout"}):
        expect_error(await client.post("/announcements/call", json=bad), 422,
                     "validation_error")
    expect_error(await client.post("/announcements/call", json={"studentId": "STU-yok"}),
                 404, "student_not_found")
    audit = [row for row in await table(store, "audit") if row["action"] == "announcement.call"]
    assert audit[0]["request_id"] == "req-9" and audit[0]["student_id"] == ELIF


async def test_call_student_bell_states(client: httpx.AsyncClient, world: World,
                                        clock: Clock) -> None:
    world.announce.status = "disabled"
    expect_error(await client.post("/announcements/call", json={"studentId": ELIF}),
                 409, "bell_disabled")
    world.announce.status = "tts_unavailable"
    expect_error(await client.post("/announcements/call", json={"studentId": ELIF}),
                 503, "tts_unavailable")
    # Başarısız çağrı bekleme süresi başlatmaz.
    world.announce.status = "queued"
    assert (await client.post("/announcements/call",
                              json={"studentId": ELIF})).status_code == 202
    world.remove("bell.announce")
    clock.advance(minutes=5)
    missing = await client.post("/announcements/call", json={"studentId": ELIF})
    assert expect_error(missing, 503, "capability_missing")["capability"] == "bell.announce"


# ======================================================== §6 zil saatleri


async def test_bell_week(client: httpx.AsyncClient, world: World) -> None:
    response = await client.get("/schedule/bell-week")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"times", "profiles", "groups", "available", "reason"}
    assert list(body["times"]) == ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    # Saat setleri (§3.1, ekleyici): gün bağımsız; iç alanlar (`sort`) düşer.
    assert body["profiles"] == [{"id": "yarim-gun", "name": "Yarım gün", "times": [
        {"time": "08:40", "label": "", "kind": "ders"},
        {"time": "09:20", "label": "", "kind": "teneffus"}]}]
    assert body["times"]["mon"] == [{"id": "t1", "time": "08:30", "label": "1. ders",
                                     "kind": "ders"}]
    assert body["groups"] == [{"id": "g1", "name": "Ortaokul", "kind": "grup", "sort": 1}]
    assert (body["available"], body["reason"]) == (True, None)

    # bbd_class_schedule /week ile aynı: kaynak yoksa boş hafta + neden (200).
    world.remove("bell.week")
    missing = (await client.get("/schedule/bell-week")).json()
    assert missing["available"] is False and missing["reason"]
    assert missing["times"]["fri"] == [] and missing["groups"] == []
    assert missing["profiles"] == []


async def test_bell_week_without_profiles_from_older_bell(client: httpx.AsyncClient,
                                                          world: World) -> None:
    """Saat seti bilmeyen zil sürümü: `profiles` boş liste, uç yine 200."""
    async def old_week() -> dict[str, Any]:
        return {"times": {"mon": []}, "groups": []}

    world.replace("bell.week", old_week)
    body = (await client.get("/schedule/bell-week")).json()
    assert body["profiles"] == [] and body["available"] is True


# ================================================= §9 öğrenci grupları

GROUP_KEYS = {"id", "name", "callName", "kind", "archived", "archivedAt", "updatedAt",
              "revision", "members"}


async def test_groups_full_listing_shape(client: httpx.AsyncClient, world: World) -> None:
    response = await client.get("/groups")
    assert response.status_code == 200, response.text
    body = response.json()
    assert list(body) == ["generatedAt", "revision", "full", "complete", "groups", "etag"]
    assert body["generatedAt"] == "2026-10-12T09:30:00+03:00"
    assert (body["revision"], body["full"], body["complete"]) == (4182, True, True)
    assert response.headers["etag"] == f'"{body["etag"]}"'
    # Revizyon sırası; arşivliler tam listede vardır.
    assert [group["id"] for group in body["groups"]] == ["aa01", "0b9e", "77c1"]
    for group in body["groups"]:
        assert set(group) == GROUP_KEYS
        for member in group["members"]:
            assert set(member) == {"studentId", "validFrom", "validTo"}  # telefon/gerekçe yok
    tyt = body["groups"][1]
    assert tyt == {
        "id": "0b9e", "name": "TYT Hızlandırma", "callName": "TYT grubu", "kind": "group",
        "archived": False, "archivedAt": None, "updatedAt": "2026-10-12T09:12:44+03:00",
        "revision": 4181,
        "members": [{"studentId": ELIF, "validFrom": "2026-09-15", "validTo": None},
                    {"studentId": CAN, "validFrom": "2026-09-15", "validTo": "2026-10-09"}],
    }
    assert body["groups"][2]["kind"] == "individual"
    assert body["groups"][0]["archived"] is True
    assert "gizli gerekçe" not in response.text
    assert world.groups.lists == [None]


async def test_groups_since_incremental_and_304(client: httpx.AsyncClient,
                                                world: World) -> None:
    full = await client.get("/groups")
    again = await client.get("/groups", headers={"If-None-Match": full.headers["etag"]})
    assert again.status_code == 304 and again.content == b""

    delta = await client.get("/groups", params={"since": 4181})
    body = delta.json()
    assert (body["full"], body["revision"]) == (False, 4182)
    assert [group["id"] for group in body["groups"]] == ["77c1"]
    assert body["groups"][0]["members"] == [
        {"studentId": DENIZ, "validFrom": "2026-09-01", "validTo": None}]
    assert world.groups.lists[-1] == 4181

    nothing = (await client.get("/groups", params={"since": 4182})).json()
    assert nothing["groups"] == [] and nothing["full"] is False

    # İstemci KM'den ileride (KM yedekten döndü): tam liste, `full: true`.
    ahead = (await client.get("/groups", params={"since": 9999})).json()
    assert ahead["full"] is True and len(ahead["groups"]) == 3


async def test_groups_include_classes_single_class_model(client: httpx.AsyncClient,
                                                        world: World) -> None:
    """§2.6: `include=classes` → bütün sınıflar + `legacyClassName`/`classLevel`; eski yanıt aynı."""
    old = (await client.get("/groups")).json()
    assert "classesIncluded" not in old
    assert [group["id"] for group in old["groups"]] == ["aa01", "0b9e", "77c1"]

    response = await client.get("/groups", params={"include": "classes"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert list(body) == ["generatedAt", "revision", "full", "complete", "classesIncluded",
                          "groups", "etag"]
    assert body["classesIncluded"] is True
    assert [group["id"] for group in body["groups"]] == ["aa01", "c8a", "0b9e", "77c1"]
    for group in body["groups"]:
        assert set(group) == GROUP_KEYS | {"legacyClassName", "classLevel", "academicYear"}
    eight = body["groups"][1]
    assert eight["legacyClassName"] == "8-A"
    assert eight["academicYear"] == "2026-2027" and body["groups"][2]["academicYear"] is None
    assert eight["classLevel"] == {"gradeLevel": 8, "track": None, "examTarget": "LGS"}
    assert body["groups"][2]["legacyClassName"] is None and body["groups"][2]["classLevel"] is None
    assert "origin" not in response.text
    # Görünümlerin ETag'i ve önbelleği ayrı.
    assert body["etag"] != old["etag"]
    again = await client.get("/groups", params={"include": "classes"},
                             headers={"If-None-Match": response.headers["etag"]})
    assert again.status_code == 304
    cross = await client.get("/groups", headers={"If-None-Match": response.headers["etag"]})
    assert cross.status_code == 200 and "classesIncluded" not in cross.json()
    assert world.groups.views == ["legacy", "classes"]

    delta = (await client.get("/groups", params={"since": 4181, "include": "classes"})).json()
    assert delta["classesIncluded"] is True and [g["id"] for g in delta["groups"]] == ["77c1"]

    bad = expect_error(await client.get("/groups", params={"include": "hepsi"}),
                       422, "validation_error")
    assert bad["fields"] == ["include"]


async def test_groups_include_classes_old_student_module(client: httpx.AsyncClient,
                                                         world: World) -> None:
    """Eski öğrenci modülü `view` almaz: eski yanıt, `classesIncluded` YOK (Akademi eski kipte)."""
    legacy_list = world.groups.list

    async def old_list(since_revision: int | None = None) -> dict[str, Any]:
        result = await legacy_list(since_revision)
        result.pop("view", None)
        return result

    world.groups.list = old_list  # type: ignore[method-assign]
    body = (await client.get("/groups", params={"include": "classes"})).json()
    assert "classesIncluded" not in body
    assert [group["id"] for group in body["groups"]] == ["aa01", "0b9e", "77c1"]
    assert all("legacyClassName" not in group for group in body["groups"])


async def test_groups_include_classes_tombstone_and_year(client: httpx.AsyncClient,
                                                        world: World) -> None:
    """İş 30: kalıcı silinen sınıf `since` akışında mezar taşı; eski görünümde yok;
    bozuk dönem null'a düşer (beyaz liste)."""
    world.groups.tombstones.append({"id": "dd01", "deleted": True,
                                    "deletedAt": "2026-10-14T22:40:00+03:00", "revision": 4183,
                                    "name": "gizli ad", "reason": "gizli gerekçe"})
    world.groups.classes[0]["academicYear"] = "2026-2028"
    world.groups.current = 4183
    delta = (await client.get("/groups", params={"since": 4181, "include": "classes"})).json()
    assert [g["id"] for g in delta["groups"]] == ["77c1", "dd01"]
    assert delta["groups"][1] == {"id": "dd01", "deleted": True,
                                  "deletedAt": "2026-10-14T22:40:00+03:00", "revision": 4183}
    old = (await client.get("/groups", params={"since": 4181})).json()
    assert [g["id"] for g in old["groups"]] == ["77c1"]
    full = (await client.get("/groups", params={"include": "classes"})).json()
    assert "dd01" not in [g["id"] for g in full["groups"]]
    assert next(g for g in full["groups"] if g["id"] == "c8a")["academicYear"] is None


@pytest.mark.parametrize("since", ["abc", "-1", "1.5", "1e3"])
async def test_groups_since_validation(since: str, client: httpx.AsyncClient) -> None:
    error = expect_error(await client.get("/groups", params={"since": since}),
                         422, "validation_error")
    assert error["fields"] == ["since"]


async def test_groups_incomplete_and_capability_missing(client: httpx.AsyncClient,
                                                        world: World) -> None:
    world.groups.complete = False
    body = (await client.get("/groups")).json()
    assert body["complete"] is False
    world.groups.complete = True
    assert (await client.get("/groups")).json()["complete"] is True  # yarım liste saklanmadı

    world.remove("bbd_students.groups")
    missing = expect_error(await client.get("/groups"), 503, "capability_missing")
    assert missing["capability"] == "bbd_students.groups"


# ======================================================== §10 zil planı


def plan_day(day: str, *, revision: int = 912, plan_hash: str = "c41e", mode: str = "normal",
             profile_id: str | None = None, calls: list[dict[str, Any]] | None = None,
             reason: str | None = None) -> dict[str, Any]:
    return {"date": day, "mode": mode, "profileId": profile_id, "reason": reason,
            "revision": revision, "planHash": plan_hash, "calls": calls or []}


CALL = {"ref": "6a1f", "at": "09:00", "groupKind": "group", "groupName": "TYT grubu",
        "groupRef": "km_group:0b9e", "teacherName": "Hüseyin hoca", "room": "B-2",
        "subject": "Matematik", "kind": "lesson"}


def plan_body(*days: dict[str, Any]) -> dict[str, Any]:
    return {"from": days[0]["date"], "to": days[-1]["date"], "days": list(days)}


async def test_put_bell_plan_day_results(client: httpx.AsyncClient, world: World,
                                         store: Any) -> None:
    body = plan_body(
        plan_day("2026-10-12", calls=[CALL]),
        plan_day("2026-10-13", mode="profile", profile_id="yarim-gun", reason="Deneme günü"),
        plan_day("2026-10-14", mode="profile", profile_id="bilinmeyen"),
    )
    response = await client.put("/bell/plan", json=body,
                                headers=on_behalf("akademi:bell-plan"))
    assert response.status_code == 200, response.text
    result = response.json()
    assert set(result) == {"days", "voicesPending", "bellEnabled"}
    assert result["days"] == [
        {"date": "2026-10-12", "result": "applied", "revision": 912},
        {"date": "2026-10-13", "result": "applied", "revision": 912},
        {"date": "2026-10-14", "result": "rejected", "revision": 912,
         "error": "unknown_profile", "details": {"profileId": "bilinmeyen"}},
    ]
    assert (result["voicesPending"], result["bellEnabled"]) == (7, True)
    sent, actor = world.bell_plan.applied[0]
    assert sent == body and actor == "Akademi Servis (akademi:bell-plan)"

    # Aynı özet → unchanged; küçük revizyon → stale; aynı revizyon farklı özet → çatışma.
    again = plan_body(plan_day("2026-10-12", calls=[CALL]),
                      plan_day("2026-10-13", revision=900, plan_hash="eski"))
    assert [day["result"] for day in (await client.put("/bell/plan", json=again)).json()
            ["days"]] == ["unchanged", "stale"]
    conflict = plan_body(plan_day("2026-10-12", plan_hash="farkli"),
                         plan_day("2026-10-13", revision=913, plan_hash="yeni"))
    days = (await client.put("/bell/plan", json=conflict)).json()["days"]
    assert days[0] == {"date": "2026-10-12", "result": "rejected", "revision": 912,
                       "error": "revision_conflict"}
    assert days[1]["result"] == "applied"                  # diğer günler uygulanır

    audit = await table(store, "audit")
    assert [row["action"] for row in audit] == ["bell.plan.put"] * 3
    detail = json.loads(audit[0]["detail"])
    assert detail == {"from": "2026-10-12", "to": "2026-10-14", "days": 3,
                      "results": {"applied": 2, "rejected": 1}}
    assert audit[0]["on_behalf_of"] == "akademi:bell-plan"


async def test_put_bell_plan_validation_from_bell(client: httpx.AsyncClient) -> None:
    body = plan_body(plan_day("2026-10-12"))
    body["days"][0]["revision"] = "bir"
    error = expect_error(await client.put("/bell/plan", json=body), 422, "validation_error")
    assert error["fields"] == ["days[0].revision"]
    assert error["details"] == {"path": "days[0].revision"}
    assert "bir" not in error["message"]


@pytest.mark.parametrize("raw", [b"[]", b"{bozuk", b'"metin"'])
async def test_put_bell_plan_body_must_be_json_object(raw: bytes,
                                                      client: httpx.AsyncClient) -> None:
    response = await client.put("/bell/plan", content=raw,
                                headers={"Content-Type": "application/json"})
    expect_error(response, 422, "validation_error")


async def test_put_bell_plan_size_limit(client: httpx.AsyncClient, world: World) -> None:
    body = plan_body(plan_day("2026-10-12"))
    body["pad"] = "x" * (512 * 1024)
    expect_error(await client.put("/bell/plan", json=body), 413, "payload_too_large")
    assert world.bell_plan.applied == []


async def test_put_bell_plan_idempotency(client: httpx.AsyncClient, world: World) -> None:
    body = plan_body(plan_day("2026-10-12", calls=[CALL]))
    headers = {"Idempotency-Key": "plan-c41e"}
    first = await client.put("/bell/plan", json=body, headers=headers)
    replay = await client.put("/bell/plan", json=body, headers=headers)
    assert replay.headers.get("idempotent-replayed") == "true"
    assert replay.json() == first.json()
    assert len(world.bell_plan.applied) == 1               # ağ tekrarı ikinci kez uygulanmaz
    other = plan_body(plan_day("2026-10-12", revision=913, plan_hash="baska"))
    expect_error(await client.put("/bell/plan", json=other, headers=headers),
                 409, "idempotency_conflict")


async def test_put_bell_plan_capability_missing(client: httpx.AsyncClient,
                                                world: World) -> None:
    world.remove("bell.plan")
    error = expect_error(await client.put("/bell/plan", json=plan_body(plan_day("2026-10-12"))),
                         503, "capability_missing")
    assert error["capability"] == "bell.plan"
    expect_error(await client.get("/bell/plan/status"), 503, "capability_missing")


STATUS_DAY_KEYS = {"date", "mode", "profileId", "reason", "source", "revision", "planHash",
                   "calls", "voicesReady", "voicesPending", "lastDispatchAt", "override",
                   "lastResult", "lastError"}


async def test_bell_plan_status_shape_window_and_304(client: httpx.AsyncClient,
                                                     world: World) -> None:
    await client.put("/bell/plan", json=plan_body(plan_day("2026-10-12", calls=[CALL])))
    response = await client.get("/bell/plan/status")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"bellEnabled", "sessionCallsEnabled", "agent", "days"}
    assert body["agent"] == {"online": True, "lastSeenAt": "2026-10-12T09:29:40+03:00"}
    # Varsayılan pencere: bugünden 14 gün (Europe/Istanbul).
    assert world.bell_plan.status_args[-1] == ("2026-10-12", "2026-10-25")
    (day,) = body["days"]
    assert set(day) == STATUS_DAY_KEYS                      # `receivedAt` gibi iç alan düşer
    assert (day["source"], day["revision"], day["calls"], day["voicesPending"]) == \
        ("akademi", 912, 1, 1)

    cached = await client.get("/bell/plan/status",
                              headers={"If-None-Match": response.headers["etag"]})
    assert cached.status_code == 304 and cached.content == b""

    await client.get("/bell/plan/status", params={"from": "2026-10-01", "to": "2026-10-31"})
    assert world.bell_plan.status_args[-1] == ("2026-10-01", "2026-10-31")


@pytest.mark.parametrize("params,field", [
    ({"from": "2026-10-01", "to": "2026-11-01"}, "to"),    # 32 gün
    ({"from": "2026-10-12", "to": "2026-10-11"}, "to"),
    ({"from": "12.10.2026"}, "from"),
])
async def test_bell_plan_status_range_validation(params: dict[str, str], field: str,
                                                 client: httpx.AsyncClient,
                                                 world: World) -> None:
    error = expect_error(await client.get("/bell/plan/status", params=params),
                         422, "validation_error")
    assert error["fields"] == [field]
    assert world.bell_plan.status_args == []


# ========================================================== §7 OTP SMS


async def test_sms_otp(client: httpx.AsyncClient, world: World, store: Any,
                       log: RecordingLog) -> None:
    sent = await client.post("/sms/otp", json={"phone": MOTHER, "code": "583012",
                                               "purpose": "login"},
                             headers={"X-Request-Id": "otp-1"})
    assert sent.status_code == 200
    assert sent.json() == {"sent": True, "jobId": "1791000001", "channel": "otp"}
    assert world.canteen.calls[-1] == ("send_sms", {
        "phone": MOTHER,
        "message": "BBD Akademi giris kodunuz: 583012. Kodu kimseyle paylasmayin. 4uZ+1CsiIp1"})
    assert world.canteen.sms_purposes[-1] == "otp"
    rows = await table(store, "sms_log")
    assert rows[-1]["phone_masked"] == "532****543" and rows[-1]["status"] == "sent"
    assert rows[-1]["request_id"] == "otp-1"
    for name in ("sms_log", "hits", "idempotency", "audit"):
        dump = repr(await table(store, name))
        assert "583012" not in dump and MOTHER not in dump
    assert "583012" not in repr(log.records) and "583012" not in sent.text

    for bad in ({"phone": MOTHER, "code": "58301", "purpose": "login"},
                {"phone": MOTHER, "code": "583012", "purpose": "reset"},
                {"phone": "2125551122", "code": "583012", "purpose": "login"},
                {"phone": MOTHER, "code": "583012", "purpose": "login", "text": "keyfî"}):
        response = await client.post("/sms/otp", json=bad)
        if "text" in bad:  # istemci metni yok sayılır; gönderilen hep şablondur
            assert response.status_code == 200
            assert "keyfî" not in world.canteen.calls[-1][1]["message"]
        else:
            expect_error(response, 422, "validation_error")

    world.canteen.sms_sent = False
    failed = await client.post("/sms/otp", json={"phone": FATHER, "code": "111111",
                                                 "purpose": "login"})
    expect_error(failed, 502, "sms_failed")
    world.canteen.sms_sent = True
    world.canteen.down = True
    expect_error(await client.post("/sms/otp", json={"phone": FATHER, "code": "111111",
                                                     "purpose": "login"}),
                 503, "canteen_unavailable")


async def test_sms_otp_rate_limits(client: httpx.AsyncClient, clock: Clock,
                                   store: Any) -> None:
    body = {"phone": MOTHER, "code": "583012", "purpose": "login"}
    for _ in range(5):
        assert (await client.post("/sms/otp", json=body)).status_code == 200
        clock.advance(minutes=1)
    limited = await client.post("/sms/otp", json=body)
    error = expect_error(limited, 429, "rate_limited")
    assert 0 < error["retryAfterSeconds"] <= 3600
    assert (await table(store, "sms_log"))[-1]["status"] == "rate_limited"
    clock.advance(minutes=56)
    assert (await client.post("/sms/otp", json=body)).status_code == 200

    # Toplam sınır: dakikada 60.
    clock.advance(hours=2)
    statuses = [(await client.post("/sms/otp", json={
        "phone": f"55{index:08d}", "code": "583012", "purpose": "login"})).status_code
        for index in range(61)]
    assert statuses[:60] == [200] * 60 and statuses[60] == 429


async def test_sms_otp_idempotent_replay_does_not_resend(client: httpx.AsyncClient,
                                                         world: World) -> None:
    body = {"phone": MOTHER, "code": "583012", "purpose": "login"}
    key = {"Idempotency-Key": "otp-key-1"}
    assert (await client.post("/sms/otp", json=body, headers=key)).status_code == 200
    replay = await client.post("/sms/otp", json=body, headers=key)
    assert replay.json() == {"sent": True, "jobId": "1791000001", "channel": "otp"}
    assert replay.headers["idempotent-replayed"] == "true"
    assert world.canteen.count("send_sms") == 1
    expect_error(await client.post("/sms/otp", json={**body, "code": "000001"}, headers=key),
                 409, "idempotency_conflict")


# ========================================================= §8 genel ayarlar


async def test_public_settings(client: httpx.AsyncClient, world: World) -> None:
    response = await client.get("/settings/public")
    assert response.status_code == 200
    assert response.json() == {"institutionName": "Benim Başarı Dünyam",
                               "timezone": "Europe/Istanbul",
                               "features": {"canteen": True, "tablet": True, "bell": True}}
    world.remove("bell.announce", "bbd_tablet.read")
    assert (await client.get("/settings/public")).json()["features"] == \
        {"canteen": True, "tablet": False, "bell": False}


# ------------------------------------------------------------- göç ve tablolar


async def test_migration_creates_only_own_tables(store: Any) -> None:
    sql = (MODULE_ROOT / "backend" / "migrations" / "001_mobile_gateway.sql").read_text("utf-8")
    from km_core.store.dialect import translate_ddl

    translate_ddl(sql)
    for name in ("idempotency", "hits", "sms_log", "audit"):
        assert await store.fetch_all(f"SELECT * FROM {store.table(name)}") == []
        assert store.table(name).startswith("mod_mobile_gateway_")


def test_readme_documents_staging_overrides() -> None:
    text = (MODULE_ROOT / "README.md").read_text(encoding="utf-8")
    assert "KM__modules__bbd_canteen_api__base_url" in text
    assert "kmsk_" in text and "ADR 0028" in text
    assert Path(MODULE_ROOT / "module.yaml").is_file()
