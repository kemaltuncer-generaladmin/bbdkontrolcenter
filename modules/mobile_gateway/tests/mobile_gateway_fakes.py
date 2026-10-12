"""Mobil Geçit testlerinin yetenek taklitleri.

Biçimler, birleştirilecek dalda yazılan yeteneklerin bu modüle bildirilen
arayüzünü izler (README → "Tüketilen yetenekler"). Gerçek kantine, tablete
ya da zile çıkılmaz; gerçek SMS gönderilmez.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

ELIF = "STU-a1b2c3d4e5"
CAN = "STU-b2c3d4e5f6"
DENIZ = "STU-c3d4e5f6a7"

#: Kantin erişim kodları — testlerin hiçbir çıktıda görmemesi gereken değerler.
CODES = {"483920": ELIF, "561207": CAN, "774411": DENIZ}

MOTHER = "5329876543"
FATHER = "5051112233"
ELIF_PHONE = "5321234567"

PR_PENDING = {
    "ref": "PR-01JPENDING", "studentId": ELIF, "studentName": "Elif Kaya", "amount": 4250,
    "status": "PENDING", "amountFixed": False, "linkUrl": "https://ode.bbdstore.com.tr/p/AB12CD",
    "payToken": "tok.pending", "origin": "admin", "createdAt": 1791784800000,
    "updatedAt": "2026-10-12T06:00:00.250000Z", "paidAt": None,
    "parentPhone": MOTHER, "smsText": "Sayın veli, borç 42,50 TL",
}
PR_PAID = {
    "ref": "PR-01JPAID", "studentId": ELIF, "amount": 10000, "status": "PAID",
    "amountFixed": True, "linkUrl": "https://ode.bbdstore.com.tr/p/ZZ99", "payToken": "tok.paid",
    "origin": "bulk", "createdAt": 1790845200000, "updatedAt": "2026-10-01T09:00:00Z",
    "paidAt": "2026-10-01T09:05:00Z",
}
PR_OTHER = {**PR_PAID, "ref": "PR-01JOTHER", "studentId": CAN}


def directory_records() -> dict[str, dict[str, Any]]:
    """`bbd_students.directory` StudentRecord'ları (sözleşme §1 + Ekler)."""
    return {
        ELIF: {
            "id": ELIF, "firstName": "Elif", "lastName": "Kaya", "displayName": "Elif Kaya",
            "className": "8-A", "schoolNo": "1234", "studentPhone": ELIF_PHONE,
            "guardians": [
                {"slot": 2, "name": "Mehmet Kaya", "phone": FATHER},
                {"slot": 1, "name": "Ayşe Kaya", "phone": MOTHER},
            ],
            "canteen": {"balance": 4250, "spendingLimit": 20000, "isBlocked": False,
                        "dailyLimit": 15000, "guardianBlocked": False},
            "gradeLevel": 8, "track": None, "examTarget": "LGS",
            "updatedAt": "2026-10-11T15:02:11Z", "phoneIssues": [],
            # Asla dönmeyecek alanlar — geçit beyaz listeyle düşürmeli.
            "accessCode": "483920", "qrKey": "c2VjcmV0", "notes": "iç not",
        },
        CAN: {
            "id": CAN, "firstName": "Can", "lastName": "Kaya", "displayName": "Can Kaya",
            "className": "12-SAY", "schoolNo": "998", "studentPhone": None,
            "guardians": [
                {"slot": 1, "name": "Ayşe Kaya", "phone": MOTHER},
                {"slot": 2, "name": "Ev", "phone": None},
            ],
            "canteen": {"balance": 0, "spendingLimit": None, "isBlocked": True,
                        "dailyLimit": None, "guardianBlocked": True},
            "gradeLevel": 12, "track": "SAY", "examTarget": "YKS",
            "updatedAt": "2026-10-10T08:00:00+03:00", "phoneIssues": ["guardian2Phone"],
        },
        DENIZ: {
            "id": DENIZ, "firstName": "Deniz", "lastName": "Ak", "displayName": "",
            "className": None, "schoolNo": None,
            # Normalize edilmemiş gelirse geçit onarır; onarılamazsa null + phoneIssues.
            "studentPhone": "+90 (532) 000 00 01",
            "guardians": [{"slot": 1, "name": "Veli", "phone": "0212 555 11 22"}],
            "canteen": None,
            "gradeLevel": "graduate", "track": "dil", "examTarget": "KPSS",
            "updatedAt": 1791633600000, "phoneIssues": [],
        },
    }


