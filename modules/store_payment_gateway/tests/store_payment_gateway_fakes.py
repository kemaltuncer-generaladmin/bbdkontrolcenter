"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ, SMS GÖNDERMEZ.

`FakeStore` SQL'i ayrıştırmaz; servisin yazdığı birkaç ifadeyi tanıyacak
kadarını yapar. Süzgeç doğruluğu burada DEĞİL, `collect.filter_clause`
üzerinde doğrudan sınanır: sahte bir WHERE yorumlayıcısı yazsaydık testler
gerçek SQL'i değil kendi taklidimizi doğrulardı.
"""

from __future__ import annotations

import re
from typing import Any

#: `INSERT INTO ... (a, b, c) VALUES (?, ?, ?)` içindeki sütun adları.
_INSERT_COLUMNS = re.compile(r"INSERT INTO \S+ \((.*?)\) VALUES", re.DOTALL)
_UPDATE_COLUMNS = re.compile(r"UPDATE \S+ SET (.*?) WHERE", re.DOTALL)


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

    def __init__(self, module_id: str = "store_payment_gateway") -> None:
        self.module_id = module_id
        self.requests: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.prefs: dict[str, str] = {}
        #: Son okuma sorgusu (sql, params). Süzgecin GERÇEKTEN sorguya
        #: geçtiğini sınamak için: sahte depo WHERE yorumlamıyor.
        self.reads: list[tuple[str, tuple[Any, ...]]] = []
        self._next_id = 1

    def table(self, name: str) -> str:
        return f"mod_{self.module_id}_{name}"

    # ------------------------------------------------------------ yazma

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        text = " ".join(sql.split())
        if "_requests" in text and text.startswith("INSERT"):
            columns = [name.strip() for name in _INSERT_COLUMNS.search(text).group(1).split(",")]
            row = dict(zip(columns, params, strict=False))
            row.setdefault("status", "draft")
            row["id"] = self._next_id
            self._next_id += 1
            for column in ("token", "link", "store_status", "sms_state", "sms_at",
                           "settle_method", "settle_ref", "reason", "updated_at"):
                row.setdefault(column, "")
            for column in ("order_id", "invoice_id", "net", "tax", "gross"):
                row.setdefault(column, 0)
            self.requests.append(row)
        elif "_requests" in text and text.startswith("UPDATE"):
            columns = [part.split("=")[0].strip()
                       for part in _UPDATE_COLUMNS.search(text).group(1).split(",")]
            values, request_id = params[:-1], params[-1]
            for row in self.requests:
                if row["id"] == request_id:
                    row.update(dict(zip(columns, values, strict=False)))
        elif "_events" in text and text.startswith("INSERT"):
            keys = ("request_id", "action", "reason", "actor", "result", "detail", "created_at")
            entry = dict(zip(keys, params, strict=False))
            entry["id"] = len(self.events) + 1
            self.events.append(entry)
        elif "_prefs" in text:
            self.prefs[params[0]] = params[1]

    # ------------------------------------------------------------ okuma

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        text = " ".join(sql.split())
        if "COUNT(*)" in text:
            return {"total": len(self.requests)}
        if "_prefs" in text:
            value = self.prefs.get(params[0])
            return {"value": value} if value is not None else None
        if "WHERE id = ?" in text:
            return next((row for row in self.requests if row["id"] == params[-1]), None)
        if "WHERE code = ?" in text:
            return next((row for row in self.requests if row["code"] == params[-1]), None)
        return None

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        text = " ".join(sql.split())
        self.reads.append((text, params))
        if "_events" in text:
            rows = [row for row in reversed(self.events) if row["request_id"] == params[0]]
            return rows
        rows = list(reversed(self.requests))
        if "OFFSET" in text:
            limit, offset = params[-2], params[-1]
            return rows[offset:offset + limit]
        return rows[:params[-1]] if params else rows


class FakeSmsResult:
    def __init__(self, *, accepted: bool = True, dry_run: bool = False,
                 job_id: str = "JOB-1", parts: int = 1) -> None:
        self.accepted = accepted
        self.dry_run = dry_run
        self.job_id = job_id
        self.parts = parts
        self.recipients = 1


class FakeProvider:
    def __init__(self, result: FakeSmsResult | None = None,
                 failure: Exception | None = None) -> None:
        self.result = result or FakeSmsResult()
        self.failure = failure
        self.sent: list[tuple[str, str]] = []

    async def send(self, messages: Any, *, header: str | None = None,
                   **_: Any) -> FakeSmsResult:
        if self.failure:
            raise self.failure
        for message in messages:
            self.sent.append((message.to, message.text))
        return self.result


class FakeNotify:
    """`notify` platform yeteneğinin testlik yüzü."""

    def __init__(self, *, configured: bool = True, dry_run: bool = False,
                 provider: FakeProvider | None = None) -> None:
        self.configured = configured
        self._dry_run = dry_run
        self.provider = provider or FakeProvider()

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    async def ready(self) -> dict[str, Any]:
        return {"provider": "netgsm", "enabled": True, "dryRun": self._dry_run,
                "configured": self.configured, "header": "BBDUNYAM", "error": ""}

    async def sms(self) -> FakeProvider:
        return self.provider


class FakeApi:
    """`store.api` yeteneğinin testlik yüzü. Yalnız kullanılan metotlar var.

    `bbd_create_payment_request` BİLEREK YOKTUR: geçitte de yok. Serbest
    tahsilat yolunu sınayan test onu `add_standalone()` ile takar.
    """

    def __init__(self, products: dict[int, dict[str, Any]] | None = None) -> None:
        self.products_by_id = products or {}
        #: `GET /admin/orders/{id}` — faturalar GÖMÜLÜ gelir (canlıda doğrulandı).
        self.orders_by_id: dict[int, dict[str, Any]] = {}
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()
        self.link_payload: dict[str, Any] = {"token": "TKN-1", "url": "https://ode.me/TKN-1"}
        self.links_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.attempts_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.invoices_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.products_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.tax_categories_payload: dict[str, Any] = {"items": []}
        self.tax_rates_payload: dict[str, Any] = {"items": []}
        self.read_only = False

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        if name in self.fail:
            raise RuntimeError(f"{name} patladı")
        self.calls.append((name, args, kwargs))

    def used(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for called, _, kwargs in self.calls if called == name]

    def state(self) -> dict[str, Any]:
        return {"baseUrl": "https://ornek", "readOnly": self.read_only,
                "dryRunDefault": True, "pageSize": 50, "requireReason": True}

    async def health(self) -> dict[str, Any]:
        self._record("health")
        return {"ok": True, "error": ""}

    async def tax_categories(self) -> dict[str, Any]:
        self._record("tax_categories")
        return self.tax_categories_payload

    async def tax_rates(self, filters: Any = None) -> dict[str, Any]:
        self._record("tax_rates", filters)
        return self.tax_rates_payload

    async def product(self, product_id: int) -> dict[str, Any]:
        self._record("product", product_id)
        if product_id not in self.products_by_id:
            raise RuntimeError("Kayıt bulunamadı")
        return self.products_by_id[product_id]

    async def products(self, filters: Any = None, *, page: int = 1,
                       per_page: int | None = None, all_pages: bool = False) -> dict[str, Any]:
        self._record("products", filters, page=page, per_page=per_page)
        return self.products_payload

    async def bbd_create_payment_link(self, *, order_id: int, amount: int, reason: str,
                                      actor: str = "",
                                      dry_run: bool | None = None) -> dict[str, Any]:
        self._record("bbd_create_payment_link", order_id=order_id, amount=amount,
                     reason=reason, actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run), "data": dict(self.link_payload)}

    async def bbd_cancel_payment_link(self, token: str, *, reason: str, actor: str = "",
                                      dry_run: bool | None = None) -> dict[str, Any]:
        self._record("bbd_cancel_payment_link", token, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def bbd_payment_links(self, filters: Any = None) -> dict[str, Any]:
        self._record("bbd_payment_links", filters)
        return self.links_payload

    async def bbd_payment_attempts(self, filters: Any = None, *, page: int = 1,
                                   per_page: int | None = None) -> dict[str, Any]:
        self._record("bbd_payment_attempts", filters, page=page, per_page=per_page)
        return self.attempts_payload

    async def invoices(self, filters: Any = None, *, page: int = 1,
                       per_page: int | None = None) -> dict[str, Any]:
        self._record("invoices", filters, page=page, per_page=per_page)
        return self.invoices_payload

    async def order(self, order_id: int) -> dict[str, Any]:
        """Sipariş detayı. CANLI DAVRANIŞ: bilinmeyen kimlik hata verir,
        `invoices` gömülüdür ve fatura satırı `orderId` TAŞIMAZ."""
        self._record("order", order_id)
        if int(order_id) not in self.orders_by_id:
            raise RuntimeError("Sipariş bulunamadı")
        return self.orders_by_id[int(order_id)]

    async def record_transaction(self, *, payload: dict[str, Any], reason: str, actor: str = "",
                                 dry_run: bool | None = None) -> dict[str, Any]:
        self._record("record_transaction", payload=payload, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    def add_standalone(self) -> None:
        """Geçide serbest tahsilat ucunu takar (henüz gerçekte YOK)."""
        async def bbd_create_payment_request(*, payload: dict[str, Any], reason: str,
                                             actor: str = "",
                                             dry_run: bool | None = None) -> dict[str, Any]:
            self._record("bbd_create_payment_request", payload=payload, reason=reason,
                         actor=actor, dry_run=dry_run)
            return {"ok": True, "dryRun": bool(dry_run), "data": dict(self.link_payload)}

        self.bbd_create_payment_request = bbd_create_payment_request  # type: ignore[attr-defined]
