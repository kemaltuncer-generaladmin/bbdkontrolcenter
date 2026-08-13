"""UDİT İşlem Kayıtları — iş kuralları.

BU EKRAN SALT OKUNURDUR. Burada tek bir yazma çağrısı yoktur; `store.api`
geçidinin denetim uçları da yalnız okumadır (yazma metodu bilerek yok). Modülün
yerel tablosu bile denetim kaydına değil, DENETİM DÖKÜMÜ ALINMASINA aittir:
kaydı okumak serbesttir, kaydı binlerce satır hâlinde dışarı çıkarmak iz
bırakır.

İKİ KAYNAK, TEK ZAMAN ÇİZGİSİ (ayrıntı: `records.py` başlığı):
  · uzak `admin_api_audits` — başarılı yazmalar + alan farkı, gerekçe YOK
  · yerel `mod_store_api_audit` — gerekçe + gönderilemeyen/reddedilen istek

SAYFALAMA. 20 ekran içinde imleçli sayfalama kullanan TEK ekran budur; çünkü
kayıt sayısı sınırsız büyür ve "sayfa 412/9.318" diye bir şey kullanıcıya bir
şey anlatmaz. Tarih aralığı ZORUNLUDUR: açık uçlu denetim sorgusu mağazayı
dakikalarca meşgul eder ve ekranı da doldurmaz.

UZAK SİSTEM DÜŞERSE EKRAN AYAKTA KALIR (K7) ve DAHASINI YAPAR: mağaza
erişilemezken bile geçidin yerel izi gösterilir — "ne yapmaya çalıştık" sorusu
tam da o anda sorulur.
"""

from __future__ import annotations

import asyncio
import base64
import os
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from km_sdk import ExportError, build_pdf, csv_bytes, number, report_dir, write_private

from . import records

#: Uzak uç sayfa boyu. Geçit (`store_api/backend/paging.py`) sunucunun
#: `MAX_PER_PAGE = 50` sınırını uyguluyor ve daha büyüğünü SESSİZCE 50'ye
#: kırpıyor. 100'lük sayfa bu yüzden iki uzak istekle dolar; 100 istemek
#: "yarım sayfa aldım ama hepsi sandım" hatasını üretirdi.
REMOTE_PAGE = 50

#: Rapor/CSV üretirken taranacak en çok kayıt. Denetim kaydı sınırsız büyür;
#: tavan olmadan "son bir yılın dökümü" isteği sunucuyu da diski de yorar.
REPORT_ROW_CAP = 2_000

#: Tek istekte çekilecek en çok uzak sayfa. `need` kadar satır toplanamazsa
#: ekran "daha var" der ve imleç bir sonraki sayfayı getirir.
MAX_REMOTE_PAGES = 6

#: Döküm üretirken gezilecek en çok imleç sayfası. Mağaza süzgeci uygulamazsa
#: süzme sayfa İÇİNDE yapılır ve tek eşleşen satır için onlarca sayfa gezilir;
#: sınırsız tarama geçidin 55 istek/dk kovasını dakikalarca kilitler ve ekran
#: donmuş görünür. Sınıra dayanan döküm "eksik olabilir" DER — sessizce yarım
#: kalmaz; yarım olduğunu söylemeyen denetim dökümü yanlış dökümden beterdir.
MAX_SCAN_PAGES = 60


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


class PreviewError(RuntimeError):
    """Önizleme görüntüsü üretilemedi. Rapor yine de kaydedilmiştir."""


