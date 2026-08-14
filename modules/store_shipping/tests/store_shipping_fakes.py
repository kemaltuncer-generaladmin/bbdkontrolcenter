"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`FakeStore` SQL'i ayrıştırmaz; servisin yazdığı dört ifadeyi (denetim satırı,
bölge upsert'i, manifesto kaydı, tercih upsert'i) tanıyacak kadarını yapar.
Amaç çekirdek depoyu taklit etmek değil, servisin doğru anda doğru satırı
yazdığını görmek.
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

    def __init__(self, module_id: str = "store_shipping") -> None:
        self.module_id = module_id
        self.audit: list[dict[str, Any]] = []
        self.zones: list[dict[str, Any]] = []
        self.manifests: list[dict[str, Any]] = []
        self.prefs: dict[str, str] = {}

    def table(self, name: str) -> str:
        return f"mod_{self.module_id}_{name}"

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        text = " ".join(sql.split())
        if "_audit" in text and text.startswith("INSERT"):
            keys = ("shipment_id", "order_id", "action", "reason", "actor", "result",
                    "detail", "created_at")
            self.audit.append(dict(zip(keys, params, strict=False)))
        elif "_zones" in text and text.startswith("INSERT"):
            keys = ("city", "district", "zone", "surcharge", "delivers", "note", "actor",
                    "updated_at")
            row = dict(zip(keys, params, strict=False))
            row["id"] = len(self.zones) + 1
            existing = [item for item in self.zones
                        if item["city"] == row["city"] and item["district"] == row["district"]]
            if existing:
                existing[0].update(row)
            else:
                self.zones.append(row)
        elif "_manifests" in text and text.startswith("INSERT"):
            keys = ("name", "path", "carrier", "shipments", "count", "actor", "created_at")
            self.manifests.append(dict(zip(keys, params, strict=False)))
        elif "_prefs" in text:
            self.prefs[params[0]] = params[1]

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        if "_prefs" in sql:
            value = self.prefs.get(params[0])
            return {"value": value} if value is not None else None
        return None

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if "_zones" in sql:
            return [dict(row) for row in self.zones]
        if "_audit" in sql:
            rows = list(reversed(self.audit))
            if "WHERE shipment_id" in sql:
                rows = [row for row in rows if row["shipment_id"] == params[0]]
            return rows
        return []


