"""İadeler — iş kuralları.

VERİ MAĞAZADADIR, KARAR BURADADIR. Kredi notu, sipariş kalemi ve POS denemesi
`store.api` geçidinden gelir (K4); bu modül kopya tutmaz. Yerel tablolar yalnız
mağazada KARŞILIĞI OLMAYAN iki şeyi saklar: yazma gerekçesi (denetim izi) ve
ONAYA SUNULAN HESABIN KENDİSİ (jeton) — uygulanan şeyin önizlenen şey olduğunu
kanıtlar.

İKİ KAYNAK. Bagisto'nun kredi notu (`refunds`) parayı, BBD'nin RMA talebi
(`bbd_return_requests`) süreci temsil eder. `/api/admin/bbd/*` uçları HÂLÂ
YAZILIYOR; yoksa geçit `bbd_endpoint_missing` döner ve o bölüm ekranda
"uç hazır olunca açılacak" diye KAPALI görünür — sessizce patlamaz (K7).

ÖLÇEK. "Yüzler" mertebesinde kayıt; süzme istemcidedir. Sunucu bir tarih
penceresini tavanla tarar ve tamamını döner.

PARA. İçeride her yerde KURUŞ (int). Telde ondalık metin gider (`from_kurus`).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from km_sdk import ExportError, build_pdf, csv_bytes, money, number, report_dir, write_private

from . import calc

#: BBD uçları henüz yayında değilken geçidin döndüğü hata kodu.
MISSING_CODE = "bbd_endpoint_missing"

MISSING_TEXT = ("Bu bölüm mağaza tarafındaki BBD ucu yayına girince açılacak. "
                "Ekranın geri kalanı çalışmaya devam ediyor.")

#: Talep listesi sayfa sayfa taranırken kabul edilen üst sınır. Bozuk `meta`
#: yüzünden sonsuz döngüye girmemek için var.
REQUEST_PAGE_LIMIT = 60


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


class PreviewError(RuntimeError):
    """Önizleme görüntüsü üretilemedi. Rapor yine de kaydedilmiştir."""


class RefundsService:
    """İadeler ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ."""

    def __init__(self, *, api: Any, store: Any, log: Any, config: dict[str, Any],
                 printer: Any = None, category: str = "Mağaza", subcategory: str = "Finans",
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
        self._calc = store.table("calc")

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

    async def refunds(self, *, date_from: str = "", date_to: str = "") -> dict[str, Any]:
        """Kredi notları + RMA talepleri, tek liste + KPI.

        Süzme İSTEMCİDEDİR (ölçek "yüzler"); sunucu pencereyi tarar, satırları
        normalleştirir ve özeti hesaplar. İki kaynaktan biri düşerse diğeri
        yine gösterilir — ekran ayakta kalır (K7).
        """
        start, end, window_note = self._window(date_from, date_to)
        today = calc.today_iso()
        warnings: list[str] = [window_note] if window_note else []

        credit_rows, credit_ok, credit_error, truncated = await self._scan_refunds(start, end)
        if not credit_ok:
            warnings.append(f"Kredi notları okunamadı: {credit_error}")

        request_rows, requests_ok, request_error = await self._scan_requests(start, end)
        if not requests_ok:
            warnings.append(f"İade talepleri okunamadı: {request_error}")
        elif request_error:
            # Tarama ortada koptu: elde ne varsa gösteriliyor ama liste EKSİK.
            # Sessiz kalmak, açık talebi olmayan bir gün gibi görünmesine yol açar.
            warnings.append(f"İade talepleri eksik alındı: {request_error}")

        rows = calc.merge_rows(credit_rows, request_rows)
        calc.decorate(rows, today=today, free_return_days=self._free_return_days,
                      return_wait_days=self._return_wait_days)

        sales, sales_error = await self._period_sales()
        summary = calc.summarize(rows, today=today, sales_total=sales)
        if sales is None:
            summary["rateNote"] = (
                "İade oranı hesaplanamadı: dönem satış tutarı okunamadı"
                + (f" ({sales_error})" if sales_error else "")
                + ". Oran uydurulmaz, boş bırakılır.")
        else:
            summary["rateNote"] = ""

        return {
            "ok": True,
            "connected": credit_ok or requests_ok,
            "error": "" if (credit_ok or requests_ok) else (credit_error or request_error),
            "items": rows,
            "summary": summary,
            "warnings": warnings,
            "truncated": truncated,
            "range": {"from": start, "to": end},
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

    async def _scan_refunds(self, start: str, end: str) -> tuple[list[dict[str, Any]], bool,
                                                                str, bool]:
        # `date_from`/`date_to` CANLIDA UYGULANIYOR (doğrulandı: 2026-07-26'dan
        # sonrası 0 kayıt döndü). `channel`/`locale` ise SESSİZCE YOK SAYILIYOR
        # — Laravel tanımadığı sorgu parametresini atar. İkisi de gönderiliyor
        # çünkü zararsız ve uç desteklerse çalışır; ama "kanala göre süzüyoruz"
        # SANILMASIN: mağazada tek kanal var ve süzme olmadan da doğru sonuç
        # çıkıyor. Kanal ayrımı gerekirse süzme burada, satır bazında yapılmalı.
        filters = {"channel": self._channel, "locale": self._locale,
                   "date_from": start, "date_to": end}
        try:
            payload = await self._api.refunds(filters, all_pages=True)
        except Exception as failure:  # noqa: BLE001 — ekran ayakta kalmalı (K7)
            self._log.warning("kredi notları okunamadı", error=str(failure))
            return [], False, self._fail(failure), False
        raw = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
        cap = self._scan_cap
        truncated = bool(payload.get("truncated")) or len(raw) > cap
        return [calc.refund_row(item) for item in raw[:cap]], True, "", truncated

    async def _scan_requests(self, start: str, end: str) -> tuple[list[dict[str, Any]], bool, str]:
        """RMA taleplerini sayfa sayfa toplar.

        `bbd_return_requests` toplu tarama parametresi almıyor; sayfalar SIRAYLA
        çekilir. Uç henüz yayında değilse boş liste + açıklama döner, ekran
        kredi notlarıyla çalışmaya devam eder.
        """
        rows: list[dict[str, Any]] = []
        filters = {"date_from": start, "date_to": end, "type": "refund"}
        page = 1
        while page <= REQUEST_PAGE_LIMIT and len(rows) < self._scan_cap:
            try:
                payload = await self._api.bbd_return_requests(filters, page=page, per_page=50)
            except Exception as failure:  # noqa: BLE001 — K7
                if page > 1:
                    # Kısmî liste yalanla dolmaz: elde ne varsa o gösterilir
                    # ve neden eksik olduğu söylenir.
                    return rows, True, self._fail(failure)
                self._log.info("iade talepleri okunamadı", error=str(failure))
                return [], False, self._fail(failure)
            items = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
            rows.extend(calc.request_row(item) for item in items)
            meta = payload.get("meta") or {}
            last = calc.as_int(meta.get("lastPage"), page)
            if not items or page >= last:
                break
            page += 1
        return rows[:self._scan_cap], True, ""

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

    async def order_card(self, order_id: int) -> dict[str, Any]:
        """Çekmecenin tek isteği: sipariş + kalemler + kargo + POS + geçmiş iadeler.

        Parçalar SIRAYLA ve TEK TEK korunarak çekilir: biri patlarsa çekmecenin
        gerisi yine dolar (K7). Paralel çekmek geçidin dakikada 55 istek
        kovasını hızla tüketiyor.
        """
        try:
            raw = await self._api.order(int(order_id))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("sipariş okunamadı", orderId=order_id, error=str(failure))
            return {"ok": False, "connected": False, "error": self._fail(failure)}

        warnings: list[str] = []

        async def part(label: str, call: Any) -> tuple[Any, str]:
            try:
                return await call(), ""
            except Exception as failure:  # noqa: BLE001 — uzak sistem
                message = self._fail(failure)
                warnings.append(f"{label}: {message}")
                return None, message

        history, _ = await part("geçmiş iadeler", lambda: self._api.refunds(
            {"order_id": int(order_id)}, per_page=50))
        payments, payments_error = await part("POS denemeleri", lambda: self._api.
                                              bbd_payment_attempts({"order_id": int(order_id)},
                                                                   per_page=50))
        shipments, shipments_error = await part("gönderiler", lambda: self._api.bbd_shipments(
            {"order_id": int(order_id)}, per_page=50))

        history_rows = [item for item in ((history or {}).get("items") or [])
                        if isinstance(item, dict)]
        view = calc.order_view(raw, basis=self._basis,
                               shipping_refunded=None if history is None
                               else calc.shipping_refunded_of(history_rows))
        if history is None and view["shippingAvailable"] > 0:
            warnings.append(
                "Geçmiş iadeler okunamadığı için kargo bedelinin daha önce geri verilip "
                "verilmediği DOĞRULANAMADI; kargo kutusunu işaretlemeden önce kontrol edin.")

        return {
            "ok": True, "connected": True, "error": "", "warnings": warnings,
            "order": view,
            "shippingVerified": history is not None,
            "history": [calc.refund_row(item) for item in history_rows],
            "payments": {
                "available": payments is not None,
                "error": payments_error,
                "items": [self._payment_row(item)
                          for item in ((payments or {}).get("items") or [])
                          if isinstance(item, dict)],
                # POS İADESİ DÜĞMESİ TIKLANMADAN ÖNCE KAPANIR. Liste okunuyor
                # (hangi karta ne çekildiği gerçek bir bilgi), yalnız parayı
                # geri gönderme adımı buradan yapılmıyor.
                "refundBlocked": True,
                "refundReason": self.POS_REFUND_REASON,
            },
            "shipments": {
                "available": shipments is not None,
                "error": shipments_error,
                "items": [self._shipment_row(item)
                          for item in ((shipments or {}).get("items") or [])
                          if isinstance(item, dict)],
            },
            "shippingDefault": self._shipping_default,
            "basisNotice": "" if view["invoicedAny"] else (
                "Bu siparişte faturalanmış adet yok. İade edilebilir adet sipariş adedinden "
                "alındı; parası tahsil edilmemiş bir kalemi iade etmediğinizden emin olun."),
        }

    @staticmethod
    def _payment_row(raw: dict[str, Any]) -> dict[str, Any]:
        amount = calc.to_kurus(calc.pick(raw, "amount", "total")) or 0
        refunded = calc.to_kurus(calc.pick(raw, "refunded_amount", "refundedAmount")) or 0
        return {
            "id": calc.as_int(calc.pick(raw, "id", "attempt_id")),
            "createdAt": calc.text(calc.pick(raw, "created_at", "createdAt"))[:19],
            "bank": calc.text(calc.pick(raw, "bank", "provider", "pos", "gateway")),
            "card": calc.text(calc.pick(raw, "masked_card", "card", "card_last4")),
            "installment": calc.as_int(calc.pick(raw, "installment", "installments")),
            "status": calc.text(calc.pick(raw, "status", "state")),
            "amount": amount,
            "refunded": refunded,
            "refundable": max(0, amount - refunded),
        }

    @staticmethod
    def _shipment_row(raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": calc.as_int(calc.pick(raw, "id", "shipment_id")),
            "carrier": calc.text(calc.pick(raw, "carrier", "carrier_name")),
            "tracking": calc.text(calc.pick(raw, "tracking_number", "trackingNumber",
                                            "tracking_no")),
            "status": calc.text(calc.pick(raw, "status", "state")),
            "direction": calc.text(calc.pick(raw, "direction", "kind")),
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
            try:
                history = await self._api.refunds({"order_id": int(order_id)}, per_page=50)
                already = calc.shipping_refunded_of(
                    [item for item in (history.get("items") or []) if isinstance(item, dict)])
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
                      dry_run: bool = True) -> dict[str, Any]:
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

        await self._record(order_id=order_id, action="create_refund", reason=reason, actor=actor,
                           result="denendi", detail={"total": row["total"], "items": items})
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
                           detail={"total": row["total"], "items": items})
        return {"ok": True, "error": "", "orderId": order_id, "total": int(row["total"]),
                "dryRun": bool(result.get("dryRun", dry_run)),
                "sent": bool(result.get("sent", not dry_run)),
                "warning": warning,
                "notice": "Kredi notu oluştu. Parayı karta geri vermek AYRI bir adımdır: "
                          "POS iadesini aşağıdaki bölümden başlatın."}

    #: SANAL POS İADESİ BU EKRANDAN YAPILMAZ — geçit ucu bilerek yazmadı.
    #:
    #: Gerekçe tek cümle: para hareketi. Kredi notu bir muhasebe kaydıdır ve
    #: geri alınabilir; kartın parasını geri göndermek geri alınamaz ve
    #: bankanın kendi mutabakatına girer.
    POS_REFUND_REASON = (
        "Sanal POS iadesi Kontrol Merkezi'nden yapılmıyor: bu adım parayı MÜŞTERİNİN "
        "KARTINA geri gönderir ve geri alınamaz. Kredi notu (muhasebe kaydı) buradan "
        "kesilir; paranın kartа dönüşü Bagisto panelinden ya da sanal POS ekranından "
        "başlatılır. Ekranda kesilen kredi notu bu adımı BEKLER, kendiliğinden "
        "tamamlamaz."
    )

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
        """Sanal POS iadesi — parayı karta geri verir.

        Kredi notundan AYRI tutulur: kredi notu muhasebe kaydıdır, bu ise banka
        hareketidir. Birini yapıp diğerini yapmamak mümkündür ve ekran ikisini
        ayrı ayrı gösterir; tek düğmede birleştirmek, POS reddettiğinde "iade
        edildi" yazan bir kayıt bırakırdı.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        value = calc.as_int(amount)
        if value <= 0:
            return {"ok": False, "error": "İade tutarı sıfırdan büyük olmalı."}
        if not calc.as_int(attempt_id):
            return {"ok": False, "error": "POS işlemi seçilmedi."}

        await self._record(order_id=order_id, action="pos_refund", reason=reason, actor=actor,
                           result="denendi", detail={"attemptId": attempt_id, "amount": value})
        try:
            result = await self._api.bbd_refund_payment(int(attempt_id), amount=value,
                                                        reason=reason, actor=actor,
                                                        dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(order_id=order_id, action="pos_refund", reason=reason, actor=actor,
                               result="hata", detail={"error": str(failure)})
            # Geçit bu ucu BİLEREK yazmadı ve istek hiç çıkmaz; hata metni onu
            # zaten söylüyor. `blocked` bayrağı ekranın düğmeyi bir daha
            # açmamasını sağlar — nedeni okunup kapatılmış bir düğme, her
            # tıklamada aynı cümleyi gösteren bir düğmeden iyidir.
            return {"ok": False, "error": self._fail(failure),
                    "blocked": self._by_design(failure),
                    "missing": self._missing(failure)}

        await self._record(order_id=order_id, action="pos_refund", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"attemptId": attempt_id, "amount": value})
        return {"ok": True, "error": "", "amount": value,
                "dryRun": bool(result.get("dryRun", dry_run)),
                "bankStatus": calc.text(calc.pick(result, "status", "bank_status")),
                "bankMessage": calc.text(calc.pick(result, "message", "bank_message")),
                "reference": calc.text(calc.pick(result, "reference", "bank_reference"))}

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

        await self._record(order_id=0, action=f"request_{wanted}", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"requestId": request_id})
        return {"ok": True, "error": "", "status": wanted,
                "statusLabel": calc.STATUS_LABELS[wanted],
                "dryRun": bool(result.get("dryRun", dry_run))}

    async def return_shipment(self, shipment_id: int, *, order_id: int = 0, reason: str,
                              actor: str, dry_run: bool = True) -> dict[str, Any]:
        """Müşteriden geri gönderim için iade gönderisi açar (takip no üretir)."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        if not calc.as_int(shipment_id):
            return {"ok": False, "error": "Geri gönderim için bir gönderi seçilmedi."}

        await self._record(order_id=order_id, action="return_shipment", reason=reason,
                           actor=actor, result="denendi", detail={"shipmentId": shipment_id})
        try:
            result = await self._api.bbd_return_shipment(int(shipment_id), reason=reason,
                                                         actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(order_id=order_id, action="return_shipment", reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure),
                    "missing": self._missing(failure)}

        await self._record(order_id=order_id, action="return_shipment", reason=reason,
                           actor=actor, result="dry_run" if dry_run else "ok",
                           detail={"shipmentId": shipment_id})
        return {"ok": True, "error": "",
                "tracking": calc.text(calc.pick(result, "tracking_number", "trackingNumber")),
                "carrier": calc.text(calc.pick(result, "carrier", "carrier_name")),
                "dryRun": bool(result.get("dryRun", dry_run))}

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
        """Pencerenin tamamının CSV'si — rapor klasörüne yazılır."""
        payload = await self.refunds(date_from=date_from, date_to=date_to)
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
                                         date_to=calc.text(params.get("dateTo")))
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
