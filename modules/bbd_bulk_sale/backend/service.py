"""Toplu Satış — iş kuralları.

İKİ KİP:

- **Aynı sepet herkese** (`shared`): seçili tüm öğrencilere aynı kalemler.
- **Öğrenci başına ayrı sepet** (`per_student`): her öğrenci için ayrı sepet
  kurulup kuyruğa alınır, sonunda hepsi tek seferde işlenir. Şartnamedeki
  "öğrencinin profiline girip ürünleri toplu ekleme" akışı budur.

Öğle Yemeği modülüyle aynı üç güvence geçerlidir: deterministik `local_id` ile
çift borç imkânsız, gönderimden önce ön izleme, geri alma silme değil ters kayıt.
Kod ortak değildir çünkü modül modülü import etmez (K3) — her modül kendi
dikey dilimidir.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

#: `local_id` üretiminin sabit ad alanı. DEĞİŞTİRİLEMEZ (idempotency zinciri).
BULK_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://kontrol-merkezi/bbd_bulk_sale")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _epoch_ms(day: str, hour: int = 12) -> int:
    parsed = datetime.fromisoformat(day).replace(hour=hour, minute=30, second=0, microsecond=0)
    return int(parsed.timestamp() * 1000)


def _day_bounds_ms(day: str) -> tuple[int, int]:
    start = datetime.fromisoformat(day)
    return int(start.timestamp() * 1000), int((start + timedelta(days=1)).timestamp() * 1000)


class BulkSaleService:
    def __init__(self, *, canteen: Any, store: Any, log: Any, config: dict[str, Any]) -> None:
        self._canteen = canteen
        self._store = store
        self._log = log
        self._config = config
        self._batches = store.table("batch")
        self._entries = store.table("entry")
        self._presets = store.table("preset")

    # ------------------------------------------------------------- okuma

    async def workspace(self) -> dict[str, Any]:
        """Ekranın açılış verisi: öğrenciler, ürünler, şablonlar, son partiler."""
        try:
            snapshot = await self._canteen.snapshot()
            errors = snapshot.get("errors") or []
            students, products = snapshot["students"], snapshot["products"]
            connected, error = not errors, "; ".join(errors)
        except Exception as failure:  # noqa: BLE001 — kantin dışarısı; ekran ayakta kalmalı
            students, products = [], []
            connected, error = False, str(failure)
            self._log.warning("kantin okunamadı", error=error)

        presets = await self._store.fetch_all(
            f"SELECT id, name, cart_json, created_at, created_by FROM {self._presets} "
            f"ORDER BY name"
        )
        batches = await self._store.fetch_all(
            f"SELECT * FROM {self._batches} ORDER BY created_at DESC LIMIT 30"
        )

        return {
            "connected": connected,
            "error": error,
            "students": [self._student_view(item) for item in students],
            # Satışa yalnız aktif ürün çıkar; pasifi listelemek yanıltıcı olur.
            "products": [item for item in products if item.get("isActive")],
            "presets": [
                {"id": row["id"], "name": row["name"],
                 "cart": json.loads(row["cart_json"] or "[]"),
                 "createdBy": row["created_by"]}
                for row in presets
            ],
            "batches": [dict(row) for row in batches],
        }

    async def batch_detail(self, batch_ref: str) -> dict[str, Any]:
        batch = await self._store.fetch_one(
            f"SELECT * FROM {self._batches} WHERE batch_ref = ?", (batch_ref,)
        )
        if batch is None:
            return {"ok": False, "error": "Parti bulunamadı."}
        entries = await self._store.fetch_all(
            f"SELECT * FROM {self._entries} WHERE batch_ref = ? ORDER BY rowid", (batch_ref,)
        )
        return {
            "ok": True,
            "batch": dict(batch),
            "entries": [
                {**dict(entry), "items": json.loads(entry["items_json"] or "[]")}
                for entry in entries
            ],
        }

    # ---------------------------------------------------------- ön izleme

    async def preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Gönderim yapmadan ne olacağını söyler: engel, limit, stok, tutar."""
        sale_date = str(payload["date"])
        orders = self._orders(payload)
        if not orders:
            return {"ok": False, "error": "İşlenecek öğrenci/sepet yok.", "rows": [], "summary": {}}

        try:
            snapshot = await self._canteen.snapshot()
            students = {str(item.get("id")): item for item in snapshot["students"]}
            products = {int(item["id"]): item for item in snapshot["products"]}
            error = "; ".join(snapshot.get("errors") or [])
        except Exception as failure:  # noqa: BLE001
            return {"ok": False, "error": str(failure), "rows": [], "summary": {}}

        # Stok toplamı ÖĞRENCİ BAŞINA DEĞİL, tüm parti için hesaplanır: 40 öğrenciye
        # birer su satılacaksa kantinde 40 su olmalı.
        need: dict[int, int] = {}
        rows: list[dict[str, Any]] = []
        eligible = 0
        total_amount = 0

        for kantin_id, items in orders.items():
            student = students.get(kantin_id)
            if student is None:
                rows.append(self._row(kantin_id, "", "missing", "Öğrenci kantinde bulunamadı."))
                continue

            name = str(student.get("displayName") or "")
            lines, amount, bad = [], 0, None
            for item in items:
                product = products.get(int(item["productId"]))
                if product is None:
                    bad = f"Ürün bulunamadı (#{item['productId']})."
                    break
                if not product.get("isActive"):
                    bad = f"“{product['name']}” pasif; kasada satışa çıkmıyor."
                    break
                qty = int(item["qty"])
                unit = int(item.get("unitPrice") or product.get("price") or 0)
                if unit < 1 or qty < 1:
                    bad = f"“{product['name']}” için adet/fiyat geçersiz."
                    break
                lines.append({"productId": product["id"], "name": product["name"],
                              "qty": qty, "unitPrice": unit, "lineTotal": unit * qty})
                amount += unit * qty
                need[int(product["id"])] = need.get(int(product["id"]), 0) + qty

            if bad:
                rows.append(self._row(kantin_id, name, "invalid", bad))
                continue

            balance = int(student.get("balance") or 0)
            limit = student.get("spendingLimit")

            if student.get("isBlocked"):
                rows.append(self._row(kantin_id, name, "blocked", "Öğrenci engelli; satış açılmaz."))
            elif limit is not None and balance + amount > int(limit):
                rows.append(self._row(
                    kantin_id, name, "limit",
                    f"Harcama limiti aşılıyor ({(balance + amount) / 100:.2f} ₺ > {int(limit) / 100:.2f} ₺).",
                    amount=amount, balance=balance, lines=lines))
            else:
                rows.append(self._row(kantin_id, name, "ok", "",
                                      amount=amount, balance=balance, lines=lines))
                eligible += 1
                total_amount += amount

        # Uygun olmayan satırların stok ihtiyacını düşür — gönderilmeyecekler.
        for row in rows:
            if row["verdict"] in ("ok",):
                continue
            for line in row.get("lines") or []:
                key = int(line["productId"])
                need[key] = max(0, need.get(key, 0) - int(line["qty"]))

        stock_rows = []
        for product_id, quantity in sorted(need.items()):
            product = products.get(product_id)
            if product is None or quantity == 0:
                continue
            have = int(product.get("stock") or 0)
            stock_rows.append({
                "productId": product_id, "name": product["name"],
                "need": quantity, "have": have, "short": max(0, quantity - have),
            })

        return {
            "ok": True,
            "error": error,
            "rows": rows,
            "stock": stock_rows,
            "summary": {
                "selected": len(orders),
                "eligible": eligible,
                "blocked": sum(1 for row in rows if row["verdict"] == "blocked"),
                "limit": sum(1 for row in rows if row["verdict"] == "limit"),
                "invalid": sum(1 for row in rows if row["verdict"] in ("invalid", "missing")),
                "totalAmount": total_amount,
                "date": sale_date,
                "stockShort": sum(row["short"] for row in stock_rows),
            },
        }

    @staticmethod
    def _row(kantin_id: str, name: str, verdict: str, message: str, *, amount: int = 0,
             balance: int = 0, lines: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return {"kantinId": kantin_id, "name": name, "verdict": verdict, "message": message,
                "amount": amount, "balance": balance, "lines": lines or []}

    @staticmethod
    def _orders(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        """İki kipi tek biçime indirir: {kantinId: [kalem, …]}."""
        if payload.get("mode") == "per_student":
            return {
                str(order["kantinId"]): list(order.get("items") or [])
                for order in payload.get("orders") or []
                if order.get("items")
            }
        cart = list(payload.get("cart") or [])
        if not cart:
            return {}
        return {str(kantin_id): cart for kantin_id in payload.get("students") or []}

    # -------------------------------------------------------------- yazma

    async def commit(self, payload: dict[str, Any], *, actor: str) -> dict[str, Any]:
        preview = await self.preview(payload)
        if not preview["ok"]:
            return preview
        if payload.get("dryRun"):
            return {**preview, "dryRun": True}

        targets = [row for row in preview["rows"] if row["verdict"] == "ok"]
        if not targets:
            return {**preview, "committed": False, "error": "İşlenecek uygun öğrenci yok."}

        sale_date = str(payload["date"])
        mode = str(payload.get("mode") or "shared")
        batch_ref = f"B{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"

        await self._store.execute(
            f"INSERT INTO {self._batches} (batch_ref, sale_date, mode, cart_json, note, "
            f"created_at, created_by, total_count, total_amount) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (batch_ref, sale_date, mode,
             json.dumps(payload.get("cart") or [], ensure_ascii=False),
             str(payload.get("note") or ""), _now(), actor, len(targets),
             int(preview["summary"]["totalAmount"])),
        )

        sales: list[dict[str, Any]] = []
        for row in targets:
            kantin_id = row["kantinId"]
            seq = await self._next_seq(sale_date, kantin_id)
            # Sepet içeriği de anahtara girer: aynı gün aynı öğrenciye FARKLI bir
            # sepet gönderilebilsin, aynısı iki kez gönderilirse duplicate olsun.
            fingerprint = json.dumps(
                sorted((int(line["productId"]), int(line["qty"]), int(line["unitPrice"]))
                       for line in row["lines"]),
                separators=(",", ":"),
            )
            local_id = str(uuid.uuid5(
                BULK_NAMESPACE, f"{sale_date}:{kantin_id}:{seq}:{fingerprint}"
            ))

            await self._store.execute(
                f"INSERT INTO {self._entries} (local_id, batch_ref, sale_date, kantin_id, "
                f"student_name, items_json, amount, seq, status, created_at) "
                f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                (local_id, batch_ref, sale_date, kantin_id, row["name"],
                 json.dumps(row["lines"], ensure_ascii=False), int(row["amount"]), seq, _now()),
            )
            sales.append({
                "localId": local_id,
                "studentId": kantin_id,
                "createdAt": _epoch_ms(sale_date, int(self._config.get("sale_hour") or 12)),
                "items": [{"productId": int(line["productId"]), "qty": int(line["qty"]),
                           "unitPrice": int(line["unitPrice"])} for line in row["lines"]],
            })

        results = await self._canteen.sell_batch(sales)

        ok = failed = 0
        for result in results:
            status = str(result.get("status") or "failed")
            if status in ("created", "duplicate"):
                ok += 1
            else:
                failed += 1
            await self._store.execute(
                f"UPDATE {self._entries} SET status = ?, reason = ?, server_id = ? "
                f"WHERE local_id = ?",
                (status, str(result.get("reason") or ""), result.get("serverId"),
                 str(result.get("localId") or "")),
            )

        await self._store.execute(
            f"UPDATE {self._batches} SET ok_count = ?, fail_count = ? WHERE batch_ref = ?",
            (ok, failed, batch_ref),
        )
        self._log.info("toplu satış işlendi", batch=batch_ref, ok=ok, failed=failed)

        detail = await self.batch_detail(batch_ref)
        return {"ok": True, "committed": True, "batchRef": batch_ref,
                "okCount": ok, "failCount": failed,
                "entries": detail.get("entries", []), "summary": preview["summary"]}

    async def _next_seq(self, sale_date: str, kantin_id: str) -> int:
        row = await self._store.fetch_one(
            f"SELECT COUNT(*) AS n FROM {self._entries} WHERE sale_date = ? AND kantin_id = ?",
            (sale_date, kantin_id),
        )
        return int((row or {}).get("n") or 0)

    # ----------------------------------------------------------- geri alma

    async def reverse(self, *, batch_ref: str | None = None, local_id: str | None = None,
                      reason: str) -> dict[str, Any]:
        """SİLMEZ — kantinde ters cari kayıt + stok iadesi oluşturur."""
        if local_id:
            rows = await self._store.fetch_all(
                f"SELECT * FROM {self._entries} WHERE local_id = ?", (local_id,))
        elif batch_ref:
            rows = await self._store.fetch_all(
                f"SELECT * FROM {self._entries} WHERE batch_ref = ? AND reversed_at IS NULL "
                f"AND status IN ('created','duplicate')", (batch_ref,))
        else:
            return {"ok": False, "error": "Geri alınacak parti ya da satır belirtilmedi."}

        done, failures = 0, []
        for row in rows:
            entry_id = str(row["local_id"])
            if row["reversed_at"]:
                continue
            try:
                await self._canteen.reverse_sale(entry_id, reason)
            except Exception as failure:  # noqa: BLE001 — biri patlarsa diğerleri sürsün
                self._log.warning("satır geri alınamadı", localId=entry_id, error=str(failure))
                failures.append({"localId": entry_id, "error": str(failure)})
                continue
            await self._store.execute(
                f"UPDATE {self._entries} SET reversed_at = ?, reversed_reason = ? "
                f"WHERE local_id = ?", (_now(), reason, entry_id))
            done += 1

        return {"ok": True, "reversed": done, "failures": failures}

    # -------------------------------------------------------------- şablon

    async def save_preset(self, name: str, cart: list[dict[str, Any]], *,
                          actor: str) -> dict[str, Any]:
        if not cart:
            return {"ok": False, "error": "Boş sepet şablon olarak kaydedilemez."}
        await self._store.execute(
            f"INSERT INTO {self._presets} (name, cart_json, created_at, created_by) "
            f"VALUES (?, ?, ?, ?) "
            f"ON CONFLICT(name) DO UPDATE SET cart_json = excluded.cart_json, "
            f"created_at = excluded.created_at, created_by = excluded.created_by",
            (name.strip(), json.dumps(cart, ensure_ascii=False), _now(), actor),
        )
        return {"ok": True, "name": name.strip()}

    async def delete_preset(self, preset_id: int) -> dict[str, Any]:
        # Şablon kullanıcı kısayoludur, iş verisi değil; silinmesi veri kaybı sayılmaz.
        await self._store.execute(f"DELETE FROM {self._presets} WHERE id = ?", (int(preset_id),))
        return {"ok": True}

    # ------------------------------------------------------------ yardımcı

    @staticmethod
    def _student_view(student: dict[str, Any]) -> dict[str, Any]:
        return {
            "kantinId": student.get("id"),
            "displayName": student.get("displayName"),
            "balance": int(student.get("balance") or 0),
            "spendingLimit": student.get("spendingLimit"),
            "isBlocked": bool(student.get("isBlocked")),
        }
