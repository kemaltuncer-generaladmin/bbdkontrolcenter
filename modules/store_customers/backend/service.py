"""Müşteriler — iş kuralları.

VERİ MAĞAZADADIR, KARAR BURADADIR. Müşteri, adres, sipariş ve yorum `store.api`
geçidinden gelir (K4); bu modül Bagisto verisinin kopyasını DİSKTE tutmaz.
Yerel tablolar yalnız mağazada KARŞILIĞI OLMAYAN üç şeyi saklar: yazma
gerekçesi (denetim izi), yorumun "spam" etiketi ve mağaza yanıtının kopyası,
bu ekrandan yapılan izin değişikliklerinin geçmişi.

İKİ LİSTE YOLU — bilerek ayrı:

  · `source="customers"` — CANLI. Sunucu tarafında sayfalanır; arama, grup,
    durum süzgeçleri mağazaya gider. Varsayılan yol budur.
  · `source="segment"`   — TARAMA. RFM segmenti, harcama aralığı, şehir, son
    sipariş aralığı ve "e-postası doğrulanmamış" süzgeçlerini Bagisto'nun
    müşteri ucu KABUL ETMİYOR (canlıda uygulandığı doğrulanan süzgeçler:
    name · email · phone · customer_group_id · status[0/1]). Bunlar
    seçildiğinde nüfusun tamamı bir kez taranır, BELLEKTE (diskte değil) kısa
    süre tutulur ve süzme/sayfalama burada yapılır. Ekran hangi yolda olduğunu
    ve taramanın ne zaman yapıldığını SÖYLER.

SAYILAR SİPARİŞLERDEN GELİR. Mağazanın müşteri LİSTE ucu sipariş sayısı,
harcama ve son sipariş tarihi vermiyor; detay ucu yalnız ilk ikisini veriyor.
Üçü olmadan RFM segmenti hesaplanamaz, tablo baştan sona "—" olurdu. Bu yüzden
siparişler bir kez taranıp müşteriye göre toplulaştırılır (`_order_stats`).
Toplulaştırma yapılamazsa ya da eksikse SIFIR UYDURULMAZ: sütun boş kalır,
segment "Bilinmiyor" olur ve ekran nedenini yazar.

Tarama sonucu diske yazılmaz: müşteri adı ve e-postası kişisel veridir, ikinci
bir kopyası ne kadar az yerde durursa o kadar iyidir (KVKK).

UZAK SİSTEM DÜŞERSE EKRAN AYAKTA KALIR (K7): `connected: False` + `error`
döner, panel durumu anlatır. İstisna dışarı sızmaz.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from km_sdk import (
    ExportError,
    build_pdf,
    csv_bytes,
    money,
    number,
    percent,
    report_dir,
    write_private,
)

from . import analytics

#: Tarama tavanı. Geçit `max_items` ile zaten 5.000'de kesiyor; buradaki tavan
#: bozuk bir `meta` yüzünden sonsuz sayfalamayı ve bellekte devasa liste
#: tutmayı engeller.
SCAN_CAP = 5_000

#: Rapora giren en çok satır. Daha uzun tablo hem PDF'i şişiriyor hem de
#: kimsenin okumadığı 200 sayfa üretiyor.
REPORT_ROW_CAP = 1_500

REVIEW_ACTIONS = analytics.REVIEW_ACTIONS

#: Ekranın camelCase alanları → Bagisto alan adları. Listede OLMAYAN alan
#: gövdeye KONMAZ: tanımadığımız alanı geri göndermek, mağazanın başka bir
#: yerinden yazılmış değeri ezmektir.
FIELD_ALIASES = {
    "firstName": "first_name",
    "lastName": "last_name",
    "email": "email",
    "phone": "phone",
    "groupId": "customer_group_id",
    "newsletter": "subscribed_to_news_letter",
    "gender": "gender",
    "birthDate": "date_of_birth",
}

#: Ayarların `core_config` anahtarları: (ayar adı, varsayılan anahtar, etiket).
#: Anahtar sürüme göre değişebildiği için sabit yazılmaz; bulunmayan anahtara
#: YAZILMAZ ve ekran "bulunamadı" der — sessiz başarısızlık olmaz.
#:
#: VARSAYILANLAR CANLIDAN OKUNDU (`GET /api/admin/configuration?slug=…`).
#: "Üyeliksiz alışveriş" BİLEREK YOK: canlıda `customer.settings` altında böyle
#: bir anahtar bulunmuyor, Bagisto onu `sales.checkout` bölümünde tutuyor ve o
#: bölüm Siparişler/Ödeme ekranlarının işidir. Buraya koymak, hiçbir şeyi
#: değiştirmeyen bir anahtara yazıp "kaydettim" demek olurdu.
SETTING_KEYS = {
    "emailVerification": ("email_verification_key",
                          "customer.settings.email.verification",
                          "E-posta doğrulama"),
    "newsletterDefault": ("newsletter_default_key",
                          "customer.settings.create_new_account_options.news_letter",
                          "Yeni kayıtta bülten aboneliği"),
    "defaultGroup": ("default_group_key",
                     "customer.settings.create_new_account_options.default_group",
                     "Varsayılan müşteri grubu"),
    "gdprEnabled": ("gdpr_enabled_key", "general.gdpr.settings.enabled",
                    "KVKK modülü açık"),
    "gdprText": ("gdpr_text_key", "general.gdpr.agreement.agreement_content",
                 "KVKK aydınlatma metni"),
}

#: Onay kutusu olarak yazılan ayarlar (1/0). `defaultGroup` metin (grup KODU),
#: `gdprText` uzun metindir.
SETTING_FLAGS = ("emailVerification", "newsletterDefault", "gdprEnabled")


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def _today() -> str:
    return analytics.today_iso()


class PreviewError(RuntimeError):
    """Önizleme görüntüsü üretilemedi. Rapor yine de kaydedilmiştir."""


class CustomersService:
    """Müşteriler ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ."""

    def __init__(self, *, api: Any, store: Any, log: Any, config: dict[str, Any],
                 printer: Any = None, publish: Any = None, category: str = "Mağaza",
                 subcategory: str = "Müşteri", fallback_dir: Path | None = None) -> None:
        self._api = api
        self._store = store
        self._log = log
        self._config = config or {}
        self._printer = printer
        self._publish = publish
        self._category = category
        self._subcategory = subcategory
        self._fallback = fallback_dir or Path.home() / "km-raporlar"

        self._audit = store.table("audit")
        self._flags = store.table("review_flags")
        self._consent = store.table("consent")

        #: Nüfus taramasının BELLEKTEKİ kopyası. Diske yazılmaz (KVKK).
        self._scan: dict[str, Any] = {"rows": [], "at": 0.0, "truncated": False, "error": ""}
        #: Sipariş toplulaştırması — müşteri kaydında sipariş sayısı, harcama ve
        #: son sipariş tarihi OLMADIĞI için (canlıda doğrulandı) siparişlerden
        #: sayılır. O da bellekte durur; tutar ve tarih kişisel veri değildir
        #: ama diske ikinci bir kopya yazmanın da gereği yok.
        self._stats: dict[str, Any] = {"map": {}, "at": 0.0, "truncated": False, "error": ""}
        #: Ekran açılışında liste ve KPI uçları AYNI ANDA istenir; kilit
        #: olmasaydı ikisi de nüfusu/siparişleri baştan tarar ve mağazanın
        #: 60 istek/dk sınırı boşuna iki katı yenirdi.
        self._scan_lock = asyncio.Lock()
        self._stats_lock = asyncio.Lock()

    # ------------------------------------------------------------- ayarlar

    @property
    def _channel(self) -> str:
        return str(self._config.get("channel") or "default")

    @property
    def _locale(self) -> str:
        return str(self._config.get("locale") or "tr")

    @property
    def _page_size(self) -> int:
        return max(10, min(200, analytics.as_int(self._config.get("page_size"), 50)))

    @property
    def _scan_cap(self) -> int:
        return max(100, min(SCAN_CAP, analytics.as_int(self._config.get("segment_scan_cap"), 2000)))

    @property
    def _scan_ttl(self) -> int:
        return max(30, min(3600, analytics.as_int(self._config.get("segment_scan_ttl"), 300)))

    @property
    def _reply_field(self) -> str:
        return str(self._config.get("review_reply_field") or "reply")

    @property
    def _thresholds(self) -> dict[str, int]:
        limits = dict(analytics.DEFAULT_THRESHOLDS)
        for key, fallback in analytics.DEFAULT_THRESHOLDS.items():
            if self._config.get(key) is not None:
                limits[key] = analytics.as_int(self._config[key], fallback)
        return limits

    @property
    def _export_dir(self) -> Path:
        # HER ÇAĞRIDA yeniden çözülür: ay değişince klasör kendiliğinden değişir.
        return report_dir(self._category, subcategory=self._subcategory,
                          fallback=self._fallback,
                          configured=str(self._config.get("export_path") or ""))

    # ------------------------------------------------------ yerel tablolar

    async def _record(self, *, customer_id: int = 0, review_id: int = 0, action: str,
                      reason: str, actor: str, result: str, detail: Any = None) -> None:
        """Yerel denetim izi. Bagisto denetim tutuyor ama GEREKÇEYİ tutmuyor;
        ayrıca ağ koparsa "ne yapmaya çalıştık" kaydı yalnız burada kalır."""
        try:
            await self._store.execute(
                f"INSERT INTO {self._audit} "
                "(customer_id, review_id, action, reason, actor, result, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (int(customer_id or 0), int(review_id or 0), action, reason, actor, result,
                 json.dumps(detail or {}, ensure_ascii=False), _now()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın
            self._log.warning("denetim izi yazılamadı", action=action, error=str(failure))

    async def _record_consent(self, *, customer_id: int, kind: str, before: Any, after: Any,
                              actor: str, reason: str) -> None:
        try:
            await self._store.execute(
                f"INSERT INTO {self._consent} "
                "(customer_id, kind, before_value, after_value, actor, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (int(customer_id), kind, analytics.text(before), analytics.text(after),
                 actor, reason, _now()),
            )
        except Exception as failure:  # noqa: BLE001 — geçmiş yazılamadı, iş durmasın
            self._log.warning("izin geçmişi yazılamadı", kind=kind, error=str(failure))

    async def _review_flags(self, review_ids: list[int]) -> dict[int, dict[str, Any]]:
        ids = [int(item) for item in review_ids if analytics.as_int(item)]
        if not ids:
            return {}
        holes = ", ".join("?" for _ in ids)
        try:
            rows = await self._store.fetch_all(
                f"SELECT review_id, spam, reply FROM {self._flags} "
                f"WHERE review_id IN ({holes})", tuple(ids))
        except Exception as failure:  # noqa: BLE001 — etiket okunamadı, yorum yine gösterilir
            self._log.warning("yorum etiketleri okunamadı", error=str(failure))
            return {}
        return {analytics.as_int(row["review_id"]): dict(row) for row in rows}

    async def _set_flag(self, review_id: int, *, spam: bool | None = None, reply: str | None = None,
                        actor: str = "", reason: str = "") -> None:
        current = (await self._review_flags([review_id])).get(int(review_id)) or {}
        merged_spam = analytics.as_int(current.get("spam"), 0) if spam is None else int(bool(spam))
        merged_reply = analytics.text(current.get("reply")) if reply is None else reply
        try:
            await self._store.execute(
                f"INSERT INTO {self._flags} (review_id, spam, reply, actor, reason, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(review_id) DO UPDATE SET spam = excluded.spam, "
                "reply = excluded.reply, actor = excluded.actor, reason = excluded.reason, "
                "updated_at = excluded.updated_at",
                (int(review_id), merged_spam, merged_reply, actor, reason, _now()),
            )
        except Exception as failure:  # noqa: BLE001 — etiket yazılamadı, iş durmasın
            self._log.warning("yorum etiketi yazılamadı", reviewId=review_id, error=str(failure))

    # ------------------------------------------------------------- yardımcı

    @staticmethod
    def _fail(failure: Exception) -> str:
        message = str(failure).strip()
        return message or "Mağazaya ulaşılamadı."

    def _guard(self, reason: str) -> str:
        """Gerekçe backend'de DE doğrulanır (K9): arayüzde gizlemek yetmez."""
        return analytics.reason_error(reason)

    async def _emit(self, name: str, payload: dict[str, Any]) -> None:
        """Olay yayını iş akışını KESMEZ: dinleyicisi olmayan olay hata değildir."""
        if self._publish is None:
            return
        try:
            await self._publish(name, payload)
        except Exception as failure:  # noqa: BLE001 — olay yolu ekranı düşürmez (K7)
            self._log.warning("olay yayınlanamadı", event=name, error=str(failure))

    # ================================================================ liste

    def _needs_scan(self, *, segment: str, city: str, newsletter: str, status: str = "",
                    orders_min: int | None = None, orders_max: int | None = None,
                    spend_min: int | None = None, spend_max: int | None = None,
                    registered_from: str = "", registered_to: str = "",
                    last_order_from: str = "", last_order_to: str = "") -> bool:
        """Bu süzgeçleri mağaza KABUL ETMİYOR; taramaya düşülmeli.

        `status="unverified"` de buraya girer: mağazanın müşteri ucu `status`
        alanını 0/1 olarak biliyor, "e-postası doğrulanmamış" diye bir süzgeci
        yok. Canlı yola gönderilseydi Laravel tanımadığı değeri sessizce yok
        sayar ve ekran "doğrulanmamışlar" başlığı altında HERKESİ gösterirdi.
        """
        return bool(segment or city or newsletter or status == "unverified"
                    or orders_min is not None or orders_max is not None
                    or spend_min is not None or spend_max is not None
                    or registered_from or registered_to
                    or last_order_from or last_order_to)

    async def _order_stats(self, *, refresh: bool = False) -> dict[str, Any]:
        """Siparişlerin müşteriye göre toplulaştırılmış hâli — bellekte, kısa ömürlü.

        CANLIDA DOĞRULANDI: `GET /api/admin/customers` sipariş sayısı, harcama
        ve son sipariş tarihi taşımıyor; detay ucu `totalOrders` ve
        `totalAmountSpent` veriyor ama son sipariş tarihini o da vermiyor.
        Üçü olmadan RFM segmenti hesaplanamaz. Sipariş ucu üçünü de veriyor,
        bu yüzden tarama bir kez yapılıp `segment_scan_ttl` boyunca tutulur.

        Başarısız olursa boş sözlük + hata döner; çağıran satırları OLDUĞU GİBİ
        bırakır ve segment "Bilinmiyor" kalır — sıfır uydurulmaz (K7).
        """
        async with self._stats_lock:
            return await self._order_stats_locked(refresh=refresh)

    async def _order_stats_locked(self, *, refresh: bool) -> dict[str, Any]:
        fresh = (time.monotonic() - self._stats["at"]) < self._scan_ttl
        if not refresh and fresh and self._stats["map"]:
            return self._stats

        try:
            payload = await self._api.orders({}, all_pages=True)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("sipariş toplulaştırması yapılamadı", error=str(failure))
            if self._stats["map"]:
                return {**self._stats, "error": self._fail(failure)}
            return {"map": {}, "at": 0.0, "truncated": False, "error": self._fail(failure)}

        rows = [analytics.order_row(item) for item in (payload.get("items") or [])
                if isinstance(item, dict)]
        self._stats = {
            "map": analytics.order_stats(rows),
            "at": time.monotonic(),
            "truncated": bool(payload.get("truncated")),
            "error": "",
        }
        return self._stats

    def _enrich(self, rows: list[dict[str, Any]], stats: dict[str, Any],
                *, limits: dict[str, int]) -> None:
        """Satırları sipariş toplulaştırmasıyla tamamlar — tarama EKSİKSİZSE.

        Tarama tavana dayandıysa (`truncated`) toplulaştırma eksiktir: eksik
        listeyle "bu müşterinin hiç siparişi yok" demek, siparişi olan
        müşteriyi yanlış segmente düşürür. O durumda satırlara dokunulmaz.
        """
        if stats.get("error") or stats.get("truncated"):
            return
        today = _today()
        for row in rows:
            analytics.apply_order_stats(row, stats["map"], today=today, thresholds=limits)

    async def customers(self, *, q: str = "", segment: str = "", group_id: int = 0,
                        status: str = "", city: str = "", newsletter: str = "",
                        orders_min: int | None = None, orders_max: int | None = None,
                        spend_min: int | None = None, spend_max: int | None = None,
                        registered_from: str = "", registered_to: str = "",
                        last_order_from: str = "", last_order_to: str = "",
                        sort: str = "", order: str = "desc", page: int = 1,
                        size: int = 0, refresh: bool = False) -> dict[str, Any]:
        """Müşteri listesi. Süzgece göre CANLI ya da TARAMA yolu seçilir."""
        per_page = size or self._page_size
        scan_needed = self._needs_scan(
            segment=segment, city=city, newsletter=newsletter, status=status,
            orders_min=orders_min, orders_max=orders_max, spend_min=spend_min,
            spend_max=spend_max, registered_from=registered_from, registered_to=registered_to,
            last_order_from=last_order_from, last_order_to=last_order_to)

        if scan_needed:
            return await self._from_scan(
                q=q, segment=segment, group_id=group_id, status=status, city=city,
                newsletter=newsletter, orders_min=orders_min, orders_max=orders_max,
                spend_min=spend_min, spend_max=spend_max, registered_from=registered_from,
                registered_to=registered_to, last_order_from=last_order_from,
                last_order_to=last_order_to, page=page, size=per_page, refresh=refresh)

        filters: dict[str, Any] = {}
        query = analytics.text(q)
        if query:
            # Bagisto arama alanını ad/e-posta/telefon olarak AYIRIYOR; "@"
            # içeren metni ada göndermek personelin e-postayla arayamamasına
            # yol açıyordu.
            if "@" in query:
                filters["email"] = query
            elif query.replace(" ", "").isdigit():
                filters["phone"] = query
            else:
                filters["name"] = query
        if group_id:
            filters["customer_group_id"] = int(group_id)
        if status in ("0", "1"):
            filters["status"] = int(status)
        if sort:
            filters["sort"] = sort
            filters["order"] = "asc" if order == "asc" else "desc"

        try:
            payload = await self._api.customers(filters, page=page, per_page=per_page)
        except Exception as failure:  # noqa: BLE001 — ekran ayakta kalmalı (K7)
            self._log.warning("müşteri listesi okunamadı", error=str(failure))
            return self._empty_list(page, per_page, error=self._fail(failure))

        limits = self._thresholds
        today = _today()
        rows = [analytics.customer_row(item, today=today, thresholds=limits)
                for item in (payload.get("items") or [])]
        stats = await self._order_stats()
        self._enrich(rows, stats, limits=limits)
        meta = payload.get("meta") or {}
        unknown = len([row for row in rows if row["segment"] == "unknown"])
        return {
            "ok": True, "connected": True, "error": "",
            "items": rows,
            "total": analytics.as_int(meta.get("total"), len(rows)),
            "page": analytics.as_int(meta.get("currentPage"), page),
            "size": analytics.as_int(meta.get("perPage"), per_page),
            "pages": analytics.as_int(meta.get("lastPage"), 1),
            "source": "customers",
            "scannedAt": "",
            "truncated": False,
            "unknownSegments": unknown,
            "thresholds": limits,
            "statsError": analytics.text(stats.get("error")),
            "statsTruncated": bool(stats.get("truncated")),
        }

    @staticmethod
    def _empty_list(page: int, size: int, *, error: str, source: str = "customers",
                    scanned_at: str = "") -> dict[str, Any]:
        return {"ok": True, "connected": False, "error": error, "items": [], "total": 0,
                "page": page, "size": size, "pages": 0, "source": source,
                "scannedAt": scanned_at, "truncated": False, "unknownSegments": 0,
                "thresholds": dict(analytics.DEFAULT_THRESHOLDS),
                "statsError": "", "statsTruncated": False}

    async def _scan_rows(self, *, refresh: bool = False) -> dict[str, Any]:
        """Nüfusun tamamı — BELLEKTE, kısa ömürlü.

        Bagisto sipariş toplamına göre süzdürmediği için segment/harcama
        süzgeçleri nüfusu gerektiriyor. Tarama sayfa sayfa ve SIRAYLA yapılır
        (geçit hız kovasını kendisi tutar); sonuç `segment_scan_ttl` boyunca
        bellekte kalır ki her süzgeç değişikliği yeni bir tarama başlatmasın.
        Diske YAZILMAZ: kişisel verinin ikinci kopyası olmasın.
        """
        async with self._scan_lock:
            return await self._scan_rows_locked(refresh=refresh)

    async def _scan_rows_locked(self, *, refresh: bool) -> dict[str, Any]:
        fresh = (time.monotonic() - self._scan["at"]) < self._scan_ttl
        if not refresh and fresh and self._scan["rows"]:
            return self._scan

        try:
            payload = await self._api.customers({}, all_pages=True)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("müşteri taraması yapılamadı", error=str(failure))
            # Bayat tarama HİÇBİR ŞEYDEN iyidir; yaşı ekranda yazar.
            if self._scan["rows"]:
                return {**self._scan, "error": self._fail(failure)}
            return {"rows": [], "at": 0.0, "truncated": False, "error": self._fail(failure)}

        limits = self._thresholds
        today = _today()
        raw = payload.get("items") or []
        rows = [analytics.customer_row(item, today=today, thresholds=limits)
                for item in raw[:self._scan_cap]]
        # Segment, harcama ve "son sipariş" süzgeçlerinin tamamı siparişlerden
        # gelen sayılara dayanıyor; tarama onlarsız hiçbir işe yaramaz.
        stats = await self._order_stats(refresh=refresh)
        self._enrich(rows, stats, limits=limits)
        self._scan = {
            "rows": rows,
            "at": time.monotonic(),
            "stamp": _now(),
            "truncated": bool(payload.get("truncated")) or len(raw) > self._scan_cap,
            "error": "",
            "statsError": analytics.text(stats.get("error")),
            "statsTruncated": bool(stats.get("truncated")),
        }
        return self._scan

    async def _from_scan(self, *, page: int, size: int, refresh: bool,
                         **filters: Any) -> dict[str, Any]:
        scan = await self._scan_rows(refresh=refresh)
        if not scan["rows"]:
            return self._empty_list(page, size, error=scan["error"] or "Tarama boş döndü.",
                                    source="segment")

        matched = [row for row in scan["rows"] if analytics.match_row(row, **filters)]
        start = max(0, (page - 1) * size)
        return {
            "ok": True, "connected": not scan["error"], "error": scan["error"],
            "items": matched[start:start + size],
            "total": len(matched),
            "page": page,
            "size": size,
            "pages": max(1, -(-len(matched) // size)),
            "source": "segment",
            "scannedAt": scan.get("stamp", ""),
            "truncated": scan["truncated"],
            "unknownSegments": len([row for row in matched if row["segment"] == "unknown"]),
            "thresholds": self._thresholds,
            "statsError": analytics.text(scan.get("statsError")),
            "statsTruncated": bool(scan.get("statsTruncated")),
        }

    async def overview(self, *, refresh: bool = False) -> dict[str, Any]:
        """Mini KPI şeridi ve segment sayaçları — tek taramadan."""
        scan = await self._scan_rows(refresh=refresh)
        if not scan["rows"]:
            return {"ok": True, "connected": False, "error": scan["error"],
                    "kpi": {}, "segments": {}, "scannedAt": "", "truncated": False}
        limits = self._thresholds
        return {
            "ok": True, "connected": not scan["error"], "error": scan["error"],
            "kpi": analytics.kpi_of(scan["rows"], today=_today(), new_days=limits["new_days"]),
            "segments": analytics.segment_counts(scan["rows"]),
            "scannedAt": scan.get("stamp", ""),
            "truncated": scan["truncated"],
            "statsError": analytics.text(scan.get("statsError")),
            "statsTruncated": bool(scan.get("statsTruncated")),
        }

    # ================================================================ künye

    async def card(self, customer_id: int) -> dict[str, Any]:
        """Müşteri künyesi: kayıt + segment + altı aylık harcama eğrisi.

        Parça parça hata: sipariş listesi patlarsa künyenin gerisi yine dolar (K7).
        """
        try:
            raw = await self._api.customer(int(customer_id))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("müşteri okunamadı", customerId=customer_id, error=str(failure))
            return {"ok": False, "connected": False, "error": self._fail(failure)}

        limits = self._thresholds
        today = _today()
        row = analytics.customer_row(raw, today=today, thresholds=limits)
        warnings: list[str] = []

        orders = await self._customer_orders(int(customer_id), warnings)
        # `honored is None` = siparişlerin kime ait olduğu doğrulanamadı; o
        # zaman hiçbir sayı hesaplanmaz. Doğrulandıysa (sunucu süzdü ya da biz
        # yerelde süzdük) sayı KESİNDİR ve segmentin "bilinmiyor" kalmasını
        # engeller. Mağazanın kendi verdiği toplam varsa ona dokunulmaz.
        if orders["honored"] is not None:
            counted = len(orders["items"])
            total = sum(int(item["total"] or 0) for item in orders["items"])
            partial = any(item["total"] is None for item in orders["items"])
            if row["orders"] is None:
                row["orders"] = counted
                row["computed"] = True
            if row["spend"] is None:
                row["spend"] = total
                row["spendPartial"] = partial
                row["computed"] = True
            elif row["spend"] != total or row["orders"] != counted:
                # CANLIDA GÖRÜLDÜ: müşteri 12 için mağaza 35.499 TL diyor,
                # siparişlerin toplamı 36.326 TL. Fark iptal/bekleyen
                # siparişlerden. Listede toplulaştırma, künyede mağazanın
                # sayısı görünüyor; farkı SÖYLEMEZSEK aynı müşteri iki ekranda
                # iki farklı rakam gösterir ve kimse hangisine güveneceğini
                # bilmez.
                warnings.append(
                    f"Mağaza {row['orders']} sipariş / {money(row['spend'])} diyor; "
                    f"sipariş listesinin toplamı {counted} sipariş / {money(total)}. "
                    "Fark genellikle iptal edilmiş ya da tamamlanmamış siparişlerdendir; "
                    "künyedeki rakam mağazanın kendi hesabıdır.")
            row["avgBasket"] = analytics.average_basket(row["spend"], row["orders"])
            if not row["lastOrderAt"] and orders["items"]:
                row["lastOrderAt"] = max(analytics.day(item["createdAt"])
                                         for item in orders["items"])
                row["recencyDays"] = analytics.days_between(row["lastOrderAt"], today)
            if not row["city"] and orders["items"]:
                newest = max(orders["items"], key=lambda item: analytics.day(item["createdAt"]))
                row["city"] = newest.get("city") or ""
            row["segment"] = analytics.segment_of(
                orders=row["orders"], spend=row["spend"], recency_days=row["recencyDays"],
                tenure_days=row["tenureDays"], thresholds=limits)

        return {
            "ok": True, "connected": True, "error": "", "warnings": warnings,
            "customer": row,
            "segmentLabel": analytics.SEGMENT_LABELS.get(row["segment"], row["segment"]),
            "spark": analytics.monthly_spend(orders["items"], today=today, months=6),
            "orders": orders["items"][:20],
            "ordersHonored": orders["honored"],
            "ordersLocalFiltered": orders["localFiltered"],
            "ordersUnverifiable": orders["unverifiable"],
            "consents": analytics.consent_view(raw),
            "thresholds": limits,
        }

    async def _customer_orders(self, customer_id: int,
                               warnings: list[str]) -> dict[str, Any]:
        """Müşterinin siparişleri. SÜZGECİN UYGULANDIĞI DOĞRULANIR.

        CANLIDA DOĞRULANDI — `GET /api/admin/orders?customer_id=1` süzgeci
        UYGULAMIYOR: 17 siparişin tamamı, altı ayrı müşterininki karışık
        geliyor. Laravel tanımadığı sorgu parametresini sessizce yok sayar.
        Bu yüzden liste HER ZAMAN satırdaki `customerId` ile yerelde süzülür;
        sunucunun süzgeci tuttuğu doğrulanamıyorsa liste boş bırakılmaz,
        YERELDE süzülür ve ekran bunu söyler.

        Satırlarda `customerId` hiç yoksa (`honored is None`) hiçbir satır
        gösterilmez: kime ait olduğu doğrulanamayan siparişi bir müşteriye
        yazmak, ekranın yapabileceği en pahalı yalandır.
        """
        try:
            payload = await self._api.orders({"customer_id": int(customer_id)}, all_pages=True)
        except Exception as failure:  # noqa: BLE001 — K7
            warnings.append(f"siparişler: {self._fail(failure)}")
            return {"items": [], "honored": None, "localFiltered": False,
                    "unverifiable": False}
        rows = [analytics.order_row(item) for item in (payload.get("items") or [])
                if isinstance(item, dict)]
        if not rows:
            return {"items": [], "honored": None, "localFiltered": False,
                    "unverifiable": False}

        honored = analytics.filter_honored(rows, "customerId", int(customer_id))
        if honored is None:
            warnings.append(
                "Sipariş satırları müşteri kimliği taşımıyor; hangi siparişin bu müşteriye "
                "ait olduğu doğrulanamadı ve liste gösterilmiyor.")
            return {"items": [], "honored": None, "localFiltered": False,
                    "unverifiable": True}
        mine = [row for row in rows if row["customerId"] == int(customer_id)]
        if honored is False:
            warnings.append(
                "Mağaza sipariş süzgecini uygulamadı; liste bu müşterinin kimliğine göre "
                "burada süzüldü.")
        return {"items": mine, "honored": honored, "localFiltered": honored is False,
                "unverifiable": False}

    async def orders(self, customer_id: int) -> dict[str, Any]:
        warnings: list[str] = []
        result = await self._customer_orders(int(customer_id), warnings)
        return {"ok": True, "connected": not warnings, "error": " · ".join(warnings),
                "items": result["items"], "honored": result["honored"],
                "localFiltered": result["localFiltered"],
                "unverifiable": result["unverifiable"]}

    async def addresses(self, customer_id: int) -> dict[str, Any]:
        try:
            payload = await self._api.customer_addresses(int(customer_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": True, "connected": False, "error": self._fail(failure), "items": []}
        return {"ok": True, "connected": True, "error": "",
                "items": [analytics.address_row(item) for item in (payload.get("items") or [])]}

    async def returns(self, customer_id: int) -> dict[str, Any]:
        """İade/değişim talepleri. BBD ucu yayında değilse ekran bunu söyler."""
        try:
            payload = await self._api.bbd_return_requests({"customer_id": int(customer_id)})
        except Exception as failure:  # noqa: BLE001 — uç henüz yayında olmayabilir
            self._log.info("iade talepleri okunamadı", customerId=customer_id,
                           error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "items": [], "available": False}
        rows = [{
            "id": analytics.as_int(item.get("id")),
            "orderId": analytics.as_int(analytics.pick(item, "order_id")),
            "kind": analytics.text(analytics.pick(item, "type", "kind")),
            "subject": analytics.text(analytics.pick(item, "subject", "title")),
            "status": analytics.text(analytics.pick(item, "status")),
            "createdAt": analytics.text(analytics.pick(item, "created_at"))[:19],
            "customerId": analytics.as_int(analytics.pick(item, "customer_id")),
        } for item in (payload.get("items") or []) if isinstance(item, dict)]
        honored = analytics.filter_honored(rows, "customerId", int(customer_id))
        if honored is False:
            return {"ok": True, "connected": True, "available": True, "items": [],
                    "error": "Mağaza müşteri süzgecini uygulamadı; başka müşterilerin "
                             "talepleri karışmasın diye liste gösterilmiyor."}
        return {"ok": True, "connected": True, "error": "", "items": rows, "available": True}

    async def notes(self, customer_id: int) -> dict[str, Any]:
        try:
            payload = await self._api.customer_notes(int(customer_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": True, "connected": False, "error": self._fail(failure), "items": []}
        return {"ok": True, "connected": True, "error": "", "items": [{
            "id": analytics.as_int(item.get("id")),
            "note": analytics.text(analytics.pick(item, "note", "comment")),
            "actor": analytics.text(analytics.pick(item, "created_by", "author")),
            "createdAt": analytics.text(analytics.pick(item, "created_at"))[:19],
        } for item in (payload.get("items") or []) if isinstance(item, dict)]}

    async def consents(self, customer_id: int) -> dict[str, Any]:
        """İzin geçmişi: mağazadaki ŞU ANKİ durum + bu ekranın yerel geçmişi
        + açık KVKK talepleri.

        Bagisto izin DEĞİŞİKLİĞİ geçmişi tutmuyor; "ne zaman onay verdi"
        sorusunun cevabı yalnız buradan başlayarak birikir. Ekran bunu söyler
        ve geçmişin bu modülün açıldığı günden itibaren dolduğunu yazar.
        """
        out: dict[str, Any] = {"ok": True, "connected": True, "error": "", "current": [],
                               "history": [], "requests": [], "requestsAvailable": True}
        try:
            raw = await self._api.customer(int(customer_id))
            out["current"] = analytics.consent_view(raw)
        except Exception as failure:  # noqa: BLE001 — K7
            out["connected"] = False
            out["error"] = self._fail(failure)

        try:
            rows = await self._store.fetch_all(
                f"SELECT kind, before_value, after_value, actor, reason, created_at "
                f"FROM {self._consent} WHERE customer_id = ? ORDER BY id DESC LIMIT 200",
                (int(customer_id),))
            out["history"] = [{
                "kind": row["kind"],
                "label": analytics.CONSENT_LABELS.get(row["kind"], row["kind"]),
                "before": row["before_value"], "after": row["after_value"],
                "actor": row["actor"], "reason": row["reason"], "createdAt": row["created_at"],
            } for row in rows]
        except Exception as failure:  # noqa: BLE001 — geçmiş okunamadı, gerisi dursun
            out["error"] = out["error"] or self._fail(failure)

        try:
            payload = await self._api.gdpr_requests({"customer_id": int(customer_id)})
            rows = [analytics.gdpr_row(item) for item in (payload.get("items") or [])
                    if isinstance(item, dict)]
            out["requests"] = [row for row in rows
                               if not row["customerId"] or row["customerId"] == int(customer_id)]
        except Exception as failure:  # noqa: BLE001 — KVKK ucu yayında olmayabilir
            out["requestsAvailable"] = False
            out["requestsError"] = self._fail(failure)
        return out

    async def audit(self, *, customer_id: int = 0, limit: int = 100) -> dict[str, Any]:
        """Bu ekrandan yapılan yazmaların YEREL izi (gerekçeleriyle)."""
        sql = (f"SELECT customer_id, review_id, action, reason, actor, result, created_at "
               f"FROM {self._audit} ")
        params: tuple[Any, ...] = ()
        if customer_id:
            sql += "WHERE customer_id = ? "
            params = (int(customer_id),)
        sql += "ORDER BY id DESC LIMIT ?"
        params = (*params, max(1, min(500, int(limit))))
        try:
            rows = await self._store.fetch_all(sql, params)
        except Exception as failure:  # noqa: BLE001 — iz okunamadı, ekran dursun
            return {"ok": True, "items": [], "error": self._fail(failure)}
        return {"ok": True, "error": "", "items": [{
            "customerId": row["customer_id"], "reviewId": row["review_id"],
            "action": row["action"], "reason": row["reason"], "actor": row["actor"],
            "result": row["result"], "createdAt": row["created_at"],
        } for row in rows]}

    async def reference(self) -> dict[str, Any]:
        """Süzgeçleri besleyen referans listeler. Geçit 15 dk önbellekli."""
        out: dict[str, Any] = {
            "ok": True, "connected": True, "error": "",
            "groups": [], "channels": [],
            "segments": analytics.segment_options(),
            "reviewStates": [{"value": key, "label": analytics.REVIEW_LABELS[key]}
                             for key in analytics.REVIEW_STATES],
        }
        try:
            payload = await self._api.customer_groups()
            out["groups"] = [{"id": analytics.as_int(item.get("id")),
                              "name": analytics.text(item.get("name")) or "Grup"}
                             for item in (payload.get("items") or []) if isinstance(item, dict)]
        except Exception as failure:  # noqa: BLE001 — K7
            out["connected"] = False
            out["error"] = self._fail(failure)

        try:
            snapshot = await self._api.snapshot()
            parts = snapshot.get("parts") or {}
            out["channels"] = [{"code": analytics.text(item.get("code")),
                                "name": analytics.text(item.get("name"))}
                               for item in (parts.get("channels") or [])
                               if isinstance(item, dict)]
            out["stale"] = bool(snapshot.get("stale"))
        except Exception as failure:  # noqa: BLE001 — K7
            out["error"] = out["error"] or self._fail(failure)
        return out

    # =============================================================== yazma

    async def save(self, customer_id: int, *, patch: dict[str, Any], reason: str, actor: str,
                   dry_run: bool = True) -> dict[str, Any]:
        """Müşteri künyesini günceller — OKU-DEĞİŞTİR-YAZ.

        Yazmadan önce kayıt TAZE okunur: aradan geçen sürede müşteri kendi
        hesabından adres/telefon değiştirmiş olabilir ve dokunmadığımız alanı
        eski değeriyle geri göndermek onun değişikliğini siler.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        clean = self._clean_patch(patch)
        if not clean:
            return {"ok": False, "error": "Değişen alan yok."}

        try:
            current = await self._api.customer(int(customer_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}

        # GÖVDE snake_case KALIR. Mağaza yanıtı camelCase veriyor ama SORGU ve
        # GÖVDE tarafında camelCase sessizce yok sayılıyor (canlıda doğrulandı:
        # `customerGroupId` süzgeci uygulanmadı, `customer_group_id` uygulandı).
        # `pick` okurken iki biçimi de dener; yazarken tek biçim vardır.
        body: dict[str, Any] = {}
        for key in ("first_name", "last_name", "email", "phone", "status",
                    "subscribed_to_news_letter", "gender", "date_of_birth"):
            value = analytics.pick(current, key)
            if value is not None:
                body[key] = value
        # Grup kaydın içinde `group: {id, code, name}` olarak gömülü geliyor;
        # düz `customer_group_id` alanı canlıda YOK. Gömülüden okunmazsa
        # güncelleme grubu sıfırlardı.
        group_id, _ = analytics.group_of(current)
        if group_id:
            body["customer_group_id"] = group_id
        body.update(clean)
        body["channel"] = self._channel

        before_news = analytics.flag(current, "newsletter")
        await self._record(customer_id=customer_id, action="update_customer", reason=reason,
                           actor=actor, result="denendi", detail={"fields": sorted(clean)})
        try:
            result = await self._api.update_customer(int(customer_id), payload=body,
                                                     reason=reason, actor=actor,
                                                     dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(customer_id=customer_id, action="update_customer", reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(customer_id=customer_id, action="update_customer", reason=reason,
                           actor=actor, result="dry_run" if dry_run else "ok",
                           detail={"fields": sorted(clean)})
        if "subscribed_to_news_letter" in clean and not dry_run:
            await self._record_consent(customer_id=customer_id, kind="newsletter",
                                       before=before_news,
                                       after=bool(clean["subscribed_to_news_letter"]),
                                       actor=actor, reason=reason)
        self._scan["at"] = 0.0        # tarama bayatladı; segment yeniden hesaplansın
        return {"ok": True, "error": "", "fields": sorted(clean),
                "dryRun": bool(result.get("dryRun", dry_run))}

    def _clean_patch(self, patch: Any) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in (patch or {}).items():
            target = FIELD_ALIASES.get(key)
            if not target:
                continue
            if target == "customer_group_id":
                out[target] = analytics.as_int(value)
            elif target == "subscribed_to_news_letter":
                out[target] = 1 if value else 0
            else:
                out[target] = analytics.text(value)
        # `status` bu uçtan YAZILMAZ: pasifleştirme ayrı izin ister (ADR 0012).
        out.pop("status", None)
        return out

    async def set_status(self, customer_ids: list[int], *, active: bool, reason: str,
                         actor: str, dry_run: bool = True) -> dict[str, Any]:
        """Aktif/Pasif — SİLME YOK, PASİFLEŞTİRME VAR (ADR 0012).

        Müşteri silinseydi siparişleri, faturaları ve iadeleri öksüz kalırdı;
        mali kayıt geçmişe dönük bozulur. Pasif müşteri giriş yapamaz ama
        kaydı ve geçmişi durur.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        ids = [int(item) for item in (customer_ids or []) if analytics.as_int(item)]
        if not ids:
            return {"ok": False, "error": "Müşteri seçilmedi."}
        if len(ids) > 500:
            return {"ok": False, "error": "Tek seferde en çok 500 müşteri."}

        action = "activate" if active else "deactivate"
        await self._record(customer_id=ids[0] if len(ids) == 1 else 0, action=action,
                           reason=reason, actor=actor, result="denendi", detail={"ids": ids})
        try:
            result = await self._api.update_customer_status(ids, active=active, reason=reason,
                                                            actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(customer_id=0, action=action, reason=reason, actor=actor,
                               result="hata", detail={"error": str(failure), "ids": ids})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(customer_id=ids[0] if len(ids) == 1 else 0, action=action,
                           reason=reason, actor=actor, result="dry_run" if dry_run else "ok",
                           detail={"ids": ids})
        self._scan["at"] = 0.0
        return {"ok": True, "error": "", "count": len(ids),
                "dryRun": bool(result.get("dryRun", dry_run)),
                "notice": "Pasif müşteri giriş yapamaz; siparişleri ve faturaları durur."}

    async def add_note(self, customer_id: int, *, note: str, reason: str, actor: str,
                       dry_run: bool = True) -> dict[str, Any]:
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        body = analytics.text(note)
        if len(body) < 3:
            return {"ok": False, "error": "Not en az 3 karakter olmalı."}
        try:
            result = await self._api.add_customer_note(int(customer_id), note=body,
                                                       reason=reason, actor=actor,
                                                       dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(customer_id=customer_id, action="add_note", reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}
        await self._record(customer_id=customer_id, action="add_note", reason=reason,
                           actor=actor, result="dry_run" if dry_run else "ok")
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run))}

    # ================================================================= KVKK

    async def gdpr_export(self, customer_id: int, *, reason: str, actor: str,
                          dry_run: bool = True) -> dict[str, Any]:
        """KVKK veri paketi — MAĞAZA ÜRETİR, biz yalnız isteriz.

        Paketi burada kurmak, müşterinin tüm kişisel verisini Kontrol
        Merkezi'nin diskinden geçirmek olurdu. Mağaza kendi paketini üretir ve
        bize yalnız indirme bağlantısını verir.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        await self._record(customer_id=customer_id, action="gdpr_export", reason=reason,
                           actor=actor, result="denendi")
        try:
            result = await self._api.gdpr_download_data(int(customer_id), reason=reason,
                                                        actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(customer_id=customer_id, action="gdpr_export", reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(customer_id=customer_id, action="gdpr_export", reason=reason,
                           actor=actor, result="dry_run" if dry_run else "ok")
        data = result.get("data") if isinstance(result.get("data"), dict) else result
        return {"ok": True, "error": "",
                "dryRun": bool(result.get("dryRun", dry_run)),
                "url": analytics.text(analytics.pick(data, "url", "download_url", "link")),
                "message": analytics.text(analytics.pick(data, "message"))
                           or "Veri paketi mağazada üretildi.",
                "notice": "Paket kişisel veri taşır: yalnız talebi yapan müşteriye, "
                          "kimliği doğrulanarak teslim edilir."}

    async def anonymize(self, customer_id: int, *, reason: str, actor: str,
                        dry_run: bool = True) -> dict[str, Any]:
        """KVKK anonimleştirme — GERİ ALINAMAZ, AYRI İZİN ister.

        Mağaza anonimleştirmeyi KVKK TALEBİ üzerinden yürütüyor; talep
        olmadan çağrılabilecek bir uç yok. "Elle" anonimleştirme (ad/e-posta
        alanlarını üzerine yazmak) yapılmaz: bu, sipariş ve fatura
        kayıtlarındaki kişisel veriyi olduğu yerde bırakır ve ekran işi
        bitirdiğini SANDIRIR — KVKK'da en tehlikeli hata budur.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        try:
            payload = await self._api.gdpr_requests({"customer_id": int(customer_id)})
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "available": False, "error": self._fail(failure)}

        rows = [analytics.gdpr_row(item) for item in (payload.get("items") or [])
                if isinstance(item, dict)]
        mine = [row for row in rows
                if not row["customerId"] or row["customerId"] == int(customer_id)]
        open_rows = [row for row in mine if row["status"] not in ("completed", "declined")]
        if not open_rows:
            return {"ok": False, "available": False,
                    "error": "Anonimleştirme mağazanın KVKK talebi üzerinden yürür ve bu "
                             "müşterinin açık talebi yok. Müşteri vitrinden talep açtığında "
                             "bu düğme çalışır."}

        request = open_rows[0]
        action = str(self._config.get("gdpr_anonymize_action") or "anonymize")
        await self._record(customer_id=customer_id, action="anonymize", reason=reason,
                           actor=actor, result="denendi", detail={"requestId": request["id"]})
        try:
            result = await self._api.process_gdpr_request(
                request["id"], payload={"action": action, "status": "completed"},
                reason=reason, actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(customer_id=customer_id, action="anonymize", reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"ok": False, "available": True, "error": self._fail(failure)}

        await self._record(customer_id=customer_id, action="anonymize", reason=reason,
                           actor=actor, result="dry_run" if dry_run else "ok",
                           detail={"requestId": request["id"]})
        await self._record_consent(customer_id=customer_id, kind="gdpr", before="aktif",
                                   after="anonimleştirildi", actor=actor, reason=reason)
        if not dry_run:
            await self._emit("store.customer.anonymized",
                             {"customerId": int(customer_id), "requestId": request["id"],
                              "actor": actor, "reason": reason})
        self._scan["at"] = 0.0
        return {"ok": True, "available": True, "error": "",
                "dryRun": bool(result.get("dryRun", dry_run)),
                "requestId": request["id"], "notice": analytics.ANONYMIZE_NOTICE}

    # =============================================================== yorum

    async def reviews(self, *, q: str = "", status: str = "", ratings: Any = None,
                      customer_id: int = 0, product_id: int = 0, unanswered: bool = False,
                      page: int = 1, size: int = 0) -> dict[str, Any]:
        """Yorum moderasyon listesi.

        AYRI YORUM MODÜLÜ YOKTUR: yorum müşterinin sesidir ve müşteri
        ekranında yönetilir. Puan süzgeci ÇOKLU olduğu için (1–5 arasından
        birkaçı) mağazanın tek değerli `rating` süzgecine sığmıyor; tek puan
        seçilirse sunucuya gider, birden çoksa sayfada süzülür ve ekran bunu
        söyler.
        """
        per_page = size or self._page_size
        wanted = analytics.rating_list(ratings)
        filters: dict[str, Any] = {}
        if status in analytics.REVIEW_STATES:
            filters["status"] = status
        if customer_id:
            filters["customer_id"] = int(customer_id)
        if product_id:
            filters["product_id"] = int(product_id)
        if len(wanted) == 1:
            filters["rating"] = wanted[0]

        try:
            payload = await self._api.reviews(filters, page=page, per_page=per_page)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("yorumlar okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure), "items": [],
                    "total": 0, "page": page, "size": per_page, "pages": 0,
                    "summary": analytics.review_summary([]), "clientFiltered": False,
                    "pending": 0}

        raw = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
        flags = await self._review_flags([analytics.as_int(item.get("id")) for item in raw])
        rows = [analytics.review_row(item, flags=flags, reply_field=self._reply_field)
                for item in raw]

        # SUNUCU SÜZGECİ TUTTU MU. Laravel tanımadığı parametreyi sessizce yok
        # sayar (sipariş ucunda `customer_id` için CANLIDA doğrulandı). Durum
        # süzgeci tutmazsa "Bekleyen" başlığı altında onaylı yorumlar görünür
        # ve moderatör kuyruğunun bittiğini sanır; satırlar elimizdeyken burada
        # süzmek ucuzdur.
        client_filtered = False
        if status in analytics.REVIEW_STATES and \
                analytics.filter_honored(rows, "status", status) is False:
            rows = [row for row in rows if row["status"] == status]
            client_filtered = True
        if product_id and analytics.filter_honored(rows, "productId", int(product_id)) is False:
            rows = [row for row in rows if row["productId"] == int(product_id)]
            client_filtered = True
        if len(wanted) > 1:
            rows = [row for row in rows if row["rating"] in wanted]
            client_filtered = True
        if unanswered:
            rows = [row for row in rows if not row["hasReply"]]
            client_filtered = True
        if q:
            needle = analytics.fold(q)
            rows = [row for row in rows if needle in analytics.fold(" ".join(
                [row["title"], row["comment"], row["productName"], row["customerName"]]))]
            client_filtered = True

        meta = payload.get("meta") or {}
        if customer_id:
            # Çekmecede müşterinin yorumları: süzgecin uygulandığı doğrulanır,
            # yoksa başkasının yorumu bu müşteriye ait görünürdü.
            honored = analytics.filter_honored(rows, "customerId", int(customer_id))
            if honored is False:
                return {"ok": True, "connected": True, "items": [], "total": 0, "page": page,
                        "size": per_page, "pages": 0, "summary": analytics.review_summary([]),
                        "clientFiltered": False, "pending": 0,
                        "error": "Mağaza müşteri süzgecini uygulamadı; başkasının yorumu "
                                 "karışmasın diye liste gösterilmiyor."}

        return {
            "ok": True, "connected": True, "error": "",
            "items": rows,
            "total": analytics.as_int(meta.get("total"), len(rows)),
            "page": analytics.as_int(meta.get("currentPage"), page),
            "size": analytics.as_int(meta.get("perPage"), per_page),
            "pages": analytics.as_int(meta.get("lastPage"), 1),
            "summary": analytics.review_summary(rows),
            "clientFiltered": client_filtered,
            "pending": await self._pending_reviews(),
        }

    async def _pending_reviews(self) -> int:
        """Bekleyen yorum sayacı — sekme rozetini besler.

        Tek satır istenir; sayı `meta.total`dan okunur. Tüm bekleyenleri
        çekmek sayaç için israftır.
        """
        try:
            payload = await self._api.reviews({"status": "pending"}, page=1, per_page=1)
        except Exception as failure:  # noqa: BLE001 — sayaç yoksa ekran yine çalışır
            self._log.info("bekleyen yorum sayısı okunamadı", error=str(failure))
            return 0
        meta = payload.get("meta") or {}
        return analytics.as_int(meta.get("total"), len(payload.get("items") or []))

    async def moderate(self, review_ids: list[int], *, action: str, reason: str, actor: str,
                       dry_run: bool = True) -> dict[str, Any]:
        """Yorum moderasyonu: onayla · reddet · spam · beklemeye al.

        SPAM MAĞAZADA YOK (analytics TUZAK 7). Bagisto'nun üç durumu var;
        "spam" mağazada REDDEDİLMİŞ yoruma karşılık gelir ve ayrıca yerel
        etikete yazılır. Böylece hem vitrin doğru olur hem de operatörün
        "bu reklam" kararı kaybolmaz.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        if action not in REVIEW_ACTIONS:
            return {"ok": False, "error": f"Bilinmeyen yorum işlemi: {action}"}
        ids = [int(item) for item in (review_ids or []) if analytics.as_int(item)]
        if not ids:
            return {"ok": False, "error": "Yorum seçilmedi."}

        status = REVIEW_ACTIONS[action]
        await self._record(review_id=ids[0] if len(ids) == 1 else 0, action=f"review_{action}",
                           reason=reason, actor=actor, result="denendi", detail={"ids": ids})
        try:
            result = await self._api.update_review_status(ids, status=status, reason=reason,
                                                          actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(review_id=0, action=f"review_{action}", reason=reason,
                               actor=actor, result="hata",
                               detail={"error": str(failure), "ids": ids})
            return {"ok": False, "error": self._fail(failure)}

        if not dry_run:
            for review_id in ids:
                await self._set_flag(review_id, spam=action == "spam", actor=actor,
                                     reason=reason)
        await self._record(review_id=ids[0] if len(ids) == 1 else 0, action=f"review_{action}",
                           reason=reason, actor=actor, result="dry_run" if dry_run else "ok",
                           detail={"ids": ids, "status": status})
        if not dry_run:
            await self._emit("store.review.moderated",
                             {"reviewIds": ids, "status": status, "spam": action == "spam",
                              "actor": actor})
        note = ""
        if action == "spam":
            note = ("Mağazada spam durumu yok: yorum REDDEDİLDİ ve burada spam olarak "
                    "etiketlendi. Vitrinde görünmez, kaydı durur.")
        return {"ok": True, "error": "", "count": len(ids), "status": status,
                "dryRun": bool(result.get("dryRun", dry_run)), "notice": note}

    async def reply(self, review_id: int, *, body: str, reason: str, actor: str,
                    dry_run: bool = True) -> dict[str, Any]:
        """Mağaza yanıtı yazar. Yanıt VİTRİNDE yayınlanır."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        message = analytics.text(body)
        if len(message) < 5:
            return {"ok": False, "error": "Yanıt en az 5 karakter olmalı."}

        payload = {self._reply_field: message}
        await self._record(review_id=review_id, action="review_reply", reason=reason,
                           actor=actor, result="denendi")
        try:
            result = await self._api.update_review(int(review_id), payload=payload,
                                                   reason=reason, actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(review_id=review_id, action="review_reply", reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"ok": False, "error":
                    f"{self._fail(failure)} — mağaza yanıt alanını kabul etmedi. Alan adı "
                    f"ayardan düzeltilebilir (review_reply_field, şu an "
                    f"`{self._reply_field}`)."}

        if not dry_run:
            await self._set_flag(review_id, reply=message, actor=actor, reason=reason)
        await self._record(review_id=review_id, action="review_reply", reason=reason,
                           actor=actor, result="dry_run" if dry_run else "ok")
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run)),
                "reply": message}

    # ============================================================== ayarlar

    def _setting_key(self, slot: str) -> str:
        name, fallback, _ = SETTING_KEYS[slot]
        return str(self._config.get(name) or fallback)

    @staticmethod
    def _config_value(values: Any, key: str) -> dict[str, Any]:
        """Ayarın MEVCUT değeri — bulunamazsa "yok" denir, sıfır uydurulmaz.

        Bulunmayan anahtara yazmak `core_config` içinde hiçbir şeyi
        etkilemeyen yeni bir satır açar ve kullanıcı ayarı değiştirdiğini
        sanır — sessiz başarısızlık.
        """
        if not isinstance(values, dict):
            return {"key": key, "found": False, "value": None}
        if key in values:
            return {"key": key, "found": True, "value": values[key]}
        tail = key.rsplit(".", 1)[-1]
        for name, value in values.items():
            if str(name).rsplit(".", 1)[-1] == tail:
                return {"key": str(name), "found": True, "value": value}
        return {"key": key, "found": False, "value": None}

    async def settings(self) -> dict[str, Any]:
        """Müşteriyle ilgili mağaza ayarları.

        `store_settings` ekranı YOKTUR: her ayar onu kullanan ekranda durur.
        Müşteri kaydı, e-posta doğrulama, varsayılan grup ve KVKK metinleri
        buranın işidir; vergi `store_tax`ta, kargo `store_shipping`de.
        """
        slug = str(self._config.get("customer_config_slug") or "customer.settings")
        gdpr_slug = str(self._config.get("gdpr_config_slug") or "general.gdpr")
        out: dict[str, Any] = {"ok": True, "connected": True, "error": "", "store": {},
                               "storeAvailable": True, "slug": slug, "gdprSlug": gdpr_slug,
                               "groups": [], "local": {"thresholds": self._thresholds}}

        try:
            payload = await self._api.configuration(slug, channel=self._channel,
                                                    locale=self._locale)
        except Exception as failure:  # noqa: BLE001 — K7
            out["storeAvailable"] = False
            out["error"] = self._fail(failure)
            return out

        values = payload.get("values") if isinstance(payload.get("values"), dict) else payload
        if not payload:
            # AYRIM ÖNEMLİ: `values` BOŞ ama VAR ise bölüm okundu, anahtar yok
            # demektir (aşağıda "bulunamadı" diye raporlanır). Payload'ın
            # KENDİSİ boşsa bölüm hiç okunamamıştır.
            # CANLIDA DOĞRULANDI: `/api/admin/configuration` TEK ELEMANLI LİSTE
            # döndürüyor (`[{slug, channel, locale, values:{…}}]`), geçidin
            # tekil-kayıt yolu ise listeyi sözlüğe çeviremeyip boş sözlük
            # veriyor. Boş sözlüğü "anahtar bulunamadı" saymak yanlış teşhistir:
            # kullanıcı anahtar adlarını düzeltmeye çalışır, sorun orada
            # değildir. Ekran bunu AÇIKÇA söyler ve ayar yazılmaz.
            out["storeAvailable"] = False
            out["error"] = (
                f"Mağaza ayar bölümü ({slug}) okunamadı: mağaza tek elemanlı LİSTE "
                "döndürüyor, store_api geçidi ise tekil kayıt bekliyor ve boş sözlük "
                "veriyor. Ayarlar bu yüzden okunamıyor ve yazılamıyor; geçidin "
                "`configuration` metodu listeyi açacak şekilde düzeltilmeli. "
                "Ekranın diğer sekmeleri çalışmaya devam eder.")
            return out
        gdpr_values = values
        if gdpr_slug != slug:
            try:
                gdpr_payload = await self._api.configuration(gdpr_slug, channel=self._channel,
                                                             locale=self._locale)
                gdpr_values = gdpr_payload.get("values") \
                    if isinstance(gdpr_payload.get("values"), dict) else gdpr_payload
            except Exception as failure:  # noqa: BLE001 — KVKK bölümü okunamadı
                out["error"] = self._fail(failure)
                gdpr_values = {}

        for slot in SETTING_KEYS:
            source = gdpr_values if slot.startswith("gdpr") else values
            entry = self._config_value(source, self._setting_key(slot))
            entry["label"] = SETTING_KEYS[slot][2]
            out["store"][slot] = entry

        try:
            groups = await self._api.customer_groups()
            # `code` ZORUNLU: varsayılan grup ayarı canlıda KODLA tutuluyor
            # (`…default_group` = `general`), kimlikle değil.
            out["groups"] = [{"id": analytics.as_int(item.get("id")),
                              "code": analytics.text(item.get("code")),
                              "name": analytics.text(item.get("name")) or "Grup"}
                             for item in (groups.get("items") or []) if isinstance(item, dict)]
        except Exception as failure:  # noqa: BLE001 — gruplar gelmezse alan metin olur
            out["error"] = out["error"] or self._fail(failure)
        return out

    async def save_settings(self, *, values: dict[str, Any], reason: str, actor: str,
                            dry_run: bool = True) -> dict[str, Any]:
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        current = await self.settings()
        if not current.get("storeAvailable"):
            return {"ok": False, "error": current.get("error") or "Mağaza ayarları okunamadı."}

        store = current.get("store") or {}
        buckets: dict[str, dict[str, Any]] = {current["slug"]: {}, current["gdprSlug"]: {}}
        skipped: list[str] = []
        changed: list[str] = []

        for slot, wanted in (values or {}).items():
            if slot not in SETTING_KEYS or wanted is None:
                continue
            entry = store.get(slot) or {}
            if not entry.get("found"):
                skipped.append(SETTING_KEYS[slot][2])
                continue
            slug = current["gdprSlug"] if slot.startswith("gdpr") else current["slug"]
            if slot in SETTING_FLAGS:
                buckets[slug][entry["key"]] = 1 if wanted else 0
            else:
                # `defaultGroup` dahil metindir: canlıda grup KODU tutuluyor
                # (`general`), kimlik yazmak ayarı bozardı.
                buckets[slug][entry["key"]] = analytics.text(wanted)
            changed.append(SETTING_KEYS[slot][2])

        for slug, body in buckets.items():
            if not body:
                continue
            try:
                await self._api.update_configuration(slug, values=body, reason=reason,
                                                     actor=actor, channel=self._channel,
                                                     locale=self._locale, dry_run=dry_run)
            except Exception as failure:  # noqa: BLE001 — K7
                await self._record(action="save_settings", reason=reason, actor=actor,
                                   result="hata", detail={"error": str(failure), "slug": slug})
                return {"ok": False, "error": self._fail(failure), "changed": [],
                        "skipped": skipped}

        await self._record(action="save_settings", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"changed": changed, "skipped": skipped})
        return {"ok": True, "error": "", "changed": changed, "skipped": skipped,
                "dryRun": dry_run}

    # ================================================================ rapor

    async def export_csv(self, *, filters: dict[str, Any] | None = None,
                         rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Tüm müşterilerin CSV'si — rapor klasörüne yazılır.

        DOSYA KİŞİSEL VERİ TAŞIR: `write_private` 0600 izinle yazar ve dosya
        masaüstündeki rapor rafında durur.
        """
        if rows is not None:
            data = rows
            truncated = False
        else:
            scan = await self._scan_rows()
            if not scan["rows"]:
                return {"ok": False, "error": scan["error"] or "Dışa aktarılacak kayıt yok."}
            data = [row for row in scan["rows"]
                    if analytics.match_row(row, **(filters or {}))]
            truncated = scan["truncated"]

        headers = ["Ad Soyad", "E-posta", "Telefon", "Grup", "Segment", "Sipariş",
                   "Toplam harcama", "Ortalama sepet", "Son sipariş", "Kayıt", "Şehir",
                   "Durum"]
        table = [[row["name"], row["email"], row["phone"], row["groupName"],
                  analytics.SEGMENT_LABELS.get(row["segment"], row["segment"]),
                  "—" if row["orders"] is None else row["orders"],
                  "—" if row["spend"] is None else money(row["spend"]),
                  "—" if row["avgBasket"] is None else money(row["avgBasket"]),
                  row["lastOrderAt"] or "—", row["createdAt"] or "—", row["city"] or "—",
                  "Aktif" if row["status"] else "Pasif"] for row in data]

        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        name = f"magaza-musteri-listesi-{stamp}.csv"
        try:
            path = write_private(self._export_dir / name, csv_bytes(headers, table))
        except (OSError, ExportError) as failure:
            return {"ok": False, "error": f"Dosya yazılamadı: {failure}"}
        return {"ok": True, "error": "", "path": str(path), "name": name,
                "rows": len(table), "truncated": truncated,
                "notice": "Dosya kişisel veri taşır; paylaşmadan önce KVKK sorumlusuna sorun."}

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
        if kind == "card":
            return await self._card_report(analytics.as_int(params.get("customerId")))
        if kind not in ("segments", "reviews"):
            return {"ok": False, "error": f"Bilinmeyen rapor: {kind}"}

        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        if kind == "segments":
            scan = await self._scan_rows()
            if not scan["rows"]:
                return {"ok": False, "error": scan["error"] or "Rapora girecek müşteri yok."}
            content = self._segment_pdf(scan["rows"], truncated=scan["truncated"])
            name = f"magaza-musteri-segment-{stamp}.pdf"
            count = len(scan["rows"])
        else:
            listed = await self.reviews(page=1, size=200)
            if not listed.get("items"):
                return {"ok": False, "error": listed.get("error") or "Rapora girecek yorum yok."}
            content = self._review_pdf(listed["items"], listed["summary"])
            name = f"magaza-yorum-ozeti-{stamp}.pdf"
            count = len(listed["items"])

        try:
            path = write_private(self._export_dir / name, content)
        except (OSError, ExportError) as failure:
            return {"ok": False, "error": str(failure)}
        self._log.info("müşteri raporu üretildi", kind=kind, rows=count, path=str(path))
        return {"ok": True, "error": "", "path": str(path), "name": name,
                "bytes": len(content), "rows": count, "truncated": False}

    async def _card_report(self, customer_id: int) -> dict[str, Any]:
        if not customer_id:
            return {"ok": False, "error": "Karne için müşteri seçilmeli."}
        card = await self.card(customer_id)
        if not card.get("ok"):
            return {"ok": False, "error": card.get("error") or "Müşteri okunamadı."}
        content = self._card_pdf(card)
        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        name = f"magaza-musteri-karnesi-{customer_id}-{stamp}.pdf"
        try:
            path = write_private(self._export_dir / name, content)
        except (OSError, ExportError) as failure:
            return {"ok": False, "error": str(failure)}
        return {"ok": True, "error": "", "path": str(path), "name": name,
                "bytes": len(content), "rows": 1, "truncated": False}

    def _segment_pdf(self, rows: list[dict[str, Any]], *, truncated: bool) -> bytes:
        limits = self._thresholds
        kpi = analytics.kpi_of(rows, today=_today(), new_days=limits["new_days"])
        counts = analytics.segment_counts(rows)
        sections: list[dict[str, Any]] = [{
            "kind": "tiles", "title": "Özet",
            "tiles": [
                ("Müşteri", number(kpi["total"])),
                (f"Yeni ({kpi['newDays']}g)", number(kpi["new"])),
                ("Tekrar eden",
                 "—" if kpi["repeatRate"] is None else percent(kpi["repeatRate"])),
                ("Ortalama YBD",
                 "—" if kpi["avgLifetime"] is None else money(kpi["avgLifetime"])),
            ],
        }, {
            "kind": "table", "title": "Segment dağılımı",
            "headers": ["Segment", "Müşteri", "Pay"],
            "align": "LRR", "widths": [3, 1, 1],
            "rows": [[analytics.SEGMENT_LABELS[key], number(counts.get(key, 0)),
                      percent(round(counts.get(key, 0) * 100 / max(1, len(rows)), 1))]
                     for key in analytics.SEGMENT_ORDER],
        }]

        top = sorted([row for row in rows if row["spend"] is not None],
                     key=lambda row: row["spend"], reverse=True)[:60]
        if top:
            sections.append({
                "kind": "table", "title": "En çok harcayan 60 müşteri",
                "headers": ["Ad Soyad", "Segment", "Sipariş", "Harcama", "Son sipariş"],
                "align": "LLRRL", "widths": [2.6, 1.4, 0.8, 1.2, 1.2],
                "rows": [[row["name"],
                          analytics.SEGMENT_LABELS.get(row["segment"], row["segment"]),
                          number(row["orders"] or 0), money(row["spend"]),
                          row["lastOrderAt"] or "—"] for row in top],
            })
        unknown = counts.get("unknown", 0)
        if unknown:
            sections.append({"kind": "note", "text":
                             f"{unknown} müşterinin sipariş toplamı okunamadı; segmenti "
                             "hesaplanmadı ve oranlara girmedi."})
        if truncated:
            sections.append({"kind": "note",
                             "text": "Tarama tavana dayandı; liste eksik olabilir."})
        return build_pdf(title="Müşteri segment raporu",
                         subtitle=f"{len(rows)} müşteri · RFM eşikleri: taze "
                                  f"{limits['recent_days']}g · uykuda {limits['sleeping_days']}g",
                         sections=sections, footer="Kontrol Merkezi · Mağaza")

    def _card_pdf(self, card: dict[str, Any]) -> bytes:
        row = card["customer"]
        sections: list[dict[str, Any]] = [{
            "kind": "tiles", "title": "Künye",
            "tiles": [
                ("Segment", card["segmentLabel"]),
                ("Sipariş", "—" if row["orders"] is None else number(row["orders"])),
                ("Toplam harcama", "—" if row["spend"] is None else money(row["spend"])),
                ("Ortalama sepet",
                 "—" if row["avgBasket"] is None else money(row["avgBasket"])),
            ],
        }, {
            "kind": "table", "title": "Son 6 ay",
            "headers": ["Ay", "Harcama"], "align": "LR", "widths": [2, 1],
            "rows": [[item["month"], money(item["total"])] for item in card["spark"]],
        }]
        if card["orders"]:
            sections.append({
                "kind": "table", "title": "Son siparişler",
                "headers": ["Sipariş", "Tarih", "Durum", "Tutar"],
                "align": "LLLR", "widths": [1.2, 1.4, 1.2, 1],
                "rows": [[item["increment"] or f"#{item['id']}", item["createdAt"][:10],
                          item["statusLabel"] or "—", money(item["total"])]
                         for item in card["orders"][:20]],
            })
        sections.append({"kind": "note", "text":
                         "Bu belge kişisel veri taşır; yalnız iş gereği paylaşılır."})
        return build_pdf(title=f"Müşteri karnesi · {row['name']}",
                         subtitle=f"{row['email'] or '—'} · kayıt {row['createdAt'] or '—'}",
                         sections=sections, footer="Kontrol Merkezi · Mağaza")

    def _review_pdf(self, rows: list[dict[str, Any]],
                    summary: dict[str, Any]) -> bytes:
        sections: list[dict[str, Any]] = [{
            "kind": "tiles", "title": "Özet",
            "tiles": [("Yorum", number(summary["total"])),
                      ("Bekleyen", number(summary["pending"])),
                      ("Onaylı", number(summary["approved"])),
                      ("Ortalama puan",
                       "—" if summary["average"] is None else str(summary["average"]))],
        }, {
            "kind": "table", "title": "Puan dağılımı",
            "headers": ["Puan", "Adet"], "align": "LR", "widths": [2, 1],
            "rows": [[item["label"], number(item["value"])] for item in summary["breakdown"]],
        }, {
            "kind": "table", "title": "Yorumlar",
            "headers": ["Puan", "Ürün", "Müşteri", "Durum", "Tarih"],
            "align": "RLLLL", "widths": [0.6, 2.4, 1.6, 1, 1.2],
            "rows": [[str(row["rating"]), row["productName"], row["customerName"],
                      row["statusLabel"], row["createdAt"][:10]]
                     for row in rows[:REPORT_ROW_CAP]],
        }]
        return build_pdf(title="Yorum özeti",
                         subtitle=f"{summary['total']} yorum · {summary['unanswered']} yanıtsız",
                         sections=sections, footer="Kontrol Merkezi · Mağaza")

    async def _render_pages(self, path: Path, *, max_pages: int = 12,
                            dpi: int = 110) -> list[str]:
        binary = shutil.which("pdftoppm")
        if not binary:
            raise PreviewError(
                "Önizleme üretilemedi: `pdftoppm` yok (poppler-utils kurulmalı). "
                "Rapor yine de kaydedildi ve yazdırılabilir.")
        with tempfile.TemporaryDirectory(prefix="km-musteri-onizleme-") as folder:
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


class CustomerCard:
    """`store.customer.card` yeteneği — başka ekranların müşteri künyesi.

    Sipariş, iade, talep ve fatura ekranları "bu müşteri kim" sorusunu bu
    nesneye sorar; hiçbiri `store.api`ye kendi müşteri isteğini atmaz ve
    hiçbiri bu modülü import etmez (K3). Yalnız OKUR: künye üzerinden yazma
    yolu YOKTUR, yazma Müşteriler ekranının kendi uçlarından geçer ve orada
    izin denetlenir (K9).
    """

    def __init__(self, service: CustomersService) -> None:
        self._service = service

    async def card(self, customer_id: int) -> dict[str, Any]:
        """Tam künye: kayıt + segment + altı aylık eğri + son siparişler."""
        return await self._service.card(int(customer_id))

    async def summary(self, customer_id: int) -> dict[str, Any]:
        """Tek satırlık özet — rozet çizmek isteyen ekranlar için."""
        full = await self._service.card(int(customer_id))
        if not full.get("ok"):
            return {"ok": False, "error": full.get("error", ""), "customerId": int(customer_id)}
        row = full["customer"]
        return {"ok": True, "error": "", "customerId": row["id"], "name": row["name"],
                "email": row["email"], "phone": row["phone"], "groupName": row["groupName"],
                "segment": row["segment"], "segmentLabel": full["segmentLabel"],
                "orders": row["orders"], "spend": row["spend"], "status": row["status"],
                "lastOrderAt": row["lastOrderAt"]}
