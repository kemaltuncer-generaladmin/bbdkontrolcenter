"""Talepler (RMA) — iş kuralları.

VERİ MAĞAZADADIR, KARAR BURADADIR. Talep kaydı `store.api` geçidinden gelir
(K4). Bu modül uzak kaydın kopyasını tutmaz; yerel tablolar yalnız mağazada
KARŞILIĞI OLMAYAN üç şeyi saklar:

  1. GEREKÇE (denetim izi). Bagisto denetim kaydı gerekçe alanı taşımıyor;
     ayrıca ağ koparsa "ne yapmaya çalıştık" bilgisi yalnız burada kalır.
  2. İÇ NOT. Personelin kendi arasında yazdığı metin uzağa HİÇ gönderilmez:
     "internal" bayrağının müşteri portalında yanlış yorumlanması geri
     alınamaz bir sızıntı olurdu.
  3. İADE AKTARIMI. Onaylanan talebin İadeler ekranına devri; olay yolunu
     dinleyen olmasa bile "devredildi mi" sorusunun cevabı burada durur.

UZAK UÇ HENÜZ YAYINDA OLMAYABİLİR. Geçit onu `bbd_endpoint_missing` hatasına
çeviriyor. Ekran bu durumda ÇÖKMEZ (K7): `connected: False` + anlaşılır bir
metin döner ve yerel iz/notlar okunmaya devam eder.

MAĞAZANIN YAZMA SÖZLEŞMESİ (2026-08-15, `PUT bbd/return-requests/{id}` yayında)
Uç ÜÇ ALAN yazar ve tanımadığı alanı sessizce yok saymaz, isteği reddeder:

    status  → durum kimliği (ekranın altı anahtarı DEĞİL, mağazanın dokuz
              kimliğinden biri). Kimlik 5 ve 8 para hareketi doğurduğu için
              409 alır.
    note    → İÇ not; siparişin yöneticiye açık yorum defterine yazılır ve
              müşteriye e-posta GÖNDERİLMEZ.
    items   → talebin MEVCUT kalemlerinin adedi/sebebi. Kalem EKLENMEZ,
              SİLİNMEZ; adresleme `rma_items.id` iledir.

BU EKRANIN ÜÇ İŞİ MAĞAZADA KARŞILIĞI OLMADIĞI İÇİN KAPALI — ve kapalı olduğunu
`features()` ile SÖYLER, tıklanınca hata vermez:

  · ÖNCELİK. `rma` tablosunda öncelik sütunu yok. SLA saatini önceliğe göre
    hesaplıyoruz ama öncelik hiçbir yerde SAKLANMIYOR; yazılabilirmiş gibi
    göstermek, ayarlandığını sanılan ama her tazelemede geri dönen bir alan
    üretirdi.
  · ATAMA. Aynı sebep; mağaza bu alanı 503 ile reddediyor ve REDDEDERKEN aynı
    isteğin durum/not kısmını da yazmıyor.
  · MÜŞTERİYE YANIT. `rma_messages` zinciri müşteriye görünür ama bu uçtan
    YAZILMIYOR; yazan tek yer panelin kendi ekranı. Buradan gönderiyormuş gibi
    davranmak, cevap beklediğini sanan bir müşteri bırakırdı.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from km_sdk import ExportError, build_pdf, csv_bytes, money, number, report_dir, write_private

from . import rma

#: Rapor için taranan en çok talep. Uzak uç sayfa başına 50 kırpıyor; 2.000
#: tavanı bozuk `meta` yüzünden sonsuz taramayı da engeller.
REPORT_ROW_CAP = 2_000

BULK_ACTIONS = ("status", "close")

#: MAĞAZADA KARŞILIĞI OLMADIĞI İÇİN KAPALI işler — anahtar → (etiket, neden).
#:
#: Ekran bunları `features()` üzerinden okur ve düğmeyi TIKLANMADAN ÖNCE kapatır.
#: Kapalı düğmenin nedeni yazılı olmak zorunda: "çalışmıyor" ile "burada
#: yapılmıyor" kullanıcı için bambaşka iki durumdur.
CLOSED_FEATURES = {
    "priority": (
        "Öncelik değiştirme",
        ("Mağazada iade talebinin önceliği diye bir kayıt YOK (`rma` tablosunda sütun "
        "bulunmuyor). Öncelik yalnız SLA saatini hesaplamak için kullanılıyor ve "
        "kaydedilemez; kaydedilebilirmiş gibi göstermek, her tazelemede geri dönen "
         "bir alan olurdu."),
    ),
    "assign": (
        "Talep atama",
        ("Mağazada iade talebine ATAMA alanı yok. Mağaza bu alanı gönderen isteği "
        "tümüyle reddediyor — aynı istekteki durum ve iç not da yazılmıyor. Atamayı "
         "müşteriye görünen bir alana yazmak ise geri alınamaz bir sızıntı olurdu."),
    ),
    "reply": (
        "Müşteriye yanıt",
        ("Mağazanın iade ucu müşteriye mesaj YAZMIYOR (yalnız durum satırını zincire "
        "ekliyor) ve bu uç müşteriye e-posta göndermiyor. Yanıt Bagisto panelinden "
        "yazılır; buradan gönderiyormuş gibi davranmak, cevap bekleyen bir müşteri "
         "bırakırdı."),
    ),
}

#: Toplu işlemde tek seferde dokunulacak en çok talep. Uzak uçta TOPLU YAZMA
#: YOK: her talep ayrı PUT ile gider ve mağaza dakikada 60 istek kabul eder.
#: 50'de durmak, yarıda kalan bir toplu işin kaç kaydı değiştirdiğini
#: kullanıcının hâlâ takip edebileceği bir büyüklüktür.
BULK_LIMIT = 50


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


class PreviewError(RuntimeError):
    """Önizleme görüntüsü üretilemedi. Rapor yine de kaydedilmiştir."""


class RequestsService:
    """Talepler ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ."""

    def __init__(self, *, api: Any, store: Any, log: Any, config: dict[str, Any],
                 printer: Any = None, publish: Any = None, category: str = "Mağaza",
                 subcategory: str = "Müşteri", fallback_dir: Path | None = None) -> None:
        self._api = api
        self._store = store
        self._log = log
        self._config = config or {}
        self._printer = printer
        self._publish = publish
        self._category = category
        self._subcategory = subcategory
        self._fallback = fallback_dir or Path.home() / "km-raporlar"

        self._audit = store.table("audit")
        self._notes = store.table("notes")
        self._handoff = store.table("handoff")

    # ------------------------------------------------------------- ayarlar

    @property
    def _page_size(self) -> int:
        return max(10, min(200, rma.as_int(self._config.get("page_size"), 50)))

    @property
    def _board_size(self) -> int:
        return max(3, min(50, rma.as_int(self._config.get("board_column_size"), 12)))

    @property
    def _hours(self) -> dict[str, int]:
        return rma.sla_hours(self._config.get("sla_hours"))

    @property
    def _templates(self) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for item in self._config.get("reply_templates") or []:
            if not isinstance(item, dict):
                continue
            title = rma.text(item.get("title"))
            body = rma.text(item.get("body"))
            if title and body:
                out.append({"title": title, "body": body})
        return out

    @property
    def _export_dir(self) -> Path:
        # HER ÇAĞRIDA yeniden çözülür: ay değişince klasör kendiliğinden değişir.
        return report_dir(self._category, subcategory=self._subcategory,
                          fallback=self._fallback,
                          configured=str(self._config.get("export_path") or ""))

    # --------------------------------------------------------- yetenek beyanı

    def features(self) -> dict[str, Any]:
        """Hangi düğme açılabilir — kapalı olanın NEDENİ yazılıdır.

        Panel bunu okuyup düğmeyi tıklanmadan önce kapatır. K9: burada
        "kapalı" demek yetkilendirme DEĞİLDİR — aynı işler serviste de
        reddedilir ve mağaza da kendi kapısını tutar.
        """
        closed = {key: {"available": False, "reason": f"{label} — {why}"}
                  for key, (label, why) in CLOSED_FEATURES.items()}
        return {
            **closed,
            "status": {"available": True, "reason": ""},
            "note": {"available": True, "reason": ""},
            "items": {"available": True, "reason": ""},
            "decide": {"available": True, "reason": ""},
            "printer": {"available": self._printer is not None,
                        "reason": "" if self._printer is not None else
                                  "Yazıcı yeteneği bu kurulumda yok; PDF üretilir, "
                                  "basılmaz."},
        }

    # ------------------------------------------------------- yerel tablolar

    async def _record(self, *, request_id: int, action: str, reason: str, actor: str,
                      result: str, detail: Any = None) -> None:
        try:
            await self._store.execute(
                f"INSERT INTO {self._audit} "
                "(request_id, action, reason, actor, result, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (int(request_id or 0), action, reason, actor, result,
                 json.dumps(detail or {}, ensure_ascii=False), _now()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın
            self._log.warning("denetim izi yazılamadı", action=action, error=str(failure))

    async def _local_notes(self, request_id: int) -> list[dict[str, Any]]:
        try:
            return await self._store.fetch_all(
                f"SELECT id, body, actor, created_at FROM {self._notes} "
                "WHERE request_id = ? ORDER BY id",
                (int(request_id),))
        except Exception as failure:  # noqa: BLE001 — not okunamadı, zincir yine dolsun
            self._log.warning("iç notlar okunamadı", requestId=request_id, error=str(failure))
            return []

    async def _handoff_rows(self, request_id: int) -> list[dict[str, Any]]:
        try:
            return await self._store.fetch_all(
                f"SELECT amount, items, actor, reason, created_at FROM {self._handoff} "
                "WHERE request_id = ? ORDER BY id DESC",
                (int(request_id),))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("iade aktarımı okunamadı", requestId=request_id,
                              error=str(failure))
            return []

    # -------------------------------------------------------------- yardımcı

    @staticmethod
    def _fail(failure: Exception) -> str:
        message = str(failure).strip()
        return message or "Mağazaya ulaşılamadı."

    @staticmethod
    def _guard(reason: str) -> str:
        """Gerekçe backend'de DE doğrulanır (K9): arayüzde gizlemek yetmez."""
        return rma.reason_error(reason)

    # ================================================================= liste

    async def requests(self, *, q: str = "", kind: str = "", status: str = "",
                       priority: str = "", assignee: str = "", channel: str = "",
                       start: str = "", end: str = "", date_field: str = "created",
                       sla: str = "", product: str = "", awaiting: bool = False,
                       page: int = 1, size: int = 0) -> dict[str, Any]:
        """Sayfalanmış talep listesi. SUNUCU TARAFI — tam liste çekilmez."""
        per_page = size or self._page_size
        filters = rma.list_filters(q=q, kind=kind, status=status, priority=priority,
                                   assignee=assignee, channel=channel, start=start, end=end,
                                   date_field=date_field, sla=sla, product=product,
                                   awaiting=awaiting)
        try:
            payload = await self._api.bbd_return_requests(filters, page=page, per_page=per_page)
        except Exception as failure:  # noqa: BLE001 — ekran ayakta kalmalı (K7)
            self._log.warning("talep listesi okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "items": [], "total": 0, "page": page, "size": per_page, "pages": 0,
                    "summary": rma.sla_summary([]), "narrowed": False}

        hours = self._hours
        rows = [rma.request_row(item, hours=hours)
                for item in (payload.get("items") or []) if isinstance(item, dict)]
        rows, narrowed = rma.apply_local_guards(rows, sla=sla, awaiting=awaiting)
        meta = payload.get("meta") or {}
        return {
            "ok": True, "connected": True, "error": "",
            "items": rows,
            "total": rma.as_int(meta.get("total"), len(rows)),
            "page": rma.as_int(meta.get("currentPage"), page),
            "size": rma.as_int(meta.get("perPage"), per_page),
            "pages": rma.as_int(meta.get("lastPage"), 1),
            "summary": rma.sla_summary(rows),
            # Uzak uç türetilmiş süzgeci uygulamadıysa sayfa yerelde daraltıldı;
            # ekran bunu SÖYLER, sayfa sayısının artık tam olmadığını gizlemez.
            "narrowed": narrowed,
        }

    async def board(self, *, q: str = "", kind: str = "", priority: str = "",
                    assignee: str = "", channel: str = "", start: str = "", end: str = "",
                    date_field: str = "created", sla: str = "", product: str = "",
                    awaiting: bool = False) -> dict[str, Any]:
        """Pano görünümü — her durum bir sütun.

        Sütunlar SIRAYLA çekilir (durum başına bir istek). Tek büyük sayfayı
        çekip gruplamak daha az istek ederdi ama sütun başlıklarındaki sayı
        yalan olurdu: "Yeni (8)" yazarken aslında 143 tane olabilir. Sütun
        başına ayrı çağrı, `meta.total` sayesinde GERÇEK sayıyı verir.
        """
        hours = self._hours
        size = self._board_size
        collected: list[dict[str, Any]] = []
        totals: dict[str, int] = {}
        errors: list[str] = []
        connected = False
        narrowed = False

        for status in rma.STATUS_ORDER:
            filters = rma.list_filters(q=q, kind=kind, status=status, priority=priority,
                                       assignee=assignee, channel=channel, start=start,
                                       end=end, date_field=date_field, sla=sla,
                                       product=product, awaiting=awaiting)
            try:
                payload = await self._api.bbd_return_requests(filters, page=1, per_page=size)
            except Exception as failure:  # noqa: BLE001 — sütun sütun hata (K7)
                errors.append(f"{rma.STATUS_LABELS[status]}: {self._fail(failure)}")
                totals[status] = 0
                continue
            connected = True
            rows = [rma.request_row(item, hours=hours)
                    for item in (payload.get("items") or []) if isinstance(item, dict)]
            rows, column_narrowed = rma.apply_local_guards(rows, sla=sla, awaiting=awaiting)
            narrowed = narrowed or column_narrowed
            meta = payload.get("meta") or {}
            totals[status] = rma.as_int(meta.get("total"), len(rows))
            collected.extend(rows)

        warnings = list(errors) if connected else []
        if narrowed:
            # Sütun başlığındaki sayı uzaktan geliyor, kartlar yerelde
            # daraltıldı: ikisi artık aynı şeyi saymıyor, söylenmesi gerekir.
            warnings.append("SLA / “yanıt bizde” süzgeci mağaza tarafında uygulanmadı; "
                            "kartlar yerelde daraltıldı, sütun sayıları daraltmadan önceki "
                            "listeye ait.")
        return {"ok": True, "connected": connected,
                "error": "" if connected else (errors[0] if errors else ""),
                "warnings": warnings, "narrowed": narrowed,
                "columns": rma.board_columns(collected, totals),
                "summary": rma.sla_summary(collected)}

    # ================================================================= künye

    async def card(self, request_id: int) -> dict[str, Any]:
        """Talep künyesi: kayıt + zincir + sipariş + iade edilebilir kalemler.

        Parça parça hata: sipariş okunamazsa talep yine açılır ve ekran neyin
        eksik olduğunu söyler (K7).
        """
        warnings: list[str] = []
        notes = await self._local_notes(request_id)
        handoff = await self._handoff_rows(request_id)

        try:
            raw = await self._api.bbd_return_request(int(request_id))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("talep okunamadı", requestId=request_id, error=str(failure))
            # Uzak kayıt yoksa bile YEREL iz gösterilir: personel en azından
            # bu talebe ne yaptığımızı görür.
            return {"ok": False, "connected": False, "error": self._fail(failure),
                    "notes": [{"body": rma.text(row["body"]), "author": rma.text(row["actor"]),
                               "createdAt": rma.local_stamp(row["created_at"])}
                              for row in notes]}

        row = rma.request_row(raw, hours=self._hours)
        thread = rma.thread_rows(raw, notes)
        derived = rma.awaiting_us(thread, row["status"])
        if row["awaitingUs"] is None:
            row["awaitingUs"] = derived

        order: dict[str, Any] = {}
        items: list[dict[str, Any]] = []
        if row["orderId"]:
            try:
                order = await self._api.order(int(row["orderId"]))
            except Exception as failure:  # noqa: BLE001 — sipariş yoksa talep yine açılır
                warnings.append(f"sipariş: {self._fail(failure)}")
            else:
                items = rma.return_item_rows(order, raw)

        return {
            "ok": True, "connected": True, "error": "", "warnings": warnings,
            "request": row,
            "thread": thread,
            "items": items,
            "estimate": rma.refund_estimate(items),
            # Alan adı eşlemesi `rma.order_summary` içinde: canlı mağaza
            # camelCase konuşuyor ve tek yazımla aranırsa kart "—" ile dolar.
            "order": rma.order_summary(order, order_id=row["orderId"],
                                       order_number=row["orderNumber"]),
            "handoff": [{"amount": rma.as_int(item["amount"]),
                         "items": json.loads(item["items"] or "[]"),
                         "actor": rma.text(item["actor"]),
                         "reason": rma.text(item["reason"]),
                         "createdAt": rma.local_stamp(item["created_at"])}
                        for item in handoff],
            "templates": self._templates,
        }

    async def reference(self) -> dict[str, Any]:
        """Süzgeçlerin ve durum açılırının beslendiği listeler.

        İKİ AYRI DURUM LİSTESİ ve karıştırılmazlar:

          `statuses`      — EKRANIN altı anahtarı. Pano sütunları ve süzgeç.
          `storeStatuses` — MAĞAZANIN dokuz kaydı (kimlik + başlık). Yazma
                            açılırı yalnız bunu kullanır; para hareketi doğuran
                            iki satır listeden ÇIKARILMAZ, `blocked` işaretiyle
                            durur ve nedeni yanında yazar. Çıkarmak "böyle bir
                            durum yok" demek olurdu; oysa var ve panelden
                            seçilebiliyor — buradan seçilemediğini SÖYLEMEK
                            gerekir.

        Mağaza sözlüğü LİSTE YANITININ `meta`sından gelir; ikinci bir istek
        atılmaz. Okunamazsa açılır boş kalır ve ekran nedenini yazar (K7).
        """
        out: dict[str, Any] = {
            "ok": True, "connected": True, "error": "",
            "types": [{"value": key, "label": label} for key, label in rma.TYPE_LABELS.items()],
            "statuses": [{"value": key, "label": rma.STATUS_LABELS[key]}
                         for key in rma.STATUS_ORDER],
            "priorities": [{"value": key, "label": rma.PRIORITY_LABELS[key]}
                           for key in rma.PRIORITY_ORDER],
            "channels": [{"value": key, "label": label}
                         for key, label in rma.CHANNEL_LABELS.items()],
            "slaHours": self._hours,
            "templates": self._templates,
            "assignees": [],
            "storeStatuses": [],
            "storeStatusError": "",
            "features": self.features(),
        }
        try:
            payload = await self._api.bbd_return_requests({}, page=1, per_page=1)
        except Exception as failure:  # noqa: BLE001 — K7: durum açılırı boş kalır
            out["storeStatusError"] = (
                "Mağazanın iade durumları okunamadı; durum açılırı boş kaldı ve durum "
                f"değiştirilemiyor — {self._fail(failure)}")
        else:
            out["storeStatuses"] = rma.status_options(payload.get("meta"))
            if not out["storeStatuses"]:
                out["storeStatusError"] = (
                    "Mağaza durum sözlüğünü göndermedi; durum açılırı boş kaldı. Durumu "
                    "yanlış bir kimlikle yazmaktansa hiç yazmamak doğrudur.")

        # ATANAN LİSTESİ ARTIK ÇEKİLMİYOR: mağazada iade talebine atama alanı
        # yok ve alan gönderilen istek tümüyle reddediliyor. Boş liste olarak
        # KALIR ki ekran "atama kapalı" derken alanı da doldurmasın.
        return out

    async def audit(self, *, request_id: int = 0, limit: int = 50) -> dict[str, Any]:
        """Bu ekrandan yapılan yazmaların YEREL izi (gerekçeleriyle)."""
        sql = (f"SELECT request_id, action, reason, actor, result, created_at "
               f"FROM {self._audit} ")
        params: tuple[Any, ...] = ()
        if request_id:
            sql += "WHERE request_id = ? "
            params = (int(request_id),)
        sql += "ORDER BY id DESC LIMIT ?"
        params = (*params, max(1, min(500, int(limit))))
        try:
            rows = await self._store.fetch_all(sql, params)
        except Exception as failure:  # noqa: BLE001 — iz okunamadı, ekran dursun
            return {"ok": True, "items": [], "error": self._fail(failure)}
        return {"ok": True, "error": "", "items": [
            {"requestId": row["request_id"], "action": row["action"], "reason": row["reason"],
             "actor": row["actor"], "result": row["result"],
             "createdAt": rma.local_stamp(row["created_at"])}
            for row in rows
        ]}

    # ================================================================= yazma

    async def add_note(self, request_id: int, *, body: str, actor: str) -> dict[str, Any]:
        """İÇ NOT — yalnız yerel. Uzağa gönderilmez, müşteri göremez.

        Gerekçe İSTENMEZ: not zaten metnin kendisidir ve yıkıcı bir işlem
        değildir. İkinci bir metin istemek personeli boş yere yazmaya zorlar.
        """
        content = rma.text(body)
        if len(content) < 2:
            return {"ok": False, "error": "Not boş olamaz."}
        if len(content) > 4_000:
            return {"ok": False, "error": "Not en çok 4.000 karakter olabilir."}
        try:
            await self._store.execute(
                f"INSERT INTO {self._notes} (request_id, body, actor, created_at) "
                "VALUES (?, ?, ?, ?)",
                (int(request_id), content, actor, _now()))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": f"Not yazılamadı: {self._fail(failure)}"}
        await self._record(request_id=request_id, action="note", reason="", actor=actor,
                           result="ok", detail={"length": len(content)})
        return {"ok": True, "error": "", "local": True}

    async def reply(self, request_id: int, *, body: str, reason: str, actor: str,
                    status: str = "", dry_run: bool = True) -> dict[str, Any]:
        """MÜŞTERİYE yanıt — MAĞAZADA UCU YOK, istek gönderilmez.

        Eskiden burası `{"message": …, "internal": False}` gövdesiyle uzağa
        gidiyordu; mağazanın iade ucu böyle bir alan tanımıyor ve isteği 422 ile
        reddediyor. Kullanıcı "gönderildi" görmüyordu ama ham bir hata metni
        görüyordu — ikisi de yanlış.

        NEDEN "YAZAMIYORUM" DEMEK, BİR YERE YAZMAKTAN İYİ: tek yazılabilir
        zincir `rma_messages` ve o zincir MÜŞTERİYE görünür; oraya yazmak
        gönderdiğimizi sandığımız yanıtı müşteri hesabında bir nota
        dönüştürürdü. Yanıt Bagisto panelinden yazılır.

        Yazılmak istenen metin KAYBOLMAZ: iç not olarak yereldeki nota
        yazılabilir ve ekran bunu önerir.
        """
        label, why = CLOSED_FEATURES["reply"]
        await self._record(request_id=request_id, action="reply", reason=reason, actor=actor,
                           result="uc_yok", detail={"length": len(rma.text(body))})
        return {"ok": False, "blocked": True, "feature": "reply",
                "error": f"{label} bu ekrandan yapılamıyor. {why}"}

    async def update(self, request_id: int, *, status: str = "", priority: str = "",
                     assignee: str = "", reason: str, actor: str,
                     dry_run: bool = True) -> dict[str, Any]:
        """Durum yazma. ÖNCELİK ve ATAMA MAĞAZADA YOK — istek hiç gönderilmez.

        Eskiden üçü tek gövdede gidiyordu ve mağaza gövdenin TAMAMINI
        reddediyordu: atama alanı 503, öncelik 422. Yani "durumu da değiştirdim"
        sanan kullanıcının durumu da yazılmıyordu. Şimdi kapalı alan istekten
        önce ayrılıyor ve neden kapalı olduğu söyleniyor.

        `status` MAĞAZANIN DURUM KİMLİĞİDİR (metin de kabul edilir, sayıya
        çevrilir). Ekranın altı anahtarı yazma tarafında kullanılmaz: dokuz
        durumu altıya indiren eşleme tek yönlüdür ve tersi belirsizdir.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        for key, value in (("priority", priority), ("assign", assignee)):
            if not rma.text(value):
                continue
            label, why = CLOSED_FEATURES[key]
            await self._record(request_id=request_id, action="update", reason=reason,
                               actor=actor, result="uc_yok", detail={"field": key})
            return {"ok": False, "blocked": True, "feature": key,
                    "error": f"{label} bu ekrandan yapılamıyor. {why}"}

        status_id = rma.as_int(status)
        if not status_id:
            return {"ok": False, "error": "Değişen alan yok."}
        blocked = rma.status_error(status_id)
        if blocked:
            # Mağaza da 409 döndürür (K9). Burada durdurmanın sebebi kullanıcıyı
            # gerekçe yazıp gönderdikten SONRA reddetmemek.
            return {"ok": False, "blocked": True, "feature": "money",
                    "error": blocked}

        payload = {"status": status_id}
        await self._record(request_id=request_id, action="update", reason=reason, actor=actor,
                           result="denendi", detail=payload)
        try:
            result = await self._api.bbd_update_return_request(
                int(request_id), payload=payload, reason=reason, actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(request_id=request_id, action="update", reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}
        await self._record(request_id=request_id, action="update", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok", detail=payload)
        return {"ok": True, "error": "", "fields": ["status"],
                # "Onayladım" ile "para gitti" AYRI: mağaza onay geçişinin
                # panel düğmesini açtığını söylüyor, biz de söylüyoruz.
                "unlocksPanelRefund": bool(result.get("unlocksPanelRefundButton")),
                "customerNotified": bool(result.get("customerNotified", False)),
                "dryRun": bool(result.get("dryRun", dry_run))}

    async def set_items(self, request_id: int, *, selection: dict[str, int], reason: str,
                        actor: str, dry_run: bool = True) -> dict[str, Any]:
        """İade edilecek kalem adetleri.

        İKİ DOĞRULAMA, İKİ AYRI SEBEP:

         1. Adet sipariş adedini AŞAMAZ — daha önce iade edilmiş ve iptal
            edilmiş adet düşülür. Aşarsa mağaza iki kez para iade eder.
         2. Kalem TALEPTE ZATEN OLMALI. Mağaza kalem EKLEMİYOR (vendor'ın
            modelinde ilişki `HasOne`; panelin iade akışı talebin yalnızca ilk
            kalemini görüyor). Talepte olmayan bir satıra adet yazmak, ekranda
            "2 kalem" yazan ama bankaya tek kalem giden bir talep üretirdi.

        MAĞAZA `rma_items.id` İLE ADRESLİYOR, sipariş kalemi kimliğiyle değil.
        Ekran sipariş kalemi üzerinden çalıştığı için çeviri burada yapılır ve
        karşılığı olmayan satır AÇIKÇA reddedilir.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        wanted = {rma.as_int(key): rma.as_int(value) for key, value in (selection or {}).items()}
        wanted = {key: value for key, value in wanted.items() if key and value > 0}
        if not wanted:
            return {"ok": False, "error": "İade edilecek kalem seçilmedi."}

        card = await self.card(request_id)
        if not card.get("ok"):
            return {"ok": False, "error": card.get("error") or "Talep okunamadı."}
        if not card["items"]:
            return {"ok": False,
                    "error": "Siparişin kalemleri okunamadı; seçim doğrulanamadığı için "
                             "yazılmadı."}
        invalid = rma.selection_error(card["items"], wanted)
        if invalid:
            return {"ok": False, "error": invalid}

        known = {row["itemId"]: row for row in card["items"]}
        missing = [known[item_id]["name"] for item_id in wanted
                   if not known.get(item_id, {}).get("rmaItemId")]
        if missing:
            return {"ok": False, "blocked": True, "feature": "items",
                    "error": "Bu talepte olmayan kaleme adet yazılamaz — mağaza iade "
                             "talebine kalem EKLEMİYOR, yalnız var olanı güncelliyor. "
                             f"Talepte bulunmayan: {', '.join(missing)}. Yeni kalem için "
                             "Bagisto panelinden ayrı bir talep açılır."}

        estimate = rma.refund_estimate(card["items"], wanted)
        payload = {"items": [{"id": known[item_id]["rmaItemId"], "quantity": qty}
                             for item_id, qty in sorted(wanted.items())]}
        await self._record(request_id=request_id, action="set_items", reason=reason, actor=actor,
                           result="denendi", detail=payload)
        try:
            result = await self._api.bbd_update_return_request(
                int(request_id), payload=payload, reason=reason, actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(request_id=request_id, action="set_items", reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}
        await self._record(request_id=request_id, action="set_items", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={**payload, "amount": estimate["amount"]})
        return {"ok": True, "error": "", "estimate": estimate,
                "itemsUpdated": rma.as_int(result.get("itemsUpdated"), len(payload["items"])),
                "dryRun": bool(result.get("dryRun", dry_run))}

    async def decide(self, request_id: int, *, approve: bool, reason: str, actor: str,
                     dry_run: bool = True) -> dict[str, Any]:
        """Onayla / Reddet.

        ONAY PARA İADE ETMEZ. Bu ekran talebi "onaylandı" yapar ve seçili
        kalemleri İadeler ekranına DEVREDER (yerel kayıt + olay). Parayı iade
        etmek ayrı bir ekranın, ayrı bir iznin ve ayrı bir onayın işidir;
        buradan iade başlatmak, iade iznini olmayan personele para iade
        ettirirdi (K9).
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        card = await self.card(request_id)
        if not card.get("ok"):
            return {"ok": False, "error": card.get("error") or "Talep okunamadı."}

        chosen = [item for item in card["items"] if (item.get("qty") or 0) > 0]
        estimate = rma.refund_estimate(card["items"])
        if approve and card["request"]["type"] in ("return", "exchange") and not chosen:
            # İki farklı sebep, iki farklı cümle: "seçmedin" ile "okuyamadım"
            # aynı metne sığdırılırsa personel olmayan bir hatayı arar.
            if card["warnings"]:
                return {"ok": False,
                        "error": "Siparişin kalemleri okunamadı; hangi ürünün geri geleceği "
                                 f"doğrulanamadığı için onay yazılmadı ({card['warnings'][0]})."}
            return {"ok": False,
                    "error": "İade/değişim talebi kalem seçilmeden onaylanmaz; hangi ürünün "
                             "geri geleceği yazılı olmalı."}

        # KARAR MAĞAZANIN DURUM KİMLİĞİYLE YAZILIR: onay → "Accept" (2),
        # ret → "Declined" (7). İkisi de PARA HAREKETİ DOĞURMAZ.
        #
        # `resolution` diye bir alan GÖNDERİLMİYOR: mağaza tanımadığı alanla
        # gelen isteğin tamamını reddediyordu, yani gerekçe uğruna kararın
        # kendisi yazılmıyordu. Gerekçe zaten `X-Bbd-Reason` başlığıyla gidiyor
        # ve mağazanın denetim satırına yazılıyor.
        action = "approve" if approve else "reject"
        status = "approved" if approve else "rejected"
        payload = {"status": rma.DECISION_STATUS_IDS[action]}
        await self._record(request_id=request_id, action=action, reason=reason, actor=actor,
                           result="denendi", detail={"amount": estimate["amount"]})
        try:
            result = await self._api.bbd_update_return_request(
                int(request_id), payload=payload, reason=reason, actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(request_id=request_id, action=action, reason=reason, actor=actor,
                               result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        handed = False
        if approve and not dry_run and chosen:
            handed = await self._hand_off(request_id, card=card, chosen=chosen,
                                          estimate=estimate, reason=reason, actor=actor)
        await self._record(request_id=request_id, action=action, reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"amount": estimate["amount"], "handedOff": handed})
        return {"ok": True, "error": "", "status": status, "estimate": estimate,
                "handedOff": handed,
                # "ONAYLADIM" İLE "PARA GİTTİ" AYRI. Onay hiçbir para hareketi
                # yapmaz ama Bagisto panelindeki "bankaya iade gönder" düğmesinin
                # görünürlük koşuludur; ekran bunu yazmazsa operatör paranın
                # gittiğini sanır.
                "unlocksPanelRefund": bool(result.get("unlocksPanelRefundButton")),
                "customerNotified": bool(result.get("customerNotified", False)),
                "dryRun": bool(result.get("dryRun", dry_run))}

    async def _hand_off(self, request_id: int, *, card: dict[str, Any],
                        chosen: list[dict[str, Any]], estimate: dict[str, Any],
                        reason: str, actor: str) -> bool:
        """Onaylanan talebi İadeler'e devreder — yerel kayıt + olay.

        Olay yolunu dinleyen olmasa da (İadeler modülü kapalı olabilir) kayıt
        burada kalır; "onayladık ama iade açılmadı" durumu ekranda görünür.
        """
        items = [{"orderItemId": item["itemId"], "sku": item["sku"], "qty": item["qty"],
                  "unitPrice": item["unitPrice"]} for item in chosen]
        try:
            await self._store.execute(
                f"INSERT INTO {self._handoff} "
                "(request_id, order_id, amount, items, actor, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (int(request_id), int(card["request"]["orderId"]), int(estimate["amount"]),
                 json.dumps(items, ensure_ascii=False), actor, reason, _now()))
        except Exception as failure:  # noqa: BLE001 — devir kaydı yazılamadı
            self._log.warning("iade aktarımı yazılamadı", requestId=request_id,
                              error=str(failure))
            return False

        if self._publish is None:
            return True
        try:
            await self._publish("store_requests.approved", {
                "requestId": int(request_id),
                "orderId": int(card["request"]["orderId"]),
                "amount": int(estimate["amount"]),
                "items": items,
                "actor": actor,
                "reason": reason,
            })
        except Exception as failure:  # noqa: BLE001 — dinleyici patlarsa onay geri alınmaz
            self._log.warning("iade olayı yayınlanamadı", requestId=request_id,
                              error=str(failure))
        return True

    async def bulk(self, request_ids: list[int], *, action: str, value: str = "",
                   reason: str, actor: str, dry_run: bool = True) -> dict[str, Any]:
        """Toplu durum/atama/kapatma. SIRAYLA yazar — uzakta toplu uç yok."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        if action not in BULK_ACTIONS:
            return {"ok": False, "error": f"Bilinmeyen toplu işlem: {action}"}
        ids = [int(item) for item in (request_ids or []) if rma.as_int(item)]
        if not ids:
            return {"ok": False, "error": "Talep seçilmedi."}
        if len(ids) > BULK_LIMIT:
            return {"ok": False,
                    "error": f"Tek seferde en çok {BULK_LIMIT} talep. Mağazada toplu uç yok; "
                             "her talep ayrı istekle yazılır."}

        if action == "close":
            # KAPATMAK AYRI BİR İŞ DEĞİL, BİR DURUM YAZMAKTIR ("Solved").
            # Vitrin tarafı da tam olarak öyle yapıyor; ayrı bir "close" ucu
            # aynı gerçeği yazan ikinci bir yol olurdu.
            payload = {"status": rma.DECISION_STATUS_IDS["close"]}
        else:
            status_id = rma.as_int(value)
            blocked = rma.status_error(status_id)
            if blocked:
                return {"ok": False, "blocked": True, "feature": "money", "error": blocked}
            payload = {"status": status_id}

        applied = 0
        failed: list[int] = []
        error = ""
        for request_id in ids:
            try:
                await self._api.bbd_update_return_request(
                    request_id, payload=payload, reason=reason, actor=actor, dry_run=dry_run)
            except Exception as failure:  # noqa: BLE001 — biri patlarsa gerisi sürsün
                failed.append(request_id)
                error = error or self._fail(failure)
                await self._record(request_id=request_id, action=f"bulk_{action}", reason=reason,
                                   actor=actor, result="hata", detail={"error": str(failure)})
                continue
            applied += 1
        await self._record(request_id=0, action=f"bulk_{action}", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"applied": applied, "failed": failed, "payload": payload})
        return {"ok": not failed, "error": error, "applied": applied, "failed": failed,
                "dryRun": dry_run}

    # ========================================================= iade kargosu
    #
    # MÜŞTERİ ÜRÜNÜ NASIL GERİ GÖNDERECEK — bu ekranın cevapsız kalan sorusu.
    # Talep onaylandıktan sonra operatörün elinde talep vardır, gönderi kimliği
    # yoktur; mağaza tarafında bu yüzden ayrı bir denetleyici var ve girdisini
    # TALEP kimliğinden alır (`ReturnShipmentController`).
    #
    # ÜÇ ADIM AYRI DÜĞMEDİR VE YALNIZCA BİRİ PARA HARCAR:
    #   1. "İade kargosu aç"  → para YOK, mükerrer korumalı, geri alınabilir.
    #   2. "Etiketi satın al" → PARA HARCAR. Ayrı izin (`store_requests.purchase`),
    #      20 karakter gerekçe, `dryRun` varsayılanı açık.
    #   3. "Durumu tazele"    → yalnız okur; "ürün elimize ulaştı mı" bundan çıkar.
    #
    # Onaylanan her talep etiketle sonuçlanmaz: müşteri ürünü kendi getirebilir,
    # kargoya vermeden vazgeçebilir, talep sonradan reddedilebilir. Etiketi
    # onayla birlikte otomatik almak, bu üç durumun her birinde bize kesilmiş
    # bir fatura demektir.
    #
    # PARA İADESİ BURADAN YAPILMAZ ve kargo hareketi talebin DURUMUNU
    # DEĞİŞTİRMEZ. Panelin "Bankaya iade gönder" düğmesi yalnız talep
    # "Onaylandı" iken açıktır; talebi otomatik ilerletmek o düğmeyi tam da
    # ürün elimize ulaştığı anda KAPATIRDI.

    async def shipment(self, request_id: int) -> dict[str, Any]:
        """Talebin iade kargosu — SALT OKUMA, hiçbir şey açmaz.

        GÖNDERİ YOKSA HATA DEĞİLDİR: uç 200 + `shipment: null` döner ve ekran
        "kargo yok, açabilirsiniz" der. Uç ya da Geliver kapalıysa ekran
        DÜŞMEZ (K7): `connected: False` döner, talebin geri kalanı çalışır.

        Etiket künyesi AYRI ve İSTEĞE BAĞLI bir istektir: yalnız etiket satın
        alınmışsa sorulur. Alınmamışken sormak her satır için bir 409 üretir
        ve hız kovasından boşuna pay yer.
        """
        try:
            raw = await self._api.bbd_return_request_shipment(int(request_id))
        except Exception as failure:  # noqa: BLE001 — K7: talep ekranı düşmesin
            self._log.info("iade kargosu okunamadı", requestId=request_id, error=str(failure))
            return {"ok": False, "connected": False, "error": self._fail(failure),
                    "shipment": rma.return_shipment_row({}), "label": {}}

        row = rma.return_shipment_row(raw)
        label: dict[str, Any] = {}
        if row["labelPurchased"]:
            try:
                label = await self._api.bbd_return_request_label_info(int(request_id))
            except Exception as failure:  # noqa: BLE001 — künye yoksa kargo yine görünür
                self._log.info("iade etiketi künyesi okunamadı", requestId=request_id,
                               error=str(failure))
        return {"ok": True, "connected": True, "error": "", "shipment": row,
                "label": label if isinstance(label, dict) else {}}

    async def open_shipment(self, request_id: int, *, reason: str, actor: str,
                            dry_run: bool = True) -> dict[str, Any]:
        """İade gönderisini açar. PARA HARCAMAZ, ETİKET ALMAZ.

        Mükerrer korumalıdır: aynı talep için ikinci gönderi açılmaz, var olan
        döner. Bu yüzden düğmeye iki kez basmak hata göstermez.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        await self._record(request_id=request_id, action="open_return_shipment", reason=reason,
                           actor=actor, result="denendi")
        try:
            result = await self._api.bbd_open_return_request_shipment(
                int(request_id), reason=reason, actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(request_id=request_id, action="open_return_shipment",
                               reason=reason, actor=actor, result="hata",
                               detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        dry = bool(result.get("dryRun", dry_run))
        await self._record(request_id=request_id, action="open_return_shipment", reason=reason,
                           actor=actor, result="dry_run" if dry else "ok")
        return {"ok": True, "error": "", "dryRun": dry,
                "shipment": rma.return_shipment_row(result),
                "notice": "Gönderi açıldı; ETİKET SATIN ALINMADI. Etiket ayrı bir düğmeyle "
                          "ve ayrı yetkiyle alınır — onaylanan her talep etiketle "
                          "sonuçlanmaz." if not dry else
                          "Kuru prova: mağazaya gerçek istek gönderilmedi."}

    async def sync_shipment(self, request_id: int, *, reason: str, actor: str,
                            dry_run: bool = True) -> dict[str, Any]:
        """Kargo durumunu taşıyıcıdan tazeler. YALNIZ OKUR.

        "Ürün elimize ulaştı mı" sorusunun cevabı buradan gelir. Teslim
        damgası bir kez atıldıktan sonra geri alınmaz; talebin DURUMU ise
        değişmez (para iadesi panelden yapılır).
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        try:
            result = await self._api.bbd_sync_return_request_shipment(
                int(request_id), reason=reason, actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(request_id=request_id, action="sync_return_shipment",
                               reason=reason, actor=actor, result="hata",
                               detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        dry = bool(result.get("dryRun", dry_run))
        row = rma.return_shipment_row(result)
        await self._record(request_id=request_id, action="sync_return_shipment", reason=reason,
                           actor=actor, result="dry_run" if dry else "ok",
                           detail={"stage": row["stage"], "received": row["received"]})
        return {"ok": True, "error": "", "dryRun": dry, "shipment": row}

    async def purchase_label(self, request_id: int, *, offer_id: str = "", reason: str,
                             actor: str, dry_run: bool = True) -> dict[str, Any]:
        """İADE ETİKETİNİ SATIN ALIR — PARA HARCAR, GERİ ALINAMAZ.

        Gerekçe burada 20 karakter ister; uçtaki şema da öyle. İki yerde
        doğrulanır çünkü istemci şemayı atlatabilir (K9). Yazma ÖNCESİ de
        denetim izine satır atılır: istek zaman aşımına uğrarsa uzakta
        uygulanmış olabilir ve "ne yapmaya çalıştık" kaydı yerelde kalmalı.

        Mağaza ayrıca kendi kapısını tutar: ayrı yetki (`sales.geliver.purchase`),
        `dryRun` varsayılanı açık ve satın alınmış gönderiye ikinci ödeme yok.
        """
        problem = rma.purchase_reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}

        await self._record(request_id=request_id, action="purchase_return_label", reason=reason,
                           actor=actor, result="denendi", detail={"offerId": rma.text(offer_id)})
        try:
            result = await self._api.bbd_purchase_return_request_label(
                int(request_id), offer_id=rma.text(offer_id), reason=reason, actor=actor,
                dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(request_id=request_id, action="purchase_return_label",
                               reason=reason, actor=actor, result="hata",
                               detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        dry = bool(result.get("dryRun", dry_run))
        # KURU PROVADA TEKLİF LİSTESİ GELİR: ekran hangi taşıyıcının ne kadara
        # alınacağını satın almadan önce gösterir. ZARF AÇILIR — mağaza prova
        # yanıtını `wouldChange` içine sarıyor; kökten okumak listeyi her
        # provada BOŞ gösterir ve ekran "teklif yok" derdi.
        body = rma.dry_run_body(result)
        offers = [item for item in (body.get("offers") or []) if isinstance(item, dict)]
        await self._record(request_id=request_id, action="purchase_return_label", reason=reason,
                           actor=actor, result="dry_run" if dry else "ok",
                           detail={"offerId": rma.text(offer_id), "offers": len(offers)})
        return {"ok": True, "error": "", "dryRun": dry, "offers": offers,
                "offersReady": bool(body.get("offersReady")),
                "shipment": rma.return_shipment_row(result),
                "notice": ("Kuru prova: PARA HARCANMADI. Listeden teklif seçip “uygula” "
                           "işaretlediğinizde etiket satın alınır."
                           if dry else "Etiket satın alındı; tutar kargo hesabınızdan çıktı.")}

    async def label(self, request_id: int) -> dict[str, Any]:
        """Satın alınmış iade etiketini PDF olarak diske yazar ve yolunu döner.

        BAYT İSTENİR, ADRES DEĞİL: Geliver'ın verdiği adres süreli imzalıdır
        ve imza ölünce elde çalışmayan bir bağlantı kalır. Mağaza dosyayı
        satın alma anında saklıyor; burada o kopya indirilir.

        Etiket hazır değilse uç 409 döner ve mesajı doğrudan gösterilebilir
        ("etiket henüz satın alınmadı" / "dosya okunamadı, senkronlayın").
        """
        try:
            raw = await self._api.bbd_return_request_label(int(request_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        if not raw:
            return {"ok": False,
                    "error": "Mağaza boş bir etiket dosyası döndürdü; gönderiyi senkronlayıp "
                             "tekrar deneyin."}

        name = f"iade-etiketi-talep-{int(request_id)}.pdf"
        try:
            path = write_private(self._export_dir / name, bytes(raw))
        except OSError as failure:
            return {"ok": False, "error": f"Dosya yazılamadı: {failure}"}
        await self._record(request_id=request_id, action="download_return_label", reason="",
                           actor="", result="ok", detail={"bytes": len(raw)})
        return {"ok": True, "error": "", "path": str(path), "name": name, "bytes": len(raw)}

    # ================================================================= rapor

    async def _scan(self, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
        """Rapor için tarama — sayfa sayfa, tavanlı."""
        hours = self._hours
        rows: list[dict[str, Any]] = []
        page = 1
        truncated = False
        while True:
            payload = await self._api.bbd_return_requests(filters, page=page, per_page=50)
            batch = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
            rows.extend(rma.request_row(item, hours=hours) for item in batch)
            meta = payload.get("meta") or {}
            last = rma.as_int(meta.get("lastPage"), page)
            if len(rows) >= REPORT_ROW_CAP:
                # "Tavana dayandı" ancak GERÇEKTEN dışarıda kayıt kaldıysa
                # söylenir: tam 2.000 kayıtlık eksiksiz bir raporu "eksik
                # olabilir" diye damgalamak, kullanıcıyı olmayan veriyi
                # aramaya gönderirdi.
                truncated = len(rows) > REPORT_ROW_CAP or page < last
                rows = rows[:REPORT_ROW_CAP]
                break
            if page >= last or not batch:
                break
            page += 1
        return rows, truncated

    async def export_csv(self, *, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """TÜM kayıtların CSV'si. Görünen sayfanın CSV'sini panel kendi üretir."""
        try:
            rows, truncated = await self._scan(dict(filters or {}))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        headers, table = rma.csv_table(rows)
        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        name = f"magaza-talep-listesi-{stamp}.csv"
        try:
            path = write_private(self._export_dir / name, csv_bytes(headers, table))
        except (OSError, ExportError) as failure:
            return {"ok": False, "error": f"Dosya yazılamadı: {failure}"}
        return {"ok": True, "error": "", "path": str(path), "name": name,
                "rows": len(table), "truncated": truncated}

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
        if kind == "rma":
            return await self._rma_form(rma.as_int(params.get("requestId")))
        if kind == "sla":
            return await self._sla_report(params)
        return {"ok": False, "error": f"Bilinmeyen rapor: {kind}"}

    async def _rma_form(self, request_id: int) -> dict[str, Any]:
        """RMA formu — MÜŞTERİYE gider, kutuya konur, yazdırılır."""
        if not request_id:
            return {"ok": False, "error": "Form için talep seçilmeli."}
        card = await self.card(request_id)
        if not card.get("ok"):
            return {"ok": False, "error": card.get("error") or "Talep okunamadı."}

        row = card["request"]
        chosen = [item for item in card["items"] if (item.get("qty") or 0) > 0]
        estimate = rma.refund_estimate(card["items"])
        sections: list[dict[str, Any]] = [{
            "kind": "tiles", "title": "Talep künyesi",
            "tiles": [("Talep no", row["code"]), ("Tür", row["typeLabel"]),
                      ("Sipariş", row["orderNumber"] or f"#{row['orderId']}"),
                      ("Açılış", row["createdAt"] or "—")],
        }, {
            "kind": "tiles", "title": "",
            "tiles": [("Müşteri", row["customerName"]), ("Durum", row["statusLabel"]),
                      ("Geri gönderim kodu", row["returnCode"] or "—"),
                      ("Kalem tutarı", money(estimate["amount"]))],
        }]

        if chosen:
            sections.append({
                "kind": "table", "title": "İade edilecek kalemler",
                "headers": ["SKU", "Ürün", "Adet", "Birim", "Tutar"],
                "align": "LLRRR", "widths": [1.1, 3, 0.6, 1, 1],
                "rows": [[item["sku"], item["name"], number(item["qty"]),
                          money(item["unitPrice"]), money(item["qty"] * item["unitPrice"])]
                         for item in chosen],
            })
        else:
            sections.append({"kind": "note",
                             "text": "Kalem seçilmemiş. Kutuya konan ürünleri müşteri bu "
                                     "formun arkasına elle yazabilir."})

        sections.append({
            "kind": "note",
            "text": "Bu formu iade paketinin İÇİNE koyunuz. Ürünler kullanılmamış, eksiksiz "
                    "ve orijinal ambalajında olmalıdır. Kargo geri gönderim kodu yukarıdadır; "
                    "kod yoksa müşteri hizmetleri ile iletişime geçiniz.",
        })
        sections.append({
            "kind": "table", "title": "Teslim / kabul",
            "headers": ["Gönderen (ad, soyad, imza)", "Tarih", "Teslim alan (ad, imza)"],
            "align": "LLL", "widths": [2, 1, 2],
            "rows": [["", "", ""], ["", "", ""]],
        })

        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        name = f"magaza-rma-formu-{request_id}-{stamp}.pdf"
        try:
            # `build_pdf` de ExportError fırlatır (reportlab kurulu değilse).
            # Try'ın DIŞINDA bırakılırsa istisna route'a kadar çıkar ve servis
            # "HTTP hatası fırlatmaz" sözleşmesi kırılır (K7).
            content = build_pdf(
                title="İade / Değişim Formu (RMA)",
                subtitle=f"{row['code']} · {row['typeLabel']} · {row['customerName']}",
                sections=sections, footer="Kontrol Merkezi · BBD Store")
            path = write_private(self._export_dir / name, content)
        except (OSError, ExportError) as failure:
            return {"ok": False, "error": str(failure)}
        self._log.info("RMA formu üretildi", requestId=request_id, path=str(path))
        return {"ok": True, "error": "", "path": str(path), "name": name,
                "bytes": len(content), "rows": len(chosen)}

    async def _sla_report(self, params: dict[str, Any]) -> dict[str, Any]:
        filters = rma.list_filters(
            kind=rma.text(params.get("type")), status=rma.text(params.get("status")),
            assignee=rma.text(params.get("assignee")), start=rma.text(params.get("start")),
            end=rma.text(params.get("end")))
        try:
            rows, truncated = await self._scan(filters)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        if not rows:
            return {"ok": False, "error": "Bu süzgeçte rapora girecek talep yok."}

        summary = rma.sla_summary(rows)
        late = sorted([row for row in rows if row["sla"]["state"] == "overdue"],
                      key=lambda row: row["sla"]["hoursLeft"] or 0)
        by_person: dict[str, dict[str, int]] = {}
        for row in rows:
            slot = by_person.setdefault(row["assignee"] or "(atanmamış)",
                                        {"total": 0, "overdue": 0})
            slot["total"] += 1
            if row["sla"]["state"] == "overdue":
                slot["overdue"] += 1

        sections: list[dict[str, Any]] = [{
            "kind": "tiles", "title": "Özet",
            "tiles": [("Talep", number(summary["total"])), ("Açık", number(summary["open"])),
                      ("Süresi geçen", number(summary["overdue"])),
                      ("Bugün doluyor", number(summary["today"]))],
        }, {
            "kind": "table", "title": "Kişi bazlı yük",
            "headers": ["Atanan", "Talep", "Süresi geçen"],
            "align": "LRR", "widths": [3, 1, 1],
            "rows": [[name, number(slot["total"]), number(slot["overdue"])]
                     for name, slot in sorted(by_person.items(),
                                              key=lambda pair: -pair[1]["overdue"])],
        }]
        if late:
            sections.append({
                "kind": "table", "title": "Süresi geçen talepler",
                "headers": ["Talep no", "Açılış", "Tür", "Müşteri", "Atanan", "Gecikme (saat)"],
                "align": "LLLLLR", "widths": [1, 1.2, 0.8, 2, 1.4, 1],
                "rows": [[row["code"], row["createdAt"], row["typeLabel"], row["customerName"],
                          row["assignee"] or "—",
                          number(abs(row["sla"]["hoursLeft"] or 0), 1)] for row in late[:200]],
            })
        if truncated:
            sections.append({"kind": "note",
                             "text": "Liste tavana dayandı; rapor eksik olabilir."})

        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        name = f"magaza-talep-sla-{stamp}.pdf"
        try:
            # `build_pdf` de ExportError fırlatır; try'ın içinde durmalı (K7).
            content = build_pdf(
                title="SLA raporu",
                subtitle=f"{summary['total']} talep · {summary['overdue']} gecikmiş",
                sections=sections, footer="Kontrol Merkezi · BBD Store")
            path = write_private(self._export_dir / name, content)
        except (OSError, ExportError) as failure:
            return {"ok": False, "error": str(failure)}
        return {"ok": True, "error": "", "path": str(path), "name": name,
                "bytes": len(content), "rows": len(rows), "truncated": truncated}

    async def _render_pages(self, path: Path, *, max_pages: int = 12,
                            dpi: int = 110) -> list[str]:
        binary = shutil.which("pdftoppm")
        if not binary:
            raise PreviewError(
                "Önizleme üretilemedi: `pdftoppm` yok (poppler-utils kurulmalı). "
                "Rapor yine de kaydedildi ve yazdırılabilir.")
        with tempfile.TemporaryDirectory(prefix="km-talep-onizleme-") as folder:
            target = Path(folder) / "sayfa"
            try:
                process = await asyncio.create_subprocess_exec(
                    binary, "-png", "-r", str(int(dpi)), "-f", "1", "-l", str(int(max_pages)),
                    str(path), str(target),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                _, err = await asyncio.wait_for(process.communicate(), timeout=60)
            except TimeoutError:
                # Süre dolduğunda `wait_for` yalnız beklemeyi bırakır; süreç
                # kendi başına çalışmaya devam eder ve geçici klasör silinirken
                # asılı kalır. Açıkça öldürülür.
                with contextlib.suppress(ProcessLookupError, OSError):
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
