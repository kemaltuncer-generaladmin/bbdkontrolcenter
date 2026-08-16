"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`FakeStore` SQL'i ayrıştırmaz; servisin yazdığı üç ifadeyi (denetim satırı,
toplu iş kaydı, tercih upsert'i) tanıyacak kadarını yapar. Amaç çekirdek depoyu
taklit etmek değil, servisin doğru anda doğru satırı yazdığını görmek.
"""

from __future__ import annotations

import re
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

    def __init__(self, module_id: str = "store_orders") -> None:
        self.module_id = module_id
        self.audit: list[dict[str, Any]] = []
        self.batch: dict[str, dict[str, Any]] = {}
        self.prefs: dict[str, str] = {}

    def table(self, name: str) -> str:
        return f"mod_{self.module_id}_{name}"

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        text = " ".join(sql.split())
        if "_audit" in text and text.startswith("INSERT"):
            keys = ("order_id", "action", "reason", "actor", "result", "detail", "created_at")
            self.audit.append(dict(zip(keys, params, strict=False)))
        elif "_batch" in text and text.startswith("INSERT"):
            token, kind, job_params, rows, created = params
            self.batch[token] = {"token": token, "kind": kind, "params": job_params,
                                 "rows": rows, "status": "preview", "created_at": created}
        elif "_batch" in text and text.startswith("UPDATE"):
            status, actor, reason, applied, job_params, token = params
            row = self.batch.get(token)
            if row:
                row.update(status=status, actor=actor, reason=reason, applied_at=applied,
                           params=job_params)
        elif "_prefs" in text:
            self.prefs[params[0]] = params[1]

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        if "_batch" in sql:
            return self.batch.get(params[0])
        return None

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if "_prefs" in sql:
            return [{"key": key, "value": value} for key, value in self.prefs.items()]
        if "_audit" in sql:
            rows = list(reversed(self.audit))
            if re.search(r"WHERE order_id", sql):
                rows = [row for row in rows if row["order_id"] == params[0]]
            return rows
        return []


class FakeApi:
    """`store.api` yeteneğinin testlik yüzü. Yalnız kullanılan metotlar var."""

    def __init__(self, orders: dict[int, dict[str, Any]] | None = None,
                 shallow: list[dict[str, Any]] | None = None) -> None:
        self.orders_by_id = orders or {}
        #: Tam taramanın döndüreceği SIĞ satırlar. Canlı `/orders` ucu fatura,
        #: gönderi ve tutar dökümü taşımıyor; verildiğinde tarama bunları
        #: döndürür ve servis detaylandırmak zorunda kalır.
        self.shallow = shallow
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()
        #: `bbd/orders` satırları — checkout'ta seçilen kargo firması.
        self.bbd_order_rows: list[dict[str, Any]] = []
        #: `fail` içindeki ad kaç BAŞARILI çağrıdan sonra patlasın. 0 = hemen.
        #: Kısmi başarı ancak böyle sınanır: toplu işte kimi geçer, kimi patlar.
        self.fail_after = 0
        self.list_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.config_payload: dict[str, Any] = {}
        self.shipment_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.label_bytes = b"%PDF-1.4 sahte etiket"
        #: ÖDEME KANITI. Canlıda fatura listesi `state` + `orderIncrementId`,
        #: POS listesi `state` + `order_id` taşıyor; ikisi de AYRI uçtur ve
        #: sipariş gövdesinde yoktur. Varsayılan BOŞ: kanıtsız ekran bugünkü
        #: gibi "Bilinmiyor" demeli, testler kanıtı açıkça vermeli.
        self.invoice_payload: dict[str, Any] = {"items": [], "meta": {"total": 0}}
        self.attempt_payload: dict[str, Any] = {"items": [], "meta": {"total": 0}}
        #: İADE TALEPLERİ (RMA). Canlıda uç `order_id` süzgecini UYGULAMIYOR —
        #: ne verilirse verilsin bütün talepleri döndürüyor. Sahte de öyle
        #: davranır: süzgeci yerelde yapan kod yoksa test kırmızı olsun.
        self.return_payload: dict[str, Any] = {"items": [], "meta": {}}

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        if name in self.fail and len(self.args_of(name)) >= self.fail_after:
            raise RuntimeError(f"{name} patladı")
        self.calls.append((name, args, kwargs))

    def used(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for called, _, kwargs in self.calls if called == name]

    def args_of(self, name: str) -> list[tuple[Any, ...]]:
        return [args for called, args, _ in self.calls if called == name]

    # ------------------------------------------------------------- okuma

    async def bbd_orders(self, filters: Any = None, *, page: int = 1,
                         per_page: int | None = None,
                         all_pages: bool = False) -> dict[str, Any]:
        """`bbd/orders` — müşterinin seçtiği kargo firmasını taşır.

        Alanlar CANLIDAKİ gibi snake_case: `shipping_title`.
        """
        self._record("bbd_orders", filters, page=page, per_page=per_page)
        return {"items": self.bbd_order_rows, "meta": {"total": len(self.bbd_order_rows)}}

    async def orders(self, filters: Any = None, *, page: int = 1, per_page: int | None = None,
                     all_pages: bool = False) -> dict[str, Any]:
        self._record("orders", filters, page=page, per_page=per_page, all_pages=all_pages)
        if all_pages:
            items = self.shallow if self.shallow is not None else list(self.orders_by_id.values())
            return {"items": list(items), "meta": {"total": len(items)}, "truncated": False}
        return self.list_payload

    async def order(self, order_id: int) -> dict[str, Any]:
        self._record("order", order_id)
        if order_id not in self.orders_by_id:
            raise RuntimeError("Kayıt bulunamadı")
        return self.orders_by_id[order_id]

    async def order_comments(self, order_id: int, *, page: int = 1,
                             per_page: int | None = None) -> dict[str, Any]:
        self._record("order_comments", order_id, page=page, per_page=per_page)
        return {"items": [{"id": 1, "comment": "Müşteri aradı", "customer_notified": 0,
                           "created_at": "2026-08-10T10:00:00"}], "meta": {}}

    async def transactions(self, filters: Any = None, *, page: int = 1,
                           per_page: int | None = None,
                           all_pages: bool = False) -> dict[str, Any]:
        self._record("transactions", filters, page=page, per_page=per_page)
        return {"items": [{"id": 7, "type": "sale", "status": "approved", "amount": "120.00",
                           "created_at": "2026-08-10T10:05:00"}], "meta": {}}

    async def invoices(self, filters: Any = None, *, page: int = 1,
                       per_page: int | None = None,
                       all_pages: bool = False) -> dict[str, Any]:
        self._record("invoices", filters, page=page, per_page=per_page, all_pages=all_pages)
        return self.invoice_payload

    async def bbd_payment_attempts(self, filters: Any = None, *, page: int = 1,
                                   per_page: int | None = None) -> dict[str, Any]:
        self._record("bbd_payment_attempts", filters, page=page, per_page=per_page)
        return self.attempt_payload

    async def snapshot(self, *, refresh: bool = False) -> dict[str, Any]:
        self._record("snapshot", refresh=refresh)
        return {"parts": {"channels": [{"code": "default", "name": "Varsayılan"}],
                          "customer_groups": [{"id": 2, "name": "Genel"}]},
                "errors": [], "stale": False, "storedAt": ""}

    async def configuration(self, slug: str, *, channel: str = "",
                            locale: str = "") -> dict[str, Any]:
        self._record("configuration", slug, channel=channel, locale=locale)
        return self.config_payload

    async def bbd_carriers(self) -> dict[str, Any]:
        self._record("bbd_carriers")
        return {"items": [{"code": "aras", "name": "Aras Kargo"}], "meta": {}}

    async def bbd_return_requests(self, filters: Any = None, *, page: int = 1,
                                  per_page: int | None = None) -> dict[str, Any]:
        self._record("bbd_return_requests", filters, page=page, per_page=per_page)
        return self.return_payload

    async def bbd_shipments(self, filters: Any = None, *, page: int = 1,
                            per_page: int | None = None,
                            all_pages: bool = False) -> dict[str, Any]:
        self._record("bbd_shipments", filters, page=page, per_page=per_page)
        return self.shipment_payload

    async def bbd_shipment_label(self, shipment_id: int) -> bytes:
        self._record("bbd_shipment_label", shipment_id)
        return self.label_bytes

    # ------------------------------------------------------------- yazma

    async def cancel_order(self, order_id: int, *, reason: str, actor: str = "",
                           dry_run: bool | None = None) -> dict[str, Any]:
        self._record("cancel_order", order_id, reason=reason, actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run), "sent": not dry_run}

    async def create_invoice(self, order_id: int, *, items: dict[str, int] | None = None,
                             reason: str, actor: str = "",
                             dry_run: bool | None = None) -> dict[str, Any]:
        self._record("create_invoice", order_id, items=items, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run), "sent": not dry_run}

    async def create_shipment(self, order_id: int, *, payload: dict[str, Any], reason: str,
                              actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        self._record("create_shipment", order_id, payload=payload, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run), "sent": not dry_run}

    async def add_order_comment(self, order_id: int, *, comment: str, notify: bool = False,
                                reason: str, actor: str = "",
                                dry_run: bool | None = None) -> dict[str, Any]:
        self._record("add_order_comment", order_id, comment=comment, notify=notify,
                     reason=reason, actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run), "sent": not dry_run}


class FakeStageNotify:
    """`store.notify.stage` yeteneğinin testlik yüzü. GERÇEK SMS GÖNDERMEZ.

    Bildirimler modülü BURADAN IMPORT EDİLMEZ (K3): iki modülün arasındaki tek
    sözleşme aşama adı ile `stages.stage_order()` künyesidir ve bu sahte yüz de
    yalnız onu bilir.

    `calls` her isteği tutar; testler "SMS istendi mi, hangi künyeyle" sorusunu
    buradan cevaplar. Tekrar engeli bu tarafta DEĞİLDİR (o Bildirimler
    modülünün ve veritabanının işi); burada yalnız `done` ile önden eleme
    taklit edilir.
    """

    def __init__(self, *, enabled: tuple[str, ...] = (), available: bool = True) -> None:
        self.enabled = list(enabled)
        self.available = available
        self.calls: list[dict[str, Any]] = []
        self.done_ids: dict[str, list[int]] = {}
        self.fail = False
        self.result: dict[str, Any] = {"ok": True, "sent": True, "result": "sent",
                                       "note": "Gönderildi (1 parça)."}

    async def state(self) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError("bildirimler patladı")
        return {"ok": True, "available": self.available, "enabled": list(self.enabled)}

    async def notify(self, *, stage: str, order: dict[str, Any], actor: str = "",
                     dry_run: bool = True) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError("bildirimler patladı")
        self.calls.append({"stage": stage, "order": order, "actor": actor,
                           "dryRun": dry_run})
        return {"stage": stage, **self.result, "sent": self.result["sent"] and not dry_run}

    async def done(self, *, stage: str, order_ids: list[int]) -> dict[str, Any]:
        return {"ok": True, "ids": [item for item in self.done_ids.get(stage, [])
                                    if item in order_ids]}
