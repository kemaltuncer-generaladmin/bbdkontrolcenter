"""İadeler — iş kuralları.

VERİ MAĞAZADADIR, KARAR BURADADIR. Kredi notu, sipariş kalemi ve POS denemesi
`store.api` geçidinden gelir (K4); bu modül kopya tutmaz. Yerel tablolar yalnız
mağazada KARŞILIĞI OLMAYAN iki şeyi saklar: yazma gerekçesi (denetim izi) ve
ONAYA SUNULAN HESABIN KENDİSİ (jeton) — uygulanan şeyin önizlenen şey olduğunu
kanıtlar.

İKİ KAYNAK. Bagisto'nun kredi notu (`refunds`) parayı, BBD'nin RMA talebi
(`bbd_return_requests`) süreci temsil eder. Bu ekranın kullandığı BBD uçları
CANLIDA YAYINDA (ölçüm 2026-08-16: `return-requests` ve `shipments` 200
dönüyor); bir dönem hepsi 404'tü ve kod bunu varsayıyordu. Buna rağmen eksik-uç
dalı DURUYOR ve durmalı: uç geri çekilirse ya da mağaza sürümü değişirse geçit
`bbd_endpoint_missing` döner, o bölüm ekranda "uç hazır olunca açılacak" diye
KAPALI görünür ve ekranın geri kalanı ayakta kalır — sessizce patlamaz (K7).

ÖLÇEK. "Yüzler" mertebesinde kayıt; süzme istemcidedir. Ekran açılırken her
kaynağın YALNIZ İLK SAYFASI (en yeni kayıtlar) çekilir; pencerenin tamamı ancak
kullanıcı süzgeç kullanınca ya da "Tümünü yükle" deyince taranır. Rapor ve CSV
her zaman tamamını tarar. Liste yanıtı kısa ömürlü önbellekte durur (TEK UÇUŞ);
başarılı bir yazmadan sonra önbellek boşaltılır.

PARA. İçeride her yerde KURUŞ (int). Telde ondalık metin gider (`from_kurus`).
POS denemesi ise tutarı ZATEN kuruş tam sayısı olarak verir (`amount_minor`).
"""

from __future__ import annotations

import asyncio
import base64
import json
import shutil
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from km_sdk import (
    ExportError,
    build_pdf,
    csv_bytes,
    money,
    number,
    print_report_file,
    printer_status_of,
    report_dir,
    write_private,
)

from . import calc

#: BBD ucu yayında DEĞİLKEN geçidin döndüğü hata kodu. Uçlar bugün ayakta
#: (bkz. modül açıklaması) ama bu dal SİLİNMEZ: ekranın uçsuz da ayakta kalması
#: K7'nin kendisidir ve kod yolu ancak sınandığı sürece güvenilir.
MISSING_CODE = "bbd_endpoint_missing"

MISSING_TEXT = ("Bu bölüm mağaza tarafındaki BBD ucu yayına girince açılacak. "
                "Ekranın geri kalanı çalışmaya devam ediyor.")

#: Talep listesi sayfa sayfa taranırken kabul edilen üst sınır. Bozuk `meta`
#: yüzünden sonsuz döngüye girmemek için var.
REQUEST_PAGE_LIMIT = 60

#: İlk boyamada her kaynaktan istenen satır sayısı. BBD uçları `per_page`
#: değerini sunucuda 50'ye kırpıyor; daha büyüğünü istemek bir şey kazandırmaz.
FIRST_PAGE_SIZE = 50

