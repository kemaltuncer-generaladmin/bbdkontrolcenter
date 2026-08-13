"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`FakeStore` SQL'i ayrıştırmaz; servisin yazdığı dört ifadeyi (denetim satırı,
yasal numara upsert'i, seri upsert'i, varsayılan seri temizliği) tanıyacak
kadarını yapar. Amaç çekirdek depoyu taklit etmek değil, servisin doğru anda
doğru satırı yazdığını görmek.
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

    def __init__(self, module_id: str = "store_invoices") -> None:
        self.module_id = module_id
        self.audit: list[dict[str, Any]] = []
        self.legal: dict[int, dict[str, Any]] = {}
        self.series: dict[str, dict[str, Any]] = {}
        self.broken = False          # True → her yazma patlar (K7 denemesi)
        self.read_broken = False     # True → her okuma patlar

    def table(self, name: str) -> str:
        return f"mod_{self.module_id}_{name}"

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        if self.broken:
            raise RuntimeError("depo yazılamadı")
        query = " ".join(sql.split())
        if "_audit" in query and query.startswith("INSERT"):
            keys = ("invoice_id", "order_id", "action", "reason", "actor", "result",
                    "detail", "created_at")
            self.audit.append(dict(zip(keys, params, strict=False)))
        elif "_legal" in query and query.startswith("INSERT"):
            keys = ("invoice_id", "series", "number", "legal_no", "issued_at", "note",
                    "actor", "updated_at")
            row = dict(zip(keys, params, strict=False))
            self.legal[int(row["invoice_id"])] = row
        elif "_series" in query and query.startswith("INSERT"):
            keys = ("code", "label", "start_no", "pad", "year_reset", "is_default",
                    "note", "actor", "updated_at")
            row = dict(zip(keys, params, strict=False))
            self.series[str(row["code"])] = row
        elif "_series" in query and query.startswith("UPDATE"):
            for code, row in self.series.items():
                if code != params[0]:
                    row["is_default"] = 0

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        if self.read_broken:
            raise RuntimeError("depo okunamadı")
        query = " ".join(sql.split())
        if "_legal" in query and "series = ?" in query:
            for row in self.legal.values():
                if row["series"] == params[0] and int(row["number"]) == int(params[1]):
                    return row
            return None
        return None

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if self.read_broken:
            raise RuntimeError("depo okunamadı")
        query = " ".join(sql.split())
        if "_legal" in query and "IN (" in query:
            wanted = {int(item) for item in params}
            return [row for key, row in self.legal.items() if key in wanted]
        if "_legal" in query:
            return list(self.legal.values())
        if "_series" in query:
            return sorted(self.series.values(), key=lambda row: row["code"])
        if "_audit" in query:
            rows = list(reversed(self.audit))
            if "WHERE invoice_id" in query:
                rows = [row for row in rows if int(row["invoice_id"]) == int(params[0])]
            return rows
        return []


