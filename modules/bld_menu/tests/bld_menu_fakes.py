"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`FakeStore` SQL'i ayrıştırmaz; servisin yazdığı dört ifadeyi (denetim satırı,
önizleme kaydı, önizleme kapatma, tercih okuma) tanıyacak kadarını yapar. Amaç
çekirdek depoyu taklit etmek değil, servisin DOĞRU ANDA DOĞRU SATIRI yazdığını
görmek — özellikle `result="denendi"` izinin geçit çağrısından ÖNCE düşmesini.

`FakeApi` `bld.api` yeteneğinin testlik yüzüdür. `.calls` her çağrıyı sırasıyla
tutar: "kuru provada geçide gerçek yazma gitmedi" iddiası ancak bu listedeki
`dry_run` değerine bakılarak kanıtlanabilir. `.fail` kümesine bir metot adı
atılırsa o metot patlar ve K7 (geçit düşerse ekran ayakta kalır) sınanır.

GÖVDELER SÖZLEŞMEDEN KOPYALANDI (`BLD/docs/control/menu.md`). Alan adları ve
tipleri oradaki gibidir — modülün kendi uydurduğu bir gövdeye karşı geçen test
hiçbir şey kanıtlamaz.
"""

from __future__ import annotations

import json
from typing import Any

#: `menu.md` → `GET /days/{date}` gövdesi, kısaltılmadan.
DAY: dict[str, Any] = {
    "id": 214,
    "location_id": 1,
    "date": "2026-08-17",
    "title": "Ev Yemeği Menüsü",
    "description": "Mercimek çorbası, tavuk sote, pilav, sütlaç",
    "internal_note": "Tavuk tedarikçisi B firması",
    "package_price_kurus": 18000,
    "components_sellable": True,
    "status": "published",
    "cutoff_time": "08:00",
    "capacity_total": 120,
    "image_path": "veykemtu/daily/2026-08-17.jpg",
    "items_total_kurus": 21500,
    "published_at": "2026-08-16T13:40:00Z",
    "created_at": "2026-08-10T08:12:00Z",
    "updated_at": "2026-08-16T13:40:00Z",
    "items": [
        {"id": 901, "menu_id": 12, "name": "Günün Çorbası: Mercimek",
         "label": "Günün Çorbası: Mercimek", "quantity": 1, "sort_order": 10,
         "price_override_kurus": None, "unit_price_kurus": 4500,
         "is_required": True, "sellable_alone": True, "capacity": None,
         "sold_out": False},
        {"id": 902, "menu_id": 27, "name": "Tavuk Sote", "label": None,
         "quantity": 1, "sort_order": 20, "price_override_kurus": 9000,
         "unit_price_kurus": 9000, "is_required": True, "sellable_alone": True,
         "capacity": 60, "sold_out": False},
    ],
}

#: Taslak gün — yayın ön denetimlerini sınamak için.
DRAFT_DAY: dict[str, Any] = {
    **DAY, "id": 231, "date": "2026-08-24", "status": "draft",
    "published_at": None, "capacity_total": None, "cutoff_time": None,
}

#: `menu.md` → `GET /calendar` satırları: biri menüsü olan, biri OLMAYAN gün.
CALENDAR: list[dict[str, Any]] = [
    {"date": "2026-08-17", "id": 214, "status": "published", "title": "Ev Yemeği Menüsü",
     "package_price_kurus": 18000, "item_count": 4, "capacity_total": 120,
     "sold_total": 86, "remaining_total": 34, "cutoff_time": "08:00",
     "cutoff_passed": True, "closed": False, "closed_reason": None,
     "orderable": False, "not_orderable_reason": "cutoff_passed"},
    {"date": "2026-08-22", "id": None, "status": None, "title": None,
     "package_price_kurus": None, "item_count": 0, "capacity_total": None,
     "sold_total": 0, "remaining_total": None, "cutoff_time": None,
     "cutoff_passed": False, "closed": False, "closed_reason": None,
     "orderable": False, "not_orderable_reason": "not_published"},
]

CALENDAR_META: dict[str, Any] = {
    "from": "2026-08-17", "to": "2026-08-23", "location_id": 1,
    "default_cutoff_time": "08:00", "max_lookahead_days": 7,
}

#: `menu.md` → `GET /days/{date}/stock` gövdesi.
STOCK: dict[str, Any] = {
    "date": "2026-08-17",
    "day": {"capacity": 120, "sold": 86, "sold_orders": 66, "sold_subscriptions": 20,
            "remaining": 34, "full": False},
    "items": [
        {"item_id": 901, "menu_id": 12, "name": "Günün Çorbası: Mercimek",
         "capacity": None, "sold": 86, "sold_orders": 66, "sold_subscriptions": 20,
         "remaining": None, "full": False, "sold_out": False},
        {"item_id": 902, "menu_id": 27, "name": "Tavuk Sote",
         "capacity": 60, "sold": 60, "sold_orders": 46, "sold_subscriptions": 14,
         "remaining": 0, "full": True, "sold_out": False},
    ],
    "blocking": {"day": False, "items": [27]},
}

#: `menu.md` → `PUT /days/{date}/stock` yanıtının `data`/`would` bloğu.
STOCK_RESULT: dict[str, Any] = {
    "date": "2026-08-17",
    "day": {"capacity": 120, "sold": 86, "remaining": 34, "full": False},
    "items": [{"item_id": 902, "capacity": 60, "sold": 60, "remaining": 0,
               "full": True, "oversold": False}],
    "warnings": [{"code": "capacity_below_sold", "item_id": 902, "capacity": 60,
                  "sold": 60}],
}


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

    def __init__(self, module_id: str = "bld_menu") -> None:
        self.module_id = module_id
        self.audit: list[dict[str, Any]] = []
        self.previews: dict[str, dict[str, Any]] = {}
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
            keys = ("target_type", "target_date", "target_id", "action", "reason",
                    "actor", "result", "detail", "created_at")
            self.audit.append(dict(zip(keys, params, strict=False)))
        elif "_stock_preview" in text and text.startswith("INSERT"):
            token, date, proposed, baseline, warnings, actor, reason, created = params
            self.previews[token] = {"token": token, "menu_date": date,
                                    "proposed": proposed, "baseline": baseline,
                                    "warnings": warnings, "status": "dry_run",
                                    "actor": actor, "reason": reason,
                                    "created_at": created, "applied_at": ""}
        elif "_stock_preview" in text and text.startswith("UPDATE"):
            applied_at, token = params
            row = self.previews.get(token)
            if row:
                row.update(status="applied", applied_at=applied_at)
        elif "_prefs" in text:
            self.prefs[params[0]] = params[1]

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        if "_stock_preview" in sql:
            return self.previews.get(params[0])
        return None

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if "_prefs" in sql:
            return [{"key": key, "value": value} for key, value in self.prefs.items()]
        if "_audit" in sql:
            return list(reversed(self.audit))
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


class FakeError(RuntimeError):
    """`BldApiError`in testlik ikizi — `code` alanı taşır.

    Gerçek sınıf `modules/bld_api/backend/errors.py` içindedir ve BURAYA
    IMPORT EDİLMEZ: bir panel modülü başka bir modülün backend'ini import
    etmez (K2/K4). Servis de zaten sınıfa değil `code` ÖZNİTELİĞİNE bakıyor;
    testin taklit etmesi gereken sözleşme budur.
    """

    def __init__(self, message: str, *, code: str = "http") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class FakeApi:
    """`bld.api` yeteneğinin testlik yüzü. Yalnız `menu` + `products` metotları.

    METOT ADLARI VE İMZALARI `modules/bld_api/backend/client.py` İLE BİREBİR
    AYNI OLMALIDIR (donmuş tablo: `modules/bld_api/README.md` §2 ve §3).
    Uydurma bir ad (`menu_days` gibi) buradaki testleri yeşil tutar ama
    canlıda `AttributeError` verir — ve servis istisnayı K7 gereği yuttuğu
    için hata ekranda "BLD'ye ulaşılamadı" diye görünür: yanlış metot adı,
    düşmüş bir sunucudan AYIRT EDİLEMEZ.

    `reason` VARSAYILANI GEÇİTTEKİYLE AYNI OLMALIDIR. Gerçek geçitte gerekçe
    istemeyen menü metotlarında `reason: str = ""`, isteyenlerde varsayılan
    YOK. Buradaki taklit varsayılanı gereksiz yere zorunlu bırakırsa, servisin
    gerekçesiz çağrısı testte `TypeError` verir ama canlıda çalışır — ve
    tersi daha kötüsüdür: taklit gevşek olsaydı, geçidin zorunlu tuttuğu bir
    alanı atlayan servis testte geçer, canlıda patlardı.

    KISMİ YAZMA `UNSET` İLE ÇALIŞIR. Gerçek geçitte gönderilmeyen alanın
    varsayılanı `UNSET` nöbetçisidir ve gövdeye konmaz; burada karşılığı,
    `**kwargs` ile YALNIZ gelen anahtarları kaydetmektir. Böylece "servis
    gerçekten yalnız değişen alanı gönderdi mi" sorusu ölçülebilir.
    """

    def __init__(self, *, day: dict[str, Any] | None = None,
                 stock: dict[str, Any] | None = None) -> None:
        self.day_rows: dict[str, dict[str, Any]] = {}
        if day is not None:
            self.day_rows[str(day["date"])] = day
        self.stock_payload: dict[str, Any] = dict(stock or STOCK)
        self.calendar_rows: list[dict[str, Any]] = [dict(row) for row in CALENDAR]
        self.calendar_meta: dict[str, Any] = dict(CALENDAR_META)
        self.product_rows: list[dict[str, Any]] = [
            {"menu_id": 12, "name": "Mercimek Çorbası", "price_kurus": 4500,
             "category": "Çorba", "sold_out": False, "status": True},
            {"menu_id": 27, "name": "Tavuk Sote", "price_kurus": 9000,
             "category": "Ana Yemek", "sold_out": False, "status": True},
        ]
        self.product_truncated = False
        self.stock_result: dict[str, Any] = dict(STOCK_RESULT)

        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()
        self.fail_code = "http"
        #: `fail` içindeki ad kaç BAŞARILI çağrıdan sonra patlasın. 0 = hemen.
        self.fail_after = 0
        #: Sunucu isteği kuru prova olarak işlediyse yanıt `dry_run: true`
        #: taşır. Testler bunu açarak "geçit varsayılanı provaya çevirdi"
        #: durumunu taklit eder.
        self.echo_dry_run = True

    # ------------------------------------------------------------- kayıt

    def _record(self, name: str, /, *args: Any, **kwargs: Any) -> None:
        if name in self.fail and len(self.args_of(name)) >= self.fail_after:
            raise FakeError(f"{name} patladı", code=self.fail_code)
        self.calls.append((name, args, kwargs))

    def used(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for called, _, kwargs in self.calls if called == name]

    def args_of(self, name: str) -> list[tuple[Any, ...]]:
        return [args for called, args, _ in self.calls if called == name]

    def names(self) -> list[str]:
        return [name for name, _, _ in self.calls]

    def writes(self) -> list[str]:
        """Yazan çağrıların adları."""
        yazan = ("create_menu_day", "update_menu_day", "delete_menu_day",
                 "publish_menu_day", "unpublish_menu_day", "create_menu_item",
                 "update_menu_item", "delete_menu_item", "set_menu_stock",
                 "duplicate_menu_day")
        return [name for name, _, _ in self.calls if name in yazan]

    def _envelope(self, data: Any, *, dry_run: bool | None) -> dict[str, Any]:
        """`00-genel.md` §3.1 yazma zarfı: gerçekte `data`, provada `would`."""
        if dry_run and self.echo_dry_run:
            return {"ok": True, "dry_run": True, "audit_id": 1502, "would": data}
        return {"ok": True, "dry_run": False, "audit_id": 1501, "data": data}

    # ------------------------------------------------------------- okuma

    async def menu_calendar(self, *, date_from: str, date_to: str,
                            location_id: int | None = None) -> dict[str, Any]:
        self._record("menu_calendar", date_from=date_from, date_to=date_to,
                     location_id=location_id)
        return {"items": [dict(row) for row in self.calendar_rows],
                "meta": dict(self.calendar_meta)}

    async def menu_day(self, date: str, *, location_id: int | None = None) -> dict[str, Any]:
        self._record("menu_day", date, location_id=location_id)
        row = self.day_rows.get(str(date))
        if row is None:
            raise FakeError(f"{date} gününe menü yok", code="not_found")
        return {"data": dict(row)}

    async def menu_stock(self, date: str, *,
                         location_id: int | None = None) -> dict[str, Any]:
        self._record("menu_stock", date, location_id=location_id)
        return {"data": dict(self.stock_payload)}

    async def product_picker(self, *, only_active: bool = True) -> dict[str, Any]:
        self._record("product_picker", only_active=only_active)
        return {"items": [dict(row) for row in self.product_rows],
                "total": len(self.product_rows), "pages": 1,
                "truncated": self.product_truncated}

    # ------------------------------------------------------------- yazma

    async def create_menu_day(self, *, date: str, actor: str, reason: str = "",
                              dry_run: bool | None = None, **fields: Any) -> dict[str, Any]:
        self._record("create_menu_day", date=date, reason=reason, actor=actor,
                     dry_run=dry_run, **fields)
        row = {**DRAFT_DAY, "date": date, "status": "draft",
               "items": fields.get("items") or []}
        return self._envelope(row, dry_run=dry_run)

    async def update_menu_day(self, date: str, *, actor: str, reason: str = "",
                              dry_run: bool | None = None, **fields: Any) -> dict[str, Any]:
        self._record("update_menu_day", date, reason=reason, actor=actor,
                     dry_run=dry_run, **fields)
        return self._envelope({**self.day_rows.get(date, DAY), **fields},
                              dry_run=dry_run)

    async def delete_menu_day(self, date: str, *, location_id: int | None = None,
                              reason: str, actor: str,
                              dry_run: bool | None = None) -> dict[str, Any]:
        self._record("delete_menu_day", date, location_id=location_id, reason=reason,
                     actor=actor, dry_run=dry_run)
        return self._envelope({"deleted": True, "date": date}, dry_run=dry_run)

    async def publish_menu_day(self, date: str, *, location_id: int | None = None,
                               reason: str, actor: str,
                               dry_run: bool | None = None) -> dict[str, Any]:
        self._record("publish_menu_day", date, location_id=location_id, reason=reason,
                     actor=actor, dry_run=dry_run)
        return self._envelope({"date": date, "status": "published",
                               "published_at": "2026-08-16T13:40:00Z"}, dry_run=dry_run)

    async def unpublish_menu_day(self, date: str, *, location_id: int | None = None,
                                 reason: str, actor: str,
                                 dry_run: bool | None = None) -> dict[str, Any]:
        self._record("unpublish_menu_day", date, location_id=location_id, reason=reason,
                     actor=actor, dry_run=dry_run)
        return self._envelope({"date": date, "status": "draft"}, dry_run=dry_run)

    async def create_menu_item(self, date: str, *, menu_id: int, actor: str,
                               reason: str = "",
                               dry_run: bool | None = None, **fields: Any) -> dict[str, Any]:
        self._record("create_menu_item", date, menu_id=menu_id, reason=reason,
                     actor=actor, dry_run=dry_run, **fields)
        return self._envelope({"id": 903, "menu_id": menu_id, **fields}, dry_run=dry_run)

    async def update_menu_item(self, date: str, item_id: int, *, actor: str,
                               reason: str = "",
                               dry_run: bool | None = None, **fields: Any) -> dict[str, Any]:
        self._record("update_menu_item", date, item_id, reason=reason, actor=actor,
                     dry_run=dry_run, **fields)
        return self._envelope({"id": item_id, **fields}, dry_run=dry_run)

    async def delete_menu_item(self, date: str, item_id: int, *, actor: str,
                               reason: str = "",
                               dry_run: bool | None = None) -> dict[str, Any]:
        self._record("delete_menu_item", date, item_id, reason=reason, actor=actor,
                     dry_run=dry_run)
        return self._envelope({"deleted": True, "item_id": item_id}, dry_run=dry_run)

    async def set_menu_stock(self, date: str, *, capacity_total: int | None,
                             items: list[dict[str, Any]], location_id: int | None = None,
                             actor: str, reason: str = "",
                             dry_run: bool | None = None) -> dict[str, Any]:
        self._record("set_menu_stock", date, capacity_total=capacity_total, items=items,
                     location_id=location_id, reason=reason, actor=actor, dry_run=dry_run)
        return self._envelope(dict(self.stock_result), dry_run=dry_run)

    async def duplicate_menu_day(self, date: str, *, target_date: str,
                                 overwrite: bool = False, location_id: int | None = None,
                                 reason: str, actor: str,
                                 dry_run: bool | None = None) -> dict[str, Any]:
        self._record("duplicate_menu_day", date, target_date=target_date,
                     overwrite=overwrite, location_id=location_id, reason=reason,
                     actor=actor, dry_run=dry_run)
        return self._envelope({"source_date": date, "target_date": target_date,
                               "id": 231, "item_count": 4, "status": "draft"},
                              dry_run=dry_run)
