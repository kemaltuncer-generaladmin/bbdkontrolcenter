"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`FakeStore` SQL'i ayrıştırmaz; servisin yazdığı üç ifadeyi (denetim satırı,
tercih yazma, tercih/iz okuma) tanıyacak kadarını yapar. Amaç çekirdek depoyu
taklit etmek değil, servisin DOĞRU ANDA DOĞRU SATIRI yazdığını görmek —
özellikle `result="denendi"` izinin geçit çağrısından ÖNCE düşmesini.

`FakeApi` `bld.api` yeteneğinin testlik yüzüdür. `.calls` her çağrıyı sırasıyla
tutar: "yazma isteği hiç gitmedi" iddiası ancak bu liste boş kalarak
kanıtlanabilir. `.fail` kümesine bir metot adı atılırsa o metot patlar ve K7
(geçit düşerse ekran ayakta kalır) sınanır.

FIXTURE'LAR SÖZLEŞMEDEN KOPYALANMIŞTIR (`BLD/docs/control/products.md`). Alan
adları ve tipleri oradaki gibidir; modülün kendi uydurduğu bir gövdeye karşı
geçen test hiçbir şey kanıtlamaz.
"""

from __future__ import annotations

import json
from typing import Any

#: `products.md` → `GET /` yanıtındaki ürün satırı, kısaltılmadan.
PRODUCT: dict[str, Any] = {
    "menu_id": 27,
    "name": "Tavuk Sote",
    "description": "Tereyağında sotelenmiş tavuk",
    "price_kurus": 9000,
    "minimum_qty": 1,
    "priority": 10,
    "status": True,
    "category_ids": [3],
    "image_url": "https://api.bld.example/uploads/menu/tavuk-sote.jpg",
    "sold_out_today": False,
    "sold_out_reason": None,
    "is_package_product": False,
    "options": [],
    "created_at": "2026-06-02T07:00:00Z",
    "updated_at": "2026-08-11T19:20:00Z",
}

#: "Günün Menüsü" paket ürünü: kendi fiyatı 0,00'dır ve gerçek fiyat o günün
#: paket fiyatıdır. `price_kurus` yazılması sunucuda `422` verir.
PACKAGE_PRODUCT: dict[str, Any] = {
    **PRODUCT,
    "menu_id": 41,
    "name": "Günün Menüsü",
    "price_kurus": 0,
    "is_package_product": True,
    "image_url": None,
}

#: `products.md` → seçenek örneği. SALT OKUNUR; `values[].id` sipariş
#: revizyonundaki `option_value_ids` alanına doğrudan konur.
OPTION: dict[str, Any] = {
    "id": 7,
    "name": "Ekstra",
    "type": "checkbox",
    "required": False,
    "values": [{"id": 31, "name": "Ekstra pilav", "price_delta_kurus": 2500}],
}

#: `products.md` → `GET /categories` yanıtı.
CATEGORIES: list[dict[str, Any]] = [
    {"category_id": 3, "name": "Ana Yemek", "description": None, "parent_id": None,
     "priority": 10, "status": True, "slug": "ana-yemek", "menu_count": 22},
    {"category_id": 4, "name": "Çorba", "description": None, "parent_id": None,
     "priority": 20, "status": True, "slug": "corba", "menu_count": 9},
    {"category_id": 5, "name": "Mercimek", "description": None, "parent_id": 4,
     "priority": 10, "status": True, "slug": "mercimek", "menu_count": 3},
]

GEREKCE = "Zam sonrası fiyat güncellendi"
AKTOR = "Ayşe Yılmaz"

#: Gerçek geçidin `UNSET` nöbetçisinin karşılığı: "bu alan hiç gönderilmedi".
#: `None` KULLANILAMAZ çünkü `None` gerçek bir değerdir — `description: null`
#: açıklamayı boşaltır, `parent_id: null` kategoriyi kök seviyeye taşır.
_UNSET = object()


def _given(**fields: Any) -> dict[str, Any]:
    """Yalnız GERÇEKTEN gönderilen alanlar."""
    return {key: value for key, value in fields.items() if value is not _UNSET}


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

    def __init__(self, module_id: str = "bld_products") -> None:
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
    """Olay yolu. Kuru provada ve başarısız çağrıda BOŞ kalmalı."""

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
    """`bld.api` yeteneğinin testlik yüzü. Yalnız ürün alanı metotları var.

    METOT ADLARI VE İMZALARI `modules/bld_api/backend/client.py` İLE BİREBİR
    AYNI OLMALIDIR (donmuş tablo: `bld_api/README.md` §3). Uydurma bir ad
    (`list_products` gibi) buradaki testleri yeşil tutar ama canlıda
    `AttributeError` verir — ve servis istisnayı K7 gereği yuttuğu için hata
    ekranda "BLD'ye ulaşılamadı" diye görünür: yanlış metot adı, düşmüş bir
    sunucudan AYIRT EDİLEMEZ.

    Liste metotları `{"items": [...], "meta": {...}}` döndürür; gerçek geçit
    `_list()` içinde zarfı böyle açıyor.
    """

    def __init__(self, products: list[dict[str, Any]] | None = None,
                 categories: list[dict[str, Any]] | None = None) -> None:
        self.product_rows = products if products is not None else [dict(PRODUCT)]
        self.category_rows = categories if categories is not None else [
            dict(row) for row in CATEGORIES]
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()
        #: Sunucu yanıtındaki `dry_run` — geçit onu aynen geçiriyor.
        self.echo_dry_run = True
        self.audit_id = 1602

    # ------------------------------------------------------------- kayıt

    # `name` KONUM-ONLY (`/`): geçidin iki metodu `name=` adında bir anahtar
    # argüman taşıyor (`create_product`, `create_category`) ve normal bir
    # parametre olsaydı çağrı "name için iki değer" diye patlardı — üstelik
    # servis istisnayı yutup `ok: False` döndüğü için test, kuralın çalıştığını
    # sanarak YEŞİL kalırdı.
    def _record(self, name: str, /, *args: Any, **kwargs: Any) -> None:
        if name in self.fail:
            raise RuntimeError(f"{name} patladı")
        self.calls.append((name, args, kwargs))

    def used(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for called, _, kwargs in self.calls if called == name]

    def args_of(self, name: str) -> list[tuple[Any, ...]]:
        return [args for called, args, _ in self.calls if called == name]

    def names(self) -> list[str]:
        return [name for name, _, _ in self.calls]

    def writes(self) -> list[str]:
        """Yazan çağrıların adları. Engellenen işlemde BOŞ kalmalı."""
        yazan = ("create_product", "update_product", "delete_product",
                 "set_product_image", "delete_product_image", "mark_product_sold_out",
                 "clear_product_sold_out", "create_category", "update_category")
        return [name for name, _, _ in self.calls if name in yazan]

    def _envelope(self, data: dict[str, Any], dry_run: bool | None) -> dict[str, Any]:
        body: dict[str, Any] = {"ok": True, "audit_id": self.audit_id}
        if self.echo_dry_run:
            body["dry_run"] = bool(dry_run)
        if dry_run:
            body["would"] = dict(data)
        else:
            body["data"] = dict(data)
        return body

    # ------------------------------------------------------------- okuma

    async def products(self, *, q: str = "", category_id: int | None = None,
                       status: str = "", sold_out: bool | None = None, sort: str = "",
                       direction: str = "", page: int = 1,
                       per_page: int | None = None) -> dict[str, Any]:
        self._record("products", q=q, category_id=category_id, status=status,
                     sold_out=sold_out, sort=sort, direction=direction, page=page,
                     per_page=per_page)
        rows = [dict(row) for row in self.product_rows]
        if status == "inactive":
            rows = [row for row in rows if not row.get("status")]
        if sold_out is not None:
            rows = [row for row in rows if bool(row.get("sold_out_today")) is sold_out]
        return {"items": rows, "meta": {"page": page, "per_page": per_page or 25,
                                        "total": len(rows), "last_page": 1}}

    async def product(self, menu_id: int) -> dict[str, Any]:
        self._record("product", menu_id)
        for row in self.product_rows:
            if int(row.get("menu_id") or 0) == int(menu_id):
                return {"data": dict(row)}
        raise RuntimeError("Ürün bulunamadı")

    async def categories(self) -> dict[str, Any]:
        self._record("categories")
        return {"items": [dict(row) for row in self.category_rows], "meta": {}}

    # ------------------------------------------------------------- yazma

    async def create_product(self, *, name: str, price_kurus: int,
                             description: str | None = None, minimum_qty: int = 1,
                             priority: int = 0, status: bool = True,
                             category_ids: list[int] | None = None, reason: str, actor: str,
                             dry_run: bool | None = None) -> dict[str, Any]:
        self._record("create_product", name=name, price_kurus=price_kurus,
                     description=description, minimum_qty=minimum_qty, priority=priority,
                     status=status, category_ids=category_ids, reason=reason, actor=actor,
                     dry_run=dry_run)
        return self._envelope({**PRODUCT, "menu_id": 99, "name": name,
                               "price_kurus": price_kurus}, dry_run)

    # ALANLAR AÇIKÇA YAZILIR, `**fields` DEĞİL. Gerçek geçitte her alanın kendi
    # `UNSET` varsayılanı var; burada `**fields` yazmak, servisin uydurduğu bir
    # alan adını (`price`, `is_active`) sessizce kabul eder ve test yeşil kalırdı.
    async def update_product(self, menu_id: int, *, name: Any = _UNSET,
                             description: Any = _UNSET, price_kurus: Any = _UNSET,
                             minimum_qty: Any = _UNSET, priority: Any = _UNSET,
                             status: Any = _UNSET, category_ids: Any = _UNSET,
                             reason: str, actor: str,
                             dry_run: bool | None = None) -> dict[str, Any]:
        fields = _given(name=name, description=description, price_kurus=price_kurus,
                        minimum_qty=minimum_qty, priority=priority, status=status,
                        category_ids=category_ids)
        self._record("update_product", menu_id, reason=reason, actor=actor,
                     dry_run=dry_run, **fields)
        return self._envelope({**PRODUCT, "menu_id": menu_id, **fields}, dry_run)

    async def delete_product(self, menu_id: int, *, reason: str, actor: str,
                             dry_run: bool | None = None) -> dict[str, Any]:
        self._record("delete_product", menu_id, reason=reason, actor=actor, dry_run=dry_run)
        return self._envelope({"menu_id": menu_id, "status": False, "soft_deleted": True},
                              dry_run)

    async def set_product_image(self, menu_id: int, *, content: Any, filename: str,
                                reason: str, actor: str,
                                dry_run: bool | None = None) -> dict[str, Any]:
        self._record("set_product_image", menu_id, content=content, filename=filename,
                     reason=reason, actor=actor, dry_run=dry_run)
        body = self._envelope({"menu_id": menu_id, "image_url": "https://x/27.jpg",
                               "mime": "image/jpeg", "bytes": 184320}, dry_run)
        # Geçit yüklenen dosyanın künyesini böyle ekliyor; İÇERİK GİRMEZ.
        body["upload"] = {"filename": filename, "mime": "image/jpeg", "bytes": 184320}
        return body

    async def delete_product_image(self, menu_id: int, *, reason: str, actor: str,
                                   dry_run: bool | None = None) -> dict[str, Any]:
        self._record("delete_product_image", menu_id, reason=reason, actor=actor,
                     dry_run=dry_run)
        return self._envelope({"menu_id": menu_id, "image_url": None}, dry_run)

    async def mark_product_sold_out(self, menu_id: int, *, note: str | None = None,
                                    reason: str, actor: str,
                                    dry_run: bool | None = None) -> dict[str, Any]:
        self._record("mark_product_sold_out", menu_id, note=note, reason=reason,
                     actor=actor, dry_run=dry_run)
        return self._envelope({"menu_id": menu_id, "sold_out_today": True,
                               "sold_out_on": "2026-08-16", "sold_out_reason": reason},
                              dry_run)

    async def clear_product_sold_out(self, menu_id: int, *, reason: str, actor: str,
                                     dry_run: bool | None = None) -> dict[str, Any]:
        self._record("clear_product_sold_out", menu_id, reason=reason, actor=actor,
                     dry_run=dry_run)
        return self._envelope({"menu_id": menu_id, "sold_out_today": False}, dry_run)

    async def create_category(self, *, name: str, description: str | None = None,
                              parent_id: int | None = None, priority: int = 0,
                              status: bool = True, reason: str, actor: str,
                              dry_run: bool | None = None) -> dict[str, Any]:
        self._record("create_category", name=name, description=description,
                     parent_id=parent_id, priority=priority, status=status, reason=reason,
                     actor=actor, dry_run=dry_run)
        return self._envelope({"category_id": 9, "name": name, "parent_id": parent_id},
                              dry_run)

    async def update_category(self, category_id: int, *, name: Any = _UNSET,
                              description: Any = _UNSET, parent_id: Any = _UNSET,
                              priority: Any = _UNSET, status: Any = _UNSET, reason: str,
                              actor: str, dry_run: bool | None = None) -> dict[str, Any]:
        fields = _given(name=name, description=description, parent_id=parent_id,
                        priority=priority, status=status)
        self._record("update_category", category_id, reason=reason, actor=actor,
                     dry_run=dry_run, **fields)
        return self._envelope({"category_id": category_id, **fields}, dry_run)


def make_service(**kwargs: Any) -> tuple[Any, FakeApi, FakeStore, FakeBus]:
    """Servis + sahteleri birlikte kurar; her test tek satırla başlasın."""
    from bld_products_backend.service import ProductsService

    api = kwargs.pop("api", None) or FakeApi()
    store = kwargs.pop("store", None) or FakeStore()
    bus = kwargs.pop("bus", None) or FakeBus()
    config = kwargs.pop("config", None) or {"dry_run_default": False, "page_size": 25}
    service = ProductsService(api=api, store=store, log=FakeLog(), config=config,
                              publish=bus)
    return service, api, store, bus
