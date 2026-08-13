"""Kantin Ürünleri — iş kuralları.

KANTİN OTORİTEDİR. Ürün listesi her açılışta kantinden okunur; burada kopyası
tutulmaz. Yazma da kantinin kendi `POST /api/products` ucundan geçer — kiosk
ne görüyorsa aynısı, ayrı bir yol yok.

SİLME YOKTUR. "Ürünü sil" demek `isActive=false` demektir: kasada satışa
çıkmaz ama satır, geçmiş satışlardaki bağı ve raporları yerinde kalır.
Kantin API'sinde zaten silme ucu yoktur; bu kısıt bilinçlidir.

DEĞİŞİKLİK GÜNLÜĞÜ. Kantin "kim neyi değiştirdi" izini tutmaz. Her yazma
öncesi/sonrası hâliyle kendi tablomuza düşülür; yanlış fiyat girildiğinde
eski değere dönmenin tek yolu budur.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from datetime import UTC, datetime
from typing import Any

#: Kantinin kabul ettiği görsel türleri (`ProductController::attachImage`).
IMAGE_MIME = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "png": "image/png", "webp": "image/webp",
}

#: Kantin 24 MB'a kadar kabul ediyor; base64 şişmesini de hesaba katıyoruz.
MAX_IMAGE_BYTES = 24 * 1024 * 1024


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class ProductService:
    def __init__(self, *, canteen: Any, store: Any, log: Any) -> None:
        self._canteen = canteen
        self._store = store
        self._log = log
        self._audit = store.table("audit")

    # ------------------------------------------------------------- okuma

    async def list_products(self) -> dict[str, Any]:
        """Ürünler + sağlık denetimi. Kantin düşse de ekran açılır (K7)."""
        try:
            products = await self._canteen.products()
            connected, error = True, ""
        except Exception as failure:  # noqa: BLE001 — kantin dışarısı
            products, connected, error = [], False, str(failure)
            self._log.warning("ürünler okunamadı", error=error)

        return {
            "connected": connected,
            "error": error,
            "products": products,
            "summary": self._summary(products),
            "health": self._health(products),
        }

    @staticmethod
    def _summary(products: list[dict[str, Any]]) -> dict[str, Any]:
        active = [p for p in products if p.get("isActive")]
        stock_value = sum(int(p.get("price") or 0) * int(p.get("stock") or 0) for p in active)
        return {
            "total": len(products),
            "active": len(active),
            "passive": len(products) - len(active),
            "outOfStock": sum(1 for p in active if int(p.get("stock") or 0) <= 0),
            "stockUnits": sum(int(p.get("stock") or 0) for p in active),
            "stockValue": stock_value,
        }

    @staticmethod
    def _health(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Düzeltilmeyi bekleyen durumlar. Her biri ekranda tıklanabilir bir listedir."""
        def ids(predicate) -> list[int]:
            return [int(p["id"]) for p in products if predicate(p)]

        return [
            {
                "key": "no_barcode",
                "label": "Barkodsuz ürün",
                "hint": "Kasada barkodla okutulamaz; elden seçilmesi gerekir.",
                "products": ids(lambda p: not p.get("barcode") and p.get("isActive")),
            },
            {
                "key": "no_image",
                "label": "Görselsiz ürün",
                "hint": "Kasa ekranında yalnız adıyla görünür.",
                "products": ids(lambda p: not p.get("imageUrl") and p.get("isActive")),
            },
            {
                "key": "out_of_stock",
                "label": "Stoğu biten aktif ürün",
                "hint": "Kantin yetersiz stokta satışı reddeder.",
                "products": ids(lambda p: p.get("isActive") and int(p.get("stock") or 0) <= 0),
            },
            {
                "key": "passive_with_stock",
                "label": "Pasif ama stoklu ürün",
                "hint": "Rafta duruyor ama kasada satışa çıkmıyor.",
                "products": ids(lambda p: not p.get("isActive") and int(p.get("stock") or 0) > 0),
            },
        ]

    async def audit_log(self, *, product_id: int | None = None,
                        limit: int = 200) -> list[dict[str, Any]]:
        if product_id is not None:
            rows = await self._store.fetch_all(
                f"SELECT * FROM {self._audit} WHERE product_id = ? ORDER BY id DESC LIMIT ?",
                (int(product_id), int(limit)),
            )
        else:
            rows = await self._store.fetch_all(
                f"SELECT * FROM {self._audit} ORDER BY id DESC LIMIT ?", (int(limit),)
            )
        return [dict(row) for row in rows]

    # -------------------------------------------------------------- yazma

    async def save(self, payload: dict[str, Any], *, actor: str) -> dict[str, Any]:
        """Ürünü ekler/günceller.

        Kantin ucu PATCH değil UPSERT'tür: `name`, `price` ve `stock` her istekte
        zorunludur. Bu yüzden kısmi düzenlemede eksik alanlar MEVCUT üründen
        tamamlanır — kullanıcının dokunmadığı alan sıfırlanmaz.
        """
        product_id = payload.get("id")
        before: dict[str, Any] = {}
        if product_id:
            before = await self._find(int(product_id)) or {}

        fields: dict[str, Any] = {
            "name": str(payload.get("name") or before.get("name") or "").strip(),
            "price": int(payload["price"]) if payload.get("price") is not None
            else int(before.get("price") or 0),
            "stock": int(payload["stock"]) if payload.get("stock") is not None
            else int(before.get("stock") or 0),
        }
        if product_id:
            fields["id"] = int(product_id)

        barcode = payload.get("barcode", before.get("barcode"))
        barcode = (str(barcode).strip() or None) if barcode is not None else None
        if barcode:
            fields["barcode"] = barcode

        is_active = payload.get("isActive")
        fields["isActive"] = bool(before.get("isActive", True)) if is_active is None else bool(is_active)

        if not fields["name"]:
            return {"ok": False, "error": "Ürün adı boş olamaz."}
        if fields["price"] < 1:
            return {"ok": False, "error": "Fiyat en az 1 kuruş olmalı."}
        if fields["stock"] < 0:
            return {"ok": False, "error": "Stok negatif olamaz."}

        image = self._decode_image(payload)
        if isinstance(image, str):          # hata mesajı döndü
            return {"ok": False, "error": image}

        try:
            product = await self._canteen.upsert_product(fields, image=image)
        except Exception as failure:  # noqa: BLE001 — kantin dışarısı
            self._log.warning("ürün yazılamadı", error=str(failure))
            return {"ok": False, "error": str(failure)}

        await self._record(
            action="update" if product_id else "create",
            before=before, after=product, actor=actor,
            note=str(payload.get("note") or ""),
        )
        return {"ok": True, "product": product, "imageStatus": product.get("imageStatus")}

    @staticmethod
    def _decode_image(payload: dict[str, Any]) -> tuple[str, bytes, str] | None | str:
        """base64 görseli çözer. Hata durumunda mesaj dizgisi döner."""
        raw = payload.get("imageBase64")
        if not raw:
            return None

        name = str(payload.get("imageName") or "urun.jpg")
        extension = name.rsplit(".", 1)[-1].lower() if "." in name else "jpg"
        mime = IMAGE_MIME.get(extension)
        if mime is None:
            return "Görsel biçimi desteklenmiyor. JPG, PNG veya WEBP olmalı."

        # "data:image/jpeg;base64,...." biçimi de kabul edilir.
        if "," in raw and raw.strip().startswith("data:"):
            raw = raw.split(",", 1)[1]

        try:
            content = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError):
            return "Görsel çözülemedi (geçersiz base64)."

        if len(content) > MAX_IMAGE_BYTES:
            return "Görsel çok büyük (en çok 24 MB)."
        if not content:
            return "Görsel boş."

        return (name, content, mime)

    async def set_active(self, product_id: int, active: bool, *,
                         actor: str, note: str = "") -> dict[str, Any]:
        """Pasifleştirme/geri açma — SİLME DEĞİL.

        Kantinde silme ucu yoktur; pasif ürün kasada satışa çıkmaz ama satırı,
        geçmiş satışlardaki bağı ve raporlardaki payı olduğu gibi kalır.
        """
        before = await self._find(product_id)
        if before is None:
            return {"ok": False, "error": "Ürün bulunamadı."}

        try:
            product = await self._canteen.upsert_product({
                "id": product_id,
                "name": before["name"],
                "price": int(before["price"]),
                "stock": int(before["stock"]),
                "barcode": before.get("barcode"),
                "isActive": active,
            } if before.get("barcode") else {
                "id": product_id,
                "name": before["name"],
                "price": int(before["price"]),
                "stock": int(before["stock"]),
                "isActive": active,
            })
        except Exception as failure:  # noqa: BLE001
            return {"ok": False, "error": str(failure)}

        await self._record(action="activate" if active else "deactivate",
                           before=before, after=product, actor=actor, note=note)
        return {"ok": True, "product": product}

    async def adjust_stock(self, product_id: int, delta: int, reason: str, *,
                           actor: str) -> dict[str, Any]:
        """Stok girişi/çıkışı. Kantin `local_id` ile idempotent tutar."""
        before = await self._find(product_id)
        if before is None:
            return {"ok": False, "error": "Ürün bulunamadı."}
        if delta == 0:
            return {"ok": False, "error": "Değişim sıfır olamaz."}

        try:
            result = await self._canteen.adjust_stock(
                local_id=str(uuid.uuid4()), product_id=product_id,
                delta=delta, reason=reason or "Kontrol Merkezi stok düzeltme",
            )
        except Exception as failure:  # noqa: BLE001
            return {"ok": False, "error": str(failure)}

        after = {**before, "stock": result.get("newStock")}
        await self._record(action="stock", before=before, after=after,
                           actor=actor, note=f"{delta:+d} · {reason}")
        return {"ok": True, "newStock": result.get("newStock"), "delta": delta}

    async def bulk_price(self, product_ids: list[int], *, percent: float | None = None,
                         amount: int | None = None, actor: str,
                         dry_run: bool = True) -> dict[str, Any]:
        """Toplu fiyat güncelleme — yüzde ya da sabit kuruş.

        Kuru prova varsayılan AÇIK: ne olacağını görmeden fiyat değişmez.
        Sonuç 1 kuruşun altına düşerse o ürün atlanır (kantin `min:1` ister).
        """
        if percent is None and amount is None:
            return {"ok": False, "error": "Yüzde ya da tutar verilmeli."}

        products = {int(p["id"]): p for p in await self._canteen.products()}
        rows, applied, skipped = [], 0, 0

        for product_id in product_ids:
            product = products.get(int(product_id))
            if product is None:
                continue
            old = int(product.get("price") or 0)
            new = round(old * (1 + percent / 100)) if percent is not None else old + int(amount)
            new = int(new)

            if new < 1:
                rows.append({"id": product_id, "name": product["name"], "old": old,
                             "new": new, "status": "skipped",
                             "reason": "Fiyat 1 kuruşun altına düşüyor."})
                skipped += 1
                continue

            row = {"id": product_id, "name": product["name"], "old": old, "new": new,
                   "status": "preview"}
            if not dry_run:
                try:
                    updated = await self._canteen.upsert_product({
                        "id": product_id, "name": product["name"], "price": new,
                        "stock": int(product.get("stock") or 0),
                        "isActive": bool(product.get("isActive")),
                        **({"barcode": product["barcode"]} if product.get("barcode") else {}),
                    })
                    await self._record(action="price_bulk", before=product, after=updated,
                                       actor=actor,
                                       note=f"{old / 100:.2f} → {new / 100:.2f} ₺")
                    row["status"] = "applied"
                    applied += 1
                except Exception as failure:  # noqa: BLE001 — biri patlarsa diğerleri sürsün
                    row["status"] = "failed"
                    row["reason"] = str(failure)
                    skipped += 1
            rows.append(row)

        return {"ok": True, "dryRun": dry_run, "rows": rows,
                "applied": applied, "skipped": skipped}

    # ---------------------------------------------------------- yardımcı

    async def _find(self, product_id: int) -> dict[str, Any] | None:
        for product in await self._canteen.products():
            if int(product.get("id") or 0) == int(product_id):
                return product
        return None

    async def _record(self, *, action: str, before: dict[str, Any], after: dict[str, Any],
                      actor: str, note: str = "") -> None:
        await self._store.execute(
            f"INSERT INTO {self._audit} (product_id, barcode, name, action, "
            f"before_json, after_json, note, actor, created_at) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                after.get("id") or before.get("id"),
                str(after.get("barcode") or before.get("barcode") or ""),
                str(after.get("name") or before.get("name") or ""),
                action,
                json.dumps(self._trim(before), ensure_ascii=False),
                json.dumps(self._trim(after), ensure_ascii=False),
                note, actor, _now(),
            ),
        )

    @staticmethod
    def _trim(product: dict[str, Any]) -> dict[str, Any]:
        """Günlüğe yalnız anlamlı alanlar yazılır — `updatedAt` gürültüsü değil."""
        keys = ("id", "barcode", "name", "price", "stock", "isActive", "imageUrl")
        return {key: product[key] for key in keys if key in product}
