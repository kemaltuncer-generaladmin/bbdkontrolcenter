"""Kantin Raporları — iş kuralları.

KAYNAK KANTİNDİR, HESAP BURADADIR. Kantin `GET /api/transactions` ucundan
kalem kırılımlı ham işlem listesi verir; öğrenci/ürün/sınıf kırılımları,
ABC analizi ve stok tükenme tahmini bu modülde hesaplanır. Kantine ek yük
bindirilmez, kantin kodu değişmez.

ÖNBELLEK YOKTUR. Her rapor kantinden taze çekilir. Kantin verisi geçmişe
dönük değişebiliyor (satış iptali cironun düşmesi, nakit tahsilatın bakiyeyi
değiştirmesi); saklanan kopya sessizce yanlış rakam veriyordu. Çekilen gün
yine kendi tablomuza yazılır ama YALNIZ İZ olarak — rapor üretilirken oradan
hiç okunmaz.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from km_sdk import report_dir, write_private

from . import analytics
from .export import ExportError, build_pdf, csv_bytes, money


class PreviewError(RuntimeError):
    """Önizleme görüntüsü üretilemedi. Rapor yine de kaydedilmiştir."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _bounds_ms(day: str) -> tuple[int, int]:
    start = datetime.fromisoformat(day)
    return int(start.timestamp() * 1000), int((start + timedelta(days=1)).timestamp() * 1000)


def _days_between(start: str, end: str) -> list[str]:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if last < first:
        first, last = last, first
    # Tek istekte çekilebilecek makul üst sınır: 400 gün.
    span = min((last - first).days, 400)
    return [(first + timedelta(days=offset)).isoformat() for offset in range(span + 1)]


def _tr_date(iso: str) -> str:
    try:
        return date.fromisoformat(iso).strftime("%d.%m.%Y")
    except ValueError:
        return iso


