"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`FakeStore` SQL'i ayrıştırmaz; servisin yazdığı iki ifadeyi (denetim satırı,
tercih upsert'i) tanıyacak kadarını yapar. Amaç çekirdek depoyu taklit etmek
değil, servisin doğru anda doğru satırı yazdığını görmek.

`FakeApi` yalnız panonun çağırdığı metotları taşır ve adları `store_api`
geçidindeki imzalarla birebir aynıdır — uydurma metot yoksa test de yanlış
metot adını yakalar.
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

    def __init__(self, module_id: str = "store_dashboard") -> None:
        self.module_id = module_id
        self.audit: list[dict[str, Any]] = []
        self.prefs: dict[str, str] = {}

    def table(self, name: str) -> str:
        return f"mod_{self.module_id}_{name}"

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        text = " ".join(sql.split())
        if "_audit" in text and text.startswith("INSERT"):
            keys = ("action", "reason", "actor", "result", "detail", "created_at")
            self.audit.append(dict(zip(keys, params, strict=False)))
        elif "_prefs" in text:
            self.prefs[params[0]] = params[1]

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        if "_prefs" in sql:
            value = self.prefs.get(params[0])
            return {"value": value} if value is not None else None
        return None

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if "_audit" in sql:
            return list(reversed(self.audit))
        return []


class FakePrinter:
    """`printer` yeteneğinin testlik yüzü. Basılan yolu kaydeder."""

    def __init__(self) -> None:
        self.printed: list[str] = []

    async def print_file(self, path: Any, *, title: str = "", copies: int = 1) -> dict[str, Any]:
        self.printed.append(str(path))
        return {"printer": "BBD-Ofis", "job": 1, "copies": copies, "title": title}

    async def status(self) -> dict[str, Any]:
        return {"ready": True, "error": "", "target": {"name": "BBD-Ofis"}}


def order(order_id: int, *, created: str, total: str, status: str = "processing",
          items: list[dict[str, Any]] | None = None, **extra: Any) -> dict[str, Any]:
    """Bagisto sipariş kaydının testlik minimumu."""
    row: dict[str, Any] = {
        "id": order_id,
        "increment_id": f"BBD-{order_id}",
        "created_at": created,
        "status": status,
        "grand_total": total,
        "customer_first_name": "Ayşe",
        "customer_last_name": "Yılmaz",
        "customer_email": "ayse@example.com",
        "total_qty_ordered": 2,
        "items": items if items is not None else [
            {"name": "Kalem", "sku": "KLM-1", "qty_ordered": 2, "total": "20.00"},
        ],
    }
    row.update(extra)
    return row


