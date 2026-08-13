"""Deneme Kulübü — iş kuralları.

VERİ MAĞAZADADIR, KARAR BURADADIR. Deneme, katılımcı ve sonuç kayıtları
`store.api` geçidinden `/api/admin/bbd/trial-club/*` uçlarıyla gelir (K4); bu
modül onların kopyasını tutmaz. Yerel tablolar yalnız mağazada KARŞILIĞI
OLMAYAN üç şeyi saklar: yazma gerekçesi (denetim izi), sonuç yükleme
önizlemesi (uygulanan şeyin önizlenen şey olduğunu kanıtlar) ve bu ekrana özel
görüntü tercihi.

İKİ BİLDİRİM YOLU — bilinçli ayrım:
 · TOPLU bildirim `store.api.bbd_send_notification` ile TEK istekte gider.
   Yüz kişiye tek tek çağrı atmak dakikada 55 istekli hız kovasını tüketir ve
   yarıda kalırsa kimin aldığı belirsiz kalır.
 · TEK KİŞİLİK hatırlatma `store.notify.send` yeteneğinden geçer; şablon ve
   gönderim geçmişi Bildirimler ekranında tek yerde toplansın diye. Yetenek
   yoksa o satır eylemi ekranda GİZLENİR (K7).

UZAK SİSTEM DÜŞERSE EKRAN AYAKTA KALIR (K7): `connected: False` + `error`
döner, panel durumu anlatır. İstisna dışarı sızmaz.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from km_sdk import (
    ExportError,
    build_pdf,
    csv_bytes,
    money,
    number,
    plan_text,
    report_dir,
    write_private,
)

from . import trial

#: Katılımcı taraması için üst sınır. En kalabalık deneme ~600 kişi; 3.000
#: tavanı büyümeye yer bırakır ve bozuk `meta` yüzünden sonsuz taramayı keser.
SCAN_CAP = 3_000

#: Sunucunun kabul ettiği en büyük sayfa boyu. Geçit de bu değerde kırpıyor
#: (`paging.MAX_PER_PAGE`); daha büyüğünü istemek hata vermez, sessizce eksik
#: sayfa döndürür.
PAGE_CAP = 50

#: Sayfa taramasının tavanı — bozuk `meta` sonsuz döngü üretmesin.
PAGE_LIMIT = 100

REPORT_KINDS = ("attendance", "cards", "results")

#: Mağazada henüz UCU OLMAYAN işler. Ekran bunları kapalı gösterir ve NEDENİNİ
#: yazar; sessizce 404 ile patlamaz.
PENDING_ENDPOINTS = {
    "addMember": (
        "Katılımcı ekleme",
        "bbd_add_trial_member (POST /api/admin/bbd/trial-club/exams/{id}/members)",
    ),
    "removeMember": (
        "Katılımcı çıkarma",
        "bbd_update_trial_member (PUT /api/admin/bbd/trial-club/members/{id})",
    ),
}


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


class PreviewError(RuntimeError):
    """Önizleme görüntüsü üretilemedi. Rapor yine de kaydedilmiştir."""


class TrialClubService:
    """Deneme Kulübü ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ."""

    def __init__(self, *, api: Any, store: Any, log: Any, config: dict[str, Any],
                 printer: Any = None, notifier: Any = None, publish: Any = None,
                 category: str = "Mağaza", subcategory: str = "Müşteri",
                 fallback_dir: Path | None = None) -> None:
        self._api = api
        self._store = store
        self._log = log
        self._config = config or {}
        self._printer = printer
        self._notifier = notifier
        self._publish = publish
        self._category = category
        self._subcategory = subcategory
        self._fallback = fallback_dir or Path.home() / "km-raporlar"

        self._audit = store.table("audit")
        self._upload = store.table("upload")
        self._prefs = store.table("prefs")

    # ------------------------------------------------------------- ayarlar

    @property
    def _page_size(self) -> int:
        return max(10, min(200, trial.as_int(self._config.get("page_size"), 50)))

    @property
    def _near_full(self) -> int:
        return max(50, min(100, trial.as_int(self._config.get("near_full_percent"),
                                             trial.NEAR_FULL)))

    @property
    def _max_upload_rows(self) -> int:
        return max(10, min(5_000, trial.as_int(self._config.get("max_upload_rows"), 2_000)))

    @property
    def _max_recipients(self) -> int:
        return max(1, min(2_000, trial.as_int(self._config.get("max_recipients"), 500)))

    @property
    def _spare_rows(self) -> int:
        return max(0, min(50, trial.as_int(self._config.get("attendance_spare_rows"), 5)))

    async def _pref_int(self, key: str, default: int, *, low: int, high: int) -> int:
        """Yerel tercih varsa O geçerlidir, yoksa modül ayarı.

        Ayarlar sekmesindeki iki sayı (kontenjan uyarı eşiği, yoklama boş
        satırı) tabloya yazılıyordu ama okunmuyordu: kullanıcı değeri
        değiştiriyor, ekran hiç değişmiyordu. Değeri KULLANAN her yol buradan
        geçer.
        """
        raw = await self._pref(key)
        if not raw:
            return default
        return max(low, min(high, trial.as_int(raw, default)))

    @property
    def _export_dir(self) -> Path:
        # HER ÇAĞRIDA yeniden çözülür: ay değişince klasör kendiliğinden değişir.
        return report_dir(self._category, subcategory=self._subcategory,
                          fallback=self._fallback,
                          configured=str(self._config.get("export_path") or ""))

    # ------------------------------------------------------ yerel tablolar

    async def _record(self, *, exam_id: int = 0, member_id: int = 0, action: str, reason: str,
                      actor: str, result: str, detail: Any = None) -> None:
        """Yerel denetim izi. Bagisto denetim tutuyor ama GEREKÇEYİ tutmuyor;
        ayrıca ağ koparsa "ne yapmaya çalıştık" kaydı yalnız burada kalır."""
        try:
            await self._store.execute(
                f"INSERT INTO {self._audit} "
                "(exam_id, member_id, action, reason, actor, result, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (int(exam_id or 0), int(member_id or 0), action, reason, actor, result,
                 json.dumps(detail or {}, ensure_ascii=False), _now()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın
            self._log.warning("denetim izi yazılamadı", action=action, error=str(failure))

    async def _pref(self, key: str) -> str:
        try:
            row = await self._store.fetch_one(
                f"SELECT value FROM {self._prefs} WHERE key = ?", (key,))
        except Exception as failure:  # noqa: BLE001 — tercih okunamadı, varsayılan yeter
            self._log.warning("tercih okunamadı", key=key, error=str(failure))
            return ""
        return str(row["value"]) if row else ""

    async def _set_pref(self, key: str, value: str, actor: str) -> None:
        await self._store.execute(
            f"INSERT INTO {self._prefs} (key, value, actor, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, actor = excluded.actor, "
            "updated_at = excluded.updated_at",
            (key, value, actor, _now()),
        )

    # ------------------------------------------------------------- yardımcı

    @staticmethod
    def _fail(failure: Exception) -> str:
        message = str(failure).strip()
        return message or "Mağazaya ulaşılamadı."

    @staticmethod
    def _guard(reason: str) -> str:
        """Gerekçe backend'de DE doğrulanır (K9): arayüzde gizlemek yetmez."""
        return trial.reason_error(reason)

    def features(self) -> dict[str, Any]:
        """Ekranın hangi düğmeyi açabileceği. Kapalı olanın NEDENİ yazılıdır."""
        pending = {key: {"available": False,
                         "reason": f"{label} için mağaza ucu henüz yayında değil "
                                   f"({method}). Uç yayınlanınca bu düğme açılacak."}
                   for key, (label, method) in PENDING_ENDPOINTS.items()}
        return {
            **pending,
            "notifyBulk": {"available": True, "reason": ""},
            "notifyOne": {
                "available": self._notifier is not None,
                "reason": "" if self._notifier is not None else
                          "Bildirimler modülü kapalı (store.notify.send yeteneği yok); "
                          "tek kişilik hatırlatma gizlendi.",
            },
            "printer": {"available": self._printer is not None,
                        "reason": "" if self._printer is not None else
                                  "Yazıcı yeteneği bu kurulumda yok; PDF üretilir, basılmaz."},
        }

    # =============================================================== okuma

    async def _exam_rows(self, filters: dict[str, Any] | None = None
                         ) -> tuple[list[dict[str, Any]], bool, str]:
        """Deneme listesi + "liste kesildi mi" bayrağı + hata.

        TUZAK: geçidin `bbd_trial_exams` metodu sayfa parametresi almıyor ve
        sunucu `per_page`i 50'ye kırpıyor. Deneme sayısı bugün onlarla ölçülüyor
        ama sessizce 51. denemeyi düşürmek, o denemeyi ekranda YOK göstermek
        olurdu; `meta` daha çok kayıt diyorsa ekran bunu SÖYLER.
        """
        try:
            payload = await self._api.bbd_trial_exams(filters or {})
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("denemeler okunamadı", error=str(failure))
            return [], False, self._fail(failure)
        today = trial.today_iso()
        near = await self._pref_int("near_full_percent", self._near_full, low=50, high=100)
        raw = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
        rows = [trial.exam_row(item, today=today, near=near) for item in raw]
        meta = payload.get("meta") or {}
        truncated = (trial.as_int(meta.get("lastPage"), 1) > 1
                     or trial.as_int(meta.get("total"), len(raw)) > len(raw))
        return rows, truncated, ""

    async def exams(self, *, q: str = "", status: str = "",
                    results: str = "") -> dict[str, Any]:
        """Deneme listesi.

        Deneme sayısı ONLARLA ölçülür (yılda ~20), katılımcı gibi binlerle
        değil; süzme İSTEMCİ TARAFINDA değil burada yapılır ama liste tek
        sayfadan gelir. Sunucu tarafı süzgeç ucu yayınlanınca `filters`
        doldurulacak — şimdilik gönderilen süzgecin uygulandığı VARSAYILMAZ.
        """
        rows, truncated, error = await self._exam_rows()
        needle = trial.fold(q)
        if needle:
            rows = [row for row in rows
                    if needle in trial.fold(row["name"]) or needle in trial.fold(row["grade"])]
        if status:
            rows = [row for row in rows if row["status"] == status
                    or row["registrationState"] == status]
        if results:
            rows = [row for row in rows if row["resultsState"] == results]

        near = await self._pref_int("near_full_percent", self._near_full, low=50, high=100)
        return {"ok": True, "connected": not error, "error": error, "items": rows,
                "total": len(rows), "nearFull": near, "truncated": truncated,
                "features": self.features()}

    async def exam(self, exam_id: int) -> dict[str, Any]:
        """Tek deneme.

        TUZAK: geçitte tekil deneme ucu YOK (`bbd_trial_exams` koleksiyon
        döner). Süzgeç gönderilir ama uygulandığı VARSAYILMAZ — Laravel
        tanımadığı sorgu parametresini sessizce yok sayar; dönen listede kimlik
        eşleşmesi ARANIR. Bulunamazsa hata döner: yanlış deneme üzerinde işlem
        yapmaktansa hiç yapmamak doğrudur.
        """
        rows, _, error = await self._exam_rows({"id": int(exam_id)})
        if error:
            return {"ok": False, "connected": False, "error": error}
        found = next((row for row in rows if row["id"] == int(exam_id)), None)
        if found is None:
            return {"ok": False, "connected": True,
                    "error": f"#{exam_id} numaralı deneme bulunamadı."}
        return {"ok": True, "connected": True, "error": "", "exam": found,
                "publishNotice": trial.PUBLISH_NOTICE}

    async def overview(self) -> dict[str, Any]:
        """KPI şeridi. Deneme listesinden hesaplanır — TEK istek.

        Katılımcı toplamı ayrı ve UCUZ bir çağrıyla (bir satırlık sayfa) `meta`
        üzerinden okunur; binlerce satır çekip saymak KPI için savurganlıktır.
        """
        rows, truncated, error = await self._exam_rows()
        today = trial.today_iso()
        open_exams = [row for row in rows if row["registrationState"] == "open"]
        upcoming = [row for row in rows if row["examDate"] and row["examDate"] >= today]
        pending = [row for row in rows if row["resultsState"] == "pending"]
        limited = [row for row in rows if row["capacity"]["capacity"] > 0]
        seats = sum(row["capacity"]["capacity"] for row in limited)
        taken = sum(row["capacity"]["enrolled"] for row in limited)

        members_total = None
        try:
            payload = await self._api.bbd_trial_members({}, page=1, per_page=1)
            meta = payload.get("meta") or {}
            members_total = trial.as_int(meta.get("total"), len(payload.get("items") or []))
        except Exception as failure:  # noqa: BLE001 — K7, KPI eksik kalır ama ekran durur
            self._log.info("katılımcı sayısı okunamadı", error=str(failure))

        return {
            "ok": True, "connected": not error, "error": error, "truncated": truncated,
            "tiles": {
                "exams": len(rows),
                "openExams": len(open_exams),
                "upcoming": len(upcoming),
                "seats": seats,
                "taken": taken,
                "fillPercent": round(taken * 100 / seats, 1) if seats else None,
                "members": members_total,
                "pendingResults": len(pending),
            },
            "features": self.features(),
        }

    async def members(self, *, q: str = "", exam_id: int = 0, payment: str = "",
                      attendance: str = "", city: str = "", grade: str = "",
                      page: int = 1, size: int = 0) -> dict[str, Any]:
        """Katılımcı listesi — SUNUCU TARAFI sayfalama."""
        per_page = size or self._page_size
        filters: dict[str, Any] = {}
        if exam_id:
            filters["exam_id"] = int(exam_id)
        if trial.text(q):
            filters["search"] = trial.text(q)
        if payment:
            filters["payment_status"] = payment
        if attendance in ("attended", "absent"):
            filters["attended"] = 1 if attendance == "attended" else 0
        if trial.text(city):
            filters["city"] = trial.text(city)
        if trial.text(grade):
            filters["grade"] = trial.text(grade)

        try:
            payload = await self._api.bbd_trial_members(filters, page=page, per_page=per_page)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("katılımcılar okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "items": [], "total": 0, "page": page, "size": per_page, "pages": 0}

        raw = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
        meta = payload.get("meta") or {}
        return {
            "ok": True, "connected": True, "error": "",
            "items": [trial.member_row(item) for item in raw],
            "total": trial.as_int(meta.get("total"), len(raw)),
            "page": trial.as_int(meta.get("currentPage"), page),
            "size": trial.as_int(meta.get("perPage"), per_page),
            "pages": trial.as_int(meta.get("lastPage"), 1),
        }

    async def _all_members(self, exam_id: int) -> tuple[list[dict[str, Any]], bool, str]:
        """Bir denemenin TÜM katılımcıları — eşleştirme ve bildirim için.

        Sayfalar SIRAYLA çekilir (geçit `all_pages` içinde hız kovasını
        tutuyor); tavan aşılırsa `truncated` döner ve ekran bunu SÖYLER.

        SÜZGECİN UYGULANDIĞI KANITLANIR, VARSAYILMAZ. Laravel tanımadığı sorgu
        parametresini sessizce yok sayar; `exam_id` yok sayılırsa kulübün TÜM
        katılımcıları döner. Bu listeye sonuç yazmak başka denemenin öğrencisine
        net yazmak, bildirim göndermek de yüzlerce kişiye boşuna (ve ücretli)
        SMS atmak demektir. Bu yüzden:
          · deneme kimliği TAŞIYAN ve eşleşen satırlar alınır,
          · hiçbir satır kimlik taşımıyorsa kanıt yok sayılır ve işlem DURUR.
        """
        try:
            payload = await self._api.bbd_trial_members({"exam_id": int(exam_id)},
                                                        all_pages=True)
        except Exception as failure:  # noqa: BLE001 — K7
            return [], False, self._fail(failure)
        raw = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
        truncated = bool(payload.get("truncated")) or len(raw) > SCAN_CAP
        rows = [trial.member_row(item) for item in raw[:SCAN_CAP]]
        tagged = [row for row in rows if row["examId"]]
        if rows and not tagged:
            return [], truncated, (
                "Katılımcı kayıtları deneme kimliği taşımıyor; `exam_id` süzgecinin "
                "uygulandığı kanıtlanamıyor. Yanlış denemenin katılımcılarına sonuç "
                "yazmamak ve onlara bildirim göndermemek için işlem durduruldu.")
        return [row for row in tagged if row["examId"] == int(exam_id)], truncated, ""

    async def _all_results(self, exam_id: int) -> tuple[list[dict[str, Any]], bool, str]:
        """Bir denemenin TÜM sonuç satırları — rapor ve CSV için.

        TUZAK: geçit `per_page` değerini 50'ye kırpar (`MAX_PER_PAGE`); tek
        sayfada 200 istemek hata vermez, sessizce 50 satır döner. Karne ve
        sonuç listesi böylece 51. öğrenciden sonrasını kaybederdi. Sayfalar
        SIRAYLA çekilir — paralel istek hız kovasını tüketiyor.
        """
        collected: list[dict[str, Any]] = []
        page = 1
        while True:
            try:
                payload = await self._api.bbd_trial_results(int(exam_id), page=page,
                                                            per_page=PAGE_CAP)
            except Exception as failure:  # noqa: BLE001 — K7
                self._log.warning("sonuçlar okunamadı", examId=exam_id, error=str(failure))
                return [], False, self._fail(failure)
            items = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
            collected.extend(items)
            meta = payload.get("meta") or {}
            last = trial.as_int(meta.get("lastPage"), page)
            if not items or page >= last:
                return collected, False, ""
            if len(collected) >= SCAN_CAP or page >= PAGE_LIMIT:
                # Bozuk `meta` yüzünden sonsuz taramaya girmemek için tavan var.
                return collected[:SCAN_CAP], True, ""
            page += 1

    async def _result_set(self, exam_id: int) -> tuple[list[dict[str, Any]], dict[str, Any],
                                                       bool, str]:
        """Sıralanmış tüm sonuçlar + istatistik. Rapor/CSV bunu kullanır."""
        raw, truncated, error = await self._all_results(int(exam_id))
        if error:
            return [], {}, False, error
        rows = trial.derive_ranks([trial.result_row(item) for item in raw])
        return rows, trial.score_stats(rows), truncated, ""

    async def _member(self, member_id: int) -> tuple[dict[str, Any] | None, str]:
        """Tek katılımcı. Tekil uç YOK; süzgeç gönderilir ama uygulandığı
        VARSAYILMAZ — dönen listede kimlik eşleşmesi aranır."""
        try:
            payload = await self._api.bbd_trial_members({"id": int(member_id)}, page=1,
                                                        per_page=self._page_size)
        except Exception as failure:  # noqa: BLE001 — K7
            return None, self._fail(failure)
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            row = trial.member_row(item)
            if row["id"] == int(member_id):
                return row, ""
        return None, f"#{member_id} numaralı katılımcı bulunamadı."

    async def results(self, exam_id: int, *, page: int = 1, size: int = 0) -> dict[str, Any]:
        per_page = size or self._page_size
        try:
            payload = await self._api.bbd_trial_results(int(exam_id), page=page,
                                                        per_page=per_page)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("sonuçlar okunamadı", examId=exam_id, error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "items": [], "total": 0, "page": page, "size": per_page, "pages": 0,
                    "stats": {}}

        raw = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
        meta = payload.get("meta") or {}
        rows = trial.derive_ranks([trial.result_row(item) for item in raw])
        return {
            "ok": True, "connected": True, "error": "", "items": rows,
            "total": trial.as_int(meta.get("total"), len(rows)),
            "page": trial.as_int(meta.get("currentPage"), page),
            "size": trial.as_int(meta.get("perPage"), per_page),
            "pages": trial.as_int(meta.get("lastPage"), 1),
            "stats": trial.score_stats(rows),
        }

    async def audit(self, *, exam_id: int = 0, limit: int = 50) -> dict[str, Any]:
        """Bu ekrandan yapılan yazmaların YEREL izi (gerekçeleriyle)."""
        sql = (f"SELECT exam_id, member_id, action, reason, actor, result, created_at "
               f"FROM {self._audit} ")
        params: tuple[Any, ...] = ()
        if exam_id:
            sql += "WHERE exam_id = ? "
            params = (int(exam_id),)
        sql += "ORDER BY id DESC LIMIT ?"
        params = (*params, max(1, min(500, int(limit))))
        try:
            rows = await self._store.fetch_all(sql, params)
        except Exception as failure:  # noqa: BLE001 — iz okunamadı, ekran dursun
            return {"ok": True, "items": [], "error": self._fail(failure)}
        return {"ok": True, "error": "", "items": [
            {"examId": row["exam_id"], "memberId": row["member_id"], "action": row["action"],
             "reason": row["reason"], "actor": row["actor"], "result": row["result"],
             "createdAt": row["created_at"]}
            for row in rows
        ]}

    # =============================================================== yazma

    async def save_exam(self, exam_id: int | None, *, payload: dict[str, Any], reason: str,
                        actor: str, dry_run: bool = True) -> dict[str, Any]:
        """Deneme açar ya da günceller.

        Kontenjan bu uçtan da yazılabilir ama KAYITLININ ALTINA çekilemez:
        kural `capacity_change_error` içinde ve iki yolda da uygulanır (K9).
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        name = trial.text(payload.get("name"))
        if not name:
            return {"ok": False, "error": "Deneme adı zorunlu."}
        exam_date = trial.text(payload.get("examDate"))[:10]
        if not exam_date:
            return {"ok": False, "error": "Sınav tarihi zorunlu."}

        capacity = trial.as_int(payload.get("capacity"))
        enrolled = 0
        if exam_id:
            current = await self.exam(int(exam_id))
            if not current.get("ok"):
                return {"ok": False, "error": current.get("error", "Deneme okunamadı.")}
            enrolled = current["exam"]["capacity"]["enrolled"]
        problem = trial.capacity_change_error(capacity, enrolled)
        if problem:
            return {"ok": False, "error": problem}

        body: dict[str, Any] = {
            "name": name,
            "exam_date": exam_date,
            "exam_time": trial.text(payload.get("examTime"))[:5],
            "capacity": capacity,
            "venue": trial.text(payload.get("venue")),
            "grade": trial.text(payload.get("grade")),
            "registration_start": trial.text(payload.get("registrationStart"))[:10],
            "registration_end": trial.text(payload.get("registrationEnd"))[:10],
        }
        fee = payload.get("feeKurus")
        if fee is not None:
            body["fee"] = trial.from_kurus(fee)

        action = "update_exam" if exam_id else "create_exam"
        await self._record(exam_id=int(exam_id or 0), action=action, reason=reason, actor=actor,
                           result="denendi", detail=body)
        try:
            result = await self._api.bbd_save_trial_exam(
                payload=body, exam_id=int(exam_id) if exam_id else None, reason=reason,
                actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(exam_id=int(exam_id or 0), action=action, reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        new_id = trial.as_int((result.get("data") or {}).get("id")
                              if isinstance(result.get("data"), dict) else result.get("id"))
        await self._record(exam_id=int(exam_id or new_id), action=action, reason=reason,
                           actor=actor, result="dry_run" if dry_run else "ok", detail=body)
        return {"ok": True, "error": "", "id": int(exam_id or new_id),
                "dryRun": bool(result.get("dryRun", dry_run))}

    async def set_capacity(self, exam_id: int, *, capacity: int, reason: str, actor: str,
                           dry_run: bool = True) -> dict[str, Any]:
        """Kontenjan değiştirme — OKU-DOĞRULA-YAZ.

        Deneme taze okunur: aradan geçen sürede yeni kayıt gelmiş olabilir ve
        ekrandaki eski "kayıtlı" sayısına göre yapılan doğrulama yanlış karar
        verirdi.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        current = await self.exam(int(exam_id))
        if not current.get("ok"):
            return {"ok": False, "error": current.get("error", "Deneme okunamadı.")}
        row = current["exam"]
        enrolled = row["capacity"]["enrolled"]
        problem = trial.capacity_change_error(capacity, enrolled)
        if problem:
            return {"ok": False, "error": problem}
        before = row["capacity"]["capacity"]
        if int(capacity) == before:
            return {"ok": False, "error": "Kontenjan zaten bu."}

        await self._record(exam_id=exam_id, action="set_capacity", reason=reason, actor=actor,
                           result="denendi", detail={"from": before, "to": int(capacity)})
        try:
            result = await self._api.bbd_save_trial_exam(
                payload={"capacity": int(capacity)}, exam_id=int(exam_id), reason=reason,
                actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(exam_id=exam_id, action="set_capacity", reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(exam_id=exam_id, action="set_capacity", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"from": before, "to": int(capacity)})
        return {"ok": True, "error": "", "from": before, "to": int(capacity),
                "enrolled": enrolled, "dryRun": bool(result.get("dryRun", dry_run))}

    async def add_member(self, exam_id: int, *, reason: str, actor: str) -> dict[str, Any]:
        """UÇ YOK. Sessizce patlamak yerine ne eksik olduğunu söyler."""
        await self._record(exam_id=exam_id, action="add_member", reason=reason, actor=actor,
                           result="uc_yok")
        label, method = PENDING_ENDPOINTS["addMember"]
        return {"ok": False, "pending": True,
                "error": f"{label} mağaza tarafında henüz yok. Gereken uç: {method}. "
                         "Uç yayınlanınca bu ekran kendiliğinden çalışacak."}

    async def remove_member(self, member_id: int, *, reason: str, actor: str) -> dict[str, Any]:
        """UÇ YOK. Katılımcı SİLİNMEZ, kaydı iptal edilir (ADR 0012) — o uç da yok."""
        await self._record(member_id=member_id, action="remove_member", reason=reason,
                           actor=actor, result="uc_yok")
        label, method = PENDING_ENDPOINTS["removeMember"]
        return {"ok": False, "pending": True,
                "error": f"{label} mağaza tarafında henüz yok. Gereken uç: {method}. "
                         "Kayıt silinmez, iptal edilir; uç yayınlanınca açılacak."}

    # ======================================================= sonuç yükleme

    async def upload_preview(self, exam_id: int, *, content: str,
                             filename: str = "") -> dict[str, Any]:
        """CSV → EŞLEŞTİRME ÖNİZLEMESİ. Mağazaya HİÇBİR ŞEY yazmaz.

        Önizleme yerel tabloya yazılır ve bir jeton döner; uygulama o jetonla
        gelir. Böylece uygulanan şeyin önizlenen şey olduğu kanıtlanır: ekran
        açıkken katılımcı listesi değişse bile yazılan satırlar kullanıcının
        onayladığı satırlardır.
        """
        parsed = trial.parse_result_csv(content, max_rows=self._max_upload_rows)
        if not parsed.get("ok"):
            return {"ok": False, "error": parsed.get("error", "Dosya okunamadı."),
                    "columns": parsed.get("columns", {})}

        members, truncated, error = await self._all_members(int(exam_id))
        if error:
            return {"ok": False, "error": f"Katılımcı listesi alınamadı: {error}"}
        if not members:
            return {"ok": False,
                    "error": "Bu denemede katılımcı yok; eşleştirilecek kimse bulunamadı."}

        matches = trial.match_results(parsed["rows"], members)
        summary = trial.match_summary(matches, member_count=len(members))
        missing = trial.missing_members(matches, members)
        rows = trial.upload_rows(matches)

        token = uuid.uuid4().hex
        try:
            await self._store.execute(
                f"INSERT INTO {self._upload} "
                "(token, exam_id, filename, summary, rows, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'preview', ?)",
                (token, int(exam_id), trial.text(filename)[:200],
                 json.dumps(summary, ensure_ascii=False),
                 json.dumps(rows, ensure_ascii=False), _now()),
            )
        except Exception as failure:  # noqa: BLE001 — jeton yoksa uygulama açılmaz
            self._log.warning("önizleme kaydedilemedi", error=str(failure))
            return {"ok": False, "error": "Önizleme kaydedilemedi; yükleme açılmadı."}

        warnings = list(parsed.get("warnings") or [])
        if truncated:
            warnings.append("Katılımcı listesi tavana dayandı; eşleştirme eksik olabilir.")

        return {"ok": True, "error": "", "token": token, "examId": int(exam_id),
                "delimiter": parsed["delimiter"], "columns": parsed["columns"],
                "matches": matches, "summary": summary, "missingMembers": missing,
                "warnings": warnings, "applicable": summary["writable"] > 0}

    async def upload_apply(self, *, token: str, reason: str, actor: str,
                           dry_run: bool = True) -> dict[str, Any]:
        """Önizlenen satırları yazar. Jeton yoksa ya da tüketilmişse reddedilir."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        try:
            row = await self._store.fetch_one(
                f"SELECT token, exam_id, rows, summary, status FROM {self._upload} "
                "WHERE token = ?", (trial.text(token),))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        if not row:
            return {"ok": False,
                    "error": "Önizleme bulunamadı. Eşleştirme tablosu görülmeden sonuç "
                             "yazılmaz; dosyayı yeniden yükleyin."}
        if row["status"] != "preview":
            return {"ok": False, "error": "Bu yükleme zaten uygulandı."}

        rows = json.loads(row["rows"])
        if not rows:
            return {"ok": False, "error": "Önizlemede yazılacak eşleşmiş satır yok."}

        exam_id = int(row["exam_id"])
        await self._record(exam_id=exam_id, action="upload_results", reason=reason, actor=actor,
                           result="denendi", detail={"rows": len(rows), "token": row["token"]})
        try:
            result = await self._api.bbd_upload_trial_results(
                exam_id, rows=rows, reason=reason, actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(exam_id=exam_id, action="upload_results", reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        warning = ""
        try:
            await self._store.execute(
                f"UPDATE {self._upload} SET status = ?, actor = ?, reason = ?, applied_at = ? "
                "WHERE token = ?",
                ("dry_run" if dry_run else "applied", actor, reason, _now(), row["token"]),
            )
        except Exception as failure:  # noqa: BLE001 — SONUÇLAR ZATEN YAZILDI
            # Jeton "uygulandı" diye işaretlenemedi. İstisnayı dışarı bırakmak
            # 500 üretir; kullanıcı yazmanın gitmediğini sanıp aynı jetonla
            # yeniden dener ve satırlar mağazaya İKİNCİ kez yazılır. Bu yüzden
            # sonuç başarıyla döner, uyarı ayrıca söylenir.
            self._log.warning("yükleme jetonu kapatılamadı", token=row["token"],
                              error=str(failure))
            await self._record(exam_id=exam_id, action="upload_results", reason=reason,
                               actor=actor, result="jeton_kapatilamadi",
                               detail={"token": row["token"], "error": str(failure)})
            warning = ("Sonuçlar yazıldı ama yükleme jetonu kapatılamadı; aynı dosyayı "
                       "TEKRAR uygulamayın, ikinci kez yazılır.")

        await self._record(exam_id=exam_id, action="upload_results", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"rows": len(rows), "token": row["token"]})
        return {"ok": True, "error": "", "written": len(rows), "examId": exam_id,
                "dryRun": bool(result.get("dryRun", dry_run)), "warning": warning,
                "notice": "Sonuçlar yazıldı ama HENÜZ YAYINLANMADI; katılımcı görmüyor."}

    async def publish(self, exam_id: int, *, reason: str, actor: str,
                      dry_run: bool = True) -> dict[str, Any]:
        """Sonuçları yayınlar — katılımcıya görünür hâle getirir."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        await self._record(exam_id=exam_id, action="publish_results", reason=reason, actor=actor,
                           result="denendi")
        try:
            result = await self._api.bbd_publish_trial_results(int(exam_id), reason=reason,
                                                               actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(exam_id=exam_id, action="publish_results", reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(exam_id=exam_id, action="publish_results", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok")
        if self._publish is not None and not dry_run:
            try:
                await self._publish("store.trial.results_published",
                                    {"examId": int(exam_id), "actor": actor})
            except Exception as failure:  # noqa: BLE001 — olay yayını işi bozmaz
                self._log.warning("olay yayınlanamadı", error=str(failure))
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run)),
                "notice": trial.PUBLISH_NOTICE}

    # ============================================================= bildirim

    def _notify_plan(self, members: list[dict[str, Any]], *, audience: str, channel: str,
                     message: str, truncated: bool = False) -> dict[str, Any]:
        """Alıcı çözümü — TEK YER. Önizleme ve gönderme aynı fonksiyondan geçer.

        Kitleyi sunucunun yeniden çözmesine bırakmak, önizlenenden başka bir
        gruba SMS gitmesi demekti; bu yüzden alıcı listesi burada çözülür ve
        gövdeye AÇIKÇA konur.
        """
        chosen = trial.audience_members(members, audience)
        split = trial.recipients(chosen, channel)
        plan = plan_text(trial.text(message)) if channel == "sms" and message else None
        reachable = split["reachable"]
        return {
            "audience": audience, "audienceLabel": trial.AUDIENCES[audience],
            "channel": channel, "total": len(chosen), "reachable": len(reachable),
            "recipients": reachable,
            "unreachable": split["unreachable"][:50],
            "unreachableCount": len(split["unreachable"]),
            "truncated": truncated, "capped": len(reachable) > self._max_recipients,
            "limit": self._max_recipients,
            "smsParts": plan.parts if plan else None,
            "smsUnicode": bool(plan.unicode) if plan else None,
            "estimatedSms": (plan.parts * len(reachable)) if plan else None,
        }

    async def notify_preview(self, exam_id: int, *, audience: str, channel: str,
                             message: str = "") -> dict[str, Any]:
        """Kime, kaç kişiye, kaç SMS parçası — GÖNDERMEDEN ÖNCE.

        Alıcıların KENDİSİ ekrana dönmez: panelin telefon listesine ihtiyacı
        yok, sayı yeter. Kişisel veriyi gereksiz yere tele koymayız.
        """
        if audience not in trial.AUDIENCES:
            return {"ok": False, "error": f"Bilinmeyen kitle: {audience}"}
        members, truncated, error = await self._all_members(int(exam_id))
        if error:
            return {"ok": False, "error": f"Katılımcı listesi alınamadı: {error}"}
        plan = self._notify_plan(members, audience=audience, channel=channel, message=message,
                                 truncated=truncated)
        plan.pop("recipients", None)
        return {"ok": True, "error": "", **plan}

    async def notify_bulk(self, exam_id: int, *, audience: str, channel: str, template: str,
                          message: str, reason: str, actor: str,
                          dry_run: bool = True) -> dict[str, Any]:
        """Toplu bildirim — TEK istekte. Alıcılar açıkça gönderilir.

        Yüz kişiye tek tek çağrı atmak dakikada 55 istekli hız kovasını
        tüketir, dakikalarca sürer ve yarıda kalırsa kimin aldığı belirsiz
        kalır. Geçidin toplu bildirim ucu bu iş içindir.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        if channel not in ("sms", "email", "push"):
            return {"ok": False, "error": f"Bilinmeyen kanal: {channel}"}
        if audience not in trial.AUDIENCES:
            return {"ok": False, "error": f"Bilinmeyen kitle: {audience}"}
        if not trial.text(message) and not trial.text(template):
            return {"ok": False, "error": "Şablon ya da mesaj metni gerekli."}

        members, truncated, error = await self._all_members(int(exam_id))
        if error:
            return {"ok": False, "error": f"Katılımcı listesi alınamadı: {error}"}
        plan = self._notify_plan(members, audience=audience, channel=channel, message=message,
                                 truncated=truncated)
        if plan["reachable"] == 0:
            return {"ok": False,
                    "error": "Bu kitlede ulaşılabilir alıcı yok; gönderim yapılmadı."}
        if plan["capped"]:
            return {"ok": False,
                    "error": f"Alıcı sayısı {plan['reachable']}, sınır "
                             f"{self._max_recipients}. Kitleyi daraltın ya da modül ayarından "
                             "sınırı bilerek yükseltin."}

        body = {
            "channel": channel,
            "template": trial.text(template),
            "message": trial.text(message),
            "context": {"trialExamId": int(exam_id), "audience": audience},
            "recipients": plan["recipients"],
        }

        await self._record(exam_id=exam_id, action="notify_bulk", reason=reason, actor=actor,
                           result="denendi",
                           detail={"channel": channel, "audience": audience,
                                   "count": plan["reachable"]})
        try:
            result = await self._api.bbd_send_notification(payload=body, reason=reason,
                                                           actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(exam_id=exam_id, action="notify_bulk", reason=reason, actor=actor,
                               result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(exam_id=exam_id, action="notify_bulk", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"channel": channel, "audience": audience,
                                   "count": plan["reachable"]})
        return {"ok": True, "error": "", "sent": plan["reachable"],
                "skipped": plan["unreachableCount"],
                "estimatedSms": plan.get("estimatedSms"),
                "dryRun": bool(result.get("dryRun", dry_run))}

    async def notify_one(self, member_id: int, *, template: str, channel: str, reason: str,
                         actor: str, dry_run: bool = True) -> dict[str, Any]:
        """Tek kişilik hatırlatma — Bildirimler modülünün yeteneğinden geçer.

        Yetenek yoksa uç KAPALIDIR ve bunu söyler; sessizce başarı dönmez.
        """
        if self._notifier is None:
            return {"ok": False, "pending": True,
                    "error": "Bildirim yeteneği bu kurulumda yok (store_notifications kapalı). "
                             "Ekranda bu düğme gizlenir."}
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        # `store.notify.send` alıcıyı ADRES olarak alır; mobil bildirimin adresi
        # yoktur (cihaz jetonu mağazada durur). Toplu yol `push` destekler, bu
        # yol desteklemez ve bunu söyler — sessizce e-postaya düşmez.
        if channel not in ("sms", "email"):
            return {"ok": False,
                    "error": "Tek kişilik hatırlatma yalnız SMS ya da e-posta ile gider; "
                             "mobil bildirim için toplu gönderim kullanılmalı."}

        row, error = await self._member(int(member_id))
        if row is None:
            return {"ok": False, "error": error}
        target = trial.phone_key(row["phone"]) if channel == "sms" else row["email"]
        if not target:
            return {"ok": False,
                    "error": "Katılımcının bu kanalda ulaşılabilir adresi yok."}

        # Bu yolun şablonu Bildirimler modülünün ŞABLON KİMLİĞİDİR (`store:12`),
        # toplu gönderimdeki mağaza şablon anahtarı değil. İkisini tek alanda
        # toplamak, biri çalışırken öbürünü sessizce reddettiriyordu.
        template_id = (trial.text(template)
                       or await self._pref("notify_direct_template")
                       or trial.text(self._config.get("notify_direct_template")))
        if not template_id:
            return {"ok": False,
                    "error": "Hatırlatma şablonu seçilmemiş. Ayarlar sekmesindeki "
                             "'Tek kişilik hatırlatma şablonu' alanına Bildirimler "
                             "ekranındaki şablon kimliğini yazın (örn. store:12)."}

        # "Denendi" kaydı istekten ÖNCE düşer — bütün öbür yazma yolları gibi.
        # Yoksa yetenek çağrısı patladığında (ağ koptu, modül düştü) "ne yapmaya
        # çalıştık" bilgisi hiçbir yerde kalmıyordu; oysa gönderim uzakta
        # yapılmış olabilir. Şablon RAW değil ÇÖZÜLMÜŞ hâliyle yazılır: tercihten
        # gelen şablonda alan boş görünüyor ve iz hangi şablonun gittiğini
        # söylemiyordu.
        detail = {"channel": channel, "template": template_id}
        await self._record(member_id=member_id, exam_id=row["examId"], action="notify_one",
                           reason=reason, actor=actor, result="denendi", detail=detail)
        try:
            result = await self._notifier.send(
                template=template_id, recipients=[target], channel=channel,
                values={"name": row["name"], "exam": row["examName"]},
                reason=reason, actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(member_id=member_id, exam_id=row["examId"], action="notify_one",
                               reason=reason, actor=actor, result="hata",
                               detail={**detail, "error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}
        if isinstance(result, dict) and result.get("ok") is False:
            # Yetenek kendi kapılarından (sessiz saat, günlük limit, şablon
            # biçimi) geçmemiş olabilir; "gönderildi" demeden nedenini iletir.
            await self._record(member_id=member_id, exam_id=row["examId"], action="notify_one",
                               reason=reason, actor=actor, result="hata",
                               detail={**detail, "error": trial.text(result.get("error"))})
            return {"ok": False, "error": trial.text(result.get("error"))
                    or "Bildirim gönderilemedi."}

        await self._record(member_id=member_id, exam_id=row["examId"], action="notify_one",
                           reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok", detail=detail)
        return {"ok": True, "error": "", "dryRun": bool(dry_run),
                "result": result if isinstance(result, dict) else {}}

    # ============================================================== ayarlar

    async def settings(self) -> dict[str, Any]:
        """`store_settings` ekranı yok; bu ekranın ayarı burada durur.

        Hepsi YEREL tercihtir ve vitrini etkilemez: kontenjan uyarı eşiği,
        yoklama çizelgesindeki boş satır sayısı, varsayılan bildirim kanalı ve
        şablonu. Mağaza tarafında karşılığı olan bir ayar YOKTUR — olsaydı
        `core_config` üzerinden yazılırdı.
        """
        return {"ok": True, "error": "",
                "local": {
                    "nearFullPercent": await self._pref_int(
                        "near_full_percent", self._near_full, low=50, high=100),
                    "attendanceSpareRows": await self._pref_int(
                        "spare_rows", self._spare_rows, low=0, high=50),
                    "notifyChannel": (await self._pref("notify_channel")
                                      or trial.text(self._config.get("notify_channel"))
                                      or "sms"),
                    "notifyTemplate": (await self._pref("notify_template")
                                       or trial.text(self._config.get("notify_template"))
                                       or "trial_reminder"),
                    "notifyDirectTemplate": (
                        await self._pref("notify_direct_template")
                        or trial.text(self._config.get("notify_direct_template"))),
                },
                "limits": {"maxUploadRows": self._max_upload_rows,
                           "maxRecipients": self._max_recipients},
                "audiences": [{"value": key, "label": label}
                              for key, label in trial.AUDIENCES.items()],
                "features": self.features()}

    async def save_settings(self, *, near_full_percent: int | None = None,
                            attendance_spare_rows: int | None = None,
                            notify_channel: str = "", notify_template: str = "",
                            notify_direct_template: str = "",
                            actor: str = "") -> dict[str, Any]:
        """Yerel tercihler. GEREKÇE İSTEMEZ: mağazaya hiçbir şey yazılmaz,
        yalnız bu ekranın görünümü değişir.

        Yerel depo yazılamazsa uç ÇÖKMEZ (K7): servis HTTP hatası fırlatmaz,
        `{"ok": False, "error": …}` döner ve ekran nedenini gösterir. İstisnayı
        dışarı bırakmak Ayarlar sekmesini anlamsız bir 500 ile kapatırdı.
        """
        wanted: list[tuple[str, str, str]] = []
        if near_full_percent is not None:
            wanted.append(("near_full_percent",
                           str(max(50, min(100, int(near_full_percent)))),
                           "kontenjan uyarı eşiği"))
        if attendance_spare_rows is not None:
            wanted.append(("spare_rows", str(max(0, min(50, int(attendance_spare_rows)))),
                           "yoklama boş satırı"))
        if notify_channel in ("sms", "email", "push"):
            wanted.append(("notify_channel", notify_channel, "bildirim kanalı"))
        if trial.text(notify_template):
            wanted.append(("notify_template", trial.text(notify_template)[:64],
                           "bildirim şablonu"))
        if trial.text(notify_direct_template):
            wanted.append(("notify_direct_template", trial.text(notify_direct_template)[:64],
                           "tek kişilik hatırlatma şablonu"))

        changed: list[str] = []
        for key, value, label in wanted:
            try:
                await self._set_pref(key, value, actor)
            except Exception as failure:  # noqa: BLE001 — K7
                self._log.warning("tercih yazılamadı", key=key, error=str(failure))
                return {"ok": False, "changed": changed,
                        "error": f"“{label}” kaydedilemedi: {self._fail(failure)}"}
            changed.append(label)
        return {"ok": True, "error": "", "changed": changed}

    # ================================================================ rapor

    async def export_csv(self, exam_id: int, *, scope: str = "members") -> dict[str, Any]:
        """Katılımcı ya da sonuç listesinin CSV'si — rapor klasörüne yazılır."""
        if scope == "results":
            # Tek sayfa ÇEKİLMEZ: geçit `per_page`i 50'ye kırpıyor ve dosyanın
            # 51. satırdan sonrası sessizce eksik çıkardı.
            rows, _, truncated, error = await self._result_set(int(exam_id))
            if error:
                return {"ok": False, "error": error}
            headers = ["Kayıt no", "Ad Soyad", "Sınıf", "Katılım", "Doğru", "Yanlış", "Boş",
                       "Net", "Puan", "Sıra"]
            table = [[row["code"], row["name"], row["grade"], row["attendanceLabel"],
                      row["correct"], row["wrong"], row["blank"], row["net"], row["score"],
                      row["rank"]] for row in rows]
            name = "magaza-deneme-sonuclari"
            if truncated:
                table.append(["", "LİSTE TAVANA DAYANDI — eksik olabilir", "", "", "", "",
                              "", "", "", ""])
        else:
            rows, truncated, error = await self._all_members(int(exam_id))
            if error:
                return {"ok": False, "error": error}
            headers = ["Kayıt no", "Ad Soyad", "E-posta", "Telefon", "Sınıf", "Şehir",
                       "İlçe", "Ödeme", "Katılım", "Kayıt tarihi"]
            table = [[row["code"], row["name"], row["email"], row["phone"], row["grade"],
                      row["city"], row["district"], row["paymentLabel"],
                      row["attendanceLabel"], row["registeredAt"]] for row in rows]
            name = "magaza-deneme-katilimcilari"
            if truncated:
                table.append(["", "LİSTE TAVANA DAYANDI — eksik olabilir", "", "", "", "",
                              "", "", "", ""])

        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        filename = f"{name}-{stamp}.csv"
        try:
            path = write_private(self._export_dir / filename, csv_bytes(headers, table))
        except OSError as failure:
            return {"ok": False, "error": f"Dosya yazılamadı: {failure}"}
        return {"ok": True, "error": "", "path": str(path), "name": filename,
                "rows": len(table)}

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
        if kind not in REPORT_KINDS:
            return {"ok": False, "error": f"Bilinmeyen rapor: {kind}"}
        exam_id = trial.as_int(params.get("examId"))
        if not exam_id:
            return {"ok": False, "error": "Rapor için deneme seçilmeli."}

        detail = await self.exam(exam_id)
        if not detail.get("ok"):
            return {"ok": False, "error": detail.get("error", "Deneme okunamadı.")}
        exam = detail["exam"]

        if kind == "attendance":
            rows, truncated, error = await self._all_members(exam_id)
            if error:
                return {"ok": False, "error": error}
            if not rows:
                return {"ok": False, "error": "Bu denemede katılımcı yok; çizelge boş olurdu."}
            spare = await self._pref_int("spare_rows", self._spare_rows, low=0, high=50)
            content = self._attendance_pdf(exam, rows, truncated=truncated, spare=spare)
            name = "magaza-deneme-yoklama"
        else:
            # SIRA ve ORTALAMA tüm sonuçlardan hesaplanır: tek sayfayla (50
            # satır) üretilen karne, öğrenciye yanlış sıra ve yanlış ortalama
            # yazardı.
            results, stats, truncated, error = await self._result_set(exam_id)
            if error:
                return {"ok": False, "error": error}
            if not results:
                return {"ok": False, "error": "Bu denemenin sonucu henüz yüklenmemiş."}
            if kind == "cards":
                member_id = trial.as_int(params.get("memberId"))
                chosen = [row for row in results if row["memberId"] == member_id] \
                    if member_id else results
                if not chosen:
                    return {"ok": False, "error": "Karne üretilecek katılımcı bulunamadı."}
                content = self._cards_pdf(exam, chosen, stats)
                name = "magaza-deneme-karne"
            else:
                content = self._results_pdf(exam, results, stats, truncated=truncated)
                name = "magaza-deneme-sonuc-listesi"

        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        filename = f"{name}-{stamp}.pdf"
        try:
            path = write_private(self._export_dir / filename, content)
        except (OSError, ExportError) as failure:
            return {"ok": False, "error": str(failure)}
        self._log.info("deneme raporu üretildi", kind=kind, examId=exam_id, path=str(path))
        return {"ok": True, "error": "", "path": str(path), "name": filename,
                "bytes": len(content), "kind": kind}

    def _header(self, exam: dict[str, Any]) -> str:
        parts = [exam["name"]]
        if exam["examDate"]:
            parts.append(exam["examDate"] + (f" {exam['examTime']}" if exam["examTime"] else ""))
        if exam["venue"]:
            parts.append(exam["venue"])
        return " · ".join(parts)

    def _attendance_pdf(self, exam: dict[str, Any], members: list[dict[str, Any]], *,
                        truncated: bool, spare: int) -> bytes:
        rows = trial.attendance_rows(members, spare=spare)
        sections: list[dict[str, Any]] = [{
            "kind": "tiles", "title": "Deneme",
            "tiles": [("Kayıtlı", number(len(members))),
                      ("Kontenjan", number(exam["capacity"]["capacity"])
                       if exam["capacity"]["capacity"] else "sınırsız"),
                      ("Tarih", exam["examDate"] or "—"),
                      ("Ücret", money(exam["feeKurus"]) if exam["feeKurus"] else "Ücretsiz")],
        }, {
            "kind": "table", "title": "Yoklama çizelgesi",
            "headers": ["Sıra", "Ad Soyad", "Sınıf", "Kayıt no", "İmza"],
            "align": "RLLLL", "widths": [0.5, 3, 1, 1, 2],
            "rows": rows,
        }, {
            "kind": "note",
            "text": "Telefon ve e-posta bu çizelgede BASILMAZ: kâğıt sınıfta elden ele "
                    "dolaşır. İletişim bilgisi ekranda görünür.",
        }]
        if truncated:
            sections.append({"kind": "note",
                             "text": "Katılımcı listesi tavana dayandı; çizelge eksik olabilir."})
        return build_pdf(title="Yoklama çizelgesi", subtitle=self._header(exam),
                         sections=sections, footer="Kontrol Merkezi · Deneme Kulübü")

    def _cards_pdf(self, exam: dict[str, Any], rows: list[dict[str, Any]],
                   stats: dict[str, Any]) -> bytes:
        """Kişi başı sonuç karnesi. Her katılımcı KENDİ SAYFASINDA başlar —
        karneler tek tek dağıtıldığı için sayfa ortasında bölünmemeli."""
        sections: list[dict[str, Any]] = []
        for index, row in enumerate(rows):
            if index:
                sections.append({"kind": "break"})
            sections.append({
                "kind": "tiles", "title": row["name"],
                "tiles": [("Net", trial.text(row["net"]) or "—"),
                          ("Puan", trial.text(row["score"]) or "—"),
                          ("Sıra", trial.text(row["rank"]) or "—"),
                          ("Katılım", row["attendanceLabel"])],
            })
            sections.append({
                "kind": "table", "title": "Ayrıntı",
                "headers": ["Doğru", "Yanlış", "Boş", "Net", "Puan"],
                "align": "RRRRR", "widths": [1, 1, 1, 1, 1],
                "rows": [[trial.text(row["correct"]) or "—", trial.text(row["wrong"]) or "—",
                          trial.text(row["blank"]) or "—", trial.text(row["net"]) or "—",
                          trial.text(row["score"]) or "—"]],
            })
            sections.append({
                "kind": "tiles", "title": "Denemenin geneli",
                "tiles": [("Katılan", number(stats.get("attended") or 0)),
                          ("Net ortalaması", trial.text(stats.get("netAverage")) or "—"),
                          ("En yüksek net", trial.text(stats.get("netBest")) or "—"),
                          ("Puan ortalaması", trial.text(stats.get("scoreAverage")) or "—")],
            })
        return build_pdf(title="Sonuç karnesi", subtitle=self._header(exam),
                         sections=sections, footer="Kontrol Merkezi · Deneme Kulübü")

    def _results_pdf(self, exam: dict[str, Any], rows: list[dict[str, Any]],
                     stats: dict[str, Any], *, truncated: bool = False) -> bytes:
        ordered = sorted(rows, key=lambda row: (row["rank"] or 10**9))
        sections: list[dict[str, Any]] = [{
            "kind": "tiles", "title": "Özet",
            "tiles": [("Sonuç", number(stats.get("count") or 0)),
                      ("Katılan", number(stats.get("attended") or 0)),
                      ("Net ortalaması", trial.text(stats.get("netAverage")) or "—"),
                      ("En yüksek net", trial.text(stats.get("netBest")) or "—")],
        }, {
            "kind": "table", "title": "Sonuç listesi",
            "headers": ["Sıra", "Ad Soyad", "Sınıf", "Doğru", "Yanlış", "Net", "Puan"],
            "align": "RLLRRRR", "widths": [0.6, 3, 1, 0.8, 0.8, 0.9, 1],
            "rows": [[trial.text(row["rank"]) or "—", row["name"], row["grade"] or "—",
                      trial.text(row["correct"]) or "—", trial.text(row["wrong"]) or "—",
                      trial.text(row["net"]) or "—", trial.text(row["score"]) or "—"]
                     for row in ordered[:400]],
        }]
        if len(ordered) > 400:
            sections.append({"kind": "note", "text": "Liste ilk 400 sonuçla sınırlandı."})
        if truncated:
            sections.append({"kind": "note",
                             "text": "Sonuç listesi tavana dayandı; kayıtların tamamı "
                                     "okunamadı ve sıralama eksik olabilir."})
        if exam["resultsState"] != "published":
            sections.append({"kind": "note",
                             "text": "Bu sonuçlar HENÜZ YAYINLANMADI; katılımcı göremiyor."})
        return build_pdf(title="Deneme sonuçları", subtitle=self._header(exam),
                         sections=sections, footer="Kontrol Merkezi · Deneme Kulübü")

    async def _render_pages(self, path: Path, *, max_pages: int = 12,
                            dpi: int = 110) -> list[str]:
        binary = shutil.which("pdftoppm")
        if not binary:
            raise PreviewError(
                "Önizleme üretilemedi: `pdftoppm` yok (poppler-utils kurulmalı). "
                "Rapor yine de kaydedildi ve yazdırılabilir.")
        with tempfile.TemporaryDirectory(prefix="km-deneme-onizleme-") as folder:
            target = Path(folder) / "sayfa"
            process = None
            try:
                process = await asyncio.create_subprocess_exec(
                    binary, "-png", "-r", str(int(dpi)), "-f", "1", "-l", str(int(max_pages)),
                    str(path), str(target),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                _, err = await asyncio.wait_for(process.communicate(), timeout=60)
            except TimeoutError:
                # Süre dolunca süreç ÖLDÜRÜLÜR: aksi hâlde her zaman aşımı
                # geçici klasör silinmiş bir pdftoppm'i arkada bırakıyordu.
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