class ReportService:
    def __init__(self, *, canteen: Any, store: Any, log: Any, config: dict[str, Any],
                 printer: Any = None, category: str = "Kantin",
                 fallback_dir: Path | None = None) -> None:
        self._canteen = canteen
        self._store = store
        self._log = log
        self._config = config
        self._printer = printer
        self._category = category
        self._days = store.table("day")
        self._saved = store.table("saved")
        self._fallback_dir = fallback_dir or Path("data/exports")

    @property
    def _export_dir(self) -> Path:
        """Çıktı klasörü — her çağrıda YENİDEN çözülür.

        Ay değiştiğinde klasör kendiliğinden değişsin diye açılışta bir kez
        hesaplanmaz; uygulama aylarca açık kalabilir.
        """
        return report_dir(
            self._category,
            fallback=self._fallback_dir,
            configured=str(self._config.get("export_path") or ""),
        )

    # ------------------------------------------------------- veri toplama

    #: Kantinin tek istekte döndürebileceği en fazla satır (API sınırı).
    MAX_ROWS = 5000

    async def _record_day(self, day: str, rows: list[dict[str, Any]]) -> None:
        """Bir günün ham verisini kendi tablomuza yazar.

        BU ÖNBELLEK DEĞİLDİR: rapor üretilirken buradan hiç okunmaz. Tek
        işlevi "kantin o gün ne demişti" sorusunun cevabını saklamak —
        anlaşmazlık çıkarsa bakılacak iz.
        """
        total = sum(int(item.get("total") or 0) for item in analytics.live(rows))
        await self._store.execute(
            f"INSERT INTO {self._days} (day, payload_json, tx_count, total, fetched_at) "
            f"VALUES (?, ?, ?, ?, ?) "
            f"ON CONFLICT(day) DO UPDATE SET payload_json = excluded.payload_json, "
            f"tx_count = excluded.tx_count, total = excluded.total, "
            f"fetched_at = excluded.fetched_at",
            (day, json.dumps(rows, ensure_ascii=False), len(rows), total, _now()),
        )

    async def _fetch_day(self, day: str) -> list[dict[str, Any]]:
        """Tek günü çeker ve kaydeder."""
        start, end = _bounds_ms(day)
        rows = await self._canteen.transactions(from_ms=start, to_ms=end, limit=self.MAX_ROWS)
        await self._record_day(day, rows)
        return rows

    @staticmethod
    def _day_of(row: dict[str, Any]) -> str:
        """İşlemin YEREL gününü verir; kantin epoch-ms yazar."""
        created = int(row.get("createdAt") or 0)
        if not created:
            return ""
        return datetime.fromtimestamp(created / 1000).astimezone().date().isoformat()

    async def transactions(self, start: str, end: str, *,
                           refresh: bool = True) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Aralıktaki işlemler — HER ZAMAN kantinden taze çekilir.

        ÖNBELLEK KALDIRILDI. Eskiden çekilen günler kendi tablomuza yazılıyor
        ve ikinci açılışta oradan okunuyordu. Ama kantin verisi GEÇMİŞE DÖNÜK
        değişebiliyor: bir satış iptal edilince (`reverse`) o günün cirosu
        düşüyor, nakit tahsilat bakiyeyi değiştiriyor. Önbellekten okunan rapor
        bu değişiklikleri göstermiyordu — yani sessizce YANLIŞ rakam veriyordu.

        TEK İSTEK. Aralık kantine bir kerede sorulur; 30 günlük rapor için 30
        ayrı istek atmak hem yavaş hem de kantine gereksiz yük. Dönen satır
        sayısı API sınırına dayanırsa (kesilmiş olabilir) gün gün çekmeye
        düşülür — eksik veriyle rapor üretmektense yavaş üretmek yeğdir.

        `refresh` parametresi geriye dönük uyumluluk için duruyor; davranışı
        değiştirmez, veri her hâlükârda kaynaktan gelir.
        """
        wanted = _days_between(start, end)
        if not wanted:
            return [], {"days": 0, "fetched": 0, "fromCache": 0, "live": True,
                        "errors": [], "connected": True}

        from_ms, _ = _bounds_ms(wanted[0])
        _, to_ms = _bounds_ms(wanted[-1])

        errors: list[str] = []
        transactions: list[dict[str, Any]] = []
        truncated = False

        try:
            transactions = await self._canteen.transactions(
                from_ms=from_ms, to_ms=to_ms, limit=self.MAX_ROWS,
            )
            truncated = len(transactions) >= self.MAX_ROWS
        except Exception as failure:  # noqa: BLE001 — kantin dışarısı
            self._log.warning("aralık çekilemedi", start=start, end=end, error=str(failure))
            errors.append(str(failure))
            truncated = True  # tek istek olmadı; gün gün denenecek

        if truncated:
            # Sınıra dayandık ya da istek düştü: gün gün topla.
            self._log.info("aralık gün gün çekiliyor", days=len(wanted),
                           reason="sınır" if not errors else "hata")
            transactions, errors = [], []
            for day in wanted:
                try:
                    transactions.extend(await self._fetch_day(day))
                except Exception as failure:  # noqa: BLE001
                    self._log.warning("gün çekilemedi", day=day, error=str(failure))
                    errors.append(f"{day}: {failure}")
        else:
            # İz kaydı: gelen satırları günlerine dağıtıp yaz.
            by_day: dict[str, list[dict[str, Any]]] = {day: [] for day in wanted}
            for row in transactions:
                day = self._day_of(row)
                if day in by_day:
                    by_day[day].append(row)
            for day, rows in by_day.items():
                await self._record_day(day, rows)

        return transactions, {
            "days": len(wanted),
            "fetched": len(wanted) - len(errors),
            "fromCache": 0,
            "live": True,
            "requests": len(wanted) if truncated else 1,
            "errors": errors,
            "connected": not errors,
        }

    # ---------------------------------------------------------- rapor

    async def report(self, start: str, end: str, *, refresh: bool = False,
                     classes: dict[str, str] | None = None,
                     compare_previous: bool = True) -> dict[str, Any]:
        """Tüm sekmelerin verisini tek çağrıda üretir."""
        transactions, meta = await self.transactions(start, end, refresh=refresh)

        snapshot_errors: list[str] = []
        try:
            snapshot = await self._canteen.snapshot()
            students = snapshot["students"]
            products = snapshot["products"]
            snapshot_errors = list(snapshot.get("errors") or [])
        except Exception as failure:  # noqa: BLE001
            self._log.warning("kantin özeti okunamadı", error=str(failure))
            students, products = [], []
            snapshot_errors = [str(failure)]

        if snapshot_errors:
            self._log.warning("kantin özeti eksik", errors=snapshot_errors)

        balances = {str(item.get("id")): int(item.get("balance") or 0) for item in students}
        product_map = {int(item["id"]): item for item in products}
        span = len(_days_between(start, end))

        student_rows = analytics.by_student(transactions, balances=balances, classes=classes or {})
        product_rows = analytics.by_product(transactions, products=product_map, days=span)

        collections: dict[str, Any] = {}
        try:
            from_ms, _ = _bounds_ms(start)
            _, to_ms = _bounds_ms(end)
            collections = await self._canteen.collections(from_ms=from_ms, to_ms=to_ms)
        except Exception as failure:  # noqa: BLE001 — tahsilat okunamazsa rapor yine gelir
            self._log.warning("tahsilat okunamadı", error=str(failure))

        payload: dict[str, Any] = {
            "range": {"start": start, "end": end, "days": span},
            "meta": {**meta, "snapshotErrors": snapshot_errors},
            "overview": analytics.overview(transactions),
            "students": student_rows,
            "products": product_rows,
            "classes": analytics.by_class(student_rows),
            "deadStock": analytics.dead_stock(product_rows, products),
            "collections": collections,
            # Öğrenci listesi okunamadıysa 0 YAZMAYIZ: sıfır borç ile "bilinmiyor"
            # aynı şey değildir; ekran bunu "—" gösterir.
            "openReceivables": (
                sum(value for value in balances.values() if value > 0) if students else None
            ),
        }

        if compare_previous:
            previous_end = (date.fromisoformat(start) - timedelta(days=1)).isoformat()
            previous_start = (date.fromisoformat(previous_end) - timedelta(days=span - 1)).isoformat()
            previous_tx, _ = await self.transactions(previous_start, previous_end)
            payload["compare"] = {
                "range": {"start": previous_start, "end": previous_end},
                **analytics.compare(payload["overview"], analytics.overview(previous_tx)),
            }

        return payload

    async def student_detail(self, kantin_id: str, start: str, end: str) -> dict[str, Any]:
        """Tek öğrencinin dökümü — karne PDF'inin de kaynağı."""
        transactions, _ = await self.transactions(start, end)
        mine = [
            item for item in analytics.live(transactions)
            if str(item.get("studentId")) == kantin_id
        ]
        rows = analytics.by_student(mine)
        products = analytics.by_product(mine, days=len(_days_between(start, end)))
        return {
            "kantinId": kantin_id,
            "summary": rows[0] if rows else None,
            "products": products,
            "transactions": sorted(mine, key=lambda item: int(item.get("createdAt") or 0),
                                   reverse=True),
            "byDay": analytics.overview(mine)["byDay"],
        }

    # --------------------------------------------------------- çıktı

    async def export(self, kind: str, params: dict[str, Any]) -> dict[str, Any]:
        """PDF/CSV üretir, `data/exports/` altına yazar ve yolunu döner."""
        start, end = str(params["start"]), str(params["end"])
        self._export_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")

        try:
            if kind == "summary_pdf":
                data = await self.report(start, end, classes=params.get("classes"),
                                         compare_previous=False)
                content = self._summary_pdf(data)
                name = f"kantin-ozet-{start}_{end}-{stamp}.pdf"
            elif kind == "products_pdf":
                data = await self.report(start, end, compare_previous=False)
                content = self._products_pdf(data)
                name = f"kantin-urun-{start}_{end}-{stamp}.pdf"
            elif kind == "student_pdf":
                detail = await self.student_detail(str(params["studentId"]), start, end)
                content = self._student_pdf(detail, start, end)
                safe = "".join(
                    char for char in str(detail.get("summary", {}).get("name") or params["studentId"])
                    if char.isalnum() or char in " -_"
                ).strip().replace(" ", "-") or "ogrenci"
                name = f"kantin-karne-{safe}-{start}_{end}-{stamp}.pdf"
            elif kind == "students_csv":
                data = await self.report(start, end, classes=params.get("classes"),
                                         compare_previous=False)
                content = csv_bytes(
                    ["ogrenci_id", "ad", "sinif", "islem", "adet", "tutar_tl",
                     "ortalama_tl", "bakiye_tl", "favori_urun"],
                    [[row["studentId"], row["name"], row["className"], row["count"], row["units"],
                      f'{row["total"] / 100:.2f}'.replace(".", ","),
                      f'{row["average"] / 100:.2f}'.replace(".", ","),
                      f'{row["balance"] / 100:.2f}'.replace(".", ","),
                      row["favourite"]] for row in data["students"]],
                )
                name = f"kantin-ogrenci-{start}_{end}-{stamp}.csv"
            elif kind == "products_csv":
                data = await self.report(start, end, compare_previous=False)
                content = csv_bytes(
                    ["urun_id", "ad", "adet", "ciro_tl", "islem", "alici", "pay_yuzde",
                     "abc", "stok", "gunluk_ortalama", "kalan_gun"],
                    [[row["productId"], row["name"], row["qty"],
                      f'{row["total"] / 100:.2f}'.replace(".", ","),
                      row["count"], row["buyers"],
                      str(row["share"]).replace(".", ","), row["abc"],
                      row["stock"] if row["stock"] is not None else "",
                      str(row["perDay"]).replace(".", ","),
                      row["daysLeft"] if row["daysLeft"] is not None else ""]
                     for row in data["products"]],
                )
                name = f"kantin-urun-{start}_{end}-{stamp}.csv"
            else:
                return {"ok": False, "error": f"Bilinmeyen rapor türü: {kind}"}
        except ExportError as failure:
            return {"ok": False, "error": str(failure)}

        # Rapor kişisel veri taşır: öğrenci adı, harcama, bakiye.
        path = write_private(self._export_dir / name, content)
        self._log.info("rapor üretildi", kind=kind, path=str(path), bytes=len(content))
        return {"ok": True, "path": str(path), "name": name, "bytes": len(content)}

    # ------------------------------------------------------ önizleme/baskı

    async def preview(self, kind: str, params: dict[str, Any], *,
                      max_pages: int = 12, dpi: int = 110) -> dict[str, Any]:
        """Raporu üretir ve sayfalarını GÖRÜNTÜ olarak döner.

        Önizleme, basılacak PDF'in BİREBİR kendisidir: dosya `pdftoppm` ile
        sayfa sayfa PNG'ye çevrilir. HTML ile "benzerini" çizmek yanlış olurdu —
        ekranda gördüğü ile kâğıttan çıkan farklı olan bir önizleme, önizleme
        değildir.
        """
        produced = await self.export(kind, params)
        if not produced.get("ok"):
            return produced

        path = Path(produced["path"])
        if path.suffix.lower() != ".pdf":
            return {**produced, "pages": [], "pageCount": 0,
                    "previewError": "Bu çıktı PDF değil; önizlenemez."}

        try:
            pages, total = await self._render_pages(path, max_pages=max_pages, dpi=dpi)
        except PreviewError as failure:
            return {**produced, "pages": [], "pageCount": 0, "previewError": str(failure)}

        return {**produced, "pages": pages, "pageCount": total,
                "shown": len(pages), "previewError": ""}

    async def _render_pages(self, path: Path, *, max_pages: int,
                            dpi: int) -> tuple[list[str], int]:
        """PDF sayfalarını PNG data-URI listesine çevirir."""
        binary = shutil.which("pdftoppm")
        if not binary:
            raise PreviewError(
                "Önizleme üretilemedi: `pdftoppm` bulunamadı (poppler-utils kurulu olmalı). "
                "Rapor yine de kaydedildi ve yazdırılabilir."
            )

        with tempfile.TemporaryDirectory(prefix="km-onizleme-") as folder:
            target = Path(folder) / "sayfa"
            args = [binary, "-png", "-r", str(int(dpi)),
                    "-f", "1", "-l", str(int(max_pages)), str(path), str(target)]
            try:
                process = await asyncio.create_subprocess_exec(
                    *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                _, err = await asyncio.wait_for(process.communicate(), timeout=60)
            except TimeoutError:
                process.kill()
                await process.wait()
                raise PreviewError("Önizleme üretilemedi: süre aşıldı.") from None
            except OSError as failure:
                raise PreviewError(f"Önizleme üretilemedi: {failure}") from failure

            if process.returncode != 0:
                raise PreviewError(
                    f"Önizleme üretilemedi: {err.decode(errors='replace').strip() or 'bilinmeyen hata'}"
                )

            files = sorted(Path(folder).glob("sayfa*.png"))
            pages = [
                "data:image/png;base64," + base64.b64encode(item.read_bytes()).decode("ascii")
                for item in files
            ]

        return pages, await self._page_count(path) or len(pages)

    @staticmethod
    async def _page_count(path: Path) -> int:
        """PDF'in gerçek sayfa sayısı — önizlemede kaç sayfanın gizlendiğini söylemek için."""
        binary = shutil.which("pdfinfo")
        if not binary:
            return 0
        try:
            process = await asyncio.create_subprocess_exec(
                binary, str(path),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(process.communicate(), timeout=15)
        except (TimeoutError, OSError):
            return 0
        match = re.search(r"^Pages:\s+(\d+)", out.decode(errors="replace"), re.MULTILINE)
        return int(match.group(1)) if match else 0

    async def print_report(self, path: str, *, copies: int = 1) -> dict[str, Any]:
        """Üretilmiş raporu USB'deki yazıcıya gönderir. Onay sorulmaz.

        GÜVENLİK: yalnız BİZİM ürettiğimiz klasördeki dosya basılabilir.
        İstemciden gelen serbest bir yol, `lp` ile rastgele dosya bastırmaya
        (ve içeriğini kâğıda dökmeye) açık kapı bırakırdı.
        """
        if self._printer is None:
            return {"ok": False,
                    "error": "Yazıcı bağlantısı hatası: yazıcı yeteneği bu kurulumda yok."}

        candidate = Path(path).expanduser()
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            return {"ok": False, "error": "Basılacak rapor bulunamadı."}

        allowed = self._export_dir.resolve()
        if not str(resolved).startswith(str(allowed) + os.sep):
            return {"ok": False,
                    "error": "Bu dosya rapor klasöründe değil; güvenlik gereği basılmaz."}

        try:
            result = await self._printer.print_file(
                resolved, title=resolved.name, copies=copies,
            )
        except Exception as failure:  # noqa: BLE001 — yazıcı dışarısı
            self._log.warning("baskı başarısız", path=str(resolved), error=str(failure))
            return {"ok": False, "error": str(failure)}

        self._log.info("rapor yazdırıldı", path=str(resolved), printer=result.get("printer"))
        return {"ok": True, **result, "name": resolved.name}

    async def printer_status(self) -> dict[str, Any]:
        """Ekranın yazdır düğmesini doğru anlatabilmesi için yazıcı durumu."""
        if self._printer is None:
            return {"ready": False,
                    "error": "Yazıcı bağlantısı hatası: yazıcı yeteneği bu kurulumda yok."}
        return await self._printer.status()

    # ------------------------------------------------------- PDF şablonları

    @staticmethod
    def _period(start: str, end: str) -> str:
        return (f"{_tr_date(start)} – {_tr_date(end)}" if start != end else _tr_date(start))

    def _summary_pdf(self, data: dict[str, Any]) -> bytes:
        overview = data["overview"]
        start, end = data["range"]["start"], data["range"]["end"]

        sections: list[dict[str, Any]] = [
            {"kind": "tiles", "title": "Dönem özeti", "tiles": [
                ("Toplam ciro", money(overview["total"])),
                ("İşlem", str(overview["count"])),
                ("Alışveriş yapan öğrenci", str(overview["students"])),
                ("Satılan adet", str(overview["units"])),
                ("Ortalama sepet", money(overview["average"])),
                ("Öğrenci başına", money(overview["perStudent"])),
                ("Açık alacak", money(data["openReceivables"])),
                ("İptal edilen", f'{overview["reversedCount"]} · {money(overview["reversedTotal"])}'),
            ]},
        ]

        if overview["byDay"]:
            sections.append({"kind": "bars", "title": "Günlük ciro", "bars": [
                (_tr_date(row["day"]), row["total"], money(row["total"]))
                for row in overview["byDay"]
            ]})

        busy = [row for row in overview["byHour"] if row["count"] > 0]
        if busy:
            sections.append({"kind": "bars", "title": "Saat dağılımı (yoğunluk)", "bars": [
                (f'{row["hour"]:02d}:00', row["count"], f'{row["count"]} işlem')
                for row in busy
            ]})

        top_students = data["students"][:25]
        if top_students:
            sections.append({
                "kind": "table",
                "title": f"Öğrenci bazlı (ilk {len(top_students)})",
                "headers": ["Öğrenci", "Sınıf", "İşlem", "Adet", "Tutar", "Ortalama", "Bakiye"],
                "align": "LLRRRRR",
                "widths": [3.2, 1.1, 0.9, 0.9, 1.5, 1.5, 1.5],
                "rows": [[row["name"], row["className"] or "—", row["count"], row["units"],
                          money(row["total"]), money(row["average"]), money(row["balance"])]
                         for row in top_students],
            })

        top_products = data["products"][:25]
        if top_products:
            sections.append({
                "kind": "table",
                "title": f"Ürün bazlı (ilk {len(top_products)})",
                "headers": ["Ürün", "Adet", "Ciro", "Pay %", "ABC", "Alıcı"],
                "align": "LRRRCR",
                "widths": [3.6, 0.9, 1.5, 0.9, 0.6, 0.9],
                "rows": [[row["name"], row["qty"], money(row["total"]),
                          f'%{row["share"]:.1f}'.replace(".", ","), row["abc"], row["buyers"]]
                         for row in top_products],
            })

        if data["classes"] and not (len(data["classes"]) == 1 and data["classes"][0]["className"] == "—"):
            sections.append({
                "kind": "table", "title": "Sınıf bazlı",
                "headers": ["Sınıf", "Öğrenci", "İşlem", "Tutar", "Öğrenci başına"],
                "align": "LRRRR", "widths": [2, 1, 1, 1.5, 1.5],
                "rows": [[row["className"], row["students"], row["count"],
                          money(row["total"]), money(row["perStudent"])]
                         for row in data["classes"]],
            })

        sections.append({"kind": "note", "text":
                         "İptal edilen satışlar ciroya ve kırılımlara dahil edilmemiştir. "
                         "Tutarlar kuruş duyarlığında hesaplanmış, sunumda ₺'ye çevrilmiştir."})

        return build_pdf(
            title="Kantin Satış Raporu",
            subtitle=f"{self._period(start, end)} · {data['range']['days']} gün",
            sections=sections,
            footer="Kontrol Merkezi · Kantin Raporları",
        )

    def _products_pdf(self, data: dict[str, Any]) -> bytes:
        start, end = data["range"]["start"], data["range"]["end"]
        rows = data["products"]

        sections: list[dict[str, Any]] = [
            {"kind": "tiles", "title": "Ürün performansı", "tiles": [
                ("Satan ürün çeşidi", str(len(rows))),
                ("Toplam adet", str(sum(row["qty"] for row in rows))),
                ("Toplam ciro", money(sum(row["total"] for row in rows))),
                ("A sınıfı ürün", str(sum(1 for row in rows if row["abc"] == "A"))),
            ]},
            {"kind": "table", "title": "Tüm ürünler — ciroya göre",
             "headers": ["Ürün", "Adet", "Ciro", "Pay %", "Küm. %", "ABC", "Stok", "Kalan gün"],
             "align": "LRRRRCRR",
             "widths": [3.4, 0.8, 1.4, 0.8, 0.8, 0.6, 0.8, 0.9],
             "rows": [[row["name"], row["qty"], money(row["total"]),
                       f'%{row["share"]:.1f}'.replace(".", ","),
                       f'%{row["cumulativeShare"]:.1f}'.replace(".", ","),
                       row["abc"],
                       row["stock"] if row["stock"] is not None else "—",
                       f'{row["daysLeft"]:.1f}'.replace(".", ",") if row["daysLeft"] is not None else "—"]
                      for row in rows]},
        ]

        risky = [row for row in rows if row["daysLeft"] is not None and row["daysLeft"] <= 7]
        if risky:
            sections.append({
                "kind": "table", "title": "Stoğu tükenmek üzere (≤ 7 gün)",
                "headers": ["Ürün", "Stok", "Günlük ortalama", "Tahmini kalan gün"],
                "align": "LRRR", "widths": [3.5, 1, 1.4, 1.4],
                "rows": [[row["name"], row["stock"],
                          str(row["perDay"]).replace(".", ","),
                          f'{row["daysLeft"]:.1f}'.replace(".", ",")]
                         for row in sorted(risky, key=lambda row: row["daysLeft"])],
            })

        dead = data["deadStock"][:30]
        if dead:
            sections.append({
                "kind": "table",
                "title": "Ölü stok — dönemde hiç satılmayan aktif ürünler",
                "headers": ["Ürün", "Stok", "Birim fiyat", "Bağlı tutar"],
                "align": "LRRR", "widths": [3.5, 1, 1.4, 1.4],
                "rows": [[row["name"], row["stock"], money(row["price"]), money(row["value"])]
                         for row in dead],
            })
            sections.append({"kind": "note", "text":
                             f"Ölü stokta bağlı toplam tutar: "
                             f"{money(sum(row['value'] for row in data['deadStock']))}."})

        sections.append({"kind": "note", "text":
                         "ABC sınıfı ciro payına göredir: kümülatif %80'e kadar A, "
                         "%95'e kadar B, kalanı C. Kalan gün tahmini dönemdeki günlük "
                         "ortalama satışa dayanır; sezon etkisini hesaba katmaz."})

        return build_pdf(
            title="Ürün Performans Raporu",
            subtitle=f"{self._period(start, end)} · {data['range']['days']} gün",
            sections=sections,
            footer="Kontrol Merkezi · Kantin Raporları",
        )

    def _student_pdf(self, detail: dict[str, Any], start: str, end: str) -> bytes:
        summary = detail.get("summary") or {}
        name = summary.get("name") or detail["kantinId"]

        sections: list[dict[str, Any]] = [
            {"kind": "tiles", "title": "Özet", "tiles": [
                ("Toplam harcama", money(summary.get("total"))),
                ("İşlem", str(summary.get("count") or 0)),
                ("Alınan adet", str(summary.get("units") or 0)),
                ("Ortalama sepet", money(summary.get("average"))),
            ]},
        ]

        if detail["products"]:
            sections.append({
                "kind": "table", "title": "Aldığı ürünler",
                "headers": ["Ürün", "Adet", "Tutar", "Kez"],
                "align": "LRRR", "widths": [4, 1, 1.5, 1],
                "rows": [[row["name"], row["qty"], money(row["total"]), row["count"]]
                         for row in detail["products"]],
            })

        rows = detail["transactions"][:120]
        if rows:
            sections.append({
                "kind": "table", "title": f"İşlem dökümü (son {len(rows)})",
                "headers": ["Tarih", "Ürünler", "Tutar"],
                "align": "LLR", "widths": [1.6, 5, 1.4],
                "rows": [[
                    analytics.local(item["createdAt"]).strftime("%d.%m.%Y %H:%M")
                    if item.get("createdAt") else "—",
                    ", ".join(f'{line["qty"]}× {line["name"]}' for line in item.get("items") or []),
                    money(item.get("total")),
                ] for item in rows],
            })

        sections.append({"kind": "note", "text":
                         "Bu döküm yalnız kantin harcamalarını gösterir. Güncel bakiye "
                         f"{money(summary.get('balance'))} olup pozitif değer borcu belirtir. "
                         "İptal edilen işlemler listeye dahil değildir."})

        return build_pdf(
            title=f"Öğrenci Kantin Karnesi — {name}",
            subtitle=f"{self._period(start, end)}",
            sections=sections,
            footer="Kontrol Merkezi · Kantin Raporları",
        )

    # -------------------------------------------------- kayıtlı raporlar

    async def saved_reports(self) -> list[dict[str, Any]]:
        rows = await self._store.fetch_all(
            f"SELECT id, name, params_json, created_at, created_by FROM {self._saved} "
            f"ORDER BY name"
        )
        return [
            {"id": row["id"], "name": row["name"],
             "params": json.loads(row["params_json"] or "{}"),
             "createdBy": row["created_by"]}
            for row in rows
        ]

    async def save_report(self, name: str, params: dict[str, Any], *,
                          actor: str) -> dict[str, Any]:
        await self._store.execute(
            f"INSERT INTO {self._saved} (name, params_json, created_at, created_by) "
            f"VALUES (?, ?, ?, ?) "
            f"ON CONFLICT(name) DO UPDATE SET params_json = excluded.params_json, "
            f"created_at = excluded.created_at, created_by = excluded.created_by",
            (name.strip(), json.dumps(params, ensure_ascii=False), _now(), actor),
        )
        return {"ok": True, "name": name.strip()}

    async def delete_report(self, report_id: int) -> dict[str, Any]:
        # Kayıtlı rapor bir kısayoldur, iş verisi değil.
        await self._store.execute(f"DELETE FROM {self._saved} WHERE id = ?", (int(report_id),))
        return {"ok": True}

    async def cache_state(self) -> dict[str, Any]:
        row = await self._store.fetch_one(
            f"SELECT COUNT(*) AS days, MIN(day) AS first, MAX(day) AS last, "
            f"MAX(fetched_at) AS last_fetch FROM {self._days}"
        )
        return dict(row or {})
