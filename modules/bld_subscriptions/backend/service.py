"""Abonelikler — iş kuralları.

VERİ BLD'DEDİR, KARAR SUNUCUDADIR, EKRAN BURADADIR. Abonelik, satırları,
teslimat noktaları, duraklamaları, istisnaları, üretim defteri, sözleşmeleri ve
dönem ödemeleri BLD sunucusunda durur ve buraya `bld.api` geçidinden gelir
(K4); bu modül ham `httpx` kullanmaz ve UZAK VERİNİN KOPYASINI TUTMAZ. Yerel
tablolar yalnız BLD'de karşılığı olmayan iki şeyi saklar: yazma gerekçesinin
denetim izi (özellikle "fiyatı kim, ne zaman, neden anlaştı") ve bu ekrana özel
görüntüleme tercihleri.

Neden kopya tutulmuyor: bir aboneliğin durumu gece işiyle de değişir (üretim
defteri yazılır, sipariş oluşur), müşteri sözleşmeyi kendi telefonundan
imzalar ve dönem ödemesi ileride uygulamadan da kapanacak
(`_devralinan-odeme-yapisi.md`). Yerel bir kopya her zaman bir tur geride olur
ve "sözleşme bekleniyor" görünen bir abonelik çoktan imzalanmış olabilir;
yönetici buna bakarak müşteriyi ikinci kez arar.

ÜÇ KURAL YENİDEN YAZILMADI, ÇÜNKÜ ÜÇÜ DE SUNUCUDADIR:

  1. ÜRETİM TAKVİMİ — `Subscription::upcomingServiceDays()`. Kapalı gün,
     duraklama ve istisna birlikte uygulanır; ekran `service_days` listesinden
     kendi takvimini türetseydi mutfakla ayrışırdı.
  2. GECİKMİŞ BORÇ — `overdue` sunucuda hesaplanır. Saati kaymış bir panelde
     borç bir gün erken kırmızıya dönerdi.
  3. SÖZLEŞME DURUM MAKİNESİ — `pending → sent → signed` geçişleri ve
     `signed`in terminal oluşu sunucuda uygulanır. Buradaki `open`/`terminal`
     alanları YALNIZ düğme çizmek içindir ve kapıyı sunucu tutar.

OTP AKIŞI BURADA YOKTUR VE OLMAYACAKTIR (K3). Sözleşme SMS'ini ve OTP
doğrulamasını BLD yürütür; bu servis yalnız `create/resend/cancel` uçlarını
tetikler. Kod doğrulayan bir metot yazmak, bu modülü `bld_sms`e bağlar ve iki
yerde ayrışabilen bir güvenlik akışı üretirdi.

UZAK SİSTEM DÜŞERSE EKRAN AYAKTA KALIR (K7): okuma uçları
`{"ok": True, "connected": False, "error": ...}` döner, İSTİSNA DIŞARI SIZMAZ.
`ok: True` OKUMANIN BAŞARISIZLIĞINI DEĞİL, UCUN SAĞLIĞINI anlatır; ayrımı
`connected` taşır ve panelin onu OKUMASI gerekir.

UÇ HENÜZ DAĞITILMAMIŞ OLABİLİR. Sunucu tarafı paralel yazılıyor ve geçit bu
durumda `control_endpoint_missing` kodunu veriyor. Bu bir ARIZA DEĞİLDİR:
okuma yanıtları `missing_endpoint: True` taşır ve ekran kırmızı hata yerine
"bu uç sunucuya henüz dağıtılmadı" der. `not_found` ile karıştırılmaz —
ilki "uç yok", ikincisi "kayıt yok".

YAZMA ZİNCİRİ — yazma uçlarının hepsi bu beş adımı bu sırayla uygular:

    1. gerekçe denetimi (10–500; arayüzde zorunlu göstermek yetmez, K9)
    2. gövde ön denetimi (tarih, aralık, seçim listesi)
    3. yerel iz: `result="denendi"`  ← ağ koparsa geriye YALNIZ bu kalır
    4. geçit çağrısı — `dry_run=` HER ZAMAN AÇIKÇA verilir
    5. yerel iz: `ok` / `dry_run` / `hata`

Üçüncü adım kritiktir: sözleşme gönderilirken ağ koparsa müşteriye SMS gidip
gitmediği belirsizdir ve "kim denedi" sorusunun cevabı yalnız o satırda kalır.

`dry_run` HER ÇAĞRIDA AÇIKÇA GEÇER. Geçidin varsayılanına güvenilmez:
`config/local.yaml` git dışıdır ve orada `true` yazıyor; bayrağı atlayan bir
modül hiçbir şey yazmadan `{"ok": true}` alır ve ekran "kaydedildi" der
(`bld_api/README.md`).
"""

from __future__ import annotations

import json
from typing import Any

from . import subs as sb

#: Yerel denetim izinin `result` sütununun alabileceği değerler.
TRIED = "denendi"
DONE = "ok"
DRY = "dry_run"
FAILED = "hata"

#: Abonelik durumu bu ekrandan değiştiğinde yayınlanan olay (manifest).
#: DÖRT EYLEM TEK OLAYDA: dinleyicinin sorusu "bu abonelik artık üretiyor mu"
#: biçimindedir ve cevabı `to` alanında yazılı.
STATUS_EVENT = "bld_subscriptions.subscription_status_changed"

#: Tahsilat AYRI BİR OLAYDIR: bu bir para hareketidir ve geri alma ucu YOKTUR.
#: Durum olayıyla aynı ada konsaydı, tahsilatı gövdeye bakarak ayırmak zorunda
#: kalan bir dinleyici onu kaçırdığında sessizce yanlış çalışırdı.
PAID_EVENT = "bld_subscriptions.payment_marked_paid"

#: Olay yükleri camelCase'tir: olay yolu Kontrol Merkezi'nin KENDİ iç
#: yüzeyidir. HTTP yanıtları sözleşmenin snake_case sözlüğünü korur.

#: Geçidin "uç henüz sunucuda yok" kodu. `not_found` ile AYRI şeydir.
MISSING = "control_endpoint_missing"


