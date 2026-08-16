"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`FakeStore` SQL'i ayrıştırmaz; servisin yazdığı iki ifadeyi (künye upsert'i ve
denetim satırı) tanıyacak kadarını yapar. Amaç çekirdek depoyu taklit etmek
değil, servisin doğru anda doğru satırı yazdığını görmek.
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


PLAN_COLUMNS = ("product_id", "name", "sku", "pricing_mode", "discount_percent", "set_price",
                "tax_rate", "valid_from", "valid_to", "note", "components", "actor", "updated_at")

AUDIT_COLUMNS = ("bundle_id", "action", "reason", "actor", "result", "detail", "created_at")


class FakeStore:
    """`ModuleStore` yüzeyi. Satırları bellekte tutar."""

    def __init__(self, module_id: str = "store_bundles") -> None:
        self.module_id = module_id
        self.plans: dict[int, dict[str, Any]] = {}
        self.audit: list[dict[str, Any]] = []
        self.broken = False

    def table(self, name: str) -> str:
        return f"mod_{self.module_id}_{name}"

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        if self.broken:
            raise RuntimeError("depo yazılamıyor")
        text = " ".join(sql.split())
        if "_plan" in text and text.startswith("INSERT"):
            row = dict(zip(PLAN_COLUMNS, params, strict=False))
            self.plans[int(row["product_id"])] = row
        elif "_audit" in text and text.startswith("INSERT"):
            self.audit.append(dict(zip(AUDIT_COLUMNS, params, strict=False)))

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        return None

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if "_plan" in sql:
            return [self.plans[key] for key in sorted(self.plans)]
        if "_audit" in sql:
            rows = list(reversed(self.audit))
            if "WHERE bundle_id" in sql:
                rows = [row for row in rows if row["bundle_id"] == params[0]]
            return rows
        return []


def product(product_id: int, *, name: str, sku: str, price: str = "100.00",
            cost: str | None = "60.00", stock: int = 10, status: int = 1,
            special: str | None = None, special_from: str = "", special_to: str = "",
            **extra: Any) -> dict[str, Any]:
    """`GET /api/admin/catalog/products/{id}` yanıtı — CANLIDAKİ BİÇİMDE.

    Canlıdan (13.08.2026, ürün 17) doğrulanan iki tuzak buraya kasten konmuştur:

      1. Gövde **camelCase** konuşur: `specialPrice`, `baseImageUrl`, `urlKey`.
      2. **`cost` gövdede HİÇ YOKTUR.** Maliyet yalnız `attributes` dizisinde
         `{"code": "cost", "value": "385.0000"}` satırı olarak gelir. Aynı
         dizide `special_price` de snake_case'tir.

    Bu sahteyi "düz sözlükte cost" diye yazmak, ekranın kâr sütununun canlıda
    baştan sona “—” çıkmasını test yeşilken gizler.
    """
    attributes: list[dict[str, Any]] = [
        {"id": 1, "code": "sku", "value": sku},
        {"id": 2, "code": "name", "value": name},
        {"id": 3, "code": "status", "value": status},
        {"id": 11, "code": "price", "value": f"{float(price):.4f}"},
        {"id": 12, "code": "cost", "value": None if cost is None else f"{float(cost):.4f}"},
        {"id": 13, "code": "special_price",
         "value": None if special is None else f"{float(special):.4f}"},
        {"id": 14, "code": "special_price_from", "value": special_from or None},
        {"id": 15, "code": "special_price_to", "value": special_to or None},
    ]
    row: dict[str, Any] = {
        "id": product_id, "sku": sku, "name": name, "type": "simple", "status": status,
        "price": price, "quantity": stock,
        "specialPrice": None, "specialPriceFrom": None, "specialPriceTo": None,
        "baseImageUrl": None, "crossSells": [],
        "inventories": [{"sourceId": 1, "sourceCode": "default", "qty": stock}],
        "attributes": attributes,
    }
    row.update(extra)
    return row


def light(product_id: int, *, name: str, sku: str, price: float = 100.0,
          status: int = 1) -> dict[str, Any]:
    """`GET /api/admin/products` (seçici) satırı — canlıdaki hafif biçim.

    Bu uç ürünü **maliyetsiz** ve fiyatı **sayı** olarak verir; `price` zaten
    indirim uygulanmış tutardır.
    """
    return {"id": product_id, "sku": sku, "type": "simple", "name": name, "status": status,
            "price": price, "formattedPrice": f"₺{price:,.2f}",
            "baseImageUrl": "https://bbdstore.com.tr/x.webp", "isSaleable": True}


