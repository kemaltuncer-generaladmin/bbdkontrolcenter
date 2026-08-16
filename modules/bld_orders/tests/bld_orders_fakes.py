"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`FakeStore` SQL'i ayrıştırmaz; servisin dokunduğu dört ifadeyi (denetim satırı
yazma, denetim satırı okuma, tercih yazma, tercih okuma) tanıyacak kadarını
yapar. Amaç çekirdek depoyu taklit etmek değil, servisin DOĞRU ANDA DOĞRU
SATIRI yazdığını görmek — özellikle `result="denendi"` izinin geçit
çağrısından ÖNCE düşmesini.

`FakeApi` `bld.api` yeteneğinin testlik yüzüdür ve YALNIZ panel yolunun sekiz
metodunu taşır. `.calls` her çağrıyı sırasıyla tutar: "kuru provada uzağa
gerçek yazma gitmedi" iddiası ancak `dry_run` argümanına bakarak kanıtlanabilir
— sözleşmede kuru prova İSTEĞİ GERÇEKTEN GÖNDERİR (`00-genel.md` §3.1), yalnız
sunucu `$apply`'ı çağırmaz. `.fail` kümesine bir metot adı atılırsa o metot
patlar ve K7 (geçit düşerse ekran ayakta kalır) sınanır.

FIXTURE'LAR SÖZLEŞMEDEN KOPYALANDI (`BLD/docs/control/orders.md`). Modülün
kendi uydurduğu bir gövdeye karşı geçen test hiçbir şey kanıtlamaz.
"""

from __future__ import annotations

import json
from typing import Any

#: `GET /api/control/orders` yanıtındaki satır — sözleşmedeki örneğin aynısı.
ORDER_ROW: dict[str, Any] = {
    "id": 8421,
    "order_number": "BLD-8421",
    "status": "hazirlaniyor",
    "service_date": "2026-08-16",
    "requested_at": "2026-08-16T09:30:00Z",
    "delivery_type": "delivery",
    "customer_id": 312,
    "customer_name": "Acme Gıda — Mehmet Kaya",
    "customer_phone": "5321234567",
    "item_count": 12,
    "total_kurus": 216000,
    "payment_method": "online",
    "payment_status": "paid",
    "is_subscription": False,
    "subscription_id": None,
    "revision_no": 1,
    "has_invoice": False,
    "created_at": "2026-08-15T18:04:00Z",
    "updated_at": "2026-08-16T06:20:00Z",
}

#: `GET /api/control/orders/{order}` — `OrderPresenter::editable()` çıktısı.
#: `can_undo` / `undo_until` BİLEREK YOKTUR: sözleşme bu alanları saymıyor ve
#: servisin "bilinmiyor" demesi ancak böyle sınanabilir.
ORDER_DETAIL: dict[str, Any] = {
    "id": 8421,
    "order_number": "BLD-8421",
    "status": "hazirlaniyor",
    "service_date": "2026-08-16",
    "requested_at": "2026-08-16T09:30:00Z",
    "delivery_type": "delivery",
    "customer_name": "Mehmet Kaya",
    "customer_phone": "5321234567",
    "customer_note": "Kapıda zili çalmayın",
    "revision_no": 1,
    "editable": True,
    "not_editable_reason": None,
    "items": [
        {"menu_id": 88, "name": "Günün Menüsü (16.08.2026)", "quantity": 12,
         "option_value_ids": [], "note": None},
    ],
    "totals": {"subtotal_kurus": 216000, "delivery_fee_kurus": 0,
               "total_kurus": 216000, "currency": "TRY"},
    "payment": {"method": "online", "status": "paid"},
    "invoice": {"id": None, "invoice_no": None},
}

#: `GET /api/control/orders/{order}/revisions` satırı.
REVISION_ROW: dict[str, Any] = {
    "revision_no": 1,
    "reason": "Müşteri iki porsiyon azalttı",
    "note": "Kontrol Merkezi · Ayşe Yılmaz",
    "refund_kurus": 36000,
    "extra_charge_kurus": 0,
    "created_by_device_id": None,
    "created_at": "2026-08-16T06:20:00Z",
}

#: `GET /api/control/orders/{order}/invoice` gövdesi.
INVOICE: dict[str, Any] = {
    "id": 44,
    "invoice_no": "BLD-2026-000044",
    "order_id": 8421,
    "status": "issued",
    "issued_at": "2026-08-16T15:00:00Z",
    "total_kurus": 216000,
    "html_url": "/api/control/invoices/44/html",
}

#: `POST /api/control/orders/{order}/cancel` yanıtının `data` bloğu.
CANCEL_DATA: dict[str, Any] = {
    "refund_kurus": 216000,
    "refund_created": True,
    "sms_sent": True,
    "stock_released": {"day": 12, "items": [{"menu_id": 88, "quantity": 12}]},
}

SERVER_TIME = "2026-08-16T09:00:00Z"


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

    def __init__(self, module_id: str = "bld_orders") -> None:
        self.module_id = module_id
        self.audit: list[dict[str, Any]] = []
        self.prefs: dict[str, str] = {}
        #: `True` ise her yazma patlar — "iz yazılamazsa iş durmasın" (K7).
        self.broken = False

    def table(self, name: str) -> str:
        return f"mod_{self.module_id}_{name}"

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        if self.broken:
            raise RuntimeError("depo yazılamıyor")
        text = " ".join(sql.split())
        if "_audit" in text and text.startswith("INSERT"):
            keys = ("target_type", "target_id", "action", "reason", "actor", "result",
                    "detail", "created_at")
            row = dict(zip(keys, params, strict=False))
            row["id"] = len(self.audit) + 1
            self.audit.append(row)
        elif "_prefs" in text:
            self.prefs[str(params[0])] = str(params[1])

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        return None

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if "_prefs" in sql:
            return [{"key": key, "value": value} for key, value in self.prefs.items()]
        if "_audit" in sql:
            limit = int(params[0]) if params else len(self.audit)
            return list(reversed(self.audit))[:limit]
        return []

    # ------------------------------------------------------------- kolaylık

    def actions(self, action: str) -> list[dict[str, Any]]:
        return [row for row in self.audit if row["action"] == action]

    def results(self, action: str) -> list[str]:
        return [row["result"] for row in self.audit if row["action"] == action]

    def detail(self, index: int) -> dict[str, Any]:
        return json.loads(self.audit[index]["detail"])


class FakeBus:
    """Olay yolu. Kuru provada BOŞ kalmalı."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.fail = False

    async def __call__(self, event: str, payload: dict[str, Any] | None = None) -> None:
        if self.fail:
            raise RuntimeError("dinleyici patladı")
        self.events.append((event, payload or {}))

    def names(self) -> list[str]:
        return [name for name, _ in self.events]


