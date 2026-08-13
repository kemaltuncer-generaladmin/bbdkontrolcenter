"""CMS — iş kuralları.

VERİ MAĞAZADADIR, KARAR BURADADIR. Sayfalar `store.api` geçidinden gelir (K4);
bu modül Bagisto verisinin kopyasını tutmaz. Yerel tablolar yalnız mağazada
KARŞILIĞI OLMAYAN iki şeyi saklar: yazma gerekçesi (denetim izi) ve SÜRÜM
GEÇMİŞİ — Bagisto CMS sayfasının eski hâlini tutmuyor, "dün ne yazıyordu"
sorusunun cevabı yalnız burada var. Geri alma da bu tablodan yapılır.

ÖLÇEK. CMS sayfası onlarla ölçülür (yasal metinler, kurumsal sayfalar, SSS).
Bu yüzden liste TAMAMI çekilip burada süzülür: mağaza ucu içerik METNİNDE
arama yapamıyor ve arama bu ekranın ana işi. Aynı yaklaşım 1.419 üründe
yasaktır (store_products tam liste çekmez); fark ölçektedir.

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
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any

from km_sdk import ExportError, build_pdf, csv_bytes, number, report_dir, write_private

from . import content, seo

#: Arama terimi taramasının üst sınırı (sayfa sayısı). Uç `results` süzgeci
#: sunmuyor; sonuçsuz aramaları görebilmek için liste sırayla çekilir. Canlıda
#: 19 terim var, 10 sayfa (≈500 terim) fazlasıyla yeter ve bozuk `meta`
#: yüzünden sonsuz taramayı da engeller.
TERM_PAGES = 10
TERM_PAGE_SIZE = 50

#: Rapor ve liste taramasında kabul edilen üst sınır. Canlıda onlarca sayfa
#: var; 500 tavanı büyümeye yer bırakır ve bozuk `meta` yüzünden sonsuz
#: taramayı engeller.
PAGE_CAP = 500

CHIPS = ("seo_missing", "broken_links", "empty", "legal")


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


class PreviewError(RuntimeError):
    """Önizleme görüntüsü üretilemedi. Rapor yine de kaydedilmiştir."""


class CmsService:
    """CMS ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ."""

    def __init__(self, *, api: Any, store: Any, log: Any, config: dict[str, Any],
                 printer: Any = None, category: str = "Mağaza", subcategory: str = "Müşteri",
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
        self._versions = store.table("versions")

    # ------------------------------------------------------------- ayarlar

    @property
    def _channel(self) -> str:
        return str(self._config.get("channel") or "default")

    @property
    def _locale(self) -> str:
        return str(self._config.get("locale") or "tr")

    @property
    def _base_url(self) -> str:
        return str(self._config.get("site_url") or "")

    @property
    def _page_size(self) -> int:
        return max(10, min(200, content.as_int(self._config.get("page_size"), 25)))

    @property
    def _legal_slugs(self) -> dict[str, str]:
        raw = self._config.get("legal_slugs")
        if not isinstance(raw, dict):
            return {}
        return {str(key): str(value or "") for key, value in raw.items()}

    @property
    def _faq_slug(self) -> str:
        return str(self._config.get("faq_slug") or "")

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

    def _guard(self, reason: str) -> str:
        """Gerekçe backend'de DE doğrulanır (K9): arayüzde gizlemek yetmez."""
        return content.reason_error(reason)

    async def _record(self, *, page_id: int, action: str, reason: str, actor: str,
                      result: str, detail: Any = None) -> None:
        """Yerel denetim izi. Bagisto denetim tutuyor ama GEREKÇEYİ tutmuyor;
        ayrıca ağ koparsa "ne yapmaya çalıştık" kaydı burada kalır."""
        try:
            await self._store.execute(
                f"INSERT INTO {self._audit} "
                "(page_id, action, reason, actor, result, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (int(page_id or 0), action, reason, actor, result,
                 json.dumps(detail or {}, ensure_ascii=False), _now()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın
            self._log.warning("denetim izi yazılamadı", action=action, error=str(failure))

    # ================================================================ tarama

    async def _scan(self) -> tuple[list[tuple[dict[str, Any], str]], str]:
        """Tüm sayfalar: (satır, ham HTML) çiftleri. Hata metni ikinci değerde."""
        try:
            payload = await self._api.cms_pages({"locale": self._locale}, all_pages=True)
        except Exception as failure:  # noqa: BLE001 — ekran ayakta kalmalı (K7)
            self._log.warning("CMS sayfaları okunamadı", error=str(failure))
            return [], self._fail(failure)

        out: list[tuple[dict[str, Any], str]] = []
        for raw in (payload.get("items") or [])[:PAGE_CAP]:
            if not isinstance(raw, dict):
                continue
            html = content.text(content.field(raw, self._locale, "html_content", "content"))
            out.append((content.page_row(raw, locale=self._locale), html))
        return out, ""

    async def _rewrite_sources(self) -> set[str]:
        """Yönlendirme kaynakları — kırık bağlantı denetimi bunları sağlam sayar."""
        try:
            payload = await self._api.url_rewrites({"locale": self._locale})
        except Exception as failure:  # noqa: BLE001 — K7, denetim yine çalışır
            self._log.info("yönlendirmeler okunamadı", error=str(failure))
            return set()
        return {content.normalize_path(content.redirect_row(raw)["source"])
                for raw in (payload.get("items") or []) if isinstance(raw, dict)}

    def _enrich(self, rows: list[tuple[dict[str, Any], str]],
                rewrites: set[str]) -> list[dict[str, Any]]:
        """Satırlara dal, SEO durumu ve kırık bağlantı sayısını ekler."""
        slugs = {content.fold(row["slug"]) for row, _ in rows if row["slug"]}
        legal = self._legal_slugs
        out: list[dict[str, Any]] = []
        for row, html in rows:
            broken = content.broken_links(html, known_slugs=slugs, rewrite_sources=rewrites)
            item = dict(row)
            item["group"] = content.page_group(row, legal_slugs=legal, faq_slug=self._faq_slug)
            item["groupLabel"] = content.GROUP_LABELS[item["group"]]
            item["seoComplete"] = bool(row["metaTitle"] and row["metaDescription"])
            item["brokenLinks"] = len(broken)
            item["brokenList"] = [entry["href"] for entry in broken]
            out.append(item)
        return out

    # ================================================================= liste

    async def pages(self, *, q: str = "", chip: str = "", channel: str = "",
                    page: int = 1, size: int = 0) -> dict[str, Any]:
        """Sayfa ağacı + liste. Arama BAŞLIK, SLUG ve İÇERİK METNİNDE çalışır."""
        per_page = size or self._page_size
        rows, error = await self._scan()
        if error:
            return {"ok": True, "connected": False, "error": error, "items": [], "tree": [],
                    "total": 0, "page": 1, "size": per_page, "pages": 0, "counts": {}}

        rewrites = await self._rewrite_sources()
        enriched = self._enrich(rows, rewrites)
        bodies = {row["id"]: content.strip_html(html) for row, html in rows}

        counts = {
            "all": len(enriched),
            "seo_missing": len([row for row in enriched if not row["seoComplete"]]),
            "broken_links": len([row for row in enriched if row["brokenLinks"]]),
            "empty": len([row for row in enriched if row["chars"] < 200]),
            "legal": len([row for row in enriched if row["group"] == "legal"]),
        }

        items = [row for row in enriched
                 if content.page_matches(row, bodies.get(row["id"], ""), q)]
        if chip == "seo_missing":
            items = [row for row in items if not row["seoComplete"]]
        elif chip == "broken_links":
            items = [row for row in items if row["brokenLinks"]]
        elif chip == "empty":
            items = [row for row in items if row["chars"] < 200]
        elif chip == "legal":
            items = [row for row in items if row["group"] == "legal"]
        if channel:
            items = [row for row in items if channel in row["channels"] or not row["channels"]]

        query = content.text(q)
        for row in items:
            row["hit"] = content.snippet(bodies.get(row["id"], ""), query) if query else ""

        items.sort(key=lambda row: (list(content.GROUP_LABELS).index(row["group"]),
                                    content.fold(row["title"])))
        total = len(items)
        pages_count = max(1, -(-total // per_page)) if total else 0
        start = max(0, (max(1, page) - 1) * per_page)
        window = items[start:start + per_page]

        return {
            "ok": True, "connected": True, "error": "",
            "items": window,
            "tree": self._tree(items),
            "total": total, "page": page, "size": per_page, "pages": pages_count,
            "counts": counts,
            "scanned": len(enriched),
            "capped": len(enriched) >= PAGE_CAP,
        }

    @staticmethod
    def _tree(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Sol ağaç: dal → sayfa. Boş dal gösterilmez."""
        out: list[dict[str, Any]] = []
        for key, label in content.GROUP_LABELS.items():
            branch = [row for row in items if row["group"] == key]
            if not branch:
                continue
            out.append({"key": key, "label": label, "count": len(branch),
                        "items": [{"id": row["id"], "title": row["title"], "slug": row["slug"],
                                   "status": row["status"], "seoComplete": row["seoComplete"],
                                   "brokenLinks": row["brokenLinks"], "chars": row["chars"],
                                   "hit": row.get("hit", "")} for row in branch]})
        return out

    # ================================================================ detay

    async def page(self, page_id: int) -> dict[str, Any]:
        """Tek sayfa: içerik, SEO ölçüsü, kırık bağlantılar ve sürüm sayısı."""
        try:
            raw = await self._api.cms_page(int(page_id))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("sayfa okunamadı", pageId=page_id, error=str(failure))
            return {"ok": False, "connected": False, "error": self._fail(failure)}

        row = content.page_row(raw, locale=self._locale)
        html = content.text(content.field(raw, self._locale, "html_content", "content"))
        clean = content.sanitize_html(html)

        rows, _ = await self._scan()
        slugs = {content.fold(item["slug"]) for item, _ in rows if item["slug"]}
        rewrites = await self._rewrite_sources()

        versions = await self.versions(page_id)
        return {
            "ok": True, "connected": True, "error": "",
            "page": row,
            "content": clean,
            # Temizlik bir şey attıysa kullanıcı BİLİR: sessizce kısaltmak,
            # "kaydettim ama içerik eksildi" şaşkınlığı üretir.
            "sanitized": clean != html,
            "seo": content.seo_view(title=row["title"], meta_title=row["metaTitle"],
                                    meta_description=row["metaDescription"],
                                    slug=row["slug"], base_url=self._base_url),
            "brokenLinks": content.broken_links(html, known_slugs=slugs,
                                                rewrite_sources=rewrites),
            "group": content.page_group(row, legal_slugs=self._legal_slugs,
                                        faq_slug=self._faq_slug),
            "versions": versions.get("items", []),
            "notice": content.PUBLISH_NOTICE,
        }

    async def versions(self, page_id: int, *, limit: int = 30) -> dict[str, Any]:
        """Sürüm geçmişi. Kayıtlar YAZMADAN ÖNCE alınır; en yenisi başta."""
        try:
            rows = await self._store.fetch_all(
                f"SELECT id, page_id, title, slug, meta_title, meta_description, "
                f"actor, reason, action, created_at, length(html_content) AS size "
                f"FROM {self._versions} WHERE page_id = ? ORDER BY id DESC LIMIT ?",
                (int(page_id), max(1, min(200, int(limit)))),
            )
        except Exception as failure:  # noqa: BLE001 — geçmiş okunamadı, ekran dursun
            return {"ok": True, "items": [], "error": self._fail(failure)}
        return {"ok": True, "error": "", "items": [
            {"id": row["id"], "title": row["title"], "slug": row["slug"],
             "metaTitle": row["meta_title"], "metaDescription": row["meta_description"],
             "actor": row["actor"], "reason": row["reason"], "action": row["action"],
             "createdAt": row["created_at"], "chars": content.as_int(row["size"])}
            for row in rows
        ]}

    async def version_body(self, version_id: int) -> dict[str, Any]:
        """Bir sürümün tam içeriği — geri almadan önce okunur."""
        try:
            row = await self._store.fetch_one(
                f"SELECT id, page_id, title, slug, html_content, meta_title, meta_description, "
                f"meta_keywords, actor, created_at FROM {self._versions} WHERE id = ?",
                (int(version_id),))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        if not row:
            return {"ok": False, "error": "Sürüm bulunamadı."}
        return {"ok": True, "error": "", "version": {
            "id": row["id"], "pageId": row["page_id"], "title": row["title"],
            "slug": row["slug"], "content": content.sanitize_html(row["html_content"]),
            "metaTitle": row["meta_title"], "metaDescription": row["meta_description"],
            "metaKeywords": row["meta_keywords"], "actor": row["actor"],
            "createdAt": row["created_at"],
        }}

    async def _snapshot(self, page_id: int, raw: dict[str, Any], *, actor: str,
                        reason: str, action: str) -> bool:
        """Yazmadan ÖNCE mevcut hâli saklar.

        SİLME YOK, EKLEME VAR: geri alma da yeni bir sürüm bırakır, satır
        silinmez. Böylece "geri aldım ama yanlış sürüme döndüm" durumu da
        geri alınabilir.
        """
        row = content.page_row(raw, locale=self._locale)
        html = content.text(content.field(raw, self._locale, "html_content", "content"))
        try:
            await self._store.execute(
                f"INSERT INTO {self._versions} (page_id, title, slug, html_content, "
                "meta_title, meta_description, meta_keywords, actor, reason, action, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (int(page_id), row["title"], row["slug"], html, row["metaTitle"],
                 row["metaDescription"], row["metaKeywords"], actor, reason, action, _now()),
            )
        except Exception as failure:  # noqa: BLE001 — sürüm alınamadıysa yazma durur
            self._log.warning("sürüm kaydedilemedi", pageId=page_id, error=str(failure))
            return False
        return True

    # ================================================================ yazma

    async def save(self, page_id: int, *, patch: dict[str, Any], reason: str, actor: str,
                   dry_run: bool = True) -> dict[str, Any]:
        """Sayfayı günceller — OKU-DEĞİŞTİR-YAZ, önce sürüm alınır."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        clean = content.normalize_patch(patch)
        if not clean:
            return {"ok": False, "error": "Değişen alan yok."}

        try:
            current = await self._api.cms_page(int(page_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}

        if not await self._snapshot(page_id, current, actor=actor, reason=reason,
                                    action="save"):
            # Sürüm alınamadıysa YAZILMAZ: geri alınamayacak bir değişikliği
            # sessizce uygulamak, bu ekranın tek gerçek güvencesini kaldırır.
            return {"ok": False,
                    "error": "Önceki sürüm kaydedilemedi; geri alınamayacak yazma yapılmadı."}

        before_slug = content.text(content.field(current, self._locale, "url_key", "slug"))
        body = content.write_body(current, clean, locale=self._locale)
        after_slug = content.text(body.get("url_key"))

        await self._record(page_id=page_id, action="save", reason=reason, actor=actor,
                           result="denendi", detail={"fields": sorted(clean)})
        try:
            result = await self._api.save_cms_page(payload=body, page_id=int(page_id),
                                                   reason=reason, actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(page_id=page_id, action="save", reason=reason, actor=actor,
                               result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(page_id=page_id, action="save", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"fields": sorted(clean)})
        return {
            "ok": True, "error": "", "fields": sorted(clean),
            "dryRun": bool(result.get("dryRun", dry_run)),
            "sent": bool(result.get("sent", not dry_run)),
            "slugNotice": content.slug_change_notice(before_slug, after_slug),
            "notice": content.PUBLISH_NOTICE,
        }

    async def create(self, *, title: str, slug: str, reason: str, actor: str,
                     dry_run: bool = True) -> dict[str, Any]:
        """Yeni sayfa açar. İçerik düzenleyicide doldurulur."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        name = content.text(title)
        key = content.slugify(slug) or content.slugify(name)
        if not name or not key:
            return {"ok": False, "error": "Başlık zorunlu; slug başlıktan türetilemedi."}

        rows, error = await self._scan()
        if error:
            return {"ok": False, "error": error}
        if any(content.fold(row["slug"]) == content.fold(key) for row, _ in rows):
            return {"ok": False, "error": f"`{key}` adresi zaten kullanılıyor."}

        body = {"page_title": name, "url_key": key, "html_content": "",
                "meta_title": name, "meta_description": "", "locale": self._locale}
        try:
            result = await self._api.save_cms_page(payload=body, page_id=None, reason=reason,
                                                   actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(page_id=0, action="create", reason=reason, actor=actor,
                               result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        data = result.get("data") if isinstance(result.get("data"), dict) else result
        new_id = content.as_int(data.get("id") if isinstance(data, dict) else 0)
        await self._record(page_id=new_id, action="create", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok", detail={"slug": key})
        return {"ok": True, "error": "", "id": new_id, "slug": key,
                "dryRun": bool(result.get("dryRun", dry_run))}

    async def restore(self, page_id: int, *, version_id: int, reason: str, actor: str,
                      dry_run: bool = True) -> dict[str, Any]:
        """Sayfayı eski sürüme döndürür — SATIR SİLİNMEZ, yeni sürüm eklenir."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        found = await self.version_body(version_id)
        if not found.get("ok"):
            return found
        version = found["version"]
        if content.as_int(version["pageId"]) != int(page_id):
            return {"ok": False, "error": "Bu sürüm başka bir sayfaya ait."}

        try:
            current = await self._api.cms_page(int(page_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}

        if not await self._snapshot(page_id, current, actor=actor, reason=reason,
                                    action="restore"):
            return {"ok": False,
                    "error": "Geri almadan önceki hâl kaydedilemedi; işlem yapılmadı."}

        patch = {
            "page_title": version["title"], "url_key": version["slug"],
            "html_content": version["content"], "meta_title": version["metaTitle"],
            "meta_description": version["metaDescription"],
            "meta_keywords": version["metaKeywords"],
        }
        body = content.write_body(current, patch, locale=self._locale)
        try:
            result = await self._api.save_cms_page(payload=body, page_id=int(page_id),
                                                   reason=reason, actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(page_id=page_id, action="restore", reason=reason, actor=actor,
                               result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(page_id=page_id, action="restore", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"versionId": int(version_id)})
        return {"ok": True, "error": "", "versionId": int(version_id),
                "dryRun": bool(result.get("dryRun", dry_run)), "notice": content.PUBLISH_NOTICE}

    # ================================================================== SSS

    async def faq(self) -> dict[str, Any]:
        """SSS sayfası soru-cevap çiftlerine ayrılmış hâlde.

        Bagisto'da SSS diye bir kayıt türü YOK; SSS normal bir CMS sayfasıdır.
        Ekran onu `<h3>` soru + cevap kuralıyla yapılandırır — yeni bir veri
        modeli uydurmadan, sayfayı elle düzenleyen kişiyi de kilitlemeden.
        """
        slug = self._faq_slug
        if not slug:
            return {"ok": True, "connected": True, "error": "", "available": False,
                    "reason": "SSS sayfasının adresi ayarlanmamış (ayar: faq_slug).",
                    "pairs": [], "pageId": 0}

        rows, error = await self._scan()
        if error:
            return {"ok": True, "connected": False, "error": error, "available": False,
                    "pairs": [], "pageId": 0, "reason": error}

        match = next(((row, html) for row, html in rows
                      if content.fold(row["slug"]) == content.fold(slug)), None)
        if match is None:
            return {"ok": True, "connected": True, "error": "", "available": False,
                    "pairs": [], "pageId": 0,
                    "reason": f"`{slug}` adresinde bir sayfa yok. Sayfalar sekmesinden "
                              "açtıktan sonra sorular buradan düzenlenebilir."}

        row, html = match
        pairs = content.faq_parse(html)
        return {
            "ok": True, "connected": True, "error": "", "available": True,
            "pageId": row["id"], "slug": row["slug"], "title": row["title"],
            "pairs": pairs,
            # Ayrıştırma boşsa sayfa bu düzende yazılmamıştır; ekran ham
            # düzenleyiciye yönlendirir ve içeriği YENİDEN YAZMAZ.
            "structured": bool(pairs) or content.as_int(row["chars"]) == 0,
            "chars": row["chars"],
        }

    async def save_faq(self, *, pairs: list[dict[str, Any]], reason: str, actor: str,
                       dry_run: bool = True) -> dict[str, Any]:
        """Soru-cevap listesini sayfaya yazar. Sayfanın tamamı yeniden kurulur."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        current = await self.faq()
        if not current.get("available"):
            return {"ok": False, "error": current.get("reason") or "SSS sayfası bulunamadı."}
        clean = [pair for pair in (pairs or [])
                 if isinstance(pair, dict) and content.text(pair.get("question"))]
        if not clean:
            return {"ok": False, "error": "En az bir soru gerekir."}

        html = content.faq_render(clean)
        result = await self.save(current["pageId"], patch={"content": html}, reason=reason,
                                 actor=actor, dry_run=dry_run)
        if result.get("ok"):
            result["count"] = len(clean)
        return result

    # =========================================================== yasal metin

    async def legal(self) -> dict[str, Any]:
        """Dört yasal metnin durumu. Eksik metin mevzuat riskidir; ekran söyler."""
        rows, error = await self._scan()
        if error:
            return {"ok": True, "connected": False, "error": error, "items": []}
        items = content.legal_status([row for row, _ in rows], self._legal_slugs)
        return {"ok": True, "connected": True, "error": "", "items": items,
                "missing": len([item for item in items if not item["found"]])}

    # ========================================================= yönlendirmeler

    async def redirects(self, *, q: str = "") -> dict[str, Any]:
        """301/302 kayıtları. Liste küçük; süzme burada yapılır.

        Çakışan kaynak adresler İŞARETLENİR ama satır gizlenmez: çakışmayı
        görmeden düzeltmek mümkün değil.
        """
        try:
            payload = await self._api.url_rewrites({"locale": self._locale})
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("yönlendirmeler okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure), "items": [],
                    "conflicts": 0, "total": 0}

        rows = [seo.rewrite_row(raw) for raw in (payload.get("items") or [])
                if isinstance(raw, dict)]
        conflicts = seo.mark_conflicts(rows)
        total = len(rows)
        rows = [row for row in rows if seo.rewrite_matches(row, q)]
        return {"ok": True, "connected": True, "error": "", "items": rows,
                "conflicts": conflicts, "total": total,
                "entities": [{"value": key, "label": label}
                             for key, label in seo.ENTITY_LABELS.items()]}

    async def save_redirect(self, *, source: str, target: str, kind: int = 301,
                            rewrite_id: int = 0, entity: str = seo.DEFAULT_ENTITY,
                            reason: str, actor: str,
                            dry_run: bool = True) -> dict[str, Any]:
        """Yönlendirme ekler/günceller. Döngü ve çakışma YAZMADAN ÖNCE yakalanır."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        existing = await self.redirects()
        if not existing.get("connected"):
            return {"ok": False,
                    "error": "Mevcut yönlendirmeler okunamadı; çakışma denetlenmeden "
                             f"yazılmaz. ({existing.get('error')})"}

        problem = (seo.entity_error(entity)
                   or content.redirect_error(source, target, kind, existing["items"],
                                             rewrite_id=rewrite_id))
        if problem:
            return {"ok": False, "error": problem}

        # `entity_type` mağazada ZORUNLU alandır; göndermeyen istek doğrulamadan
        # döner. Gövde snake_case yazılır (okuma camelCase gelse de).
        body = {"entity_type": content.text(entity) or seo.DEFAULT_ENTITY,
                "request_path": content.normalize_path(source),
                "target_path": content.text(target),
                "redirect_type": int(kind), "locale": self._locale}
        try:
            result = await self._api.save_url_rewrite(
                payload=body, rewrite_id=int(rewrite_id) or None, reason=reason, actor=actor,
                dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(page_id=0, action="save_redirect", reason=reason, actor=actor,
                               result="hata", detail={"error": str(failure), **body})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(page_id=0, action="save_redirect", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok", detail=body)
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run)),
                "source": body["request_path"], "target": body["target_path"]}

    async def delete_redirect(self, rewrite_id: int, *, reason: str, actor: str,
                              dry_run: bool = True) -> dict[str, Any]:
        """Yönlendirmeyi siler.

        SİLME BURADA VAR — sayfa silmenin aksine. Yönlendirme bir içerik değil,
        bir kuraldır: yanlış kurulmuş 301 vitrini döngüye sokar ve onu
        "pasifleştirmenin" mağaza tarafında karşılığı yok. Kaydın ne olduğu
        silmeden önce denetim izine yazılır (ADR 0012).
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        if int(rewrite_id) <= 0:
            return {"ok": False, "error": "Silinecek kayıt seçilmedi."}

        existing = await self.redirects()
        row = next((item for item in existing.get("items") or []
                    if content.as_int(item.get("id")) == int(rewrite_id)), None)

        await self._record(page_id=0, action="delete_redirect", reason=reason, actor=actor,
                           result="denendi", detail={"rewriteId": int(rewrite_id), **(row or {})})
        try:
            result = await self._api.delete_url_rewrite(int(rewrite_id), reason=reason,
                                                        actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(page_id=0, action="delete_redirect", reason=reason, actor=actor,
                               result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(page_id=0, action="delete_redirect", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"rewriteId": int(rewrite_id)})
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run)),
                "notice": "Yönlendirme kalktı; bu adrese gelen eski bağlantılar artık "
                          "404 verecek."}

    # ======================================================== arama terimleri

    async def search_terms(self, *, q: str = "", only_zero: bool = False,
                           page: int = 1, size: int = 0) -> dict[str, Any]:
        """Müşterinin ne aradığı. SONUÇSUZ ARAMALAR ayrı süzgeçtir.

        Liste sırayla çekilip burada süzülür: uçta `results` süzgeci yok ve
        "aradı, bulamadı" satırlarını sunucu ayıramıyor. Ölçek buna izin
        veriyor (canlıda 19 terim); 1.419 üründe aynı yaklaşım YASAK olurdu.
        """
        per_page = size or self._page_size
        rows: list[dict[str, Any]] = []
        capped = False
        number = 1
        while number <= TERM_PAGES:
            try:
                payload = await self._api.search_terms({"locale": self._locale}, page=number,
                                                       per_page=TERM_PAGE_SIZE)
            except Exception as failure:  # noqa: BLE001 — ekran ayakta kalmalı (K7)
                self._log.warning("arama terimleri okunamadı", error=str(failure))
                return {"ok": True, "connected": False, "error": self._fail(failure),
                        "items": [], "summary": seo.term_summary([]), "total": 0,
                        "page": 1, "size": per_page, "pages": 0, "capped": False}
            items = [seo.term_row(raw) for raw in (payload.get("items") or [])
                     if isinstance(raw, dict)]
            rows.extend(items)
            meta = payload.get("meta") or {}
            last = content.as_int(meta.get("lastPage"), 1)
            if not items or number >= last:
                break
            number += 1
            # Tavana dayandık: liste EKSİK. Ekran bunu söyler; sessizce
            # kırpılmış bir listeyi tam sanmak yanlış karar üretir.
            capped = number > TERM_PAGES

        summary = seo.term_summary(rows)
        found = [row for row in rows if seo.term_matches(row, q)]
        if only_zero:
            found = [row for row in found if row["zeroResults"]]
        found.sort(key=seo.term_sort_key)

        total = len(found)
        pages_count = max(1, -(-total // per_page)) if total else 0
        start = max(0, (max(1, page) - 1) * per_page)
        return {"ok": True, "connected": True, "error": "",
                "items": found[start:start + per_page], "summary": summary,
                "total": total, "page": page, "size": per_page, "pages": pages_count,
                "capped": capped}

    # ========================================================== eş anlamlılar

    async def synonyms(self, *, q: str = "") -> dict[str, Any]:
        """Eş anlamlı grupları. Liste küçük; süzme burada yapılır."""
        try:
            payload = await self._api.search_synonyms({}, per_page=TERM_PAGE_SIZE)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("eş anlamlılar okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "items": [], "total": 0}

        rows = [seo.synonym_row(raw) for raw in (payload.get("items") or [])
                if isinstance(raw, dict)]
        total = len(rows)
        return {"ok": True, "connected": True, "error": "", "total": total,
                "items": [row for row in rows if seo.synonym_matches(row, q)]}

    async def save_synonym(self, *, name: str, terms: Any, synonym_id: int = 0,
                           reason: str, actor: str, dry_run: bool = True) -> dict[str, Any]:
        """Eş anlamlı grubu ekler/günceller. Çakışan kelime yazmadan önce yakalanır."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        existing = await self.synonyms()
        if not existing.get("connected"):
            return {"ok": False,
                    "error": "Mevcut gruplar okunamadı; çakışma denetlenmeden yazılmaz. "
                             f"({existing.get('error')})"}

        problem = seo.synonym_error(name, terms, existing["items"], synonym_id=synonym_id)
        if problem:
            return {"ok": False, "error": problem}

        # TUZAK: `terms` liste değil, virgülle ayrılmış TEK metindir.
        body = {"name": content.text(name), "terms": seo.synonym_text(terms)}
        try:
            if int(synonym_id or 0):
                result = await self._api.update_search_synonym(
                    int(synonym_id), payload=body, reason=reason, actor=actor, dry_run=dry_run)
            else:
                result = await self._api.create_search_synonym(
                    payload=body, reason=reason, actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(page_id=0, action="save_synonym", reason=reason, actor=actor,
                               result="hata", detail={"error": str(failure), **body})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(page_id=0, action="save_synonym", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok", detail=body)
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run)),
                "name": body["name"], "terms": seo.synonym_terms(terms)}

    async def delete_synonym(self, synonym_id: int, *, reason: str, actor: str,
                             dry_run: bool = True) -> dict[str, Any]:
        """Grubu siler. Grup bir kural, bir içerik değil; silmesi veri kaybı değildir."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        if int(synonym_id) <= 0:
            return {"ok": False, "error": "Silinecek grup seçilmedi."}

        try:
            result = await self._api.delete_search_synonym(int(synonym_id), reason=reason,
                                                           actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(page_id=0, action="delete_synonym", reason=reason, actor=actor,
                               result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(page_id=0, action="delete_synonym", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"synonymId": int(synonym_id)})
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run))}

    # ========================================================== site haritası

    async def sitemaps(self) -> dict[str, Any]:
        """Site haritası tanımları + son üretim zamanı."""
        try:
            payload = await self._api.sitemaps({}, per_page=TERM_PAGE_SIZE)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("site haritaları okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "items": [], "summary": seo.sitemap_summary([])}

        rows = [seo.sitemap_row(raw, base_url=self._base_url)
                for raw in (payload.get("items") or []) if isinstance(raw, dict)]
        return {"ok": True, "connected": True, "error": "", "items": rows,
                "summary": seo.sitemap_summary(rows)}

    async def save_sitemap(self, *, file_name: str, path: str = "/", sitemap_id: int = 0,
                           reason: str, actor: str, dry_run: bool = True) -> dict[str, Any]:
        """Tanım ekler/günceller. TANIM DOSYAYI ÜRETMEZ; üretim ayrı adımdır."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        existing = await self.sitemaps()
        if not existing.get("connected"):
            return {"ok": False,
                    "error": "Mevcut tanımlar okunamadı; çakışma denetlenmeden yazılmaz. "
                             f"({existing.get('error')})"}

        problem = seo.sitemap_error(file_name, path, existing["items"], sitemap_id=sitemap_id)
        if problem:
            return {"ok": False, "error": problem}

        body = {"file_name": content.text(file_name), "path": content.text(path) or "/"}
        try:
            if int(sitemap_id or 0):
                result = await self._api.update_sitemap(int(sitemap_id), payload=body,
                                                        reason=reason, actor=actor,
                                                        dry_run=dry_run)
            else:
                result = await self._api.create_sitemap(payload=body, reason=reason,
                                                        actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(page_id=0, action="save_sitemap", reason=reason, actor=actor,
                               result="hata", detail={"error": str(failure), **body})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(page_id=0, action="save_sitemap", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok", detail=body)
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run)),
                "fileName": body["file_name"],
                "notice": "Tanım kaydedildi ama dosya HENÜZ ÜRETİLMEDİ; “Üret” düğmesine "
                          "basın."}

    async def generate_sitemap(self, sitemap_id: int, *, reason: str, actor: str,
                               dry_run: bool = True) -> dict[str, Any]:
        """Site haritasını üretir — AĞIR İŞ.

        Yayındaki her kategori, ürün ve sayfa dolaşılıp XML yeniden yazılır;
        1.419 üründe zaman aşımına yaklaşır. Ekran bunu önden söyler, sessizce
        beklemez.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        if int(sitemap_id) <= 0:
            return {"ok": False, "error": "Üretilecek site haritası seçilmedi."}

        await self._record(page_id=0, action="generate_sitemap", reason=reason, actor=actor,
                           result="denendi", detail={"sitemapId": int(sitemap_id)})
        try:
            result = await self._api.generate_sitemap(int(sitemap_id), reason=reason,
                                                      actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(page_id=0, action="generate_sitemap", reason=reason, actor=actor,
                               result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(page_id=0, action="generate_sitemap", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"sitemapId": int(sitemap_id)})
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run)),
                "generatedAt": content.text(result.get("generatedAt"))[:19],
                "files": content.as_int(result.get("generatedSitemaps")),
                "indexFile": content.text(result.get("indexFile"))}

    # =============================================================== bloklar

    async def blocks(self, *, q: str = "") -> dict[str, Any]:
        """Statik bloklar — bu ekranda SALT OKUNUR.

        Bloklar Bagisto'da tema özelleştirmesi olarak duruyor ve slider/banner
        slotları da aynı tabloda. Onların sahibi Ana Ekran Görselleri ekranı;
        aynı kaydı iki ekranın yazması, birinin diğerinin üstüne geçmesi
        demektir. Metin bloğu düzenlemesi için BBD'ye özel uç bekleniyor.
        """
        try:
            payload = await self._api.themes({"channel": self._channel})
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.info("bloklar okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "items": [], "editable": False}

        rows = [content.block_row(raw) for raw in (payload.get("items") or [])
                if isinstance(raw, dict)]
        wanted = content.fold(q)
        if wanted:
            rows = [row for row in rows
                    if wanted in content.fold(f"{row['name']} {row['type']}")]
        return {
            "ok": True, "connected": True, "error": "", "items": rows, "editable": False,
            "notice": "Bloklar burada salt okunur. Görsel/slider blokları Ana Ekran "
                      "Görselleri ekranından yönetilir; metin bloğu düzenlemesi mağaza "
                      "tarafındaki BBD ucu yayınlanınca bu sekmede açılacak.",
        }

    # ================================================================ menüler

    async def menus(self) -> dict[str, Any]:
        """Vitrin menüsü — UÇ HENÜZ YOK.

        Geçitte yalnız `admin_menu` var ve o YÖNETİCİ PANELİNİN menüsüdür;
        vitrin menüsüyle ilgisi yok. Yanlış ucu kullanıp "menü buradan
        yönetiliyor" demek, personelin mağaza yönetici menüsünü bozmasına
        kapı açardı. Sekme durumu anlatır, sessizce patlamaz (K7).
        """
        return {
            "ok": True, "connected": True, "error": "", "available": False,
            "items": [],
            "reason": "Vitrin menüsü için mağaza tarafında uç yok. `bbd_cms_menus` ve "
                      "`bbd_save_cms_menu` uçları yayınlanınca bu sekme kendiliğinden "
                      "açılacak; o zamana kadar menü Bagisto yönetici arayüzünden "
                      "düzenlenir.",
        }

    # ============================================================== referans

    async def reference(self) -> dict[str, Any]:
        """Süzgeçlerin beslendiği listeler. Geçit 900 sn önbellekli."""
        out: dict[str, Any] = {"ok": True, "connected": True, "error": "",
                               "channels": [], "locales": [],
                               "channel": self._channel, "locale": self._locale,
                               "siteUrl": self._base_url,
                               "legalSlugs": self._legal_slugs, "faqSlug": self._faq_slug}
        try:
            channels = await self._api.channels()
            out["channels"] = [{"code": content.text(item.get("code")),
                                "name": content.text(item.get("name"))}
                               for item in (channels.get("items") or [])
                               if isinstance(item, dict)]
        except Exception as failure:  # noqa: BLE001 — K7
            out["connected"] = False
            out["error"] = self._fail(failure)

        try:
            locales = await self._api.locales()
            out["locales"] = [{"code": content.text(item.get("code")),
                               "name": content.text(item.get("name"))}
                              for item in (locales.get("items") or [])
                              if isinstance(item, dict)]
        except Exception as failure:  # noqa: BLE001 — K7
            out["connected"] = False
            out["error"] = out["error"] or self._fail(failure)
        return out

    async def audit(self, *, page_id: int = 0, limit: int = 50) -> dict[str, Any]:
        """Bu ekrandan yapılan yazmaların YEREL izi (gerekçeleriyle)."""
        sql = f"SELECT page_id, action, reason, actor, result, created_at FROM {self._audit} "
        params: tuple[Any, ...] = ()
        if page_id:
            sql += "WHERE page_id = ? "
            params = (int(page_id),)
        sql += "ORDER BY id DESC LIMIT ?"
        params = (*params, max(1, min(500, int(limit))))
        try:
            rows = await self._store.fetch_all(sql, params)
        except Exception as failure:  # noqa: BLE001 — iz okunamadı, ekran dursun
            return {"ok": True, "items": [], "error": self._fail(failure)}
        return {"ok": True, "error": "", "items": [
            {"pageId": row["page_id"], "action": row["action"], "reason": row["reason"],
             "actor": row["actor"], "result": row["result"], "createdAt": row["created_at"]}
            for row in rows
        ]}

    # ================================================================= rapor

    async def export_csv(self) -> dict[str, Any]:
        """Sayfa listesinin CSV'si — rapor klasörüne yazılır."""
        rows, error = await self._scan()
        if error:
            return {"ok": False, "error": error}
        enriched = self._enrich(rows, await self._rewrite_sources())

        headers = ["Başlık", "Adres", "Dal", "Kanal", "Durum", "Karakter", "Meta başlık",
                   "Meta açıklama", "Kırık bağlantı", "Güncelleme"]
        table = [[row["title"], f"/{row['slug']}", row["groupLabel"], ", ".join(row["channels"]),
                  "—" if row["status"] is None else ("Yayında" if row["status"] else "Kapalı"),
                  row["chars"], row["metaTitle"], row["metaDescription"],
                  row["brokenLinks"], row["updatedAt"]] for row in enriched]

        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        name = f"magaza-cms-sayfalari-{stamp}.csv"
        try:
            path = write_private(self._export_dir / name, csv_bytes(headers, table))
        except OSError as failure:
            return {"ok": False, "error": f"Dosya yazılamadı: {failure}"}
        return {"ok": True, "error": "", "path": str(path), "name": name, "rows": len(table)}

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
        if kind not in ("inventory", "legal"):
            return {"ok": False, "error": f"Bilinmeyen rapor: {kind}"}

        rows, error = await self._scan()
        if error:
            return {"ok": False, "error": error}
        if not rows:
            return {"ok": False, "error": "Mağazada CMS sayfası yok."}

        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        if kind == "inventory":
            enriched = self._enrich(rows, await self._rewrite_sources())
            body = self._inventory_pdf(enriched)
            name = f"magaza-icerik-envanteri-{stamp}.pdf"
        else:
            body = self._legal_pdf(rows)
            name = f"magaza-yasal-metinler-{stamp}.pdf"

        try:
            path = write_private(self._export_dir / name, body)
        except (OSError, ExportError) as failure:
            return {"ok": False, "error": str(failure)}
        self._log.info("CMS raporu üretildi", kind=kind, rows=len(rows), path=str(path))
        return {"ok": True, "error": "", "path": str(path), "name": name,
                "bytes": len(body), "rows": len(rows)}

    @staticmethod
    def _cell(value: Any) -> str:
        """PDF hücresi. TUZAK: rapor üreteci hücreyi reportlab `Paragraph`'ına
        veriyor ve o mini-HTML ayrıştırıyor; `Ürün & Fiyat` gibi bir başlık
        kaçırılmazsa PDF üretimi patlar."""
        return escape(content.text(value), quote=False)

    def _inventory_pdf(self, rows: list[dict[str, Any]]) -> bytes:
        missing_seo = [row for row in rows if not row["seoComplete"]]
        broken = [row for row in rows if row["brokenLinks"]]
        thin = [row for row in rows if row["chars"] < 200]
        sections: list[dict[str, Any]] = [{
            "kind": "tiles", "title": "Özet",
            "tiles": [("Sayfa", number(len(rows))), ("SEO eksik", number(len(missing_seo))),
                      ("Kırık bağlantı", number(len(broken))), ("İçeriği zayıf", number(len(thin)))],
        }, {
            "kind": "table", "title": "Sayfalar",
            "headers": ["Başlık", "Adres", "Dal", "Karakter", "SEO"],
            "align": "LLLRL", "widths": [2.4, 2, 1.2, 0.8, 0.8],
            "rows": [[self._cell(row["title"]), self._cell(f"/{row['slug']}"),
                      row["groupLabel"], number(row["chars"]),
                      "tam" if row["seoComplete"] else "eksik"] for row in rows[:300]],
        }]
        if broken:
            sections.append({
                "kind": "table", "title": "Kırık iç bağlantılar",
                "headers": ["Sayfa", "Bağlantı"], "align": "LL", "widths": [2, 3],
                "rows": [[self._cell(row["title"]), self._cell(href)] for row in broken
                         for href in row["brokenList"][:5]][:200],
            })
        return build_pdf(title="İçerik envanteri", subtitle=f"{len(rows)} CMS sayfası",
                         sections=sections, footer="Kontrol Merkezi · Mağaza")

    def _legal_pdf(self, rows: list[tuple[dict[str, Any], str]]) -> bytes:
        """Yasal metinlerin O GÜNKÜ tam hâli.

        Bu rapor arşiv içindir: bir uyuşmazlıkta "sözleşme o tarihte ne
        diyordu" sorusunun cevabı kâğıda basılabilir olmalı.
        """
        slugs = {content.fold(value): key for key, value in self._legal_slugs.items()
                 if content.text(value)}
        sections: list[dict[str, Any]] = []
        for row, html in rows:
            key = slugs.get(content.fold(row["slug"]))
            if not key:
                continue
            plain = content.strip_html(html)[:20_000]
            title = f"{content.LEGAL_LABELS.get(key, row['title'])} (/{row['slug']})"
            if sections:
                # Her metin yeni sayfada başlar: arşivden tek bir sözleşme
                # koparıp dosyalamak mümkün olsun.
                sections.append({"kind": "break"})
            if not plain:
                sections.append({"kind": "note", "title": title, "text": "(metin boş)"})
                continue
            # Metin parçalara bölünür: tek `Paragraph` içine 20 bin karakter
            # koymak reportlab'ı sayfa taşmasında çaresiz bırakıyor.
            for index in range(0, len(plain), 1_500):
                sections.append({"kind": "note",
                                 "title": title if index == 0 else "",
                                 "text": self._cell(plain[index:index + 1_500])})
        if not sections:
            sections.append({"kind": "note",
                             "text": "Ayarda tanımlı yasal metin sayfası mağazada bulunamadı."})
        return build_pdf(title="Yasal metinler",
                         subtitle=datetime.now(UTC).astimezone().strftime("%d.%m.%Y %H:%M"),
                         sections=sections, footer="Kontrol Merkezi · Mağaza")

    async def _render_pages(self, path: Path, *, max_pages: int = 12,
                            dpi: int = 110) -> list[str]:
        binary = shutil.which("pdftoppm")
        if not binary:
            raise PreviewError(
                "Önizleme üretilemedi: `pdftoppm` yok (poppler-utils kurulmalı). "
                "Rapor yine de kaydedildi ve yazdırılabilir.")
        with tempfile.TemporaryDirectory(prefix="km-cms-onizleme-") as folder:
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
