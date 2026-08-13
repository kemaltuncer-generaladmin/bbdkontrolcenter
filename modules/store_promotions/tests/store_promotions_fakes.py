"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`FakeStore` SQL'i ayrıştırmaz; servisin yazdığı iki ifadeyi (denetim satırı ve
kupon partisi) tanıyacak kadarını yapar. Amaç çekirdek depoyu taklit etmek
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


class FakeStore:
    """`ModuleStore` yüzeyi. Satırları bellekte tutar."""

    def __init__(self, module_id: str = "store_promotions") -> None:
        self.module_id = module_id
        self.audit: list[dict[str, Any]] = []
        self.batches: dict[str, dict[str, Any]] = {}

    def table(self, name: str) -> str:
        return f"mod_{self.module_id}_{name}"

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        text = " ".join(sql.split())
        if "_audit" in text and text.startswith("INSERT"):
            keys = ("rule_id", "scope", "action", "reason", "actor", "result", "detail",
                    "created_at")
            self.audit.append(dict(zip(keys, params, strict=False)))
        elif "_batches" in text and text.startswith("INSERT"):
            keys = ("token", "rule_id", "rule_name", "prefix", "count", "length", "expires_at",
                    "codes", "path", "actor", "reason", "status", "created_at")
            row = dict(zip(keys, params, strict=False))
            self.batches[row["token"]] = row

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        if "_batches" in sql:
            return self.batches.get(params[0])
        return None

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if "_audit" in sql:
            rows = list(reversed(self.audit))
            if "WHERE rule_id" in sql:
                rows = [row for row in rows if row["rule_id"] == params[0]]
            return rows
        if "_batches" in sql:
            rows = list(reversed(self.batches.values()))
            if "WHERE rule_id" in sql:
                rows = [row for row in rows if row["rule_id"] == params[0]]
            return rows
        return []


#: CANLI mağazanın döndürdüğü biçim: `/api/admin/marketing/cart-rules/{id}`
#: alanları **camelCase** yayınlıyor (bbdstore.com.tr · Bagisto 2.4.8 +
#: bagisto-api 2.3.1). Sahte veriyi snake_case yazmak, ekranın canlıda hiçbir
#: alanı okuyamadığını testten SAKLIYORDU; bu sözlük artık canlı yanıtın
#: birebir kopyasıdır.
KURAL = {
    "id": 7,
    "name": "Eylül kampanyası",
    "description": "Okul dönüşü",
    "status": 1,
    "startsFrom": "2026-08-01T00:00:00+03:00",
    "endsTill": "2026-09-30T23:59:59+03:00",
    "couponType": 1,
    "useAutoGeneration": 1,
    "couponCode": "EYLUL",
    "usesPerCoupon": 100,
    "usagePerCustomer": 1,
    "timesUsed": 12,
    "conditionType": 1,
    "conditions": [
        {"attribute": "cart|base_sub_total", "attribute_type": "price",
         "operator": ">=", "value": "500.0000"},
    ],
    "endOtherRules": 0,
    "discountAmount": 10,
    "actionType": "by_percent",
    "discountQuantity": 0,
    "discountStep": "0",
    "applyToShipping": 0,
    "freeShipping": 0,
    "usesAttributeConditions": 0,
    "sortOrder": 1,
    "channels": [{"id": 1, "code": "default", "name": "Benim Başarı Dünyam"}],
    "customerGroups": [{"id": 2, "code": "general", "name": "Genel"}],
}

#: Kural LİSTESİ ucu kapsamı ve koşulları VERMEZ — canlıda `null` gelir.
#: Simülasyonun ve süzgecin bu ayrımı görmesi gerekiyor.
KURAL_LISTE_SATIRI = {
    **{key: value for key, value in KURAL.items()
       if key not in ("conditions", "channels", "customerGroups")},
    "conditions": None,
    "channels": None,
    "customerGroups": None,
}


