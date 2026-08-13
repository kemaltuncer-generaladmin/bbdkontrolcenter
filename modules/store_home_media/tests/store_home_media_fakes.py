"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`FakeStore` SQL'i ayrıştırmaz; servisin yazdığı iki ifadeyi (denetim satırı ve
görsel kaydı) tanıyacak kadarını yapar. Amaç çekirdek depoyu taklit etmek
değil, servisin doğru anda doğru satırı yazdığını görmek.
"""

from __future__ import annotations

import base64
import struct
import zlib
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

    def __init__(self, module_id: str = "store_home_media") -> None:
        self.module_id = module_id
        self.audit: list[dict[str, Any]] = []
        self.assets: list[dict[str, Any]] = []

    def table(self, name: str) -> str:
        return f"mod_{self.module_id}_{name}"

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        text = " ".join(sql.split())
        if "_audit" in text and text.startswith("INSERT"):
            keys = ("slot_id", "area", "action", "reason", "actor", "result", "detail",
                    "created_at")
            self.audit.append(dict(zip(keys, params, strict=False)))
        elif "_assets" in text and text.startswith("INSERT"):
            keys = ("slot_id", "area", "sha256", "mime", "width", "height", "bytes", "verdict",
                    "note", "actor", "created_at")
            self.assets.append(dict(zip(keys, params, strict=False)))

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        return None

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if "_audit" in sql:
            rows = list(reversed(self.audit))
            if "WHERE slot_id" in sql:
                rows = [row for row in rows if row["slot_id"] == params[0]]
            return rows
        return []


class FakeApiError(RuntimeError):
    """`StoreApiError` yüzü — geçit hataları `code` taşır ve servis ona bakar.

    `store_api` import EDİLMEZ (K3): modül modülü import etmez, test de etmez.
    Taklit edilen tek şey yüzeydir: mesaj + `code`.
    """

    def __init__(self, message: str, *, code: str = "", status: int = 0) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


class FakeApi:
    """`store.api` yeteneğinin testlik yüzü. Yalnız kullanılan metotlar var."""

    def __init__(self, carousel: list[dict[str, Any]] | None = None) -> None:
        self.carousel_items = carousel if carousel is not None else []
        self.theme_items: list[dict[str, Any]] = []
        self.lookup_items: list[dict[str, Any]] = []
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()
        #: `fail` yerine bu kullanılırsa metot verilen istisnayı fırlatır —
        #: geçidin kodlu hatalarını (`bbd_endpoint_missing`) taklit etmek için.
        self.raises: dict[str, Exception] = {}

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        if name in self.raises:
            raise self.raises[name]
        if name in self.fail:
            raise RuntimeError(f"{name} patladı")
        self.calls.append((name, args, kwargs))

    def used(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for called, _, kwargs in self.calls if called == name]

    async def bbd_carousel(self, filters: Any = None, *, page: int = 1,
                           per_page: int | None = None) -> dict[str, Any]:
        self._record("bbd_carousel", filters, page=page, per_page=per_page)
        return {"items": [dict(item) for item in self.carousel_items], "meta": {}}

    async def bbd_save_carousel_slot(self, *, payload: dict[str, Any],
                                     slot_id: int | None = None, reason: str, actor: str = "",
                                     dry_run: bool | None = None) -> dict[str, Any]:
        self._record("bbd_save_carousel_slot", payload=payload, slot_id=slot_id, reason=reason,
                     actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run), "sent": not dry_run,
                "data": {"id": slot_id or 99}}

    async def bbd_reorder_carousel(self, *, order: list[int], reason: str, actor: str = "",
                                   dry_run: bool | None = None) -> dict[str, Any]:
        self._record("bbd_reorder_carousel", order=order, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def upload_media(self, *, content: Any, filename: str, mime: str = "",
                           slot: str = "", position: int | None = None, reason: str,
                           actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        """Geçidin `upload_media` imzası — POST /api/admin/bbd/home/slides.

        Gerçek uç 2026-08-13'te 404; testler ikisini de kurar: yayına girmiş
        hâlini (buradaki başarılı yanıt) ve `raises` ile 404 hâlini.
        """
        self._record("upload_media", content=content, filename=filename, mime=mime, slot=slot,
                     position=position, reason=reason, actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run), "sent": not dry_run,
                "url": f"https://bbdstore.com.tr/storage/home/{filename}"}

    async def themes(self, filters: Any = None) -> dict[str, Any]:
        self._record("themes", filters)
        return {"items": [dict(item) for item in self.theme_items], "meta": {}}

    async def channels(self) -> dict[str, Any]:
        self._record("channels")
        return {"items": [{"code": "default", "name": "Varsayılan"}]}

    async def locales(self) -> dict[str, Any]:
        self._record("locales")
        return {"items": [{"code": "tr", "name": "Türkçe"}]}

    async def category_tree(self) -> dict[str, Any]:
        self._record("category_tree")
        return {"items": [{"id": 1, "name": "Kitap", "slug": "kitap",
                           "children": [{"id": 2, "name": "9. Sınıf", "slug": "9-sinif"}]}]}

    async def cms_pages(self, filters: Any = None, *, page: int = 1,
                        per_page: int | None = None) -> dict[str, Any]:
        self._record("cms_pages", filters, page=page, per_page=per_page)
        return {"items": [{"id": 3, "page_title": "Mesafeli Satış", "url_key": "mesafeli-satis"}]}

    async def product_lookup(self, filters: Any = None, *,
                             per_page: int | None = None) -> dict[str, Any]:
        self._record("product_lookup", filters, per_page=per_page)
        return {"items": [dict(item) for item in self.lookup_items], "meta": {}}


# ------------------------------------------------------------------ görsel

def png_bytes(width: int, height: int) -> bytes:
    """Gerçek bir PNG başlığı üretir — testler `image_dimensions`'ı sahtelemez.

    Görsel içeriği önemsiz; ölçüyü BAŞLIKTAN okuduğumuz için IHDR yeterlidir.
    """
    header = struct.pack(">II", width, height) + bytes([8, 6, 0, 0, 0])
    chunk = b"IHDR" + header
    ihdr = struct.pack(">I", len(header)) + chunk + struct.pack(">I", zlib.crc32(chunk))
    return b"\x89PNG\r\n\x1a\n" + ihdr + b"\x00\x00\x00\x00IEND\xaeB`\x82"


def png_data_url(width: int, height: int) -> str:
    return "data:image/png;base64," + base64.b64encode(png_bytes(width, height)).decode("ascii")


def jpeg_bytes(width: int, height: int) -> bytes:
    """SOF0 çerçevesi taşıyan asgari JPEG."""
    sof = b"\xff\xc0" + struct.pack(">H", 17) + bytes([8]) + struct.pack(">HH", height, width)
    return b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 4) + b"\x00\x00" + sof + b"\xff\xd9"