class AuditService:
    """UDİT ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ."""

    def __init__(self, *, api: Any, store: Any, log: Any, config: dict[str, Any],
                 printer: Any = None, category: str = "Mağaza", subcategory: str = "Denetim",
                 fallback_dir: Path | None = None) -> None:
        self._api = api
        self._store = store
        self._log = log
        self._config = config or {}
        self._printer = printer
        self._category = category
        self._subcategory = subcategory
        self._fallback = fallback_dir or Path.home() / "km-raporlar"

        self._exports = store.table("exports")

    # ------------------------------------------------------------- ayarlar

    @property
    def _page_size(self) -> int:
        return max(25, min(250, records.as_int(self._config.get("page_size"),
                                               records.DEFAULT_PAGE_SIZE)))

    @property
    def _max_range_days(self) -> int:
        return max(1, min(400, records.as_int(self._config.get("max_range_days"), 92)))

    @property
    def _local_limit(self) -> int:
        """Geçit izinden bir seferde okunacak satır sayısı.

        Yerel izin tarih süzgeci YOKTUR (`audit_trail` yalnız `limit` alır);
        bu yüzden son N satır okunup burada süzülür. Tavan dolduğunda ekran
        "geçit izi eksik olabilir" der — sessizce eksik liste göstermez.
        """
        return max(100, min(5_000, records.as_int(self._config.get("local_scan_limit"), 800)))

    @property
    def _export_dir(self) -> Path:
        # HER ÇAĞRIDA yeniden çözülür: ay değişince klasör kendiliğinden değişir.
        return report_dir(self._category, subcategory=self._subcategory,
                          fallback=self._fallback,
                          configured=str(self._config.get("export_path") or ""))

    @staticmethod
    def _fail(failure: Exception) -> str:
        message = str(failure).strip()
        return message or "Mağazaya ulaşılamadı."

    # ================================================================ liste

    async def entries(self, *, start: str, end: str, q: str = "", user: str = "",
                      action: str = "", entity: str = "", entity_id: int = 0, ip: str = "",
                      result: str = "", source: str = "", destructive: bool = False,
                      reasoned: bool = False, cursor: str = "",
                      size: int = 0) -> dict[str, Any]:
        """Bir imleç sayfası. Tarih aralığı ZORUNLUDUR."""
        first, last, problem = records.range_bounds(start, end)
        if problem:
            return self._empty(problem, guard=True)
        span = records.range_days(first, last)
        if span > self._max_range_days:
            return self._empty(
                f"Aralık {span} gün; en çok {self._max_range_days} gün sorgulanabilir. "
                "Aralığı daraltın.", guard=True)

        position = records.decode_cursor(cursor)
        if position is None:
            return self._empty("İmleç okunamadı; listeyi baştan yükleyin.", guard=True)
        remote_offset, local_offset, after = position

        filters = {"start": first, "end": last, "q": q, "user": user, "action": action,
                   "entity": entity, "entityId": entity_id, "ip": ip, "result": result,
                   "source": source, "destructive": destructive, "reasoned": reasoned}
        wanted = size or self._page_size

        local_rows, local_error, local_truncated = await self._local_rows()
        remote_rows, remote_total, remote_more, remote_error = await self._remote_rows(
            filters, offset=remote_offset, need=wanted)

        # Uzak satırın gerekçesi geçit izinden gelir (records.join_reason).
        records.join_reason(remote_rows, local_rows)

        local_window = records.local_only(local_rows)[local_offset:]
        # Kaynak süzgeci yalnızca DIŞ pencereyi daraltır; ofsetler kaynaktaki
        # gerçek konumu göstermeye devam etsin diye eleme `keep` ile yapılır.
        #
        # `scope` = mağazaya GERÇEKTEN giden süzgeçler. Sunucunun süzgeci
        # uygulayıp uygulamadığını yalnız bunlarla ölçebiliriz: `source` ve
        # `reasoned` mağazaya hiç gitmez, onlar yüzünden elenen satır sunucunun
        # kusuru değildir ve "sunucu süzmedi" uyarısını YANLIŞ yere astırırdı.
        scope = records.remote_scope(filters)
        dropped = {"remote": 0}

        def keep(row: dict[str, Any]) -> bool:
            ok = records.matches(row, filters)
            if not ok and row.get("source") == "store" and not records.matches(row, scope):
                dropped["remote"] += 1
            return ok

        merged = records.merge_page(remote_rows, local_window, size=wanted, after=after,
                                    keep=keep)
        rows = merged["rows"]

        next_remote = remote_offset + merged["usedRemote"]
        next_local = local_offset + merged["usedLocal"]
        used = merged["usedRemote"] + merged["usedLocal"]
        has_more = bool(merged["more"] or remote_more)
        next_cursor = ""
        if has_more and (rows or used):
            # SAYFA TAMAMEN ELENMİŞ OLABİLİR. Mağaza süzgeci uygulamadığında
            # (Laravel tanımadığı parametreyi sessizce yok sayar) bir sayfanın
            # tüm satırları burada elenir. Böyle bir sayfada imleci BOŞ döndürmek
            # listeyi kilitler: "Sonraki sayfa" düğmesi ilk sayfayı yeniden
            # yükler ve döküm sessizce "kayıt yok" der — oysa kayıt vardır,
            # yalnız bir sayfa ötededir. Satır yoksa `after` sınırı DEĞİŞMEZ:
            # ofsetler zaten elenen satırların ötesine geçti.
            next_cursor = records.encode_cursor(next_remote, next_local,
                                                rows[-1]["sortKey"] if rows else after)

        warnings: list[str] = []
        sent = records.remote_filters(filters)
        server_filtered = True
        if dropped["remote"] and sent:
            # Mağaza süzgeci uygulamadı: satırlar burada elendi. Sayfa DOLU
            # görünmeyebilir ve "kaç kayıt var" sayısı yanıltıcı olur.
            server_filtered = False
            warnings.append(
                "Mağaza süzgeci uygulamadı; kayıtlar sayfa içinde süzüldü. "
                "Sayfa doluluğu ve toplam sayı yanıltıcı olabilir.")
        if local_truncated:
            warnings.append(
                f"Geçit izinden yalnız son {self._local_limit} satır okundu; "
                "gönderilemeyen eski istekler bu listede olmayabilir.")
        if local_error:
            warnings.append(f"Geçit izi okunamadı: {local_error}")

        return {
            "ok": True,
            "connected": not remote_error,
            "error": remote_error,
            "items": rows,
            "count": len(rows),
            "cursor": next_cursor,
            "hasMore": has_more,
            "remoteTotal": remote_total,
            "serverFiltered": server_filtered,
            "warnings": warnings,
            "range": {"start": first, "end": last, "days": span},
            "readOnly": True,
        }

    def _empty(self, error: str, *, guard: bool = False) -> dict[str, Any]:
        """Süzgeç kapısına takılan istek. `ok` True'dur: bu bir HATA DEĞİL,
        ekranın kullanıcıya söylemesi gereken bir kuraldır."""
        return {"ok": True, "connected": False, "error": error, "items": [], "count": 0,
                "cursor": "", "hasMore": False, "remoteTotal": 0, "serverFiltered": True,
                "warnings": [], "range": {}, "readOnly": True, "guard": guard}

    async def _local_rows(self) -> tuple[list[dict[str, Any]], str, bool]:
        """Geçidin yerel izi. Uzak sistemden BAĞIMSIZ okunur (K7)."""
        try:
            raw = await self._api.audit_trail(limit=self._local_limit)
        except Exception as failure:  # noqa: BLE001 — iz okunamazsa uzak liste yine gelsin
            self._log.warning("geçit izi okunamadı", error=str(failure))
            return [], self._fail(failure), False
        rows = [records.local_row(item) for item in (raw or [])]
        rows.sort(key=lambda row: row["sortKey"], reverse=True)
        return rows, "", len(rows) >= self._local_limit

    async def _remote_rows(self, filters: dict[str, Any], *, offset: int,
                           need: int) -> tuple[list[dict[str, Any]], int, bool, str]:
        """Uzak denetim tablosundan `offset` konumundan başlayan pencere.

        Uzak taraf OFSETLİ sayfalıyor, biz imleçli sayfalıyoruz: imleç uzak
        kaynaktaki konumu (satır numarası) taşır ve burada sayfa numarasına
        çevrilir. Sayfa boyu 50'de sabittir (bkz. `REMOTE_PAGE`).
        """
        page = offset // REMOTE_PAGE + 1
        skip = offset % REMOTE_PAGE
        query = records.remote_filters(filters)
        collected: list[Any] = []
        total = 0
        more = False
        fetched = 0

        while len(collected) < need + skip and fetched < MAX_REMOTE_PAGES:
            try:
                payload = await self._api.bbd_audit(query, page=page, per_page=REMOTE_PAGE)
            except Exception as failure:  # noqa: BLE001 — ekran ayakta kalmalı (K7)
                self._log.warning("denetim kayıtları okunamadı", page=page, error=str(failure))
                window = [records.remote_row(item) for item in collected[skip:]]
                return window, total, False, self._fail(failure)

            items = payload.get("items") or []
            meta = payload.get("meta") or {}
            total = records.as_int(meta.get("total"), total)
            collected.extend(items)
            fetched += 1

            last_page = records.as_int(meta.get("lastPage"))
            if len(items) < REMOTE_PAGE or (last_page and page >= last_page):
                more = False
                break
            page += 1
            more = True

        window = [records.remote_row(item) for item in collected[skip:]]
        if len(window) > need:
            more = True
        return window, total, more, ""

    # =============================================================== ayrıntı

    async def entry(self, key: str) -> dict[str, Any]:
        """Tek kaydın tam hâli: kırpılmamış fark tablosu + ham JSON.

        Listede taşınan fark KIRPILMIŞTIR (80 alan, 4 KB ham JSON); toplu ayar
        yazmalarında kırpılmamış gövde sayfayı megabaytlara çıkarıyordu. Çekmece
        açılınca kaydın tamamı buradan tazelenir.
        """
        raw_key = records.text(key)
        if raw_key.startswith("r:"):
            entry_id = records.as_int(raw_key[2:])
            if not entry_id:
                # Mağaza kaydı numara taşımıyorsa (`r:<damga>-<varlık>-<no>`)
                # tazeleyemeyiz. `ok: False` döndürmek çekmeceye kırmızı bir
                # hata astırırdı; oysa listedeki kırpılmış hâl DOĞRUDUR — yalnız
                # eksiktir. Ekran bunu söyler ve elindekini göstermeye devam eder.
                return {"ok": True, "connected": True, "item": None,
                        "error": "Bu kayıt mağaza tarafında numara taşımıyor; "
                                 "listedeki hâli gösteriliyor."}
            try:
                payload = await self._api.bbd_audit_entry(entry_id)
            except Exception as failure:  # noqa: BLE001 — K7
                return {"ok": False, "connected": False, "error": self._fail(failure)}
            row = records.remote_row(payload)
            local, _, _ = await self._local_rows()
            records.join_reason([row], local)
            return {"ok": True, "connected": True, "error": "", "item": row}

        if raw_key.startswith("g:"):
            wanted = raw_key[2:]
            local, error, _ = await self._local_rows()
            for row in local:
                if row["key"] == raw_key or row["requestId"] == wanted:
                    return {"ok": True, "connected": True, "error": "", "item": row}
            return {"ok": False, "error": error or "Geçit izinde bu kayıt yok."}

        return {"ok": False, "error": "Bilinmeyen kayıt anahtarı."}

    # ============================================== store.audit.for yeteneği

    async def history(self, entity: str, entity_id: Any = 0, *, limit: int = 50,
                      days: int = 0) -> dict[str, Any]:
        """`store.audit.for` — BİR KAYDIN işlem geçmişi.

        19 panelin çekmecesindeki "İşlem geçmişi" sekmesi budur. İmza bilerek
        DAR tutulmuştur: varlık adı + kayıt numarası + kaç satır. Süzgeç,
        sayfalama ve imleç bu yüzeyde YOKTUR — çağıran panel bir kaydın
        geçmişini gösterir, denetim ekranı yazmaz. Daha fazlası gerekiyorsa
        panel `open('store_udit_logs', {entity, entityId})` ile bu ekrana
        geçer; sözleşme genişlemez, ekran devralır.

        `days` verilmezse yapılandırmadaki tavan (varsayılan 92 gün) kullanılır:
        açık uçlu denetim sorgusu yasaktır, yetenek de bu kuralın dışında
        değildir.
        """
        name = records.text(entity)
        if not name:
            # Varlık adı olmadan bu yetenek "bir kaydın geçmişi" değil, tüm
            # denetim kaydı olurdu. Çağıran panel yanlışlıkla boş ad geçtiğinde
            # çekmecesine mağazanın TAMAMINI dökmek sessiz bir felakettir;
            # ekranın kendisi zaten süzgeçsiz listeyi gösterebiliyor.
            return {"ok": False, "connected": False,
                    "error": "İşlem geçmişi için varlık adı zorunludur.",
                    "items": [], "hasMore": False, "range": {}, "readOnly": True,
                    "screen": "store_udit_logs"}

        window = max(1, min(self._max_range_days, days or self._max_range_days))
        today = datetime.now(UTC).astimezone().date()
        start = (today - timedelta(days=window - 1)).isoformat()
        page = await self.entries(start=start, end=today.isoformat(),
                                  entity=name,
                                  entity_id=records.as_int(entity_id),
                                  size=max(1, min(200, int(limit))))
        return {
            "ok": True,
            "connected": page.get("connected", False),
            "error": page.get("error", ""),
            "items": page.get("items", []),
            "hasMore": page.get("hasMore", False),
            "range": page.get("range", {}),
            "readOnly": True,
            # Panel bu bilgiyle "tümünü gör" düğmesini kurar; hedefi kendi
            # bilmez, biz söyleriz (K3).
            "screen": "store_udit_logs",
        }

    # =============================================================== referans

    async def reference(self) -> dict[str, Any]:
        """Süzgeçlerin beslendiği listeler. Kullanıcı listesi mağazadan gelir."""
        out: dict[str, Any] = {
            "ok": True, "connected": True, "error": "",
            "users": [],
            "entities": [{"value": key, "label": label}
                         for key, label in sorted(records.ENTITY_LABELS.items(),
                                                  key=lambda item: item[1])],
            "actions": [{"value": key, "label": label} for key, label in (
                ("create", "Oluşturma"), ("update", "Güncelleme"), ("delete", "Silme"),
                ("login", "Giriş"), ("export", "Dışa aktarma"),
                ("configuration", "Ayar değişikliği"))],
            "results": [{"value": key, "label": view[0]} for key, view in
                        records.RESULT_VIEW.items() if key != "error"],
            "maxRangeDays": self._max_range_days,
            "pageSize": self._page_size,
        }
        try:
            payload = await self._api.admin_users()
        except Exception as failure:  # noqa: BLE001 — K7: süzgeç serbest metne düşer
            out["connected"] = False
            out["error"] = self._fail(failure)
            return out
        for item in payload.get("items") or []:
            name = records.text(item.get("name") or item.get("full_name") or item.get("email"))
            if name:
                out["users"].append({"value": name, "label": name})
        out["users"].sort(key=lambda item: item["label"])
        return out

    # ================================================================= tarama

    async def _scan(self, filters: dict[str, Any], *, cap: int = REPORT_ROW_CAP) -> tuple[
            list[dict[str, Any]], bool, str]:
        """Aralığın tamamını imleçle tarar. Tavana dayanırsa SÖYLER.

        İki ayrı tavan var ve İKİSİ DE `truncated` üretir: satır tavanı
        (`cap`) ve sayfa bütçesi (`MAX_SCAN_PAGES`). İkincisi, mağaza süzgeci
        uygulamadığında devreye girer: sayfalar dolu gelir ama içlerinden
        eşleşen çıkmaz. Bütçeyi sessizce tüketip `truncated=False` demek,
        eksik dökümü tam döküm diye imzalatmak olurdu.
        """
        rows: list[dict[str, Any]] = []
        cursor = ""
        error = ""
        for _ in range(MAX_SCAN_PAGES):
            page = await self.entries(cursor=cursor, size=records.DEFAULT_PAGE_SIZE, **filters)
            error = error or records.text(page.get("error"))
            rows.extend(page.get("items") or [])
            cursor = records.text(page.get("cursor"))
            if len(rows) >= cap:
                return rows[:cap], True, error
            if not page.get("hasMore") or not cursor:
                break
        else:
            return rows, True, error
        return rows, False, error

    # ================================================================= rapor

    async def preview(self, kind: str, params: dict[str, Any] | None = None,
                      *, actor: str = "") -> dict[str, Any]:
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
        """`dump` = aralığın dökümü · `record` = tek kaydın tüm geçmişi."""
        if kind not in ("dump", "record"):
            return {"ok": False, "error": f"Bilinmeyen rapor: {kind}"}

        filters = self._report_filters(params, single=kind == "record")
        first, last, problem = records.range_bounds(filters.get("start"), filters.get("end"))
        if problem:
            return {"ok": False, "error": problem}

        rows, truncated, error = await self._scan(filters)
        if not rows:
            return {"ok": False,
                    "error": error or "Bu aralıkta ve süzgeçte denetim kaydı yok."}

        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        window = f"{first[:10]}_{last[:10]}"
        if kind == "record":
            label = f"{records.entity_label(filters.get('entity', ''))} " \
                    f"#{records.as_int(filters.get('entity_id'))}"
            content = self._record_pdf(rows, label=label, first=first, last=last,
                                       truncated=truncated)
            name = f"magaza-denetim-kayit-{window}-{stamp}.pdf"
        else:
            content = self._dump_pdf(rows, first=first, last=last, truncated=truncated)
            name = f"magaza-denetim-dokumu-{window}-{stamp}.pdf"

        try:
            path = write_private(self._export_dir / name, content)
        except (OSError, ExportError) as failure:
            return {"ok": False, "error": str(failure)}

        await self._record_export(kind=f"pdf:{kind}", rows=len(rows), path=str(path),
                                  actor=actor, start=first, end=last)
        self._log.info("denetim raporu üretildi", kind=kind, rows=len(rows), path=str(path))
        return {"ok": True, "error": error, "path": str(path), "name": name,
                "bytes": len(content), "rows": len(rows), "truncated": truncated}

    def _report_filters(self, params: dict[str, Any], *, single: bool) -> dict[str, Any]:
        out = {
            "start": records.text(params.get("start")),
            "end": records.text(params.get("end")),
            "q": records.text(params.get("q")),
            "user": records.text(params.get("user")),
            "action": records.text(params.get("action")),
            "entity": records.text(params.get("entity")),
            "entity_id": records.as_int(params.get("entityId")),
            "ip": records.text(params.get("ip")),
            "result": records.text(params.get("result")),
            "source": records.text(params.get("source")),
            "destructive": bool(params.get("destructive")),
            "reasoned": bool(params.get("reasoned")),
        }
        if single:
            # Tek kayıt raporunda serbest metin aramasının işi yok: kaydın
            # TÜM geçmişi istenir, aramaya uyan parçası değil.
            out.update({"q": "", "destructive": False, "reasoned": False})
        return out

    def _dump_pdf(self, rows: list[dict[str, Any]], *, first: str, last: str,
                  truncated: bool) -> bytes:
        destructive = [row for row in rows if row["destructive"]]
        reasoned = [row for row in rows if row["reason"]]
        users = {row["user"] for row in rows if row["user"] and row["user"] != "—"}
        gateway = [row for row in rows if row["source"] == "gateway"]
        sections: list[dict[str, Any]] = [{
            "kind": "tiles", "title": "Özet",
            "tiles": [("Kayıt", number(len(rows))), ("Yıkıcı", number(len(destructive))),
                      ("Gerekçeli", number(len(reasoned))), ("Kullanıcı", number(len(users))),
                      ("Gönderilemeyen", number(len(gateway)))],
        }, {
            "kind": "table", "title": "Denetim kayıtları",
            "headers": ["Zaman", "Kullanıcı", "İşlem", "Varlık", "Özet", "Sonuç"],
            "align": "LLLLLL", "widths": [1.3, 1.3, 1.1, 1.4, 2.4, 0.9],
            "rows": [[row["at"].replace("T", " "), row["user"], row["actionLabel"],
                      f"{row['entityLabel']} #{row['entityId']}" if row["entityId"]
                      else row["entityLabel"],
                      row["summary"] or row["reason"][:60], row["resultLabel"]]
                     for row in rows[:600]],
        }]
        if destructive:
            sections.append({
                "kind": "table", "title": "Yıkıcı işlemler",
                "headers": ["Zaman", "Kullanıcı", "İşlem", "Kayıt", "Gerekçe"],
                "align": "LLLLL", "widths": [1.3, 1.3, 1.1, 1.2, 3],
                "rows": [[row["at"].replace("T", " "), row["user"], row["actionLabel"],
                          f"{row['entityLabel']} #{row['entityId']}",
                          row["reason"] or "— gerekçe kaydedilmemiş —"]
                         for row in destructive[:200]],
            })
        if truncated or len(rows) > 600:
            sections.append({"kind": "note",
                             "text": "Liste tavana dayandı; döküm eksik olabilir. "
                                     "Aralığı daraltıp yeniden alın."})
        sections.append({"kind": "note",
                         "text": "Bu döküm SALT OKUNUR bir kayıttan üretilmiştir. "
                                 "Denetim kaydı silinemez, düzenlenemez."})
        return build_pdf(title="Denetim dökümü",
                         subtitle=f"{first.replace('T', ' ')} – {last.replace('T', ' ')} · "
                                  f"{len(rows)} kayıt",
                         sections=sections, footer="Kontrol Merkezi · Mağaza")

    def _record_pdf(self, rows: list[dict[str, Any]], *, label: str, first: str, last: str,
                    truncated: bool) -> bytes:
        sections: list[dict[str, Any]] = []
        for row in rows[:60]:
            changed = [item for item in row["diff"] if item["changed"]]
            sections.append({
                "kind": "table",
                "title": f"{row['at'].replace('T', ' ')} · {row['user']} · "
                         f"{row['actionLabel']} ({row['resultLabel']})",
                "headers": ["Alan", "Öncesi", "Sonrası"],
                "align": "LLL", "widths": [1.4, 2.3, 2.3],
                "rows": [[item["field"], item["before"] or "—", item["after"] or "—"]
                         for item in changed[:30]] or [["—", "—", "—"]],
            })
            if row["reason"]:
                sections.append({"kind": "note", "text": f"Gerekçe: {row['reason']}"})
        if truncated or len(rows) > 60:
            sections.append({"kind": "note", "text": "Geçmiş tavana dayandı; eksik olabilir."})
        return build_pdf(title=f"{label} — işlem geçmişi",
                         subtitle=f"{first.replace('T', ' ')} – {last.replace('T', ' ')} · "
                                  f"{len(rows)} kayıt",
                         sections=sections, footer="Kontrol Merkezi · Mağaza")

    async def export_csv(self, params: dict[str, Any] | None = None, *,
                         actor: str = "") -> dict[str, Any]:
        """Aralığın tamamının CSV'si — rapor klasörüne yazılır.

        Görünen sayfanın CSV'sini panel kendisi üretir (sunucuya hiç gitmez).
        """
        filters = self._report_filters(params or {}, single=False)
        first, last, problem = records.range_bounds(filters.get("start"), filters.get("end"))
        if problem:
            return {"ok": False, "error": problem}

        rows, truncated, error = await self._scan(filters)
        if not rows:
            return {"ok": False, "error": error or "Bu aralıkta denetim kaydı yok."}

        headers = ["Zaman", "Kaynak", "Kullanıcı", "İşlem", "Varlık", "Kayıt", "Özet",
                   "Gerekçe", "IP", "Sonuç", "Yıkıcı"]
        table = [[row["at"].replace("T", " "), row["sourceLabel"], row["user"],
                  row["actionLabel"], row["entityLabel"], row["entityId"] or "",
                  row["summary"], row["reason"], row["ip"], row["resultLabel"],
                  "evet" if row["destructive"] else "hayır"] for row in rows]

        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        name = f"magaza-denetim-{first[:10]}_{last[:10]}-{stamp}.csv"
        try:
            path = write_private(self._export_dir / name, csv_bytes(headers, table))
        except OSError as failure:
            return {"ok": False, "error": f"Dosya yazılamadı: {failure}"}

        await self._record_export(kind="csv", rows=len(rows), path=str(path), actor=actor,
                                  start=first, end=last)
        return {"ok": True, "error": error, "path": str(path), "name": name,
                "rows": len(table), "truncated": truncated}

    async def exports(self, *, limit: int = 50) -> dict[str, Any]:
        """Bu ekrandan alınan dökümlerin izi. Kim, ne zaman, hangi aralığı aldı."""
        try:
            rows = await self._store.fetch_all(
                f"SELECT kind, range_start, range_end, rows, path, actor, created_at "
                f"FROM {self._exports} ORDER BY id DESC LIMIT ?",
                (max(1, min(500, int(limit))),))
        except Exception as failure:  # noqa: BLE001 — iz okunamadı, ekran dursun
            return {"ok": True, "items": [], "error": self._fail(failure)}
        return {"ok": True, "error": "", "items": [
            {"kind": row["kind"], "start": row["range_start"], "end": row["range_end"],
             "rows": row["rows"], "path": row["path"], "actor": row["actor"],
             "createdAt": row["created_at"]}
            for row in rows
        ]}

    async def _record_export(self, *, kind: str, rows: int, path: str, actor: str,
                             start: str, end: str) -> None:
        """Döküm izi. EKRAN SALT OKUNUR AMA DÖKÜM DIŞARI ÇIKAR.

        Denetim kaydını okumak serbesttir; onu binlerce satır hâlinde bir
        dosyaya döküp taşımak izlenmesi gereken bir olaydır. Bagisto bu olayı
        görmez (dosya bizim tarafımızda üretilir), bu yüzden tek karşılığı
        olmayan veri budur ve modülün tek yerel tablosunu o doğurur.
        """
        try:
            await self._store.execute(
                f"INSERT INTO {self._exports} "
                "(kind, range_start, range_end, rows, path, actor, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (kind, start, end, int(rows), path, actor, _now()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, rapor durmasın
            self._log.warning("döküm izi yazılamadı", kind=kind, error=str(failure))

    # =============================================================== yazdırma

    async def _render_pages(self, path: Path, *, max_pages: int = 12,
                            dpi: int = 110) -> list[str]:
        binary = shutil.which("pdftoppm")
        if not binary:
            raise PreviewError(
                "Önizleme üretilemedi: `pdftoppm` yok (poppler-utils kurulmalı). "
                "Rapor yine de kaydedildi ve yazdırılabilir.")
        with tempfile.TemporaryDirectory(prefix="km-denetim-onizleme-") as folder:
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
        """Üretilmiş raporu yazıcıya gönderir.

        GÜVENLİK: yalnız BİZİM rapor klasörümüzdeki dosya basılabilir. Serbest
        yol kabul etmek, `lp` ile makinedeki herhangi bir dosyayı kâğıda
        döktürmeye açık kapı bırakırdı — denetim ekranında bu kapı, okuma
        yetkisini dosya okuma yetkisine çevirirdi.
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


class AuditProvider:
    """`store.audit.for` yeteneğinin BACKEND yüzü.

    Panel tarafındaki eşi `ui/panel/index.js` içindeki `capabilities()`
    dışa vurumudur; ikisi aynı imzayı taşır. Yüzey KÜÇÜK tutulmuştur: tek
    metot, üç parametre. Yetenek yüzeyi büyüdükçe onu tüketen 19 modül bu
    modüle bağlanır ve K3'ün gevşek bağı sıkı bağa döner.
    """

    def __init__(self, service: AuditService) -> None:
        self._service = service

    async def for_record(self, entity: str, entity_id: Any = 0, *, limit: int = 50,
                         days: int = 0) -> dict[str, Any]:
        return await self._service.history(entity, entity_id, limit=limit, days=days)

    async def __call__(self, entity: str, entity_id: Any = 0, *, limit: int = 50,
                       days: int = 0) -> dict[str, Any]:
        return await self.for_record(entity, entity_id, limit=limit, days=days)
