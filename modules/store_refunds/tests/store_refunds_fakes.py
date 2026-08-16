"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`FakeStore` SQL'i ayrıştırmaz; servisin yazdığı üç ifadeyi (denetim satırı,
hesap jetonu, jeton güncellemesi) tanıyacak kadarını yapar. Amaç çekirdek
depoyu taklit etmek değil, servisin doğru anda doğru satırı yazdığını görmek.
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

    def __init__(self, module_id: str = "store_refunds") -> None:
        self.module_id = module_id
        self.audit: list[dict[str, Any]] = []
        self.calc: dict[str, dict[str, Any]] = {}

    def table(self, name: str) -> str:
        return f"mod_{self.module_id}_{name}"

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        text = " ".join(sql.split())
        if "_audit" in text and text.startswith("INSERT"):
            keys = ("order_id", "action", "reason", "actor", "result", "detail", "created_at")
            self.audit.append(dict(zip(keys, params, strict=False)))
        elif "_calc" in text and text.startswith("INSERT"):
            token, order_id, total, lines, body, store_total, created = params
            self.calc[token] = {"token": token, "order_id": order_id, "total": total,
                                "lines": lines, "body": body, "store_total": store_total,
                                "status": "preview", "created_at": created}
        elif "_calc" in text and text.startswith("UPDATE"):
            status, actor, reason, applied, token = params
            row = self.calc.get(token)
            if row:
                row.update(status=status, actor=actor, reason=reason, applied_at=applied)

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        if "_calc" in sql:
            return self.calc.get(params[0])
        return None

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if "_audit" in sql:
            rows = list(reversed(self.audit))
            if "WHERE order_id" in sql:
                rows = [row for row in rows if row["order_id"] == params[0]]
            return rows
        return []


class FakeError(RuntimeError):
    """Geçidin `StoreApiError`'ı — yalnız `code` alanı taklit edilir."""

    def __init__(self, message: str, code: str = "http") -> None:
        super().__init__(message)
        self.code = code


class FakeApi:
    """`store.api` yeteneğinin testlik yüzü. Yalnız kullanılan metotlar var."""

    def __init__(self, orders: dict[int, dict[str, Any]] | None = None) -> None:
        self.orders_by_id = orders or {}
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()
        #: Adı buradaysa "uç henüz yayında değil" hatası fırlatılır.
        self.absent: set[str] = set()
        self.refunds_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.requests_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.preview_payload: dict[str, Any] = {}
        self.stats_payload: dict[str, Any] = {}
        self.payments_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.shipments_payload: dict[str, Any] = {"items": [], "meta": {}}

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        if name in self.absent:
            raise FakeError(f"{name} yayında değil", code="bbd_endpoint_missing")
        if name in self.fail:
            raise FakeError(f"{name} patladı")
        self.calls.append((name, args, kwargs))

    def used(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for called, _, kwargs in self.calls if called == name]

    def args(self, name: str) -> list[tuple[Any, ...]]:
        return [args for called, args, _ in self.calls if called == name]

    # ------------------------------------------------------------- okuma

    async def refunds(self, filters: Any = None, *, page: int = 1, per_page: int | None = None,
                      all_pages: bool = False) -> dict[str, Any]:
        self._record("refunds", filters, page=page, per_page=per_page, all_pages=all_pages)
        return self.refunds_payload

    async def bbd_return_requests(self, filters: Any = None, *, page: int = 1,
                                  per_page: int | None = None) -> dict[str, Any]:
        self._record("bbd_return_requests", filters, page=page, per_page=per_page)
        return self.requests_payload

    async def order(self, order_id: int) -> dict[str, Any]:
        self._record("order", order_id)
        if order_id not in self.orders_by_id:
            raise FakeError("Sipariş bulunamadı", code="not_found")
        return self.orders_by_id[order_id]

    async def dashboard_stats(self, *, kind: str = "over-all", start: str = "", end: str = "",
                              channel: str = "") -> dict[str, Any]:
        self._record("dashboard_stats", kind=kind, start=start, end=end, channel=channel)
        return self.stats_payload

    async def bbd_payment_attempts(self, filters: Any = None, *, page: int = 1,
                                   per_page: int | None = None) -> dict[str, Any]:
        self._record("bbd_payment_attempts", filters, page=page, per_page=per_page)
        return self.payments_payload

    async def bbd_shipments(self, filters: Any = None, *, page: int = 1,
                            per_page: int | None = None,
                            all_pages: bool = False) -> dict[str, Any]:
        self._record("bbd_shipments", filters, page=page, per_page=per_page)
        return self.shipments_payload

    # ------------------------------------------------------------- yazma

    async def refund_preview(self, order_id: int, *, items: dict[str, int]) -> dict[str, Any]:
        self._record("refund_preview", order_id, items=items)
        return self.preview_payload

    async def create_refund(self, order_id: int, *, items: dict[str, int],
                            adjustments: dict[str, Any] | None = None, reason: str,
                            actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        self._record("create_refund", order_id, items=items, adjustments=adjustments,
                     reason=reason, actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run), "sent": not dry_run}

    async def bbd_refund_payment(self, attempt_id: int, *, amount: int, reason: str,
                                 actor: str = "",
                                 dry_run: bool | None = None) -> dict[str, Any]:
        self._record("bbd_refund_payment", attempt_id, amount=amount, reason=reason,
                     actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run), "status": "approved",
                "reference": "BNK-1"}

    async def bbd_update_return_request(self, request_id: int, *, payload: dict[str, Any],
                                        reason: str, actor: str = "",
                                        dry_run: bool | None = None) -> dict[str, Any]:
        self._record("bbd_update_return_request", request_id, payload=payload, reason=reason,
                     actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def bbd_return_shipment(self, shipment_id: int, *, reason: str, actor: str = "",
                                  dry_run: bool | None = None) -> dict[str, Any]:
        self._record("bbd_return_shipment", shipment_id, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run), "tracking_number": "IADE-9"}

    async def add_order_comment(self, order_id: int, *, comment: str, notify: bool = False,
                                reason: str, actor: str = "",
                                dry_run: bool | None = None) -> dict[str, Any]:
        self._record("add_order_comment", order_id, comment=comment, notify=notify,
                     reason=reason, actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}


