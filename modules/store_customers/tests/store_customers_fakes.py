"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`FakeStore` SQL'i ayrıştırmaz; servisin yazdığı dört ifadeyi (denetim satırı,
izin geçmişi, yorum etiketi upsert'i ve okumalar) tanıyacak kadarını yapar.
Amaç çekirdek depoyu taklit etmek değil, servisin doğru anda doğru satırı
yazdığını görmek.
"""

from __future__ import annotations

import asyncio
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

    def __init__(self, module_id: str = "store_customers") -> None:
        self.module_id = module_id
        self.audit: list[dict[str, Any]] = []
        self.consent: list[dict[str, Any]] = []
        self.flags: dict[int, dict[str, Any]] = {}

    def table(self, name: str) -> str:
        return f"mod_{self.module_id}_{name}"

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        text = " ".join(sql.split())
        if "_audit" in text and text.startswith("INSERT"):
            keys = ("customer_id", "review_id", "action", "reason", "actor", "result",
                    "detail", "created_at")
            self.audit.append(dict(zip(keys, params, strict=False)))
        elif "_consent" in text and text.startswith("INSERT"):
            keys = ("customer_id", "kind", "before_value", "after_value", "actor", "reason",
                    "created_at")
            self.consent.append(dict(zip(keys, params, strict=False)))
        elif "_review_flags" in text:
            review_id, spam, reply, actor, reason, updated = params
            self.flags[int(review_id)] = {"review_id": int(review_id), "spam": spam,
                                          "reply": reply, "actor": actor, "reason": reason,
                                          "updated_at": updated}

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        return None

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        text = " ".join(sql.split())
        if "_review_flags" in text:
            wanted = {int(item) for item in params}
            return [row for key, row in self.flags.items() if key in wanted]
        if "_consent" in text:
            return [row for row in reversed(self.consent)
                    if row["customer_id"] == params[0]]
        if "_audit" in text:
            rows = list(reversed(self.audit))
            if "WHERE customer_id" in text:
                rows = [row for row in rows if row["customer_id"] == params[0]]
            return rows
        return []


class FakeApi:
    """`store.api` yeteneğinin testlik yüzü. Yalnız kullanılan metotlar var."""

    def __init__(self, customers: dict[int, dict[str, Any]] | None = None) -> None:
        self.customers_by_id = customers or {}
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()
        self.list_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.orders_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.reviews_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.gdpr_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.returns_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.config_payload: dict[str, Any] = {}

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        if name in self.fail:
            raise RuntimeError(f"{name} patladı")
        self.calls.append((name, args, kwargs))

    def used(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for called, _, kwargs in self.calls if called == name]

    def args_of(self, name: str) -> list[tuple[Any, ...]]:
        return [args for called, args, _ in self.calls if called == name]

    # ------------------------------------------------------------- müşteri

    async def customers(self, filters: Any = None, *, page: int = 1,
                        per_page: int | None = None,
                        all_pages: bool = False) -> dict[str, Any]:
        self._record("customers", filters, page=page, per_page=per_page, all_pages=all_pages)
        # GERÇEK BİR BEKLEME NOKTASI: ağ çağrısı denetimi bırakır. Bırakmasaydı
        # eşzamanlılık testleri (iki istek tek tarama yapmalı) yalancı biçimde
        # geçerdi — sahte metot hiç askıya alınmadığı için yarış hiç oluşmaz.
        await asyncio.sleep(0)
        return self.list_payload

    async def customer(self, customer_id: int) -> dict[str, Any]:
        self._record("customer", customer_id)
        if customer_id not in self.customers_by_id:
            raise RuntimeError("Kayıt bulunamadı")
        return self.customers_by_id[customer_id]

    async def update_customer(self, customer_id: int, *, payload: dict[str, Any], reason: str,
                              actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        self._record("update_customer", customer_id, payload=payload, reason=reason,
                     actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def update_customer_status(self, customer_ids: list[int], *, active: bool, reason: str,
                                     actor: str = "",
                                     dry_run: bool | None = None) -> dict[str, Any]:
        self._record("update_customer_status", customer_ids, active=active, reason=reason,
                     actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def customer_addresses(self, customer_id: int) -> dict[str, Any]:
        # CANLI BİÇİM: yanıt camelCase gelir (`firstName`, `defaultAddress`).
        self._record("customer_addresses", customer_id)
        return {"items": [{"id": 1, "firstName": "Ayşe", "address": "Atatürk Cd. 5",
                           "city": "İzmir", "state": "İzmir", "defaultAddress": True,
                           "vatId": None}]}

    async def customer_notes(self, customer_id: int) -> dict[str, Any]:
        self._record("customer_notes", customer_id)
        return {"items": [{"id": 3, "note": "Telefonla arandı.", "createdAt": "2026-08-01"}]}

    async def add_customer_note(self, customer_id: int, *, note: str, reason: str,
                                actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        self._record("add_customer_note", customer_id, note=note, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def customer_groups(self) -> dict[str, Any]:
        # CANLI BİÇİM: grubun KODU da gelir; varsayılan grup ayarı kodu kullanır.
        self._record("customer_groups")
        return {"items": [{"id": 1, "code": "general", "name": "Genel"},
                          {"id": 2, "code": "wholesale", "name": "Öğretmen"}]}

    async def orders(self, filters: Any = None, *, page: int = 1, per_page: int | None = None,
                     all_pages: bool = False) -> dict[str, Any]:
        self._record("orders", filters, page=page, per_page=per_page, all_pages=all_pages)
        await asyncio.sleep(0)
        return self.orders_payload

    async def snapshot(self, *, refresh: bool = False) -> dict[str, Any]:
        self._record("snapshot", refresh=refresh)
        return {"parts": {"channels": [{"code": "default", "name": "Varsayılan"}]},
                "errors": [], "stale": False}

    # ---------------------------------------------------------------- KVKK

    async def gdpr_requests(self, filters: Any = None, *, page: int = 1,
                            per_page: int | None = None) -> dict[str, Any]:
        self._record("gdpr_requests", filters, page=page, per_page=per_page)
        return self.gdpr_payload

    async def process_gdpr_request(self, request_id: int, *, payload: dict[str, Any],
                                   reason: str, actor: str = "",
                                   dry_run: bool | None = None) -> dict[str, Any]:
        self._record("process_gdpr_request", request_id, payload=payload, reason=reason,
                     actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def gdpr_download_data(self, customer_id: int, *, reason: str, actor: str = "",
                                 dry_run: bool | None = None) -> dict[str, Any]:
        self._record("gdpr_download_data", customer_id, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run), "data": {"url": "https://ornek/paket.zip"}}

    # --------------------------------------------------------------- yorum

    async def reviews(self, filters: Any = None, *, page: int = 1, per_page: int | None = None,
                      all_pages: bool = False) -> dict[str, Any]:
        self._record("reviews", filters, page=page, per_page=per_page, all_pages=all_pages)
        return self.reviews_payload

    async def update_review(self, review_id: int, *, payload: dict[str, Any], reason: str,
                            actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        self._record("update_review", review_id, payload=payload, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def update_review_status(self, review_ids: list[int], *, status: str, reason: str,
                                   actor: str = "",
                                   dry_run: bool | None = None) -> dict[str, Any]:
        self._record("update_review_status", review_ids, status=status, reason=reason,
                     actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    # --------------------------------------------------------------- talep

    async def bbd_return_requests(self, filters: Any = None, *, page: int = 1,
                                  per_page: int | None = None) -> dict[str, Any]:
        self._record("bbd_return_requests", filters, page=page, per_page=per_page)
        return self.returns_payload

    # --------------------------------------------------------------- ayar

    async def configuration(self, slug: str, *, channel: str = "",
                            locale: str = "") -> dict[str, Any]:
        self._record("configuration", slug, channel=channel, locale=locale)
        return self.config_payload

    async def update_configuration(self, slug: str, *, values: dict[str, Any], reason: str,
                                   actor: str = "", channel: str = "", locale: str = "",
                                   dry_run: bool | None = None) -> dict[str, Any]:
        self._record("update_configuration", slug, values=values, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True}


class FakeScan:
    """`store.scan` yeteneğinin testlik yüzü — SIRAYLA koşar, arka planda değil.

    Gerçek `BackgroundScan` yükleyiciyi ayrı bir görevde çalıştırır; test
    ortamında bu, "tarama bitti mi" sorusunu zamanlamaya bağlar ve testi
    kırılgan yapar. Burada yükleyici çağrıldığı anda beklenir: sonuç
    deterministiktir ve testler `state` alanının doğru dolduğunu görebilir.

    Beklemenin gerçek kodda YAPILMADIĞINI ayrıca sınayan test var
    (`test_liste_siparis_taramasini_beklemez`): orada yükleyici hiç bitmez.
    """

    def __init__(self) -> None:
        self.entries: dict[str, dict[str, Any]] = {}
        self.loads: list[str] = []
        #: Uçuştaki yükleme. Gerçek katmanda "anahtar başına tek tarama"
        #: garantisini `entry.running` veriyor; burada da aynı garanti olmalı,
        #: yoksa eş zamanlı iki istek mağazayı iki kez tarar.
        self._inflight: dict[str, asyncio.Task[Any]] = {}

    def scoped(self, namespace: str) -> FakeScan:
        return self

    def _entry(self, key: str) -> dict[str, Any]:
        return self.entries.setdefault(
            key, {"state": "empty", "value": None, "stale": True, "running": False,
                  "at": "", "ageSeconds": 0, "error": ""})

    def peek(self, key: str) -> dict[str, Any]:
        return dict(self._entry(key))

    def invalidate(self, key: str) -> None:
        self._entry(key)["stale"] = True

    async def read(self, key: str, loader: Any, *, ttl: int | None = None,
                   refresh: bool = False, wait: float = 0.0) -> dict[str, Any]:
        entry = self._entry(key)
        if entry["state"] == "ready" and not entry["stale"] and not refresh:
            return dict(entry)

        task = self._inflight.get(key)
        if task is None or task.done():
            self.loads.append(key)
            task = asyncio.ensure_future(loader())
            self._inflight[key] = task
        try:
            value = await asyncio.shield(task)
        except Exception as failure:  # noqa: BLE001 — gerçek katman da yutar
            entry.update(state="error", error=str(failure) or "Tarama yapılamadı.",
                         running=False)
            return dict(entry)
        entry.update(state="ready", value=value, stale=False, running=False,
                     at="2026-08-18T00:00:00+03:00", error="")
        return dict(entry)
