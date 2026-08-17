"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`FakeStore` SQL'i ayrıştırmaz; servisin dokunduğu dört ifadeyi (denetim satırı
yazma, denetim satırı okuma, tercih yazma, tercih okuma) tanıyacak kadarını
yapar. Amaç çekirdek depoyu taklit etmek değil, servisin DOĞRU ANDA DOĞRU
SATIRI yazdığını görmek — özellikle `result="denendi"` izinin geçit
çağrısından ÖNCE düşmesini ve fiyatın `price_kurus` SÜTUNUNA yazılmasını.

`FakeApi` `bld.api` yeteneğinin testlik yüzüdür ve YALNIZ abonelik alanının 27
metodunu taşır. `.calls` her çağrıyı sırasıyla tutar: "kuru provada uzağa
gerçek yazma gitmedi" iddiası ancak `dry_run` argümanına bakarak
kanıtlanabilir — sözleşmede kuru prova İSTEĞİ GERÇEKTEN GÖNDERİR
(`00-genel.md` §3.1), yalnız sunucu `$apply`'ı çağırmaz. `.fail` kümesine bir
metot adı atılırsa o metot patlar ve K7 (geçit düşerse ekran ayakta kalır)
sınanır.

METOT ADLARI VE İMZALARI `modules/bld_api/backend/client.py` İLE BİREBİR AYNI
OLMALIDIR. Uydurma bir ad buradaki testleri yeşil tutar ama canlıda
`AttributeError` verir; servis istisnayı K7 gereği yuttuğu için hata ekranda
"BLD'ye ulaşılamadı" diye görünür ve YANLIŞ METOT ADI DÜŞMÜŞ BİR SUNUCUDAN
AYIRT EDİLEMEZ.