class SubscriptionsService:
    """Abonelikler ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ.

    Servis bir istisna ile cevap verseydi ekran beyaz bir hata sayfası
    gösterirdi; burada her yol `{"ok": ..., "error": ...}` ile biter ve panel
    kullanıcıya ne olduğunu YAZAR. 4xx yalnız izin ve şema kapısından çıkar.
    """

    def __init__(self, *, api: Any, store: Any, log: Any, config: dict[str, Any],
                 publish: Any = None) -> None:
        self._api = api
        self._store = store
        self._log = log
        self._config = config or {}
        self._publish = publish

        self._audit = store.table("audit")
        self._prefs = store.table("prefs")

    # ------------------------------------------------------------- ayarlar

    @property
    def _dry_run_default(self) -> bool:
        """İstemci `dryRun` alanını HİÇ göndermezse geçerli olan varsayılan.

        VARSAYILAN KAPALI. Açık bırakmak, panelden yapılan her yazmayı sessizce
        provaya çevirmek olurdu: ekran "gönderildi" der, müşteriye sözleşme
        SMS'i hiç gitmez ve fark ancak müşteri aradığında anlaşılır.
        """
        return bool(self._config.get("dry_run_default", False))

    @property
    def _page_size(self) -> int:
        """Sayfa boyutu varsayılanı. Tavan 100 (`00-genel.md` §5)."""
        return max(5, min(100, sb.as_int(self._config.get("page_size"), 25)))

    @property
    def _calendar_days(self) -> int:
        """Takvimin açtığı pencere. Sunucu tavanı 92 gün."""
        return max(7, min(sb.MAX_CALENDAR_DAYS,
                          sb.as_int(self._config.get("calendar_days"), 30)))

    @property
    def _expires_in_days(self) -> int:
        """İmza bağlantısının varsayılan ömrü (1–30, sözleşme varsayılanı 7)."""
        return max(sb.MIN_EXPIRES_DAYS, min(sb.MAX_EXPIRES_DAYS,
                                            sb.as_int(self._config.get("expires_in_days"),
                                                      sb.DEFAULT_EXPIRES_DAYS)))

    def _dry(self, dry_run: bool | None) -> bool:
        return self._dry_run_default if dry_run is None else bool(dry_run)

    # ------------------------------------------------------------- yardımcı

    @staticmethod
    def _fail(failure: Exception) -> str:
        message = str(failure).strip()
        return message or "BLD sunucusuna ulaşılamadı."

    @staticmethod
    def _code(failure: Exception) -> str:
        """Geçidin hata kodu. `BldApiError` import EDİLMEZ (K2/K3): başka bir
        modülün sınıfına bağlanmak, o modül yüklenmediğinde bu modülü de
        düşürürdü. Kod bir dizedir ve `getattr` ile okunur."""
        return str(getattr(failure, "code", "") or "")

    def _read_failure(self, failure: Exception, *, what: str,
                      **extra: Any) -> dict[str, Any]:
        """Okuma hatasının ORTAK gövdesi (K7).

        `control_endpoint_missing` AYRI İŞARETLENİR: sunucu tarafı paralel
        yazılıyor ve dağıtılmamış bir uç arıza değildir. Ekran onu kırmızı bir
        hata kutusuyla göstermemeli, yoksa personel her açılışta var olmayan
        bir sorunu bildirir.
        """
        code = self._code(failure)
        missing = code == MISSING
        if not missing:
            self._log.warning(f"{what} okunamadı", error=str(failure), code=code)
        return {
            "ok": True, "connected": False, "error": self._fail(failure),
            "code": code, "missing_endpoint": missing, **extra,
        }

    async def _record(self, *, action: str, reason: str, actor: str, result: str,
                      target_type: str = "subscription", target_id: int = 0,
                      price_kurus: int | None = None, detail: Any = None) -> None:
        """Yerel denetim izi. BLD de `veykemtu_control_audit` tutuyor
        (`00-genel.md` §8); bu satır ONUN YERİNE DEĞİL, ONDAN ÖNCE yazılır.

        Ayrım önemli: uzak kayıt yalnız sunucuya ULAŞAN isteği bilir. Ağ
        koparsa, geçit patlarsa ya da istek yarıda kalırsa "kim neyi denedi"
        sorusunun cevabı yalnız burada kalır.

        `price_kurus` AYRI BİR SÜTUNDUR, `detail` JSON'unun içinde değil:
        "fiyatı kim, ne zaman, neden anlaştı" bu ekranın en çok sorulan
        sorusudur ve JSON içinden aranan bir alan, sıralanamaz ve
        indekslenemez.
        """
        try:
            await self._store.execute(
                f"INSERT INTO {self._audit} "
                "(target_type, target_id, action, reason, actor, result, price_kurus, "
                "detail, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (target_type, int(target_id or 0), action, sb.text(reason), actor, result,
                 price_kurus, json.dumps(detail or {}, ensure_ascii=False), sb.now_iso()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın (K7)
            self._log.warning("denetim izi yazılamadı", action=action, error=str(failure))

    async def _announce(self, event: str, payload: dict[str, Any]) -> None:
        """Olayı veri yoluna bırakır (K3).

        Yayın BAŞARISIZ OLSA BİLE iş başarılıdır: abonelik BLD'de değişmiştir,
        dinleyicinin patlaması onu geri almaz (K7).
        """
        if self._publish is None:
            return
        try:
            await self._publish(event, payload)
        except Exception as failure:  # noqa: BLE001 — dinleyici bizi düşürmez (K7)
            self._log.warning("olay yayınlanamadı", event=event, error=str(failure))

    @staticmethod
    def _items(payload: Any) -> list[dict[str, Any]]:
        """Liste yanıtından satırları çıkarır.

        Geçit sayfalı uçları `{"items": [...], "meta": {...}}` biçiminde
        açıyor; `data` ve düz dizi de kabul edilir çünkü tek bir ada bağlanmak,
        ad tutmadığında ekranı SESSİZCE boş gösterirdi.
        """
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("items", "data"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def _meta(payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict) and isinstance(payload.get("meta"), dict):
            return dict(payload["meta"])
        return {}

    @staticmethod
    def _record_of(payload: Any, *keys: str) -> dict[str, Any]:
        """Tekil yanıttan kaydı çıkarır (`{"data": {...}}` ya da düz sözlük)."""
        if not isinstance(payload, dict):
            return {}
        for key in keys:
            value = payload.get(key)
            if isinstance(value, dict):
                return value
        return payload

    @staticmethod
    def _server_time(payload: Any) -> str:
        """Yanıttaki `server_time` — geri sayımların ve "bayat mı" sorusunun
        tabanı budur; istemcinin saati kaymış olabilir (`00-genel.md` §6)."""
        if isinstance(payload, dict):
            return sb.text(payload.get("server_time"))
        return ""

    @staticmethod
    def _would(payload: Any, dry: bool) -> Any:
        return payload.get("would") if isinstance(payload, dict) and dry else None

    # ================================================================= okuma

    async def overview(self) -> dict[str, Any]:
        """Panel açılışı — SÖZLEŞME VE TERCİH, ağa çıkmaz.

        Abonelik alanında bir "özet" ucu YOKTUR (`subscriptions.md` uçları
        sayıyor); uydurulmadı. Ekranın açılışta ihtiyacı olan şey zaten
        sunucuda değil: durum kodları, hafta günleri, gerekçe sınırları ve
        kullanıcının kendi tercihleri. Bunları ağa çıkmadan vermek, geçit
        düşükken bile sekme şeridinin çizilebilmesi demektir (K7).
        """
        return {
            "ok": True,
            "connected": None,   # bu uç ağa çıkmaz; bağlantı listeden anlaşılır
            "error": "",
            "contract": sb.screen_contract(),
            "prefs": await self.prefs(),
            "limits": {
                "page_size": self._page_size,
                "calendar_days": self._calendar_days,
                "expires_in_days": self._expires_in_days,
            },
        }

    async def subscriptions(self, *, status: Any = None, customer_id: int = 0, q: str = "",
                            service_day: int = 0, active_on: str = "", page: int = 1,
                            per_page: int = 0) -> dict[str, Any]:
        """Abonelik listesi — SAYFALI.

        `unpaid_periods` ve `unpaid_total_kurus` listeden gelir; ödenmemiş
        dönemi öğrenmek için satır satır ödeme çağrısı yapmak dokuz abonelikte
        dokuz istek demekti (sözleşme).
        """
        codes, problem = sb.status_filter(status, sb.STATUS_CODES)
        if not problem:
            problem = sb.date_error(active_on, field="Üretim günü")
        if not problem and service_day and (service_day < 1 or service_day > 7):
            problem = "Servis günü 1–7 arası olmalı."
        if problem:
            # Süzgeç hatası bir BAĞLANTI sorunu değildir: `connected` bilinmiyor
            # ve liste boş. Ekran ikisini karıştırmasın diye `ok: False`.
            return {"ok": False, "connected": None, "error": problem,
                    "items": [], "meta": {}}

        size = max(5, min(100, int(per_page) or self._page_size))
        try:
            payload = await self._api.subscriptions(
                status=codes or None, customer_id=int(customer_id) or None,
                q=sb.text(q), service_day=int(service_day) or None,
                active_on=sb.text(active_on), page=max(1, int(page)), per_page=size)
        except Exception as failure:  # noqa: BLE001 — K7
            return self._read_failure(failure, what="abonelik listesi",
                                      items=[], meta={})

        rows = [sb.subscription_row(raw) for raw in self._items(payload)]
        meta = self._meta(payload)
        return {
            "ok": True, "connected": True, "error": "", "missing_endpoint": False,
            "items": rows,
            "meta": {**meta, "page": sb.as_int(meta.get("page"), max(1, int(page))),
                     "per_page": sb.as_int(meta.get("per_page"), size)},
            "server_time": self._server_time(payload),
            # Sayaçlar YALNIZ BU SAYFANIN kayıtlarını sayar. Toplamı vermek
            # ikinci bir istek ederdi ve "sayfadaki 25 kaydın 4'ü ödemedi" ile
            # "137 aboneliğin 4'ü ödemedi" karıştırılırsa yönetici yanlış karar
            # verir; panel bu ayrımı yazıyla söyler.
            "page_counts": self._counts(rows),
            "page_unpaid_kurus": sum(row["unpaid_total_kurus"] for row in rows),
        }

    @staticmethod
    def _counts(rows: list[dict[str, Any]]) -> dict[str, int]:
        counts = dict.fromkeys(sb.STATUS_CODES, 0)
        for row in rows:
            code = sb.text(row.get("status"))
            if code in counts:
                counts[code] += 1
        return counts

    async def subscription(self, subscription_id: int) -> dict[str, Any]:
        """Tek abonelik — alt kayıtları ve akış şeridiyle."""
        try:
            payload = await self._api.subscription(int(subscription_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return self._read_failure(failure, what="abonelik",
                                      subscription={},
                                      subscription_id=int(subscription_id))

        raw = self._record_of(payload, "data", "subscription")
        if not raw:
            return {"ok": False, "connected": True, "error": "Abonelik kaydı boş döndü.",
                    "subscription": {}, "subscription_id": int(subscription_id)}
        return {"ok": True, "connected": True, "error": "", "missing_endpoint": False,
                "subscription": sb.subscription_view(raw),
                "subscription_id": int(subscription_id),
                "server_time": self._server_time(payload)}

    async def calendar(self, subscription_id: int, *, date_from: str = "",
                       days: int = 0) -> dict[str, Any]:
        """Önümüzdeki servis günleri — YALNIZ ÜRETİM YAPILACAK GÜNLER.

        Hafta sonu menü olmadığı için (iş kararı 4) beş günlük bir abonelikte
        cumartesi/pazar HİÇ GÖRÜNMEZ; kapalı günler `closed: true` ile görünür.
        Ekran boş bir hücre çizip "o gün ne oldu" sorusunu cevapsız bırakmaz.
        """
        problem = sb.date_error(date_from, field="Başlangıç")
        if problem:
            return {"ok": False, "connected": None, "error": problem, "items": [],
                    "meta": {}}
        window = max(1, min(sb.MAX_CALENDAR_DAYS, int(days) or self._calendar_days))
        try:
            payload = await self._api.subscription_calendar(
                int(subscription_id), date_from=sb.text(date_from), days=window)
        except Exception as failure:  # noqa: BLE001 — K7
            return self._read_failure(failure, what="servis takvimi", items=[], meta={})

        rows = [sb.calendar_row(raw) for raw in self._items(payload)]
        return {"ok": True, "connected": True, "error": "", "missing_endpoint": False,
                "items": rows, "meta": {**self._meta(payload), "days": window},
                "server_time": self._server_time(payload)}

    async def runs(self, subscription_id: int, *, date_from: str = "", date_to: str = "",
                   page: int = 1, per_page: int = 0) -> dict[str, Any]:
        """Üretim defteri (sayfalı) — idempotency kaydı.

        Bir (abonelik × nokta × gün) en fazla bir sipariş; güvence koddaki `if`
        değil VERİTABANI KISITIDIR. Bu ekran o güvenceyi tekrar etmez, defteri
        okur.
        """
        for value, field in ((date_from, "Başlangıç"), (date_to, "Bitiş")):
            problem = sb.date_error(value, field=field)
            if problem:
                return {"ok": False, "connected": None, "error": problem,
                        "items": [], "meta": {}}
        size = max(5, min(100, int(per_page) or self._page_size))
        try:
            payload = await self._api.subscription_runs(
                int(subscription_id), date_from=sb.text(date_from),
                date_to=sb.text(date_to), page=max(1, int(page)), per_page=size)
        except Exception as failure:  # noqa: BLE001 — K7
            return self._read_failure(failure, what="üretim defteri", items=[], meta={})

        meta = self._meta(payload)
        return {"ok": True, "connected": True, "error": "", "missing_endpoint": False,
                "items": [sb.run_row(raw) for raw in self._items(payload)],
                "meta": {**meta, "page": sb.as_int(meta.get("page"), max(1, int(page))),
                         "per_page": sb.as_int(meta.get("per_page"), size)},
                "server_time": self._server_time(payload)}

    async def contracts(self, subscription_id: int) -> dict[str, Any]:
        """Aboneliğin sözleşmeleri. `token` ve `sign_url` DÖNMEZ."""
        try:
            payload = await self._api.subscription_contracts(int(subscription_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return self._read_failure(failure, what="sözleşmeler", items=[],
                                      subscription_id=int(subscription_id))

        rows = [sb.contract_row(raw) for raw in self._items(payload)]
        return {"ok": True, "connected": True, "error": "", "missing_endpoint": False,
                "items": rows, "subscription_id": int(subscription_id),
                # AÇIK SÖZLEŞME VARSA YENİSİ AÇILAMAZ (sunucu `409` verir):
                # iki geçerli bağlantı, hangisinin imzalandığını belirsiz
                # kılardı. Ekran düğmeyi buna göre kapatır ve nedenini yazar.
                "open_contract_id": next((row["id"] for row in rows if row["open"]), 0),
                "signed_contract_id": next((row["id"] for row in rows
                                            if row["status"] == "signed"), 0),
                "server_time": self._server_time(payload)}

    async def contract(self, contract_id: int) -> dict[str, Any]:
        """Tek sözleşme."""
        try:
            payload = await self._api.subscription_contract(int(contract_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return self._read_failure(failure, what="sözleşme", contract={},
                                      contract_id=int(contract_id))
        raw = self._record_of(payload, "data", "contract")
        if not raw:
            return {"ok": False, "connected": True, "error": "Sözleşme kaydı boş döndü.",
                    "contract": {}, "contract_id": int(contract_id)}
        return {"ok": True, "connected": True, "error": "", "missing_endpoint": False,
                "contract": sb.contract_row(raw), "contract_id": int(contract_id)}

    async def payments(self, subscription_id: int, *, status: Any = None,
                       date_from: str = "", date_to: str = "") -> dict[str, Any]:
        """Dönem ödemeleri — SAYFALANMAZ (bir aboneliğin dönem sayısı sınırlı).

        `meta` dönem toplamlarını taşır ve burada YENİDEN TOPLANMAZ: satırların
        toplamını almak, sunucunun `void` kayıtları nasıl saydığını tahmin
        etmek olurdu.
        """
        codes, problem = sb.status_filter(status, ("pending", "paid", "void"))
        if not problem:
            for value, field in ((date_from, "Başlangıç"), (date_to, "Bitiş")):
                problem = sb.date_error(value, field=field)
                if problem:
                    break
        if problem:
            return {"ok": False, "connected": None, "error": problem, "items": [],
                    "meta": {}}
        try:
            payload = await self._api.subscription_payments(
                int(subscription_id), status=codes or None, date_from=sb.text(date_from),
                date_to=sb.text(date_to))
        except Exception as failure:  # noqa: BLE001 — K7
            return self._read_failure(failure, what="dönem ödemeleri", items=[], meta={},
                                      subscription_id=int(subscription_id))

        rows = [sb.payment_row(raw) for raw in self._items(payload)]
        return {"ok": True, "connected": True, "error": "", "missing_endpoint": False,
                "items": rows, "meta": self._meta(payload),
                "subscription_id": int(subscription_id),
                "overdue_count": sum(1 for row in rows if row["overdue"]),
                "server_time": self._server_time(payload)}

    async def requests(self, *, status: Any = None, q: str = "", date_from: str = "",
                       date_to: str = "", page: int = 1,
                       per_page: int = 0) -> dict[str, Any]:
        """Teklif talepleri (sayfalı) — İLETİŞİM BİLGİSİ MASKELİ.

        Maskeyi sunucu uygular; arayacak kişi kaydı açar. Bu uçlar müşteri
        okuma denetimine (KVKK) TABİ DEĞİLDİR — bu kayıtlar müşteri değil,
        henüz iletişime geçilmemiş adaylardır ve liste günde birkaç kez açılan
        bir iş kuyruğudur (sözleşme kararı).
        """
        codes, problem = sb.status_filter(status, sb.REQUEST_STATUS_CODES)
        if not problem:
            for value, field in ((date_from, "Başlangıç"), (date_to, "Bitiş")):
                problem = sb.date_error(value, field=field)
                if problem:
                    break
        if problem:
            return {"ok": False, "connected": None, "error": problem, "items": [],
                    "meta": {}}

        size = max(5, min(100, int(per_page) or self._page_size))
        try:
            payload = await self._api.quote_requests(
                status=codes or None, q=sb.text(q), date_from=sb.text(date_from),
                date_to=sb.text(date_to), page=max(1, int(page)), per_page=size)
        except Exception as failure:  # noqa: BLE001 — K7
            return self._read_failure(failure, what="talep listesi", items=[], meta={})

        rows = [sb.request_row(raw) for raw in self._items(payload)]
        meta = self._meta(payload)
        return {"ok": True, "connected": True, "error": "", "missing_endpoint": False,
                "items": rows,
                "meta": {**meta, "page": sb.as_int(meta.get("page"), max(1, int(page))),
                         "per_page": sb.as_int(meta.get("per_page"), size)},
                "page_counts": {code: sum(1 for row in rows if row["status"] == code)
                                for code in sb.REQUEST_STATUS_CODES},
                "server_time": self._server_time(payload)}

    async def request(self, request_id: int) -> dict[str, Any]:
        """Tek talep — MASKESİZ.

        Maskesiz okuma bilinçli bir sınırdır: arayacak kişinin numaraya
        ihtiyacı var. Kayıt SİLİNMEZ ve içeriği DEĞİŞTİRİLEMEZ; yazılabilen
        yalnız `status` ve `admin_note`.
        """
        try:
            payload = await self._api.quote_request(int(request_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return self._read_failure(failure, what="talep", request={},
                                      request_id=int(request_id))
        raw = self._record_of(payload, "data", "request")
        if not raw:
            return {"ok": False, "connected": True, "error": "Talep kaydı boş döndü.",
                    "request": {}, "request_id": int(request_id)}
        return {"ok": True, "connected": True, "error": "", "missing_endpoint": False,
                "request": sb.request_view(raw), "request_id": int(request_id)}

    async def audit(self, *, target_type: str = "", target_id: int = 0,
                    limit: int = 100) -> dict[str, Any]:
        """Bu EKRANDAN yapılan yazma denemelerinin YEREL izi.

        Sunucunun izi (`/api/control/audit`) ayrı bir sorunun cevabıdır ve ayrı
        bir modülün işidir. Buradaki satırlar sunucuya HİÇ ULAŞMAMIŞ denemeleri
        de içerir — tam olarak bu yüzden var. `price_kurus` dolu satırlar
        "fiyatı kim, ne zaman, neden anlaştı" sorusunu cevaplar.
        """
        count = max(1, min(500, int(limit) or 100))
        where = ""
        params: list[Any] = []
        if sb.text(target_type):
            where = "WHERE target_type = ?"
            params.append(sb.text(target_type))
            if int(target_id or 0):
                where += " AND target_id = ?"
                params.append(int(target_id))
        params.append(count)

        try:
            rows = await self._store.fetch_all(
                f"SELECT id, target_type, target_id, action, reason, actor, result, "
                f"price_kurus, detail, created_at FROM {self._audit} {where} "
                f"ORDER BY id DESC LIMIT ?", tuple(params))
        except Exception as failure:  # noqa: BLE001 — iz okunamadı, ekran ayakta (K7)
            self._log.warning("yerel iz okunamadı", error=str(failure))
            return {"ok": True, "connected": None, "error": "", "items": []}

        items = []
        for row in rows:
            try:
                detail = json.loads(row["detail"] or "{}")
            except (TypeError, ValueError):
                detail = {}
            items.append({
                "id": sb.as_int(row["id"]),
                "target_type": sb.text(row["target_type"]),
                "target_id": sb.as_int(row["target_id"]),
                "action": sb.text(row["action"]),
                "reason": sb.text(row["reason"]),
                "actor": sb.text(row["actor"]),
                "result": sb.text(row["result"]),
                "price_kurus": sb.as_money(row["price_kurus"]),
                "detail": detail,
                "created_at": sb.text(row["created_at"]),
            })
        return {"ok": True, "connected": None, "error": "", "items": items}

    # ------------------------------------------------------ ekran tercihi

    #: Yazılabilen tercihler. Kapalı liste: tanınmayan bir anahtarı kabul etmek,
    #: yazım hatasını sessizce diske yazıp hiçbir yerde kullanmamak olurdu.
    PREF_KEYS = ("page_size", "calendar_days", "expires_in_days")

    async def prefs(self) -> dict[str, Any]:
        """Ekran tercihleri: yerel kayıt varsa o, yoksa modül ayarı.

        Tercihler BLD'yi ETKİLEMEZ; yalnız bu ekranın ne gösterdiğini belirler.
        Tek istisna `expires_in_days`: o bir GÖNDERİM VARSAYILANIDIR ve
        gönderilirken sunucuya gider — kullanıcı her sözleşmede aynı sayıyı
        yeniden yazmasın diye burada durur, ama gerçek sınırı (1–30) sunucu
        tutar.
        """
        stored: dict[str, str] = {}
        try:
            rows = await self._store.fetch_all(f"SELECT key, value FROM {self._prefs}")
            stored = {str(row["key"]): str(row["value"]) for row in rows}
        except Exception as failure:  # noqa: BLE001 — tercih okunamadı, varsayılan yeter
            self._log.warning("tercih okunamadı", error=str(failure))

        return {
            "page_size": max(5, min(100, sb.as_int(stored.get("page_size"),
                                                   self._page_size))),
            "calendar_days": max(7, min(sb.MAX_CALENDAR_DAYS,
                                        sb.as_int(stored.get("calendar_days"),
                                                  self._calendar_days))),
            "expires_in_days": max(sb.MIN_EXPIRES_DAYS,
                                   min(sb.MAX_EXPIRES_DAYS,
                                       sb.as_int(stored.get("expires_in_days"),
                                                 self._expires_in_days))),
        }

    async def save_prefs(self, values: dict[str, Any], *, actor: str) -> dict[str, Any]:
        """Görüntüleme tercihini yazar. BLD'YE HİÇBİR ŞEY GİTMEZ.

        Bu bir iş yazması değildir ve gerekçe istemez: kimse "sayfa boyutunu 50
        yaptım" gerekçesini denetim izinde aramaz. Gerekçeyi burada da zorunlu
        kılmak, gerçek yazmalardaki gerekçe alışkanlığını törenselleştirirdi.
        """
        unknown = sorted(set(values or {}) - set(self.PREF_KEYS))
        if unknown:
            return {"ok": False, "error": f"Tanınmayan tercih: {', '.join(unknown)}."}

        stamp = sb.now_iso()
        for key, value in (values or {}).items():
            raw = "true" if value is True else "false" if value is False else sb.text(value)
            try:
                await self._store.execute(
                    f"INSERT INTO {self._prefs} (key, value, actor, updated_at) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                    "value = excluded.value, actor = excluded.actor, "
                    "updated_at = excluded.updated_at",
                    (key, raw, actor, stamp))
            except Exception as failure:  # noqa: BLE001 — K7
                self._log.warning("tercih yazılamadı", key=key, error=str(failure))
                return {"ok": False, "error": f"Tercih yazılamadı: {failure}"}
        return {"ok": True, "error": "", "prefs": await self.prefs()}

    # ================================================== yazma — ortak iskelet

    async def _write(self, *, action: str, reason: str, actor: str, call: Any,
                     target_type: str = "subscription", target_id: int = 0,
                     price_kurus: int | None = None, detail: dict[str, Any] | None = None,
                     dry: bool) -> dict[str, Any]:
        """Yazma zincirinin üç ortak adımı: iz → çağrı → iz.

        Gerekçe ve gövde denetimi ÇAĞIRANIN işidir ve bu metoda gelmeden önce
        biter: her yazmanın kendi kuralı var (dönem aralığı, duraklatma
        tarihleri, istisna tutarlılığı) ve onları burada toplamak, tek bir
        dev `if` merdiveni üretirdi.

        Dönen sözlük yazmanın ORTAK gövdesidir; çağıran kendi alanlarını
        ekler. `raw` ham yanıttır — kayıt biçimcisi çağıranın elindedir.
        """
        base = dict(detail or {})
        await self._record(action=action, reason=reason, actor=actor, result=TRIED,
                           target_type=target_type, target_id=target_id,
                           price_kurus=price_kurus, detail={**base, "dry_run": dry})
        try:
            payload = await call()
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action=action, reason=reason, actor=actor, result=FAILED,
                               target_type=target_type, target_id=target_id,
                               price_kurus=price_kurus,
                               detail={**base, "dry_run": dry, "error": str(failure)})
            return {"ok": False, "error": self._enrich(failure), "raw": None,
                    "code": self._code(failure)}

        await self._record(action=action, reason=reason, actor=actor,
                           result=DRY if dry else DONE, target_type=target_type,
                           target_id=target_id, price_kurus=price_kurus,
                           detail={**base, "audit_id": sb.as_int(payload.get("audit_id"))
                                   if isinstance(payload, dict) else 0})
        return {"ok": True, "error": "", "dry_run": dry, "raw": payload,
                "would": self._would(payload, dry),
                "warnings": sb.warnings_of(payload)}

    def _enrich(self, failure: Exception) -> str:
        """Geçidin Türkçe mesajına, bu alana özel bir cümle ekler.

        Kod → cümle eşlemesi BURADA durur çünkü aynı kod farklı alanlarda
        farklı şey anlatır: `conflict` sipariş alanında "zaten iptal edilmiş",
        burada "açık sözleşme var" ya da "dönem zaten borçlandırılmış"
        olabilir. Genel bir cümle yazmak, personelin ne yapacağını
        söylememekle aynı şey.
        """
        message = self._fail(failure)
        code = self._code(failure)
        if code == MISSING:
            return (f"{message} Bu uç sunucuya HENÜZ DAĞITILMADI; sunucu tarafı paralel "
                    "yazılıyor. Bekleyin, yeniden denemenin faydası yok.")
        if code == "conflict":
            return (f"{message} Ayrıntı yanıtta: aynı dönem zaten borçlandırılmış, açık "
                    "bir sözleşme duruyor ya da o gün için sipariş çoktan üretilmiş "
                    "olabilir. Listeyi tazeleyip bakın.")
        if code == "validation":
            return (f"{message} Sunucu ön denetimleri kuru provada da koşar: fiyatsız ya "
                    "da imzasız bir abonelik aktifleştirilemez.")
        return message

    # ================================================== yazma — abonelik

    async def create(self, *, customer_id: int, start_date: str, service_days: Any,
                     default_quantity: int, reason: str, actor: str,
                     delivery_type: str = "delivery", menu_mode: str = "daily_menu",
                     end_date: str = "", delivery_time_from: str = "",
                     delivery_time_to: str = "", agreed_unit_price_kurus: int | None = None,
                     lines: Any = None, delivery_points: Any = None,
                     location_id: int = 0, dry_run: bool | None = None) -> dict[str, Any]:
        """Yeni abonelik — `pending` DOĞAR.

        Fiyatı ve sözleşmesi tamamlanmadan üretim yapmamalı; aktifleştirme ayrı
        bir eylemdir ve ayrı bir denetim satırı bırakır (sözleşme).

        `payment_mode` GÖNDERİLMEZ ve seçilemez: tek geçerli değer
        `prepaid_monthly` (iş kararı 1 — cari hesap kalktı) ve geçit onu
        varsayılan olarak koyuyor. Ekrandan seçtirmek, seçilemeyen bir seçim
        kutusu çizmek olurdu.
        """
        problem = self._create_error(
            customer_id=customer_id, start_date=start_date, service_days=service_days,
            default_quantity=default_quantity, delivery_type=delivery_type,
            menu_mode=menu_mode, end_date=end_date, lines=lines,
            delivery_points=delivery_points, reason=reason)
        if problem:
            return {"ok": False, "error": problem}

        days, _ = sb.service_days(service_days)
        dry = self._dry(dry_run)
        price = sb.as_money(agreed_unit_price_kurus)
        result = await self._write(
            action="subscription.create", reason=reason, actor=actor, dry=dry,
            price_kurus=price,
            detail={"customer_id": int(customer_id), "service_days": days,
                    "menu_mode": sb.text(menu_mode)},
            call=lambda: self._api.create_subscription(
                customer_id=int(customer_id), start_date=sb.text(start_date),
                service_days=days, default_quantity=int(default_quantity),
                delivery_type=sb.text(delivery_type), menu_mode=sb.text(menu_mode),
                end_date=sb.text(end_date) or None,
                delivery_time_from=sb.text(delivery_time_from) or None,
                delivery_time_to=sb.text(delivery_time_to) or None,
                agreed_unit_price_kurus=price,
                # `daily_menu` iken `lines` GÖNDERİLEMEZ (sunucu 422 verir) ve
                # geçit de o kipte alanı gövdeye hiç koymuyor; burada boş liste
                # göndermek yeterli ve güvenli.
                lines=[dict(line) for line in (lines or [])],
                delivery_points=[dict(point) for point in (delivery_points or [])],
                location_id=int(location_id) or None,
                reason=sb.text(reason), actor=actor, dry_run=dry))
        if not result["ok"]:
            return result

        record = self._record_of(result["raw"], "data", "subscription")
        return {**{key: value for key, value in result.items() if key != "raw"},
                "subscription": sb.subscription_view(record) if record and not dry else {}}

    def _create_error(self, *, customer_id: int, start_date: str, service_days: Any,
                      default_quantity: int, delivery_type: str, menu_mode: str,
                      end_date: str, lines: Any, delivery_points: Any,
                      reason: str) -> str:
        """Yeni abonelik gövdesinin ön denetimi.

        Sunucu hepsini TEKRAR denetliyor (K9 — çift kapı); buradaki kapı ağ
        turunu ve anlamsız bir denetim satırını önler. Kurallar sözleşmeden
        alındı, uydurulmadı.
        """
        problem = sb.reason_error(reason)
        if problem:
            return problem
        if int(customer_id or 0) <= 0:
            return ("Müşteri kimliği zorunlu. Bu uç müşteri YARATMAZ: hesap açmak parola "
                    "ve e-posta doğrulaması ister ve o iş bu sözleşmenin dışında.")
        problem = sb.required_date_error(start_date, field="Başlangıç günü")
        if problem:
            return problem
        problem = sb.date_error(end_date, field="Bitiş günü")
        if problem:
            return problem
        if sb.text(end_date) and sb.text(end_date) <= sb.text(start_date):
            return "Bitiş günü başlangıçtan sonra olmalı."
        _days, problem = sb.service_days(service_days)
        if problem:
            return problem
        if int(default_quantity or 0) <= 0:
            return "Varsayılan porsiyon adedi en az 1 olmalı."
        problem = sb.choice_error(delivery_type, sb.DELIVERY_TYPES, field="Teslimat türü")
        if problem:
            return problem
        problem = sb.choice_error(menu_mode, sb.MENU_MODES, field="Menü kipi")
        if problem:
            return problem
        # SABİT LİSTE SEÇİP LİSTE VERMEMEK, hiçbir şey üretmeyen bir kural
        # yaratırdı; günün menüsü seçilip liste vermek ise o listenin sessizce
        # yok sayılması olurdu. İkisi de sunucuda 422.
        if sb.text(menu_mode) == "fixed_list" and not (lines or []):
            return "Sabit liste seçildi ama kalem verilmedi; günsüz bir kural gibi bu da "\
                   "hiçbir şey üretmez."
        if sb.text(menu_mode) == "daily_menu" and (lines or []):
            return "Günün menüsü seçiliyken kalem listesi gönderilemez: menü o günün "\
                   "yayınlanmış menüsünden gelir."
        if sb.text(delivery_type) == "delivery" and not (delivery_points or []):
            return "Adrese teslimde en az bir teslimat noktası gerekir."
        return ""

    async def update(self, subscription_id: int, *, reason: str, actor: str,
                     changes: dict[str, Any], dry_run: bool | None = None) -> dict[str, Any]:
        """Abonelik kuralını günceller — KISMİ.

        `customer_id`, `location_id`, `start_date` ve `status` YAZILAMAZ:
        müşteriyi değiştirmek yeni abonelik açmaktır ve durum kendi
        uçlarındadır (sözleşme).

        KURAL DEĞİŞİKLİĞİ ÜRETİLMİŞ SİPARİŞLERİ ETKİLEMEZ. Yarın için sipariş
        zaten üretildiyse bugün adedi değiştirmek onu değiştirmez; sunucu bunu
        `warnings.generated_orders_unaffected` ile söyler ve bu ekran o uyarıyı
        OLDUĞU GİBİ yukarı taşır. Yutulsaydı yönetici yarının siparişini
        değiştirdiğini sanırdı.
        """
        problem = sb.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}
        clean, problem = self._update_changes(changes)
        if problem:
            return {"ok": False, "error": problem}
        if not clean:
            return {"ok": False, "error": "Değişen bir alan yok; aynı kuralı yeniden "
                                          "yazmak denetim izine boş bir satır eklerdi."}
        dry = self._dry(dry_run)
        price = sb.as_money(clean.get("agreed_unit_price_kurus"))
        result = await self._write(
            action="subscription.update", reason=reason, actor=actor, dry=dry,
            target_id=int(subscription_id), price_kurus=price,
            detail={"fields": sorted(clean)},
            call=lambda: self._api.update_subscription(
                int(subscription_id), reason=sb.text(reason), actor=actor, dry_run=dry,
                **clean))
        if not result["ok"]:
            return result
        record = self._record_of(result["raw"], "data", "subscription")
        return {**{key: value for key, value in result.items() if key != "raw"},
                "subscription": sb.subscription_view(record) if record and not dry else {}}

    #: `PATCH` ile yazılabilen alanlar (sözleşme). Liste KAPALIDIR: tanınmayan
    #: bir anahtarı geçide yollamak, `_patch()` onu gövdeye koyduğu için
    #: sunucuda 422 üretirdi ve hata "hangi alan" demezdi.
    UPDATE_KEYS = ("end_date", "delivery_type", "delivery_time_from", "delivery_time_to",
                   "service_days", "menu_mode", "default_quantity",
                   "agreed_unit_price_kurus", "lines", "delivery_points")

    def _update_changes(self, changes: dict[str, Any]) -> tuple[dict[str, Any], str]:
        """Kısmi güncelleme gövdesini süzer ve denetler."""
        unknown = sorted(set(changes or {}) - set(self.UPDATE_KEYS))
        if unknown:
            return {}, (f"Bu alanlar abonelik kuralında değiştirilemez: {', '.join(unknown)}. "
                        "Müşteri, konum ve başlangıç günü yazılamaz; durum kendi "
                        "uçlarındadır.")
        clean: dict[str, Any] = {}
        for key, value in (changes or {}).items():
            if key == "service_days":
                days, problem = sb.service_days(value)
                if problem:
                    return {}, problem
                clean[key] = days
            elif key == "end_date":
                problem = sb.date_error(value, field="Bitiş günü")
                if problem:
                    return {}, problem
                clean[key] = sb.text(value) or None
            elif key in ("delivery_type", "menu_mode"):
                allowed = sb.DELIVERY_TYPES if key == "delivery_type" else sb.MENU_MODES
                field = "Teslimat türü" if key == "delivery_type" else "Menü kipi"
                problem = sb.choice_error(value, allowed, field=field)
                if problem:
                    return {}, problem
                clean[key] = sb.text(value)
            elif key == "default_quantity":
                quantity = sb.as_int(value, 0)
                if quantity <= 0:
                    return {}, "Varsayılan porsiyon adedi en az 1 olmalı."
                clean[key] = quantity
            elif key == "agreed_unit_price_kurus":
                price = sb.as_money(value)
                if price is None or price < 0:
                    return {}, ("Anlaşılan birim fiyat kuruş tam sayı olmalı; boş "
                                "bırakmak fiyatı SİLMEZ, alanı hiç göndermemek gerekir.")
                clean[key] = price
            elif key in ("lines", "delivery_points"):
                if not isinstance(value, list):
                    return {}, f"'{key}' bir liste olmalı."
                # TAM LİSTEDİR: `id` taşıyan satır güncellenir, taşımayan
                # eklenir, listede olmayan SİLİNİR (sözleşme). Fark göndermek
                # gönderilmeyen satırları sessizce düşürürdü.
                clean[key] = [dict(item) for item in value if isinstance(item, dict)]
            else:
                clean[key] = sb.text(value)
        return clean, ""

    async def activate(self, subscription_id: int, *, reason: str,
                       actor: str, dry_run: bool | None = None) -> dict[str, Any]:
        """Aboneliği aktifleştirir — ÖN DENETİMLER SUNUCUDA.

        Fiyat dolu olmalı ve İMZALI SÖZLEŞME bulunmalı (iş kararı 9). İkisini
        burada tekrar denetlemek cazip ama yanlış olurdu: taze okuma bir tur
        daha ağ demek ve karar yine sunucunun. Sunucu reddederse kod
        `validation` gelir ve `_enrich` cümleyi tamamlar.
        """
        return await self._status_write(
            subscription_id, action="subscription.activate", to="active", reason=reason,
            actor=actor, dry_run=dry_run,
            call=lambda dry: self._api.activate_subscription(
                int(subscription_id), reason=sb.text(reason), actor=actor, dry_run=dry))

    async def pause(self, subscription_id: int, *, start_date: str, end_date: str,
                    reason: str, actor: str, pause_reason: str = "",
                    dry_run: bool | None = None) -> dict[str, Any]:
        """Aboneliği aralıklı duraklatır — İPTAL DEĞİLDİR.

        Aralık boyunca üretim durur, sonra AYNI FİYATLA devam eder. Aralıktaki
        üretilmiş siparişler OTOMATİK İPTAL EDİLMEZ; sunucu onları
        `warnings.generated_orders_in_range` ile listeler ve yönetici tek tek
        karar verir (iptalleri `bld_orders` işidir).
        """
        problem = sb.reason_error(reason) or sb.pause_error(start_date, end_date)
        if problem:
            return {"ok": False, "error": problem}
        return await self._status_write(
            subscription_id, action="subscription.pause", to="paused", reason=reason,
            actor=actor, dry_run=dry_run,
            detail={"start_date": sb.text(start_date), "end_date": sb.text(end_date)},
            call=lambda dry: self._api.pause_subscription(
                int(subscription_id), start_date=sb.text(start_date),
                end_date=sb.text(end_date), pause_reason=sb.text(pause_reason) or None,
                reason=sb.text(reason), actor=actor, dry_run=dry))

    async def resume(self, subscription_id: int, *, reason: str, actor: str,
                     dry_run: bool | None = None) -> dict[str, Any]:
        """Duraklamayı bitirir. Satırı SİLMEZ: "ne zaman duraklatıldı, ne zaman
        devam edildi" sorusunun cevabı kalmalı."""
        return await self._status_write(
            subscription_id, action="subscription.resume", to="active", reason=reason,
            actor=actor, dry_run=dry_run,
            call=lambda dry: self._api.resume_subscription(
                int(subscription_id), reason=sb.text(reason), actor=actor, dry_run=dry))

    async def cancel(self, subscription_id: int, *, effective_date: str, reason: str,
                     actor: str, dry_run: bool | None = None) -> dict[str, Any]:
        """Aboneliği iptal eder — GERİ DÖNÜŞÜ YOKTUR.

        Yeniden başlatmak yeni abonelik açmaktır: iptal edilmiş bir kuralı
        canlandırmak, iptal tarihinden sonraki günlerin hangi kurala tabi
        olduğunu belirsiz kılardı (sözleşme).

        İPTAL PARA ÜRETMEZ ve bu yüzden ayrı bir izin anahtarı YOKTUR
        (`bld_orders.cancel`den ayrılan yer burası): abonelik iptali üretilmiş
        siparişlere DOKUNMAZ, onları düşürmek `bld_orders.cancel` iznini ister
        ve orada iade kaydı doğar. Yıkıcılığın karşılığı burada gerekçe + çift
        denetim satırıdır (ADR 0012).
        """
        problem = sb.reason_error(reason) or sb.required_date_error(
            effective_date, field="Geçerlilik günü")
        if problem:
            return {"ok": False, "error": problem}
        return await self._status_write(
            subscription_id, action="subscription.cancel", to="cancelled", reason=reason,
            actor=actor, dry_run=dry_run,
            detail={"effective_date": sb.text(effective_date)},
            call=lambda dry: self._api.cancel_subscription(
                int(subscription_id), effective_date=sb.text(effective_date),
                reason=sb.text(reason), actor=actor, dry_run=dry))

    async def _status_write(self, subscription_id: int, *, action: str, to: str,
                            reason: str, actor: str, call: Any,
                            detail: dict[str, Any] | None = None,
                            dry_run: bool | None = None) -> dict[str, Any]:
        """Dört durum ucunun ortak gövdesi.

        TAZE OKUMA yapılır: `from` alanı olayın yükünde ve denetim izinde
        duruyor ve "hangi durumdan çıkıldı" sorusunun cevabı, isteğin çıktığı
        andaki durum olmalı. Okuma başarısız olursa yazma DURMAZ — okumanın
        düşmesi yazmayı engellememeli, yalnız `from` bilinmez kalır.
        """
        problem = sb.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}
        dry = self._dry(dry_run)

        fresh = await self.subscription(subscription_id)
        before = sb.text(fresh.get("subscription", {}).get("status"))

        result = await self._write(
            action=action, reason=reason, actor=actor, dry=dry,
            target_id=int(subscription_id),
            detail={**(detail or {}), "from": before, "to": to},
            call=lambda: call(dry))
        if not result["ok"]:
            return result

        record = self._record_of(result["raw"], "data", "subscription")
        payload = {key: value for key, value in result.items() if key != "raw"}
        payload["subscription"] = sb.subscription_view(record) if record and not dry else {}
        if dry:
            payload["announced"] = False
            return payload

        # KURU PROVADA OLAY YAYINLANMAZ: BLD'de hiçbir şey değişmedi,
        # dinleyicileri "durum değişti" diye uyandırmak yalan olurdu.
        await self._announce(STATUS_EVENT, {
            "subscriptionId": int(subscription_id), "from": before, "to": to,
            "action": action, "reason": sb.text(reason), "actor": actor})
        payload["announced"] = True
        return payload

    # ================================================== yazma — istisna/üretim

    async def create_exception(self, subscription_id: int, *, service_date: str,
                               reason: str, actor: str, skip: bool = False,
                               quantity_override: int | None = None, note: str = "",
                               dry_run: bool | None = None) -> dict[str, Any]:
        """Tek-gün istisnası: "yarın 20 değil 12" ya da "yarın atla".

        KURAL DEĞİŞİKLİĞİ DEĞİLDİR. Aynı gün için ikinci istisna ÜZERİNE YAZILIR
        ve `409` verilmez — yönetici aynı güne iki kez karar verebilir, son
        karar geçerlidir ve denetim izinde ikisi de görünür.
        """
        problem = sb.reason_error(reason) or sb.required_date_error(
            service_date, field="Servis günü")
        if problem:
            return {"ok": False, "error": problem}
        if skip and quantity_override is not None:
            # Geçit de kesiyor; burada kesmek kullanıcıya kendi cümlesini
            # gösterir ve boş bir denetim satırı yazılmasını önler.
            return {"ok": False, "error": "«Atla» seçiliyken adet yazılamaz: «atla ama 12 "
                                          "yap» tutarsız bir kayıt olurdu."}
        if not skip and (quantity_override is None or int(quantity_override) <= 0):
            return {"ok": False, "error": "Ya günü atlayın ya da 1'den büyük bir adet "
                                          "yazın; ikisi de boş bir istisna hiçbir şeyi "
                                          "değiştirmez."}
        dry = self._dry(dry_run)
        result = await self._write(
            action="subscription.exception.create", reason=reason, actor=actor, dry=dry,
            target_id=int(subscription_id),
            detail={"service_date": sb.text(service_date), "skip": bool(skip),
                    "quantity_override": quantity_override},
            call=lambda: self._api.create_subscription_exception(
                int(subscription_id), service_date=sb.text(service_date), skip=bool(skip),
                quantity_override=None if skip else int(quantity_override or 0),
                note=sb.text(note) or None, reason=sb.text(reason), actor=actor,
                dry_run=dry))
        if not result["ok"]:
            return result
        record = self._record_of(result["raw"], "data", "exception")
        return {**{key: value for key, value in result.items() if key != "raw"},
                "exception": sb.exception_row(record) if record and not dry else {}}

    async def delete_exception(self, subscription_id: int, service_date: str, *,
                               reason: str, actor: str,
                               dry_run: bool | None = None) -> dict[str, Any]:
        """İstisnayı kaldırır — GERÇEK SİLME.

        Sözleşme burada bilinçli olarak silmeyi seçiyor: istisna bir BELGE
        değil bir KURALDIR ve pasifleştirilmiş bir kural, uygulanıp
        uygulanmadığı belirsiz bir kayıt olurdu. Denetim izi yine de kalır
        (hem burada hem sunucuda).
        """
        problem = sb.reason_error(reason) or sb.required_date_error(
            service_date, field="Servis günü")
        if problem:
            return {"ok": False, "error": problem}
        dry = self._dry(dry_run)
        result = await self._write(
            action="subscription.exception.delete", reason=reason, actor=actor, dry=dry,
            target_id=int(subscription_id), detail={"service_date": sb.text(service_date)},
            call=lambda: self._api.delete_subscription_exception(
                int(subscription_id), sb.text(service_date), reason=sb.text(reason),
                actor=actor, dry_run=dry))
        return {key: value for key, value in result.items() if key != "raw"}

    async def generate(self, subscription_id: int, *, service_date: str, reason: str,
                       actor: str, release_now: bool = False,
                       dry_run: bool | None = None) -> dict[str, Any]:
        """Gece işini beklemeden belirli bir gün için üretim yapar.

        Kural gece işiyle AYNIDIR (`SubscriptionGenerateCommand`), ayrı bir
        kopya yazılmaz. Defterde satır varsa `conflict` gelir: idempotency
        VERİTABANI KISITINDAN doğar ve uç onu 500 yerine 409'a çevirir.

        `release_now=True` üretilen siparişi ANINDA KDS'e düşürür; varsayılan
        `False`, yani normal serbest bırakma saati (07:00) geçerlidir. Ekran
        bunu açıkça soruyor: sessizce erken düşen kırk sipariş, sabah işbaşı
        yapan mutfağın panosunu doldurup o an gelen gerçek bir siparişi
        görünmez kılardı.
        """
        problem = sb.reason_error(reason) or sb.required_date_error(
            service_date, field="Servis günü")
        if problem:
            return {"ok": False, "error": problem}
        dry = self._dry(dry_run)
        result = await self._write(
            action="subscription.generate", reason=reason, actor=actor, dry=dry,
            target_id=int(subscription_id),
            detail={"service_date": sb.text(service_date),
                    "release_now": bool(release_now)},
            call=lambda: self._api.generate_subscription_orders(
                int(subscription_id), service_date=sb.text(service_date),
                release_now=bool(release_now), reason=sb.text(reason), actor=actor,
                dry_run=dry))
        if not result["ok"]:
            return result

        data = self._record_of(result["raw"], "data")
        return {**{key: value for key, value in result.items() if key != "raw"},
                "service_date": sb.text(data.get("service_date")) or sb.text(service_date),
                "created": [dict(item) for item in data.get("created") or []
                            if isinstance(item, dict)],
                # BOŞ `skipped` LİSTESİ SESSİZ KALMAZ: ekran "üretilmedi"
                # satırlarını da yazar, yoksa "neden sipariş yok" sorusunun
                # cevabı hiçbir yerde durmaz.
                "skipped": [dict(item) for item in data.get("skipped") or []
                            if isinstance(item, dict)]}

    async def release_order(self, order_id: int, *, reason: str, actor: str,
                            dry_run: bool | None = None) -> dict[str, Any]:
        """Üretilmiş bir siparişi KDS'e ERKEN düşürür.

        Abonelik siparişleri gece üretilir ve mutfağa 07:00'de düşer (iş
        kararı 7). Zaten serbest bırakılmışsa sunucu `ok: true` döner ve `409`
        VERMEZ — bu bir hata değil, "zaten yapılmış" durumudur.

        Denetim hedefi SİPARİŞTİR, abonelik değil (`subscriptions.md` denetim
        tablosu): soru "bu sipariş neden erken düştü" biçiminde sorulur.
        """
        problem = sb.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}
        dry = self._dry(dry_run)
        result = await self._write(
            action="subscription.order.release", reason=reason, actor=actor, dry=dry,
            target_type="order", target_id=int(order_id),
            call=lambda: self._api.release_subscription_order(
                int(order_id), reason=sb.text(reason), actor=actor, dry_run=dry))
        if not result["ok"]:
            return result
        data = self._record_of(result["raw"], "data")
        return {**{key: value for key, value in result.items() if key != "raw"},
                "order_id": sb.as_int(data.get("order_id"), int(order_id)),
                "released_at": sb.text(data.get("released_at")),
                "was_scheduled_for": sb.text(data.get("was_scheduled_for"))}

    # ================================================== yazma — talepler

    async def update_request(self, request_id: int, *, reason: str, actor: str,
                             status: str = "", admin_note: str | None = None,
                             dry_run: bool | None = None) -> dict[str, Any]:
        """Talebin durumu ve İÇ NOTU. Ziyaretçinin yazdığı içerik değişmez."""
        problem = sb.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}
        problem = sb.choice_error(status, sb.REQUEST_STATUS_CODES, field="Talep durumu")
        if problem:
            return {"ok": False, "error": problem}
        changes: dict[str, Any] = {}
        if sb.text(status):
            changes["status"] = sb.text(status)
        if admin_note is not None:
            changes["admin_note"] = sb.text(admin_note)
        if not changes:
            return {"ok": False, "error": "Değişen bir alan yok."}

        dry = self._dry(dry_run)
        result = await self._write(
            action="subscription.request.update", reason=reason, actor=actor, dry=dry,
            target_type="quote_request", target_id=int(request_id),
            detail={"fields": sorted(changes)},
            call=lambda: self._api.update_quote_request(
                int(request_id), reason=sb.text(reason), actor=actor, dry_run=dry,
                **changes))
        if not result["ok"]:
            return result
        record = self._record_of(result["raw"], "data", "request")
        return {**{key: value for key, value in result.items() if key != "raw"},
                "request": sb.request_view(record) if record and not dry else {}}

    async def convert_request(self, request_id: int, *, customer_id: int,
                              subscription: dict[str, Any], reason: str, actor: str,
                              dry_run: bool | None = None) -> dict[str, Any]:
        """Talebi aboneliğe çevirir. TALEP SİLİNMEZ.

        `status = kapandi` olur ve `converted_subscription_id` dolar. Abonelik
        yine `pending` DOĞAR: sözleşme imzalanmadan aktifleşmez.

        `subscription` bloğu `create` gövdesiyle AYNI doğrulamalardan geçer ve
        o doğrulama burada tekrar yazılmaz — `_create_error` paylaşılıyor.
        Ayrı iki denetim, talepten açılan aboneliğin elle açılandan farklı
        kurallara tabi olması demekti.
        """
        block = dict(subscription or {})
        problem = self._create_error(
            customer_id=customer_id, start_date=block.get("start_date", ""),
            service_days=block.get("service_days"),
            default_quantity=sb.as_int(block.get("default_quantity")),
            delivery_type=sb.text(block.get("delivery_type")) or "delivery",
            menu_mode=sb.text(block.get("menu_mode")) or "daily_menu",
            end_date=sb.text(block.get("end_date")), lines=block.get("lines"),
            delivery_points=block.get("delivery_points"), reason=reason)
        if problem:
            return {"ok": False, "error": problem}

        days, _ = sb.service_days(block.get("service_days"))
        price = sb.as_money(block.get("agreed_unit_price_kurus"))
        body = {
            "start_date": sb.text(block.get("start_date")),
            "end_date": sb.text(block.get("end_date")) or None,
            "delivery_type": sb.text(block.get("delivery_type")) or "delivery",
            "delivery_time_from": sb.text(block.get("delivery_time_from")) or None,
            "delivery_time_to": sb.text(block.get("delivery_time_to")) or None,
            "service_days": days,
            "menu_mode": sb.text(block.get("menu_mode")) or "daily_menu",
            "default_quantity": sb.as_int(block.get("default_quantity")),
            "agreed_unit_price_kurus": price,
            # TEK GEÇERLİ DEĞER (iş kararı 1). Sözleşme bu bloğu `POST /`
            # gövdesiyle aynı doğrulamadan geçiriyor ve orada alan zorunlu.
            "payment_mode": "prepaid_monthly",
            "delivery_points": [dict(point) for point
                                in block.get("delivery_points") or []],
        }
        if sb.text(block.get("menu_mode")) == "fixed_list":
            body["lines"] = [dict(line) for line in block.get("lines") or []]
        if sb.as_int(block.get("location_id")):
            body["location_id"] = sb.as_int(block.get("location_id"))

        dry = self._dry(dry_run)
        result = await self._write(
            action="subscription.request.convert", reason=reason, actor=actor, dry=dry,
            target_type="quote_request", target_id=int(request_id), price_kurus=price,
            detail={"customer_id": int(customer_id), "service_days": days},
            call=lambda: self._api.convert_quote_request(
                int(request_id), customer_id=int(customer_id), subscription=body,
                reason=sb.text(reason), actor=actor, dry_run=dry))
        if not result["ok"]:
            return result
        data = self._record_of(result["raw"], "data")
        created = self._record_of(data, "subscription")
        return {**{key: value for key, value in result.items() if key != "raw"},
                "request_id": sb.as_int(data.get("request_id"), int(request_id)),
                "request_status": sb.text(data.get("request_status")),
                "subscription_id": sb.as_int(created.get("id")),
                "subscription_status": sb.text(created.get("status"))}

    # ================================================== yazma — sözleşmeler

    async def create_contract(self, subscription_id: int, *, reason: str, actor: str,
                              phone: str = "", expires_in_days: int = 0,
                              send_sms: bool = True,
                              dry_run: bool | None = None) -> dict[str, Any]:
        """Sözleşme oluşturur ve imza bağlantısını gönderir.

        BU EKRAN OTP AKIŞI KURMAZ (K3). Sözleşme bir PDF değil, tek kullanımlık
        bir bağlantıdır: müşteri açar, metni okur, telefonuna gelen kodu
        SUNUCUNUN sayfasında girer ve onaylar. Kontrol Merkezi yalnız tetikler.

        `sign_url` YALNIZ `send_sms=False` iken doludur ve yanıtta öyle gelir.
        SMS gönderildiğinde `null` döner: bağlantı zaten müşterinin telefonunda
        ve panelde de göstermek onu ikinci bir yerde sızdırılabilir kılardı.
        Bu servis bağlantıyı YEREL İZE YAZMAZ — denetim satırında duran bir
        imza bağlantısı, tam olarak sunucunun engellediği şey olurdu.
        """
        problem = sb.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}
        days = int(expires_in_days) or self._expires_in_days
        if days < sb.MIN_EXPIRES_DAYS or days > sb.MAX_EXPIRES_DAYS:
            return {"ok": False,
                    "error": f"Bağlantı ömrü {sb.MIN_EXPIRES_DAYS}–{sb.MAX_EXPIRES_DAYS} "
                             "gün arası olmalı; süresiz bir imza bağlantısı bir yıl sonra "
                             "ele geçtiğinde hâlâ geçerli olurdu."}
        dry = self._dry(dry_run)
        result = await self._write(
            action="subscription.contract.create", reason=reason, actor=actor, dry=dry,
            target_type="subscription_contract", target_id=int(subscription_id),
            # TELEFON İZE YAZILMAZ, yalnız verilip verilmediği yazılır: yerel iz
            # kişisel veri deposu değil ve numara zaten sunucudaki kayıtta.
            detail={"expires_in_days": days, "send_sms": bool(send_sms),
                    "phone_given": bool(sb.text(phone))},
            call=lambda: self._api.create_subscription_contract(
                int(subscription_id), phone=sb.text(phone), expires_in_days=days,
                send_sms=bool(send_sms), reason=sb.text(reason), actor=actor,
                dry_run=dry))
        if not result["ok"]:
            return result
        data = self._record_of(result["raw"], "data", "contract")
        return {**{key: value for key, value in result.items() if key != "raw"},
                "contract_id": sb.as_int(data.get("id")),
                "status": sb.text(data.get("status")),
                "expires_at": sb.text(data.get("expires_at")),
                "sms_sent": bool(data.get("sms_sent")),
                # Elden iletilecek bağlantı EKRANDA gösterilir ama HİÇBİR YERE
                # yazılmaz: `send_sms=False` seçen yönetici onu kopyalayacak.
                "sign_url": sb.text(data.get("sign_url"))}

    async def resend_contract(self, contract_id: int, *, reason: str, actor: str,
                              expires_in_days: int = 0,
                              dry_run: bool | None = None) -> dict[str, Any]:
        """Aynı sözleşmenin bağlantısını yeniden gönderir ve süresini tazeler.

        YENİ TOKEN ÜRETİLMEZ: müşterinin elindeki eski SMS'in çalışmaya devam
        etmesi, "hangi linke tıklayacağım" sorusunu ortadan kaldırır (sözleşme).
        """
        problem = sb.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}
        days = int(expires_in_days) or self._expires_in_days
        if days < sb.MIN_EXPIRES_DAYS or days > sb.MAX_EXPIRES_DAYS:
            return {"ok": False,
                    "error": f"Bağlantı ömrü {sb.MIN_EXPIRES_DAYS}–{sb.MAX_EXPIRES_DAYS} "
                             "gün arası olmalı."}
        dry = self._dry(dry_run)
        result = await self._write(
            action="subscription.contract.resend", reason=reason, actor=actor, dry=dry,
            target_type="subscription_contract", target_id=int(contract_id),
            detail={"expires_in_days": days},
            call=lambda: self._api.resend_subscription_contract(
                int(contract_id), expires_in_days=days, reason=sb.text(reason),
                actor=actor, dry_run=dry))
        if not result["ok"]:
            return result
        data = self._record_of(result["raw"], "data", "contract")
        return {**{key: value for key, value in result.items() if key != "raw"},
                "contract_id": sb.as_int(data.get("id"), int(contract_id)),
                "status": sb.text(data.get("status")),
                "expires_at": sb.text(data.get("expires_at"))}

    async def cancel_contract(self, contract_id: int, *, reason: str, actor: str,
                              dry_run: bool | None = None) -> dict[str, Any]:
        """Sözleşmeyi iptal eder. İMZALANMIŞ SÖZLEŞMEDE `conflict`.

        İmzalanmış bir sözleşmeyi iptal edilmiş göstermek, imzanın kendisini
        geçersiz kılmaktır; yeni koşullar YENİ BİR SÖZLEŞME gerektirir
        (sözleşme). Kapıyı sunucu tutar; ekran düğmeyi gizler ve nedenini yazar.
        """
        problem = sb.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}
        dry = self._dry(dry_run)
        result = await self._write(
            action="subscription.contract.cancel", reason=reason, actor=actor, dry=dry,
            target_type="subscription_contract", target_id=int(contract_id),
            call=lambda: self._api.cancel_subscription_contract(
                int(contract_id), reason=sb.text(reason), actor=actor, dry_run=dry))
        if not result["ok"]:
            return result
        data = self._record_of(result["raw"], "data", "contract")
        return {**{key: value for key, value in result.items() if key != "raw"},
                "contract_id": sb.as_int(data.get("id"), int(contract_id)),
                "status": sb.text(data.get("status"))}

    # ================================================== yazma — ödemeler

    async def create_payment(self, subscription_id: int, *, period_start: str,
                             period_end: str, due_date: str, reason: str, actor: str,
                             amount_kurus: int | None = None, note: str = "",
                             dry_run: bool | None = None) -> dict[str, Any]:
        """Dönem borcu oluşturur.

        `amount_kurus` BOŞ GÖNDERİLİRSE SUNUCU HESAPLAR: dönemdeki üretilmiş ve
        iptal edilmemiş siparişlerin toplamı. Elle tutar yazmak serbesttir ama
        varsayılan hesaplanmış olmalı — yönetici her ay çarpma yapmamalı.
        Yanıttaki `amount_source` ikisini ayırır ve "bu tutar nereden geldi"
        sorusunun cevabıdır.

        KURU PROVA BURADA GERÇEKTEN İŞE YARAR: hesap yapılır ve `order_count`
        ile döner, hiçbir satır yazılmadan. Yöneticinin borcu yazmadan önce
        görmesi gereken tam olarak budur.
        """
        problem = sb.reason_error(reason) or sb.period_error(period_start, period_end,
                                                             due_date)
        if problem:
            return {"ok": False, "error": problem}
        amount = sb.as_money(amount_kurus)
        if amount is not None and amount <= 0:
            return {"ok": False, "error": "Elle yazılan tutar sıfırdan büyük olmalı; "
                                          "hesaplatmak için alanı boş bırakın."}
        dry = self._dry(dry_run)
        result = await self._write(
            action="subscription.payment.create", reason=reason, actor=actor, dry=dry,
            target_type="subscription_payment", target_id=int(subscription_id),
            price_kurus=amount,
            detail={"period_start": sb.text(period_start),
                    "period_end": sb.text(period_end),
                    "amount_source": "manual" if amount is not None else "calculated"},
            call=lambda: self._api.create_subscription_payment(
                int(subscription_id), period_start=sb.text(period_start),
                period_end=sb.text(period_end), due_date=sb.text(due_date),
                amount_kurus=amount, note=sb.text(note) or None, reason=sb.text(reason),
                actor=actor, dry_run=dry))
        if not result["ok"]:
            return result
        data = self._record_of(result["raw"], "data", "payment")
        return {**{key: value for key, value in result.items() if key != "raw"},
                "payment": sb.payment_row(data) if data and not dry else {},
                "amount_kurus": sb.as_int(data.get("amount_kurus")),
                "amount_source": sb.text(data.get("amount_source")),
                "order_count": sb.as_int(data.get("order_count"))}

    async def mark_paid(self, payment_id: int, *, method: str, reason: str, actor: str,
                        paid_at: str = "", reference: str = "",
                        create_invoice: bool = False, subscription_id: int = 0,
                        dry_run: bool | None = None) -> dict[str, Any]:
        """Dönem borcunu TAHSİL EDİLDİ işaretler.

        GERİ ALMA UCU YOKTUR. Yanlış işaretlenen bir tahsilat yeni bir dönem
        kaydıyla düzeltilir; para defterinde silme yoktur (sözleşme). Bu
        yüzden ekran gerekçe ister ve iki denetim satırı yazar.

        Zaten `paid` ise sunucu `409` verir: ikinci kez tahsil işaretlemek
        tutarı iki kez saydırırdı. Aynı kural, müşterinin uygulamadan ödediği
        yolu da kapsayacak (`_devralinan-odeme-yapisi.md`).
        """
        problem = sb.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}
        if sb.text(method) not in sb.PAYMENT_METHODS:
            return {"ok": False,
                    "error": f"Ödeme yöntemi zorunlu ve yalnız "
                             f"{', '.join(sb.PAYMENT_METHODS)} olabilir."}
        # GELECEK BİR AN REDDEDİLİR (sözleşme). Karşılaştırma dize üzerindedir
        # ve YALNIZ sözleşmenin biçiminde (`...Z`, UTC) geçerlidir; başka bir
        # ofsetle gelen bir damgayı burada çözmeye çalışmak, saat dilimi
        # aritmetiğini ekranda ikinci kez yazmak olurdu. Bu yüzden kapı
        # biçimden emin olmadıkça HİÇ KAPANMAZ ve asıl denetim sunucudadır:
        # yanlış yorumlanmış bir damgayla reddedilen geçerli bir tahsilat,
        # sunucuya hiç ulaşmadığı için teşhis edilemezdi.
        if sb.utc_stamp_in_future(paid_at):
            return {"ok": False, "error": "Tahsilat anı gelecekte olamaz; boş bırakılırsa "
                                          "sunucu şimdiyi yazar."}
        dry = self._dry(dry_run)
        result = await self._write(
            action="subscription.payment.paid", reason=reason, actor=actor, dry=dry,
            target_type="subscription_payment", target_id=int(payment_id),
            detail={"method": sb.text(method), "create_invoice": bool(create_invoice),
                    "subscription_id": int(subscription_id)},
            call=lambda: self._api.mark_subscription_payment_paid(
                int(payment_id), method=sb.text(method), paid_at=sb.text(paid_at),
                reference=sb.text(reference), create_invoice=bool(create_invoice),
                reason=sb.text(reason), actor=actor, dry_run=dry))
        if not result["ok"]:
            return result

        data = self._record_of(result["raw"], "data", "payment")
        payload = {key: value for key, value in result.items() if key != "raw"}
        payload.update({
            "payment": sb.payment_row(data) if data and not dry else {},
            "invoice_id": sb.as_int(data.get("invoice_id")),
            "invoice_no": sb.text(data.get("invoice_no")),
        })
        if dry:
            payload["announced"] = False
            return payload

        await self._announce(PAID_EVENT, {
            "subscriptionId": int(subscription_id), "paymentId": int(payment_id),
            "amountKurus": sb.as_int(data.get("amount_kurus")),
            "method": sb.text(method), "invoiceId": sb.as_int(data.get("invoice_id")),
            "actor": actor})
        payload["announced"] = True
        return payload
