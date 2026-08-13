"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`FakeStore` SQL'i ayrıştırmaz; servisin yazdığı dört ifadeyi (denetim satırı,
çalışma kaydı, çalışma güncellemesi, susturma satırı, tercih upsert'i)
tanıyacak kadarını yapar. Amaç çekirdek depoyu taklit etmek değil, servisin
doğru anda doğru satırı yazdığını görmek.
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

    def __init__(self, module_id: str = "store_ai") -> None:
        self.module_id = module_id
        self.audit: list[dict[str, Any]] = []
        self.runs: dict[str, dict[str, Any]] = {}
        self.ignored: list[dict[str, Any]] = []
        self.prefs: dict[str, str] = {}

    def table(self, name: str) -> str:
        return f"mod_{self.module_id}_{name}"

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        text = " ".join(sql.split())
        if "_audit" in text:
            keys = ("target_id", "action", "reason", "actor", "result", "detail", "created_at")
            self.audit.append(dict(zip(keys, params, strict=False)))
        elif "_run" in text and text.startswith("INSERT"):
            keys = ("token", "run_id", "tool", "params", "rows", "summary_text", "status",
                    "actor", "reason", "tokens", "cost", "targets", "duration_ms", "error",
                    "created_at")
            row = dict(zip(keys, params, strict=False))
            row["applied"] = 0
            row["applied_at"] = ""
            self.runs[str(row["token"])] = row
        elif "_run" in text and text.startswith("UPDATE"):
            rows, status, applied, actor, reason, applied_at, token = params
            row = self.runs.get(str(token))
            if row:
                row.update(rows=rows, status=status, actor=actor, reason=reason,
                           applied_at=applied_at,
                           applied=int(row.get("applied", 0)) + int(applied))
        elif "_ignored" in text:
            if "released_at" in text:
                key, reason, actor, created, released = params
                self.ignored.append({"finding_key": key, "kind": "", "target_id": 0,
                                     "reason": reason, "actor": actor, "created_at": created,
                                     "released_at": released})
            else:
                key, kind, target_id, reason, actor, created = params
                self.ignored.append({"finding_key": key, "kind": kind, "target_id": target_id,
                                     "reason": reason, "actor": actor, "created_at": created,
                                     "released_at": ""})
        elif "_prefs" in text:
            self.prefs[str(params[0])] = str(params[1])

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        text = " ".join(sql.split())
        if "_prefs" in text:
            value = self.prefs.get(str(params[0]))
            return {"value": value} if value is not None else None
        if "_run" in text:
            return self.runs.get(str(params[0]))
        return None

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        text = " ".join(sql.split())
        if "_run" in text:
            return list(reversed(list(self.runs.values())))
        if "_ignored" in text:
            return list(self.ignored)
        if "_audit" in text:
            return list(reversed(self.audit))
        return []


class MissingEndpoint(RuntimeError):
    """Geçidin `bbd_endpoint_missing` hatasının testlik ikizi."""

    code = "bbd_endpoint_missing"

    def __init__(self, message: str = "Uç henüz yayında değil.") -> None:
        super().__init__(message)