FIXTURE'LAR SÖZLEŞMEDEN KOPYALANDI (`BLD/docs/control/subscriptions.md`).
Modülün kendi uydurduğu bir gövdeye karşı geçen test hiçbir şey kanıtlamaz.
"""

from __future__ import annotations

import json
from typing import Any

#: `GET /api/control/subscriptions` yanıtındaki satır — sözleşmedeki örneğin
#: aynısı.
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

#: `GET /subscriptions/{id}` — alt kayıtlar gövdenin İÇİNDEDİR, ayrı uç yok.
SUBSCRIPTION_DETAIL: dict[str, Any] = {
    "id": 18,
    "customer_id": 312,
    "customer_label": "Acme Gıda A.Ş.",
    "location_id": 1,
    "status": "active",
    "start_date": "2026-08-01",
    "end_date": None,
    "delivery_type": "delivery",
    "delivery_time_from": "11:30",
    "delivery_time_to": "12:30",
    "service_days": [1, 2, 3, 4, 5],
    "menu_mode": "daily_menu",
    "default_quantity": 20,
    "agreed_unit_price_kurus": 16000,
    "payment_mode": "prepaid_monthly",
    "lines": [],
    "delivery_points": [{"id": 9, "address_id": 704, "quantity": 20, "note": "Arka kapı"}],
    "pauses": [],
    "exceptions": [
        {"id": 77, "service_date": "2026-08-20", "skip": False,
         "quantity_override": 12, "note": "Toplantı"},
    ],
    "contract": {
        "id": 7, "subscription_id": 18, "status": "signed",
        "sent_to_phone": "5321234567", "sent_at": "2026-08-14T12:00:00Z",
        "expires_at": "2026-08-21T12:00:00Z", "signed_at": "2026-08-14T12:06:00Z",
        "otp_verified_at": "2026-08-14T12:06:00Z", "cancelled_at": None,
        "cancel_reason": None,
        "terms_snapshot": {"agreed_unit_price_kurus": 16000, "service_days": [1, 2, 3, 4, 5],
                           "default_quantity": 20, "start_date": "2026-09-01",
                           "end_date": None, "payment_mode": "prepaid_monthly"},
        "created_at": "2026-08-14T11:58:00Z",
    },
    "created_at": "2026-07-30T10:00:00Z",
    "updated_at": "2026-08-14T12:06:00Z",
}

#: `GET /{id}/calendar` — kapalı gün GÖRÜNÜR (`closed: true`).
CALENDAR_ROWS: list[dict[str, Any]] = [
    {"date": "2026-08-17", "weekday": 1, "quantity": 20, "closed": False, "note": None,
     "exception": None, "generated": True, "order_id": 8455,
     "released_at": "2026-08-17T04:00:00Z"},
    {"date": "2026-08-18", "weekday": 2, "quantity": 12, "closed": False, "note": None,
     "exception": {"skip": False, "quantity_override": 12, "note": "Toplantı"},
     "generated": False, "order_id": None, "released_at": None},
    {"date": "2026-08-30", "weekday": 7, "quantity": 20, "closed": True,
     "note": "30 Ağustos Zafer Bayramı", "exception": None, "generated": False,
     "order_id": None, "released_at": None},
]

#: `GET /{id}/runs` — ikinci satırda `order_id: null`: üretim DENENDİ ama
#: sipariş oluşmadı (kapalı gün, menü yayınlanmamış, stok dolu).
RUN_ROWS: list[dict[str, Any]] = [
    {"id": 2201, "service_date": "2026-08-17", "delivery_point_id": 9, "order_id": 8455,
     "order_number": "BLD-8455", "order_status": "hazirlaniyor", "quantity": 20,
     "released_at": "2026-08-17T04:00:00Z", "created_at": "2026-08-17T00:12:00Z"},
    {"id": 2202, "service_date": "2026-08-18", "delivery_point_id": 9, "order_id": None,
     "order_number": None, "order_status": None, "quantity": 12, "released_at": None,
     "created_at": "2026-08-18T00:12:00Z"},
]

#: `GET /requests` satırı — İLETİŞİM BİLGİSİ MASKELİ (maskeyi sunucu uygular).
REQUEST_ROW: dict[str, Any] = {
    "id": 88,
    "full_name": "Mehmet K.",
    "organization": "Acme Gıda A.Ş.",
    "telephone": "532****567",
    "email": "m***@acme.com.tr",
    "service_type": "kurumsal-catering",
    "headcount": 20,
    "frequency": "haftalik",
    "start_date": "2026-09-01",
    "location": "Ankara / Çankaya",
    "status": "yeni",
    "converted_subscription_id": None,
    "created_at": "2026-08-14T11:05:00Z",
}

#: `GET /requests/{id}` — MASKESİZ.
REQUEST_DETAIL: dict[str, Any] = {
    **REQUEST_ROW,
    "full_name": "Mehmet Kaya",
    "telephone": "5321234567",
    "email": "mehmet@acme.com.tr",
    "menu_preference": "Vejetaryen seçenek olsun",
    "kitchen_note": "Baharatsız",
    "message": "20 kişilik günlük menü istiyoruz.",
    "kvkk_accepted_at": "2026-08-14T11:04:00Z",
    "submitted_at": "2026-08-14T11:05:00Z",
    "admin_note": "",
}

#: `GET /{id}/contracts` satırı.
CONTRACT_ROW: dict[str, Any] = dict(SUBSCRIPTION_DETAIL["contract"])

#: `GET /{id}/payments` satırı — `overdue` SUNUCUDAN gelir.
PAYMENT_ROW: dict[str, Any] = {
    "id": 41, "period_start": "2026-08-01", "period_end": "2026-08-31",
    "amount_kurus": 640000, "due_date": "2026-08-05", "status": "pending",
    "method": None, "paid_at": None, "reference": None, "note": None,
    "invoice_id": None, "overdue": True, "overdue_days": 11,
}

PAYMENT_META: dict[str, Any] = {
    "total_kurus": 640000, "paid_kurus": 0, "pending_kurus": 640000,
    "overdue_kurus": 640000,
}

SERVER_TIME = "2026-08-16T09:00:00Z"

GEREKCE = "Acme Gıda ile aylık abonelik anlaşması yapıldı"


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

    def __init__(self, module_id: str = "bld_subscriptions") -> None:
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
                    "price_kurus", "detail", "created_at")
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
            rows = list(reversed(self.audit))
            # Süzgeç SQL'de `WHERE target_type = ? [AND target_id = ?]` biçiminde
            # ve son parametre daima `LIMIT`; burada aynı sırayı elle uyguluyoruz
            # ki servisin parametreleri DOĞRU SIRADA dizdiği görülsün.
            #
            # Değerler liste kavrayışının İÇİNDE değil, ÖNCE çözülür. İçeride
            # `values.pop(0)` yazmak onu satır başına bir kez çalıştırır ve
            # üçüncü satırda `IndexError` verirdi — üstelik servisin K7 kapısı
            # o istisnayı yutup "iz okunamadı, ekran ayakta" derdi ve testte
            # yalnız boş bir liste görünürdü.
            values = list(params)
            limit = int(values.pop()) if values else len(rows)
            wanted_type = values.pop(0) if ("target_type = ?" in sql and values) else None
            wanted_id = values.pop(0) if ("target_id = ?" in sql and values) else None
            if wanted_type is not None:
                rows = [row for row in rows if row["target_type"] == wanted_type]
            if wanted_id is not None:
                rows = [row for row in rows if int(row["target_id"]) == int(wanted_id)]
            return rows[:limit]
        return []

    # ------------------------------------------------------------- kolaylık

    def actions(self, action: str) -> list[dict[str, Any]]:
        return [row for row in self.audit if row["action"] == action]

    def results(self, action: str) -> list[str]:
        return [row["result"] for row in self.audit if row["action"] == action]

    def prices(self, action: str) -> list[Any]:
        return [row["price_kurus"] for row in self.audit if row["action"] == action]

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


class _FakeError(RuntimeError):
    """`BldApiError`in testlik ikizi.

    Gerçek sınıf import EDİLMEZ: servis de etmiyor (K2/K3 — başka bir modülün
    sınıfına bağlanmak, o modül yüklenmediğinde bu modülü de düşürürdü). Servis
    kodu `getattr(failure, "code", "")` ile okuyor; burada da aynı yüzey var.
    """

    def __init__(self, message: str, *, code: str = "http") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class FakeApi:
    """`bld.api` yeteneğinin testlik yüzü. Yalnız `subscriptions` alanı."""

    def __init__(self, *, rows: list[dict[str, Any]] | None = None,
                 detail: dict[str, Any] | None = None) -> None:
        self.rows = rows if rows is not None else [dict(SUBSCRIPTION_ROW)]
        self.detail_payload = dict(detail) if detail is not None else dict(
            SUBSCRIPTION_DETAIL)
        self.calendar_rows = [dict(row) for row in CALENDAR_ROWS]
        self.run_rows = [dict(row) for row in RUN_ROWS]
        self.request_rows = [dict(REQUEST_ROW)]
        self.request_payload = dict(REQUEST_DETAIL)
        self.contract_rows = [dict(CONTRACT_ROW)]
        self.payment_rows = [dict(PAYMENT_ROW)]
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()
        #: Patlayan metodun hata kodu (`BldApiError.code` karşılığı).
        self.fail_code = "transport"

    # ------------------------------------------------------------- kayıt

    # `name` KONUM-ONLY (`/`): geçidin metotları `status=` ve `note=` gibi
    # anahtar argümanlar taşıyor ve normal bir parametre olsaydı çağrı "iki
    # değer" diye patlardı — üstelik servis istisnayı yutup `ok: False`
    # döndüğü için test, kuralın çalıştığını sanarak YEŞİL kalırdı.
    def _record(self, name: str, /, *args: Any, **kwargs: Any) -> None:
        if name in self.fail:
            raise _FakeError(f"{name} patladı", code=self.fail_code)
        self.calls.append((name, args, kwargs))

    def used(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for called, _, kwargs in self.calls if called == name]

    def args_of(self, name: str) -> list[tuple[Any, ...]]:
        return [args for called, args, _ in self.calls if called == name]

    def names(self) -> list[str]:
        return [name for name, _, _ in self.calls]

    # ------------------------------------------------------ okuma — abonelik

    async def subscriptions(self, *, status: Any = None, customer_id: int | None = None,
                            q: str = "", service_day: int | None = None,
                            active_on: str = "", page: int = 1,
                            per_page: int | None = None) -> dict[str, Any]:
        self._record("subscriptions", status=status, customer_id=customer_id, q=q,
                     service_day=service_day, active_on=active_on, page=page,
                     per_page=per_page)
        return {"items": [dict(row) for row in self.rows],
                "meta": {"page": page, "per_page": per_page or 25,
                         "total": len(self.rows), "last_page": 1},
                "server_time": SERVER_TIME}

    async def subscription(self, subscription_id: int) -> dict[str, Any]:
        self._record("subscription", subscription_id)
        return {**dict(self.detail_payload), "server_time": SERVER_TIME}

    async def subscription_calendar(self, subscription_id: int, *, date_from: str = "",
                                    days: int | None = None) -> dict[str, Any]:
        self._record("subscription_calendar", subscription_id, date_from=date_from,
                     days=days)
        return {"items": [dict(row) for row in self.calendar_rows],
                "meta": {"from": date_from, "days": days,
                         "subscription_id": subscription_id},
                "server_time": SERVER_TIME}

    async def subscription_runs(self, subscription_id: int, *, date_from: str = "",
                                date_to: str = "", page: int = 1,
                                per_page: int | None = None) -> dict[str, Any]:
        self._record("subscription_runs", subscription_id, date_from=date_from,
                     date_to=date_to, page=page, per_page=per_page)
        return {"items": [dict(row) for row in self.run_rows],
                "meta": {"page": page, "per_page": per_page or 25,
                         "total": len(self.run_rows), "last_page": 1},
                "server_time": SERVER_TIME}

    # ------------------------------------------------------- okuma — talepler

    async def quote_requests(self, *, status: Any = None, q: str = "",
                             date_from: str = "", date_to: str = "", page: int = 1,
                             per_page: int | None = None) -> dict[str, Any]:
        self._record("quote_requests", status=status, q=q, date_from=date_from,
                     date_to=date_to, page=page, per_page=per_page)
        return {"items": [dict(row) for row in self.request_rows],
                "meta": {"page": page, "per_page": per_page or 25,
                         "total": len(self.request_rows), "last_page": 1},
                "server_time": SERVER_TIME}

    async def quote_request(self, request_id: int) -> dict[str, Any]:
        self._record("quote_request", request_id)
        return dict(self.request_payload)

    # ---------------------------------------------------- okuma — sözleşme/ödeme

    async def subscription_contracts(self, subscription_id: int) -> dict[str, Any]:
        self._record("subscription_contracts", subscription_id)
        return {"items": [dict(row) for row in self.contract_rows],
                "server_time": SERVER_TIME}

    async def subscription_contract(self, contract_id: int) -> dict[str, Any]:
        self._record("subscription_contract", contract_id)
        return dict(self.contract_rows[0])

    async def subscription_payments(self, subscription_id: int, *, status: Any = None,
                                    date_from: str = "",
                                    date_to: str = "") -> dict[str, Any]:
        self._record("subscription_payments", subscription_id, status=status,
                     date_from=date_from, date_to=date_to)
        return {"items": [dict(row) for row in self.payment_rows],
                "meta": dict(PAYMENT_META), "server_time": SERVER_TIME}

    # ------------------------------------------------------ yazma — abonelik

    async def create_subscription(self, *, customer_id: int, start_date: str,
                                  service_days: list[int], default_quantity: int,
                                  delivery_type: str = "delivery",
                                  menu_mode: str = "daily_menu",
                                  payment_mode: str = "prepaid_monthly",
                                  end_date: str | None = None,
                                  delivery_time_from: str | None = None,
                                  delivery_time_to: str | None = None,
                                  agreed_unit_price_kurus: int | None = None,
                                  lines: list[dict[str, Any]] | None = None,
                                  delivery_points: list[dict[str, Any]] | None = None,
                                  location_id: int | None = None, reason: str, actor: str,
                                  dry_run: bool | None = None) -> dict[str, Any]:
        self._record("create_subscription", customer_id=customer_id,
                     start_date=start_date, service_days=service_days,
                     default_quantity=default_quantity, delivery_type=delivery_type,
                     menu_mode=menu_mode, payment_mode=payment_mode, end_date=end_date,
                     delivery_time_from=delivery_time_from,
                     delivery_time_to=delivery_time_to,
                     agreed_unit_price_kurus=agreed_unit_price_kurus, lines=lines,
                     delivery_points=delivery_points, location_id=location_id,
                     reason=reason, actor=actor, dry_run=dry_run)
        if dry_run:
            return {"ok": True, "dry_run": True, "audit_id": 1900,
                    "would": {"action": "subscription.create",
                              "customer_id": customer_id, "service_days": service_days,
                              "first_service_dates": ["2026-09-01", "2026-09-02",
                                                      "2026-09-03"],
                              "monthly_estimate_kurus": 7040000}}
        return {"ok": True, "dry_run": False, "audit_id": 1900,
                "data": {**dict(self.detail_payload), "id": 19, "status": "pending",
                         "contract": None, "agreed_unit_price_kurus":
                             agreed_unit_price_kurus}}

    async def update_subscription(self, subscription_id: int, *, reason: str, actor: str,
                                  dry_run: bool | None = None,
                                  **changes: Any) -> dict[str, Any]:
        self._record("update_subscription", subscription_id, reason=reason, actor=actor,
                     dry_run=dry_run, **changes)
        if dry_run:
            return {"ok": True, "dry_run": True, "audit_id": 1901,
                    "would": {"action": "subscription.update", "fields": sorted(changes)}}
        return {"ok": True, "dry_run": False, "audit_id": 1901,
                "data": {**dict(self.detail_payload), **changes},
                # KURAL DEĞİŞİKLİĞİ ÜRETİLMİŞ SİPARİŞLERİ ETKİLEMEZ; sözleşme
                # bunu `warnings` ile söylüyor ve ekran yutmamalı.
                "warnings": [{"code": "generated_orders_unaffected",
                              "dates": ["2026-08-17"], "order_ids": [8455]}]}

    async def activate_subscription(self, subscription_id: int, *, reason: str,
                                    actor: str,
                                    dry_run: bool | None = None) -> dict[str, Any]:
        self._record("activate_subscription", subscription_id, reason=reason, actor=actor,
                     dry_run=dry_run)
        if dry_run:
            return {"ok": True, "dry_run": True, "audit_id": 1910,
                    "would": {"action": "subscription.activate"}}
        return {"ok": True, "dry_run": False, "audit_id": 1910,
                "data": {**dict(self.detail_payload), "status": "active"}}

    async def pause_subscription(self, subscription_id: int, *, start_date: str,
                                 end_date: str, pause_reason: str | None = None,
                                 reason: str, actor: str,
                                 dry_run: bool | None = None) -> dict[str, Any]:
        self._record("pause_subscription", subscription_id, start_date=start_date,
                     end_date=end_date, pause_reason=pause_reason, reason=reason,
                     actor=actor, dry_run=dry_run)
        if dry_run:
            return {"ok": True, "dry_run": True, "audit_id": 1911,
                    "would": {"action": "subscription.pause"}}
        return {"ok": True, "dry_run": False, "audit_id": 1911,
                "data": {**dict(self.detail_payload), "status": "paused"},
                # ARALIKTAKİ ÜRETİLMİŞ SİPARİŞLER OTOMATİK İPTAL EDİLMEZ.
                "warnings": [{"code": "generated_orders_in_range",
                              "order_ids": [8501, 8502],
                              "dates": ["2026-09-01", "2026-09-02"]}]}

    async def resume_subscription(self, subscription_id: int, *, reason: str, actor: str,
                                  dry_run: bool | None = None) -> dict[str, Any]:
        self._record("resume_subscription", subscription_id, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dry_run": bool(dry_run), "audit_id": 1912,
                "data": {**dict(self.detail_payload), "status": "active"}}

    async def cancel_subscription(self, subscription_id: int, *, effective_date: str,
                                  reason: str, actor: str,
                                  dry_run: bool | None = None) -> dict[str, Any]:
        self._record("cancel_subscription", subscription_id,
                     effective_date=effective_date, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dry_run": bool(dry_run), "audit_id": 1913,
                "data": {**dict(self.detail_payload), "status": "cancelled",
                         "end_date": effective_date}}

    async def create_subscription_exception(self, subscription_id: int, *,
                                            service_date: str, skip: bool = False,
                                            quantity_override: int | None = None,
                                            note: str | None = None, reason: str,
                                            actor: str,
                                            dry_run: bool | None = None) -> dict[str, Any]:
        self._record("create_subscription_exception", subscription_id,
                     service_date=service_date, skip=skip,
                     quantity_override=quantity_override, note=note, reason=reason,
                     actor=actor, dry_run=dry_run)
        return {"ok": True, "dry_run": bool(dry_run), "audit_id": 1920,
                "data": {"id": 77, "service_date": service_date, "skip": skip,
                         "quantity_override": quantity_override, "note": note}}

    async def delete_subscription_exception(self, subscription_id: int, service_date: str,
                                            *, reason: str, actor: str,
                                            dry_run: bool | None = None) -> dict[str, Any]:
        self._record("delete_subscription_exception", subscription_id, service_date,
                     reason=reason, actor=actor, dry_run=dry_run)
        return {"ok": True, "dry_run": bool(dry_run), "audit_id": 1921, "data": {}}

    async def generate_subscription_orders(self, subscription_id: int, *,
                                           service_date: str, release_now: bool = False,
                                           reason: str, actor: str,
                                           dry_run: bool | None = None) -> dict[str, Any]:
        self._record("generate_subscription_orders", subscription_id,
                     service_date=service_date, release_now=release_now, reason=reason,
                     actor=actor, dry_run=dry_run)
        created = [{"run_id": 2201, "order_id": 8455, "order_number": "BLD-8455",
                    "delivery_point_id": 9, "quantity": 20,
                    "release_at": "2026-08-17T04:00:00Z"}]
        if dry_run:
            return {"ok": True, "dry_run": True, "audit_id": 1930,
                    "would": {"action": "subscription.generate",
                              "service_date": service_date, "would_create": created}}
        return {"ok": True, "dry_run": False, "audit_id": 1930,
                "data": {"service_date": service_date, "created": created,
                         "skipped": []}}

    async def release_subscription_order(self, order_id: int, *, reason: str, actor: str,
                                         dry_run: bool | None = None) -> dict[str, Any]:
        self._record("release_subscription_order", order_id, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dry_run": bool(dry_run), "audit_id": 1940,
                "data": {"order_id": order_id, "released_at": SERVER_TIME,
                         "was_scheduled_for": "2026-08-17T04:00:00Z"}}

    # ------------------------------------------------------- yazma — talepler

    async def update_quote_request(self, request_id: int, *, reason: str, actor: str,
                                   dry_run: bool | None = None,
                                   **changes: Any) -> dict[str, Any]:
        self._record("update_quote_request", request_id, reason=reason, actor=actor,
                     dry_run=dry_run, **changes)
        return {"ok": True, "dry_run": bool(dry_run), "audit_id": 1950,
                "data": {**dict(self.request_payload), **changes}}

    async def convert_quote_request(self, request_id: int, *, customer_id: int,
                                    subscription: dict[str, Any], reason: str,
                                    actor: str,
                                    dry_run: bool | None = None) -> dict[str, Any]:
        self._record("convert_quote_request", request_id, customer_id=customer_id,
                     subscription=subscription, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dry_run": bool(dry_run), "audit_id": 1951,
                "data": {"request_id": request_id, "request_status": "kapandi",
                         # ABONELİK YİNE `pending` DOĞAR: sözleşme imzalanmadan
                         # aktifleşmez.
                         "subscription": {"id": 19, "status": "pending"}}}

    # ---------------------------------------------------- yazma — sözleşmeler

    async def create_subscription_contract(self, subscription_id: int, *, phone: str = "",
                                           expires_in_days: int = 7,
                                           send_sms: bool = True, reason: str, actor: str,
                                           dry_run: bool | None = None) -> dict[str, Any]:
        self._record("create_subscription_contract", subscription_id, phone=phone,
                     expires_in_days=expires_in_days, send_sms=send_sms, reason=reason,
                     actor=actor, dry_run=dry_run)
        return {"ok": True, "dry_run": bool(dry_run), "audit_id": 1960,
                "data": {"id": 8, "status": "sent" if send_sms else "pending",
                         "sent_to_phone": phone or "5321234567",
                         "expires_at": "2026-08-23T09:00:00Z",
                         # `sign_url` YALNIZ `send_sms=False` iken dolu.
                         "sign_url": None if send_sms else "https://bld.example/s/abc",
                         "sms_sent": bool(send_sms)}}

    async def resend_subscription_contract(self, contract_id: int, *,
                                           expires_in_days: int | None = None,
                                           reason: str, actor: str,
                                           dry_run: bool | None = None) -> dict[str, Any]:
        self._record("resend_subscription_contract", contract_id,
                     expires_in_days=expires_in_days, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dry_run": bool(dry_run), "audit_id": 1961,
                "data": {"id": contract_id, "status": "sent",
                         "expires_at": "2026-08-23T09:00:00Z"}}

    async def cancel_subscription_contract(self, contract_id: int, *, reason: str,
                                           actor: str,
                                           dry_run: bool | None = None) -> dict[str, Any]:
        self._record("cancel_subscription_contract", contract_id, reason=reason,
                     actor=actor, dry_run=dry_run)
        return {"ok": True, "dry_run": bool(dry_run), "audit_id": 1962,
                "data": {"id": contract_id, "status": "cancelled"}}

    # ------------------------------------------------------- yazma — ödemeler

    async def create_subscription_payment(self, subscription_id: int, *,
                                          period_start: str, period_end: str,
                                          due_date: str, amount_kurus: int | None = None,
                                          note: str | None = None, reason: str,
                                          actor: str,
                                          dry_run: bool | None = None) -> dict[str, Any]:
        self._record("create_subscription_payment", subscription_id,
                     period_start=period_start, period_end=period_end, due_date=due_date,
                     amount_kurus=amount_kurus, note=note, reason=reason, actor=actor,
                     dry_run=dry_run)
        # KURU PROVA HESABI GERÇEKTEN YAPAR: yöneticinin borcu yazmadan önce
        # görmesi gereken tam olarak budur.
        body = {"id": 42, "period_start": period_start, "period_end": period_end,
                "amount_kurus": amount_kurus if amount_kurus is not None else 640000,
                "amount_source": "manual" if amount_kurus is not None else "calculated",
                "order_count": 40, "due_date": due_date, "status": "pending"}
        if dry_run:
            return {"ok": True, "dry_run": True, "audit_id": 1970, "would": body}
        return {"ok": True, "dry_run": False, "audit_id": 1970, "data": body}

    async def mark_subscription_payment_paid(self, payment_id: int, *, method: str,
                                             paid_at: str = "", reference: str = "",
                                             create_invoice: bool = False, reason: str,
                                             actor: str,
                                             dry_run: bool | None = None) -> dict[str, Any]:
        self._record("mark_subscription_payment_paid", payment_id, method=method,
                     paid_at=paid_at, reference=reference, create_invoice=create_invoice,
                     reason=reason, actor=actor, dry_run=dry_run)
        return {"ok": True, "dry_run": bool(dry_run), "audit_id": 1980,
                "data": {"id": payment_id, "status": "paid", "method": method,
                         "amount_kurus": 640000, "paid_at": paid_at or SERVER_TIME,
                         "invoice_id": 45 if create_invoice else None,
                         "invoice_no": "BLD-2026-000045" if create_invoice else None}}


def make_service(*, api: FakeApi | None = None, store: FakeStore | None = None,
                 config: dict[str, Any] | None = None, bus: FakeBus | None = None) -> Any:
    """Servisi sahte bağlamla kurar. Testlerin tek kurulum yolu."""
    from bld_subscriptions_backend.service import SubscriptionsService

    settings = {"dry_run_default": False, "page_size": 25, "calendar_days": 30,
                "expires_in_days": 7}
    settings.update(config or {})
    return SubscriptionsService(
        api=api or FakeApi(),
        store=store or FakeStore(),
        log=FakeLog(),
        config=settings,
        publish=bus,
    )
