"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`FakeStore` SQL'i ayrıştırmaz; servisin dokunduğu on ifadeyi (gözlem yazma /
birleştirme / okuma, defter yazma / okuma, denetim satırı yazma / okuma, tercih
yazma / okuma) tanıyacak kadarını yapar. Amaç çekirdek depoyu taklit etmek
değil, servisin DOĞRU ANDA DOĞRU SATIRI yazdığını görmek — özellikle aynı
gözlemin İKİNCİ SATIR AÇMADIĞINI ve `result="denendi"` izinin geçit
çağrısından ÖNCE düştüğünü.

`FakeApi` `bld.api` yeteneğinin testlik yüzüdür ve YALNIZ bu ekranın kullandığı
altı metodu taşır. `.calls` her çağrıyı sırasıyla tutar: "kuru provada uzağa
gerçek yazma gitmedi" iddiası ancak `dry_run` argümanına bakarak
kanıtlanabilir — sözleşmede kuru prova İSTEĞİ GERÇEKTEN GÖNDERİR
(`00-genel.md` §3.1), yalnız sunucu `$apply`'ı çağırmaz. `.fail` kümesine bir
metot adı atılırsa o metot patlar ve K7 (geçit düşerse ekran ayakta kalır)
sınanır.

FIXTURE'LAR SÖZLEŞMEDEN KOPYALANDI (`BLD/docs/control/monitor.md`). Modülün
kendi uydurduğu bir gövdeye karşı geçen test hiçbir şey kanıtlamaz.
"""

from __future__ import annotations

import json
from typing import Any

#: `GET /api/control/monitor/events` yanıtındaki satır — sözleşmedeki örneğin
#: aynısı. `context` LİSTEDE DÖNMEZ ve burada da yok.
EVENT_ROW: dict[str, Any] = {
    "id": 3311,
    "source": "mutfakapp",
    "level": "error",
    "code": "printer_unreachable",
    "message": "Yazıcıya ulaşılamadı: /dev/usb/lp0 açılamıyor",
    "device_id": 2,
    "device_name": "Mutfak Kasa 1",
    "app_version": "1.4.2",
    "occurrence_count": 47,
    "first_seen_at": "2026-08-16T05:12:00Z",
    "last_seen_at": "2026-08-16T08:58:00Z",
    "resolved_at": None,
    "resolved_by_actor": None,
    "resolve_note": None,
}

#: `GET /api/control/monitor/events/{id}` — tam kayıt + `context` + `related`.
EVENT_DETAIL: dict[str, Any] = {
    **EVENT_ROW,
    "context": {
        "device_path": "/dev/usb/lp0",
        "errno": 13,
        "queue_pending": 4,
        "last_successful_print_at": "2026-08-16T05:02:00Z",
    },
    "related": {
        "device_online": True,
        "device_printer_ok": False,
        "queue_pending": 4,
        "queue_failed": 2,
    },
}

#: `GET /api/control/monitor/devices` satırı. ÜÇ DURUMLU ALANLAR burada
#: bilerek dolu; `null` hâli ayrı bir testte kurulur.
DEVICE_ROW: dict[str, Any] = {
    "device_id": 2,
    "name": "Mutfak Kasa 1",
    "online": True,
    "last_seen_at": "2026-08-16T08:59:40Z",
    "app_version": "1.4.2",
    "printer_ok": False,
    "sound_ok": True,
    "alarm_muted": False,
    "queue_pending": 4,
    "queue_failed": 2,
    "queue_oldest_at": "2026-08-16T08:18:00Z",
    "queue_oldest_age_minutes": 41,
    "last_error": "Yazıcıya ulaşılamadı: /dev/usb/lp0",
    "health_reported_at": "2026-08-16T08:59:00Z",
    "revoked": False,
    "open_event_count": 2,
}

DEVICE_META: dict[str, Any] = {
    "total": 2, "online": 1, "revoked": 0,
    "printer_fault": 1, "queue_pending": 4, "queue_failed": 2,
}

#: `GET /api/control/monitor/summary` — `data` bloğu (geçit zarfı açıyor).
SUMMARY: dict[str, Any] = {
    "events": {
        "open": {"info": 12, "warning": 3, "error": 2, "critical": 1},
        "open_total": 18,
        "critical_open": 1,
        "last_24h": {"info": 40, "warning": 9, "error": 6, "critical": 1},
        "oldest_open_at": "2026-08-12T11:00:00Z",
        "by_source": {"mutfakapp": 4, "musteriapp": 1, "website": 0,
                      "platform": 1, "kontrol_merkezi": 0},
    },
    "devices": {
        "total": 2, "online": 1, "revoked": 0,
        "printer_fault": 1, "queue_pending": 4, "queue_failed": 2,
        "queue_oldest_age_minutes": 41,
    },
    "health": {"status": "degraded", "reasons": ["printer_fault", "critical_event_open"]},
}

#: Her şeyin yolunda olduğu özet — kutuların yeşil olduğu hâli sınamak için.
HEALTHY: dict[str, Any] = {
    "events": {
        "open": {"info": 0, "warning": 0, "error": 0, "critical": 0},
        "open_total": 0, "critical_open": 0,
        "last_24h": {"info": 0, "warning": 0, "error": 0, "critical": 0},
        "oldest_open_at": None,
        "by_source": {"mutfakapp": 0, "musteriapp": 0, "website": 0,
                      "platform": 0, "kontrol_merkezi": 0},
    },
    "devices": {"total": 2, "online": 2, "revoked": 0, "printer_fault": 0,
                "queue_pending": 0, "queue_failed": 0, "queue_oldest_age_minutes": 0},
    "health": {"status": "ok", "reasons": []},
}

SERVER_TIME = "2026-08-16T09:00:00Z"


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
    """`ModuleStore` yüzeyi. Satırları bellekte tutar.

    Dört tabloyu ayırmak için SQL metnindeki tablo adı aranır; sorgu
    çözümlemesi yapılmaz. Kırılgan görünüyor ama sessiz değil: tanınmayan bir
    ifade `RuntimeError` ile patlar ve test kırmızı olur — servise sessizce
    "hiçbir şey yazılmadı" demekten iyidir.
    """

    def __init__(self, module_id: str = "bld_status_monitor") -> None:
        self.module_id = module_id
        self.events: list[dict[str, Any]] = []
        self.runbook: dict[str, dict[str, Any]] = {}
        self.audit: list[dict[str, Any]] = []
        self.prefs: dict[str, str] = {}
        #: `True` ise her yazma patlar — "iz yazılamazsa iş durmasın" (K7).
        self.broken = False
        #: `True` ise her okuma patlar.
        self.read_broken = False

    def table(self, name: str) -> str:
        return f"mod_{self.module_id}_{name}"

    # --------------------------------------------------------------- yazma

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        if self.broken:
            raise RuntimeError("depo yazılamıyor")
        text = " ".join(sql.split())
        if "_audit" in text and text.startswith("INSERT"):
            keys = ("target_type", "target_id", "action", "reason", "actor", "result",
                    "detail", "created_at")
            row = dict(zip(keys, params, strict=False))
            row["id"] = len(self.audit) + 1
            self.audit.append(row)
            return
        if "_events" in text and text.startswith("INSERT"):
            keys = ("kind", "source", "component", "level", "code", "message", "result",
                    "detail", "fingerprint", "first_seen_at", "last_seen_at")
            row = dict(zip(keys, params, strict=False))
            row["id"] = len(self.events) + 1
            row["occurrence_count"] = 1
            self.events.append(row)
            return
        if "_events" in text and text.startswith("UPDATE"):
            stamp, message, level, result, detail, finger = params
            for row in self.events:
                if row["fingerprint"] == finger:
                    row["occurrence_count"] += 1
                    row["last_seen_at"] = stamp
                    row["message"] = message
                    row["level"] = level
                    row["result"] = result
                    row["detail"] = detail
            return
        if "_runbook" in text and text.startswith("INSERT"):
            keys = ("key", "title", "description", "channel", "action", "device_id",
                    "enabled", "actor", "created_at", "updated_at")
            row = dict(zip(keys, params, strict=False))
            existing = self.runbook.get(row["key"])
            if existing:
                # ON CONFLICT DO UPDATE — `created_at` KORUNUR.
                row["created_at"] = existing["created_at"]
            self.runbook[row["key"]] = row
            return
        if "_prefs" in text:
            self.prefs[str(params[0])] = str(params[1])
            return
        raise RuntimeError(f"FakeStore tanımadığı ifadeyi aldı: {text[:80]}")

    # --------------------------------------------------------------- okuma

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        if self.read_broken:
            raise RuntimeError("depo okunamıyor")
        text = " ".join(sql.split())
        if "_events" in text:
            for row in self.events:
                if row["fingerprint"] == params[0]:
                    return dict(row)
            return None
        if "_runbook" in text:
            row = self.runbook.get(str(params[0]))
            return dict(row) if row else None
        raise RuntimeError(f"FakeStore tanımadığı ifadeyi aldı: {text[:80]}")

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if self.read_broken:
            raise RuntimeError("depo okunamıyor")
        text = " ".join(sql.split())
        if "_prefs" in text:
            return [{"key": key, "value": value} for key, value in self.prefs.items()]
        if "_audit" in text:
            limit = int(params[0]) if params else len(self.audit)
            return [dict(row) for row in reversed(self.audit)][:limit]
        if "_events" in text:
            limit = int(params[0]) if params else len(self.events)
            rows = sorted(self.events, key=lambda row: (row["last_seen_at"], row["id"]),
                          reverse=True)
            return [dict(row) for row in rows][:limit]
        if "_runbook" in text:
            rows = sorted(self.runbook.values(),
                          key=lambda row: (-int(row["enabled"]), str(row["key"])))
            return [dict(row) for row in rows]
        raise RuntimeError(f"FakeStore tanımadığı ifadeyi aldı: {text[:80]}")

    # ------------------------------------------------------------- kolaylık

    def actions(self, action: str) -> list[dict[str, Any]]:
        return [row for row in self.audit if row["action"] == action]

    def results(self, action: str) -> list[str]:
        return [row["result"] for row in self.audit if row["action"] == action]

    def detail(self, index: int) -> dict[str, Any]:
        return json.loads(self.audit[index]["detail"])

    def codes(self) -> list[str]:
        return [row["code"] for row in self.events]


class FakeBus:
    """Olay yolu. Kuru provada BOŞ kalmalı."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.fail = False

    async def __call__(self, event: str, payload: dict[str, Any] | None = None) -> None:
        if self.fail:
            raise RuntimeError("dinleyici patladı")
        self.events.append((event, payload or {}))

    def names(self) -> list[str]:
        return [name for name, _ in self.events]