class FakeApi:
    """`bld.api` yeteneğinin testlik yüzü. Yalnız `orders` alanının metotları.

    METOT ADLARI VE İMZALARI `modules/bld_api/backend/client.py` İLE BİREBİR
    AYNI OLMALIDIR. Uydurma bir ad (`orders()` gibi — o KDS yolunun metodudur)
    buradaki testleri yeşil tutar ama canlıda `AttributeError` verir; servis
    istisnayı K7 gereği yuttuğu için hata ekranda "BLD'ye ulaşılamadı" diye
    görünür ve yanlış metot adı DÜŞMÜŞ BİR SUNUCUDAN AYIRT EDİLEMEZ.
    """

    def __init__(self, *, rows: list[dict[str, Any]] | None = None,
                 detail: dict[str, Any] | None = None) -> None:
        self.rows = rows if rows is not None else [dict(ORDER_ROW)]
        self.detail_payload = dict(detail) if detail is not None else dict(ORDER_DETAIL)
        self.revision_rows: list[dict[str, Any]] = [dict(REVISION_ROW)]
        self.invoice_payload: dict[str, Any] = dict(INVOICE)
        self.cancel_payload: dict[str, Any] = {"ok": True, "audit_id": 1820,
                                               "data": dict(CANCEL_DATA), "warnings": []}
        self.document: dict[str, Any] = {
            "content_type": "text/csv", "status": 200,
            "filename": "bld-siparisler-2026-08-01_2026-08-16.csv",
            # BOM BİLEREK BURADA: servisin baytları olduğu gibi yazdığını
            # kanıtlamak, ancak BOM'lu bir gövdeyle mümkün.
            "content": b"\xef\xbb\xbfsiparis_no\r\nBLD-8421\r\n",
            "text": "siparis_no\r\nBLD-8421\r\n", "bytes": 30,
            "total_rows": 137, "truncated": False,
        }
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()
        #: Patlayan metodun hata kodu (`BldApiError.code` karşılığı).
        self.fail_code = "transport"

    # ------------------------------------------------------------- kayıt

    # `name` KONUM-ONLY (`/`): geçidin metotları `status=` ve `source=` gibi
    # anahtar argümanlar taşıyor ve normal bir parametre olsaydı çağrı "iki
    # değer" diye patlardı — üstelik servis istisnayı yutup `ok: False`
    # döndüğü için test, kuralın çalıştığını sanarak YEŞİL kalırdı.
    def _record(self, name: str, /, *args: Any, **kwargs: Any) -> None:
        if name in self.fail:
            raise _FakeError(f"{name} patladı", code=self.fail_code)
        self.calls.append((name, args, kwargs))

    def used(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for called, _, kwargs in self.calls if called == name]

    def args_of(self, name: str) -> list[tuple[Any, ...]]:
        return [args for called, args, _ in self.calls if called == name]

    def names(self) -> list[str]:
        return [name for name, _, _ in self.calls]

    # ------------------------------------------------------------- okuma

    async def order_list(self, *, service_date: str = "", date_from: str = "",
                         date_to: str = "", status: Any = None, delivery_type: str = "",
                         customer_id: int | None = None, subscription_id: int | None = None,
                         source: str = "", q: str = "", page: int = 1,
                         per_page: int | None = None) -> dict[str, Any]:
        self._record("order_list", service_date=service_date, date_from=date_from,
                     date_to=date_to, status=status, delivery_type=delivery_type,
                     customer_id=customer_id, subscription_id=subscription_id,
                     source=source, q=q, page=page, per_page=per_page)
        return {"items": [dict(row) for row in self.rows],
                "meta": {"page": page, "per_page": per_page or 25,
                         "total": len(self.rows), "last_page": 1},
                "server_time": SERVER_TIME}

    async def order_detail(self, order_id: int) -> dict[str, Any]:
        self._record("order_detail", order_id)
        return {**dict(self.detail_payload), "server_time": SERVER_TIME}

    async def order_revision_history(self, order_id: int) -> dict[str, Any]:
        self._record("order_revision_history", order_id)
        return {"items": [dict(row) for row in self.revision_rows], "meta": {}}

    async def order_invoice(self, order_id: int) -> dict[str, Any]:
        self._record("order_invoice", order_id)
        return dict(self.invoice_payload)

    async def export_orders(self, *, service_date: str = "", date_from: str = "",
                            date_to: str = "", status: Any = None, delivery_type: str = "",
                            customer_id: int | None = None,
                            subscription_id: int | None = None, source: str = "",
                            q: str = "", max_rows: int | None = None) -> dict[str, Any]:
        self._record("export_orders", service_date=service_date, date_from=date_from,
                     date_to=date_to, status=status, delivery_type=delivery_type,
                     customer_id=customer_id, subscription_id=subscription_id,
                     source=source, q=q, max_rows=max_rows)
        return dict(self.document)

    # ------------------------------------------------------------- yazma

    async def revise_order(self, order_id: int, *, items: list[dict[str, Any]], reason: str,
                           actor: str, note: str = "", requested_at: str = "",
                           customer_note: str = "",
                           dry_run: bool | None = None) -> dict[str, Any]:
        self._record("revise_order", order_id, items=items, reason=reason, actor=actor,
                     note=note, requested_at=requested_at, customer_note=customer_note,
                     dry_run=dry_run)
        if dry_run:
            return {"ok": True, "dry_run": True, "audit_id": 1801,
                    "would": {"action": "order.revise", "order_id": order_id,
                              "next_revision_no": 2, "items": items}}
        return {"ok": True, "dry_run": False, "audit_id": 1801,
                "order": {**dict(self.detail_payload), "revision_no": 2},
                "revision": {"revision_no": 2, "refund_kurus": 36000,
                             "extra_charge_kurus": 0,
                             "created_by_device_id": None,
                             "created_at": "2026-08-16T09:05:00Z"}}

    async def change_order_status(self, order_id: int, *, status: str, reason: str,
                                  actor: str,
                                  dry_run: bool | None = None) -> dict[str, Any]:
        self._record("change_order_status", order_id, status=status, reason=reason,
                     actor=actor, dry_run=dry_run)
        if dry_run:
            return {"ok": True, "dry_run": True, "audit_id": 1810,
                    "would": {"action": "order.status", "order_id": order_id,
                              "to": status}}
        return {"ok": True, "dry_run": False, "audit_id": 1810,
                "order": {**dict(self.detail_payload), "status": status}}

    async def cancel_order(self, order_id: int, *, refund: bool = True,
                           notify_customer: bool = True, reason: str, actor: str,
                           dry_run: bool | None = None) -> dict[str, Any]:
        self._record("cancel_order", order_id, refund=refund,
                     notify_customer=notify_customer, reason=reason, actor=actor,
                     dry_run=dry_run)
        if dry_run:
            return {"ok": True, "dry_run": True, "audit_id": 1820,
                    "would": {"action": "order.cancel", "order_id": order_id,
                              "from": "hazirlaniyor", "refund_kurus": 216000,
                              "would_refund": refund, "would_notify": notify_customer,
                              "stock_would_release": {"day": 12}}}
        return dict(self.cancel_payload)


