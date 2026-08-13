"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`FakeStore` SQL'i ayrıştırmaz; servisin yazdığı iki ifadeyi (denetim satırı,
sürüm satırı) tanıyacak kadarını yapar. Amaç çekirdek depoyu taklit etmek
değil, servisin doğru anda doğru satırı yazdığını görmek.
"""

from __future__ import annotations

from typing import Any


class FakeLog:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict[str, Any]]] = []

    def _add(self, level: str, message: str, **fields: Any) -> None:
        self.records.append((level, message, fields))

    def info(self, message: str, **fields: Any) -> None:
        self._add("info", message, **fields)

    def warning(self, message: str, **fields: Any) -> None:
        self._add("warning", message, **fields)

    def error(self, message: str, **fields: Any) -> None:
        self._add("error", message, **fields)


class FakeStore:
    """`ModuleStore` yüzeyi. Satırları bellekte tutar."""

    def __init__(self, module_id: str = "store_cms") -> None:
        self.module_id = module_id
        self.audit: list[dict[str, Any]] = []
        self.versions: list[dict[str, Any]] = []
        self.broken = False          # True: her yazma patlar (sürüm alınamaz)

    def table(self, name: str) -> str:
        return f"mod_{self.module_id}_{name}"

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        if self.broken:
            raise RuntimeError("depo yazılamıyor")
        text = " ".join(sql.split())
        if "_audit" in text and text.startswith("INSERT"):
            keys = ("page_id", "action", "reason", "actor", "result", "detail", "created_at")
            self.audit.append(dict(zip(keys, params, strict=False)))
        elif "_versions" in text and text.startswith("INSERT"):
            keys = ("page_id", "title", "slug", "html_content", "meta_title",
                    "meta_description", "meta_keywords", "actor", "reason", "action",
                    "created_at")
            row = dict(zip(keys, params, strict=False))
            row["id"] = len(self.versions) + 1
            self.versions.append(row)

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        if "_versions" in sql:
            for row in self.versions:
                if row["id"] == params[0]:
                    return row
        return None

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if "_versions" in sql:
            rows = [row for row in self.versions if row["page_id"] == params[0]]
            return [{**row, "size": len(row["html_content"])} for row in reversed(rows)]
        if "_audit" in sql:
            rows = list(reversed(self.audit))
            if "WHERE page_id" in " ".join(sql.split()):
                rows = [row for row in rows if row["page_id"] == params[0]]
            return rows
        return []


PAGE = {
    "id": 7,
    "translations": [{
        "locale": "tr",
        "page_title": "Mesafeli Satış Sözleşmesi",
        "url_key": "mesafeli-satis-sozlesmesi",
        "html_content": "<p>Sözleşme metni. <a href=\"/iade-ve-cayma-hakki\">İade</a></p>",
        "meta_title": "Mesafeli satış sözleşmesi | BBD Store",
        "meta_description": "Mesafeli satış sözleşmesinin tam metni.",
        "meta_keywords": "",
    }],
    "channels": [{"id": 1, "code": "default"}],
    "updated_at": "2026-08-01T10:00:00",
}


class FakeApi:
    """`store.api` yeteneğinin testlik yüzü. Yalnız kullanılan metotlar var."""

    def __init__(self, pages: list[dict[str, Any]] | None = None) -> None:
        self.pages_list = pages if pages is not None else [dict(PAGE)]
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()
        self.rewrites: list[dict[str, Any]] = []
        self.themes_payload: dict[str, Any] = {"items": []}
        #: Arama terimleri SAYFA SAYFA döner: servis tavan denetimini
        #: yapabilsin diye sahtesi de sayfalamayı taklit eder.
        self.terms: list[dict[str, Any]] = []
        self.term_page_size = 50
        self.synonym_rows: list[dict[str, Any]] = []
        self.sitemap_rows: list[dict[str, Any]] = []
        self.generated: dict[str, Any] = {"generatedAt": "2026-08-13T12:00:00",
                                          "generatedSitemaps": 3, "indexFile": "sitemap.xml"}

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        if name in self.fail:
            raise RuntimeError(f"{name} patladı")
        self.calls.append((name, args, kwargs))

    def used(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for called, _, kwargs in self.calls if called == name]

    async def cms_pages(self, filters: Any = None, *, page: int = 1, per_page: int | None = None,
                        all_pages: bool = False) -> dict[str, Any]:
        self._record("cms_pages", filters, page=page, per_page=per_page, all_pages=all_pages)
        return {"items": self.pages_list, "meta": {"total": len(self.pages_list)}}

    async def cms_page(self, page_id: int) -> dict[str, Any]:
        self._record("cms_page", page_id)
        for row in self.pages_list:
            if row.get("id") == page_id:
                return row
        raise RuntimeError("Kayıt bulunamadı")

    async def save_cms_page(self, *, payload: dict[str, Any], page_id: int | None = None,
                            reason: str, actor: str = "",
                            dry_run: bool | None = None) -> dict[str, Any]:
        self._record("save_cms_page", page_id, payload=payload, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run), "sent": not dry_run, "data": {"id": 99}}

    async def url_rewrites(self, filters: Any = None, *, page: int = 1,
                           per_page: int | None = None) -> dict[str, Any]:
        self._record("url_rewrites", filters, page=page, per_page=per_page)
        return {"items": self.rewrites, "meta": {}}

    async def save_url_rewrite(self, *, payload: dict[str, Any], rewrite_id: int | None = None,
                               reason: str, actor: str = "",
                               dry_run: bool | None = None) -> dict[str, Any]:
        self._record("save_url_rewrite", rewrite_id, payload=payload, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def delete_url_rewrite(self, rewrite_id: int, *, reason: str, actor: str = "",
                                 dry_run: bool | None = None) -> dict[str, Any]:
        self._record("delete_url_rewrite", rewrite_id, reason=reason, actor=actor,
                     dry_run=dry_run)
        self.rewrites = [row for row in self.rewrites if row.get("id") != rewrite_id]
        return {"ok": True, "dryRun": bool(dry_run)}

    async def search_terms(self, filters: Any = None, *, page: int = 1,
                           per_page: int | None = None) -> dict[str, Any]:
        self._record("search_terms", filters, page=page, per_page=per_page)
        size = per_page or self.term_page_size
        start = (max(1, page) - 1) * size
        window = self.terms[start:start + size]
        last = max(1, -(-len(self.terms) // size))
        return {"items": window, "meta": {"currentPage": page, "perPage": size,
                                          "lastPage": last, "total": len(self.terms)}}

    async def search_synonyms(self, filters: Any = None, *, page: int = 1,
                              per_page: int | None = None) -> dict[str, Any]:
        self._record("search_synonyms", filters, page=page, per_page=per_page)
        return {"items": self.synonym_rows, "meta": {}}

    async def create_search_synonym(self, *, payload: dict[str, Any], reason: str,
                                    actor: str = "",
                                    dry_run: bool | None = None) -> dict[str, Any]:
        self._record("create_search_synonym", payload=payload, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def update_search_synonym(self, synonym_id: int, *, payload: dict[str, Any],
                                    reason: str, actor: str = "",
                                    dry_run: bool | None = None) -> dict[str, Any]:
        self._record("update_search_synonym", synonym_id, payload=payload, reason=reason,
                     actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def delete_search_synonym(self, synonym_id: int, *, reason: str, actor: str = "",
                                    dry_run: bool | None = None) -> dict[str, Any]:
        self._record("delete_search_synonym", synonym_id, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def sitemaps(self, filters: Any = None, *, page: int = 1,
                       per_page: int | None = None) -> dict[str, Any]:
        self._record("sitemaps", filters, page=page, per_page=per_page)
        return {"items": self.sitemap_rows, "meta": {}}

    async def create_sitemap(self, *, payload: dict[str, Any], reason: str, actor: str = "",
                             dry_run: bool | None = None) -> dict[str, Any]:
        self._record("create_sitemap", payload=payload, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def update_sitemap(self, sitemap_id: int, *, payload: dict[str, Any], reason: str,
                             actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        self._record("update_sitemap", sitemap_id, payload=payload, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def generate_sitemap(self, sitemap_id: int, *, reason: str, actor: str = "",
                               dry_run: bool | None = None) -> dict[str, Any]:
        self._record("generate_sitemap", sitemap_id, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {**self.generated, "dryRun": bool(dry_run)}

    async def themes(self, filters: Any = None) -> dict[str, Any]:
        self._record("themes", filters)
        return self.themes_payload

    async def channels(self) -> dict[str, Any]:
        self._record("channels")
        return {"items": [{"code": "default", "name": "Varsayılan"}]}

    async def locales(self) -> dict[str, Any]:
        self._record("locales")
        return {"items": [{"code": "tr", "name": "Türkçe"}]}
