"""Mobil Geçit testlerinin yetenek taklitleri.

Biçimler, birleştirilecek dalda yazılan yeteneklerin bu modüle bildirilen
arayüzünü izler (README → "Tüketilen yetenekler"). Gerçek kantine, tablete
ya da zile çıkılmaz; gerçek SMS gönderilmez.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
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

SALE_TOST = {
    "serverId": "8f0c-1", "localId": "l-1", "total": 3500, "createdAt": 1791722460000,
    "reversedAt": None, "reversedReason": None,
    "items": [{"productId": 3, "name": "Tost", "qty": 1, "unitPrice": 2500, "lineTotal": 2500},
              {"productId": 9, "name": "Ayran", "qty": 2, "unitPrice": 500}],
}
SALE_REVERSED = {
    "serverId": "8f0c-2", "localId": "l-2", "total": 1200, "createdAt": 1791636060000,
    "reversedAt": 1791636100000, "reversedReason": "yanlış ürün", "items": [],
}


def _ms(text: str) -> int:
    return int(datetime.fromisoformat(text).timestamp() * 1000)


#: Kantin hesap dökümü (§1.7) — `transactions` / `collections` taklitleriyle AYNI
#: veri: defter yolu ile eski yol aynı §3 yanıtını üretmeli. Yeniden eskiye.
LEDGER = [
    {"id": 906, "studentId": CAN, "at": 1791722460000, "kind": "sale", "direction": "debit",
     "amount": 999, "balanceAfter": 0, "ref": "l-other",
     "sale": {"serverId": "other", "total": 999, "createdAt": 1791722460000, "items": []}},
    {"id": 905, "studentId": ELIF, "at": 1791722460000, "kind": "sale", "direction": "debit",
     "amount": 3500, "balanceAfter": 4250, "ref": "l-1", "sale": SALE_TOST},
    # CAN'ın nakit tahsilatı (kimlik sırası dışı ama taklit sıralar; LEDGER[1] ve
    # LEDGER[-1] testlerde ELIF satırı olarak kullanılır).
    {"id": 900, "studentId": CAN, "at": _ms("2026-10-05T07:00:00+00:00"), "kind": "cash",
     "direction": "credit", "amount": 1, "balanceAfter": 0, "ref": "col-can", "sale": None},
    {"id": 904, "studentId": ELIF, "at": 1791636100000, "kind": "sale_reversal",
     "direction": "credit", "amount": 1200, "balanceAfter": 750, "ref": "l-2",
     "sale": SALE_REVERSED},
    {"id": 903, "studentId": ELIF, "at": 1791636060000, "kind": "sale", "direction": "debit",
     "amount": 1200, "balanceAfter": 1950, "ref": "l-2", "sale": SALE_REVERSED},
    {"id": 902, "studentId": ELIF, "at": _ms("2026-10-05T07:00:00+00:00"), "kind": "payment",
     "direction": "credit", "amount": 5000, "balanceAfter": 750, "ref": "PR-01JPAID",
     "sale": None},
    {"id": 901, "studentId": ELIF, "at": _ms("2026-10-01T07:00:00+00:00"), "kind": "cash",
     "direction": "credit", "amount": 10000, "balanceAfter": 5750, "ref": "col-1",
     "sale": None},
]


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
    """`bbd_canteen_api.client.CanteenError` ile aynı yüz: status + reason
    (+ kantin red gövdesinin sayısal ayrıntısı, varsa)."""

    def __init__(self, message: str, *, status: int | None = None,
                 reason: str | None = None, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.reason = reason
        self.details = details


class FakeCanteen:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.down = False
        self.fail_reason: str | None = None
        self.origins: list[str | None] = []
        self.feed_cursor: dict[str, Any] = {"nextSince": 1791784800250, "hasMore": True}
        #: Defter ucu: "ok" (kantin §1.7) ya da "unsupported" (eski kantin; istemci
        #: `reason="ledger_unsupported"` ile reddeder).
        self.ledger_mode = "ok"
        self.ledger_rows = deepcopy(LEDGER)
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

    async def ledger(self, student_id: str, limit: int = 50, cursor: str | None = None,
                     from_ms: int | None = None, to_ms: int | None = None) -> dict[str, Any]:
        """`bbd_canteen_api` `ledger()` dönüş biçimi (imleç opak: "c<id>")."""
        self._enter("ledger", {"student_id": student_id, "limit": limit, "cursor": cursor,
                               "from_ms": from_ms, "to_ms": to_ms})
        if self.ledger_mode == "unsupported":
            raise CanteenError("ledger yok", status=404, reason="ledger_unsupported")
        if student_id not in self.students:
            raise CanteenError("404", status=404, reason="student_not_found")
        rows = [row for row in self.ledger_rows if row["studentId"] == student_id
                and (from_ms is None or row["at"] >= from_ms)
                and (to_ms is None or row["at"] < to_ms)
                and (cursor is None or row["id"] < int(cursor[1:]))]
        rows.sort(key=lambda row: row["id"], reverse=True)
        chunk = [{key: value for key, value in row.items() if key != "studentId"}
                 for row in rows[:limit]]
        more = len(rows) > limit
        page: dict[str, Any] = {"data": deepcopy(chunk),
                                "nextCursor": f"c{chunk[-1]['id']}" if more else None,
                                "hasMore": more}
        if cursor is None:
            page["student"] = deepcopy(self.students[student_id])
            pending = [row for row in self.requests
                       if row["studentId"] == student_id and row["status"] == "PENDING"]
            page["pendingPaymentRequest"] = deepcopy(pending[0]) if pending else None
        return page

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

    async def create_payment_request(self, student_id: str, amount: int | None = None,
                                     origin: str | None = None) -> dict[str, Any]:
        self._enter("create_payment_request", (student_id, amount))
        self.origins.append(origin)
        if self.fail_reason:
            limit = {"balance": 4250, "maxAmount": 4250} \
                if self.fail_reason == "exceeds_debt" else None
            raise CanteenError("reddedildi", status=422 if self.fail_reason in
                               ("exceeds_debt", "invalid_amount") else 409,
                               reason=self.fail_reason, details=limit)
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
            limit = {"balance": 4250, "maxAmount": 4250} \
                if self.fail_reason == "exceeds_debt" else None
            raise CanteenError("422", status=422, reason=self.fail_reason, details=limit)
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
        return {"data": deepcopy(self.requests[:limit]), **self.feed_cursor}

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


class PreLedgerCanteen(FakeCanteen):
    """Hesap dökümünü bilmeyen kantin istemcisi (`ledger` metodu yok)."""

    ledger = None  # type: ignore[assignment]


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


class DirectoryUnavailable(RuntimeError):
    """`bbd_students` dizini kantine ulaşamadı."""


class FakeDirectory:
    def __init__(self) -> None:
        self.records = directory_records()
        self.complete = True
        self.down = False
        self.since_seen: list[Any] = []

    async def list(self, since: datetime | None = None) -> dict[str, Any]:
        self.since_seen.append(since)
        students = list(self.records.values())
        if since is not None:
            students = students[:1]
        return {"students": deepcopy(students), "classes": ["8-A", "12-SAY", "8-A"],
                "complete": self.complete}

    async def get(self, student_id: str) -> dict[str, Any] | None:
        if self.down:
            raise DirectoryUnavailable("Kantine ulaşılamadı.")
        record = self.records.get(student_id)
        return deepcopy(record) if record else None

    async def find_by_phone(self, phone10: str) -> list[dict[str, Any]]:
        return []

    def card_qr_text(self, student_id: str) -> str:  # senkron yetenek de desteklenir
        if student_id not in self.records:
            raise KeyError(student_id)
        return "djEuQUJDREVGRw"


class TabletCapabilityError(Exception):
    """`bbd_tablet` yeteneğinin hatası: `reason` makine içindir."""

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


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
            raise TabletCapabilityError("yok", reason="student_not_found")
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
        if student_id == DENIZ:
            raise TabletCapabilityError("kantin", reason="canteen_unavailable")
        if not package_name:  # gerçek yetenek ek süreyi uygulama bazında verir
            raise TabletCapabilityError("Ek süre uygulama bazındadır; uygulama seçilmeli.",
                                        reason="validation_error")
        if student_id not in (ELIF, CAN):
            raise TabletCapabilityError("yok", reason="student_not_found")
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
            raise TabletCapabilityError("yok", reason="session_not_found")
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
        # Saat setleri (§3.1) — `sort` ve `updatedAt` geçitte düşer.
        "profiles": [{"id": "yarim-gun", "name": "Yarım gün", "sort": 1,
                      "updatedAt": "2026-10-12T09:00:00+03:00",
                      "times": [{"time": "08:40", "label": "", "kind": "ders"},
                                {"time": "09:20", "label": "", "kind": "teneffus"}]}],
        "days": [{"date": "2026-10-12", "mode": "normal"}],
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


# ------------------------------------------------- öğrenci grupları ve zil planı


def group_record(group_id: str, name: str, *, kind: str = "group", revision: int = 1,
                 members: list[dict[str, Any]] | None = None, call_name: str = "",
                 archived_at: str | None = None) -> dict[str, Any]:
    """`bbd_students.groups` kaydı — gerçek yeteneğin `_record(full=False)` biçimi."""
    return {
        "id": group_id, "name": name, "callName": call_name, "kind": kind,
        "archived": archived_at is not None, "archivedAt": archived_at,
        "updatedAt": "2026-10-12T09:12:44+03:00", "revision": revision,
        "members": deepcopy(members or []),
    }


class FakeGroups:
    """`bbd_students.groups`: `list(since_revision)`, `get`, `revision()`.

    Gerçek yetenek yalnız kendi tablosunu okur; burada liste bellekte durur.
    `with_revision=False` eski (revizyon okuması sunmayan) yeteneği taklit eder.
    """

    def __init__(self, *, with_revision: bool = True) -> None:
        self.current = 4182
        self.records = [
            group_record("0b9e", "TYT Hızlandırma", call_name="TYT grubu", revision=4181,
                         members=[
                             {"studentId": ELIF, "validFrom": "2026-09-15", "validTo": None,
                              "id": "m1", "reason": "gizli gerekçe"},
                             {"studentId": CAN, "validFrom": "2026-09-15",
                              "validTo": "2026-10-09"},
                         ]),
            group_record("77c1", "İlayda", kind="individual", revision=4182,
                         members=[{"studentId": DENIZ, "validFrom": "2026-09-01",
                                   "validTo": None}]),
            group_record("aa01", "Eski LGS", revision=12, archived_at="2026-06-30T18:00:00+03:00"),
        ]
        self.lists: list[int | None] = []
        self.revisions = 0
        self.complete = True
        if not with_revision:
            self.revision = None  # type: ignore[assignment]

    async def list(self, since_revision: int | None = None) -> dict[str, Any]:
        self.lists.append(since_revision)
        # Sığ kopya: `deepcopy` 4.000 üyelikte yüzlerce ms sürer ve hız
        # ölçümünü taklidin maliyetiyle doldururdu.
        found = [{**item, "members": [dict(member) for member in item["members"]]}
                 for item in self.records
                 if since_revision is None or item["revision"] > since_revision]
        return {"revision": self.current, "full": since_revision is None,
                "groups": found, "complete": self.complete}

    async def get(self, group_id: str) -> dict[str, Any] | None:
        return next((deepcopy(item) for item in self.records if item["id"] == group_id), None)

    async def revision(self) -> int:
        self.revisions += 1
        return self.current


class PlanError(ValueError):
    """Zil modülünün `PlanError`ı gibi: `status`, `code`, `details` taşır."""

    def __init__(self, message: str, *, status: int = 422, code: str = "validation_error",
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.details = details or {}


class FakeBellPlan:
    """`bell.plan`: gün bazında karar — `revision` + `planHash` kuralı (§3.3).

    Gerçek kurallar zil modülünün testlerindedir; burada geçidin taşıdığı
    biçim ve hata çevirisi sınanır.
    """

    def __init__(self) -> None:
        self.days: dict[str, dict[str, Any]] = {}
        self.profiles = {"yarim-gun"}
        self.applied: list[tuple[dict[str, Any], str]] = []
        self.status_args: list[tuple[str, str]] = []
        self.enabled = True

    async def apply_days(self, body: Any, *, actor: str) -> dict[str, Any]:
        self.applied.append((deepcopy(body), actor))
        days = body.get("days")
        if not isinstance(days, list) or not days:
            raise PlanError("days liste olmalı.", details={"path": "days"})
        for index, day in enumerate(days):
            if not isinstance(day.get("revision"), int):
                raise PlanError(f"days[{index}].revision pozitif tamsayı olmalı.",
                                details={"path": f"days[{index}].revision"})
        results: list[dict[str, Any]] = []
        for day in days:
            record: dict[str, Any] = {"date": day["date"], "revision": day["revision"]}
            existing = self.days.get(day["date"])
            if day.get("mode") == "profile" and day.get("profileId") not in self.profiles:
                record.update(result="rejected", error="unknown_profile",
                              details={"profileId": day.get("profileId")})
            elif existing and existing["planHash"] == day["planHash"]:
                record["result"] = "unchanged"
            elif existing and day["revision"] < existing["revision"]:
                record["result"] = "stale"
            elif existing and day["revision"] == existing["revision"]:
                record.update(result="rejected", error="revision_conflict")
            else:
                self.days[day["date"]] = deepcopy(day)
                record["result"] = "applied"
            results.append(record)
        return {"days": results, "voicesPending": 7, "bellEnabled": self.enabled}

    async def status(self, from_date: Any, to_date: Any) -> dict[str, Any]:
        self.status_args.append((str(from_date), str(to_date)))
        if str(from_date) > str(to_date):
            raise PlanError("to, from'dan önce olamaz.", details={"path": "to"})
        out = []
        for key in sorted(self.days):
            if not str(from_date) <= key <= str(to_date):
                continue
            day = self.days[key]
            out.append({
                "date": key, "mode": day["mode"], "source": "akademi",
                "revision": day["revision"], "planHash": day["planHash"],
                "profileId": day.get("profileId"), "reason": day.get("reason"),
                "calls": len(day["calls"]), "voicesReady": max(0, len(day["calls"]) - 1),
                "voicesPending": min(1, len(day["calls"])),
                "lastDispatchAt": None, "override": None, "lastResult": "applied",
                "lastError": None, "receivedAt": "2026-10-12T09:30:00+03:00",
            })
        return {"bellEnabled": self.enabled, "sessionCallsEnabled": False,
                "agent": {"online": True, "lastSeenAt": "2026-10-12T06:29:40+00:00"},
                "days": out}

    async def override_day(self, day: Any, mode: str = "off", *, actor: str) -> dict[str, Any]:
        return {"ok": True, "days": []}

    async def clear_override(self, day: Any, *, actor: str) -> dict[str, Any]:
        return {"ok": True, "days": []}
