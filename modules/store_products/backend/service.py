"""Ürünler — iş kuralları.

VERİ MAĞAZADADIR, KARAR BURADADIR. Katalog `store.api` geçidinden gelir (K4);
bu modül Bagisto verisinin kopyasını tutmaz. Yerel tablolar yalnız mağazada
KARŞILIĞI OLMAYAN üç şeyi saklar: yazma gerekçesi (denetim izi), toplu işlem
önizlemesi (uygulanan şeyin önizlenen şey olduğunu kanıtlar) ve bu ekrana
özel görüntü tercihi.

ÖLÇEK. Canlıda 1.419 ürün, 41 kategori. Liste HER ZAMAN sunucu tarafında
sayfalanır; tam katalog yalnız rapor üretirken ve tavanla çekilir.

UZAK SİSTEM DÜŞERSE EKRAN AYAKTA KALIR (K7): `connected: False` + `error`
döner, panel durumu anlatır. İstisna dışarı sızmaz.
"""

from __future__ import annotations

import asyncio
import base64
import functools
import json
import os
import re
import shutil
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from km_sdk import ExportError, build_pdf, csv_bytes, money, number, report_dir, write_private

from . import book, catalog, deleted, images, schema

#: Rapor için tam katalog taranırken kabul edilen üst sınır. 1.419 ürün
#: 29 sayfa eder; 3.000 tavanı büyümeye yer bırakır ve bozuk `meta` yüzünden
#: sonsuz taramayı engeller.
REPORT_ROW_CAP = 3_000

#: Tek seferde silinebilecek en çok ürün. HESAP: ürün başına DÖRT istek gider —
#: önizlemede taze okuma + satış özeti, silmede taze okuma + DELETE. Geçit
#: dakikada 55 istekte tutuyor, yani 25 ürün ≈ iki dakika. Tavanı büyütmek
#: kullanıcıyı ekran başında daha uzun bekletir ve yarıda kalma ihtimalini
#: büyütür; silme yarıda kalınca geri alınamaz. Daha büyük temizlik işi
#: birkaç turda yapılır — her tur kendi önizlemesini gösterir.
DELETE_LIMIT = 25

#: Sipariş kalemi işaretlerken kataloğa sorulan en çok BENZERSİZ ürün.
#: Bir siparişte onlarca kalem olabilir ama ürün sayısı azdır; tavan, bir
#: rapor sayfasının yüzlerce kalemiyle gelen çağrının hız kovasını
#: tüketmesini engeller.
MARK_LOOKUP_CAP = 50

#: Geçitte bulunmayan uç için TEK metin. Ekran "uç yok" ile "mağaza reddetti"
#: arasındaki farkı görebilsin diye ayrı tutulur.
GATEWAY_GAP = (
    "Geçitte (store_api) `{method}` metodu yok; K4 gereği bu modül mağazaya ham "
    "istek atmaz. Mağaza ucu var ({endpoint}) ama geçide eklenmeden buradan "
    "çağrılamaz."
)

BULK_KINDS = ("price", "stock", "category", "status", "book")

#: Toplu kitap yazmasında dokunulabilen alanlar. LİSTE BİLEREK KISA: sayfa
#: sayısı ve desi kargo ücretinin girdisidir ve bir rafın tamamına aynı değeri
#: yazmak gerçek bir iş (aynı serinin 40 fasikülü). ISBN/yazar/yayınevi ise
#: ürüne ÖZGÜdür; toplu yazmak onları hatalı hâle getirmenin en hızlı yoludur.
BULK_BOOK_FIELDS = ("pageCount", "desi")

#: Mağazanın ürün görseli ucundaki SUNUCU sınırı — 4 MB
#: (`AdminCatalogProductImageProcessor::MAX_BYTES`). Geçit de aynı sınırı
#: uyguluyor; burada TEKRAR bilinmesinin sebebi kullanıcıya sınırı dosya
#: seçilir seçilmez söyleyebilmek: geçidin reddi bir ağ turu ve bir hız kovası
#: payı harcar, üstelik hata metni "Mağaza isteği doğrulayamadı" gibi anlamsız
#: çıkar. Ayar bu tavanı aşağı çekebilir, YUKARI ÇEKEMEZ.
STORE_IMAGE_LIMIT_BYTES = 4 * 1024 * 1024

#: Nitelik listesinin çip süzgeçleri (istemci değil BURADA süzülür: 36 satırlık
#: liste küçük ama kural — "kullanılmayan" gibi bir çip kullanım hesabını
#: gerektiriyor ve o hesap burada).
ATTRIBUTE_SCOPES = ("system", "custom", "required", "filterable", "options", "unused")

#: url_key çakışmasında mağazaya gidilecek EN ÇOK tur sayısı (TUZAK 6).
#: Her tur bir istek eder ve mağaza dakikada 60 kabul ediyor; çakışma nadir
#: olduğu için ilk tur çoğu zaman yetiyor. Tur başına dönen satırlardan
#: "dolu" anahtarlar toplandığı için `roman-2`, `roman-3` sırası tek turda
#: atlanabiliyor; dördü aşan çakışmada ekran "elle yazın" der.
URL_KEY_ROUNDS = 4

#: Ürün açarken taslağa konabilen alanlar → Bagisto öznitelik adları.
#: `catalog.FIELD_ALIASES` ile aynı işi görür ama ürün AÇMA yüzeyi daha dar:
#: burada olmayan alan (örneğin indirim penceresi) düzenleyicide doldurulur.
CREATE_FIELDS = {
    "name": "name",
    "urlKey": "url_key",
    "metaTitle": "meta_title",
    "metaDescription": "meta_description",
    "shortDescription": "short_description",
    "description": "description",
}


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def _today() -> str:
    return catalog.today_iso()


class PreviewError(RuntimeError):
    """Önizleme görüntüsü üretilemedi. Rapor yine de kaydedilmiştir."""


