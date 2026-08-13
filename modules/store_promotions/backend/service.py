"""Promosyonlar — iş kuralları.

VERİ MAĞAZADADIR, KARAR BURADADIR. Kural, koşul, kupon ve kullanım sayacı
`store.api` geçidinden gelir (K4); bu modül onların kopyasını tutmaz. Yerel
tablolar yalnız mağazada KARŞILIĞI OLMAYAN iki şeyi saklar: yazma gerekçesi
(denetim izi) ve üretilen kupon partisi (hangi önekle, kaç adet, kim, neden).

UZAK SİSTEM DÜŞERSE EKRAN AYAKTA KALIR (K7): `connected: False` + `error`
döner, panel durumu anlatır. İstisna dışarı sızmaz.

İKİ AYRI YAZMA İZNİ. Kuralı DÜZENLEMEK (`manage`) ile YAYINA ALMAK
(`activate`) ayrı tutulur: taslak kimseye para kaybettirmez, yayındaki kural
her siparişte indirim düşer. Yeni kural her zaman taslak açılır.
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

from . import analytics, promotions

#: Simülasyon ve performans için çekilen kural sayısı tavanı. Mağazada
#: kampanya sayısı onlarla ölçülür; tavan bozuk `meta` yüzünden sonsuz
#: taramayı engeller.
RULE_SCAN_CAP = 500

#: Ödeme yöntemlerinin durduğu ayar bölümü. Bulunamazsa ekran ödeme koşulunu
#: serbest metin olarak sorar — açılırı boş göstermek yerine.
PAYMENT_SLUG = "sales.payment_methods"

#: Kupon listesi taranırken kullanılan sayfa boyu. `per_page=200` istemek
#: İŞE YARAMAZ: geçit sunucunun `MAX_PER_PAGE` sınırına (50) kırpıyor. 200
#: yazmak "4.000 kod tarıyorum" sanısı üretiyordu; gerçek 1.000'di.
COUPON_PAGE_SIZE = 50

#: Taranacak en çok sayfa. 60 × 50 = 3.000 kod. Tavan hem bozuk `meta` yüzünden
#: sonsuz döngüyü hem de hız kovasını (dakikada 55 istek) korur.
COUPON_PAGE_CAP = 60

#: Simülasyonda TEKİL okunacak en çok kural. Mağazanın kural LİSTESİ `conditions`,
#: `channels` ve `customerGroups` alanlarını `null` veriyor; bunlar yalnız tekil
#: kayıtta dolu geliyor. Listeye bakıp "koşul yok" demek, "750 TL üzeri" kuralını
#: 1 TL'lik sepete uygulamak olurdu — sessiz ve yanlış. Bu yüzden yalnız AKTİF
#: kurallar tekil okunur; tavan hız kovasını (dakikada 55) korur.
SIMULATE_DETAIL_CAP = 40

#: Performansta indirim tutarı için tekil okunacak en çok sipariş. Sipariş
#: LİSTESİ `discountAmount` taşımıyor (yalnız detayda var); kupon kullanılan
#: siparişler tek tek okunur. Tavan aşılırsa indirim "eksik" işaretlenir ve
#: ekran oranı "—" gösterir — sıfır yazmak kampanyayı bedava sanmaktır.
ORDER_DETAIL_CAP = 250


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


class PreviewError(RuntimeError):
    """Önizleme görüntüsü üretilemedi. Rapor yine de kaydedilmiştir."""


class PromotionsService:
    """Promosyon ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ."""

    def __init__(self, *, api: Any, store: Any, log: Any, config: dict[str, Any],
                 printer: Any = None, publish: Any = None, category: str = "Mağaza",
                 subcategory: str = "Satış", fallback_dir: Path | None = None) -> None:
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
        self._batches = store.table("batches")

    # ------------------------------------------------------------- ayarlar

    @property
    def _channel(self) -> str:
        return str(self._config.get("channel") or "default")

    @property
    def _locale(self) -> str:
        return str(self._config.get("locale") or "tr")

    @property
    def _page_size(self) -> int:
        return max(10, min(200, promotions.as_int(self._config.get("page_size"), 50)))

    @property
    def _expiring_days(self) -> int:
        return max(1, min(90, promotions.as_int(self._config.get("expiring_days"), 7)))

    @property
    def _order_cap(self) -> int:
        return max(100, min(20_000, promotions.as_int(
            self._config.get("performance_order_cap"), 2000)))

    @property
    def _export_dir(self) -> Path:
        # HER ÇAĞRIDA yeniden çözülür: ay değişince klasör kendiliğinden değişir.
        return report_dir(self._category, subcategory=self._subcategory,
                          fallback=self._fallback,
                          configured=str(self._config.get("export_path") or ""))

    # ------------------------------------------------------ yerel tablolar

    async def _record(self, *, rule_id: int, scope: str, action: str, reason: str, actor: str,
                      result: str, detail: Any = None) -> None:
        """Yerel denetim izi. Bagisto denetim tutuyor ama GEREKÇEYİ tutmuyor;
        ayrıca ağ koparsa "ne yapmaya çalıştık" kaydı burada kalır."""
        try:
            await self._store.execute(
                f"INSERT INTO {self._audit} "
                "(rule_id, scope, action, reason, actor, result, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (int(rule_id or 0), scope, action, reason, actor, result,
                 json.dumps(detail or {}, ensure_ascii=False), _now()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın
            self._log.warning("denetim izi yazılamadı", action=action, error=str(failure))

    async def _announce(self, action: str, rule_id: int, scope: str) -> None:
        """Olay veri yolu (K3). Yük KİMLİK taşır, kural içeriği taşımaz:
        dinleyen modül veriyi kendi izniyle geçitten okur."""
        if self._publish is None:
            return
        try:
            await self._publish("store.promotion.changed",
                                {"ruleId": int(rule_id or 0), "scope": scope, "action": action})
        except Exception as failure:  # noqa: BLE001 — olay yayını işi düşürmez
            self._log.warning("olay yayınlanamadı", action=action, error=str(failure))

    # ------------------------------------------------------------ yardımcı

    @staticmethod
    def _fail(failure: Exception) -> str:
        message = str(failure).strip()
        return message or "Mağazaya ulaşılamadı."

    @staticmethod
    def _guard(reason: str) -> str:
        """Gerekçe backend'de DE doğrulanır (K9): arayüzde gizlemek yetmez."""
        return promotions.reason_error(reason)

    @staticmethod
    def _sent(result: Any, dry_run: bool) -> dict[str, Any]:
        payload = result if isinstance(result, dict) else {}
        return {"dryRun": bool(payload.get("dryRun", dry_run)),
                "sent": bool(payload.get("sent", not dry_run))}

    # ================================================== sepet kuralı — liste

    async def rules(self, *, q: str = "", status: str = "", kind: str = "", chip: str = "",
                    channel_id: int = 0, group_id: int = 0, page: int = 1,
                    size: int = 0) -> dict[str, Any]:
        """Sepet kuralları.

        SÜZME İSTEMCİDE YAPILIR — ve bu bilinçlidir. Mağazanın kural listesi ucu
        yalnız sayfalıyor, `status`/`action_type` süzgeci yok; kural sayısı ise
        onlarla ölçülüyor (1.419 ürünlük katalogla karıştırılmamalı). Tamamı bir
        kez çekilip burada süzülür; böylece "şu an aktif" gibi TAKVİME bağlı
        süzgeçler de doğru çalışır — sunucu onları zaten bilemezdi.
        """
        per_page = size or self._page_size
        today = promotions.today_iso()
        try:
            payload = await self._api.cart_rules(all_pages=True)
            connected, error = True, ""
        except Exception as failure:  # noqa: BLE001 — ekran ayakta kalmalı (K7)
            self._log.warning("sepet kuralları okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "items": [], "total": 0, "page": page, "size": per_page, "pages": 0,
                    "counts": {}}

        rows = [promotions.cart_rule_row(item, today=today, expiring_days=self._expiring_days)
                for item in (payload.get("items") or [])[:RULE_SCAN_CAP]
                if isinstance(item, dict)]
        counts = self._counts(rows)
        picked = self._filter(rows, q=q, status=status, kind=kind, chip=chip,
                              channel_id=channel_id, group_id=group_id)
        total = len(picked)
        pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, pages))
        window = picked[(page - 1) * per_page: page * per_page]
        # Kanal/grup süzgeci ancak liste bu alanları TAŞIYORSA çalışır; canlıda
        # taşımıyor. Ekran bunu bilmeli, yoksa seçim yapan kullanıcı hiçbir şey
        # değişmediğini görür ve süzgecin bozuk olduğunu anlamaz.
        scope_known = any(row["scopeKnown"] for row in rows)
        return {"ok": True, "connected": connected, "error": error, "items": window,
                "total": total, "page": page, "size": per_page, "pages": pages,
                "counts": counts, "scopeFilterable": scope_known}

    @staticmethod
    def _counts(rows: list[dict[str, Any]]) -> dict[str, int]:
        return {
            "active": sum(1 for row in rows if row["status"] == "active"),
            "scheduled": sum(1 for row in rows if row["status"] == "scheduled"),
            "paused": sum(1 for row in rows if row["status"] == "paused"),
            "expired": sum(1 for row in rows if row["status"] == "expired"),
            "neverUsed": sum(1 for row in rows if row["neverUsed"]),
            "exhausted": sum(1 for row in rows if row["usage"]["exhausted"]),
            "expiring": sum(1 for row in rows if row["expiringSoon"]),
            "all": len(rows),
        }

    @staticmethod
    def _filter(rows: list[dict[str, Any]], *, q: str, status: str, kind: str, chip: str,
                channel_id: int, group_id: int) -> list[dict[str, Any]]:
        needle = promotions.text(q).casefold()
        out = []
        for row in rows:
            if needle and needle not in (row["name"] + " " + row["code"]).casefold():
                continue
            if status and row["status"] != status:
                continue
            if kind and row["actionType"] != kind:
                continue
            # Kapsam BİLİNMİYORSA elenmez: liste ucu kanal/grup vermiyor ve
            # bilinmeyeni "eşleşmedi" saymak kuralları sessizce yok ederdi.
            if channel_id and row["scopeKnown"] and channel_id not in row["channels"]:
                continue
            if group_id and row["scopeKnown"] and group_id not in row["customerGroups"]:
                continue
            if chip == "active" and row["status"] != "active":
                continue
            if chip == "neverUsed" and not row["neverUsed"]:
                continue
            if chip == "exhausted" and not row["usage"]["exhausted"]:
                continue
            if chip == "expiring" and not row["expiringSoon"]:
                continue
            out.append(row)
        return out

    async def rule(self, rule_id: int) -> dict[str, Any]:
        """Kural künyesi + koşullar + kuponlar. Parça parça hata (K7)."""
        today = promotions.today_iso()
        try:
            raw = await self._api.cart_rule(int(rule_id))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("kural okunamadı", ruleId=rule_id, error=str(failure))
            return {"ok": False, "connected": False, "error": self._fail(failure)}

        warnings: list[str] = []
        coupons: list[dict[str, Any]] = []
        if promotions.as_int(promotions.pick(raw, "coupon_type")) == 1:
            try:
                payload = await self._api.coupons(int(rule_id), per_page=self._page_size)
                coupons = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
            except Exception as failure:  # noqa: BLE001 — kupon gelmezse künye yine dolar
                warnings.append(f"kuponlar: {self._fail(failure)}")

        detail = promotions.cart_rule_detail(raw, coupons=coupons, today=today)
        return {"ok": True, "connected": True, "error": "", "warnings": warnings,
                "rule": detail}

    # ================================================ sepet kuralı — yazma

    async def save_rule(self, rule_id: int, *, patch: dict[str, Any], reason: str, actor: str,
                        dry_run: bool = True) -> dict[str, Any]:
        """Kuralı günceller — OKU-DEĞİŞTİR-YAZ.

        Yazmadan önce kural TAZE okunur: aradan geçen sürede mağaza yöneticisi
        aynı kurala dokunmuş olabilir ve dokunmadığımız alanlar (bizim
        tanımadığımız koşullar dahil) onun değeriyle geri gider.

        DURUM BU UÇTAN DEĞİŞMEZ: yayına alma/durdurma ayrı izin ister.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        clean = dict(patch or {})
        clean.pop("status", None)

        broken = promotions.condition_error(clean.get("conditions"))
        if broken:
            return {"ok": False, "error": broken}

        try:
            current = await self._api.cart_rule(int(rule_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}

        body = promotions.write_rule_body(current, clean)
        invalid = promotions.rule_error(body)
        if invalid:
            return {"ok": False, "error": invalid}

        await self._record(rule_id=rule_id, scope="cart", action="update_cart_rule",
                           reason=reason, actor=actor, result="denendi",
                           detail={"fields": sorted(clean)})
        try:
            result = await self._api.save_cart_rule(payload=body, rule_id=int(rule_id),
                                                    reason=reason, actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(rule_id=rule_id, scope="cart", action="update_cart_rule",
                               reason=reason, actor=actor, result="hata",
                               detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(rule_id=rule_id, scope="cart", action="update_cart_rule",
                           reason=reason, actor=actor, result="dry_run" if dry_run else "ok",
                           detail={"fields": sorted(clean)})
        await self._announce("update", rule_id, "cart")
        return {"ok": True, "error": "", **self._sent(result, dry_run),
                "fields": sorted(clean),
                "maxDiscountSkipped": bool(
                    isinstance(clean.get("action"), dict)
                    and "maxDiscount" in clean["action"]
                    and not promotions.field_supported(current, promotions.MAX_DISCOUNT_KEY))}

    async def create_rule(self, *, patch: dict[str, Any], reason: str, actor: str,
                          dry_run: bool = True) -> dict[str, Any]:
        """Yeni kural — HER ZAMAN TASLAK. Yayına almak ayrı izin ister."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        broken = promotions.condition_error((patch or {}).get("conditions"))
        if broken:
            return {"ok": False, "error": broken}

        channels, groups = await self._defaults()
        body = promotions.new_rule_body(patch or {}, channel_ids=channels, group_ids=groups)
        invalid = promotions.rule_error(body)
        if invalid:
            return {"ok": False, "error": invalid}

        await self._record(rule_id=0, scope="cart", action="create_cart_rule", reason=reason,
                           actor=actor, result="denendi", detail={"name": body.get("name")})
        try:
            result = await self._api.save_cart_rule(payload=body, reason=reason, actor=actor,
                                                    dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(rule_id=0, scope="cart", action="create_cart_rule", reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        new_id = self._new_id(result)
        await self._record(rule_id=new_id, scope="cart", action="create_cart_rule", reason=reason,
                           actor=actor, result="dry_run" if dry_run else "ok",
                           detail={"name": body.get("name")})
        await self._announce("create", new_id, "cart")
        return {"ok": True, "error": "", "id": new_id, **self._sent(result, dry_run),
                "notice": "Kural TASLAK açıldı; vitrinde indirim yapmaz. Yayına almak "
                          "ayrı yetki ister."}

    @staticmethod
    def _new_id(result: Any) -> int:
        if not isinstance(result, dict):
            return 0
        data = result.get("data")
        if isinstance(data, dict):
            return promotions.as_int(data.get("id"))
        return promotions.as_int(result.get("id"))

    async def set_rule_status(self, rule_id: int, *, active: bool, scope: str = "cart",
                              reason: str, actor: str, dry_run: bool = True) -> dict[str, Any]:
        """Yayına alma / durdurma — PARAYI ETKİLEYEN İŞLEM, ayrı izinle gelir.

        Durum toplu uçtan değil kuralın kendi kaydından yazılır; Bagisto'nun
        kural listesinde toplu durum ucu yok ve kural sayısı zaten az.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        catalog = scope == "catalog"
        reader = self._api.catalog_rule if catalog else self._api.cart_rule
        writer = self._api.save_catalog_rule if catalog else self._api.save_cart_rule
        try:
            current = await reader(int(rule_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}

        builder = promotions.write_catalog_body if catalog else promotions.write_rule_body
        body = builder(current, {"status": bool(active)})
        action = "activate" if active else "pause"
        await self._record(rule_id=rule_id, scope=scope, action=action, reason=reason,
                           actor=actor, result="denendi")
        try:
            result = await writer(payload=body, rule_id=int(rule_id), reason=reason, actor=actor,
                                  dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(rule_id=rule_id, scope=scope, action=action, reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(rule_id=rule_id, scope=scope, action=action, reason=reason,
                           actor=actor, result="dry_run" if dry_run else "ok")
        await self._announce(action, rule_id, scope)
        notice = ""
        if active:
            start = promotions.day(body.get("starts_from"))
            end = promotions.day(body.get("ends_till"))
            today = promotions.today_iso()
            if start and today < start:
                notice = f"Kural açıldı ama {start} tarihine kadar indirim yapmaz."
            elif end and today > end:
                notice = f"Kural açıldı ama bitiş tarihi ({end}) geçmiş; indirim yapmaz."
        return {"ok": True, "error": "", **self._sent(result, dry_run), "notice": notice}

    async def copy_rule(self, rule_id: int, *, reason: str, actor: str,
                        dry_run: bool = True) -> dict[str, Any]:
        """Kuralı kopyalar. Kopya mağaza tarafında PASİF açılır."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        try:
            result = await self._api.copy_cart_rule(int(rule_id), reason=reason, actor=actor,
                                                    dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        await self._record(rule_id=rule_id, scope="cart", action="copy_cart_rule", reason=reason,
                           actor=actor, result="dry_run" if dry_run else "ok")
        return {"ok": True, "error": "", **self._sent(result, dry_run)}

    # ============================================================== katalog

    async def catalog_rules(self, *, q: str = "", status: str = "", page: int = 1,
                            size: int = 0) -> dict[str, Any]:
        per_page = size or self._page_size
        today = promotions.today_iso()
        try:
            payload = await self._api.catalog_rules(page=page, per_page=per_page)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("katalog kuralları okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure), "items": [],
                    "total": 0, "page": page, "size": per_page, "pages": 0}

        rows = [promotions.catalog_rule_row(item, today=today)
                for item in (payload.get("items") or []) if isinstance(item, dict)]
        needle = promotions.text(q).casefold()
        if needle:
            rows = [row for row in rows if needle in row["name"].casefold()]
        if status:
            rows = [row for row in rows if row["status"] == status]
        meta = payload.get("meta") or {}
        return {"ok": True, "connected": True, "error": "", "items": rows,
                "total": promotions.as_int(meta.get("total"), len(rows)),
                "page": promotions.as_int(meta.get("currentPage"), page),
                "size": promotions.as_int(meta.get("perPage"), per_page),
                "pages": promotions.as_int(meta.get("lastPage"), 1)}

    async def catalog_rule(self, rule_id: int) -> dict[str, Any]:
        try:
            raw = await self._api.catalog_rule(int(rule_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "connected": False, "error": self._fail(failure)}
        return {"ok": True, "connected": True, "error": "",
                "rule": promotions.catalog_rule_detail(raw, today=promotions.today_iso())}

    async def save_catalog_rule(self, rule_id: int, *, patch: dict[str, Any], reason: str,
                                actor: str, dry_run: bool = True) -> dict[str, Any]:
        """Katalog kuralı — VİTRİN FİYATINI değiştirir, sepeti değil."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        clean = dict(patch or {})
        clean.pop("status", None)
        broken = promotions.condition_error(clean.get("conditions"))
        if broken:
            return {"ok": False, "error": broken}

        if rule_id:
            try:
                current = await self._api.catalog_rule(int(rule_id))
            except Exception as failure:  # noqa: BLE001 — K7
                return {"ok": False, "error": self._fail(failure)}
            body = promotions.write_catalog_body(current, clean)
        else:
            channels, groups = await self._defaults()
            body = promotions.new_catalog_body(clean, channel_ids=channels, group_ids=groups)

        invalid = promotions.rule_error(body)
        if invalid:
            return {"ok": False, "error": invalid}

        action = "update_catalog_rule" if rule_id else "create_catalog_rule"
        await self._record(rule_id=rule_id, scope="catalog", action=action, reason=reason,
                           actor=actor, result="denendi", detail={"fields": sorted(clean)})
        try:
            result = await self._api.save_catalog_rule(
                payload=body, rule_id=int(rule_id) or None, reason=reason, actor=actor,
                dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(rule_id=rule_id, scope="catalog", action=action, reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        new_id = rule_id or self._new_id(result)
        await self._record(rule_id=new_id, scope="catalog", action=action, reason=reason,
                           actor=actor, result="dry_run" if dry_run else "ok")
        await self._announce(action, new_id, "catalog")
        return {"ok": True, "error": "", "id": new_id, **self._sent(result, dry_run),
                "notice": "Katalog kuralı vitrin fiyatını değiştirir; yansıması indeksleme "
                          "sonrası görünür."}

    # ================================================================ kupon

    async def coupons(self, rule_id: int, *, page: int = 1, size: int = 0) -> dict[str, Any]:
        per_page = size or self._page_size
        today = promotions.today_iso()
        try:
            payload = await self._api.coupons(int(rule_id), page=page, per_page=per_page)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": True, "connected": False, "error": self._fail(failure), "items": [],
                    "total": 0, "page": page, "size": per_page, "pages": 0}
        rows = [promotions.coupon_row(item, today=today)
                for item in (payload.get("items") or []) if isinstance(item, dict)]
        meta = payload.get("meta") or {}
        return {"ok": True, "connected": True, "error": "", "items": rows,
                "total": promotions.as_int(meta.get("total"), len(rows)),
                "page": promotions.as_int(meta.get("currentPage"), page),
                "size": promotions.as_int(meta.get("perPage"), per_page),
                "pages": promotions.as_int(meta.get("lastPage"), 1),
                "used": sum(row["usage"]["used"] for row in rows)}

    def code_preview(self, *, prefix: str, length: int, fmt: str) -> dict[str, Any]:
        """Üretilecek kodun ÖRNEĞİ. Gerçek kodları mağaza üretir."""
        problem = analytics.generator_error(
            count=1, length=length, min_length=self._min_length,
            max_length=self._max_length, max_count=self._max_count)
        if problem:
            return {"ok": False, "error": problem}
        return {"ok": True, "error": "",
                "samples": analytics.code_preview(prefix=prefix, length=length, fmt=fmt, count=3),
                "notice": "Örnektir. Gerçek kodları mağaza üretir; benzersizliği o garanti eder."}

    @property
    def _max_count(self) -> int:
        """Tek partide üretilebilecek kod sayısı — TARAYABİLDİĞİMİZ KADAR.

        Mağazanın toplu üretim ucu ürettiği kodları döndürmüyor; kodları ancak
        kupon listesini önce/sonra okuyup farkını alarak öğreniyoruz. Tarama
        3.000 kodda duruyor (`COUPON_PAGE_CAP × COUPON_PAGE_SIZE`); daha
        fazlasına izin vermek, "5.000 istedim 3.000 geldi" diyen ama aslında
        5.000'i de üretmiş bir ekran demekti. Ayar bunun üstünü isterse
        sessizce kırpılmaz — üst sınır referans ucundan ekrana bildirilir.
        """
        configured = max(1, promotions.as_int(self._config.get("coupon_max_count"), 3000))
        return min(configured, COUPON_PAGE_CAP * COUPON_PAGE_SIZE)

    @property
    def _min_length(self) -> int:
        return max(4, promotions.as_int(self._config.get("coupon_min_length"), 6))

    @property
    def _max_length(self) -> int:
        return max(self._min_length,
                   promotions.as_int(self._config.get("coupon_max_length"), 24))

    async def generate_coupons(self, rule_id: int, *, prefix: str, count: int, length: int,
                               fmt: str = "alphanumeric", reason: str, actor: str,
                               dry_run: bool = True) -> dict[str, Any]:
        """Toplu kupon üretir ve ÜRETİLEN KODLARI CSV'ye yazar.

        TUZAK: mağazanın toplu üretim ucu ürettiği kodları yanıtta döndürmüyor.
        Bu yüzden kupon listesi üretimden ÖNCE ve SONRA okunup farkı alınır.
        Kuru provada mağazaya hiç istek gitmez (çekirdek uçlarda `dryRun`
        gövdeye konmaz); o durumda dosya da yazılmaz, ekran bunu söyler.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        limits = analytics.generator_error(count=count, length=length,
                                           min_length=self._min_length,
                                           max_length=self._max_length,
                                           max_count=self._max_count)
        if limits:
            return {"ok": False, "error": limits}

        before, before_cut = await self._all_coupons(rule_id)
        if before_cut:
            return {"ok": False,
                    "error": "Bu kuralın kupon listesi tam okunamadı. Üretim yapılmadı: "
                             "hangi kodların YENİ olduğunu ayırt edemeyeceğimiz için parti "
                             "dosyası eksik çıkardı."}
        payload = promotions.coupon_payload(prefix=prefix, count=count, length=length, fmt=fmt)
        await self._record(rule_id=rule_id, scope="coupon", action="generate_coupons",
                           reason=reason, actor=actor, result="denendi", detail=payload)
        try:
            result = await self._api.generate_coupons(int(rule_id), payload=payload,
                                                      reason=reason, actor=actor,
                                                      dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(rule_id=rule_id, scope="coupon", action="generate_coupons",
                               reason=reason, actor=actor, result="hata",
                               detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        state = self._sent(result, dry_run)
        if not state["sent"]:
            await self._record(rule_id=rule_id, scope="coupon", action="generate_coupons",
                               reason=reason, actor=actor, result="dry_run", detail=payload)
            return {"ok": True, "error": "", **state, "codes": [], "path": "", "name": "",
                    "notice": "Kuru prova: mağazaya istek gitmedi, kupon üretilmedi."}

        after, after_cut = await self._all_coupons(rule_id)
        codes = analytics.new_codes(before, after)
        written = await self._write_batch(rule_id=rule_id, codes=codes, prefix=prefix,
                                          count=count, length=length, actor=actor, reason=reason)
        await self._record(rule_id=rule_id, scope="coupon", action="generate_coupons",
                           reason=reason, actor=actor, result="ok",
                           detail={**payload, "produced": len(codes), "listTruncated": after_cut})
        await self._announce("coupons", rule_id, "coupon")
        short = len(codes) < promotions.as_int(count)
        # İKİ AYRI DURUM, İKİ AYRI CÜMLE. "Az kod üretildi" demek, listeyi
        # okuyamadığımız için az GÖRDÜĞÜMÜZ durumda yalan olurdu: kodlar
        # mağazada duruyor, elimizdeki dosya eksik.
        if after_cut:
            notice = ("Kodlar üretildi ama kupon listesi tam okunamadı; aşağıdaki dosya "
                      "EKSİK olabilir. Tam listeyi 'Kupon listesini dışa aktar' ile alın.")
        elif short:
            notice = ("İstenen adetten az kod üretildi; mağaza bazı kodları benzersizlik "
                      "yüzünden atlamış olabilir.")
        else:
            notice = ""
        return {"ok": True, "error": "", **state, "codes": codes, **written,
                "requested": promotions.as_int(count), "produced": len(codes),
                "listTruncated": after_cut, "notice": notice}

    async def _all_coupons(self, rule_id: int) -> tuple[list[dict[str, Any]], bool]:
        """Kuralın kuponları — sayfa sayfa, SIRAYLA. `(satırlar, eksik_mi)`.

        Tek sayfayla yetinmek iki yerde sessiz hata üretirdi: üretim sonrası
        fark alınırken sonraki sayfalar "yeni" görünmezdi, kaldırma denetiminde
        ise ikinci sayfadaki KULLANILMIŞ kod fark edilmeden silinirdi.

        EKSİK TARAMA GİZLENMEZ. Ağ koptuğunda ya da tavana dayanıldığında ikinci
        değer `True` döner; çağıranlar buna göre ya reddeder (kaldırma) ya da
        eksikliği ekrana yazar (üretim). "Eldekiyle devam edip sustuk" en kötü
        seçenekti: kullanılmış bir kuponu sildirebilirdi.
        """
        rows: list[dict[str, Any]] = []
        page = 1
        while True:
            if page > COUPON_PAGE_CAP:
                self._log.warning("kupon taraması tavana dayandı", ruleId=rule_id, page=page)
                return rows, True
            try:
                payload = await self._api.coupons(int(rule_id), page=page,
                                                  per_page=COUPON_PAGE_SIZE)
            except Exception as failure:  # noqa: BLE001 — K7
                self._log.warning("kupon listesi okunamadı", ruleId=rule_id, page=page,
                                  error=str(failure))
                return rows, True
            items = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
            rows.extend(items)
            meta = payload.get("meta") or {}
            last = promotions.as_int(meta.get("lastPage"), 1)
            if not items or page >= last:
                return rows, False
            page += 1

    async def _write_batch(self, *, rule_id: int, codes: list[str], prefix: str, count: int,
                           length: int, actor: str, reason: str) -> dict[str, Any]:
        if not codes:
            return {"path": "", "name": "", "token": ""}
        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        name = f"magaza-kupon-{promotions.text(prefix).lower() or 'parti'}-{stamp}.csv"
        table = [[code, promotions.text(prefix), str(rule_id)] for code in codes]
        try:
            path = write_private(self._export_dir / name,
                                 csv_bytes(["Kupon kodu", "Önek", "Kural"], table))
        except (OSError, ExportError) as failure:
            self._log.warning("kupon dosyası yazılamadı", error=str(failure))
            return {"path": "", "name": "", "token": "",
                    "fileError": f"Kodlar üretildi ama dosya yazılamadı: {failure}"}

        token = uuid.uuid4().hex
        try:
            await self._store.execute(
                f"INSERT INTO {self._batches} (token, rule_id, rule_name, prefix, count, length, "
                "expires_at, codes, path, actor, reason, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (token, int(rule_id), "", promotions.text(prefix), len(codes), int(length), "",
                 json.dumps(codes, ensure_ascii=False), str(path), actor, reason, "ok", _now()),
            )
        except Exception as failure:  # noqa: BLE001 — parti kaydı yazılamadı, kodlar duruyor
            self._log.warning("kupon partisi kaydedilemedi", error=str(failure))
        return {"path": str(path), "name": name, "token": token}

    async def add_coupon(self, rule_id: int, *, code: str, reason: str, actor: str,
                         dry_run: bool = True) -> dict[str, Any]:
        """Tek kupon kodu ekler (elle yazılan kampanya kodu)."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        wanted = promotions.text(code).upper()
        if len(wanted) < 3:
            return {"ok": False, "error": "Kupon kodu en az 3 karakter olmalı."}
        try:
            result = await self._api.create_coupon(int(rule_id), payload={"code": wanted},
                                                   reason=reason, actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        await self._record(rule_id=rule_id, scope="coupon", action="create_coupon", reason=reason,
                           actor=actor, result="dry_run" if dry_run else "ok",
                           detail={"code": wanted})
        return {"ok": True, "error": "", "code": wanted, **self._sent(result, dry_run)}

    async def remove_coupons(self, rule_id: int, *, coupon_ids: list[int], reason: str,
                             actor: str, dry_run: bool = True) -> dict[str, Any]:
        """Kullanılmamış kupon kodlarını kaldırır.

        KULLANILMIŞ KOD KALDIRILMAZ: kodun izi siparişte duruyor ve performans
        ekranı o kodu sipariş satırından okuyor; kaldırılırsa geçmiş kampanya
        raporu "bilinmeyen kod" satırlarıyla dolar.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        ids = [int(item) for item in (coupon_ids or []) if promotions.as_int(item)]
        if not ids:
            return {"ok": False, "error": "Kupon seçilmedi."}

        current, cut = await self._all_coupons(rule_id)
        # GÖRMEDİĞİMİZ KODU SİLDİRMEYİZ. Tarama eksikse seçilen kod listede
        # bulunmayabilir; "bulunamadı" ile "kullanılmamış" aynı şey değildir ve
        # bu ayrımı yapmamak kullanılmış bir kuponu sildirirdi.
        seen = {promotions.as_int(promotions.pick(row, "id")) for row in current}
        missing = [item for item in ids if item not in seen]
        if cut and missing:
            return {"ok": False,
                    "error": "Kupon listesi tam okunamadı; seçilen kodların kullanılıp "
                             "kullanılmadığı doğrulanamadı. Güvenlik gereği kaldırma "
                             "yapılmadı — sayfayı tazeleyip yeniden deneyin."}
        used = [promotions.text(promotions.pick(row, "code")) for row in current
                if promotions.as_int(promotions.pick(row, "id")) in ids
                and promotions.as_int(promotions.pick(row, "times_used")) > 0]
        if used:
            return {"ok": False,
                    "error": f"Kullanılmış kupon kaldırılmaz: {', '.join(used[:5])}"
                             f"{' …' if len(used) > 5 else ''}. Kodun izi siparişte duruyor."}

        await self._record(rule_id=rule_id, scope="coupon", action="delete_coupons",
                           reason=reason, actor=actor, result="denendi", detail={"ids": ids})
        try:
            result = await self._api.delete_coupons(int(rule_id), coupon_ids=ids, reason=reason,
                                                    actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(rule_id=rule_id, scope="coupon", action="delete_coupons",
                               reason=reason, actor=actor, result="hata",
                               detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}
        await self._record(rule_id=rule_id, scope="coupon", action="delete_coupons",
                           reason=reason, actor=actor, result="dry_run" if dry_run else "ok",
                           detail={"ids": ids})
        return {"ok": True, "error": "", "count": len(ids), **self._sent(result, dry_run)}

    async def batches(self, *, rule_id: int = 0, limit: int = 50) -> dict[str, Any]:
        """Üretilen kupon partileri — CSV kaybolursa buradan yeniden yazılır."""
        sql = (f"SELECT token, rule_id, prefix, count, length, path, actor, reason, status, "
               f"created_at FROM {self._batches} ")
        params: tuple[Any, ...] = ()
        if rule_id:
            sql += "WHERE rule_id = ? "
            params = (int(rule_id),)
        sql += "ORDER BY id DESC LIMIT ?"
        params = (*params, max(1, min(200, int(limit))))
        try:
            rows = await self._store.fetch_all(sql, params)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": True, "items": [], "error": self._fail(failure)}
        return {"ok": True, "error": "", "items": [
            {"token": row["token"], "ruleId": row["rule_id"], "prefix": row["prefix"],
             "count": row["count"], "length": row["length"], "path": row["path"],
             "actor": row["actor"], "reason": row["reason"], "status": row["status"],
             "createdAt": row["created_at"]}
            for row in rows]}

    async def batch_codes(self, token: str) -> dict[str, Any]:
        try:
            row = await self._store.fetch_one(
                f"SELECT codes, prefix, rule_id, path FROM {self._batches} WHERE token = ?",
                (promotions.text(token),))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        if not row:
            return {"ok": False, "error": "Bu parti bulunamadı."}
        return {"ok": True, "error": "", "codes": json.loads(row["codes"] or "[]"),
                "prefix": row["prefix"], "ruleId": row["rule_id"], "path": row["path"]}

    # =========================================================== simülasyon

    async def simulate(self, *, cart: dict[str, Any], draft: dict[str, Any] | None = None,
                       coupon: str = "") -> dict[str, Any]:
        """Örnek sepeti bugünkü kurallara (ve varsa TASLAK kurala) uygular.

        Taslak, kaydedilmemiş kuralın ekrandaki hâlidir: aynı kimlikli kaydın
        yerine geçer. "Kaydetmeden önce ne olacak" sorusunun cevabı budur.

        Bu motor MAĞAZANIN MOTORU DEĞİLDİR; sonuç ekranda böyle etiketlenir.

        TUZAK: kural LİSTESİ `conditions`/`channels`/`customerGroups` alanlarını
        `null` veriyor — bunlar yalnız TEKİL kayıtta dolu. Listeye bakan bir
        simülasyon her kuralı "koşulsuz" sanıp her sepete uygulardı. Bu yüzden
        aktif kurallar tek tek okunur (tavan: `SIMULATE_DETAIL_CAP`); aktif
        olmayanlar zaten kapıda eleneceği için liste satırıyla yetinir.
        """
        today = promotions.today_iso()
        try:
            payload = await self._api.cart_rules(all_pages=True)
            connected, error = True, ""
        except Exception as failure:  # noqa: BLE001 — K7
            payload, connected, error = {"items": []}, False, self._fail(failure)

        raws = [item for item in (payload.get("items") or [])[:RULE_SCAN_CAP]
                if isinstance(item, dict)]
        rules: list[dict[str, Any]] = []
        partial = 0
        detailed = 0
        for raw in raws:
            live = promotions.rule_status(raw, today) == "active"
            if live and detailed < SIMULATE_DETAIL_CAP:
                detailed += 1
                try:
                    raw = await self._api.cart_rule(promotions.as_int(promotions.pick(raw, "id")))
                except Exception as failure:  # noqa: BLE001 — K7, eldekiyle devam
                    partial += 1
                    self._log.warning("kural ayrıntısı okunamadı", error=str(failure))
            elif live:
                partial += 1
            rules.append(promotions.cart_rule_detail(raw, today=today))

        if draft:
            draft_id = promotions.as_int(draft.get("id"))
            rules = [rule for rule in rules if rule["id"] != draft_id or not draft_id]
            rules.append(self._draft_rule(draft, today=today))

        result = analytics.simulate(self._clean_cart(cart), rules, coupon=coupon, today=today)
        notice = ("Bu hesap Kontrol Merkezi'nin kuralları okumasıdır; kesin tutar "
                  "ödeme adımında mağazanın kendi motorunda oluşur.")
        if partial:
            notice += (f" {partial} aktif kuralın ayrıntısı okunamadı; onların koşulları "
                       "hesaba KATILMADI.")
        return {"ok": True, "connected": connected, "error": error, "result": result,
                "ruleCount": len(rules), "partialRules": partial, "notice": notice}

    @staticmethod
    def _draft_rule(draft: dict[str, Any], *, today: str) -> dict[str, Any]:
        """Ekrandaki taslağı simülasyon modeline çevirir.

        Taslağın durumu KULLANICININ SEÇTİĞİ gibi denenir: "yayına alsam ne
        olurdu" sorusunu yanıtlamak için taslak `active` kabul edilir, ama
        takvimi yine de uygulanır — tarihi geçmiş bir taslak yine ölüdür.
        """
        raw = {
            "id": promotions.as_int(draft.get("id")),
            "name": promotions.text(draft.get("name")) or "(taslak)",
            "status": 1,
            "starts_from": promotions.day(draft.get("startsFrom")),
            "ends_till": promotions.day(draft.get("endsTill")),
            "coupon_type": 1 if promotions.text(draft.get("couponType")) == "specific" else 0,
            "coupon_code": promotions.text(draft.get("couponCode")),
            "condition_type": 2 if promotions.text(draft.get("conditionType")) == "any" else 1,
            "conditions": promotions.encode_conditions(draft.get("conditions")),
            "action_type": promotions.text((draft.get("action") or {}).get("kind")) or "by_percent",
            "discount_amount": "0",
            "discount_step": promotions.as_int((draft.get("action") or {}).get("step")),
            "discount_quantity": promotions.as_int((draft.get("action") or {}).get("quantity")),
            promotions.FREE_SHIPPING_KEY:
                1 if (draft.get("action") or {}).get("freeShipping") else 0,
            "sort_order": promotions.as_int((draft.get("limits") or {}).get("priority")),
            "end_other_rules": 1 if (draft.get("limits") or {}).get("endOtherRules") else 0,
            "uses_per_coupon": promotions.as_int((draft.get("limits") or {}).get("perCoupon")),
            "times_used": 0,
            "channels": [promotions.as_int(one) for one in (draft.get("channels") or [])],
            "customer_groups": [promotions.as_int(one)
                                for one in (draft.get("customerGroups") or [])],
        }
        action = draft.get("action") or {}
        raw["discount_amount"] = (f"{promotions.as_float(action.get('value')):g}"
                                  if raw["action_type"] == "by_percent"
                                  else promotions.from_kurus(promotions.as_int(
                                      action.get("value"))))
        detail = promotions.cart_rule_detail(raw, today=today)
        detail["name"] = f"{detail['name']} (taslak)"
        if promotions.as_int(action.get("maxDiscount")) > 0:
            detail["action"]["maxDiscount"] = promotions.as_int(action.get("maxDiscount"))
        return detail

    @staticmethod
    def _clean_cart(cart: dict[str, Any]) -> dict[str, Any]:
        items = []
        for item in (cart or {}).get("items") or []:
            if not isinstance(item, dict):
                continue
            items.append({
                "name": promotions.text(item.get("name")) or "Kalem",
                "price": max(0, promotions.as_int(item.get("price"))),
                "qty": max(1, promotions.as_int(item.get("qty"), 1)),
                "productId": promotions.as_int(item.get("productId")),
                "categoryIds": [promotions.as_int(one) for one in (item.get("categoryIds") or [])
                                if promotions.as_int(one)],
            })
        return {
            "items": items,
            "shipping": max(0, promotions.as_int((cart or {}).get("shipping"))),
            "paymentMethod": promotions.text((cart or {}).get("paymentMethod")),
            "customerGroupId": promotions.as_int((cart or {}).get("customerGroupId")),
            "channelId": promotions.as_int((cart or {}).get("channelId")),
            "firstOrder": bool((cart or {}).get("firstOrder")),
        }

    # ============================================================ performans

    async def performance(self, *, start: str = "", end: str = "") -> dict[str, Any]:
        """Kupon başına ciro ve indirim maliyeti — SİPARİŞ LİSTESİNDEN.

        Bagisto'nun raporlama ucu kampanya kırılımı vermiyor; kupon kodu sipariş
        satırında duruyor. Aralık verilmezse son `performance_days` gün alınır.

        TUZAK: sipariş LİSTESİ indirim tutarını taşımıyor — `discountAmount`
        yalnız sipariş DETAYINDA var. Kupon kullanılan siparişler bu yüzden tek
        tek okunur (tavan `ORDER_DETAIL_CAP`); okunamayan sipariş "indirim
        bilinmiyor" sayılır ve ekran tutarı "—" gösterir. 0 yazmak kampanyayı
        maliyetsiz göstermek olurdu.
        """
        end_day = promotions.day(end) or promotions.today_iso()
        if start:
            start_day = promotions.day(start)
        else:
            days = max(1, promotions.as_int(self._config.get("performance_days"), 30))
            start_day = (datetime.fromisoformat(end_day) - timedelta(days=days - 1)).date() \
                .isoformat()

        filters = {"date_from": start_day, "date_to": end_day}
        try:
            payload = await self._api.orders(filters, all_pages=True)
            connected, error = True, ""
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("performans için sipariş okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "start": start_day, "end": end_day, "rows": [], "totals": {}, "usage": [],
                    "truncated": False}

        orders = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
        truncated = bool(payload.get("truncated")) or len(orders) > self._order_cap
        orders = orders[:self._order_cap]
        orders = await self._with_discounts(orders)

        rules: list[dict[str, Any]] = []
        try:
            rule_payload = await self._api.cart_rules(all_pages=True)
            rules = [promotions.cart_rule_row(item, today=end_day)
                     for item in (rule_payload.get("items") or [])[:RULE_SCAN_CAP]
                     if isinstance(item, dict)]
        except Exception as failure:  # noqa: BLE001 — kural gelmezse kod adı boş kalır
            error = error or self._fail(failure)

        result = analytics.coupon_performance(orders, rules=rules)
        return {"ok": True, "connected": connected, "error": error,
                "start": start_day, "end": end_day, "rows": result["rows"],
                "totals": result["totals"], "usage": analytics.rule_usage_bars(rules),
                "orders": len(orders), "truncated": truncated,
                "discountComplete": bool(result["totals"].get("discountComplete", True))}

    async def _with_discounts(self, orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Kuponlu siparişlere DETAYDAN indirim tutarını ekler.

        Yalnız kupon kodu taşıyan siparişler okunur: kampanya maliyeti sorusu
        onlarla ilgili ve 17 siparişlik bir mağazada bile tüm listeyi tek tek
        okumak hız kovasını (dakikada 55) boşuna harcardı. Okunamayan sipariş
        DEĞİŞTİRİLMEDEN geçer; `coupon_performance` onu "indirim bilinmiyor"
        sayar.
        """
        out: list[dict[str, Any]] = []
        fetched = 0
        for raw in orders:
            code = promotions.text(promotions.pick(raw, "coupon_code"))
            known = promotions.pick(raw, "discount_amount") is not None
            if not code or known or fetched >= ORDER_DETAIL_CAP:
                out.append(raw)
                continue
            fetched += 1
            try:
                detail = await self._api.order(promotions.as_int(promotions.pick(raw, "id")))
            except Exception as failure:  # noqa: BLE001 — K7, eksik veri sessiz kalmaz
                self._log.warning("sipariş ayrıntısı okunamadı", error=str(failure))
                out.append(raw)
                continue
            amount = promotions.pick(detail, "discount_amount")
            out.append(raw if amount is None else {**raw, "discountAmount": amount})
        return out

    # ============================================================== referans

    async def reference(self) -> dict[str, Any]:
        """Süzgeçlerin ve koşul düzenleyicisinin beslendiği listeler.

        Her parça AYRI hata yakalar: ödeme yöntemleri gelmezse kategori seçici
        yine dolar (K7). Desteklenmeyen koşul türü GİZLENMEZ, kapalı gösterilir
        ve nedeni yazılır — sessizce eksik bir ekran, kullanıcıyı "burada niye
        yok" diye arattırır.
        """
        out: dict[str, Any] = {
            "ok": True, "connected": True, "error": "", "warnings": [],
            "channels": [], "customerGroups": [], "categories": [], "paymentMethods": [],
            "actionTypes": [{"value": key, "label": label}
                            for key, label in promotions.ACTION_LABELS.items()],
            "operators": [{"value": key, "label": label}
                          for key, label in promotions.OPERATOR_LABELS.items()],
            "conditionKinds": self._condition_kinds(),
            "statuses": [{"value": key, "label": label}
                         for key, label in promotions.STATUS_LABELS.items()],
            "limits": {"maxCount": self._max_count, "minLength": self._min_length,
                       "maxLength": self._max_length},
        }

        async def part(label: str, call: Any) -> list[dict[str, Any]]:
            try:
                result = await call()
            except Exception as failure:  # noqa: BLE001 — uzak sistem
                out["warnings"].append(f"{label}: {self._fail(failure)}")
                out["connected"] = False
                return []
            return [item for item in (result.get("items") or []) if isinstance(item, dict)]

        for item in await part("kanallar", self._api.channels):
            out["channels"].append({"id": promotions.as_int(item.get("id")),
                                    "code": promotions.text(item.get("code")),
                                    "name": promotions.text(item.get("name"))})
        for item in await part("müşteri grupları", self._api.customer_groups):
            out["customerGroups"].append({"id": promotions.as_int(item.get("id")),
                                          "name": promotions.text(item.get("name"))})
        for item in await part("kategoriler", self._api.category_tree):
            out["categories"].extend(self._flatten(item))

        out["paymentMethods"] = await self._payment_methods(out)
        if not out["paymentMethods"]:
            # Açılır boş kalacak; koşul GİZLENMEZ, ekran kodu elle sordurur ve
            # nedenini yazar. Sessiz boş bir açılır "ödeme koşulu bozuk" demektir.
            for row in out["conditionKinds"]:
                if row["value"] == "payment":
                    row["note"] = ("Ödeme yöntemleri mağazadan okunamadı; kod elle yazılır "
                                   "(ör. `cashondelivery`).")
                    row["freeText"] = True
        return out

    def _condition_kinds(self) -> list[dict[str, Any]]:
        rows = [{"value": key, "label": label, "available": True, "note": "", "freeText": False}
                for key, label in promotions.CONDITION_LABELS.items()]
        rows.append({
            "value": "customerGroup", "label": "Müşteri grubu", "available": True,
            "freeText": False,
            "note": "Koşul satırı değil kuralın kapsamıdır; 'Kimler' alanında düzenlenir.",
        })
        # İSTENEN AMA OLMAYAN: mağazada "ilk sipariş" diye bir koşul özniteliği
        # yok. Gizlemek yerine kapalı gösterilir; kullanıcı neden olmadığını
        # okur ve uç yayınlanınca burası kendiliğinden açılır.
        rows.append({
            "value": "firstOrder", "label": "Yalnız ilk sipariş", "available": False,
            "freeText": False,
            "note": "Mağaza tarafında bu koşul yok. BBD ucu (/api/admin/bbd/…) yayınlanınca "
                    "açılacak; şimdilik seçilemez.",
        })
        return rows

    @staticmethod
    def _flatten(node: Any, depth: int = 0) -> list[dict[str, Any]]:
        if not isinstance(node, dict):
            return []
        rows = [{"id": promotions.as_int(node.get("id")),
                 "label": ("— " * depth) + promotions.text(node.get("name")),
                 "depth": depth}]
        for child in node.get("children") or []:
            rows.extend(PromotionsService._flatten(child, depth + 1))
        return rows

    async def _payment_methods(self, out: dict[str, Any]) -> list[dict[str, Any]]:
        """Ödeme yöntemleri `core_config` içindedir; uç ya da anahtar yoksa
        ekran koşulu serbest metin sorar — boş bir açılır göstermek yerine.

        CANLI DURUM: `/api/admin/configuration?slug=…` bir DİZİ döndürüyor
        (`[{slug, channel, locale, values:{…}}]`), geçidin tekil okuyucusu ise
        sözlük bekliyor ve diziyi `{}`'ye düşürüyor. Geçit paylaşılan dosyadır,
        burada düzeltilemez; bu yüzden üç biçim de (dizi · zarflı sözlük · düz
        sözlük) okunur ve hiçbiri tutmazsa AÇIKÇA uyarı yazılır. `values`
        anahtarları `sales.payment_methods.<kod>.<alan>` düz metindir; başlık
        `<kod>.title` satırından toplanır.
        """
        try:
            payload = await self._api.configuration(PAYMENT_SLUG, channel=self._channel,
                                                    locale=self._locale)
        except Exception as failure:  # noqa: BLE001 — K7
            out["warnings"].append(f"ödeme yöntemleri: {self._fail(failure)}")
            return []

        if isinstance(payload, list):
            payload = next((item for item in payload if isinstance(item, dict)), {})
        values = payload.get("values") if isinstance(payload, dict) else None
        if not isinstance(values, dict):
            values = payload if isinstance(payload, dict) else {}

        titles: dict[str, str] = {}
        order: list[str] = []
        for key, value in values.items():
            parts = str(key).split(".")
            if len(parts) < 2:
                continue
            code, field = parts[-2], parts[-1]
            if code not in titles:
                titles[code] = ""
                order.append(code)
            if field == "title" and not isinstance(value, dict):
                titles[code] = promotions.text(value)
        rows = [{"value": code, "label": titles[code] or code} for code in order]
        if not rows:
            out["warnings"].append(
                "ödeme yöntemleri: mağaza ayar ucu okunamadı; ödeme koşulunda kod elle yazılır.")
        return rows

    async def _defaults(self) -> tuple[list[int], list[int]]:
        """Yeni kuralın kanal ve müşteri grubu varsayılanları.

        Boş bırakmak kuralı HİÇBİR kanalda çalışmaz yapıyor; mağazanın tüm
        kanalları ve grupları seçili gelir, kullanıcı daraltır.
        """
        channels: list[int] = []
        groups: list[int] = []
        try:
            payload = await self._api.channels()
            channels = [promotions.as_int(item.get("id"))
                        for item in (payload.get("items") or []) if isinstance(item, dict)]
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("kanallar okunamadı", error=str(failure))
        try:
            payload = await self._api.customer_groups()
            groups = [promotions.as_int(item.get("id"))
                      for item in (payload.get("items") or []) if isinstance(item, dict)]
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("müşteri grupları okunamadı", error=str(failure))
        return [one for one in channels if one], [one for one in groups if one]

    async def products(self, q: str = "") -> dict[str, Any]:
        """Ürün koşulu için hafif arama. Tam katalog ÇEKİLMEZ.

        SÜZGEÇ ADI `query` — `name` DEĞİL. Seçici ucu (`/api/admin/products`)
        `name` parametresini TANIMIYOR ve Laravel tanımadığı sorgu parametresini
        sessizce yok sayıyor: canlıda `?name=zzzzzz` 1.421 ürünün tamamını
        döndürüyordu. Ekran arama yapıyor sanıp alakasız ilk 25 ürünü
        gösteriyordu. `?query=zzzzzz` → 0, `?query=matematik` → 322.
        """
        try:
            payload = await self._api.product_lookup({"query": promotions.text(q)}, per_page=25)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": True, "connected": False, "error": self._fail(failure), "items": []}
        rows = [{"id": promotions.as_int(item.get("id")),
                 "name": promotions.text(item.get("name")),
                 "sku": promotions.text(item.get("sku"))}
                for item in (payload.get("items") or []) if isinstance(item, dict)]
        return {"ok": True, "connected": True, "error": "", "items": rows}

    async def audit(self, *, rule_id: int = 0, limit: int = 50) -> dict[str, Any]:
        """Bu ekrandan yapılan yazmaların YEREL izi (gerekçeleriyle)."""
        sql = (f"SELECT rule_id, scope, action, reason, actor, result, created_at "
               f"FROM {self._audit} ")
        params: tuple[Any, ...] = ()
        if rule_id:
            sql += "WHERE rule_id = ? "
            params = (int(rule_id),)
        sql += "ORDER BY id DESC LIMIT ?"
        params = (*params, max(1, min(500, int(limit))))
        try:
            rows = await self._store.fetch_all(sql, params)
        except Exception as failure:  # noqa: BLE001 — iz okunamadı, ekran dursun
            return {"ok": True, "items": [], "error": self._fail(failure)}
        return {"ok": True, "error": "", "items": [
            {"ruleId": row["rule_id"], "scope": row["scope"], "action": row["action"],
             "reason": row["reason"], "actor": row["actor"], "result": row["result"],
             "createdAt": row["created_at"]}
            for row in rows]}

    # ================================================================ rapor

    async def export_csv(self, *, kind: str = "rules", rule_id: int = 0) -> dict[str, Any]:
        """Kural listesi ya da kupon listesi CSV'si — rapor klasörüne yazılır."""
        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        truncated = False
        if kind == "coupons":
            if not rule_id:
                return {"ok": False, "error": "Kupon dışa aktarımı için kural seçilmeli."}
            rows, cut = await self._all_coupons(rule_id)
            if not rows:
                return {"ok": False, "error": "Bu kuralda kupon yok ya da liste okunamadı."}
            truncated = cut
            if cut:
                # Dosya yine yazılır — eksik CSV hiç CSV'den iyidir — ama
                # eksikliği dosyayı açan kişi de görsün diye ekrana taşınır.
                self._log.warning("kupon dışa aktarımı eksik", ruleId=rule_id, rows=len(rows))
            today = promotions.today_iso()
            table = [[promotions.text(promotions.pick(row, "code")),
                      promotions.as_int(promotions.pick(row, "times_used")),
                      promotions.as_int(promotions.pick(row, "usage_limit")) or "sınırsız",
                      promotions.day(promotions.pick(row, "expired_at")) or "—",
                      promotions.coupon_row(row, today=today)["stateLabel"]] for row in rows]
            headers = ["Kupon kodu", "Kullanım", "Limit", "Son kullanma", "Durum"]
            name = f"magaza-kupon-listesi-{stamp}.csv"
        else:
            listing = await self.rules(size=RULE_SCAN_CAP)
            if not listing.get("connected"):
                return {"ok": False, "error": listing.get("error") or "Kurallar okunamadı."}
            headers = ["Ad", "Kod", "Tür", "Değer", "Başlangıç", "Bitiş", "Kullanım",
                       "Öncelik", "Durum"]
            table = [[row["name"], row["code"] or "—", row["actionLabel"], row["valueLabel"],
                      row["startsFrom"] or "—", row["endsTill"] or "—", row["usage"]["label"],
                      row["priority"], row["statusLabel"]] for row in listing["items"]]
            name = f"magaza-kampanya-listesi-{stamp}.csv"

        try:
            path = write_private(self._export_dir / name, csv_bytes(headers, table))
        except (OSError, ExportError) as failure:
            return {"ok": False, "error": f"Dosya yazılamadı: {failure}"}
        return {"ok": True, "error": "", "path": str(path), "name": name, "rows": len(table),
                "truncated": truncated,
                "notice": ("Kupon listesi tam okunamadı; dosya EKSİK olabilir."
                           if truncated else "")}

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
        if kind not in ("performance", "rules"):
            return {"ok": False, "error": f"Bilinmeyen rapor: {kind}"}

        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        if kind == "performance":
            data = await self.performance(start=promotions.text(params.get("start")),
                                          end=promotions.text(params.get("end")))
            if not data.get("connected"):
                return {"ok": False, "error": data.get("error") or "Siparişler okunamadı."}
            if not data["rows"]:
                return {"ok": False,
                        "error": "Bu aralıkta kupon kullanılan sipariş yok; rapor boş çıkardı."}
            content = self._performance_pdf(data)
            name = f"magaza-kampanya-performansi-{stamp}.pdf"
        else:
            listing = await self.rules(size=RULE_SCAN_CAP)
            if not listing.get("connected"):
                return {"ok": False, "error": listing.get("error") or "Kurallar okunamadı."}
            if not listing["items"]:
                return {"ok": False, "error": "Rapora girecek kural yok."}
            content = self._rules_pdf(listing)
            name = f"magaza-kampanya-listesi-{stamp}.pdf"

        try:
            path = write_private(self._export_dir / name, content)
        except (OSError, ExportError) as failure:
            return {"ok": False, "error": str(failure)}
        self._log.info("promosyon raporu üretildi", kind=kind, path=str(path))
        return {"ok": True, "error": "", "path": str(path), "name": name, "bytes": len(content)}

    def _performance_pdf(self, data: dict[str, Any]) -> bytes:
        totals = data["totals"]
        cost = totals.get("costRatio")
        # Eksik indirim verisi kâğıtta da "—" olur: basılmış bir rapordaki
        # yanlış rakam ekrandakinden daha uzun yaşar.
        complete = bool(totals.get("discountComplete", True))
        sections: list[dict[str, Any]] = [{
            "kind": "tiles", "title": "Özet",
            "tiles": [("Kuponlu sipariş", number(totals.get("couponOrders", 0))),
                      ("Kupon cirosu", money(totals.get("couponRevenue", 0))),
                      ("İndirim maliyeti",
                       money(totals.get("couponDiscount", 0)) if complete else "—"),
                      ("Ortalama sepet (kuponlu)", money(totals.get("average", 0))),
                      ("Ortalama sepet (kuponsuz)", money(totals.get("plainAverage", 0))),
                      ("İndirim / ciro", percent(cost) if cost is not None else "—")],
        }, {
            "kind": "table", "title": "Kupon başına",
            "headers": ["Kod", "Kampanya", "Sipariş", "Ciro", "İndirim", "Ortalama", "Oran"],
            "align": "LLRRRRR", "widths": [1.2, 2, 0.8, 1.2, 1.2, 1.2, 0.8],
            "rows": [[row["code"], row["rule"] or "—", number(row["orders"]),
                      money(row["revenue"]),
                      money(row["discount"]) if row.get("discountKnown", True) else "—",
                      money(row["average"]),
                      percent(row["costRatio"]) if row["costRatio"] is not None else "—"]
                     for row in data["rows"][:200]],
        }]
        if not complete:
            sections.append({"kind": "note",
                             "text": "Bazı siparişlerin indirim tutarı okunamadı; indirim "
                                     "sütunu ve oranı '—' bırakıldı. Ciro rakamları tamdır."})
        usage = [row for row in data.get("usage") or [] if row["used"]]
        if usage:
            sections.append({
                "kind": "bars", "title": "Kural kullanımı",
                "bars": [(row["name"], row["used"], row["label"]) for row in usage[:15]],
            })
        if data.get("truncated"):
            sections.append({"kind": "note",
                             "text": "Sipariş taraması tavana dayandı; rakamlar eksik olabilir."})
        sections.append({"kind": "note",
                         "text": "İptal edilen siparişler ciroya dahil edilmemiştir."})
        return build_pdf(title="Kampanya performansı",
                         subtitle=f"{data['start']} – {data['end']} · "
                                  f"{number(data.get('orders', 0))} sipariş tarandı",
                         sections=sections, footer="Kontrol Merkezi · Mağaza")

    def _rules_pdf(self, listing: dict[str, Any]) -> bytes:
        rows = listing["items"]
        counts = listing.get("counts") or {}
        sections: list[dict[str, Any]] = [{
            "kind": "tiles", "title": "Özet",
            "tiles": [("Kural", number(counts.get("all", len(rows)))),
                      ("Aktif", number(counts.get("active", 0))),
                      ("Zamanlanmış", number(counts.get("scheduled", 0))),
                      ("Duraklatıldı", number(counts.get("paused", 0))),
                      ("Süresi doldu", number(counts.get("expired", 0))),
                      ("Hiç kullanılmamış", number(counts.get("neverUsed", 0)))],
        }, {
            "kind": "table", "title": "Kampanyalar",
            "headers": ["Ad", "Kod", "Tür", "Değer", "Başlangıç", "Bitiş", "Kullanım", "Durum"],
            "align": "LLLRLLRL", "widths": [2.4, 1.1, 1.4, 0.9, 1, 1, 0.9, 1],
            "rows": [[row["name"], row["code"] or "—", row["actionLabel"], row["valueLabel"],
                      row["startsFrom"] or "—", row["endsTill"] or "—", row["usage"]["label"],
                      row["statusLabel"]] for row in rows[:300]],
        }]
        return build_pdf(title="Kampanya listesi",
                         subtitle=f"{len(rows)} kural · {promotions.today_iso()}",
                         sections=sections, footer="Kontrol Merkezi · Mağaza")

    async def _render_pages(self, path: Path, *, max_pages: int = 12,
                            dpi: int = 110) -> list[str]:
        binary = shutil.which("pdftoppm")
        if not binary:
            raise PreviewError(
                "Önizleme üretilemedi: `pdftoppm` yok (poppler-utils kurulmalı). "
                "Rapor yine de kaydedildi ve yazdırılabilir.")
        with tempfile.TemporaryDirectory(prefix="km-promosyon-onizleme-") as folder:
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
