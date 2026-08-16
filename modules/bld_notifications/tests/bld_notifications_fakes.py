"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`FakeStore` SQL'i ayrıştırmaz; servisin dokunduğu iki ifadeyi (denetim satırı
yazma, yerel izi okuma) tanıyacak kadarını yapar. Amaç çekirdek depoyu taklit
etmek değil, servisin DOĞRU ANDA DOĞRU SATIRI yazdığını görmek — özellikle
`result="denendi"` izinin geçit çağrısından ÖNCE düşmesini.

`FakeApi` `bld.api` yeteneğinin testlik yüzüdür. `.calls` her çağrıyı sırasıyla
tutar: "geçersiz gövde uzağa hiç gitmedi" iddiası ancak bu liste boş kalarak
kanıtlanabilir. `.fail` kümesine bir metot adı atılırsa o metot patlar ve K7
(geçit düşerse ekran ayakta kalır) sınanır.
"""

from __future__ import annotations

import json
from typing import Any

#: `docs/control/notifications.md` §"GET / — liste" örneğinden BİREBİR.
#: Alan adları ve tipleri SÖZLEŞMEDEKİ gibidir — modülün kendi uydurduğu bir
#: gövdeye karşı geçen test hiçbir şey kanıtlamaz.
NOTICE: dict[str, Any] = {
    "id": 12,
    "title": "30 Ağustos'ta kapalıyız",
    "body": "30 Ağustos Zafer Bayramı nedeniyle üretim yapılmayacaktır.\n"
            "Siparişlerinizi 29 Ağustos'a kadar iletebilirsiniz.",
    "level": "warning",
    "audience": "customers",
    "status": "published",
    "starts_at": "2026-08-20T00:00:00Z",
    "ends_at": "2026-08-31T00:00:00Z",
    "action_label": None,
    "action_url": None,
    "dismissible": True,
    "published_at": "2026-08-16T09:00:00Z",
    "live": False,
    "seen_count": 0,
    "created_at": "2026-08-16T08:55:00Z",
    "updated_at": "2026-08-16T09:00:00Z",
}

#: `GET /{id}/stats` örneği — ölçülebilen (izlenebilir) duyuru.
STATS: dict[str, Any] = {
    "id": 12,
    "status": "published",
    "audience": "customers",
    "audience_size": 214,
    "seen_count": 84,
    "dismissed_count": 51,
    "seen_rate": 0.39,
    "first_seen_at": "2026-08-20T06:12:00Z",
    "last_seen_at": "2026-08-24T18:40:00Z",
    "trackable": True,
    "daily": [{"date": "2026-08-20", "seen": 46}, {"date": "2026-08-21", "seen": 22}],
}

#: Kitlesi `all` olan duyurunun istatistiği: sayılar `null`, SIFIR DEĞİL.
STATS_UNTRACKABLE: dict[str, Any] = {
    "id": 13, "status": "published", "audience": "all",
    "audience_size": 0, "seen_count": None, "dismissed_count": None,
    "seen_rate": None, "first_seen_at": None, "last_seen_at": None,
    "trackable": False, "daily": None,
}


class _Unset:
    """Geçidin `UNSET` işaretinin testlik ikizi.

    `client.py` kısmi yazmada bu işareti kullanıyor: `UNSET` alan gövdeye
    KONMAZ, `None` konur. Fake bunu taklit etmezse "gönderilmedi" ile
    "boşaltıldı" ayrımı testte kaybolur ve pencereyi temizlemenin çalıştığını
    sanan bir test yeşil kalırdı.
    """

    def __repr__(self) -> str:
        return "UNSET"


UNSET: Any = _Unset()


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

    def __init__(self, module_id: str = "bld_notifications") -> None:
        self.module_id = module_id
        self.audit: list[dict[str, Any]] = []
        #: `True` ise her yazma patlar — "iz yazılamazsa iş durmasın" (K7).
        self.broken = False

    def table(self, name: str) -> str:
        return f"mod_{self.module_id}_{name}"

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        if self.broken:
            raise RuntimeError("depo yazılamıyor")
        text = " ".join(sql.split())
        if "_audit" in text and text.startswith("INSERT"):
            keys = ("target_type", "target_id", "action", "reason", "actor", "result",
                    "detail", "created_at")
            self.audit.append(dict(zip(keys, params, strict=False)))

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        return None

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if self.broken:
            raise RuntimeError("depo okunamıyor")
        if "_audit" in sql:
            rows = []
            for index, row in enumerate(reversed(self.audit), start=1):
                rows.append({"id": index, **row})
            return rows
        return []

    # ------------------------------------------------------------- kolaylık

    def actions(self, action: str) -> list[dict[str, Any]]:
        return [row for row in self.audit if row["action"] == action]

    def results(self, action: str) -> list[str]:
        return [row["result"] for row in self.audit if row["action"] == action]

    def detail(self, index: int) -> dict[str, Any]:
        return json.loads(self.audit[index]["detail"])


class BldApiError(RuntimeError):
    """Geçidin hata türünün testlik ikizi — `.code` taşır."""

    def __init__(self, message: str, *, code: str = "") -> None:
        super().__init__(message)
        self.code = code


class FakeApi:
    """`bld.api` yeteneğinin testlik yüzü. Yalnız duyuru metotları var.

    METOT ADLARI VE İMZALARI `modules/bld_api/backend/client.py` İLE BİREBİR
    AYNI OLMALIDIR. Uydurma bir ad (`list_notifications` gibi) buradaki
    testleri yeşil tutar ama canlıda `AttributeError` verir — ve servis
    istisnayı K7 gereği yuttuğu için hata ekranda "BLD'ye ulaşılamadı" diye
    görünür: yanlış metot adı, düşmüş bir sunucudan AYIRT EDİLEMEZ.
    """

    def __init__(self, rows: list[dict[str, Any]] | None = None,
                 stats: dict[str, Any] | None = None) -> None:
        self.rows = [dict(NOTICE)] if rows is None else rows
        self.stats_payload = dict(STATS) if stats is None else stats
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()
        #: `fail` içindeki ad hangi hata koduyla patlasın.
        self.fail_code = ""
        self.meta: dict[str, Any] = {"page": 1, "per_page": 25, "total": 1,
                                     "last_page": 1, "live_count": 1}
        self.server_time = "2026-08-16T09:00:00Z"
        #: Yazma yanıtlarına eklenecek bloklar.
        self.warnings: list[dict[str, Any]] = []
        self.dry_run_echo = False
        self.publish_payload: dict[str, Any] = {
            "id": 12, "status": "published", "published_at": "2026-08-16T09:00:00Z",
            "live": False, "live_from": "2026-08-20T00:00:00Z",
            "estimated_audience": 214,
        }

    # ------------------------------------------------------------- kayıt

    def _record(self, name: str, /, *args: Any, **kwargs: Any) -> None:
        if name in self.fail:
            raise BldApiError(f"{name} patladı", code=self.fail_code)
        self.calls.append((name, args, kwargs))

    def used(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for called, _, kwargs in self.calls if called == name]

    def names(self) -> list[str]:
        return [name for name, _, _ in self.calls]

    def writes(self) -> list[str]:
        """Yazan çağrıların adları. Doğrulama kapısında BOŞ kalmalı."""
        yazan = ("create_notification", "update_notification",
                 "publish_notification", "archive_notification")
        return [name for name, _, _ in self.calls if name in yazan]

    def _envelope(self, data: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {"ok": True, "dry_run": self.dry_run_echo,
                                "audit_id": 2401}
        body["would" if self.dry_run_echo else "data"] = data
        if self.warnings:
            body["warnings"] = list(self.warnings)
        return body

    # ------------------------------------------------------------- okuma

    async def notifications(self, *, status: str = "", audience: str = "", level: str = "",
                            live: bool | None = None, q: str = "", page: int = 1,
                            per_page: int | None = None) -> dict[str, Any]:
        self._record("notifications", status=status, audience=audience, level=level,
                     live=live, q=q, page=page, per_page=per_page)
        return {"items": [dict(row) for row in self.rows], "meta": dict(self.meta),
                "server_time": self.server_time}

    async def notification_stats(self, notification_id: int) -> dict[str, Any]:
        self._record("notification_stats", notification_id)
        return {"data": dict(self.stats_payload), "server_time": self.server_time}

    # ------------------------------------------------------------- yazma

    async def create_notification(
        self, *, title: str, body: str, level: str = "info", audience: str = "customers",
        starts_at: str | None = None, ends_at: str | None = None,
        action_label: str | None = None, action_url: str | None = None,
        dismissible: bool = True, reason: str, actor: str, dry_run: bool | None = None,
    ) -> dict[str, Any]:
        self._record("create_notification", title=title, body=body, level=level,
                     audience=audience, starts_at=starts_at, ends_at=ends_at,
                     action_label=action_label, action_url=action_url,
                     dismissible=dismissible, reason=reason, actor=actor, dry_run=dry_run)
        return self._envelope({**NOTICE, "id": 44, "title": title, "body": body,
                               "level": level, "audience": audience, "status": "draft",
                               "starts_at": starts_at, "ends_at": ends_at,
                               "published_at": None, "live": False})

    async def update_notification(
        self, notification_id: int, *, title: Any = UNSET, body: Any = UNSET,
        level: Any = UNSET, audience: Any = UNSET, starts_at: Any = UNSET,
        ends_at: Any = UNSET, action_label: Any = UNSET, action_url: Any = UNSET,
        dismissible: Any = UNSET, reason: str, actor: str, dry_run: bool | None = None,
    ) -> dict[str, Any]:
        given = {key: value for key, value in {
            "title": title, "body": body, "level": level, "audience": audience,
            "starts_at": starts_at, "ends_at": ends_at, "action_label": action_label,
            "action_url": action_url, "dismissible": dismissible,
        }.items() if not isinstance(value, _Unset)}
        self._record("update_notification", notification_id, reason=reason, actor=actor,
                     dry_run=dry_run, **given)
        return self._envelope({**NOTICE, **given})

    async def publish_notification(self, notification_id: int, *, reason: str, actor: str,
                                   dry_run: bool | None = None) -> dict[str, Any]:
        self._record("publish_notification", notification_id, reason=reason, actor=actor,
                     dry_run=dry_run)
        return self._envelope(dict(self.publish_payload))

    async def archive_notification(self, notification_id: int, *, reason: str, actor: str,
                                   dry_run: bool | None = None) -> dict[str, Any]:
        self._record("archive_notification", notification_id, reason=reason, actor=actor,
                     dry_run=dry_run)
        return self._envelope({**NOTICE, "id": notification_id, "status": "archived",
                               "live": False})