class FakeApi:
    """`bld.api` yeteneğinin testlik yüzü. Yalnız bu ekranın kullandığı metotlar.

    METOT ADLARI VE İMZALARI `modules/bld_api/backend/client.py` İLE BİREBİR
    AYNI OLMALIDIR. Uydurma bir ad buradaki testleri yeşil tutar ama canlıda
    `AttributeError` verir; servis istisnayı K7 gereği yuttuğu için hata
    ekranda "BLD'ye ulaşılamadı" diye görünür ve YANLIŞ METOT ADI DÜŞMÜŞ BİR
    SUNUCUDAN AYIRT EDİLEMEZ.
    """

    def __init__(self, *, summary: dict[str, Any] | None = None,
                 events: list[dict[str, Any]] | None = None,
                 detail: dict[str, Any] | None = None) -> None:
        self.summary_payload = dict(summary) if summary is not None else dict(SUMMARY)
        self.event_rows = events if events is not None else [dict(EVENT_ROW)]
        self.detail_payload = dict(detail) if detail is not None else dict(EVENT_DETAIL)
        self.device_rows: list[dict[str, Any]] = [dict(DEVICE_ROW)]
        self.device_meta: dict[str, Any] = dict(DEVICE_META)
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()
        #: Patlayan metodun hata kodu (`BldApiError.code` karşılığı).
        self.fail_code = "transport"

    # ------------------------------------------------------------- kayıt

    # `name` KONUM-ONLY (`/`): geçidin metotları `command=` ve `source=` gibi
    # anahtar argümanlar taşıyor ve normal bir parametre olsaydı çağrı "iki
    # değer" diye patlardı — üstelik servis istisnayı yutup `ok: False`
    # döndüğü için test, kuralın çalıştığını sanarak YEŞİL kalırdı.
    def _record(self, name: str, /, *args: Any, **kwargs: Any) -> None:
        if name in self.fail:
            raise _FakeError(f"{name} patladı", code=self.fail_code)
        self.calls.append((name, args, kwargs))

    def used(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for called, _, kwargs in self.calls if called == name]

    def args_of(self, name: str) -> list[tuple[Any, ...]]:
        return [args for called, args, _ in self.calls if called == name]

    def names(self) -> list[str]:
        return [name for name, _, _ in self.calls]

    # ------------------------------------------------------------- okuma

    async def monitor_summary(self) -> dict[str, Any]:
        self._record("monitor_summary")
        return {**dict(self.summary_payload), "server_time": SERVER_TIME}

    async def monitor_devices(self) -> dict[str, Any]:
        self._record("monitor_devices")
        return {"items": [dict(row) for row in self.device_rows],
                "meta": dict(self.device_meta), "server_time": SERVER_TIME}

    async def monitor_events(self, *, source: Any = None, level: Any = None,
                             code: str = "", device_id: int | None = None,
                             since: str = "", resolved: str = "", q: str = "",
                             page: int = 1, per_page: int | None = None) -> dict[str, Any]:
        self._record("monitor_events", source=source, level=level, code=code,
                     device_id=device_id, since=since, resolved=resolved, q=q,
                     page=page, per_page=per_page)
        return {"items": [dict(row) for row in self.event_rows],
                "meta": {"page": page, "per_page": per_page or 25,
                         "total": len(self.event_rows), "last_page": 1,
                         "open_counts": {"info": 12, "warning": 3, "error": 2,
                                         "critical": 1}},
                "server_time": SERVER_TIME}

    async def monitor_event(self, event_id: int) -> dict[str, Any]:
        self._record("monitor_event", event_id)
        return {**dict(self.detail_payload), "server_time": SERVER_TIME}

    # ------------------------------------------------------------- yazma

    async def resolve_monitor_event(self, event_id: int, *, note: str = "", reason: str,
                                    actor: str,
                                    dry_run: bool | None = None) -> dict[str, Any]:
        self._record("resolve_monitor_event", event_id, note=note, reason=reason,
                     actor=actor, dry_run=dry_run)
        if dry_run:
            return {"ok": True, "dry_run": True, "audit_id": 2501,
                    "would": {"action": "monitor.resolve", "id": event_id}}
        return {"ok": True, "dry_run": False, "audit_id": 2501,
                "data": {"id": event_id, "resolved_at": "2026-08-16T09:05:00Z",
                         "resolved_by_actor": actor,
                         "resolve_note": f"{reason}\n{note}".strip()}}

    async def send_command(self, device_id: int, *, command: str,
                           payload: dict[str, Any] | None = None, reason: str, actor: str,
                           dry_run: bool | None = None) -> dict[str, Any]:
        self._record("send_command", device_id, command=command, payload=payload,
                     reason=reason, actor=actor, dry_run=dry_run)
        if dry_run:
            return {"ok": True, "dry_run": True, "audit_id": 2600,
                    "would": {"action": "device.command", "command": command}}
        return {"ok": True, "dry_run": False, "audit_id": 2600,
                "data": {"id": 991, "command": command, "device_id": device_id}}


class _FakeError(RuntimeError):
    """`BldApiError`in testlik ikizi.

    Gerçek sınıf import EDİLMEZ: servis de etmiyor (K2/K3 — başka bir modülün
    sınıfına bağlanmak, o modül yüklenmediğinde bu modülü de düşürürdü). Servis
    kodu `getattr(failure, "code", "")` ile okuyor; burada da aynı yüzey var.
    """

    def __init__(self, message: str, *, code: str = "http") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def make_service(*, api: FakeApi | None = None, store: FakeStore | None = None,
                 config: dict[str, Any] | None = None, bus: FakeBus | None = None) -> Any:
    """Servisi sahte bağlamla kurar. Testlerin tek kurulum yolu."""
    from bld_status_monitor_backend.service import StatusMonitorService

    settings = {"dry_run_default": False, "page_size": 25, "poll_seconds": 60}
    settings.update(config or {})
    return StatusMonitorService(
        api=api or FakeApi(),
        store=store or FakeStore(),
        log=FakeLog(),
        config=settings,
        publish=bus,
    )
