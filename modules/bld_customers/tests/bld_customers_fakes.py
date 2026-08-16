"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`FakeStore` SQL'i ayrıştırmaz; servisin yazdığı dört ifadeyi (KVKK erişim
satırı, yazma izi satırı, tercih yazma, tercih okuma) tanıyacak kadarını yapar.
Amaç çekirdek depoyu taklit etmek değil, servisin DOĞRU ANDA DOĞRU SATIRI
yazdığını görmek — özellikle her okumanın bir erişim satırı bırakmasını ve
yazmalardaki `result="denendi"` izinin geçit çağrısından ÖNCE düşmesini.

`FakeApi` `bld.api` yeteneğinin testlik yüzüdür. METOT ADLARI VE İMZALARI
`modules/bld_api/backend/client.py` İLE BİREBİR AYNIDIR. Uydurma bir ad
(`get_customer` gibi) buradaki testleri yeşil tutar ama canlıda
`AttributeError` verir — ve servis istisnayı K7 gereği yuttuğu için hata
ekranda "BLD'ye ulaşılamadı" diye görünür: yanlış metot adı, düşmüş bir
sunucudan AYIRT EDİLEMEZ.

`.calls` her çağrıyı sırasıyla tutar: "engellenen istek uzağa hiç gitmedi"
iddiası ancak bu liste boş kalarak kanıtlanabilir. `.fail` kümesine bir metot
adı atılırsa o metot patlar ve K7 (geçit düşerse ekran ayakta kalır) sınanır.

