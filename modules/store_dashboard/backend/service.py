"""Kontrol Paneli — iş kuralları.

VERİ MAĞAZADADIR, KARAR BURADADIR. Her rakam `store.api` geçidinden gelir
(K4); bu modül Bagisto verisinin kopyasını tutmaz. Yerel tablolar yalnız
mağazada KARŞILIĞI OLMAYAN iki şeyi saklar: yazma gerekçesi (denetim izi) ve
bu ekranın görüntü tercihi (çalışma kanalı, karşılaştırma kipi, saat dilimi).

KART KART HATA (K7). Pano dokuz ayrı kaynaktan besleniyor. Biri patlarsa
ekranın tamamı düşmez: her uç kendi `{ok, connected, error}` üçlüsünü döner,
kart "okunamadı" der ve diğerleri dolar. Servis HTTP hatası FIRLATMAZ.

YAYINDA OLMAYAN UÇLAR. `/api/admin/bbd/*` uçları mağaza tarafında
YAZILMAKTADIR. Onlara bağlı kartlar (kritik stok, yedek, POS, kargo, BLD)
patlamaz: `available: False` + açıklama döner ve ekran "uç hazır olunca
açılacak" yazar. Sessiz sıfır göstermek, olmayan bir sağlığı var göstermektir.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import tempfile
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from km_sdk import ExportError, build_pdf, csv_bytes, money, number, report_dir, write_private

from . import config_map, metrics

#: Panonun rapor/CSV üretirken taradığı en çok satır. Tavan olmadan bozuk bir
#: `meta` sonsuz sayfalama üretir ve hız kovasını (dk 55) tüketir.
DEFAULT_SCAN_CAP = 2000

#: Pano rafının ömrü (saniye). Bkz. `_Shelf`.
DEFAULT_CACHE_SECONDS = 60

#: `reporting/overview` penceresi. Panonun oradan okuduğu İKİ alan da
#: (`orders.pendingCount`, `bld`) pencereden BAĞIMSIZDIR — pencere yalnız
#: sunucunun hesaplamadığı bölümleri küçük tutmak için dar verilir.
OVERVIEW_DAYS = 1

#: `outOfStock` KPI'sının neden boş olduğunun ÖLÇÜLMÜŞ nedeni.
#:
#: BULUNAN İSRAF (2026-08-14). Bu KPI için her pano açılışında
#: `bbd_catalog_health` çağrılıyordu ve yanıtta aranan alan HİÇ YOKTU. Uç
#: canlıda şunu döndürüyor:
#:     {"summary": {"no_image", "no_description", "no_meta", "zero_price",
#:                  "no_category", "not_indexed"},
#:      "issues": [...], "ignoredSkus": [...]}
#: Stokla ilgili tek bir alan bile yok; servis `out_of_stock`/`outOfStock`
#: arıyor, bulamıyor ve HER SEFERİNDE `None` dönüyordu. Yani ölçülen ~450 ms,
#: sonucu baştan belli bir soruya harcanıyordu.
#:
#: Çağrı kaldırıldı; KPI **aynı değeri** (`None`) veriyor, artık bedava.
#: Sessiz sıfır YAZILMADI: sıfır "hiçbir ürün tükenmedi" demektir ve bunu
#: bilmiyoruz. Kritik stok kartındaki eşik altı listesinden saymak da
#: yapılmadı — o BAŞKA bir soruya (eşiğin altı) verilen cevaptır ve iki ayrı
#: tanımdan tek rakam üretmek, hangisinin doğru olduğu bilinemeyen bir sayı
#: doğurur.
OUT_OF_STOCK_NOTE = (
    "Mağazada tükenen ürün sayısını veren bir uç yok: katalog sağlığı ucu yalnız "
    "görsel/açıklama/kategori/dizin sorunlarını sayıyor, stok alanı taşımıyor. "
    "Sayı uydurmamak için boş bırakıldı; eşiğin altındaki ürünler 'Kritik stok' "
    "kartında listeleniyor."
)

#: Yerel tercih anahtarları. Hepsi bu EKRANIN tercihidir; vitrini etkilemez.
PREF_KEYS = ("channel", "locale", "timezone", "date_format", "week_start", "compare")

#: Bakım modunun bu kurulumdaki gerçek yeri ve neden buradan YAZILMADIĞI.
#: Ekran bu metni aynen gösterir.
MAINTENANCE_NOTE = (
    "Bakım modu bu Bagisto kurulumunda `core_config` içinde DEĞİL, satış kanalı kaydında "
    "tutuluyor (`isMaintenanceOn` · `maintenanceModeText` · `allowedIps`); "
    "`general.content.maintenance_mode.*` anahtarları mağazanın ayar ağacında hiç ilan "
    "edilmemiş. Kanal yazma ucu ise kanal adını, bakım metnini ve vitrin SEO'sunu dile "
    "bağlı alt nesnelerle (`translations[]`) alıyor; tek kanallı canlı mağazada bu gövde "
    "denenmeden gönderilseydi 422 alınması iyi ihtimal, kanal adının ve vitrin meta "
    "alanlarının boşalması kötü ihtimaldi. Durum burada okunur gösterilir; vitrini kapatma "
    "işlemi doğrulanmış bir `store.api` metodu gelene kadar mağaza yönetiminden yapılır."
)


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


class PreviewError(RuntimeError):
    """Önizleme görüntüsü üretilemedi. Rapor yine de kaydedilmiştir."""


class _Shelf:
    """Panonun KISA ÖMÜRLÜ okuma rafı — ölçülmüş bir boşluğu kapatır.

    BULUNAN SORUN (2026-08-14, canlıya karşı ölçüldü). Panonun ikinci açılışı
    birincisi kadar sürüyordu; 18 isteğin 18'i yeniden gidiyordu. Sebep
    önbelleğin TTL'i ya da anahtarı DEĞİL: geçidin önbelleği (`store_api`
    `ReferenceCache`/`SnapshotCache`) yalnız `_cached()` üzerinden okunan
    REFERANS listelere (kategori ağacı, öznitelik, aile ve `snapshot()`
    parçaları) bağlı. Panonun çağırdığı uçların HİÇBİRİ oradan geçmiyor —
    yani önbellek bozuk değildi, bu yola HİÇ UYGULANMAMIŞTI.

    NEDEN GEÇİDE EKLENMEDİ. Geçidin kuralı yerinde duruyor: sipariş, ürün ve
    müşteri LİSTELERİ önbelleğe alınmaz, yoksa personel "kaydettim ama listede
    yok" yaşar. O kural yazma yapan ekranlar (Ürünler, Siparişler) içindir.
    Pano yazmaz; salt okunur bir özettir, kendiliğinden yenilenmez ve elinde
    "Yenile" düğmesi vardır. Raf bu yüzden PANONUN İÇİNDE durur ve yalnız
    panonun kendi yanıtlarını tutar; başka ekranın gördüğü veriye dokunmaz.

    TEK UÇUŞ (single-flight). Panel beş ucu aynı anda çağırıyor; ikisi
    (`pending`, `system`) aynı `reporting/overview` özetini istiyor. Aynı
    anahtar için ikinci çağrı yeni istek açmaz, süren isteği bekler. Bu
    `asyncio.gather` ile çoklu çağrı DEĞİLDİR — tam tersi: aynı çağrının iki
    kez gitmesini engeller.

    HATA ÖNBELLEĞE ALINMAZ — iki biçimiyle birden. Patlayan çağrı (istisna)
    bekleyenlerin hepsine aynen yansır ve saklanmaz. Servis HTTP hatası
    fırlatmadığı için ikinci biçim daha sinsi: kart "okunamadı" diyen bir
    SÖZLÜK döner. O da saklanmaz (`keep`); saklansaydı kartın üstündeki
    "Tekrar dene" düğmesi bir dakika boyunca aynı hatayı geri verir, yani
    hiçbir şey yapmayan bir düğme olurdu.

    Saat MONOTONİKtir: makine saati NTP ile geri alınırsa duvar saatli raf
    "gelecekte" kalır ve hiç tazelenmez.
    """

    def __init__(self, ttl: int) -> None:
        # ttl <= 0 "raf kapalı" demektir; ayarla kapatılabilsin diye aşağı
        # sınır 1'e çekilmez.
        self._ttl = max(0, int(ttl))
        self._values: dict[str, tuple[float, Any]] = {}
        self._flights: dict[str, asyncio.Task[Any]] = {}
        #: Geçersizleştirme sayacı: biri rafın tamamı, biri anahtar başına.
        #: Süren bir uçuş geçersizleştirmeden SONRA bitip rafa yazsaydı,
        #: "Yenile" düğmesi kendi eskisini geri koyardı.
        #:
        #: SAYAÇ NEDEN ANAHTAR BAŞINA. Tazeleme rafın TAMAMINI düşürseydi
        #: şöyle olurdu: panel beş ucu aynı anda çağırır, her biri sırayla
        #: rafı düşürür, en son düşüren dışındaki DÖRDÜNÜN sonucu "eski
        #: sayaçla üretildi" diye atılırdı. Dört kart bir sonraki açılışta
        #: yeniden mağazaya giderdi — kimse yanlış veri görmezdi ama
        #: tazeleme rafı boşaltmış olurdu.
        self._era = 0
        self._keys: dict[str, int] = {}

    def _stamp(self, key: str) -> int:
        return self._era + self._keys.get(key, 0)

    @staticmethod
    def worth_keeping(value: Any) -> bool:
        """Rafa konmaya değer mi — okunamamış kart SAKLANMAZ."""
        if not isinstance(value, dict):
            return True
        return value.get("connected") is not False and value.get("available") is not False

    async def read(self, key: str, produce: Callable[[], Awaitable[Any]], *,
                   fresh: bool = False) -> tuple[Any, int]:
        """`(değer, saniye_yaşı)`. Yaş 0 ise değer bu çağrıda üretildi."""
        if self._ttl <= 0:
            return await produce(), 0
        if fresh:
            # YALNIZ BU ANAHTAR düşer; süren uçuşu da bırakır ki tazeleme
            # gerçekten mağazaya gitsin.
            self._keys[key] = self._keys.get(key, 0) + 1
            self._values.pop(key, None)
            self._flights.pop(key, None)

        entry = self._values.get(key)
        if entry is not None:
            stamp, value = entry
            age = int(time.monotonic() - stamp)
            if age <= self._ttl:
                return value, age
            del self._values[key]

        mark = self._stamp(key)
        flight = self._flights.get(key)
        if flight is None:
            flight = asyncio.ensure_future(produce())
            # Sonucu kimse almadan uçuş biterse (bekleyen iptal edildi)
            # Python "alınmamış istisna" uyarısı basar; burada okunur.
            flight.add_done_callback(
                lambda done: None if done.cancelled() else done.exception())
            self._flights[key] = flight
        try:
            # `shield`: bekleyenin iptali süren isteği öldürmesin — diğer
            # bekleyen o sonucu kullanacak.
            value = await asyncio.shield(flight)
        finally:
            if self._flights.get(key) is flight and flight.done():
                del self._flights[key]
        if mark == self._stamp(key) and self.worth_keeping(value):
            self._values[key] = (time.monotonic(), value)
        return value, 0

    def drop(self) -> None:
        """Rafın TAMAMINI boşaltır (ayar yazıldıktan sonra). Süren uçuşlar
        iptal EDİLMEZ, yalnız artık rafa yazamaz."""
        self._values.clear()
        self._era += 1


class DashboardService:
    """Kontrol Paneli'nin tüm iş kuralları. HTTP hatası FIRLATMAZ."""

    def __init__(self, *, api: Any, store: Any, log: Any, config: dict[str, Any],
                 printer: Any = None, category: str = "Mağaza", subcategory: str = "Satış",
                 fallback_dir: Path | None = None) -> None:
        self._api = api
        self._store = store
        self._log = log
        self._config = config or {}
        self._printer = printer
        self._category = category
        self._subcategory = subcategory
        self._fallback = fallback_dir or Path.home() / "km-raporlar"

        self._audit = store.table("audit")
        self._prefs = store.table("prefs")
        self._shelf = _Shelf(metrics.as_int(self._config.get("cache_seconds"),
                                            DEFAULT_CACHE_SECONDS))

    # ------------------------------------------------------------- ayarlar

    async def _channel(self) -> str:
        return await self._pref("channel") or str(self._config.get("channel") or "default")

    async def _locale(self) -> str:
        return await self._pref("locale") or str(self._config.get("locale") or "tr")

    @property
    def _scan_cap(self) -> int:
        return max(100, min(20_000, metrics.as_int(self._config.get("scan_cap"),
                                                   DEFAULT_SCAN_CAP)))

    @property
    def _export_dir(self) -> Path:
        # HER ÇAĞRIDA yeniden çözülür: ay değişince klasör kendiliğinden değişir.
        return report_dir(self._category, subcategory=self._subcategory,
                          fallback=self._fallback,
                          configured=str(self._config.get("export_path") or ""))

    def _key(self, name: str, fallback: str) -> str:
        return str(self._config.get(name) or fallback)

    # ------------------------------------------------------ yerel tablolar

    async def _pref(self, key: str) -> str:
        try:
            row = await self._store.fetch_one(
                f"SELECT value FROM {self._prefs} WHERE key = ?", (key,))
        except Exception as failure:  # noqa: BLE001 — tercih okunamadı, varsayılan yeter
            self._log.warning("tercih okunamadı", key=key, error=str(failure))
            return ""
        return str(row["value"]) if row else ""

    async def _set_pref(self, key: str, value: str, actor: str) -> None:
        await self._store.execute(
            f"INSERT INTO {self._prefs} (key, value, actor, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, actor = excluded.actor, "
            "updated_at = excluded.updated_at",
            (key, value, actor, _now()),
        )

    async def _record(self, *, action: str, reason: str, actor: str, result: str,
                      detail: Any = None) -> None:
        """Yerel denetim izi. Bagisto denetim kaydı tutuyor ama GEREKÇEYİ
        tutmuyor; ayrıca ağ koparsa "ne yapmaya çalıştık" kaydı burada kalır."""
        try:
            await self._store.execute(
                f"INSERT INTO {self._audit} "
                "(action, reason, actor, result, detail, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (action, reason, actor, result,
                 json.dumps(detail or {}, ensure_ascii=False), _now()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın
            self._log.warning("denetim izi yazılamadı", action=action, error=str(failure))

    # ------------------------------------------------------------ yardımcı

    @staticmethod
    def _fail(failure: Exception) -> str:
        message = str(failure).strip()
        return message or "Mağazaya ulaşılamadı."

    @staticmethod
    def _pending(failure: Exception) -> str:
        """BBD uçları için hata metni. Uç yayında değilse ekran bunu söyler."""
        return (f"{DashboardService._fail(failure)} — bu bölüm mağaza tarafındaki BBD ucuna "
                "bağlı; uç hazır olunca kendiliğinden açılacak.")

    # ================================================================ özet

    async def summary(self, *, start: str = "", end: str = "", channel: str = "",
                      compare: str = "", fresh: bool = False) -> dict[str, Any]:
        """Panonun ana yükü: KPI + günlük ciro + durum + saat + en çok satan."""
        span = metrics.normalize_range(start, end)
        mode = compare if compare in metrics.COMPARE_MODES else \
            str(self._config.get("compare") or "previous")
        if mode not in metrics.COMPARE_MODES:
            mode = "previous"
        working = metrics.text(channel) or await self._channel()
        key = f"summary|{span['start']}|{span['end']}|{working}|{mode}"
        payload, age = await self._shelf.read(
            key, lambda: self._build_summary(span, mode, working), fresh=fresh)
        return {**payload, "ageSeconds": age}

    async def _build_summary(self, span: dict[str, Any], mode: str,
                             working: str) -> dict[str, Any]:
        """Özetin gerçek hesabı — raf boşken çalışır.

        TEK TARAMA, ÇOK ÇIKTI. Aynı sipariş kümesinden hem sekiz KPI hem üç
        grafik çıkar; her kart için ayrı istek atmak hız kovasını boşa harcar
        ve kartlar arasında tutarsız rakam üretirdi (arada sipariş gelebilir).

        DÖNEM + KARŞILAŞTIRMA DÖNEMİ TEK SORGUDA. Aralıklar bitişikse
        (varsayılan `previous` kipi hep öyledir) üç uç ikişer kez değil BİRER
        kez çağrılır; dönemlere ayırma zaten yerelde yapılıyordu. `lastYear`
        kipinde aralıklar bir yıl uzak olduğu için ayrı ayrı çağrılır
        (`metrics.merge_ranges`). Ölçüm: 7 istek → 3 istek, rakamlar aynı.
        """
        previous_span = metrics.previous_range(span["start"], span["end"], mode)
        notes: list[str] = [span["note"]] if span["note"] else []

        compared = mode != "none"
        window = metrics.merge_ranges(span, previous_span) if compared else None
        #: Siparişler/iadeler/müşteriler için mağazaya sorulacak aralık.
        #: Birleşik aralık dönemleri KAPSAR; ayrım aşağıda yerelde yapılır.
        asked = window or span

        scanned_rows, scan = await self._scan_orders(asked["start"], asked["end"], working)
        if not scan["ok"]:
            return {"ok": True, "connected": False, "error": scan["error"],
                    "range": span, "previousRange": previous_span, "compare": mode,
                    "channel": working, "kpis": [], "daily": [], "statuses": [],
                    "hours": [], "topProducts": [], "topSource": "", "notes": notes}
        notes.extend(scan["notes"])
        current = self._within(scanned_rows, span)

        refund_rows, refund_note = await self._refund_rows(asked["start"], asked["end"])
        customer_rows, customer_note = await self._customer_rows(asked["start"], asked["end"])

        numbers = metrics.snapshot_numbers(
            current,
            refunds=self._refund_sum(refund_rows, span),
            new_customers=self._customer_count(customer_rows, span),
            # Kaynağı olmayan rakam UYDURULMAZ (bkz. OUT_OF_STOCK_NOTE).
            out_of_stock=None)
        numbers["refundsNote"] = refund_note
        numbers["customersNote"] = customer_note
        numbers["outOfStockNote"] = OUT_OF_STOCK_NOTE

        previous_numbers: dict[str, Any] | None = None
        if compared:
            previous_numbers = await self._previous_numbers(
                previous_span, working, notes,
                merged=(scanned_rows, refund_rows, customer_rows) if window else None)

        top = self._top_products(current)
        return {
            "ok": True, "connected": True, "error": "",
            "range": span, "previousRange": previous_span, "compare": mode, "channel": working,
            "kpis": metrics.kpi_tiles(numbers, previous_numbers),
            "daily": metrics.daily_series(current),
            "statuses": metrics.status_counts(current),
            "hours": metrics.hour_counts(current),
            "topProducts": top,
            "topSource": "orders" if top else "",
            "cancelled": numbers["cancelled"],
            # Ekranda yazan "N sipariş tarandı" DÖNEMİN sayısıdır. Birleşik
            # sorgu karşılaştırma dönemini de getirdiği için ham satır sayısı
            # yazılsaydı kullanıcı iki katını görürdü.
            "scanned": len(current),
            "truncated": scan["truncated"],
            "notes": [note for note in notes if note],
        }

    async def _previous_numbers(self, previous_span: dict[str, str], working: str,
                                notes: list[str],
                                merged: tuple[list[Any], list[Any], list[Any]] | None,
                                ) -> dict[str, Any] | None:
        """Karşılaştırma döneminin rakamları.

        `merged` doluysa üç küme de birleşik sorgudan geldi ve YENİ İSTEK
        ATILMAZ — yalnız yerelde süzülür. Boşsa (uzak `lastYear` dönemi)
        dönemin kendi sorguları atılır.
        """
        if merged is not None:
            orders, refunds, customers = merged
            return metrics.snapshot_numbers(
                self._within(orders, previous_span),
                refunds=self._refund_sum(refunds, previous_span),
                new_customers=self._customer_count(customers, previous_span),
                out_of_stock=None)

        rows, scan = await self._scan_orders(previous_span["start"], previous_span["end"],
                                             working)
        if not scan["ok"]:
            notes.append("Karşılaştırma dönemi okunamadı; yüzdeler gösterilmiyor.")
            return None
        refunds, _ = await self._refund_rows(previous_span["start"], previous_span["end"])
        customers, _ = await self._customer_rows(previous_span["start"], previous_span["end"])
        return metrics.snapshot_numbers(
            self._within(rows, previous_span),
            refunds=self._refund_sum(refunds, previous_span),
            new_customers=self._customer_count(customers, previous_span),
            out_of_stock=None)

    def _top_products(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """En çok satan — sipariş KALEMLERİNDEN.

        KALDIRILAN YEDEK KAYNAK (ölçüm 2026-08-14). Liste boş kalınca
        `reporting/products` ucuna düşülüyordu. O uç ÜRÜN SATIRI DÖNDÜRMÜYOR;
        canlıda gelen yanıt tek elemanlı bir liste ve içeriği adet zaman
        serisi:
            [{"entity": "products", "type": "total-sold-quantities",
              "dateRange": {...}, "statistics": {"quantities": {...},
              "over_time": {...}}}]
        Üstelik geçidin tekil okuyucusu liste yanıtı sözlüğe çeviremediği için
        yedek kaynak `{}` alıyordu; yani çağrı sonucu HER ZAMAN boştu. Dönem
        siparişsizken bu, açılış başına bir istek israfıydı.

        `bbd/catalog/bestsellers` de yerine KONMADI: o tablo TÜM ZAMANLARIN
        net satış adedini tutuyor (`bbd_product_bestsellers`, günlük yeniden
        kuruluyor), dönemin değil. "Bugün en çok satan" başlığının altına tüm
        zamanların listesini koymak, boş listeden daha yanıltıcı olurdu.
        """
        return metrics.top_products(rows,
                                    metrics.as_int(self._config.get("top_products"), 10))

    @staticmethod
    def _within(rows: list[dict[str, Any]], span: dict[str, str]) -> list[dict[str, Any]]:
        """Taranan sipariş satırlarını bir döneme indirger."""
        return [row for row in rows
                if metrics.in_range(row["date"], span["start"], span["end"])]

    @staticmethod
    def _refund_sum(rows: list[dict[str, Any]] | None,
                    span: dict[str, str]) -> int | None:
        """İade toplamı. Liste OKUNAMADIYSA (`None`) sıfır değil `None` döner:
        sıfır "iade yok" demektir, oysa bilmiyoruz."""
        if rows is None:
            return None
        return sum(metrics.refund_total(item) for item in rows
                   if metrics.in_range(metrics.created_day(item),
                                       span["start"], span["end"]))

    @staticmethod
    def _customer_count(rows: list[dict[str, Any]] | None,
                        span: dict[str, str]) -> int | None:
        if rows is None:
            return None
        return len([item for item in rows
                    if metrics.in_range(metrics.created_day(item),
                                        span["start"], span["end"])])

    async def _scan_orders(self, start: str, end: str,
                           channel: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Aralığın siparişlerini tarar. SÜZME İŞİ ÇAĞIRANA AİT.

        TUZAK: Laravel tanımadığı sorgu parametresini SESSİZCE yok sayar.
        Tarih süzgecinin uygulandığı VARSAYILMAZ; dönen satırlar çağıran
        tarafından gün alanına göre ayrıca süzülür (`_within`). Böylece
        süzgeç yok sayılsa bile rakam doğru çıkar — yalnız tarama pahalılaşır
        ve ekran bunu söyler.

        ÖLÇÜM (2026-08-14): bu mağaza süzgeci UYGULUYOR — `date_from`/
        `date_to` verilen sorgu 18 yerine 0 satır döndürdü. Yerel süzme yine
        de kaldırılmadı: sürüm yükseltmesi süzgeci sessizce düşürebilir ve o
        gün rakam değil yalnız hız değişsin.
        """
        filters = {"channel": channel, "date_from": start, "date_to": end}
        try:
            payload = await self._api.orders(filters, all_pages=True)
        except Exception as failure:  # noqa: BLE001 — ekran ayakta kalmalı (K7)
            self._log.warning("sipariş taraması başarısız", error=str(failure))
            return [], {"ok": False, "error": self._fail(failure), "notes": [],
                        "truncated": False}

        raw = payload.get("items") or []
        capped = raw[:self._scan_cap]
        rows = [metrics.order_row(item) for item in capped if isinstance(item, dict)]
        # Süzgecin uygulandığı, SORULAN aralığa göre sınanır: dışarıda satır
        # varsa mağaza süzgeci yok saymıştır.
        outside = [row for row in rows if not metrics.in_range(row["date"], start, end)]

        notes: list[str] = []
        if outside:
            notes.append("Mağaza tarih süzgecini uygulamadı; sonuç yerelde süzüldü.")
        truncated = bool(payload.get("truncated")) or len(raw) > len(capped)
        if truncated:
            notes.append(f"Sipariş taraması {len(capped)} satırda kesildi; rakamlar EKSİK.")
        return rows, {"ok": True, "error": "", "notes": notes, "truncated": truncated}

    async def _refund_rows(self, start: str,
                           end: str) -> tuple[list[dict[str, Any]] | None, str]:
        """Aralığın iade satırları. Okunamazsa `None` — boş liste DEĞİL."""
        try:
            payload = await self._api.refunds({"date_from": start, "date_to": end},
                                              all_pages=True)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.info("iade toplamı okunamadı", error=str(failure))
            return None, self._fail(failure)
        return [item for item in (payload.get("items") or [])[:self._scan_cap]
                if isinstance(item, dict)], ""

    async def _customer_rows(self, start: str,
                             end: str) -> tuple[list[dict[str, Any]] | None, str]:
        """Dönemde açılan müşteri kayıtları.

        Sayım YERELDE yapılır: müşteri listesi ucunun tarih süzgecini
        uyguladığı belgelenmemiş. Liste tavana dayanırsa sayı alt sınırdır ve
        ekran bunu söyler.
        """
        try:
            payload = await self._api.customers({"created_at_from": start,
                                                 "created_at_to": end}, all_pages=True)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.info("yeni müşteri sayısı okunamadı", error=str(failure))
            return None, self._fail(failure)
        note = "Müşteri listesi tavana dayandı; sayı alt sınırdır." \
            if payload.get("truncated") else ""
        return [item for item in (payload.get("items") or [])[:self._scan_cap]
                if isinstance(item, dict)], note

    # ================================================================ kartlar

    async def _overview(self, *, fresh: bool = False) -> dict[str, Any]:
        """`reporting/overview` — İKİ kartın ortak, PENCEREDEN BAĞIMSIZ kaynağı.

        NE ALINIR, NEDEN. Uç tek çağrıda sipariş/kargo/BLD/ödeme linki özeti
        veriyor ama alanların bir kısmı ZAMAN PENCERESİNE bağlı. Panodan
        yalnız pencereden BAĞIMSIZ iki alan okunur (kaynak koddan doğrulandı,
        canlıda ölçüldü — bkz. `store.api.bbd_reporting_overview`):

            orders.pendingCount → "Ödeme/onay bekleyen sipariş" satırı
            bld                 → "BLD fiş kuyruğu" kartı

        `orders.byStatus` PENCEREYE BAĞLI olduğu için "Hazırlanıyor durumunda
        sipariş" satırı BURADAN ALINMADI; o hâlâ kendi tüm-zamanlar sorgusunu
        atıyor. Pencereli sayıyı tüm zamanların sayısı diye göstermek, hata
        vermeyen ama yanlış bir rakam üretirdi.

        Raf sayesinde iki kart aynı yanıtı paylaşır: panel ikisini aynı anda
        çağırsa bile mağazaya TEK istek gider. `fresh` ("Yenile") bu ortak
        kaydı da düşürür — düşürmeseydi tazelenmiş bir panonun iki satırı
        bir dakikaya kadar eski kalırdı.
        """
        payload, _ = await self._shelf.read(
            "overview", lambda: self._api.bbd_reporting_overview(days=OVERVIEW_DAYS),
            fresh=fresh)
        return payload if isinstance(payload, dict) else {}

    async def recent_orders(self, *, limit: int = 0, fresh: bool = False) -> dict[str, Any]:
        """Son siparişler — satır Siparişler ekranına gider."""
        count = limit or metrics.as_int(self._config.get("recent_orders"), 10)
        payload, age = await self._shelf.read(
            f"recent|{count}", lambda: self._recent_orders(count), fresh=fresh)
        return {**payload, "ageSeconds": age}

    async def _recent_orders(self, count: int) -> dict[str, Any]:
        try:
            payload = await self._api.orders(
                {"channel": await self._channel(), "sort": "created_at", "order": "desc"},
                page=1, per_page=max(1, min(50, count)))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": True, "connected": False, "error": self._fail(failure), "items": []}
        rows = [metrics.order_row(item) for item in (payload.get("items") or [])
                if isinstance(item, dict)]
        return {"ok": True, "connected": True, "error": "", "items": rows[:count]}

    async def critical_stock(self, *, limit: int = 0, fresh: bool = False) -> dict[str, Any]:
        """Kritik stok — Bagisto'nun kendi stok eşiği raporundan.

        BULUNAN HATA (2026-08-14). Burası katalog sağlığı ucuna `low_stock`
        diye bir sorun tipi soruyordu. ÖYLE BİR TİP YOK — canlıda ölçüldü,
        servisin tanıdığı tipler yalnızca:

            no_image · no_description · no_meta · zero_price · no_category
            · not_indexed

        Hiçbiri stokla ilgili değil. Kart bu yüzden her açılışta boş geliyor
        ve "uç henüz yayında değil" diyordu; oysa uç yayındaydı, SORU yanlıştı.
        Kartın adı "Kritik stok" olduğu için kullanıcı bunu "kritik stokta ürün
        yok" diye okuyordu — canlıda eşiğin altında 5 ürün varken.

        Doğru kaynak Bagisto'nun kendi panosu: `dashboard/stats` ucunun
        `stock-threshold-products` türü. Eşik değerini mağaza belirler
        (`catalog.inventory.stock_options.low_stock_threshold`), biz yeniden
        hesaplamayız — iki ayrı eşik iki ayrı cevap üretirdi.

        Uç sayfalanmaz; tek seferde eşik altındaki ürünleri döndürür, biz
        kartın istediği kadarını gösteririz.
        """
        count = limit or metrics.as_int(self._config.get("critical_stock"), 10)
        payload, age = await self._shelf.read(
            f"stock|{count}", lambda: self._critical_stock(count), fresh=fresh)
        return {**payload, "ageSeconds": age}

    async def _critical_stock(self, count: int) -> dict[str, Any]:
        try:
            payload = await self._api.dashboard_stats(kind="stock-threshold-products")
        except Exception as failure:  # noqa: BLE001 — K7: kart düşer, pano ayakta
            return {"ok": True, "available": False, "error": self._fail(failure), "items": []}

        satirlar = payload.get("statistics")
        if not isinstance(satirlar, list):
            satirlar = []

        items: list[dict[str, Any]] = []
        for raw in satirlar:
            if not isinstance(raw, dict):
                continue
            items.append({
                "id": metrics.as_int(raw.get("id")),
                "name": metrics.text(metrics.pick(raw, "name")) or "(adsız)",
                "sku": metrics.text(metrics.pick(raw, "sku")),
                # `total_qty` metin geliyor ("18") — sayıya çevrilmeden
                # sıralama alfabetik olur ve "9" > "18" çıkar.
                "stock": metrics.as_int(metrics.pick(raw, "total_qty", "quantity", "qty"), 0),
                "detail": metrics.text(metrics.pick(raw, "formatted_price")),
            })
        items.sort(key=lambda satir: satir["stock"])
        return {"ok": True, "available": True, "error": "", "items": items[:count],
                "total": len(items)}

    async def pending_work(self, *, fresh: bool = False) -> dict[str, Any]:
        """Bekleyen işler — her satır ilgili panele gider."""
        payload, age = await self._shelf.read(
            "pending", lambda: self._pending_work(fresh), fresh=fresh)
        return {**payload, "ageSeconds": age}

    async def _pending_work(self, fresh: bool) -> dict[str, Any]:
        """Satırlar TEK TEK hata verir: yorum ucu patlarsa iade talepleri yine
        listelenir. Sayı okunamayan satır "okunamadı" der, sıfır göstermez.

        "Ödeme/onay bekleyen sipariş" TOPLU ÖZETTEN gelir (`_overview`), geri
        kalanı kendi ucundan. Özet patlarsa yalnız O SATIR "okunamadı" der;
        diğer üç satır dolmaya devam eder (K7) — toplu uca geçmek "hepsi ya
        da hiçbiri" değildir.
        """
        rows: list[dict[str, Any]] = []

        async def count(label: str, target: str, payload_key: str,
                        call: Any, *, pending: bool = False) -> None:
            try:
                payload = await call()
            except Exception as failure:  # noqa: BLE001 — satır satır hata
                rows.append({"key": payload_key, "label": label, "target": target,
                             "count": None,
                             "error": self._pending(failure) if pending else self._fail(failure)})
                return
            meta = payload.get("meta") or {}
            total = meta.get("total")
            value = metrics.as_int(total) if total is not None \
                else len(payload.get("items") or [])
            rows.append({"key": payload_key, "label": label, "target": target,
                         "count": value, "error": ""})

        await count("Onay bekleyen yorum", "store_customers", "reviews",
                    lambda: self._api.reviews({"status": "pending"}, page=1, per_page=1))
        await count("Açık iade/değişim talebi", "store_requests", "returns",
                    lambda: self._api.bbd_return_requests({"status": "pending"}, page=1,
                                                          per_page=1),
                    pending=True)

        # KAYNAK: toplu özet (`orders.pendingCount`). Denetleyicide bu sayı
        # zaman penceresi UYGULANMADAN hesaplanıyor, yani eski `orders?
        # status=pending` sorgusunun tam karşılığı. Canlıda ikisi de 0 ölçüldü.
        try:
            overview = await self._overview(fresh=fresh)
        except Exception as failure:  # noqa: BLE001 — satır satır hata (K7)
            rows.append({"key": "pendingOrders", "label": "Ödeme/onay bekleyen sipariş",
                         "target": "store_orders", "count": None,
                         "error": self._pending(failure)})
        else:
            orders = overview.get("orders")
            if isinstance(orders, dict) and orders.get("pendingCount") is not None:
                rows.append({"key": "pendingOrders",
                             "label": "Ödeme/onay bekleyen sipariş",
                             "target": "store_orders",
                             "count": metrics.as_int(orders["pendingCount"]), "error": ""})
            else:
                # `orders: null` = "bu belirtecin sipariş yetkisi yok".
                # Sıfır göstermek yetkisizliği "iş yok" diye okuturdu.
                rows.append({"key": "pendingOrders",
                             "label": "Ödeme/onay bekleyen sipariş",
                             "target": "store_orders", "count": None,
                             "error": "Toplu özet sipariş bölümünü döndürmedi; "
                                      "belirtecin sipariş yetkisi olmayabilir."})

        # KAYNAK: kendi ucu. Toplu özetteki `byStatus` PENCEREYE bağlı olduğu
        # için oradan okunamaz — pencereli sayıyı tüm zamanların sayısı diye
        # göstermek sessiz bir yanlış rakam olurdu.
        await count("Hazırlanıyor durumunda sipariş", "store_orders", "processingOrders",
                    lambda: self._api.orders({"status": "processing"}, page=1, per_page=1))
        return {"ok": True, "items": rows}

    async def system_health(self, *, fresh: bool = False) -> dict[str, Any]:
        """Sistem sağlığı: geçit, mağaza, yedek, POS, kargo, BLD fişi, GİB."""
        payload, age = await self._shelf.read(
            "system", lambda: self._system_health(fresh), fresh=fresh)
        return {**payload, "ageSeconds": age}

    async def _system_health(self, fresh: bool) -> dict[str, Any]:
        cards: list[dict[str, Any]] = []

        gate: dict[str, Any] = {}
        try:
            gate = dict(self._api.state())
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("geçit durumu okunamadı", error=str(failure))

        try:
            health = await self._api.health()
        except Exception as failure:  # noqa: BLE001 — K7
            health = {"ok": False, "error": self._fail(failure)}
        cards.append({
            "key": "store", "label": "Mağaza bağlantısı",
            "state": "good" if health.get("ok") else "bad",
            "value": f"{metrics.as_int(health.get('elapsedMs'))} ms" if health.get("ok")
                     else "ulaşılamıyor",
            "detail": metrics.text(health.get("error")) or metrics.text(gate.get("baseUrl")),
        })
        cards.append({
            "key": "gate", "label": "Yazma kilidi",
            "state": "warn" if gate.get("readOnly") else "good",
            "value": "Salt okunur" if gate.get("readOnly") else "Yazma açık",
            "detail": "Acil fren açıkken hiçbir ekran mağazaya yazamaz."
                      if gate.get("readOnly")
                      else "Yazma istekleri mağazaya gidiyor; kuru prova varsayılan "
                           f"{'açık' if gate.get('dryRunDefault') else 'kapalı'}.",
        })

        cards.append(await self._backup_card())
        cards.append(await self._count_card(
            "pos", "Sanal POS", lambda: self._api.bbd_pos_terminals(),
            singular="terminal"))
        cards.append(await self._count_card(
            "carriers", "Kargo entegrasyonu", lambda: self._api.bbd_carriers(),
            singular="taşıyıcı"))
        cards.append(await self._failed_jobs_card(fresh=fresh))
        cards.append({
            "key": "gib", "label": "GİB / e-fatura",
            "state": "unknown", "value": "—",
            "detail": "Geçitte e-fatura sağlık ucu yok. Uç eklenince bu kart "
                      "kendiliğinden dolacak.",
        })
        return {"ok": True, "items": cards}

    async def _backup_card(self) -> dict[str, Any]:
        try:
            payload = await self._api.bbd_backups()
        except Exception as failure:  # noqa: BLE001 — uç henüz yayında olmayabilir
            return {"key": "backup", "label": "Son yedek", "state": "unknown", "value": "—",
                    "detail": self._pending(failure)}
        newest = metrics.newest_backup(payload.get("items") or [])
        if not newest:
            return {"key": "backup", "label": "Son yedek", "state": "bad", "value": "yok",
                    "detail": "Envanterde tarihli yedek bulunamadı."}
        age = metrics.backup_age_days(newest["createdAt"])
        limit = metrics.as_int(self._config.get("backup_warn_days"), 2)
        state = "good" if age is not None and age <= limit else "warn"
        return {"key": "backup", "label": "Son yedek", "state": state,
                "value": newest["createdAt"][:16].replace("T", " "),
                "detail": f"{newest['name']} · {age} gün önce" if age is not None
                          else newest["name"]}

    async def _count_card(self, key: str, label: str, call: Any, *,
                          singular: str) -> dict[str, Any]:
        try:
            payload = await call()
        except Exception as failure:  # noqa: BLE001 — uç henüz yayında olmayabilir
            return {"key": key, "label": label, "state": "unknown", "value": "—",
                    "detail": self._pending(failure)}
        items = payload.get("items") or []
        active = [item for item in items
                  if not isinstance(item, dict) or metrics.as_int(
                      metrics.pick(item, "status", "active"), 1)]
        return {"key": key, "label": label,
                "state": "good" if active else "warn",
                "value": f"{len(active)} etkin {singular}",
                "detail": f"Tanımlı {len(items)} {singular}."}

    async def _failed_jobs_card(self, *, fresh: bool = False) -> dict[str, Any]:
        """BLD fiş kuyruğu — KAYNAK: toplu özetin `bld` bölümü.

        Özet, fiş kuyruğunu durum başına sayıyor ve bu sayıda ZAMAN PENCERESİ
        YOK (denetleyicide `since` süzgeci uygulanmıyor) — yani tüm zamanların
        kuyruğu. Canlıda ölçüldü: `{"sent": 8}`.

        BULUNAN HATA (2026-08-14) — `critical_stock`takinin aynısı, başka
        kılıkta. Kart `bld/jobs?status=failed` diye soruyordu. `failed` DİYE
        BİR DURUM YOK: `BldPrintJob` yalnız dört durum tanıyor —

            pending · sent · duplicate · dead

        Laravel tanınmayan değeri hata saymaz, yalnız hiçbir satır eşleşmez;
        uç `total: 0` döndürüyordu ve kart HER AÇILIŞTA "sorun yok" diyordu.
        Kuyrukta ölü iş olsaydı da aynısını derdi — yani kart bir sağlık
        göstergesi değil, sabit bir yeşil ışıktı.

        Başarısızlığın gerçek adı `dead` (`BldPrintJob::isDead()`).
        `duplicate` başarısızlık DEĞİLDİR (`isDelivered()` onu teslim sayar),
        `pending` ise henüz yolda. Bu yüzden sayım `dead` üzerinden yapılır;
        `failed` adı, sunucu ileride o adı kullanırsa diye toplamda tutulur.
        Bugünkü rakam DEĞİŞMEZ (dead = 0 → "sorun yok"), ama artık ölü iş
        çıktığında kart bunu söyleyecek.
        """
        try:
            overview = await self._overview(fresh=fresh)
        except Exception as failure:  # noqa: BLE001 — uç henüz yayında olmayabilir
            return {"key": "bld", "label": "BLD fiş kuyruğu", "state": "unknown", "value": "—",
                    "detail": self._pending(failure)}
        queue = overview.get("bld")
        if not isinstance(queue, dict):
            # `null` = yetki yok ya da tablo yok. "Sorun yok" DEMEZ.
            return {"key": "bld", "label": "BLD fiş kuyruğu", "state": "unknown", "value": "—",
                    "detail": "Toplu özet BLD bölümünü döndürmedi; belirtecin BLD yetkisi "
                              "olmayabilir ya da fiş kuyruğu bu kurulumda yok."}
        failed = sum(metrics.as_int(queue.get(state), 0) for state in ("failed", "dead"))
        return {"key": "bld", "label": "BLD fiş kuyruğu",
                "state": "good" if failed == 0 else "bad",
                "value": "sorun yok" if failed == 0 else f"{failed} başarısız iş",
                "detail": "Başarısız işler Siparişler ekranından yeniden denenir."}

    # =============================================================== ayarlar

    #: Ayar grupları: (grup, config anahtarı, varsayılan core_config anahtarı, etiket)
    IDENTITY_FIELDS = (
        ("name", "identity_name_key", "general.content.shop_information.shop_name",
         "Mağaza adı"),
        ("email", "identity_email_key", "general.content.shop_information.shop_email",
         "E-posta"),
        ("phone", "identity_phone_key", "general.content.shop_information.shop_phone",
         "Telefon"),
        ("address", "identity_address_key", "general.content.shop_information.shop_address",
         "Adres"),
        ("taxNumber", "identity_tax_key", "general.content.shop_information.tax_number",
         "VKN / vergi dairesi"),
    )
    SEO_FIELDS = (
        ("metaTitle", "seo_title_key", "general.content.seo.meta_title", "Meta başlık"),
        ("metaDescription", "seo_description_key", "general.content.seo.meta_description",
         "Meta açıklama"),
        ("metaKeywords", "seo_keywords_key", "general.content.seo.meta_keywords",
         "Anahtar kelimeler"),
    )
    MAINTENANCE_FIELDS = (
        ("status", "maintenance_status_key", "general.content.maintenance_mode.status",
         "Bakım modu"),
        ("allowedIps", "maintenance_ips_key", "general.content.maintenance_mode.allowed_ips",
         "İzinli IP listesi"),
        ("message", "maintenance_text_key",
         "general.content.maintenance_mode.maintenance_mode_text", "Gösterilecek metin"),
    )

    async def settings(self) -> dict[str, Any]:
        """Ayarlar sekmesi. `store_settings` ekranı YOKTUR: mağaza kimliği,
        kanal/para/dil, bakım modu, SEO ve rapor klasörü buraya düştü."""
        channel = await self._channel()
        locale = await self._locale()
        out: dict[str, Any] = {
            "ok": True, "connected": True, "error": "", "storeAvailable": True,
            "local": {
                "channel": channel,
                "locale": locale,
                "timezone": await self._pref("timezone")
                or str(self._config.get("timezone") or "Europe/Istanbul"),
                "dateFormat": await self._pref("date_format")
                or str(self._config.get("date_format") or "gg.aa.yyyy"),
                "weekStart": metrics.as_int(await self._pref("week_start")
                                            or self._config.get("week_start"), 1),
                "compare": await self._pref("compare")
                or str(self._config.get("compare") or "previous"),
            },
            "reportDir": self._report_dir_state(),
            "channels": [], "currencies": [], "locales": [],
            "identity": {}, "seo": {}, "maintenance": {},
        }

        try:
            snapshot = await self._api.snapshot()
        except Exception as failure:  # noqa: BLE001 — K7
            out["connected"] = False
            out["error"] = self._fail(failure)
            snapshot = {"parts": {}}
        parts = snapshot.get("parts") or {}
        out["stale"] = bool(snapshot.get("stale"))
        out["channels"] = [{"code": metrics.text(item.get("code")),
                            "name": metrics.text(item.get("name")),
                            "currency": self._nested_code(item, "base_currency"),
                            "locale": self._nested_code(item, "default_locale")}
                           for item in parts.get("channels") or [] if isinstance(item, dict)]
        out["currencies"] = [{"code": metrics.text(item.get("code")),
                              "name": metrics.text(item.get("name"))}
                             for item in parts.get("currencies") or [] if isinstance(item, dict)]
        out["locales"] = [{"code": metrics.text(item.get("code")),
                           "name": metrics.text(item.get("name"))}
                          for item in parts.get("locales") or [] if isinstance(item, dict)]

        groups = await self._config_groups(channel, locale)
        out["storeAvailable"] = groups["available"]
        if not groups["available"]:
            out["error"] = out["error"] or groups["error"]
        out["identity"] = self._group_view(self.IDENTITY_FIELDS, groups["values"],
                                           self._key("identity_slug", "general.content"))
        out["seo"] = self._group_view(self.SEO_FIELDS, groups["values"],
                                      self._key("seo_slug", "general.content"))
        out["maintenance"] = self._group_view(self.MAINTENANCE_FIELDS, groups["values"],
                                              self._key("maintenance_slug", "general.content"))
        status = out["maintenance"]["fields"].get("status") or {}
        out["maintenance"]["enabled"] = bool(metrics.as_int(status.get("value"), 0)) \
            if status.get("found") else None
        return out

    @staticmethod
    def _nested_code(item: dict[str, Any], key: str) -> str:
        nested = item.get(key)
        if isinstance(nested, dict):
            return metrics.text(nested.get("code") or nested.get("name"))
        return metrics.text(item.get(f"{key}_code") or nested)

    async def _config_groups(self, channel: str, locale: str) -> dict[str, Any]:
        """Ayar bölümlerini slug başına BİR KEZ okur.

        Üç grup da aynı slug altında olabiliyor; her alan için ayrı istek
        atmak dakikada 55 isteklik kovayı ayar ekranına harcardı.
        """
        slugs = {self._key("identity_slug", "general.content"),
                 self._key("seo_slug", "general.content"),
                 self._key("maintenance_slug", "general.content")}
        values: dict[str, Any] = {}
        error = ""
        for slug in sorted(slugs):
            try:
                payload = await self._api.configuration(slug, channel=channel, locale=locale)
            except Exception as failure:  # noqa: BLE001 — K7
                error = error or self._fail(failure)
                continue
            found = payload.get("values") if isinstance(payload.get("values"), dict) else payload
            if isinstance(found, dict):
                values.update(found)
        return {"available": not error or bool(values), "error": error, "values": values}

    def _group_view(self, fields: tuple[tuple[str, str, str, str], ...],
                    values: dict[str, Any], slug: str) -> dict[str, Any]:
        out: dict[str, Any] = {"slug": slug, "fields": {}}
        for name, config_key, default_key, label in fields:
            entry = metrics.config_value(values, self._key(config_key, default_key))
            entry["label"] = label
            out["fields"][name] = entry
        return out

    def _report_dir_state(self) -> dict[str, Any]:
        """Rapor klasörünün gerçek durumu. Yol AYARDAN gelir ve ekrandan
        değiştirilmez: dosya yolu ayarını çalışırken yazmak, sonraki açılışta
        geri dönen bir "ayar" üretirdi."""
        configured = str(self._config.get("export_path") or "")
        try:
            path = self._export_dir
        except OSError as failure:
            return {"path": configured, "configured": bool(configured), "ready": False,
                    "error": f"Klasör açılamadı: {failure}", "freeBytes": None}
        free = None
        try:
            free = shutil.disk_usage(path).free
        except OSError:
            free = None
        return {"path": str(path), "configured": bool(configured),
                "ready": os.access(path, os.W_OK), "error": "", "freeBytes": free}

    async def save_settings(self, *, local: dict[str, Any] | None = None,
                            identity: dict[str, Any] | None = None,
                            seo: dict[str, Any] | None = None,
                            reason: str, actor: str,
                            dry_run: bool = True) -> dict[str, Any]:
        """Yerel tercihler + mağaza ayarları. Bakım modu BURADAN yazılmaz."""
        problem = metrics.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}

        changed: list[str] = []
        for key, value in (local or {}).items():
            slot = "date_format" if key == "dateFormat" else \
                "week_start" if key == "weekStart" else key
            if slot not in PREF_KEYS or value in (None, ""):
                continue
            await self._set_pref(slot, str(value), actor)
            changed.append(slot)
        # Çalışma kanalı/karşılaştırma değişmiş olabilir; raftaki yanıtlar o
        # tercihe göre kurulmuştu.
        self._shelf.drop()

        current = await self.settings()
        skipped: list[str] = []
        writes: dict[str, dict[str, Any]] = {}

        for group, payload, fields in (("identity", identity, self.IDENTITY_FIELDS),
                                       ("seo", seo, self.SEO_FIELDS)):
            for name, _config_key, _default, label in fields:
                if not payload or name not in payload:
                    continue
                entry = (current.get(group) or {}).get("fields", {}).get(name) or {}
                if not entry.get("found"):
                    skipped.append(label)
                    continue
                slug = current[group]["slug"]
                writes.setdefault(slug, {})[entry["key"]] = payload[name]
                changed.append(label)

        for slug, values in writes.items():
            try:
                await self._api.update_configuration(slug, values=values, reason=reason,
                                                     actor=actor,
                                                     channel=await self._channel(),
                                                     locale=await self._locale(),
                                                     dry_run=dry_run)
            except Exception as failure:  # noqa: BLE001 — K7
                await self._record(action="save_settings", reason=reason, actor=actor,
                                   result="hata", detail={"slug": slug, "error": str(failure)})
                return {"ok": False, "error": self._fail(failure), "changed": changed}

        await self._record(action="save_settings", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"changed": changed, "skipped": skipped})
        return {"ok": True, "error": "", "changed": changed, "skipped": skipped,
                "dryRun": dry_run}

    async def set_maintenance(self, *, enabled: bool, allowed_ips: str = "", message: str = "",
                              reason: str, actor: str, dry_run: bool = True) -> dict[str, Any]:
        """BAKIM MODU — vitrini kapatır. Ayrı izin, gerekçe ve kuru prova.

        Anahtar mağaza yapılandırmasında BULUNAMAZSA yazılmaz ve iş reddedilir:
        bulunmayan anahtara yazmak `core_config` içinde etkisiz bir satır açar,
        kullanıcı vitrini kapattığını sanır ve mağaza açık kalır.
        """
        problem = metrics.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}

        current = await self.settings()
        fields = (current.get("maintenance") or {}).get("fields") or {}
        status = fields.get("status") or {}
        if not status.get("found"):
            return {"ok": False,
                    "error": "Bakım modu anahtarı mağaza yapılandırmasında bulunamadı. "
                             "Bulunmayan anahtara yazmak vitrini kapatmaz, yalnız kapattığınızı "
                             "sandırırdı. Anahtar adı modül ayarından düzeltilebilir."}

        values: dict[str, Any] = {status["key"]: 1 if enabled else 0}
        for name, value in (("allowedIps", allowed_ips), ("message", message)):
            entry = fields.get(name) or {}
            if entry.get("found") and value != "":
                values[entry["key"]] = value

        await self._record(action="maintenance", reason=reason, actor=actor, result="denendi",
                           detail={"enabled": enabled})
        try:
            result = await self._api.update_configuration(
                current["maintenance"]["slug"], values=values, reason=reason, actor=actor,
                channel=await self._channel(), locale=await self._locale(), dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="maintenance", reason=reason, actor=actor, result="hata",
                               detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(action="maintenance", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"enabled": enabled, "keys": sorted(values)})
        return {"ok": True, "error": "", "enabled": enabled,
                "dryRun": bool(result.get("dryRun", dry_run)),
                "notice": "Vitrin kapandı; yalnız izinli IP'ler girebilir."
                          if enabled and not dry_run else ""}

    # ========================================================== yapılandırma

    async def config_view(self) -> dict[str, Any]:
        """Yapılandırma sekmesi — işletmenin dokunduğu ayarlar, kaynak rozetiyle.

        TEK İSTEK. Ağacın tamamı (156 KB · 344 alan) BURAYA bir kez gelir,
        ekrana yalnız beyaz listedeki ~30 alan çıkar. Alternatif on bir ayrı
        `configuration(slug)` çağrısıydı: kıt olan bant genişliği değil,
        dakikada 55 isteklik hız kovası. Onu ayar ekranına harcamak, aynı anda
        çalışan diğer ekranları sıraya sokardı.
        """
        channel = await self._channel()
        locale = await self._locale()
        out: dict[str, Any] = {
            "ok": True, "connected": True, "error": "", "channel": channel, "locale": locale,
            "groups": [], "declared": 0, "missing": [],
            "maintenance": {"available": False, "enabled": None, "message": "",
                            "allowedIps": "", "channel": channel, "error": "",
                            "note": MAINTENANCE_NOTE},
        }

        try:
            payload = await self._api.configuration_menu(
                include_values=True, channel=channel, locale=locale)
        except Exception as failure:  # noqa: BLE001 — ekran ayakta kalmalı (K7)
            self._log.warning("ayar ağacı okunamadı", error=str(failure))
            out["connected"] = False
            out["error"] = self._fail(failure)
            return out

        declared = config_map.declared_fields(self._menu_tree(payload))
        if not declared:
            out["connected"] = False
            out["error"] = ("Mağaza ayar ağacı boş döndü; hangi anahtarın gerçekten var "
                            "olduğu doğrulanamadığı için hiçbir alan açılmadı.")
            return out

        out["declared"] = len(declared)
        out["groups"] = config_map.build_view(declared)
        out["missing"] = [field["label"] for group in out["groups"]
                          for field in group["fields"] if not field["found"]]
        out["maintenance"] = await self._maintenance_view(channel)
        return out

    @staticmethod
    def _menu_tree(payload: Any) -> Any:
        """`configuration_menu` zarfını açar.

        Uç tek elemanlı liste veriyor ve geçit onu `{"items": [...]}` olarak
        sarıyor; ikisi de burada çözülür.
        """
        items = payload.get("items") if isinstance(payload, dict) else None
        first = items[0] if isinstance(items, list) and items else payload
        return first.get("tree") if isinstance(first, dict) else []

    async def _maintenance_view(self, channel_code: str) -> dict[str, Any]:
        """Bakım modunun GERÇEK durumu — satış kanalı kaydından, SALT OKUNUR."""
        out: dict[str, Any] = {"available": False, "enabled": None, "message": "",
                               "allowedIps": "", "channel": channel_code, "error": "",
                               "note": MAINTENANCE_NOTE}
        try:
            payload = await self._api.channels()
        except Exception as failure:  # noqa: BLE001 — K7
            out["error"] = self._fail(failure)
            return out
        rows = [row for row in (payload.get("items") or []) if isinstance(row, dict)]
        row = next((item for item in rows
                    if metrics.text(item.get("code")) == channel_code),
                   rows[0] if rows else None)
        if row is None:
            out["error"] = "Satış kanalı okunamadı; bakım modu durumu bilinmiyor."
            return out
        out.update(
            available=True,
            enabled=bool(row.get("isMaintenanceOn")),
            message=metrics.text(row.get("maintenanceModeText")),
            allowedIps=metrics.text(row.get("allowedIps")),
            channel=metrics.text(row.get("code")) or channel_code,
        )
        return out

    async def save_config(self, *, changes: dict[str, Any] | None, reason: str, actor: str,
                          dry_run: bool = True) -> dict[str, Any]:
        """Yapılandırma yazar. YAZMADAN ÖNCE ANAHTARIN VAR OLDUĞU DOĞRULANIR.

        Ağaç ekranın açılışında değil, KAYDETME ANINDA yeniden okunur. Ekran
        dakikalarca açık kalabiliyor; o sırada mağazada bir eklenti kapatılıp
        alan ilan edilmez hâle gelirse açılıştaki bilgiye güvenmek tam da
        kaçınmak istediğimiz sessiz başarısızlığı üretirdi: `core_config`
        içine kimsenin okumadığı bir satır.
        """
        problem = metrics.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}

        changes = {str(key): value for key, value in (changes or {}).items()}
        if not changes:
            return {"ok": False, "error": "Değişen alan yok."}
        if len(changes) > config_map.MAX_CHANGES:
            return {"ok": False,
                    "error": f"Tek seferde en çok {config_map.MAX_CHANGES} alan yazılabilir; "
                             f"{len(changes)} alan geldi."}

        channel = await self._channel()
        locale = await self._locale()
        try:
            payload = await self._api.configuration_menu(
                include_values=True, channel=channel, locale=locale)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False,
                    "error": f"{self._fail(failure)} Anahtarların var olduğu doğrulanamadı; "
                             "hiçbir şey yazılmadı."}

        declared = config_map.declared_fields(self._menu_tree(payload))
        if not declared:
            return {"ok": False,
                    "error": "Mağaza ayar ağacı boş döndü; anahtarların var olduğu "
                             "doğrulanamadı ve hiçbir şey yazılmadı."}

        writes, skipped = config_map.resolve_writes(changes, declared)
        if not writes:
            await self._record(action="save_config", reason=reason, actor=actor,
                               result="atlandı", detail={"skipped": skipped})
            return {"ok": False, "written": [], "skipped": skipped,
                    "error": "Gönderilen alanların hiçbiri mağazada yazılabilir değil; "
                             "hiçbir şey yazılmadı."}

        written: list[str] = []
        for slug, values in sorted(writes.items()):
            try:
                await self._api.update_configuration(
                    slug, values=values, reason=reason, actor=actor,
                    channel=channel, locale=locale, dry_run=dry_run)
            except Exception as failure:  # noqa: BLE001 — K7
                await self._record(action="save_config", reason=reason, actor=actor,
                                   result="hata",
                                   detail={"slug": slug, "written": written,
                                           "error": str(failure)})
                # Kısmi yazma GİZLENMEZ: önceki gruplar mağazaya gitti.
                return {"ok": False, "error": self._fail(failure),
                        "written": written, "skipped": skipped}
            written.extend(sorted(values))

        await self._record(action="save_config", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"written": written, "skipped": skipped,
                                   "dark": config_map.going_dark(changes, declared)})
        return {"ok": True, "error": "", "written": written, "skipped": skipped,
                "dryRun": dry_run}

    async def audit(self, *, limit: int = 50) -> dict[str, Any]:
        try:
            rows = await self._store.fetch_all(
                f"SELECT action, reason, actor, result, created_at FROM {self._audit} "
                "ORDER BY id DESC LIMIT ?", (max(1, min(500, int(limit))),))
        except Exception as failure:  # noqa: BLE001 — iz okunamadı, ekran dursun
            return {"ok": True, "items": [], "error": self._fail(failure)}
        return {"ok": True, "error": "", "items": [
            {"action": row["action"], "reason": row["reason"], "actor": row["actor"],
             "result": row["result"], "createdAt": row["created_at"]} for row in rows]}

    # ================================================================= rapor

    async def export_csv(self, *, start: str = "", end: str = "", channel: str = "",
                         compare: str = "") -> dict[str, Any]:
        """KPI + günlük ciro CSV'si — rapor klasörüne yazılır."""
        summary = await self.summary(start=start, end=end, channel=channel, compare=compare)
        if not summary.get("connected"):
            return {"ok": False, "error": summary.get("error") or "Mağazaya ulaşılamadı."}

        headers = ["Bölüm", "Ad", "Değer", "Önceki dönem", "Değişim %"]
        table: list[list[Any]] = []
        for tile in summary["kpis"]:
            value = tile["value"]
            shown = "—" if value is None else \
                (money(value) if tile["kind"] == "money" else number(value))
            previous = tile.get("previous")
            table.append(["KPI", tile["label"], shown,
                          "" if previous is None else
                          (money(previous) if tile["kind"] == "money" else number(previous)),
                          "" if not tile.get("delta") or tile["delta"]["percent"] is None
                          else tile["delta"]["percent"]])
        for point in summary["daily"]:
            table.append(["Günlük ciro", point["date"], money(point["value"]), "", ""])
        for part in summary["statuses"]:
            table.append(["Durum", part["label"], number(part["value"]), "", ""])

        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        name = f"magaza-pano-{stamp}.csv"
        try:
            path = write_private(self._export_dir / name, csv_bytes(headers, table))
        except OSError as failure:
            return {"ok": False, "error": f"Dosya yazılamadı: {failure}"}
        return {"ok": True, "error": "", "path": str(path), "name": name, "rows": len(table)}

    async def preview(self, kind: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        produced = await self.build_report(kind, params or {})
        if not produced.get("ok"):
            return produced
        try:
            pages = await self._render_pages(Path(produced["path"]))
        except PreviewError as failure:
            return {**produced, "pages": [], "previewError": str(failure)}
        return {**produced, "pages": pages, "previewError": ""}

    async def build_report(self, kind: str, params: dict[str, Any]) -> dict[str, Any]:
        if kind != "daily":
            return {"ok": False, "error": f"Bilinmeyen rapor: {kind}"}
        summary = await self.summary(start=metrics.text(params.get("start")),
                                     end=metrics.text(params.get("end")),
                                     channel=metrics.text(params.get("channel")),
                                     compare=metrics.text(params.get("compare")))
        if not summary.get("connected"):
            return {"ok": False, "error": summary.get("error") or "Mağazaya ulaşılamadı."}

        content = self._daily_pdf(summary)
        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        name = f"magaza-pano-ozeti-{stamp}.pdf"
        try:
            path = write_private(self._export_dir / name, content)
        except (OSError, ExportError) as failure:
            return {"ok": False, "error": str(failure)}
        self._log.info("pano raporu üretildi", path=str(path))
        return {"ok": True, "error": "", "path": str(path), "name": name,
                "bytes": len(content)}

    def _daily_pdf(self, summary: dict[str, Any]) -> bytes:
        span = summary["range"]
        tiles: list[tuple[str, str]] = []
        for tile in summary["kpis"]:
            value = tile["value"]
            shown = "—" if value is None else \
                (money(value) if tile["kind"] == "money" else number(value))
            if tile.get("delta") and tile["delta"]["percent"] is not None:
                shown += f"  ({tile['delta']['percent']:+.1f}%)"
            tiles.append((tile["label"], shown))

        sections: list[dict[str, Any]] = [{"kind": "tiles", "title": "Özet", "tiles": tiles}]
        if summary["statuses"]:
            sections.append({
                "kind": "table", "title": "Sipariş durumu dağılımı",
                "headers": ["Durum", "Adet"], "align": "LR", "widths": [3, 1],
                "rows": [[part["label"], number(part["value"])]
                         for part in summary["statuses"]],
            })
        if summary["topProducts"]:
            sections.append({
                "kind": "bars", "title": "En çok satan ürünler",
                "bars": [(item["name"], item["qty"], f"{number(item['qty'])} adet")
                         for item in summary["topProducts"]],
            })
        if summary["daily"]:
            sections.append({
                "kind": "table", "title": "Günlük ciro",
                "headers": ["Gün", "Ciro"], "align": "LR", "widths": [2, 1],
                "rows": [[point["date"], money(point["value"])] for point in summary["daily"]],
            })
        for note in summary.get("notes") or []:
            sections.append({"kind": "note", "text": note})

        return build_pdf(title="Mağaza pano özeti",
                         subtitle=f"{span['start']} – {span['end']} · kanal "
                                  f"{summary['channel']} · {summary['scanned']} sipariş tarandı",
                         sections=sections, footer="Kontrol Merkezi · Mağaza")

    async def _render_pages(self, path: Path, *, max_pages: int = 12,
                            dpi: int = 110) -> list[str]:
        binary = shutil.which("pdftoppm")
        if not binary:
            raise PreviewError(
                "Önizleme üretilemedi: `pdftoppm` yok (poppler-utils kurulmalı). "
                "Rapor yine de kaydedildi ve yazdırılabilir.")
        with tempfile.TemporaryDirectory(prefix="km-pano-onizleme-") as folder:
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
        if self._printer is None:
            return {"ok": False, "error": "Yazıcı yeteneği bu kurulumda yok."}
        try:
            resolved = Path(path).expanduser().resolve(strict=True)
        except OSError:
            return {"ok": False, "error": "Basılacak rapor bulunamadı."}
        allowed = self._export_dir.resolve()
        if not str(resolved).startswith(str(allowed) + os.sep):
            return {"ok": False,
                    "error": "Bu dosya rapor klasöründe değil; güvenlik gereği basılmaz."}
        try:
            result = await self._printer.print_file(resolved, title=resolved.name,
                                                    copies=max(1, min(20, int(copies))))
        except Exception as failure:  # noqa: BLE001 — yazıcı dışarısı
            return {"ok": False, "error": self._fail(failure)}
        return {"ok": True, **result, "name": resolved.name}

    async def printer_status(self) -> dict[str, Any]:
        if self._printer is None:
            return {"ready": False, "error": "Yazıcı yeteneği bu kurulumda yok."}
        try:
            return await self._printer.status()
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ready": False, "error": self._fail(failure)}
