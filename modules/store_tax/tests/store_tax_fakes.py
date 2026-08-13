"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`FakeStore` SQL'i ayrıştırmaz; servisin yazdığı dört ifadeyi (denetim satırı,
geçerlilik notu, kullanım sayacı, eşleme jetonu) tanıyacak kadarını yapar. Amaç
çekirdek depoyu taklit etmek değil, servisin doğru anda doğru satırı yazdığını
görmek.
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

    def __init__(self, module_id: str = "store_tax") -> None:
        self.module_id = module_id
        self.audit: list[dict[str, Any]] = []
        self.effective: dict[int, dict[str, Any]] = {}
        self.usage: dict[int, dict[str, Any]] = {}
        self.assign: dict[str, dict[str, Any]] = {}

    def table(self, name: str) -> str:
        return f"mod_{self.module_id}_{name}"

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        text = " ".join(sql.split())
        if "_audit" in text and text.startswith("INSERT"):
            keys = ("target", "action", "reason", "actor", "result", "detail", "created_at")
            self.audit.append(dict(zip(keys, params, strict=False)))
        elif "_effective" in text:
            rate_id, effective_from, note, actor, updated = params
            self.effective[int(rate_id)] = {
                "rate_id": int(rate_id), "effective_from": effective_from, "note": note,
                "actor": actor, "updated_at": updated}
        elif "_usage" in text:
            category_id, count, scanned = params
            self.usage[int(category_id)] = {
                "tax_category_id": int(category_id), "product_count": int(count),
                "scanned_at": scanned}
        elif "_assign" in text and text.startswith("INSERT"):
            token, job_params, rows, created = params
            self.assign[token] = {"token": token, "params": job_params, "rows": rows,
                                  "status": "preview", "created_at": created}
        elif "_assign" in text and text.startswith("UPDATE"):
            status, actor, reason, applied, token = params
            row = self.assign.get(token)
            if row:
                row.update(status=status, actor=actor, reason=reason, applied_at=applied)

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        if "_assign" in sql:
            return self.assign.get(params[0])
        return None

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if "_effective" in sql:
            return list(self.effective.values())
        if "_usage" in sql:
            return list(self.usage.values())
        if "_audit" in sql:
            rows = list(reversed(self.audit))
            if "WHERE target" in " ".join(sql.split()):
                rows = [row for row in rows if row["target"] == params[0]]
            return rows
        return []


class FakeApi:
    """`store.api` yeteneğinin testlik yüzü. Yalnız kullanılan metotlar var."""

    def __init__(self, *, rates: list[dict[str, Any]] | None = None,
                 categories: list[dict[str, Any]] | None = None,
                 products: dict[int, dict[str, Any]] | None = None) -> None:
        self.rate_items = rates if rates is not None else []
        self.category_items = categories if categories is not None else []
        self.products_by_id = products or {}
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()
        self.list_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.invoice_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.refund_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.order_payload: dict[str, Any] = {"items": [], "meta": {}}

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        if name in self.fail:
            raise RuntimeError(f"{name} patladı")
        self.calls.append((name, args, kwargs))

    def used(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for called, _, kwargs in self.calls if called == name]

    def args_of(self, name: str) -> list[tuple[Any, ...]]:
        return [args for called, args, _ in self.calls if called == name]

    # ------------------------------------------------------------- vergi

    async def tax_rates(self, filters: Any = None) -> dict[str, Any]:
        self._record("tax_rates", filters)
        return {"items": list(self.rate_items), "meta": {}}

    async def tax_rate(self, rate_id: int) -> dict[str, Any]:
        self._record("tax_rate", rate_id)
        found = next((item for item in self.rate_items
                      if int(item.get("id", 0)) == int(rate_id)), None)
        if found is None:
            raise RuntimeError("Kayıt bulunamadı")
        return dict(found)

    async def tax_categories(self) -> dict[str, Any]:
        self._record("tax_categories")
        return {"items": list(self.category_items), "meta": {}}

    async def save_tax_rate(self, *, payload: dict[str, Any], rate_id: int | None = None,
                            reason: str, actor: str = "",
                            dry_run: bool | None = None) -> dict[str, Any]:
        self._record("save_tax_rate", rate_id, payload=payload, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run), "sent": not dry_run, "data": {"id": 77}}

    async def save_tax_category(self, *, payload: dict[str, Any], category_id: int | None = None,
                                reason: str, actor: str = "",
                                dry_run: bool | None = None) -> dict[str, Any]:
        self._record("save_tax_category", category_id, payload=payload, reason=reason,
                     actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run), "data": {"id": 9}}

    # ------------------------------------------------------------- ürün

    async def products(self, filters: Any = None, *, page: int = 1, per_page: int | None = None,
                       all_pages: bool = False) -> dict[str, Any]:
        self._record("products", filters, page=page, per_page=per_page, all_pages=all_pages)
        return self.list_payload

    async def product(self, product_id: int) -> dict[str, Any]:
        self._record("product", product_id)
        if product_id not in self.products_by_id:
            raise RuntimeError("Kayıt bulunamadı")
        return dict(self.products_by_id[product_id])

    async def update_product(self, product_id: int, *, payload: dict[str, Any], reason: str,
                             actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        self._record("update_product", product_id, payload=payload, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    # ------------------------------------------------------------- belge

    async def invoices(self, filters: Any = None, *, page: int = 1, per_page: int | None = None,
                       all_pages: bool = False) -> dict[str, Any]:
        self._record("invoices", filters, page=page, per_page=per_page, all_pages=all_pages)
        return self.invoice_payload

    async def refunds(self, filters: Any = None, *, page: int = 1, per_page: int | None = None,
                      all_pages: bool = False) -> dict[str, Any]:
        self._record("refunds", filters, page=page, per_page=per_page, all_pages=all_pages)
        return self.refund_payload

    async def orders(self, filters: Any = None, *, page: int = 1, per_page: int | None = None,
                     all_pages: bool = False) -> dict[str, Any]:
        self._record("orders", filters, page=page, per_page=per_page, all_pages=all_pages)
        return self.order_payload
