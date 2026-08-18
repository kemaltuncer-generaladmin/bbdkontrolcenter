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
            return list(reversed(self.audit))
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
    """`store.api` yeteneğinin testlik yüzü. Yalnız kullanılan metotlar var.

    ALAN ADLARI MAĞAZANIN GERÇEK YANITINDAN alınmıştır: BBD ucu camelCase
    döndürüyor (`imageUrl`, `themeId`). Taklit snake_case döndürseydi, `pick`
    içindeki camelCase düzeltmesinin gerçekten çalıştığını hiçbir test göremezdi.
    """

    def __init__(self, slides: list[dict[str, Any]] | None = None) -> None:
        self.slide_items = slides if slides is not None else []
        self.lookup_items: list[dict[str, Any]] = []
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()
        #: `fail` yerine bu kullanılırsa metot verilen istisnayı fırlatır —
        #: geçidin kodlu hatalarını (`bbd_endpoint_missing`) taklit etmek için.
        self.raises: dict[str, Exception] = {}
        #: Yükleme ucunun döndürdüğü yol. Mağaza SERBEST YOL KABUL ETMİYOR:
        #: liste yazması yalnız bu klasördeki dosyayı taşıyabilir.
        self.upload_path = "storage/theme/1/sliders/yeni.webp"

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        if name in self.raises:
            raise self.raises[name]
        if name in self.fail:
            raise RuntimeError(f"{name} patladı")
        self.calls.append((name, args, kwargs))

    def used(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for called, _, kwargs in self.calls if called == name]

    async def bbd_home_slides(self, filters: Any = None) -> dict[str, Any]:
        self._record("bbd_home_slides", filters)
        return {"items": [dict(item) for item in self.slide_items],
                "meta": {"themeId": 1, "total": len(self.slide_items)}}

    async def bbd_save_home_slides(self, *, slides: list[dict[str, Any]], reason: str,
                                   actor: str = "",
                                   dry_run: bool | None = None) -> dict[str, Any]:
        self._record("bbd_save_home_slides", slides=slides, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def upload_media(self, *, content: Any, filename: str, mime: str = "", reason: str,
                           actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        """Geçidin `upload_media` imzası —
        POST /api/admin/bbd/storefront/home-slides/image.

        `slot`/`position` ALANLARI YOK: uç tek bir şeride hizmet ediyor ve sıra,
        liste yazmasındaki dizinin kendi sırası. Taklit bunları kabul etseydi,
        onları hâlâ gönderen bir servis testte yakalanmazdı.
        """
        self._record("upload_media", content=content, filename=filename, mime=mime,
                     reason=reason, actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run),
                "image": self.upload_path,
                "url": f"https://bbdstore.com.tr/{self.upload_path}"}

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