#: Ödeme ağ geçidi kodu → ekranda görünen ad. Bilinmeyen kod OLDUĞU GİBİ yazılır.
GATEWAY_LABELS = {"kuveytturk": "Kuveyt Türk"}


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def _flag(value: Any) -> bool | None:
    """Mağazanın bayrağı: `true`/`1`/`"1"` → True. Alan hiç yoksa `None`."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "evet")
    return bool(value)


class PreviewError(RuntimeError):
    """Önizleme görüntüsü üretilemedi. Rapor yine de kaydedilmiştir."""


class _ListCache:
    """Liste yanıtının kısa ömürlü önbelleği — TEK UÇUŞ (single-flight).

    NEDEN. Mağaza yönetici API'si jeton başına dakikada 60 isteğe izin veriyor,
    geçit bunu bütün ekranlar için 55'e bölüştürüyor. Ekranı açıp kapatmak,
    sekme değiştirip dönmek ya da iki kişinin aynı anda açması her seferinde
    aynı üç-beş isteği yeniden atıyordu.

    TEK UÇUŞ. Aynı anahtar için bir tarama sürerken gelen ikinci istek yeni bir
    tarama BAŞLATMAZ; ilkinin sonucunu bekler. `shield` yüzünden bekleyenlerden
    birinin bağlantısı koparsa tarama yarıda kalmaz.

    TAZELİK. `clear()` başarılı her yazmadan sonra çağrılır ve NESİL sayacını
    artırır: yazmadan ÖNCE başlamış bir tarama bitince önbelleğe yazılmaz —
    aksi hâlde yeni oluşan iade, süre dolana kadar listede görünmezdi.
    Eksik ya da hatalı tarama hiç saklanmaz; bir sonraki açılış yeniden sorar.
    """

    def __init__(self, ttl: Callable[[], float],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl
        self._clock = clock
        self._hits: dict[Any, tuple[float, Any]] = {}
        self._flights: dict[Any, asyncio.Future[Any]] = {}
        self._generation = 0

    def peek(self, key: Any) -> Any:
        hit = self._hits.get(key)
        if hit is not None and hit[0] > self._clock():
            return hit[1]
        return None

    async def get(self, key: Any, produce: Callable[[], Awaitable[Any]], *,
                  keep: Callable[[Any], bool]) -> Any:
        found = self.peek(key)
        if found is not None:
            return found
        flight = self._flights.get(key)
        if flight is None:
            generation = self._generation
            flight = asyncio.ensure_future(produce())
            self._flights[key] = flight
            # Geri çağrı, bekleyenler uyanmadan ÖNCE koşar (kayıt sırası):
            # ilk yanıt döndüğünde önbellek çoktan dolmuş olur.
            flight.add_done_callback(
                lambda done: self._land(key, done, generation, keep))
        return await asyncio.shield(flight)

    def _land(self, key: Any, done: asyncio.Future[Any], generation: int,
              keep: Callable[[Any], bool]) -> None:
        if self._flights.get(key) is done:
            self._flights.pop(key, None)
        if done.cancelled() or done.exception() is not None:
            return
        ttl = self._ttl()
        value = done.result()
        if generation == self._generation and ttl > 0 and keep(value):
            self._hits[key] = (self._clock() + ttl, value)

    def clear(self) -> None:
        self._generation += 1
        self._hits.clear()
        self._flights.clear()


class RefundsService:
    """İadeler ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ."""

    def __init__(self, *, api: Any, store: Any, log: Any, config: dict[str, Any],
                 printer: Any = None, category: str = "Mağaza", subcategory: str = "Finans",
                 fallback_dir: Path | None = None,
                 clock: Callable[[], float] | None = None) -> None:
        self._api = api
        self._store = store
        self._log = log
        self._config = config or {}
        self._printer = printer
        self._category = category
        self._subcategory = subcategory
        self._fallback = fallback_dir or Path.home() / "km-raporlar"

        self._audit = store.table("audit")
        self._calc = store.table("calc")
        self._list_cache = _ListCache(lambda: self._list_ttl, clock or time.monotonic)

    # ------------------------------------------------------------- ayarlar

    @property
    def _channel(self) -> str:
        return str(self._config.get("channel") or "default")

    @property
    def _locale(self) -> str:
        return str(self._config.get("locale") or "tr")

    @property
    def _scan_cap(self) -> int:
        return max(100, min(5_000, calc.as_int(self._config.get("scan_cap"), 1_500)))

    @property
    def _range_days(self) -> int:
        return max(1, min(730, calc.as_int(self._config.get("default_range_days"), 90)))

    @property
    def _free_return_days(self) -> int:
        return max(0, min(90, calc.as_int(self._config.get("free_return_days"), 14)))

    @property
    def _return_wait_days(self) -> int:
        return max(1, min(60, calc.as_int(self._config.get("return_wait_days"), 7)))

    @property
    def _basis(self) -> str:
        return "ordered" if str(self._config.get("refundable_basis")) == "ordered" else "invoiced"

    @property
    def _shipping_default(self) -> bool:
        return bool(self._config.get("refund_shipping_default"))

    @property
    def _list_ttl(self) -> int:
        """Liste önbelleğinin ömrü (sn). 0 önbelleği kapatır."""
        return max(0, min(300, calc.as_int(self._config.get("list_cache_seconds"), 90)))

    @property
    def _export_dir(self) -> Path:
        # HER ÇAĞRIDA yeniden çözülür: ay değişince klasör kendiliğinden değişir.
        return report_dir(self._category, subcategory=self._subcategory,
                          fallback=self._fallback,
                          configured=str(self._config.get("export_path") or ""))

    # ------------------------------------------------------------ yardımcı

    @staticmethod
    def _fail(failure: Exception) -> str:
        if getattr(failure, "code", "") == MISSING_CODE:
            return MISSING_TEXT
        message = str(failure).strip()
        return message or "Mağazaya ulaşılamadı."

    @staticmethod
    def _missing(failure: Exception) -> bool:
        return getattr(failure, "code", "") == MISSING_CODE

    def _guard(self, reason: str) -> str:
        """Gerekçe backend'de DE doğrulanır (K9): arayüzde gizlemek yetmez."""
        return calc.reason_error(reason)

    @staticmethod
    def _order_of(item: Any) -> int | None:
        """Satırın sipariş kimliği; alan yoksa ya da çözülemezse `None`."""
        value = calc.pick(item, "order_id", "orderId")
        if value in (None, ""):
            return None
        found = calc.as_int(value, -1)
        return found if found > 0 else None

    @classmethod
    def _belongs(cls, item: Any, order_id: int) -> bool:
        """Satır KESİNLİKLE bu siparişin mi — kimliği yoksa HAYIR.

        Kart ve kargo satırları için: başka müşterinin maskeli kartını ya da
        takip numarasını göstermektense satırı göstermemek doğrudur. Geçit de
        aynı süzmeyi yapıyor (`store_api`); bu ikinci kapı, geçit ya da mağaza
        süzgeci bir gün yine sessizce yok sayarsa çekmeceyi korur.
        """
        return isinstance(item, dict) and cls._order_of(item) == int(order_id)

    @classmethod
    def _foreign(cls, item: Any, order_id: int) -> bool:
        """Satır KESİNLİKLE başka siparişin mi — kimliği yoksa HAYIR.

        Para kapısındaki RMA ve kredi notu satırları için: kimliği okunamayan
        satır atılmaz, hesaba katılır. Atmak, var olan bir RMA'yı ya da daha
        önce iade edilmiş kargoyu yok saymak olurdu.
        """
        found = cls._order_of(item) if isinstance(item, dict) else None
        return found is not None and found != int(order_id)

    @staticmethod
    def _order_ref(raw: Any, order_id: int) -> str:
        """Kredi notu süzgecine gidecek sipariş değeri.

        TUZAK — MAĞAZADA DOĞRULANDI (`AdminRefundCollectionProvider::applyFilters`):
        `GET /refunds?order_id=X` sipariş KİMLİĞİNE değil, sipariş NUMARASINA
        bakar ve `increment_id LIKE '%X%'` ile süzer. `order_id=12` 112, 120,
        312 numaralı siparişlerin kredi notlarını da getirir; numara kimlikten
        farklıysa bu siparişinkileri HİÇ getirmeyebilir. Geçmiş iadelerden
        "daha önce iade edilen kargo" toplandığı için ikisi de para hatasıdır:
        biri kargoyu eksik, diğeri İKİNCİ KEZ iade ettirir. Bu yüzden sipariş
        numarası gönderilir ve dönen satırlar kimlikle yeniden süzülür.
        """
        return calc.text(calc.pick(raw, "increment_id", "incrementId")) or str(int(order_id))

    @classmethod
    def _history_of(cls, payload: Any, order_id: int) -> tuple[list[dict[str, Any]], bool]:
        """Kredi notu yanıtından YALNIZ bu siparişin satırları + liste tam mı.

        Süzgeç `LIKE` olduğu için tek sayfa başka siparişlerle dolabilir:
        `lastPage > 1` ise bu siparişin bir kredi notu ikinci sayfada kalmış
        olabilir ve liste "tam" sayılmaz.
        """
        source = payload if isinstance(payload, dict) else {}
        rows = [item for item in (source.get("items") or [])
                if isinstance(item, dict) and not cls._foreign(item, order_id)]
        meta = source.get("meta") if isinstance(source.get("meta"), dict) else {}
        return rows, calc.as_int((meta or {}).get("lastPage"), 1) <= 1

    def _window(self, date_from: str, date_to: str) -> tuple[str, str, str]:
        """Tarih penceresi + çözülemeyen tarih için uyarı metni.

        Uçtaki şema yalnız uzunluğa bakıyor; `dateTo=abc` gelebilir. Ham metni
        `fromisoformat`'a vermek ekranı 500 ile düşürüyordu (K7): çözülemeyen
        tarih yok sayılır ve kullanıcıya SÖYLENİR — sessizce başka bir pencere
        göstermek, personelin baktığı aralığı yanlış bilmesi demektir.
        """
        given_from, given_to = calc.text(date_from), calc.text(date_to)
        clean_from, clean_to = calc.iso_day(given_from), calc.iso_day(given_to)
        bad = [raw for raw, clean in ((given_from, clean_from), (given_to, clean_to))
               if raw and not clean]
        note = (f"Anlaşılmayan tarih yok sayıldı: {', '.join(bad)}. Varsayılan pencere "
                "kullanıldı.") if bad else ""

        end = clean_to or calc.today_iso()
        if clean_from:
            return clean_from, end, note
        start = datetime.fromisoformat(end) - timedelta(days=self._range_days)
        return start.date().isoformat(), end, note

    async def _record(self, *, order_id: int, action: str, reason: str, actor: str,
                      result: str, detail: Any = None) -> None:
        """Yerel denetim izi. Bagisto denetim tutuyor ama "neden iade edildi"
        alanı yok; ayrıca ağ koparsa "ne yapmaya çalıştık" kaydı burada kalır."""
        try:
            await self._store.execute(
                f"INSERT INTO {self._audit} "
                "(order_id, action, reason, actor, result, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (int(order_id or 0), action, reason, actor, result,
                 json.dumps(detail or {}, ensure_ascii=False), _now()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın
            self._log.warning("denetim izi yazılamadı", action=action, error=str(failure))

    # ================================================================ liste

    async def refunds(self, *, date_from: str = "", date_to: str = "", full: bool = False,
                      fresh: bool = False) -> dict[str, Any]:
        """Kredi notları + RMA talepleri, tek liste + KPI.

        Süzme İSTEMCİDEDİR (ölçek "yüzler"); sunucu pencereyi tarar, satırları
        normalleştirir ve özeti hesaplar. İki kaynaktan biri düşerse diğeri
        yine gösterilir — ekran ayakta kalır (K7).

        `full=False` (ekranın ilk açılışı): her kaynağın yalnız İLK SAYFASI
        (en yeni kayıtlar) gelir ve yanıt `partial` der. Ekran ilk sayfayı
        gösterir; süzgeç kullanılınca ya da "Tümünü yükle" denince `full=True`
        ile pencerenin tamamı istenir. Tam tarama önbellekteyse ilk sayfa
        isteğine de o verilir — elde varken eksiğini göstermenin anlamı yok.

        `fresh=True` ("Yenile") önbelleği boşaltır.
        """
        start, end, window_note = self._window(date_from, date_to)
        if fresh:
            self._list_cache.clear()
        bundle = None if full else self._list_cache.peek((start, end, True))
        if bundle is None:
            bundle = await self._list_cache.get(
                (start, end, bool(full)),
                lambda: self._load_list(start, end, full=bool(full)),
                keep=lambda value: bool(value["complete"]))
        warnings = ([window_note] if window_note else []) + list(bundle["warnings"])
        return {**bundle["payload"], "warnings": warnings}

    async def _load_list(self, start: str, end: str, *, full: bool) -> dict[str, Any]:
        """Üç bağımsız okuma AYNI ANDA yapılır (kredi notu · RMA · dönem satışı).

        Sıralı yapıldığında bekleme süresi üçünün toplamıydı. İstek SAYISI
        değişmez; geçidin hız kovası (dakikada 55) istekleri yine kendisi sıraya
        koyar, eşzamanlılık yalnız boşta bekleme süresini kısaltır.
        """
        today = calc.today_iso()
        credit, requests, sales_part = await asyncio.gather(
            self._scan_refunds(start, end, full=full),
            self._scan_requests(start, end, full=full),
            self._period_sales())
        sales, sales_error = sales_part
        credit_ok, credit_error = credit["ok"], credit["error"]
        requests_ok, request_error = requests["ok"], requests["error"]

        warnings: list[str] = []
        if not credit_ok:
            warnings.append(f"Kredi notları okunamadı: {credit_error}")
        if not requests_ok:
            warnings.append(f"İade talepleri okunamadı: {request_error}")
        elif request_error:
            # Tarama ortada koptu: elde ne varsa gösteriliyor ama liste EKSİK.
            # Sessiz kalmak, açık talebi olmayan bir gün gibi görünmesine yol açar.
            warnings.append(f"İade talepleri eksik alındı: {request_error}")

        rows = calc.merge_rows(credit["rows"], requests["rows"])
        calc.decorate(rows, today=today, free_return_days=self._free_return_days,
                      return_wait_days=self._return_wait_days)

        summary = calc.summarize(rows, today=today, sales_total=sales)
        if sales is None:
            summary["rateNote"] = (
                "İade oranı hesaplanamadı: dönem satış tutarı okunamadı"
                + (f" ({sales_error})" if sales_error else "")
                + ". Oran uydurulmaz, boş bırakılır.")
        else:
            summary["rateNote"] = ""

        more = {"refunds": bool(credit["more"]), "requests": bool(requests["more"])}
        partial = more["refunds"] or more["requests"]
        payload = {
            "ok": True,
            "connected": credit_ok or requests_ok,
            "error": "" if (credit_ok or requests_ok) else (credit_error or request_error),
            "items": rows,
            "summary": summary,
            "truncated": bool(credit["truncated"]),
            "full": full,
            "partial": partial,
            "more": more,
            "partialNote": (
                f"Her kaynağın en yeni {FIRST_PAGE_SIZE} kaydı gösteriliyor; aralığın "
                "tamamı yüklenmedi. Özet rakamlar da bu kayıtlardan hesaplandı."
            ) if partial else "",
            "range": {"from": start, "to": end},
            "loadedAt": _now(),
            "sources": {
                "refunds": {"available": credit_ok, "error": credit_error},
                "requests": {"available": requests_ok, "error": request_error,
                             "missing": request_error == MISSING_TEXT},
            },
            "settings": {
                "freeReturnDays": self._free_return_days,
                "returnWaitDays": self._return_wait_days,
                "shippingDefault": self._shipping_default,
                "pageSize": max(10, min(200, calc.as_int(self._config.get("page_size"), 50))),
            },
        }
        # Yalnız EKSİKSİZ okuma saklanır: kaynaklardan biri düştüyse ya da
        # tarama ortada koptuysa bir sonraki açılış yeniden sormalı.
        complete = credit_ok and requests_ok and not request_error
        return {"payload": payload, "warnings": warnings, "complete": complete}

    async def _scan_refunds(self, start: str, end: str, *, full: bool = True) -> dict[str, Any]:
        # `date_from`/`date_to` CANLIDA UYGULANIYOR (doğrulandı: 2026-07-26'dan
        # sonrası 0 kayıt döndü). `channel`/`locale` ise SESSİZCE YOK SAYILIYOR
        # — Laravel tanımadığı sorgu parametresini atar. İkisi de gönderiliyor
        # çünkü zararsız ve uç desteklerse çalışır; ama "kanala göre süzüyoruz"
        # SANILMASIN: mağazada tek kanal var ve süzme olmadan da doğru sonuç
        # çıkıyor. Kanal ayrımı gerekirse süzme burada, satır bazında yapılmalı.
        #
        # İLK SAYFA EN YENİLERDİR: uç varsayılan olarak `id` azalan sıralar
        # (`AbstractAdminCollectionProvider::resolveSort`).
        filters = {"channel": self._channel, "locale": self._locale,
                   "date_from": start, "date_to": end}
        try:
            if full:
                payload = await self._api.refunds(filters, all_pages=True)
            else:
                payload = await self._api.refunds(filters, page=1, per_page=FIRST_PAGE_SIZE)
        except Exception as failure:  # noqa: BLE001 — ekran ayakta kalmalı (K7)
            self._log.warning("kredi notları okunamadı", error=str(failure))
            return {"rows": [], "ok": False, "error": self._fail(failure),
                    "truncated": False, "more": False}
        raw = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
        cap = self._scan_cap
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        more = not full and self._has_more(meta or {}, len(raw))
        truncated = full and (bool(payload.get("truncated")) or len(raw) > cap)
        return {"rows": [calc.refund_row(item) for item in raw[:cap]], "ok": True,
                "error": "", "truncated": truncated, "more": more}

    async def _scan_requests(self, start: str, end: str, *, full: bool = True) -> dict[str, Any]:
        """RMA taleplerini sayfa sayfa toplar (`full=False` ise yalnız ilk sayfa).

        `bbd_return_requests` toplu tarama parametresi almıyor; sayfalar SIRAYLA
        çekilir. Uç yayında değilse boş liste + açıklama döner, ekran kredi
        notlarıyla çalışmaya devam eder (K7 — uç bugün ayakta, dal yine durur).
        İlk sayfa en yeni taleplerdir (mağaza `orderByDesc('id')`).
        """
        rows: list[dict[str, Any]] = []
        more = False
        # TARİH SÜZGECİNİN ADI `from`/`to`. CANLIDA DOĞRULANDI (2026-08-16):
        # `from=2026-08-01&to=2026-08-16` → 0 kayıt, `from=2026-07-01` → 2 kayıt;
        # `date_from`/`date_to` ise HİÇ UYGULANMIYOR (aynı pencerede 2 kayıt
        # döndü). Bir dönem `date_from`/`date_to` gönderiliyordu: Laravel
        # tanımadığı parametreyi atar, hata çıkmaz — ekran seçilen tarih
        # aralığının DIŞINDAKİ talepleri de listeliyordu. Yanlış veri, veri
        # yokluğundan tehlikelidir çünkü kendini belli etmez.
        #
        # `type` de tanınmıyor (uç yalnız `status`/`from`/`to` süzer); zararsız
        # olduğu için bırakıldı ama "türe göre süzüyoruz" SANILMASIN.
        filters = {"from": start, "to": end, "type": "refund"}
        page = 1
        while page <= REQUEST_PAGE_LIMIT and len(rows) < self._scan_cap:
            try:
                payload = await self._api.bbd_return_requests(filters, page=page,
                                                              per_page=FIRST_PAGE_SIZE)
            except Exception as failure:  # noqa: BLE001 — K7
                if page > 1:
                    # Kısmî liste yalanla dolmaz: elde ne varsa o gösterilir
                    # ve neden eksik olduğu söylenir.
                    return {"rows": rows, "ok": True, "error": self._fail(failure),
                            "more": False}
                self._log.info("iade talepleri okunamadı", error=str(failure))
                return {"rows": [], "ok": False, "error": self._fail(failure), "more": False}
            items = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
            rows.extend(calc.request_row(item) for item in items)
            meta = payload.get("meta") or {}
            if not full:
                more = bool(items) and self._has_more(meta, len(items))
                break
            last = calc.as_int(meta.get("lastPage"), page)
            if not items or page >= last:
                break
            page += 1
        return {"rows": rows[:self._scan_cap], "ok": True, "error": "", "more": more}

    @staticmethod
    def _has_more(meta: dict[str, Any], count: int) -> bool:
        """İlk sayfadan sonra kayıt var mı. `meta.lastPage` yoksa DOLU sayfa
        "devamı olabilir" sayılır — eksik listeyi tam sanmaktansa fazladan bir
        "Tümünü yükle" göstermek doğrudur."""
        last = meta.get("lastPage")
        if last in (None, ""):
            return count >= FIRST_PAGE_SIZE
        return calc.as_int(last, 1) > 1

    async def _period_sales(self) -> tuple[int | None, str]:
        """Bu ayın satış tutarı — iade oranının paydası."""
        start, end = calc.month_bounds()
        try:
            payload = await self._api.dashboard_stats(kind="total-sales", start=start, end=end,
                                                      channel=self._channel)
        except Exception as failure:  # noqa: BLE001 — K7
            return None, self._fail(failure)
        return calc.sales_of(payload), ""

    async def audit(self, *, order_id: int = 0, limit: int = 50) -> dict[str, Any]:
        """Bu ekrandan yapılan yazmaların YEREL izi (gerekçeleriyle)."""
        sql = (f"SELECT order_id, action, reason, actor, result, created_at "
               f"FROM {self._audit} ")
        params: tuple[Any, ...] = ()
        if order_id:
            sql += "WHERE order_id = ? "
            params = (int(order_id),)
        sql += "ORDER BY id DESC LIMIT ?"
        params = (*params, max(1, min(500, int(limit))))
        try:
            rows = await self._store.fetch_all(sql, params)
        except Exception as failure:  # noqa: BLE001 — iz okunamadı, ekran dursun
            return {"ok": True, "items": [], "error": self._fail(failure)}
        return {"ok": True, "error": "", "items": [
            {"orderId": row["order_id"], "action": row["action"], "reason": row["reason"],
             "actor": row["actor"], "result": row["result"], "createdAt": row["created_at"]}
            for row in rows
        ]}

    # ============================================================== çekmece

    async def order_card(self, order_id: int, *, request_id: int = 0) -> dict[str, Any]:
        """Çekmecenin tek isteği: sipariş + kalemler + kargo + POS + geçmiş iadeler.

        Parçalar AYNI ANDA ve TEK TEK korunarak çekilir: biri patlarsa
        çekmecenin gerisi yine dolar (K7). Eskiden sıralıydı ve bekleme beş
        isteğin toplamıydı; istek sayısı değişmedi, kovayı geçit yönetiyor.

        `request_id` verilirse (açık bir RMA satırından gelindiyse) o talebin
        kalemleri de okunur ve iade adetleri talepteki adetlerle ÖNERİLİR.
        Öneri yalnız kutuları doldurur; hesap ve onay yine kullanıcınındır.
        """
        order_id = int(order_id)
        request_id = calc.as_int(request_id)
        warnings: list[str] = []

        async def part(label: str, call: Callable[[], Awaitable[Any]]) -> tuple[Any, str]:
            try:
                return await call(), ""
            except Exception as failure:  # noqa: BLE001 — uzak sistem
                message = self._fail(failure)
                warnings.append(f"{label}: {message}")
                return None, message

        async def fetch_order() -> tuple[Any, Exception | None]:
            try:
                return await self._api.order(order_id), None
            except Exception as failure:  # noqa: BLE001 — K7
                return None, failure

        async def no_detail() -> tuple[Any, str]:
            return None, ""

        (raw, order_failure), (history, _), (payments, payments_error), \
            (shipments, shipments_error), (requests, requests_error), (detail, _) = \
            await asyncio.gather(
                fetch_order(),
                # Kimlikle önden sorulur: bu mağazada sipariş numarası kimlikle
                # aynıdır ve ikinci tur gerekmez. Farklıysa aşağıda numarayla
                # yeniden sorulur (bkz. `_order_ref`).
                part("geçmiş iadeler", lambda: self._api.refunds(
                    {"order_id": order_id}, per_page=50)),
                part("POS denemeleri", lambda: self._api.bbd_payment_attempts(
                    {"order_id": order_id}, per_page=50)),
                part("gönderiler", lambda: self._api.bbd_shipments(
                    {"order_id": order_id}, per_page=50)),
                part("RMA talepleri", lambda: self._api.bbd_return_requests(
                    {"order_id": order_id}, page=1, per_page=50)),
                part("RMA kalemleri", lambda: self._api.bbd_return_request(request_id))
                if request_id > 0 else no_detail(),
            )
        if order_failure is not None:
            self._log.warning("sipariş okunamadı", orderId=order_id, error=str(order_failure))
            return {"ok": False, "connected": False, "error": self._fail(order_failure)}

        reference = self._order_ref(raw, order_id)
        if history is not None and reference != str(order_id):
            history, _ = await part("geçmiş iadeler", lambda: self._api.refunds(
                {"order_id": reference}, per_page=50))
        history_rows, history_complete = self._history_of(history, order_id)
        history_known = history is not None and history_complete
        if history is not None and not history_complete:
            warnings.append("geçmiş iadeler: liste tek sayfaya sığmadı; bu siparişin bütün "
                            "kredi notları okunamamış olabilir.")

        request_source = requests if isinstance(requests, dict) else {}
        request_items = request_source.get("items")
        request_meta = request_source.get("meta")
        if isinstance(request_items, list):
            request_items = [item for item in request_items if not self._foreign(item, order_id)]
        rma_valid = (isinstance(request_items, list)
                     and isinstance(request_meta, dict)
                     and calc.as_int(request_meta.get("lastPage")) <= 1
                     and all(isinstance(item, dict)
                             and calc.as_int(calc.pick(item, "id", "request_id")) > 0
                             for item in request_items))
        if requests is not None and not rma_valid:
            requests_error = "RMA API yanıtı beklenen biçimde değil; güvenli onay kapalı."
            warnings.append(f"RMA talepleri: {requests_error}")
        view = calc.order_view(raw, basis=self._basis,
                               shipping_refunded=calc.shipping_refunded_of(history_rows)
                               if history_known else None)
        if not history_known and view["shippingAvailable"] > 0:
            warnings.append(
                "Geçmiş iadeler okunamadığı için kargo bedelinin daha önce geri verilip "
                "verilmediği DOĞRULANAMADI; kargo kutusunu işaretlemeden önce kontrol edin.")

        suggested = self._suggested(detail, order_id, view["lines"])

        return {
            "ok": True, "connected": True, "error": "", "warnings": warnings,
            "order": view,
            "shippingVerified": history_known,
            "history": [calc.refund_row(item) for item in history_rows],
            "payments": {
                "available": payments is not None,
                "error": payments_error,
                "items": [self._payment_row(item)
                          for item in ((payments or {}).get("items") or [])
                          if self._belongs(item, order_id)],
                # Ayrı POS iadesi YOKTUR: kredi notu bankaya iadeyi kendisi
                # başlatır. Liste okunur (hangi karta ne çekildi), düğme çizilmez.
                "refundBlocked": True,
                "refundReason": self.POS_REFUND_REASON,
                "note": self.POS_NOTE,
            },
            "shipments": {
                "available": shipments is not None,
                "error": shipments_error,
                "items": [self._shipment_row(item)
                          for item in ((shipments or {}).get("items") or [])
                          if self._belongs(item, order_id)],
            },
            "rma": {
                "available": requests is not None and rma_valid,
                "error": requests_error,
                "items": [calc.request_row(item) for item in request_items or []]
                         if rma_valid else [],
                "suggested": suggested,
                "suggestedFrom": request_id if suggested else 0,
            },
            "shippingDefault": self._shipping_default,
            "basisNotice": "" if view["invoicedAny"] else (
                "Bu siparişte faturalanmış adet yok. İade edilebilir adet sipariş adedinden "
                "alındı; parası tahsil edilmemiş bir kalemi iade etmediğinizden emin olun."),
        }

    @classmethod
    def _suggested(cls, detail: Any, order_id: int,
                   lines: list[dict[str, Any]]) -> dict[str, int]:
        """RMA'daki kalem adetleri → `{siparişKalemId: adet}` önerisi.

        Talep BAŞKA siparişinse ya da kapanmışsa (iade edildi / reddedildi)
        öneri yapılmaz. Adet, kalemin iade edilebilir adedini AŞMAZ: talep
        3 diyorsa ama 1 adet faturalanmışsa kutuya 1 yazılır.
        """
        if not isinstance(detail, dict):
            return {}
        source = detail.get("data") if isinstance(detail.get("data"), dict) else detail
        if cls._order_of(source) != int(order_id):
            return {}
        status = calc.status_of(calc.pick(source, "status", "state"))
        if status in (calc.ST_REFUNDED, calc.ST_REJECTED):
            return {}
        limits = {int(line["id"]): int(line["refundable"]) for line in lines}
        wanted: dict[int, int] = {}
        for item in source.get("items") or []:
            if not isinstance(item, dict):
                continue
            nested = item.get("orderItem") if isinstance(item.get("orderItem"), dict) else {}
            line_id = calc.as_int(calc.pick(item, "order_item_id", "orderItemId")
                                  or calc.pick(nested, "id"))
            qty = calc.as_int(calc.pick(item, "quantity", "qty"))
            if line_id in limits and qty > 0:
                wanted[line_id] = wanted.get(line_id, 0) + qty
        return {str(line_id): min(qty, limits[line_id])
                for line_id, qty in wanted.items() if limits[line_id] > 0}

    @staticmethod
    def _minor(raw: Any, *names: str) -> int | None:
        """KURUŞ TAM SAYISI olarak gelen alan (`amount_minor`). Yoksa `None`.

        `to_kurus` BURADA KULLANILMAZ: o ondalık TL bekler ve 12345 kuruşu
        1.234.500 kuruşa çevirirdi.
        """
        value = calc.pick(raw, *names)
        if value in (None, ""):
            return None
        found = calc.as_int(value, -1)
        return found if found >= 0 else None

    @classmethod
    def _payment_row(cls, raw: dict[str, Any]) -> dict[str, Any]:
        """POS denemesi → çekmece satırı.

        ALAN ADLARI MAĞAZANIN BEYAZ LİSTESİNDEN (`OutputMask::PAYMENT_ATTEMPT`):
        `amount_minor` · `refunded_minor` (KURUŞ tam sayı) · `masked_number` ·
        `gateway` · `state` (+ Türkçe `stateLabel`). Eskiden `amount`/
        `masked_card` aranıyordu; hiçbiri gelmediği için satırlar "0,00 ₺" ve
        boş kartla çiziliyordu. Eski adlar yine okunur (ondalık TL olarak).
        """
        amount = cls._minor(raw, "amount_minor", "amountMinor")
        if amount is None:
            amount = calc.to_kurus(calc.pick(raw, "amount", "total")) or 0
        refunded = cls._minor(raw, "refunded_minor", "refundedMinor")
        if refunded is None:
            refunded = calc.to_kurus(calc.pick(raw, "refunded_amount", "refundedAmount")) or 0
        gateway = calc.text(calc.pick(raw, "gateway", "bank", "provider", "pos"))
        return {
            "id": calc.as_int(calc.pick(raw, "id", "attempt_id")),
            "orderId": calc.as_int(calc.pick(raw, "order_id", "orderId")),
            "createdAt": calc.text(calc.pick(raw, "created_at", "createdAt"))[:19],
            "bank": GATEWAY_LABELS.get(gateway.lower(), gateway),
            "card": calc.text(calc.pick(raw, "masked_number", "maskedNumber", "masked_card",
                                        "card", "card_last4")),
            "installment": calc.as_int(calc.pick(raw, "installment", "installments")),
            "status": calc.text(calc.pick(raw, "stateLabel", "state_label", "status", "state")),
            "state": calc.text(calc.pick(raw, "state", "status")),
            "amount": amount,
            "refunded": refunded,
            "refundable": max(0, amount - refunded),
        }

    @staticmethod
    def _shipment_row(raw: dict[str, Any]) -> dict[str, Any]:
        """Geliver gönderisi → çekmece satırı.

        ALAN ADLARI MAĞAZANIN BEYAZ LİSTESİNDEN (`OutputMask::SHIPMENT` +
        `ShipmentController::present`): taşıyıcı `providerName` (görünen ad) ya
        da `provider_code`; iade gönderisi `is_return`, etiket `isPurchased`.
        Eskiden `carrier` aranıyordu; gelmediği için her satır "Taşıyıcı" diye
        çiziliyordu.
        """
        is_return = _flag(calc.pick(raw, "is_return", "isReturn"))
        purchased = _flag(calc.pick(raw, "isPurchased", "is_purchased", "labelPurchased"))
        if purchased is None:
            purchased = bool(calc.text(calc.pick(raw, "geliver_transaction_id",
                                                 "geliverTransactionId")))
        if is_return is None:
            direction = calc.text(calc.pick(raw, "direction", "kind"))
            is_return = direction.lower() in ("return", "iade")
        return {
            "id": calc.as_int(calc.pick(raw, "id", "shipment_id")),
            "orderId": calc.as_int(calc.pick(raw, "order_id", "orderId")),
            "carrier": calc.text(calc.pick(raw, "providerName", "provider_name", "carrier_name",
                                           "carrierName", "carrier", "provider_code",
                                           "providerCode")),
            "providerCode": calc.text(calc.pick(raw, "provider_code", "providerCode")),
            "tracking": calc.text(calc.pick(raw, "tracking_number", "trackingNumber",
                                            "tracking_no", "barcode")),
            "trackingUrl": calc.text(calc.pick(raw, "tracking_url", "trackingUrl")),
            "status": calc.text(calc.pick(raw, "status", "state")),
            "direction": "return" if is_return else "outbound",
            "isReturn": bool(is_return),
            "purchased": bool(purchased),
            "parentId": calc.as_int(calc.pick(raw, "parent_id", "parentId")),
            "rmaId": calc.as_int(calc.pick(raw, "rma_id", "rmaId")),
            "receivedAt": calc.text(calc.pick(raw, "return_received_at",
                                              "returnReceivedAt"))[:19],
            "error": calc.text(calc.pick(raw, "last_error", "lastError")),
            "createdAt": calc.text(calc.pick(raw, "created_at", "createdAt"))[:19],
        }

    # ================================================================ hesap

    async def calculate(self, order_id: int, *, quantities: dict[str, Any],
                        refund_shipping: bool = False, adjustment_refund: int = 0,
                        adjustment_fee: int = 0) -> dict[str, Any]:
        """İade tutarını satır satır hesaplar ve ONAY JETONU üretir.

        Yazmaz, gerekçe istemez. İki hesap birden döner: bizimki (ekranda satır
        satır görünür) ve mağazanınki (uygulanacak olan). Jeton olmadan
        `approve` reddedilir — böylece uygulanan şeyin ekranda GÖRÜLEN şey
        olduğu kanıtlanır.
        """
        try:
            raw = await self._api.order(int(order_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}

        warnings: list[str] = []
        # Kargo bedeli iade edilecekse "daha önce verildi mi" sorusunun cevabı
        # siparişte YOK (canlıda `shipping_amount_refunded` yayınlanmıyor);
        # tek kaynak siparişin kredi notlarıdır. Yalnız kutu işaretliyken
        # sorulur: gereksiz istek dakikada 55'lik kovayı tüketir.
        already: int | None = 0
        if refund_shipping:
            # Süzgeç sipariş NUMARASIYLA ve `LIKE` ile çalışır; dönen satırlar
            # kimlikle yeniden süzülür (bkz. `_order_ref`).
            try:
                history = await self._api.refunds(
                    {"order_id": self._order_ref(raw, int(order_id))}, per_page=50)
                rows, complete = self._history_of(history, int(order_id))
                if not complete:
                    raise RuntimeError("liste tek sayfaya sığmadı")
                already = calc.shipping_refunded_of(rows)
            except Exception as failure:  # noqa: BLE001 — K7
                already = None
                warnings.append(
                    "Daha önce iade edilmiş kargo bedeli okunamadı "
                    f"({self._fail(failure)}); kargo bedelini ikinci kez iade etmediğinizden "
                    "emin olun.")

        view = calc.order_view(raw, basis=self._basis, shipping_refunded=already)
        result = calc.compute(
            view["lines"], quantities or {},
            refund_shipping=bool(refund_shipping),
            shipping_available=view["shippingAvailable"],
            adjustment_refund=calc.as_int(adjustment_refund),
            adjustment_fee=calc.as_int(adjustment_fee),
        )
        if result["empty"]:
            return {"ok": False, "error": "İade edilecek adet seçilmedi."}
        if result["negative"]:
            return {"ok": False,
                    "error": "Kesinti iade tutarını sıfırın altına indiriyor; iade negatif olamaz."}

        items, adjustments = calc.refund_body(result)
        store_value: int | None = None
        store_error = ""
        try:
            preview = await self._api.refund_preview(int(order_id), items=items)
            store_value = calc.store_total(preview)
        except Exception as failure:  # noqa: BLE001 — K7; önizleme yoksa da hesap gösterilir
            store_error = self._fail(failure)
            self._log.info("mağaza iade önizlemesi alınamadı", orderId=order_id,
                           error=store_error)

        verdict = calc.compare(result["total"], store_value)
        if store_value is None and store_error:
            verdict["message"] = f"{verdict['message']} ({store_error})"

        token = uuid.uuid4().hex
        body = {"items": items, "adjustments": adjustments,
                "shippingIncluded": result["shippingIncluded"]}
        try:
            await self._store.execute(
                f"INSERT INTO {self._calc} "
                "(token, order_id, total, lines, body, store_total, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'preview', ?)",
                (token, int(order_id), int(result["total"]),
                 json.dumps(result, ensure_ascii=False), json.dumps(body, ensure_ascii=False),
                 store_value, _now()),
            )
        except Exception as failure:  # noqa: BLE001 — jeton yazılamazsa onay açılmaz
            self._log.warning("iade hesabı kaydedilemedi", error=str(failure))
            return {"ok": False,
                    "error": "Hesap kaydedilemedi; onay açılmadı. Ekranda görülmeyen bir "
                             "tutarla para çıkarılmaz."}

        return {"ok": True, "error": "", "token": token, "orderId": int(order_id),
                "calc": result, "store": verdict, "warnings": warnings,
                "order": {"number": view["orderNumber"], "customer": view["customer"],
                          "grandTotal": view["grandTotal"],
                          "totalRefunded": view["totalRefunded"]}}

    # =============================================================== onay

    async def approve(self, *, token: str, reason: str, actor: str,
                      dry_run: bool = True,
                      rma_acknowledged_ids: list[int] | None = None) -> dict[str, Any]:
        """Onaylanan hesabı uygular — PARA HAREKETİDİR (ADR 0012).

        Hesap YENİDEN HESAPLANMAZ: jetonla saklanan gövde neyse o gider.
        Aradan geçen sürede sipariş değiştiyse mağaza kendi doğrulamasıyla
        reddeder; bizim sessizce yeni bir tutar üretmemiz, kullanıcının
        onayladığından başka bir parayı çıkarmak olurdu.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        try:
            row = await self._store.fetch_one(
                f"SELECT token, order_id, total, body, status FROM {self._calc} WHERE token = ?",
                (calc.text(token),))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        if not row:
            return {"ok": False,
                    "error": "Hesap bulunamadı. İade tutarı görülmeden onaylanmaz; "
                             "kalemleri yeniden seçip hesabı alın."}
        if row["status"] != "preview":
            return {"ok": False, "error": "Bu hesap zaten onaylandı."}

        order_id = int(row["order_id"])
        body = json.loads(row["body"])
        items = {str(key): int(value) for key, value in (body.get("items") or {}).items()}
        adjustments = body.get("adjustments") or {}

        # RMA onayından "Refunded" geçişi mağazada kendiliğinden kredi notu
        # + Kuveyt Türk iadesi başlatır. Manuel kredi notu yine mümkündür, ancak
        # talep ilişkisi canlıdan yeniden okunup yetkili açıkça uyarmış olmalıdır.
        try:
            rmas = await self._api.bbd_return_requests({"order_id": order_id}, page=1,
                                                       per_page=50)
        except Exception as failure:  # noqa: BLE001 — ilişki doğrulanamazsa para çıkmaz
            await self._record(order_id=order_id, action="create_refund", reason=reason,
                               actor=actor, result="rma_check_failed",
                               detail={"error": str(failure), "total": row["total"]})
            return {"ok": False, "error": "RMA kayıtları doğrulanamadı; iade oluşturulmadı. "
                    "Bağlantıyı kontrol edip yeniden deneyin."}
        if (not isinstance(rmas, dict) or not isinstance(rmas.get("items"), list)
                or not isinstance(rmas.get("meta"), dict)
                or calc.as_int(rmas["meta"].get("lastPage")) > 1):
            await self._record(order_id=order_id, action="create_refund", reason=reason,
                               actor=actor, result="rma_check_invalid",
                               detail={"total": row["total"]})
            return {"ok": False, "error": "RMA kontrolü geçersiz yanıt verdi; iade oluşturulmadı."}
        # Çekmeceyle AYNI süzme: kesinlikle başka siparişe ait talep atılır,
        # siparişi okunamayan talep hesaba katılır (bkz. `_foreign`).
        rma_rows = [item for item in rmas["items"] if not self._foreign(item, order_id)]
        if any(not isinstance(item, dict)
               or calc.as_int(calc.pick(item, "id", "request_id")) <= 0
               for item in rma_rows):
            await self._record(order_id=order_id, action="create_refund", reason=reason,
                               actor=actor, result="rma_check_invalid",
                               detail={"total": row["total"]})
            return {"ok": False, "error": "RMA yanıtında geçersiz talep bulundu; iade oluşturulmadı."}
        live_rma_ids = sorted(calc.as_int(calc.pick(item, "id", "request_id"))
                              for item in rma_rows)
        acknowledged_ids = sorted({calc.as_int(value) for value in (rma_acknowledged_ids or [])})
        rma_summary = [{"id": calc.as_int(calc.pick(item, "id", "request_id")),
                        "status": calc.status_text(calc.pick(item, "status", "statusLabel"))}
                       for item in rma_rows]
        if rma_rows and live_rma_ids != acknowledged_ids:
            await self._record(order_id=order_id, action="create_refund", reason=reason,
                               actor=actor, result="rma_confirmation_required",
                               detail={"total": row["total"], "rma": rma_summary,
                                       "acknowledgedRmaIds": acknowledged_ids})
            return {"ok": False, "confirmationRequired": True,
                    "rma": rma_summary,
                    "error": "Bu siparişte RMA talebi var. RMA ve manuel kredi notu birlikte "
                             "Kuveyt Türk'e iade gönderebilir. Sipariş çekmecesini yenileyip "
                             "RMA uyarısını okuyun ve yetkili onayıyla yeniden deneyin."}

        await self._record(order_id=order_id, action="create_refund", reason=reason, actor=actor,
                           result="denendi", detail={"total": row["total"], "items": items,
                                                     "rmaAcknowledged": bool(rma_rows),
                                                     "acknowledgedRmaIds": acknowledged_ids,
                                                     "rma": rma_summary})
        try:
            result = await self._api.create_refund(order_id, items=items,
                                                   adjustments=adjustments, reason=reason,
                                                   actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(order_id=order_id, action="create_refund", reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        # BURADAN SONRASI PARA ÇIKTIKTAN SONRASIDIR. Yerel yazma patlarsa istisna
        # yukarı gitmemeli: kullanıcı "hata" görüp iadeyi ikinci kez denerdi (K7).
        if not result.get("dryRun", dry_run):
            # Yeni kredi notu listede HEMEN görünmeli; önbellek süresini beklerse
            # personel iadenin oluşmadığını sanıp ikinci kez dener.
            self._list_cache.clear()
        warning = ""
        try:
            await self._store.execute(
                f"UPDATE {self._calc} SET status = ?, actor = ?, reason = ?, applied_at = ? "
                "WHERE token = ?",
                ("dry_run" if dry_run else "applied", actor, reason, _now(), row["token"]),
            )
        except Exception as failure:  # noqa: BLE001 — para çıktı, iş bitti
            warning = ("İade mağazada oluştu ama hesap kaydı 'uygulandı' olarak "
                       "işaretlenemedi. Aynı hesabı İKİNCİ KEZ onaylamayın; listeyi "
                       "yenileyip kredi notunu doğrulayın.")
            self._log.error("iade jetonu işaretlenemedi", orderId=order_id, error=str(failure))

        await self._record(order_id=order_id, action="create_refund", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"total": row["total"], "items": items,
                                   "rmaAcknowledged": bool(rma_rows),
                                   "acknowledgedRmaIds": acknowledged_ids, "rma": rma_summary})
        return {"ok": True, "error": "", "orderId": order_id, "total": int(row["total"]),
                "dryRun": bool(result.get("dryRun", dry_run)),
                "sent": bool(result.get("sent", not dry_run)),
                "warning": warning,
                "notice": ("Yetkili RMA onayı kaydedildi ("
                           + ", ".join(f"#{item['id']}" for item in rma_summary)
                           + "). RMA akışı da iade başlatmış olabilir; olası çifte iadeyi "
                           "banka kayıtlarından doğrulayın. " if rma_rows else "")
                + "Kredi notu oluştu. Bu mağazada Bagisto iade olayı Kuveyt Türk "
                  "POS iadesini otomatik başlatır; ayrıca POS iadesi göndermeyin. "
                  "Banka sonucunu ödeme/iade kayıtlarından doğrulayın."}

    #: AYRI POS İADESİ BU EKRANDAN YAPILMAZ — create_refund zaten mağazada
    #: RefundRepository → sales.refund.save.after → Kuveyt Türk listener yolunu çalıştırır.
    #:
    #: Ayrı bir POS iadesi ikinci kez para çıkarabilir; bu yüzden by-design kapalıdır.
    POS_REFUND_REASON = (
        "Ayrı POS iadesi yapılmaz: kredi notu oluşunca Kuveyt Türk iadesi otomatik "
        "başlar; ikinci bir banka işlemi çifte para iadesi olur."
    )

    #: Çekmecenin POS bölümündeki TEK satır. Uzun paragraf okunmuyordu.
    POS_NOTE = "Banka iadesi, iade (kredi notu) oluşturulunca Kuveyt Türk'e otomatik gider."

    @staticmethod
    def _by_design(failure: Exception) -> bool:
        """Geçit "bu uç bilerek yok" mu dedi — yoksa gerçek bir arıza mı?

        İkisi ayrı cevaplardır: birincisinde düğme kapanır, ikincisinde
        "tekrar dene" anlamlıdır.
        """
        return getattr(failure, "code", "") == "bbd_endpoint_by_design"

    def features(self) -> dict[str, Any]:
        """Hangi düğme açılabilir — kapalı olanın NEDENİ yazılıdır."""
        return {
            "posRefund": {"available": False, "reason": self.POS_REFUND_REASON},
            "printer": {"available": self._printer is not None,
                        "reason": "" if self._printer is not None else
                                  "Yazıcı yeteneği bu kurulumda yok; PDF üretilir, "
                                  "basılmaz."},
        }

    async def pos_refund(self, *, attempt_id: int, amount: int, order_id: int = 0,
                         reason: str, actor: str, dry_run: bool = True) -> dict[str, Any]:
        """Ayrı POS çağrısını engelle; mağaza kredi notu listener'ı zaten tetikler."""
        del attempt_id, amount, order_id, reason, actor, dry_run
        return {"ok": False, "blocked": True, "error": self.POS_REFUND_REASON}

    # ============================================================== süreç

    async def set_request_status(self, request_id: int, *, status: str, reason: str, actor: str,
                                 dry_run: bool = True) -> dict[str, Any]:
        """Talebin sürecini ilerletir (ürün bekleniyor → geldi → reddedildi).

        Para hareketi DEĞİLDİR; `manage` izniyle yapılır. İade parasını çıkaran
        tek yol `approve` ucudur.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        wanted = calc.text(status)
        if wanted not in calc.STATUS_LABELS or wanted in (calc.ST_REFUNDED, calc.ST_OTHER):
            return {"ok": False,
                    "error": "Bu durum buradan yazılmaz. 'İade edildi' ancak onay ucundan, "
                             "para gerçekten geri verilerek oluşur."}

        await self._record(order_id=0, action=f"request_{wanted}", reason=reason, actor=actor,
                           result="denendi", detail={"requestId": request_id})
        try:
            result = await self._api.bbd_update_return_request(
                int(request_id), payload={"status": wanted}, reason=reason, actor=actor,
                dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(order_id=0, action=f"request_{wanted}", reason=reason, actor=actor,
                               result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure),
                    "missing": self._missing(failure)}

        if not result.get("dryRun", dry_run):
            self._list_cache.clear()            # durum çipi hemen doğru saymalı
        await self._record(order_id=0, action=f"request_{wanted}", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"requestId": request_id})
        return {"ok": True, "error": "", "status": wanted,
                "statusLabel": calc.STATUS_LABELS[wanted],
                "dryRun": bool(result.get("dryRun", dry_run))}

    @classmethod
    def _return_view(cls, payload: Any) -> dict[str, Any]:
        """İade gönderisi yanıtını tek biçime indirir.

        İki uç iki biçim döner: gönderi ucu gönderinin KENDİSİNİ
        (`ShipmentController::present`), talep ucu ise talebin kargo özetini
        (`{labelPurchased, shipment: {...}}` — `ReturnShipmentController::present`).
        """
        source = payload if isinstance(payload, dict) else {}
        if isinstance(source.get("data"), dict):
            source = source["data"]
        nested = source.get("shipment")
        row = cls._shipment_row(nested if isinstance(nested, dict) else source)
        return {"id": row["id"], "tracking": row["tracking"], "carrier": row["carrier"],
                "purchased": row["purchased"] or bool(_flag(source.get("labelPurchased")))}

    async def return_shipment(self, shipment_id: int, *, order_id: int = 0, request_id: int = 0,
                              reason: str, actor: str, dry_run: bool = True) -> dict[str, Any]:
        """Müşteriden geri alım: iade gönderisini AÇAR ve ETİKETİNİ SATIN ALIR.

        PARA HARCAR (kargo ücreti) — `store_refunds.ship_return` izni + gerekçe
        (ADR 0012). Kullanıcı TEK onay verir; arkada iki adım koşar:

          1. Gönderiyi aç — para harcamaz, mağazada mükerrer korumalıdır: aynı
             gönderi/talep için ikinci kayıt açılmaz, var olan döner.
          2. Etiketi satın al — PARA HARCAR. Etiket zaten alınmışsa bu adım
             ATLANIR; ikinci etiket alınmaz.

        NEDEN İKİ ADIM. Eskiden yalnız 1. adım çağrılıyordu ve ekran "ücret
        mağazaya yansır" derken mağazada yalnız bir TASLAK oluşuyordu: etiket
        yok, takip numarası yok. Gönderi ucunun `purchaseNow` alanı bunu tek
        istekte yapabilir ama geçidin `bbd_return_shipment` metodu gövdeye alan
        koymuyor; mağazanın kendi RMA zinciri de aynı iki adımı kullanır
        (`ReturnShipmentManager::purchaseLabel` → `ShipmentManager::purchase`).

        HANGİ UÇ. RMA talebi varsa (`request_id`) talep zinciri kullanılır: iade
        gönderisi talebe bağlanır ve "ürün geldi mi" bilgisi talebe düşer. Talep
        mağazada ONAYLANMIŞ olmalıdır; değilse mağazanın kendi açıklaması
        gösterilir. Talep yoksa giden gönderinin üzerinden açılır.

        2. adım patlarsa taslak kalır ve bu AÇIKÇA söylenir; yeniden denemek
        güvenlidir çünkü 1. adım var olan taslağı döndürür.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        shipment_id, request_id = calc.as_int(shipment_id), calc.as_int(request_id)
        if shipment_id <= 0 and request_id <= 0:
            return {"ok": False, "error": "Geri gönderim için bir gönderi seçilmedi."}

        detail: dict[str, Any] = {"shipmentId": shipment_id, "requestId": request_id}

        async def note(result: str, **extra: Any) -> None:
            await self._record(order_id=order_id, action="return_shipment", reason=reason,
                               actor=actor, result=result, detail={**detail, **extra})

        await note("denendi")
        try:
            if request_id > 0:
                opened = await self._api.bbd_open_return_request_shipment(
                    request_id, reason=reason, actor=actor, dry_run=dry_run)
            else:
                opened = await self._api.bbd_return_shipment(
                    shipment_id, reason=reason, actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await note("hata", step="open", error=str(failure))
            return {"ok": False, "error": self._fail(failure),
                    "missing": self._missing(failure)}

        if (opened or {}).get("dryRun", dry_run):
            await note("dry_run")
            return {"ok": True, "error": "", "dryRun": True, "purchased": False,
                    "tracking": "", "carrier": "",
                    "message": "Kuru prova: gönderi açılmadı, etiket satın alınmadı."}

        first = self._return_view(opened)
        if first["purchased"]:
            await note("ok", alreadyPurchased=True, returnId=first["id"])
            return {"ok": True, "error": "", "dryRun": False, "purchased": True,
                    "tracking": first["tracking"], "carrier": first["carrier"],
                    "message": "Bu gönderinin iade etiketi zaten alınmış; ikinci etiket "
                               "alınmadı."}

        draft_note = ("İade gönderisi açıldı ama etiket SATIN ALINMADI: {why} Yeniden "
                      "denerseniz aynı gönderi kullanılır; ikinci gönderi açılmaz.")
        try:
            if request_id > 0:
                bought = await self._api.bbd_purchase_return_request_label(
                    request_id, reason=reason, actor=actor, dry_run=dry_run)
            elif first["id"] > 0:
                bought = await self._api.bbd_purchase_shipment(
                    first["id"], offer_id="", reason=reason, actor=actor, dry_run=dry_run)
            else:
                await note("taslak", error="iade gönderisi numarası yanıtta yok")
                return {"ok": False, "draft": True,
                        "error": draft_note.format(why="gönderi numarası okunamadı.")}
        except Exception as failure:  # noqa: BLE001 — K7; taslak açık kaldı, söylenir
            await note("taslak", step="purchase", returnId=first["id"], error=str(failure))
            return {"ok": False, "draft": True,
                    "error": draft_note.format(why=f"{self._fail(failure)}.")}

        second = self._return_view(bought)
        sandbox = bool((bought or {}).get("dryRun", dry_run))
        purchased = second["purchased"] and not sandbox
        if purchased:
            self._list_cache.clear()
        await note("ok" if purchased else "taslak", returnId=second["id"] or first["id"],
                   tracking=second["tracking"])
        return {"ok": True, "error": "", "dryRun": sandbox, "purchased": purchased,
                "tracking": second["tracking"] or first["tracking"],
                "carrier": second["carrier"] or first["carrier"],
                "message": "" if purchased else
                ("İade gönderisi açıldı ama mağaza etiketin satın alındığını doğrulamadı; "
                 "Kargo ekranından kontrol edin.")}

    async def notify_customer(self, order_id: int, *, message: str, reason: str, actor: str,
                              dry_run: bool = True) -> dict[str, Any]:
        """Müşteriye bilgi verir — siparişe not düşerek.

        Mağazanın "sipariş notu" ucu notu kaydeder VE müşteriye e-posta atar.
        Ayrı bir bildirim şablonu ekranı (`store_notifications`) varken burada
        şablon kurmak, aynı metnin iki yerde yaşaması olurdu.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        body = calc.text(message)
        if len(body) < 10:
            return {"ok": False, "error": "Müşteriye gidecek mesaj en az 10 karakter olmalı."}

        try:
            result = await self._api.add_order_comment(int(order_id), comment=body, notify=True,
                                                       reason=reason, actor=actor,
                                                       dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(order_id=order_id, action="notify_customer", reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(order_id=order_id, action="notify_customer", reason=reason,
                           actor=actor, result="dry_run" if dry_run else "ok",
                           detail={"chars": len(body)})
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run))}

    # ================================================================ rapor

    @staticmethod
    def _credit_gap(payload: dict[str, Any]) -> str:
        """Kredi notları okunamadıysa arşivlenecek çıktı ÜRETİLMEZ.

        Eksik listeyi dosyaya yazmak, aylar sonra "o gün 3 iade varmış" diye
        okunacak yanlış bir belge bırakır. Ekranda eksik liste gösterilebilir
        (durumu yazıyor), diske yazılamaz.
        """
        source = payload["sources"]["refunds"]
        if source["available"]:
            return ""
        return (f"Kredi notları okunamadı ({source['error']}); dosya yazılmadı. "
                "Eksik listeyi arşivlemek yanlış rakamla belge bırakır.")

    async def export_csv(self, *, date_from: str = "", date_to: str = "") -> dict[str, Any]:
        """Pencerenin tamamının CSV'si — rapor klasörüne yazılır.

        Her zaman TAM tarama: ekranın ilk sayfası arşive girmez.
        """
        payload = await self.refunds(date_from=date_from, date_to=date_to, full=True)
        gap = self._credit_gap(payload)
        if gap:
            return {"ok": False, "error": gap}
        rows = payload["items"]
        headers = ["İade no", "Sipariş no", "Tarih", "Müşteri", "Adet", "Sebep", "Tutar",
                   "Yöntem", "Taşıyıcı", "Takip no", "Durum"]
        table = [[row["number"], row["orderNumber"], row["date"], row["customer"],
                  row["itemCount"], row["reasonLabel"] or "—", money(row["amount"]),
                  row["methodLabel"], row["carrier"] or "—", row["tracking"] or "—",
                  row["statusLabel"]] for row in rows]

        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        name = f"magaza-iade-listesi-{stamp}.csv"
        try:
            path = write_private(self._export_dir / name, csv_bytes(headers, table))
        except OSError as failure:
            return {"ok": False, "error": f"Dosya yazılamadı: {failure}"}
        return {"ok": True, "error": "", "path": str(path), "name": name,
                "rows": len(table), "truncated": payload["truncated"]}

    async def preview(self, kind: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Raporu üretir ve sayfalarını görüntü olarak döner (kit `reportChain`)."""
        produced = await self.build_report(kind, params or {})
        if not produced.get("ok"):
            return produced
        try:
            pages = await self._render_pages(Path(produced["path"]))
        except PreviewError as failure:
            return {**produced, "pages": [], "previewError": str(failure)}
        return {**produced, "pages": pages, "previewError": ""}

    async def build_report(self, kind: str, params: dict[str, Any]) -> dict[str, Any]:
        if kind not in ("summary", "form"):
            return {"ok": False, "error": f"Bilinmeyen rapor: {kind}"}
        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")

        if kind == "summary":
            payload = await self.refunds(date_from=calc.text(params.get("dateFrom")),
                                         date_to=calc.text(params.get("dateTo")), full=True)
            gap = self._credit_gap(payload)
            if gap:
                return {"ok": False, "error": gap}
            if not payload["items"]:
                return {"ok": False, "error": "Bu aralıkta rapora girecek iade yok."}
            content = self._summary_pdf(payload)
            name = f"magaza-iade-icmali-{stamp}.pdf"
            rows = len(payload["items"])
        else:
            order_id = calc.as_int(params.get("orderId"))
            if not order_id:
                return {"ok": False, "error": "İade formu için sipariş seçilmeli."}
            card = await self.order_card(order_id)
            if not card.get("ok"):
                return {"ok": False, "error": card.get("error") or "Sipariş okunamadı."}
            content = self._form_pdf(card)
            name = f"magaza-iade-formu-{order_id}-{stamp}.pdf"
            rows = len(card["order"]["lines"])

        try:
            path = write_private(self._export_dir / name, content)
        except (OSError, ExportError) as failure:
            return {"ok": False, "error": str(failure)}
        self._log.info("iade raporu üretildi", kind=kind, rows=rows, path=str(path))
        return {"ok": True, "error": "", "path": str(path), "name": name,
                "bytes": len(content), "rows": rows}

    def _summary_pdf(self, payload: dict[str, Any]) -> bytes:
        summary = payload["summary"]
        window = payload["range"]
        rate = summary["refundRate"]
        sections: list[dict[str, Any]] = [{
            "kind": "tiles", "title": "Özet",
            "tiles": [("Kayıt", number(summary["total"])),
                      ("Bekleyen", money(summary["pendingAmount"])),
                      ("Bu ay iade", money(summary["monthAmount"])),
                      ("İade oranı", "—" if rate is None else f"%{number(rate, 1)}")],
        }]

        by_status: dict[str, list[dict[str, Any]]] = {}
        for row in payload["items"]:
            by_status.setdefault(row["status"], []).append(row)
        sections.append({
            "kind": "table", "title": "Durum dağılımı",
            "headers": ["Durum", "Adet", "Tutar"], "align": "LRR",
            "widths": [2, 1, 1.4],
            "rows": [[calc.STATUS_LABELS[key], number(len(by_status.get(key, []))),
                      money(sum(row["amount"] for row in by_status.get(key, [])))]
                     for key in calc.STATUS_ORDER if by_status.get(key)],
        })
        sections.append({
            "kind": "table", "title": f"İadeler · {window['from']} – {window['to']}",
            "headers": ["İade no", "Sipariş", "Tarih", "Müşteri", "Sebep", "Tutar", "Durum"],
            "align": "LLLLLRL", "widths": [1.1, 1, 0.9, 2, 1.1, 1.1, 1.1],
            "rows": [[row["number"], row["orderNumber"] or "—", row["date"], row["customer"],
                      row["reasonLabel"] or "—", money(row["amount"]), row["statusLabel"]]
                     for row in payload["items"][:400]],
        })
        if summary.get("rateNote"):
            sections.append({"kind": "note", "text": summary["rateNote"]})
        if payload["truncated"]:
            sections.append({"kind": "note",
                             "text": "Liste tavana dayandı; aralığı daraltın."})
        for warning in payload["warnings"]:
            sections.append({"kind": "note", "text": warning})

        return build_pdf(title="İade icmali",
                         subtitle=f"{window['from']} – {window['to']} · "
                                  f"{number(summary['total'])} kayıt",
                         sections=sections, footer="Kontrol Merkezi · Mağaza")

    def _form_pdf(self, card: dict[str, Any]) -> bytes:
        order = card["order"]
        sections: list[dict[str, Any]] = [{
            "kind": "tiles", "title": "Sipariş",
            "tiles": [("Sipariş no", order["orderNumber"] or "—"),
                      ("Tarih", order["orderDate"][:10] or "—"),
                      ("Müşteri", order["customer"] or "—"),
                      ("Sipariş tutarı", money(order["grandTotal"]))],
        }, {
            "kind": "table", "title": "İade edilebilir kalemler",
            "headers": ["SKU", "Ürün", "Sipariş", "Faturalanan", "İade edilen",
                        "İade edilebilir", "Birim"],
            "align": "LLRRRRR", "widths": [1.1, 2.6, 0.7, 0.9, 0.9, 1, 1],
            "rows": [[line["sku"], line["name"], number(line["qtyOrdered"]),
                      number(line["qtyInvoiced"]), number(line["qtyRefunded"]),
                      number(line["refundable"]), money(line["unitPrice"])]
                     for line in order["lines"]],
        }, {
            "kind": "note",
            "text": "Müşteri iade edeceği kalemleri işaretler, kutuya bu formu koyar. "
                    f"İade edilebilir kargo bedeli: {money(order['shippingAvailable'])}.",
        }]
        if card.get("basisNotice"):
            sections.append({"kind": "note", "text": card["basisNotice"]})
        return build_pdf(title="İade formu",
                         subtitle=f"{order['orderNumber'] or order['orderId']} · "
                                  f"{order['customer']}",
                         sections=sections, footer="Kontrol Merkezi · Mağaza")

    async def _render_pages(self, path: Path, *, max_pages: int = 12,
                            dpi: int = 110) -> list[str]:
        binary = shutil.which("pdftoppm")
        if not binary:
            raise PreviewError(
                "Önizleme üretilemedi: `pdftoppm` yok (poppler-utils kurulmalı). "
                "Rapor yine de kaydedildi ve yazdırılabilir.")
        with tempfile.TemporaryDirectory(prefix="km-iade-onizleme-") as folder:
            target = Path(folder) / "sayfa"
            try:
                process = await asyncio.create_subprocess_exec(
                    binary, "-png", "-r", str(int(dpi)), "-f", "1", "-l", str(int(max_pages)),
                    str(path), str(target),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                _, err = await asyncio.wait_for(process.communicate(), timeout=60)
            except TimeoutError:
                raise PreviewError("Önizleme üretilemedi: süre aşıldı.") from None
            except OSError as failure:
                raise PreviewError(f"Önizleme üretilemedi: {failure}") from failure
            if process.returncode != 0:
                raise PreviewError(
                    f"Önizleme üretilemedi: {err.decode(errors='replace').strip()}")
            return ["data:image/png;base64," + base64.b64encode(item.read_bytes()).decode("ascii")
                    for item in sorted(Path(folder).glob("sayfa*.png"))]

    async def print_report(self, path: str, *, copies: int = 1) -> dict[str, Any]:
        """Üretilmiş raporu yazıcıya gönderir.

        GÜVENLİK: yalnız BİZİM rapor klasörümüzdeki dosya basılabilir. Serbest
        yol kabul etmek, `lp` ile makinedeki herhangi bir dosyayı kâğıda
        döktürmeye açık kapı bırakırdı.
        """
        return await print_report_file(self._printer, path, copies=copies, fail=self._fail,
                                       allowed_dir=lambda: self._export_dir)

    async def printer_status(self) -> dict[str, Any]:
        return await printer_status_of(self._printer, fail=self._fail)
