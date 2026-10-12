"""Tek öğrenci kartı: serbest tutarlı talep, ön ödeme sınırlı nakit tahsilat.

Sözleşme: bbd-akademi `kantin-veli-kontrolleri.md` §1.5. Kantin taklittir.
"""

from __future__ import annotations

from typing import Any

from bbd_payment_request_backend.service import PaymentRequestService


class CanteenError(RuntimeError):
    def __init__(self, message: str, *, reason: str, payload: dict[str, Any] | None = None,
                 status: int = 422) -> None:
        super().__init__(message)
        self.reason = reason
        self.status = status
        self.payload = payload or {}


class Log:
    def info(self, *_a: Any, **_k: Any) -> None:
        pass

    def warning(self, *_a: Any, **_k: Any) -> None:
        pass


class Canteen:
    def __init__(self, *, balance: int = 4250, allow: bool = False, maximum: int = 0) -> None:
        self.balance = balance
        self.prepayment = {"allowPrepayment": allow, "maxPrepayment": maximum}
        self.requests: list[tuple[str, int | None, str]] = []
        self.collections: list[tuple[str, int, str]] = []
        self.reject: CanteenError | None = None

    async def student(self, opaque_id: str) -> dict[str, Any]:
        return {"id": opaque_id, "displayName": "Elif Kaya", "parentPhone": "5329876543",
                "balance": self.balance}

    async def canteen_settings_public(self) -> dict[str, Any]:
        return dict(self.prepayment)

    async def payment_requests(self) -> list[dict[str, Any]]:
        return [{"ref": "PR-1", "studentId": "STU-1", "status": "PENDING", "amount": 4250,
                 "createdAt": 1, "amountFixed": False},
                {"ref": "PR-0", "studentId": "STU-2", "status": "PENDING", "amount": 10}]

    async def create_payment_request(self, student_id: str, amount: int | None = None, *,
                                     origin: str = "api") -> dict[str, Any]:
        if self.reject is not None:
            raise self.reject
        self.requests.append((student_id, amount, origin))
        return {"queued": True, "ref": "PR-9", "amount": amount or self.balance,
                "status": "PENDING", "linkUrl": "https://ode.invalid/p/X",
                "amountFixed": amount is not None}

    async def record_collection(self, student_id: str, amount: int,
                                local_id: str) -> dict[str, Any]:
        if self.reject is not None:
            raise self.reject
        self.collections.append((student_id, amount, local_id))
        return {"localId": local_id, "amount": amount, "balance": self.balance - amount,
                "smsQueued": True}


def service(store: Any, canteen: Canteen) -> PaymentRequestService:
    return PaymentRequestService(canteen=canteen, store=store, log=Log(), config={})


async def test_kart_verisi(store: Any) -> None:
    state = await service(store, Canteen(allow=True, maximum=20000)).student_state("STU-1")
    assert state["ok"] is True
    assert state["student"] == {"kantinId": "STU-1", "displayName": "Elif Kaya",
                                "balance": 4250, "hasPhone": True}
    assert state["maxCollect"] == 24250 and state["maxRequest"] == 24250
    assert state["pendingRequest"]["ref"] == "PR-1"


async def test_serbest_tutarli_talep_admin_kaynagiyla(store: Any) -> None:
    canteen = Canteen()
    result = await service(store, canteen).request_one("STU-1", amount=2000, actor="Ayşe")
    assert result["ok"] is True and result["amount"] == 2000 and result["amountFixed"] is True
    assert canteen.requests == [("STU-1", 2000, "admin")]
    entries = await store.fetch_all("SELECT * FROM mod_bbd_payment_request_entry")
    assert entries[0]["status"] == "queued" and entries[0]["ref"] == "PR-9"

    full = await service(store, canteen).request_one("STU-1", amount=None, actor="Ayşe")
    assert full["ok"] is True and canteen.requests[-1] == ("STU-1", None, "admin")


async def test_talep_reddi_azami_tutarla(store: Any) -> None:
    canteen = Canteen()
    canteen.reject = CanteenError("aşıyor", reason="exceeds_debt",
                                  payload={"balance": 4250, "maxAmount": 4250})
    result = await service(store, canteen).request_one("STU-1", amount=9000, actor="Ayşe")
    assert result["ok"] is False and result["reason"] == "exceeds_debt"
    assert result["maxAmount"] == 4250 and "42.50" in result["error"]
    entries = await store.fetch_all("SELECT * FROM mod_bbd_payment_request_entry")
    assert entries[0]["status"] == "failed" and entries[0]["reason"] == "exceeds_debt"


async def test_tahsilat_on_odeme_kapaliyken_borcu_asamaz(store: Any) -> None:
    canteen = Canteen()
    result = await service(store, canteen).collect(
        {"kantinId": "STU-1", "amount": 5000}, actor="Ayşe")
    assert result["ok"] is False and result["maxAmount"] == 4250
    assert canteen.collections == []


async def test_tahsilat_on_odeme_acikken_borc_arti_sinir(store: Any) -> None:
    canteen = Canteen(allow=True, maximum=2000)
    svc = service(store, canteen)
    ok = await svc.collect({"kantinId": "STU-1", "amount": 6000, "localId": "u-1"}, actor="A")
    assert ok["ok"] is True and ok["balance"] == -1750 and ok["settled"] is True
    assert canteen.collections == [("STU-1", 6000, "u-1")]
    too_much = await svc.collect({"kantinId": "STU-1", "amount": 6251}, actor="A")
    assert too_much["ok"] is False and too_much["maxAmount"] == 6250


async def test_tahsilat_borcsuz_ve_kantin_reddi(store: Any) -> None:
    canteen = Canteen(balance=0)
    result = await service(store, canteen).collect({"kantinId": "STU-1", "amount": 100},
                                                   actor="A")
    assert result["ok"] is False and "borç yok" in result["error"]

    canteen = Canteen()
    canteen.reject = CanteenError("yok", reason="no_debt")
    result = await service(store, canteen).collect({"kantinId": "STU-1", "amount": 100},
                                                   actor="A")
    assert result == {"ok": False, "error": "Öğrencinin güncel borcu yok."}
    rows = await store.fetch_all("SELECT * FROM mod_bbd_payment_request_collection")
    assert rows[0]["status"] == "failed"