class ProductsService:
    """Ürünler ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ."""

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

        self._audit = store.table("audit")
        self._bulk = store.table("bulk")
        self._prefs = store.table("prefs")

        #: Öznitelik ailesi bir kez çözülüp saklanır (`_default_family`).
        self._family_cache = 0

        #: Kitap alanlarının GERÇEK nitelik kodları — bir kez çözülür.
        #: `None` = henüz sorulmadı; boş sözlük "soruldu, hiçbiri yok" demektir
        #: ve o ikisi karıştırılamaz (ikincisinde tekrar sorulmaz).
        self._book_codes: dict[str, str] | None = None

    # ------------------------------------------------------------- ayarlar

    @property
    def _channel(self) -> str:
        return str(self._config.get("channel") or "default")

    @property
    def _locale(self) -> str:
        return str(self._config.get("locale") or "tr")

    @property
    def _page_size(self) -> int:
        return max(10, min(200, int(self._config.get("page_size") or 50)))

    @property
    def _export_dir(self) -> Path:
        # HER ÇAĞRIDA yeniden çözülür: ay değişince klasör kendiliğinden değişir.
        return report_dir(self._category, subcategory=self._subcategory,
                          fallback=self._fallback,
                          configured=str(self._config.get("export_path") or ""))

    async def _threshold(self) -> int:
        """Kritik stok eşiği. Bu ekranın GÖRÜNTÜ tercihidir, mağaza ayarı değil:
        yalnız hangi satırın "Kritik" boyanacağını belirler, vitrini etkilemez."""
        row = await self._pref("low_stock_threshold")
        if row:
            return catalog.as_int(row, 5)
        return catalog.as_int(self._config.get("low_stock_threshold"), 5)

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

    async def _record(self, *, product_id: int, action: str, reason: str, actor: str,
                      result: str, detail: Any = None) -> None:
        """Yerel denetim izi. Bagisto denetim tutuyor ama GEREKÇEYİ tutmuyor;
        ayrıca ağ koparsa "ne yapmaya çalıştık" kaydı burada kalır."""
        try:
            await self._store.execute(
                f"INSERT INTO {self._audit} "
                "(product_id, action, reason, actor, result, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (int(product_id or 0), action, reason, actor, result,
                 json.dumps(detail or {}, ensure_ascii=False), _now()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın
            self._log.warning("denetim izi yazılamadı", action=action, error=str(failure))

    # ------------------------------------------------------------- yardımcı

    @staticmethod
    def _fail(failure: Exception) -> str:
        message = str(failure).strip()
        return message or "Mağazaya ulaşılamadı."

    @classmethod
    def _conflict_text(cls, failure: Exception, message: str) -> str:
        """409 `conflict` ise ANLAŞILIR metin, değilse hatanın kendisi.

        Geçit kodu `code` alanında taşıyor (`StoreApiError.code`). Alan
        `getattr` ile okunur, sınıf İMPORT EDİLMEZ: modül modülü import etmez
        (K3) ve geçidin iç dosyasına bağlanmak K4'ün tek kapısını delerdi.
        Kod yoksa (başka bir istisna) davranış değişmez.
        """
        if str(getattr(failure, "code", "") or "") == "conflict":
            return message
        return cls._fail(failure)

    def _guard(self, reason: str) -> str:
        """Gerekçe backend'de DE doğrulanır (K9): arayüzde gizlemek yetmez."""
        return catalog.reason_error(reason)

    # --------------------------------------------------------- kitap alanları

    async def book_codes(self) -> dict[str, str]:
        """Kitap alanlarının mağazadaki GERÇEK nitelik kodları.

        ═══════════════════════════════════════════════════════════════════
        NEDEN SORULUYOR, NEDEN VARSAYILMIYOR

        Ekranın istediği altı alandan üçünün kodu ölçülmüş (`page_count`,
        `isbn`, `desi` — mağazanın kargo hesabı bu adlarla okuyor); kalan üçü
        (yayınevi, yazar, baskı yılı) için kataloğun hangi kodu kullandığı
        BİLİNMİYOR. Tahmin etmenin bedeli sessizdir: Bagisto tanımadığı
        özniteliği yok sayar, istek 200 döner, personel "kaydettim" der ve
        değer hiçbir yere yazılmamıştır.

        BİR KEZ SORULUR: nitelik listesi 36 satır ve kataloğun şemasıdır —
        ürün ekranı açık kaldığı sürece değişmez. Sorulamazsa BOŞ döner ve
        SAKLANMAZ; bağlantı düzelince yeniden denenir. Boş sonucu kalıcı
        saymak, geçici bir ağ hatasını "bu mağazada kitap alanı yok"a
        çevirirdi.
        """
        if self._book_codes is not None:
            return self._book_codes
        try:
            payload = await self._api.attributes()
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("kitap nitelikleri çözülemedi", error=str(failure))
            return {}
        codes = book.resolve_codes(payload.get("items") or [])
        self._book_codes = codes
        return codes

    def _book_view(self, raw: dict[str, Any], codes: dict[str, str]) -> dict[str, Any]:
        """Ürün kaydından kitap künyesi + desi dökümü.

        DESİ DÖKÜMÜ HER ZAMAN ÜRETİLİR, alanlar çözülemese bile: ürünün kargo
        hesabında kaç desi saydığı, ekranın onu düzenleyip düzenleyemediğinden
        bağımsız bir gerçektir ve personelin görmesi gereken ilk şeydir.
        """
        values = book.view(functools.partial(catalog.attribute, raw), codes)
        return {
            "values": values,
            "fields": book.field_specs(codes),
            "desi": self._desi_of(raw),
            "rules": book.rules(),
        }

    def _desi_of(self, raw: dict[str, Any], overrides: dict[str, Any] | None = None,
                 ) -> dict[str, Any]:
        """Ürünün kargo hesabındaki desisi.

        KODLAR ÇÖZÜLMÜŞ ADLARDAN DEĞİL, KANONİK ADLARDAN OKUNUR
        (`book.PAGE_CODE` / `book.DESI_CODE`). Mağazanın `DesiCalculator`'ı bu
        iki özniteliği harfi harfine okuyor; başka bir addan okumak, ekranın
        mağazadan farklı bir desi göstermesi demek olurdu.

        DÖKÜM HER ZAMAN ÜRETİLİR — nitelik listesi okunamamış olsa da. Ürünün
        kaç desi saydığı, ekranın o alanları düzenleyip düzenleyememesinden
        bağımsız bir gerçektir ve personelin görmesi gereken ilk şeydir.
        """
        picked = overrides or {}
        return book.explain(
            desi_value=picked.get("desi", catalog.attribute(raw, book.DESI_CODE)),
            pages=picked.get("pageCount", catalog.attribute(raw, book.PAGE_CODE)),
            kind=catalog.text(catalog.attribute(raw, "type")),
            sku=catalog.text(catalog.attribute(raw, "sku")),
        )

    # ================================================================ liste

    async def products(self, *, q: str = "", chip: str = "", status: str = "",
                       kind: str = "", family: str = "", category_id: int = 0,
                       price_min: int | None = None, price_max: int | None = None,
                       sort: str = "", order: str = "desc", page: int = 1,
                       size: int = 0) -> dict[str, Any]:
        """Sayfalanmış ürün listesi. SUNUCU TARAFI — tam liste çekilmez.

        `chip` üç bulgu için veri kaynağını değiştirir: tükenen/kritik/görselsiz
        ürünler ürün listesi ucuyla SÜZÜLEMEZ (stok envanterde, görsel ilişkide
        durur). Bagisto'nun sayfalı sağlık bulgusu ucu tam bunun içindir.
        """
        threshold = await self._threshold()
        per_page = size or self._page_size
        today = _today()

        if chip in ("out_of_stock", "low_stock", "no_image", "seo_missing"):
            return await self._issues(chip, page=page, size=per_page, threshold=threshold)

        filters: dict[str, Any] = {"channel": self._channel, "locale": self._locale}
        query = catalog.text(q)
        if query:
            # SKU'ya benzeyen aramayı SKU alanına yollamak, "ABC-123" yazan
            # personelin ürünü ilk denemede bulmasını sağlıyor.
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._\-/]{1,}", query):
                filters["sku"] = query
            else:
                filters["name"] = query
        if chip == "passive":
            filters["status"] = 0
        elif status != "":
            filters["status"] = 1 if status in ("1", "active", "true") else 0
        if kind:
            filters["type"] = kind
        if family:
            filters["attribute_family"] = family
        if price_min is not None:
            filters["price_from"] = catalog.from_kurus(price_min)
        if price_max is not None:
            filters["price_to"] = catalog.from_kurus(price_max)
        if sort:
            filters["sort"] = sort
            filters["order"] = "asc" if order == "asc" else "desc"
        if category_id:
            # Gönderilir ama uygulandığı VARSAYILMAZ: aşağıda doğrulanır.
            filters["category_id"] = int(category_id)

        try:
            payload = await self._api.products(filters, page=page, per_page=per_page)
            connected, error = True, ""
        except Exception as failure:  # noqa: BLE001 — ekran ayakta kalmalı (K7)
            self._log.warning("ürün listesi okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "items": [], "total": 0, "page": page, "size": per_page, "pages": 0,
                    "threshold": threshold, "categoryFilter": None, "source": "products"}

        raw = payload.get("items") or []
        rows = [catalog.product_row(item, threshold=threshold, today=today) for item in raw]
        meta = payload.get("meta") or {}
        # Kategori süzgeci gönderildi ama uygulandığı VARSAYILMAZ: Laravel
        # tanımadığı sorgu parametresini sessizce yok sayar. Yok sayıldıysa
        # süzülmemiş liste süzülmüş gibi gösterilmez; panel `categoryFilter:
        # false` görünce uyarı çizer. İstemcide süzmek de yanlış olurdu —
        # 50 satırlık sayfayı süzmek 1.419 ürünlük kataloğu temsil etmez.
        honored = (catalog.filter_honored(rows, "categoryIds", category_id)
                   if category_id else None)

        return {
            "ok": True, "connected": connected, "error": error,
            "items": rows,
            "total": catalog.as_int(meta.get("total"), len(rows)),
            "page": catalog.as_int(meta.get("currentPage"), page),
            "size": catalog.as_int(meta.get("perPage"), per_page),
            "pages": catalog.as_int(meta.get("lastPage"), 1),
            "threshold": threshold,
            "categoryFilter": honored,
            "source": "products",
        }

    async def _issues(self, kind: str, *, page: int, size: int,
                      threshold: int) -> dict[str, Any]:
        try:
            payload = await self._api.bbd_catalog_issues({"kind": kind}, page=page, per_page=size)
        except Exception as failure:  # noqa: BLE001 — uç henüz yayında olmayabilir
            self._log.warning("katalog bulguları okunamadı", kind=kind, error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "items": [], "total": 0, "page": page, "size": size, "pages": 0,
                    "threshold": threshold, "categoryFilter": None, "source": kind}

        raw = payload.get("items") or []
        meta = payload.get("meta") or {}
        return {
            "ok": True, "connected": True, "error": "",
            "items": [catalog.issue_row(item, threshold=threshold) for item in raw],
            "total": catalog.as_int(meta.get("total"), len(raw)),
            "page": catalog.as_int(meta.get("currentPage"), page),
            "size": catalog.as_int(meta.get("perPage"), size),
            "pages": catalog.as_int(meta.get("lastPage"), 1),
            "threshold": threshold, "categoryFilter": None, "source": kind,
        }

    # ================================================================ künye

    async def card(self, product_id: int) -> dict[str, Any]:
        """Ürün künyesi: kayıt + envanter + grup fiyatları tek yanıtta.

        Üç çağrı SIRAYLA yapılır. Paralel çekmek hız kovasını (dk 55) daha
        hızlı tüketiyor ve Bagisto tarafında aynı ürün üzerinde kilit üretiyor.
        """
        threshold = await self._threshold()
        try:
            raw = await self._api.product(int(product_id))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("ürün okunamadı", productId=product_id, error=str(failure))
            return {"ok": False, "connected": False, "error": self._fail(failure)}

        warnings: list[str] = []

        async def part(label: str, call: Any) -> list[dict[str, Any]]:
            """Parça parça hata: biri patlarsa künyenin gerisi yine dolar (K7)."""
            try:
                result = await call()
            except Exception as failure:  # noqa: BLE001 — uzak sistem
                warnings.append(f"{label}: {self._fail(failure)}")
                return []
            return result.get("items") or []

        inventories = await part("stok",
                                 lambda: self._api.product_inventories(int(product_id)))
        group_prices = await part("grup fiyatları",
                                  lambda: self._api.customer_group_prices(int(product_id)))
        sources = await part("depolar", self._api.inventory_sources)

        row = catalog.product_row(raw, threshold=threshold, today=_today(),
                                  inventories=inventories)
        variants = [catalog.product_row(item, threshold=threshold, today=_today())
                    for item in (raw.get("variants") or []) if isinstance(item, dict)]

        # Kitap künyesi künyenin PARÇASIDIR, ayrı bir çağrı değil: sayfa
        # sayısı ile desi aynı ekranda yan yana durmalı — biri ötekinin
        # girdisi ve personel ikisini bir arada görmeden karar veremez.
        codes = await self.book_codes()
        if not codes:
            warnings.append("kitap alanları: nitelik listesi okunamadı, alanlar açılmadı")

        return {
            "ok": True, "connected": True, "error": "", "warnings": warnings,
            "product": row,
            "book": self._book_view(raw, codes),
            "price": catalog.price_view(raw, group_prices, today=_today()),
            "inventories": catalog.inventory_rows(inventories, sources),
            "images": catalog.image_list(raw),
            "imageRules": self.image_rules(),
            "variants": variants,
            "seo": {
                "metaTitle": catalog.text(catalog.attribute(raw, "meta_title")),
                "metaKeywords": catalog.text(catalog.attribute(raw, "meta_keywords")),
                "metaDescription": catalog.text(catalog.attribute(raw, "meta_description")),
                "urlKey": row["urlKey"],
            },
            "descriptions": {
                "short": catalog.text(catalog.attribute(raw, "short_description")),
                "long": catalog.text(catalog.attribute(raw, "description")),
            },
            "threshold": threshold,
            "indexNotice": catalog.INDEX_NOTICE,
        }

    async def reference(self) -> dict[str, Any]:
        """Süzgeçlerin beslendiği referans listeler. Geçit 15/30 dk önbellekli.

        `fields` alanı TEK SEÇENEKLİ ALANLARIN tarifidir (bkz.
        `catalog.choice_fields`): mağazada kaç kanal/dil/para birimi/depo/vergi
        kategorisi olduğu sayılır ve panel hangi alanı çizeceğine buna göre
        karar verir. Sayı ekranda sabitlenmez — ikinci kanal açıldığı gün alan
        kendiliğinden geri gelir.
        """
        out: dict[str, Any] = {"ok": True, "connected": True, "error": "",
                               "categories": [], "families": [], "channels": [],
                               "locales": [], "sources": [], "types": [],
                               "currencies": [], "taxCategories": [],
                               "fields": catalog.choice_fields({})}
        out["types"] = [{"value": key, "label": label}
                        for key, label in catalog.TYPE_LABELS.items()]

        # KİTAP ALANLARI VE DESİ KATSAYILARI REFERANSTAN GELİR.
        # Panel sayfa sayısı yazılırken desiyi ANINDA hesaplıyor; katsayıları
        # kendi içine yazsaydı aynı sayı üç yerde (mağazadaki PHP, buradaki
        # Python, oradaki JS) yaşar ve biri değiştiğinde diğerleri sessizce
        # eski kalırdı. Ekranın gösterdiği desi ile müşteriden alınan ücretin
        # ayrışması, tam olarak kaçınılmak istenen şey.
        book_codes = await self.book_codes()
        out["bookFields"] = book.field_specs(book_codes)
        out["desiRules"] = book.rules()
        try:
            tree = await self._api.category_tree()
            out["categories"] = catalog.category_options(tree.get("items") or [])
        except Exception as failure:  # noqa: BLE001 — K7
            out["connected"] = False
            out["error"] = self._fail(failure)

        try:
            snapshot = await self._api.snapshot()
        except Exception as failure:  # noqa: BLE001 — K7
            out["error"] = out["error"] or self._fail(failure)
            return out

        parts = snapshot.get("parts") or {}
        out["fields"] = catalog.choice_fields(parts)
        out["families"] = [{"id": catalog.as_int(item.get("id")),
                            "name": catalog.text(item.get("name"))}
                           for item in parts.get("attribute_families") or []]
        out["channels"] = [{"code": catalog.text(item.get("code")),
                            "name": catalog.text(item.get("name"))}
                           for item in parts.get("channels") or []]
        out["locales"] = [{"code": catalog.text(item.get("code")),
                           "name": catalog.text(item.get("name"))}
                          for item in parts.get("locales") or []]
        out["sources"] = [{"id": catalog.as_int(item.get("id")),
                           "name": catalog.text(item.get("name"))}
                          for item in parts.get("inventory_sources") or []]
        out["currencies"] = [{"code": catalog.text(item.get("code")),
                              "name": catalog.text(item.get("name"))}
                             for item in parts.get("currencies") or []]
        out["taxCategories"] = [{"id": catalog.as_int(item.get("id")),
                                 "name": catalog.text(item.get("name"))}
                                for item in parts.get("tax_categories") or []]
        out["stale"] = bool(snapshot.get("stale"))
        out["storedAt"] = catalog.text(snapshot.get("storedAt"))
        return out

    async def health(self) -> dict[str, Any]:
        """Katalog sağlığı — KPI şeridini besler. Uç yoksa ekran çökmez."""
        try:
            payload = await self._api.bbd_catalog_health()
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.info("katalog sağlığı alınamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure), "tiles": {}}
        return {"ok": True, "connected": True, "error": "", "tiles": payload}

    async def audit(self, *, product_id: int = 0, limit: int = 50) -> dict[str, Any]:
        """Bu ekrandan yapılan yazmaların YEREL izi (gerekçeleriyle).

        Silinen ürünün satırları burada KALIR ve kırmızı “silinmiş” ile
        işaretlenir: "bu ürüne ne oldu" sorusunun cevabı, ürün katalogdan
        gittikten sonra da durmalıdır. İşaret AĞA ÇIKMADAN verilir — kaynağı
        kendi denetim izimizdir (`delete_product` + `ok`), dolayısıyla mağaza
        kapalıyken de doğrudur.
        """
        sql = (f"SELECT product_id, action, reason, actor, result, created_at "
               f"FROM {self._audit} ")
        params: tuple[Any, ...] = ()
        if product_id:
            sql += "WHERE product_id = ? "
            params = (int(product_id),)
        sql += "ORDER BY id DESC LIMIT ?"
        params = (*params, max(1, min(500, int(limit))))
        try:
            rows = await self._store.fetch_all(sql, params)
        except Exception as failure:  # noqa: BLE001 — iz okunamadı, ekran dursun
            return {"ok": True, "items": [], "error": self._fail(failure), "deletedIds": []}

        gone = deleted.ids_from_audit(rows)
        return {"ok": True, "error": "", "deletedIds": sorted(gone),
                "deletedLabel": deleted.LABEL, "items": [
            {"productId": row["product_id"], "action": row["action"], "reason": row["reason"],
             "actor": row["actor"], "result": row["result"], "createdAt": row["created_at"],
             "productDeleted": row["product_id"] in gone}
            for row in rows
        ]}

    # =============================================================== yazma

    async def save(self, product_id: int, *, patch: dict[str, Any], reason: str,
                   actor: str, dry_run: bool = True,
                   book_patch: dict[str, Any] | None = None) -> dict[str, Any]:
        """Tek ürünü günceller — OKU-DEĞİŞTİR-YAZ (TUZAK 1).

        Yazmadan önce ürün TAZE okunur: aradan geçen sürede başka bir ekran
        (ya da mağaza yöneticisi) aynı ürüne yazmış olabilir; kullanıcının
        dokunmadığı alan onun değeriyle geri gönderilir.

        ─────────────────────────────────────────────────────────────────────
        KİTAP ALANLARI AYRI PARAMETREDİR, `patch` İÇİNDE DEĞİL

        `patch` bu ekranın SABİT alanlarını taşır ve adları koda gömülü
        (`catalog.FIELD_ALIASES`). Kitap künyesinin nitelik kodları ise
        kuruluma göre değişiyor ve çalışma anında çözülüyor; ikisini aynı
        sözlüğe koymak, "tanınmayan anahtar sessizce düşürülür" kuralını
        kitap alanları için delmek olurdu — ya da tam tersi, yanlış yazılmış
        bir kitap alanı sessizce yok sayılırdı.

        KİTAP ALANLARI GÖNDERİLMESE DE GÖVDEYE KONUR (`extra`): kısmi PUT
        onları boşaltabilir ve boşalan bir `page_count` ürünü kargo hesabında
        varsayılan 1,0 desiye çıkarır — yani sessizce paraya dokunur.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        try:
            current = await self._api.product(int(product_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}

        if "sku" in (patch or {}):
            return {"ok": False,
                    "error": "SKU bu uçtan değiştirilmez; ayrı onay isteyen uç kullanılır."}

        codes = await self.book_codes()
        book_draft = book_patch or {}
        if book_draft:
            errors = book.draft_errors(book_draft, codes)
            if errors:
                field, message = next(iter(errors.items()))
                return {"ok": False, "error": message, "fieldErrors": errors, "field": field}

        clean = catalog.normalize_patch(patch)
        book_clean = book.patch_for(book_draft, codes)
        if not clean and not book_clean:
            return {"ok": False, "error": "Değişen alan yok."}

        body = catalog.write_body(current, {**clean, **book_clean}, channel=self._channel,
                                  locale=self._locale,
                                  sku=catalog.text(catalog.attribute(current, "sku")),
                                  extra=[code for code in codes.values() if code])
        await self._record(product_id=product_id, action="update_product", reason=reason,
                           actor=actor, result="denendi",
                           detail={"fields": sorted({*clean, *book_clean})})
        try:
            result = await self._api.update_product(int(product_id), payload=body, reason=reason,
                                                    actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(product_id=product_id, action="update_product", reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(product_id=product_id, action="update_product", reason=reason,
                           actor=actor, result="dry_run" if dry_run else "ok",
                           detail={"fields": sorted({*clean, *book_clean})})
        notice = catalog.INDEX_NOTICE if "status" in clean or "url_key" in clean else ""
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run)),
                "sent": bool(result.get("sent", not dry_run)), "notice": notice,
                "fields": sorted({*clean, *book_clean}),
                # Yazıldıktan sonraki desi: sayfa sayısı değiştiyse rakam da
                # değişti ve ekran onu yeniden okumak zorunda kalmasın.
                "desi": self._desi_of(current, book_draft)}

    async def set_status(self, product_ids: list[int], *, active: bool, reason: str,
                         actor: str, dry_run: bool = True) -> dict[str, Any]:
        """Aktif/Pasif — vitrinden kaldırır, kaydı SİLMEZ.

        Silmenin yerine geçmez, YANINDA durur: pasifleştirme geri alınabilir
        (`status` 1 yazmak yeter), silme alınamaz. "Ürün bir daha satılmayacak
        ama kaydı dursun" ile "ürün yanlış açıldı, katalogdan gitsin" ayrı iki
        istektir ve ayrı iki düğmesi vardır (silme: `delete_products`).

        Toplu uç kullanılır: 1.419 üründe tek tek PUT atmak hız sınırını aşar
        ve yarıda kalırsa katalog tutarsız kalır.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        ids = [int(item) for item in (product_ids or []) if catalog.as_int(item)]
        if not ids:
            return {"ok": False, "error": "Ürün seçilmedi."}

        action = "activate" if active else "deactivate"
        await self._record(product_id=ids[0] if len(ids) == 1 else 0, action=action,
                           reason=reason, actor=actor, result="denendi", detail={"ids": ids})
        try:
            result = await self._api.update_product_status(ids, active=active, reason=reason,
                                                           actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(product_id=0, action=action, reason=reason, actor=actor,
                               result="hata", detail={"error": str(failure), "ids": ids})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(product_id=ids[0] if len(ids) == 1 else 0, action=action,
                           reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok", detail={"ids": ids})
        return {"ok": True, "error": "", "count": len(ids),
                "dryRun": bool(result.get("dryRun", dry_run)), "notice": catalog.INDEX_NOTICE}

    async def set_stock(self, product_id: int, *, quantities: dict[str, int], reason: str,
                        actor: str, dry_run: bool = True) -> dict[str, Any]:
        """Kaynak bazlı stok yazar. Değerler MUTLAKTIR, fark değil (TUZAK 5)."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        clean = {str(key): max(0, catalog.as_int(value))
                 for key, value in (quantities or {}).items() if catalog.as_int(key)}
        if not clean:
            return {"ok": False, "error": "Yazılacak depo/adet verilmedi."}

        await self._record(product_id=product_id, action="update_inventory", reason=reason,
                           actor=actor, result="denendi", detail=clean)
        try:
            result = await self._api.update_inventory(int(product_id), quantities=clean,
                                                      reason=reason, actor=actor,
                                                      dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(product_id=product_id, action="update_inventory", reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(product_id=product_id, action="update_inventory", reason=reason,
                           actor=actor, result="dry_run" if dry_run else "ok", detail=clean)
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run)),
                "quantities": clean}

    async def set_categories(self, product_id: int, *, category_ids: list[int], reason: str,
                             actor: str, dry_run: bool = True) -> dict[str, Any]:
        """Ürünün kategori kümesini yazar. Liste TAM gönderilir.

        Bagisto bu alanı kısmi kabul etmiyor: gönderilmeyen kategori üründen
        düşüyor. Bu yüzden ekran da tam listeyi gönderir ve kullanıcıya
        "seçili olmayan kaldırılır" der — sessiz kaldırma olmaz.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        ids = sorted({int(item) for item in (category_ids or []) if catalog.as_int(item)})

        try:
            current = await self._api.product(int(product_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}

        body = catalog.write_body(current, {}, channel=self._channel, locale=self._locale,
                                  sku=catalog.text(catalog.attribute(current, "sku")))
        body["categories"] = ids
        try:
            result = await self._api.update_product(int(product_id), payload=body, reason=reason,
                                                    actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(product_id=product_id, action="set_categories", reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(product_id=product_id, action="set_categories", reason=reason,
                           actor=actor, result="dry_run" if dry_run else "ok",
                           detail={"categories": ids})
        return {"ok": True, "error": "", "categories": ids,
                "dryRun": bool(result.get("dryRun", dry_run))}

    async def change_sku(self, product_id: int, *, sku: str, reason: str, actor: str,
                         dry_run: bool = True) -> dict[str, Any]:
        """SKU değişikliği — AYRI ONAY, AYRI İZİN (TUZAK 7).

        SKU değişince Bagisto `product_flat` tablosunu ürün için yeniden yazar
        ve eski vitrin bağlantıları kırılır. Kaydet düğmesinin arkasına
        saklanmaz: kullanıcı ne olacağını okuyup ayrıca onaylar.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        wanted = catalog.text(sku)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._\-/]{1,63}", wanted):
            return {"ok": False,
                    "error": "SKU harf/rakam ile başlamalı; yalnız . _ - / içerebilir."}

        try:
            current = await self._api.product(int(product_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        before = catalog.text(catalog.attribute(current, "sku"))
        if before == wanted:
            return {"ok": False, "error": "SKU zaten bu."}

        body = catalog.write_body(current, {}, channel=self._channel, locale=self._locale,
                                  sku=wanted)
        await self._record(product_id=product_id, action="change_sku", reason=reason,
                           actor=actor, result="denendi", detail={"from": before, "to": wanted})
        try:
            result = await self._api.update_product(int(product_id), payload=body, reason=reason,
                                                    actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(product_id=product_id, action="change_sku", reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(product_id=product_id, action="change_sku", reason=reason,
                           actor=actor, result="dry_run" if dry_run else "ok",
                           detail={"from": before, "to": wanted})
        return {"ok": True, "error": "", "from": before, "to": wanted,
                "dryRun": bool(result.get("dryRun", dry_run)),
                "notice": "Eski SKU ile kayıtlı vitrin bağlantıları kırılır. "
                          + catalog.INDEX_NOTICE}

    # ================================================================ silme
    #
    # ÜRÜN SİLME GERÇEK SİLMEDİR — pasifleştirme değil. Karar kullanıcınındır
    # ve gerekçesi ölçülmüş bir gerçeğe dayanır:
    #
    #   `order_items` ürünün ADINI, SKU'sunu, FİYATINI ve TOPLAMINI kendi
    #   satırında saklıyor; `order_items.product_id` NULL kabul ediyor ve
    #   `products` tablosuna YABANCI ANAHTAR KISITI YOK
    #   (2018_09_27_113207_create_order_items_table.php:51,58-59). Yani silme
    #   ne engellenir ne de geçmişi bozar: kalem yerinde kalır, yalnız ürün
    #   bağlantısı boşa düşer ve raporda `deleted.py` kuralıyla kırmızı
    #   "silinmiş" görünür.
    #
    # MAĞAZA TARAFINDA ENGEL YOK: `AdminCatalogProductDeleteProcessor` yalnız
    # `catalog.products.delete` iznine bakıp `ProductRepository::delete` çağırıyor
    # ("No in-order guard (matches monolith ProductController::destroy)").
    # Varyantlı üründe varyantlar mağaza tarafında zincirle düşüyor.
    #
    # TEK GERÇEK ENGEL VERİTABANINDA: `rma_items.variant_id` → `products`
    # bağı ON DELETE RESTRICT (2025_11_14_173959). Ürün bir iade talebinde
    # geçiyorsa MySQL silmeyi reddeder ve uç 500 döner. Bu yüzden hata o ürün
    # için AYRI raporlanır; toplu iş yüzünden durmaz.
    #
    # TOPLU SİLME NEDEN TEK TEK YAPILIR: mağazada toplu uç var
    # (`POST /catalog/products/mass-delete`) ama tek bir ürün patlarsa
    # TAMAMINA 500 dönüyor ve hangisinin gittiği yanıttan okunamıyor. "Kısmi
    # başarı gerçekçi raporlanır" isteği o uçla karşılanamaz; sırayla silmek
    # her ürün için ayrı sonuç üretir.

    async def _sales_impact(self, product_id: int) -> dict[str, Any]:
        """Bu ürün kaç siparişte geçti, kaç adet satıldı.

        Kaynak mağazanın satış özeti tablosudur (`bbd_product_bestsellers`:
        `product_id · sold_qty · order_count · last_ordered_at`). Siparişleri
        tek tek tarayıp kalemlerine bakmak 1.419 ürünlük katalogda yüzlerce
        istek eder ve hız kovasını bitirir; özet tablo aynı soruyu tek istekte
        cevaplıyor.

        ÜÇ CEVAP: `known` (sayı var) · `unknown` (okunamadı ya da süzgeç
        uygulanmadı) · `unavailable` (geçitte uç yok). Bilinmeyeni sıfır
        saymak, "hiç satılmamış" diye gösterip kullanıcıyı yanlış bir güvenle
        sildirmek olurdu — silme geri alınamaz.
        """
        empty = {"state": "unknown", "orderCount": None, "soldQty": None, "lastOrderedAt": ""}
        fetch = getattr(self._api, "bbd_bestsellers", None)
        if fetch is None:
            return {**empty, "state": "unavailable",
                    "note": GATEWAY_GAP.format(method="bbd_bestsellers",
                                               endpoint="GET /api/admin/bbd/catalog/bestsellers")
                    + " Bu ürünün kaç siparişte geçtiği ÖĞRENİLEMEDİ."}

        try:
            payload = await fetch({"product_id": int(product_id)}, page=1, per_page=1)
        except Exception as failure:  # noqa: BLE001 — K7: silme akışı düşmesin
            self._log.info("satış özeti okunamadı", productId=product_id, error=str(failure))
            return {**empty, "note": f"Satış özeti okunamadı: {self._fail(failure)}"}

        rows = [row for row in (payload.get("items") or []) if isinstance(row, dict)]
        match = next((row for row in rows
                      if catalog.as_int(row.get("productId") or row.get("product_id"))
                      == int(product_id)), None)
        if match is None and rows:
            # Laravel tanımadığı sorgu parametresini SESSİZCE yok sayar: dönen
            # satır başka bir ürünün. Onu bu ürünün satışı saymak, silinen
            # üründen bambaşka bir rakam göstermek olurdu.
            return {**empty,
                    "note": "Mağaza satış özetini ürün bazında süzmedi (Laravel tanımadığı "
                            "parametreyi yok sayar); bu ürünün rakamı doğrulanamadı."}
        if match is None:
            return {"state": "known", "orderCount": 0, "soldQty": 0, "lastOrderedAt": "",
                    "note": "Bu ürün hiçbir siparişte geçmiyor."}
        return {
            "state": "known",
            "orderCount": catalog.as_int(match.get("orderCount") or match.get("order_count")),
            "soldQty": catalog.as_int(match.get("soldQty") or match.get("sold_qty")),
            "lastOrderedAt": catalog.text(match.get("lastOrderedAt")
                                          or match.get("last_ordered_at"))[:19],
            "note": "",
        }

    def _delete_warnings(self, rows: list[dict[str, Any]]) -> list[str]:
        """Silmeden önce ekranda okunacak uyarılar. Hepsi ölçülmüş gerçek."""
        notes = [
            ("Silme GERİ ALINAMAZ: ürün kaydı, öznitelik değerleri, görselleri, stok "
             "satırları ve kategori bağları mağazadan gider."),
            ("Geçmiş siparişler BOZULMAZ. Sipariş kalemi ürünün adını, SKU'sunu ve "
             "fiyatını kendi satırında saklıyor; kalem raporlarda kırmızı "
             f"“{deleted.LABEL}” ibaresiyle görünmeye devam eder."),
            ("Ürün bir iade talebinde (RMA) geçiyorsa mağaza veritabanı silmeyi "
             "reddeder ve o ürün “silinemedi” olarak raporlanır; diğerleri silinir."),
        ]
        if any(row.get("variantCount") for row in rows):
            notes.append("Varyantlı ürün silinince VARYANTLARI DA silinir; mağaza zinciri "
                         "kendisi düşürür.")
        if any(row.get("status") for row in rows):
            notes.append("Seçimde AKTİF ürün var: vitrinde duran bir ürünü siliyorsunuz. "
                         "Yalnız vitrinden kaldırmak istiyorsanız “Pasifleştir” geri "
                         "alınabilir bir işlemdir.")
        return notes

    async def delete_preview(self, product_ids: list[int]) -> dict[str, Any]:
        """NE SİLİNECEĞİNİ gösterir — hiçbir şey silmez, gerekçe istemez.

        Satırlar mağazadan TAZE okunur: ekranda açık duran liste beş dakika
        önce çekilmiş olabilir ve o aradan sonra ürünün stoğu, durumu ya da
        adı değişmiş olabilir. Kullanıcı bilerek silsin diye her satırın
        yanında kaç siparişte geçtiği ve kaç adet satıldığı durur.
        """
        ids = self._clean_ids(product_ids)
        if not ids:
            return {"ok": False, "error": "Ürün seçilmedi."}
        if len(ids) > DELETE_LIMIT:
            return {"ok": False,
                    "error": f"Tek seferde en çok {DELETE_LIMIT} ürün silinebilir; "
                             f"{len(ids)} seçildi. Seçimi daraltın."}

        threshold = await self._threshold()
        rows: list[dict[str, Any]] = []
        missing: list[int] = []
        for product_id in ids:
            try:
                raw = await self._api.product(product_id)
            except Exception as failure:  # noqa: BLE001 — K7: biri okunamazsa gerisi sürsün
                missing.append(product_id)
                self._log.warning("silme önizlemesi için ürün okunamadı",
                                  productId=product_id, error=str(failure))
                continue
            row = catalog.product_row(raw, threshold=threshold, today=_today())
            variants = raw.get("variants")
            rows.append({
                **row,
                "imageCount": len(catalog.image_list(raw)),
                "variantCount": len(variants) if isinstance(variants, list) else 0,
                "sales": await self._sales_impact(product_id),
            })

        if not rows:
            return {"ok": False, "error": "Seçilen ürünlerin hiçbiri okunamadı; "
                                          "okunamayan ürün silinmez."}

        capable = getattr(self._api, "delete_product", None) is not None
        return {
            "ok": True, "error": "", "rows": rows, "missing": missing,
            "summary": {
                "total": len(rows),
                "active": len([row for row in rows if row["status"]]),
                "withStock": len([row for row in rows if row["stock"] > 0]),
                "sold": len([row for row in rows
                             if (row["sales"].get("orderCount") or 0) > 0]),
                "salesUnknown": len([row for row in rows
                                     if row["sales"].get("state") != "known"]),
            },
            "warnings": self._delete_warnings(rows),
            "capable": capable,
            "capabilityError": "" if capable else GATEWAY_GAP.format(
                method="delete_product", endpoint="DELETE /api/admin/catalog/products/{id}"),
            "notice": catalog.INDEX_NOTICE,
        }

    @staticmethod
    def _clean_ids(product_ids: Any) -> list[int]:
        """Yinelenen kimlik TEK kez silinir; sıra kullanıcının seçtiği sıradır."""
        out: list[int] = []
        for item in product_ids or []:
            found = catalog.as_int(item)
            if found and found not in out:
                out.append(found)
        return out

    async def delete_products(self, product_ids: list[int], *, reason: str, actor: str,
                              dry_run: bool = True) -> dict[str, Any]:
        """Ürünleri GERÇEKTEN siler. Kısmi başarı olduğu gibi raporlanır.

        Her ürün için önce TAZE okuma yapılır: silinen şeyin adı ve SKU'su
        denetim izine yazılsın diye. Kayıt zaten yoksa o ürün “silinemedi”
        değil “zaten yok” sayılır — aynı düğmeye iki kez basmak hata
        göstermemeli.

        Sırayla gidilir ve biri patlayınca DURULMAZ: iade talebinde geçen tek
        bir ürün (RMA kısıtı) yüzünden seçimdeki diğerlerinin silinmemesi,
        kullanıcıyı listeyi tek tek elemeye zorlardı.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        ids = self._clean_ids(product_ids)
        if not ids:
            return {"ok": False, "error": "Ürün seçilmedi."}
        if len(ids) > DELETE_LIMIT:
            return {"ok": False,
                    "error": f"Tek seferde en çok {DELETE_LIMIT} ürün silinebilir; "
                             f"{len(ids)} seçildi. Seçimi daraltın."}

        remove = getattr(self._api, "delete_product", None)
        if remove is None:
            gap = GATEWAY_GAP.format(method="delete_product",
                                     endpoint="DELETE /api/admin/catalog/products/{id}")
            await self._record(product_id=ids[0] if len(ids) == 1 else 0,
                               action="delete_product", reason=reason, actor=actor,
                               result="reddedildi", detail={"ids": ids, "why": "gateway_gap"})
            return {"ok": False, "error": gap, "deleted": [], "missing": [],
                    "failed": [{"id": item, "sku": "", "name": "", "error": gap}
                               for item in ids],
                    "dryRun": bool(dry_run)}

        done: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        missing: list[dict[str, Any]] = []

        for product_id in ids:
            sku, name = "", ""
            try:
                raw = await self._api.product(product_id)
                sku = catalog.text(catalog.attribute(raw, "sku"))
                name = catalog.text(catalog.attribute(raw, "name"))
            except Exception as failure:  # noqa: BLE001 — K7
                if str(getattr(failure, "code", "") or "") == "not_found":
                    missing.append({"id": product_id, "sku": "", "name": "",
                                    "error": "Ürün mağazada bulunamadı; zaten silinmiş."})
                    await self._record(product_id=product_id, action="delete_product",
                                       reason=reason, actor=actor, result="zaten yok")
                    continue
                failed.append({"id": product_id, "sku": "", "name": "",
                               "error": f"Ürün okunamadı, silinmedi: {self._fail(failure)}"})
                continue

            await self._record(product_id=product_id, action="delete_product", reason=reason,
                               actor=actor, result="denendi", detail={"sku": sku, "name": name})
            try:
                result = await remove(product_id, reason=reason, actor=actor, dry_run=dry_run)
            except Exception as failure:  # noqa: BLE001 — biri patlarsa gerisi sürsün
                await self._record(product_id=product_id, action="delete_product", reason=reason,
                                   actor=actor, result="hata",
                                   detail={"sku": sku, "error": str(failure)})
                failed.append({"id": product_id, "sku": sku, "name": name,
                               "error": self._delete_failure(failure, sku)})
                continue

            dry = bool(result.get("dryRun", dry_run)) if isinstance(result, dict) else bool(dry_run)
            await self._record(product_id=product_id, action="delete_product", reason=reason,
                               actor=actor, result="dry_run" if dry else "ok",
                               detail={"sku": sku, "name": name})
            done.append({"id": product_id, "sku": sku, "name": name, "dryRun": dry})

        dry_all = all(row["dryRun"] for row in done) if done else bool(dry_run)
        error = ""
        if failed:
            error = f"{len(failed)} ürün silinemedi: " + " · ".join(
                f"#{row['id']} {row['sku'] or ''} — {row['error']}".strip() for row in failed)
        return {
            "ok": not failed,
            "error": error,
            "deleted": [row["id"] for row in done],
            "rows": done,
            "failed": failed,
            "missing": missing,
            "dryRun": dry_all,
            "notice": catalog.INDEX_NOTICE,
        }

    def _delete_failure(self, failure: Exception, sku: str) -> str:
        """500'ü ANLAŞILIR hâle getirir: en sık nedeni iade talebi kısıtıdır.

        Mağaza ucu tek tek silmede alttaki istisnayı 500 olarak geçiriyor
        (`AdminCatalogProductDeleteProcessor` → `InvalidInputException(..., 500)`).
        Ham metin ("Mağaza hata verdi (500)") kullanıcıya ne yapacağını
        söylemiyor; en olası nedeni söyleyen metinle değiştirilir. Kod 500
        değilse davranış değişmez.
        """
        if str(getattr(failure, "code", "") or "") != "server":
            return self._fail(failure)
        return (f"Mağaza `{sku or 'ürünü'}` silmedi (500). En olası neden: ürün bir iade "
                "talebinde (RMA) geçiyor — veritabanı o bağı silmeye kapalı tutuyor "
                "(`rma_items.variant_id` ON DELETE RESTRICT). İade talebi kapanana kadar "
                "ürün silinemez; vitrinden kaldırmak için “Pasifleştir” kullanılabilir. "
                f"Mağazanın döndürdüğü metin: {self._fail(failure)}")

    # -------------------------------------------- silinmiş ürün işaretleme

    async def _known_products(self, ids: list[int]) -> tuple[set[int], bool]:
        """Hangi kimlik hâlâ katalogda + liste TAM MI okundu.

        İkinci dönüş değeri şart: "okundu, yok" ile "okunamadı" ikisi de boş
        küme üretir ama ilkinde ürün gerçekten silinmiştir, ikincisinde hiçbir
        şey bilinmiyordur (bkz. `deleted.py`). Ayrım geçidin hata koduna
        dayanır — 404 `not_found` demek "kayıt yok", diğer her şey "okunamadı".
        """
        known: set[int] = set()
        complete = True
        for product_id in ids:
            try:
                await self._api.product(int(product_id))
            except Exception as failure:  # noqa: BLE001 — K7
                if str(getattr(failure, "code", "") or "") == "not_found":
                    continue
                complete = False
                continue
            known.add(int(product_id))
        return known, complete

    async def mark_order_items(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """Sipariş kalemlerini “silinmiş” ibaresiyle işaretler.

        Kural `deleted.py` içindedir ve `store_products.deleted_marker`
        yeteneğiyle ilan edilir; rapor ve sipariş ekranları AYNI kuralı
        kullanır, kopyalamaz (K3). Bu uç, kural için gereken katalog çözümünü
        (hangi kimlik hâlâ var) geçitten yapan tarafıdır.
        """
        rows_in = [item for item in (items or []) if isinstance(item, dict)]
        ids = deleted.product_ids(rows_in)
        capped = ids[:MARK_LOOKUP_CAP]
        known, complete = await self._known_products(capped)
        if len(ids) > MARK_LOOKUP_CAP:
            # Tavana takılan kimlikler ÇÖZÜLMEDİ; çözülmüş gibi davranmak
            # onları toptan "silinmiş" gösterirdi.
            complete = False
        rows = deleted.mark_items(rows_in, known_ids=known, lookup_complete=complete)
        return {"ok": True, "error": "", "items": rows, "summary": deleted.summary(rows),
                "lookupComplete": complete, "label": deleted.LABEL,
                "note": "" if complete else
                        f"Katalog tam okunamadı; kalemler “{deleted.UNKNOWN_LABEL}” "
                        "gösterildi — hiçbiri silinmiş sayılmadı."}

    async def check_url_key(self, *, url_key: str, product_id: int = 0) -> dict[str, Any]:
        """url_key benzersizliğini YAZMADAN ÖNCE yoklar (TUZAK 6)."""
        wanted = catalog.slugify(url_key)
        if not wanted:
            return {"ok": True, "state": "empty", "suggestion": "",
                    "message": "URL anahtarı boş olamaz."}
        try:
            payload = await self._api.products({"url_key": wanted, "channel": self._channel,
                                                "locale": self._locale}, page=1, per_page=5)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": True, "state": "unknown", "suggestion": wanted,
                    "message": f"Benzersizlik doğrulanamadı: {self._fail(failure)}"}

        items = payload.get("items") or []
        verdict = catalog.url_key_verdict(wanted, items, product_id=product_id)
        # Çakışma varsa BOŞTA OLAN bir anahtar da önerilir: kullanıcıyı
        # "kalem-2 boş mudur" diye elle denemeye bırakmak, aynı 422'yi ikinci
        # kez yemesi demekti. Öneri aynı yanıttan hesaplanır, EK İSTEK YOK.
        free = catalog.next_url_key(wanted, catalog.url_keys(items)) \
            if verdict["state"] == "taken" else wanted
        return {"ok": True, "state": verdict["state"], "suggestion": wanted, "free": free,
                "takenBy": verdict["takenBy"], "message": verdict["message"]}

    # ------------------------------------------------- ürün açma yardımcıları

    async def _unique_url_key(self, base: str, *, product_id: int = 0) -> dict[str, Any]:
        """Çakışmayan url_key'i YAZMADAN ÖNCE bulur (TUZAK 6).

        Mağazadan dönen her satırın anahtarı toplanır: url_key süzgeci `LIKE`
        gibi davranıp `roman`, `roman-2`, `roman-3`ün üçünü birden döndürürse
        sıradaki boş numara TEK TURDA bulunur. Süzgeç tam eşleşme yapıyorsa
        tur başına bir aday denenir.

        ÜÇ CEVAP vardır, ikisi değil: `free` · `taken` (aday değiştirildi) ·
        `unknown`. Laravel tanımadığı sorgu parametresini sessizce yok sayıyor;
        süzülmemiş listeyi "çakışma yok" saymak 422'yi kaydet düğmesine
        bırakmak, "çakışma var" saymak doğru yazan kullanıcıyı durdurmak olurdu.
        """
        wanted = catalog.slugify(base)
        if not wanted:
            return {"value": "", "state": "empty", "takenBy": 0, "changed": False,
                    "message": "URL anahtarı boş olamaz."}

        taken: set[str] = set()
        candidate = wanted
        for _ in range(URL_KEY_ROUNDS):
            try:
                payload = await self._api.products(
                    {"url_key": candidate, "channel": self._channel, "locale": self._locale},
                    page=1, per_page=self._page_size)
            except Exception as failure:  # noqa: BLE001 — K7: ürün açma akışı düşmesin
                self._log.warning("url anahtarı doğrulanamadı", error=str(failure))
                return {"value": candidate, "state": "unknown", "takenBy": 0,
                        "changed": candidate != wanted,
                        "message": f"Benzersizlik doğrulanamadı: {self._fail(failure)} "
                                   "Mağaza kaydetme sırasında reddedebilir."}

            items = payload.get("items") or []
            verdict = catalog.url_key_verdict(candidate, items, product_id=product_id)
            if verdict["state"] != "taken":
                return {"value": candidate, "state": verdict["state"],
                        "takenBy": verdict["takenBy"], "changed": candidate != wanted,
                        "message": verdict["message"]}

            taken.add(candidate)
            taken.update(catalog.url_keys(items))
            nxt = catalog.next_url_key(wanted, taken)
            if not nxt or nxt == candidate:
                break
            candidate = nxt

        return {"value": candidate, "state": "unknown", "takenBy": 0, "changed": True,
                "message": f"`{wanted}` ve sıradaki numaralı biçimleri dolu; "
                           "URL anahtarını elle yazın."}

    async def _category_index(self) -> tuple[dict[int, dict[str, Any]], str]:
        """Kategori ağacını geçitten okur. VARSAYILMAZ — okunamazsa söylenir."""
        try:
            tree = await self._api.category_tree()
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("kategori ağacı okunamadı", error=str(failure))
            return {}, ("Kategori ağacı okunamadı; üst kategoriler kendiliğinden "
                        f"eklenemedi: {self._fail(failure)}")
        return catalog.category_index(tree), ""

    async def _choices(self) -> dict[str, dict[str, Any]]:
        """Tek seçenekli alan tarifi. Anlık görüntü geçitte 30 dk önbellekli.

        Okunamazsa BOŞ tarif döner: her alan `state: "none"` olur, hiçbiri
        "tek seçenek" sayılmaz ve hiçbir değer kendiliğinden uygulanmaz.
        Okunamayan listeden "demek ki tek tanesi var" sonucu çıkarmak, ürüne
        yanlış vergi kategorisi yazdırırdı.
        """
        try:
            snapshot = await self._api.snapshot()
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("seçenek sayıları okunamadı", error=str(failure))
            return catalog.choice_fields({})
        return catalog.choice_fields(snapshot.get("parts") or {})

    async def _sources(self) -> tuple[list[dict[str, Any]], str]:
        """Envanter kaynakları (depolar). Stok BURAYA yazılır (TUZAK 5)."""
        try:
            payload = await self._api.inventory_sources()
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("envanter kaynakları okunamadı", error=str(failure))
            return [], f"Depolar okunamadı; stok yazılamaz: {self._fail(failure)}"
        rows = [{"id": catalog.as_int(item.get("id")),
                 "name": catalog.text(item.get("name") or item.get("code"))}
                for item in (payload.get("items") or []) if isinstance(item, dict)]
        return [row for row in rows if row["id"]], ""

    async def _draft(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Ürün açma taslağını KURAR — hiçbir şey yazmaz.

        `plan` ve `create` aynı kuralları kullansın diye tek yerdedir: panelde
        gösterilen değerle mağazaya giden değerin ayrışması, kullanıcının
        onayladığı şeyden başkasını yazmak olurdu.

        Otomatik doldurulan her alan `auto` listesine, gerekçesi `notes`
        sözlüğüne girer; panel bu ikisiyle "otomatik dolduruldu" işaretini çizer.
        """
        data = payload or {}
        auto: list[str] = []
        notes: dict[str, str] = {}
        warnings: list[str] = []

        name = catalog.text(data.get("name"))
        draft: dict[str, Any] = {
            "sku": catalog.text(data.get("sku")),
            "type": catalog.text(data.get("type")) or "simple",
            "name": name,
            "shortDescription": catalog.text(data.get("shortDescription")),
            "description": catalog.text(data.get("description")),
        }

        # --- url_key: ürün adından türetilir, çakışma yazmadan önce çözülür
        typed_key = catalog.text(data.get("urlKey"))
        url_key = await self._unique_url_key(typed_key or name)
        draft["urlKey"] = url_key["value"]
        if url_key["value"] and not typed_key:
            auto.append("urlKey")
            notes["urlKey"] = ("Ürün adından türetildi (Türkçe harfler katlandı: "
                               "ı→i, ş→s, ğ→g, ü→u, ö→o, ç→c).")
        if url_key["changed"] and url_key["value"]:
            auto.append("urlKey")          # yinelenen ad sona `sorted(set(...))` ile düşer
            notes["urlKey"] = (f"`{catalog.slugify(typed_key or name)}` mağazada kullanılıyor; "
                               f"numaralandırıldı → `{url_key['value']}`.")
        if url_key["state"] == "unknown" and url_key["message"]:
            warnings.append(url_key["message"])

        # --- SEO: boş alanlar addan ve kısa açıklamadan (düz metne indirilerek)
        seo = catalog.seo_defaults(name=name, short_description=draft["shortDescription"],
                                   description=draft["description"],
                                   meta_title=data.get("metaTitle"),
                                   meta_description=data.get("metaDescription"))
        draft["metaTitle"] = seo["metaTitle"]
        draft["metaDescription"] = seo["metaDescription"]
        auto.extend(seo["auto"])
        if "metaTitle" in seo["auto"]:
            notes["metaTitle"] = (f"Ürün adından türetildi; en çok "
                                  f"{catalog.META_TITLE_LIMIT} karakter.")
        if "metaDescription" in seo["auto"]:
            source = "kısa açıklamadan" if catalog.plain_text(draft["shortDescription"]) \
                else ("açıklamadan" if catalog.plain_text(draft["description"]) else "ürün adından")
            notes["metaDescription"] = (f"HTML düz metne indirilip {source} türetildi; en çok "
                                        f"{catalog.META_DESCRIPTION_LIMIT} karakter.")

        # --- kategori: seçilenin ÜSTLERİ de bağlanır (ağaç geçitten okunur)
        index, tree_error = await self._category_index()
        if tree_error:
            warnings.append(tree_error)
        # Panel taslakta genişletilmiş listeyi kullanıcıya gösterip onaylatıyor;
        # `expandParents: false` ile geliyor. Yazarken ikinci kez genişletmek,
        # kullanıcının listeden ÇIKARDIĞI üst kategoriyi geri koyardı.
        expand = data.get("expandParents")
        expanded = catalog.expand_categories(index, data.get("categoryIds") or [],
                                             expand=True if expand is None else bool(expand))
        draft["categoryIds"] = expanded["ids"]
        if expanded["added"]:
            auto.append("categoryIds")
            names = ", ".join(row["name"] for row in expanded["trail"] if row["auto"])
            notes["categoryIds"] = (f"Üst kategoriler ağaca göre eklendi: {names}. "
                                    "Vitrinde üst rafta da görünmesi için gerekir; "
                                    "istemediğinizi listeden çıkarabilirsiniz.")
        if expanded["unknown"] and index:
            warnings.append("Şu kategoriler ağaçta bulunamadı, olduğu gibi yazılacak: "
                            + ", ".join(f"#{item}" for item in expanded["unknown"]))

        # --- aile: ekranda alan yok, kendiliğinden çözülür (mevcut davranış)
        asked_family = catalog.as_int(data.get("attributeFamilyId"))
        family = asked_family or await self._default_family()
        draft["attributeFamilyId"] = family
        if family and not asked_family:
            auto.append("attributeFamilyId")
            notes["attributeFamilyId"] = ("Mağazadaki varsayılan öznitelik ailesi seçildi; "
                                          "ürün açıldıktan sonra değiştirilemez.")
        if not family:
            warnings.append("Mağazada öznitelik ailesi bulunamadı; ürün açılamaz.")

        # --- tek seçenekli alanlar: kanal · dil · para birimi · depo · vergi
        # Ekranda alan YOK; değer buradan gider. Karar mağazadan gelen seçenek
        # SAYISINDAN çıkar, koda gömülü bir varsayımdan değil.
        choices = await self._choices()
        tax = choices.get("taxCategoryId") or {}
        asked_tax = catalog.as_int(data.get("taxCategoryId"))
        single_tax = catalog.as_int((tax.get("auto") or {}).get("value"))
        tax_id = asked_tax or (single_tax if tax.get("state") == "single" else 0)
        draft["taxCategoryId"] = tax_id
        if tax_id and not asked_tax:
            label = catalog.text((tax.get("auto") or {}).get("label")) or f"#{tax_id}"
            auto.append("taxCategoryId")
            notes["taxCategoryId"] = (f"Mağazadaki tek vergi kategorisi (`{label}`) uygulandı; "
                                      "seçenek olmadığı için ekranda sorulmadı.")
        elif not asked_tax and tax.get("state") == "many":
            # İkinci vergi kategorisi açılmış: artık "tek seçenek" yalanı
            # söylenemez. Sessizce birini seçmek yanlış KDV oranı demektir.
            warnings.append(f"Mağazada {tax.get('count')} vergi kategorisi var; hangisinin "
                            "uygulanacağı belirsiz olduğu için ürün vergi kategorisiz "
                            "açılır. Düzenleyiciden seçin.")

        # --- stok: girilmediyse 0 yazılır, ürün "stokta yok" doğar
        sources, source_error = await self._sources()
        if source_error:
            warnings.append(source_error)
        asked_source = catalog.as_int(data.get("sourceId"))
        known = [row["id"] for row in sources]
        source_id = asked_source if asked_source in known else (known[0] if known else 0)
        draft["sourceId"] = source_id
        if source_id and source_id != asked_source:
            auto.append("sourceId")
            name_of = next((row["name"] for row in sources if row["id"] == source_id), "")
            notes["sourceId"] = f"Stok `{name_of or source_id}` deposuna yazılır."

        raw_stock = data.get("stock")
        if raw_stock in (None, ""):
            draft["stock"] = 0
            auto.append("stock")
            notes["stock"] = ("Stok girilmedi; deposuna 0 yazılır ve ürün “stokta yok” "
                              "olarak doğar. Stok satırı hiç yazılmasaydı ürün stok "
                              "takibinin dışında kalır, vitrinde belirsiz görünürdü.")
        else:
            draft["stock"] = max(0, catalog.as_int(raw_stock))

        # --- fiyat: uydurulmaz. Boşsa yazılmaz ve ne olacağı söylenir.
        price = data.get("price")
        draft["price"] = None if price in (None, "") else max(0, catalog.as_int(price))
        if draft["price"] is None and draft["type"] not in catalog.PRICELESS_TYPES:
            warnings.append("Fiyat girilmedi; ürün fiyatsız açılır. Sıfır yazmak 0 TL'lik "
                            "gerçek bir fiyattır, uydurulmaz — fiyatı düzenleyicide girin.")

        # --- durum: yeni ürün PASİF doğar
        status = data.get("status")
        draft["status"] = bool(status) if status is not None else False
        if status is None:
            auto.append("status")
            notes["status"] = ("Yeni ürün PASİF açılır: stoğu 0, görseli yok ve fiyatı henüz "
                               "denetlenmedi. Hazır olunca “Aktif” yapın.")

        return {
            "draft": draft, "auto": sorted(set(auto)), "notes": notes, "warnings": warnings,
            "urlKeyCheck": url_key, "categoryTrail": expanded["trail"], "sources": sources,
            # Panel form alanlarını buna göre çizer: tek seçenekli alan hiç
            # görünmez, çok seçenekli alan geri gelir.
            "fields": choices,
            # İki referans okuması da düştüyse mağaza ayakta değildir; taslak
            # yine döner (K7) ama ekran "bağlantı yok" der, uydurmaz.
            "connected": not (bool(tree_error) and bool(source_error)),
        }

    async def plan(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        """Ürün açma ÖNİZLEMESİ — mağazaya hiçbir şey yazmaz.

        Panel bunu ad/kategori değiştikçe çağırır ve dönen taslağı ekrana
        basar: otomatik doldurulan alan ARKADA kalmaz, işaretiyle görünür ve
        kullanıcı üzerine yazabilir. `create` aynı kuralları yeniden uygular —
        onay ile yazma arasında geçen sürede url_key kapılmış olabilir.
        """
        plan = await self._draft(payload or {})
        return {"ok": True, "error": "", **plan}

    async def _default_family(self) -> int:
        """Yeni ürünün yazılacağı öznitelik ailesi. Bulunamazsa 0.

        Sıra: ayardaki `default_family_id` → mağazadaki tek aile → kodu/adı
        `default` olan aile → ilk aile.

        Bir kez çözülüp saklanır: aile listesi yılda bir değişir, her ürün
        açılışında sorgulamanın anlamı yok.
        """
        if self._family_cache:
            return self._family_cache

        configured = catalog.as_int(self._config.get("default_family_id"))
        if configured:
            self._family_cache = configured
            return configured

        try:
            page = await self._api.families({}, per_page=100)
        except Exception as failure:  # noqa: BLE001 — K7: ürün açma akışı düşmesin
            self._log.warning("öznitelik ailesi okunamadı", error=str(failure))
            return 0

        rows = page.get("data") or page.get("items") or []
        ids = [catalog.as_int(schema.pick(row, "id")) for row in rows]
        ids = [value for value in ids if value]
        if not ids:
            return 0
        if len(ids) == 1:
            self._family_cache = ids[0]
            return ids[0]

        for row in rows:
            label = f"{schema.text(schema.pick(row, 'code'))} " \
                    f"{schema.text(schema.pick(row, 'name'))}".lower()
            if "default" in label or "varsayılan" in label:
                found = catalog.as_int(schema.pick(row, "id"))
                if found:
                    self._family_cache = found
                    return found

        self._family_cache = ids[0]
        return ids[0]

    async def create(self, *, payload: dict[str, Any], reason: str, actor: str,
                     dry_run: bool = True) -> dict[str, Any]:
        """Ürünü AÇAR VE DOLDURUR — url_key, üst kategoriler, SEO ve stok dahil.

        Bagisto ürünü İKİ AŞAMADA doğar: `POST` yalnız tip/aile/SKU kabul eder,
        geri kalan her şey ürün kimliği belli olduktan sonra `PUT` ile yazılır.
        Bu yüzden akış dört istektir:

          1. `create_product`  — tip · aile · SKU
          2. `product`         — taze kayıt (OKU-DEĞİŞTİR-YAZ, TUZAK 1)
          3. `update_product`  — ad · url_key · SEO · açıklama · fiyat · durum
                                 · kategoriler (tek gövdede, ayrı istek değil)
          4. `update_inventory`— stok (ürünün üstünde değil, depoda — TUZAK 5)

        Otomatik alanlar `_draft` ile HESAPLANIR: panel bunları `plan` ucundan
        alıp kullanıcıya göstermiştir, burada YENİDEN hesaplanır çünkü onayla
        yazma arasında url_key kapılmış olabilir. Kullanıcının yazdığı değer
        ezilmez; yalnız BOŞ alanlar doldurulur.

        KURU PROVA tek istektir: gerçek ürün doğmadığı için kimlik yoktur,
        2-4. adımlar hayalî bir kimliğe yazmak olurdu. Ne yazılacağı `steps`
        içinde "planlandı" olarak döner.

        Bir adım patlarsa ürün YİNE DE açılmış olur (K7): kimlik ve hangi
        adımın düştüğü döner, sessizce yarım kalmaz.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        plan = await self._draft(payload or {})
        draft = plan["draft"]
        sku = draft["sku"]
        family = catalog.as_int(draft["attributeFamilyId"])
        if not sku:
            return {"ok": False, "error": "SKU zorunlu."}
        if not family:
            # Öznitelik ailesi Bagisto'da ÇOK ürün tipli / çok satıcılı mağazalar
            # için var: farklı ürün grupları farklı alan setleri taşısın diye.
            # Burada tek satıcı ve tek ürün tipi (kitap) olduğu için aile bir
            # kez kurulur ve bir daha dokunulmaz. Kullanıcıya her ürün açılışında
            # sorulması anlamsız bir seçim üretirdi; sessizce çözülür.
            return {"ok": False, "error": (
                "Mağazada öznitelik ailesi bulunamadı; ürün açılamaz. "
                "Bagisto'da en az bir aile tanımlı olmalı."
            )}

        body = {"sku": sku, "type": draft["type"], "attribute_family_id": family,
                "channel": self._channel, "locale": self._locale}
        await self._record(product_id=0, action="create_product", reason=reason, actor=actor,
                           result="denendi", detail=body)
        try:
            result = await self._api.create_product(payload=body, reason=reason, actor=actor,
                                                    dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(product_id=0, action="create_product", reason=reason, actor=actor,
                               result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure), "auto": plan["auto"],
                    "notes": plan["notes"], "draft": draft}

        new_id = catalog.as_int((result.get("data") or {}).get("id")
                                if isinstance(result.get("data"), dict) else result.get("id"))
        await self._record(product_id=new_id, action="create_product", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok", detail=body)

        dry = bool(result.get("dryRun", dry_run))
        steps = [{"step": "create", "ok": True, "state": "dry_run" if dry else "ok", "error": ""}]
        if dry or not new_id:
            for step in ("details", "inventory"):
                steps.append({"step": step, "ok": True, "state": "planlandı", "error": ""})
            return {"ok": True, "error": "", "id": new_id, "dryRun": True, "steps": steps,
                    "draft": draft, "auto": plan["auto"], "notes": plan["notes"],
                    "warnings": plan["warnings"], "categoryTrail": plan["categoryTrail"],
                    "notice": catalog.INDEX_NOTICE}

        detail_step = await self._write_details(new_id, draft=draft, sku=sku, reason=reason,
                                                actor=actor)
        steps.append(detail_step)
        stock_step = await self._write_stock(new_id, draft=draft, reason=reason, actor=actor)
        steps.append(stock_step)

        failed = [step for step in steps if not step["ok"]]
        return {
            "ok": not failed,
            "error": "" if not failed else (
                f"Ürün #{new_id} açıldı ama tamamlanamadı: "
                + " · ".join(step["error"] for step in failed)
            ),
            "id": new_id, "dryRun": False, "steps": steps, "draft": draft,
            "auto": plan["auto"], "notes": plan["notes"], "warnings": plan["warnings"],
            "categoryTrail": plan["categoryTrail"], "notice": catalog.INDEX_NOTICE,
        }

    async def _write_details(self, product_id: int, *, draft: dict[str, Any], sku: str,
                             reason: str, actor: str) -> dict[str, Any]:
        """Yeni ürünün ad/url_key/SEO/fiyat/durum/kategori gövdesini yazar.

        OKU-DEĞİŞTİR-YAZ (TUZAK 1): kayıt taze okunur, gövde onun üstüne kurulur.
        Kategoriler AYNI gövdede gider — ayrı istek ikinci bir tur, ikinci bir
        hız kovası payı ve yarıda kalma ihtimali demekti.
        """
        patch: dict[str, Any] = {}
        for key, target in CREATE_FIELDS.items():
            value = catalog.text(draft.get(key))
            if value:
                patch[target] = value
        if draft.get("price") is not None:
            patch["price"] = draft["price"]
        if catalog.as_int(draft.get("taxCategoryId")):
            # Ekranda alan yok (tek kategori var), değeri yine de AÇIKÇA
            # yazılır: yazılmazsa ürün vergi kategorisiz doğar ve vitrinde
            # KDV'siz fiyat gösterir.
            patch["tax_category_id"] = catalog.as_int(draft["taxCategoryId"])
        patch["status"] = 1 if draft.get("status") else 0

        try:
            current = await self._api.product(int(product_id))
        except Exception as failure:  # noqa: BLE001 — K7: ürün açıldı, ayrıntı yazılamadı
            await self._record(product_id=product_id, action="create_details", reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"step": "details", "ok": False, "state": "hata",
                    "error": f"Ürün okunamadı, ayrıntılar yazılamadı: {self._fail(failure)}"}

        body = catalog.write_body(current, patch, channel=self._channel, locale=self._locale,
                                  sku=sku)
        categories = [int(item) for item in (draft.get("categoryIds") or [])]
        if categories:
            body["categories"] = categories

        try:
            await self._api.update_product(int(product_id), payload=body, reason=reason,
                                           actor=actor, dry_run=False)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(product_id=product_id, action="create_details", reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"step": "details", "ok": False, "state": "hata",
                    "error": f"Ayrıntılar yazılamadı: {self._fail(failure)}"}

        await self._record(product_id=product_id, action="create_details", reason=reason,
                           actor=actor, result="ok",
                           detail={"fields": sorted(patch), "categories": categories})
        return {"step": "details", "ok": True, "state": "ok", "error": "",
                "fields": sorted(patch), "categories": categories}

    async def _write_stock(self, product_id: int, *, draft: dict[str, Any], reason: str,
                           actor: str) -> dict[str, Any]:
        """Stoğu depoya yazar — GİRİLMEDİYSE 0 (TUZAK 5).

        Satır hiç yazılmasaydı ürün stok takibinin dışında kalır ve vitrinde
        "stokta var mı" sorusunun cevabı belirsiz olurdu; 0 yazmak ürünü
        açıkça "stokta yok" doğurur.
        """
        source_id = catalog.as_int(draft.get("sourceId"))
        quantity = max(0, catalog.as_int(draft.get("stock")))
        if not source_id:
            return {"step": "inventory", "ok": False, "state": "hata",
                    "error": "Depo bulunamadı; stok yazılmadı. Ürün stok takibinin dışında."}
        try:
            await self._api.update_inventory(int(product_id), quantities={str(source_id): quantity},
                                             reason=reason, actor=actor, dry_run=False)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(product_id=product_id, action="create_stock", reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"step": "inventory", "ok": False, "state": "hata",
                    "error": f"Stok yazılamadı: {self._fail(failure)}"}

        await self._record(product_id=product_id, action="create_stock", reason=reason,
                           actor=actor, result="ok",
                           detail={"sourceId": source_id, "quantity": quantity})
        return {"step": "inventory", "ok": True, "state": "ok", "error": "",
                "sourceId": source_id, "quantity": quantity}

    async def copy(self, product_id: int, *, reason: str, actor: str,
                   dry_run: bool = True) -> dict[str, Any]:
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        try:
            result = await self._api.copy_product(int(product_id), reason=reason, actor=actor,
                                                  dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        await self._record(product_id=product_id, action="copy_product", reason=reason,
                           actor=actor, result="dry_run" if dry_run else "ok")
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run))}

    async def reorder_images(self, product_id: int, *, order: list[int], reason: str,
                             actor: str, dry_run: bool = True) -> dict[str, Any]:
        """Görsel sırası. Listenin İLK elemanı kapak görselidir."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        ids = [int(item) for item in (order or []) if catalog.as_int(item)]
        if len(ids) < 2:
            return {"ok": False, "error": "Sıralamak için en az iki görsel gerekir."}
        try:
            result = await self._api.reorder_product_images(int(product_id), order=ids,
                                                            reason=reason, actor=actor,
                                                            dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        await self._record(product_id=product_id, action="reorder_images", reason=reason,
                           actor=actor, result="dry_run" if dry_run else "ok",
                           detail={"order": ids})
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run))}

    async def remove_image(self, product_id: int, image_id: int, *, reason: str, actor: str,
                           dry_run: bool = True) -> dict[str, Any]:
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        try:
            result = await self._api.remove_product_image(int(product_id), int(image_id),
                                                          reason=reason, actor=actor,
                                                          dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        await self._record(product_id=product_id, action="remove_image", reason=reason,
                           actor=actor, result="dry_run" if dry_run else "ok",
                           detail={"imageId": int(image_id)})
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run))}

    # -------------------------------------------------------- görsel yükleme

    @property
    def _image_max_bytes(self) -> int:
        """Bu ekranın görsel sınırı — ayar ile sunucu sınırının KÜÇÜĞÜ.

        Ayar 10 MB yazsa bile sunucu 4 MB'ı reddediyor; büyük sınır göstermek
        kullanıcıyı boşuna bekletip anlamsız bir 422'ye götürürdü.
        """
        wanted = max(1, min(64, catalog.as_int(self._config.get("image_max_mb"), 4)))
        return min(wanted * 1024 * 1024, STORE_IMAGE_LIMIT_BYTES)

    def image_rules(self) -> dict[str, Any]:
        """Panelin dosya SEÇİLİR SEÇİLMEZ uygulayacağı kurallar.

        Aynı kurallar `upload_image` içinde tekrar uygulanır: buradan dönen
        değerler arayüzün erken uyarı vermesi içindir, yetkilendirme değildir (K9).
        """
        return {
            "maxBytes": self._image_max_bytes,
            "accept": list(images.ACCEPTED_MIMES),
            "minWidth": max(0, catalog.as_int(self._config.get("image_min_width"),
                                              images.DEFAULT_MIN_SIDE)),
            "minHeight": max(0, catalog.as_int(self._config.get("image_min_height"),
                                               images.DEFAULT_MIN_SIDE)),
            "maxRatio": self._image_max_ratio,
        }

    @property
    def _image_max_ratio(self) -> float:
        try:
            ratio = float(self._config.get("image_max_ratio") or images.DEFAULT_MAX_RATIO)
        except (TypeError, ValueError):
            ratio = images.DEFAULT_MAX_RATIO
        return max(1.0, min(10.0, ratio))

    async def images(self, product_id: int) -> dict[str, Any]:
        """Görsel listesi — yükleme/kaldırma sonrası tazeleme içindir.

        Çekmecenin tamamını (`card`) yeniden çekmek üç istek eder ve açık olan
        formların kirli alanlarını sıfırlardı; burada tek istek yeter.
        """
        rules = self.image_rules()
        try:
            raw = await self._api.product(int(product_id))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("görseller okunamadı", productId=product_id, error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "images": [], "rules": rules}
        return {"ok": True, "connected": True, "error": "",
                "images": catalog.image_list(raw), "rules": rules}

    async def upload_image(self, product_id: int, *, filename: str, mime: str = "",
                           content: Any, position: int | None = None, reason: str,
                           actor: str, dry_run: bool = True) -> dict[str, Any]:
        """TEK görsel yükler. Çoklu seçimde panel bunu SIRAYLA çağırır.

        NEDEN DOSYA BAŞINA BİR İSTEK: hata dosya başına anlatılabiliyor
        ("üçüncü dosya 6 MB olduğu için gitmedi"), ilerleme çubuğu gerçek
        ilerlemeyi gösteriyor ve her yüklemenin kendi denetim satırı ile
        kendi `X-Bbd-Request-Id` değeri oluyor. Toplu gövde gönderilseydi
        yarıda kalan yüklemede hangi dosyanın gittiği bilinemezdi.

        Dosya İSTEK KURULMADAN ÖNCE incelenir: tür/boyut reddi mağazaya hiç
        gitmez, hız kovasından pay bile harcamaz.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem, "file": catalog.text(filename)}

        rules = self.image_rules()
        try:
            report = images.inspect_upload(
                content, filename=filename, mime=mime, max_bytes=rules["maxBytes"],
                min_width=rules["minWidth"], min_height=rules["minHeight"],
                max_ratio=rules["maxRatio"])
        except images.ImageError as failure:
            # Reddedilen dosya da denetim izine girer: "bu görsel neden
            # yüklenmedi" sorusunun cevabı ekran kapandıktan sonra da durmalı.
            await self._record(product_id=product_id, action="upload_image", reason=reason,
                               actor=actor, result="reddedildi",
                               detail={"file": catalog.text(filename), "error": str(failure)})
            return {"ok": False, "error": str(failure), "file": catalog.text(filename),
                    "rejected": True}

        detail = {"file": report["filename"], "mime": report["mime"], "bytes": report["bytes"],
                  "size": f"{report['width']}x{report['height']}"}
        await self._record(product_id=product_id, action="upload_image", reason=reason,
                           actor=actor, result="denendi", detail=detail)
        try:
            result = await self._api.upload_product_image(
                int(product_id), content=content, filename=report["filename"],
                mime=report["mime"], position=position, reason=reason, actor=actor,
                dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(product_id=product_id, action="upload_image", reason=reason,
                               actor=actor, result="hata",
                               detail={**detail, "error": str(failure)})
            return {"ok": False, "error": self._fail(failure), "file": report["filename"],
                    "warnings": report["warnings"]}

        await self._record(product_id=product_id, action="upload_image", reason=reason,
                           actor=actor, result="dry_run" if dry_run else "ok", detail=detail)
        body = result.get("data") if isinstance(result.get("data"), dict) else result
        return {
            "ok": True, "error": "", "file": report["filename"], "mime": report["mime"],
            "bytes": report["bytes"], "width": report["width"], "height": report["height"],
            "warnings": report["warnings"],
            "imageId": catalog.as_int(body.get("id")) if isinstance(body, dict) else 0,
            "dryRun": bool(result.get("dryRun", dry_run)),
        }

    async def save_group_price(self, product_id: int, *, group_id: int, qty: int, value: int,
                               price_id: int | None, reason: str, actor: str,
                               dry_run: bool = True) -> dict[str, Any]:
        """Müşteri grubu fiyatı (TUZAK 4'ün üçüncü katmanı)."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        body = {"customer_group_id": int(group_id), "qty": max(1, int(qty)),
                "value_type": "fixed", "value": catalog.from_kurus(value)}
        try:
            result = await self._api.save_customer_group_price(
                int(product_id), payload=body, price_id=price_id, reason=reason, actor=actor,
                dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        await self._record(product_id=product_id, action="group_price", reason=reason,
                           actor=actor, result="dry_run" if dry_run else "ok", detail=body)
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run))}

    # ========================================================= toplu işlem

    async def bulk_preview(self, *, kind: str, product_ids: list[int], mode: str = "",
                           amount: int = 0, rounding: str = "none", category_id: int = 0,
                           active: bool = True, field: str = "",
                           value: str = "") -> dict[str, Any]:
        """FARK TABLOSU. Toplu işlem bu tablo gösterilmeden UYGULANMAZ.

        Önizleme yerel tabloya yazılır ve bir jeton döner; uygulama o jetonla
        gelir. Böylece uygulanan şeyin önizlenen şey olduğu kanıtlanır —
        ekran açıkken fiyat değişirse `bulk_apply` sürüklenmeyi yakalar.
        """
        if kind not in BULK_KINDS:
            return {"ok": False, "error": f"Bilinmeyen toplu işlem: {kind}"}
        ids = [int(item) for item in (product_ids or []) if catalog.as_int(item)]
        if not ids:
            return {"ok": False, "error": "Ürün seçilmedi."}
        if len(ids) > 500:
            return {"ok": False,
                    "error": "Tek seferde en çok 500 ürün. Daha büyük iş için süzgeci daraltın."}

        codes: dict[str, str] = {}
        if kind == "book":
            if field not in BULK_BOOK_FIELDS:
                return {"ok": False,
                        "error": "Toplu kitap yazması yalnız sayfa sayısı ve desi için "
                                 "açıktır; ISBN, yazar ve yayınevi ürüne özgüdür."}
            codes = await self.book_codes()
            if not codes.get(field):
                return {"ok": False,
                        "error": f"Katalogda `{field}` alanına karşılık gelen nitelik yok; "
                                 "yazılsaydı mağaza isteği kabul eder ama değeri hiçbir yere "
                                 "koymazdı."}
            if mode == "set":
                problem = book.field_error(field, value)
                if problem:
                    return {"ok": False, "error": problem}

        threshold = await self._threshold()
        rows: list[dict[str, Any]] = []
        missing: list[int] = []
        for product_id in ids:
            try:
                raw = await self._api.product(product_id)
            except Exception as failure:  # noqa: BLE001 — K7
                missing.append(product_id)
                self._log.warning("önizleme için ürün okunamadı", productId=product_id,
                                  error=str(failure))
                continue
            row = catalog.product_row(raw, threshold=threshold, today=_today())
            if kind == "book":
                row["book"] = book.view(functools.partial(catalog.attribute, raw), codes)
            rows.append(row)

        if not rows:
            return {"ok": False, "error": "Seçilen ürünlerin hiçbiri okunamadı."}

        if kind == "book":
            diff = book.bulk_rows(rows, field=field, mode=mode or "set", amount=value)
        elif kind == "price":
            diff = catalog.bulk_price_rows(rows, mode=mode or "percent", amount=amount,
                                           rounding=rounding)
        elif kind == "stock":
            diff = catalog.bulk_stock_rows(rows, mode=mode or "set", amount=amount)
        elif kind == "category":
            name = await self._category_name(category_id)
            diff = catalog.bulk_category_rows(rows, action=mode or "add",
                                              category_id=category_id, category_name=name)
        else:
            diff = [{"id": row["id"], "sku": row["sku"], "name": row["name"],
                     "before": 1 if row["status"] else 0, "after": 1 if active else 0,
                     "delta": (1 if active else 0) - (1 if row["status"] else 0),
                     "skipped": row["status"] == active, "note": ""} for row in rows]

        token = uuid.uuid4().hex
        params = {"kind": kind, "mode": mode, "amount": amount, "rounding": rounding,
                  "categoryId": category_id, "active": active,
                  # Kitap yazmasında UYGULANACAK DEĞER jetonun içinde durur:
                  # uygulanan şeyin önizlenen şey olduğunun kanıtı budur.
                  "field": field, "value": value, "code": codes.get(field, "")}
        try:
            await self._store.execute(
                f"INSERT INTO {self._bulk} (token, kind, params, rows, status, created_at) "
                "VALUES (?, ?, ?, ?, 'preview', ?)",
                (token, kind, json.dumps(params, ensure_ascii=False),
                 json.dumps(diff, ensure_ascii=False), _now()),
            )
        except Exception as failure:  # noqa: BLE001 — jeton yazılamazsa uygula reddedilir
            self._log.warning("önizleme kaydedilemedi", error=str(failure))
            return {"ok": False, "error": "Önizleme kaydedilemedi; uygulama açılmadı."}

        return {"ok": True, "error": "", "token": token, "kind": kind, "rows": diff,
                "summary": catalog.diff_summary(diff), "missing": missing,
                "applicable": self._bulk_supported(kind),
                "note": self._bulk_note(kind)}

    def _bulk_supported(self, kind: str) -> bool:
        """Uygulamanın TOPLU bir uçla yapılabildiği işler.

        KİTAP ALANLARI DA SIRAYLA YAZILIR: mağazada öznitelik yazan bir toplu
        uç yok ve olsaydı bile her ürün TAZE okunmak zorunda (OKU-DEĞİŞTİR-YAZ,
        TUZAK 1). Sınırı `bulk_direct_limit` belirler ve varsayılanı 0'dır —
        yani ekran açıkça açılmadan tek satır yazmaz.
        """
        if kind == "status":
            return True
        return self._direct_limit > 0

    @property
    def _direct_limit(self) -> int:
        """Toplu uç yokken sırayla yazılmasına izin verilen ürün sayısı.

        VARSAYILAN 0 — yani kapalı. 1.419 üründe tek tek PUT atmak dakikada 60
        istek sınırına takılır, yarım saat sürer ve yarıda kalırsa kataloğun
        yarısı yeni yarısı eski fiyatta kalır. Küçük düzeltmeler için (bir
        rafın 12 ürünü) yönetici bu sınırı bilerek açabilir; açtığında ne
        olacağı ekranda yazar.
        """
        return max(0, min(100, catalog.as_int(self._config.get("bulk_direct_limit"), 0)))

    def _bulk_note(self, kind: str) -> str:
        if kind == "status":
            return "Toplu durum ucu kullanılır; tek istekte uygulanır."
        if self._direct_limit > 0:
            return (f"Mağazada toplu uç yok; en çok {self._direct_limit} ürün SIRAYLA yazılır. "
                    "Yarıda kalırsa yazılanlar kalır, kalanlar eski değerinde durur.")
        return ("Mağazada bu iş için toplu uç yok. Fark tablosunu CSV olarak alıp "
                "uygulayabilirsiniz; toplu uç yayınlanınca düğme açılacak.")

    async def _category_name(self, category_id: int) -> str:
        if not category_id:
            return ""
        try:
            payload = await self._api.category(int(category_id))
        except Exception:  # noqa: BLE001 — ad bulunamazsa numara yeter
            return f"#{category_id}"
        return catalog.text(payload.get("name")) or f"#{category_id}"

    async def bulk_apply(self, *, token: str, reason: str, actor: str, dry_run: bool = True,
                         may_deactivate: bool = False) -> dict[str, Any]:
        """Önizlenen farkı uygular. Jeton yoksa ya da tüketilmişse reddedilir.

        `may_deactivate` çağıranın `store_products.deactivate` iznini taşır.
        Toplu yol olmasaydı pasifleştirme için o izin gerekiyordu; toplu yolun
        yalnız `store_products.bulk` istemesi, ayrı tutulan yıkıcı izni arka
        kapıdan atlatmak olurdu (K9).
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        try:
            row = await self._store.fetch_one(
                f"SELECT token, kind, params, rows, status FROM {self._bulk} WHERE token = ?",
                (catalog.text(token),))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        if not row:
            return {"ok": False,
                    "error": "Önizleme bulunamadı. Fark tablosu görülmeden toplu işlem "
                             "uygulanmaz; önizlemeyi yeniden alın."}
        if row["status"] != "preview":
            return {"ok": False, "error": "Bu önizleme zaten uygulandı."}

        kind = row["kind"]
        diff = json.loads(row["rows"])
        params = json.loads(row["params"])
        targets = [item for item in diff if not item.get("skipped") and item.get("delta")]
        if not targets:
            return {"ok": False, "error": "Önizlemede değişen satır yok."}

        if kind == "status":
            if not params.get("active") and not may_deactivate:
                return {"ok": False,
                        "error": "Toplu pasifleştirme için ürün pasifleştirme izni gerekir "
                                 "(store_products.deactivate)."}
            result = await self.set_status([item["id"] for item in targets],
                                           active=bool(params.get("active")), reason=reason,
                                           actor=actor, dry_run=dry_run)
            applied, failed = (len(targets), 0) if result.get("ok") else (0, len(targets))
            error = result.get("error", "")
        elif self._direct_limit <= 0:
            return {"ok": False, "error": self._bulk_note(kind)}
        elif len(targets) > self._direct_limit:
            return {"ok": False,
                    "error": f"Seçim {len(targets)} ürün; sırayla yazma sınırı "
                             f"{self._direct_limit}. Toplu uç gerekiyor."}
        else:
            applied, failed, error = await self._apply_sequential(kind, targets, params,
                                                                  reason=reason, actor=actor,
                                                                  dry_run=dry_run)

        await self._store.execute(
            f"UPDATE {self._bulk} SET status = ?, actor = ?, reason = ?, applied_at = ? "
            "WHERE token = ?",
            ("dry_run" if dry_run else "applied", actor, reason, _now(), row["token"]),
        )
        await self._record(product_id=0, action=f"bulk_{kind}", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"applied": applied, "failed": failed, "token": row["token"]})
        return {"ok": failed == 0, "error": error, "applied": applied, "failed": failed,
                "dryRun": dry_run, "notice": catalog.INDEX_NOTICE}

    async def _apply_sequential(self, kind: str, targets: list[dict[str, Any]],
                                params: dict[str, Any], *, reason: str, actor: str,
                                dry_run: bool) -> tuple[int, int, str]:
        """Sırayla yazar. Geçit hız kovasını kendisi tutuyor; burada bekleme yok."""
        applied = 0
        failed = 0
        error = ""
        for item in targets:
            try:
                if kind == "price":
                    current = await self._api.product(int(item["id"]))
                    body = catalog.write_body(current, {"price": item["after"]},
                                              channel=self._channel, locale=self._locale,
                                              sku=catalog.text(catalog.attribute(current, "sku")))
                    await self._api.update_product(int(item["id"]), payload=body, reason=reason,
                                                   actor=actor, dry_run=dry_run)
                elif kind == "stock":
                    sources = await self._api.product_inventories(int(item["id"]))
                    rows = catalog.inventory_rows(sources.get("items") or [])
                    if not rows:
                        raise RuntimeError("Ürünün envanter kaynağı yok.")
                    # Fark TEK kaynağa yazılır: birden çok depoya bölmek iş
                    # kuralıdır ve ekranda sorulmadan varsayılamaz.
                    quantities = {str(rows[0]["sourceId"]): int(item["after"])}
                    await self._api.update_inventory(int(item["id"]), quantities=quantities,
                                                     reason=reason, actor=actor,
                                                     dry_run=dry_run)
                elif kind == "book":
                    current = await self._api.product(int(item["id"]))
                    codes = await self.book_codes()
                    code = catalog.text(params.get("code")) or codes.get(
                        catalog.text(params.get("field")), "")
                    if not code:
                        raise RuntimeError("Kitap niteliğinin kodu çözülemedi.")
                    body = catalog.write_body(
                        current, {code: item.get("value", "")}, channel=self._channel,
                        locale=self._locale,
                        sku=catalog.text(catalog.attribute(current, "sku")),
                        # DİĞER KİTAP ALANLARI DA GÖVDEYE KONUR: yalnız
                        # yazdığımızı göndermek, aynı üründeki ISBN'i ve
                        # yayınevini kısmi PUT ile boşaltırdı.
                        extra=[value for value in codes.values() if value])
                    await self._api.update_product(int(item["id"]), payload=body, reason=reason,
                                                   actor=actor, dry_run=dry_run)
                else:  # category
                    current = await self._api.product(int(item["id"]))
                    body = catalog.write_body(current, {}, channel=self._channel,
                                              locale=self._locale,
                                              sku=catalog.text(catalog.attribute(current, "sku")))
                    body["categories"] = item.get("categoryIds") or []
                    await self._api.update_product(int(item["id"]), payload=body, reason=reason,
                                                   actor=actor, dry_run=dry_run)
            except Exception as failure:  # noqa: BLE001 — biri patlarsa gerisi sürsün
                failed += 1
                error = error or self._fail(failure)
                await self._record(product_id=int(item["id"]), action=f"bulk_{kind}",
                                   reason=reason, actor=actor, result="hata",
                                   detail={"error": str(failure)})
                continue
            applied += 1
        return applied, failed, error

    # ====================================================== nitelik · aile

    async def _family_usage(self) -> tuple[dict[int, dict[str, Any]], list[str], bool]:
        """Nitelik kullanımı: aile detayları + aile başına ürün sayısı.

        Üçüncü dönüş değeri AİLE DÜZENİNİN TAM OKUNDUĞUNU söyler. Silme kararı
        buna dayanıyor: "aileler okundu, nitelik hiçbirinde yok" ile "aileler
        okunamadı" ikisi de BOŞ bir kullanım listesi üretir, ama birincisinde
        silmek güvenli, ikincisinde veri kaybı riskidir. Bu bayrak olmadan
        `delete_verdict` ikisini ayırt edemez ve emniyetli tarafta kalmak için
        silinebilir niteliği de reddeder.

        ÜRÜN SAYISI eksik kalırsa bayrak DÜŞMEZ: sayı yalnızca etkinin
        büyüklüğüdür, niteliğin hangi ailede olduğunu değiştirmez. Eksik sayı
        `usage_index` içinde zaten `None`'a (bilinmiyor) çevriliyor.

        İSTEKLER SIRAYLA gider. Paralel çekmek geçidin hız kovasını salkım
        hâlinde tüketiyor ve Bagisto tarafında 5xx üretmişti (geçit notu).
        Mağazada iki aile var; toplam 1 + 2×2 = 5 istek eder ve geçit aile
        listesini 900 sn önbelleğe alıyor.

        Ürün sayısı `attribute_family` süzgeciyle ve `per_page=1` ile alınır:
        yalnız `meta.total` gerekiyor, satırların kendisi değil. SÜZGEÇ AİLE
        KİMLİĞİNİ (id) alır, kodunu değil — canlıda doğrulandı (2026-08-13):
        `attribute_family=2` → 1.420, `attribute_family=kitap` → 0, hata yok.
        """
        notes: list[str] = []
        try:
            listing = await self._api.families()
        except Exception as failure:  # noqa: BLE001 — K7
            notes.append(f"Aileler okunamadı, kullanım sayıları boş: {self._fail(failure)}")
            return {}, notes, False

        complete = True
        details: list[dict[str, Any]] = []
        counts: dict[int, int] = {}
        for item in listing.get("items") or []:
            family_id = schema.as_int(schema.pick(item, "id"))
            if not family_id:
                # Kimliksiz satır: bu ailenin düzenini hiç okuyamayız.
                complete = False
                continue
            name = schema.text(schema.pick(item, "name")) or f"#{family_id}"
            try:
                details.append(await self._api.family(family_id))
            except Exception as failure:  # noqa: BLE001 — parça parça hata (K7)
                # Okunamayan aile niteliği TAŞIYOR olabilir; kullanım listesi
                # artık "hiçbir ailede yok" demeye yetmez.
                complete = False
                notes.append(f"`{name}` ailesinin nitelikleri okunamadı: {self._fail(failure)}")
                continue
            try:
                page = await self._api.products({"attribute_family": family_id},
                                                page=1, per_page=1)
                counts[family_id] = catalog.as_int((page.get("meta") or {}).get("total"))
            except Exception as failure:  # noqa: BLE001 — K7
                notes.append(f"`{name}` ailesinin ürün sayısı okunamadı: {self._fail(failure)}")
        return schema.usage_index(details, counts), notes, complete

    @staticmethod
    def _scope_match(row: dict[str, Any], scope: str) -> bool:
        if scope == "system":
            return bool(row["system"])
        if scope == "custom":
            return not row["system"]
        if scope == "required":
            return bool(row["required"])
        if scope == "filterable":
            return bool(row["filterable"])
        if scope == "options":
            return bool(row["hasOptions"])
        if scope == "unused":
            # Bilinmeyen kullanım "kullanılmıyor" DEĞİLDİR; çipe girmez.
            return row["usageProducts"] == 0
        return True

    async def attributes(self, *, q: str = "", kind: str = "",
                         scope: str = "") -> dict[str, Any]:
        """Nitelik listesi + kullanım sayıları. 36 nitelik, TEK sayfa.

        Süzme SUNUCUDA (burada) yapılır, panelde değil: "kullanılmayan" çipi
        kullanım hesabına dayanıyor ve o hesap ağ isteklerinden geliyor.
        """
        try:
            payload = await self._api.attributes()
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("nitelikler okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "items": [], "total": 0, "warnings": [], "types": self._type_options()}

        usage, warnings, families_known = await self._family_usage()
        rows = [schema.attribute_row(item, usage=usage.get(schema.as_int(schema.pick(item, "id"))))
                for item in (payload.get("items") or []) if isinstance(item, dict)]

        needle = catalog.text(q).casefold()
        if needle:
            rows = [row for row in rows
                    if needle in row["code"].casefold() or needle in row["name"].casefold()]
        if kind:
            rows = [row for row in rows if row["type"] == kind]
        if scope in ATTRIBUTE_SCOPES:
            rows = [row for row in rows if self._scope_match(row, scope)]
        rows.sort(key=lambda row: (row["system"], row["code"]))

        return {"ok": True, "connected": True, "error": "", "items": rows, "total": len(rows),
                "warnings": warnings, "types": self._type_options(),
                # Panel silme düğmesini buna göre kapatır: aile düzeni eksik
                # okunduysa hiçbir satır için "kullanılmıyor" denemez.
                "familiesKnown": families_known,
                "usageNote": "Kullanım = niteliğin bağlı olduğu ailelerdeki ÜRÜN SAYISI. "
                             "“Değeri dolu ürün” sayısı değildir; onu bilmek 1.419 ürünü tek "
                             "tek okumayı gerektirir. Silme kararı için doğru ölçü budur: "
                             "nitelik silinince değeri o ailedeki her üründen düşer."}

    @staticmethod
    def _type_options() -> list[dict[str, str]]:
        return [{"value": key, "label": label} for key, label in schema.TYPE_LABELS.items()]

    async def attribute(self, attribute_id: int) -> dict[str, Any]:
        """Nitelik künyesi: alanlar + seçenekler + silme kararı."""
        try:
            raw = await self._api.attribute(int(attribute_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "connected": False, "error": self._fail(failure)}

        usage, warnings, families_known = await self._family_usage()
        row = schema.attribute_row(raw, usage=usage.get(int(attribute_id)))
        return {
            "ok": True, "connected": True, "error": "", "warnings": warnings,
            "attribute": row,
            "options": schema.option_rows(raw),
            "familiesKnown": families_known,
            "delete": schema.delete_verdict(row, families_known=families_known),
            "deactivate": schema.deactivate_summary(row),
            "lockNotice": "Kod ve tip oluşturulduktan sonra DEĞİŞTİRİLEMEZ; alanlar kilitli. "
                          "Kod ürün değer tablolarında anahtar, tip ise değerin hangi sütunda "
                          "durduğu demektir — ikisi de sonradan değişirse mevcut değerler "
                          "okunamaz hâle gelir.",
        }

    async def create_attribute(self, *, code: str, kind: str, patch: dict[str, Any], reason: str,
                               actor: str, dry_run: bool = True) -> dict[str, Any]:
        """Nitelik açar. KOD ve TİP burada SON KEZ seçilir."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        for check in (schema.code_error(code), schema.type_error(kind)):
            if check:
                return {"ok": False, "error": check}
        if not catalog.text((patch or {}).get("name")):
            return {"ok": False, "error": "Görünen ad zorunlu."}

        body = schema.attribute_body(patch, creating=True, code=code, kind=kind)
        await self._record(product_id=0, action="create_attribute", reason=reason, actor=actor,
                           result="denendi", detail={"code": code, "type": kind})
        try:
            result = await self._api.create_attribute(payload=body, reason=reason, actor=actor,
                                                      dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(product_id=0, action="create_attribute", reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(product_id=0, action="create_attribute", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"code": code, "type": kind})
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run)),
                "notice": "Nitelik açıldı ama HİÇBİR AİLEDE değil; ürün ekranında görünmesi "
                          "için Aileler sekmesinden bir gruba eklenmeli."}

    async def update_attribute(self, attribute_id: int, *, patch: dict[str, Any], reason: str,
                               actor: str, dry_run: bool = True) -> dict[str, Any]:
        """Niteliği günceller. Kod ve tip GÖNDERİLMEZ (KURAL 1)."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        try:
            current = await self._api.attribute(int(attribute_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}

        # Kilit arayüzde DE var ama asıl kapı burası: istek elle de kurulabilir (K9).
        locked = schema.locked_error(current, patch or {})
        if locked:
            return {"ok": False, "error": locked}

        body = schema.attribute_body(patch, creating=False)
        if not body:
            return {"ok": False, "error": "Değişen alan yok."}

        await self._record(product_id=0, action="update_attribute", reason=reason, actor=actor,
                           result="denendi", detail={"id": int(attribute_id),
                                                     "fields": sorted(body)})
        try:
            result = await self._api.update_attribute(int(attribute_id), payload=body,
                                                      reason=reason, actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(product_id=0, action="update_attribute", reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(product_id=0, action="update_attribute", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"id": int(attribute_id), "fields": sorted(body)})
        return {"ok": True, "error": "", "fields": sorted(body),
                "dryRun": bool(result.get("dryRun", dry_run)), "notice": catalog.INDEX_NOTICE}

    async def deactivate_attribute(self, attribute_id: int, *, reason: str, actor: str,
                                   dry_run: bool = True) -> dict[str, Any]:
        """SİLME YERİNE PASİFLEŞTİRME (ADR 0012).

        Nitelik ve ürünlerdeki değerleri YERİNDE KALIR; yalnız vitrin, süzgeç
        ve zorunluluk bayrakları iner. Geri almak bayrakları açmakla olur.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        try:
            current = await self._api.attribute(int(attribute_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}

        row = schema.attribute_row(current)
        body = schema.deactivate_patch(row)
        if not body:
            return {"ok": False, "error": schema.deactivate_summary(row)}

        try:
            result = await self._api.update_attribute(int(attribute_id), payload=body,
                                                      reason=reason, actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(product_id=0, action="deactivate_attribute", reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(product_id=0, action="deactivate_attribute", reason=reason,
                           actor=actor, result="dry_run" if dry_run else "ok",
                           detail={"id": int(attribute_id), "flags": sorted(body)})
        return {"ok": True, "error": "", "flags": sorted(body),
                "dryRun": bool(result.get("dryRun", dry_run)),
                "notice": "Nitelik ve ürünlerdeki değerleri silinmedi; bayraklar geri açılarak "
                          "işlem geri alınabilir."}

    async def delete_attribute(self, attribute_id: int, *, reason: str, actor: str,
                               dry_run: bool = True) -> dict[str, Any]:
        """Niteliği siler — YALNIZ hiçbir ailede kullanılmıyorsa.

        Karar TAZE veriyle burada yeniden verilir: ekranda açık duran liste
        beş dakika önce çekilmiş olabilir ve o aradan sonra nitelik bir aileye
        eklenmiş olabilir.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        try:
            current = await self._api.attribute(int(attribute_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}

        usage, warnings, families_known = await self._family_usage()
        row = schema.attribute_row(current, usage=usage.get(int(attribute_id)))
        verdict = schema.delete_verdict(row, families_known=families_known)
        if not verdict["allowed"]:
            await self._record(product_id=0, action="delete_attribute", reason=reason,
                               actor=actor, result="reddedildi",
                               detail={"id": int(attribute_id), "why": verdict["reason"]})
            return {"ok": False, "error": f"{verdict['reason']} {verdict['alternative']}".strip(),
                    "verdict": verdict, "warnings": warnings}

        await self._record(product_id=0, action="delete_attribute", reason=reason, actor=actor,
                           result="denendi", detail={"id": int(attribute_id),
                                                     "code": row["code"]})
        try:
            result = await self._api.delete_attribute(int(attribute_id), reason=reason,
                                                      actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(product_id=0, action="delete_attribute", reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            # 409 burada BEKLENEN bir cevaptır, arıza değil: mağaza niteliği bir
            # ailede görüyor. Ekranın gördüğü aile listesi geçidin 900 sn'lik
            # önbelleğinden gelmiş ve bu arada nitelik bir aileye eklenmiş
            # olabilir. Ham metin ("çakışma yüzünden reddetti") kullanıcıya ne
            # yapacağını söylemiyor; söyleyen metinle değiştirilir.
            return {"ok": False, "error": self._conflict_text(
                failure,
                f"Mağaza `{row['code']}` niteliğini silmedi: bir öznitelik ailesinde "
                "kullanılıyor. Ekranın gördüğü aile listesi bayat olabilir (aileler 900 sn "
                "önbellekli). Niteliği önce Aileler sekmesinden her gruptan çıkarın ya da "
                "silme yerine PASİFLEŞTİRİN — nitelik ve ürünlerdeki değerleri yerinde kalır.",
            ), "verdict": verdict, "warnings": warnings}

        await self._record(product_id=0, action="delete_attribute", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"id": int(attribute_id), "code": row["code"]})
        return {"ok": True, "error": "", "code": row["code"],
                "dryRun": bool(result.get("dryRun", dry_run))}

    async def save_option(self, attribute_id: int, *, option_id: int | None, name: str,
                          sort_order: int = 0, swatch: str = "", reason: str, actor: str,
                          dry_run: bool = True) -> dict[str, Any]:
        """Seçenek ekler/günceller (select · multiselect · checkbox)."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        if not catalog.text(name):
            return {"ok": False, "error": "Seçenek adı zorunlu."}

        body = schema.option_body(name, sort_order=sort_order, swatch=swatch)
        action = "update_option" if option_id else "create_option"
        try:
            if option_id:
                result = await self._api.update_attribute_option(
                    int(attribute_id), int(option_id), payload=body, reason=reason, actor=actor,
                    dry_run=dry_run)
            else:
                result = await self._api.create_attribute_option(
                    int(attribute_id), payload=body, reason=reason, actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(product_id=0, action=action, reason=reason, actor=actor,
                               result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(product_id=0, action=action, reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"attributeId": int(attribute_id), "name": catalog.text(name)})
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run))}

    async def delete_option(self, attribute_id: int, option_id: int, *, reason: str, actor: str,
                            dry_run: bool = True) -> dict[str, Any]:
        """Seçeneği siler. O DEĞERİ TAŞIYAN ÜRÜNLERDEN DE DÜŞER.

        Kaç ürünün etkilendiği bu uçtan öğrenilemiyor (seçenek bazlı sayaç
        yok); ekran bunu saklamaz, "sayı bilinmiyor" der ve öyle onaylatır.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        try:
            result = await self._api.delete_attribute_option(
                int(attribute_id), int(option_id), reason=reason, actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(product_id=0, action="delete_option", reason=reason, actor=actor,
                               result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(product_id=0, action="delete_option", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"attributeId": int(attribute_id), "optionId": int(option_id)})
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run))}

    # ---------------------------------------------------------------- aile

    async def families(self) -> dict[str, Any]:
        """Aile listesi + aile başına ürün sayısı.

        Ürün sayısı listeyle GELMEZ; aile başına bir `per_page=1` isteğiyle
        `meta.total` okunur. İki aile için iki istek — kabul edilebilir ve
        ekranın en çok sorulan sorusunu ("bu ailede kaç ürün var") cevaplar.
        """
        try:
            listing = await self._api.families()
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("aileler okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "items": [], "warnings": []}

        warnings: list[str] = []
        rows: list[dict[str, Any]] = []
        for item in listing.get("items") or []:
            if not isinstance(item, dict):
                continue
            family_id = schema.as_int(schema.pick(item, "id"))
            count: int | None = None
            try:
                page = await self._api.products({"attribute_family": family_id},
                                                page=1, per_page=1)
                count = catalog.as_int((page.get("meta") or {}).get("total"))
            except Exception as failure:  # noqa: BLE001 — sayı yoksa satır yine dursun
                warnings.append(f"#{family_id} ailesinin ürün sayısı okunamadı: "
                                f"{self._fail(failure)}")
            rows.append(schema.family_row(item, product_count=count))
        rows.sort(key=lambda row: row["name"].casefold())
        return {"ok": True, "connected": True, "error": "", "items": rows, "warnings": warnings,
                "notice": "Aile listesi grupları taşımaz (mağaza `attributeGroups` alanını boş "
                          "veriyor); grup ve nitelikler aile açılınca gelir."}

    async def family(self, family_id: int) -> dict[str, Any]:
        """Aile künyesi: gruplar, gruptaki nitelikler ve atanabilir nitelik havuzu."""
        try:
            raw = await self._api.family(int(family_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "connected": False, "error": self._fail(failure)}

        detail = schema.family_detail(raw)
        pool: list[dict[str, Any]] = []
        warnings: list[str] = []
        try:
            payload = await self._api.attributes()
            pool = [schema.attribute_row(item) for item in (payload.get("items") or [])
                    if isinstance(item, dict)]
            pool.sort(key=lambda row: row["code"])
        except Exception as failure:  # noqa: BLE001 — havuz yoksa düzen yine görünür (K7)
            warnings.append(f"Atanabilir nitelik listesi okunamadı: {self._fail(failure)}")

        count: int | None = None
        try:
            page = await self._api.products({"attribute_family": int(family_id)},
                                            page=1, per_page=1)
            count = catalog.as_int((page.get("meta") or {}).get("total"))
        except Exception as failure:  # noqa: BLE001 — K7
            warnings.append(f"Ürün sayısı okunamadı: {self._fail(failure)}")

        return {
            "ok": True, "connected": True, "error": "", "warnings": warnings,
            "family": detail, "productCount": count, "pool": pool,
            "coreCodes": list(schema.CORE_CODES),
            "lockNotice": "Aile KODU oluşturulduktan sonra değiştirilemez. Grup düzeni "
                          "kaydedildiğinde mağazadaki düzen GÖNDERİLENLE DEĞİŞİR: burada "
                          "görünmeyen bir grup aileden düşer.",
        }

    async def save_family(self, family_id: int | None, *, name: str, code: str = "",
                          groups: list[dict[str, Any]] | None, reason: str, actor: str,
                          dry_run: bool = True) -> dict[str, Any]:
        """Aile açar ya da günceller.

        `groups` `None` ise grup düzenine DOKUNULMAZ (KURAL 5): yalnız ad
        değişir. Liste verildiyse önce çekirdek nitelik denetiminden geçer.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        if not catalog.text(name):
            return {"ok": False, "error": "Aile adı zorunlu."}
        if groups is not None:
            # Panel nitelikleri KİMLİKLE gönderiyor; çekirdek nitelik denetimi
            # KODLA yapılıyor. Eşleme burada, taze listeyle kurulur.
            try:
                payload = await self._api.attributes()
            except Exception as failure:  # noqa: BLE001 — K7
                return {"ok": False,
                        "error": f"Nitelik listesi okunamadı ({self._fail(failure)}); aile "
                                 "düzeni doğrulanmadan kaydedilmez — gönderilen liste "
                                 "mağazadaki düzenin yerine geçiyor."}
            codes = {schema.as_int(schema.pick(item, "id")): schema.text(schema.pick(item, "code"))
                     for item in (payload.get("items") or []) if isinstance(item, dict)}
            resolved = [{**group,
                         "attributes": [{"code": codes.get(schema.as_int(item), "")}
                                        for item in (group.get("attributeIds") or [])]}
                        for group in groups]
            guard = schema.family_guard(resolved)
            if guard:
                return {"ok": False, "error": guard}
        if family_id is None:
            check = schema.code_error(code)
            if check:
                return {"ok": False, "error": check.replace("Nitelik kodu", "Aile kodu")}

        body = schema.family_body(name=name, code=code, creating=family_id is None, groups=groups)
        action = "update_family" if family_id else "create_family"
        await self._record(product_id=0, action=action, reason=reason, actor=actor,
                           result="denendi",
                           detail={"id": family_id or 0, "groups": len(groups or [])})
        try:
            if family_id:
                result = await self._api.update_family(int(family_id), payload=body,
                                                       reason=reason, actor=actor,
                                                       dry_run=dry_run)
            else:
                result = await self._api.create_family(payload=body, reason=reason, actor=actor,
                                                       dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(product_id=0, action=action, reason=reason, actor=actor,
                               result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(product_id=0, action=action, reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"id": family_id or 0, "groups": len(groups or [])})
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run)),
                "touchedGroups": groups is not None, "notice": catalog.INDEX_NOTICE}

    # ============================================================== ayarlar

    async def settings(self) -> dict[str, Any]:
        """`store_settings` ekranı yok; stok ayarları doğal sahibinde (bu ekranda).

        İki farklı şey aynı sekmede durur ve AYRI gösterilir:
        · Bu ekranın tercihi (kritik eşik) — yalnız burada boyamayı etkiler.
        · Mağaza ayarı (tükenen ürün davranışı, arka sipariş) — vitrini etkiler
          ve Bagisto `core_config` üzerinden yazılır.
        """
        threshold = await self._threshold()
        slug = str(self._config.get("inventory_config_slug") or "catalog.inventory")
        out: dict[str, Any] = {
            "ok": True, "connected": True, "error": "",
            "local": {"lowStockThreshold": threshold},
            "storeSlug": slug,
            "store": {}, "storeAvailable": True,
        }
        try:
            payload = await self._api.configuration(slug, channel=self._channel,
                                                    locale=self._locale)
        except Exception as failure:  # noqa: BLE001 — K7
            out["storeAvailable"] = False
            out["error"] = self._fail(failure)
            return out

        values = payload.get("values") if isinstance(payload.get("values"), dict) else payload
        out["store"] = {
            "backOrder": self._config_value(values, self._key("back_order_key")),
            "outOfStock": self._config_value(values, self._key("out_of_stock_key")),
        }
        return out

    def _key(self, name: str) -> str:
        defaults = {
            "back_order_key": "catalog.inventory.stock_options.back_orders",
            "out_of_stock_key": "catalog.products.settings.show_out_of_stock_items",
        }
        return str(self._config.get(name) or defaults[name])

    @staticmethod
    def _config_value(values: Any, key: str) -> dict[str, Any]:
        """Ayarın MEVCUT değeri — bulunamazsa "yok" denir, sıfır uydurulmaz.

        Anahtar adı Bagisto sürümüne göre değişebiliyor. Bulunmayan anahtara
        yazmak, `core_config` içinde hiçbir şeyi etkilemeyen yeni bir satır
        açar ve kullanıcı ayarı değiştirdiğini sanır.
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

    async def save_settings(self, *, low_stock_threshold: int | None = None,
                            back_order: bool | None = None, out_of_stock: bool | None = None,
                            reason: str, actor: str,
                            dry_run: bool = True) -> dict[str, Any]:
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        changed: list[str] = []
        if low_stock_threshold is not None:
            value = max(0, min(9_999, int(low_stock_threshold)))
            await self._set_pref("low_stock_threshold", str(value), actor)
            changed.append("kritik eşik")

        current = await self.settings()
        store = current.get("store") or {}
        values: dict[str, Any] = {}
        skipped: list[str] = []

        # Anahtar mağazada BULUNAMADIYSA yazılmaz. Bulunmayan anahtara yazmak
        # `core_config` içinde hiçbir şeyi etkilemeyen bir satır açar ve
        # kullanıcı ayarı değiştirdiğini sanır — sessiz başarısızlık.
        for wanted, slot, label in ((back_order, "backOrder", "arka sipariş"),
                                    (out_of_stock, "outOfStock", "tükenen ürün davranışı")):
            if wanted is None:
                continue
            entry = store.get(slot) or {}
            if entry.get("found"):
                values[entry["key"]] = 1 if wanted else 0
            else:
                skipped.append(label)

        if values:
            try:
                await self._api.update_configuration(current["storeSlug"], values=values,
                                                     reason=reason, actor=actor,
                                                     channel=self._channel, locale=self._locale,
                                                     dry_run=dry_run)
            except Exception as failure:  # noqa: BLE001 — K7
                return {"ok": False, "error": self._fail(failure), "changed": changed}
            changed.extend(values)

        await self._record(product_id=0, action="save_settings", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"changed": changed, "skipped": skipped})
        return {"ok": True, "error": "", "changed": changed, "skipped": skipped,
                "dryRun": dry_run}

    # ================================================================ rapor

    async def _scan(self, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
        """Rapor için tam katalog taraması — SIRAYLA, tavanlı."""
        threshold = await self._threshold()
        payload = await self._api.products(filters, all_pages=True)
        rows = [catalog.product_row(item, threshold=threshold, today=_today())
                for item in (payload.get("items") or [])[:REPORT_ROW_CAP]]
        truncated = bool(payload.get("truncated")) or \
            len(payload.get("items") or []) > REPORT_ROW_CAP
        return rows, truncated

    async def export_csv(self, *, scope: str = "all", rows: list[dict[str, Any]] | None = None,
                         filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """Tüm kayıtların CSV'si. Görünen sayfanın CSV'sini panel kendi üretir."""
        truncated = False
        if scope == "given":
            data = rows or []
        else:
            base = {"channel": self._channel, "locale": self._locale, **(filters or {})}
            try:
                data, truncated = await self._scan(base)
            except Exception as failure:  # noqa: BLE001 — K7
                return {"ok": False, "error": self._fail(failure)}

        headers = ["SKU", "Ad", "Tip", "Kategori", "Fiyat", "İndirimli", "Stok", "Durum",
                   "URL anahtarı", "Güncelleme"]
        table = [[row["sku"], row["name"], row["typeLabel"], row["categories"],
                  money(row["price"]), money(row["specialPrice"]) if row["specialPrice"] else "",
                  row["stock"], "Aktif" if row["status"] else "Pasif",
                  row["urlKey"], row["updatedAt"]] for row in data]

        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        name = f"magaza-urun-listesi-{stamp}.csv"
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
        if kind not in ("stock", "pricelist"):
            return {"ok": False, "error": f"Bilinmeyen rapor: {kind}"}

        filters: dict[str, Any] = {"channel": self._channel, "locale": self._locale}
        if params.get("categoryId"):
            filters["category_id"] = catalog.as_int(params["categoryId"])
        if params.get("type"):
            filters["type"] = catalog.text(params["type"])

        try:
            rows, truncated = await self._scan(filters)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        if not rows:
            return {"ok": False, "error": "Bu süzgeçte rapora girecek ürün yok."}

        threshold = await self._threshold()
        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        if kind == "stock":
            content = self._stock_pdf(rows, threshold=threshold, truncated=truncated)
            name = f"magaza-stok-raporu-{stamp}.pdf"
        else:
            content = self._price_pdf(rows, truncated=truncated)
            name = f"magaza-fiyat-listesi-{stamp}.pdf"

        try:
            path = write_private(self._export_dir / name, content)
        except (OSError, ExportError) as failure:
            return {"ok": False, "error": str(failure)}
        self._log.info("ürün raporu üretildi", kind=kind, rows=len(rows), path=str(path))
        return {"ok": True, "error": "", "path": str(path), "name": name,
                "bytes": len(content), "rows": len(rows), "truncated": truncated}

    def _stock_pdf(self, rows: list[dict[str, Any]], *, threshold: int,
                   truncated: bool) -> bytes:
        out = [row for row in rows if row["stockState"] == catalog.STOCK_OUT]
        low = [row for row in rows if row["stockState"] == catalog.STOCK_LOW]
        sections: list[dict[str, Any]] = [{
            "kind": "tiles", "title": "Özet",
            "tiles": [("Ürün", number(len(rows))), ("Tükendi", number(len(out))),
                      ("Kritik", number(len(low))), ("Kritik eşiği", number(threshold))],
        }]
        for title, subset in (("Tükenen ürünler", out), ("Kritik stok", low)):
            if not subset:
                continue
            sections.append({
                "kind": "table", "title": title,
                "headers": ["SKU", "Ürün", "Kategori", "Stok", "Fiyat"],
                "align": "LLLRR", "widths": [1.1, 3, 2, 0.7, 1],
                "rows": [[row["sku"], row["name"], row["categories"], number(row["stock"]),
                          money(row["price"])] for row in subset[:400]],
            })
        if truncated:
            sections.append({"kind": "note",
                             "text": "Katalog tavana dayandı; liste eksik olabilir."})
        return build_pdf(title="Stok raporu",
                         subtitle=f"{len(rows)} ürün · kritik eşik {threshold}",
                         sections=sections, footer="Kontrol Merkezi · Mağaza")

    def _price_pdf(self, rows: list[dict[str, Any]], *, truncated: bool) -> bytes:
        active = [row for row in rows if row["status"]]
        sections: list[dict[str, Any]] = [{
            "kind": "table", "title": "Fiyat listesi",
            "headers": ["SKU", "Ürün", "Kategori", "Liste", "İndirimli"],
            "align": "LLLRR", "widths": [1.1, 3, 2, 1, 1],
            "rows": [[row["sku"], row["name"], row["categories"], money(row["price"]),
                      money(row["specialPrice"]) if row["specialState"] == "active" else "—"]
                     for row in active[:800]],
        }]
        if truncated or len(active) > 800:
            sections.append({"kind": "note",
                             "text": "Liste ilk 800 aktif ürünle sınırlandı."})
        return build_pdf(title="Fiyat listesi", subtitle=f"{len(active)} aktif ürün",
                         sections=sections, footer="Kontrol Merkezi · Mağaza")

    async def _render_pages(self, path: Path, *, max_pages: int = 12,
                            dpi: int = 110) -> list[str]:
        binary = shutil.which("pdftoppm")
        if not binary:
            raise PreviewError(
                "Önizleme üretilemedi: `pdftoppm` yok (poppler-utils kurulmalı). "
                "Rapor yine de kaydedildi ve yazdırılabilir.")
        with tempfile.TemporaryDirectory(prefix="km-urun-onizleme-") as folder:
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

        GÜVENLİK: yalnız BİZİM rapor klasörümüzdeki dosya basılabilir.
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