class FakeApi:
    """`store.api` yeteneğinin testlik yüzü. Yalnız kullanılan metotlar var."""

    def __init__(self, rules: dict[int, dict[str, Any]] | None = None) -> None:
        self.rules_by_id = rules if rules is not None else {7: dict(KURAL)}
        self.catalog_by_id: dict[int, dict[str, Any]] = {}
        # Kupon ucu da camelCase: `usageLimit` · `timesUsed` · `expiredAt`.
        self.coupon_pages: list[dict[str, Any]] = [
            {"items": [{"id": 1, "cartRuleId": 7, "code": "EYLUL-A1", "timesUsed": 0,
                        "usageLimit": 1, "usagePerCustomer": 1, "expiredAt": None}],
             "meta": {}},
        ]
        self.orders_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.order_details: dict[int, dict[str, Any]] = {}
        self._coupon_reads = 0
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()
        self.config_payload: dict[str, Any] = {}

    # ------------------------------------------------------------ yardımcı

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        if name in self.fail:
            raise RuntimeError(f"{name} patladı")
        self.calls.append((name, args, kwargs))

    def used(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for called, _, kwargs in self.calls if called == name]

    def sent(self, name: str) -> list[tuple[Any, ...]]:
        return [args for called, args, _ in self.calls if called == name]

    # ------------------------------------------------------------- kurallar

    async def cart_rules(self, filters: Any = None, *, page: int = 1,
                         per_page: int | None = None,
                         all_pages: bool = False) -> dict[str, Any]:
        """Canlı gibi davranır: LİSTE koşulu ve kapsamı `null` verir."""
        self._record("cart_rules", filters, page=page, per_page=per_page, all_pages=all_pages)
        return {"items": [self._as_list_row(item) for item in self.rules_by_id.values()],
                "meta": {}}

    @staticmethod
    def _as_list_row(raw: dict[str, Any]) -> dict[str, Any]:
        row = dict(raw)
        for key in ("conditions", "channels", "customerGroups", "customer_groups"):
            if key in row:
                row[key] = None
        return row

    async def cart_rule(self, rule_id: int) -> dict[str, Any]:
        self._record("cart_rule", rule_id)
        if rule_id not in self.rules_by_id:
            raise RuntimeError("Kayıt bulunamadı")
        return dict(self.rules_by_id[rule_id])

    async def save_cart_rule(self, *, payload: dict[str, Any], rule_id: int | None = None,
                             reason: str, actor: str = "",
                             dry_run: bool | None = None) -> dict[str, Any]:
        self._record("save_cart_rule", rule_id, payload=payload, reason=reason, actor=actor,
                     dry_run=dry_run)
        if rule_id and not dry_run:
            self.rules_by_id[rule_id] = {**self.rules_by_id.get(rule_id, {}), **payload,
                                         "id": rule_id}
        return {"ok": True, "dryRun": bool(dry_run), "sent": not dry_run, "id": rule_id or 99}

    async def copy_cart_rule(self, rule_id: int, *, reason: str, actor: str = "",
                             dry_run: bool | None = None) -> dict[str, Any]:
        self._record("copy_cart_rule", rule_id, reason=reason, actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def catalog_rules(self, filters: Any = None, *, page: int = 1,
                            per_page: int | None = None) -> dict[str, Any]:
        self._record("catalog_rules", filters, page=page, per_page=per_page)
        return {"items": [dict(item) for item in self.catalog_by_id.values()], "meta": {}}

    async def catalog_rule(self, rule_id: int) -> dict[str, Any]:
        self._record("catalog_rule", rule_id)
        if rule_id not in self.catalog_by_id:
            raise RuntimeError("Kayıt bulunamadı")
        return dict(self.catalog_by_id[rule_id])

    async def save_catalog_rule(self, *, payload: dict[str, Any], rule_id: int | None = None,
                                reason: str, actor: str = "",
                                dry_run: bool | None = None) -> dict[str, Any]:
        self._record("save_catalog_rule", rule_id, payload=payload, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run), "sent": not dry_run, "id": rule_id or 55}

    # -------------------------------------------------------------- kupon

    async def coupons(self, rule_id: int, *, page: int = 1,
                      per_page: int | None = None) -> dict[str, Any]:
        """Ardışık çağrılar `coupon_pages` listesini SIRAYLA verir; son sayfada
        kalır. Üretim testinde "önce" ve "sonra" okuması böyle ayrılır."""
        self._record("coupons", rule_id, page=page, per_page=per_page)
        index = min(self._coupon_reads, len(self.coupon_pages) - 1)
        self._coupon_reads += 1
        return self.coupon_pages[max(0, index)]

    async def create_coupon(self, rule_id: int, *, payload: dict[str, Any], reason: str,
                            actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        self._record("create_coupon", rule_id, payload=payload, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run), "sent": not dry_run}

    async def generate_coupons(self, rule_id: int, *, payload: dict[str, Any], reason: str,
                               actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        self._record("generate_coupons", rule_id, payload=payload, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run), "sent": not dry_run}

    async def delete_coupons(self, rule_id: int, *, coupon_ids: list[int], reason: str,
                             actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        self._record("delete_coupons", rule_id, coupon_ids=coupon_ids, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run), "sent": not dry_run}

    # ----------------------------------------------------------- referans

    async def channels(self) -> dict[str, Any]:
        self._record("channels")
        return {"items": [{"id": 1, "code": "default", "name": "Varsayılan"}]}

    async def customer_groups(self) -> dict[str, Any]:
        self._record("customer_groups")
        return {"items": [{"id": 2, "name": "Genel"}, {"id": 3, "name": "Bayi"}]}

    async def category_tree(self) -> dict[str, Any]:
        self._record("category_tree")
        return {"items": [{"id": 1, "name": "Kitap",
                           "children": [{"id": 4, "name": "Deneme"}]}]}

    async def configuration(self, slug: str, *, channel: str = "",
                            locale: str = "") -> dict[str, Any]:
        self._record("configuration", slug, channel=channel, locale=locale)
        return self.config_payload

    async def product_lookup(self, filters: Any = None, *,
                             per_page: int | None = None) -> dict[str, Any]:
        self._record("product_lookup", filters, per_page=per_page)
        return {"items": [{"id": 5, "name": "Kalem", "sku": "KLM-1"}]}

    async def orders(self, filters: Any = None, *, page: int = 1, per_page: int | None = None,
                     all_pages: bool = False) -> dict[str, Any]:
        self._record("orders", filters, page=page, per_page=per_page, all_pages=all_pages)
        return self.orders_payload

    async def order(self, order_id: int) -> dict[str, Any]:
        """Sipariş DETAYI. Canlıda indirim tutarı yalnız burada var."""
        self._record("order", order_id)
        if order_id not in self.order_details:
            raise RuntimeError("Sipariş bulunamadı")
        return dict(self.order_details[order_id])
