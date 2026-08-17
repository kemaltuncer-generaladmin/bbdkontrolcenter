"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`FakeStore` SQL'i ayrıştırmaz; servisin dokunduğu iki ifadeyi (tercih yazma,
tercih okuma) tanıyacak kadarını yapar. Bu modülde DENETİM TABLOSU YOK —
sözleşme bu alanda yazma ucu saymıyor ve okumaları denetlemiyor
(`BLD/docs/control/dashboard.md`), dolayısıyla taklit edilecek bir iz de yok.

`FakeApi` `bld.api` yeteneğinin testlik yüzüdür ve panel yolunun YALNIZ İKİ
metodunu taşır: `dashboard_overview` (yedi blok) ve `order_list` (canlı akış).
`.calls` her çağrıyı sırasıyla tutar. `.fail` kümesine bir metot adı atılırsa o
metot patlar ve K7 (geçit düşerse ekran ayakta kalır) sınanır; iki metodun AYRI
AYRI patlatılabilmesi bu dosyanın asıl işi — akışın düşmesi özeti düşürmemeli.

FIXTURE'LAR SÖZLEŞMEDEN KOPYALANDI (`BLD/docs/control/dashboard.md` ve
`orders.md`). Modülün kendi uydurduğu bir gövdeye karşı geçen test hiçbir şey
kanıtlamaz.
"""

from __future__ import annotations

from typing import Any

SERVER_TIME = "2026-08-16T09:00:00Z"

#: `GET /api/control/dashboard/overview` → `data` — sözleşmedeki örneğin aynısı.
OVERVIEW: dict[str, Any] = {
    "date": "2026-08-16",
    "location_id": 1,
    "sales": {
        "ordering_enabled": True,
        "paused_until": None,
        "busy": False,
        "cutoff_time": "08:00",
        "cutoff_at": "2026-08-17T05:00:00Z",
        "cutoff_passed_for_today": True,
        "seconds_to_next_cutoff": 72000,
        "next_cutoff_date": "2026-08-17",
    },
    "orders": {
        "by_status": {
            "yeni": 4,
            "onaylandi": 9,
            "hazirlaniyor": 12,
            "hazir": 3,
            "yolda": 2,
        },
        "active": 30,
        "delivered_today": 41,
        "cancelled_today": 2,
        "created_today": 73,
        "late": 1,
        "revenue_today_kurus": 13140000,
        "unreleased_subscription_orders": 0,
    },
    "capacity": {
        "menu_published": True,
        "capacity_total": 120,
        "sold_total": 86,
        "sold_orders": 66,
        "sold_subscriptions": 20,
        "remaining_total": 34,
        "fill_rate": 0.72,
        "blocked_items": [
            {"menu_id": 27, "name": "Tavuk Sote", "capacity": 60, "sold": 60},
        ],
    },
    "subscriptions": {
        "active": 7,
        "pending": 2,
        "paused": 1,
        "portions_today": 20,
        "contracts_awaiting_signature": 1,
        "unpaid_periods": 3,
        "unpaid_total_kurus": 1920000,
        "overdue_periods": 1,
        "overdue_total_kurus": 640000,
    },
    "devices": {
        "total": 2,
        "online": 1,
        "revoked": 0,
        "printer_fault": 1,
        "queue_pending": 4,
        "queue_failed": 2,
        "queue_oldest_age_minutes": 41,
    },
    "monitor": {
        "open_total": 18,
        "critical_open": 1,
        "error_open": 2,
        "warning_open": 3,
        "health_status": "degraded",
    },
    "pending_tasks": [
        {
            "code": "menu_missing",
            "level": "critical",
            "title": "Yarının menüsü girilmemiş",
            "detail": "17 Ağustos için yayınlanmış menü yok. Kesim saatine 20 saat kaldı.",
            "count": 1,
            "link": "/menu/days/2026-08-17",
        },
        {
            "code": "quote_requests_new",
            "level": "warning",
            "title": "Cevaplanmamış teklif talebi",
            "detail": "3 talep 'yeni' durumunda bekliyor.",
            "count": 3,
            "link": "/subscriptions/requests?status=yeni",
        },
        {
            "code": "printer_fault",
            "level": "warning",
            "title": "Yazıcı arızası",
            "detail": "Mutfak Kasa 1 yazıcıya ulaşamıyor, kuyrukta 4 iş var.",
            "count": 1,
            "link": "/monitor/devices",
        },
    ],
}

#: Menü yayınlanmamış gün. `capacity` alanları `null` DÖNER, sıfır değil —
#: ayrımı sınamanın tek yolu sözleşmenin bu dalını da fixture'a koymak.
UNPUBLISHED_CAPACITY: dict[str, Any] = {
    "menu_published": False,
    "capacity_total": None,
    "sold_total": None,
    "sold_orders": None,
    "sold_subscriptions": None,
    "remaining_total": None,
    "fill_rate": None,
    "blocked_items": [],
}

#: `GET /api/control/orders` yanıtındaki satır — `orders.md` örneğinin aynısı.
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

    def __init__(self, module_id: str = "bld_dashboard") -> None:
        self.module_id = module_id
        self.prefs: dict[str, str] = {}
        #: `True` ise her yazma/okuma patlar — "tercih okunamazsa iş durmasın" (K7).
        self.broken = False

    def table(self, name: str) -> str:
        return f"mod_{self.module_id}_{name}"

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        if self.broken:
            raise RuntimeError("depo yazılamıyor")
        if "_prefs" in sql:
            self.prefs[str(params[0])] = str(params[1])

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        return None

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if self.broken:
            raise RuntimeError("depo okunamıyor")
        if "_prefs" in sql:
            return [{"key": key, "value": value} for key, value in self.prefs.items()]
        return []


class FakeApi:
    """`bld.api` yeteneğinin testlik yüzü. Yalnız bu ekranın iki metodu.

    METOT ADLARI VE İMZALARI `modules/bld_api/backend/client.py` İLE BİREBİR
    AYNI OLMALIDIR. Uydurma bir ad (`overview()` gibi — o KDS yolunun metodudur)
    buradaki testleri yeşil tutar ama canlıda `AttributeError` verir; servis
    istisnayı K7 gereği yuttuğu için hata ekranda "BLD'ye ulaşılamadı" diye
    görünür ve YANLIŞ METOT ADI DÜŞMÜŞ BİR SUNUCUDAN AYIRT EDİLEMEZ.
    """

    def __init__(self, *, overview: dict[str, Any] | None = None,
                 rows: list[dict[str, Any]] | None = None) -> None:
        self.overview_payload = dict(OVERVIEW) if overview is None else dict(overview)
        self.rows = [dict(ORDER_ROW)] if rows is None else [dict(row) for row in rows]
        #: Sunucu önbellek açarsa yanıt `meta.cached_at` taşır (isteğe bağlı).
        self.meta: dict[str, Any] = {}
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()
        #: Patlayan metodun hata kodu (`BldApiError.code` karşılığı).
        self.fail_code = "transport"

    # ------------------------------------------------------------- kayıt

    # `name` KONUM-ONLY (`/`): geçidin metotları `date=` ve `status=` gibi
    # anahtar argümanlar taşıyor ve normal bir parametre olsaydı çağrı "iki
    # değer" diye patlardı — üstelik servis istisnayı yutup `ok: True,
    # connected: False` döndüğü için test, kuralın çalıştığını sanarak YEŞİL
    # kalırdı.
    def _record(self, name: str, /, *args: Any, **kwargs: Any) -> None:
        if name in self.fail:
            raise _FakeError(f"{name} patladı", code=self.fail_code)
        self.calls.append((name, args, kwargs))

    def used(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for called, _, kwargs in self.calls if called == name]

    def names(self) -> list[str]:
        return [name for name, _, _ in self.calls]

    # ------------------------------------------------------------- okuma

    async def dashboard_overview(self, *, location_id: int | None = None,
                                 date: str = "") -> dict[str, Any]:
        self._record("dashboard_overview", location_id=location_id, date=date)
        payload: dict[str, Any] = {"data": dict(self.overview_payload),
                                   "server_time": SERVER_TIME}
        if self.meta:
            payload["meta"] = dict(self.meta)
        return payload

    async def order_list(self, *, service_date: str = "", date_from: str = "",
                         date_to: str = "", status: Any = None, delivery_type: str = "",
                         customer_id: int | None = None, subscription_id: int | None = None,
                         source: str = "", q: str = "", page: int = 1,
                         per_page: int | None = None) -> dict[str, Any]:
        self._record("order_list", service_date=service_date, date_from=date_from,
                     date_to=date_to, status=status, delivery_type=delivery_type,
                     customer_id=customer_id, subscription_id=subscription_id,
                     source=source, q=q, page=page, per_page=per_page)
        return {"items": [dict(row) for row in self.rows],
                "meta": {"page": page, "per_page": per_page or 25,
                         "total": len(self.rows), "last_page": 1},
                "server_time": SERVER_TIME}


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


def make_service(*, api: FakeApi | None = None, store: FakeStore | None = None,
                 config: dict[str, Any] | None = None) -> Any:
    """Servisi sahte bağlamla kurar. Testlerin tek kurulum yolu."""
    from bld_dashboard_backend.service import DashboardService

    settings: dict[str, Any] = {"poll_seconds": 30, "location_id": 0,
                                "flow_enabled": True, "flow_limit": 10}
    settings.update(config or {})
    return DashboardService(
        api=api or FakeApi(),
        store=store or FakeStore(),
        log=FakeLog(),
        config=settings,
    )
