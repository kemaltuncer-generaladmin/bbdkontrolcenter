"""Fatura — iş kuralları.

VERİ MAĞAZADADIR, KARAR BURADADIR. Fatura ve gönderi `store.api` geçidinden
gelir (K4); bu modül Bagisto verisinin kopyasını tutmaz. Yerel tablolar yalnız
mağazada KARŞILIĞI OLMAYAN üç şeyi saklar: yasal fatura numarası eşlemesi,
seri tanımları ve yazma gerekçesi.

EN ÖNEMLİ GERÇEK: depoda hiçbir e-Fatura/e-Arşiv entegrasyonu yoktur ve
Bagisto'nun ürettiği PDF mali belge DEĞİLDİR. Bu modül belge kesmez; mağaza
faturasını yasal belgeyle EŞLER. `ledger.LEGAL_NOTICE` her yanıtta gider ve
ekranda kapatılamaz bir bant olarak durur.

UZAK SİSTEM DÜŞERSE EKRAN AYAKTA KALIR (K7): `connected: False` + `error`
döner. Yerel eşleme ve seri tablosu mağazadan bağımsız çalışmaya devam eder —
mali müşavir ağ yokken de numara girebilir.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from km_sdk import ExportError, build_pdf, csv_bytes, money, number, report_dir, write_private

from . import ledger

#: Tek seferde toplu fatura kesilebilecek en çok sipariş. Her sipariş İKİ
#: istektir: önce `/orders/{id}` ile "faturası var mı" denetimi, sonra POST.
#: Mağaza dakikada 60 istek kabul ediyor; 25 sipariş = 50 istek, sınırın
#: altında kalır ve yarıda kalan iş "hangisine kesildi" sorusunu doğurmaz.
#: (Denetim eklenmeden önce tavan 40'tı; ikinci isteği eklemek onu düşürdü.)
BULK_ISSUE_CAP = 25

#: Birleşik PDF'e girecek en çok fatura. Her biri ayrı bir GET'tir.
COMBINE_CAP = 60

REPORT_KINDS = ("period", "combined", "single", "delivery")


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


class PreviewError(RuntimeError):
    """Önizleme görüntüsü üretilemedi. Rapor yine de kaydedilmiştir."""


class InvoicesService:
    """Fatura ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ."""

    def __init__(self, *, api: Any, store: Any, log: Any, config: dict[str, Any],
                 printer: Any = None, publish: Any = None, category: str = "Mağaza",
                 subcategory: str = "Finans", fallback_dir: Path | None = None) -> None:
        self._api = api
        self._store = store
        self._log = log
        self._config = config or {}
        self._printer = printer
        self._publish = publish
        self._category = category
        self._subcategory = subcategory
        self._fallback = fallback_dir or Path.home() / "km-raporlar"

        self._legal = store.table("legal")
        self._series = store.table("series")
        self._audit = store.table("audit")

    # ------------------------------------------------------------- ayarlar

    @property
    def _page_size(self) -> int:
        return max(10, min(200, ledger.as_int(self._config.get("page_size"), 50)))

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

    def _stamp(self) -> str:
        return datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")

    # ------------------------------------------------------ yerel tablolar

    async def _legal_map(self, invoice_ids: list[int]) -> dict[int, dict[str, Any]]:
        """Seçili faturaların yasal numara eşlemeleri.

        Tek tek sorgu atmak 50 satırlık sayfada 50 sorgu eder; `IN (...)`
        parametreleri elle üretilir çünkü sürücü dizi bağlamayı desteklemiyor.
        """
        ids = [int(item) for item in invoice_ids if ledger.as_int(item)]
        if not ids:
            return {}
        holes = ",".join("?" for _ in ids)
        try:
            rows = await self._store.fetch_all(
                f"SELECT invoice_id, series, number, legal_no, issued_at, note "
                f"FROM {self._legal} WHERE invoice_id IN ({holes})", tuple(ids))
        except Exception as failure:  # noqa: BLE001 — eşleme okunamadı, liste yine gelsin
            self._log.warning("yasal numara eşlemesi okunamadı", error=str(failure))
            return {}
        return {ledger.as_int(row["invoice_id"]): dict(row) for row in rows}

    async def _all_legal(self) -> list[dict[str, Any]]:
        try:
            rows = await self._store.fetch_all(
                f"SELECT invoice_id, series, number, legal_no, issued_at FROM {self._legal}", ())
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("yasal numara listesi okunamadı", error=str(failure))
            return []
        return [dict(row) for row in rows]

    async def _series_rows(self) -> list[dict[str, Any]]:
        try:
            rows = await self._store.fetch_all(
                f"SELECT code, label, start_no, pad, year_reset, is_default, note "
                f"FROM {self._series} ORDER BY code", ())
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("seri listesi okunamadı", error=str(failure))
            return []
        return [dict(row) for row in rows]

    async def _record(self, *, invoice_id: int = 0, order_id: int = 0, action: str,
                      reason: str, actor: str, result: str, detail: Any = None) -> None:
        """Yerel denetim izi. Gerekçe yalnız burada durur."""
        try:
            await self._store.execute(
                f"INSERT INTO {self._audit} "
                "(invoice_id, order_id, action, reason, actor, result, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (int(invoice_id or 0), int(order_id or 0), action, reason, actor, result,
                 json.dumps(detail or {}, ensure_ascii=False), _now()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın
            self._log.warning("denetim izi yazılamadı", action=action, error=str(failure))

    async def _emit(self, event: str, payload: dict[str, Any]) -> None:
        """Olay yayını iş akışını DURDURMAZ: dinleyicisi olmayabilir."""
        if self._publish is None:
            return
        try:
            await self._publish(event, payload)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("olay yayınlanamadı", event=event, error=str(failure))

    # ================================================================ liste

    def _filters(self, *, state: str = "", start: str = "", end: str = "",
                 order_id: int = 0) -> dict[str, Any]:
        """Geçide gidecek süzgeçler — YALNIZ mağazanın gerçekten uyguladıkları.

        CANLIYA KARŞI DENENDİ. `state`, `date_from`, `date_to` ve `order_id`
        sonucu değiştiriyor; `search` ve `grand_total_from/to` ise 16
        faturanın 16'sını geri getiriyor, yani SESSİZCE YOK SAYILIYOR.
        Yok sayılanı göndermek "süzdüm" sanmaya yol açardı: arama ve tutar
        aralığı bu yüzden hiç gönderilmez, sayfa üzerinde YEREL uygulanır ve
        ekran bunu söyler (`localFilters`).
        """
        filters: dict[str, Any] = {}
        if ledger.text(state):
            filters["state"] = ledger.text(state)
        if start:
            filters["date_from"] = start
        if end:
            filters["date_to"] = end
        if order_id:
            filters["order_id"] = int(order_id)
        return filters

    async def invoices(self, *, q: str = "", state: str = "", start: str = "", end: str = "",
                       min_total: int | None = None, max_total: int | None = None,
                       order_id: int = 0, unmatched: bool = False, page: int = 1,
                       size: int = 0) -> dict[str, Any]:
        """Sayfalanmış fatura listesi. SUNUCU TARAFI — tam liste çekilmez.

        ÜÇ SÜZGEÇ YERELDİR ve bu gizlenmez:
         · `unmatched` (yasal numarası girilmemişler) — mağaza böyle bir alan
           tanımıyor, eşleme bizim tablomuzda.
         · `q` (arama) ve tutar aralığı — mağaza bu parametreleri SESSİZCE YOK
           SAYIYOR (canlıda denendi), gönderilse süzülmüş sanılırdı.

        Yerel süzgeç yalnız GELEN SAYFAYA uygulanır; toplam ve sayfa sayısı
        mağazanın rakamıdır. Kaç satırın elendiği ve hangi süzgecin yerel
        çalıştığı yanıtta yazar — ekran bunu kullanıcıya söyler.
        """
        per_page = size or self._page_size
        filters = self._filters(state=state, start=start, end=end, order_id=order_id)
        try:
            payload = await self._api.invoices(filters, page=page, per_page=per_page)
            connected, error = True, ""
        except Exception as failure:  # noqa: BLE001 — ekran ayakta kalmalı (K7)
            self._log.warning("fatura listesi okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "items": [], "total": 0, "page": page, "size": per_page, "pages": 0,
                    "stateFilter": None, "hiddenByLocal": 0, "localFilters": [],
                    "notice": ledger.LEGAL_NOTICE}

        raw = payload.get("items") or []
        meta = payload.get("meta") or {}
        legal = await self._legal_map([ledger.as_int(ledger.pick(item, "id")) for item in raw])
        rows = [ledger.invoice_row(item, legal=legal.get(ledger.as_int(ledger.pick(item, "id"))))
                for item in raw]
        honored = ledger.filter_honored(rows, "state", state) if state else None

        before = len(rows)
        local: list[str] = []
        if ledger.text(q):
            rows = [row for row in rows if ledger.matches_query(row, q)]
            local.append("arama")
        if min_total is not None or max_total is not None:
            rows = [row for row in rows if ledger.matches_total(row, min_total, max_total)]
            local.append("tutar aralığı")
        if unmatched:
            rows = [row for row in rows if not row["legalMatched"]]
            local.append("yasal no girilmemiş")

        return {
            "ok": True, "connected": connected, "error": error,
            "items": rows,
            "total": ledger.as_int(meta.get("total"), before),
            "page": ledger.as_int(meta.get("currentPage"), page),
            "size": ledger.as_int(meta.get("perPage"), per_page),
            "pages": ledger.as_int(meta.get("lastPage"), 1),
            "stateFilter": honored,
            "hiddenByLocal": before - len(rows),
            "localFilters": local,
            "notice": ledger.LEGAL_NOTICE,
        }

    async def card(self, invoice_id: int) -> dict[str, Any]:
        """Fatura künyesi: kayıt + kalemler + yasal eşleme tek yanıtta."""
        try:
            raw = await self._api.invoice(int(invoice_id))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("fatura okunamadı", invoiceId=invoice_id, error=str(failure))
            return {"ok": False, "connected": False, "error": self._fail(failure)}

        legal = await self._legal_map([int(invoice_id)])
        row = ledger.invoice_row(raw, legal=legal.get(int(invoice_id)))
        items = ledger.invoice_items(raw)
        return {
            "ok": True, "connected": True, "error": "",
            "invoice": row,
            "items": items,
            "rates": ledger.rate_rows([row], {row["id"]: items}),
            "history": (await self.audit(invoice_id=int(invoice_id), limit=30))["items"],
            "notice": ledger.LEGAL_NOTICE,
        }

    async def by_order(self, order_id: int) -> dict[str, Any]:
        """`store.invoice.byOrder` yeteneğinin gövdesi.

        Sipariş ekranı ve iade ekranı "bu siparişin faturası var mı, numarası
        ne" diye sorar. Bagisto listesini sipariş kimliğiyle süzeriz; süzgeç
        yok sayılırsa gelen satırlar YEREL olarak elenir — yanlış siparişin
        faturasını döndürmektense hiç döndürmemek doğrudur.
        """
        wanted = int(order_id or 0)
        if wanted <= 0:
            return {"ok": False, "error": "Sipariş numarası verilmedi.", "items": []}
        try:
            payload = await self._api.invoices({"order_id": wanted}, page=1, per_page=50)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "connected": False, "error": self._fail(failure), "items": []}

        raw = payload.get("items") or []
        legal = await self._legal_map([ledger.as_int(ledger.pick(item, "id")) for item in raw])
        rows = [ledger.invoice_row(item, legal=legal.get(ledger.as_int(ledger.pick(item, "id"))))
                for item in raw]
        rows = [row for row in rows if row["orderId"] == wanted]
        return {"ok": True, "connected": True, "error": "", "orderId": wanted,
                "items": rows, "count": len(rows), "notice": ledger.LEGAL_NOTICE}

    async def shipments(self, *, q: str = "", start: str = "", end: str = "", order_id: int = 0,
                        page: int = 1, size: int = 0) -> dict[str, Any]:
        """İrsaliyeler — Bagisto gönderi kayıtları. Belge değil, sevk izidir.

        Arama burada da YERELDİR: `/api/admin/shipments` da `search`
        parametresini sessizce yok sayıyor (canlıda denendi: 8 gönderinin
        8'i geri geldi). Tarih aralığı ve `order_id` sunucuda uygulanıyor.
        """
        per_page = size or self._page_size
        filters: dict[str, Any] = {}
        if start:
            filters["date_from"] = start
        if end:
            filters["date_to"] = end
        if order_id:
            filters["order_id"] = int(order_id)
        try:
            payload = await self._api.shipments(filters, page=page, per_page=per_page)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("gönderi listesi okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "items": [], "total": 0, "page": page, "size": per_page, "pages": 0,
                    "hiddenByLocal": 0, "localFilters": [], "notice": ledger.DELIVERY_NOTICE}

        raw = payload.get("items") or []
        meta = payload.get("meta") or {}
        rows = [ledger.shipment_row(item) for item in raw]
        before = len(rows)
        local: list[str] = []
        if ledger.text(q):
            needle = ledger.text(q).lower()
            rows = [row for row in rows
                    if needle in " ".join(ledger.text(row.get(key)).lower() for key in
                                          ("orderNumber", "customer", "carrier", "trackNumber"))]
            local.append("arama")
        return {
            "ok": True, "connected": True, "error": "",
            "items": rows,
            "total": ledger.as_int(meta.get("total"), before),
            "page": ledger.as_int(meta.get("currentPage"), page),
            "size": ledger.as_int(meta.get("perPage"), per_page),
            "pages": ledger.as_int(meta.get("lastPage"), 1),
            "hiddenByLocal": before - len(rows),
            "localFilters": local,
            "notice": ledger.DELIVERY_NOTICE,
        }

    async def uninvoiced(self, *, start: str = "", end: str = "", page: int = 1,
                         size: int = 0) -> dict[str, Any]:
        """Faturası kesilmemiş siparişler — toplu fatura kesmenin girdisi.

        EN TEHLİKELİ EKRAN BURASI, çünkü çıktısı GERİ ALINAMAZ bir işe girdi
        oluyor. Mağaza "faturası yok" diye bir süzgeç tanımıyor ve — CANLIDA
        DOĞRULANDI — sipariş LİSTESİ `invoices` dizisini HİÇ TAŞIMIYOR (yalnız
        `/orders/{id}` detayında var). Satırdaki diziye bakmak, faturası
        kesilmiş 6 siparişin 6'sını da "faturasız" göstermek demekti; kullanıcı
        hepsine ikinci kez fatura keserdi.

        Bu yüzden çapraz denetim FATURA LİSTESİNDEN yapılır: sayfadaki en eski
        siparişin gününden itibaren kesilmiş faturalar taranır (fatura
        siparişinden önce kesilemez, o yüzden bitiş sınırı gerekmez) ve
        sipariş kimlikleri elenir.

        Çapraz denetim yapılamazsa liste GİZLENMEZ ama `verified: False` döner
        ve ekran "doğrulanamadı" uyarısını basar — sessizce yanlış aday
        göstermektense uyarmak doğrudur.
        """
        per_page = size or self._page_size
        # `status` GÖNDERİLMEZ: tek değer alıyor ve `pending` siparişleri
        # dışarıda bırakıyordu. Faturalanamaz durumlar aşağıda yerel elenir.
        filters: dict[str, Any] = {}
        if start:
            filters["date_from"] = start
        if end:
            filters["date_to"] = end
        try:
            payload = await self._api.orders(filters, page=page, per_page=per_page)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "items": [], "total": 0, "page": page, "size": per_page, "pages": 0,
                    "scanned": 0, "verified": False, "verifyError": ""}

        raw = [order for order in (payload.get("items") or []) if isinstance(order, dict)]
        meta = payload.get("meta") or {}
        oldest = min((ledger.day_of(ledger.pick(order, "created_at")) for order in raw
                      if ledger.day_of(ledger.pick(order, "created_at"))), default=start)
        invoiced, verify_error = await self._invoiced_order_ids(oldest)

        items = []
        for order in raw:
            order_id = ledger.as_int(ledger.pick(order, "id"))
            status = ledger.text(ledger.pick(order, "status")).lower()
            if status in ledger.UNINVOICEABLE_STATUSES:
                continue
            rows = ledger.pick(order, "invoices")
            if isinstance(rows, list) and rows:
                continue
            if order_id in invoiced:
                continue
            items.append({
                "id": order_id,
                "number": ledger.text(ledger.pick(order, "increment_id")),
                "createdAt": ledger.short_stamp(ledger.pick(order, "created_at")),
                "customer": ledger.party(order)["name"],
                "total": ledger.to_kurus(ledger.pick(order, "grand_total", "base_grand_total")),
                "status": status,
            })
        return {
            "ok": True, "connected": True, "error": "", "items": items,
            "total": ledger.as_int(meta.get("total"), len(raw)),
            "page": ledger.as_int(meta.get("currentPage"), page),
            "size": ledger.as_int(meta.get("perPage"), per_page),
            "pages": ledger.as_int(meta.get("lastPage"), 1),
            "scanned": len(raw),
            "invoicedSkipped": len(invoiced),
            "verified": not verify_error,
            "verifyError": verify_error,
        }

    async def _invoiced_order_ids(self, since: str = "") -> tuple[set[int], str]:
        """`since` gününden beri faturası kesilmiş sipariş kimlikleri.

        Boş küme + hata metni döndüğünde çağıran ADAY LİSTESİNİ DOĞRULANMAMIŞ
        sayar; sessizce "hepsi faturasız" demez.
        """
        filters: dict[str, Any] = {}
        if ledger.text(since):
            filters["date_from"] = ledger.text(since)
        try:
            payload = await self._api.invoices(filters, all_pages=True)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("faturalı siparişler taranamadı", error=str(failure))
            return set(), self._fail(failure)
        found: set[int] = set()
        for item in (payload.get("items") or []):
            order_id = ledger.as_int(
                ledger.pick(item, "order_id") or ledger.pick(ledger.pick(item, "order"), "id"))
            if order_id:
                found.add(order_id)
        return found, ""

    async def audit(self, *, invoice_id: int = 0, limit: int = 50) -> dict[str, Any]:
        """Bu ekrandan yapılan yazmaların YEREL izi (gerekçeleriyle)."""
        sql = (f"SELECT invoice_id, order_id, action, reason, actor, result, created_at "
               f"FROM {self._audit} ")
        params: tuple[Any, ...] = ()
        if invoice_id:
            sql += "WHERE invoice_id = ? "
            params = (int(invoice_id),)
        sql += "ORDER BY id DESC LIMIT ?"
        params = (*params, max(1, min(500, int(limit))))
        try:
            rows = await self._store.fetch_all(sql, params)
        except Exception as failure:  # noqa: BLE001 — iz okunamadı, ekran dursun
            return {"ok": True, "items": [], "error": self._fail(failure)}
        return {"ok": True, "error": "", "items": [
            {"invoiceId": row["invoice_id"], "orderId": row["order_id"], "action": row["action"],
             "reason": row["reason"], "actor": row["actor"], "result": row["result"],
             "createdAt": row["created_at"]}
            for row in rows
        ]}

    # ========================================================= seri & numara

    async def series(self) -> dict[str, Any]:
        """Seriler, kullanılmış numaralar ve BOŞLUK UYARILARI.

        Boşluk mali müşavir için kırmızı bayraktır: atlanan numara ya iptal
        edilmiş bir faturadır ya da kaydı hiç girilmemiştir. İkisi de sessiz
        kalmamalı.
        """
        rows = await self._series_rows()
        legal = await self._all_legal()

        used: dict[str, list[int]] = {}
        for record in legal:
            code = ledger.text(record.get("series"))
            if not code:
                continue
            used.setdefault(code, []).append(ledger.as_int(record.get("number")))

        known = {ledger.text(row["code"]) for row in rows}
        items: list[dict[str, Any]] = []
        for row in rows:
            code = ledger.text(row["code"])
            numbers = used.get(code, [])
            gaps = ledger.number_gaps(numbers)
            items.append({
                "code": code,
                "label": ledger.text(row["label"]),
                "startNo": ledger.as_int(row["start_no"], 1),
                "pad": ledger.as_int(row["pad"], ledger.DEFAULT_PAD),
                "yearReset": bool(ledger.as_int(row["year_reset"])),
                "isDefault": bool(ledger.as_int(row["is_default"])),
                "note": ledger.text(row["note"]),
                "usedCount": len(numbers),
                "lastNumber": max(numbers) if numbers else 0,
                "nextNumber": ledger.next_number(numbers, row["start_no"]),
                "gaps": gaps,
                "gapMessage": ledger.gap_message(code, gaps),
            })

        # Tanımsız seride kayıt varsa GÖRÜNÜR olmalı: numara girilirken seri
        # elle yazılabiliyor ve yazım hatası sessizce kaybolurdu.
        orphans = [{"code": code, "usedCount": len(numbers),
                    "gaps": ledger.number_gaps(numbers),
                    "gapMessage": ledger.gap_message(code, ledger.number_gaps(numbers))}
                   for code, numbers in sorted(used.items()) if code not in known]

        chosen = next((item["code"] for item in items if item["isDefault"]), "")
        return {"ok": True, "error": "", "items": items, "orphans": orphans,
                # Tablo boşken ilk kurulumda ekran çıplak kalmasın diye ayardan
                # bir kod önerilir; tanımlı seri varsa AYAR EZİLMEZ, o kazanır.
                "defaultSeries": chosen or ledger.text(self._config.get("default_series")),
                "matchedCount": len([item for item in legal if ledger.text(item["legal_no"])]),
                "notice": ledger.LEGAL_NOTICE}

    async def save_series(self, *, code: str, label: str, start_no: int, pad: int,
                          year_reset: bool, is_default: bool, note: str, actor: str,
                          reason: str) -> dict[str, Any]:
        problem = ledger.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}
        clean = ledger.text(code).upper()
        if not clean or len(clean) > 16:
            return {"ok": False, "error": "Seri kodu 1-16 karakter olmalı."}

        try:
            await self._store.execute(
                f"INSERT INTO {self._series} "
                "(code, label, start_no, pad, year_reset, is_default, note, actor, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(code) DO UPDATE SET label = excluded.label, "
                "start_no = excluded.start_no, pad = excluded.pad, "
                "year_reset = excluded.year_reset, is_default = excluded.is_default, "
                "note = excluded.note, actor = excluded.actor, updated_at = excluded.updated_at",
                (clean, ledger.text(label), max(1, ledger.as_int(start_no, 1)),
                 max(1, min(16, ledger.as_int(pad, ledger.DEFAULT_PAD))),
                 1 if year_reset else 0, 1 if is_default else 0, ledger.text(note),
                 actor, _now()),
            )
            if is_default:
                # Varsayılan TEK olmalı; iki varsayılan, hangisinin önerileceğini
                # rastgele kılar.
                await self._store.execute(
                    f"UPDATE {self._series} SET is_default = 0 WHERE code <> ?", (clean,))
        except Exception as failure:  # noqa: BLE001 — yerel tablo yazılamadı
            return {"ok": False, "error": f"Seri kaydedilemedi: {failure}"}

        await self._record(action="save_series", reason=reason, actor=actor, result="ok",
                           detail={"code": clean, "isDefault": bool(is_default)})
        return {"ok": True, "error": "", "code": clean}

    async def save_legal_no(self, invoice_id: int, *, series: str, number: int,
                            legal_no: str = "", issued_at: str = "", note: str = "",
                            reason: str, actor: str) -> dict[str, Any]:
        """Dış sistemde kesilen yasal faturanın numarasını Bagisto faturasına eşler.

        BU İŞLEM MAĞAZAYA YAZMAZ. Tamamen yereldir ve `dryRun` kavramı yoktur:
        yazdığı şey bizim eşleme tablomuzdur, mağazada karşılığı olan bir alan
        değildir. Yine de gerekçe istenir — denetimde "bu numarayı kim, neden
        değiştirdi" sorusunun cevabı buradadır.
        """
        problem = ledger.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}
        code = ledger.text(series).upper()
        invalid = ledger.legal_error(series=code, number=number, legal_no=legal_no)
        if invalid:
            return {"ok": False, "error": invalid}

        pad = ledger.DEFAULT_PAD
        for row in await self._series_rows():
            if ledger.text(row["code"]).upper() == code:
                pad = ledger.as_int(row["pad"], ledger.DEFAULT_PAD)
                break
        full = ledger.text(legal_no) or ledger.compose_legal_no(code, number, pad)

        clash = await self._number_owner(code, ledger.as_int(number))
        if clash and clash != int(invoice_id):
            return {"ok": False,
                    "error": f"{code} serisinde {ledger.as_int(number)} numarası "
                             f"{clash} numaralı faturaya zaten eşlenmiş."}

        try:
            await self._store.execute(
                f"INSERT INTO {self._legal} "
                "(invoice_id, series, number, legal_no, issued_at, note, actor, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(invoice_id) DO UPDATE SET series = excluded.series, "
                "number = excluded.number, legal_no = excluded.legal_no, "
                "issued_at = excluded.issued_at, note = excluded.note, "
                "actor = excluded.actor, updated_at = excluded.updated_at",
                (int(invoice_id), code, ledger.as_int(number), full,
                 ledger.text(issued_at) or ledger.today_iso(), ledger.text(note), actor, _now()),
            )
        except Exception as failure:  # noqa: BLE001 — yerel tablo yazılamadı
            return {"ok": False, "error": f"Eşleme kaydedilemedi: {failure}"}

        await self._record(invoice_id=int(invoice_id), action="save_legal_no", reason=reason,
                           actor=actor, result="ok",
                           detail={"series": code, "number": ledger.as_int(number),
                                   "legalNo": full})
        return {"ok": True, "error": "", "legalNo": full, "series": code,
                "number": ledger.as_int(number)}

    async def _number_owner(self, series: str, number: int) -> int:
        """Bu seri/numara başka bir faturaya eşlenmiş mi (0 = hayır)."""
        try:
            row = await self._store.fetch_one(
                f"SELECT invoice_id FROM {self._legal} WHERE series = ? AND number = ?",
                (series, int(number)))
        except Exception as failure:  # noqa: BLE001 — denetim yapılamadı, engel çıkarma
            self._log.warning("numara çakışması denetlenemedi", error=str(failure))
            return 0
        return ledger.as_int(row["invoice_id"]) if row else 0

    # =============================================================== yazma

    async def issue(self, order_ids: list[int], *, reason: str, actor: str,
                    dry_run: bool = True) -> dict[str, Any]:
        """Seçili siparişlere fatura keser — SIRAYLA, tavanlı.

        Toplu uç yok: her sipariş ayrı POST'tur. Biri patlarsa gerisi sürer ve
        sonuç satır satır döner; "bir şey oldu" demek yerine hangi siparişin
        neden kesilemediği yazılır. GERİ ALINAMAZ: kesilen faturayı silme ucu
        yoktur, iptali `store_refunds` tarafında iade faturasıdır.
        """
        problem = ledger.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}
        ids = [int(item) for item in (order_ids or []) if ledger.as_int(item)]
        if not ids:
            return {"ok": False, "error": "Sipariş seçilmedi."}
        if len(ids) > BULK_ISSUE_CAP:
            return {"ok": False,
                    "error": f"Tek seferde en çok {BULK_ISSUE_CAP} sipariş. Mağaza dakikada "
                             "60 istek kabul ediyor; daha büyük yığın yarıda kalır."}

        await self._record(action="issue_invoices", reason=reason, actor=actor,
                           result="denendi", detail={"orders": ids})
        results: list[dict[str, Any]] = []
        created = 0
        for order_id in ids:
            existing = await self._already_invoiced(order_id)
            if existing:
                # SON KAPI. Aday listesi bir dakika önce alınmış olabilir;
                # bu arada başka biri fatura kesmiş olabilir. Kesilen fatura
                # SİLİNEMEZ, o yüzden şüphe varsa kesmemek doğrudur.
                results.append({"orderId": order_id, "ok": False, "error": existing})
                await self._record(order_id=order_id, action="issue_invoice", reason=reason,
                                   actor=actor, result="atlandı", detail={"neden": existing})
                continue
            try:
                result = await self._api.create_invoice(order_id, items=None, reason=reason,
                                                        actor=actor, dry_run=dry_run)
            except Exception as failure:  # noqa: BLE001 — biri patlarsa gerisi sürsün
                results.append({"orderId": order_id, "ok": False, "error": self._fail(failure)})
                await self._record(order_id=order_id, action="issue_invoice", reason=reason,
                                   actor=actor, result="hata", detail={"error": str(failure)})
                continue
            created += 1
            invoice_id = ledger.as_int(
                (result.get("data") or {}).get("id") if isinstance(result.get("data"), dict)
                else result.get("id"))
            results.append({"orderId": order_id, "ok": True, "error": "",
                            "invoiceId": invoice_id,
                            "dryRun": bool(result.get("dryRun", dry_run))})
            await self._record(invoice_id=invoice_id, order_id=order_id, action="issue_invoice",
                               reason=reason, actor=actor,
                               result="dry_run" if dry_run else "ok")
            if not dry_run:
                await self._emit("store.invoice.issued",
                                 {"orderId": order_id, "invoiceId": invoice_id, "actor": actor})

        failed = len(ids) - created
        return {"ok": failed == 0, "error": "" if failed == 0 else
                f"{failed} siparişte fatura kesilemedi; ayrıntı listede.",
                "created": created, "failed": failed, "dryRun": dry_run, "results": results,
                "notice": "Kesilen fatura silinemez. İptali iade faturasıdır (İadeler ekranı)."}

    async def _already_invoiced(self, order_id: int) -> str:
        """Siparişin faturası var mı — varsa kullanıcıya gösterilecek metin.

        `/api/admin/orders/{id}` detayında `invoices` dizisi VARDIR (listede
        yoktur). Detay okunamazsa BOŞ döner: erişemediğimiz için kesmeyi
        engellemek, mağaza yavaşladığında işi tamamen durdururdu — asıl
        koruma zaten aday listesindeki çapraz denetimdir.
        """
        try:
            raw = await self._api.order(int(order_id))
        except Exception as failure:  # noqa: BLE001 — K7, denetim yapılamadı
            self._log.warning("sipariş faturası denetlenemedi", orderId=order_id,
                              error=str(failure))
            return ""
        rows = ledger.pick(raw, "invoices")
        if isinstance(rows, list) and rows:
            numbers = ", ".join(
                ledger.text(ledger.pick(item, "increment_id"))
                or f"#{ledger.as_int(ledger.pick(item, 'id'))}"
                for item in rows[:5] if isinstance(item, dict))
            return (f"Bu siparişin faturası zaten var ({numbers or len(rows)}). "
                    "İkinci fatura kesilmedi; kesilen fatura silinemez.")
        return ""

    async def send_copy(self, invoice_id: int, *, reason: str, actor: str,
                        dry_run: bool = True) -> dict[str, Any]:
        """Fatura kopyasını müşteriye e-postalar. MÜŞTERİYE GİDER — gerekçeli."""
        problem = ledger.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}
        try:
            result = await self._api.send_invoice_copy(int(invoice_id), reason=reason,
                                                       actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(invoice_id=int(invoice_id), action="send_copy", reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}
        await self._record(invoice_id=int(invoice_id), action="send_copy", reason=reason,
                           actor=actor, result="dry_run" if dry_run else "ok")
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run))}

    async def set_state(self, invoice_ids: list[int], *, state: str, reason: str, actor: str,
                        dry_run: bool = True) -> dict[str, Any]:
        """Toplu fatura durumu. SİLME YOKTUR — durum değişir, kayıt kalır."""
        problem = ledger.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}
        ids = [int(item) for item in (invoice_ids or []) if ledger.as_int(item)]
        if not ids:
            return {"ok": False, "error": "Fatura seçilmedi."}
        wanted = ledger.text(state).lower()
        if wanted not in ledger.WRITABLE_STATES:
            return {"ok": False,
                    "error": f"Bu durum yazılamaz: {state}. "
                             f"Yazılabilir olanlar: {', '.join(ledger.WRITABLE_STATES)}."}

        await self._record(action="set_state", reason=reason, actor=actor, result="denendi",
                           detail={"ids": ids, "state": wanted})
        try:
            result = await self._api.update_invoice_status(ids, status=wanted, reason=reason,
                                                           actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="set_state", reason=reason, actor=actor, result="hata",
                               detail={"error": str(failure), "ids": ids})
            return {"ok": False, "error": self._fail(failure)}
        await self._record(action="set_state", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"ids": ids, "state": wanted})
        return {"ok": True, "error": "", "count": len(ids), "state": wanted,
                "dryRun": bool(result.get("dryRun", dry_run))}

    # ================================================================ rapor

    async def _scan(self, filters: dict[str, Any]
                    ) -> tuple[list[dict[str, Any]], bool, dict[int, list[dict[str, Any]]]]:
        """Rapor için tam liste taraması — geçit sayfa sayfa çeker, tavanlı.

        Kalemler DE toplanır: KDV oranı kalemdedir ve icmalin oran kırılımı
        onsuz "ayrıştırılamadı" satırına düşer.
        """
        payload = await self._api.invoices(filters, all_pages=True)
        raw = (payload.get("items") or [])[:ledger.REPORT_ROW_CAP]
        legal = await self._legal_map([ledger.as_int(ledger.pick(item, "id")) for item in raw])
        rows = [ledger.invoice_row(item, legal=legal.get(ledger.as_int(ledger.pick(item, "id"))))
                for item in raw]
        details = {row["id"]: ledger.invoice_items(item)
                   for row, item in zip(rows, raw, strict=False)}
        truncated = bool(payload.get("truncated")) or \
            len(payload.get("items") or []) > ledger.REPORT_ROW_CAP
        return rows, truncated, details

    async def export_csv(self, *, start: str = "", end: str = "",
                         state: str = "") -> dict[str, Any]:
        """Muhasebe biçimi CSV — YASAL NUMARA sütunuyla. Asıl amaç budur:
        mali müşavir bu dosyayla iki sistemi karşılaştırır."""
        filters = self._filters(state=state, start=start, end=end)
        try:
            rows, truncated, _ = await self._scan(filters)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        if not rows:
            return {"ok": False, "error": "Bu aralıkta fatura yok."}

        headers, table = ledger.csv_table(rows, money)
        name = f"magaza-fatura-listesi-{self._stamp()}.csv"
        try:
            path = write_private(self._export_dir / name, csv_bytes(headers, table))
        except OSError as failure:
            return {"ok": False, "error": f"Dosya yazılamadı: {failure}"}
        return {"ok": True, "error": "", "path": str(path), "name": name,
                "rows": len(table), "truncated": truncated}

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
        if kind not in REPORT_KINDS:
            return {"ok": False, "error": f"Bilinmeyen rapor: {kind}"}
        if kind == "period":
            return await self._period_report(params)
        if kind == "single":
            return await self._store_pdf([ledger.as_int(params.get("invoiceId"))], single=True)
        if kind == "combined":
            ids = [ledger.as_int(item) for item in (params.get("invoiceIds") or [])]
            return await self._store_pdf(ids, single=False)
        return await self._delivery_report(params)

    # ------------------------------------------------------- dönem icmali

    async def _period_report(self, params: dict[str, Any]) -> dict[str, Any]:
        start = ledger.text(params.get("start"))
        end = ledger.text(params.get("end"))
        if not start or not end:
            return {"ok": False, "error": "Dönem icmali için başlangıç ve bitiş günü zorunlu."}

        filters = self._filters(state=ledger.text(params.get("state")), start=start, end=end)
        try:
            rows, truncated, details = await self._scan(filters)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        if not rows:
            return {"ok": False, "error": "Bu dönemde fatura yok."}

        refunds: list[dict[str, Any]] = []
        refund_error = ""
        try:
            payload = await self._api.refunds({"date_from": start, "date_to": end},
                                              all_pages=True)
            refunds = [{"total": ledger.to_kurus(ledger.pick(item, "grand_total",
                                                             "base_grand_total"))}
                       for item in (payload.get("items") or [])]
        except Exception as failure:  # noqa: BLE001 — iade okunamazsa icmal yine üretilir
            refund_error = self._fail(failure)
            self._log.warning("iade listesi okunamadı", error=refund_error)

        summary = ledger.period_summary(rows, details, refunds)
        content = self._period_pdf(summary, rows, start=start, end=end, truncated=truncated,
                                   refund_error=refund_error)
        name = f"magaza-fatura-icmali-{start}_{end}.pdf"
        try:
            path = write_private(self._export_dir / name, content)
        except (OSError, ExportError) as failure:
            return {"ok": False, "error": str(failure)}
        self._log.info("dönem icmali üretildi", rows=len(rows), path=str(path))
        return {"ok": True, "error": "", "path": str(path), "name": name,
                "bytes": len(content), "rows": len(rows), "truncated": truncated,
                "summary": summary}

    def _period_pdf(self, summary: dict[str, Any], rows: list[dict[str, Any]], *, start: str,
                    end: str, truncated: bool, refund_error: str) -> bytes:
        sections: list[dict[str, Any]] = [
            {"kind": "note", "text": ledger.LEGAL_NOTICE},
            {"kind": "tiles", "title": "Dönem özeti", "tiles": [
                ("Fatura", number(summary["count"])),
                ("Matrah", money(summary["net"])),
                ("KDV", money(summary["tax"])),
                ("Toplam", money(summary["total"])),
                ("İade", money(summary["refundTotal"])),
                ("Net", money(summary["netTotal"])),
            ]},
            {"kind": "table", "title": "Oran bazlı kırılım",
             "headers": ["Oran", "Fatura", "Matrah", "KDV", "Toplam"],
             "align": "LRRRR", "widths": [1.2, 0.9, 1.4, 1.2, 1.4],
             "rows": [[row["rateLabel"], number(row["invoices"]), money(row["net"]),
                       money(row["tax"]), money(row["total"])] for row in summary["byRate"]]},
            {"kind": "table", "title": "Gün bazlı",
             "headers": ["Gün", "Fatura", "Toplam"], "align": "LRR",
             "widths": [1.4, 0.9, 1.4],
             "rows": [[row["day"], number(row["count"]), money(row["total"])]
                      for row in summary["byDay"]]},
        ]
        if summary.get("ratesDerived"):
            sections.append({"kind": "note",
                             "text": "KDV ORANI MAĞAZADAN GELMİYOR: fatura kalemlerinde oran "
                                     "alanı yok, oran KDV tutarının matraha bölümünden "
                                     "TÜRETİLMİŞTİR. Beyan öncesi kalem bazında doğrulayın."})
        if summary["missingLegal"]:
            sections.append({"kind": "note",
                             "text": f"{summary['missingLegal']} faturanın YASAL FATURA "
                                     "NUMARASI eşlenmemiş. Bu faturaların mali belgesi "
                                     "dış sistemde bulunamaz: "
                                     + ", ".join(summary["missingNumbers"])})
        if refund_error:
            sections.append({"kind": "note",
                             "text": f"İade toplamı okunamadı ({refund_error}); "
                                     "net rakam iadeler düşülmeden hesaplandı."})
        sections.append({"kind": "break"})
        sections.append({
            "kind": "table", "title": "Fatura listesi",
            "headers": ["Fatura no", "Yasal no", "Tarih", "Müşteri", "Matrah", "KDV", "Toplam"],
            "align": "LLLLRRR", "widths": [1.1, 1.3, 1.1, 2.2, 1.1, 1, 1.1],
            "rows": [[row["number"], row["legalNo"] or "—", row["createdAt"][:10],
                      row["customer"], money(row["net"]), money(row["tax"]),
                      money(row["total"])] for row in rows[:600]],
        })
        if truncated or len(rows) > 600:
            sections.append({"kind": "note",
                             "text": "Liste ilk 600 faturayla sınırlandı; özet rakamlar "
                                     "taranan tüm faturaları kapsar."})
        return build_pdf(title="Dönem fatura icmali",
                         subtitle=f"{start} — {end} · {summary['count']} fatura",
                         sections=sections, footer="Kontrol Merkezi · Mağaza · Finans")

    # ---------------------------------------------------- mağaza PDF'leri

    async def _store_pdf(self, invoice_ids: list[int], *, single: bool) -> dict[str, Any]:
        """Bagisto fatura PDF'i; birden çoksa TEK DOSYADA birleştirilir.

        Birleştirme `pdfunite` ile yapılır (poppler-utils). Yeni bir Python
        bağımlılığı EKLENMEZ: aynı paket önizleme için gereken `pdftoppm`'i de
        veriyor, yani zaten kurulu olması gereken tek şey.

        Üretilen dosya YASAL BELGE DEĞİLDİR; adı ve içindeki uyarı bunu söyler.
        """
        ids = [item for item in invoice_ids if item > 0]
        if not ids:
            return {"ok": False, "error": "Fatura seçilmedi."}
        if len(ids) > COMBINE_CAP:
            return {"ok": False,
                    "error": f"Tek dosyada en çok {COMBINE_CAP} fatura birleştirilir; "
                             "her biri mağazadan ayrı ayrı indiriliyor."}

        binary = shutil.which("pdfunite")
        if not single and binary is None:
            return {"ok": False,
                    "error": "Birleştirme için `pdfunite` yok (poppler-utils kurulmalı). "
                             "Faturaları tek tek açabilirsiniz."}

        failures: list[str] = []
        with tempfile.TemporaryDirectory(prefix="km-fatura-") as folder:
            parts: list[Path] = []
            for invoice_id in ids:
                try:
                    content = await self._api.invoice_pdf(int(invoice_id))
                except Exception as failure:  # noqa: BLE001 — biri gelmezse gerisi sürsün
                    failures.append(f"#{invoice_id}: {self._fail(failure)}")
                    continue
                part = Path(folder) / f"fatura-{invoice_id}.pdf"
                part.write_bytes(content if isinstance(content, bytes) else bytes(content))
                parts.append(part)

            if not parts:
                return {"ok": False,
                        "error": "Hiçbir fatura PDF'i alınamadı. " + " · ".join(failures[:3])}

            if len(parts) == 1:
                content = parts[0].read_bytes()
            else:
                merged = Path(folder) / "birlesik.pdf"
                joined = await self._run_pdfunite(parts, merged)
                if joined:
                    return {"ok": False, "error": joined}
                content = merged.read_bytes()

        name = (f"magaza-fatura-{ids[0]}-{self._stamp()}.pdf" if single
                else f"magaza-fatura-birlesik-{len(parts)}-{self._stamp()}.pdf")
        try:
            path = write_private(self._export_dir / name, content)
        except OSError as failure:
            return {"ok": False, "error": f"Dosya yazılamadı: {failure}"}
        return {"ok": True, "error": "", "path": str(path), "name": name,
                "bytes": len(content), "rows": len(parts), "failures": failures,
                "notice": ledger.LEGAL_NOTICE}

    async def _run_pdfunite(self, parts: list[Path], target: Path) -> str:
        """Boş metin = başarı. Hata metni doğrudan kullanıcıya gösterilir."""
        binary = shutil.which("pdfunite")
        if binary is None:
            return "Birleştirme için `pdfunite` yok (poppler-utils kurulmalı)."
        try:
            process = await asyncio.create_subprocess_exec(
                binary, *[str(item) for item in parts], str(target),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            _, err = await asyncio.wait_for(process.communicate(), timeout=120)
        except TimeoutError:
            return "Birleştirme süre aşımına uğradı."
        except OSError as failure:
            return f"Birleştirme çalıştırılamadı: {failure}"
        if process.returncode != 0 or not target.exists():
            return f"Birleştirilemedi: {err.decode(errors='replace').strip()}"
        return ""

    # ------------------------------------------------------------ irsaliye

    async def _delivery_report(self, params: dict[str, Any]) -> dict[str, Any]:
        """Sevk dökümü. Bagisto'da irsaliye PDF ucu YOK; döküm burada üretilir
        ve TASLAK olduğu belgenin üzerinde yazar."""
        ids = [ledger.as_int(item) for item in (params.get("shipmentIds") or [])]
        ids = [item for item in ids if item > 0]
        if not ids:
            return {"ok": False, "error": "İrsaliye seçilmedi."}
        if len(ids) > COMBINE_CAP:
            return {"ok": False, "error": f"Tek dökümde en çok {COMBINE_CAP} gönderi."}

        rows: list[dict[str, Any]] = []
        failures: list[str] = []
        for shipment_id in ids:
            try:
                raw = await self._api.shipment(int(shipment_id))
            except Exception as failure:  # noqa: BLE001 — biri gelmezse gerisi sürsün
                failures.append(f"#{shipment_id}: {self._fail(failure)}")
                continue
            rows.append(ledger.shipment_row(raw))
        if not rows:
            return {"ok": False, "error": "Hiçbir gönderi okunamadı. " + " · ".join(failures[:3])}

        sections: list[dict[str, Any]] = [
            {"kind": "note", "text": ledger.DELIVERY_NOTICE},
            {"kind": "table", "title": "Sevk dökümü",
             "headers": ["Gönderi", "Sipariş", "Tarih", "Müşteri", "Taşıyıcı", "Takip no",
                         "Kalem"],
             "align": "LLLLLLR", "widths": [0.8, 1.1, 1.1, 2.2, 1.2, 1.6, 0.7],
             "rows": [[f"#{row['id']}", row["orderNumber"] or "—", row["createdAt"][:10],
                       row["customer"], row["carrier"] or "—", row["trackNumber"] or "—",
                       number(row["itemCount"] or row["totalQty"])] for row in rows]},
        ]
        if failures:
            sections.append({"kind": "note",
                             "text": "Okunamayan gönderiler: " + " · ".join(failures[:5])})
        content = build_pdf(title="Sevk dökümü (irsaliye taslağı)",
                            subtitle=f"{len(rows)} gönderi · {ledger.today_iso()}",
                            sections=sections, footer="Kontrol Merkezi · Mağaza · Finans")
        name = f"magaza-sevk-dokumu-{self._stamp()}.pdf"
        try:
            path = write_private(self._export_dir / name, content)
        except (OSError, ExportError) as failure:
            return {"ok": False, "error": str(failure)}
        return {"ok": True, "error": "", "path": str(path), "name": name,
                "bytes": len(content), "rows": len(rows), "failures": failures}

    # ------------------------------------------------------- önizleme/baskı

    async def _render_pages(self, path: Path, *, max_pages: int = 12,
                            dpi: int = 110) -> list[str]:
        binary = shutil.which("pdftoppm")
        if not binary:
            raise PreviewError(
                "Önizleme üretilemedi: `pdftoppm` yok (poppler-utils kurulmalı). "
                "Rapor yine de kaydedildi ve yazdırılabilir.")
        with tempfile.TemporaryDirectory(prefix="km-fatura-onizleme-") as folder:
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


class InvoiceByOrder:
    """`store.invoice.byOrder` yeteneğinin dar yüzü.

    Sipariş, iade ve vergi ekranları "bu siparişin faturası var mı" diye
    sorar. Servisin tamamını vermek, başka modüle yazma uçlarını da açmak
    olurdu; yetenek YALNIZ okuma yapar (K3).
    """

    def __init__(self, service: InvoicesService) -> None:
        self._service = service

    async def by_order(self, order_id: int) -> dict[str, Any]:
        return await self._service.by_order(int(order_id))
