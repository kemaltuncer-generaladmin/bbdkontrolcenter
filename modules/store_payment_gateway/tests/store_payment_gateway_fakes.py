"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ, SMS GÖNDERMEZ.

`FakeStore` SQL'i ayrıştırmaz; servisin yazdığı birkaç ifadeyi tanıyacak
kadarını yapar. Süzgeç doğruluğu burada DEĞİL, `collect.filter_clause`
üzerinde doğrudan sınanır: sahte bir WHERE yorumlayıcısı yazsaydık testler
gerçek SQL'i değil kendi taklidimizi doğrulardı.
"""

from __future__ import annotations

import re
from typing import Any

#: `INSERT INTO ... (a, b, c) VALUES (?, ?, ?)` içindeki sütun adları.
_INSERT_COLUMNS = re.compile(r"INSERT INTO \S+ \((.*?)\) VALUES", re.DOTALL)
_UPDATE_COLUMNS = re.compile(r"UPDATE \S+ SET (.*?) WHERE", re.DOTALL)


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

    def __init__(self, module_id: str = "store_payment_gateway") -> None:
        self.module_id = module_id
        self.requests: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.prefs: dict[str, str] = {}
        #: Son okuma sorgusu (sql, params). Süzgecin GERÇEKTEN sorguya
        #: geçtiğini sınamak için: sahte depo WHERE yorumlamıyor.
        self.reads: list[tuple[str, tuple[Any, ...]]] = []
        self._next_id = 1

    def table(self, name: str) -> str:
        return f"mod_{self.module_id}_{name}"

    # ------------------------------------------------------------ yazma

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        text = " ".join(sql.split())
        if "_requests" in text and text.startswith("INSERT"):
            columns = [name.strip() for name in _INSERT_COLUMNS.search(text).group(1).split(",")]
            row = dict(zip(columns, params, strict=False))
            row.setdefault("status", "draft")
            row["id"] = self._next_id
            self._next_id += 1
            for column in ("token", "link", "store_status", "sms_state", "sms_at",
                           "settle_method", "settle_ref", "reason", "updated_at"):
                row.setdefault(column, "")
            # `link_id` de burada: göç 002 sütunu `DEFAULT 0` ile ekliyor ve
            # taklit deponun gerçek şemadan eksik kalmaması gerekiyor.
            for column in ("order_id", "invoice_id", "net", "tax", "gross", "link_id"):
                row.setdefault(column, 0)
            self.requests.append(row)
        elif "_requests" in text and text.startswith("UPDATE"):
            columns = [part.split("=")[0].strip()
                       for part in _UPDATE_COLUMNS.search(text).group(1).split(",")]
            values, request_id = params[:-1], params[-1]
            for row in self.requests:
                if row["id"] == request_id:
                    row.update(dict(zip(columns, values, strict=False)))
        elif "_events" in text and text.startswith("INSERT"):
            keys = ("request_id", "action", "reason", "actor", "result", "detail", "created_at")
            entry = dict(zip(keys, params, strict=False))
            entry["id"] = len(self.events) + 1
            self.events.append(entry)
        elif "_prefs" in text:
            self.prefs[params[0]] = params[1]

    # ------------------------------------------------------------ okuma

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        text = " ".join(sql.split())
        if "COUNT(*)" in text:
            return {"total": len(self.requests)}
        if "_prefs" in text:
            value = self.prefs.get(params[0])
            return {"value": value} if value is not None else None
        if "WHERE id = ?" in text:
            return next((row for row in self.requests if row["id"] == params[-1]), None)
        if "WHERE code = ?" in text:
            return next((row for row in self.requests if row["code"] == params[-1]), None)
        return None

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        text = " ".join(sql.split())
        self.reads.append((text, params))
        if "_events" in text:
            rows = [row for row in reversed(self.events) if row["request_id"] == params[0]]
            return rows
        rows = list(reversed(self.requests))
        if "OFFSET" in text:
            limit, offset = params[-2], params[-1]
            return rows[offset:offset + limit]
        return rows[:params[-1]] if params else rows


class FakeSmsResult:
    def __init__(self, *, accepted: bool = True, dry_run: bool = False,
                 job_id: str = "JOB-1", parts: int = 1) -> None:
        self.accepted = accepted
        self.dry_run = dry_run
        self.job_id = job_id
        self.parts = parts
        self.recipients = 1


class FakeProvider:
    def __init__(self, result: FakeSmsResult | None = None,
                 failure: Exception | None = None) -> None:
        self.result = result or FakeSmsResult()
        self.failure = failure
        self.sent: list[tuple[str, str]] = []

    async def send(self, messages: Any, *, header: str | None = None,
                   **_: Any) -> FakeSmsResult:
        if self.failure:
            raise self.failure
        for message in messages:
            self.sent.append((message.to, message.text))
        return self.result


class FakeNotify:
    """`notify` platform yeteneğinin testlik yüzü."""

    def __init__(self, *, configured: bool = True, dry_run: bool = False,
                 provider: FakeProvider | None = None) -> None:
        self.configured = configured
        self._dry_run = dry_run
        self.provider = provider or FakeProvider()

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    async def ready(self) -> dict[str, Any]:
        return {"provider": "netgsm", "enabled": True, "dryRun": self._dry_run,
                "configured": self.configured, "header": "BBDUNYAM", "error": ""}

    async def sms(self) -> FakeProvider:
        return self.provider


class FakeVault:
    """`secrets` platform yeteneğinin testlik yüzü.

    DEĞERLERİ AÇIKTA TUTAR ve bu bilinçlidir: testin sınadığı şey şifrelemenin
    doğruluğu değil (o `km_platform` testlerinin işi), servisin parolayı ekrana
    GERİ VERMEMESİ ve doğru anahtar adına yazması. Sahte kasa değerleri
    saklasaydı, "yanlış anahtara yazdım" hatası testte görünmezdi.
    """

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values: dict[str, str] = dict(values or {})
        self.fail = False

    async def get(self, key: str) -> str | None:
        if self.fail:
            raise RuntimeError("kasa anahtarı çözülemedi")
        return self.values.get(key)

    async def set(self, key: str, value: str) -> None:
        if self.fail:
            raise RuntimeError("kasa yazılamadı")
        self.values[key] = value


class FakeStoreApiError(RuntimeError):
    """`store_api.errors.StoreApiError` yerine geçen taklit.

    NEDEN GERÇEĞİ İMPORT EDİLMİYOR: modül modülü import etmez (K3) ve test
    tarafında bu kuralı delmek, bir gün geçidin sınıfını değiştirdiğinde bu
    modülün testlerini de kırardı. Önemli olan SINIF DEĞİL SÖZLEŞMEDİR:
    gerçek `StoreApiError.__str__` kullanıcıya gösterilebilir Türkçe metni
    döndürür ve servis onu `_fail()` içinde `str()` ile okur. Taklit de
    tam olarak bunu yapar; başka bir davranışına dayanılmıyor.

    TEK İSTİSNA `code`: servis "kayıt yok" (`not_found`) ile "ulaşamadım"ı
    ayırmak için bu alana bakıyor ve ayrım paranın doğruluğunu belirliyor —
    ilki "bu bağlantı mağazada YOK" (durum bilinmiyor, çift çekim uyarısı),
    ikincisi "mağazaya ulaşamadım" (eski durum korunur). Taklit alanı aynı
    adla taşır; sınıf yine import edilmiyor.
    """

    def __init__(self, message: str = "", *, code: str = "") -> None:
        super().__init__(message)
        self.code = code


class FakeApi:
    """`store.api` yeteneğinin testlik yüzü. Yalnız kullanılan metotlar var.

    `bbd_create_payment_link` GERÇEK GEÇİDİN SÖZLEŞMESİNİ UYGULAR (imza ve
    retler `store_api.client` içinden birebir alındı). Bir dönem burada
    imzası gerçekten FARKLI (kuruş tutar + `order_id`) bir taklit duruyordu ve
    kendi docstring'i "ikisi ayrı ayrı değiştirilirse test yeşil kalırken canlı
    akış kırık kalır" diye uyarıyordu — tam da o olmuştu. Taklit artık gerçeğin
    reddettiği her şeyi reddediyor; testin yeşil olması canlının çalışacağı
    anlamına gelsin.
    """

    def __init__(self, products: dict[int, dict[str, Any]] | None = None) -> None:
        self.products_by_id = products or {}
        #: `GET /admin/orders/{id}` — faturalar GÖMÜLÜ gelir (canlıda doğrulandı).
        self.orders_by_id: dict[int, dict[str, Any]] = {}
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()
        #: Üretilen bağlantının MAĞAZA BİÇİMİ — `PaymentLinkService::present`.
        #: Alan adları `id` ve `code`tur; mağaza "token" diye bir alan HİÇ
        #: döndürmez. Taklidin eskiden `token` döndürmesi, `code`u okuyamayan
        #: bir hatayı testten gizlerdi.
        self.link_payload: dict[str, Any] = {"id": 41, "code": "TKN-1",
                                             "url": "https://ode.me/TKN-1", "status": "pending"}
        #: Doluysa bağlantı üretimi bu METİNLE reddedilir — mağazanın (ör. il
        #: tanınmadı, tutar sapması) ya da geçidin verdiği ret böyle görünür.
        self.link_error: str = ""
        #: Doluysa İPTAL bu metinle reddedilir. "Önceki bağlantı kapatılamadı"
        #: dalını sınamak için gerekli: o dalda yeni link ÜRETİLMEMELİ.
        self.cancel_error: str = ""
        self.links_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.attempts_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.invoices_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.products_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.tax_categories_payload: dict[str, Any] = {"items": []}
        self.tax_rates_payload: dict[str, Any] = {"items": []}
        self.read_only = False

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        if name in self.fail:
            raise RuntimeError(f"{name} patladı")
        self.calls.append((name, args, kwargs))

    def used(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for called, _, kwargs in self.calls if called == name]

    def state(self) -> dict[str, Any]:
        return {"baseUrl": "https://ornek", "readOnly": self.read_only,
                "dryRunDefault": True, "pageSize": 50, "requireReason": True}

    async def health(self) -> dict[str, Any]:
        self._record("health")
        return {"ok": True, "error": ""}

    async def tax_categories(self) -> dict[str, Any]:
        self._record("tax_categories")
        return self.tax_categories_payload

    async def tax_rates(self, filters: Any = None) -> dict[str, Any]:
        self._record("tax_rates", filters)
        return self.tax_rates_payload

    async def product(self, product_id: int) -> dict[str, Any]:
        self._record("product", product_id)
        if product_id not in self.products_by_id:
            raise RuntimeError("Kayıt bulunamadı")
        return self.products_by_id[product_id]

    async def products(self, filters: Any = None, *, page: int = 1,
                       per_page: int | None = None, all_pages: bool = False) -> dict[str, Any]:
        self._record("products", filters, page=page, per_page=per_page)
        return self.products_payload

    async def bbd_create_payment_link(self, *, amount: str = "", kind: str = "custom",
                                      items: list[dict[str, Any]] | None = None,
                                      billing: dict[str, Any] | None = None,
                                      description: str = "", order_id: int | None = None,
                                      reason: str, actor: str = "",
                                      dry_run: bool | None = None) -> dict[str, Any]:
        """İmza ve retler GERÇEK GEÇİTTEN alınmıştır — kopya bilerek birebirdir.

        Reddettikleri (hepsi `store_api.client.bbd_create_payment_link` içinde
        aynı sırayla duruyor): tanınmayan `kind`, verilen `order_id`, boş
        `billing`, `custom`ta metin olmayan/boş `amount`, `custom`ta `items`,
        `product`ta eksik `items`.
        """
        if kind not in ("custom", "product"):
            raise FakeStoreApiError(f"Ödeme linki türü yalnız custom/product olabilir: {kind}")
        if order_id is not None:
            raise FakeStoreApiError(
                "Mağazanın ödeme linki ucu SİPARİŞE BAĞLANMAZ: gövdedeki `orderId` alanı "
                "okunmaz ve sessizce düşer.")
        if not isinstance(billing, dict) or not billing:
            raise FakeStoreApiError(
                "Ödeme linki için fatura bilgisi (`billing`) zorunludur: sunucu sepeti bu "
                "adresle kuruyor ve ad/soyad boşsa isteği reddediyor.")
        if kind == "custom":
            if not isinstance(amount, str) or not amount.strip():
                raise FakeStoreApiError(
                    "Serbest tutarlı ödeme linkinde `amount` ONDALIK METİN olmalıdır "
                    '("125.00").')
            if items:
                raise FakeStoreApiError("Serbest tutarlı bağlantıya ürün eklenemez.")
        elif not items:
            raise FakeStoreApiError('kind="product" için en az bir ürün gönderilmelidir.')
        if self.link_error:
            raise FakeStoreApiError(self.link_error)

        self._record("bbd_create_payment_link", amount=amount, kind=kind, items=items,
                     billing=billing, description=description, reason=reason, actor=actor,
                     dry_run=dry_run)
        if dry_run:
            # KURU PROVA YANITINDA LİNK YOKTUR. Mağaza sepeti gerçekten kurar,
            # toplamı hesaplar ve işlemi geri sarar; dönen zarf
            # `{'dryRun': True, 'wouldChange': {...}}` olur — `code`/`url` alanı
            # hiç doğmaz (`PaymentLinkService::create`). Taklit link döndürseydi,
            # kuru provanın yerel satıra sahte belirteç yazıp yazmadığını hiçbir
            # test göremezdi.
            return {"ok": True, "dryRun": True,
                    "wouldChange": {"kind": kind, "amount": amount, "currency": "TRY",
                                    "items": items or [], "expiresInHours": 48}}
        return {"ok": True, "dryRun": False, "data": dict(self.link_payload)}

    def drop_link_method(self) -> None:
        """Geçitten bağlantı üreten metodu kaldırır.

        `service._standalone` yoklaması `callable(getattr(api, ...))` yapıyor;
        örnek üzerine `None` yazmak sınıftaki metodu gölgeler ve yoklama
        "yok" der. Metot bir gün yeniden adlandırılırsa canlıda da tam olarak
        bu durum oluşur.
        """
        self.bbd_create_payment_link = None  # type: ignore[assignment]

    async def bbd_cancel_payment_link(self, link_id: int | str, *, reason: str, actor: str = "",
                                      dry_run: bool | None = None) -> dict[str, Any]:
        """İptal — KİMLİK SAYISAL OLMAK ZORUNDA, gerçek geçitteki denetimin aynısı.

        Rota `->whereNumber('id')` ile daraltılmış; geçit bu yüzden
        `key.isdigit()` istiyor ve 12 haneli `code` dizesini reddediyor. Taklit
        eskiden HER ŞEYİ kabul ediyordu — "İptal" düğmesinin canlıda hiç
        çalışmadığı, testler yeşilken fark edilmemişti.
        """
        self._record("bbd_cancel_payment_link", link_id, reason=reason, actor=actor,
                     dry_run=dry_run)
        if not str(link_id).strip().isdigit():
            raise FakeStoreApiError(
                f"Ödeme linki kimliği sayısal olmalıdır (verilen: {str(link_id)!r}).")
        if self.cancel_error:
            raise FakeStoreApiError(self.cancel_error)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def bbd_payment_links(self, filters: Any = None) -> dict[str, Any]:
        """Link listesi — SÜZGEÇLERİ MAĞAZA GİBİ UYGULAR.

        BU TAKLİDİN EN ÖNEMLİ İŞİ SÜZMEK DEĞİL, TANIMADIĞINI YOK SAYMAK.
        `PaymentLinkController::index` yalnız `status` ve `q` okur; Laravel
        tanımadığı sorgu parametresini sessizce atar ve uç "en yeni 50 link"i
        döndürür. Taklit eskiden süzgeci HİÇ okumuyor, her çağrıda hazır listeyi
        veriyordu — bu yüzden "yanlış süzgeç adı gönderiliyor" hatası testte
        görünmüyordu. Artık `token`/`order_id` göndermek burada da SESSİZLİK
        üretir, hata değil; yani yanlış ad gönderen kod testte de yanlış satırı
        görür ve düşer.
        """
        self._record("bbd_payment_links", filters)
        rows = list(self.links_payload.get("items") or [])
        keys = set(filters or {})
        applied = {"q", "status"} & keys
        if applied - {"status"}:
            needle = str((filters or {}).get("q") or "")
            rows = [row for row in rows if needle and needle in str(row.get("code") or "")]
        return {**self.links_payload, "items": rows,
                "meta": {**(self.links_payload.get("meta") or {}),
                         "ignoredFilters": sorted(keys - {"q", "status"})}}

    async def bbd_payment_link(self, link_id: int) -> dict[str, Any]:
        """Tek link — CANLI DAVRANIŞ: bilinmeyen kimlik HATA verir (404),
        boş satır değil. "Bulamadım" ile "başkasını buldum" arasındaki farkı
        koruyan uç budur."""
        self._record("bbd_payment_link", link_id)
        for row in (self.links_payload.get("items") or []):
            if int(row.get("id") or 0) == int(link_id):
                return dict(row)
        if int(self.link_payload.get("id") or 0) == int(link_id):
            return dict(self.link_payload)
        raise FakeStoreApiError("Kayıt bulunamadı (GET /payment-links).", code="not_found")

    async def bbd_payment_attempts(self, filters: Any = None, *, page: int = 1,
                                   per_page: int | None = None) -> dict[str, Any]:
        """POS denemeleri — SÜZGEÇ ADI `orderId`, CAMEL CASE.

        `PaymentAttemptController::applyFilters` yalnız `state · orderId ·
        from · to` okur. Canlıda ölçüldü (16.08.2026, salt GET):
        `?order_id=999999` → 17 satır (yok sayıldı), `?orderId=999999` → 0.
        Taklit aynı sessizliği uygular: yanlış ad SÜZMEZ, tüm listeyi döndürür.
        """
        self._record("bbd_payment_attempts", filters, page=page, per_page=per_page)
        rows = list(self.attempts_payload.get("items") or [])
        keys = set(filters or {})
        if "orderId" in keys:
            wanted = int((filters or {}).get("orderId") or 0)
            rows = [row for row in rows if int(row.get("order_id") or 0) == wanted]
        return {**self.attempts_payload, "items": rows,
                "meta": {**(self.attempts_payload.get("meta") or {}),
                         "ignoredFilters": sorted(keys - {"state", "orderId", "from", "to"})}}

    async def invoices(self, filters: Any = None, *, page: int = 1,
                       per_page: int | None = None) -> dict[str, Any]:
        self._record("invoices", filters, page=page, per_page=per_page)
        return self.invoices_payload

    async def order(self, order_id: int) -> dict[str, Any]:
        """Sipariş detayı. CANLI DAVRANIŞ: bilinmeyen kimlik hata verir,
        `invoices` gömülüdür ve fatura satırı `orderId` TAŞIMAZ."""
        self._record("order", order_id)
        if int(order_id) not in self.orders_by_id:
            raise RuntimeError("Sipariş bulunamadı")
        return self.orders_by_id[int(order_id)]

    async def record_transaction(self, *, payload: dict[str, Any], reason: str, actor: str = "",
                                 dry_run: bool | None = None) -> dict[str, Any]:
        self._record("record_transaction", payload=payload, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    # `add_standalone()` KALDIRILDI. Serbest tahsilat için ayrı bir geçit metodu
    # takmaya gerek yok: mağaza ucu zaten siparişe bağlanmayan link üretiyor ve
    # tek metot (`bbd_create_payment_link`) iki yolu da karşılıyor. Metot artık
    # varsayılan olarak DURUYOR; yokluğunu sınayan test `drop_link_method()`
    # kullanır.