class FakeApi:
    """`store.api` yeteneğinin testlik yüzü. Yalnız kullanılan metotlar var."""

    def __init__(self, products: dict[int, dict[str, Any]] | None = None) -> None:
        self.products_by_id = products or {}
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        #: Adı buraya konan metot çağrıldığında patlar — K7 sınamaları için.
        self.fail: set[str] = set()
        self.bbd_bundle_items: list[dict[str, Any]] = []
        self.carousel_items: list[dict[str, Any]] = []
        #: "Setler" kategorisinin (canlıda 42) ÜYE ürünleri. Mağazada set
        #: kavramının tamamı budur; künye mağazada saklanmaz.
        self.set_members: list[int] = []
        #: `/api/admin/products` (seçici) uçundaki bütün satırlar.
        self.lookup_items: list[dict[str, Any]] = []
        #: Ürün → kategori. Sahte süzgeç bunu kullanır; canlı yanıtta bu alan
        #: yoktur, süzme sunucuda olur.
        self.category_of: dict[int, int] = {}

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        if name in self.fail:
            raise RuntimeError(f"{name} patladı")
        self.calls.append((name, args, kwargs))

    def used(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for called, _, kwargs in self.calls if called == name]

    def count(self, name: str) -> int:
        return len([1 for called, _, _ in self.calls if called == name])

    async def bbd_bundles(self, filters: Any = None) -> dict[str, Any]:
        self._record("bbd_bundles", filters)
        return {"items": self.bbd_bundle_items, "meta": {}}

    async def bbd_set_membership(self, product_ids: list[int], *, action: str, reason: str,
                                 actor: str = "",
                                 dry_run: bool | None = None) -> dict[str, Any]:
        """`POST storefront/sets/membership` — mağazadaki TEK set yazma ucu.

        Yanıt gerçek uçtaki gibi DEĞİŞEN ürünleri sayar (`affected`); zaten
        üye olan ürün ikinci kez eklenmez.
        """
        self._record("bbd_set_membership", product_ids, action=action, reason=reason,
                     actor=actor, dry_run=dry_run)
        ids = [int(item) for item in product_ids]
        if action == "add":
            etkilenen = [item for item in ids if item not in self.set_members]
            if not dry_run:
                self.set_members.extend(etkilenen)
        else:
            etkilenen = [item for item in ids if item in self.set_members]
            if not dry_run:
                self.set_members = [item for item in self.set_members if item not in etkilenen]
        return {"ok": True, "dryRun": bool(dry_run), "action": action,
                "categoryId": 42, "affected": etkilenen}

    async def bbd_carousel(self, filters: Any = None) -> dict[str, Any]:
        self._record("bbd_carousel", filters)
        return {"items": self.carousel_items, "meta": {}}

    async def products(self, filters: Any = None, *, page: int = 1, per_page: int | None = None,
                       all_pages: bool = False) -> dict[str, Any]:
        """`/api/admin/catalog/products` — KATEGORİ SÜZGECİ TANIMAZ.

        Canlıda `category_id=42` gönderildiğinde `meta.total` 1.421 kalıyor:
        Laravel tanımadığı sorgu parametresini sessizce yok sayıyor. Sahte de
        yok sayar; süzgecin uygulandığını varsayan kod burada yakalanır.
        """
        self._record("products", filters, page=page, per_page=per_page, all_pages=all_pages)
        size = max(1, min(50, int(per_page or 50)))
        rows = list(self.lookup_items)
        return {"items": rows[:size], "meta": {"currentPage": 1, "perPage": size,
                                               "lastPage": 1, "total": len(rows)}}

    async def product(self, product_id: int) -> dict[str, Any]:
        self._record("product", product_id)
        if product_id not in self.products_by_id:
            raise RuntimeError("Kayıt bulunamadı")
        return self.products_by_id[product_id]

    async def product_lookup(self, filters: Any = None, *,
                             per_page: int | None = None) -> dict[str, Any]:
        """`/api/admin/products` — YALNIZ `category_id` ve `query` süzgeci var.

        Canlıda doğrulandı: `category_id=42` → 1 kayıt, `query=Geometri` → 67,
        ama `name=Geometri` → 1.421 (sessizce yok sayılır). Sahte de aynı
        ayrımı yapar; `name` gönderen kod testte yakalanır.
        """
        self._record("product_lookup", filters, per_page=per_page)
        rows = list(self.lookup_items)
        given = filters or {}
        category = given.get("category_id")
        if category is not None:
            rows = [row for row in rows
                    if self.category_of.get(int(row["id"])) == int(category)]
        needle = str(given.get("query") or "").casefold()
        if needle:
            rows = [row for row in rows
                    if needle in str(row.get("name", "")).casefold()
                    or needle in str(row.get("sku", "")).casefold()]
        size = max(1, min(50, int(per_page or 50)))
        return {"items": rows[:size], "meta": {"currentPage": 1, "perPage": size,
                                               "lastPage": 1, "total": len(rows)}}

    async def update_product_status(self, product_ids: list[int], *, active: bool, reason: str,
                                    actor: str = "",
                                    dry_run: bool | None = None) -> dict[str, Any]:
        self._record("update_product_status", product_ids, active=active, reason=reason,
                     actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