class FakeApi:
    """`store.api` yeteneğinin testlik yüzü. Yalnız kullanılan metotlar var."""

    def __init__(self, products: dict[int, dict[str, Any]] | None = None) -> None:
        self.products_by_id = products or {}
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()
        self.missing: set[str] = set()
        self.list_payload: dict[str, Any] = {"items": [], "meta": {}, "truncated": False}
        self.tree_payload: dict[str, Any] = {"items": []}
        self.tools_payload: dict[str, Any] = {"items": []}
        self.run_payload: dict[str, Any] = {}
        self.apply_payload: dict[str, Any] = {"ok": True}
        self.usage_payload: dict[str, Any] = {}
        self.runs_payload: dict[str, Any] = {"items": [], "meta": {}}

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        if name in self.missing:
            raise MissingEndpoint
        if name in self.fail:
            raise RuntimeError(f"{name} patladı")
        self.calls.append((name, args, kwargs))

    def used(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for called, _, kwargs in self.calls if called == name]

    async def products(self, filters: Any = None, *, page: int = 1, per_page: int | None = None,
                       all_pages: bool = False) -> dict[str, Any]:
        self._record("products", filters, page=page, per_page=per_page, all_pages=all_pages)
        return self.list_payload

    async def product(self, product_id: int) -> dict[str, Any]:
        self._record("product", product_id)
        if product_id not in self.products_by_id:
            raise RuntimeError("Kayıt bulunamadı")
        return self.products_by_id[product_id]

    async def category_tree(self) -> dict[str, Any]:
        self._record("category_tree")
        return self.tree_payload

    async def bbd_ai_tools(self) -> dict[str, Any]:
        self._record("bbd_ai_tools")
        return self.tools_payload

    async def bbd_ai_run(self, tool: str, *, payload: dict[str, Any], reason: str,
                         actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        self._record("bbd_ai_run", tool, payload=payload, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"sent": not dry_run, "dryRun": bool(dry_run), **self.run_payload}

    async def bbd_ai_apply(self, run_id: int, *, selections: list[Any], reason: str,
                           actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        self._record("bbd_ai_apply", run_id, selections=selections, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"sent": not dry_run, **self.apply_payload}

    async def bbd_ai_runs(self, filters: Any = None, *, page: int = 1,
                          per_page: int | None = None) -> dict[str, Any]:
        self._record("bbd_ai_runs", filters, page=page, per_page=per_page)
        return self.runs_payload

    async def bbd_ai_usage(self, filters: Any = None) -> dict[str, Any]:
        self._record("bbd_ai_usage", filters)
        return self.usage_payload


def live_product(product_id: int, **overrides: Any) -> dict[str, Any]:
    """CANLI liste ucunun döndürdüğü biçim — uydurma değil, kopya.

    `GET /api/admin/catalog/products?per_page=1&locale=tr&channel=default`
    yanıtından (bbdstore.com.tr, 2026-08-13) kısaltılarak alınmıştır. Alan
    adları ve değer tipleri OLDUĞU GİBİDİR:

      · camelCase (`urlKey`, `metaTitle`, `specialPrice`, `shortDescription`)
      · `images` ve `categories` **null**; sayı `imagesCount`, birincil
        kategori `categoryId`, kapak `baseImageUrl`
      · para ondalık metin (`"299.0000"`), zaman damgası saat dilimsiz

    Modülün kendi uydurduğu snake_case veriye karşı geçen test hiçbir şey
    kanıtlamıyordu; bu kayıt tam olarak onun için var.
    """
    base: dict[str, Any] = {
        "id": product_id,
        "sku": f"BBD2026SKU{product_id}",
        "name": f"Ürün {product_id}",
        "type": "simple",
        "status": 1,
        "price": "299.0000",
        "formattedPrice": "₺299,00",
        "specialPrice": None,
        "quantity": 12,
        "baseImageUrl": f"https://bbdstore.com.tr/storage/product/{product_id}/kapak.webp",
        "imagesCount": 1,
        "categoryId": 2,
        "categoryName": "TYT",
        "channel": "default",
        "locale": "tr",
        "attributeFamilyId": 2,
        "urlKey": f"urun-{product_id}",
        "visibleIndividually": True,
        "shortDescription": "<p>Kısa tanıtım.</p>",
        "description": "<p>" + ("Bu ürün hakkında yeterince uzun bir açıklama metni. " * 4)
                       + "</p>",
        "metaTitle": "Yeterince uzun bir meta başlık",
        "metaDescription": "Bu ürünün ne olduğunu anlatan, arama sonucunda okunabilir "
                           "uzunlukta bir meta açıklama metni buradadır.",
        "metaKeywords": "",
        "createdAt": "2026-07-20 21:52:57",
        "updatedAt": "2026-08-02 11:38:16",
        # Liste ucu bu alanları NULL veriyor; detay ucunda dolu geliyor.
        "images": None,
        "categories": None,
        "inventories": None,
        "variants": None,
    }
    base.update(overrides)
    return base


def product(product_id: int, **overrides: Any) -> dict[str, Any]:
    """Sağlıklı bir ürün kaydı; testler yalnız BOZDUKLARI alanı yazar."""
    base: dict[str, Any] = {
        "id": product_id,
        "sku": f"SKU-{product_id}",
        "name": f"Ürün {product_id}",
        "type": "simple",
        "status": 1,
        "price": "100.00",
        "url_key": f"urun-{product_id}",
        "meta_title": "Yeterince uzun bir meta başlık",
        "meta_description": "Bu ürünün ne olduğunu anlatan, arama sonucunda okunabilir "
                            "uzunlukta bir meta açıklama metni buradadır.",
        "description": "<p>" + ("Bu ürün hakkında yeterince uzun bir açıklama metni. " * 4)
                       + "</p>",
        "short_description": "",
        "images": [{"id": 1, "url": "https://example/a.jpg"}],
        "categories": [{"id": 7, "name": "Kitap"}],
        "updated_at": "2026-08-01T10:00:00",
    }
    base.update(overrides)
    return base
