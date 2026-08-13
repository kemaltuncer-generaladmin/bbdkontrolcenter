"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`FakeStore` SQL'i ayrıştırmaz; servisin yazdığı üç ifadeyi (denetim satırı,
toplu iş kaydı, tercih upsert'i) tanıyacak kadarını yapar. Amaç çekirdek
depoyu taklit etmek değil, servisin doğru anda doğru satırı yazdığını görmek.
"""

from __future__ import annotations

import re
from typing import Any


class FakeStoreError(RuntimeError):
    """Geçidin `StoreApiError`ının testlik ikizi.

    Modül geçidin sınıfını import EDEMEZ (K3) ve etmez; servis `code` alanını
    `getattr` ile okur. Bu yüzden taklit de sınıfı taklit etmez, YALNIZ alanı:
    testin kanıtladığı şey "409'un kodu okunuyor mu", "tip doğru mu" değil.
    """

    def __init__(self, message: str, *, status: int | None = None, code: str = "http") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


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

    def __init__(self, module_id: str = "store_products") -> None:
        self.module_id = module_id
        self.audit: list[dict[str, Any]] = []
        self.bulk: dict[str, dict[str, Any]] = {}
        self.prefs: dict[str, str] = {}

    def table(self, name: str) -> str:
        return f"mod_{self.module_id}_{name}"

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        text = " ".join(sql.split())
        if "_audit" in text and text.startswith("INSERT"):
            keys = ("product_id", "action", "reason", "actor", "result", "detail", "created_at")
            self.audit.append(dict(zip(keys, params, strict=False)))
        elif "_bulk" in text and text.startswith("INSERT"):
            token, kind, job_params, rows, created = params
            self.bulk[token] = {"token": token, "kind": kind, "params": job_params,
                                "rows": rows, "status": "preview", "created_at": created}
        elif "_bulk" in text and text.startswith("UPDATE"):
            status, actor, reason, applied, token = params
            row = self.bulk.get(token)
            if row:
                row.update(status=status, actor=actor, reason=reason, applied_at=applied)
        elif "_prefs" in text:
            key, value = params[0], params[1]
            self.prefs[key] = value

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        text = " ".join(sql.split())
        if "_prefs" in text:
            value = self.prefs.get(params[0])
            return {"value": value} if value is not None else None
        if "_bulk" in text:
            return self.bulk.get(params[0])
        return None

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if "_audit" in sql:
            rows = list(reversed(self.audit))
            if re.search(r"WHERE product_id", sql):
                rows = [row for row in rows if row["product_id"] == params[0]]
            return rows
        return []


class FakeApi:
    """`store.api` yeteneğinin testlik yüzü. Yalnız kullanılan metotlar var."""

    def __init__(self, products: dict[int, dict[str, Any]] | None = None) -> None:
        self.products_by_id = products or {}
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()
        #: {metot: geçit hata kodu} — `fail` ile birlikte kullanılır. Kod
        #: verilmezse düz `RuntimeError` atılır (kodsuz istisna hâli).
        self.fail_codes: dict[str, str] = {}
        self.list_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.issues_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.config_payload: dict[str, Any] = {}
        #: Nitelik/aile dünyası. Anahtarlar canlı uçtaki gibi camelCase.
        self.attributes_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.attributes_by_id: dict[int, dict[str, Any]] = {}
        self.families_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.families_by_id: dict[int, dict[str, Any]] = {}
        #: {aile kimliği: ürün sayısı} — `products` süzgeci bunu okur.
        self.family_totals: dict[int, int] = {}

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        if name in self.fail:
            code = self.fail_codes.get(name, "")
            if code:
                raise FakeStoreError(f"Mağaza bu işlemi çakışma yüzünden reddetti: {name}",
                                     status=409 if code == "conflict" else 500, code=code)
            raise RuntimeError(f"{name} patladı")
        self.calls.append((name, args, kwargs))

    def used(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for called, _, kwargs in self.calls if called == name]

    async def products(self, filters: Any = None, *, page: int = 1, per_page: int | None = None,
                       all_pages: bool = False) -> dict[str, Any]:
        self._record("products", filters, page=page, per_page=per_page, all_pages=all_pages)
        family = (filters or {}).get("attribute_family")
        if family is not None and int(family) in self.family_totals:
            # Aile sayımı YALNIZ meta.total okur; satır döndürmenin anlamı yok.
            return {"items": [], "meta": {"total": self.family_totals[int(family)]}}
        return self.list_payload

    async def product(self, product_id: int) -> dict[str, Any]:
        self._record("product", product_id)
        if product_id not in self.products_by_id:
            raise RuntimeError("Kayıt bulunamadı")
        return self.products_by_id[product_id]

    async def product_inventories(self, product_id: int) -> dict[str, Any]:
        self._record("product_inventories", product_id)
        return {"items": [{"inventory_source_id": 1, "qty": 11}]}

    async def customer_group_prices(self, product_id: int) -> dict[str, Any]:
        self._record("customer_group_prices", product_id)
        return {"items": []}

    async def inventory_sources(self) -> dict[str, Any]:
        self._record("inventory_sources")
        return {"items": [{"id": 1, "name": "Merkez"}]}

    async def update_product(self, product_id: int, *, payload: dict[str, Any], reason: str,
                             actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        self._record("update_product", product_id, payload=payload, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run), "sent": not dry_run}

    async def update_product_status(self, product_ids: list[int], *, active: bool, reason: str,
                                    actor: str = "",
                                    dry_run: bool | None = None) -> dict[str, Any]:
        self._record("update_product_status", product_ids, active=active, reason=reason,
                     actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def update_inventory(self, product_id: int, *, quantities: dict[str, int], reason: str,
                               actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        self._record("update_inventory", product_id, quantities=quantities, reason=reason,
                     actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def bbd_catalog_issues(self, filters: Any = None, *, page: int = 1,
                                 per_page: int | None = None) -> dict[str, Any]:
        self._record("bbd_catalog_issues", filters, page=page, per_page=per_page)
        return self.issues_payload

    async def bbd_catalog_health(self) -> dict[str, Any]:
        self._record("bbd_catalog_health")
        return {"total": 1419}

    async def category(self, category_id: int) -> dict[str, Any]:
        self._record("category", category_id)
        return {"id": category_id, "name": "Kitap"}

    async def category_tree(self) -> dict[str, Any]:
        self._record("category_tree")
        return {"items": [{"id": 1, "name": "Kitap"}]}

    async def snapshot(self, *, refresh: bool = False) -> dict[str, Any]:
        self._record("snapshot", refresh=refresh)
        return {"parts": {"channels": [{"code": "default", "name": "Varsayılan"}]},
                "errors": [], "stale": False, "storedAt": ""}

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

    # ------------------------------------------------------------- görsel

    async def upload_product_image(self, product_id: int, *, content: Any, filename: str,
                                   mime: str = "", position: int | None = None, reason: str,
                                   actor: str = "",
                                   dry_run: bool | None = None) -> dict[str, Any]:
        self._record("upload_product_image", product_id, filename=filename, mime=mime,
                     position=position, reason=reason, actor=actor, dry_run=dry_run,
                     # İçeriğin kendisi kaydedilmez; testin ilgilendiği uzunluğudur.
                     content_len=len(content) if isinstance(content, str) else len(content or b""))
        return {"ok": True, "data": {"id": 77}, "dryRun": bool(dry_run)}

    # ---------------------------------------------------- nitelik ve aile

    async def attributes(self, filters: Any = None, *, page: int = 1,
                         per_page: int | None = None,
                         all_pages: bool = True) -> dict[str, Any]:
        self._record("attributes", filters, page=page, per_page=per_page)
        return self.attributes_payload

    async def attribute(self, attribute_id: int) -> dict[str, Any]:
        self._record("attribute", attribute_id)
        if attribute_id not in self.attributes_by_id:
            raise RuntimeError("Kayıt bulunamadı")
        return self.attributes_by_id[attribute_id]

    async def create_product(self, *, payload: dict[str, Any], reason: str, actor: str = "",
                             dry_run: bool | None = None) -> dict[str, Any]:
        # Bu metot sahtede EKSİKTİ. Servis `self._api.create_product(...)` çağırıyor,
        # AttributeError düşüyor ve K7 gereği `{"ok": False}` olarak yutuluyordu:
        # yani ürün açma yolunun HİÇ test kapsamı yoktu ve kırık olduğu
        # görülmüyordu.
        self._record("create_product", payload=payload, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run),
                "data": {"id": 1500, "sku": payload.get("sku")}}

    async def create_attribute(self, *, payload: dict[str, Any], reason: str, actor: str = "",
                               dry_run: bool | None = None) -> dict[str, Any]:
        self._record("create_attribute", payload=payload, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def update_attribute(self, attribute_id: int, *, payload: dict[str, Any], reason: str,
                               actor: str = "",
                               dry_run: bool | None = None) -> dict[str, Any]:
        self._record("update_attribute", attribute_id, payload=payload, reason=reason,
                     actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def delete_attribute(self, attribute_id: int, *, reason: str, actor: str = "",
                               dry_run: bool | None = None) -> dict[str, Any]:
        self._record("delete_attribute", attribute_id, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def create_attribute_option(self, attribute_id: int, *, payload: dict[str, Any],
                                      reason: str, actor: str = "",
                                      dry_run: bool | None = None) -> dict[str, Any]:
        self._record("create_attribute_option", attribute_id, payload=payload, reason=reason,
                     actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def update_attribute_option(self, attribute_id: int, option_id: int, *,
                                      payload: dict[str, Any], reason: str, actor: str = "",
                                      dry_run: bool | None = None) -> dict[str, Any]:
        self._record("update_attribute_option", attribute_id, option_id, payload=payload,
                     reason=reason, actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def delete_attribute_option(self, attribute_id: int, option_id: int, *, reason: str,
                                      actor: str = "",
                                      dry_run: bool | None = None) -> dict[str, Any]:
        self._record("delete_attribute_option", attribute_id, option_id, reason=reason,
                     actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def families(self, filters: Any = None, *, page: int = 1, per_page: int | None = None,
                       all_pages: bool = True) -> dict[str, Any]:
        self._record("families", filters, page=page, per_page=per_page)
        return self.families_payload

    async def family(self, family_id: int) -> dict[str, Any]:
        self._record("family", family_id)
        if family_id not in self.families_by_id:
            raise RuntimeError("Kayıt bulunamadı")
        return self.families_by_id[family_id]

    async def create_family(self, *, payload: dict[str, Any], reason: str, actor: str = "",
                            dry_run: bool | None = None) -> dict[str, Any]:
        self._record("create_family", payload=payload, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def update_family(self, family_id: int, *, payload: dict[str, Any], reason: str,
                            actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        self._record("update_family", family_id, payload=payload, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}