class FakePrinter:
    """`printer` platform yeteneğinin testlik yüzü."""

    def __init__(self) -> None:
        self.printed: list[Any] = []

    async def print_file(self, path: Any, *, title: str = "", copies: int = 1) -> dict[str, Any]:
        self.printed.append((path, copies))
        return {"printer": "HP-Ofis"}

    async def status(self) -> dict[str, Any]:
        return {"ready": True, "target": {"name": "HP-Ofis"}}


#: CANLI BİÇİM — `GET /api/admin/refunds` (bbdstore.com.tr, 2026-08-13).
#: Alan adları camelCase, gömülü `order` nesnesi YOK, `items` liste ucunda
#: HER ZAMAN boş. Aşağıdaki `ORDER` sözlüğü snake_case yazılmıştır (BBD
#: uçlarının olası biçimi); ikisi birden tutuluyor çünkü modül İKİ YAZIMI DA
#: okumak zorunda. Sahte veri gerçeğin yerine geçmesin diye bu blok canlıdan
#: kopyalanmıştır — değiştirilmeden bırakın.
LIVE_REFUND = {
    "id": 8,
    "orderId": 5,
    "orderIncrementId": "5",
    "state": "refunded",
    "emailSent": True,
    "totalQty": 7,
    "subTotal": 8393,
    "grandTotal": 8393,
    "taxAmount": 0,
    "discountAmount": 0,
    "shippingAmount": 0,
    "shippingTaxAmount": 0,
    "adjustmentRefund": 0,
    "adjustmentFee": 0,
    "createdAt": "2026-07-25 13:40:46",
    "billedTo": "veysel kemal TUNCER",
    "orderStatus": "closed",
    "orderDate": "2026-07-10 02:33:36",
    "channelName": "Benim Başarı Dünyam",
    "customerName": "veysel kemal TUNCER",
    "customerEmail": "vveyselkemall@gmail.com",
    "paymentMethod": None,
    "paymentTitle": None,
    "items": [],
}

#: CANLI BİÇİM — `GET /api/admin/orders/{id}`. Zarfsız (düz sözlük), camelCase.
#: `shipping_amount_refunded` alanı YAYINLANMIYOR — kargo iadesi bu yüzden
#: kredi notlarından hesaplanır. Canlı siparişte kargo 0'dı; buradaki 120,00 ₺
#: kargo yolunu sınamak için konmuştur, alan adları canlıdaki gibidir.
LIVE_ORDER = {
    "id": 5,
    "incrementId": "5",
    "status": "closed",
    "isGuest": False,
    "customerEmail": "vveyselkemall@gmail.com",
    "customerFirstName": "veysel kemal",
    "customerLastName": "TUNCER",
    "customer": None,                      # canlıda NULL gelebiliyor
    "paymentMethod": "kuveytturk",
    "paymentTitle": "Kredi/Banka Kartı ile Öde",
    "totalQtyOrdered": 7,
    "grandTotal": 8393,
    "grandTotalInvoiced": 8393,
    "grandTotalRefunded": 0,
    "subTotal": 8393,
    "taxAmount": 0,
    "discountAmount": 0,
    "shippingAmount": 120,
    "createdAt": "2026-07-10 02:33:36",
    "items": [{
        "id": 5,
        "sku": "BBD2026SKU0540",
        "type": "simple",
        "name": "ÇAP Yayınları TYT Fizik Seti (2025-2026)",
        "productId": 547,
        "qtyOrdered": 7,
        "qtyShipped": 0,
        "qtyInvoiced": 7,
        "qtyCanceled": 0,
        "qtyRefunded": 0,
        "price": 1199,
        "total": 8393,
        "taxAmount": 0,
        "discountAmount": 0,
    }],
}

