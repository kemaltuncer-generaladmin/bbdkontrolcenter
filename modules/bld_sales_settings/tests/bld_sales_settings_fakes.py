"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`SALES` sözlüğü `BLD/docs/control/settings.md` → `GET /sales` gövdesinden
KOPYALANMIŞTIR, kısaltılmadan. Alan adları ve tipleri SÖZLEŞMEDEKİ gibidir —
modülün kendi uydurduğu bir gövdeye karşı geçen test hiçbir şey kanıtlamaz.

`FakeApi` `bld.api` yeteneğinin testlik yüzüdür. `.calls` her çağrıyı sırasıyla
tutar: "önizlemede uzağa gerçek yazma gitmedi" iddiası ancak bu listeye bakarak
kanıtlanabilir. `.fail` kümesine bir metot adı atılırsa o metot patlar ve K7
(geçit düşerse ekran ayakta kalır) sınanır. `.force_dry_run` ise bu ekranın en
pahalı arızasını taklit eder: yazma istendi, sunucu kuru prova yaptı.
"""

from __future__ import annotations

import json
from typing import Any

#: `GET /settings/sales` → `data`. Sözleşmeden birebir.
SALES: dict[str, Any] = {
    "location_id": 1,
    "location_name": "BLD Merkez Mutfak",

    "ordering_enabled": True,
    "paused_until": None,
    "pause_reason": None,
    "is_open": True,

    # `subscription_release_time` BURADA YOKTUR: ayar 17.08.2026'da sunucudan
    # kaldırıldı ve `toControlData()` artık yayınlamıyor. Sahte gövdeye
    # bırakmak, testin sözleşmede olmayan bir alana karşı geçmesi olurdu —
    # tam da panelin kaydedememesine yol açan ayrışmayı görünmez yapardı.
    "order_cutoff": "08:00",
    "max_lookahead_days": 7,

    "min_order_total_kurus": 15000,
    "delivery_fee_kurus": 2500,
    "payment_methods": ["online", "cash"],

    "busy": False,
    "busy_message": "Mutfağımız şu anda yoğun. Siparişiniz alınır ancak hazırlanması "
                    "normalden uzun sürebilir.",

    "prep_minutes": 40,
    "delivery_minutes": 20,
    "busy_extra_minutes": 15,

    "daily_menu_enabled": True,
    "daily_package_menu_id": 88,
    "auto_invoice": False,

    "server_time": "2026-08-16T09:00:00Z",
}

#: `GET /settings/sales` → `meta`. Panelin gri ipucu metinleri bundan çizilir.
SALES_META: dict[str, Any] = {
    "available_payment_methods": ["online", "cash"],
    "defaults": {
        "busy_message": "Mutfağımız şu anda yoğun. Siparişiniz alınır ancak "
                        "hazırlanması normalden uzun sürebilir.",
        "max_lookahead_days": 7,
        "prep_minutes": 40,
        "delivery_minutes": 20,
        "busy_extra_minutes": 15,
    },
}

#: `GET /settings/closed-days` → `data`.
CLOSED_DAYS: list[dict[str, Any]] = [
    {"id": 12, "date": "2026-08-30", "description": "30 Ağustos Zafer Bayramı"},
    {"id": 13, "date": "2026-10-29", "description": "29 Ekim Cumhuriyet Bayramı"},
]

#: `GET /menu/days/{date}/stock` → `data`. `sold` REZERVE porsiyondur ve
#: abonelikler onu önceden tutar; iki bileşen ayrı durur.
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
    """`ModuleStore` yüzeyi. Satırları bellekte tutar.

    SQL ayrıştırılmaz; servisin yazdığı dört ifadeyi (denetim satırı, taban
    çizgisi, taban okuma, tercih yazma) tanıyacak kadarı yapılır. Amaç çekirdek
    depoyu taklit etmek değil, servisin DOĞRU ANDA DOĞRU SATIRI yazdığını
    görmek — özellikle `result="denendi"` izinin geçit çağrısından ÖNCE
    düşmesini.
    """

    def __init__(self, module_id: str = "bld_sales_settings") -> None:
        self.module_id = module_id
        self.audit: list[dict[str, Any]] = []
        self.baselines: dict[str, dict[str, Any]] = {}
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
            self.audit.append(dict(zip(keys, params, strict=False)))
        elif "_baseline" in text and text.startswith("INSERT"):
            token, location_id, snapshot, created = params
            self.baselines[token] = {"token": token, "location_id": location_id,
                                     "snapshot": snapshot, "created_at": created}
        elif "_prefs" in text:
            self.prefs[params[0]] = params[1]

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        if "_baseline" in sql:
            return self.baselines.get(params[0])
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
    """Olay yolu. Önizlemede BOŞ kalmalı."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.fail = False

    async def __call__(self, event: str, payload: dict[str, Any] | None = None) -> None:
        if self.fail:
            raise RuntimeError("dinleyici patladı")
        self.events.append((event, dict(payload or {})))


