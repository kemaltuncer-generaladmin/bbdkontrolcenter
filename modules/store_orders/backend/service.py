"""Siparişler — iş kuralları.

VERİ MAĞAZADADIR, KARAR BURADADIR. Sipariş `store.api` geçidinden gelir (K4);
bu modül Bagisto verisinin kopyasını tutmaz. Yerel tablolar yalnız mağazada
KARŞILIĞI OLMAYAN üç şeyi saklar: yazma gerekçesi (denetim izi), toplu işlem
önizlemesi (uygulanan şeyin önizlenen şey olduğunu kanıtlar) ve bu ekrana özel
tercihler (durum adları, sipariş no biçimi, iptal süresi).

İKİ OKUMA KİPİ. Mağazanın sipariş listesi ucu yalnız birkaç süzgeç uyguluyor
(durum, kanal, tarih, tutar). O süzgeçlerle yetiniliyorsa liste SUNUCU
TARAFINDA sayfalanır. Ödeme yöntemi, kargo firması, şehir, kupon ve anahtar
süzgeçler için küme TAVANLI olarak taranır ve süzme burada yapılır; ekran kaç
kayıt tarandığını ve tavan yakalandıysa bunu SÖYLER. Yarım listeyi tam
göstermek, hiç göstermemekten kötüdür.

ÜÇÜNCÜ KİP — DETAYLANDIRMA. Liste ucu SIĞ satır veriyor (canlıda doğrulandı):
fatura, gönderi, not, ara toplam, faturalanan tutar ve müşteri grubu YOK.
Bunlara dayanan bir süzgeç seçilirse taranan küme sipariş sipariş
`GET /orders/{id}` ile detaylandırılır. Pahalıdır (kayıt başına bir istek,
geçit dakikada 55 istekte tutuyor), bu yüzden iki fren vardır: `detail_cap`
ayarı ve `updatedAt` anahtarlı bellek önbelleği. Tavan yakalanırsa yanıt
`partial: True` döner ve ekran "bazı süzgeçler eksik uygulandı" der — sessiz
yanlış liste ÜRETİLMEZ.

UZAK SİSTEM DÜŞERSE EKRAN AYAKTA KALIR (K7): `connected: False` + `error`
döner. İstisna dışarı sızmaz.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from km_sdk import ExportError, build_pdf, csv_bytes, money, number, report_dir, write_private

from . import orders as ord_
from . import stages

#: Tarama tavanı. Canlıda birkaç düzine sipariş var; tavan büyümeye yer bırakır
#: ve bozuk `meta` yüzünden sonsuz sayfalamayı engeller. Tavan yakalanırsa
#: ekran "liste eksik olabilir" der — sessiz kırpma yapılmaz.
SCAN_CAP = 2_000

#: Tek taramada en çok kaç sipariş DETAYLANDIRILIR. Her detay bir HTTP isteği
#: ve geçit dakikada 55 istekte tutuyor; 300 kayıt en kötü durumda beş dakika
#: sürerdi. Canlıda 17 sipariş var, önbellekle ikinci tarama bedavaya gelir.
DETAIL_CAP = 300

# Toplu işlem türleri. "ship" KALDIRILDI: toplu kargoya verme Kargo
# Yönetimi sihirbazına devredildi ve orada sipariş sipariş yürür. Yanlış desi
# doğrudan yanlış faturadır ve tek onaylı toplu iş onu gizler.
BATCH_KINDS = ("invoice",)

#: Ödeme kanıtı önbelleğinin ömrü (saniye). Liste ve sayaç uçları arka arkaya
#: çağrılıyor; ikisinin de aynı iki listeyi çekmesi geçidin dakikalık payını
#: boşuna harcardı. Kısa tutulur ve HER YAZMADAN SONRA düşürülür: fatura kesen
#: kişi ekranı yenilediğinde "Ödenmedi" görmeye devam etmemeli.
EVIDENCE_TTL = 15.0

#: Kanıt listeleri YENİDEN ESKİYE istenir. Pencere ölçütü buna dayanır ve
#: `orders.window_floor` sırayı VERİDEN doğrular: Laravel tanımadığı parametreyi
#: sessizce yok sayar, isteği göndermek uygulandığını KANITLAMAZ.
EVIDENCE_SORT = {"sort": "id", "order": "desc"}

#: Tek sayfa. Uç 50'de kırpıyor; daha büyük istemek sessizce yarım sayfa
#: getirirdi. Sipariş başına istek atmamak (N+1) için tavan burada duruyor.
EVIDENCE_PAGE = 50

#: Sipariş durumu bu ekrandan değiştirildiğinde yayınlanan olay (manifest).
STATUS_EVENT = "store.order.status_changed"

#: Tek taramada en çok kaç siparişe aşama SMS'i denenir. Tarama zamanlanmış
#: iştir ve kimse başında durmaz; tavan olmadan bozuk bir pencere ayarı bütün
#: geçmişi tek koşuda müşterilere duyururdu. Tavan yakalanırsa sonuç
#: `truncated: True` döner ve ekran bunu YAZAR.
SWEEP_CAP = 200


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def _rma_status(item: dict[str, Any]) -> str:
    """İade talebinin durum ADI — nesne de metin de gelebilir.

    Mağazanın RMA ucu durumu ilişkili kayıt olarak açıyor
    (`{id, title, color}`); `str()` ile metne çevirmek ekrana sözlüğün Python
    yazımını basardı. `title` yoksa `name` denenir, o da yoksa boş dönülür:
    uydurma bir durum adı, "Durum okunamadı" boşluğundan daha yanıltıcıdır.
    """
    status = ord_.pick(item, "status")
    if isinstance(status, dict):
        return ord_.text(ord_.pick(status, "title", "name", "label"))
    return ord_.text(status)


class PreviewError(RuntimeError):
    """Önizleme görüntüsü üretilemedi. Rapor yine de kaydedilmiştir."""


class OrdersService:
    """Siparişler ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ."""

    def __init__(self, *, api: Any, store: Any, log: Any, config: dict[str, Any],
                 printer: Any = None, publish: Any = None, stage_notify: Any = None,
                 category: str = "Mağaza", subcategory: str = "Satış",
                 fallback_dir: Path | None = None) -> None:
        self._api = api
        self._store = store
        self._log = log
        self._config = config or {}
        self._printer = printer
        self._publish = publish
        # `store.notify.stage` — müşteriye giden aşama SMS'i. İSTEĞE BAĞLI:
        # Bildirimler ekranı kapalıysa sipariş akışı aynen çalışır, yalnız
        # müşteri bilgilendirilmez ve bu ekran nedenini söyler (K7).
        self._stage_notify = stage_notify
        self._category = category
        self._subcategory = subcategory
        self._fallback = fallback_dir or Path.home() / "km-raporlar"

        self._audit = store.table("audit")
        self._batch = store.table("batch")
        self._prefs = store.table("prefs")

        #: Detay önbelleği: siparişId → (updatedAt, kayıt). `updatedAt`
        #: değişince kayıt bayat sayılır, yani BAYAT VERİ GÖSTERİLMEZ; amaç
        #: aynı taramanın aynı siparişi ikinci kez çekmesini önlemek.
        self._details: dict[int, tuple[str, dict[str, Any]]] = {}

        #: Ödeme kanıtı (fatura + POS haritası) ve alındığı an.
        self._evidence_index: dict[str, Any] | None = None
        self._evidence_at = 0.0

    # ------------------------------------------------------------- ayarlar

    @property
    def _channel(self) -> str:
        """Kanal KODU — yalnız `configuration()` çağrısında kullanılır."""
        return str(self._config.get("channel") or "default")

    @property
    def _channel_id(self) -> int:
        """Sipariş listesi süzgecine giden kanal KİMLİĞİ. 0 = süzme yok.

        TUZAK — CANLIDA DOĞRULANDI: `/orders?channel=` kimlik bekliyor.
        `channel=default` sıfır kayıt döndürüyor ve Laravel hata vermiyor;
        kanal kodunu göndermek listeyi sessizce boşaltırdı. Kimlik verilmezse
        süzgeç HİÇ gönderilmez — tek kanallı mağazada doğru davranış budur.
        """
        return max(0, ord_.as_int(self._config.get("channel_id"), 0))

    @property
    def _detail_cap(self) -> int:
        return max(0, min(2_000, ord_.as_int(self._config.get("detail_cap"), DETAIL_CAP)))

    @property
    def _stage_dry_run(self) -> bool:
        """Aşama SMS'i freninin ÜÇÜNCÜ katmanı — tetikleyicinin kendi bayrağı.

        Diğer ikisi Bildirimler tarafındadır (`platform.notify.sms.dry_run` ve
        `lifecycle_sms_dry_run`). Üçü de kapanmadan tek bir gerçek SMS çıkmaz.
        Varsayılan AÇIK: bir siparişi kargoya vermek, müşteriye mesaj göndermeyi
        de kapsamamalı — o karar ayrıca verilir.
        """
        return bool(self._config.get("stage_sms_dry_run", True))

    @property
    def _stage_lookback(self) -> int:
        """"Siparişiniz alındı" taramasının gün penceresi. 0 = pencere yok.

        Pencere olmasaydı aşama SMS'i ilk açıldığında tarama geçmişteki bütün
        siparişleri "yeni" görürdü; canlıda 1.800 müşteriye "siparişiniz alındı"
        gitmesi geri alınamaz.
        """
        return max(0, ord_.as_int(self._config.get("stage_sms_lookback_days"), 3))

    def _base_filters(self, filters: dict[str, Any]) -> dict[str, Any]:
        """Mağazaya GERÇEKTEN gönderilecek süzgeçler."""
        base = dict(ord_.store_filters(filters))
        if self._channel_id:
            base["channel"] = self._channel_id
        return base

    @property
    def _page_size(self) -> int:
        return max(10, min(200, ord_.as_int(self._config.get("page_size"), 50)))

    @property
    def _scan_cap(self) -> int:
        return max(100, min(SCAN_CAP, ord_.as_int(self._config.get("scan_cap"), SCAN_CAP)))

    @property
    def _export_dir(self) -> Path:
        # HER ÇAĞRIDA yeniden çözülür: ay değişince klasör kendiliğinden değişir.
        return report_dir(self._category, subcategory=self._subcategory,
                          fallback=self._fallback,
                          configured=str(self._config.get("export_path") or ""))

    async def _prefs_view(self) -> dict[str, Any]:
        """Ekran tercihleri: yerel kayıt varsa o, yoksa modül ayarı.

        Tercihler mağazayı ETKİLEMEZ. Durum adı yalnız bu ekranda görünen
        yazıyı, sipariş no biçimi yalnız gösterimi, iptal süresi yalnız bu
        ekrandan yapılan iptali sınırlar.
        """
        stored = await self._all_prefs()
        names = stored.get("status_names")
        try:
            parsed = json.loads(names) if names else {}
        except ValueError:
            parsed = {}
        return {
            "statusNames": ord_.clean_status_names(parsed or self._config.get("status_names")),
            "orderNoFormat": stored.get("order_no_format")
            or str(self._config.get("order_no_format") or "#{no}"),
            "cancelWindowHours": ord_.as_int(
                stored.get("cancel_window_hours",
                           self._config.get("cancel_window_hours")), 0),
            "lateDays": max(1, ord_.as_int(
                stored.get("late_days", self._config.get("late_days")), 3)),
        }

    # ------------------------------------------------------ yerel tablolar

    async def _all_prefs(self) -> dict[str, str]:
        try:
            rows = await self._store.fetch_all(f"SELECT key, value FROM {self._prefs}")
        except Exception as failure:  # noqa: BLE001 — tercih okunamadı, varsayılan yeter
            self._log.warning("tercih okunamadı", error=str(failure))
            return {}
        return {str(row["key"]): str(row["value"]) for row in rows}

    async def _set_pref(self, key: str, value: str, actor: str) -> None:
        await self._store.execute(
            f"INSERT INTO {self._prefs} (key, value, actor, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, actor = excluded.actor, "
            "updated_at = excluded.updated_at",
            (key, value, actor, _now()),
        )

    async def _record(self, *, order_id: int, action: str, reason: str, actor: str,
                      result: str, detail: Any = None) -> None:
        """Yerel denetim izi. Bagisto denetim tutuyor ama GEREKÇEYİ tutmuyor;
        ayrıca ağ koparsa "ne yapmaya çalıştık" kaydı yalnız burada kalır."""
        try:
            await self._store.execute(
                f"INSERT INTO {self._audit} "
                "(order_id, action, reason, actor, result, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (int(order_id or 0), action, reason, actor, result,
                 json.dumps(detail or {}, ensure_ascii=False), _now()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın
            self._log.warning("denetim izi yazılamadı", action=action, error=str(failure))

    # ------------------------------------------------------------- yardımcı

    @staticmethod
    def _fail(failure: Exception) -> str:
        message = str(failure).strip()
        return message or "Mağazaya ulaşılamadı."

    @staticmethod
    def _guard(reason: str) -> str:
        """Gerekçe backend'de DE doğrulanır (K9): arayüzde gizlemek yetmez."""
        return ord_.reason_error(reason)

    def _rows(self, raw: list[Any], prefs: dict[str, Any]) -> list[dict[str, Any]]:
        today = ord_.today_iso()
        return [ord_.order_row(item, status_names=prefs["statusNames"],
                               no_format=prefs["orderNoFormat"], today=today,
                               late_days=prefs["lateDays"])
                for item in raw if isinstance(item, dict)]

    async def _chosen_carriers(self) -> dict[str, str]:
        """Sipariş no → müşterinin checkout'ta seçtiği kargo firması. TEK İSTEK.

        Sipariş listesi ucu bu alanı VERMİYOR; `bbd/orders` veriyor. Sipariş
        başına detay okumak 50 satırlık sayfada 50 istek ederdi.

        Patlarsa liste AYAKTA kalır ve taşıyıcı sütunu bugünkü gibi (yalnız
        gönderiden) doldurulur — eksik sütun, açılmayan listeden iyidir (K7).
        """
        try:
            payload = await self._api.bbd_orders(page=1, per_page=self._page_size)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("seçilen kargo firmaları okunamadı", error=str(failure))
            return {}
        return ord_.chosen_carriers(payload.get("items") or [])

    async def _announce(self, payload: dict[str, Any]) -> None:
        """Durum değişikliğini olay yoluna bırakır (K3).

        Yayın BAŞARISIZ OLSA BİLE iş başarılıdır: sipariş mağazada iptal
        edilmiştir, dinleyicinin patlaması onu geri getirmez.
        """
        if self._publish is None:
            return
        try:
            await self._publish(STATUS_EVENT, payload)
        except Exception as failure:  # noqa: BLE001 — dinleyici bizi düşürmez (K7)
            self._log.warning("olay yayınlanamadı", event=STATUS_EVENT, error=str(failure))

    async def _detail(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Sığ liste satırını detay kaydına çevirir; önbellek `updatedAt` ile.

        Detay okunamazsa SIĞ SATIR geri döner: bir siparişin okunamaması bütün
        listeyi düşürmemeli (K7). O satır `detailed: False` kalır ve detay
        isteyen süzgeçlerin dışında bırakılır.
        """
        order_id = ord_.as_int(ord_.pick(raw, "id"))
        if not order_id:
            return raw
        stamp = ord_.text(ord_.pick(raw, "updated_at"))
        cached = self._details.get(order_id)
        if cached and cached[0] == stamp:
            return cached[1]
        try:
            full = await self._api.order(order_id)
        except Exception as failure:  # noqa: BLE001 — biri patlarsa gerisi sürsün (K7)
            self._log.warning("sipariş detayı okunamadı", orderId=order_id, error=str(failure))
            return raw
        if not isinstance(full, dict) or not ord_.has_detail(full):
            return raw
        if len(self._details) > 4_000:      # sınırsız büyümesin
            self._details.clear()
        self._details[order_id] = (stamp, full)
        return full

    # ------------------------------------------------------- ödeme kanıtı

    def _drop_evidence(self) -> None:
        """Yazmadan sonra kanıt bayattır: fatura kesildi, POS durumu değişti."""
        self._evidence_index = None
        self._evidence_at = 0.0

    @staticmethod
    def _complete(payload: dict[str, Any], items: list[Any]) -> bool:
        """Bu tek sayfa kaynağın TAMAMI mı? Değilse "kayıt yok" sonucu çıkarılamaz."""
        meta = payload.get("meta") or {}
        total = ord_.as_int(meta.get("total"), -1)
        if total < 0:
            # `meta` gelmediyse dolu sayfa "devamı var" demektir.
            return len(items) < EVIDENCE_PAGE
        return not payload.get("truncated") and total <= len(items)

    async def _evidence(self) -> dict[str, Any]:
        """Ödeme kanıtı — İKİ LİSTE İSTEĞİ, sipariş başına istek YOK (N+1 yasak).

        Bagisto'nun sipariş listesi ucu ödeme DURUMU taşımıyor, yalnız ödeme
        YÖNTEMİ. Bilgi başka yerde duruyor ve ikisi birbirini doğruluyor:
        faturanın `state` alanı ile POS denemesinin `state` alanı.

        BİR KAYNAK PATLARSA DİĞERİ ÇALIŞIR (K7): eksik kaynak `…Ok: False`
        olarak işaretlenir ve o eksiklik ekranda yazılır. Kanıt hiç
        toplanamazsa satırlar bugünkü davranışta kalır ("Bilinmiyor"), sessizce
        "Ödenmedi" yazılmaz.
        """
        now = time.monotonic()
        if self._evidence_index is not None and now - self._evidence_at < EVIDENCE_TTL:
            return self._evidence_index

        invoices: list[dict[str, Any]] = []
        attempts: list[dict[str, Any]] = []
        warnings: list[str] = []
        invoice_ok = attempt_ok = True
        invoice_complete = attempt_complete = True

        try:
            payload = await self._api.invoices(dict(EVIDENCE_SORT),
                                               per_page=EVIDENCE_PAGE)
            invoices = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
            invoice_complete = self._complete(payload, invoices)
        except Exception as failure:  # noqa: BLE001 — kanıt eksik kalır, ekran kalkar (K7)
            invoice_ok = False
            warnings.append(f"fatura listesi okunamadı ({self._fail(failure)})")
            self._log.warning("ödeme kanıtı: fatura listesi okunamadı", error=str(failure))

        try:
            payload = await self._api.bbd_payment_attempts({}, per_page=EVIDENCE_PAGE)
            attempts = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
            attempt_complete = self._complete(payload, attempts)
        except Exception as failure:  # noqa: BLE001 — POS ucu yayında olmayabilir
            attempt_ok = False
            warnings.append(f"sanal POS denemeleri okunamadı ({self._fail(failure)})")
            self._log.info("ödeme kanıtı: POS denemeleri okunamadı", error=str(failure))

        index = ord_.payment_index(invoices, attempts)
        index.update({
            "invoiceOk": invoice_ok, "attemptOk": attempt_ok,
            "invoiceComplete": invoice_complete, "attemptComplete": attempt_complete,
            "invoiceRead": len(invoices), "attemptRead": len(attempts),
            "warnings": warnings,
        })
        if index["unboundInvoices"]:
            # Fatura kaydında `orderId` NULL geliyor ve bağ `orderIncrementId`
            # üzerinden kuruluyor. İkisi de yoksa fatura hiçbir siparişe
            # bağlanamaz; SESSİZ KALMAK herkesi "Ödenmedi" gösterirdi.
            index["warnings"].append(
                f"{index['unboundInvoices']} fatura siparişe bağlanamadı "
                "(`orderIncrementId` ve `orderId` boş geldi)")
        self._evidence_index = index
        self._evidence_at = now
        return index

    async def _paid_rows(self, rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]],
                                                                   dict[str, Any]]:
        """Satırların ödeme durumunu kanıtla yeniden çözer."""
        index = await self._evidence()
        return [ord_.with_payment(row, index) for row in rows], index

    @staticmethod
    def _evidence_view(index: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Ekranın kanıt şeridi: hangi kaynak okundu, kaç satır bankaya sorulmalı."""
        return {
            "invoiceOk": bool(index.get("invoiceOk")),
            "posOk": bool(index.get("attemptOk")),
            "complete": bool(index.get("invoiceComplete") and index.get("attemptComplete")),
            "invoiceRead": ord_.as_int(index.get("invoiceRead")),
            "posRead": ord_.as_int(index.get("attemptRead")),
            "invoiceFloorDay": ord_.text(index.get("invoiceFloorDay")),
            "posFloorDay": ord_.text(index.get("attemptFloorDay")),
            "warnings": list(index.get("warnings") or []),
            "uncertain": sum(1 for row in rows if row.get("paymentAttention")),
        }

    async def _scan(self, filters: dict[str, Any], prefs: dict[str, Any], *,
                    detail: bool = False) -> tuple[list[dict[str, Any]], bool, bool,
                                                   dict[str, Any]]:
        """Tavanlı tam tarama. Mağazanın uyguladığı süzgeçler ÖNCE gönderilir;
        taranan küme böylece mümkün olduğunca küçülür.

        Dönen dörtlü: satırlar · tarama tavanı yakalandı mı · DETAY tavanı
        yakalandı mı (`partial`) · ödeme kanıtı haritası.

        Ödeme kanıtı taramanın BÜYÜKLÜĞÜNDEN bağımsızdır: iki liste isteği,
        kaç sipariş taranırsa taransın. Süzme (`matches`) kanıtla çözülmüş
        satır üzerinde yapılır — yoksa "ödeme durumu" süzgeci sığ satırlarda
        hep boş dönerdi.
        """
        payload = await self._api.orders(self._base_filters(filters), all_pages=True)
        raw = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
        truncated = bool(payload.get("truncated")) or len(raw) > self._scan_cap
        raw = raw[: self._scan_cap]

        partial = False
        if detail or ord_.needs_detail(filters):
            cap = self._detail_cap
            partial = len(raw) > cap
            # SIRAYLA: geçit zaten dakikada 55 istekte tutuyor, paralel
            # çekmek kantin deneyiminde 5xx üretmişti.
            raw = [await self._detail(item) for item in raw[:cap]] + raw[cap:]
        rows, index = await self._paid_rows(self._rows(raw, prefs))
        ord_.apply_chosen_carrier(rows, await self._chosen_carriers())
        return rows, truncated, partial, index

    # ================================================================ liste

    async def orders(self, *, filters: dict[str, Any] | None = None, page: int = 1,
                     size: int = 0) -> dict[str, Any]:
        """Sipariş listesi. Süzgeç kümesine göre sunucu sayfalar ya da taranır."""
        prefs = await self._prefs_view()
        wanted = dict(filters or {})
        wanted.setdefault("today", ord_.today_iso())
        per_page = size or self._page_size
        scanned = ord_.needs_scan(wanted)

        empty = {"ok": True, "items": [], "total": 0, "page": page, "size": per_page,
                 "pages": 0, "scanned": scanned, "truncated": False, "partial": False,
                 "summary": ord_.summary([]), "prefs": prefs,
                 "evidence": self._evidence_view(ord_.empty_index(), [])}

        try:
            if scanned:
                rows, truncated, partial, index = await self._scan(wanted, prefs)
                hits = [row for row in rows if ord_.matches(row, wanted)]
                start = max(0, (page - 1) * per_page)
                window = hits[start:start + per_page]
                return {**empty, "connected": True, "error": "", "items": window,
                        "total": len(hits), "pages": max(1, -(-len(hits) // per_page)),
                        "truncated": truncated, "partial": partial, "scannedCount": len(rows),
                        "summary": ord_.summary(hits),
                        "evidence": self._evidence_view(index, hits)}

            payload = await self._api.orders(self._base_filters(wanted), page=page,
                                             per_page=per_page)
            rows, index = await self._paid_rows(self._rows(payload.get("items") or [], prefs))
            ord_.apply_chosen_carrier(rows, await self._chosen_carriers())
        except Exception as failure:  # noqa: BLE001 — ekran ayakta kalmalı (K7)
            self._log.warning("sipariş listesi okunamadı", error=str(failure))
            return {**empty, "connected": False, "error": self._fail(failure),
                    "scannedCount": 0}

        meta = payload.get("meta") or {}
        return {
            **empty, "connected": True, "error": "", "items": rows,
            "evidence": self._evidence_view(index, rows),
            "total": ord_.as_int(meta.get("total"), len(rows)),
            "page": ord_.as_int(meta.get("currentPage"), page),
            "size": ord_.as_int(meta.get("perPage"), per_page),
            "pages": ord_.as_int(meta.get("lastPage"), 1),
            "scannedCount": len(rows),
            # Sayfa toplamı SAYFANIN toplamıdır; süzgecin tamamının cirosu
            # `/summary` ucundan gelir. İkisini karıştırmak, 29 sayfalık bir
            # süzgecin cirosunu ilk sayfanınki sanmak olurdu.
            "summary": ord_.summary(rows),
        }

    async def overview(self, *, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """Çip sayaçları + süzgecin TAMAMININ mini toplamı. Tavanlı tarama."""
        prefs = await self._prefs_view()
        wanted = dict(filters or {})
        wanted.setdefault("today", ord_.today_iso())
        # "Kargoda" ve "Geciken" çipleri gönderi durumuna bakıyor; sığ satırla
        # sayaçlar sıfır çıkardı. Sayaç şeridi bu yüzden HER ZAMAN detay ister.
        try:
            rows, truncated, partial, index = await self._scan(wanted, prefs, detail=True)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("sipariş sayaçları okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "counts": {}, "summary": ord_.summary([]), "truncated": False,
                    "partial": False, "scannedCount": 0,
                    "evidence": self._evidence_view(ord_.empty_index(), [])}

        # Çip sayaçları çipin KENDİSİ hariç süzgeçlere göre hesaplanır: bir çipe
        # basınca diğer çiplerin sayacının sıfırlanması, kullanıcıyı seçimini
        # kaldırmadan başka çipe geçemez hâle getirirdi.
        without_chip = {**wanted, "chip": ""}
        base = [row for row in rows if ord_.matches(row, without_chip)]
        return {
            "ok": True, "connected": True, "error": "",
            "counts": ord_.chip_counts(base, wanted.get("today")),
            "summary": ord_.summary([row for row in base if ord_.matches(row, wanted)]),
            "truncated": truncated, "partial": partial, "scannedCount": len(rows),
            "evidence": self._evidence_view(index, base),
        }

    # ================================================================ künye

    async def card(self, order_id: int) -> dict[str, Any]:
        """Sipariş künyesi: özet, kalemler, ödeme, gönderi, fatura, iade.

        `store.order.card` yeteneğinin de gövdesidir — Müşteriler ve Talepler
        ekranları bu yanıtı okur.
        """
        prefs = await self._prefs_view()
        try:
            raw = await self._api.order(int(order_id))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("sipariş okunamadı", orderId=order_id, error=str(failure))
            return {"ok": False, "connected": False, "error": self._fail(failure)}

        # Çekmece de kanıtla çözülmüş durumu gösterir: listede "Belirsiz" görüp
        # açınca "Ödenmedi" okumak, iki ekranın birbirini yalanlaması olurdu.
        rows, index = await self._paid_rows(self._rows([raw], prefs))
        if not rows:
            return {"ok": False, "connected": True, "error": "Sipariş kaydı boş döndü."}
        row = rows[0]

        warnings: list[str] = []
        transactions: list[dict[str, Any]] = []
        try:
            payload = await self._api.transactions({"order_id": int(order_id)})
            transactions = [item for item in (payload.get("items") or [])
                            if isinstance(item, dict)]
        except Exception as failure:  # noqa: BLE001 — parça parça hata (K7)
            warnings.append(f"ödeme işlemleri: {self._fail(failure)}")

        # CANLIDA DOĞRULANDI (2026-08-16): `GET /api/admin/bbd/return-requests`
        # 200 dönüyor ve gerçek RMA kayıtları geliyor. Bir dönem bu uç yayında
        # değildi ve ekran bölümü "uç yayınlanmadı" diye kapatıyordu; artık öyle
        # değil. Aşağıdaki `except` dalı YİNE DE DURUYOR (K7): uç bir gün geri
        # çekilirse künye ayakta kalmalı, sipariş kartı iade yüzünden çökmemeli.
        #
        # TUZAK — UÇ `order_id` SÜZGECİNİ UYGULAMIYOR. Denetleyici
        # (`ReturnRequestController::applyFilters`) yalnız `status`, `from`, `to`
        # okur; `order_id` sessizce yok sayılır ve uç BÜTÜN talepleri döndürür.
        # Süzmeyi yerelde yapmayan kod, 20 numaralı siparişin künyesinde 9 ve 11
        # numaralı siparişlerin taleplerini gösterirdi — hata da vermeden.
        # Parametre yine gönderiliyor: uç bir gün süzgeci öğrenirse istek
        # kendiliğinden ucuzlar, yerel süzgeç de zararsız kalır.
        returns: list[dict[str, Any]] = []
        returns_available = True
        try:
            payload = await self._api.bbd_return_requests({"order_id": int(order_id)})
            returns = [item for item in (payload.get("items") or [])
                       if isinstance(item, dict)
                       and ord_.as_int(ord_.pick(item, "order_id")) == int(order_id)]
        except Exception as failure:  # noqa: BLE001 — uç geri çekilebilir (K7)
            returns_available = False
            warnings.append(f"iade talepleri: {self._fail(failure)}")

        return {
            "ok": True, "connected": True, "error": "", "warnings": warnings,
            "order": row,
            "items": ord_.item_rows(raw),
            "money": ord_.money_view(raw),
            "billing": ord_.address_view(ord_.address_of(raw, "billing")),
            "shipping": ord_.address_view(ord_.address_of(raw, "shipping")),
            "shipments": ord_.shipment_rows(raw),
            "invoices": ord_.invoice_rows(raw),
            "refunds": ord_.refund_rows(raw),
            "transactions": [{
                "id": ord_.as_int(ord_.pick(item, "id")),
                # Canlıda `type` ödeme sağlayıcısının kodu (`kuveytturk`);
                # `paymentTitle` insan okuyabilir olanıdır.
                "type": ord_.text(ord_.pick(item, "payment_title", "type")),
                "status": ord_.text(ord_.pick(item, "status")),
                "amount": ord_.kurus(ord_.pick(item, "amount")),
                "createdAt": ord_.text(ord_.pick(item, "created_at"))[:19],
            } for item in transactions],
            "returns": [{
                "id": ord_.as_int(ord_.pick(item, "id")),
                # TUZAK — CANLIDA DOĞRULANDI (2026-08-16): `status` DÜZ METİN
                # DEĞİL, `{id, title, color}` nesnesi geliyor. Doğrudan metne
                # çeviren kod ekrana `{'id': 5, 'title': 'İade Edildi', ...}`
                # yazardı: hata yok, sadece okunamayan bir hücre. Metin de
                # gelebilir (uç sözleşmesi değişirse) — iki biçim de çözülür.
                "status": _rma_status(item),
                "reason": ord_.text(ord_.pick(item, "reason")),
                "createdAt": ord_.text(ord_.pick(item, "created_at"))[:19],
            } for item in returns],
            "returnsAvailable": returns_available,
            "cancelBlock": ord_.cancel_block(row, window_hours=prefs["cancelWindowHours"]),
            "evidence": self._evidence_view(index, rows),
            "prefs": prefs,
        }

    async def comments(self, order_id: int) -> dict[str, Any]:
        """Sipariş notları ve müşteri yazışması."""
        try:
            payload = await self._api.order_comments(int(order_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": True, "connected": False, "error": self._fail(failure), "items": []}
        return {"ok": True, "connected": True, "error": "", "items": [{
            "id": ord_.as_int(ord_.pick(item, "id")),
            "comment": ord_.text(ord_.pick(item, "comment")),
            "notified": bool(ord_.as_int(ord_.pick(item, "customer_notified"), 0)),
            "createdAt": ord_.text(ord_.pick(item, "created_at"))[:19],
        } for item in (payload.get("items") or []) if isinstance(item, dict)]}

    async def reference(self) -> dict[str, Any]:
        """Süzgeçlerin beslendiği referans listeler. Geçit 15/30 dk önbellekli."""
        out: dict[str, Any] = {
            "ok": True, "connected": True, "error": "",
            "statuses": [{"value": code, "label": label}
                         for code, label in ord_.STATUS_LABELS.items()],
            "paymentStates": [{"value": code, "label": label}
                              for code, label in ord_.PAYMENT_LABELS.items()],
            "dateFields": [{"value": code, "label": label}
                           for code, label in ord_.DATE_FIELDS.items()],
            "channels": [], "customerGroups": [], "carriers": [], "stale": False,
        }
        try:
            snapshot = await self._api.snapshot()
        except Exception as failure:  # noqa: BLE001 — K7
            out["connected"] = False
            out["error"] = self._fail(failure)
            return out

        parts = snapshot.get("parts") or {}
        out["channels"] = [{"code": ord_.text(ord_.pick(item, "code")),
                            "name": ord_.text(ord_.pick(item, "name"))}
                           for item in parts.get("channels") or [] if isinstance(item, dict)]
        out["customerGroups"] = [{"id": ord_.as_int(ord_.pick(item, "id")),
                                  "name": ord_.text(ord_.pick(item, "name"))}
                                 for item in parts.get("customer_groups") or []
                                 if isinstance(item, dict)]
        out["stale"] = bool(snapshot.get("stale"))
        out["storedAt"] = ord_.text(snapshot.get("storedAt"))

        try:
            carriers = await self._api.bbd_carriers()
            out["carriers"] = [{"code": ord_.text(ord_.pick(item, "code")),
                                "name": ord_.text(ord_.pick(item, "name", "title"))}
                               for item in (carriers.get("items") or [])
                               if isinstance(item, dict)]
        except Exception as failure:  # noqa: BLE001 — BBD ucu henüz yayında olmayabilir
            # Taşıyıcı listesi gelmezse kargo firması süzgeci SERBEST METİN
            # olarak çalışmaya devam eder; ekran boş bir açılır kutu göstermez.
            self._log.info("taşıyıcı listesi alınamadı", error=str(failure))
        return out

    async def audit(self, *, order_id: int = 0, limit: int = 50) -> dict[str, Any]:
        """Bu ekrandan yapılan yazmaların YEREL izi (gerekçeleriyle)."""
        sql = (f"SELECT order_id, action, reason, actor, result, created_at "
               f"FROM {self._audit} ")
        params: tuple[Any, ...] = ()
        if order_id:
            sql += "WHERE order_id = ? "
            params = (int(order_id),)
        sql += "ORDER BY id DESC LIMIT ?"
        params = (*params, max(1, min(500, int(limit))))
        try:
            rows = await self._store.fetch_all(sql, params)
        except Exception as failure:  # noqa: BLE001 — iz okunamadı, ekran dursun
            return {"ok": True, "items": [], "error": self._fail(failure)}
        return {"ok": True, "error": "", "items": [
            {"orderId": row["order_id"], "action": row["action"], "reason": row["reason"],
             "actor": row["actor"], "result": row["result"], "createdAt": row["created_at"]}
            for row in rows
        ]}

    # =============================================================== yazma

    async def add_comment(self, order_id: int, *, comment: str, notify: bool, reason: str,
                          actor: str, dry_run: bool = True) -> dict[str, Any]:
        """Sipariş notu. `notify=True` MÜŞTERİYE E-POSTA GÖNDERİR — iç yazışma
        değildir; ekran bunu her seferinde yazar."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        body = ord_.text(comment)
        if len(body) < 2:
            return {"ok": False, "error": "Not boş olamaz."}

        await self._record(order_id=order_id, action="add_comment", reason=reason, actor=actor,
                           result="denendi", detail={"notify": bool(notify)})
        try:
            result = await self._api.add_order_comment(int(order_id), comment=body,
                                                       notify=bool(notify), reason=reason,
                                                       actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(order_id=order_id, action="add_comment", reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(order_id=order_id, action="add_comment", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok", detail={"notify": bool(notify)})
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run)),
                "notified": bool(notify)}

    async def cancel(self, order_id: int, *, reason: str, actor: str,
                     dry_run: bool = True) -> dict[str, Any]:
        """Siparişi iptal eder. GERİ ALINAMAZ (TUZAK 8).

        Sipariş yazmadan ÖNCE taze okunur: aradan geçen sürede kargolanmış ya da
        zaten iptal edilmiş olabilir. Pencere ve durum denetimi burada da yapılır
        — arayüzde düğmeyi gizlemek yetkilendirme değildir (K9).
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        prefs = await self._prefs_view()
        try:
            raw = await self._api.order(int(order_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}

        rows = self._rows([raw], prefs)
        if not rows:
            return {"ok": False, "error": "Sipariş okunamadı."}
        block = ord_.cancel_block(rows[0], window_hours=prefs["cancelWindowHours"])
        if block:
            await self._record(order_id=order_id, action="cancel", reason=reason, actor=actor,
                               result="engellendi", detail={"block": block})
            return {"ok": False, "error": block}

        await self._record(order_id=order_id, action="cancel", reason=reason, actor=actor,
                           result="denendi", detail={"orderNo": rows[0]["orderNo"]})
        try:
            result = await self._api.cancel_order(int(order_id), reason=reason, actor=actor,
                                                  dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(order_id=order_id, action="cancel", reason=reason, actor=actor,
                               result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(order_id=order_id, action="cancel", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok")
        if not dry_run:
            self._drop_evidence()
        applied = bool(result.get("sent", not dry_run)) and not dry_run
        if applied:
            # Kuru provada olay YAYINLANMAZ: mağazada hiçbir şey değişmedi,
            # dinleyicileri "sipariş iptal edildi" diye uyandırmak yalan olurdu.
            await self._announce({"orderId": int(order_id), "from": rows[0]["status"],
                                  "to": "canceled", "reason": reason})
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run)),
                "orderNo": rows[0]["orderNo"], "announced": applied}

    async def invoice(self, order_id: int, *, items: dict[str, int] | None, reason: str,
                      actor: str, dry_run: bool = True) -> dict[str, Any]:
        """Fatura keser. `items` boşsa siparişin TAMAMI faturalanır."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        clean = {str(key): max(0, ord_.as_int(value))
                 for key, value in (items or {}).items() if ord_.as_int(key)}

        await self._record(order_id=order_id, action="invoice", reason=reason, actor=actor,
                           result="denendi", detail={"items": clean})
        try:
            result = await self._api.create_invoice(int(order_id), items=clean or None,
                                                    reason=reason, actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(order_id=order_id, action="invoice", reason=reason, actor=actor,
                               result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(order_id=order_id, action="invoice", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok", detail={"items": clean})
        if not dry_run:
            # Fatura kesildi: ödeme kanıtı bayat. Yenilenen ekran "Ödenmedi"
            # görmeye devam ederse kullanıcı işini yapmadığını sanır.
            self._drop_evidence()
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run)),
                "partial": bool(clean)}

    # `ship()` KALDIRILDI — kargoya verme bu modülün işi DEĞİLDİR.
    #
    # KULLANICININ KURALI: "her şey — mesela sipariş kargoya mı verilecek —
    # farklı yerde 'kargoya ver' olmasın."
    #
    # Metot Bagisto'nun KENDİ gönderi kaydını açıyordu: etiket satın
    # alınmıyor, taşıyıcıya hiç gidilmiyor, paket yola çıkmıyordu. Üstelik
    # gövdeyi `{"shipment": {…}}` sarmalıyla gönderdiği için canlı işlemci
    # `source` ve `items`ı KÖKTEN okuyup boş buluyor ve isteği reddediyordu —
    # yani muhtemelen tek satır bile yazmıyordu. Buna karşılık müşteriye
    # "kargoya verildi" SMS'ini tetikliyordu.
    #
    # Gerçek zincir (gönderi aç → teklif al → müşterinin ödediği firmayı
    # yeğle → etiket SATIN AL → takip numarasını yaz → etiket ve faturayı bas
    # → müşteriye SMS) Kargo Yönetimi'nde koşar. Aşama SMS'inin sahibi de
    # oraya geçti: gerçek takip numarası yalnız orada üretiliyor.
    #
    # `_after_ship` de bu yüzden kaldırıldı; "sipariş alındı" ve "teslim
    # edildi" taramaları (`stage_sweep`) BU MODÜLDE KALIR — onların kaynağı
    # bir tıklama değil, mağazanın durumudur.

    # ========================================================= toplu işlem

    async def batch_preview(self, *, kind: str, order_ids: list[int]) -> dict[str, Any]:
        """FARK TABLOSU. Toplu iş bu tablo gösterilmeden UYGULANMAZ.

        Önizleme yerel tabloya yazılır ve bir jeton döner; uygulama o jetonla
        gelir. Böylece uygulanan şeyin önizlenen şey olduğu kanıtlanır.
        """
        if kind not in BATCH_KINDS:
            return {"ok": False, "error": f"Bilinmeyen toplu işlem: {kind}"}
        ids = [int(item) for item in (order_ids or []) if ord_.as_int(item)]
        if not ids:
            return {"ok": False, "error": "Sipariş seçilmedi."}
        if len(ids) > 200:
            return {"ok": False,
                    "error": "Tek seferde en çok 200 sipariş. Daha büyük iş için süzgeci daraltın."}

        prefs = await self._prefs_view()
        raws: list[dict[str, Any]] = []
        missing: list[int] = []
        for order_id in ids:
            try:
                raw = await self._api.order(order_id)
            except Exception as failure:  # noqa: BLE001 — K7
                missing.append(order_id)
                self._log.warning("önizleme için sipariş okunamadı", orderId=order_id,
                                  error=str(failure))
                continue
            raws.append(raw)

        # Önizleme de kanıtla çözülmüş ödeme durumuna bakar: listede "Belirsiz"
        # diye seçtirilmeyen sipariş, önizlemede sessizce "uygun" görünmemeli.
        rows, _ = await self._paid_rows(self._rows(raws, prefs))
        if not rows:
            return {"ok": False, "error": "Seçilen siparişlerin hiçbiri okunamadı."}

        diff = ord_.batch_rows(rows, kind)
        token = uuid.uuid4().hex
        try:
            await self._store.execute(
                f"INSERT INTO {self._batch} (token, kind, params, rows, status, created_at) "
                "VALUES (?, ?, ?, ?, 'preview', ?)",
                (token, kind, json.dumps({"ids": ids}, ensure_ascii=False),
                 json.dumps(diff, ensure_ascii=False), _now()),
            )
        except Exception as failure:  # noqa: BLE001 — jeton yazılamazsa uygula reddedilir
            self._log.warning("önizleme kaydedilemedi", error=str(failure))
            return {"ok": False, "error": "Önizleme kaydedilemedi; uygulama açılmadı."}

        return {"ok": True, "error": "", "token": token, "kind": kind, "rows": diff,
                "summary": ord_.batch_summary(diff), "missing": missing}

    async def batch_apply(self, *, token: str, reason: str, actor: str,
                          dry_run: bool = True, carrier: str = "") -> dict[str, Any]:
        """Önizlenen listeyi uygular. Jeton yoksa ya da tüketilmişse reddedilir.

        KURU PROVA ÖNCE ÇALIŞIR — ARAYÜZDE DEĞİL, BURADA. Jeton üç durumdan
        geçer: `preview` → `dry_run` → `applied`. Gerçek uygulama yalnız
        `dry_run` durumundaki jetonu kabul eder; kullanıcı ne olacağını
        GÖRMEDEN mağazaya tek satır yazılmaz. Kuralı yalnız panele koymak, K9'un
        tam olarak yasakladığı şey olurdu: arayüzde gizlemek yetkilendirme
        değildir.

        TAŞIYICI PROVADA SEÇİLİR VE ORADA KALIR. Gerçek uygulama provadakinden
        farklı bir taşıyıcıyla gelirse reddedilir — onaylanan şey neyse o
        uygulanır.

        SIRAYLA yazılır ve YİNELENMEZ: geçit yazma isteklerini tekrarlamıyor
        (zaman aşımına uğrayan fatura uzakta kesilmiş olabilir). Biri patlarsa
        gerisi sürer; sonuç satır satır döner ve KISMİ BAŞARI başarısızlık
        sayılmaz.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        try:
            row = await self._store.fetch_one(
                f"SELECT token, kind, rows, status, params FROM {self._batch} WHERE token = ?",
                (ord_.text(token),))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        if not row:
            return {"ok": False,
                    "error": "Önizleme bulunamadı. Liste görülmeden toplu işlem uygulanmaz; "
                             "önizlemeyi yeniden alın."}
        if row["status"] == "applied":
            return {"ok": False, "error": "Bu önizleme zaten uygulandı."}
        if row["status"] not in ("preview", "dry_run"):
            return {"ok": False, "error": f"Önizleme durumu uygun değil: {row['status']}"}
        if not dry_run and row["status"] != "dry_run":
            return {"ok": False,
                    "error": "Önce kuru prova çalıştırılmalı: ne olacağı görülmeden gerçek "
                             "işlem yapılmaz."}

        kind = row["kind"]
        params = self._batch_params(row)
        wanted_carrier = ord_.text(carrier)
        if dry_run:
            params["carrier"] = wanted_carrier
        elif ord_.text(params.get("carrier")) != wanted_carrier:
            return {"ok": False,
                    "error": f"Kuru provada taşıyıcı “{params.get('carrier') or '—'}” idi, "
                             f"şimdi “{wanted_carrier or '—'}” geldi. Onaylanan liste neyse o "
                             "uygulanır; önizlemeyi yeniden alın."}

        diff = json.loads(row["rows"])
        targets = [item for item in diff if not item.get("skipped")]
        if not targets:
            return {"ok": False, "error": "Önizlemede uygulanacak satır yok."}

        results: list[dict[str, Any]] = []
        applied = 0
        for item in targets:
            order_id = int(item["id"])
            # TEK TÜR KALDI (`BATCH_KINDS`), dallanma yok. Kargo dalı
            # buradan çıktı: toplu kargoya verme Kargo Yönetimi'nin işi.
            outcome = await self.invoice(order_id, items=None, reason=reason, actor=actor,
                                         dry_run=dry_run)
            applied += 1 if outcome.get("ok") else 0
            # SMS SONUCU SATIR SATIR TAŞINIR. "Yedi sipariş kargolandı" demek
            # yetmez: hangisinin müşterisine haber verilemediği ve NEDEN
            # verilemediği (numara yok, takip kodu yok) aynı tabloda görünmeli.
            sms = outcome.get("sms") or {}
            results.append({"id": order_id, "orderNo": item.get("orderNo"),
                            "customer": item.get("customer", ""),
                            "ok": bool(outcome.get("ok")), "error": outcome.get("error", ""),
                            "smsSent": bool(sms.get("sent")),
                            "smsNote": ord_.text(sms.get("note"))})

        await self._store.execute(
            f"UPDATE {self._batch} SET status = ?, actor = ?, reason = ?, applied_at = ?, "
            "params = ? WHERE token = ?",
            ("dry_run" if dry_run else "applied", actor, reason, _now(),
             json.dumps(params, ensure_ascii=False), row["token"]),
        )
        if not dry_run:
            self._drop_evidence()
        failed = len(targets) - applied
        await self._record(order_id=0, action=f"batch_{kind}", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"applied": applied, "failed": failed, "carrier": wanted_carrier,
                                   "token": row["token"]})
        # KISMİ BAŞARI BAŞARISIZLIK DEĞİLDİR. On siparişin üçü patladıysa yedisi
        # gerçekten kargolanmıştır; `ok: False` demek o yediyi ekrandan silmek ve
        # kullanıcıya hepsini yeniden denetmek olurdu (ikinci gönderi = ikinci
        # kargo). Başarısızlar `results` içinde NEDENİYLE durur.
        return {"ok": applied > 0, "error": "", "applied": applied, "failed": failed,
                "partial": failed > 0 and applied > 0, "carrier": wanted_carrier,
                "results": results, "dryRun": dry_run}

    @staticmethod
    def _batch_params(row: Any) -> dict[str, Any]:
        try:
            params = json.loads(row["params"] or "{}")
        except (KeyError, TypeError, ValueError):
            return {}
        return params if isinstance(params, dict) else {}

    # ====================================================== müşteri aşama SMS'i
    #
    # METİN, FREN VE TEKRAR ENGELİ BURADA DEĞİL. Onlar Bildirimler modülünün
    # işidir ve o modül buradan IMPORT EDİLMEZ (K3); aradaki tek bağ
    # `store.notify.stage` yeteneği ile `stages.stage_order()` künyesidir.
    # Bu ekranın sorumluluğu tek cümledir: "hangi sipariş hangi aşamaya geçti".

    async def stage_state(self) -> dict[str, Any]:
        """Aşama SMS'i kurulu mu, hangi aşamalar açık.

        Ekran ve tarama bunu ÖNDEN sorar: kapalı bir aşama için sipariş detayı
        çekmek, on dakikada bir boşuna istek üretmek olurdu.
        """
        if self._stage_notify is None:
            return {"available": False, "enabled": [], "dryRun": self._stage_dry_run,
                    "lookbackDays": self._stage_lookback,
                    "error": "Bildirimler ekranı kapalı; müşteriye aşama SMS'i gönderilemez."}
        try:
            state = await self._stage_notify.state()
        except Exception as failure:  # noqa: BLE001 — bildirim tarafı bizi düşürmez (K7)
            self._log.warning("aşama SMS durumu okunamadı", error=str(failure))
            return {"available": False, "enabled": [], "dryRun": self._stage_dry_run,
                    "lookbackDays": self._stage_lookback, "error": self._fail(failure)}
        return {
            "available": bool(state.get("available")),
            "enabled": [item for item in (state.get("enabled") or [])
                        if item in stages.STAGES],
            "dryRun": self._stage_dry_run,
            "lookbackDays": self._stage_lookback,
            "error": "",
        }

    async def _stage_open(self, stage: str) -> tuple[bool, str]:
        """Bu aşama gönderime açık mı — ve DEĞİLSE nedeni.

        Neden döndürmek şart: "SMS gitmedi" diye sessiz kalmak, personelin
        müşteri aradığında ne diyeceğini bilememesi demektir.
        """
        state = await self.stage_state()
        if not state["available"]:
            return False, state["error"] or "Aşama SMS'i bu kurulumda kapalı."
        if stage not in state["enabled"]:
            return False, (f"{stages.STAGE_LABELS.get(stage, stage)} aşaması kapalı "
                           "(Bildirimler → Müşteri SMS'i).")
        return True, ""

    async def _notify_stage(self, stage: str, row: dict[str, Any], *,
                            shipment: dict[str, Any] | None = None, carrier: str = "",
                            track: str = "", actor: str = "",
                            dry_run: bool = True) -> dict[str, Any]:
        """Tek siparişin tek aşaması için SMS isteği. İSTİSNA FIRLATMAZ.

        Bildirim tarafının patlaması sipariş işini düşürmez (K7): kargo kaydı
        açılmıştır, mesajın gitmemesi onu geri almaz. Sonuç satır olarak döner
        ve çağıran ekranda gösterir.
        """
        if self._stage_notify is None:
            return {"ok": False, "sent": False, "stage": stage,
                    "note": "Bildirimler ekranı kapalı; müşteriye SMS gönderilemedi."}
        payload = stages.stage_order(row, shipment=shipment, carrier=carrier, track=track)
        try:
            result = await self._stage_notify.notify(stage=stage, order=payload, actor=actor,
                                                     dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("aşama SMS'i gönderilemedi", stage=stage,
                              orderId=payload["orderId"], error=str(failure))
            return {"ok": False, "sent": False, "stage": stage,
                    "orderId": payload["orderId"], "orderNo": payload["orderNo"],
                    "note": f"SMS katmanı hata verdi: {self._fail(failure)}"}
        return {"ok": bool(result.get("ok", True)), "sent": bool(result.get("sent")),
                "stage": stage, "orderId": payload["orderId"], "orderNo": payload["orderNo"],
                "customer": payload["customer"], "result": ord_.text(result.get("result")),
                "duplicate": bool(result.get("duplicate")),
                "note": ord_.text(result.get("note")) or ord_.text(result.get("error"))}

    async def _shipment_of(self, order_id: int) -> dict[str, Any]:
        """Siparişin taşıyıcı kaydı — takip numarası ve bağlantı için.

        TOPLU "KARGOYA VER" TAKİP NUMARASI ÜRETMEZ (her paketin numarası
        ayrıdır); numara ve takip bağlantısı taşıyıcının kendi kaydındadır.
        Uç yayında değilse boş künye döner ve mesaj "bilgi eksik" diye durur —
        uydurma bir takip kodu göndermekten iyidir.
        """
        try:
            payload = await self._api.bbd_shipments({"order_id": int(order_id)})
        except Exception as failure:  # noqa: BLE001 — BBD ucu yayında olmayabilir (K7)
            self._log.info("gönderi kaydı okunamadı", orderId=order_id, error=str(failure))
            return {}
        for item in (payload.get("items") or []):
            view = stages.shipment_view(item)
            if view["track"] or view["trackUrl"]:
                return view
        return {}

    async def stage_sweep(self, *, stage: str, actor: str = "",
                          dry_run: bool = True) -> dict[str, Any]:
        """Tarama tetikleyicisi: "sipariş alındı" ve "teslim edildi".

        NEDEN TARAMA. Bu iki aşamanın kaynağı Kontrol Merkezi'nde bir tıklama
        değil: sipariş müşterinin kendi eylemi, teslim ise Geliver'ın webhook /
        senkron ile mağazaya yazdığı durumdur. İkisini de görmenin tek yolu
        mağazaya bakmaktır.

        TEKRAR ENGELİ BİZDE DEĞİL, BİLDİRİMLER TARAFINDADIR: tarama on dakikada
        bir koşsa da aynı siparişe ikinci SMS gitmez. Burada yapılan yalnız
        boşuna istek atmamaktır — zaten gönderilmiş siparişler künye
        çıkarılmadan elenir.
        """
        problem = stages.sweep_stage_error(stage)
        if problem:
            return {"ok": False, "error": problem}
        open_, why = await self._stage_open(stage)
        if not open_:
            return {"ok": True, "error": "", "stage": stage, "skipped": True, "note": why,
                    "considered": 0, "results": []}

        if stage == stages.STAGE_PLACED:
            candidates, note, truncated = await self._placed_candidates()
        else:
            candidates, note, truncated = await self._delivered_candidates()
        if not candidates:
            return {"ok": True, "error": "", "stage": stage, "skipped": False, "note": note,
                    "considered": 0, "truncated": truncated, "results": []}

        results = [await self._notify_stage(stage, item["row"], shipment=item.get("shipment"),
                                           actor=actor,
                                           dry_run=dry_run or self._stage_dry_run)
                   for item in candidates]
        sent = len([item for item in results if item["sent"]])
        await self._record(order_id=0, action=f"stage_sweep_{stage}",
                           reason=f"Aşama taraması: {stages.STAGE_LABELS[stage]}",
                           actor=actor, result="dry_run" if dry_run else "ok",
                           detail={"considered": len(candidates), "tried": len(results),
                                   "sent": sent})
        return {"ok": True, "error": "", "stage": stage, "skipped": False, "note": note,
                "considered": len(candidates), "tried": len(results), "sent": sent,
                "truncated": truncated, "results": results}

    # `_placed_candidates` / `_delivered_candidates` ADAYLARI ZATEN ELENMİŞ
    # döndürür (`_pending`). Eleme sonrası yapılmasaydı teslim taraması her
    # koşuda, zaten haber verilmiş her sipariş için bir detay isteği daha
    # atardı; on dakikada bir koşan bir iş için bu, geçidin payını boşuna
    # yemek olurdu.

    async def _pending(self, stage: str, order_ids: list[int]) -> set[int]:
        """Bu aşama için HENÜZ gönderilmemiş sipariş kimlikleri.

        Bildirimler tarafı zaten ikinci gönderimi engelliyor; bu sorgu yalnız
        boşuna sipariş detayı çekmemek içindir. Sorgu patlarsa HEPSİ denenir:
        eleme bir hız iyileştirmesidir, engel değil — engeli sessizce
        devralmak, gerçek korumanın yerini alan sahte bir koruma olurdu.
        """
        wanted = {int(item) for item in order_ids if ord_.as_int(item)}
        done = getattr(self._stage_notify, "done", None)
        if not wanted or done is None:
            return wanted
        try:
            payload = await done(stage=stage, order_ids=sorted(wanted))
        except Exception as failure:  # noqa: BLE001 — eleme yapılamadı, hepsi denensin (K7)
            self._log.info("gönderilmiş aşama listesi okunamadı", stage=stage,
                           error=str(failure))
            return wanted
        return wanted - {ord_.as_int(item) for item in (payload.get("ids") or [])}

    async def _placed_candidates(self) -> tuple[list[dict[str, Any]], str, bool]:
        """"Siparişiniz alındı" adayları — YENİDEN ESKİYE, gün penceresiyle."""
        prefs = await self._prefs_view()
        filters = {**self._base_filters({}), **EVIDENCE_SORT}
        try:
            payload = await self._api.orders(filters, page=1, per_page=self._page_size)
        except Exception as failure:  # noqa: BLE001 — K7
            return [], f"Sipariş listesi okunamadı: {self._fail(failure)}", False
        rows = self._rows(payload.get("items") or [], prefs)
        window = self._stage_lookback
        out: list[dict[str, Any]] = []
        skipped = 0
        for row in rows:
            if not stages.within_window(row["createdDay"], days=window):
                continue
            if stages.placed_block(row):
                skipped += 1
                continue
            out.append({"row": row})
        pending = await self._pending(stages.STAGE_PLACED, [item["row"]["id"] for item in out])
        out = [item for item in out if item["row"]["id"] in pending]
        note = f"Son {window} günün siparişleri tarandı." if window else "Tüm sayfa tarandı."
        if skipped:
            note += f" {skipped} sipariş iptal/kapalı olduğu için atlandı."
        return out[:SWEEP_CAP], note, len(out) > SWEEP_CAP

    async def _delivered_candidates(self) -> tuple[list[dict[str, Any]], str, bool]:
        """"Teslim edildi" adayları — taşıyıcı kayıtlarından.

        DURUM SÜZGECİ MAĞAZAYA GÖNDERİLMEZ. Laravel tanımadığı parametreyi
        sessizce yok sayar; tanıyıp da başka bir yazım beklerse listeyi sessizce
        BOŞALTIR ve hiçbir müşteri teslim SMS'i almaz — hata da vermez. En yeni
        sayfa çekilir, teslim ayıklaması `stages.is_delivered` ile BURADA
        yapılır ve tanınmayan durum teslim SAYILMAZ.

        GÜN PENCERESİ BURADA UYGULANMAZ — "sipariş alındı"dan farklı olarak.
        Gerekçe: teslim tarihini her uç aynı adla vermiyor ve tarihi
        okunamayan kaydı pencere dışı saymak, teslim SMS'inin HİÇ gitmemesi
        demek olurdu. Aşırı gönderime karşı iki gerçek koruma zaten var: liste
        en yeni sayfayla sınırlı ve tekrar engeli sipariş başına çalışıyor.
        """
        try:
            payload = await self._api.bbd_shipments({}, page=1, per_page=self._page_size)
        except Exception as failure:  # noqa: BLE001 — BBD ucu yayında olmayabilir (K7)
            return [], f"Gönderi listesi okunamadı: {self._fail(failure)}", False
        views = [stages.shipment_view(item) for item in (payload.get("items") or [])]
        delivered = [view for view in views if view["delivered"] and view["orderId"]]
        if not delivered:
            return [], f"{len(views)} gönderi okundu; teslim edilmiş kayıt yok.", False

        prefs = await self._prefs_view()
        pending = await self._pending(stages.STAGE_DELIVERED,
                                      [view["orderId"] for view in delivered])
        out: list[dict[str, Any]] = []
        unread = 0
        for view in delivered[:SWEEP_CAP]:
            if view["orderId"] not in pending:
                continue
            try:
                raw = await self._api.order(view["orderId"])
            except Exception as failure:  # noqa: BLE001 — biri patlarsa gerisi sürsün (K7)
                unread += 1
                self._log.warning("teslim edilen siparişin künyesi okunamadı",
                                  orderId=view["orderId"], error=str(failure))
                continue
            rows = self._rows([raw], prefs)
            if rows:
                out.append({"row": rows[0], "shipment": view})
        note = f"{len(delivered)} teslim edilmiş gönderi bulundu."
        if unread:
            note += f" {unread} siparişin künyesi okunamadı; bir sonraki taramada yeniden denenir."
        return out, note, len(delivered) > SWEEP_CAP

    # ============================================================== etiket

    async def labels(self, order_ids: list[int]) -> dict[str, Any]:
        """Kargo etiketlerini rapor klasörüne indirir.

        PDF'LER BİRLEŞTİRİLMEZ: elimizde PDF birleştirici yok ve sahte bir
        "birleşik etiket" üretmek, yazıcıdan bozuk kâğıt çıkarırdı. Her etiket
        ayrı dosyadır; ekran hepsini listeler ve tek tek yazdırır.
        """
        ids = [int(item) for item in (order_ids or []) if ord_.as_int(item)]
        if not ids:
            return {"ok": False, "error": "Sipariş seçilmedi."}
        if len(ids) > 50:
            return {"ok": False, "error": "Tek seferde en çok 50 etiket."}

        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        files: list[dict[str, Any]] = []
        for order_id in ids:
            try:
                shipments = await self._api.bbd_shipments({"order_id": order_id})
                first = next((item for item in (shipments.get("items") or [])
                              if isinstance(item, dict)), None)
                if not first:
                    raise RuntimeError("Bu siparişin kargo kaydı yok.")
                content = await self._api.bbd_shipment_label(ord_.as_int(ord_.pick(first, "id")))
                name = f"magaza-kargo-etiketi-{order_id}-{stamp}.pdf"
                path = write_private(self._export_dir / name, content)
            except Exception as failure:  # noqa: BLE001 — biri patlarsa gerisi sürsün
                files.append({"orderId": order_id, "name": "", "path": "",
                              "error": self._fail(failure)})
                continue
            files.append({"orderId": order_id, "name": name, "path": str(path), "error": ""})
        return {"ok": any(not item["error"] for item in files), "error": "", "files": files}

    # ============================================================== ayarlar

    async def settings(self) -> dict[str, Any]:
        """`store_settings` ekranı yok; sipariş akışı ayarları doğal sahibinde.

        İki farklı şey aynı sekmede durur ve AYRI gösterilir:
        · Bu ekranın tercihleri (durum adları, sipariş no biçimi, iptal süresi)
          — yalnız Kontrol Merkezi'ni etkiler.
        · Mağazanın sipariş ayarları — SALT OKUNUR gösterilir; gerekçesi
          aşağıda.
        """
        prefs = await self._prefs_view()
        slug = str(self._config.get("order_config_slug") or "sales.order_settings")
        out: dict[str, Any] = {
            "ok": True, "error": "", "local": prefs, "storeSlug": slug,
            "statuses": [{"value": code, "label": label}
                         for code, label in ord_.STATUS_LABELS.items()],
            "store": [], "storeAvailable": True,
        }
        try:
            payload = await self._api.configuration(slug, channel=self._channel)
        except Exception as failure:  # noqa: BLE001 — K7
            out["storeAvailable"] = False
            out["error"] = self._fail(failure)
            return out

        # CANLIDA DOĞRULANDI: `GET /api/admin/configuration?slug=…` TEK ELEMANLI
        # DİZİ döndürüyor — `[{slug, channel, locale, values:{…}}]`. Zarfı geçit
        # açıyor; bize `{slug, channel, locale, values}` geliyor. `values`
        # gelmezse tabloyu boş göstermek "bu bölümde ayar yok" demek olurdu;
        # okunamadığı SÖYLENİR.
        values = payload.get("values") if isinstance(payload, dict) else None
        if not isinstance(values, dict) or not values:
            out["storeAvailable"] = False
            out["error"] = (
                f"Mağazanın “{slug}” ayarları okunamadı: yanıt beklenen "
                "{slug, channel, locale, values} zarfını taşımıyor. Aşağıdaki yerel "
                "tercihler bundan etkilenmez.")
            return out
        out["store"] = [{"key": str(key), "value": "" if value is None else str(value)}
                        for key, value in sorted(values.items())][:60]
        return out

    async def save_settings(self, *, status_names: dict[str, str] | None = None,
                            order_no_format: str | None = None,
                            cancel_window_hours: int | None = None, late_days: int | None = None,
                            reason: str, actor: str) -> dict[str, Any]:
        """Yalnız YEREL tercihleri yazar; mağazaya hiçbir şey gönderilmez."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        changed: list[str] = []
        if order_no_format is not None:
            trouble = ord_.format_error(order_no_format)
            if trouble:
                return {"ok": False, "error": trouble}
            await self._set_pref("order_no_format", ord_.text(order_no_format) or "#{no}", actor)
            changed.append("sipariş no biçimi")
        if status_names is not None:
            clean = ord_.clean_status_names(status_names)
            await self._set_pref("status_names", json.dumps(clean, ensure_ascii=False), actor)
            changed.append("durum adları")
        if cancel_window_hours is not None:
            await self._set_pref("cancel_window_hours",
                                 str(max(0, min(8_760, int(cancel_window_hours)))), actor)
            changed.append("iptal süresi")
        if late_days is not None:
            await self._set_pref("late_days", str(max(1, min(90, int(late_days)))), actor)
            changed.append("gecikme eşiği")

        await self._record(order_id=0, action="save_settings", reason=reason, actor=actor,
                           result="ok", detail={"changed": changed})
        return {"ok": True, "error": "", "changed": changed,
                "local": await self._prefs_view()}

    # ================================================================ rapor

    async def export_csv(self, *, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """TÜM kayıtların CSV'si — rapor klasörüne yazılır. Görünen sayfanın
        CSV'sini panel kendisi üretir (sunucuya hiç gitmez)."""
        prefs = await self._prefs_view()
        wanted = dict(filters or {})
        wanted.setdefault("today", ord_.today_iso())
        try:
            # CSV mali kayıt yerine geçiyor: ara toplam, KDV, kargo firması ve
            # takip numarası yalnız DETAYDA var, sığ satırda hepsi sıfır çıkardı.
            rows, truncated, partial, _ = await self._scan(wanted, prefs, detail=True)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        hits = [row for row in rows if ord_.matches(row, wanted)]

        headers = ["Sipariş no", "Tarih", "Müşteri", "E-posta", "Kanal", "Durum", "Ödeme",
                   "Kalem", "Ara toplam", "Kargo", "İndirim", "KDV", "Toplam", "Kargo firması",
                   "Takip no"]
        table = [[row["orderNo"], row["createdAt"], row["customer"], row["email"], row["channel"],
                  row["statusLabel"], row["paymentLabel"], row["itemCount"],
                  money(row["subTotal"]), money(row["shipping"]), money(row["discount"]),
                  money(row["tax"]), money(row["grandTotal"]), row["carrier"], row["track"]]
                 for row in hits]

        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        name = f"magaza-siparis-listesi-{stamp}.csv"
        try:
            path = write_private(self._export_dir / name, csv_bytes(headers, table))
        except OSError as failure:
            return {"ok": False, "error": f"Dosya yazılamadı: {failure}"}
        return {"ok": True, "error": "", "path": str(path), "name": name,
                "rows": len(table), "truncated": truncated, "partial": partial}

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
        if kind not in ("list", "slip", "manifest"):
            return {"ok": False, "error": f"Bilinmeyen rapor: {kind}"}
        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")

        if kind == "slip":
            produced = await self._slip_pdf(ord_.as_int(params.get("orderId")))
            name = f"magaza-siparis-fisi-{ord_.as_int(params.get('orderId'))}-{stamp}.pdf"
        elif kind == "manifest":
            produced = await self._manifest_pdf(
                [ord_.as_int(item) for item in (params.get("orderIds") or [])])
            name = f"magaza-kargo-manifestosu-{stamp}.pdf"
        else:
            produced = await self._list_pdf(params.get("filters") or {})
            name = f"magaza-siparis-listesi-{stamp}.pdf"

        if not produced.get("ok"):
            return produced
        content = produced["content"]
        try:
            path = write_private(self._export_dir / name, content)
        except (OSError, ExportError) as failure:
            return {"ok": False, "error": str(failure)}
        self._log.info("sipariş raporu üretildi", kind=kind, path=str(path))
        return {"ok": True, "error": "", "path": str(path), "name": name,
                "bytes": len(content), "rows": produced.get("rows", 0),
                "truncated": produced.get("truncated", False)}

    async def _list_pdf(self, filters: dict[str, Any]) -> dict[str, Any]:
        prefs = await self._prefs_view()
        wanted = dict(filters or {})
        wanted.setdefault("today", ord_.today_iso())
        try:
            rows, truncated, partial, _ = await self._scan(wanted, prefs, detail=True)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        hits = [row for row in rows if ord_.matches(row, wanted)]
        if not hits:
            return {"ok": False, "error": "Bu süzgeçte rapora girecek sipariş yok."}

        totals = ord_.summary(hits)
        sections: list[dict[str, Any]] = [{
            "kind": "tiles", "title": "Özet",
            "tiles": [("Sipariş", number(totals["count"])),
                      ("Ciro", money(totals["revenue"])),
                      ("Ortalama sepet", money(totals["average"])),
                      ("İptal", number(totals["canceled"]))],
        }, {
            "kind": "table", "title": "Siparişler",
            "headers": ["Sipariş no", "Tarih", "Müşteri", "Durum", "Ödeme", "Toplam"],
            "align": "LLLLLR", "widths": [1.1, 1.2, 2.2, 1, 1, 1],
            "rows": [[row["orderNo"], row["createdAt"][:16], row["customer"],
                      row["statusLabel"], row["paymentLabel"], money(row["grandTotal"])]
                     for row in hits[:600]],
        }]
        if truncated or partial or len(hits) > 600:
            sections.append({"kind": "note",
                             "text": "Liste tavana dayandı; rapor eksik olabilir."})
        return {"ok": True, "rows": len(hits), "truncated": truncated or partial,
                "content": build_pdf(title="Sipariş listesi",
                                     subtitle=f"{totals['count']} sipariş · "
                                              f"ciro {money(totals['revenue'])}",
                                     sections=sections, footer="Kontrol Merkezi · Mağaza")}

    async def _slip_pdf(self, order_id: int) -> dict[str, Any]:
        if not order_id:
            return {"ok": False, "error": "Sipariş seçilmedi."}
        card = await self.card(order_id)
        if not card.get("ok"):
            return {"ok": False, "error": card.get("error") or "Sipariş okunamadı."}
        row = card["order"]
        amounts = card["money"]
        sections: list[dict[str, Any]] = [{
            "kind": "tiles", "title": "Sipariş",
            "tiles": [("Sipariş no", row["orderNo"]), ("Tarih", row["createdAt"][:16]),
                      ("Durum", row["statusLabel"]), ("Ödeme", row["paymentLabel"])],
        }, {
            "kind": "table", "title": "Kalemler",
            "headers": ["SKU", "Ürün", "Adet", "Birim", "İndirim", "KDV", "Tutar"],
            "align": "LLRRRRR", "widths": [1.1, 2.6, 0.6, 1, 1, 1, 1],
            "rows": [[item["sku"], item["name"], number(item["quantity"]),
                      money(item["unitPrice"]), money(item["discount"]), money(item["tax"]),
                      money(item["total"])] for item in card["items"]],
        }, {
            "kind": "table", "title": "Toplam",
            "headers": ["Kalem", "Tutar"], "align": "LR", "widths": [3, 1],
            "rows": [["Ara toplam", money(amounts["subTotal"])],
                     ["Kargo", money(amounts["shipping"])],
                     ["İndirim", money(amounts["discount"])],
                     ["KDV", money(amounts["tax"])],
                     ["Genel toplam", money(amounts["grandTotal"])]],
        }, {
            "kind": "table", "title": "Adresler",
            "headers": ["Tür", "Ad", "Adres"], "align": "LLL", "widths": [0.8, 1.4, 3.4],
            "rows": [["Fatura", card["billing"]["name"], card["billing"]["line"]],
                     ["Teslimat", card["shipping"]["name"], card["shipping"]["line"]]],
        }]
        return {"ok": True, "rows": len(card["items"]), "truncated": False,
                "content": build_pdf(title="Sipariş fişi",
                                     subtitle=f"{row['orderNo']} · {row['customer']}",
                                     sections=sections, footer="Kontrol Merkezi · Mağaza")}

    async def _manifest_pdf(self, order_ids: list[int]) -> dict[str, Any]:
        ids = [item for item in order_ids if item]
        if not ids:
            return {"ok": False, "error": "Sipariş seçilmedi."}
        prefs = await self._prefs_view()
        rows: list[dict[str, Any]] = []
        for order_id in ids[:200]:
            try:
                raw = await self._api.order(order_id)
            except Exception as failure:  # noqa: BLE001 — biri patlarsa gerisi sürsün
                self._log.warning("manifesto için sipariş okunamadı", orderId=order_id,
                                  error=str(failure))
                continue
            rows.extend(self._rows([raw], prefs))
        if not rows:
            return {"ok": False, "error": "Seçilen siparişlerin hiçbiri okunamadı."}

        sections = [{
            "kind": "table", "title": "Kargo manifestosu",
            "headers": ["Sipariş no", "Müşteri", "Şehir", "Kalem", "Kargo firması", "Takip no"],
            "align": "LLLRLL", "widths": [1.1, 2, 1.2, 0.6, 1.3, 1.5],
            "rows": [[row["orderNo"], row["customer"], row["city"] or "—",
                      number(row["quantity"]), row["carrier"] or "—", row["track"] or "—"]
                     for row in rows],
        }, {
            "kind": "note",
            "text": f"{len(rows)} gönderi · teslim alan imzası: ______________________",
        }]
        return {"ok": True, "rows": len(rows), "truncated": len(ids) > 200,
                "content": build_pdf(title="Kargo manifestosu",
                                     subtitle=f"{len(rows)} sipariş",
                                     sections=sections, footer="Kontrol Merkezi · Mağaza")}

    async def _render_pages(self, path: Path, *, max_pages: int = 12,
                            dpi: int = 110) -> list[str]:
        binary = shutil.which("pdftoppm")
        if not binary:
            raise PreviewError(
                "Önizleme üretilemedi: `pdftoppm` yok (poppler-utils kurulmalı). "
                "Rapor yine de kaydedildi ve yazdırılabilir.")
        with tempfile.TemporaryDirectory(prefix="km-siparis-onizleme-") as folder:
            target = Path(folder) / "sayfa"
            process = None
            try:
                process = await asyncio.create_subprocess_exec(
                    binary, "-png", "-r", str(int(dpi)), "-f", "1", "-l", str(int(max_pages)),
                    str(path), str(target),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                _, err = await asyncio.wait_for(process.communicate(), timeout=60)
            except TimeoutError:
                # Süre dolunca süreç ÖLDÜRÜLMELİ: `wait_for` yalnız beklemeyi
                # bırakır, `pdftoppm` arkada çalışmaya ve geçici klasöre yazmaya
                # devam ederdi — klasör silinince yarım dosyalarla boğuşurdu.
                if process is not None and process.returncode is None:
                    process.kill()
                    await process.wait()
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
