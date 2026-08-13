"""Raporlar — iş kuralları.

NE YAPAR. Ağaçtaki 33 raporun her birini mağaza verisinden üretir; birden çok
yaprağı TEK PDF'te (kapak + içindekiler + bölümler) toplar; kaydedilmiş ve
zamanlanmış raporları yönetir.

ÜÇ KARAR:

1. HATA YAPRAK BAZLIDIR. Veri kümeleri (sipariş, ürün, kargo…) tek tek
   toplanır ve her birinin hatası kendi adıyla saklanır. Kargo ucu düşse bile
   Satış raporları çalışır; rapor paketinde patlayan bölüm PDF'te "üretilemedi"
   notuyla yerinde durur, paketin gerisi basılır. Bu ekranda tek bir uzak
   hatanın 33 raporu birden kapatması kabul edilemez.

2. VERİ KÜMESİ BİR KEZ ÇEKİLİR. Rapor paketinde on yaprak seçildiğinde her
   yaprak kendi siparişlerini çekseydi aynı liste on kez inerdi; mağaza
   dakikada 60 istek kabul ediyor ve ilk pakette sınır dolardı.

3. KALEM AYRINTISI TAVANLIDIR. Ürün bazlı raporlar sipariş KALEMLERİNİ ister;
   Bagisto sipariş listesi kalem taşımayabiliyor ve kalem için sipariş tek tek
   okunuyor. `detail_scan_limit` bunun tavanıdır: aşılırsa rapor eksik değil,
   "şu kadar sipariş taranabildi" notuyla çıkar.

UZAK SİSTEM DÜŞERSE EKRAN AYAKTA KALIR (K7). Servis HTTP hatası fırlatmaz;
`{"ok": False, "error": …}` döner ve panel mesajı gösterir.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from km_sdk import ExportError, build_pdf, csv_bytes, number, report_dir, write_private

from . import analytics as an
from . import builders, tree

#: Rapor için taranacak en çok kayıt. Bozuk `meta` yüzünden sonsuz sayfalama
#: olmasın diye her taramanın tavanı vardır.
DEFAULT_ROW_CAP = 3_000

#: Rapor paketinde tek seferde birleştirilebilecek en çok yaprak. Daha
#: fazlası hem 60 istek/dk sınırını hem de PDF üretim süresini zorluyor.
DEFAULT_PACKAGE_LIMIT = 12

GRANULARITIES = ("day", "week", "month")
COMPARE_MODES = ("", "previous", "year")
PERIODS = ("weekly", "monthly")


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def _loads(value: Any, fallback: Any) -> Any:
    """Saklanan JSON'u çözer; BOZUKSA listeyi düşürmez (K7).

    Satır elle ya da eski bir sürümle bozulmuş olabilir; tek bozuk kayıt için
    "Kaydedilmiş raporlar" sekmesinin tamamının hata vermesi kabul edilemez.

    TÜR DE DOĞRULANIR, yalnız ayrıştırma değil. Ayrıştırılamayan metin
    (`"{bozuk"`) yakalanıyordu ama AYRIŞTIRILABİLİR ama YANLIŞ TÜRDEKİ değer
    (`"5"` → int, `'"abc"'` → str) geçiyordu; çağıran onu liste sanıp
    dolaşınca `TypeError` atıyor ve bu istisna `saved()` / `schedules()` /
    `runs()` içindeki liste kurgusunda — try bloğunun DIŞINDA — patlayıp
    sekmenin tamamını 500'e düşürüyordu. Tek bozuk satır sekmeyi kapatamaz.
    """
    try:
        parsed = json.loads(value) if value else None
    except (TypeError, ValueError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def _today() -> str:
    """Yerel gün. `utcnow()` KULLANILMAZ: gece yarısına yakın bir günü kaydırır."""
    return datetime.now(UTC).astimezone().date().isoformat()


class PreviewError(RuntimeError):
    """Önizleme görüntüsü üretilemedi. Rapor yine de kaydedilmiştir."""


class ReportsService:
    """Raporlar ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ."""

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

        self._saved = store.table("saved")
        self._schedules = store.table("schedules")
        self._runs = store.table("runs")

    # ------------------------------------------------------------- ayarlar

    @property
    def _channel(self) -> str:
        """Kanal KODU. Yalnız `catalog/products` bunu ister (canlıda doğrulandı)."""
        return str(self._config.get("channel") or "default")

    @property
    def _channel_id(self) -> int:
        """Kanal KİMLİĞİ. 0 = süzgeci hiç gönderme.

        TUZAK — CANLIDA DOĞRULANDI, sessiz boşaltıcı: `/api/admin/orders`
        `channel=` parametresinde kanal KODUNU değil KİMLİĞİNİ bekliyor.
        `channel=default` → 0 kayıt, `channel=1` → 18 kayıt, HATA YOK. Kanal
        kodunu her sipariş isteğine eklemek bütün sipariş tabanlı raporları
        (35 yaprağın 25'i) sessizce boş gösterirdi.

        Ürün ucu ise TERSİNİ ister: `catalog/products?channel=default` → 1421
        ürün, `channel=1` → 0. Bu yüzden tek bir "kanal" ayarı yetmiyor;
        kimlik ayrı tutulur ve varsayılanı 0'dır — bilinmeyen kimlikle süzmek
        yerine hiç süzmemek doğrudur.
        """
        return max(0, an.as_int(self._config.get("channel_id"), 0))

    def _order_filters(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Sipariş/geçmiş isteğinin süzgeçleri. Kanal YALNIZ kimlik varsa gider."""
        filters: dict[str, Any] = dict(extra or {})
        if self._channel_id:
            filters["channel"] = self._channel_id
        return filters

    @property
    def _locale(self) -> str:
        return str(self._config.get("locale") or "tr")

    @property
    def _row_cap(self) -> int:
        return max(100, min(20_000, an.as_int(self._config.get("row_cap"), DEFAULT_ROW_CAP)))

    @property
    def _detail_limit(self) -> int:
        return max(0, min(2_000, an.as_int(self._config.get("detail_scan_limit"), 300)))

    @property
    def _package_limit(self) -> int:
        return max(1, min(30, an.as_int(self._config.get("package_leaf_limit"),
                                        DEFAULT_PACKAGE_LIMIT)))

    @property
    def _export_dir(self) -> Path:
        # HER ÇAĞRIDA yeniden çözülür: ay değişince klasör kendiliğinden değişir.
        return report_dir(self._category, subcategory=self._subcategory,
                          fallback=self._fallback,
                          configured=str(self._config.get("export_path") or ""))

    @staticmethod
    def _fail(failure: Exception) -> str:
        message = str(failure).strip()
        return message or "Mağazaya ulaşılamadı."

    # ============================================================== parametre

    def params(self, raw: dict[str, Any] | None = None) -> dict[str, Any]:
        """Ekrandan gelen parametreleri tek biçime indirir ve SINIRLAR.

        Aralık verilmezse son `default_range_days` gün kullanılır. Ters
        aralık (bitiş < başlangıç) sessizce düzeltilmez, çevrilir: kullanıcı
        alanları karıştırdıysa boş rapor değil doğru rapor görmelidir.
        """
        source = raw or {}
        span = max(1, min(1_000, an.as_int(self._config.get("default_range_days"), 30)))
        default_end = _today()
        default_start = (datetime.now(UTC).astimezone().date()
                         - timedelta(days=span - 1)).isoformat()

        start = an.day_of(source.get("start")) or default_start
        end = an.day_of(source.get("end")) or default_end
        if end < start:
            start, end = end, start

        granularity = an.text(source.get("granularity")) or "day"
        if granularity not in GRANULARITIES:
            granularity = "day"
        compare = an.text(source.get("compare"))
        if compare not in COMPARE_MODES:
            compare = ""
        prev_start, prev_end = an.shift_range(start, end, compare)

        return {
            "start": start, "end": end,
            "prevStart": prev_start, "prevEnd": prev_end,
            "granularity": granularity, "compare": compare,
            "channel": an.text(source.get("channel"))[:64],
            "customerGroup": an.text(source.get("customerGroup"))[:64],
            "category": an.text(source.get("category"))[:120],
            "carrier": an.text(source.get("carrier"))[:64],
            "churnDays": max(30, min(3_650, an.as_int(self._config.get("churn_days"), 180))),
            "stockoutDays": max(1, min(365,
                                       an.as_int(self._config.get("stockout_horizon_days"), 30))),
            "deadStockDays": max(1, min(3_650,
                                        an.as_int(self._config.get("dead_stock_days"), 90))),
        }

    @staticmethod
    def _window(params: dict[str, Any]) -> tuple[str, str]:
        """Çekilecek pencere — karşılaştırma açıksa önceki dönemi de kapsar."""
        start = min(params["start"], params["prevStart"] or params["start"])
        end = max(params["end"], params["prevEnd"] or params["end"])
        return start, end

    # =============================================================== ağaç

    async def catalog(self) -> dict[str, Any]:
        """Ağaç + parametre sözlüğü + geçidin durumu."""
        state: dict[str, Any] = {}
        connected, error = True, ""
        try:
            state = self._api.state() if hasattr(self._api, "state") else {}
        except Exception as failure:  # noqa: BLE001 — ekran ayakta kalmalı (K7)
            connected, error = False, self._fail(failure)

        return {
            "ok": True, "connected": connected, "error": error,
            "branches": tree.tree(),
            "search": tree.searchable(),
            "params": [{"key": key, "label": label} for key, label in tree.PARAMS],
            "granularities": [{"value": "day", "label": "Gün"},
                              {"value": "week", "label": "Hafta"},
                              {"value": "month", "label": "Ay"}],
            "compares": [{"value": "", "label": "Karşılaştırma yok"},
                         {"value": "previous", "label": "Önceki dönem"},
                         {"value": "year", "label": "Geçen yıl aynı dönem"}],
            "packageLimit": self._package_limit,
            "defaults": self.params(),
            "gateway": state,
        }

    async def reference(self) -> dict[str, Any]:
        """Süzgeç açılırlarını besleyen referans listeler (geçit önbellekli)."""
        out: dict[str, Any] = {"ok": True, "connected": True, "error": "",
                               "channels": [], "customerGroups": [], "categories": [],
                               "carriers": [], "stale": False}
        try:
            snapshot = await self._api.snapshot()
        except Exception as failure:  # noqa: BLE001 — K7
            out["connected"] = False
            out["error"] = self._fail(failure)
            return out

        parts = snapshot.get("parts") or {}
        # Kanal AÇILIRI KODU DEĞİL ADI taşır. Süzgeç sunucuda değil burada,
        # sipariş satırının `channelName` alanına karşı uygulanıyor; koda göre
        # eşleştirmek ("default" ↔ "Benim Başarı Dünyam") raporu sessizce
        # boşaltırdı.
        out["channels"] = [an.text(an.pick(item, "name", "code", default=""))
                           for item in parts.get("channels") or []]
        out["customerGroups"] = [an.text(an.pick(item, "name", "code", default=""))
                                 for item in parts.get("customer_groups") or []]
        out["stale"] = bool(snapshot.get("stale"))

        try:
            out["categories"] = an.category_names(await self._api.category_tree())
        except Exception as failure:  # noqa: BLE001 — K7
            out["error"] = out["error"] or self._fail(failure)

        try:
            payload = await self._api.bbd_carriers()
            out["carriers"] = sorted({an.text(an.pick(item, "name", "code", default=""))
                                      for item in (payload.get("items") or [])
                                      if an.pick(item, "name", "code", default="")})
        except Exception as failure:  # noqa: BLE001 — BBD ucu henüz yayında olmayabilir
            self._log.info("taşıyıcı listesi alınamadı", error=str(failure))
        return out

    # ========================================================= veri toplama

    async def _collect(self, names: list[str],
                       params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str],
                                                        list[str]]:
        """İstenen veri kümelerini SIRAYLA toplar. Her kümenin hatası kendine ait.

        Paralel çekmek hız kovasını salkım hâlinde tüketiyor ve Bagisto
        tarafında kilit üretiyor (kantin dersi, `store_api` docstring'i).
        """
        data: dict[str, Any] = {}
        errors: dict[str, str] = {}
        notes: list[str] = []
        start, end = self._window(params)
        raw_orders: list[dict[str, Any]] = []

        wanted = list(names)
        # Kalemler siparişten türer; sipariş çekilmeden kalem olamaz.
        if "lines" in wanted and "orders" not in wanted:
            wanted.insert(0, "orders")
        # Müşteri künyesi sipariş satırını ZENGİNLEŞTİRİR; önce inmeli.
        if "customers" in wanted:
            wanted.remove("customers")
            wanted.insert(0, "customers")

        for name in wanted:
            try:
                if name == "customers":
                    data["customers"] = await self._customers()
                elif name == "orders":
                    rows, raw_orders = await self._orders(start, end)
                    data["orders"] = rows
                elif name == "lines":
                    lines, note = await self._lines(raw_orders, data.get("orders") or [])
                    data["lines"] = lines
                    if note:
                        notes.append(note)
                elif name == "history":
                    data["history"] = await self._history()
                elif name == "products":
                    data["products"] = await self._products()
                elif name == "invoices":
                    data["invoices"] = await self._simple("invoices", an.invoice_row,
                                                          start, end)
                elif name == "refunds":
                    data["refunds"] = await self._simple("refunds", an.refund_row, start, end)
                elif name == "shipments":
                    data["shipments"] = await self._shipments(start, end)
                elif name == "transactions":
                    data["transactions"] = await self._simple("transactions",
                                                              an.transaction_row, start, end)
                elif name == "audit":
                    data["audit"] = await self._paged("bbd_audit", an.audit_row, start, end)
                elif name == "backups":
                    payload = await self._api.bbd_backups()
                    data["backups"] = [an.backup_row(item)
                                       for item in (payload.get("items") or [])]
                elif name == "notifications":
                    data["notifications"] = await self._paged("bbd_notifications",
                                                              an.notification_row, start, end)
                elif name == "trial":
                    payload = await self._api.bbd_trial_members(all_pages=True)
                    data["trial"] = [an.trial_row(item)
                                     for item in (payload.get("items") or [])]
                elif name == "reconciliation":
                    data["reconciliation"] = await self._api.bbd_reconciliation(
                        {"start": params["start"], "end": params["end"]})
            except Exception as failure:  # noqa: BLE001 — küme bazlı hata (K7)
                errors[name] = self._fail(failure)
                data.setdefault(name, [])
                self._log.warning("rapor verisi alınamadı", dataset=name, error=str(failure))

        note = self._fill_groups(data)
        if note:
            notes.append(note)
        return data, errors, notes

    @staticmethod
    def _fill_groups(data: dict[str, Any]) -> str:
        """Sipariş satırındaki EKSİK müşteri grubunu müşteri künyesinden doldurur.

        Sipariş listesi grubu hiç taşımıyor (canlıda doğrulandı); grup kırılımı
        bu eşleştirme olmadan her siparişi "Grupsuz" gösterirdi. Eşleştirme
        müşteri numarası, olmazsa e-posta iledir — misafir siparişinde numara
        yoktur. Satırdaki grup DOLUYSA dokunulmaz: detaydan gelen değer daha
        yakın kaynaktır.
        """
        people = data.get("customers")
        # `None` = küme hiç istenmedi (bu raporun grup ihtiyacı yok).
        # `[]` = istendi ama boş geldi; o zaman UYARI YAZILIR — sessizce
        # "Grupsuz" göstermek, grubu olmayan müşteri varmış gibi okunurdu.
        if people is None:
            return ""
        by_id = {row["id"]: row for row in people if row["id"]}
        by_mail = {row["email"]: row for row in people if row["email"]}
        missing = 0
        touched = 0
        for name in ("orders", "history"):
            for row in data.get(name) or []:
                if row.get("customerGroup"):
                    continue
                touched += 1
                found = by_id.get(row.get("customerId")) or by_mail.get(row.get("customerEmail"))
                if found and found["group"]:
                    row["customerGroup"] = found["group"]
                    if found["name"] and not row.get("customerName"):
                        row["customerName"] = found["name"]
                else:
                    missing += 1
        if missing and touched:
            return (f"{missing} siparişin müşteri grubu çözülemedi (müşteri kaydı "
                    "bulunamadı ya da grubu boş); bu satırlar “Grupsuz” sayıldı.")
        return ""

    async def _orders(self, start: str,
                      end: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        filters = self._order_filters({"date_from": start, "date_to": end})
        payload = await self._api.orders(filters, all_pages=True)
        raw = (payload.get("items") or [])[:self._row_cap]
        # Süzgeç UYGULANDIĞI VARSAYILMAZ: Laravel tanımadığı sorgu
        # parametresini sessizce yok sayar ve o zaman tüm geçmiş gelirdi.
        # Aralık burada bir kez daha uygulanır.
        pairs = [(item, an.order_row(item)) for item in raw]
        # Eşleştirme KİMLİKLE değil sırayla korunur: kimliği okunamayan kayıt
        # (0) birden fazla olduğunda kimlik kümesi hepsini birbirine karıştırırdı.
        kept = [(item, row) for item, row in pairs
                if row["day"] and start <= row["day"] <= end]
        return [row for _, row in kept], [item for item, _ in kept]

    async def _lines(self, raw_orders: list[dict[str, Any]],
                     orders: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
        """Sipariş kalemleri. Listede kalem yoksa sipariş TEK TEK okunur (tavanlı)."""
        index = {row["id"]: row for row in orders}
        lines: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []
        for item in raw_orders:
            row = index.get(an.as_int(an.pick(item, "id", default=0)))
            if row is None:
                continue
            found = an.line_rows(item, row)
            if found:
                lines.extend(found)
            else:
                pending.append(row)

        if not pending:
            return lines, ""
        limit = self._detail_limit
        if limit <= 0:
            return lines, ("Sipariş kalemleri liste ucundan gelmiyor ve tek tek okuma "
                           "kapalı (detail_scan_limit = 0); ürün bazlı sayılar eksiktir.")
        scanned = 0
        for row in pending[:limit]:
            try:
                detail = await self._api.order(row["id"])
            except Exception as failure:  # noqa: BLE001 — biri patlarsa gerisi sürsün
                self._log.info("sipariş ayrıntısı okunamadı", orderId=row["id"],
                               error=str(failure))
                continue
            lines.extend(an.line_rows(detail, row))
            scanned += 1
        if len(pending) > limit:
            return lines, (f"{len(pending)} siparişin kalemi liste ucunda yoktu; ilk "
                           f"{scanned} tanesi tek tek okundu. Ürün bazlı rakamlar bu "
                           "siparişleri kapsar.")
        return lines, ""

    async def _history(self) -> list[dict[str, Any]]:
        """Tüm sipariş geçmişi — "yeni müşteri" ve "kayıp müşteri" bunu ister."""
        payload = await self._api.orders(self._order_filters(), all_pages=True)
        return [an.order_row(item) for item in (payload.get("items") or [])[:self._row_cap]]

    async def _customers(self) -> list[dict[str, Any]]:
        """Müşteri künyeleri — sipariş satırındaki EKSİK grubu doldurur."""
        payload = await self._api.customers(all_pages=True)
        return [an.customer_row(item) for item in (payload.get("items") or [])[:self._row_cap]]

    async def _products(self) -> list[dict[str, Any]]:
        payload = await self._api.products({"channel": self._channel, "locale": self._locale},
                                           all_pages=True)
        return [an.product_row(item) for item in (payload.get("items") or [])[:self._row_cap]]

    async def _simple(self, method: str, convert: Any, start: str,
                      end: str) -> list[dict[str, Any]]:
        payload = await getattr(self._api, method)({"date_from": start, "date_to": end},
                                                   all_pages=True)
        rows = [convert(item) for item in (payload.get("items") or [])[:self._row_cap]]
        return an.within(rows, start, end)

    async def _paged(self, method: str, convert: Any, start: str,
                     end: str) -> list[dict[str, Any]]:
        """Sayfa parametresi `all_pages` desteklemeyen uçlar için tek sayfa."""
        payload = await getattr(self._api, method)({"date_from": start, "date_to": end},
                                                   per_page=50)
        rows = [convert(item) for item in (payload.get("items") or [])[:self._row_cap]]
        return an.within(rows, start, end)

    async def _shipments(self, start: str, end: str) -> list[dict[str, Any]]:
        payload = await self._api.bbd_shipments({"date_from": start, "date_to": end},
                                                all_pages=True)
        rows = [an.shipment_row(item) for item in (payload.get("items") or [])[:self._row_cap]]
        return an.within(rows, start, end)

    # ================================================================ üretim

    async def run(self, key: str, raw_params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Tek yaprak — ekranda gösterilecek sonuç."""
        params = self.params(raw_params)
        leaf = tree.leaf(key)
        if leaf is None:
            return {"ok": False, "error": f"Böyle bir rapor yok: {key}"}
        if not leaf["available"]:
            return {"ok": False, "error": leaf["reason"], "pending": True,
                    "leaf": key, "label": tree.full_label(key)}

        data, errors, notes = await self._collect(list(leaf["needs"]), params)
        missing = [name for name in leaf["needs"] if name in errors]
        if missing:
            return {"ok": False, "leaf": key, "label": tree.full_label(key),
                    "error": "; ".join(f"{name}: {errors[name]}" for name in missing),
                    "connected": False}

        result = builders.build(key, data, params)
        return {"ok": True, "error": "", "connected": True,
                "leaf": key, "label": tree.full_label(key), "hint": leaf["hint"],
                "params": params, "notes": [*notes, *result["notes"]],
                "kpis": result["kpis"], "chart": result["chart"], "table": result["table"]}

    async def _leaf_sections(self, keys: list[str], params: dict[str, Any],
                             data: dict[str, Any], errors: dict[str, str],
                             notes: list[str]) -> list[dict[str, Any]]:
        """Yaprakları PDF bölümlerine çevirir. PATLAYAN YAPRAK YERİNDE DURUR."""
        sections: list[dict[str, Any]] = []
        for index, key in enumerate(keys):
            leaf = tree.leaf(key)
            if index:
                sections.append({"kind": "break"})
            if leaf is None:
                sections.append({"kind": "note", "text": f"Bilinmeyen rapor: {key}"})
                continue
            title = tree.full_label(key)
            if not leaf["available"]:
                sections.append({"kind": "note",
                                 "text": f"{title} — üretilemedi. {leaf['reason']}"})
                continue
            missing = [name for name in leaf["needs"] if name in errors]
            if missing:
                detail = "; ".join(f"{name}: {errors[name]}" for name in missing)
                sections.append({"kind": "note",
                                 "text": f"{title} — üretilemedi. Veri alınamadı: {detail}"})
                continue
            try:
                result = builders.build(key, data, params)
            except Exception as failure:  # noqa: BLE001 — bir bölüm paketi düşürmez
                self._log.warning("rapor bölümü üretilemedi", leaf=key, error=str(failure))
                sections.append({"kind": "note",
                                 "text": f"{title} — hesaplanamadı: {self._fail(failure)}"})
                continue
            sections.extend(builders.pdf_sections(result, title=title))
        for note in notes:
            sections.append({"kind": "note", "text": note})
        return sections

    async def build_report(self, kind: str, raw_params: dict[str, Any] | None = None, *,
                           actor: str = "") -> dict[str, Any]:
        """PDF üretir. `kind` bir yaprak anahtarı ya da `package` olabilir.

        `actor` üretim izine yazılır: "Geçmiş" sekmesindeki satırın kimin
        istediğini (ya da zamanlayıcının ürettiğini) söyleyen tek kaynak budur.
        """
        params = self.params(raw_params)
        source = raw_params or {}
        if kind == "package":
            keys = [an.text(item) for item in (source.get("leaves") or []) if an.text(item)]
            keys = [key for key in keys if tree.leaf(key)]
            if not keys:
                return {"ok": False, "error": "Pakete girecek rapor seçilmedi."}
            if len(keys) > self._package_limit:
                return {"ok": False,
                        "error": f"Tek pakette en çok {self._package_limit} rapor "
                                 "birleştirilir; seçimi daraltın."}
            title = an.text(source.get("title")) or "Mağaza rapor paketi"
        else:
            if tree.leaf(kind) is None:
                return {"ok": False, "error": f"Böyle bir rapor yok: {kind}"}
            keys = [kind]
            title = tree.full_label(kind)

        data, errors, notes = await self._collect(tree.datasets_for(keys), params)
        sections: list[dict[str, Any]] = []
        if len(keys) > 1:
            sections.extend(self._cover(title, keys, params))
        sections.extend(await self._leaf_sections(keys, params, data, errors, notes))

        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        slug = "paket" if len(keys) > 1 else keys[0].replace("_", "-")
        name = f"magaza-rapor-{slug}-{stamp}.pdf"
        subtitle = f"{params['start']} — {params['end']}"
        if params["compare"]:
            subtitle += f" · karşılaştırma: {params['prevStart']} — {params['prevEnd']}"
        try:
            content = build_pdf(title=title, subtitle=subtitle, sections=sections,
                                footer="Kontrol Merkezi · Mağaza")
            path = write_private(self._export_dir / name, content)
        except (OSError, ExportError) as failure:
            await self._log_run(kind, keys, ok=False, error=str(failure), actor=actor)
            return {"ok": False, "error": str(failure)}

        await self._log_run(kind, keys, ok=True, path=str(path), name=name, actor=actor)
        self._log.info("rapor üretildi", kind=kind, leaves=len(keys), path=str(path))
        return {"ok": True, "error": "", "path": str(path), "name": name,
                "bytes": len(content), "leaves": keys,
                "failed": [key for key in keys
                           if any(item in errors for item in (tree.leaf(key) or {})
                                  .get("needs", ()))]}

    def _cover(self, title: str, keys: list[str],
               params: dict[str, Any]) -> list[dict[str, Any]]:
        """Kapak + içindekiler. Paket bir belgedir; ne içerdiği başta yazar."""
        return [
            {"kind": "tiles", "title": "Kapak",
             "tiles": [("Dönem", f"{params['start']} — {params['end']}"),
                       ("Kırılım", {"day": "Gün", "week": "Hafta",
                                    "month": "Ay"}[params["granularity"]]),
                       ("Rapor", number(len(keys))),
                       ("Hazırlayan", "Kontrol Merkezi")]},
            {"kind": "table", "title": "İçindekiler",
             "headers": ["#", "Rapor", "Dal"], "align": "RLL", "widths": [0.4, 3, 1.4],
             "rows": [[str(no + 1), tree.label_of(key),
                       tree.branch_label((tree.leaf(key) or {}).get("branch", ""))]
                      for no, key in enumerate(keys)]},
            {"kind": "note", "text": f"Paket başlığı: {title}. Her rapor kendi "
                                     "sayfasında başlar; üretilemeyen rapor yerinde "
                                     "not olarak durur."},
            {"kind": "break"},
        ]

    async def preview(self, kind: str, raw_params: dict[str, Any] | None = None, *,
                      actor: str = "") -> dict[str, Any]:
        """Raporu üretir ve sayfalarını görüntü olarak döner (kit `reportChain`)."""
        produced = await self.build_report(kind, raw_params, actor=actor)
        if not produced.get("ok"):
            return produced
        try:
            pages = await self._render_pages(Path(produced["path"]))
        except PreviewError as failure:
            return {**produced, "pages": [], "previewError": str(failure)}
        return {**produced, "pages": pages, "previewError": ""}

    async def export_csv(self, key: str, raw_params: dict[str, Any] | None = None, *,
                         actor: str = "") -> dict[str, Any]:
        """Tek yaprağın tablosunu CSV olarak rapor klasörüne yazar."""
        result = await self.run(key, raw_params)
        if not result.get("ok"):
            return result
        table = result["table"]
        if not table["headers"]:
            return {"ok": False, "error": "Bu raporun CSV'ye dökülecek tablosu yok."}

        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        name = f"magaza-rapor-{key.replace('_', '-')}-{stamp}.csv"
        try:
            path = write_private(self._export_dir / name,
                                 csv_bytes(table["headers"], table["rows"]))
        except OSError as failure:
            return {"ok": False, "error": f"Dosya yazılamadı: {failure}"}
        await self._log_run("csv", [key], ok=True, path=str(path), name=name,
                            rows=len(table["rows"]), actor=actor)
        return {"ok": True, "error": "", "path": str(path), "name": name,
                "rows": len(table["rows"]), "truncated": table.get("truncated", False)}

    # ====================================================== kayıt / zamanlama

    async def saved(self) -> dict[str, Any]:
        try:
            rows = await self._store.fetch_all(
                f"SELECT id, name, leaves, params, actor, created_at FROM {self._saved} "
                "WHERE active = 1 ORDER BY id DESC LIMIT 200")
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": True, "items": [], "error": self._fail(failure)}
        return {"ok": True, "error": "", "items": [self._saved_row(row) for row in rows]}

    @staticmethod
    def _saved_row(row: Any) -> dict[str, Any]:
        leaves = _loads(row["leaves"], [])
        return {"id": row["id"], "name": row["name"], "leaves": leaves,
                "labels": [tree.full_label(key) for key in leaves],
                "params": _loads(row["params"], {}),
                "actor": row["actor"], "createdAt": row["created_at"]}

    async def save(self, *, name: str, leaves: list[str], params: dict[str, Any],
                   actor: str) -> dict[str, Any]:
        clean = [key for key in (leaves or []) if tree.leaf(key)]
        if not clean:
            return {"ok": False, "error": "Kaydedilecek rapor seçilmedi."}
        label = an.text(name)[:80]
        if len(label) < 3:
            return {"ok": False, "error": "Rapor adı en az 3 karakter olmalı."}
        try:
            await self._store.execute(
                f"INSERT INTO {self._saved} "
                "(name, leaves, params, actor, created_at, active) VALUES (?, ?, ?, ?, ?, 1)",
                (label, json.dumps(clean, ensure_ascii=False),
                 json.dumps(self.params(params), ensure_ascii=False), actor, _now()),
            )
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        return {"ok": True, "error": "", "name": label, "leaves": clean}

    async def archive_saved(self, saved_id: int, *, reason: str,
                            actor: str) -> dict[str, Any]:
        """Kaydı ARŞİVLER. Silme yoktur (ADR 0012 · BBD veri silme yasağı)."""
        if len(an.text(reason)) < 10:
            return {"ok": False, "error": "Gerekçe en az 10 karakter olmalı."}
        try:
            await self._store.execute(
                f"UPDATE {self._saved} SET active = 0, archived_reason = ?, "
                "archived_by = ?, archived_at = ? WHERE id = ?",
                (an.text(reason)[:255], actor, _now(), int(saved_id)))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        return {"ok": True, "error": "", "id": int(saved_id)}

    async def schedules(self) -> dict[str, Any]:
        try:
            rows = await self._store.fetch_all(
                f"SELECT id, name, leaves, params, period, weekday, day_of_month, at_time, "
                f"active, actor, created_at, last_run_at, last_result FROM {self._schedules} "
                "ORDER BY id DESC LIMIT 200")
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": True, "items": [], "error": self._fail(failure)}
        return {"ok": True, "error": "", "items": [{
            "id": row["id"], "name": row["name"],
            "leaves": _loads(row["leaves"], []),
            "labels": [tree.full_label(key) for key in _loads(row["leaves"], [])],
            "params": _loads(row["params"], {}),
            "period": row["period"], "weekday": row["weekday"],
            "dayOfMonth": row["day_of_month"], "atTime": row["at_time"],
            "active": bool(row["active"]), "actor": row["actor"],
            "createdAt": row["created_at"], "lastRunAt": row["last_run_at"],
            "lastResult": row["last_result"],
        } for row in rows]}

    async def schedule(self, *, name: str, leaves: list[str], params: dict[str, Any],
                       period: str, weekday: int, day_of_month: int, at_time: str,
                       actor: str) -> dict[str, Any]:
        clean = [key for key in (leaves or []) if tree.leaf(key)]
        if not clean:
            return {"ok": False, "error": "Zamanlanacak rapor seçilmedi."}
        if period not in PERIODS:
            return {"ok": False, "error": "Sıklık haftalık ya da aylık olmalı."}
        label = an.text(name)[:80]
        if len(label) < 3:
            return {"ok": False, "error": "Zamanlanmış rapor adı en az 3 karakter olmalı."}
        moment = an.text(at_time) or "07:00"
        if not _valid_time(moment):
            return {"ok": False, "error": "Saat SS:DD biçiminde olmalı."}
        try:
            await self._store.execute(
                f"INSERT INTO {self._schedules} (name, leaves, params, period, weekday, "
                "day_of_month, at_time, active, actor, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (label, json.dumps(clean, ensure_ascii=False),
                 json.dumps(self.params(params), ensure_ascii=False), period,
                 max(0, min(6, int(weekday))), max(1, min(28, int(day_of_month))),
                 moment, actor, _now()),
            )
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        return {"ok": True, "error": "", "name": label,
                "note": "Zamanlanan rapor klasöre PDF yazar; e-posta göndermez."}

    async def toggle_schedule(self, schedule_id: int, *, active: bool,
                              actor: str) -> dict[str, Any]:
        try:
            await self._store.execute(
                f"UPDATE {self._schedules} SET active = ?, actor = ? WHERE id = ?",
                (1 if active else 0, actor, int(schedule_id)))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        return {"ok": True, "error": "", "id": int(schedule_id), "active": bool(active)}

    async def runs(self, *, limit: int = 50) -> dict[str, Any]:
        try:
            rows = await self._store.fetch_all(
                f"SELECT kind, leaves, actor, path, name, rows, ok, error, created_at "
                f"FROM {self._runs} ORDER BY id DESC LIMIT ?",
                (max(1, min(500, int(limit))),))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": True, "items": [], "error": self._fail(failure)}
        return {"ok": True, "error": "", "items": [self._run_row(row) for row in rows]}

    @staticmethod
    def _run_row(row: Any) -> dict[str, Any]:
        leaves = _loads(row["leaves"], [])
        return {
            "kind": row["kind"], "leaves": leaves,
            # Etiket SUNUCUDA çözülür: panel `sales_summary` gibi ham anahtarı
            # yazıyordu ve geçmiş sekmesi tek İngilizce/ASCII ada bakan
            # kullanıcı için okunaksızdı. Ağaç tek kaynaktır (tree.py).
            "labels": [tree.full_label(key) for key in leaves],
            "actor": row["actor"], "path": row["path"], "name": row["name"],
            "rows": row["rows"], "ok": bool(row["ok"]), "error": row["error"],
            "createdAt": row["created_at"],
        }

    async def _log_run(self, kind: str, leaves: list[str], *, ok: bool, path: str = "",
                       name: str = "", rows: int = 0, error: str = "",
                       actor: str = "") -> None:
        """Üretim izi. Ağ koparsa "ne üretmeye çalıştık" kaydı burada kalır."""
        try:
            await self._store.execute(
                f"INSERT INTO {self._runs} "
                "(kind, leaves, actor, path, name, rows, ok, error, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (kind, json.dumps(leaves, ensure_ascii=False), actor, path, name,
                 int(rows), 1 if ok else 0, error[:500], _now()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın
            self._log.warning("rapor izi yazılamadı", kind=kind, error=str(failure))

    async def run_due(self, *, when: datetime | None = None) -> dict[str, Any]:
        """Zamanlayıcının çağırdığı iş: vakti gelen raporları üretir.

        AYNI GÜN İKİ KEZ ÜRETİLMEZ: `last_run_at` günü bugünse atlanır. Makine
        uykudan uyandığında zamanlayıcı kaçırdığı tetikleyiciyi yeniden
        çalıştırıyor; kontrol olmasaydı aynı rapor arka arkaya basılırdı.
        """
        moment = (when or datetime.now(UTC).astimezone())
        today = moment.date().isoformat()
        try:
            rows = await self._store.fetch_all(
                f"SELECT id, name, leaves, params, period, weekday, day_of_month, at_time, "
                f"last_run_at FROM {self._schedules} WHERE active = 1")
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure), "produced": 0}

        produced, skipped, failed = 0, 0, 0
        for row in rows:
            # Satırın ŞEKLİ de bozulmuş olabilir (eksik sütun, elle yazılmış
            # değer). Vakit kararı tek bir tanımı düşürür, turu değil (K7):
            # okunamayan tanım yüzünden kalan raporların hiç üretilmemesi
            # kabul edilemez.
            try:
                due = _is_due(row, moment)
            except Exception as failure:  # noqa: BLE001 — K7
                failed += 1
                self._log.warning("zamanlama tanımı okunamadı",
                                  scheduleId=row.get("id") if hasattr(row, "get") else "",
                                  error=str(failure))
                continue
            if not due:
                skipped += 1
                continue
            if an.day_of(row["last_run_at"]) == today:
                skipped += 1
                continue
            # Bozuk JSON tek bir tanımı düşürür, turu değil (K7): kaydedilmiş
            # satır elle ya da eski bir sürümle bozulmuş olabilir.
            params = _loads(row["params"], {})
            leaves = _loads(row["leaves"], [])
            if not isinstance(params, dict) or not isinstance(leaves, list):
                failed += 1
                self._log.warning("zamanlanmış rapor tanımı okunamadı", scheduleId=row["id"])
                continue
            kind = "package" if len(leaves) > 1 else (leaves[0] if leaves else "")
            if not kind:
                skipped += 1
                continue
            # ÜRETİM İZİ KİMİN İSTEDİĞİNİ SÖYLER. Zamanlanmış rapor kimsenin
            # düğmeye basmadığı anda çıkar; iz "Zamanlanmış" demezse geçmiş
            # sekmesinde elle alınan raporla ayırt edilemezdi.
            result = await self.build_report(
                kind, {**params, "leaves": leaves, "title": row["name"]},
                actor=f"Zamanlanmış: {an.text(row['name'])[:60]}")
            produced += 1 if result.get("ok") else 0
            failed += 0 if result.get("ok") else 1
            # Damga TETİKLEME ANIdır, duvar saati değil: "aynı gün ikinci kez
            # üretme" kontrolü ikisinin ayrışabildiği durumda (geç çalışan iş,
            # elle verilen an) yanlış karar verirdi.
            try:
                await self._store.execute(
                    f"UPDATE {self._schedules} SET last_run_at = ?, last_result = ? WHERE id = ?",
                    (moment.isoformat(timespec="seconds"),
                     ("Üretildi: " + result.get("name", "")) if result.get("ok")
                     else ("Hata: " + result.get("error", ""))[:255], row["id"]))
            except Exception as failure:  # noqa: BLE001 — K7: bir tanım turu düşürmez
                # Damga yazılamadıysa iş bugün bir kez daha üretilebilir; bu,
                # kalan tanımların hiç üretilmemesinden iyidir.
                self._log.warning("zamanlama damgası yazılamadı", scheduleId=row["id"],
                                  error=str(failure))
        return {"ok": True, "error": "", "produced": produced, "skipped": skipped,
                "failed": failed}

    # ================================================== önizleme / yazdırma

    async def _render_pages(self, path: Path, *, max_pages: int = 24,
                            dpi: int = 110) -> list[str]:
        binary = shutil.which("pdftoppm")
        if not binary:
            raise PreviewError(
                "Önizleme üretilemedi: `pdftoppm` yok (poppler-utils kurulmalı). "
                "Rapor yine de kaydedildi ve yazdırılabilir.")
        with tempfile.TemporaryDirectory(prefix="km-rapor-onizleme-") as folder:
            target = Path(folder) / "sayfa"
            try:
                process = await asyncio.create_subprocess_exec(
                    binary, "-png", "-r", str(int(dpi)), "-f", "1", "-l", str(int(max_pages)),
                    str(path), str(target),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                _, err = await asyncio.wait_for(process.communicate(), timeout=90)
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
        """GÜVENLİK: yalnız BİZİM rapor klasörümüzdeki dosya basılabilir.

        Serbest yol kabul etmek, `lp` ile makinedeki herhangi bir dosyayı
        kâğıda döktürmeye açık kapı bırakırdı.
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


def _valid_time(value: str) -> bool:
    parts = an.text(value).split(":")
    if len(parts) != 2:
        return False
    hour, minute = an.as_int(parts[0], -1), an.as_int(parts[1], -1)
    return 0 <= hour < 24 and 0 <= minute < 60


def _is_due(row: Any, moment: datetime) -> bool:
    """Bu tetikleyicinin vakti geldi mi? Saat GEÇMİŞSE de çalışır.

    Zamanlayıcı dakikası dakikasına uyanmayabiliyor (uyku, yoğunluk); "tam
    07:00" şartı koşulsaydı kaçan rapor bir hafta sonra üretilirdi.

    BOZUK DAMGA TURU DÜŞÜRMEZ (K7). `"07:00".split(":")[:2]` iki parça
    varsayıyordu; iki nokta taşımayan bir değer (`"7"`, `""` — elle ya da eski
    bir sürümle yazılmış satır) çözme sırasında `ValueError` atıyor, istisna
    `run_due` döngüsünden geçip `run_scheduled`e kadar çıkıyor ve TEK bozuk
    satır o turdaki BÜTÜN zamanlanmış raporları iptal ediyordu — üstelik
    koşucunun "hata fırlatmaz" sözünü de çiğneyerek. Okunamayan saat
    varsayılana (07:00) düşer.
    """
    stamp = an.text(row["at_time"])
    if not _valid_time(stamp):          # yazarken kullanılan doğrulayıcının aynısı
        stamp = "07:00"
    at_hour, at_minute = stamp.split(":")
    current = moment.hour * 60 + moment.minute
    wanted = an.as_int(at_hour) * 60 + an.as_int(at_minute)
    if current < wanted:
        return False
    if row["period"] == "weekly":
        return moment.weekday() == an.as_int(row["weekday"])
    return moment.day == an.as_int(row["day_of_month"], 1)
