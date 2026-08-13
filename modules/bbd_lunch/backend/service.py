"""Öğle Yemeği — iş kuralları.

TEMEL FİKİR: kasada tek tek QR okutularak girilen yemeği, bilgisayar başında
gün seçip toplu işlemek. Yazma kantinin KENDİ satış ucundan geçer; sonuç
kasada elle girilmişten ayırt edilemez — aynı `transactions` satırı, aynı
cari defter borcu, aynı stok düşümü.

ÜÇ GÜVENCE:

1. **Çift borç imkânsız.** Her satırın `local_id`'si (gün + öğrenci + deneme
   sırası) üzerinden DETERMİNİSTİK üretilir ve GÖNDERİMDEN ÖNCE kendi
   tablomuza yazılır. Ağ koparsa aynı id ile tekrar gönderilir; kantin
   `duplicate` der ve ikinci kez borçlandırmaz.
2. **Kör gönderim yok.** `preview()` her öğrenci için engel/limit/stok ve
   "bugün zaten girilmiş mi" sorularını gönderimden ÖNCE yanıtlar.
3. **Geri alma silme değildir.** Kantindeki iptal ucu ters cari kayıt yazar,
   stoğu iade eder, satırı damgalar. Satır ne kantinde ne burada silinir.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

#: `local_id` üretiminde kullanılan sabit ad alanı. DEĞİŞTİRİLEMEZ — değişirse
#: geçmiş kayıtların idempotency zinciri kopar ve çift borç riski doğar.
LUNCH_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://kontrol-merkezi/bbd_lunch")

#: Kantinin tek satışta kabul ettiği en çok kalem adedi.
MAX_PORTION = 1000


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _epoch_ms(day: str, hour: int = 12, minute: int = 0) -> int:
    """Gün + saat → epoch-ms. Kantin `createdAt` alanını cihaz saati sayar.

    Yemek saati varsayılan 12:00'dir: kayıt gün içinde makul bir ana düşsün,
    gece yarısına yapışıp raporlarda komşu güne taşmasın.
    """
    parsed = datetime.fromisoformat(day).replace(hour=hour, minute=minute, second=0, microsecond=0)
    # Kantin epoch-ms'i app timezone'unda çözer; biz de yerel saat olarak veriyoruz.
    return int(parsed.timestamp() * 1000)


def _day_bounds_ms(day: str) -> tuple[int, int]:
    """Günün [00:00, ertesi 00:00) sınırları — kantin `to` değerini hariç tutar."""
    start = datetime.fromisoformat(day)
    return int(start.timestamp() * 1000), int((start + timedelta(days=1)).timestamp() * 1000)


class LunchService:
    def __init__(self, *, canteen: Any, store: Any, log: Any, config: dict[str, Any]) -> None:
        self._canteen = canteen
        self._store = store
        self._log = log
        self._config = config
        self._batches = store.table("batch")
        self._entries = store.table("entry")
        self._roster = store.table("roster")
        self._holidays = store.table("holiday")

    # ------------------------------------------------------------------ ürün

    async def _lunch_product(self, products: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Yemek ürününü bulur.

        Önce ayarda sabitlenmiş `product_id`, yoksa adı `product_name_hint`
        ile eşleşen ilk ürün. Ad eşleşmesi Türkçe büyük/küçük harfe duyarsızdır.
        """
        fixed = self._config.get("product_id")
        if fixed:
            for product in products:
                if int(product.get("id") or 0) == int(fixed):
                    return product

        hint = str(self._config.get("product_name_hint") or "Öğle Yemeği")
        folded = hint.casefold()
        for product in products:
            if str(product.get("name") or "").casefold() == folded:
                return product
        for product in products:
            if folded in str(product.get("name") or "").casefold():
                return product
        return None

    # ------------------------------------------------------------- okuma

    async def overview(self, month: str) -> dict[str, Any]:
        """Takvim ekranının açılış verisi: ay özeti + ürün + öğrenciler + sabit liste.

        `month` = YYYY-MM. Kantin okunamazsa ekran yine açılır (K7); ne olduğu
        `connected`/`error` alanlarında bildirilir.
        """
        snapshot = await self._safe_snapshot()

        product = await self._lunch_product(snapshot["products"])
        first = f"{month}-01"
        last_day = (datetime.fromisoformat(first).replace(day=28) + timedelta(days=4))
        last = (last_day - timedelta(days=last_day.day)).date().isoformat()

        rows = await self._store.fetch_all(
            f"SELECT service_date, "
            f"       COUNT(*) AS total, "
            f"       SUM(CASE WHEN status IN ('created','duplicate') AND reversed_at IS NULL "
            f"                THEN 1 ELSE 0 END) AS ok, "
            f"       SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed, "
            f"       SUM(CASE WHEN reversed_at IS NOT NULL THEN 1 ELSE 0 END) AS reversed "
            f"FROM {self._entries} WHERE service_date BETWEEN ? AND ? "
            f"GROUP BY service_date",
            (first, last),
        )
        days = {
            str(row["service_date"]): {
                "total": int(row["total"] or 0),
                "ok": int(row["ok"] or 0),
                "failed": int(row["failed"] or 0),
                "reversed": int(row["reversed"] or 0),
            }
            for row in rows
        }

        holidays = await self._store.fetch_all(
            f"SELECT day, label FROM {self._holidays} WHERE day BETWEEN ? AND ?", (first, last)
        )
        roster = await self._store.fetch_all(f"SELECT kantin_id FROM {self._roster}")

        return {
            "connected": snapshot["connected"],
            "error": snapshot["error"],
            "month": month,
            "days": days,
            "holidays": {str(row["day"]): str(row["label"] or "") for row in holidays},
            "roster": [str(row["kantin_id"]) for row in roster],
            "product": self._product_view(product),
            "students": [self._student_view(student) for student in snapshot["students"]],
            "portion": int(self._config.get("default_portion") or 1),
        }

    async def day(self, service_date: str) -> dict[str, Any]:
        """Seçili günün ayrıntısı: partiler, satırlar ve kasadan girilmiş yemekler."""
        batches = await self._store.fetch_all(
            f"SELECT * FROM {self._batches} WHERE service_date = ? ORDER BY created_at DESC",
            (service_date,),
        )
        entries = await self._store.fetch_all(
            f"SELECT * FROM {self._entries} WHERE service_date = ? ORDER BY rowid",
            (service_date,),
        )

        by_batch: dict[str, list[dict[str, Any]]] = {}
        for entry in entries:
            by_batch.setdefault(str(entry["batch_ref"]), []).append(dict(entry))

        return {
            "date": service_date,
            "batches": [
                {**dict(batch), "entries": by_batch.get(str(batch["batch_ref"]), [])}
                for batch in batches
            ],
            "recorded": await self._recorded_today(service_date),
        }

    async def _recorded_today(self, service_date: str) -> dict[str, int]:
        """O gün kantinde yemek ürünü satılmış öğrenciler — KAYNAK KANTİN.

        Yalnız kendi tablomuza bakmak yetmez: yemek kasadan da girilmiş olabilir.
        Çift kayıt uyarısının doğru çalışması buna bağlıdır.
        """
        try:
            products = await self._canteen.products()
            product = await self._lunch_product(products)
            if product is None:
                return {}
            start, end = _day_bounds_ms(service_date)
            transactions = await self._canteen.transactions(from_ms=start, to_ms=end, limit=5000)
        except Exception as failure:  # noqa: BLE001 — kantin dışarısı; ekran ayakta kalmalı
            self._log.warning("gün işlemleri okunamadı", error=str(failure))
            return {}

        product_id = str(product.get("id"))
        counts: dict[str, int] = {}
        for transaction in transactions:
            if transaction.get("reversedAt"):
                continue
            student = str(transaction.get("studentId") or "")
            if not student:
                continue
            for item in transaction.get("items") or []:
                if str(item.get("productId")) == product_id:
                    counts[student] = counts.get(student, 0) + int(item.get("qty") or 0)
        return counts

    # ------------------------------------------------------------ ön izleme

    async def preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Gönderim yapmadan ne olacağını söyler. Ekran bunu göstermeden yazamaz."""
        service_date = str(payload["date"])
        kantin_ids = [str(item) for item in payload.get("students") or []]
        portion = max(1, min(MAX_PORTION, int(payload.get("portion") or 1)))

        snapshot = await self._safe_snapshot()
        product = await self._lunch_product(snapshot["products"])
        if product is None:
            return {
                "ok": False,
                "error": "Kantinde öğle yemeği ürünü bulunamadı. "
                         "Ayarda `product_id` belirtin ya da kantinde ürünü açın.",
                "rows": [], "summary": {},
            }

        unit_price = int(payload.get("unitPrice") or product.get("price") or 0)
        stock = int(product.get("stock") or 0)
        # Aynı güne ikinci porsiyon ancak açıkça istenirse girilir.
        allow_repeat = bool(payload.get("allowRepeat"))
        recorded = await self._recorded_today(service_date)
        by_id = {str(item.get("id")): item for item in snapshot["students"]}

        rows: list[dict[str, Any]] = []
        eligible = 0
        for kantin_id in kantin_ids:
            student = by_id.get(kantin_id)
            if student is None:
                rows.append(self._row(kantin_id, "", "missing", "Öğrenci kantinde bulunamadı."))
                continue

            name = str(student.get("displayName") or "")
            amount = unit_price * portion
            balance = int(student.get("balance") or 0)
            limit = student.get("spendingLimit")

            if student.get("isBlocked"):
                rows.append(self._row(kantin_id, name, "blocked", "Öğrenci engelli; satış açılmaz."))
            elif limit is not None and balance + amount > int(limit):
                rows.append(self._row(
                    kantin_id, name, "limit",
                    f"Harcama limiti aşılıyor ({(balance + amount) / 100:.2f} ₺ > {int(limit) / 100:.2f} ₺).",
                ))
            elif recorded.get(kantin_id) and not allow_repeat:
                # VARSAYILAN OLARAK ENGELDİR. Kazayla iki kez "İşle" demek 40 öğrenciyi
                # iki kez borçlandırır; bunun bedeli, ikinci porsiyonu bir onay kutusu
                # arkasına koymanın maliyetinden çok büyüktür.
                rows.append(self._row(
                    kantin_id, name, "already",
                    f"Bu gün için zaten {recorded[kantin_id]} porsiyon işlenmiş — "
                    f"ikinci porsiyon için onay gerekir.",
                    amount=amount, balance=balance,
                ))
            elif recorded.get(kantin_id):
                rows.append(self._row(
                    kantin_id, name, "repeat",
                    f"Zaten {recorded[kantin_id]} porsiyon var; ikinci porsiyon bilerek ekleniyor.",
                    amount=amount, balance=balance,
                ))
                eligible += 1
            else:
                rows.append(self._row(kantin_id, name, "ok", "", amount=amount, balance=balance))
                eligible += 1

        needed = eligible * portion
        return {
            "ok": True,
            "error": snapshot["error"],
            "rows": rows,
            "summary": {
                "selected": len(kantin_ids),
                "eligible": eligible,
                "blocked": sum(1 for row in rows if row["verdict"] == "blocked"),
                "limit": sum(1 for row in rows if row["verdict"] == "limit"),
                "already": sum(1 for row in rows if row["verdict"] == "already"),
                "repeat": sum(1 for row in rows if row["verdict"] == "repeat"),
                "missing": sum(1 for row in rows if row["verdict"] == "missing"),
                "allowRepeat": allow_repeat,
                "portion": portion,
                "unitPrice": unit_price,
                "totalAmount": eligible * unit_price * portion,
                "stock": stock,
                "stockNeeded": needed,
                "stockShort": max(0, needed - stock),
            },
            "product": self._product_view(product),
        }

    @staticmethod
    def _row(kantin_id: str, name: str, verdict: str, message: str, *,
             amount: int = 0, balance: int = 0) -> dict[str, Any]:
        return {
            "kantinId": kantin_id, "name": name, "verdict": verdict,
            "message": message, "amount": amount, "balance": balance,
        }

    # -------------------------------------------------------------- yazma

    async def commit(self, payload: dict[str, Any], *, actor: str) -> dict[str, Any]:
        """Yemek kaydını işler. Kuru provada hiçbir şey gönderilmez, yalnız ön izleme döner."""
        preview = await self.preview(payload)
        if not preview["ok"]:
            return preview
        if payload.get("dryRun"):
            return {**preview, "dryRun": True}

        service_date = str(payload["date"])
        portion = int(preview["summary"]["portion"])
        unit_price = int(preview["summary"]["unitPrice"])
        product = preview["product"]

        # Yalnız ön izlemenin UYGUN dediği satırlar gider. "already" (o gün zaten
        # yemek işlenmiş) buraya girmez — ikinci porsiyon `allowRepeat` ile istenir.
        targets = [row for row in preview["rows"] if row["verdict"] in ("ok", "repeat")]
        if not targets:
            blocked_by_repeat = preview["summary"].get("already") or 0
            return {**preview, "committed": False, "error": (
                f"İşlenecek uygun öğrenci yok — {blocked_by_repeat} öğrenciye bu gün için "
                f"yemek zaten işlenmiş. İkinci porsiyon girmek istiyorsanız “ikinci porsiyona "
                f"izin ver” seçeneğini işaretleyin."
                if blocked_by_repeat else "İşlenecek uygun öğrenci yok.")}

        batch_ref = f"L{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
        # Sınıf bilgisi Öğrenci Yönetimi modülünün verisidir; arayüz onu yetenek
        # üzerinden okur ve burada yalnız iz olarak saklanır (K3 — tablosuna girmeyiz).
        classes = {str(key): str(value) for key, value in (payload.get("classes") or {}).items()}

        await self._store.execute(
            f"INSERT INTO {self._batches} (batch_ref, service_date, product_id, product_name, "
            f"unit_price, portion, note, created_at, created_by, total_count) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (batch_ref, service_date, int(product["id"]), str(product["name"]),
             unit_price, portion, str(payload.get("note") or ""), _now(), actor, len(targets)),
        )

        # GÖNDERİMDEN ÖNCE yaz: ağ koparsa hangi local_id'nin gittiği kayıtlı kalsın.
        sales: list[dict[str, Any]] = []
        for row in targets:
            kantin_id = row["kantinId"]
            seq = await self._next_seq(service_date, kantin_id)
            local_id = str(uuid.uuid5(
                LUNCH_NAMESPACE, f"{service_date}:{kantin_id}:{seq}"
            ))
            await self._store.execute(
                f"INSERT INTO {self._entries} (local_id, batch_ref, service_date, kantin_id, "
                f"student_name, class_name, amount, seq, status, created_at) "
                f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                (local_id, batch_ref, service_date, kantin_id, row["name"],
                 classes.get(kantin_id, ""), unit_price * portion, seq, _now()),
            )
            sales.append({
                "localId": local_id,
                "studentId": kantin_id,
                "createdAt": _epoch_ms(service_date, int(self._config.get("service_hour") or 12)),
                "items": [{
                    "productId": int(product["id"]),
                    "qty": portion,
                    "unitPrice": unit_price,
                }],
            })

        results = await self._canteen.sell_batch(sales)
        return await self._apply_results(batch_ref, results, preview)

    async def _next_seq(self, service_date: str, kantin_id: str) -> int:
        """Aynı gün + öğrenci için kaçıncı deneme.

        Geri alınan bir kayıt yeniden işlenebilsin diye gerekli: `local_id`
        sabit olsaydı kantin ikinci girişi 'duplicate' sayar ve iptal ettiğimiz
        yemeği bir daha giremezdik.
        """
        row = await self._store.fetch_one(
            f"SELECT COUNT(*) AS n FROM {self._entries} WHERE service_date = ? AND kantin_id = ?",
            (service_date, kantin_id),
        )
        return int((row or {}).get("n") or 0)

    async def _apply_results(self, batch_ref: str, results: list[dict[str, Any]],
                             preview: dict[str, Any]) -> dict[str, Any]:
        ok = failed = 0
        for result in results:
            local_id = str(result.get("localId") or "")
            status = str(result.get("status") or "failed")
            if status in ("created", "duplicate"):
                ok += 1
            else:
                failed += 1
            await self._store.execute(
                f"UPDATE {self._entries} SET status = ?, reason = ?, server_id = ? "
                f"WHERE local_id = ?",
                (status, str(result.get("reason") or ""), result.get("serverId"), local_id),
            )

        await self._store.execute(
            f"UPDATE {self._batches} SET ok_count = ?, fail_count = ? WHERE batch_ref = ?",
            (ok, failed, batch_ref),
        )
        self._log.info("yemek partisi işlendi", batch=batch_ref, ok=ok, failed=failed)

        entries = await self._store.fetch_all(
            f"SELECT * FROM {self._entries} WHERE batch_ref = ? ORDER BY rowid", (batch_ref,)
        )
        return {
            "ok": True, "committed": True, "batchRef": batch_ref,
            "okCount": ok, "failCount": failed,
            "entries": [dict(entry) for entry in entries],
            "summary": preview["summary"],
        }

    # ----------------------------------------------------------- geri alma

    async def reverse(self, *, batch_ref: str | None = None, local_id: str | None = None,
                      reason: str) -> dict[str, Any]:
        """Partiyi ya da tek satırı geri alır. SİLMEZ — kantinde ters kayıt oluşur."""
        if local_id:
            rows = await self._store.fetch_all(
                f"SELECT * FROM {self._entries} WHERE local_id = ?", (local_id,)
            )
        elif batch_ref:
            rows = await self._store.fetch_all(
                f"SELECT * FROM {self._entries} WHERE batch_ref = ? AND reversed_at IS NULL "
                f"AND status IN ('created','duplicate')",
                (batch_ref,),
            )
        else:
            return {"ok": False, "error": "Geri alınacak parti ya da satır belirtilmedi."}

        done, failures = 0, []
        for row in rows:
            entry_id = str(row["local_id"])
            if row["reversed_at"]:
                continue
            try:
                await self._canteen.reverse_sale(entry_id, reason)
            except Exception as failure:  # noqa: BLE001 — tek satır patlarsa diğerleri sürsün
                self._log.warning("satır geri alınamadı", localId=entry_id, error=str(failure))
                failures.append({"localId": entry_id, "error": str(failure)})
                continue
            await self._store.execute(
                f"UPDATE {self._entries} SET reversed_at = ?, reversed_reason = ? "
                f"WHERE local_id = ?",
                (_now(), reason, entry_id),
            )
            done += 1

        return {"ok": True, "reversed": done, "failures": failures}

    async def top_up_stock(self, quantity: int, reason: str) -> dict[str, Any]:
        """Yemek ürününe stok girişi — seçim stoktan fazlaysa tek tıkla çözüm.

        Stok düzeltme kantinde `local_id` ile idempotenttir; buradaki id her
        çağrıda yenidir, çünkü bu bilinçli ve tekrarlanabilir bir idari işlemdir.
        """
        products = await self._canteen.products()
        product = await self._lunch_product(products)
        if product is None:
            return {"ok": False, "error": "Yemek ürünü bulunamadı."}

        result = await self._canteen.adjust_stock(
            local_id=str(uuid.uuid4()),
            product_id=int(product["id"]),
            delta=int(quantity),
            reason=reason or "Öğle yemeği stok girişi (Kontrol Merkezi)",
        )
        return {"ok": True, "newStock": result.get("newStock"), "delta": int(quantity)}

    # -------------------------------------------------- sabit liste / tatil

    async def set_roster(self, kantin_ids: list[str]) -> dict[str, Any]:
        await self._store.execute(f"DELETE FROM {self._roster}")
        for kantin_id in kantin_ids:
            await self._store.execute(
                f"INSERT OR REPLACE INTO {self._roster} (kantin_id, updated_at) VALUES (?, ?)",
                (str(kantin_id), _now()),
            )
        return {"ok": True, "count": len(kantin_ids)}

    async def set_holiday(self, day: str, label: str, *, remove: bool = False) -> dict[str, Any]:
        if remove:
            await self._store.execute(f"DELETE FROM {self._holidays} WHERE day = ?", (day,))
        else:
            await self._store.execute(
                f"INSERT OR REPLACE INTO {self._holidays} (day, label, created_at) VALUES (?, ?, ?)",
                (day, label, _now()),
            )
        return {"ok": True, "day": day, "removed": remove}

    async def working_days(self, start: str, end: str) -> list[str]:
        """Aralıktaki iş günleri — hafta sonu ve tatiller çıkarılmış hâliyle."""
        holidays = {
            str(row["day"]) for row in
            await self._store.fetch_all(f"SELECT day FROM {self._holidays}")
        }
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        days: list[str] = []
        cursor = first
        while cursor <= last:
            iso = cursor.isoformat()
            if cursor.weekday() < 5 and iso not in holidays:
                days.append(iso)
            cursor += timedelta(days=1)
        return days

    # ------------------------------------------------------------ yardımcı

    async def _safe_snapshot(self) -> dict[str, Any]:
        """Kantin okunamasa bile ekranın açılabilmesi için hataları yutar (K7)."""
        try:
            snapshot = await self._canteen.snapshot()
            errors = snapshot.get("errors") or []
            return {
                "students": snapshot["students"],
                "products": snapshot["products"],
                "connected": not errors,
                "error": "; ".join(errors),
            }
        except Exception as failure:  # noqa: BLE001 — kantin dışarısı
            self._log.warning("kantin okunamadı", error=str(failure))
            return {"students": [], "products": [], "connected": False, "error": str(failure)}

    @staticmethod
    def _product_view(product: dict[str, Any] | None) -> dict[str, Any] | None:
        if product is None:
            return None
        return {
            "id": product.get("id"),
            "name": product.get("name"),
            "price": int(product.get("price") or 0),
            "stock": int(product.get("stock") or 0),
            "isActive": bool(product.get("isActive")),
        }

    @staticmethod
    def _student_view(student: dict[str, Any]) -> dict[str, Any]:
        return {
            "kantinId": student.get("id"),
            "displayName": student.get("displayName"),
            "balance": int(student.get("balance") or 0),
            "spendingLimit": student.get("spendingLimit"),
            "isBlocked": bool(student.get("isBlocked")),
        }
