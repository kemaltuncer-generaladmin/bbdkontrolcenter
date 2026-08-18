"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ, SMS GÖNDERMEZ.

`FakeStore` SQL'i ayrıştırmaz; servisin yazdığı beş ifadeyi (denetim satırı,
temel çizgi, tetikleyici politikası, kuru prova jetonu, jeton kapatma)
tanıyacak kadarını yapar. Amaç çekirdek depoyu taklit etmek değil, servisin
DOĞRU ANDA DOĞRU SATIRI yazdığını görmek — özellikle `result="denendi"` izinin
geçit çağrısından ÖNCE düşmesini.

`FakeApi` `bld.api` yeteneğinin testlik yüzüdür. `.calls` her çağrıyı sırasıyla
tutar: "kuru provada gerçek gönderim yapılmadı" iddiası ancak bu liste
okunarak kanıtlanabilir. `.fail` kümesine bir metot adı atılırsa o metot patlar
ve K7 (geçit düşerse ekran ayakta kalır) sınanır.

Fixture'lar SÖZLEŞMEDEN kopyalanmıştır (`BLD/docs/control/sms.md`). Modülün
kendi uydurduğu bir gövdeye karşı geçen test hiçbir şey kanıtlamaz.
"""

from __future__ import annotations

import json
from typing import Any

#: Sözleşmedeki `GET /templates` satırı, kısaltılmadan.
TEMPLATE: dict[str, Any] = {
    "key": "order_created",
    "title": "Sipariş alındı",
    "body": "Sayın {customer_name}, {service_date} tarihli {order_no} numaralı "
            "siparişiniz alındı. Tutar: {total} TL.",
    "enabled": True,
    "variables": ["order_no", "service_date", "total", "customer_name"],
    "length": 112,
    "segments": 1,
    "has_turkish_chars": True,
    "updated_at": "2026-08-02T10:00:00Z",
}

#: KAPALI doğmuş ikinci şablon — "açma bilinçli bir harekettir" dalını sınar.
TEMPLATE_OFF: dict[str, Any] = {
    "key": "order_cancelled",
    "title": "Sipariş iptal edildi",
    "body": "{order_no} numaralı {service_date} tarihli siparişiniz iptal edildi. "
            "Sebep: {reason}",
    "enabled": False,
    "variables": ["order_no", "service_date", "reason"],
    "length": 84,
    "segments": 2,
    "has_turkish_chars": True,
    "updated_at": "2026-08-02T10:00:00Z",
}

#: Sözleşmedeki `GET /log` satırı — telefon MASKELİ, gövde kırpık.
LOG_ROW: dict[str, Any] = {
    "id": 9912, "template_key": "order_created", "phone": "532****567",
    "customer_id": 312, "order_id": 8421, "subscription_id": None,
    "body": "Sayın Mehmet K., 16.08.2026 tarihli BLD-8421 numaralı siparişiniz alındı…",
    "segments": 2, "status": "sent", "error": None, "provider_ref": "NG-77219043",
    "context": "auto", "sent_at": "2026-08-15T18:04:12Z",
}

#: Sözleşmedeki `GET /announcement` gövdesi.
ANNOUNCEMENT: dict[str, Any] = {
    "body": "Değerli müşterimiz, 30 Ağustos'ta hizmet veremeyeceğiz. "
            "Siparişlerinizi 29 Ağustos'a kadar iletebilirsiniz.",
    "audience": "active_customers",
    "length": 108,
    "segments": 2,
    "encoding": "ucs2",
    "last_run_at": "2026-07-14T09:00:00Z",
    "estimate": {"recipients": 186, "segments": 372},
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

    def __init__(self, module_id: str = "bld_sms") -> None:
        self.module_id = module_id
        self.audit: list[dict[str, Any]] = []
        self.baselines: dict[str, dict[str, Any]] = {}
        self.triggers: dict[str, dict[str, Any]] = {}
        self.dry_runs: dict[str, dict[str, Any]] = {}
        #: `True` ise her yazma patlar — "iz yazılamazsa iş durmasın" (K7).
        self.broken = False

    def table(self, name: str) -> str:
        return f"mod_{self.module_id}_{name}"

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        if self.broken:
            raise RuntimeError("depo yazılamıyor")
        text = " ".join(sql.split())
        if "_audit" in text and text.startswith("INSERT"):
            keys = ("target_type", "target_key", "action", "reason", "actor", "result",
                    "detail", "created_at")
            self.audit.append(dict(zip(keys, params, strict=False)))
        elif "_templates" in text and text.startswith("INSERT"):
            keys = ("template_key", "body_hash", "length", "segments", "enabled",
                    "actor", "reason", "updated_at")
            row = dict(zip(keys, params, strict=False))
            self.baselines[str(row["template_key"])] = row
        elif "_triggers" in text and text.startswith("INSERT"):
            keys = ("template_key", "confirmed", "last_state", "changed_by", "reason",
                    "changed_at")
            row = dict(zip(keys, params, strict=False))
            key = str(row["template_key"])
            # `confirmed` GERİ DÖNMEZ: SQL'deki MAX(...) davranışı burada da
            # taklit edilir, aksi hâlde "bir kez açıldı" bilgisi kapatınca
            # kaybolur ve test yanlış bir davranışı onaylardı.
            onceki = self.triggers.get(key, {})
            row["confirmed"] = max(int(onceki.get("confirmed") or 0),
                                   int(row["confirmed"]))
            self.triggers[key] = row
        elif "_announcement_dry" in text and text.startswith("INSERT"):
            keys = ("token", "audience", "recipients", "segments", "body_hash", "actor",
                    "reason", "created_at")
            row = dict(zip(keys, params, strict=False))
            row["used_at"] = ""
            self.dry_runs[str(row["token"])] = row
        elif "_announcement_dry" in text and text.startswith("UPDATE"):
            if "WHERE token" in text:
                used_at, token = params
                row = self.dry_runs.get(str(token))
                if row:
                    row["used_at"] = used_at
            else:
                (used_at,) = params
                for row in self.dry_runs.values():
                    if not row.get("used_at"):
                        row["used_at"] = used_at

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        if "_announcement_dry" in sql:
            return self.dry_runs.get(str(params[0]))
        return None

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if "_templates" in sql:
            return list(self.baselines.values())
        if "_triggers" in sql:
            return list(self.triggers.values())
        if "_announcement_dry" in sql:
            rows = [row for row in self.dry_runs.values() if not row.get("used_at")]
            return sorted(rows, key=lambda row: str(row.get("created_at")), reverse=True)
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
    """`bld.api` yeteneğinin testlik yüzü. Yalnız SMS metotları var.

    METOT ADLARI VE İMZALARI `modules/bld_api/backend/client.py` İLE BİREBİR
    AYNI OLMALIDIR (donmuş tablo: `modules/bld_api/README.md` §10). Uydurma bir
    ad (`sms_template_list` gibi) buradaki testleri yeşil tutar ama canlıda
    `AttributeError` verir — ve servis istisnayı K7 gereği yuttuğu için hata
    ekranda "BLD'ye ulaşılamadı" diye görünür: yanlış metot adı, düşmüş bir
    sunucudan AYIRT EDİLEMEZ.
    """

    def __init__(self, templates: list[dict[str, Any]] | None = None) -> None:
        self.template_rows = templates if templates is not None else [dict(TEMPLATE)]
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()
        #: `fail` içindeki ad kaç BAŞARILI çağrıdan sonra patlasın. 0 = hemen.
        self.fail_after = 0

        self.templates_meta: dict[str, Any] = {"sender_driver": "netgsm",
                                               "sender_configured": True,
                                               "sender_header": "BLEZZETDNYM",
                                               "sender_header_source": "env",
                                               "sender_missing": []}
        #: `GET /sms/netgsm` gövdesi. PAROLA YOKTUR ve olmayacak: BLD onu
        #: hiçbir uçtan geri vermiyor, ekran yalnız "dolu mu" bilgisini görür.
        self.netgsm_payload: dict[str, Any] = {
            "header": "BLEZZETDNYM", "stored_header": "", "env_header": "BLEZZETDNYM",
            "source": "env", "header_max": 11, "username_configured": True,
            "password_configured": True, "missing": [], "driver": "netgsm",
        }
        self.log_rows: list[dict[str, Any]] = [dict(LOG_ROW)]
        self.log_meta: dict[str, Any] = {"page": 1, "per_page": 25, "total": 1,
                                         "last_page": 1, "sent_count": 1,
                                         "failed_count": 0, "segment_total": 2}
        self.announcement_payload: dict[str, Any] = dict(ANNOUNCEMENT)
        #: Yazma yanıtları. Sunucu kuru provada `would`, gerçekte `data` verir.
        self.run_would: dict[str, Any] = {
            "action": "sms.announcement.run", "audience": "active_customers",
            "recipients": 186, "segments": 368,
            "sample_rendered": "Değerli müşterimiz, 30 Ağustos'ta hizmet veremeyeceğiz…",
        }
        self.run_data: dict[str, Any] = {
            "recipients": 186, "sent": 184, "failed": 2, "segments": 368,
            "started_at": "2026-08-16T09:00:00Z", "finished_at": "2026-08-16T09:00:07Z",
            "failures": [{"phone": "533****112", "error": "Geçersiz numara"}],
        }
        self.test_status = "sent"

    # ------------------------------------------------------------- kayıt

    def _record(self, name: str, /, *args: Any, **kwargs: Any) -> None:
        if name in self.fail and len(self.args_of(name)) >= self.fail_after:
            raise RuntimeError(f"{name} patladı")
        self.calls.append((name, args, kwargs))

    def used(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for called, _, kwargs in self.calls if called == name]

    def args_of(self, name: str) -> list[tuple[Any, ...]]:
        return [args for called, args, _ in self.calls if called == name]

    def writes(self) -> list[str]:
        """Yazan çağrıların adları."""
        yazan = ("update_sms_template", "preview_sms_template", "send_test_sms",
                 "set_sms_announcement", "run_sms_announcement", "set_sms_netgsm")
        return [name for name, _, _ in self.calls if name in yazan]

    # ------------------------------------------------------------- okuma

    async def sms_templates(self) -> dict[str, Any]:
        self._record("sms_templates")
        return {"items": [dict(row) for row in self.template_rows],
                "meta": dict(self.templates_meta)}

    async def sms_log(self, *, phone: str = "", template_key: str = "", status: str = "",
                      context: str = "", customer_id: int | None = None,
                      date_from: str = "", date_to: str = "", page: int = 1,
                      per_page: int | None = None) -> dict[str, Any]:
        self._record("sms_log", phone=phone, template_key=template_key, status=status,
                     context=context, customer_id=customer_id, date_from=date_from,
                     date_to=date_to, page=page, per_page=per_page)
        return {"items": [dict(row) for row in self.log_rows], "meta": dict(self.log_meta)}

    async def sms_announcement(self) -> dict[str, Any]:
        self._record("sms_announcement")
        return dict(self.announcement_payload)

    async def sms_netgsm(self) -> dict[str, Any]:
        self._record("sms_netgsm")
        return dict(self.netgsm_payload)

    # ------------------------------------------------------------- yazma

    async def update_sms_template(self, key: str, *, reason: str, actor: str,
                                  dry_run: bool | None = None,
                                  **fields: Any) -> dict[str, Any]:
        """KISMİ yazma. `body`/`enabled` **kwargs ile toplanır bilerek.

        Gerçek geçit verilmeyen alanı `UNSET` bırakıp gövdeye HİÇ koymuyor.
        Sabit parametreli bir taklit, "yalnız değişen alan gönderildi"
        iddiasını sınanamaz hâle getirirdi: alan hep görünür ve değeri `None`
        olurdu — yani "gönderilmedi" ile "boşalt" aynı görünürdü.
        """
        self._record("update_sms_template", key, reason=reason, actor=actor,
                     dry_run=dry_run, **fields)
        text = fields.get("body") if isinstance(fields.get("body"), str) else ""
        kayit = {"key": key, "length": len(text), "segments": 1,
                 "enabled": bool(fields.get("enabled")),
                 "updated_at": "2026-08-16T09:00:00Z"}
        anahtar = "would" if dry_run else "data"
        return {"ok": True, "dry_run": bool(dry_run), "audit_id": 2301, anahtar: kayit}

    async def preview_sms_template(self, key: str, *, body: str = "",
                                   sample: dict[str, Any] | None = None, reason: str,
                                   actor: str, dry_run: bool | None = None) -> dict[str, Any]:
        self._record("preview_sms_template", key, body=body, sample=sample, reason=reason,
                     actor=actor, dry_run=dry_run)
        return {"ok": True, "dry_run": bool(dry_run), "audit_id": 2302,
                "data": {"key": key, "rendered": "Sayın Mehmet Kaya, siparişiniz hazır.",
                         "length": 38, "segments": 1, "encoding": "ucs2",
                         "unresolved_variables": []}}

    async def send_test_sms(self, *, phone: str, template_key: str = "", body: str = "",
                            sample: dict[str, Any] | None = None, reason: str, actor: str,
                            dry_run: bool | None = None) -> dict[str, Any]:
        self._record("send_test_sms", phone=phone, template_key=template_key, body=body,
                     sample=sample, reason=reason, actor=actor, dry_run=dry_run)
        kayit = {"log_id": 9912, "phone": phone,
                 "rendered": "[DENEME] Sayın Deneme, siparişiniz alındı.",
                 "segments": 2, "status": self.test_status,
                 "error": None if self.test_status == "sent" else "Geçersiz numara"}
        anahtar = "would" if dry_run else "data"
        return {"ok": True, "dry_run": bool(dry_run), "audit_id": 2310, anahtar: kayit}

    async def set_sms_announcement(self, *, body: str, audience: str, reason: str,
                                   actor: str, dry_run: bool | None = None) -> dict[str, Any]:
        self._record("set_sms_announcement", body=body, audience=audience, reason=reason,
                     actor=actor, dry_run=dry_run)
        if not dry_run:
            self.announcement_payload = {**self.announcement_payload, "body": body,
                                         "audience": audience}
        anahtar = "would" if dry_run else "data"
        return {"ok": True, "dry_run": bool(dry_run), "audit_id": 2320,
                anahtar: {"audience": audience, "length": len(body)}}

    async def set_sms_netgsm(self, *, header: str, reason: str, actor: str,
                             dry_run: bool | None = None) -> dict[str, Any]:
        self._record("set_sms_netgsm", header=header, reason=reason, actor=actor,
                     dry_run=dry_run)
        if not dry_run:
            self.netgsm_payload = {
                **self.netgsm_payload,
                "stored_header": header,
                # AYAR ÖNCE, ORTAM SONRA: boş dize ayarı siler ve ortam
                # değişkeni yeniden yürürlüğe girer.
                "header": header or str(self.netgsm_payload.get("env_header") or ""),
                "source": "setting" if header else (
                    "env" if self.netgsm_payload.get("env_header") else "none"),
            }
        anahtar = "would" if dry_run else "data"
        return {"ok": True, "dry_run": bool(dry_run), "audit_id": 2340,
                anahtar: {"header": header},
                "warnings": ["netgsm_header_applies_next_request"]}

    async def run_sms_announcement(self, *, confirm_recipients: int, reason: str, actor: str,
                                   dry_run: bool | None = None) -> dict[str, Any]:
        self._record("run_sms_announcement", confirm_recipients=confirm_recipients,
                     reason=reason, actor=actor, dry_run=dry_run)
        if dry_run:
            return {"ok": True, "dry_run": True, "audit_id": 2330,
                    "would": dict(self.run_would)}
        return {"ok": True, "dry_run": False, "audit_id": 2331, "data": dict(self.run_data)}


class FakeNotify:
    """`notify` platform yeteneğinin testlik yüzü.

    Servis bunu YALNIZ "var mı" diye soruyor; tek bir SMS bile buradan geçmez.
    Gönderim metodu bilerek KONMADI: konsaydı, ileride yanlışlıkla çağıran bir
    değişiklik testten sessizce geçerdi.
    """
