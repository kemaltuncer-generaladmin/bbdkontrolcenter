"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`FakeStore` SQL'i ayrıştırmaz; servisin yazdığı dört ifadeyi (denetim satırı,
arşiv künyesi, baskı anı güncellemesi, iki tablodan okuma) tanıyacak kadarını
yapar. Amaç çekirdek depoyu taklit etmek değil, servisin DOĞRU ANDA DOĞRU
SATIRI yazdığını görmek — özellikle `result="denendi"` izinin geçit
çağrısından ÖNCE düşmesini.

`FakeApi` `bld.api` yeteneğinin testlik yüzüdür. `.calls` her çağrıyı sırasıyla
tutar: "kuru provada bile açık bayrak geçildi" iddiası ancak bu listeye
bakılarak kanıtlanabilir. `.fail` kümesine bir metot adı atılırsa o metot
patlar ve K7 (geçit düşerse ekran ayakta kalır) sınanır.

FİKSTÜRLER SÖZLEŞMEDEN KOPYALANMIŞTIR (`BLD/docs/control/invoices.md`).
Modülün kendi uydurduğu bir gövdeye karşı geçen test hiçbir şey kanıtlamaz.
"""

from __future__ import annotations

import json
from typing import Any

#: `GET /` yanıtındaki liste satırı — sözleşmeden, kısaltılmadan.
INVOICE_ROW: dict[str, Any] = {
    "id": 44, "invoice_no": "BLD-2026-000044", "status": "issued",
    "customer_id": 312, "customer_label": "Acme Gıda A.Ş.",
    "order_id": 8421, "subscription_id": None,
    "period_start": None, "period_end": None,
    "issued_at": "2026-08-16T15:00:00Z", "total_kurus": 216000,
    "void_at": None, "html_url": "/api/control/invoices/44/html",
}

#: `GET /{id}` yanıtı: liste satırı + DONMUŞ içerik.
INVOICE_FULL: dict[str, Any] = {
    **INVOICE_ROW,
    "subscription_payment_id": None,
    "void_reason": None,
    "created_at": "2026-08-16T15:00:00Z",
    "snapshot_json": {
        "issuer": {
            "name": "BLD Catering",
            "address": "Kızılırmak Mah. 1443. Cad. No:12, Çankaya / Ankara",
            "phone": "3124445566",
            "email": "info@bld.example",
        },
        "customer": {
            "label": "Acme Gıda A.Ş.",
            "contact_person": "Mehmet Kaya",
            "tax_office": "Çankaya",
            "tax_no": "1234567890",
            "address": "Kızılırmak Mah. 1443. Cad. No:12, Kat 4, Çankaya / Ankara",
            "phone": "3124445566",
        },
        "lines": [
            {
                "description": "Günün Menüsü (16.08.2026)",
                "service_date": "2026-08-16",
                "order_number": "BLD-8421",
                "quantity": 12,
                "unit_price_kurus": 18000,
                "line_total_kurus": 216000,
            },
        ],
        "totals": {
            "subtotal_kurus": 216000, "delivery_fee_kurus": 0,
            "total_kurus": 216000, "currency": "TRY",
        },
        "payment": {"method": "online", "status": "paid",
                    "paid_at": "2026-08-16T12:00:00Z"},
        "notice": "Bu belge bilgilendirme amaçlıdır, mali değeri yoktur.",
    },
}

#: İptal edilmiş belge — filigran/uyarı yolunu sınamak için.
VOID_INVOICE: dict[str, Any] = {
    **INVOICE_FULL,
    "id": 45, "invoice_no": "BLD-2026-000045", "status": "void",
    "void_at": "2026-08-16T16:00:00Z",
    "void_reason": "Belgede yanlış kurum unvanı vardı, iptal edilip yenisi kesilecek",
}

#: Liste yanıtının `meta` bloğu. `issued_total_kurus` SÜZGEÇLENMİŞ kümenin
#: toplamıdır, sayfanın değil.
META: dict[str, Any] = {"page": 1, "per_page": 25, "total": 44, "last_page": 2,
                        "issued_total_kurus": 8912000}


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

    def __init__(self, module_id: str = "bld_invoices") -> None:
        self.module_id = module_id
        self.audit: list[dict[str, Any]] = []
        self.archive: list[dict[str, Any]] = []
        #: `True` ise her yazma patlar — "iz yazılamazsa iş durmasın" (K7).
        self.broken = False

    def table(self, name: str) -> str:
        return f"mod_{self.module_id}_{name}"

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        if self.broken:
            raise RuntimeError("depo kilitli")
        if "INSERT INTO mod_bld_invoices_audit" in sql:
            self.audit.append({
                "id": len(self.audit) + 1,
                "invoice_id": params[0], "action": params[1], "reason": params[2],
                "actor": params[3], "result": params[4],
                "detail": json.loads(params[5]), "created_at": params[6],
            })
            return
        if "INSERT INTO mod_bld_invoices_archive" in sql:
            self.archive.append({
                "id": len(self.archive) + 1,
                "invoice_id": params[0], "invoice_no": params[1], "kind": params[2],
                "path": params[3], "name": params[4], "sha256": params[5],
                "bytes": params[6], "actor": params[7], "created_at": params[8],
                "printed_at": "", "print_copies": 0,
            })
            return
        if "UPDATE mod_bld_invoices_archive" in sql:
            for row in self.archive:
                if row["path"] == params[2]:
                    row["printed_at"] = params[0]
                    row["print_copies"] += params[1]
            return
        raise AssertionError(f"beklenmeyen SQL: {sql}")

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if self.broken:
            raise RuntimeError("depo kilitli")
        rows = self.archive if "mod_bld_invoices_archive" in sql else self.audit
        if "WHERE invoice_id = ?" in sql:
            rows = [row for row in rows if row["invoice_id"] == params[0]]
            limit = params[1]
        else:
            limit = params[0]
        return [dict(row) for row in reversed(rows)][: int(limit)]

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = await self.fetch_all(sql, params)
        return rows[0] if rows else None


class FakeApiError(RuntimeError):
    """`BldApiError`in testlik ikizi: `code` alanı ekranın cümlesini seçer."""

    def __init__(self, message: str, *, code: str = "http") -> None:
        super().__init__(message)
        self.code = code


class FakeApi:
    """`bld.api` yeteneğinin testlik yüzü."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail: set[str] = set()
        self.fail_code = "transport"
        self.items: list[dict[str, Any]] = [INVOICE_ROW]
        self.meta: dict[str, Any] = dict(META)
        self.card: dict[str, Any] = dict(INVOICE_FULL)
        self.html: dict[str, Any] = {
            "content_type": "text/html", "status": 200,
            "filename": "BLD-2026-000044.html",
            "content": b"<!doctype html><title>BLD-2026-000044</title>",
            "text": "<!doctype html><title>BLD-2026-000044</title>",
            "bytes": 45, "total_rows": None, "truncated": False,
        }
        #: Sunucunun yazma yanıtı. Testler kip kip değiştirir.
        self.write_result: dict[str, Any] = {
            "ok": True, "dry_run": False, "audit_id": 2101,
            "data": {"id": 44, "invoice_no": "BLD-2026-000044", "status": "issued",
                     "total_kurus": 216000, "line_count": 1,
                     "issued_at": "2026-08-16T15:00:00Z",
                     "html_url": "/api/control/invoices/44/html"},
        }

    def _guard(self, name: str) -> None:
        if name in self.fail:
            raise FakeApiError(f"{name} patladı", code=self.fail_code)

    async def invoices(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("invoices", kwargs))
        self._guard("invoices")
        return {"items": list(self.items), "meta": dict(self.meta)}

    async def invoice(self, invoice_id: int) -> dict[str, Any]:
        self.calls.append(("invoice", {"invoice_id": invoice_id}))
        self._guard("invoice")
        return dict(self.card)

    async def invoice_html(self, invoice_id: int) -> dict[str, Any]:
        self.calls.append(("invoice_html", {"invoice_id": invoice_id}))
        self._guard("invoice_html")
        return dict(self.html)

    async def create_invoice(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("create_invoice", kwargs))
        self._guard("create_invoice")
        return dict(self.write_result)

    async def void_invoice(self, invoice_id: int, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("void_invoice", {"invoice_id": invoice_id, **kwargs}))
        self._guard("void_invoice")
        return dict(self.write_result)


class FakePrinter:
    """`printer` yeteneğinin testlik yüzü. Yokluğu da bir durumdur (K7)."""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, int]] = []
        self.broken = False

    async def print_file(self, path: Any, *, title: str = "", copies: int = 1) -> dict[str, Any]:
        if self.broken:
            raise RuntimeError("CUPS yanıt vermedi")
        self.jobs.append((str(path), copies))
        return {"printer": "BLD-Ofis", "job": len(self.jobs), "title": title}

    async def status(self) -> dict[str, Any]:
        if self.broken:
            raise RuntimeError("CUPS yanıt vermedi")
        return {"ready": True, "error": "", "target": {"name": "BLD-Ofis"}}
