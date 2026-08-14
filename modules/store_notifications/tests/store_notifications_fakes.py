"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`FakeStore` SQL'i ayrıştırmaz; servisin yazdığı ifadeleri (denetim satırı,
şablon upsert'i, tercih upsert'i, gönderim jetonu) tanıyacak kadarını yapar.
Amaç çekirdek depoyu taklit etmek değil, servisin doğru anda doğru satırı
yazdığını görmek.
"""

from __future__ import annotations

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

    def __init__(self, module_id: str = "store_notifications") -> None:
        self.module_id = module_id
        self.audit: list[dict[str, Any]] = []
        self.templates: list[dict[str, Any]] = []
        self.prefs: dict[str, str] = {}
        self.sends: dict[str, dict[str, Any]] = {}

    def table(self, name: str) -> str:
        return f"mod_{self.module_id}_{name}"

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        text = " ".join(sql.split())
        if "_audit" in text and text.startswith("INSERT"):
            keys = ("action", "reason", "actor", "result", "detail", "created_at")
            self.audit.append(dict(zip(keys, params, strict=False)))
        elif "_prefs" in text:
            self.prefs[params[0]] = params[1]
        elif "_templates" in text and text.startswith("INSERT"):
            keys = ("name", "channel", "subject", "body", "event", "active", "actor",
                    "updated_at")
            row = dict(zip(keys, params, strict=False))
            row["id"] = len(self.templates) + 1
            self.templates.append(row)
        elif "_templates" in text and text.startswith("UPDATE"):
            row_id = params[-1]
            for row in self.templates:
                if row["id"] != row_id:
                    continue
                if "active = 0" in text:          # pasifleştirme
                    row.update(active=0, actor=params[0])
                else:
                    row.update(name=params[0], channel=params[1], subject=params[2],
                               body=params[3], event=params[4], active=params[5])
        elif "_sends" in text and text.startswith("INSERT") and "'preview'" in text:
            keys = ("token", "channel", "template", "audience", "params", "recipients",
                    "parts", "cost", "actor", "created_at")
            row = dict(zip(keys, params, strict=False))
            row["status"] = "preview"
            self.sends[row["token"]] = row
        elif "_sends" in text and text.startswith("INSERT"):
            keys = ("token", "channel", "template", "params", "recipients", "actor",
                    "reason", "created_at", "applied_at")
            row = dict(zip(keys, params, strict=False))
            row["status"] = "sent"
            self.sends[row["token"]] = row
        elif "_sends" in text and text.startswith("UPDATE"):
            status, actor, reason, applied, token = params
            row = self.sends.get(token)
            if row:
                row.update(status=status, actor=actor, reason=reason, applied_at=applied)

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        text = " ".join(sql.split())
        if "_prefs" in text:
            value = self.prefs.get(params[0])
            return {"value": value} if value is not None else None
        if "_sends" in text:
            return self.sends.get(params[0])
        return None

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        text = " ".join(sql.split())
        if "_audit" in text:
            return list(reversed(self.audit))
        if "_templates" in text:
            return sorted(self.templates, key=lambda row: row["name"])
        if "_sends" in text:
            day = params[0] if params else ""
            return [row for row in self.sends.values()
                    if row.get("status") == "sent" and str(row.get("created_at", "")).startswith(day)]
        return []


class FakeNotify:
    """`notify` platform yeteneğinin testlik yüzü."""

    def __init__(self, **state: Any) -> None:
        self.state = {"provider": "netgsm", "enabled": True, "dryRun": True,
                      "configured": True, "header": "BBDUNYAM", "error": "", **state}
        self.fail = False

    async def ready(self) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError("sms katmanı patladı")
        return dict(self.state)


class FakeStoreApiError(RuntimeError):
    """Geçit hatasının testlik eşi — `code` alanı taşır.

    `StoreApiError` sınıfı IMPORT EDİLMEZ: modül modülü import etmez (K3).
    Servis de sınıfa değil, `code` alanına bakıyor.
    """

    def __init__(self, message: str, *, code: str = "") -> None:
        super().__init__(message)
        self.code = code


class FakeApi:
    """`store.api` yeteneğinin testlik yüzü. Yalnız kullanılan metotlar var."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()
        #: Uç adı → geçidin döndüreceği hata kodu (`bbd_endpoint_missing` gibi).
        self.codes: dict[str, str] = {}
        self.history_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.history_pages: list[dict[str, Any]] = []
        self.templates_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.rules_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.subscribers_payload: dict[str, Any] = {"items": [], "meta": {}}
        self.config_payload: dict[str, Any] = {}
        self.mobile_payload: dict[str, Any] = {}
        self.send_payload: dict[str, Any] = {"ok": True, "recipients": 0}
        self.health_payload: dict[str, Any] = {"ok": True, "error": "", "elapsedMs": 42}

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        if name in self.fail:
            # HATANIN TÜRÜ TAŞINABİLMELİ. Geçit hatası `code` alanı taşıyor ve
            # servis "uç yayında değil" ile "gerçek arıza"yı ona bakarak
            # ayırıyor; sahte geçit hep kodsuz bir istisna atsaydı o ayrım
            # testte hiç görünmezdi.
            raise FakeStoreApiError(f"{name} patladı", code=self.codes.get(name, ""))
        self.calls.append((name, args, kwargs))

    def used(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for called, _, kwargs in self.calls if called == name]

    def args(self, name: str) -> list[tuple[Any, ...]]:
        return [args for called, args, _ in self.calls if called == name]

    async def health(self) -> dict[str, Any]:
        self._record("health")
        return dict(self.health_payload)

    async def bbd_notifications(self, filters: Any = None, *, page: int = 1,
                                per_page: int | None = None) -> dict[str, Any]:
        self._record("bbd_notifications", filters, page=page, per_page=per_page)
        if self.history_pages:
            index = min(page, len(self.history_pages)) - 1
            return self.history_pages[index]
        return self.history_payload

    async def bbd_send_notification(self, *, payload: dict[str, Any], reason: str,
                                    actor: str = "",
                                    dry_run: bool | None = None) -> dict[str, Any]:
        self._record("bbd_send_notification", payload=payload, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"dryRun": bool(dry_run), "sent": not dry_run, **self.send_payload}

    async def bbd_notification_rules(self) -> dict[str, Any]:
        self._record("bbd_notification_rules")
        return self.rules_payload

    async def bbd_save_notification_rule(self, *, payload: dict[str, Any],
                                         rule_id: int | None = None, reason: str,
                                         actor: str = "",
                                         dry_run: bool | None = None) -> dict[str, Any]:
        self._record("bbd_save_notification_rule", payload=payload, rule_id=rule_id,
                     reason=reason, actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def email_templates(self, filters: Any = None) -> dict[str, Any]:
        self._record("email_templates", filters)
        return self.templates_payload

    async def save_email_template(self, *, payload: dict[str, Any],
                                  template_id: int | None = None, reason: str, actor: str = "",
                                  dry_run: bool | None = None) -> dict[str, Any]:
        self._record("save_email_template", payload=payload, template_id=template_id,
                     reason=reason, actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}

    async def subscribers(self, filters: Any = None, *, page: int = 1,
                          per_page: int | None = None) -> dict[str, Any]:
        self._record("subscribers", filters, page=page, per_page=per_page)
        return self.subscribers_payload

    async def configuration(self, slug: str, *, channel: str = "",
                            locale: str = "") -> dict[str, Any]:
        self._record("configuration", slug, channel=channel, locale=locale)
        return self.config_payload

    async def bbd_mobile_settings(self) -> dict[str, Any]:
        self._record("bbd_mobile_settings")
        return self.mobile_payload

    async def bbd_update_mobile_settings(self, *, payload: dict[str, Any], reason: str,
                                         actor: str = "",
                                         dry_run: bool | None = None) -> dict[str, Any]:
        self._record("bbd_update_mobile_settings", payload=payload, reason=reason, actor=actor,
                     dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run)}
