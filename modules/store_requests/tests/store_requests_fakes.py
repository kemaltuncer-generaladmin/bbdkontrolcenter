"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`FakeStore` SQL'i ayrıştırmaz; servisin yazdığı üç ifadeyi (denetim satırı,
iç not, iade devri) tanıyacak kadarını yapar. Amaç çekirdek depoyu taklit
etmek değil, servisin doğru anda doğru satırı yazdığını görmek.
"""

from __future__ import annotations

from typing import Any


class FakeLog:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict[str, Any]]] = []

    def _add(self, level: str, message: str, **fields: Any) -> None:
        self.records.append((level, message, fields))

    def info(self, message: str, **fields: Any) -> None:
        self._add("info", message, **fields)

    def warning(self, message: str, **fields: Any) -> None:
        self._add("warning", message, **fields)

    def error(self, message: str, **fields: Any) -> None:
        self._add("error", message, **fields)


class FakeStore:
    """`ModuleStore` yüzeyi. Satırları bellekte tutar."""

    def __init__(self, module_id: str = "store_requests") -> None:
        self.module_id = module_id
        self.audit: list[dict[str, Any]] = []
        self.notes: list[dict[str, Any]] = []
        self.handoff: list[dict[str, Any]] = []

    def table(self, name: str) -> str:
        return f"mod_{self.module_id}_{name}"

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        text = " ".join(sql.split())
        if "_audit" in text:
            keys = ("request_id", "action", "reason", "actor", "result", "detail", "created_at")
            self.audit.append(dict(zip(keys, params, strict=False)))
        elif "_notes" in text:
            keys = ("request_id", "body", "actor", "created_at")
            row = dict(zip(keys, params, strict=False))
            row["id"] = len(self.notes) + 1
            self.notes.append(row)
        elif "_handoff" in text:
            keys = ("request_id", "order_id", "amount", "items", "actor", "reason", "created_at")
            self.handoff.append(dict(zip(keys, params, strict=False)))

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        return None

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        text = " ".join(sql.split())
        if "_notes" in text:
            return [row for row in self.notes if row["request_id"] == params[0]]
        if "_handoff" in text:
            return [row for row in self.handoff if row["request_id"] == params[0]]
        if "_audit" in text:
            rows = list(reversed(self.audit))
            if "WHERE request_id" in text:
                rows = [row for row in rows if row["request_id"] == params[0]]
            return rows
        return []


class FakeApi:
    """`store.api` yeteneğinin testlik yüzü. Yalnız kullanılan metotlar var."""

    def __init__(self, requests: dict[int, dict[str, Any]] | None = None,
                 orders: dict[int, dict[str, Any]] | None = None) -> None:
        self.requests_by_id = requests or {}
        self.orders_by_id = orders or {}
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()
        self.list_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.pages: list[dict[str, Any]] = []
        self.users_payload: dict[str, Any] = {"items": [{"id": 1, "name": "Ayşe"}]}
        #: İade kargosu. `None` = "bu talebin kargosu yok" — mağazada bu 404
        #: DEĞİL 200'dür ve geçerli bir cevaptır.
        self.return_shipment: dict[str, Any] | None = None
        self.return_stage = "yok"
        self.return_received = False
        self.label_purchased = False
        self.return_offers: list[dict[str, Any]] = [
            {"id": "of_1", "providerName": "Sürat Kargo", "price": "84.50", "currency": "TRY"},
        ]
        self.label_bytes = b"%PDF-1.4 sahte etiket"
        #: GERÇEKTEN satın alınan etiketler. Kuru provanın buraya satır
        #: eklememesi, "prova para harcamadı"nın ölçülebilir kanıtıdır.
        self.purchases: list[int] = []

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        if name in self.fail:
            raise RuntimeError(f"{name} patladı")
        self.calls.append((name, args, kwargs))

    def used(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for called, _, kwargs in self.calls if called == name]

    def args(self, name: str) -> list[tuple[Any, ...]]:
        return [args for called, args, _ in self.calls if called == name]

    async def bbd_return_requests(self, filters: Any = None, *, page: int = 1,
                                  per_page: int | None = None) -> dict[str, Any]:
        self._record("bbd_return_requests", filters, page=page, per_page=per_page)
        if self.pages:
            return self.pages[min(page, len(self.pages)) - 1]
        return self.list_payload

    async def bbd_return_request(self, request_id: int) -> dict[str, Any]:
        self._record("bbd_return_request", request_id)
        if request_id not in self.requests_by_id:
            raise RuntimeError("Talep bulunamadı")
        return self.requests_by_id[request_id]

    async def bbd_update_return_request(self, request_id: int, *, payload: dict[str, Any],
                                        reason: str, actor: str = "",
                                        dry_run: bool | None = None) -> dict[str, Any]:
        self._record("bbd_update_return_request", request_id, payload=payload, reason=reason,
                     actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run), "sent": not dry_run}

    # ------------------------------------------------------- iade kargosu
    #
    # MAĞAZANIN BÜTÜN BU UÇLARI AYNI GÖVDEYİ DÖNER (`ReturnShipmentController::present`):
    # "aç", "senkronla", "etiket al" ve salt okuma tek sözlükten okunur. Sahte
    # de öyle davranır — ayrı biçimler üretseydi testler gerçekte olmayan bir
    # sözleşmeyi doğrulardı.

    def _present(self) -> dict[str, Any]:
        acik = self.return_shipment is not None
        return {
            "rmaId": 1, "orderId": 9,
            "rmaStatus": {"id": 2, "title": "Onaylandı"},
            "stage": self.return_stage, "stageLabel": "İade gönderisi yok" if not acik else "",
            "received": bool(self.return_received),
            "receivedAt": "2026-08-16 10:00:00" if self.return_received else None,
            "labelPurchased": bool(self.label_purchased),
            "labelStored": bool(self.label_purchased),
            "shipment": self.return_shipment,
            "refund": {"allowedHere": False, "code": "RMA_MONEY_TRANSITION_BLOCKED",
                       "panelButtonUnlocked": True,
                       "message": "Para iadesi bu uçtan yapılamaz; panelden yapılır."},
        }

    async def bbd_return_request_shipment(self, request_id: int) -> dict[str, Any]:
        self._record("bbd_return_request_shipment", request_id)
        return self._present()

    @staticmethod
    def _dry(would_change: dict[str, Any]) -> dict[str, Any]:
        """`DryRun::response` zarfı — prova alanları `wouldChange` İÇİNDE gelir.

        Gerçek yazmada alanlar KÖKTEDİR. Sahtenin provada da kök döndürmesi,
        canlıda her provanın boş görüneceği bir kodu testte yeşil gösterirdi.
        """
        return {"dryRun": True, "wouldChange": would_change}

    async def bbd_open_return_request_shipment(self, request_id: int, *, reason: str,
                                               actor: str = "",
                                               dry_run: bool | None = None) -> dict[str, Any]:
        self._record("bbd_open_return_request_shipment", request_id, reason=reason, actor=actor,
                     dry_run=dry_run)
        if dry_run:
            return self._dry({
                "action": "returnShipment.create",
                "rmaId": int(request_id),
                "existingShipmentId": (self.return_shipment or {}).get("id"),
                "wouldCreate": self.return_shipment is None,
                "rmaStatus": {"id": 2, "title": "Onaylandı"},
                "wouldSpendMoney": False, "labelPurchased": False,
            })
        if self.return_shipment is None:
            # MÜKERRER KORUMALI: ikinci çağrı yeni gönderi açmaz, var olanı döner.
            self.return_shipment = {"id": 77, "providerName": "Sürat Kargo",
                                    "canPurchase": True, "canCancel": True}
            self.return_stage = "acik"
        return self._present()

    async def bbd_sync_return_request_shipment(self, request_id: int, *, reason: str,
                                               actor: str = "",
                                               dry_run: bool | None = None) -> dict[str, Any]:
        self._record("bbd_sync_return_request_shipment", request_id, reason=reason, actor=actor,
                     dry_run=dry_run)
        if dry_run:
            return self._dry({"action": "returnShipment.sync", "rmaId": int(request_id),
                              "shipmentId": 77, "stage": self.return_stage})
        return self._present()

    async def bbd_purchase_return_request_label(self, request_id: int, *, offer_id: str = "",
                                                reason: str, actor: str = "",
                                                dry_run: bool | None = None) -> dict[str, Any]:
        self._record("bbd_purchase_return_request_label", request_id, offer_id=offer_id,
                     reason=reason, actor=actor, dry_run=dry_run)
        if dry_run:
            return self._dry({
                "action": "returnShipment.purchaseLabel", "rmaId": int(request_id),
                "shipmentId": 77, "canPurchase": True, "isPurchased": self.label_purchased,
                "offersReady": True, "offers": self.return_offers,
                "wouldSpendMoney": True,
            })
        self.label_purchased = True
        self.purchases.append(int(request_id))
        return self._present()

    async def bbd_return_request_label_info(self, request_id: int) -> dict[str, Any]:
        self._record("bbd_return_request_label_info", request_id)
        return {"rmaId": request_id, "shipmentId": 77, "labelStored": self.label_purchased,
                "isPurchased": self.label_purchased, "barcode": "BC-1",
                "trackingNumber": "TR-1"}

    async def bbd_return_request_label(self, request_id: int) -> bytes:
        self._record("bbd_return_request_label", request_id)
        return self.label_bytes

    async def order(self, order_id: int) -> dict[str, Any]:
        self._record("order", order_id)
        if order_id not in self.orders_by_id:
            raise RuntimeError("Sipariş bulunamadı")
        return self.orders_by_id[order_id]

    async def admin_users(self) -> dict[str, Any]:
        self._record("admin_users")
        return self.users_payload


class FakeBus:
    """Olay yolu. Yayınlanan olayları biriktirir."""

    def __init__(self, fail: bool = False) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.fail = fail

    async def __call__(self, name: str, payload: dict[str, Any] | None = None) -> None:
        if self.fail:
            raise RuntimeError("dinleyici patladı")
        self.events.append((name, payload or {}))
