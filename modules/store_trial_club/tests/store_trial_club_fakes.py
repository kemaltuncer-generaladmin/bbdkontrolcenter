"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`FakeStore` SQL'i ayrıştırmaz; servisin yazdığı üç ifadeyi (denetim satırı,
yükleme jetonu, tercih upsert'i) tanıyacak kadarını yapar. Amaç çekirdek
depoyu taklit etmek değil, servisin doğru anda doğru satırı yazdığını görmek.
"""

from __future__ import annotations

import re
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

    def __init__(self, module_id: str = "store_trial_club") -> None:
        self.module_id = module_id
        self.audit: list[dict[str, Any]] = []
        self.uploads: dict[str, dict[str, Any]] = {}
        self.prefs: dict[str, str] = {}

    def table(self, name: str) -> str:
        return f"mod_{self.module_id}_{name}"

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        text = " ".join(sql.split())
        if "_audit" in text and text.startswith("INSERT"):
            keys = ("exam_id", "member_id", "action", "reason", "actor", "result", "detail",
                    "created_at")
            self.audit.append(dict(zip(keys, params, strict=False)))
        elif "_upload" in text and text.startswith("INSERT"):
            token, exam_id, filename, summary, rows, created = params
            self.uploads[token] = {"token": token, "exam_id": exam_id, "filename": filename,
                                   "summary": summary, "rows": rows, "status": "preview",
                                   "created_at": created}
        elif "_upload" in text and text.startswith("UPDATE"):
            status, actor, reason, applied, token = params
            row = self.uploads.get(token)
            if row:
                row.update(status=status, actor=actor, reason=reason, applied_at=applied)
        elif "_prefs" in text:
            self.prefs[params[0]] = params[1]

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        text = " ".join(sql.split())
        if "_prefs" in text:
            value = self.prefs.get(params[0])
            return {"value": value} if value is not None else None
        if "_upload" in text:
            return self.uploads.get(params[0])
        return None

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if "_audit" in sql:
            rows = list(reversed(self.audit))
            if re.search(r"WHERE exam_id", sql):
                rows = [row for row in rows if row["exam_id"] == params[0]]
            return rows
        return []


class FakeApi:
    """`store.api` yeteneğinin testlik yüzü. Yalnız kullanılan metotlar var."""

    def __init__(self, exams: list[dict[str, Any]] | None = None,
                 members: list[dict[str, Any]] | None = None,
                 results: list[dict[str, Any]] | None = None) -> None:
        self.exam_rows = exams or []
        self.member_rows = members or []
        self.result_rows = results or []
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()
        #: True ise sunucu gönderilen süzgeci YOK SAYAR — Laravel'in gerçek
        #: davranışı. Ekranın buna dayanıklı olduğunu sınamak için.
        self.ignore_filters = False

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        if name in self.fail:
            raise RuntimeError(f"{name} patladı")
        self.calls.append((name, args, kwargs))

    def used(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for called, _, kwargs in self.calls if called == name]

    def args_of(self, name: str) -> list[tuple[Any, ...]]:
        return [args for called, args, _ in self.calls if called == name]

    async def bbd_trial_exams(self, filters: Any = None) -> dict[str, Any]:
        self._record("bbd_trial_exams", filters)
        rows = self.exam_rows
        if filters and not self.ignore_filters and filters.get("id"):
            rows = [row for row in rows if row.get("id") == filters["id"]]
        return {"items": rows, "meta": {"total": len(rows)}}

    async def bbd_trial_members(self, filters: Any = None, *, page: int = 1,
                                per_page: int | None = None,
                                all_pages: bool = False) -> dict[str, Any]:
        self._record("bbd_trial_members", filters, page=page, per_page=per_page,
                     all_pages=all_pages)
        rows = self.member_rows
        if filters and not self.ignore_filters:
            if filters.get("exam_id"):
                rows = [row for row in rows if row.get("exam_id") == filters["exam_id"]]
            if filters.get("id"):
                rows = [row for row in rows if row.get("id") == filters["id"]]
        return {"items": rows, "meta": {"total": len(rows), "currentPage": page,
                                        "perPage": per_page or 50, "lastPage": 1}}

    async def bbd_trial_results(self, exam_id: int, *, page: int = 1,
                                per_page: int | None = None) -> dict[str, Any]:
        self._record("bbd_trial_results", exam_id, page=page, per_page=per_page)
        return {"items": self.result_rows,
                "meta": {"total": len(self.result_rows), "currentPage": page, "lastPage": 1}}

    async def bbd_save_trial_exam(self, *, payload: dict[str, Any], exam_id: int | None = None,
                                  reason: str, actor: str = "",
                                  dry_run: bool | None = None) -> dict[str, Any]:
        self._record("bbd_save_trial_exam", payload=payload, exam_id=exam_id, reason=reason,
                     actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run), "id": exam_id or 99}

    async def bbd_upload_trial_results(self, exam_id: int, *, rows: list[dict[str, Any]],
                                       reason: str, actor: str = "",
                                       dry_run: bool | None = None) -> dict[str, Any]:
        self._record("bbd_upload_trial_results", exam_id, rows=rows, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def bbd_publish_trial_results(self, exam_id: int, *, reason: str, actor: str = "",
                                        dry_run: bool | None = None) -> dict[str, Any]:
        self._record("bbd_publish_trial_results", exam_id, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def bbd_send_notification(self, *, payload: dict[str, Any], reason: str,
                                    actor: str = "",
                                    dry_run: bool | None = None) -> dict[str, Any]:
        self._record("bbd_send_notification", payload=payload, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}


class FakeNotifier:
    """`store.notify.send` yeteneğinin testlik yüzü.

    İMZA UYDURULMAZ: `store_notifications` modülündeki `NotifySender.send`
    ne alıyorsa burada da o durur (`recipients` LİSTE, `values`, `channel`).
    Uydurulmuş bir imza yüzünden bu yol testte geçip canlıda `TypeError`
    veriyordu; sahte, yeteneğin gerçek yüzeyini taklit etmek zorundadır.
    """

    def __init__(self, answer: dict[str, Any] | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self.answer = answer

    async def send(self, *, template: str, recipients: list[str], channel: str = "email",
                   values: dict[str, Any] | None = None, reason: str, actor: str = "",
                   dry_run: bool = True) -> dict[str, Any]:
        self.sent.append({"template": template, "recipients": list(recipients),
                          "channel": channel, "values": values or {}, "reason": reason,
                          "actor": actor, "dryRun": dry_run})
        return self.answer or {"ok": True, "dryRun": dry_run}


class Events:
    """Olay yolunun testlik yüzü."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, event: str, payload: dict[str, Any]) -> None:
        self.published.append((event, payload))

    def names(self) -> list[str]:
        return [name for name, _ in self.published]


def exam(exam_id: int = 1, **fields: Any) -> dict[str, Any]:
    """Ham deneme kaydı — mağazadan geldiği biçimde."""
    row = {"id": exam_id, "name": "TYT Deneme 1", "exam_date": "2026-09-20",
           "exam_time": "10:00", "capacity": 40, "enrolled": 30, "fee": "150.00",
           "venue": "Merkez Şube", "grade": "12", "registration_start": "2026-08-01",
           "registration_end": "2026-09-15", "status": "open", "results_published": False}
    row.update(fields)
    return row


def member(member_id: int = 1, **fields: Any) -> dict[str, Any]:
    """Ham katılımcı kaydı."""
    row = {"id": member_id, "exam_id": 1, "name": f"Katılımcı {member_id}",
           "email": f"k{member_id}@ornek.com", "phone": f"53212345{member_id:02d}",
           "code": f"KY-{member_id:03d}", "grade": "12", "city": "Ankara",
           "payment_status": "paid", "attended": True, "created_at": "2026-08-05T10:00:00"}
    row.update(fields)
    return row
