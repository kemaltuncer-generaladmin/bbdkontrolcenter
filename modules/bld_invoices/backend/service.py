"""Faturalar — iş kuralları.

VERİ BLD'DEDİR, KARAR BURADADIR. Belge kaydı, numarası ve donmuş içeriği
(`snapshot_json`) BLD sunucusundadır ve buraya `bld.api` geçidinden gelir (K4);
bu modül ham httpx kullanmaz ve UZAK VERİNİN KOPYASINI TUTMAZ. Yerel iki tablo
BLD'de karşılığı olmayan iki şeyi saklar:

  1. YAZMA DENEMESİNİN İZİ. BLD `veykemtu_control_audit` tutuyor ama o kayıt
     yalnız SUNUCUYA ULAŞAN isteği bilir. Ağ koparsa "kim hangi belgeyi kesmeye
     çalıştı" sorusunun cevabı yalnız burada kalır.
  2. ÜRETİLEN DOSYANIN KÜNYESİ. Fatura verisi değil, DOSYA: yol, sha256, boyut
     ve basıldığı an. Elindeki kâğıdın hangi üretimden çıktığı ancak böyle
     bilinir; belgenin kendisi her zaman sunucudan yeniden okunur.

UZAK SİSTEM DÜŞERSE EKRAN AYAKTA KALIR (K7): okuma uçları
`{"ok": True, "connected": False, "error": ...}` döner, İSTİSNA DIŞARI SIZMAZ.
`ok: True` OKUMANIN BAŞARISINI DEĞİL UCUN SAĞLIĞINI anlatır; ayrımı `connected`
taşır ve panel onu OKUR. Sunucu tarafındaki fatura uçları henüz yayında değil:
geçit temiz bir `control_endpoint_missing` veriyor ve ekran bunu "sunucu bu
turda hazır değil" diye yazıyor — bu BEKLENEN durumdur, hata değil.

BELGE DÜZENLENMEZ. `PATCH` ve `DELETE` sözleşmede yok; düzeltme
`void` + yeni belgedir. Servis bu yüzden yalnız üç yazma yolu tanır: kes,
prova, iptal.

HER YAZMADA AÇIK `dry_run=` GEÇİLİR. Geçidin varsayılanı `config/local.yaml`
ile değişebilir ve o dosya git dışıdır; bayrağı atlayan bir çağrı hiçbir şey
yazmadan `{"ok": true}` alır, ekran "belge kesildi" der ve seride olmayan bir
numara konuşulmaya başlanır.

YAZMA ZİNCİRİ — her yazma ucu bu beş adımı bu sırayla uygular:

    1. gerekçe denetimi (min 10 — arayüzde zorunlu göstermek yetmez, K9)
    2. kip/aralık denetimi (hangi belgeyi kesiyoruz)
    3. yerel iz: `result="denendi"`  ← ağ koparsa geriye YALNIZ bu kalır
    4. geçit çağrısı (açık `dry_run=`)
    5. yerel iz: `ok` / `dry_run` / `hata`
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from km_sdk import ExportError, build_pdf, report_dir, write_private

from . import documents as doc

#: Liste dökümü çekilirken tek istekte istenen satır. 200'ün üstü sunucu
#: sözleşmesinde garanti değil; altı gereksiz istek üretir.
LIST_PAGE = 200

#: Önizleme görüntüsü üretilirken en çok kaç sayfa çevrilsin.
PREVIEW_PAGES = 12


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


class PreviewError(RuntimeError):
    """Önizleme görüntüsü üretilemedi. Belge yine de kaydedilmiştir."""


class InvoicesService:
    """Faturalar ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ.

    Servis bir istisna ile cevap verseydi ekran beyaz bir hata sayfası
    gösterirdi; burada her yol `{"ok": ..., "error": ...}` ile biter. 4xx
    yalnız izin ve şema kapısından çıkar.
    """

    def __init__(self, *, api: Any, store: Any, log: Any, config: dict[str, Any],
                 printer: Any = None, category: str = "BLD",
                 subcategory: str = "Faturalar", fallback_dir: Path | None = None) -> None:
        self._api = api
        self._store = store
        self._log = log
        self._config = config or {}
        self._printer = printer
        self._category = category
        self._subcategory = subcategory
        self._fallback = fallback_dir or Path.home() / "km-raporlar"

        self._audit = store.table("audit")
        self._archive = store.table("archive")

    # ------------------------------------------------------------- ayarlar

    @property
    def _dry_run_default(self) -> bool:
        """İstemci `dryRun` alanını HİÇ göndermezse geçerli olan değer.

        Yedek değer de `False`: ayar dosyası okunamadığında ekranın "belge
        kesildi" deyip hiçbir şey yazmaması, açık bir hatadan çok daha
        pahalıdır — konuşulan numara seride yoktur.
        """
        return bool(self._config.get("dry_run_default", False))

    def _dry(self, dry_run: bool | None) -> bool:
        return self._dry_run_default if dry_run is None else bool(dry_run)

    @property
    def _page_size(self) -> int:
        return max(10, min(200, doc.as_int(self._config.get("page_size"), 25)))

    @property
    def _row_limit(self) -> int:
        return max(1, min(500, doc.as_int(self._config.get("archive_limit"), 100)))

    @property
    def _list_report_rows(self) -> int:
        return max(50, min(2000, doc.as_int(self._config.get("list_report_rows"), 500)))

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
        return message or "BLD sunucusuna ulaşılamadı."

    @staticmethod
    def _code(failure: Exception) -> str:
        """Geçidin makine okunur nedeni. `control_endpoint_missing` ile
        `not_found` AYRI şeylerdir: ilki "uç henüz yayında değil, bekle",
        ikincisi "uç var, kayıt yok" demektir ve ekran ikisine ayrı cümle
        yazar."""
        return str(getattr(failure, "code", "") or "")

    def _stamp(self) -> str:
        return datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")

    @staticmethod
    def _rows(payload: Any) -> list[dict[str, Any]]:
        """Geçidin liste zarfından satırlar. Geçit `{"items": [...]}` veriyor;
        düz dizi de kabul edilir, üçüncü bir ad UYDURULMAZ."""
        if isinstance(payload, dict):
            items = payload.get("items")
            if isinstance(items, list):
                return [row for row in items if isinstance(row, dict)]
            data = payload.get("data")
            if isinstance(data, list):
                return [row for row in data if isinstance(row, dict)]
            return []
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        return []

    @staticmethod
    def _meta(payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict) and isinstance(payload.get("meta"), dict):
            return dict(payload["meta"])
        return {}

    @staticmethod
    def _result(payload: Any) -> dict[str, Any]:
        """Yazma yanıtının `data` bloğu. Kuru provada sunucu düz gövde
        döndürüyor (`action`, `mode`, `line_count`, `total_kurus`); o durumda
        gövdenin kendisi sonuçtur."""
        if not isinstance(payload, dict):
            return {}
        data = payload.get("data")
        if isinstance(data, dict):
            return dict(data)
        return {key: value for key, value in payload.items()
                if key not in ("ok", "dry_run", "sent", "request_id", "method", "path", "body")}

    # ------------------------------------------------------ yerel tablolar

    async def _record(self, *, action: str, reason: str, actor: str, result: str,
                      invoice_id: int = 0, detail: Any = None) -> None:
        """Yerel denetim izi. SATIR SİLİNMEZ ve `snapshot_json` YAZILMAZ —
        kişisel veriyi ve adresi ikinci kez çoğaltırdı (sözleşme)."""
        try:
            await self._store.execute(
                f"INSERT INTO {self._audit} "
                "(invoice_id, action, reason, actor, result, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (int(invoice_id or 0), action, reason, actor, result,
                 json.dumps(detail or {}, ensure_ascii=False), _now()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın (K7)
            self._log.warning("denetim izi yazılamadı", action=action, error=str(failure))

    async def _archive_file(self, *, path: Path, content: bytes, kind: str, actor: str,
                            invoice_id: int = 0, invoice_no: str = "") -> str:
        """Üretilen DOSYANIN künyesi. Belgenin verisi değil, dosyanın kimliği.

        `sha256` elindeki kâğıdın hangi üretimden çıktığını kanıtlar: aynı
        belge iki kez üretildiğinde iki satır olur ve ikisi de arşivde durur.
        Silme yok — bir dosya diskten kalksa bile künyesi kalır.
        """
        digest = hashlib.sha256(content).hexdigest()
        try:
            await self._store.execute(
                f"INSERT INTO {self._archive} "
                "(invoice_id, invoice_no, kind, path, name, sha256, bytes, actor, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (int(invoice_id or 0), invoice_no, kind, str(path), path.name, digest,
                 len(content), actor, _now()),
            )
        except Exception as failure:  # noqa: BLE001 — künye yazılamadı, dosya yine üretildi
            self._log.warning("arşiv künyesi yazılamadı", path=str(path), error=str(failure))
        return digest

    async def _mark_printed(self, path: Path, copies: int) -> None:
        try:
            await self._store.execute(
                f"UPDATE {self._archive} SET printed_at = ?, print_copies = print_copies + ? "
                "WHERE path = ?", (_now(), int(copies), str(path)))
        except Exception as failure:  # noqa: BLE001 — kâğıt çıktı, kayıt düşmedi
            self._log.warning("baskı anı yazılamadı", path=str(path), error=str(failure))

    # ================================================================ okuma

    def _filters(self, *, q: str = "", status: str = "", customer_id: int = 0,
                 order_id: int = 0, subscription_id: int = 0,
                 date_from: str = "", date_to: str = "") -> dict[str, Any]:
        """Geçide gidecek süzgeçler — hepsi sözleşmede tanımlı (`GET /`).

        YEREL SÜZGEÇ YOKTUR: sunucu `q`, `status`, tarih ve kimlik
        süzgeçlerinin dördünü de uyguluyor. Sayfa üzerinde ikinci bir süzme
        yapmak, `meta.issued_total_kurus` ile ekrandaki satırları ayrıştırırdı.
        """
        return {
            "q": doc.text(q, 120),
            "status": doc.text(status, 16),
            "customer_id": int(customer_id) or None,
            "order_id": int(order_id) or None,
            "subscription_id": int(subscription_id) or None,
            "date_from": doc.text(date_from, 10),
            "date_to": doc.text(date_to, 10),
        }

    async def invoices(self, *, q: str = "", status: str = "", customer_id: int = 0,
                       order_id: int = 0, subscription_id: int = 0, date_from: str = "",
                       date_to: str = "", page: int = 1,
                       per_page: int = 0) -> dict[str, Any]:
        """Belge listesi (sunucu tarafında sayfalı).

        `meta.issued_total_kurus` SÜZGEÇLENMİŞ kümenin toplamıdır, sayfanın
        değil: ekranın alt satırındaki toplam sayfa değiştirince değişmemeli.
        Bu yüzden toplam SATIRLARDAN HESAPLANMAZ, sunucudan geldiği gibi
        taşınır — iptal edilmiş belgeler ona zaten girmiyor.
        """
        size = per_page or self._page_size
        filters = self._filters(q=q, status=status, customer_id=customer_id,
                                order_id=order_id, subscription_id=subscription_id,
                                date_from=date_from, date_to=date_to)
        try:
            payload = await self._api.invoices(page=max(1, int(page)), per_page=size, **filters)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("belge listesi okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "code": self._code(failure), "items": [], "meta": {},
                    "page": max(1, int(page)), "per_page": size,
                    "printer_available": self._printer is not None}
        meta = self._meta(payload)
        return {"ok": True, "connected": True, "error": "", "code": "",
                "items": [doc.invoice_row(row) for row in self._rows(payload)],
                "meta": meta,
                "page": doc.as_int(meta.get("page"), max(1, int(page))),
                "per_page": doc.as_int(meta.get("per_page"), size),
                # Yazıcı yeteneği İSTEĞE BAĞLI (K7): yoksa ekran çalışır,
                # yalnız "Yazdır" düğmesi kapanır ve nedenini söyler.
                "printer_available": self._printer is not None}

    async def invoice(self, invoice_id: int) -> dict[str, Any]:
        """Tek belge — donmuş içeriğiyle (`snapshot_json`)."""
        try:
            payload = await self._api.invoice(int(invoice_id))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("belge okunamadı", invoice_id=int(invoice_id),
                              error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "code": self._code(failure), "data": {}}
        return {"ok": True, "connected": True, "error": "", "code": "",
                "data": doc.invoice_card(payload if isinstance(payload, dict) else {})}

    async def archive(self, *, invoice_id: int = 0, limit: int = 0) -> dict[str, Any]:
        """Yerel arşiv: bu makinede ÜRETİLMİŞ dosyaların künyesi.

        Belge listesiyle karıştırılmamalı — burada duran şey belge değil,
        kâğıdın kaynağı olan dosyadır. BLD'ye hiç gitmez, bu yüzden geçit
        düşükken de dolu gelir.
        """
        rows = await self._local(self._archive, invoice_id=invoice_id, limit=limit,
                                 columns="id, invoice_id, invoice_no, kind, path, name, "
                                         "sha256, bytes, actor, created_at, printed_at, "
                                         "print_copies")
        return {"ok": True, "connected": True, "error": "", "items": rows}

    async def audit(self, *, invoice_id: int = 0, limit: int = 0) -> dict[str, Any]:
        """Yerel denetim izi: kim neyi denedi, sonucu ne oldu."""
        rows = await self._local(self._audit, invoice_id=invoice_id, limit=limit,
                                 columns="id, invoice_id, action, reason, actor, result, "
                                         "detail, created_at")
        return {"ok": True, "connected": True, "error": "", "items": rows}

    async def _local(self, table: str, *, invoice_id: int, limit: int,
                     columns: str) -> list[dict[str, Any]]:
        size = max(1, min(500, int(limit) or self._row_limit))
        where = "WHERE invoice_id = ? " if invoice_id else ""
        params: tuple[Any, ...] = (int(invoice_id), size) if invoice_id else (size,)
        try:
            rows = await self._store.fetch_all(
                f"SELECT {columns} FROM {table} {where}ORDER BY id DESC LIMIT ?", params)
        except Exception as failure:  # noqa: BLE001 — yerel tablo okunamadı (K7)
            self._log.warning("yerel tablo okunamadı", table=table, error=str(failure))
            return []
        return [dict(row) for row in rows]

    # ================================================================ yazma

    async def create(self, *, order_id: int = 0, subscription_id: int = 0,
                     period_start: str = "", period_end: str = "",
                     subscription_payment_id: int = 0, reason: str, actor: str,
                     dry_run: bool | None) -> dict[str, Any]:
        """Belge keser. İki kip vardır ve BİRİ SEÇİLMELİDİR.

        KURU PROVA NUMARA ÜRETMEZ (seride boşluk açardı) ama kalem sayısını ve
        toplamı hesaplayıp döner; panel gerçek çağrıdan önce onu gösterir.

        Aynı sipariş/dönem için geçerli bir belge varsa sunucu 409 verir ve
        geçit `conflict` koduyla patlar. Ekran o durumda "önce eskisini iptal
        edin" der; ikinci bir belge sessizce kesilmez.
        """
        guard = doc.reason_error(reason)
        if guard:
            return {"ok": False, "error": guard}
        problem = doc.create_error(order_id=int(order_id or 0),
                                   subscription_id=int(subscription_id or 0),
                                   period_start=period_start, period_end=period_end)
        if problem:
            return {"ok": False, "error": problem}

        dry = self._dry(dry_run)
        detail = {"mode": "order" if order_id else "subscription",
                  "order_id": int(order_id or 0),
                  "subscription_id": int(subscription_id or 0),
                  "period_start": doc.text(period_start),
                  "period_end": doc.text(period_end),
                  "subscription_payment_id": int(subscription_payment_id or 0),
                  "dry_run": dry}
        # ÜÇÜNCÜ ADIM: geçit çağrısından ÖNCE. Ağ koparsa "kim hangi belgeyi
        # kesmeye çalıştı" sorusunun cevabı yalnız bu satırda kalır.
        await self._record(action="invoice.create", reason=reason, actor=actor,
                           result=doc.TRIED, detail=detail)
        try:
            payload = await self._api.create_invoice(
                order_id=int(order_id) or None,
                subscription_id=int(subscription_id) or None,
                period_start=doc.text(period_start), period_end=doc.text(period_end),
                subscription_payment_id=int(subscription_payment_id) or None,
                reason=reason, actor=actor,
                # AÇIK BAYRAK: geçidin varsayılanına asla güvenilmez.
                dry_run=dry)
        except Exception as failure:  # noqa: BLE001 — K7
            code = self._code(failure)
            await self._record(action="invoice.create", reason=reason, actor=actor,
                               result=doc.FAILED, detail={**detail, "error": code})
            return {"ok": False, "error": self._fail(failure), "code": code, "dry_run": dry}

        result = self._result(payload)
        await self._record(action="invoice.create", reason=reason, actor=actor,
                           invoice_id=doc.as_int(result.get("id")),
                           result=doc.DRY if dry else doc.DONE,
                           detail={**detail, "invoice_no": doc.text(result.get("invoice_no")),
                                   "total_kurus": doc.as_int(result.get("total_kurus")),
                                   "audit_id": doc.as_int(
                                       payload.get("audit_id") if isinstance(payload, dict)
                                       else 0)})
        return {"ok": True, "error": "", "dry_run": dry, "data": result,
                # Yanıttaki `dry_run` SUNUCUNUN söylediğidir; ekran onu okur ve
                # istediğimizden farklıysa "yazıldı" DEMEZ.
                "server_dry_run": bool(payload.get("dry_run"))
                if isinstance(payload, dict) else dry}

    async def void(self, invoice_id: int, *, reason: str, actor: str,
                   dry_run: bool | None, allow_void: bool = True) -> dict[str, Any]:
        """Belgeyi iptal eder. GERİ ALINAMAZ.

        İzin BURADA DA denetlenir (K9 — çift kapı): uç noktanın `requires`
        kapısını geçen bir isteğin ikinci kez sınanması, uç noktanın izni
        ileride yanlışlıkla gevşetildiğinde tek kalan kapıdır.

        İptal, bağlı dönem ödemesinin durumunu DEĞİŞTİRMEZ: belge ile tahsilat
        ayrı şeylerdir, belgeyi iptal etmek parayı geri vermez.
        """
        if not allow_void:
            await self._record(action="invoice.void", reason=reason, actor=actor,
                               invoice_id=int(invoice_id), result=doc.BLOCKED)
            return {"ok": False, "error": "Belge iptali için `bld_invoices.void` izni gerekir.",
                    "code": "forbidden"}
        guard = doc.reason_error(reason)
        if guard:
            return {"ok": False, "error": guard}

        dry = self._dry(dry_run)
        await self._record(action="invoice.void", reason=reason, actor=actor,
                           invoice_id=int(invoice_id), result=doc.TRIED,
                           detail={"dry_run": dry})
        try:
            payload = await self._api.void_invoice(int(invoice_id), reason=reason, actor=actor,
                                                   dry_run=dry)
        except Exception as failure:  # noqa: BLE001 — K7
            code = self._code(failure)
            await self._record(action="invoice.void", reason=reason, actor=actor,
                               invoice_id=int(invoice_id), result=doc.FAILED,
                               detail={"dry_run": dry, "error": code})
            return {"ok": False, "error": self._fail(failure), "code": code, "dry_run": dry}

        result = self._result(payload)
        await self._record(action="invoice.void", reason=reason, actor=actor,
                           invoice_id=int(invoice_id),
                           result=doc.DRY if dry else doc.DONE,
                           detail={"dry_run": dry,
                                   "invoice_no": doc.text(result.get("invoice_no"))})
        return {"ok": True, "error": "", "dry_run": dry, "data": result,
                "server_dry_run": bool(payload.get("dry_run"))
                if isinstance(payload, dict) else dry}

    # =============================================================== belge

    async def save_html(self, invoice_id: int, *, actor: str) -> dict[str, Any]:
        """Sunucunun yazdırılabilir HTML'ini diske yazar.

        NEDEN AYRI BİR YOL: sunucunun HTML'i belgenin RESMÎ biçimidir (iptal
        filigranı dâhil) ve tek dosyadır, dış bağımlılığı yoktur. Kabuk bir
        tarayıcı sekmesi açamadığı için dosya rapor klasörüne yazılır ve
        kullanıcı istediği yerde açar. Kâğıda basma yolu bundan AYRIDIR ve
        PDF üzerinden gider (CUPS bir HTML dosyasını güvenilir basmaz).
        """
        try:
            payload = await self._api.invoice_html(int(invoice_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure), "code": self._code(failure)}

        content = payload.get("content") if isinstance(payload, dict) else b""
        if not isinstance(content, bytes) or not content:
            text_body = payload.get("text") if isinstance(payload, dict) else ""
            content = str(text_body or "").encode("utf-8")
        if not content:
            return {"ok": False, "error": "Sunucu boş bir belge döndürdü; dosya yazılmadı."}

        card = await self.invoice(int(invoice_id))
        row = card.get("data") or {}
        name = doc.text(payload.get("filename")) if isinstance(payload, dict) else ""
        if not name:
            name = f"{doc.file_stem(row) or f'belge-{int(invoice_id)}'}.html"
        try:
            path = write_private(self._export_dir / name, content)
        except OSError as failure:
            return {"ok": False, "error": f"Dosya yazılamadı: {failure}"}

        digest = await self._archive_file(path=path, content=content, kind=doc.KIND_HTML,
                                          actor=actor, invoice_id=int(invoice_id),
                                          invoice_no=doc.text(row.get("invoice_no")))
        return {"ok": True, "error": "", "path": str(path), "name": path.name,
                "bytes": len(content), "sha256": digest}

    # ============================================================== raporlar

    async def preview(self, kind: str, params: dict[str, Any] | None = None, *,
                      actor: str = "") -> dict[str, Any]:
        """Belgeyi üretir ve sayfalarını görüntü olarak döner (kit `reportChain`)."""
        produced = await self.build_report(kind, params or {}, actor=actor)
        if not produced.get("ok"):
            return produced
        try:
            pages = await self._render_pages(Path(produced["path"]))
        except PreviewError as failure:
            return {**produced, "pages": [], "previewError": str(failure)}
        return {**produced, "pages": pages, "previewError": ""}

    async def build_report(self, kind: str, params: dict[str, Any], *,
                           actor: str = "") -> dict[str, Any]:
        if kind not in doc.REPORT_KINDS:
            return {"ok": False, "error": f"Bilinmeyen belge türü: {kind}"}
        if kind == "invoice":
            return await self._invoice_pdf(doc.as_int(params.get("invoice_id")), actor=actor)
        return await self._list_pdf(params, actor=actor)

    async def _invoice_pdf(self, invoice_id: int, *, actor: str) -> dict[str, Any]:
        """Tek belgenin A4 PDF'i — İÇERİK `snapshot_json`DAN gelir.

        Canlı tablodan üretilseydi aynı belge iki farklı zamanda iki farklı
        kâğıt verirdi; donmuş içerik bunun için var.
        """
        if invoice_id <= 0:
            return {"ok": False, "error": "Belge seçilmedi."}
        card = await self.invoice(invoice_id)
        if not card.get("connected"):
            return {"ok": False, "error": card.get("error") or "Belge okunamadı.",
                    "code": card.get("code", "")}
        row = card.get("data") or {}
        if not doc.as_int(row.get("id")):
            return {"ok": False, "error": "Belge bulunamadı."}

        # BAŞLIK VE ALT BAŞLIK DA KAÇIRILIR: `build_pdf` ikisini de reportlab'ın
        # mini XML'iyle çiziyor ve müşteri unvanındaki tek bir `&`, belgeyi
        # üretilemez kılardı (bölüm gövdeleri `pdf_sections` içinde kaçırılıyor).
        subtitle = doc.esc(" · ".join(part for part in (
            doc.text(row.get("customer_label")),
            doc.source_label(row),
            doc.moment(row.get("issued_at")),
        ) if part))
        title = doc.esc(
            f"Bilgi belgesi — {doc.text(row.get('invoice_no')) or f'#{invoice_id}'}")
        if row.get("status") == "void":
            title = f"İPTAL EDİLMİŞ — {title}"
        try:
            content = build_pdf(title=title, subtitle=subtitle,
                                sections=doc.pdf_sections(row),
                                # Dipnot HER SAYFANIN altında basılır; belgenin
                                # bir sayfası tek başına dolaşıma girse bile
                                # ibare onunla birlikte gider.
                                footer=doc.NOTICE)
        except ExportError as failure:
            return {"ok": False, "error": str(failure)}

        name = f"{doc.file_stem(row)}-{self._stamp()}.pdf"
        try:
            path = write_private(self._export_dir / name, content)
        except OSError as failure:
            return {"ok": False, "error": f"Dosya yazılamadı: {failure}"}
        digest = await self._archive_file(path=path, content=content, kind=doc.KIND_PDF,
                                          actor=actor, invoice_id=invoice_id,
                                          invoice_no=doc.text(row.get("invoice_no")))
        return {"ok": True, "error": "", "path": str(path), "name": name,
                "bytes": len(content), "sha256": digest,
                "status": doc.text(row.get("status"))}

    async def _list_pdf(self, params: dict[str, Any], *, actor: str) -> dict[str, Any]:
        """Süzgeçlenmiş belge listesinin dökümü.

        Sayfa sayfa toplanır ve tavana takılırsa döküm bunu YAZAR: eksik bir
        listeyi tam gibi göstermek, "toplam tutmuyor" diye saatler harcatır.
        """
        filters = self._filters(
            q=doc.text(params.get("q")), status=doc.text(params.get("status")),
            customer_id=doc.as_int(params.get("customer_id")),
            order_id=doc.as_int(params.get("order_id")),
            subscription_id=doc.as_int(params.get("subscription_id")),
            date_from=doc.text(params.get("date_from")),
            date_to=doc.text(params.get("date_to")))

        rows: list[dict[str, Any]] = []
        meta: dict[str, Any] = {}
        page = 1
        cap = self._list_report_rows
        while len(rows) < cap:
            try:
                payload = await self._api.invoices(page=page, per_page=LIST_PAGE, **filters)
            except Exception as failure:  # noqa: BLE001 — K7
                return {"ok": False, "error": self._fail(failure), "code": self._code(failure)}
            chunk = self._rows(payload)
            meta = self._meta(payload) or meta
            rows.extend(doc.invoice_row(item) for item in chunk)
            last = doc.as_int(meta.get("last_page"), page)
            if not chunk or page >= last:
                break
            page += 1
        truncated = len(rows) > cap
        rows = rows[:cap]

        if not rows:
            return {"ok": False, "error": "Bu süzgeçte belge yok; döküm üretilmedi."}

        label = ", ".join(f"{key}={value}" for key, value in filters.items() if value)
        try:
            content = build_pdf(
                title="Fatura belgeleri dökümü",
                subtitle=f"{len(rows)} belge · {self._stamp()}",
                sections=doc.list_sections(rows, meta=meta, filter_label=label,
                                           truncated=truncated),
                footer=doc.NOTICE)
        except ExportError as failure:
            return {"ok": False, "error": str(failure)}

        name = f"fatura-dokumu-{self._stamp()}.pdf"
        try:
            path = write_private(self._export_dir / name, content)
        except OSError as failure:
            return {"ok": False, "error": f"Dosya yazılamadı: {failure}"}
        digest = await self._archive_file(path=path, content=content, kind=doc.KIND_LIST,
                                          actor=actor)
        return {"ok": True, "error": "", "path": str(path), "name": name,
                "bytes": len(content), "sha256": digest, "rows": len(rows),
                "truncated": truncated}

    # ------------------------------------------------------- önizleme/baskı

    async def _render_pages(self, path: Path, *, max_pages: int = PREVIEW_PAGES,
                            dpi: int = 110) -> list[str]:
        """PDF sayfalarını görüntüye çevirir (`pdftoppm`).

        ÖNİZLEME GERÇEK DOSYANIN KENDİSİDİR: ekranda görülen ile yazıcıdan
        çıkan aynıdır. Araç yoksa belge yine yazılmıştır ve basılabilir.
        """
        binary = shutil.which("pdftoppm")
        if not binary:
            raise PreviewError(
                "Önizleme üretilemedi: `pdftoppm` yok (poppler-utils kurulmalı). "
                "Belge yine de kaydedildi ve yazdırılabilir.")
        with tempfile.TemporaryDirectory(prefix="km-fatura-onizleme-") as folder:
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
        """Üretilmiş belgeyi yazıcıya gönderir.

        GÜVENLİK: yalnız BİZİM rapor klasörümüzdeki dosya basılabilir. Serbest
        yol kabul etmek, `lp` ile makinedeki herhangi bir dosyayı kâğıda
        döktürmeye açık kapı bırakırdı.
        """
        if self._printer is None:
            return {"ok": False, "error": "Yazıcı yeteneği bu kurulumda yok; belge dosyası "
                                          "yine de üretildi ve klasörden açılabilir."}
        try:
            resolved = Path(path).expanduser().resolve(strict=True)
        except OSError:
            return {"ok": False, "error": "Basılacak belge bulunamadı."}
        allowed = self._export_dir.resolve()
        if not str(resolved).startswith(str(allowed) + os.sep):
            return {"ok": False,
                    "error": "Bu dosya rapor klasöründe değil; güvenlik gereği basılmaz."}
        count = max(1, min(20, int(copies)))
        try:
            result = await self._printer.print_file(resolved, title=resolved.name, copies=count)
        except Exception as failure:  # noqa: BLE001 — yazıcı dışarısı
            return {"ok": False, "error": self._fail(failure)}
        await self._mark_printed(resolved, count)
        return {"ok": True, **result, "name": resolved.name}

    async def printer_status(self) -> dict[str, Any]:
        if self._printer is None:
            return {"ready": False, "error": "Yazıcı yeteneği bu kurulumda yok."}
        try:
            return await self._printer.status()
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ready": False, "error": self._fail(failure)}