class CanteenError(RuntimeError):
    """`bbd_canteen_api.client.CanteenError` ile aynı yüz: status + reason."""

    def __init__(self, message: str, *, status: int | None = None,
                 reason: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.reason = reason


class FakeCanteen:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.down = False
        self.fail_reason: str | None = None
        self.sms_sent = True
        self.students: dict[str, dict[str, Any]] = {
            ELIF: {"id": ELIF, "displayName": "Elif Kaya", "parentPhone": MOTHER,
                   "balance": 4250, "spendingLimit": 20000, "isBlocked": False,
                   "dailyLimit": 15000, "guardianBlocked": False,
                   "restrictedProductIds": [3, 17], "spentToday": 2500,
                   "controlsUpdatedAt": "2026-10-12T06:30:00Z", "accessCode": "483920"},
            CAN: {"id": CAN, "displayName": "Can Kaya", "parentPhone": MOTHER, "balance": 0,
                  "spendingLimit": None, "isBlocked": True, "dailyLimit": None,
                  "guardianBlocked": True, "restrictedProductIds": [],
                  "spentToday": 0, "controlsUpdatedAt": None},
        }
        self.requests = [deepcopy(PR_PENDING), deepcopy(PR_PAID), deepcopy(PR_OTHER)]
        self.notifications = [
            {"id": 71, "studentId": ELIF, "kind": "payment_request", "amount": 4250,
             "createdAt": "2026-10-12T06:00:00Z", "updatedAt": "2026-10-12T06:00:01.500000Z",
             "status": "sent", "to": MOTHER, "text": "Sayın veli, ödeme linki: …"},
            {"id": 72, "studentId": CAN, "kind": "debt_threshold", "amount": None,
             "createdAt": "2026-10-12T05:00:00Z", "status": "sent", "to": MOTHER,
             "message": "Borç eşiği aşıldı"},
        ]

    def _enter(self, name: str, args: Any = None) -> None:
        self.calls.append((name, args))
        if self.down:
            raise CanteenError(f"{name} → bağlantı hatası")

    def count(self, name: str) -> int:
        return sum(1 for call, _ in self.calls if call == name)

    async def lookup_student_by_code(self, code: str) -> dict[str, str]:
        self._enter("lookup_student_by_code")  # kod BİLEREK kaydedilmez
        student_id = CODES.get(code)
        if student_id is None:
            raise CanteenError("POST /api/students/lookup-code → 404", status=404)
        return {"id": student_id, "displayName": "x"}

    async def get_student(self, student_id: str) -> dict[str, Any]:
        self._enter("get_student", student_id)
        if student_id not in self.students:
            raise CanteenError("404", status=404, reason="student_not_found")
        return {"data": deepcopy(self.students[student_id])}

    async def transactions(self, *, from_ms: int | None = None, to_ms: int | None = None,
                           limit: int | None = None) -> list[dict[str, Any]]:
        self._enter("transactions", (from_ms, to_ms, limit))
        return [
            {"serverId": "8f0c-1", "studentId": ELIF, "studentName": "Elif Kaya", "total": 3500,
             "createdAt": 1791722460000, "reversedAt": None,
             "items": [{"productId": 3, "name": "Tost", "qty": 1, "unitPrice": 2500,
                        "lineTotal": 2500},
                       {"productId": 9, "name": "Ayran", "qty": 2, "unitPrice": 500}]},
            {"serverId": "8f0c-2", "studentId": ELIF, "total": 1200,
             "createdAt": 1791636060000, "reversedAt": 1791636100000, "items": []},
            {"serverId": "other", "studentId": CAN, "total": 999, "createdAt": 1791722460000,
             "items": []},
        ]

    async def collections(self, *, from_ms: int, to_ms: int,
                          student_id: str | None = None) -> dict[str, Any]:
        self._enter("collections", student_id)
        return {"entries": [
            {"studentId": ELIF, "createdAt": "2026-10-01T07:00:00Z", "amount": 10000,
             "method": "CASH"},
            {"studentId": ELIF, "createdAt": "2026-10-05T07:00:00Z", "amount": 5000,
             "method": "payment_link"},
            {"studentId": CAN, "createdAt": "2026-10-05T07:00:00Z", "amount": 1,
             "method": "CASH"},
        ]}

    async def payment_requests(self) -> list[dict[str, Any]]:
        self._enter("payment_requests")
        return deepcopy(self.requests)

    async def create_payment_request(self, student_id: str,
                                     amount: int | None = None) -> dict[str, Any]:
        self._enter("create_payment_request", (student_id, amount))
        if self.fail_reason:
            raise CanteenError("reddedildi", status=422 if self.fail_reason in
                               ("exceeds_debt", "invalid_amount") else 200,
                               reason=self.fail_reason)
        if student_id not in self.students:
            raise CanteenError("404", status=404, reason="student_not_found")
        return {"queued": True, "ref": "PR-01JNEW", "amount": amount or 4250,
                "status": "PENDING", "amountFixed": amount is not None,
                "linkUrl": "https://ode.bbdstore.com.tr/p/NEW1", "payToken": "tok.new",
                "parentPhone": MOTHER}

    async def update_controls(self, student_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        self._enter("update_controls", (student_id, deepcopy(patch)))
        if student_id not in self.students:
            raise CanteenError("404", status=404, reason="student_not_found")
        if 999999 in (patch.get("restrictedProductIds") or []):
            raise CanteenError("422", status=422, reason="validation_error")
        self.students[student_id].update(patch)
        self.students[student_id]["controlsUpdatedAt"] = "2026-10-12T06:31:00Z"
        return {"data": deepcopy(self.students[student_id])}

    async def record_collection(self, student_id: str, amount: int,
                                local_id: str) -> dict[str, Any]:
        self._enter("record_collection", (student_id, amount, local_id))
        if self.fail_reason:
            raise CanteenError("422", status=422, reason=self.fail_reason)
        balance = int(self.students[student_id]["balance"]) - amount
        self.students[student_id]["balance"] = balance
        return {"localId": local_id, "amount": amount, "balance": balance}

    async def products(self) -> list[dict[str, Any]]:
        self._enter("products")
        return [{"id": 3, "barcode": "869", "name": "Tost", "price": 2500, "stock": 10,
                 "isActive": True, "imageUrl": None, "updatedAt": 1},
                {"id": 17, "name": "Kola", "price": 3000, "isActive": False,
                 "imageUrl": "https://kantin/img/17.png"}]

    async def payment_requests_feed(self, updated_since: str | None = None,
                                    limit: int = 100) -> dict[str, Any]:
        self._enter("payment_requests_feed", (updated_since, limit))
        return {"data": deepcopy(self.requests[:limit])}

    async def parent_notifications_feed(self, updated_since: str | None = None,
                                        limit: int = 100) -> list[dict[str, Any]]:
        self._enter("parent_notifications_feed", (updated_since, limit))
        return deepcopy(self.notifications[:limit])

    async def canteen_settings_public(self) -> dict[str, Any]:
        self._enter("canteen_settings_public")
        return {"allowPrepayment": True, "maxPrepayment": 50000, "netgsmPassword": "x"}

    async def send_sms(self, *, student_id: str | None = None, phone: str | None = None,
                       include_debt: bool = False, include_daily: bool = False,
                       message: str | None = None) -> dict[str, Any]:
        self._enter("send_sms", {"phone": phone, "message": message})
        if not self.sms_sent:
            return {"sent": False, "reason": "netgsm"}
        return {"sent": True, "text": message}


class LegacyCanteen:
    """Bugünkü `bbd_canteen_api` istemcisi: yeni metotlar yok, `queued: false`."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def student(self, student_id: str) -> dict[str, Any]:
        self.calls.append("student")
        return {"id": student_id, "balance": 100, "spendingLimit": 0, "isBlocked": False}

    async def create_payment_request(self, student_id: str) -> dict[str, Any]:
        self.calls.append("create_payment_request")
        return {"queued": False, "reason": "no_phone"}

    async def collect_cash(self, opaque_id: str, *, amount: int,
                           local_id: str) -> dict[str, Any]:
        self.calls.append("collect_cash")
        return {"balance": 0}


class FakeDirectory:
    def __init__(self) -> None:
        self.records = directory_records()
        self.complete = True
        self.since_seen: list[Any] = []

    async def list(self, since: str | None = None) -> dict[str, Any]:
        self.since_seen.append(since)
        students = list(self.records.values())
        if since is not None:
            students = students[:1]
        return {"students": deepcopy(students), "classes": ["8-A", "12-SAY", "8-A"],
                "complete": self.complete}

    async def get(self, student_id: str) -> dict[str, Any] | None:
        record = self.records.get(student_id)
        return deepcopy(record) if record else None

    async def find_by_phone(self, phone10: str) -> list[dict[str, Any]]:
        return []

    def card_qr_text(self, student_id: str) -> str:  # senkron yetenek de desteklenir
        if student_id not in self.records:
            raise KeyError(student_id)
        return "djEuQUJDREVGRw"


class NotFoundError(LookupError):
    pass


class ConflictError(RuntimeError):
    pass


class FakeTabletRead:
    def __init__(self) -> None:
        self.usage_args: list[tuple[str, str, str]] = []

    async def board(self) -> dict[str, Any]:
        return {
            "summary": {"free": 6, "inUse": 9, "offline": 2, "locked": 1, "pending": 3},
            "devices": [{
                "id": 12, "name": "Tablet 07", "state": "in_use", "battery": 64,
                "charging": False, "lastSeenAt": "2026-10-12T06:29:40Z",
                "tokenHash": "never", "activeSession": {
                    "id": 991, "studentId": ELIF, "studentName": "Elif Kaya",
                    "loginAt": "2026-10-12T06:00:00Z", "sessionToken": "never"},
            }, {"id": 13, "name": "Tablet 08", "state": "free", "battery": None,
                "charging": True, "lastSeenAt": None, "activeSession": None}],
        }

    async def student_usage(self, student_id: str, date_from: str,
                            date_to: str) -> dict[str, Any]:
        self.usage_args.append((student_id, date_from, date_to))
        if student_id not in (ELIF, CAN):
            raise NotFoundError(student_id)
        return {
            "days": [{"date": "2026-10-11", "totalSeconds": 5400,
                      "apps": [{"package": "com.example.app", "label": "Örnek",
                                "seconds": 3600},
                               {"package": "com.unknown", "label": None, "seconds": 1800}]}],
            "sessions": [{"id": 991, "deviceName": "Tablet 07",
                          "loginAt": "2026-10-12T06:00:00Z", "endedAt": None,
                          "endReason": None}],
            "activeSession": {"id": 991, "deviceName": "Tablet 07",
                              "loginAt": "2026-10-12T06:00:00Z"},
            "blockedUntil": None,
        }


class FakeTabletControl:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def grant(self, student_id: str, package_name: str | None, extra_seconds: int,
                    reason: str, actor: str) -> int:
        self.calls.append(("grant", {"student_id": student_id, "package_name": package_name,
                                     "extra_seconds": extra_seconds, "reason": reason,
                                     "actor": actor}))
        if student_id not in (ELIF, CAN):
            raise NotFoundError(student_id)
        return 55

    async def block(self, student_id: str, until: Any, reason: str, actor: str) -> Any:
        self.calls.append(("block", {"student_id": student_id, "until": until,
                                     "reason": reason, "actor": actor}))
        return until

    async def unblock(self, student_id: str, reason: str, actor: str) -> None:
        self.calls.append(("unblock", {"student_id": student_id, "reason": reason,
                                       "actor": actor}))

    async def end_session(self, session_id: str, reason: str, actor: str) -> bool:
        self.calls.append(("end_session", {"session_id": session_id, "reason": reason,
                                           "actor": actor}))
        if session_id == "404":
            raise NotFoundError(session_id)
        if session_id == "busy":
            raise ConflictError(session_id)
        return session_id != "ended"


class FakeBellAnnounce:
    def __init__(self) -> None:
        self.status = "queued"
        self.pages: list[dict[str, Any]] = []

    async def page(self, text: str, *, cache_key: str | None = None, actor: str | None = None,
                   request_id: str | None = None) -> dict[str, Any]:
        self.pages.append({"text": text, "cache_key": cache_key, "actor": actor,
                           "request_id": request_id})
        if self.status == "queued":
            return {"status": "queued", "voiceCached": True, "etaSeconds": 3}
        return {"status": self.status, "voiceCached": False, "etaSeconds": 0}


async def bell_week() -> dict[str, Any]:
    return {
        "times": {"mon": [{"id": "t1", "time": "08:30", "label": "1. ders", "kind": "ders",
                           "internal": 1}],
                  "tue": [], "wed": [], "thu": [], "fri": [], "sat": [], "sun": []},
        "groups": [{"id": "g1", "name": "Ortaokul", "kind": "grup", "sort": 1}],
    }


class FakeVault:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value


class RecordingLog:
    """structlog yüzü: her çağrı kaydedilir ki kişisel verinin sızmadığı sınansın."""

    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict[str, Any]]] = []

    def _record(self, level: str, event: str, **fields: Any) -> None:
        self.records.append((level, event, fields))

    def info(self, event: str, **fields: Any) -> None:
        self._record("info", event, **fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._record("warning", event, **fields)

    def error(self, event: str, **fields: Any) -> None:
        self._record("error", event, **fields)