#: CANLI BİÇİM — `GET /api/admin/dashboard/stats?type=total-sales`.
#: Zarfsız TEK ELEMANLI LİSTE döner ve tutar `statistics.total_sales.current`
#: altındadır. Bu ad aranmadığı için iade oranı KPI'ı hep boş kalıyordu.
LIVE_SALES_STATS = [{
    "type": "total-sales",
    "dateRange": "01 Ağu - 13 Ağu",
    "statistics": {
        "total_orders": {"previous": 4, "current": 7, "progress": 75},
        "total_sales": {"previous": 8, "current": 16, "formatted_total": "₺16,00",
                        "progress": 100},
        "over_time": [{"label": "03 Ağu", "total": "2.0000", "count": 1}],
    },
}]

#: CANLI BİÇİM — `GET /api/admin/bbd/return-requests` (bbdstore.com.tr,
#: 2026-08-16). Bu uç bir dönem 404 dönüyordu; artık YAYINDA ve yanıtı şu üç
#: şeyi söylüyor:
#:  · Yazım KARIŞIK: `order_id`/`created_at` snake, `orderIncrementId`/
#:    `customerName`/`itemCount` camel — aynı sözlükte yan yana.
#:  · Durum düz metin DEĞİL, SÖZLÜK: `{"id", "title", "color"}`.
#:  · Başlıklar TÜRKÇE (mağazada `rma_statuses`/`rma_reasons` Türkçeleştirildi).
#: Canlıdan kopyalanmıştır — değiştirilmeden bırakın.
LIVE_RETURN_REQUEST = {
    "id": 2,
    "order_id": 11,
    "rma_status_id": 5,
    "package_condition": "packed",
    "created_at": "2026-07-20T22:17:51.000000Z",
    "updated_at": "2026-07-20T22:19:30.000000Z",
    "status": {"id": 5, "title": "İade Edildi", "color": "#0d9488"},
    "reason": "Üretim Hatası",
    "reasons": ["Üretim Hatası"],
    "itemCount": 1,
    "totalQuantity": 1,
    "lastActivityAt": "2026-07-21 01:19:30",
    "orderIncrementId": "11",
    "orderStatus": "closed",
    "customerName": "veysel kemal TUNCER",
    "isGuest": False,
}


#: Üç kalemli örnek sipariş. Kalem 1 tamamı faturalanmış, kalem 2 kısmen iade
#: edilmiş, kalem 3 hiç faturalanmamış (iade edilemez olmalı).
ORDER = {
    "id": 12,
    "increment_id": "S-1001",
    "created_at": "2026-08-01T10:00:00",
    "customer_first_name": "Ayşe",
    "customer_last_name": "Yılmaz",
    "customer_email": "ayse@ornek.tr",
    "status": "processing",
    "grand_total": "540.00",
    "grand_total_refunded": "0.00",
    "shipping_amount": "40.00",
    "shipping_tax_amount": "8.00",
    "shipping_amount_refunded": "0.00",
    "items": [
        {"id": 101, "sku": "KTP-1", "name": "Matematik Soru Bankası",
         "qty_ordered": 3, "qty_invoiced": 3, "qty_refunded": 0,
         "price": "100.00", "total": "300.00", "tax_amount": "60.00",
         "discount_amount": "30.00"},
        {"id": 102, "sku": "KTP-2", "name": "Fizik Konu Anlatımı",
         "qty_ordered": 2, "qty_invoiced": 2, "qty_refunded": 1,
         "price": "50.00", "total": "100.00", "tax_amount": "20.00",
         "discount_amount": "0.00"},
        {"id": 103, "sku": "KTP-3", "name": "Deneme Seti",
         "qty_ordered": 1, "qty_invoiced": 0, "qty_refunded": 0,
         "price": "80.00", "total": "80.00", "tax_amount": "16.00",
         "discount_amount": "0.00"},
    ],
}
