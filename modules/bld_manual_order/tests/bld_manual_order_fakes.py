"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

Bu modülün YEREL TABLOSU YOKTUR, bu yüzden `FakeStore` de yoktur: servis
`ctx.store`a hiç dokunmuyor (`module.py` başlığı). Sahte bir depo kurmak,
olmayan bir bağımlılığı varmış gibi göstermek olurdu.

`FakeApi` `bld.api` yeteneğinin testlik yüzüdür ve YALNIZ bu ekranın kullandığı
altı metodu taşır. `.calls` her çağrıyı sırasıyla tutar: "kuru provada uzağa
gerçek yazma gitmedi" iddiası ancak `dry_run` argümanına bakarak
kanıtlanabilir — sözleşmede kuru prova İSTEĞİ GERÇEKTEN GÖNDERİR
(`00-genel.md` §3.1), yalnız sunucu `$apply`'ı çağırmaz. `.fail` kümesine bir
metot adı atılırsa o metot patlar ve K7 (geçit düşerse ekran ayakta kalır)
sınanır.

METOT ADLARI VE İMZALARI `modules/bld_api/backend/client.py` İLE BİREBİR AYNI
OLMALIDIR. Uydurma bir ad buradaki testleri yeşil tutar ama canlıda
`AttributeError` verir; servis istisnayı K7 gereği yuttuğu için hata ekranda
"BLD'ye ulaşılamadı" diye görünür ve YANLIŞ METOT ADI DÜŞMÜŞ BİR SUNUCUDAN
AYIRT EDİLEMEZ. Sipariş açma metodu (`create_order`) geçidin donmuş tablosunda
bu ekranla PARALEL yazıldı; eski bir `bld_api` yüklüyse metot hiç olmayabilir.
Servis onu `getattr` ile arıyor ve bulamadığında açık bir cümle döndürüyor —
`FakeApiWithoutCreate` tam olarak o hâli sınar.