class _FakeError(RuntimeError):
    """`BldApiError`in testlik ikizi.

    Gerçek sınıf import EDİLMEZ: servis de etmiyor (K2/K3 — başka bir modülün
    sınıfına bağlanmak, o modül yüklenmediğinde bu modülü de düşürürdü). Servis
    kodu `getattr(failure, "code", "")` ile okuyor; burada da aynı yüzey var.
    """

    def __init__(self, message: str, *, code: str = "http") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def make_service(*, api: FakeApi | None = None, store: FakeStore | None = None,
                 config: dict[str, Any] | None = None, bus: FakeBus | None = None,
                 export_dir: Any = None) -> Any:
    """Servisi sahte bağlamla kurar. Testlerin tek kurulum yolu."""
    from bld_orders_backend.service import OrdersService

    settings = {"dry_run_default": False, "page_size": 25}
    settings.update(config or {})
    if export_dir is not None:
        # Ayarda `export_path` doluysa rapor hiyerarşisi UYGULANMAZ ve dosya
        # doğrudan buraya yazılır; test masaüstüne dosya bırakmaz.
        settings["export_path"] = str(export_dir)
    return OrdersService(
        api=api or FakeApi(),
        store=store or FakeStore(),
        log=FakeLog(),
        config=settings,
        publish=bus,
        fallback_dir=export_dir,
    )