class FakeApi:
    """`store.api` yeteneğinin testlik yüzü. Yalnız kullanılan metotlar var."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()
        self.orders_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.order_by_id: dict[int, dict[str, Any]] = {}
        self.shipments_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.shipment_by_id: dict[int, dict[str, Any]] = {}
        self.offers_payload: dict[str, Any] = {"items": []}
        self.carriers_payload: dict[str, Any] = {"items": []}
        self.rates_payload: dict[str, Any] = {}
        #: `bbd/orders` satırları — kargo firması ve e-posta buradan gelir.
        self.bbd_order_rows: list[dict[str, Any]] = []
        self.label_bytes: dict[int, bytes] = {}
        #: Canlı mağazadaki tek kanal: kod `default`, kimlik 1.
        self.channels_payload: dict[str, Any] = {
            "items": [{"id": 1, "code": "default", "name": "Benim Başarı Dünyam"}]}

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        if name in self.fail:
            raise RuntimeError(f"{name} patladı")
        self.calls.append((name, args, kwargs))

    def used(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for called, _, kwargs in self.calls if called == name]

    def args_of(self, name: str) -> list[tuple[Any, ...]]:
        return [args for called, args, _ in self.calls if called == name]

    # ------------------------------------------------------------ sipariş

    async def orders(self, filters: Any = None, *, page: int = 1, per_page: int | None = None,
                     all_pages: bool = False) -> dict[str, Any]:
        self._record("orders", filters, page=page, per_page=per_page, all_pages=all_pages)
        return self.orders_payload

    async def channels(self) -> dict[str, Any]:
        self._record("channels")
        return self.channels_payload

    async def order(self, order_id: int) -> dict[str, Any]:
        self._record("order", order_id)
        if order_id not in self.order_by_id:
            raise RuntimeError("Kayıt bulunamadı")
        return self.order_by_id[order_id]

    # ------------------------------------------------------------- kargo

    async def bbd_shipments(self, filters: Any = None, *, page: int = 1,
                            per_page: int | None = None,
                            all_pages: bool = False) -> dict[str, Any]:
        self._record("bbd_shipments", filters, page=page, per_page=per_page,
                     all_pages=all_pages)
        return self.shipments_payload

    async def bbd_shipment(self, shipment_id: int) -> dict[str, Any]:
        self._record("bbd_shipment", shipment_id)
        if shipment_id not in self.shipment_by_id:
            raise RuntimeError("Gönderi bulunamadı")
        return self.shipment_by_id[shipment_id]

    async def bbd_create_shipment(self, order_id: int, *, payload: dict[str, Any], reason: str,
                                  actor: str = "",
                                  dry_run: bool | None = None) -> dict[str, Any]:
        self._record("bbd_create_shipment", order_id, payload=payload, reason=reason,
                     actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run), "sent": not dry_run, "data": {"id": 77}}

    async def bbd_orders(self, filters: Any = None, *, page: int = 1,
                         per_page: int | None = None,
                         all_pages: bool = False) -> dict[str, Any]:
        """`bbd/orders` — kargo firmasını taşıyan zengin liste.

        Alanlar CANLIDAKİ gibi snake_case: `shipping_title`, `customer_email`.
        """
        self._record("bbd_orders", filters, page=page, per_page=per_page)
        return {"items": self.bbd_order_rows, "meta": {"total": len(self.bbd_order_rows)}}

    async def create_shipment(self, order_id: int, *, payload: dict[str, Any], reason: str,
                              actor: str = "",
                              dry_run: bool | None = None) -> dict[str, Any]:
        """Bagisto'nun KENDİ gönderi ucu — test yolu.

        `bbd_create_shipment` (Geliver) ile karıştırılmamalı: ikisi ayrı uç,
        ayrı gövde, ayrı sonuç. Sahtenin ikisini de taşıması, servisin hangi
        yola gittiğini testin görebilmesi içindir.
        """
        self._record("create_shipment", order_id, payload=payload, reason=reason,
                     actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run), "sent": not dry_run, "data": {"id": 501}}

    async def bbd_shipment_offers(self, shipment_id: int) -> dict[str, Any]:
        self._record("bbd_shipment_offers", shipment_id)
        return self.offers_payload

    async def bbd_purchase_shipment(self, shipment_id: int, *, offer_id: str, reason: str,
                                    actor: str = "",
                                    dry_run: bool | None = None) -> dict[str, Any]:
        self._record("bbd_purchase_shipment", shipment_id, offer_id=offer_id, reason=reason,
                     actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run), "sent": not dry_run,
                "price": "34.50", "data": {"tracking_number": "1234567890"}}

    async def bbd_cancel_shipment(self, shipment_id: int, *, reason: str, actor: str = "",
                                  dry_run: bool | None = None) -> dict[str, Any]:
        self._record("bbd_cancel_shipment", shipment_id, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def bbd_return_shipment(self, shipment_id: int, *, reason: str, actor: str = "",
                                  dry_run: bool | None = None) -> dict[str, Any]:
        self._record("bbd_return_shipment", shipment_id, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def bbd_sync_shipment(self, shipment_id: int, *, reason: str, actor: str = "",
                                dry_run: bool | None = None) -> dict[str, Any]:
        self._record("bbd_sync_shipment", shipment_id, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def bbd_shipment_label(self, shipment_id: int) -> bytes:
        self._record("bbd_shipment_label", shipment_id)
        if shipment_id not in self.label_bytes:
            raise RuntimeError("Etiket yok")
        return self.label_bytes[shipment_id]

    async def bbd_carriers(self) -> dict[str, Any]:
        self._record("bbd_carriers")
        return self.carriers_payload

    async def bbd_test_carrier(self, carrier: str, *, reason: str, actor: str = "",
                               dry_run: bool | None = None) -> dict[str, Any]:
        self._record("bbd_test_carrier", carrier, reason=reason, actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run), "message": "Bağlantı kuruldu."}

    async def bbd_shipping_rates(self) -> dict[str, Any]:
        self._record("bbd_shipping_rates")
        return self.rates_payload

    async def bbd_update_shipping_rates(self, *, payload: dict[str, Any], reason: str,
                                        actor: str = "",
                                        dry_run: bool | None = None) -> dict[str, Any]:
        self._record("bbd_update_shipping_rates", payload=payload, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def bbd_refresh_price_list(self, *, reason: str, actor: str = "",
                                     dry_run: bool | None = None) -> dict[str, Any]:
        self._record("bbd_refresh_price_list", reason=reason, actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}


class FakeNotifier:
    """`store.notify.send` yeteneğinin testlik yüzü."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, *, template: str, to: str, data: dict[str, Any], reason: str,
                   actor: str = "", dry_run: bool = True) -> dict[str, Any]:
        self.sent.append({"template": template, "to": to, "data": data, "reason": reason,
                          "dryRun": dry_run})
        return {"ok": True, "dryRun": dry_run}


class Events:
    """Olay yolunun testlik yüzü."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, event: str, payload: dict[str, Any]) -> None:
        self.published.append((event, payload))

    def names(self) -> list[str]:
        return [name for name, _ in self.published]
