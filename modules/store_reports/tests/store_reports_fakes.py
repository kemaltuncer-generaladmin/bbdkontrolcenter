"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`FakeStore` SQL'i ayrıştırmaz; servisin yazdığı dört ifadeyi (kayıt, arşiv,
zamanlama, üretim izi) tanıyacak kadarını yapar. Amaç çekirdek depoyu taklit
etmek değil, servisin doğru anda doğru satırı yazdığını görmek.

`FakeApi` yalnız `ReportsService`'in ÇAĞIRDIĞI metotları taşır. `fail`
kümesine bir metot adı konursa o çağrı patlar — yaprak bazlı hatanın testi
budur.
"""

from __future__ import annotations

import itertools
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

    def __init__(self, module_id: str = "store_reports") -> None:
        self.module_id = module_id
        self.saved: list[dict[str, Any]] = []
        self.schedules: list[dict[str, Any]] = []
        self.runs: list[dict[str, Any]] = []
        self._ids = itertools.count(1)

    def table(self, name: str) -> str:
        return f"mod_{self.module_id}_{name}"

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        text = " ".join(sql.split())
        if "_saved" in text and text.startswith("INSERT"):
            keys = ("name", "leaves", "params", "actor", "created_at")
            row = dict(zip(keys, params, strict=False))
            row.update(id=next(self._ids), active=1)
            self.saved.append(row)
        elif "_saved" in text and text.startswith("UPDATE"):
            reason, actor, moment, saved_id = params
            for row in self.saved:
                if row["id"] == saved_id:
                    row.update(active=0, archived_reason=reason, archived_by=actor,
                               archived_at=moment)
        elif "_schedules" in text and text.startswith("INSERT"):
            keys = ("name", "leaves", "params", "period", "weekday", "day_of_month",
                    "at_time", "actor", "created_at")
            row = dict(zip(keys, params, strict=False))
            row.update(id=next(self._ids), active=1, last_run_at="", last_result="")
            self.schedules.append(row)
        elif "_schedules" in text and "last_run_at = ?" in text:
            moment, result, schedule_id = params
            for row in self.schedules:
                if row["id"] == schedule_id:
                    row.update(last_run_at=moment, last_result=result)
        elif "_schedules" in text and text.startswith("UPDATE"):
            active, actor, schedule_id = params
            for row in self.schedules:
                if row["id"] == schedule_id:
                    row.update(active=active, actor=actor)
        elif "_runs" in text:
            keys = ("kind", "leaves", "actor", "path", "name", "rows", "ok", "error",
                    "created_at")
            self.runs.append(dict(zip(keys, params, strict=False)))

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = await self.fetch_all(sql, params)
        return rows[0] if rows else None

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if "_saved" in sql:
            return [row for row in reversed(self.saved) if row.get("active")]
        if "_schedules" in sql:
            return [row for row in self.schedules if row.get("active")]
        if "_runs" in sql:
            return list(reversed(self.runs))[:params[0] if params else 50]
        return []


def order(order_id: int, day: str, total: str, **extra: Any) -> dict[str, Any]:
    """Bagisto biçiminde sipariş. Para ONDALIK gelir — tuzağın kendisi."""
    payload: dict[str, Any] = {
        "id": order_id,
        "increment_id": f"S{order_id:04d}",
        "created_at": f"{day} 14:20:00",
        "status": "processing",
        "channel_name": "Varsayılan",
        "payment_title": "Kredi kartı",
        "customer_id": 100 + order_id,
        "customer_first_name": "Ali",
        "customer_last_name": f"Veli {order_id}",
        "customer_email": f"musteri{order_id}@ornek.com",
        "customer_group": "Genel",
        "grand_total": total,
        "sub_total": total,
        "shipping_amount": "20.00",
        "discount_amount": "0.00",
        "tax_amount": "0.00",
        "coupon_code": "",
        "total_item_count": 1,
        "shipping_address": {"city": "İstanbul", "state": "34"},
    }
    payload.update(extra)
    return payload


def line(product_id: int, name: str, qty: int, total: str,
         tax_percent: float = 20.0) -> dict[str, Any]:
    return {"product_id": product_id, "sku": f"SKU-{product_id}", "name": name,
            "qty_ordered": qty, "price": total, "total": total,
            "tax_amount": f"{float(total) * tax_percent / 100:.2f}",
            "tax_percent": tax_percent}


class FakeApi:
    """`store.api` yeteneğinin testlik yüzü. Yalnız kullanılan metotlar var."""

    def __init__(self, orders: list[dict[str, Any]] | None = None) -> None:
        self.order_list = orders or []
        self.product_list: list[dict[str, Any]] = []
        self.shipment_list: list[dict[str, Any]] = []
        self.refund_list: list[dict[str, Any]] = []
        self.invoice_list: list[dict[str, Any]] = []
        self.transaction_list: list[dict[str, Any]] = []
        self.audit_list: list[dict[str, Any]] = []
        self.backup_list: list[dict[str, Any]] = []
        self.notification_list: list[dict[str, Any]] = []
        self.trial_list: list[dict[str, Any]] = []
        self.customer_list: list[dict[str, Any]] = []
        self.details: dict[int, dict[str, Any]] = {}
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        if name in self.fail:
            raise RuntimeError(f"{name} patladı")
        self.calls.append((name, args, kwargs))

    def used(self, name: str) -> int:
        return len([1 for called, _, _ in self.calls if called == name])

    def state(self) -> dict[str, Any]:
        return {"baseUrl": "https://ornek", "readOnly": True}

    @staticmethod
    def _wrap(items: list[dict[str, Any]]) -> dict[str, Any]:
        return {"items": items, "meta": {"total": len(items)}, "truncated": False}

    async def orders(self, filters: Any = None, *, page: int = 1, per_page: int | None = None,
                     all_pages: bool = False) -> dict[str, Any]:
        self._record("orders", filters, all_pages=all_pages)
        return self._wrap(self.order_list)

    async def order(self, order_id: int) -> dict[str, Any]:
        self._record("order", order_id)
        if order_id not in self.details:
            raise RuntimeError("Kayıt bulunamadı")
        return self.details[order_id]

    async def products(self, filters: Any = None, *, page: int = 1, per_page: int | None = None,
                       all_pages: bool = False) -> dict[str, Any]:
        self._record("products", filters, all_pages=all_pages)
        return self._wrap(self.product_list)

    async def customers(self, filters: Any = None, *, page: int = 1,
                        per_page: int | None = None,
                        all_pages: bool = False) -> dict[str, Any]:
        self._record("customers", filters, all_pages=all_pages)
        return self._wrap(self.customer_list)

    async def invoices(self, filters: Any = None, *, page: int = 1, per_page: int | None = None,
                       all_pages: bool = False) -> dict[str, Any]:
        self._record("invoices", filters)
        return self._wrap(self.invoice_list)

    async def refunds(self, filters: Any = None, *, page: int = 1, per_page: int | None = None,
                      all_pages: bool = False) -> dict[str, Any]:
        self._record("refunds", filters)
        return self._wrap(self.refund_list)

    async def transactions(self, filters: Any = None, *, page: int = 1,
                           per_page: int | None = None,
                           all_pages: bool = False) -> dict[str, Any]:
        self._record("transactions", filters)
        return self._wrap(self.transaction_list)

    async def bbd_shipments(self, filters: Any = None, *, page: int = 1,
                            per_page: int | None = None,
                            all_pages: bool = False) -> dict[str, Any]:
        self._record("bbd_shipments", filters)
        return self._wrap(self.shipment_list)

    async def bbd_audit(self, filters: Any = None, *, page: int = 1,
                        per_page: int | None = None) -> dict[str, Any]:
        self._record("bbd_audit", filters)
        return self._wrap(self.audit_list)

    async def bbd_backups(self) -> dict[str, Any]:
        self._record("bbd_backups")
        return self._wrap(self.backup_list)

    async def bbd_notifications(self, filters: Any = None, *, page: int = 1,
                                per_page: int | None = None) -> dict[str, Any]:
        self._record("bbd_notifications", filters)
        return self._wrap(self.notification_list)

    async def bbd_trial_members(self, filters: Any = None, *, page: int = 1,
                                per_page: int | None = None,
                                all_pages: bool = False) -> dict[str, Any]:
        self._record("bbd_trial_members", filters)
        return self._wrap(self.trial_list)

    async def bbd_reconciliation(self, filters: Any = None) -> dict[str, Any]:
        self._record("bbd_reconciliation", filters)
        return {"matched": 3, "mismatched": 1, "rows": []}

    async def bbd_carriers(self) -> dict[str, Any]:
        self._record("bbd_carriers")
        return self._wrap([{"name": "Aras"}])

    async def category_tree(self) -> dict[str, Any]:
        # CANLIDAKİ gibi İÇ İÇE ve tepede tek "Kök": düz liste taklidi,
        # ağacı düzleştiren kodu hiç sınamıyordu.
        self._record("category_tree")
        return self._wrap([{"id": 1, "name": "Kök", "parentId": None, "children": [
            {"id": 2, "name": "Kitap", "parentId": 1, "children": [
                {"id": 3, "name": "Deneme", "parentId": 2, "children": []},
            ]},
        ]}])

    async def snapshot(self, *, refresh: bool = False) -> dict[str, Any]:
        self._record("snapshot", refresh=refresh)
        return {"parts": {"channels": [{"code": "default", "name": "Varsayılan"}],
                          "customer_groups": [{"name": "Genel"}]},
                "errors": [], "stale": False, "storedAt": ""}