class FakeApiError(RuntimeError):
    """Geçidin `BldApiError`'ünün testlik karşılığı — `.code` taşır."""

    def __init__(self, message: str, *, code: str = "transport") -> None:
        super().__init__(message)
        self.code = code


class FakeApi:
    """`bld.api` yeteneğinin testlik yüzü.

    `.calls` → `[(metot_adı, kwargs), …]`. Kuru prova bayrağının GERÇEKTEN
    geçirildiğini kanıtlamanın tek yolu bu listedir: geçidin varsayılanına
    güvenen bir modül `dry_run` anahtarını hiç göndermez ve bu testte
    `KeyError` ile düşer.
    """

    def __init__(self, *, sales: dict[str, Any] | None = None) -> None:
        self.sales_data = dict(sales or SALES)
        self.meta = dict(SALES_META)
        self.closed = [dict(row) for row in CLOSED_DAYS]
        self.stock_data = {"2026-08-17": json.loads(json.dumps(STOCK))}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        #: Patlaması istenen metot adları (K7 dayanıklılığı).
        self.fail: set[str] = set()
        #: `True` ise sunucu, gerçek yazma istendiğinde bile `dry_run: true`
        #: döner. Bu ekranın en pahalı arızası — üçüncü kapı bunu yakalamalı.
        self.force_dry_run = False
        #: `PUT /sales` yanıtındaki `changed` listesi zorla boşaltılsın mı.
        self.force_empty_changed = False
        #: Kuru prova isteği geçitten hiç çıkmadıysa (`sent: False`).
        self.force_not_sent = False

    def _guard(self, name: str) -> None:
        if name in self.fail:
            raise FakeApiError("BLD sunucusuna ulaşılamadı.", code="transport")

    def _envelope(self, *, dry_run: bool, data: Any) -> dict[str, Any]:
        said = True if self.force_dry_run else bool(dry_run)
        payload: dict[str, Any] = {"ok": True, "dry_run": said, "audit_id": 1701}
        if self.force_not_sent and dry_run:
            payload["sent"] = False
        if said:
            payload["would"] = data
        else:
            payload["data"] = data
        return payload

    # ------------------------------------------------------------- okumalar

    async def sales_settings(self, *, location_id: int | None = None) -> dict[str, Any]:
        self.calls.append(("sales_settings", {"location_id": location_id}))
        self._guard("sales_settings")
        return dict(self.sales_data)

    async def settings_reference(self, *, location_id: int | None = None) -> dict[str, Any]:
        self.calls.append(("settings_reference", {"location_id": location_id}))
        self._guard("settings_reference")
        return dict(self.meta)

    async def closed_days(self, *, date_from: str = "", date_to: str = "") -> dict[str, Any]:
        self.calls.append(("closed_days", {"date_from": date_from, "date_to": date_to}))
        self._guard("closed_days")
        return {"items": [dict(row) for row in self.closed],
                "meta": {"from": date_from, "to": date_to}}

    async def menu_stock(self, date: str, *,
                         location_id: int | None = None) -> dict[str, Any]:
        self.calls.append(("menu_stock", {"date": date, "location_id": location_id}))
        self._guard("menu_stock")
        found = self.stock_data.get(date)
        if found is None:
            raise FakeApiError(f"{date} için menü bulunamadı.", code="not_found")
        return json.loads(json.dumps(found))

    # -------------------------------------------------------------- yazmalar

    async def update_sales_settings(self, *, location_id: int | None = None, reason: str,
                                    actor: str, dry_run: bool, **fields: Any) -> dict[str, Any]:
        self.calls.append(("update_sales_settings",
                           {"fields": dict(fields), "location_id": location_id,
                            "reason": reason, "actor": actor, "dry_run": dry_run}))
        self._guard("update_sales_settings")
        changed = [key for key, value in fields.items() if self.sales_data.get(key) != value]
        if not (self.force_dry_run or dry_run):
            self.sales_data.update(fields)
        payload = self._envelope(dry_run=dry_run, data=dict(self.sales_data))
        payload["changed"] = [] if self.force_empty_changed else changed
        return payload

    async def pause_ordering(self, *, until: str | None = None,
                             customer_message: str | None = None,
                             location_id: int | None = None, reason: str, actor: str,
                             dry_run: bool) -> dict[str, Any]:
        self.calls.append(("pause_ordering",
                           {"until": until, "customer_message": customer_message,
                            "location_id": location_id, "reason": reason,
                            "actor": actor, "dry_run": dry_run}))
        self._guard("pause_ordering")
        if not (self.force_dry_run or dry_run):
            self.sales_data.update(ordering_enabled=False, paused_until=until,
                                   pause_reason=customer_message)
        return self._envelope(dry_run=dry_run, data={
            "ordering_enabled": False, "paused_until": until,
            "pause_reason": customer_message})

    async def resume_ordering(self, *, location_id: int | None = None, reason: str,
                              actor: str, dry_run: bool) -> dict[str, Any]:
        self.calls.append(("resume_ordering", {"location_id": location_id, "reason": reason,
                                               "actor": actor, "dry_run": dry_run}))
        self._guard("resume_ordering")
        if not (self.force_dry_run or dry_run):
            self.sales_data.update(ordering_enabled=True, paused_until=None,
                                   pause_reason=None)
        return self._envelope(dry_run=dry_run, data={
            "ordering_enabled": True, "paused_until": None, "pause_reason": None})

    async def create_closed_day(self, *, date: str, description: str | None = None,
                                reason: str, actor: str, dry_run: bool) -> dict[str, Any]:
        self.calls.append(("create_closed_day",
                           {"date": date, "description": description, "reason": reason,
                            "actor": actor, "dry_run": dry_run}))
        self._guard("create_closed_day")
        if any(row["date"] == date for row in self.closed):
            raise FakeApiError("Bu tarih zaten kapalı gün.", code="conflict")
        row = {"id": 99, "date": date, "description": description}
        if not (self.force_dry_run or dry_run):
            self.closed.append(row)
        payload = self._envelope(dry_run=dry_run, data=row)
        payload["warnings"] = []
        return payload

    async def delete_closed_day(self, date: str, *, reason: str, actor: str,
                                dry_run: bool) -> dict[str, Any]:
        self.calls.append(("delete_closed_day", {"date": date, "reason": reason,
                                                 "actor": actor, "dry_run": dry_run}))
        self._guard("delete_closed_day")
        if not any(row["date"] == date for row in self.closed):
            raise FakeApiError("Kapalı gün bulunamadı.", code="not_found")
        if not (self.force_dry_run or dry_run):
            self.closed = [row for row in self.closed if row["date"] != date]
        return self._envelope(dry_run=dry_run, data={"deleted": True, "date": date})

    async def set_menu_stock(self, date: str, *, capacity_total: int | None,
                             items: list[dict[str, Any]], location_id: int | None = None,
                             reason: str, actor: str, dry_run: bool) -> dict[str, Any]:
        self.calls.append(("set_menu_stock",
                           {"date": date, "capacity_total": capacity_total,
                            "items": [dict(row) for row in items],
                            "location_id": location_id, "reason": reason,
                            "actor": actor, "dry_run": dry_run}))
        self._guard("set_menu_stock")
        day = self.stock_data.get(date)
        if day is None:
            raise FakeApiError(f"{date} için menü bulunamadı.", code="not_found")
        if not (self.force_dry_run or dry_run):
            day["day"]["capacity"] = capacity_total
            for row in items:
                for existing in day["items"]:
                    if existing["item_id"] == row["item_id"]:
                        existing["capacity"] = row["capacity"]
        payload = self._envelope(dry_run=dry_run, data=json.loads(json.dumps(day)))
        target = payload.get("would") or payload.get("data") or {}
        target["warnings"] = [{"code": "capacity_below_sold", "item_id": 902,
                               "capacity": 60, "sold": 60}]
        return payload
