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
        #: Kategori ağacı. Canlıdaki gibi KÖK kategori tepede durur ve
        #: gerçek raflar onun çocuklarıdır.
        self.tree_payload: dict[str, Any] = {"items": [{"id": 1, "name": "Kitap"}]}
        #: Mağazada DOLU olan url_key'ler. `products` süzgeci bunlara bakar.
        self.taken_url_keys: set[str] = set()
        #: Laravel tanımadığı sorgu parametresini SESSİZCE yok sayar. Bu bayrak
        #: kapatıldığında sahte de öyle davranır: süzgeç gönderilir ama yanıt
        #: süzülmemiş listedir — servis bunu "bilinmiyor" saymalıdır.
        self.url_key_filter_honored = True
        #: Envanter kaynakları (depolar). Boşaltmak "depo okunamadı" hâlini kurar.
        self.sources_payload: list[dict[str, Any]] = [{"id": 1, "name": "Merkez"}]
        #: Açılan ürüne verilecek sıradaki kimlik.
        self.next_product_id = 1500
        #: Anlık görüntü parçaları. CANLIDAKİ GERÇEK: tek kanal, tek dil, tek
        #: para birimi, tek depo, tek vergi kategorisi. Test ikinci bir kanal
        #: eklediğinde alanın GERİ GELDİĞİ görülür — kural sayıdan çıkıyor.
        self.snapshot_parts: dict[str, Any] = {
            "channels": [{"id": 1, "code": "default", "name": "Varsayılan"}],
            "locales": [{"id": 1, "code": "tr", "name": "Türkçe"}],
            "currencies": [{"id": 1, "code": "TRY", "name": "Türk Lirası"}],
            "inventory_sources": [{"id": 1, "code": "default", "name": "Merkez"}],
            "tax_categories": [{"id": 1, "code": "kdv", "name": "KDV"}],
            "attribute_families": [{"id": 2, "code": "kitap", "name": "Kitap"}],
            "customer_groups": [{"id": 1, "code": "general", "name": "Genel"}],
        }
        #: Silinen ürünlerin kimlikleri — silme gerçekten oldu mu, test bakar.
        self.deleted_ids: list[int] = []
        #: {ürün: (siparişSayısı, satılanAdet)} — satış özeti tablosu.
        self.bestsellers: dict[int, tuple[int, int]] = {}
        #: Laravel tanımadığı sorgu parametresini SESSİZCE yok sayar. Kapatınca
        #: sahte de öyle davranır: süzgeç gönderilir, BAŞKA ürünün satırı döner.
        self.bestseller_filter_honored = True

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
        wanted = (filters or {}).get("url_key")
        if wanted is not None and self.taken_url_keys:
            if not self.url_key_filter_honored:
                # Süzgeç yok sayıldı: kataloğun ilk sayfası döner.
                return {"items": [{"id": index, "sku": f"SKU-{index}", "url_key": key}
                                  for index, key in enumerate(sorted(self.taken_url_keys), 1)],
                        "meta": {}}
            hit = str(wanted).lower()
            return {"items": [{"id": 900, "sku": "DOLU-1", "url_key": hit}]
                    if hit in self.taken_url_keys else [], "meta": {}}
        return self.list_payload

    async def product(self, product_id: int) -> dict[str, Any]:
        self._record("product", product_id)
        if product_id not in self.products_by_id:
            # Geçit 404'ü `not_found` koduyla veriyor ve servis "kayıt yok" ile
            # "okunamadı" ayrımını O KODA dayandırıyor (silinmiş ürün tespiti).
            # Kodsuz düz bir istisna, ikisini ayırt edemeyen bir sahte üretir.
            raise FakeStoreError("Kayıt bulunamadı", status=404, code="not_found")
        return self.products_by_id[product_id]

    async def product_inventories(self, product_id: int) -> dict[str, Any]:
        self._record("product_inventories", product_id)
        return {"items": [{"inventory_source_id": 1, "qty": 11}]}

    async def customer_group_prices(self, product_id: int) -> dict[str, Any]:
        self._record("customer_group_prices", product_id)
        return {"items": []}

    async def inventory_sources(self) -> dict[str, Any]:
        self._record("inventory_sources")
        return {"items": list(self.sources_payload)}

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
        return self.tree_payload

    async def snapshot(self, *, refresh: bool = False) -> dict[str, Any]:
        self._record("snapshot", refresh=refresh)
        return {"parts": dict(self.snapshot_parts), "errors": [], "stale": False,
                "storedAt": ""}

    # --------------------------------------------------------------- silme

    async def delete_product(self, product_id: int, *, reason: str, actor: str = "",
                             dry_run: bool | None = None) -> dict[str, Any]:
        """Geçitteki `delete_product` ucunun testlik ikizi.

        DİKKAT — bu metot CANLI GEÇİTTE HENÜZ YOK. Sahtede bulunması, silme
        akışının geçit ucu eklendiği gün çalışacağını kanıtlar; ucun eksikliği
        ayrıca `hasattr` ile test ediliyor (bkz. test_store_products_delete).
        """
        self._record("delete_product", product_id, reason=reason, actor=actor, dry_run=dry_run)
        if not dry_run:
            self.deleted_ids.append(int(product_id))
            self.products_by_id.pop(int(product_id), None)
        return {"ok": True, "dryRun": bool(dry_run), "sent": not dry_run}

    async def bbd_bestsellers(self, filters: Any = None, *, page: int = 1,
                              per_page: int | None = None) -> dict[str, Any]:
        self._record("bbd_bestsellers", filters, page=page, per_page=per_page)
        wanted = (filters or {}).get("product_id")
        rows = [{"productId": key, "orderCount": counts[0], "soldQty": counts[1],
                 "lastOrderedAt": "2026-08-01T10:00:00"}
                for key, counts in sorted(self.bestsellers.items())]
        if wanted is None or not self.bestseller_filter_honored:
            return {"items": rows[:1], "meta": {"total": len(rows)}}
        hit = [row for row in rows if row["productId"] == int(wanted)]
        return {"items": hit, "meta": {"total": len(hit)}}

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
        if dry_run:
            # KURU PROVADA ÜRÜN DOĞMAZ, dolayısıyla kimlik de yoktur. Geçit
            # sentetik yanıt döner (`{"ok": True, "dryRun": True, "sent": False}`)
            # ve kimliği olmayan bir ürüne ayrıntı yazılamaz. Sahtenin kimlik
            # uydurması, servisin hayalî bir kimliğe yazdığını gizlerdi.
            return {"ok": True, "dryRun": True, "sent": False}
        new_id = self.next_product_id
        self.next_product_id += 1
        # Açılan ürün ARTIK OKUNABİLİR: servis ayrıntıları yazmadan önce kaydı
        # taze okuyor (OKU-DEĞİŞTİR-YAZ) ve o okuma gerçek akışın parçası.
        self.products_by_id[new_id] = {
            "id": new_id, "sku": payload.get("sku"), "type": payload.get("type") or "simple",
            "attribute_family_id": payload.get("attribute_family_id"), "status": 0,
            "url_key": "", "name": "",
        }
        return {"ok": True, "dryRun": False, "sent": True,
                "data": {"id": new_id, "sku": payload.get("sku")}}

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