FIXTURE'LAR SÖZLEŞMEDEN KOPYALANDI (`BLD/docs/control/orders.md`,
`customers.md`, `settings.md`, `menu.md`). Modülün kendi uydurduğu bir gövdeye
karşı geçen test hiçbir şey kanıtlamaz.
"""

from __future__ import annotations

from typing import Any

SERVER_TIME = "2026-08-17T05:00:00Z"          # = Europe/Istanbul 08:00
SERVER_TIME_LATE = "2026-08-17T12:00:00Z"     # = Europe/Istanbul 15:00
TODAY = "2026-08-17"

#: `POST /api/control/orders` yanıtının `data` bloğu — `GET /` satırının aynısı.
ORDER_ROW: dict[str, Any] = {
    "id": 8422,
    "order_number": "BLD-8422",
    "status": "onaylandi",
    "service_date": TODAY,
    "requested_at": "2026-08-17T05:05:00Z",
    "delivery_type": "delivery",
    "customer_id": 312,
    "customer_name": "Acme Gıda — Mehmet Kaya",
    "customer_phone": "5321234567",
    "item_count": 12,
    "total_kurus": 216000,
    "payment_method": "cash",
    "payment_status": "pending",
    "is_subscription": False,
    "subscription_id": None,
    "revision_no": 1,
    "has_invoice": False,
    "created_at": "2026-08-17T05:05:00Z",
    "updated_at": "2026-08-17T05:05:00Z",
}

#: `GET /api/control/customers` satırı.
CUSTOMER_ROW: dict[str, Any] = {
    "id": 312,
    "first_name": "Mehmet",
    "last_name": "Kaya",
    "email": "tel-5321234567@bld.invalid",
    "telephone": "5321234567",
    "bld_org_name": "Acme Gıda",
    "status": True,
}

#: `GET /api/control/products` satırı (seçici taraması).
PRODUCT_ROW: dict[str, Any] = {
    "menu_id": 88,
    "name": "Günün Menüsü",
    "price_kurus": 18000,
    "category": "Menüler",
    "status": True,
    "sold_out": False,
}

#: `GET /api/control/settings/sales` gövdesi (kısaltılmış ama alan adları aynı).
SALES_SETTINGS: dict[str, Any] = {
    "data": {
        "location_id": 1,
        "ordering_enabled": True,
        "paused_until": None,
        "pause_reason": None,
        "order_cutoff": "08:00",
        "max_lookahead_days": 7,
        "min_order_total_kurus": 15000,
        "delivery_fee_kurus": 2500,
        "payment_methods": ["online", "cash"],
    },
    "meta": {"available_payment_methods": ["online", "cash"]},
    "server_time": SERVER_TIME,
}

#: `GET /api/control/menu/days/{date}` — yalnız ekranın okuduğu alanlar.
MENU_DAY: dict[str, Any] = {
    "data": {"date": TODAY, "title": "Mercimek · Tavuk Sote", "cutoff_time": None},
    "server_time": SERVER_TIME,
}

#: `GET /api/control/menu/days/{date}/stock` — sözleşmedeki örneğin aynısı.
MENU_STOCK: dict[str, Any] = {
    "data": {
        "date": TODAY,
        "day": {"capacity": 120, "sold": 86, "sold_orders": 66,
                "sold_subscriptions": 20, "remaining": 34, "full": False},
        "items": [
            {"item_id": 901, "menu_id": 88, "name": "Günün Menüsü",
             "capacity": None, "sold": 86, "remaining": None,
             "full": False, "sold_out": False},
            {"item_id": 902, "menu_id": 27, "name": "Tavuk Sote",
             "capacity": 60, "sold": 58, "remaining": 2,
             "full": False, "sold_out": False},
        ],
        "blocking": {"day": False, "items": []},
    },
    "server_time": SERVER_TIME,
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

    def levels(self) -> list[str]:
        return [level for level, _, _ in self.records]


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
    """`bld.api` yeteneğinin testlik yüzü. Bu ekranın kullandığı altı metot."""

    def __init__(self, *, customers_rows: list[dict[str, Any]] | None = None,
                 products_rows: list[dict[str, Any]] | None = None,
                 settings: dict[str, Any] | None = None,
                 menu_day: dict[str, Any] | None = None,
                 stock: dict[str, Any] | None = None) -> None:
        self.customer_rows = customers_rows if customers_rows is not None \
            else [dict(CUSTOMER_ROW)]
        self.product_rows = products_rows if products_rows is not None \
            else [dict(PRODUCT_ROW)]
        self.settings_payload = dict(settings) if settings is not None \
            else _deep(SALES_SETTINGS)
        self.menu_day_payload = dict(menu_day) if menu_day is not None \
            else _deep(MENU_DAY)
        self.stock_payload = dict(stock) if stock is not None else _deep(MENU_STOCK)
        self.order_payload: dict[str, Any] = {
            "ok": True, "dry_run": False, "audit_id": 1830,
            "data": dict(ORDER_ROW),
            "customer": {"id": 312, "created": False},
            "warnings": [],
        }
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()
        #: Patlayan metodun hata kodu (`BldApiError.code` karşılığı).
        self.fail_code = "transport"

    # ------------------------------------------------------------- kayıt

    # `name` KONUM-ONLY (`/`): geçidin metotları `status=` ve `date=` gibi
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

    # ------------------------------------------------------------- okuma

    async def customers(self, *, actor: str, q: str = "", status: str = "",
                        has_subscription: bool | None = None, sort: str = "",
                        direction: str = "", page: int = 1,
                        per_page: int | None = None) -> dict[str, Any]:
        self._record("customers", actor=actor, q=q, status=status,
                     has_subscription=has_subscription, sort=sort, direction=direction,
                     page=page, per_page=per_page)
        return {"items": [dict(row) for row in self.customer_rows],
                "meta": {"page": page, "per_page": per_page or 25,
                         "total": len(self.customer_rows), "last_page": 1},
                "server_time": SERVER_TIME}

    async def product_picker(self, *, only_active: bool = True) -> dict[str, Any]:
        self._record("product_picker", only_active=only_active)
        return {"items": [dict(row) for row in self.product_rows],
                "total": len(self.product_rows), "pages": 1, "truncated": False}

    async def sales_settings(self, *, location_id: int | None = None) -> dict[str, Any]:
        self._record("sales_settings", location_id=location_id)
        return _deep(self.settings_payload)

    async def menu_day(self, date: str, *,
                       location_id: int | None = None) -> dict[str, Any]:
        self._record("menu_day", date, location_id=location_id)
        return _deep(self.menu_day_payload)

    async def menu_stock(self, date: str, *,
                         location_id: int | None = None) -> dict[str, Any]:
        self._record("menu_stock", date, location_id=location_id)
        return _deep(self.stock_payload)

    # ------------------------------------------------------------- yazma

    async def create_order(
        self, *, service_date: str, delivery_type: str, payment_method: str,
        items: list[dict[str, Any]], actor: str, customer_id: int | None = None,
        customer: dict[str, Any] | None = None, address: dict[str, Any] | None = None,
        customer_note: str = "", location_id: int | None = None, reason: str = "",
        dry_run: bool | None = None,
    ) -> dict[str, Any]:
        """`POST /api/control/orders`.

        İmza `bld_api/backend/client.py::create_order` ile BİREBİR AYNI (donmuş
        tablo §5). Ad ya da alanlar değişirse buradaki taklit de değişmeli —
        testin yeşil kalması, canlıda çalıştığı anlamına gelmez.

        GERÇEK GEÇİT BURADA OLMAYAN ÜÇ KAPI DAHA TUTUYOR (`payment_method`
        listesi, "iki kipten biri" kuralı, teslimatta adres) ve hepsi
        `BldApiError(code="payload")` fırlatıyor. Taklit onları TEKRARLAMAZ:
        servis o kapılara zaten kendisi bakıyor ve testler ağa hiç çıkılmadığını
        (`api.names() == []`) ölçüyor. İkinci bir kopya, kapı serviste
        kaldırıldığında da testi yeşil bırakırdı.
        """
        self._record("create_order", service_date=service_date,
                     delivery_type=delivery_type, payment_method=payment_method,
                     items=items, actor=actor, customer_id=customer_id,
                     customer=customer, address=address, customer_note=customer_note,
                     location_id=location_id, reason=reason, dry_run=dry_run)
        if dry_run:
            # Kuru provada `201` DEĞİL `200` döner ve sipariş numarası YOKTUR:
            # hiçbir satır oluşmadı, `201 Created` yalan olurdu.
            return {"ok": True, "dry_run": True, "audit_id": 1830,
                    "would": {"action": "order.create", "service_date": service_date,
                              "delivery_type": delivery_type,
                              "payment_method": payment_method,
                              "customer_id": customer_id,
                              "would_create_customer": customer_id is None,
                              "item_count": len(items), "items": items},
                    "warnings": []}
        return _deep(self.order_payload)


class FakeApiWithoutCreate(FakeApi):
    """Geçidin sipariş açma metodu HENÜZ EKLENMEMİŞ hâli.

    `create_order` bilerek silinir: `getattr` ile aranan bir metot yoksa servis
    AÇIK bir cümle döndürmeli. Doğrudan çağırıp `AttributeError`i K7 yutucusuna
    bırakmak, yanlış/eksik metot adını düşmüş bir sunucudan ayırt edilemez
    kılardı.
    """

    create_order = None  # type: ignore[assignment]


def _deep(value: Any) -> Any:
    """Sığ kopya yetmez: testler `data`/`items` altını değiştiriyor ve fixture
    sabitleri paylaşılırsa bir test diğerini sessizce bozar."""
    if isinstance(value, dict):
        return {key: _deep(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_deep(item) for item in value]
    return value


def make_service(*, api: FakeApi | None = None, log: FakeLog | None = None,
                 config: dict[str, Any] | None = None) -> Any:
    """Servisi sahte bağlamla kurar. Testlerin tek kurulum yolu."""
    from bld_manual_order_backend.service import ManualOrderService

    settings = {"dry_run_default": False, "customer_page_size": 20,
                "min_search_chars": 3}
    settings.update(config or {})
    return ManualOrderService(api=api or FakeApi(), log=log or FakeLog(),
                              config=settings)


#: Geçerli bir taslak — testler bunun üstüne yazar.
def draft(**extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "service_date": TODAY,
        "delivery_type": "pickup",
        "payment_method": "cash",
        "items": [{"menu_id": 88, "quantity": 2}],
        "actor": "Ayşe Yılmaz",
        "customer_id": 312,
        "allow_manage": True,
    }
    base.update(extra)
    return base
