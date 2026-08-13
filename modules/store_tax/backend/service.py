"""Vergilendirme — iş kuralları.

VERİ MAĞAZADADIR, KARAR BURADADIR. Oran, vergi kategorisi ve ürünün vergi
özniteliği `store.api` geçidinden gelir (K4); bu modül Bagisto verisinin
kopyasını tutmaz. Yerel tablolar yalnız mağazada KARŞILIĞI OLMAYAN dört şeyi
saklar: yazma gerekçesi, oranın geçerlilik tarihi, vergi kategorisi başına ürün
sayısı (tarama sonucu) ve toplu eşleme önizlemesi.

KDV İCMALİ AYRI BİR SORUMLULUKTUR. `accountant` rolünün ana ekranıdır ve
rakamları mali müşavire gider; bu yüzden burada eksik veriyle rapor
ÜRETİLMEZ. Tarih süzgecinin uygulandığı doğrulanır, belge tavanına dayanılırsa
rapor reddedilir ve nedeni söylenir. "Yaklaşık doğru" bir beyan, yanlış
beyandır.

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
    report_dir,
    write_private,
)

from . import summary as vat
from . import taxes

#: KDV icmaline girecek en çok belge. Aşılırsa rapor ÜRETİLMEZ: eksik belgeyle
#: hesaplanan matrah beyanı eksiltir ve bunu fark etmenin yolu yoktur.
DOCUMENT_CAP = 4_000

#: Katalog taramasının tavanı (1.419 ürün ≈ 29 sayfa; 3.000 büyümeye yer bırakır).
CATALOG_CAP = 3_000

REPORT_KINDS = ("vat", "rates")


class PreviewError(RuntimeError):
    """Önizleme görüntüsü üretilemedi. Rapor yine de kaydedilmiştir."""


class TaxService:
    """Vergilendirme ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ."""

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
        self._effective = store.table("effective")
        self._usage = store.table("usage")
        self._assign = store.table("assign")

    # ------------------------------------------------------------- ayarlar

    @property
    def _channel(self) -> str:
        return str(self._config.get("channel") or "default")

    @property
    def _locale(self) -> str:
        return str(self._config.get("locale") or "tr")

    @property
    def _page_size(self) -> int:
        return max(10, min(200, taxes.as_int(self._config.get("page_size"), 50)))

    @property
    def _summary_days(self) -> int:
        return max(1, min(400, taxes.as_int(self._config.get("summary_days"), 30)))

    @property
    def _document_cap(self) -> int:
        return max(100, min(20_000, taxes.as_int(self._config.get("document_cap"), DOCUMENT_CAP)))

    @property
    def _catalog_cap(self) -> int:
        return max(100, min(20_000, taxes.as_int(self._config.get("catalog_scan_cap"),
                                                 CATALOG_CAP)))

    @property
    def _assign_limit(self) -> int:
        """Toplu eşlemede SIRAYLA yazılacak en çok ürün.

        Bagisto'da ürünün vergi kategorisini toplu yazan bir uç YOK; tek yol
        ürün başına PUT. Mağaza dakikada 60 istek kabul ettiği için sınır
        bilerek küçüktür: 50 ürün ≈ iki dakika. Daha büyük iş için mağaza
        tarafında toplu uç gerekir; ekran bunu söyler, sessizce yarım iş yapmaz.
        """
        return max(0, min(200, taxes.as_int(self._config.get("assign_limit"), 50)))

    @property
    def _export_dir(self) -> Path:
        # HER ÇAĞRIDA yeniden çözülür: ay değişince klasör kendiliğinden değişir.
        return report_dir(self._category, subcategory=self._subcategory,
                          fallback=self._fallback,
                          configured=str(self._config.get("export_path") or ""))

    # ------------------------------------------------------------ yardımcı

    @staticmethod
    def _fail(failure: Exception) -> str:
        message = str(failure).strip()
        return message or "Mağazaya ulaşılamadı."

    async def _record(self, *, target: str, action: str, reason: str, actor: str,
                      result: str, detail: Any = None) -> None:
        """Yerel denetim izi. Bagisto denetim tutuyor ama GEREKÇEYİ tutmuyor;
        ayrıca ağ koparsa "ne yapmaya çalıştık" kaydı burada kalır."""
        try:
            await self._store.execute(
                f"INSERT INTO {self._audit} "
                "(target, action, reason, actor, result, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (target, action, reason, actor, result,
                 json.dumps(detail or {}, ensure_ascii=False), taxes.now_iso()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın
            self._log.warning("denetim izi yazılamadı", action=action, error=str(failure))

    # ======================================================== yerel tablolar

    async def _effective_map(self) -> dict[int, dict[str, Any]]:
        try:
            rows = await self._store.fetch_all(
                f"SELECT rate_id, effective_from, note, actor, updated_at FROM {self._effective}")
        except Exception as failure:  # noqa: BLE001 — not okunamadı, oranlar yine gösterilir
            self._log.warning("geçerlilik notları okunamadı", error=str(failure))
            return {}
        return {taxes.as_int(row["rate_id"]): dict(row) for row in rows}

    async def _usage_map(self) -> dict[int, dict[str, Any]]:
        try:
            rows = await self._store.fetch_all(
                f"SELECT tax_category_id, product_count, scanned_at FROM {self._usage}")
        except Exception as failure:  # noqa: BLE001 — sayı yoksa "taranmadı" denir
            self._log.warning("kullanım sayıları okunamadı", error=str(failure))
            return {}
        return {taxes.as_int(row["tax_category_id"]): dict(row) for row in rows}

    # =============================================================== okuma

    async def _load(self, *, with_usage: bool = True) -> dict[str, Any]:
        """Oran + kategori + yerel notların birleşimi. Ekranın çekirdek verisi.

        İki uç SIRAYLA çağrılır: paralel çağrı hız kovasını (dk 55) daha hızlı
        tüketiyor ve tekrar denemede sıraya girmek zorunda kalıyor.
        """
        out: dict[str, Any] = {"connected": True, "error": "", "rates": [], "categories": [],
                               "conflicts": [], "membershipKnown": True}
        try:
            rate_payload = await self._api.tax_rates()
            category_payload = await self._api.tax_categories()
        except Exception as failure:  # noqa: BLE001 — ekran ayakta kalmalı (K7)
            self._log.warning("vergi kayıtları okunamadı", error=str(failure))
            out["connected"] = False
            out["error"] = self._fail(failure)
            return out

        effective = await self._effective_map()
        usage = await self._usage_map() if with_usage else {}

        raw_categories = [item for item in (category_payload.get("items") or [])
                          if isinstance(item, dict)]
        # Oran → hangi kategorilerde? Kategori kaydı oranları taşıyor, oran
        # kaydı kategorileri taşımıyor; ilişki burada ters çevrilir.
        #
        # AMA canlıda liste ucu ilişkiyi `null` döndürüyor (bkz. taxes.
        # `rate_ids_known`). Tek bir kategoride bile okunamıyorsa ilişki
        # BÜTÜNÜYLE bilinmiyor sayılır: yarım bilgiyle "bu oran hiçbir
        # kategoride yok" demek, olmayan bir sorunu ihbar etmektir.
        membership_known = all(taxes.rate_ids_known(raw) for raw in raw_categories)
        membership: dict[int, list[int]] = {}
        for raw in raw_categories:
            for rate_id in taxes.rate_ids_of(raw):
                membership.setdefault(rate_id, []).append(taxes.as_int(raw.get("id")))

        rates = [taxes.rate_row(item, effective=effective.get(taxes.as_int(item.get("id"))),
                                category_ids=membership.get(taxes.as_int(item.get("id"))))
                 for item in (rate_payload.get("items") or []) if isinstance(item, dict)]
        categories = [taxes.category_row(item, usage=usage.get(taxes.as_int(item.get("id"))),
                                         rates=rates)
                      for item in raw_categories]
        clashes = taxes.conflicts(rates, categories) if membership_known else []
        taxes.mark_flags(rates, categories, clashes, membership_known=membership_known)

        out["rates"] = rates
        out["categories"] = categories
        out["conflicts"] = clashes
        out["membershipKnown"] = membership_known
        out["scannedAt"] = max((row["scannedAt"] for row in categories if row["scannedAt"]),
                               default="")
        return out

    async def rates(self, *, q: str = "", min_percent: float | None = None,
                    max_percent: float | None = None, region: str = "",
                    flag: str = "") -> dict[str, Any]:
        """Vergi oranları listesi.

        SÜZME İSTEMCİDE DEĞİL BURADA: oran sayısı onlarla ölçülür (1.419 ürünle
        değil), tamamı zaten tek istekte geliyor ve süzgeç mantığının test
        edilebilir tek kopyası servistir.
        """
        loaded = await self._load()
        rows = loaded["rates"]
        needle = taxes.text(q).casefold()
        if needle:
            rows = [row for row in rows
                    if needle in row["name"].casefold()
                    or needle in row["percentLabel"].casefold()
                    or needle in row["region"].casefold()]
        if min_percent is not None:
            rows = [row for row in rows
                    if row["percent"] is not None and row["percent"] >= min_percent]
        if max_percent is not None:
            rows = [row for row in rows
                    if row["percent"] is not None and row["percent"] <= max_percent]
        if taxes.text(region):
            rows = [row for row in rows if row["region"] == region]
        if flag == "unassigned":
            rows = [row for row in rows if row["unassigned"]]
        elif flag == "conflict":
            rows = [row for row in rows if row["conflict"]]

        return {
            "ok": True, "connected": loaded["connected"], "error": loaded["error"],
            "items": rows, "total": len(rows), "all": len(loaded["rates"]),
            "regions": sorted({row["region"] for row in loaded["rates"]}),
            "conflicts": loaded["conflicts"],
            "counts": {
                "unassigned": len([row for row in loaded["rates"] if row["unassigned"]]),
                "conflict": len([row for row in loaded["rates"] if row["conflict"]]),
            },
            "scannedAt": loaded.get("scannedAt", ""),
            "notice": taxes.RATE_NOTICE,
            "membershipKnown": loaded["membershipKnown"],
            "membershipNotice": "" if loaded["membershipKnown"] else taxes.MEMBERSHIP_NOTICE,
        }

    async def categories(self) -> dict[str, Any]:
        loaded = await self._load()
        return {"ok": True, "connected": loaded["connected"], "error": loaded["error"],
                "items": loaded["categories"], "rates": loaded["rates"],
                "scannedAt": loaded.get("scannedAt", ""),
                "notice": taxes.RATE_NOTICE,
                "membershipKnown": loaded["membershipKnown"],
                "membershipNotice": "" if loaded["membershipKnown"] else taxes.MEMBERSHIP_NOTICE}

    async def regions(self) -> dict[str, Any]:
        """Bölge görünümü — oranlardan TÜRETİLİR.

        Bagisto'da ayrı bir vergi bölgesi kaydı yoktur; kapsam oranın üzerinde
        durur. Ayrı bir kayıt uydurmak yerine ülke/eyalet kırılımı çıkarılır.
        """
        loaded = await self._load(with_usage=False)
        return {"ok": True, "connected": loaded["connected"], "error": loaded["error"],
                "items": taxes.region_rows(loaded["rates"]),
                "conflicts": loaded["conflicts"],
                "derived": True,
                "notice": "Bölgeler ayrı bir kayıt değildir: her oranın kendi ülke/eyalet/posta "
                          "kodu kapsamı vardır ve bu görünüm onlardan türetilir."}

    async def rate(self, rate_id: int) -> dict[str, Any]:
        """Tek oranın düzenleme künyesi — yazma gövdesi bunun üzerine kurulur."""
        problem = taxes.id_error(rate_id, "Oran")
        if problem:
            return {"ok": False, "connected": True, "error": problem}
        try:
            raw = await self._api.tax_rate(int(rate_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "connected": False, "error": self._fail(failure)}
        # BOŞ GÖVDE KÜNYE SAYILMAZ. Mağaza 200 dönüp tanımadığımız (ya da boş)
        # bir gövde verirse `rate_row` yine de satır üretir: id 0, ad "#0",
        # oran "okunamadı". O satırı düzenlemeye açmak, kullanıcıya var olmayan
        # bir kaydı düzenletmek ve kaydettiğinde ne olacağını bilmemek demektir.
        if not taxes.as_int((raw or {}).get("id")):
            return {"ok": False, "connected": True,
                    "error": "Oran künyesi okunamadı: mağaza beklenen alanları döndürmedi. "
                             "Düzenleme açılmadı — okuyamadığımız bir kaydı yazmak, "
                             "göremediğimiz alanları ezmek olurdu."}
        effective = (await self._effective_map()).get(int(rate_id))
        return {"ok": True, "connected": True, "error": "",
                "rate": taxes.rate_row(raw, effective=effective),
                "raw": {key: raw.get(key) for key in taxes.RATE_FIELDS if key in raw},
                "notice": taxes.RATE_NOTICE}

    async def audit(self, *, target: str = "", limit: int = 50) -> dict[str, Any]:
        sql = f"SELECT target, action, reason, actor, result, created_at FROM {self._audit} "
        params: tuple[Any, ...] = ()
        if taxes.text(target):
            sql += "WHERE target = ? "
            params = (taxes.text(target),)
        sql += "ORDER BY id DESC LIMIT ?"
        params = (*params, max(1, min(500, int(limit))))
        try:
            rows = await self._store.fetch_all(sql, params)
        except Exception as failure:  # noqa: BLE001 — iz okunamadı, ekran dursun
            return {"ok": True, "items": [], "error": self._fail(failure)}
        return {"ok": True, "error": "", "items": [
            {"target": row["target"], "action": row["action"], "reason": row["reason"],
             "actor": row["actor"], "result": row["result"], "createdAt": row["created_at"]}
            for row in rows]}

    # =============================================================== yazma

    async def save_rate(self, rate_id: int | None, *, payload: dict[str, Any],
                        effective_from: str = "", note: str = "", reason: str,
                        actor: str, dry_run: bool = True) -> dict[str, Any]:
        """Oran ekler/günceller — OKU-DEĞİŞTİR-YAZ.

        Geçerlilik tarihi mağazaya GİTMEZ: Bagisto'da böyle bir alan yok.
        Yerelde saklanır ve ekranda "bizim notumuz" olarak gösterilir. Mağazaya
        yollamak, Laravel'in tanımadığı alanı sessizce yok saymasına ve
        kullanıcının "tarihi ayarladım" sanmasına yol açardı.
        """
        problem = taxes.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}
        if rate_id is not None:
            id_problem = taxes.id_error(rate_id, "Oran")
            if id_problem:
                return {"ok": False, "error": id_problem}
        date_problem = taxes.effective_error(effective_from)
        if date_problem:
            return {"ok": False, "error": date_problem}

        patch = taxes.rate_patch(payload)
        current: dict[str, Any] = {}
        if rate_id:
            try:
                current = await self._api.tax_rate(int(rate_id))
            except Exception as failure:  # noqa: BLE001 — K7
                return {"ok": False, "error": self._fail(failure)}

        body = taxes.rate_body(current, patch)
        form_problem = taxes.rate_form_error(body)
        if form_problem:
            return {"ok": False, "error": form_problem}

        target = f"rate:{rate_id}" if rate_id else "rate:new"
        action = "update_tax_rate" if rate_id else "create_tax_rate"
        await self._record(target=target, action=action, reason=reason, actor=actor,
                           result="denendi", detail=body)
        try:
            result = await self._api.save_tax_rate(payload=body, rate_id=rate_id, reason=reason,
                                                   actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(target=target, action=action, reason=reason, actor=actor,
                               result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        new_id = rate_id or self._new_id(result)
        # Geçerlilik notu yalnız mağaza yazması BAŞARILI olduktan sonra yazılır;
        # aksi hâlde var olmayan bir orana ait not birikirdi.
        if new_id and (effective_from or note) and not dry_run:
            await self._save_effective(new_id, effective_from, note, actor)

        await self._record(target=f"rate:{new_id}" if new_id else target, action=action,
                           reason=reason, actor=actor, result="dry_run" if dry_run else "ok",
                           detail={"body": body, "effectiveFrom": effective_from})
        return {"ok": True, "error": "", "id": new_id,
                "dryRun": bool(result.get("dryRun", dry_run)),
                "sent": bool(result.get("sent", not dry_run)),
                "notice": f"{taxes.RATE_NOTICE} {taxes.INDEX_NOTICE}"}

    @staticmethod
    def _new_id(result: Any) -> int:
        if not isinstance(result, dict):
            return 0
        data = result.get("data")
        if isinstance(data, dict):
            return taxes.as_int(data.get("id"))
        return taxes.as_int(result.get("id"))

    async def _save_effective(self, rate_id: int, effective_from: str, note: str,
                              actor: str) -> None:
        try:
            await self._store.execute(
                f"INSERT INTO {self._effective} "
                "(rate_id, effective_from, note, actor, updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(rate_id) DO UPDATE SET effective_from = excluded.effective_from, "
                "note = excluded.note, actor = excluded.actor, updated_at = excluded.updated_at",
                (int(rate_id), taxes.text(effective_from), taxes.text(note)[:255], actor,
                 taxes.now_iso()),
            )
        except Exception as failure:  # noqa: BLE001 — not yazılamadı, oran yazıldı
            self._log.warning("geçerlilik notu yazılamadı", rateId=rate_id, error=str(failure))

    async def save_effective(self, rate_id: int, *, effective_from: str, note: str,
                             actor: str) -> dict[str, Any]:
        """Yalnız yerel geçerlilik notunu yazar — mağazaya HİÇ istek gitmez."""
        id_problem = taxes.id_error(rate_id, "Oran")
        if id_problem:
            return {"ok": False, "error": id_problem}
        problem = taxes.effective_error(effective_from)
        if problem:
            return {"ok": False, "error": problem}
        await self._save_effective(int(rate_id), effective_from, note, actor)
        await self._record(target=f"rate:{int(rate_id)}", action="set_effective_date",
                           reason="Yerel geçerlilik notu", actor=actor, result="ok",
                           detail={"effectiveFrom": effective_from})
        return {"ok": True, "error": "", "effectiveFrom": taxes.text(effective_from),
                "note": "Bu tarih YEREL bir nottur; mağaza oranı bu tarihte kendiliğinden "
                        "değiştirmez."}

    async def save_category(self, category_id: int | None, *, payload: dict[str, Any],
                            reason: str, actor: str, dry_run: bool = True) -> dict[str, Any]:
        """Vergi kategorisi ekler/günceller. Oran listesi TAM gönderilir."""
        problem = taxes.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}
        if category_id is not None:
            id_problem = taxes.id_error(category_id, "Vergi kategorisi")
            if id_problem:
                return {"ok": False, "error": id_problem}

        current: dict[str, Any] = {}
        if category_id:
            loaded = await self._load(with_usage=False)
            if not loaded["connected"]:
                return {"ok": False, "error": loaded["error"]}
            found = next((item for item in loaded["categories"]
                          if item["id"] == int(category_id)), None)
            if not found:
                return {"ok": False, "error": "Vergi kategorisi bulunamadı."}
            if not found["ratesKnown"]:
                # Bagisto kısmi `taxrates` kabul etmiyor: gönderilmeyen oran
                # kategoriden DÜŞER. Mevcut ilişkiyi okuyamadan yazmak, göremediğimiz
                # oranları sessizce silmek olurdu.
                return {"ok": False,
                        "error": "Bu kategorinin oran bağlantısı mağazadan okunamıyor; "
                                 "güncelleme reddedildi. Kaydetmek, göremediğimiz oranları "
                                 "kategoriden düşürürdü. " + taxes.MEMBERSHIP_NOTICE}
            current = {"code": found["code"], "name": found["name"],
                       "description": found["description"], "taxrates": found["rateIds"]}

        patch: dict[str, Any] = {}
        aliases = {"code": "code", "name": "name", "description": "description",
                   "rateIds": "taxrates"}
        for key, value in (payload or {}).items():
            if key in aliases:
                patch[aliases[key]] = value

        body = taxes.category_body(current, patch)
        form_problem = taxes.category_form_error(body)
        if form_problem:
            return {"ok": False, "error": form_problem}

        target = f"category:{category_id}" if category_id else "category:new"
        action = "update_tax_category" if category_id else "create_tax_category"
        await self._record(target=target, action=action, reason=reason, actor=actor,
                           result="denendi", detail=body)
        try:
            result = await self._api.save_tax_category(payload=body, category_id=category_id,
                                                       reason=reason, actor=actor,
                                                       dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(target=target, action=action, reason=reason, actor=actor,
                               result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(target=target, action=action, reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok", detail=body)
        return {"ok": True, "error": "", "id": category_id or self._new_id(result),
                "dryRun": bool(result.get("dryRun", dry_run)),
                "notice": f"{taxes.RATE_NOTICE} {taxes.INDEX_NOTICE}"}

    # ========================================================= ürün eşlemesi

    async def mapping(self, *, q: str = "", tax_category_id: int = 0,
                      page: int = 1, size: int = 0) -> dict[str, Any]:
        """Ürün → vergi kategorisi görünümü. SUNUCU TARAFI sayfalama.

        `taxCategoryId` liste ucundan DOLU GELMİYOR (canlıda hep `null`, tekil
        uçta dolu); o zaman satır "Bilinmiyor" der. Kesin değer önizlemede gelir.

        KATALOG KATEGORİSİ SÜZGECİ YOK: canlıda `category_id=999999` bile 1.421
        ürünün hepsini döndürüyor, yani Laravel parametreyi yok sayıyor.
        Uygulanmayan bir süzgeci arayüze koymak, kullanıcıya süzülmüş sanılan
        bir liste göstermektir.
        """
        per_page = size or self._page_size
        loaded = await self._load(with_usage=False)
        names = {row["id"]: row["name"] for row in loaded["categories"]}
        # VERGİ KATEGORİSİ LİSTESİ AYRI BİR UÇTAN GELİYOR ve ayrı düşebiliyor.
        # Düştüğünde açılır kutu boşalıyor, satırlar kategori ADI yerine "#1"
        # yazıyor ve "Fark tablosunu göster" hedef seçilemediği için hiçbir zaman
        # çalışmıyordu — sebebi ekranda hiçbir yerde yazmıyordu. Sessiz ölü
        # denetim bırakılmaz: durum yazıyla söylenir.
        taxonomy_error = "" if loaded["connected"] else loaded["error"]
        taxonomy_note = "" if loaded["connected"] else (
            f"Vergi kategorisi listesi okunamadı ({loaded['error']}). Hedef kategori "
            "seçilemediği için toplu eşleme AÇILMAZ ve sütunda kategori adı yerine "
            "kimliği görünür. Ürün listesi bundan etkilenmez.")

        filters: dict[str, Any] = {"channel": self._channel, "locale": self._locale}
        needle = taxes.text(q)
        if needle:
            filters["name"] = needle

        try:
            payload = await self._api.products(filters, page=page, per_page=per_page)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("eşleme listesi okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "items": [], "total": 0, "page": page, "size": per_page, "pages": 0,
                    "categories": loaded["categories"], "taxKnown": False,
                    "categoriesConnected": loaded["connected"],
                    "categoriesError": taxonomy_error, "note": taxonomy_note,
                    "limit": self._assign_limit}

        raw = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
        rows = [taxes.product_row(item, categories=names) for item in raw]
        tax_known = any(row["taxKnown"] for row in rows)
        note = taxonomy_note
        if tax_category_id:
            # Yalnız BİLİNEN satırlar süzülür: bilinmeyeni süzgece dahil etmek
            # "bu kategoride 0 ürün var" yalanını üretirdi.
            rows = [row for row in rows
                    if row["taxKnown"] and row["taxCategoryId"] == int(tax_category_id)]
            if not tax_known:
                # Taksonomi notu varsa EZİLMEZ: iki ayrı sorun iki ayrı cümledir.
                filtered = (
                    "Vergi kategorisi süzgeci uygulanamadı: mağazanın ürün liste ucu bu "
                    "alanı doldurmuyor. Süzgeç boş liste üretti — «bu kategoride ürün "
                    "yok» ANLAMINA GELMEZ. Kesin değer için ürünleri seçip önizleme alın."
                )
                note = " ".join(item for item in (note, filtered) if item)
        meta = payload.get("meta") or {}
        return {
            "ok": True, "connected": True, "error": "", "note": note,
            "categoriesConnected": loaded["connected"], "categoriesError": taxonomy_error,
            "items": rows,
            "total": taxes.as_int(meta.get("total"), len(rows)),
            "page": taxes.as_int(meta.get("currentPage"), page),
            "size": taxes.as_int(meta.get("perPage"), per_page),
            "pages": taxes.as_int(meta.get("lastPage"), 1),
            "categories": loaded["categories"],
            "taxKnown": tax_known,
            "limit": self._assign_limit,
        }

    async def scan_usage(self, *, actor: str = "") -> dict[str, Any]:
        """Katalog taraması: vergi kategorisi başına ürün sayısı.

        PAHALIDIR ve elle tetiklenir. 1.419 ürün 29 sayfa eder ve sıralı çekilir;
        her ekran açılışında yapılamaz. Sonuç tarihiyle birlikte saklanır ve
        ekran sayının ne kadar bayat olduğunu SÖYLER.
        """
        try:
            payload = await self._api.products(
                {"channel": self._channel, "locale": self._locale}, all_pages=True)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}

        rows = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
        truncated = bool(payload.get("truncated")) or len(rows) > self._catalog_cap
        rows = rows[:self._catalog_cap]
        counts, seen = taxes.usage_counts(rows)
        if not seen:
            return {"ok": False,
                    "error": "Ürün listesi vergi kategorisi alanını taşımıyor; sayılar "
                             "hesaplanamadı. Sıfır yazmak «hiçbir ürüne atanmamış» "
                             "süzgecini yanlış yakardı, bu yüzden hiçbir şey kaydedilmedi."}

        stamp = taxes.now_iso()
        loaded = await self._load(with_usage=False)
        # KATEGORİ LİSTESİ OKUNAMADIYSA HİÇBİR ŞEY YAZILMAZ. Sayısı sıfır çıkan
        # kategori de yazılmak zorunda: "taranmadı" ile "ürünü yok" ayrımı ancak
        # böyle korunur. Liste okunamazsa yalnız üründe GÖRÜLEN kategoriler
        # yazılırdı; ürünü kalmamış bir kategori eski (sıfır olmayan) sayısıyla
        # asılı kalır ve «hiçbir ürüne atanmamış» süzgeci sessizce yanlış çalışırdı.
        # Tarama elle tetiklenir ve tekrarlanabilir; yarım yazmaktansa hiç yazmamak
        # doğrudur.
        if not loaded["connected"]:
            return {"ok": False,
                    "error": f"Ürünler tarandı ({len(rows)} kayıt) ama vergi kategorisi "
                             f"listesi okunamadı: {loaded['error']} Sayılar KAYDEDİLMEDİ — "
                             "ürünü kalmamış kategoriler eski sayısıyla asılı kalır ve "
                             "«hiçbir ürüne atanmamış» süzgeci yanlış çalışırdı."}
        wanted = {row["id"] for row in loaded["categories"]} | set(counts)
        try:
            for category_id in sorted(wanted):
                await self._store.execute(
                    f"INSERT INTO {self._usage} (tax_category_id, product_count, scanned_at) "
                    "VALUES (?, ?, ?) ON CONFLICT(tax_category_id) DO UPDATE SET "
                    "product_count = excluded.product_count, scanned_at = excluded.scanned_at",
                    (int(category_id), int(counts.get(category_id, 0)), stamp),
                )
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": f"Sayılar kaydedilemedi: {failure}"}

        await self._record(target="mapping", action="scan_usage", reason="Katalog taraması",
                           actor=actor, result="ok",
                           detail={"products": len(rows), "categories": len(wanted)})
        return {"ok": True, "error": "", "products": len(rows), "categories": len(wanted),
                "unassigned": counts.get(0, 0), "scannedAt": stamp, "truncated": truncated}

    async def assign_preview(self, *, product_ids: list[int],
                             tax_category_id: int) -> dict[str, Any]:
        """FARK TABLOSU. Toplu eşleme bu tablo gösterilmeden UYGULANMAZ.

        Ürünler TEK TEK ve TAZE okunur: liste ucundan gelen vergi kategorisi
        eksik ya da bayat olabilir ve uygulama zaten ürünün tam gövdesine
        ihtiyaç duyar (OKU-DEĞİŞTİR-YAZ).
        """
        ids = [int(item) for item in (product_ids or []) if taxes.as_int(item)]
        if not ids:
            return {"ok": False, "error": "Ürün seçilmedi."}
        if not tax_category_id:
            return {"ok": False, "error": "Hedef vergi kategorisi seçilmedi."}
        if self._assign_limit <= 0:
            return {"ok": False, "error": self._assign_note(0)}
        if len(ids) > self._assign_limit:
            return {"ok": False,
                    "error": f"Tek seferde en çok {self._assign_limit} ürün eşlenebilir "
                             f"({len(ids)} seçildi). Mağazada toplu uç yok; her ürün ayrı "
                             "istekle yazılıyor ve dakikada 60 istek sınırı var."}

        loaded = await self._load(with_usage=False)
        # ÖNCE BAĞLANTI, SONRA "BULUNAMADI". Mağaza erişilemezken kategori
        # listesi boş geliyor ve aşağıdaki arama boş dönüyordu; ekran
        # "hedef vergi kategorisi mağazada bulunamadı" diyordu. Kategori
        # duruyor, mağaza kapalıydı — kullanıcıyı var olmayan bir veri
        # sorununu aramaya yollayan bir yalan.
        if not loaded["connected"]:
            return {"ok": False, "connected": False,
                    "error": f"Vergi kategorileri okunamadı, önizleme açılmadı: "
                             f"{loaded['error']}"}
        target = next((row for row in loaded["categories"] if row["id"] == int(tax_category_id)),
                      None)
        if not target:
            return {"ok": False, "error": "Hedef vergi kategorisi mağazada bulunamadı."}

        names = {row["id"]: row["name"] for row in loaded["categories"]}
        rows: list[dict[str, Any]] = []
        missing: list[int] = []
        for product_id in ids:
            try:
                raw = await self._api.product(product_id)
            except Exception as failure:  # noqa: BLE001 — biri okunamazsa gerisi sürsün
                missing.append(product_id)
                self._log.warning("önizleme için ürün okunamadı", productId=product_id,
                                  error=str(failure))
                continue
            rows.append(taxes.product_row(raw, categories=names))

        if not rows:
            return {"ok": False, "error": "Seçilen ürünlerin hiçbiri okunamadı."}

        diff = taxes.assign_rows(rows, category_id=int(tax_category_id),
                                 category_name=target["name"])
        token = uuid.uuid4().hex
        params = {"taxCategoryId": int(tax_category_id), "taxCategoryName": target["name"]}
        try:
            await self._store.execute(
                f"INSERT INTO {self._assign} (token, params, rows, status, created_at) "
                "VALUES (?, ?, ?, 'preview', ?)",
                (token, json.dumps(params, ensure_ascii=False),
                 json.dumps(diff, ensure_ascii=False), taxes.now_iso()),
            )
        except Exception as failure:  # noqa: BLE001 — jeton yoksa uygula reddedilir
            self._log.warning("önizleme kaydedilemedi", error=str(failure))
            return {"ok": False, "error": "Önizleme kaydedilemedi; uygulama açılmadı."}

        return {"ok": True, "error": "", "token": token, "rows": diff,
                "summary": taxes.assign_summary(diff), "missing": missing,
                "target": params, "note": self._assign_note(len(diff)),
                "notice": taxes.RATE_NOTICE}

    def _assign_note(self, count: int) -> str:
        if self._assign_limit <= 0:
            return ("Toplu eşleme kapalı (assign_limit = 0). Mağazada ürünün vergi "
                    "kategorisini toplu yazan bir uç yok; her ürün ayrı istekle yazılır. "
                    "Yönetici sınırı ayardan açabilir.")
        return (f"Mağazada toplu uç yok: {count} ürün SIRAYLA yazılır ve bu yaklaşık "
                f"{max(1, count // 55 + 1)} dakika sürer. Yarıda kalırsa yazılanlar kalır, "
                "kalanlar eski kategorisinde durur — hangisinin yazıldığı işlem geçmişinde "
                "görünür.")

    async def assign_apply(self, *, token: str, reason: str, actor: str,
                           dry_run: bool = True) -> dict[str, Any]:
        """Önizlenen eşlemeyi uygular. Jeton yoksa ya da tüketilmişse reddedilir."""
        problem = taxes.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}
        try:
            row = await self._store.fetch_one(
                f"SELECT token, params, rows, status FROM {self._assign} WHERE token = ?",
                (taxes.text(token),))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        if not row:
            return {"ok": False,
                    "error": "Önizleme bulunamadı. Fark tablosu görülmeden toplu eşleme "
                             "uygulanmaz; önizlemeyi yeniden alın."}
        if row["status"] != "preview":
            return {"ok": False, "error": "Bu önizleme zaten uygulandı."}

        diff = json.loads(row["rows"])
        params = json.loads(row["params"])
        targets = [item for item in diff if not item.get("skipped")]
        if not targets:
            return {"ok": False, "error": "Önizlemede değişen satır yok."}
        if self._assign_limit <= 0:
            return {"ok": False, "error": self._assign_note(len(targets))}

        applied, failed, error, done = await self._apply_sequential(
            targets, tax_category_id=taxes.as_int(params.get("taxCategoryId")),
            reason=reason, actor=actor, dry_run=dry_run)

        try:
            await self._store.execute(
                f"UPDATE {self._assign} SET status = ?, actor = ?, reason = ?, applied_at = ? "
                "WHERE token = ?",
                ("dry_run" if dry_run else "applied", actor, reason, taxes.now_iso(),
                 row["token"]),
            )
        except Exception as failure:  # noqa: BLE001 — jeton kapatılamadı, iş bitti
            self._log.warning("önizleme kapatılamadı", error=str(failure))

        await self._record(target="mapping", action="assign_tax_category", reason=reason,
                           actor=actor, result="dry_run" if dry_run else "ok",
                           detail={"applied": applied, "failed": failed,
                                   "token": row["token"], "ids": done})
        return {"ok": failed == 0, "error": error, "applied": applied, "failed": failed,
                "appliedIds": done, "dryRun": dry_run,
                "notice": f"{taxes.RATE_NOTICE} {taxes.INDEX_NOTICE}"}

    async def _apply_sequential(self, targets: list[dict[str, Any]], *, tax_category_id: int,
                                reason: str, actor: str,
                                dry_run: bool) -> tuple[int, int, str, list[int]]:
        """Sırayla yazar. Geçit hız kovasını kendisi tutuyor; burada bekleme yok."""
        applied = 0
        failed = 0
        error = ""
        done: list[int] = []
        for item in targets:
            product_id = taxes.as_int(item.get("id"))
            try:
                current = await self._api.product(product_id)
                body = taxes.product_body(current, tax_category_id=tax_category_id,
                                          channel=self._channel, locale=self._locale)
                await self._api.update_product(product_id, payload=body, reason=reason,
                                               actor=actor, dry_run=dry_run)
            except Exception as failure:  # noqa: BLE001 — biri patlarsa gerisi sürsün
                failed += 1
                error = error or self._fail(failure)
                await self._record(target=f"product:{product_id}", action="assign_tax_category",
                                   reason=reason, actor=actor, result="hata",
                                   detail={"error": str(failure)})
                continue
            applied += 1
            done.append(product_id)
        return applied, failed, error, done

    # ============================================================ KDV icmali

    def _default_range(self) -> tuple[str, str]:
        today = datetime.now(UTC).astimezone().date()
        return (today - timedelta(days=self._summary_days - 1)).isoformat(), today.isoformat()

    async def summary(self, *, start: str = "", end: str = "",
                      channel: str = "") -> dict[str, Any]:
        """Dönem KDV icmali — `accountant` rolünün ana ekranı.

        ÜÇ BELGE KÜMESİ sırayla çekilir: fatura (matrah + KDV), iade (düşüm) ve
        iptal edilen sipariş (yalnız rapor edilir). Paralel çekmek hız kovasını
        daha hızlı tüketir ve ilk hatada üçünü birden kaybettirir.
        """
        first, last = self._default_range()
        start = taxes.text(start) or first
        end = taxes.text(end) or last
        if start > end:
            return {"ok": False, "error": "Başlangıç tarihi bitişten sonra olamaz."}

        loaded = await self._load(with_usage=False)
        known = [row["percent"] for row in loaded["rates"] if row["percent"] is not None]

        # KANAL SÜZGECİ MAĞAZAYA GÖNDERİLMEZ: canlıda `?channel=zzzz` bile
        # bütün faturaları döndürüyor, yani parametre sessizce yok sayılıyor.
        # Gönderip "süzüldü" saymak, tek kanalın matrahı diye hepsinin
        # matrahını beyan etmek olurdu. Ayıklama `summary.summarize` içinde
        # kanal ADIYLA yapılır ve rapor bunu yazar.
        window = {"date_from": start, "date_to": end}

        try:
            invoices = await self._collect(self._api.invoices, window, "fatura")
            refunds = await self._collect(self._api.refunds, window, "iade")
            canceled = await self._collect(self._api.orders, {**window, "status": "canceled"},
                                           "iptal edilmiş sipariş")
        except _CollectError as failure:
            return {"ok": False, "connected": failure.connected, "error": str(failure)}

        result = vat.summarize(invoices=invoices, refunds=refunds, canceled=canceled,
                               start=start, end=end, known_percents=known, channel=channel)
        result["ok"] = True
        result["connected"] = True
        result["error"] = ""
        result["ratesConnected"] = loaded["connected"]
        result["notice"] = taxes.RATE_NOTICE
        if not loaded["connected"]:
            result["warnings"].append(
                "Tanımlı oranlar okunamadı; kalem kırılımı olmayan faturaların oranı "
                "türetilemedi ve «çözülemedi» satırına düştü.")
        return result

    async def _collect(self, call: Any, filters: dict[str, Any], label: str) -> list[Any]:
        """Belge kümesini çeker ve İKİ EMNİYET KEMERİNİ birden takar.

        1. TAVAN: belge sayısı tavana dayanırsa hata verilir. Eksik belgeyle
           hesaplanan matrah beyanı eksiltir ve bunu fark etmenin yolu yoktur.
        2. SÜZGEÇ DENETİMİ: Laravel tanımadığı sorgu parametresini SESSİZCE yok
           sayar. Aralık dışı belge geldiyse süzgeç uygulanmamıştır; yerelde
           kırpıp devam etmek de yanlış olurdu, çünkü sayfalama zaten yanlış
           kümenin üzerinde dönmüştür.
        """
        try:
            payload = await call(filters, all_pages=True)
        except Exception as failure:
            raise _CollectError(f"Belge listesi okunamadı ({label}): {self._fail(failure)}",
                                connected=False) from failure

        items = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
        if payload.get("truncated") or len(items) >= self._document_cap:
            raise _CollectError(
                f"Bu aralıkta {len(items)}+ belge var ({label}) ve tavana "
                f"({self._document_cap}) dayandı. Eksik belgeyle KDV icmali ÜRETİLMEDİ: "
                "yanlış beyan, geç beyandan kötüdür. Aralığı daraltın.", connected=True)

        honored = vat.range_honored(items, filters["date_from"], filters["date_to"])
        if honored is False:
            raise _CollectError(
                f"Mağaza {label} listesinde tarih süzgecini uygulamadı: aralık dışı belgeler "
                "döndü. İcmal ÜRETİLMEDİ — süzülmemiş bir listeden hesaplanan matrah "
                "yanlıştır.", connected=True)
        return items

    # ================================================================ rapor

    async def export_csv(self, kind: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Tüm kayıtların CSV'si. Görünen sayfanın CSV'sini panel kendi üretir."""
        params = params or {}
        if kind == "rates":
            loaded = await self._load()
            if not loaded["connected"]:
                return {"ok": False, "error": loaded["error"]}
            headers = ["Ad", "Oran", "Bölge", "Posta kodu", "Vergi kategorisi",
                       "Geçerlilik (yerel)", "Çakışma"]
            known = loaded["membershipKnown"]
            table = [[row["name"], row["percentLabel"], row["region"], row["zipLabel"],
                      self._category_count(row, known), row["effectiveFrom"] or "—",
                      "Evet" if row["conflict"] else "Hayır"] for row in loaded["rates"]]
            name = f"magaza-vergi-oranlari-{self._stamp()}.csv"
        elif kind == "vat":
            result = await self.summary(start=taxes.text(params.get("start")),
                                        end=taxes.text(params.get("end")),
                                        channel=taxes.text(params.get("channel")))
            if not result.get("ok"):
                return result
            headers = ["Oran", "Matrah", "KDV", "Toplam", "İade matrah", "İade KDV",
                       "Net matrah", "Net KDV", "Belge"]
            table = [[row["label"], money(row["base"]),
                      money(row["tax"]), money(row["total"]), money(row["refundBase"]),
                      money(row["refundTax"]), money(row["netBase"]), money(row["netTax"]),
                      number(row["documents"])] for row in result["rates"]]
            name = f"magaza-kdv-icmali-{result['range']['start']}_{result['range']['end']}.csv"
        else:
            return {"ok": False, "error": f"Bilinmeyen CSV: {kind}"}

        try:
            path = write_private(self._export_dir / name, csv_bytes(headers, table))
        except OSError as failure:
            return {"ok": False, "error": f"Dosya yazılamadı: {failure}"}
        return {"ok": True, "error": "", "path": str(path), "name": name, "rows": len(table)}

    @staticmethod
    def _stamp() -> str:
        return datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")

    @staticmethod
    def _category_count(row: dict[str, Any], membership_known: bool) -> str:
        """Oranın kaç vergi kategorisinde olduğu — BİLİNMİYORSA 0 YAZILMAZ.

        Ekran bunu "okunamadı" diye gösteriyordu ama CSV ve PDF `0` yazıyordu:
        aynı veri iki ayrı şey söylüyordu ve kâğıda basılan «0», mali müşavire
        "bu oran hiçbir kategoride kullanılmıyor" diye okunurdu. Canlıda
        (2026-08-13) liste ucu ilişkiyi hiç döndürmüyor, yani bu satır kâğıtta
        HER ZAMAN 0 çıkıyordu.
        """
        return number(row["categoryCount"]) if membership_known else "okunamadı"

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

        if kind == "vat":
            result = await self.summary(start=taxes.text(params.get("start")),
                                        end=taxes.text(params.get("end")),
                                        channel=taxes.text(params.get("channel")))
            if not result.get("ok"):
                return result
            content = self._vat_pdf(result)
            name = (f"magaza-kdv-icmali-{result['range']['start']}"
                    f"_{result['range']['end']}.pdf")
        else:
            loaded = await self._load()
            if not loaded["connected"]:
                return {"ok": False, "error": loaded["error"]}
            if not loaded["rates"]:
                return {"ok": False, "error": "Mağazada tanımlı vergi oranı yok."}
            content = self._rates_pdf(loaded)
            name = f"magaza-vergi-oranlari-{self._stamp()}.pdf"

        try:
            path = write_private(self._export_dir / name, content)
        except (OSError, ExportError) as failure:
            return {"ok": False, "error": str(failure)}
        self._log.info("vergi raporu üretildi", kind=kind, path=str(path))
        return {"ok": True, "error": "", "path": str(path), "name": name, "bytes": len(content)}

    def _vat_pdf(self, result: dict[str, Any]) -> bytes:
        """KDV icmali PDF'i — mali müşavire giden belge.

        Çözülemeyen tutar KENDİ SATIRINDA durur ve dipnotta açıklanır: raporu
        okuyan kişi neyin kesin neyin şüpheli olduğunu görmeden imza atmamalı.
        """
        window = result["range"]
        totals = result["totals"]
        sections: list[dict[str, Any]] = [{
            "kind": "tiles", "title": "Dönem toplamı",
            "tiles": [("Net matrah", money(totals["netBase"])),
                      ("Net KDV", money(totals["netTax"])),
                      ("Net toplam", money(totals["netTotal"])),
                      ("Fatura", number(result["documents"]["invoices"])),
                      ("İade", number(result["documents"]["refunds"]))],
        }, {
            "kind": "table", "title": "Oran bazlı kırılım",
            "headers": ["Oran", "Matrah", "KDV", "Toplam", "İade KDV", "Net KDV", "Belge"],
            "align": "LRRRRRR", "widths": [1.2, 1.3, 1.2, 1.3, 1.2, 1.2, 0.7],
            "rows": [[row["label"], money(row["base"]), money(row["tax"]), money(row["total"]),
                      money(row["refundTax"]), money(row["netTax"]), number(row["documents"])]
                     for row in result["rates"]],
        }]

        if result["unresolved"]["documents"]:
            sections.append({
                "kind": "table", "title": "Oranı çözülemeyen belgeler",
                "headers": ["Matrah", "KDV", "İade matrah", "İade KDV", "Belge"],
                "align": "RRRRR", "widths": [1, 1, 1, 1, 0.8],
                "rows": [[money(result["unresolved"]["base"]), money(result["unresolved"]["tax"]),
                          money(result["unresolved"]["refundBase"]),
                          money(result["unresolved"]["refundTax"]),
                          number(result["unresolved"]["documents"])]],
            })

        if result["channels"]:
            sections.append({
                "kind": "table", "title": "Kanal kırılımı",
                "headers": ["Kanal", "Matrah", "KDV", "İade KDV", "Net toplam", "Belge"],
                "align": "LRRRRR", "widths": [1.6, 1.2, 1.2, 1.2, 1.3, 0.7],
                "rows": [[row["channel"], money(row["base"]), money(row["tax"]),
                          money(row["refundTax"]), money(row["netTotal"]),
                          number(row["documents"])] for row in result["channels"]],
            })

        sections.append({"kind": "note", "text": taxes.RATE_NOTICE})
        for warning in result["warnings"]:
            sections.append({"kind": "note", "text": warning})

        subtitle = f"{window['start']} – {window['end']}"
        if result.get("channel"):
            subtitle += f" · kanal: {result['channel']}"
        return build_pdf(title="KDV icmali", subtitle=subtitle, sections=sections,
                         footer="Kontrol Merkezi · Mağaza · Finans")

    def _rates_pdf(self, loaded: dict[str, Any]) -> bytes:
        known = loaded["membershipKnown"]
        sections: list[dict[str, Any]] = [{
            "kind": "table", "title": "Vergi oranları",
            "headers": ["Ad", "Oran", "Bölge", "Posta kodu", "Kategori", "Geçerlilik"],
            "align": "LRLLRL", "widths": [2, 0.8, 1.6, 1.2, 0.8, 1],
            "rows": [[row["name"], row["percentLabel"], row["region"], row["zipLabel"],
                      self._category_count(row, known), row["effectiveFrom"] or "—"]
                     for row in loaded["rates"]],
        }, {
            "kind": "table", "title": "Vergi kategorileri",
            "headers": ["Kod", "Ad", "Oranlar", "Ürün"],
            "align": "LLLR", "widths": [1.2, 2, 2, 0.8],
            "rows": [[row["code"], row["name"], row["percentLabel"],
                      "—" if row["productCount"] is None else number(row["productCount"])]
                     for row in loaded["categories"]],
        }]
        if loaded["conflicts"]:
            sections.append({
                "kind": "table", "title": "Çakışan kurallar",
                "headers": ["Vergi kategorisi", "Bölge", "Kurallar", "Durum"],
                "align": "LLLL", "widths": [1.4, 1.4, 2.4, 1],
                "rows": [[item["categoryName"], item["region"], " ↔ ".join(item["rateNames"]),
                          "Çakışma" if item["kind"] == "conflict" else "Yinelenmiş"]
                         for item in loaded["conflicts"]],
            })
        sections.append({"kind": "note", "text": taxes.RATE_NOTICE})
        sections.append({"kind": "note", "text":
                         "Geçerlilik tarihi Kontrol Merkezi'nin YEREL notudur: Bagisto vergi "
                         "oranında tarih alanı tutmaz ve oranı bu tarihte kendiliğinden "
                         "değiştirmez."})
        if not known:
            # Kâğıt, ekranla aynı şeyi söylemek zorunda: "Kategori" sütununda
            # sayı yerine «okunamadı» yazıyorsa nedeni de raporda durmalı.
            sections.append({"kind": "note", "text": taxes.MEMBERSHIP_NOTICE})
        rate_count = number(len(loaded["rates"]))
        category_count = number(len(loaded["categories"]))
        return build_pdf(title="Vergi oranları ve kategorileri",
                         subtitle=f"{rate_count} oran · {category_count} vergi kategorisi",
                         sections=sections, footer="Kontrol Merkezi · Mağaza · Finans")

    async def _render_pages(self, path: Path, *, max_pages: int = 12,
                            dpi: int = 110) -> list[str]:
        binary = shutil.which("pdftoppm")
        if not binary:
            raise PreviewError(
                "Önizleme üretilemedi: `pdftoppm` yok (poppler-utils kurulmalı). "
                "Rapor yine de kaydedildi ve yazdırılabilir.")
        with tempfile.TemporaryDirectory(prefix="km-vergi-onizleme-") as folder:
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

    # =============================================================== yetenek

    async def rate_table(self) -> dict[str, Any]:
        """`store.tax.rates` yeteneğinin verdiği salt okunur tablo.

        Tüketici ekranlar (Ürünler, Fatura, Siparişler) "bu vergi kategorisi
        yüzde kaç" sorusunu buradan sorar; kendi başlarına geçide gitmezler.
        HATA FIRLATMAZ: yetenek çağrısı patlarsa tüketen ekran düşerdi (K7).
        """
        loaded = await self._load(with_usage=False)
        by_category = {}
        for category in loaded["categories"]:
            by_category[category["id"]] = {
                "id": category["id"], "code": category["code"], "name": category["name"],
                "percentages": category["percentages"],
                "percent": category["percentages"][0] if category["percentages"] else None,
            }
        return {"connected": loaded["connected"], "error": loaded["error"],
                "rates": loaded["rates"], "categories": list(by_category.values()),
                "byCategoryId": by_category, "notice": taxes.RATE_NOTICE,
                # Tüketen ekran `percent: None` gördüğünde "KDV yok" sanmasın:
                # ilişki okunamıyorsa yüzde de bilinmiyordur.
                "membershipKnown": loaded["membershipKnown"]}


class _CollectError(RuntimeError):
    """Belge kümesi güvenle çekilemedi — icmal ÜRETİLMEZ."""

    def __init__(self, message: str, *, connected: bool) -> None:
        super().__init__(message)
        self.connected = connected


__all__ = ["PreviewError", "TaxService"]