class FakeApi:
    """`store.api` yeteneğinin testlik yüzü. Yalnız kullanılan metotlar var."""

    def __init__(self, orders: list[dict[str, Any]] | None = None) -> None:
        self.order_rows = orders or []
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()
        self.refund_rows: list[dict[str, Any]] = []
        self.customer_rows: list[dict[str, Any]] = []
        self.catalog_health: dict[str, Any] = {"out_of_stock": 3}
        self.issue_rows: list[dict[str, Any]] = []
        #: `dashboard/stats?type=stock-threshold-products` satırları. Canlıdaki
        #: biçim: `total_qty` METİN, sıralama YOK (biz sıralıyoruz).
        self.stock_threshold_rows: list[dict[str, Any]] = []
        self.counts: dict[str, int] = {}
        self.config_payload: dict[str, Any] = {}
        #: `configuration/menu` ağacı — düğüm anahtarı slug, `fields` alan
        #: tanımları. Testler bunu `menu_tree(...)` ile kurar.
        self.menu_tree: list[dict[str, Any]] = []
        self.channel_rows: list[dict[str, Any]] = [
            {"id": 1, "code": "default", "name": "Benim Başarı Dünyam",
             "isMaintenanceOn": False, "maintenanceModeText": None, "allowedIps": None},
        ]
        self.snapshot_payload: dict[str, Any] = {"parts": {}, "errors": [], "stale": False}
        self.reporting_payload: Any = {}
        #: `None` ise varsayılan toplu özet üretilir (bkz.
        #: `bbd_reporting_overview`); doldurulursa aynen döner.
        self.overview_payload: dict[str, Any] | None = None
        self.truncate_orders = False
        #: Tarih süzgecini uygulayıp uygulamadığı — Laravel'in sessizce yok
        #: sayması testlerde açıkça kurulabilsin diye.
        self.honor_dates = True

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        if name in self.fail:
            raise RuntimeError(f"{name} patladı")
        self.calls.append((name, args, kwargs))

    def used(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for called, _, kwargs in self.calls if called == name]

    def args(self, name: str) -> list[tuple[Any, ...]]:
        return [args for called, args, _ in self.calls if called == name]

    # ------------------------------------------------------------ sipariş

    async def orders(self, filters: Any = None, *, page: int = 1, per_page: int | None = None,
                     all_pages: bool = False) -> dict[str, Any]:
        self._record("orders", filters, page=page, per_page=per_page, all_pages=all_pages)
        rows = self.order_rows
        if self.honor_dates and (filters or {}).get("date_from"):
            start = filters["date_from"]
            end = filters.get("date_to") or "9999-12-31"
            rows = [row for row in rows if start <= str(row["created_at"])[:10] <= end]
        if not all_pages:
            rows = rows[: (per_page or 10)]
        meta = {"total": self.counts.get("orders", len(rows))}
        return {"items": rows, "meta": meta, "truncated": self.truncate_orders}

    async def refunds(self, filters: Any = None, *, page: int = 1, per_page: int | None = None,
                      all_pages: bool = False) -> dict[str, Any]:
        self._record("refunds", filters, page=page, all_pages=all_pages)
        return {"items": self.refund_rows, "meta": {}, "truncated": False}

    async def customers(self, filters: Any = None, *, page: int = 1,
                        per_page: int | None = None,
                        all_pages: bool = False) -> dict[str, Any]:
        self._record("customers", filters, page=page, all_pages=all_pages)
        return {"items": self.customer_rows, "meta": {}, "truncated": False}

    async def reviews(self, filters: Any = None, *, page: int = 1, per_page: int | None = None,
                      all_pages: bool = False) -> dict[str, Any]:
        self._record("reviews", filters, page=page, per_page=per_page)
        return {"items": [], "meta": {"total": self.counts.get("reviews", 0)}}

    async def reporting(self, area: str, filters: Any = None, *,
                        view: bool = False) -> dict[str, Any]:
        self._record("reporting", area, filters=filters, view=view)
        return self.reporting_payload

    # ------------------------------------------------------------ katalog

    async def bbd_catalog_health(self) -> dict[str, Any]:
        self._record("bbd_catalog_health")
        return self.catalog_health

    async def bbd_catalog_issues(self, filters: Any = None, *, page: int = 1,
                                 per_page: int | None = None) -> dict[str, Any]:
        self._record("bbd_catalog_issues", filters, page=page, per_page=per_page)
        return {"items": self.issue_rows, "meta": {"total": len(self.issue_rows)}}

    async def dashboard_stats(self, *, kind: str = "over-all", start: str = "",
                              end: str = "", channel: str = "") -> dict[str, Any]:
        """Bagisto panosu. Kritik stok kartının kaynağı.

        `total_qty` CANLIDA METİN geliyor ("18") — sahte de öyle vermeli,
        yoksa test sayıya çevirmeyi kanıtlayamaz.
        """
        self._record("dashboard_stats", kind=kind, start=start, end=end)
        return {"type": kind, "statistics": self.stock_threshold_rows}

    # -------------------------------------------------------------- sağlık

    def state(self) -> dict[str, Any]:
        return {"baseUrl": "https://bbdstore.com.tr", "readOnly": True,
                "dryRunDefault": True, "pageSize": 50, "requireReason": True}

    async def health(self) -> dict[str, Any]:
        self._record("health")
        return {"ok": True, "error": "", "code": "", "status": 200, "elapsedMs": 42}

    async def bbd_backups(self) -> dict[str, Any]:
        self._record("bbd_backups")
        return {"items": [{"name": "bbd-2026-08-12.tar.gz", "created_at": "2026-08-12 03:00:00",
                           "bytes": 1024, "scope": "database"}], "meta": {}}

    async def bbd_pos_terminals(self) -> dict[str, Any]:
        self._record("bbd_pos_terminals")
        return {"items": [{"id": 1, "name": "İş Bankası", "status": 1}], "meta": {}}

    async def bbd_carriers(self) -> dict[str, Any]:
        self._record("bbd_carriers")
        return {"items": [{"code": "geliver", "status": 1}], "meta": {}}

    async def bbd_bld_jobs(self, filters: Any = None, *, page: int = 1,
                           per_page: int | None = None) -> dict[str, Any]:
        self._record("bbd_bld_jobs", filters, page=page, per_page=per_page)
        return {"items": [], "meta": {"total": self.counts.get("bld_failed", 0)}}

    async def bbd_reporting_overview(self, *, days: int = 7) -> dict[str, Any]:
        """Toplu özet — `pendingCount` ve `bld` panonun İKİ kartını besler.

        Canlıdaki biçim birebir taklit edilir: `bld` DURUM→ADET sözlüğüdür
        (`pending · sent · duplicate · dead`), "failed" diye bir durum YOKTUR.
        Yetkisiz bölüm `null` gelir — `overview_payload` doğrudan kurularak
        o dal da sınanabilir.
        """
        self._record("bbd_reporting_overview", days=days)
        if self.overview_payload is not None:
            return self.overview_payload
        return {
            "window": {"days": days, "since": "2026-08-13T00:00:00+03:00"},
            "orders": {"total": 3, "revenue": 1050.0, "byStatus": {"processing": 3},
                       "pendingCount": self.counts.get("pending_orders", 0)},
            "shipping": {"created": 0, "purchased": 0, "awaitingPurchase": 0, "withError": 0},
            "bld": {"sent": 8, "dead": self.counts.get("bld_failed", 0)},
            "paymentLinks": {"byStatus": [], "expiringSoon": 0},
        }

    async def bbd_return_requests(self, filters: Any = None, *, page: int = 1,
                                  per_page: int | None = None) -> dict[str, Any]:
        self._record("bbd_return_requests", filters, page=page, per_page=per_page)
        return {"items": [], "meta": {"total": self.counts.get("returns", 0)}}

    # --------------------------------------------------------------- ayar

    async def snapshot(self, *, refresh: bool = False) -> dict[str, Any]:
        self._record("snapshot", refresh=refresh)
        return self.snapshot_payload

    async def configuration(self, slug: str, *, channel: str = "",
                            locale: str = "") -> dict[str, Any]:
        self._record("configuration", slug, channel=channel, locale=locale)
        return self.config_payload

    async def update_configuration(self, slug: str, *, values: dict[str, Any], reason: str,
                                   actor: str = "", channel: str = "", locale: str = "",
                                   dry_run: bool | None = None) -> dict[str, Any]:
        # channel/locale DE kaydedilir: yazmanın okumayla aynı kapsama gitmesi
        # testle kilitlenmeli. Yanlış kanala yazmak, kullanıcının ekranda
        # görmediği bir değeri değiştirir.
        self._record("update_configuration", slug, values=values, reason=reason, actor=actor,
                     channel=channel, locale=locale, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def configuration_menu(self, *, slug: str = "", include_values: bool = False,
                                 channel: str = "", locale: str = "") -> dict[str, Any]:
        self._record("configuration_menu", slug=slug, include_values=include_values,
                     channel=channel, locale=locale)
        return {"items": [{"id": "configuration-menu", "slug": slug or None,
                           "tree": self.menu_tree}], "meta": {}}

    async def channels(self) -> dict[str, Any]:
        self._record("channels")
        return {"items": self.channel_rows, "meta": {}}


def menu_node(slug: str, fields: list[dict[str, Any]],
              children: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Ayar ağacı düğümü. `code` verilmezse `slug.name` kurulur."""
    return {
        "key": slug,
        "name": slug,
        "fields": [{"name": field["name"],
                    "code": field.get("code", f"{slug}.{field['name']}"),
                    "title": field.get("title", field["name"]),
                    "type": field.get("type", "text"),
                    "default": field.get("default"),
                    "value": field.get("value")}
                   for field in fields],
        "children": children or [],
    }
