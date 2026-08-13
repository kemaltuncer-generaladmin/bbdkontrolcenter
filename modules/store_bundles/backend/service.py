"""Setler — iş kuralları.

VERİ MAĞAZADADIR, KARAR BURADADIR. Ürün, fiyat, stok ve durum `store.api`
geçidinden gelir (K4); bu modül onların kopyasını DİSKE yazmaz. Yerel tablo
yalnız mağazada karşılığı olmayan iki şeyi tutar: set künyesi/bileşen tanımı
(adet, bileşen indirimi, zorunluluk) ve yazma gerekçesi.

İKİ KAYNAK, TEK EKRAN. Set tanımı iki yerden gelebilir:
  · `/api/admin/bbd/bundles` — BBD'ye özel uç. YAYINDA DEĞİL; hazır olduğunda
    kendiliğinden birincil kaynak olur.
  · "Setler" kategorisi (canlıda 42) + yerel künye — bugün çalışan yol.
Ekran hangisinin kullanıldığını söyler; sessizce yer değiştirmez.

UZAK SİSTEM DÜŞERSE EKRAN AYAKTA KALIR (K7): `connected: False` + `error`
döner, yerel künyeler yine listelenir ve "fiyat okunamadı" yazar.
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

from km_sdk import ExportError, build_pdf, csv_bytes, money, number, report_dir, write_private

from . import bundles

#: Bileşen ürünü okuması bu sürede tazedir (ayarla ezilir). Bellekte durur,
#: diske YAZILMAZ.
DEFAULT_CACHE_SECONDS = 120


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def _today() -> str:
    """Yerel takvim günü. `utcnow().date()` kullanılmaz: geçerlilik penceresi
    mağazanın takvim gününe göre açılır ve gece yarısından sonra UTC bir gün
    geride kalır."""
    return datetime.now(UTC).astimezone().date().isoformat()


class PreviewError(RuntimeError):
    """Önizleme görüntüsü üretilemedi. Rapor yine de kaydedilmiştir."""


class BundlesService:
    """Setler ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ."""

    def __init__(self, *, api: Any, store: Any, log: Any, config: dict[str, Any],
                 printer: Any = None, category: str = "Mağaza", subcategory: str = "Ürün",
                 fallback_dir: Path | None = None) -> None:
        self._api = api
        self._store = store
        self._log = log
        self._config = config or {}
        self._printer = printer
        self._category = category
        self._subcategory = subcategory
        self._fallback = fallback_dir or Path.home() / "km-raporlar"

        self._plan_table = store.table("plan")
        self._audit_table = store.table("audit")

        # Bileşen ürünlerinin BELLEKTEKİ tazelik kopyası. Kalıcı olsaydı
        # mağazadaki fiyat değişikliğinden sonra yanlış kâr gösterirdi.
        self._cache: dict[int, tuple[float, dict[str, Any]]] = {}
        # Kategori taramasından gelen HAFİF satırlar. Ayrı durur: seçici ucu
        # maliyet döndürmüyor, o satırı fiyat/maliyet önbelleğine koymak aynı
        # ürün bir başka setin bileşeniyse kârı "hesaplanamadı" yapardı.
        self._light: dict[int, dict[str, Any]] = {}

    # ------------------------------------------------------------- ayarlar

    @property
    def _channel(self) -> str:
        return str(self._config.get("channel") or "default")

    @property
    def _locale(self) -> str:
        return str(self._config.get("locale") or "tr")

    @property
    def _set_category(self) -> int:
        return max(1, bundles.as_int(self._config.get("set_category_id"), 42))

    @property
    def _tax_rate(self) -> float:
        try:
            return max(0.0, float(self._config.get("tax_rate", 20)))
        except (TypeError, ValueError):
            return 20.0

    @property
    def _includes_tax(self) -> bool:
        return bool(self._config.get("prices_include_tax", True))

    @property
    def _fetch_cap(self) -> int:
        return max(10, min(400, bundles.as_int(self._config.get("component_fetch_cap"), 80)))

    @property
    def _max_sets(self) -> int:
        return max(5, min(500, bundles.as_int(self._config.get("max_sets"), 60)))

    @property
    def _cache_seconds(self) -> int:
        return max(0, min(3600, bundles.as_int(self._config.get("cache_seconds"),
                                               DEFAULT_CACHE_SECONDS)))

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

    # ------------------------------------------------------ yerel tablolar

    async def _plans(self) -> dict[int, dict[str, Any]]:
        """Yerel set künyeleri. Okunamazsa BOŞ döner ve ekran mağazayla çalışır."""
        try:
            rows = await self._store.fetch_all(
                f"SELECT product_id, name, sku, pricing_mode, discount_percent, set_price, "
                f"tax_rate, valid_from, valid_to, note, components, updated_at, actor "
                f"FROM {self._plan_table} ORDER BY product_id", ())
        except Exception as failure:  # noqa: BLE001 — künye okunamadı, ekran dursun
            self._log.warning("set künyeleri okunamadı", error=str(failure))
            return {}

        out: dict[int, dict[str, Any]] = {}
        for row in rows or []:
            try:
                components = json.loads(row["components"] or "[]")
            except (TypeError, ValueError):
                components = []
            definition = bundles.definition_row({
                "productId": row["product_id"], "name": row["name"], "sku": row["sku"],
                "pricingMode": row["pricing_mode"], "discountPercent": row["discount_percent"],
                "setPrice": None if row["set_price"] is None
                else bundles.from_kurus(row["set_price"]),
                "taxRate": row["tax_rate"], "validFrom": row["valid_from"],
                "validTo": row["valid_to"], "note": row["note"], "components": components,
            })
            definition["updatedAt"] = bundles.text(row["updated_at"])[:19]
            definition["actor"] = bundles.text(row["actor"])
            out[definition["productId"]] = definition
        return out

    async def _write_plan(self, definition: dict[str, Any], actor: str) -> None:
        await self._store.execute(
            f"INSERT INTO {self._plan_table} "
            "(product_id, name, sku, pricing_mode, discount_percent, set_price, tax_rate, "
            " valid_from, valid_to, note, components, actor, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(product_id) DO UPDATE SET name = excluded.name, sku = excluded.sku, "
            "pricing_mode = excluded.pricing_mode, discount_percent = excluded.discount_percent, "
            "set_price = excluded.set_price, tax_rate = excluded.tax_rate, "
            "valid_from = excluded.valid_from, valid_to = excluded.valid_to, "
            "note = excluded.note, components = excluded.components, actor = excluded.actor, "
            "updated_at = excluded.updated_at",
            (definition["productId"], definition["name"], definition["sku"],
             definition["pricingMode"], definition["discountPercent"], definition["setPrice"],
             definition["taxRate"], definition["validFrom"], definition["validTo"],
             definition["note"],
             json.dumps(definition["components"], ensure_ascii=False), actor, _now()),
        )

    async def _record(self, *, bundle_id: int, action: str, reason: str, actor: str,
                      result: str, detail: Any = None) -> None:
        """Yerel denetim izi. Bagisto gerekçeyi tutmuyor; ayrıca ağ koparsa
        "ne yapmaya çalıştık" kaydı yalnız burada kalır."""
        try:
            await self._store.execute(
                f"INSERT INTO {self._audit_table} "
                "(bundle_id, action, reason, actor, result, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (int(bundle_id or 0), action, reason, actor, result,
                 json.dumps(detail or {}, ensure_ascii=False), _now()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın
            self._log.warning("denetim izi yazılamadı", action=action, error=str(failure))

    # ==================================================== mağazadan okuma

    async def _product(self, product_id: int) -> dict[str, Any] | None:
        """Tek ürün — bellek tazeliğiyle. Okunamazsa `None`, istisna sızmaz."""
        stamp = self._cache.get(product_id)
        if stamp and (time.monotonic() - stamp[0]) < self._cache_seconds:
            return stamp[1]
        try:
            raw = await self._api.product(int(product_id))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.info("bileşen okunamadı", productId=product_id, error=str(failure))
            return None
        # Detay yanıtı kaynak bazlı stoğu da taşıyor; vitrindeki `quantity`
        # türetilmiş ve geç güncellenen bir alan. Elimizdeyken kesin olanı
        # kullanırız (satır `stockExact` ile işaretlenir).
        view = bundles.product_view(raw, inventories=raw.get("inventories"), today=_today())
        view["id"] = view["id"] or int(product_id)
        self._cache[product_id] = (time.monotonic(), view)
        return view

    async def _component_products(self, ids: list[int]) -> tuple[dict[int, dict[str, Any]], int]:
        """Bileşen ürünlerini SIRAYLA okur. Tavan aşılırsa kalanı okumaz.

        Paralel istek kantin deneyiminde Bagisto tarafında kilit yüzünden 5xx
        üretmişti; geçidin hız kovası da (dk 55) sıralı davranışı bekliyor.
        """
        wanted = list(dict.fromkeys(int(item) for item in ids if int(item or 0) > 0))
        skipped = max(0, len(wanted) - self._fetch_cap)
        found: dict[int, dict[str, Any]] = {}
        for product_id in wanted[:self._fetch_cap]:
            view = await self._product(product_id)
            if view is not None:
                found[product_id] = view
        return found, skipped

    async def _store_sets(self) -> tuple[list[dict[str, Any]], str, str, int]:
        """Mağazadaki set tanımları. Dönüş: (tanımlar, kaynak, hata, okunmayan).

        Önce BBD ucu denenir; yoksa "Setler" kategorisi taranır. İki yol da
        kapalıysa hata metniyle boş liste döner — ekran yerel künyelerle
        çalışmaya devam eder.

        KATEGORİ SÜZGECİ HANGİ UÇTA ÇALIŞIYOR. `/api/admin/catalog/products`
        `category_id` parametresini TANIMIYOR ve Laravel tanımadığı sorgu
        parametresini sessizce yok sayıyor: o uca sorulduğunda 1.421 ürünün
        ilk sayfası "set listesi" diye geri geliyordu. Süzgeci gerçekten
        uygulayan uç seçici ucu (`/api/admin/products`, `product_lookup`);
        canlıda `category_id=42` ile `total: 1` dönüyor, süzgeçsiz `1421`.
        Seçici ucu sayfalamaz: en çok 50 satır verir, fazlası sayılıp ekrana
        söylenir (sessizce eksik liste gösterilmez).
        """
        try:
            payload = await self._api.bbd_bundles({"channel": self._channel})
        except Exception as failure:  # noqa: BLE001 — uç henüz yayında olmayabilir
            self._log.info("BBD set ucu kullanılamadı", error=self._fail(failure))
        else:
            items = payload.get("items") or []
            missed = max(0, len(items) - self._max_sets)
            return ([bundles.definition_row(item) for item in items][:self._max_sets],
                    "bbd", "", missed)

        try:
            payload = await self._api.product_lookup(
                {"category_id": self._set_category, "channel": self._channel,
                 "locale": self._locale},
                per_page=min(50, self._max_sets))
        except Exception as failure:  # noqa: BLE001 — K7
            return [], "offline", self._fail(failure), 0

        items = payload.get("items") or []
        total = bundles.as_int((payload.get("meta") or {}).get("total"), len(items))
        out: list[dict[str, Any]] = []
        for raw in items[:self._max_sets]:
            view = bundles.product_view(raw, today=_today())
            if not view["id"]:
                continue
            self._light[view["id"]] = view
            out.append(bundles.definition_row({
                "productId": view["id"], "name": view["name"], "sku": view["sku"],
            }))
        return out, "category", "", max(0, total - len(out))

    async def _carousel_ids(self) -> set[int]:
        """Ana sayfa carousel'indeki ürünler. Ekran yalnız rozet çizmek için okur;
        düzenleme `store_home_media` ekranının işidir."""
        slot = bundles.as_int(self._config.get("carousel_id"))
        if not slot:
            return set()
        try:
            payload = await self._api.bbd_carousel({"id": slot})
        except Exception as failure:  # noqa: BLE001 — uç yoksa rozet çizilmez
            self._log.info("carousel okunamadı", error=str(failure))
            return set()
        found: set[int] = set()
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            # İLK DOLU ALAN KAZANIR, hepsi değil. Slot hem `productId` hem
            # `target_id` taşıyabiliyor ve `target_id` ürün olmak zorunda değil
            # (kategori/CMS hedefi de olabiliyor). Üçünü birden toplamak, o
            # kimlikle çakışan başka bir sete “ana sayfa” rozeti taktırırdı.
            for key in ("product_id", "productId", "target_id"):
                slot_product = bundles.as_int(item.get(key))
                if slot_product:
                    found.add(slot_product)
                    break
        return found

    # ================================================================ liste

    async def _rows(self) -> dict[str, Any]:
        """Ekranın tüm listesi: tanım + taze ürün verisi + hesap."""
        plans = await self._plans()
        self._light.clear()          # her yenilemede taze; bayat ad/fiyat kalmasın
        store_sets, source, error, missed = await self._store_sets()

        definitions: dict[int, dict[str, Any]] = {}
        for item in store_sets:
            if item["productId"]:
                definitions[item["productId"]] = item
        # Yerel künye mağazadaki tanımın ÜSTÜNE biner: adet/indirim/zorunluluk
        # yalnız burada tutuluyor (bkz. migrations/001_bundles.sql). Mağazadan
        # gelen ad/SKU korunur — orası kaydın sahibidir.
        for product_id, plan in plans.items():
            base = definitions.get(product_id) or {}
            merged = {**base, **plan, "productId": product_id}
            merged["name"] = base.get("name") or plan["name"]
            merged["sku"] = base.get("sku") or plan["sku"]
            merged["components"] = plan["components"] or base.get("components") or []
            definitions[product_id] = merged

        component_ids: list[int] = []
        for item in definitions.values():
            component_ids.extend(part["productId"] for part in item.get("components") or [])
        products, skipped = await self._component_products(component_ids)

        carousel = await self._carousel_ids()
        today = _today()
        rows: list[dict[str, Any]] = []
        for product_id, item in definitions.items():
            # Kategori taramasından gelen satır ekranın set sütunları için
            # yeterli (ad, SKU, durum, vitrin fiyatı, görsel); yalnız yerel
            # künyesi olup kategoride görünmeyen setler için ek istek çıkar.
            set_product = self._light.get(product_id) or await self._product(product_id)
            rows.append(self._row(item, set_product, products, carousel, today))

        rows.sort(key=bundles.sort_key)
        return {
            "items": rows, "source": source, "error": error,
            "connected": source != "offline",
            "planCount": len(plans), "skipped": skipped, "missed": missed,
        }

    def _row(self, definition: dict[str, Any], set_product: dict[str, Any] | None,
             products: dict[int, dict[str, Any]], carousel: set[int],
             today: str) -> dict[str, Any]:
        components = [bundles.merge_component(part, products.get(part["productId"]))
                      for part in definition.get("components") or []]

        # Set fiyatı: künyede yazılı değilse MAĞAZADAKİ ürün fiyatı geçerlidir —
        # müşterinin ödediği tutar odur. Uydurma bir fiyatla kâr hesaplamak
        # ekranı yalancı yapar.
        price = definition.get("setPrice")
        if price is None and set_product:
            price = bundles.to_kurus(set_product.get("effectivePrice"))

        rate = definition["taxRate"] if definition.get("taxRate") is not None \
            else self._tax_rate
        calc = bundles.compute(components, pricing_mode=definition.get("pricingMode", "fixed"),
                               discount_percent=definition.get("discountPercent", 0),
                               set_price=price, tax_rate=rate,
                               prices_include_tax=self._includes_tax)
        marks = bundles.flags(calc, components)
        product_id = definition["productId"]
        return {
            "id": product_id,
            "sku": definition.get("sku") or (set_product or {}).get("sku", ""),
            "name": definition.get("name") or (set_product or {}).get("name", f"#{product_id}"),
            "status": bool((set_product or {}).get("status")),
            "imageUrl": (set_product or {}).get("imageUrl", ""),
            "storePrice": (set_product or {}).get("effectivePrice"),
            "readable": set_product is not None,
            "pricingMode": definition.get("pricingMode", "fixed"),
            # KÜNYEDE YAZILI fiyat — `calc.setPrice` ile aynı şey DEĞİL: künye
            # boşsa hesap mağazadaki fiyatı kullanır. Düzenleyici bu alanı
            # okur; hesabın sonucunu forma yazsaydı ilk kaydetmede mağaza
            # fiyatı yerele donar ve mağazadaki zam bir daha yansımazdı.
            "planPrice": definition.get("setPrice"),
            "discountPercent": definition.get("discountPercent", 0),
            "taxRate": rate,
            "validFrom": definition.get("validFrom", ""),
            "validTo": definition.get("validTo", ""),
            "validity": bundles.validity_state(definition.get("validFrom", ""),
                                               definition.get("validTo", ""), today),
            "note": definition.get("note", ""),
            "updatedAt": definition.get("updatedAt", ""),
            "components": calc["lines"],
            "calc": calc,
            "flags": marks,
            "warning": bundles.warning_text(marks),
            "onCarousel": product_id in carousel,
            "hasPlan": bool(definition.get("components")),
        }

    async def bundles(self) -> dict[str, Any]:
        """Set listesi. Küçük bir küme olduğu için TEK seferde gelir; süzme ve
        sıralama ekranda yapılır (1.419 ürünlük katalogla karıştırılmamalı)."""
        try:
            payload = await self._rows()
        except Exception as failure:  # noqa: BLE001 — ekran ayakta kalmalı (K7)
            self._log.warning("set listesi kurulamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "items": [], "source": "offline", "notice": "", "skipped": 0, "missed": 0}
        return {
            "ok": True, "connected": payload["connected"], "error": payload["error"],
            "items": payload["items"], "source": payload["source"],
            "skipped": payload["skipped"], "missed": payload["missed"],
            "setCategoryId": self._set_category,
            "notice": self._source_notice(payload["source"], payload["skipped"],
                                          payload["missed"]),
            "taxRate": self._tax_rate, "pricesIncludeTax": self._includes_tax,
        }

    def _source_notice(self, source: str, skipped: int, missed: int = 0) -> str:
        base = {
            "bbd": "Set tanımları mağazadaki BBD ucundan geliyor.",
            "category": f"Mağazada set ürün tipi yok: liste, {self._set_category} numaralı "
                        "“Setler” kategorisindeki ürünlerden kuruluyor. Bileşen adedi, "
                        "bileşen indirimi ve zorunluluk bilgisi Kontrol Merkezi'nde tutulur.",
            "offline": "Mağazaya ulaşılamadı; yalnız yerel künyeler listeleniyor ve fiyatlar "
                       "okunamadı.",
        }.get(source, "")
        if missed:
            base += (f" Kategoride {missed} set daha var ama tek istekte en çok 50 satır "
                     "geliyor; liste EKSİK. Kalanı görmek için kategoriyi bölmek ya da BBD "
                     "set ucunu yayına almak gerekir.")
        if skipped:
            base += (f" Bileşen okuma tavanı aşıldı: {skipped} ürün okunmadı, o satırların "
                     "hesabı eksik. Ayardan `component_fetch_cap` büyütülebilir.")
        return base

    async def bundle(self, bundle_id: int) -> dict[str, Any]:
        """Tek setin düzenleyici künyesi (bileşen tablosu + canlı hesap)."""
        listing = await self.bundles()
        for row in listing["items"]:
            if row["id"] == int(bundle_id):
                return {"ok": True, "connected": listing["connected"], "error": listing["error"],
                        "bundle": row, "crossSells": await self._cross_sells(bundle_id)}
        product = await self._product(int(bundle_id))
        if product is None:
            return {"ok": False, "error": "Set okunamadı; mağazaya ulaşılamıyor olabilir."}
        empty = bundles.definition_row({"productId": int(bundle_id), "name": product["name"],
                                        "sku": product["sku"]})
        return {"ok": True, "connected": True, "error": "",
                "bundle": self._row(empty, product, {}, set(), _today()),
                "crossSells": await self._cross_sells(bundle_id)}

    async def _cross_sells(self, bundle_id: int) -> list[dict[str, Any]]:
        """Mağazadaki çapraz satış bağları — "bileşen olarak al" düğmesini besler.

        Bağ ADET ve İNDİRİM taşımaz; buradan gelen her kalem adet 1, indirim 0
        ve zorunlu olarak eklenir. Ekran bunu söyler.
        """
        try:
            raw = await self._api.product(int(bundle_id))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.info("çapraz satış bağları okunamadı", error=str(failure))
            return []
        out: list[dict[str, Any]] = []
        for key in ("cross_sells", "crossSells", "cross_sell_products"):
            items = raw.get(key) if isinstance(raw, dict) else None
            if not isinstance(items, list):
                continue
            for item in items:
                view = bundles.product_view(item, today=_today()) if isinstance(item, dict) else {}
                if view.get("id"):
                    out.append({"productId": view["id"], "sku": view["sku"],
                                "name": view["name"]})
            break
        return out

    async def lookup(self, *, q: str = "", limit: int = 40) -> dict[str, Any]:
        """Bileşen seçicisini besleyen hafif ürün araması.

        ARAMA PARAMETRESİ `query`. Seçici ucu `name` diye bir süzgeç tanımıyor
        ve Laravel tanımadığı parametreyi sessizce yok sayıyor: `name=…` ile
        sorulduğunda kullanıcı ne yazarsa yazsın kataloğun ilk 40 ürünü
        dönüyordu. Canlıda `query=Geometri` → 67 kayıt, `name=Geometri` →
        1.421. `query` hem ada hem SKU'ya bakıyor.
        """
        filters: dict[str, Any] = {"channel": self._channel, "locale": self._locale}
        query = bundles.text(q)
        if query:
            filters["query"] = query
        try:
            payload = await self._api.product_lookup(filters,
                                                     per_page=max(5, min(50, int(limit))))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": True, "connected": False, "error": self._fail(failure), "items": []}
        items = []
        for raw in payload.get("items") or []:
            view = bundles.product_view(raw, today=_today())
            if view["id"]:
                items.append({"id": view["id"], "sku": view["sku"], "name": view["name"],
                              "price": view["effectivePrice"], "status": view["status"]})
        return {"ok": True, "connected": True, "error": "", "items": items}

    async def calc(self, *, components: Any, pricing_mode: str = "fixed",
                   discount_percent: int = 0, set_price: int | None = None,
                   tax_rate: float | None = None) -> dict[str, Any]:
        """CANLI HESAP KUTUSU. Yazmaz; fiyatları İSTEMCİDEN ALMAZ.

        Ekran yalnız tanımı (ürün, adet, indirim, zorunluluk) gönderir; birim
        fiyat ve maliyet burada mağaza verisinden çözülür. İstemcinin gönderdiği
        tutarla hesaplamak, ekranda kârlı görünen bir setin gerçekte zarar
        etmesine kapı açardı.
        """
        definitions = bundles.normalize_components(components)
        products, skipped = await self._component_products(
            [item["productId"] for item in definitions])
        rows = [bundles.merge_component(item, products.get(item["productId"]))
                for item in definitions]
        result = bundles.compute(rows, pricing_mode=pricing_mode,
                                 discount_percent=discount_percent, set_price=set_price,
                                 tax_rate=self._tax_rate if tax_rate is None else tax_rate,
                                 prices_include_tax=self._includes_tax)
        marks = bundles.flags(result, rows)
        # Satır toplamlı sürüm döner: bileşen tablosu "satır" sütununu ondan yazar.
        return {"ok": True, "error": "", "components": result["lines"], "calc": result,
                "flags": marks,
                "warning": bundles.warning_text(marks), "skipped": skipped,
                "connected": bool(products) or not definitions}

    # =============================================================== yazma

    async def save(self, bundle_id: int, *, payload: dict[str, Any], reason: str, actor: str,
                   dry_run: bool = True) -> dict[str, Any]:
        """Set künyesini kaydeder.

        İKİ AŞAMA, İKİ AYRI SONUÇ:
          1. Künye (bileşen adedi/indirimi/zorunluluğu) YEREL tabloya yazılır —
             mağazada karşılığı olmadığı için tek yer burasıdır.
          2. BBD ucu yayındaysa aynı künye mağazaya da gönderilir. Uç yoksa
             yanıt `stored: False` döner ve ekran "mağazaya yazılmadı" der;
             sessizce başarılı görünmez.

        SET FİYATINI MAĞAZAYA YAZMAZ. Set bir üründür ve ürün fiyatı yazmak
        Bagisto'da oku-değiştir-yaz gerektirir (kısmi PUT alanları boşaltır);
        o kural Ürünler ekranının işidir. Buradan fiyat düzenlemesi Ürünler
        ekranına yönlendirilir.
        """
        problem = bundles.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}

        definition = bundles.definition_row({**(payload or {}), "productId": int(bundle_id)})
        if not definition["productId"]:
            return {"ok": False, "error": "Set ürünü seçilmedi."}
        if not definition["components"]:
            return {"ok": False,
                    "error": "Set en az bir bileşen ister; bileşensiz set normal üründür."}
        if definition["pricingMode"] == "fixed" and definition["setPrice"] is None:
            # Boş bırakılabilir: o zaman mağazadaki ürün fiyatı geçerlidir.
            definition["setPrice"] = None
        if any(item["productId"] == definition["productId"] for item in definition["components"]):
            return {"ok": False, "error": "Set kendi bileşeni olamaz."}

        await self._record(bundle_id=definition["productId"], action="save_bundle", reason=reason,
                           actor=actor, result="denendi",
                           detail={"components": len(definition["components"])})
        try:
            await self._write_plan(definition, actor)
        except Exception as failure:  # noqa: BLE001 — künye yazılamazsa iş yarım kalmasın
            await self._record(bundle_id=definition["productId"], action="save_bundle",
                               reason=reason, actor=actor, result="hata",
                               detail={"error": str(failure)})
            return {"ok": False, "error": f"Set künyesi kaydedilemedi: {failure}"}

        stored, notice = False, ""
        try:
            result = await self._api.bbd_save_bundle(
                payload=self._store_payload(definition), bundle_id=definition["productId"],
                reason=reason, actor=actor, dry_run=dry_run)
            stored = not bool(result.get("dryRun", dry_run))
            notice = "" if stored else "Kuru prova: mağazaya istek gönderilmedi."
        except Exception as failure:  # noqa: BLE001 — uç henüz yayında olmayabilir (K7)
            notice = ("Set künyesi Kontrol Merkezi'nde saklandı ama mağazaya YAZILMADI: "
                      f"{self._fail(failure)}")
            self._log.info("set mağazaya yazılamadı", bundleId=definition["productId"],
                           error=str(failure))

        await self._record(bundle_id=definition["productId"], action="save_bundle", reason=reason,
                           actor=actor, result="ok" if stored else "yerel",
                           detail={"stored": stored, "dryRun": dry_run})
        return {"ok": True, "error": "", "stored": stored, "dryRun": dry_run,
                "notice": notice, "id": definition["productId"]}

    def _store_payload(self, definition: dict[str, Any]) -> dict[str, Any]:
        """BBD ucuna gidecek gövde. Alan adları uç sözleşmesine göre snake_case."""
        return {
            "product_id": definition["productId"],
            "name": definition["name"],
            "pricing_mode": definition["pricingMode"],
            "discount_percent": definition["discountPercent"],
            "set_price": None if definition["setPrice"] is None
            else bundles.from_kurus(definition["setPrice"]),
            "valid_from": definition["validFrom"] or None,
            "valid_to": definition["validTo"] or None,
            "components": [{"product_id": item["productId"], "qty": item["qty"],
                            "discount": item["discount"], "required": item["required"]}
                           for item in definition["components"]],
        }

    async def set_status(self, bundle_id: int, *, active: bool, reason: str, actor: str,
                         dry_run: bool = True) -> dict[str, Any]:
        """Seti vitrinden kaldırır/geri alır. SİLME YOKTUR (ADR 0012).

        Set bir üründür; silinseydi geçmiş siparişlerin kalemleri öksüz kalırdı.
        Yerel künye de silinmez: hangi bileşenlerden oluştuğu bilgisi, satılmış
        setlerin geçmişini okumak için gerekir.
        """
        problem = bundles.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}
        if not int(bundle_id or 0):
            return {"ok": False, "error": "Set seçilmedi."}

        action = "activate" if active else "deactivate"
        await self._record(bundle_id=bundle_id, action=action, reason=reason, actor=actor,
                           result="denendi")
        try:
            result = await self._api.update_product_status([int(bundle_id)], active=active,
                                                           reason=reason, actor=actor,
                                                           dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(bundle_id=bundle_id, action=action, reason=reason, actor=actor,
                               result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        self._cache.pop(int(bundle_id), None)
        await self._record(bundle_id=bundle_id, action=action, reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok")
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run)),
                "notice": "Vitrine yansıması birkaç dakika sürebilir (arama dizini yenilenir)."}

    async def audit(self, *, bundle_id: int = 0, limit: int = 50) -> dict[str, Any]:
        sql = (f"SELECT bundle_id, action, reason, actor, result, created_at "
               f"FROM {self._audit_table} ")
        params: tuple[Any, ...] = ()
        if bundle_id:
            sql += "WHERE bundle_id = ? "
            params = (int(bundle_id),)
        sql += "ORDER BY id DESC LIMIT ?"
        params = (*params, max(1, min(500, int(limit))))
        try:
            rows = await self._store.fetch_all(sql, params)
        except Exception as failure:  # noqa: BLE001 — iz okunamadı, ekran dursun
            return {"ok": True, "items": [], "error": self._fail(failure)}
        return {"ok": True, "error": "", "items": [
            {"bundleId": row["bundle_id"], "action": row["action"], "reason": row["reason"],
             "actor": row["actor"], "result": row["result"], "createdAt": row["created_at"]}
            for row in rows
        ]}

    # ================================================================ rapor

    async def export_csv(self) -> dict[str, Any]:
        listing = await self.bundles()
        rows = listing["items"]
        if not rows:
            return {"ok": False, "error": "Dışa aktarılacak set yok."}
        headers, table = bundles.csv_table(rows)
        # İlk üç sütun metin/sayı, 4-7 arası kuruştur; para biçimi burada verilir
        # ki `bundles.csv_table` biçimden bağımsız kalsın (PDF de onu kullanıyor).
        body = [[*line[:3],
                 *[money(cell) if cell is not None else "—" for cell in line[3:7]],
                 *line[7:]] for line in table]
        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        name = f"magaza-set-listesi-{stamp}.csv"
        # `csv_bytes` de `ExportError` atabiliyor ve çağrısı `write_private`'ın
        # ARGÜMANIYDI: yalnız `OSError` yakalayan `try` onu kaçırıyordu ve uç
        # 500 dönüyordu. K7 gereği ekran ayakta kalmalı, sebebi yazmalı.
        try:
            content = csv_bytes(headers, body)
        except ExportError as failure:
            return {"ok": False, "error": f"CSV üretilemedi: {failure}"}
        try:
            path = write_private(self._export_dir / name, content)
        except OSError as failure:
            return {"ok": False, "error": f"Dosya yazılamadı: {failure}"}
        return {"ok": True, "error": "", "path": str(path), "name": name, "rows": len(body)}

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
        if kind not in ("sets", "risk"):
            return {"ok": False, "error": f"Bilinmeyen rapor: {kind}"}
        listing = await self.bundles()
        rows = listing["items"]
        if kind == "risk":
            rows = [row for row in rows if row["warning"]]
        if not rows:
            return {"ok": False, "error": "Rapora girecek set yok."}

        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        # PDF ÜRETİMİ DE `try` İÇİNDE. `build_pdf`, `reportlab` kurulu değilse
        # `ExportError` atıyor; çağrı `try`'ın DIŞINDAYDI ve istisna uca kadar
        # çıkıp 500 üretiyordu. Ekran o durumda bomboş kalır ve sebebini
        # söyleyemezdi (K7).
        try:
            content = self._pdf(rows, only_risk=kind == "risk", notice=listing["notice"])
        except ExportError as failure:
            return {"ok": False, "error": f"Rapor üretilemedi: {failure}"}
        name = f"magaza-set-{'riskli' if kind == 'risk' else 'listesi'}-{stamp}.pdf"
        try:
            path = write_private(self._export_dir / name, content)
        except (OSError, ExportError) as failure:
            return {"ok": False, "error": str(failure)}
        self._log.info("set raporu üretildi", kind=kind, rows=len(rows), path=str(path))
        return {"ok": True, "error": "", "path": str(path), "name": name,
                "bytes": len(content), "rows": len(rows)}

    def _pdf(self, rows: list[dict[str, Any]], *, only_risk: bool, notice: str) -> bytes:
        loss = [row for row in rows if row["flags"]["loss"]]
        empty_stock = [row for row in rows if row["flags"]["outOfStock"]]
        passive = [row for row in rows if row["flags"]["passive"]]
        sections: list[dict[str, Any]] = [{
            "kind": "tiles", "title": "Özet",
            "tiles": [("Set", number(len(rows))), ("Zararına", number(len(loss))),
                      ("Bileşeni tükenmiş", number(len(empty_stock))),
                      ("Bileşeni pasif", number(len(passive)))],
        }, {
            "kind": "table", "title": "Riskli setler" if only_risk else "Set listesi",
            "headers": ["Set", "Bileşen", "Set fiyatı", "Bileşen toplamı", "Kâr", "Uyarı"],
            "align": "LRRRRL", "widths": [3, 0.8, 1.1, 1.2, 1.1, 2],
            "rows": [[row["name"], number(len(row["components"])),
                      money(row["calc"]["setPrice"] or 0),
                      money(row["calc"]["afterComponentDiscount"]),
                      money(row["calc"]["profit"]) if row["calc"]["profit"] is not None else "—",
                      row["warning"] or "—"] for row in rows[:200]],
        }]
        if notice:
            sections.append({"kind": "note", "text": notice})
        sections.append({"kind": "note",
                         "text": "Kâr, KDV hariç set fiyatından bileşen maliyetleri düşülerek "
                                 "hesaplanır. Maliyeti girilmemiş bileşen varsa kâr “—” görünür; "
                                 "sıfır maliyet varsayılmaz."})
        return build_pdf(title="Setler", subtitle=f"{len(rows)} set",
                         sections=sections, footer="Kontrol Merkezi · Mağaza")

    async def _render_pages(self, path: Path, *, max_pages: int = 12,
                            dpi: int = 110) -> list[str]:
        binary = shutil.which("pdftoppm")
        if not binary:
            raise PreviewError(
                "Önizleme üretilemedi: `pdftoppm` yok (poppler-utils kurulmalı). "
                "Rapor yine de kaydedildi ve yazdırılabilir.")
        with tempfile.TemporaryDirectory(prefix="km-set-onizleme-") as folder:
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