SÖZLEŞMEDEN KOPYALANMIŞ GÖVDELER. `CUSTOMER_ROW`, `CUSTOMER_DETAIL`,
`ORDER_ROW`, `SUBSCRIPTION_ROW`, `ADDRESS_ROW` ve `SMS_ROW`
`BLD/docs/control/customers.md`, `orders.md`, `subscriptions.md` ve `sms.md`
içindeki örnek gövdelerdir; kısaltılmadı. Modülün kendi uydurduğu bir gövdeye
karşı geçen test hiçbir şey kanıtlamaz.
"""

from __future__ import annotations

import json
from typing import Any

#: `customers.md` → `GET /` örnek satırı.
CUSTOMER_ROW: dict[str, Any] = {
    "customer_id": 312,
    "first_name": "Mehmet",
    "last_name": "Kaya",
    "email": "mehmet.kaya@acme.com.tr",
    "telephone": "5321234567",
    "status": True,
    "is_activated": True,
    "account_type": "corporate",
    "org_name": "Acme Gıda A.Ş.",
    "order_count": 128,
    "last_order_at": "2026-08-15T18:04:00Z",
    "subscription_count": 1,
    "created_at": "2026-03-02T09:11:00Z",
}

#: `customers.md` → `GET /{id}` örnek gövdesi (`stats` dâhil).
CUSTOMER_DETAIL: dict[str, Any] = {
    **CUSTOMER_ROW,
    "tax_office": "Çankaya",
    "tax_no": "1234567890",
    "contact_person": "Mehmet Kaya",
    "org_phone": "3124445566",
    "last_login": "2026-08-15T17:50:00Z",
    "stats": {
        "order_count": 128,
        "cancelled_order_count": 3,
        "total_spent_kurus": 27648000,
        "first_order_at": "2026-03-05T10:00:00Z",
        "last_order_at": "2026-08-15T18:04:00Z",
        "active_subscription_count": 1,
        "unpaid_total_kurus": 640000,
        "address_count": 2,
    },
}

#: `orders.md` → `GET /` satırı. `customers.md` bu biçimi AYNEN kullanıyor.
ORDER_ROW: dict[str, Any] = {
    "id": 8421,
    "order_number": "BLD-8421",
    "status": "hazirlaniyor",
    "service_date": "2026-08-16",
    "requested_at": "2026-08-16T09:30:00Z",
    "delivery_type": "delivery",
    "customer_id": 312,
    "customer_name": "Acme Gıda — Mehmet Kaya",
    "customer_phone": "5321234567",
    "item_count": 12,
    "total_kurus": 216000,
    "payment_method": "online",
    "payment_status": "paid",
    "is_subscription": False,
    "subscription_id": None,
    "revision_no": 1,
    "has_invoice": False,
    "created_at": "2026-08-15T18:04:00Z",
    "updated_at": "2026-08-16T06:20:00Z",
}

#: `subscriptions.md` → `GET /` satırı.
SUBSCRIPTION_ROW: dict[str, Any] = {
    "id": 18,
    "customer_id": 312,
    "customer_label": "Acme Gıda A.Ş.",
    "status": "active",
    "start_date": "2026-08-01",
    "end_date": None,
    "service_days": [1, 2, 3, 4, 5],
    "menu_mode": "daily_menu",
    "default_quantity": 20,
    "agreed_unit_price_kurus": 16000,
    "payment_mode": "prepaid_monthly",
    "delivery_point_count": 1,
    "contract_status": "signed",
    "next_service_date": "2026-08-17",
    "unpaid_periods": 1,
    "unpaid_total_kurus": 640000,
}

#: `customers.md` → `GET /{id}/addresses` satırı.
ADDRESS_ROW: dict[str, Any] = {
    "address_id": 704,
    "label": "Merkez ofis",
    "line_1": "Kızılırmak Mah. 1443. Cad. No:12",
    "line_2": "Kat 4",
    "city": "Ankara",
    "district": "Çankaya",
    "neighbourhood": "Kızılırmak",
    "postcode": "06520",
    "latitude": 39.9042,
    "longitude": 32.8597,
    "is_default": True,
}

#: `sms.md` → `GET /log` satırı. TELEFON ZATEN MASKELİ GELİR.
SMS_ROW: dict[str, Any] = {
    "id": 9912,
    "template_key": "order_created",
    "phone": "532****567",
    "customer_id": 312,
    "order_id": 8421,
    "subscription_id": None,
    "body": "Sayın Mehmet K., 16.08.2026 tarihli BLD-8421 numaralı siparişiniz alındı…",
    "segments": 2,
    "status": "sent",
    "error": None,
    "provider_ref": "NG-77219043",
    "context": "auto",
    "sent_at": "2026-08-15T18:04:12Z",
}

ACTOR = "Ayşe Yılmaz"
REASON = "Müşteri telefon numarasını değiştirdi, kayıt güncellendi"


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

    def levels(self, level: str) -> list[str]:
        return [message for kind, message, _ in self.records if kind == level]


class FakeStore:
    """`ModuleStore` yüzeyi. Satırları bellekte tutar."""

    def __init__(self, module_id: str = "bld_customers") -> None:
        self.module_id = module_id
        self.access: list[dict[str, Any]] = []
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
        if "_access" in text and text.startswith("INSERT"):
            keys = ("actor", "action", "scope", "customer_id", "filters", "result",
                    "error", "created_at")
            self.access.append(dict(zip(keys, params, strict=False)))
        elif "_audit" in text and text.startswith("INSERT"):
            keys = ("customer_id", "action", "reason", "actor", "result", "detail",
                    "created_at")
            self.audit.append(dict(zip(keys, params, strict=False)))
        elif "_prefs" in text:
            self.prefs[str(params[0])] = str(params[1])

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        return None

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if "_prefs" in sql:
            return [{"key": key, "value": value} for key, value in self.prefs.items()]
        if "_access" in sql:
            return self._select(self.access, sql, params)
        if "_audit" in sql:
            return self._select(self.audit, sql, params)
        return []

    @staticmethod
    def _select(source: list[dict[str, Any]], sql: str,
                params: tuple[Any, ...]) -> list[dict[str, Any]]:
        """`WHERE` ve `LIMIT`'in KABA taklidi — SQL ayrıştırılmaz.

        Yalnız servisin ürettiği iki koşul (`customer_id = ?`, `actor = ?`) ve
        son parametredeki `LIMIT` tanınır. Tam bir motor taklit etmek gereksiz;
        gereken şey, servis koşulu EKLEMEYİ UNUTURSA testin düşmesi. Süzgeci
        burada da yok sayan bir sahte depo, unutulmuş bir `WHERE` ile yeşil
        kalırdı ve o eksik koşul canlıda "bu müşterinin erişim geçmişi"
        ekranını BÜTÜN müşterilerin geçmişiyle doldururdu.
        """
        rows = list(reversed(source))
        values = list(params)
        limit = values.pop() if values else len(rows)
        # DEĞER ÖNCE ÇEKİLİR: `values.pop(0)` doğrudan bir liste üretecinin
        # içinde çağrılırsa HER SATIRDA bir kez çalışır ve ikinci satırda liste
        # boşalır. Hata da servisin `except`ine düşüp "depo okunamadı"ya
        # dönüşür — yani sahte deponun kusuru, servisin kusuru gibi görünür.
        if "customer_id = ?" in sql:
            wanted = int(values.pop(0))
            rows = [row for row in rows if int(row["customer_id"]) == wanted]
        if "actor = ?" in sql:
            wanted_actor = str(values.pop(0))
            rows = [row for row in rows if str(row["actor"]) == wanted_actor]
        return [{"id": index, **row}
                for index, row in enumerate(rows[:int(limit)], 1)]

    # ------------------------------------------------------------- kolaylık

    def scopes(self) -> list[str]:
        """Yazılan erişim satırlarının kapsamları — okuma sırasıyla."""
        return [str(row["scope"]) for row in self.access]

    def read_actions(self) -> list[str]:
        return [str(row["action"]) for row in self.access]

    def filters(self, index: int) -> dict[str, Any]:
        return json.loads(self.access[index]["filters"])

    def results(self, action: str) -> list[str]:
        return [str(row["result"]) for row in self.audit if row["action"] == action]

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


class FakeApi:
    """`bld.api` yeteneğinin testlik yüzü. Yalnız müşteri metotları var.

    `customer_subscriptions`, `customer_addresses` ve `sms_log` gerçek geçitte
    `{"items": [...], "meta": {...}}` döner (`BldApi._list`); burada da öyle
    döner. `customer` ise `_object` ile açılmış DÜZ SÖZLÜK verir — iki farklı
    şekli taklit etmek, servisin ikisini de doğru okuduğunu görmenin tek yolu.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()
        #: `fail` içindeki ad kaç BAŞARILI çağrıdan sonra patlasın. 0 = hemen.
        self.fail_after = 0
        #: Hata nesnesine takılacak geçit kodu (`BldApiError.code`).
        self.fail_code = "transport"

        self.customer_rows: list[dict[str, Any]] = [dict(CUSTOMER_ROW)]
        self.customer_meta: dict[str, Any] = {"page": 1, "per_page": 25, "total": 214,
                                              "last_page": 9}
        self.detail_row: dict[str, Any] = dict(CUSTOMER_DETAIL)
        self.order_rows: list[dict[str, Any]] = [dict(ORDER_ROW)]
        self.subscription_rows: list[dict[str, Any]] = [dict(SUBSCRIPTION_ROW)]
        self.address_rows: list[dict[str, Any]] = [dict(ADDRESS_ROW)]
        self.sms_rows: list[dict[str, Any]] = [dict(SMS_ROW)]
        self.sms_meta: dict[str, Any] = {"page": 1, "per_page": 25, "total": 4218,
                                         "last_page": 169, "segment_total": 6840}
        #: Yazma yanıtları. Gerçek geçit yazdığı kaydı geri veriyor.
        self.disable_warnings: list[dict[str, Any]] = []

    # ------------------------------------------------------------- kayıt

    def _record(self, name: str, /, *args: Any, **kwargs: Any) -> None:
        if name in self.fail and len(self.args_of(name)) >= self.fail_after:
            failure = RuntimeError(f"{name} patladı")
            failure.code = self.fail_code  # type: ignore[attr-defined]
            raise failure
        self.calls.append((name, args, kwargs))

    def used(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for called, _, kwargs in self.calls if called == name]

    def args_of(self, name: str) -> list[tuple[Any, ...]]:
        return [args for called, args, _ in self.calls if called == name]

    def names(self) -> list[str]:
        return [name for name, _, _ in self.calls]

    def writes(self) -> list[str]:
        """Yazan çağrıların adları. Engellenen istekte BOŞ kalmalı."""
        yazan = ("update_customer", "disable_customer", "enable_customer")
        return [name for name, _, _ in self.calls if name in yazan]

    # ------------------------------------------------------------- okuma

    async def customers(self, *, actor: str, q: str = "", status: str = "",
                        has_subscription: bool | None = None, sort: str = "",
                        direction: str = "", page: int = 1,
                        per_page: int | None = None) -> dict[str, Any]:
        self._record("customers", actor=actor, q=q, status=status,
                     has_subscription=has_subscription, sort=sort, direction=direction,
                     page=page, per_page=per_page)
        return {"items": [dict(row) for row in self.customer_rows],
                "meta": dict(self.customer_meta)}

    async def customer(self, customer_id: int, *, actor: str) -> dict[str, Any]:
        self._record("customer", customer_id, actor=actor)
        return dict(self.detail_row)

    async def customer_orders(self, customer_id: int, *, actor: str, status: Any = None,
                              date_from: str = "", date_to: str = "", page: int = 1,
                              per_page: int | None = None) -> dict[str, Any]:
        self._record("customer_orders", customer_id, actor=actor, status=status,
                     date_from=date_from, date_to=date_to, page=page, per_page=per_page)
        return {"items": [dict(row) for row in self.order_rows],
                "meta": {"page": page, "per_page": per_page or 25, "total": 128,
                         "last_page": 6}}

    async def customer_subscriptions(self, customer_id: int, *,
                                     actor: str) -> dict[str, Any]:
        self._record("customer_subscriptions", customer_id, actor=actor)
        return {"items": [dict(row) for row in self.subscription_rows], "meta": {}}

    async def customer_addresses(self, customer_id: int, *, actor: str) -> dict[str, Any]:
        self._record("customer_addresses", customer_id, actor=actor)
        return {"items": [dict(row) for row in self.address_rows], "meta": {}}

    async def sms_log(self, *, phone: str = "", template_key: str = "", status: str = "",
                      context: str = "", customer_id: int | None = None,
                      date_from: str = "", date_to: str = "", page: int = 1,
                      per_page: int | None = None) -> dict[str, Any]:
        self._record("sms_log", phone=phone, template_key=template_key, status=status,
                     context=context, customer_id=customer_id, date_from=date_from,
                     date_to=date_to, page=page, per_page=per_page)
        return {"items": [dict(row) for row in self.sms_rows], "meta": dict(self.sms_meta)}

    # ------------------------------------------------------------- yazma

    async def update_customer(self, customer_id: int, *, reason: str, actor: str,
                              dry_run: bool | None = None,
                              **fields: Any) -> dict[str, Any]:
        self._record("update_customer", customer_id, reason=reason, actor=actor,
                     dry_run=dry_run, **fields)
        return {"ok": True, "dry_run": bool(dry_run), "audit_id": 2001,
                "data": {**self.detail_row, **fields},
                "changed": sorted(fields)}

    async def disable_customer(self, customer_id: int, *, reason: str, actor: str,
                               dry_run: bool | None = None) -> dict[str, Any]:
        self._record("disable_customer", customer_id, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dry_run": bool(dry_run), "audit_id": 2010,
                "data": {"customer_id": customer_id, "status": False},
                "warnings": [dict(item) for item in self.disable_warnings]}

    async def enable_customer(self, customer_id: int, *, reason: str, actor: str,
                              dry_run: bool | None = None) -> dict[str, Any]:
        self._record("enable_customer", customer_id, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dry_run": bool(dry_run), "audit_id": 2011,
                "data": {"customer_id": customer_id, "status": True}}


def build(config: dict[str, Any] | None = None) -> tuple[Any, FakeApi, FakeStore,
                                                         FakeBus, FakeLog]:
    """Servisi sahte bağlamla kurar. Testlerin ortak açılışı."""
    from bld_customers_backend.service import CustomersService

    api = FakeApi()
    store = FakeStore()
    bus = FakeBus()
    log = FakeLog()
    service = CustomersService(api=api, store=store, log=log,
                               config={"dry_run_default": False, **(config or {})},
                               publish=bus)
    return service, api, store, bus, log