class FakeApi:
    """`store.api` yeteneğinin testlik yüzü. Yalnız kullanılan metotlar var."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()
        self.invoice_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.invoices_by_id: dict[int, dict[str, Any]] = {}
        self.shipment_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.shipments_by_id: dict[int, dict[str, Any]] = {}
        self.order_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.orders_by_id: dict[int, dict[str, Any]] = {}
        self.refund_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.pdf_bytes = b"%PDF-1.4 sahte"

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        if name in self.fail:
            raise RuntimeError(f"{name} patladı")
        self.calls.append((name, args, kwargs))

    def used(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for called, _, kwargs in self.calls if called == name]

    def args_of(self, name: str) -> list[tuple[Any, ...]]:
        return [args for called, args, _ in self.calls if called == name]

    async def invoices(self, filters: Any = None, *, page: int = 1, per_page: int | None = None,
                       all_pages: bool = False) -> dict[str, Any]:
        self._record("invoices", filters, page=page, per_page=per_page, all_pages=all_pages)
        return self.invoice_payload

    async def invoice(self, invoice_id: int) -> dict[str, Any]:
        self._record("invoice", invoice_id)
        if invoice_id not in self.invoices_by_id:
            raise RuntimeError("Kayıt bulunamadı")
        return self.invoices_by_id[invoice_id]

    async def invoice_pdf(self, invoice_id: int) -> bytes:
        self._record("invoice_pdf", invoice_id)
        return self.pdf_bytes

    async def send_invoice_copy(self, invoice_id: int, *, reason: str, actor: str = "",
                                dry_run: bool | None = None) -> dict[str, Any]:
        self._record("send_invoice_copy", invoice_id, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def update_invoice_status(self, invoice_ids: list[int], *, status: str, reason: str,
                                    actor: str = "",
                                    dry_run: bool | None = None) -> dict[str, Any]:
        self._record("update_invoice_status", invoice_ids, status=status, reason=reason,
                     actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def create_invoice(self, order_id: int, *, items: Any = None, reason: str,
                             actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        self._record("create_invoice", order_id, items=items, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run), "id": 900 + int(order_id)}

    async def orders(self, filters: Any = None, *, page: int = 1, per_page: int | None = None,
                     all_pages: bool = False) -> dict[str, Any]:
        self._record("orders", filters, page=page, per_page=per_page, all_pages=all_pages)
        return self.order_payload

    async def shipments(self, filters: Any = None, *, page: int = 1, per_page: int | None = None,
                        all_pages: bool = False) -> dict[str, Any]:
        self._record("shipments", filters, page=page, per_page=per_page, all_pages=all_pages)
        return self.shipment_payload

    async def shipment(self, shipment_id: int) -> dict[str, Any]:
        self._record("shipment", shipment_id)
        if shipment_id not in self.shipments_by_id:
            raise RuntimeError("Gönderi bulunamadı")
        return self.shipments_by_id[shipment_id]

    async def refunds(self, filters: Any = None, *, page: int = 1, per_page: int | None = None,
                      all_pages: bool = False) -> dict[str, Any]:
        self._record("refunds", filters, page=page, per_page=per_page, all_pages=all_pages)
        return self.refund_payload

    async def order(self, order_id: int) -> dict[str, Any]:
        """Sipariş DETAYI — `invoices` dizisi yalnız burada var (canlıda da)."""
        self._record("order", order_id)
        return self.orders_by_id.get(int(order_id), {"id": int(order_id), "invoices": []})


class FakeBus:
    """`ctx.publish` yerine geçer. Yayınlanan olayları biriktirir."""

    def __init__(self, *, broken: bool = False) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.broken = broken

    async def __call__(self, name: str, payload: dict[str, Any]) -> None:
        if self.broken:
            raise RuntimeError("veri yolu kapalı")
        self.events.append((name, payload))


def invoice_raw(invoice_id: int = 7, *, order_id: int = 3, state: str = "paid",
                created: str = "2026-08-01 10:22:03", net: str = "100.00",
                tax: str = "20.00", total: str = "120.00",
                items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """CANLI `/api/admin/invoices` satırının BİREBİR biçimi — camelCase.

    Bu fonksiyon 2026-08-13'te canlı yanıttan kopyalandı. Eskiden snake_case
    üretiyordu ve testler geçiyordu ama ekran boş açılıyordu: sahte veri
    mağazayı değil, kodun varsayımını taklit ediyordu.

    DİKKAT — canlı liste satırında `orderId`, `billingAddress` ve `vatId` YOK;
    müşteri adı `customerName` düz alanında geliyor. Fatura KALEMLERİNDE de
    `taxPercent` yok. Eksikliklerin taklit edilmesi, testin asıl işidir.
    """
    return {
        "id": invoice_id,
        "incrementId": str(invoice_id),
        "orderIncrementId": f"100000{order_id}",
        "state": state,
        "emailSent": True,
        "totalQty": 2,
        "createdAt": created,
        "subTotal": net,
        "baseSubTotal": net,
        "taxAmount": tax,
        "baseTaxAmount": tax,
        "grandTotal": total,
        "baseGrandTotal": total,
        "discountAmount": "0.00",
        "shippingAmount": "0.00",
        "orderStatus": "processing",
        "customerName": "Ayşe Yılmaz",
        "customerEmail": "ayse@example.com",
        "order": {"id": order_id},
        "items": items if items is not None else [
            {"id": 1, "orderItemId": 2, "name": "Kalem", "sku": "KLM-1", "qty": 2,
             "price": "50.00", "basePrice": "50.00", "total": net, "baseTotal": net,
             "taxAmount": tax, "discountAmount": "0.00", "productId": 11},
        ],
    }


def invoice_detail_raw(invoice_id: int = 7, *, order_id: int = 3,
                       **kwargs: Any) -> dict[str, Any]:
    """CANLI `/api/admin/invoices/{id}` biçimi: adres `order.addresses` altında."""
    row = invoice_raw(invoice_id, order_id=order_id, **kwargs)
    row["order"] = {
        "id": order_id,
        "addresses": [
            {"id": 90, "addressType": "order_shipping", "firstName": "Ayşe",
             "lastName": "Yılmaz", "companyName": "", "vatId": None},
            {"id": 91, "addressType": "order_billing", "firstName": "Ayşe",
             "lastName": "Yılmaz", "companyName": "Yılmaz Ltd.", "vatId": "1234567890",
             "email": "ayse@example.com"},
        ],
    }
    return row


def invoice_raw_snake(invoice_id: int = 7, *, order_id: int = 3,
                      state: str = "paid") -> dict[str, Any]:
    """ESKİ snake_case biçim. Mağaza normalleştirmeyi değiştirirse ekranın
    kırılmadığını göstermek için duruyor — `pick` iki biçimi de okumalı."""
    return {
        "id": invoice_id,
        "increment_id": f"INV-{invoice_id:04d}",
        "order_id": order_id,
        "state": state,
        "created_at": "2026-08-01 10:22:03",
        "sub_total": "100.00",
        "tax_amount": "20.00",
        "grand_total": "120.00",
        "billing_address": {"first_name": "Ayşe", "last_name": "Yılmaz",
                            "company_name": "Yılmaz Ltd.", "vat_id": "1234567890",
                            "email": "ayse@example.com"},
        "items": [{"name": "Kalem", "sku": "KLM-1", "qty": 2, "price": "50.00",
                   "total": "100.00", "tax_amount": "20.00", "tax_percent": "20.0000"}],
    }


def shipment_raw(shipment_id: int = 8, *, order_id: int = 12) -> dict[str, Any]:
    """CANLI `/api/admin/shipments` satırı — camelCase, adres düz alanda."""
    return {
        "id": shipment_id,
        "orderId": order_id,
        "orderIncrementId": str(order_id),
        "shippedTo": "veysel kemal TUNCER",
        "customerName": None,
        "customerEmail": "kemal@example.com",
        "totalQty": 2,
        "carrierCode": None,
        "carrierTitle": "Aras",
        "trackNumber": "R123",
        "inventorySourceId": 1,
        "inventorySourceName": "Varsayılan",
        "createdAt": "2026-08-02 11:38:16",
        "billingAddress": {"id": 98, "addressType": "order_billing",
                           "firstName": "veysel kemal", "lastName": "TUNCER",
                           "companyName": "deneme", "email": "kemal@example.com"},
        "items": [{"id": 9, "sku": "KLM-1", "qty": 2}],
    }


def order_raw(order_id: int = 3, *, status: str = "processing",
              total: str = "120.00") -> dict[str, Any]:
    """CANLI `/api/admin/orders` LİSTE satırı — `invoices` dizisi YOKTUR."""
    return {
        "id": order_id,
        "incrementId": f"100000{order_id}",
        "status": status,
        "statusLabel": status.title(),
        "grandTotal": total,
        "baseGrandTotal": total,
        "createdAt": "2026-08-01 09:00:00",
        "customerName": "Ayşe Yılmaz",
        "customerEmail": "ayse@example.com",
        "totalItemCount": 1,
    }
