"""Kargo Yönetimi — iş kuralları.

VERİ MAĞAZADADIR, KARAR BURADADIR. Gönderi, taşıyıcı ve desi→ücret matrisi
`store.api` geçidinden gelir (K4); bu modül onların kopyasını tutmaz. Yerel
tablolar yalnız mağazada KARŞILIĞI OLMAYAN dördü saklar: yazma gerekçesi,
il/ilçe→bölge eşlemesi, üretilen teslimat manifestoları ve ekran tercihleri.

PARA HARCAYAN İŞ VAR. `purchase` taşıyıcıdan etiket satın alır ve geri
alınamaz. Üç kapı birden uygulanır: ayrı izin (`store_shipping.purchase`,
uçta), en az 20 karakterlik gerekçe (burada TEKRAR doğrulanır — K9) ve
`dry_run` varsayılanı. Kuru provada geçit isteği hiç göndermez; ekran neyin
gideceğini gösterir.

UZAK SİSTEM DÜŞERSE EKRAN AYAKTA KALIR (K7): `connected: False` + `error`
döner, panel durumu ve son bilinen veriyi anlatır. İstisna dışarı sızmaz.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from km_sdk import ExportError, build_pdf, csv_bytes, money, number, report_dir, write_private

from . import analytics, labels, shipping

#: Rapor/CSV için tam tarama tavanı. 30 günde binlerce gönderi olmaz; tavan
#: bozuk `meta` yüzünden sonsuz sayfalamayı engellemek içindir.
SCAN_CAP = 4_000

REPORT_KINDS = ("labels", "manifest", "performance", "receipt")


def _now() -> str:
    return shipping.now_iso()


class PreviewError(RuntimeError):
    """Önizleme görüntüsü üretilemedi. Rapor yine de kaydedilmiştir."""


class ShippingService:
    """Kargo ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ."""

    def __init__(self, *, api: Any, store: Any, log: Any, config: dict[str, Any],
                 printer: Any = None, notifier: Any = None, publish: Any = None,
                 category: str = "Mağaza", subcategory: str = "Kargo",
                 fallback_dir: Path | None = None) -> None:
        self._api = api
        self._store = store
        self._log = log
        self._config = config or {}
        self._printer = printer
        self._notifier = notifier
        self._publish = publish
        self._category = category
        self._subcategory = subcategory
        self._fallback = fallback_dir or Path.home() / "km-raporlar"

        self._audit = store.table("audit")
        self._zones = store.table("zones")
        self._manifests = store.table("manifests")
        self._prefs = store.table("prefs")

        #: Ayardaki kanal KODUNUN karşılığı olan kanal kimliği. Bir kez çözülür
        #: ve saklanır; geçit kanal listesini zaten 900 sn önbelleğe alıyor.
        self._channel_id: int | None = None

    # ------------------------------------------------------------- ayarlar

    @property
    def _channel(self) -> str:
        return str(self._config.get("channel") or "default")

    @property
    def _locale(self) -> str:
        return str(self._config.get("locale") or "tr")

    @property
    def _page_size(self) -> int:
        return max(10, min(200, shipping.as_int(self._config.get("page_size"), 50)))

    @property
    def _divisor(self) -> int:
        return max(1000, min(6000, shipping.as_int(self._config.get("desi_divisor"), 3000)))

    @property
    def _idle_limit(self) -> int:
        return max(1, min(30, shipping.as_int(self._config.get("idle_days"), 3)))

    @property
    def _default_format(self) -> str:
        wanted = str(self._config.get("label_format") or labels.THERMAL)
        return wanted if wanted in labels.FORMATS else labels.THERMAL

    @property
    def _batch_limit(self) -> int:
        return max(1, min(200, shipping.as_int(self._config.get("label_batch_limit"), 60)))

    @property
    def _export_dir(self) -> Path:
        # HER ÇAĞRIDA yeniden çözülür: ay değişince klasör kendiliğinden değişir.
        return report_dir(self._category, subcategory=self._subcategory,
                          fallback=self._fallback,
                          configured=str(self._config.get("export_path") or ""))

    # ------------------------------------------------------ yerel tablolar

    async def _pref(self, key: str) -> str:
        try:
            row = await self._store.fetch_one(
                f"SELECT value FROM {self._prefs} WHERE key = ?", (key,))
        except Exception as failure:  # noqa: BLE001 — tercih okunamadı, varsayılan yeter
            self._log.warning("tercih okunamadı", key=key, error=str(failure))
            return ""
        return str(row["value"]) if row else ""

    async def _set_pref(self, key: str, value: str, actor: str) -> bool:
        """Tercihi yazar. Yazılamazsa `False` döner — uç 500 vermez (K7)."""
        try:
            await self._store.execute(
                f"INSERT INTO {self._prefs} (key, value, actor, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, actor = excluded.actor, "
                "updated_at = excluded.updated_at",
                (key, value, actor, _now()),
            )
        except Exception as failure:  # noqa: BLE001 — ekran ayakta kalmalı (K7)
            self._log.warning("tercih yazılamadı", key=key, error=str(failure))
            return False
        return True

    async def _record(self, *, shipment_id: int = 0, order_id: int = 0, action: str,
                      reason: str = "", actor: str = "", result: str = "",
                      detail: Any = None) -> None:
        """Yerel denetim izi. Mağaza denetim tutuyor ama GEREKÇEYİ tutmuyor;
        ayrıca ağ koparsa "ne yapmaya çalıştık" kaydı burada kalır — etiket
        satın alma para harcayan bir iştir."""
        try:
            await self._store.execute(
                f"INSERT INTO {self._audit} "
                "(shipment_id, order_id, action, reason, actor, result, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (int(shipment_id or 0), int(order_id or 0), action, reason, actor, result,
                 json.dumps(detail or {}, ensure_ascii=False), _now()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın
            self._log.warning("denetim izi yazılamadı", action=action, error=str(failure))

    async def _zone_rows(self) -> list[dict[str, Any]]:
        try:
            return await self._store.fetch_all(
                f"SELECT city, district, zone, surcharge, delivers, note FROM {self._zones}")
        except Exception as failure:  # noqa: BLE001 — bölge yoksa ek ücret 0 sayılır
            self._log.warning("bölge tablosu okunamadı", error=str(failure))
            return []

    # ------------------------------------------------------------- yardımcı

    @staticmethod
    def _fail(failure: Exception) -> str:
        message = str(failure).strip()
        return message or "Mağazaya ulaşılamadı."

    @staticmethod
    def _pending(failure: Exception) -> bool:
        """Uç henüz yayında değil mi — yoksa gerçek bir arıza mı?

        İkisi ayrı cevaplardır ve ekranda ayrı davranış gerektirir: yayında
        olmayan uçta "Kaydet" düğmesi TIKLANMADAN kapanmalı, gerçek arızada
        "tekrar dene" anlamlıdır.

        Kod alanı `StoreApiError` üstünde durur; çevrilebilir METNE bakarak
        tahmin etmek çeviri değişince kırılır. Sınıf import EDİLMEZ (K3):
        modül modülü import etmez, yalnız kod dizesi taşınır.
        """
        return getattr(failure, "code", "") == "bbd_endpoint_missing"

    def _guard(self, reason: str, minimum: int = shipping.MIN_REASON) -> str:
        """Gerekçe backend'de DE doğrulanır (K9): arayüzde gizlemek yetmez."""
        return shipping.reason_error(reason, minimum)

    async def _order_channel(self) -> dict[str, Any]:
        """Bagisto ÇEKİRDEK sipariş ucuna gidecek kanal süzgeci.

        TUZAK — CANLI DOĞRULANDI: `/api/admin/orders` `channel` süzgecini kanal
        KİMLİĞİYLE uyguluyor (`channel=1` → 17 sipariş). Ayardaki kanal KODU
        gönderilince (`channel=default`) süzgeç eşleşme bulamıyor ve HİÇ
        sipariş dönmüyor; Laravel bilinmeyen değeri hata da vermiyor. Bu yüzden
        kod önce kimliğe çevrilir.

        Çevrilemezse süzgeç HİÇ GÖNDERİLMEZ: kanal süzmemek, tek kanallı bir
        mağazada zararsızdır; yanlış değer göndermek ise ekranı boş bırakır.
        """
        if self._channel_id:
            return {"channel": self._channel_id}
        wanted = shipping.fold(self._channel)
        if not wanted:
            return {}
        try:
            payload = await self._api.channels()
        except Exception as failure:  # noqa: BLE001 — çözülemedi, süzgeçsiz devam (K7)
            self._log.warning("kanal listesi okunamadı, kanal süzgeci gönderilmiyor",
                              error=str(failure))
            return {}
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            if shipping.fold(item.get("code")) == wanted or (
                    shipping.as_int(item.get("id"), -1) == shipping.as_int(self._channel, -2)):
                self._channel_id = shipping.as_int(item.get("id"))
                break
        if self._channel_id:
            return {"channel": self._channel_id}
        self._log.warning("kanal kodu mağazada bulunamadı; kanal süzgeci gönderilmiyor",
                          channel=self._channel)
        return {}

    async def _emit(self, event: str, payload: dict[str, Any]) -> None:
        if self._publish is None:
            return
        try:
            await self._publish(event, payload)
        except Exception as failure:  # noqa: BLE001 — olay yolu işi durdurmaz
            self._log.warning("olay yayınlanamadı", event=event, error=str(failure))

    # ========================================================= kargoya hazır

    async def ready(self, *, q: str = "", page: int = 1, size: int = 0) -> dict[str, Any]:
        """Ödemesi alınmış ama kargolanmamış siparişler.

        SÜZME YARIM YAPILIR VE BU SÖYLENİR: mağaza "gönderisi olmayan sipariş"
        süzgeci sunmuyor; durum süzgeci sunucuda uygulanır, kalan adet kontrolü
        SAYFA İÇİNDE yapılır. Bu yüzden `total` sunucunun sayısı, `shown` ise
        gerçekten hazır olanlardır ve panel ikisini birden gösterir — süzülmüş
        listeyi tam liste gibi sunmak yanlış olurdu.

        İKİNCİ YARIMLIK: sipariş LİSTESİ ucu faturalanan/kargolanan adedi hiç
        vermiyor (canlı doğrulandı). Bu bilgi olmadan "hazır değil" demek
        listeyi tümden boşaltırdı; bilgisi olmayan sipariş engellenmez ve
        `verified: False` ile bunun doğrulanmadığı söylenir.
        """
        per_page = size or self._page_size
        filters: dict[str, Any] = {
            "status": str(self._config.get("ready_status") or "processing"),
            **(await self._order_channel()),
        }
        if shipping.text(q):
            filters["customer"] = shipping.text(q)

        try:
            payload = await self._api.orders(filters, page=page, per_page=per_page)
        except Exception as failure:  # noqa: BLE001 — ekran ayakta kalmalı (K7)
            self._log.warning("kargoya hazır siparişler okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "items": [], "blocked": [], "total": 0, "shown": 0, "page": page,
                    "size": per_page, "pages": 0, "verified": False,
                    "divisor": self._divisor}

        zones = await self._zone_rows()
        today = shipping.today_iso()
        rows = [shipping.ready_row(item, today=today, divisor=self._divisor, zones=zones)
                for item in (payload.get("items") or []) if isinstance(item, dict)]
        ready_rows = [row for row in rows if row["ready"]]
        meta = payload.get("meta") or {}
        return {
            "ok": True, "connected": True, "error": "",
            "items": ready_rows,
            "blocked": [row for row in rows if not row["ready"]],
            "total": shipping.as_int(meta.get("total"), len(rows)),
            "shown": len(ready_rows),
            "page": shipping.as_int(meta.get("currentPage"), page),
            "size": shipping.as_int(meta.get("perPage"), per_page),
            "pages": shipping.as_int(meta.get("lastPage"), 1),
            # Liste ucu fatura/kargo adedini vermiyorsa hazırlık DOĞRULANMADI;
            # ekran bunu yazar, sihirbaz siparişi tek tek okuyup yine denetler.
            "verified": all(row["paymentKnown"] and row["shipmentKnown"] for row in rows),
            "divisor": self._divisor,
        }

    # =============================================================== liste

    def _list_filters(self, *, q: str, carrier: str, status: str, city: str,
                      date_from: str, date_to: str, date_field: str, payer: str,
                      desi_min: int, desi_max: int) -> dict[str, Any]:
        filters: dict[str, Any] = {"channel": self._channel}
        if shipping.text(q):
            filters["q"] = shipping.text(q)
        if carrier:
            filters["carrier"] = shipping.fold(carrier)
        if status:
            filters["status"] = status
        if city:
            filters["city"] = shipping.text(city)
        if date_from:
            filters["date_from"] = date_from
        if date_to:
            filters["date_to"] = date_to
        if date_field in ("created", "delivered"):
            filters["date_field"] = date_field
        if payer in ("sender", "receiver"):
            filters["payer"] = payer
        if desi_min:
            filters["desi_from"] = int(desi_min)
        if desi_max:
            filters["desi_to"] = int(desi_max)
        return filters

    async def shipments(self, *, q: str = "", carrier: str = "", status: str = "",
                        city: str = "", date_from: str = "", date_to: str = "",
                        date_field: str = "created", payer: str = "", desi_min: int = 0,
                        desi_max: int = 0, flag: str = "", page: int = 1,
                        size: int = 0) -> dict[str, Any]:
        """Sayfalanmış gönderi listesi. SUNUCU TARAFI sayfalama.

        `flag` (geciken / adres sorunu / tahsil edilmemiş) SAYFA İÇİNDE
        uygulanır: bu üçü taşıyıcıda bir alan değil, bizim türettiğimiz
        bulgudur. Panel bunu yazar; süzülmüş sayfa tam liste gibi gösterilmez.
        """
        per_page = size or self._page_size
        filters = self._list_filters(q=q, carrier=carrier, status=status, city=city,
                                     date_from=date_from, date_to=date_to,
                                     date_field=date_field, payer=payer,
                                     desi_min=desi_min, desi_max=desi_max)
        try:
            payload = await self._api.bbd_shipments(filters, page=page, per_page=per_page)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("gönderi listesi okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "items": [], "total": 0, "shown": 0, "page": page, "size": per_page,
                    "pages": 0, "totals": analytics.totals([]), "flag": flag,
                    "idleLimit": self._idle_limit}

        zones = await self._zone_rows()
        today = shipping.today_iso()
        rows = [shipping.shipment_row(item, today=today, idle_limit=self._idle_limit,
                                      zones=zones)
                for item in (payload.get("items") or []) if isinstance(item, dict)]
        shown = [row for row in rows if not flag or flag in row["flags"]]
        meta = payload.get("meta") or {}
        return {
            "ok": True, "connected": True, "error": "",
            "items": shown,
            "total": shipping.as_int(meta.get("total"), len(rows)),
            "shown": len(shown),
            "page": shipping.as_int(meta.get("currentPage"), page),
            "size": shipping.as_int(meta.get("perPage"), per_page),
            "pages": shipping.as_int(meta.get("lastPage"), 1),
            "totals": analytics.totals(rows),
            "flag": flag,
            "idleLimit": self._idle_limit,
        }

    async def shipment(self, shipment_id: int) -> dict[str, Any]:
        """Gönderi künyesi: kayıt + hareket geçmişi + ücret dökümü."""
        try:
            raw = await self._api.bbd_shipment(int(shipment_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "connected": False, "error": self._fail(failure)}

        zones = await self._zone_rows()
        row = shipping.shipment_row(raw, today=shipping.today_iso(),
                                    idle_limit=self._idle_limit, zones=zones)
        zone = shipping.zone_for(row["city"], row["district"], zones)
        return {
            "ok": True, "connected": True, "error": "",
            "shipment": row,
            "movements": shipping.movement_rows(raw.get("movements") or raw.get("events")),
            "zone": zone,
            "notify": self._notifier is not None,
        }

    # ------------------------------------------------- sağlanan yetenek

    async def by_order(self, order_id: int) -> dict[str, Any]:
        """`store.shipment.byOrder` — Siparişler/Talepler/İadeler bunu okur.

        Yetenek KENDİ sözleşmesini korur: uzak sistem düşse bile istisna
        fırlatmaz, `connected: False` döner. Aksi hâlde bir kargo arızası
        Siparişler ekranını da düşürürdü (K7).
        """
        try:
            payload = await self._api.bbd_shipments({"order_id": int(order_id)}, page=1,
                                                    per_page=25)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.info("siparişin gönderileri okunamadı", orderId=order_id,
                           error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "orderId": int(order_id), "items": []}

        today = shipping.today_iso()
        rows = [shipping.shipment_row(item, today=today, idle_limit=self._idle_limit)
                for item in (payload.get("items") or []) if isinstance(item, dict)]
        return {"ok": True, "connected": True, "error": "", "orderId": int(order_id),
                "items": rows}

    # ============================================================ taşıyıcı

    async def carriers(self) -> dict[str, Any]:
        """Taşıyıcı tanımları. Kimlik bilgileri iki kez maskelenir (geçit + burada)."""
        try:
            payload = await self._api.bbd_carriers()
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": True, "connected": False, "error": self._fail(failure), "items": []}
        today = shipping.today_iso()
        return {"ok": True, "connected": True, "error": "",
                "items": [shipping.carrier_row(item, today=today)
                          for item in (payload.get("items") or []) if isinstance(item, dict)]}

    async def test_carrier(self, carrier: str, *, reason: str, actor: str,
                           dry_run: bool = True) -> dict[str, Any]:
        """Bağlantı sınaması. Yazma ucudur (geçit gerekçe ister) ama yan etkisi
        yoktur: taşıyıcıya kimlik doğrulama isteği gider, gönderi oluşmaz."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        code = shipping.fold(carrier)
        if not code:
            return {"ok": False, "error": "Taşıyıcı seçilmedi."}
        try:
            result = await self._api.bbd_test_carrier(code, reason=reason, actor=actor,
                                                      dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="test_carrier", reason=reason, actor=actor,
                               result="hata", detail={"carrier": code, "error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}
        await self._record(action="test_carrier", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok", detail={"carrier": code})
        return {"ok": True, "error": "", "carrier": code,
                "dryRun": bool(result.get("dryRun", dry_run)),
                "message": shipping.text(result.get("message")),
                "at": shipping.now_iso()}

    # ======================================================= ücretlendirme

    async def rates(self) -> dict[str, Any]:
        """Desi→ücret matrisi, ücretsiz kargo eşiği ve kapıda ödeme bedeli."""
        try:
            payload = await self._api.bbd_shipping_rates()
        except Exception as failure:  # noqa: BLE001 — K7
            # UÇ YAYINDA DEĞİLSE YAZMA DÜĞMESİ DE KAPANIR. Açık bırakmak,
            # kullanıcıya matrisi doldurtup gerekçe yazdırdıktan SONRA "uç
            # yayında değil" demek olurdu — emek boşa gider ve ekran arızalı
            # görünür.
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "endpointPending": self._pending(failure),
                    "carriers": [], "freeThreshold": 0, "codFee": 0, "promise": "",
                    "problems": []}

        carriers: list[dict[str, Any]] = []
        for item in payload.get("carriers") or []:
            if not isinstance(item, dict):
                continue
            tiers = shipping.tier_rows(item.get("tiers") or item.get("desi_rates"))
            carriers.append({
                "code": shipping.fold(item.get("code") or item.get("carrier")),
                "label": shipping.carrier_label(item.get("code") or item.get("carrier")),
                "tiers": tiers,
                "problems": shipping.tier_problems(tiers),
            })
        return {
            "ok": True, "connected": True, "error": "", "endpointPending": False,
            "carriers": carriers,
            "freeThreshold": shipping.to_kurus(payload.get("free_shipping_threshold")) or 0,
            "codFee": shipping.to_kurus(payload.get("cod_fee")) or 0,
            "promise": shipping.text(payload.get("delivery_promise")),
            "problems": [problem for item in carriers for problem in item["problems"]],
        }

    async def save_rates(self, *, carriers: list[dict[str, Any]], free_threshold: int,
                         cod_fee: int, promise: str, reason: str, actor: str,
                         dry_run: bool = True) -> dict[str, Any]:
        """Ücretlendirmeyi yazar. VİTRİNİ DOĞRUDAN ETKİLER.

        Kademelerde boşluk/örtüşme varsa yazma REDDEDİLİR: açıkta kalan bir
        desi aralığı, vitrinde "kargo ücreti hesaplanamadı" hatasına ya da
        sıfır ücretli gönderiye dönüşür ve zararı sipariş sipariş birikir.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        payload_carriers: list[dict[str, Any]] = []
        problems: list[str] = []
        for item in carriers or []:
            code = shipping.fold(item.get("code"))
            if not code:
                continue
            # Ekrandan gelen fiyat ZATEN kuruştur; `tier_rows` ondalık okur ve
            # burada kullanılırsa tarifeyi yüz katına çıkarır.
            tiers = shipping.tier_input(item.get("tiers"))
            found = shipping.tier_problems(tiers)
            problems.extend(f"{shipping.carrier_label(code)}: {line}" for line in found)
            payload_carriers.append({"code": code, "tiers": shipping.tier_payload(tiers)})

        if not payload_carriers:
            return {"ok": False, "error": "Kaydedilecek taşıyıcı matrisi yok."}
        if problems:
            return {"ok": False, "error": "Matris tutarsız, yazılmadı.", "problems": problems}

        body = {
            "carriers": payload_carriers,
            "free_shipping_threshold": shipping.from_kurus(max(0, int(free_threshold or 0))),
            "cod_fee": shipping.from_kurus(max(0, int(cod_fee or 0))),
            "delivery_promise": shipping.text(promise)[:255],
        }
        await self._record(action="save_rates", reason=reason, actor=actor, result="denendi",
                           detail={"carriers": [item["code"] for item in payload_carriers]})
        try:
            result = await self._api.bbd_update_shipping_rates(payload=body, reason=reason,
                                                               actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="save_rates", reason=reason, actor=actor, result="hata",
                               detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(action="save_rates", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok", detail=body)
        return {"ok": True, "error": "", "problems": [],
                "dryRun": bool(result.get("dryRun", dry_run))}

    async def refresh_price_list(self, *, reason: str, actor: str,
                                 dry_run: bool = True) -> dict[str, Any]:
        """Taşıyıcının fiyat listesini mağazaya yeniden çektirir."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        try:
            result = await self._api.bbd_refresh_price_list(reason=reason, actor=actor,
                                                            dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        await self._record(action="refresh_price_list", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok")
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run))}

    # ============================================================== bölge

    async def zones(self) -> dict[str, Any]:
        """İl/ilçe → bölge eşlemesi. YEREL tablodur; mağazada karşılığı yok.

        Bu eşleme şimdilik yalnız Kontrol Merkezi'nin ücret dökümünde ve
        sihirbaz uyarısında kullanılır; vitrindeki kargo ücretine yansıması
        için mağaza tarafında bir bölge ucu gerekiyor. Ekran bunu yazar —
        "kaydettim, vitrin değişti" yanılgısı üretilmez.
        """
        try:
            rows = await self._store.fetch_all(
                f"SELECT id, city, district, zone, surcharge, delivers, note, updated_at "
                f"FROM {self._zones} ORDER BY city, district")
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": True, "items": [], "error": self._fail(failure), "storeSynced": False}
        return {
            "ok": True, "error": "", "storeSynced": False,
            "items": [{
                "id": shipping.as_int(row["id"]),
                "city": shipping.text(row["city"]),
                "district": shipping.text(row["district"]),
                "zone": shipping.text(row["zone"]),
                "surcharge": shipping.as_int(row["surcharge"]),
                "delivers": bool(shipping.as_int(row["delivers"], 1)),
                "note": shipping.text(row["note"]),
                "updatedAt": shipping.text(row["updated_at"])[:19],
            } for row in rows],
        }

    async def save_zone(self, *, city: str, district: str, zone: str, surcharge: int,
                        delivers: bool, note: str, reason: str, actor: str) -> dict[str, Any]:
        """Tek bölge satırını yazar (ekle/güncelle). SİLME YOK: teslimat
        yapılmayan bölge `delivers = 0` ile işaretlenir, satır durur —
        silinseydi "neden bu ilçeye göndermiyorduk" kaydı kaybolurdu."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        city_name = shipping.text(city)
        if not city_name:
            return {"ok": False, "error": "İl adı zorunlu."}
        try:
            await self._store.execute(
                f"INSERT INTO {self._zones} "
                "(city, district, zone, surcharge, delivers, note, actor, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(city, district) DO UPDATE SET zone = excluded.zone, "
                "surcharge = excluded.surcharge, delivers = excluded.delivers, "
                "note = excluded.note, actor = excluded.actor, updated_at = excluded.updated_at",
                (city_name, shipping.text(district), shipping.text(zone),
                 max(0, int(surcharge or 0)), 1 if delivers else 0, shipping.text(note)[:255],
                 actor, _now()),
            )
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        await self._record(action="save_zone", reason=reason, actor=actor, result="ok",
                           detail={"city": city_name, "district": shipping.text(district)})
        return {"ok": True, "error": ""}

    # ========================================================== sihirbaz

    async def quote(self, *, order_id: int, carrier: str, desi_value: float, weight: float,
                    payer: str = "sender", cod: int = 0) -> dict[str, Any]:
        """Gönderi ücretinin DÖKÜMÜ — yazmadan önce ekranda gösterilir.

        Hesap yereldir ve tahmindir; kesin tutar taşıyıcı teklifinden
        (`offers`) gelir. İkisi ayrı gösterilir: tahmini gerçek fiyat gibi
        sunmak, sonradan gelen faturayı sürpriz yapar.
        """
        units = shipping.billed_units(desi_value, weight)
        rate_payload = await self.rates()
        zones = await self._zone_rows()

        order: dict[str, Any] = {}
        if order_id:
            try:
                order = await self._api.order(int(order_id))
            except Exception as failure:  # noqa: BLE001 — K7
                self._log.info("teklif için sipariş okunamadı", orderId=order_id,
                               error=str(failure))

        # Teslimat adresi `addresses[]` listesinden ayıklanır (canlı yanıtta
        # `shipping_address` diye bir alan YOK); fatura adresine düşülmez.
        address = shipping.shipping_address(order)
        zone = shipping.zone_for(address.get("city"), address.get("district")
                                 or address.get("state"), zones)
        tiers: list[dict[str, Any]] = []
        for item in rate_payload.get("carriers") or []:
            if item["code"] == shipping.fold(carrier):
                tiers = item["tiers"]
                break

        estimate = shipping.quote(
            units=units, tiers=tiers,
            # CANLI DOĞRULANDI: sepet toplamı `grandTotal`; `grand_total` yok.
            # Yanlış okunursa ücretsiz kargo eşiği hiç uygulanmazdı.
            basket_total=shipping.to_kurus(
                order.get("grandTotal", order.get("grand_total"))) or 0,
            free_threshold=shipping.as_int(rate_payload.get("freeThreshold")),
            zone_surcharge=zone["surcharge"],
            cod=bool(cod), cod_fee=shipping.as_int(rate_payload.get("codFee")),
            payer="receiver" if shipping.fold(payer) == "receiver" else "sender",
        )
        return {"ok": True, "error": "", "connected": bool(rate_payload.get("connected")),
                "quote": estimate, "zone": zone, "units": units,
                "tierCount": len(tiers)}

    async def create_shipment(self, order_id: int, *, carrier: str, packages: int,
                              desi_value: float, weight: float, payer: str, cod: int,
                              note: str, reason: str, actor: str,
                              dry_run: bool = True,
                              provider: str = "") -> dict[str, Any]:
        """Gönderi TASLAĞI açar. ETİKET SATIN ALINMAZ.

        Taslak açmak ile etiket almak bilerek ayrı iki adımdır: ilki
        ücretsizdir ve düzeltilebilir, ikincisi para harcar ve geri alınamaz.
        Tek düğmede birleştirmek, ölçüsü yanlış girilmiş bir gönderinin
        parasını ödetirdi.

        `provider` İKİ YOLU AYIRIR:

          "geliver"  GERÇEK. Taşıyıcıya gider, sonraki adımda etiket satın
                     alınır, PARA HARCAR.
          "bagisto"  TEST. Bagisto'nun kendi gönderi kaydını açar; taşıyıcıya
                     hiç çıkmaz, para harcamaz, takip numarasını biz üretiriz.
                     Sipariş gerçekten "kargolandı" durumuna geçtiği için
                     akışın tamamı (bilgilendirme, fiş) para harcamadan
                     denenebilir.

        Boş bırakılırsa ayardaki `provider` kullanılır.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        yol = (shipping.fold(provider) or shipping.fold(self._config.get("provider"))
               or "geliver")
        if yol not in ("geliver", "bagisto"):
            return {"ok": False, "error": f"Bilinmeyen kargo yolu: {provider}"}
        if yol == "bagisto":
            return await self._test_shipment(int(order_id), carrier=carrier, note=note,
                                             reason=reason, actor=actor, dry_run=dry_run)

        zones = await self._zone_rows()
        order: dict[str, Any] = {}
        try:
            order = await self._api.order(int(order_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}

        state = shipping.ready_state(order)
        if not state["ready"]:
            return {"ok": False, "error": f"Bu sipariş kargoya hazır değil: {state['blocked']}"}

        address = shipping.shipping_address(order)
        zone = shipping.zone_for(address.get("city"),
                                 address.get("district") or address.get("state"), zones)
        warnings = shipping.wizard_problems(carrier=carrier, desi_value=desi_value,
                                            weight=weight, packages=packages, payer=payer,
                                            cod=cod, delivers=zone["delivers"])
        blocking = [line for line in warnings if "Taşıyıcı seçilmedi" in line
                    or "Paket sayısı" in line or "Desi ve ağırlık boş" in line]
        if blocking:
            return {"ok": False, "error": " ".join(blocking), "warnings": warnings}

        body = shipping.wizard_body(order_id=int(order_id), carrier=carrier,
                                    packages=packages, desi_value=desi_value, weight=weight,
                                    payer=payer, cod=cod, note=note, divisor=self._divisor)
        await self._record(order_id=order_id, action="create_shipment", reason=reason,
                           actor=actor, result="denendi", detail=body)
        try:
            result = await self._api.bbd_create_shipment(int(order_id), payload=body,
                                                         reason=reason, actor=actor,
                                                         dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(order_id=order_id, action="create_shipment", reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure), "warnings": warnings}

        shipment_id = self._extract_id(result)
        await self._record(shipment_id=shipment_id, order_id=order_id,
                           action="create_shipment", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok", detail=body)
        await self._emit("store.shipment.created", {
            "orderId": int(order_id), "shipmentId": shipment_id,
            "carrier": body["carrier"], "dryRun": bool(dry_run)})
        return {"ok": True, "error": "", "shipmentId": shipment_id, "warnings": warnings,
                "dryRun": bool(result.get("dryRun", dry_run)),
                "sent": bool(result.get("sent", not dry_run)), "body": body}

    async def _test_shipment(self, order_id: int, *, carrier: str, note: str,
                             reason: str, actor: str, dry_run: bool) -> dict[str, Any]:
        """Bagisto'nun kendi gönderi kaydı — TEST YOLU. Taşıyıcıya çıkılmaz.

        Geliver yolundan üç farkı var ve üçü de bilerek:

        1. ÖLÇÜ SORULMAZ. Desi/ağırlık/kapıda ödeme taşıyıcı için anlamlıdır;
           burada taşıyıcı yok. Sormak, kullanıcıya işe yaramayan bir form
           doldurtmak olurdu.
        2. TAKİP NUMARASINI BİZ ÜRETİRİZ ve `TEST-` ile başlar. Gören herkes
           (müşteri dâhil) bunun deneme olduğunu anlar.
        3. SİPARİŞİN DETAYI OKUNUR, listesi değil. Kargolanacak adet kalem
           başına `qtyOrdered - qtyShipped`; bu alanlar sipariş LİSTESİ ucunda
           YOK (canlı doğrulandı). Listeden gelen kayıtla çalışmak, kısmi
           kargolanmış siparişin kalemlerini ikinci kez gönderirdi.
        """
        try:
            order = await self._api.order(int(order_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}

        state = shipping.ready_state(order)
        if not state["ready"]:
            return {"ok": False, "error": f"Bu sipariş kargoya hazır değil: {state['blocked']}"}

        title = (shipping.text(carrier)
                 or shipping.text(self._config.get("test_carrier_title"))
                 or "BBD Test Kargo")
        track = shipping.test_track_number(order_id, when=shipping.today_iso(),
                                           suffix=await self._test_sequence(order_id))
        body = shipping.bagisto_body(order, carrier_title=title, track_number=track,
                                     source_id=self._source_id())

        blocking = shipping.bagisto_problems(body)
        if blocking:
            return {"ok": False, "error": " ".join(blocking)}

        await self._record(order_id=order_id, action="create_test_shipment", reason=reason,
                           actor=actor, result="denendi", detail=body)
        try:
            result = await self._api.create_shipment(int(order_id), payload=body,
                                                     reason=reason, actor=actor,
                                                     dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(order_id=order_id, action="create_test_shipment", reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        shipment_id = self._extract_id(result)
        await self._record(shipment_id=shipment_id, order_id=order_id,
                           action="create_test_shipment", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok", detail=body)
        await self._emit("store.shipment.created", {
            "orderId": int(order_id), "shipmentId": shipment_id,
            "carrier": title, "provider": "bagisto", "test": True,
            "dryRun": bool(dry_run)})
        return {
            "ok": True, "error": "", "provider": "bagisto", "test": True,
            "shipmentId": shipment_id, "trackNumber": track, "carrier": title,
            "items": body["items"],
            "note": shipping.text(note)[:255],
            "dryRun": bool(result.get("dryRun", dry_run)),
            "sent": bool(result.get("sent", not dry_run)),
            "warnings": [("Bu bir TEST gönderisidir: taşıyıcıya çıkılmadı, "
                          "etiket satın alınmadı, para harcanmadı.")],
            "body": body,
        }

    def _source_id(self) -> int:
        """Stok kaynağı. Mağazada tek depo var (id=1, 'Varsayılan')."""
        return max(1, shipping.as_int(self._config.get("inventory_source_id"), 1))

    async def _test_sequence(self, order_id: int) -> int:
        """Aynı siparişe bugün kaçıncı test gönderisi.

        Takip numarası sabit üretildiği için sıra olmadan ikinci gönderi
        birincisiyle AYNI numarayı alırdı. Sıra denetim defterinden sayılır;
        sayılamazsa 1 döner — numara çakışırsa Bagisto reddeder, sessizce
        yanlış kayıt oluşmaz.
        """
        try:
            rows = await self._store.fetch_all(
                f"SELECT COUNT(*) AS n FROM {self._store.table('audit')} "
                "WHERE action = ? AND order_id = ? AND substr(created_at, 1, 10) = ? "
                "AND result = 'ok'",
                ("create_test_shipment", int(order_id), shipping.today_iso()),
            )
        except Exception:  # noqa: BLE001 — sayaç okunamazsa akış durmaz
            return 1
        first = (rows or [{}])[0]
        return max(1, shipping.as_int(first.get("n"), 0) + 1)

    @staticmethod
    def _extract_id(result: Any) -> int:
        if not isinstance(result, dict):
            return 0
        data = result.get("data")
        if isinstance(data, dict):
            return shipping.as_int(data.get("id"))
        return shipping.as_int(result.get("id") or result.get("shipmentId"))

    async def offers(self, shipment_id: int) -> dict[str, Any]:
        """Taşıyıcı teklifleri. Fiyat KURUŞA çevrilir; sıralama ucuzdan pahalıya."""
        try:
            payload = await self._api.bbd_shipment_offers(int(shipment_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": True, "connected": False, "error": self._fail(failure), "items": []}

        rows: list[dict[str, Any]] = []
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            rows.append({
                "id": shipping.text(item.get("id") or item.get("offer_id")),
                "carrier": shipping.fold(item.get("carrier") or item.get("provider")),
                "carrierLabel": shipping.carrier_label(item.get("carrier")
                                                       or item.get("provider")),
                "service": shipping.text(item.get("service") or item.get("service_name")),
                "price": shipping.to_kurus(item.get("price", item.get("amount"))) or 0,
                "currency": shipping.text(item.get("currency")) or "TRY",
                "promise": shipping.text(item.get("estimated_delivery")
                                         or item.get("promise")),
            })
        rows.sort(key=lambda row: row["price"])
        return {"ok": True, "connected": True, "error": "", "items": rows}

    # ================================================== PARA HARCAYAN UÇLAR

    async def purchase(self, shipment_id: int, *, offer_id: str, reason: str, actor: str,
                       dry_run: bool = True) -> dict[str, Any]:
        """ETİKET SATIN ALIR — PARA HARCAR, GERİ ALINAMAZ.

        Gerekçe burada 20 karakter ister; uçtaki şema da öyle. İki yerde
        doğrulanır çünkü istemci şemayı atlatabilir (K9). Yazma ÖNCESİ de
        denetim izine satır atılır: istek zaman aşımına uğrarsa uzakta
        uygulanmış olabilir ve "ne yapmaya çalıştık" kaydı yerelde kalmalı.
        """
        problem = self._guard(reason, shipping.MIN_PURCHASE_REASON)
        if problem:
            return {"ok": False, "error": problem}
        offer = shipping.text(offer_id)
        if not offer:
            return {"ok": False, "error": "Teklif seçilmedi; hangi taşıyıcıdan alınacağı belli değil."}

        await self._record(shipment_id=shipment_id, action="purchase_label", reason=reason,
                           actor=actor, result="denendi", detail={"offerId": offer})
        try:
            result = await self._api.bbd_purchase_shipment(int(shipment_id), offer_id=offer,
                                                           reason=reason, actor=actor,
                                                           dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(shipment_id=shipment_id, action="purchase_label", reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        tracking = shipping.text((result.get("data") or {}).get("tracking_number")
                                 if isinstance(result.get("data"), dict)
                                 else result.get("trackingNumber"))
        fee = shipping.to_kurus(result.get("price")) or 0
        await self._record(shipment_id=shipment_id, action="purchase_label", reason=reason,
                           actor=actor, result="dry_run" if dry_run else "ok",
                           detail={"offerId": offer, "trackingNo": tracking, "fee": fee})
        await self._emit("store.shipment.purchased", {
            "shipmentId": int(shipment_id), "orderId": 0, "carrier": "",
            "trackingNo": tracking, "feeKurus": fee, "dryRun": bool(dry_run)})
        return {"ok": True, "error": "", "trackingNo": tracking, "fee": fee,
                "dryRun": bool(result.get("dryRun", dry_run)),
                "sent": bool(result.get("sent", not dry_run))}

    async def retour(self, shipment_id: int, *, reason: str, actor: str,
                     dry_run: bool = True) -> dict[str, Any]:
        """İade gönderisi açar. BU DA ETİKET ALIR: ayrı izin ve uzun gerekçe
        ister — iade etiketi de faturalanır."""
        problem = self._guard(reason, shipping.MIN_PURCHASE_REASON)
        if problem:
            return {"ok": False, "error": problem}
        await self._record(shipment_id=shipment_id, action="return_shipment", reason=reason,
                           actor=actor, result="denendi")
        try:
            result = await self._api.bbd_return_shipment(int(shipment_id), reason=reason,
                                                         actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(shipment_id=shipment_id, action="return_shipment", reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}
        await self._record(shipment_id=shipment_id, action="return_shipment", reason=reason,
                           actor=actor, result="dry_run" if dry_run else "ok")
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run))}

    async def cancel(self, shipment_id: int, *, reason: str, actor: str,
                     dry_run: bool = True) -> dict[str, Any]:
        """Gönderiyi iptal eder. Taşıyıcı teslim aldıysa iptal reddedilebilir;
        o zaman hata METNİ olduğu gibi gösterilir, başarı gibi sunulmaz."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        await self._record(shipment_id=shipment_id, action="cancel_shipment", reason=reason,
                           actor=actor, result="denendi")
        try:
            result = await self._api.bbd_cancel_shipment(int(shipment_id), reason=reason,
                                                         actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(shipment_id=shipment_id, action="cancel_shipment", reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}
        await self._record(shipment_id=shipment_id, action="cancel_shipment", reason=reason,
                           actor=actor, result="dry_run" if dry_run else "ok")
        await self._emit("store.shipment.cancelled", {
            "shipmentId": int(shipment_id), "orderId": 0, "reason": reason,
            "dryRun": bool(dry_run)})
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run))}

    async def sync(self, shipment_ids: list[int], *, reason: str, actor: str,
                   dry_run: bool = True) -> dict[str, Any]:
        """Taşıyıcıdan durum tazeler. Sırayla: geçit hız kovasını kendisi tutar,
        ama biri patlarsa gerisi devam etmeli."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        ids = [int(item) for item in (shipment_ids or []) if shipping.as_int(item)]
        if not ids:
            return {"ok": False, "error": "Gönderi seçilmedi."}
        if len(ids) > self._batch_limit:
            return {"ok": False,
                    "error": f"Tek seferde en çok {self._batch_limit} gönderi senkronlanır."}

        done = 0
        failed: list[int] = []
        error = ""
        for shipment_id in ids:
            try:
                await self._api.bbd_sync_shipment(shipment_id, reason=reason, actor=actor,
                                                  dry_run=dry_run)
            except Exception as failure:  # noqa: BLE001 — biri patlarsa gerisi sürsün
                failed.append(shipment_id)
                error = error or self._fail(failure)
                continue
            done += 1
        await self._record(action="sync_shipments", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"done": done, "failed": failed})
        return {"ok": not failed, "error": error, "done": done, "failed": failed,
                "dryRun": bool(dry_run)}

    async def notify_customer(self, shipment_id: int, *, template: str, reason: str,
                              actor: str, dry_run: bool = True) -> dict[str, Any]:
        """Müşteriye kargo bildirimi. Bildirim modülü kapalıysa uç KAPALIDIR
        ve bunu söyler; sessizce başarı dönmez."""
        if self._notifier is None:
            return {"ok": False,
                    "error": "Bildirim yeteneği bu kurulumda yok (store_notifications kapalı). "
                             "Ekranda bu düğme gizlenir."}
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        detail = await self.shipment(int(shipment_id))
        if not detail.get("ok"):
            return {"ok": False, "error": detail.get("error", "Gönderi okunamadı.")}
        row = detail["shipment"]
        try:
            result = await self._notifier.send(
                template=shipping.text(template) or "shipment_status",
                to=row["phone"],
                data={"trackingNo": row["trackingNo"], "carrier": row["carrierLabel"],
                      "status": row["statusLabel"], "orderId": row["orderId"]},
                reason=reason, actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        await self._record(shipment_id=shipment_id, order_id=row["orderId"],
                           action="notify_customer", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"template": shipping.text(template)})
        return {"ok": True, "error": "", "dryRun": bool(dry_run),
                "result": result if isinstance(result, dict) else {}}

    # ============================================================ performans

    async def _scan(self, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
        """Rapor/performans için tam tarama — SIRAYLA, tavanlı."""
        payload = await self._api.bbd_shipments(filters, all_pages=True)
        zones = await self._zone_rows()
        today = shipping.today_iso()
        raw = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
        rows = [shipping.shipment_row(item, today=today, idle_limit=self._idle_limit,
                                      zones=zones)
                for item in raw[:SCAN_CAP]]
        return rows, bool(payload.get("truncated")) or len(raw) > SCAN_CAP

    async def performance(self, *, days: int = 0, carrier: str = "") -> dict[str, Any]:
        """Taşıyıcı performansı ve kargo kâr/zararı."""
        window = max(7, min(365, days or shipping.as_int(
            self._config.get("performance_days"), 30)))
        start = _days_ago(window)
        filters: dict[str, Any] = {"channel": self._channel, "date_from": start,
                                   "date_field": "created"}
        if carrier:
            filters["carrier"] = shipping.fold(carrier)

        try:
            rows, truncated = await self._scan(filters)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "days": window, "carriers": [], "delays": [], "money": {}, "late": [],
                    "totals": analytics.totals([]), "truncated": False}

        return {
            "ok": True, "connected": True, "error": "", "days": window, "from": start,
            "carriers": analytics.carrier_performance(rows),
            "delays": analytics.delay_distribution(rows),
            "money": analytics.money_summary(rows),
            "late": analytics.late_rows(rows, limit=25),
            "totals": analytics.totals(rows),
            "truncated": truncated,
        }

    async def audit(self, *, shipment_id: int = 0, limit: int = 50) -> dict[str, Any]:
        """Bu ekrandan yapılan yazmaların YEREL izi (gerekçeleriyle)."""
        sql = (f"SELECT shipment_id, order_id, action, reason, actor, result, created_at "
               f"FROM {self._audit} ")
        params: tuple[Any, ...] = ()
        if shipment_id:
            sql += "WHERE shipment_id = ? "
            params = (int(shipment_id),)
        sql += "ORDER BY id DESC LIMIT ?"
        params = (*params, max(1, min(500, int(limit))))
        try:
            rows = await self._store.fetch_all(sql, params)
        except Exception as failure:  # noqa: BLE001 — iz okunamadı, ekran dursun
            return {"ok": True, "items": [], "error": self._fail(failure)}
        return {"ok": True, "error": "", "items": [
            {"shipmentId": row["shipment_id"], "orderId": row["order_id"],
             "action": row["action"], "reason": row["reason"], "actor": row["actor"],
             "result": row["result"], "createdAt": row["created_at"]}
            for row in rows
        ]}

    # ================================================================ CSV

    async def export_csv(self, *, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """Tüm gönderilerin CSV'si. Görünen sayfanın CSV'sini panel kendi üretir."""
        base = {"channel": self._channel, **(filters or {})}
        try:
            rows, truncated = await self._scan(base)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}

        headers = ["Takip no", "Sipariş", "Müşteri", "Şehir", "İlçe", "Taşıyıcı", "Desi",
                   "Kg", "Ücret", "Ödeme", "Kapıda ödeme", "Oluşturma", "Son hareket",
                   "Geçen gün", "Durum"]
        table = [[row["trackingNo"], row["orderNumber"], row["customer"], row["city"],
                  row["district"], row["carrierLabel"], row["desi"], row["weight"],
                  money(row["fee"]), row["payerLabel"],
                  money(row["cod"]) if row["cod"] else "", row["createdAt"],
                  row["lastMoveAt"],
                  "" if row["ageDays"] is None else row["ageDays"], row["statusLabel"]]
                 for row in rows]

        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        name = f"magaza-kargo-listesi-{stamp}.csv"
        try:
            path = write_private(self._export_dir / name, csv_bytes(headers, table))
        except OSError as failure:
            return {"ok": False, "error": f"Dosya yazılamadı: {failure}"}
        return {"ok": True, "error": "", "path": str(path), "name": name,
                "rows": len(table), "truncated": truncated}

    # =============================================================== rapor

    async def preview(self, kind: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Belgeyi üretir ve sayfalarını görüntü olarak döner (kit `reportChain`)."""
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
            return {"ok": False, "error": f"Bilinmeyen belge: {kind}"}
        if kind == "labels":
            return await self._build_labels(params)
        if kind == "manifest":
            return await self._build_manifest(params)
        if kind == "receipt":
            return await self._build_receipt(params)
        return await self._build_performance(params)

    # -------------------------------------------------------------- fiş

    async def _build_receipt(self, params: dict[str, Any]) -> dict[str, Any]:
        """Kargo fişi — TEST gönderisinin kâğıt karşılığı.

        NEDEN AYRI BİR BELGE: gerçek gönderinin etiketi TAŞIYICIDAN gelir
        (`_build_labels`). Test yolunda taşıyıcı yoktur, dolayısıyla etiket de
        yoktur; fiş bu boşluğu doldurur — akışın tamamı (paketle, yapıştır,
        çıkışa koy) para harcamadan denenebilsin diye.

        FİŞ ETİKET DEĞİLDİR ve öyle görünmemelidir: üstünde büyük "TEST
        GÖNDERİSİ" bandı ve "bu fişle kargo verilemez" satırı taşır. Gerçek
        etiketin taklidi basılsaydı, bir gün biri onu koliye yapıştırıp
        kargoya verirdi.
        """
        order_id = shipping.as_int(params.get("orderId"))
        track = shipping.text(params.get("trackNumber"))
        if not order_id and not track:
            return {"ok": False, "error": "Fişi basılacak gönderi belirtilmedi."}

        try:
            order = await self._api.order(int(order_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}

        address = shipping.shipping_address(order)
        alici = shipping.customer_name(order, address)
        kalemler = [
            [shipping.text(item.get("sku")),
             shipping.text(item.get("name")) or "—",
             number(shipping.as_int(item.get("qty_ordered") or item.get("qtyOrdered")))]
            for item in (order.get("items") or []) if isinstance(item, dict)
        ]
        siparis_no = shipping.text(order.get("increment_id") or order.get("incrementId"))

        sections: list[dict[str, Any]] = [
            {"kind": "note",
             "text": "TEST GÖNDERİSİ — taşıyıcıya çıkmadı, etiket satın alınmadı, "
                     "para harcanmadı. BU FİŞLE KARGO VERİLEMEZ."},
            {"kind": "tiles", "title": "Gönderi",
             "tiles": [("Takip no", track or "—"),
                       ("Sipariş", siparis_no or str(order_id)),
                       ("Taşıyıcı", shipping.text(params.get("carrier")) or "BBD Test Kargo"),
                       ("Tarih", shipping.today_iso())]},
            {"kind": "table", "title": "Alıcı",
             "headers": ["Alan", "Değer"], "align": "LL", "widths": [1.2, 4.0],
             "rows": [["Ad soyad", alici or "—"],
                      ["Şehir / İlçe",
                       f"{address.get('city', '')} / {address.get('district', '')}".strip(" /")
                       or "—"],
                      ["Telefon", shipping.mask(address.get("phone"))]]},
        ]
        if kalemler:
            sections.append({"kind": "table", "title": "Kalemler",
                             "headers": ["SKU", "Ürün", "Adet"],
                             "align": "LLR", "widths": [1.4, 3.6, 0.8],
                             "rows": kalemler})

        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        name = f"magaza-kargo-fis-{track or order_id}-{stamp}.pdf"
        try:
            content = build_pdf(
                title="Kargo fişi (TEST)",
                subtitle=f"{track or '—'} · Sipariş {siparis_no or order_id}",
                sections=sections, footer="Kontrol Merkezi · Mağaza · Kargo")
            path = write_private(self._export_dir / name, content)
        except (OSError, ExportError) as failure:
            return {"ok": False, "error": f"Fiş üretilemedi: {failure}"}
        return {"ok": True, "error": "", "path": str(path), "name": name,
                "test": True, "trackNumber": track}

    # ------------------------------------------------------------- etiket

    async def _build_labels(self, params: dict[str, Any]) -> dict[str, Any]:
        """Toplu etiket sayfası. Etiketlerin KENDİSİ taşıyıcıdan gelir.

        Bir gönderinin etiketi okunamazsa iş DURMAZ ama sessiz de kalmaz:
        alınabilenler birleştirilir, alınamayanların takip numarası döner ve
        ekran "şu üçü elde basılmalı" der. Eksik etiketi atlayıp "hazır"
        demek, kargoya etiketsiz paket çıkmasına yol açardı.
        """
        wanted = [int(item) for item in (params.get("shipmentIds") or [])
                  if shipping.as_int(item)]
        if not wanted:
            return {"ok": False, "error": "Etiketi alınacak gönderi seçilmedi."}
        if len(wanted) > self._batch_limit:
            return {"ok": False,
                    "error": f"Tek seferde en çok {self._batch_limit} etiket birleştirilir; "
                             "seçimi bölün."}

        fmt = shipping.text(params.get("format")) or self._default_format
        if fmt not in labels.FORMATS:
            return {"ok": False, "error": f"Bilinmeyen etiket biçimi: {fmt}"}

        content: list[bytes] = []
        missing: list[int] = []
        for shipment_id in wanted:
            try:
                raw = await self._api.bbd_shipment_label(shipment_id)
            except Exception as failure:  # noqa: BLE001 — biri yoksa gerisi sürsün
                missing.append(shipment_id)
                self._log.warning("etiket alınamadı", shipmentId=shipment_id,
                                  error=str(failure))
                continue
            if raw:
                content.append(bytes(raw))
            else:
                missing.append(shipment_id)

        if not content:
            return {"ok": False,
                    "error": "Hiçbir etiket alınamadı. Etiket ancak satın alındıktan sonra "
                             "oluşur; önce teklif seçip etiketi satın alın."}

        try:
            merged = labels.compose(content, fmt)
        except labels.LabelError as failure:
            return {"ok": False, "error": str(failure), "missing": missing}

        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        name = f"magaza-kargo-etiket-{fmt}-{stamp}.pdf"
        try:
            path = write_private(self._export_dir / name, merged)
        except OSError as failure:
            return {"ok": False, "error": f"Dosya yazılamadı: {failure}"}

        await self._record(action="build_labels", result="ok",
                           detail={"count": len(content), "format": fmt, "missing": missing})
        return {"ok": True, "error": "", "path": str(path), "name": name,
                "bytes": len(merged), "count": len(content), "missing": missing,
                "format": fmt, "formatLabel": labels.FORMAT_LABELS[fmt]}

    # -------------------------------------------------------- manifesto

    async def _build_manifest(self, params: dict[str, Any]) -> dict[str, Any]:
        """Teslimat manifestosu — şoföre imzalatılan liste.

        İmza sütunu BOŞ bırakılır ve satır yüksekliği elle imza atılacak kadar
        geniştir; bu bir kayıt değil, KAĞIT ÜZERİNDE delildir.
        """
        wanted = [int(item) for item in (params.get("shipmentIds") or [])
                  if shipping.as_int(item)]
        if not wanted:
            return {"ok": False, "error": "Manifestoya girecek gönderi seçilmedi."}

        rows: list[dict[str, Any]] = []
        missing: list[int] = []
        zones = await self._zone_rows()
        today = shipping.today_iso()
        for shipment_id in wanted:
            try:
                raw = await self._api.bbd_shipment(shipment_id)
            except Exception as failure:  # noqa: BLE001 — K7
                missing.append(shipment_id)
                self._log.warning("manifesto için gönderi okunamadı", shipmentId=shipment_id,
                                  error=str(failure))
                continue
            rows.append(shipping.shipment_row(raw, today=today, idle_limit=self._idle_limit,
                                              zones=zones))
        if not rows:
            return {"ok": False, "error": "Seçilen gönderilerin hiçbiri okunamadı."}

        carrier = shipping.fold(params.get("carrier")) or (rows[0]["carrier"] if rows else "")
        driver = shipping.text(params.get("driver"))
        total_desi = sum(row["units"] for row in rows)
        total_cod = sum(row["cod"] for row in rows)

        sections: list[dict[str, Any]] = [
            {"kind": "tiles", "title": "Özet",
             "tiles": [("Gönderi", number(len(rows))), ("Toplam desi", number(total_desi)),
                       ("Kapıda ödeme", money(total_cod)),
                       ("Taşıyıcı", shipping.carrier_label(carrier))]},
            {"kind": "table", "title": "Teslim edilen gönderiler",
             "headers": ["#", "Takip no", "Sipariş", "Alıcı", "Şehir/İlçe", "Desi",
                         "Kapıda ödeme", "İmza"],
             "align": "LLLLLRRL", "widths": [0.4, 1.6, 1, 2, 1.6, 0.6, 1, 1.6],
             "rows": [[str(index), row["trackingNo"] or "—", row["orderNumber"],
                       row["customer"], f"{row['city']} / {row['district']}".strip(" /"),
                       number(row["units"]), money(row["cod"]) if row["cod"] else "—", ""]
                      for index, row in enumerate(rows, start=1)]},
            {"kind": "note",
             "text": f"Teslim alan: {driver or '____________________'}     "
                     "İmza: ____________________     Tarih/Saat: ____________________"},
        ]
        if missing:
            sections.append({"kind": "note",
                             "text": f"Okunamayan gönderi: {len(missing)} adet — listeye "
                                     "GİRMEDİ, elle kontrol edin."})

        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        name = f"magaza-kargo-manifesto-{stamp}.pdf"
        try:
            # `build_pdf` de ExportError fırlatabilir (yazı tipi/uzunluk);
            # dışarı sızarsa uç 500 verir ve ekran çöker (K7).
            content = build_pdf(
                title="Teslimat manifestosu",
                subtitle=f"{shipping.carrier_label(carrier)} · {len(rows)} gönderi · {today}",
                sections=sections, footer="Kontrol Merkezi · Mağaza · Kargo")
            path = write_private(self._export_dir / name, content)
        except (OSError, ExportError) as failure:
            return {"ok": False, "error": f"Manifesto üretilemedi: {failure}"}

        try:
            await self._store.execute(
                f"INSERT INTO {self._manifests} "
                "(name, path, carrier, shipments, count, actor, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, str(path), carrier,
                 json.dumps([row["trackingNo"] for row in rows], ensure_ascii=False),
                 len(rows), driver, _now()),
            )
        except Exception as failure:  # noqa: BLE001 — kayıt yazılamadı, belge yine üretildi
            self._log.warning("manifesto kaydı yazılamadı", error=str(failure))

        return {"ok": True, "error": "", "path": str(path), "name": name,
                "bytes": len(content), "count": len(rows), "missing": missing}

    # --------------------------------------------------------- performans

    async def _build_performance(self, params: dict[str, Any]) -> dict[str, Any]:
        payload = await self.performance(days=shipping.as_int(params.get("days")),
                                         carrier=shipping.text(params.get("carrier")))
        if not payload.get("connected"):
            return {"ok": False, "error": payload.get("error") or "Mağazaya ulaşılamadı."}
        rows = payload["carriers"]
        if not rows:
            return {"ok": False, "error": "Bu pencerede rapora girecek gönderi yok."}

        summary = payload["money"]
        sections: list[dict[str, Any]] = [
            {"kind": "tiles", "title": "Özet",
             "tiles": [("Gönderi", number(payload["totals"]["total"])),
                       ("Teslim", number(payload["totals"]["delivered"])),
                       ("Geciken", number(payload["totals"]["late"])),
                       ("Kargo maliyeti", money(summary["cost"])),
                       ("Tahsil edilen", money(summary["collected"])),
                       ("Fark", money(summary["margin"]))]},
            {"kind": "table", "title": "Taşıyıcı performansı",
             "headers": ["Taşıyıcı", "Gönderi", "Teslim", "Teslim edilemedi", "Ort. gün",
                         "Maliyet", "Tahsil", "Fark"],
             "align": "LRRRRRRR", "widths": [2, 0.8, 0.8, 1.1, 0.9, 1.2, 1.2, 1.2],
             "rows": [[row["label"], number(row["count"]), number(row["delivered"]),
                       f"{number(row['undelivered'])}"
                       + (f" (%{row['failRate']})" if row["failRate"] is not None else ""),
                       "—" if row["avgDays"] is None else f"{row['avgDays']}",
                       money(row["cost"]), money(row["collected"]), money(row["margin"])]
                      for row in rows]},
            {"kind": "bars", "title": "Teslim süresi dağılımı",
             "bars": [(item["label"], item["count"], number(item["count"]))
                      for item in payload["delays"]]},
        ]
        if summary["missingCollected"]:
            sections.append({"kind": "note",
                             "text": f"{summary['missingCollected']} gönderide müşteriden "
                                     "tahsil edilen kargo bedeli okunamadı; kâr/zarar bu "
                                     "kadar eksik hesaplandı."})
        if payload["truncated"]:
            sections.append({"kind": "note", "text": "Liste tavana dayandı; eksik olabilir."})

        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        name = f"magaza-kargo-performans-{stamp}.pdf"
        try:
            content = build_pdf(
                title="Taşıyıcı performans raporu",
                subtitle=f"Son {payload['days']} gün · {payload['from']} sonrası",
                sections=sections, footer="Kontrol Merkezi · Mağaza · Kargo")
            path = write_private(self._export_dir / name, content)
        except (OSError, ExportError) as failure:
            return {"ok": False, "error": f"Rapor üretilemedi: {failure}"}
        return {"ok": True, "error": "", "path": str(path), "name": name,
                "bytes": len(content), "count": payload["totals"]["total"]}

    # ---------------------------------------------------- önizleme/baskı

    async def _render_pages(self, path: Path, *, max_pages: int = 12,
                            dpi: int = 110) -> list[str]:
        binary = shutil.which("pdftoppm")
        if not binary:
            raise PreviewError(
                "Önizleme üretilemedi: `pdftoppm` yok (poppler-utils kurulmalı). "
                "Belge yine de kaydedildi ve yazdırılabilir.")
        with tempfile.TemporaryDirectory(prefix="km-kargo-onizleme-") as folder:
            target = Path(folder) / "sayfa"
            try:
                process = await asyncio.create_subprocess_exec(
                    binary, "-png", "-r", str(int(dpi)), "-f", "1", "-l", str(int(max_pages)),
                    str(path), str(target),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                _, err = await asyncio.wait_for(process.communicate(), timeout=60)
            except TimeoutError:
                # Süre dolunca süreç ÖLDÜRÜLÜR: `wait_for` yalnız beklemeyi
                # bırakır, `pdftoppm` arkada çalışmaya ve geçici klasör
                # silinirken diske yazmaya devam ederdi.
                process.kill()
                await process.wait()
                raise PreviewError("Önizleme üretilemedi: süre aşıldı.") from None
            except OSError as failure:
                raise PreviewError(f"Önizleme üretilemedi: {failure}") from failure
            if process.returncode != 0:
                raise PreviewError(
                    f"Önizleme üretilemedi: {err.decode(errors='replace').strip()}")
            return ["data:image/png;base64," + base64.b64encode(item.read_bytes()).decode("ascii")
                    for item in sorted(Path(folder).glob("sayfa*.png"))]

    async def print_report(self, path: str, *, copies: int = 1) -> dict[str, Any]:
        """Üretilmiş belgeyi yazıcıya gönderir.

        GÜVENLİK: yalnız BİZİM rapor klasörümüzdeki dosya basılabilir. Serbest
        yol kabul etmek, `lp` ile makinedeki herhangi bir dosyayı kâğıda
        döktürmeye açık kapı bırakırdı.
        """
        if self._printer is None:
            return {"ok": False, "error": "Yazıcı yeteneği bu kurulumda yok."}
        try:
            resolved = Path(path).expanduser().resolve(strict=True)
        except OSError:
            return {"ok": False, "error": "Basılacak belge bulunamadı."}
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

    # ============================================================ tercihler

    async def settings(self) -> dict[str, Any]:
        """Bu ekranın tercihleri. `store_settings` ekranı yoktur: kargo ücreti
        ve desi burada, vergi `store_tax`'ta, stok `store_products`'ta durur."""
        return {
            "ok": True, "error": "",
            "labelFormat": (await self._pref("label_format")) or self._default_format,
            "defaultCarrier": await self._pref("default_carrier"),
            "idleDays": shipping.as_int(await self._pref("idle_days"), self._idle_limit),
            "divisor": self._divisor,
            "formats": [{"value": key, "label": labels.FORMAT_LABELS[key]}
                        for key in labels.FORMATS],
            "labelLibrary": _pypdf_state(),
            "batchLimit": self._batch_limit,
        }

    async def save_settings(self, *, label_format: str = "", default_carrier: str = "",
                            idle_days: int | None = None, reason: str,
                            actor: str) -> dict[str, Any]:
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        changed: list[str] = []
        refused: list[str] = []

        def note(label: str, written: bool) -> None:
            (changed if written else refused).append(label)

        if label_format:
            if label_format not in labels.FORMATS:
                return {"ok": False, "error": f"Bilinmeyen etiket biçimi: {label_format}"}
            note("etiket biçimi", await self._set_pref("label_format", label_format, actor))
        if default_carrier:
            note("varsayılan taşıyıcı",
                 await self._set_pref("default_carrier", shipping.fold(default_carrier), actor))
        if idle_days is not None:
            note("gecikme eşiği",
                 await self._set_pref("idle_days", str(max(1, min(30, int(idle_days)))), actor))

        await self._record(action="save_settings", reason=reason, actor=actor,
                           result="hata" if refused else "ok",
                           detail={"changed": changed, "refused": refused})
        if refused:
            # Yazılamayan tercihi "kaydedildi" diye göstermek, ayarı değiştirdiğini
            # sanan kullanıcıyı bir dahaki açılışta eski değerle karşılaştırır.
            return {"ok": False, "changed": changed,
                    "error": f"Şu tercihler yazılamadı: {', '.join(refused)}."}
        return {"ok": True, "error": "", "changed": changed}


def _days_ago(days: int) -> str:
    """`days` gün önceki YEREL takvim günü.

    `timedelta` yerine ordinal aritmetiği: yaz saati geçişinde `now - 30 gün`
    bazı saatlerde bir gün kayıyor, takvim günü kaymamalı.
    """
    today = datetime.now(UTC).astimezone().date()
    return date.fromordinal(today.toordinal() - max(0, int(days))).isoformat()


def _pypdf_state() -> dict[str, Any]:
    """Toplu etiket için gereken kütüphane kurulu mu — ekran ÖNCEDEN söyler."""
    try:
        import pypdf  # noqa: F401 - varlık yoklaması
    except ImportError:
        return {"ready": False, "message": labels.MISSING_LIBRARY}
    return {"ready": True, "message": ""}
