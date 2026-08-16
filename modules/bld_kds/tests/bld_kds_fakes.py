"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`FakeStore` SQL'i ayrıştırmaz; servisin yazdığı dört ifadeyi (denetim satırı,
önizleme kaydı, önizleme kapatma, tercih okuma) tanıyacak kadarını yapar. Amaç
çekirdek depoyu taklit etmek değil, servisin DOĞRU ANDA DOĞRU SATIRI yazdığını
görmek — özellikle `result="denendi"` izinin geçit çağrısından ÖNCE düşmesini.

`FakeApi` `bld.api` yeteneğinin testlik yüzüdür. `.calls` her çağrıyı sırasıyla
tutar: "kuru provada uzağa çağrı gitmedi" iddiası ancak bu liste boş kalarak
kanıtlanabilir. `.fail` kümesine bir metot adı atılırsa o metot patlar ve K7
(geçit düşerse ekran ayakta kalır) sınanır.
"""

from __future__ import annotations

import json
from typing import Any

#: K-21 §2.1'deki `device` nesnesi, kısaltılmadan. Alan adları ve tipleri
#: SÖZLEŞMEDEKİ gibidir — modülün kendi uydurduğu bir gövdeye karşı geçen test
#: hiçbir şey kanıtlamaz. `settings` bloğu 16 mevcut anahtarı taşır; yeni 7
#: kilit anahtarı BİLEREK YOKTUR: sahadaki cihazın bugünkü hâli budur ve alan
#: eklenmesinin o cihazı kilitlememesi gerekir.
DEVICE: dict[str, Any] = {
    "id": 3, "name": "Mutfak Kasası",
    "online": True, "last_seen_at": "2026-08-14T08:59:10Z",
    "created_at": "2026-08-04T10:00:00Z", "revoked_at": None,
    "pairing": {"code": "ABCD-2345", "expires_at": "2026-08-14T09:10:00Z", "usable": True},
    "health": {"reported_at": "2026-08-14T08:59:10Z", "printer_ok": True,
               "print_queue_pending": 0, "print_queue_failed": 0, "app_version": "1.0.0"},
    "settings": {"poll_seconds": 5, "sound_enabled": True, "warning_after_minutes": 10,
                 "late_after_minutes": 20, "printer_device_path": "/dev/usb/lp0",
                 "printer_code_page": 29, "health_seconds": 30,
                 "connection_alarm_seconds": 60, "alarm_silenceable": True,
                 "volume_percent": 80, "audio_sink": "", "tts_enabled": False,
                 "tts_rate_percent": 100, "alarm_repeat_seconds": 10,
                 "alarm_max_repeats": 0, "touch_mode": True,
                 "updated_at": "2026-08-12T12:00:00Z"},
    "settings_updated_at": "2026-08-12T12:00:00Z",
    "pending_command_count": 0,
}

#: `OrderPresenter::editable` görünümünün kısaltılmışı.
ORDER: dict[str, Any] = {
    "id": 12, "order_number": "S-12", "status": "hazirlaniyor", "revision": 1,
    "customer_name": "Ayşe Yılmaz", "requested_at": "2026-08-14T12:00:00Z",
    "customer_note": None,
    "items": [{"menu_id": 4, "name": "Mercimek Çorbası", "quantity": 2},
              {"menu_id": 5, "name": "Ayran", "quantity": 1}],
}


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

    def __init__(self, module_id: str = "bld_kds") -> None:
        self.module_id = module_id
        self.audit: list[dict[str, Any]] = []
        self.previews: dict[str, dict[str, Any]] = {}
        self.prefs: dict[str, str] = {}
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
        elif "_settings_preview" in text and text.startswith("INSERT"):
            token, device_id, proposed, baseline, diff, actor, reason, created = params
            self.previews[token] = {"token": token, "device_id": device_id,
                                    "proposed": proposed, "baseline": baseline,
                                    "diff": diff, "status": "dry_run", "actor": actor,
                                    "reason": reason, "created_at": created,
                                    "applied_at": ""}
        elif "_settings_preview" in text and text.startswith("UPDATE"):
            applied_at, token = params
            row = self.previews.get(token)
            if row:
                row.update(status="applied", applied_at=applied_at)
        elif "_prefs" in text:
            self.prefs[params[0]] = params[1]

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        if "_settings_preview" in sql:
            return self.previews.get(params[0])
        return None

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if "_prefs" in sql:
            return [{"key": key, "value": value} for key, value in self.prefs.items()]
        if "_audit" in sql:
            return list(reversed(self.audit))
        return []

    # ------------------------------------------------------------- kolaylık

    def actions(self, action: str) -> list[dict[str, Any]]:
        return [row for row in self.audit if row["action"] == action]

    def results(self, action: str) -> list[str]:
        return [row["result"] for row in self.audit if row["action"] == action]

    def detail(self, index: int) -> dict[str, Any]:
        return json.loads(self.audit[index]["detail"])


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
    """`bld.api` yeteneğinin testlik yüzü. Yalnız KDS metotları var.

    METOT ADLARI VE İMZALARI `modules/bld_api/backend/client.py` İLE BİREBİR
    AYNI OLMALIDIR. Uydurma bir ad (`kds_devices` gibi) buradaki testleri
    yeşil tutar ama canlıda `AttributeError` verir — ve servis istisnayı K7
    gereği yuttuğu için hata ekranda "BLD'ye ulaşılamadı" diye görünür:
    yanlış metot adı, düşmüş bir sunucudan AYIRT EDİLEMEZ.

    `menu` metodu BİLEREK YOKTUR: geçit bu turda taşımıyor. Servisin eksik
    metodu `getattr` ile karşılayıp çökmemesi ancak böyle sınanabilir.
    """

    def __init__(self, devices: list[dict[str, Any]] | None = None,
                 orders: dict[int, dict[str, Any]] | None = None) -> None:
        self.device_rows = devices if devices is not None else []
        self.orders_by_id = orders or {}
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()
        #: `fail` içindeki ad kaç BAŞARILI çağrıdan sonra patlasın. 0 = hemen.
        self.fail_after = 0

        self.overview_payload: dict[str, Any] = {
            "devices": {"total": 2, "online": 1, "revoked": 0, "printer_fault": 0,
                        "queue_pending": 0, "queue_failed": 0},
            "orders": {"active": 7, "by_status": {"yeni": 2, "hazirlaniyor": 5},
                       "today": 31, "late": 1},
            "print_jobs": {"today": 42},
            "server_time": "2026-08-14T09:00:00Z",
        }
        # Liste uçları DÜZ DİZİ döner: gerçek geçit zarfı kendi açıyor
        # (`BldApi._items`). Burada sarmalayıcı taklit etmek, servisin hiç
        # karşılaşmayacağı bir biçimi sınamak olurdu.
        self.command_rows: list[dict[str, Any]] = []
        self.print_job_rows: list[dict[str, Any]] = []
        self.order_rows: list[dict[str, Any]] = []
        self.revision_rows: list[dict[str, Any]] = []
        #: Yazma yanıtları. Gerçek geçit yazdığı kaydı geri veriyor.
        self.created_device: dict[str, Any] = {}
        self.pairing_payload: dict[str, Any] = {"code": "ABCD-2345",
                                                "expires_at": "2026-08-14T09:10:00Z"}

    # ------------------------------------------------------------- kayıt

    # `name` KONUM-ONLY (`/`): geçidin iki metodu `name=` adında bir anahtar
    # argüman taşıyor (`create_device`, `rename_device`) ve normal bir parametre
    # olsaydı çağrı "name için iki değer" diye patlardı — üstelik servis
    # istisnayı yutup `ok: False` döndüğü için test, kuralın çalıştığını
    # sanarak YEŞİL kalırdı.
    def _record(self, name: str, /, *args: Any, **kwargs: Any) -> None:
        if name in self.fail and len(self.args_of(name)) >= self.fail_after:
            raise RuntimeError(f"{name} patladı")
        self.calls.append((name, args, kwargs))

    def used(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for called, _, kwargs in self.calls if called == name]

    def args_of(self, name: str) -> list[tuple[Any, ...]]:
        return [args for called, args, _ in self.calls if called == name]

    def writes(self) -> list[str]:
        """Yazan çağrıların adları. Kuru provada BOŞ kalmalı."""
        yazan = ("create_device", "rename_device", "new_pairing_code", "revoke_device",
                 "update_device_settings", "send_command", "create_order_revision",
                 "set_order_status")
        return [name for name, _, _ in self.calls if name in yazan]

    # ------------------------------------------------------------- okuma

    async def overview(self) -> dict[str, Any]:
        self._record("overview")
        return self.overview_payload

    async def devices(self) -> list[dict[str, Any]]:
        self._record("devices")
        return [dict(row) for row in self.device_rows]

    async def device_commands(self, device_id: int) -> list[dict[str, Any]]:
        self._record("device_commands", device_id)
        return list(self.command_rows)

    async def print_jobs(self, *, device_id: int | None = None, order_id: int | None = None,
                         limit: int | None = None) -> list[dict[str, Any]]:
        self._record("print_jobs", device_id=device_id, order_id=order_id, limit=limit)
        return list(self.print_job_rows)

    async def orders(self, *, include_completed: bool = False,
                     since: str = "") -> list[dict[str, Any]]:
        self._record("orders", include_completed=include_completed, since=since)
        return list(self.order_rows)

    async def order(self, order_id: int) -> dict[str, Any]:
        self._record("order", order_id)
        if order_id not in self.orders_by_id:
            raise RuntimeError("Sipariş bulunamadı")
        return dict(self.orders_by_id[order_id])

    async def order_revisions(self, order_id: int) -> list[dict[str, Any]]:
        self._record("order_revisions", order_id)
        return list(self.revision_rows)

    # ------------------------------------------------------------- yazma

    async def create_device(self, *, name: str, reason: str, actor: str,
                            dry_run: bool | None = None) -> dict[str, Any]:
        self._record("create_device", name=name, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"data": self.created_device or {
            "id": 9, "name": name, "online": False,
            "pairing": {"code": "WXYZ-9876", "usable": True},
        }}

    async def rename_device(self, device_id: int, *, name: str, reason: str, actor: str,
                            dry_run: bool | None = None) -> dict[str, Any]:
        self._record("rename_device", device_id, name=name, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"data": {"id": device_id, "name": name}}

    async def new_pairing_code(self, device_id: int, *, reason: str, actor: str,
                               dry_run: bool | None = None) -> dict[str, Any]:
        self._record("new_pairing_code", device_id, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"data": dict(self.pairing_payload)}

    async def revoke_device(self, device_id: int, *, reason: str, actor: str,
                            dry_run: bool | None = None) -> dict[str, Any]:
        self._record("revoke_device", device_id, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True}

    async def update_device_settings(self, device_id: int, *, settings: dict[str, Any],
                                     reason: str, actor: str,
                                     dry_run: bool | None = None) -> dict[str, Any]:
        self._record("update_device_settings", device_id, settings=settings, reason=reason,
                     actor=actor, dry_run=dry_run)
        return {"data": {"id": device_id, "settings": settings}}

    async def send_command(self, device_id: int, *, command: str,
                           payload: dict[str, Any] | None = None, reason: str, actor: str,
                           dry_run: bool | None = None) -> dict[str, Any]:
        self._record("send_command", device_id, command=command, payload=payload,
                     reason=reason, actor=actor, dry_run=dry_run)
        return {"data": {"id": 41, "command": command, "payload": payload,
                         "created_at": "2026-08-14T09:00:00Z"}}

    async def create_order_revision(self, order_id: int, *, items: list[dict[str, Any]],
                                    reason: str, actor: str, note: str = "",
                                    requested_at: str = "", customer_note: str = "",
                                    dry_run: bool | None = None) -> dict[str, Any]:
        self._record("create_order_revision", order_id, items=items, reason=reason,
                     actor=actor, note=note, requested_at=requested_at,
                     customer_note=customer_note, dry_run=dry_run)
        return {"revision": {"revision_no": 2, "reason": reason, "refund_kurus": 0,
                             "extra_charge_kurus": 1500,
                             "created_at": "2026-08-14T09:00:00Z"},
                "order": {**self.orders_by_id.get(order_id, {}), "revision_no": 2}}

    async def set_order_status(self, order_id: int, *, status: str, reason: str, actor: str,
                               dry_run: bool | None = None) -> dict[str, Any]:
        self._record("set_order_status", order_id, status=status, reason=reason,
                     actor=actor, dry_run=dry_run)
        return {"data": {**self.orders_by_id.get(order_id, {}), "status": status}}
