"""Bildirimler — iş kuralları.

GÖNDERİM MAĞAZADAN GEÇER (K4). Bu modül kendi SMTP'sini ya da SMS istemcisini
kurmaz; şablonu hazırlar, alıcıyı seçtirir ve `store.api` geçidine verir.
Mağazanın gönderim kaydı tektir — ekran ikinci bir gerçeklik üretmez.

YEREL TABLOLAR yalnız mağazada KARŞILIĞI OLMAYAN dört şeyi tutar:
  1. GEREKÇE (Bagisto denetim tutuyor, "neden" alanı yok),
  2. SMS/Push ŞABLONLARI (Bagisto'da yalnız e-posta şablonu var),
  3. SESSİZ SAATLER ve GÜNLÜK LİMİT (bu ekranın gönderim disiplini),
  4. TOPLU GÖNDERİM ÖNİZLEMESİ (uygulanan işin önizlenen iş olduğunu kanıtlar).

TOPLU GÖNDERİM GERİ ALINAMAZ. Canlıda alıcı sayısı 1.800 civarındadır ve
gönderilen SMS'in parası harcanmıştır. Üç kapı: ayrı izin (`.broadcast`),
gerekçe (>=10 karakter, burada DA doğrulanır) ve `dryRun` varsayılanı — kuru
prova alıcı sayısını ve tahmini maliyeti verir, jeton üretir; gerçek gönderim
o jetonla gelir.

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
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from km_sdk import ExportError, build_pdf, csv_bytes, money, number, report_dir, write_private

from . import messaging

#: Rapor için gönderim geçmişi taranırken kabul edilen üst sınır. Geçmiş
#: sürekli büyür; tavan olmadan bir rapor isteği geçidin hız kovasını tüketir.
REPORT_ROW_CAP = 5_000

#: Rapor taramasında en çok kaç sayfa çekilir.
#:
#: SUNUCU `per_page`'İ 50'YE KIRPAR — canlıda doğrulandı (2026-08-13,
#: `?per_page=200` → `meta.perPage: 50`). Yani 100 istesek de sayfa başına 50
#: satır gelir. Eski değer (50 sayfa) "100'lük sayfa × 50 = 5.000" varsayımıyla
#: yazılmıştı; gerçekte 2.500 satırda durup raporu `truncated` işaretliyordu ve
#: `REPORT_ROW_CAP` hiçbir zaman ulaşılamayan ölü bir sabitti. İki tavan artık
#: aynı şeyi söylüyor: 100 × 50 = 5.000.
REPORT_PAGE_SIZE = 50
REPORT_PAGE_CAP = REPORT_ROW_CAP // REPORT_PAGE_SIZE

#: Kendine test gönderiminin denetim gerekçesi. Kullanıcıdan gerekçe istemek
#: burada anlamsız olurdu (alıcı kendisi), ama geçit yazma için gerekçe şart
#: koşuyor ve denetim izinde ne olduğu okunur durmalı.
TEST_REASON = "Şablon denemesi — gönderim kendi adresime yapıldı."


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


class PreviewError(RuntimeError):
    """Önizleme görüntüsü üretilemedi. Rapor yine de kaydedilmiştir."""


class NotifySender:
    """`store.notify.send` yeteneğinin uygulaması.

    Siparişler, Talepler, İadeler ve Deneme Kulübü ekranları müşteriye bildirim
    göndermek için bunu kullanır. Kendi başına kanal açmaz: her çağrı bu
    modülün sessiz saat, günlük limit ve gerekçe kapılarından geçer — aksi
    hâlde dört ekran aynı disiplini dört kez (ve dört farklı biçimde yanlış)
    kurardı.
    """

    def __init__(self, service: NotificationsService) -> None:
        self._service = service

    async def send(self, *, template: str, recipients: list[str], channel: str = "email",
                   values: dict[str, Any] | None = None, reason: str, actor: str = "",
                   dry_run: bool = True) -> dict[str, Any]:
        """Şablon + alıcı → gönderim. `dry_run` VARSAYILAN AÇIK."""
        return await self._service.send_direct(
            template=template, recipients=recipients, channel=channel, values=values,
            reason=reason, actor=actor, dry_run=dry_run)

    async def templates(self, *, channel: str = "") -> dict[str, Any]:
        """Çağıran modülün şablon seçebilmesi için künye listesi."""
        payload = await self._service.templates()
        items = payload.get("items") or []
        if channel:
            items = [item for item in items if item["channel"] == channel]
        return {"ok": True, "items": items, "connected": payload.get("connected", False),
                "error": payload.get("error", "")}


class NotificationsService:
    """Bildirimler ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ."""

    def __init__(self, *, api: Any, store: Any, log: Any, config: dict[str, Any],
                 printer: Any = None, notify: Any = None, publish: Any = None,
                 category: str = "Mağaza", subcategory: str = "Müşteri",
                 fallback_dir: Path | None = None) -> None:
        self._api = api
        self._store = store
        self._log = log
        self._config = config or {}
        self._printer = printer
        self._notify = notify
        self._publish = publish
        self._category = category
        self._subcategory = subcategory
        self._fallback = fallback_dir or Path.home() / "km-raporlar"

        self._audit = store.table("audit")
        self._templates = store.table("templates")
        self._prefs = store.table("prefs")
        self._sends = store.table("sends")

    # ------------------------------------------------------------- ayarlar

    @property
    def _channel(self) -> str:
        return str(self._config.get("channel") or "default")

    @property
    def _locale(self) -> str:
        return str(self._config.get("locale") or "tr")

    @property
    def _page_size(self) -> int:
        return max(10, min(200, messaging.as_int(self._config.get("page_size"), 100)))

    @property
    def _recipient_cap(self) -> int:
        return max(1, messaging.as_int(self._config.get("bulk_recipient_cap"),
                                       messaging.DEFAULT_RECIPIENT_CAP))

    @property
    def _export_dir(self) -> Path:
        # HER ÇAĞRIDA yeniden çözülür: ay değişince klasör kendiliğinden değişir.
        return report_dir(self._category, subcategory=self._subcategory,
                          fallback=self._fallback,
                          configured=str(self._config.get("export_path") or ""))

    async def _pref(self, key: str, default: str = "") -> str:
        try:
            row = await self._store.fetch_one(
                f"SELECT value FROM {self._prefs} WHERE key = ?", (key,))
        except Exception as failure:  # noqa: BLE001 — tercih okunamadı, varsayılan yeter
            self._log.warning("tercih okunamadı", key=key, error=str(failure))
            return default
        return str(row["value"]) if row else default

    async def _set_pref(self, key: str, value: str, actor: str) -> None:
        await self._store.execute(
            f"INSERT INTO {self._prefs} (key, value, actor, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, actor = excluded.actor, "
            "updated_at = excluded.updated_at",
            (key, value, actor, _now()),
        )

    async def _discipline(self) -> dict[str, Any]:
        """Sessiz saatler + günlük limit — yerel tercih, yoksa modül ayarı."""
        start = await self._pref("quiet_start", str(self._config.get("quiet_hours_start") or ""))
        end = await self._pref("quiet_end", str(self._config.get("quiet_hours_end") or ""))
        raw_enabled = await self._pref("quiet_enabled", "")
        enabled = (raw_enabled == "1") if raw_enabled else \
            bool(self._config.get("quiet_hours_enabled", True))
        limit = messaging.as_int(
            await self._pref("daily_limit", ""),
            messaging.as_int(self._config.get("daily_send_limit"), 0))
        return {"start": start or "22:00", "end": end or "08:00", "enabled": enabled,
                "limit": limit}

    async def _sent_today(self) -> int:
        """Bugün BU EKRANDAN gerçekten gönderilen alıcı sayısı.

        Mağaza geçmişi sayılmaz: uç düşükse ya da yayında değilse limit
        kendiliğinden sıfırlanır ve koruma kaybolurdu. Yerel kayıt her koşulda
        durur.
        """
        try:
            rows = await self._store.fetch_all(
                f"SELECT recipients FROM {self._sends} "
                "WHERE status = 'sent' AND substr(created_at, 1, 10) = ?",
                (messaging.today_iso(),))
        except Exception as failure:  # noqa: BLE001 — sayaç okunamadı
            self._log.warning("günlük sayaç okunamadı", error=str(failure))
            return 0
        return sum(messaging.as_int(row["recipients"]) for row in rows)

    # ------------------------------------------------------------- yardımcı

    @staticmethod
    def _fail(failure: Exception) -> str:
        message = str(failure).strip()
        return message or "Mağazaya ulaşılamadı."

    @staticmethod
    def _guard(reason: str) -> str:
        """Gerekçe backend'de DE doğrulanır (K9): arayüzde gizlemek yetmez."""
        return messaging.reason_error(reason)

    async def _record(self, *, action: str, reason: str, actor: str, result: str,
                      detail: Any = None) -> None:
        """Yerel denetim izi. Ağ koparsa "ne yapmaya çalıştık" kaydı burada kalır."""
        try:
            await self._store.execute(
                f"INSERT INTO {self._audit} "
                "(action, reason, actor, result, detail, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (action, reason, actor, result,
                 json.dumps(detail or {}, ensure_ascii=False), _now()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın
            self._log.warning("denetim izi yazılamadı", action=action, error=str(failure))

    async def _emit(self, event: str, payload: dict[str, Any]) -> None:
        if self._publish is None:
            return
        try:
            await self._publish(event, payload)
        except Exception as failure:  # noqa: BLE001 — olay yolu bizi düşürmez (K7)
            self._log.warning("olay yayınlanamadı", event=event, error=str(failure))

    # ================================================================ sabit

    def catalog(self) -> dict[str, Any]:
        """Olaylar, kanallar, durumlar ve değişken paleti. AĞA ÇIKMAZ.

        Panel bu listeleri kendi içinde tutmaz: olay adı ile kural gövdesindeki
        değer aynı kaynaktan gelmeli, yoksa ekran kabul ettiği bir kuralı
        sunucu reddeder.
        """
        return {
            "ok": True,
            "events": [{"key": key, "label": label, "variables": list(keys)}
                       for key, label, keys in messaging.EVENTS],
            "channels": [{"key": key, "label": messaging.CHANNEL_LABELS[key]}
                         for key in messaging.CHANNELS],
            # Kitle listesi de BURADAN gelir. Panelin kendi kopyasını tutması,
            # backend'in kabul ettiği kümeyle ayrışabilen ikinci bir gerçeklik
            # üretirdi: ekran seçtirdiği kitlenin reddedildiğini ancak kuru
            # provada öğrenirdi.
            "audiences": [{"key": key, "label": messaging.AUDIENCE_LABELS[key]}
                          for key in messaging.AUDIENCES],
            "statuses": [{"key": key, "label": label}
                         for key, label in messaging.STATUS_LABELS.items()],
            "variables": messaging.variable_palette(),
            "sample": messaging.sample_values(),
        }

    # =============================================================== geçmiş

    async def history(self, *, q: str = "", channel: str = "", template: str = "",
                      status: str = "", event: str = "", date_from: str = "",
                      date_to: str = "", cost_min: int | None = None,
                      cost_max: int | None = None, failed_only: bool = False,
                      page: int = 1, size: int = 0) -> dict[str, Any]:
        """Gönderim geçmişi — SUNUCU TARAFI sayfalama.

        Geçmiş sürekli büyür; "hepsini çek, istemcide süz" bu ekranda hiçbir
        zaman doğru olmaz.
        """
        per_page = size or self._page_size
        filters = self._history_filters(
            q=q, channel=channel, template=template, status=status, event=event,
            date_from=date_from, date_to=date_to, cost_min=cost_min, cost_max=cost_max,
            failed_only=failed_only)

        try:
            payload = await self._api.bbd_notifications(filters, page=page, per_page=per_page)
        except Exception as failure:  # noqa: BLE001 — ekran ayakta kalmalı (K7)
            self._log.warning("gönderim geçmişi okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "items": [], "total": 0, "page": page, "size": per_page, "pages": 0,
                    "summary": messaging.history_summary([])}

        rows = [messaging.history_row(item) for item in (payload.get("items") or [])]
        meta = payload.get("meta") or {}
        return {
            "ok": True, "connected": True, "error": "",
            "items": rows,
            "total": messaging.as_int(meta.get("total"), len(rows)),
            "page": messaging.as_int(meta.get("currentPage"), page),
            "size": messaging.as_int(meta.get("perPage"), per_page),
            "pages": messaging.as_int(meta.get("lastPage"), 1),
            "summary": messaging.history_summary(rows),
        }

    def _history_filters(self, *, q: str, channel: str, template: str, status: str,
                         event: str, date_from: str, date_to: str,
                         cost_min: int | None, cost_max: int | None,
                         failed_only: bool) -> dict[str, Any]:
        # KANIT OLMAYAN SÜZGEÇ GÖNDERİLMEZ. Buraya eskiden her istekte
        # `channel_code` konuyordu; ne uygulandığı kanıtlanabiliyor (uç henüz
        # yayında değil) ne de bir işe yarıyor: canlıda TEK kanal var
        # (`id: 1`, `code: "default"`). Laravel tanımadığı parametreyi sessizce
        # yok sayar, tanıyıp da kimlik beklerse listeyi sessizce BOŞALTIR —
        # Siparişler ekranında `channel=default` tam olarak bunu yapmıştı.
        # `channel` ayarı yalnız mağaza ayarı okurken kullanılır
        # (`/configuration?channel=`, canlıda doğrulandı).
        filters: dict[str, Any] = {}
        if messaging.text(q):
            filters["q"] = messaging.text(q)
        if channel in messaging.CHANNELS:
            filters["channel"] = channel
        if messaging.text(template):
            filters["template"] = messaging.text(template)
        if status in messaging.STATUS_LABELS:
            filters["status"] = status
        if event in messaging.EVENT_KEYS:
            filters["event"] = event
        if messaging.text(date_from):
            filters["date_from"] = messaging.text(date_from)[:10]
        if messaging.text(date_to):
            filters["date_to"] = messaging.text(date_to)[:10]
        # Maliyet mağazada ONDALIK tutuluyor; süzgeç kuruş olarak geliyor.
        if cost_min is not None:
            filters["cost_from"] = f"{cost_min / 100:.2f}"
        if cost_max is not None:
            filters["cost_to"] = f"{cost_max / 100:.2f}"
        if failed_only:
            filters["failed"] = 1
        return filters

    async def resend(self, notification_id: int, *, reason: str, actor: str,
                     dry_run: bool = True) -> dict[str, Any]:
        """Başarısız gönderimi yeniden dener.

        Yeniden gönderim YENİ BİR GÖNDERİMDİR: para harcar ve müşteriye ikinci
        kez ulaşır. Bu yüzden gerekçe ister ve kuru prova varsayılan açıktır.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        payload = {"resendOf": int(notification_id)}
        await self._record(action="resend", reason=reason, actor=actor, result="denendi",
                           detail=payload)
        try:
            result = await self._api.bbd_send_notification(payload=payload, reason=reason,
                                                           actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="resend", reason=reason, actor=actor, result="hata",
                               detail={"error": str(failure), **payload})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(action="resend", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok", detail=payload)
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run)),
                "sent": bool(result.get("sent", not dry_run))}

    # ============================================================= şablonlar

    async def templates(self) -> dict[str, Any]:
        """E-posta şablonları MAĞAZADAN, SMS/Push şablonları YERELDEN.

        Bagisto'nun e-posta şablonlarını kopyalamak yanlış olurdu: mağazanın
        kendi mailer'ı onları kullanıyor ve kopya ilk değişiklikte yalan söyler.
        SMS ve Push'un Bagisto'da karşılığı YOK; onlar yerelde durur.
        """
        items: list[dict[str, Any]] = []
        connected, error = True, ""
        try:
            payload = await self._api.email_templates()
            items.extend(messaging.store_template_row(item)
                         for item in (payload.get("items") or []) if isinstance(item, dict))
        except Exception as failure:  # noqa: BLE001 — K7
            connected = False
            error = self._fail(failure)
            self._log.warning("e-posta şablonları okunamadı", error=error)

        try:
            rows = await self._store.fetch_all(
                f"SELECT id, name, channel, subject, body, event, active, updated_at "
                f"FROM {self._templates} ORDER BY name")
            items.extend(messaging.local_template_row(dict(row)) for row in rows)
        except Exception as failure:  # noqa: BLE001 — yerel tablo okunamadı
            error = error or self._fail(failure)
            self._log.warning("yerel şablonlar okunamadı", error=str(failure))

        return {"ok": True, "connected": connected, "error": error, "items": items}

    async def save_template(self, *, template: str = "", name: str, channel: str,
                            subject: str = "", body: str, event: str = "",
                            active: bool = True, reason: str, actor: str,
                            dry_run: bool = True) -> dict[str, Any]:
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        # KAYNAK ÖNCE ÇÖZÜLÜR. Alan biçimi denetimi (konu, ad, gövde) BUNDAN
        # SONRA gelir: yerel bir şablonu e-postaya çevirmeye çalışan kullanıcıya
        # önce "e-postada konu alanı yok" demek, düzeltilse bile kaydı açmayan
        # bir hatayı düzelttirmek olurdu. Asıl engel önce söylenir.
        source, number = messaging.split_template_id(template) if template else ("", 0)
        if template and not source:
            return {"ok": False, "error": "Şablon kimliği çözülemedi."}
        if source == "store" and channel != "email":
            return {"ok": False,
                    "error": "Mağazadaki şablon yalnız e-posta olabilir; kanalı değiştirmek "
                             "için yeni bir yerel şablon açın."}
        if source == "local" and channel == "email":
            return {"ok": False,
                    "error": "E-posta şablonları mağazada tutulur; yerel şablon e-postaya "
                             "çevrilemez."}

        problem = messaging.template_problem(name=name, channel=channel, subject=subject,
                                             body=body)
        if problem:
            return {"ok": False, "error": problem}
        if event and event not in messaging.EVENT_KEYS:
            return {"ok": False, "error": f"Bilinmeyen olay: {event}."}

        if channel == "email":
            return await self._save_store_template(
                template_id=number or None, name=name, body=body,
                active=active, reason=reason, actor=actor, dry_run=dry_run)
        return await self._save_local_template(
            row_id=number or None, name=name, channel=channel, subject=subject, body=body,
            event=event, active=active, reason=reason, actor=actor)

    async def _save_store_template(self, *, template_id: int | None, name: str,
                                   body: str, active: bool, reason: str, actor: str,
                                   dry_run: bool) -> dict[str, Any]:
        """E-posta şablonunu MAĞAZAYA yazar.

        GÖVDE MAĞAZANIN ŞEMASIDIR, bizim ekranımızın değil. Canlı şema
        (`/api/admin/docs` → `AdminMarketingTemplate`) yalnız üç alan tanıyor
        ve üçü de POST'ta zorunlu: `name` · `status` · `content`.

        İKİ TUZAK:
          · `status` bir KELİMEDİR (`active|inactive|draft`). `1`/`0` göndermek
            422 döndürür ve kullanıcı şablonun kaydedildiğini sanır.
          · `subject` diye bir alan YOKTUR; göndermek Laravel'in sessizce yok
            saydığı bir alan eklemekti. Konu zaten `template_problem` kapısından
            geçemez, gövdeye de konmaz.
        """
        payload = {"name": messaging.text(name), "content": body,
                   "status": messaging.store_template_status(active)}
        await self._record(action="save_email_template", reason=reason, actor=actor,
                           result="denendi", detail={"id": template_id, "name": payload["name"]})
        try:
            result = await self._api.save_email_template(
                payload=payload, template_id=template_id, reason=reason, actor=actor,
                dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="save_email_template", reason=reason, actor=actor,
                               result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(action="save_email_template", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"id": template_id, "name": payload["name"]})
        return {"ok": True, "error": "", "source": "store",
                "dryRun": bool(result.get("dryRun", dry_run))}

    async def _save_local_template(self, *, row_id: int | None, name: str, channel: str,
                                   subject: str, body: str, event: str, active: bool,
                                   reason: str, actor: str) -> dict[str, Any]:
        """SMS/Push şablonu yereldedir; mağazaya yazma yoktur, kuru prova anlamsızdır."""
        try:
            if row_id:
                await self._store.execute(
                    f"UPDATE {self._templates} SET name = ?, channel = ?, subject = ?, "
                    "body = ?, event = ?, active = ?, actor = ?, updated_at = ? WHERE id = ?",
                    (messaging.text(name), channel, messaging.text(subject), body,
                     messaging.text(event), 1 if active else 0, actor, _now(), int(row_id)))
            else:
                await self._store.execute(
                    f"INSERT INTO {self._templates} "
                    "(name, channel, subject, body, event, active, actor, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (messaging.text(name), channel, messaging.text(subject), body,
                     messaging.text(event), 1 if active else 0, actor, _now()))
        except Exception as failure:  # noqa: BLE001 — yerel yazma
            return {"ok": False, "error": f"Şablon kaydedilemedi: {failure}"}

        await self._record(action="save_local_template", reason=reason, actor=actor, result="ok",
                           detail={"id": row_id, "channel": channel, "name": name})
        return {"ok": True, "error": "", "source": "local", "dryRun": False}

    async def deactivate_template(self, template: str, *, reason: str, actor: str,
                                  dry_run: bool = True) -> dict[str, Any]:
        """Şablon SİLİNMEZ, pasifleştirilir (ADR 0012): geçmiş gönderimler
        hangi şablonla gittiğini göstermeye devam etmeli."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        source, number = messaging.split_template_id(template)
        if not source:
            return {"ok": False, "error": "Şablon kimliği çözülemedi."}
        if source == "store":
            try:
                # Durum KELİMEDİR (canlı şema): `0` göndermek 422 döndürür ve
                # şablon aktif kalırdı — ekran "pasifleştirildi" derken.
                result = await self._api.save_email_template(
                    payload={"status": messaging.store_template_status(False)},
                    template_id=number, reason=reason, actor=actor, dry_run=dry_run)
            except Exception as failure:  # noqa: BLE001 — K7
                return {"ok": False, "error": self._fail(failure)}
            dry = bool(result.get("dryRun", dry_run))
        else:
            try:
                await self._store.execute(
                    f"UPDATE {self._templates} SET active = 0, actor = ?, updated_at = ? "
                    "WHERE id = ?", (actor, _now(), number))
            except Exception as failure:  # noqa: BLE001 — yerel yazma
                return {"ok": False, "error": f"Şablon pasifleştirilemedi: {failure}"}
            dry = False

        await self._record(action="deactivate_template", reason=reason, actor=actor,
                           result="dry_run" if dry else "ok", detail={"template": template})
        return {"ok": True, "error": "", "dryRun": dry}

    def preview_template(self, *, channel: str, subject: str = "", body: str,
                         values: dict[str, Any] | None = None,
                         recipients: int = 1) -> dict[str, Any]:
        """Örnek veriyle doldurulmuş önizleme + SMS sayacı. AĞA ÇIKMAZ."""
        problem = messaging.channel_error(channel)
        if problem:
            return {"ok": False, "error": problem}
        data = {**messaging.sample_values(), **(values or {})}
        rendered_body = messaging.render(body, data)
        rendered_subject = messaging.render(subject, data)
        price = messaging.as_int(self._config.get("sms_price_kurus"), 0)
        plan = messaging.sms_plan(rendered_body["text"], recipients=recipients,
                                  price_kurus=price) if channel == "sms" else None
        return {
            "ok": True, "error": "",
            "subject": rendered_subject["text"],
            "body": rendered_body["text"],
            "missing": sorted(set(rendered_body["missing"] + rendered_subject["missing"])),
            "unknown": sorted(set(rendered_body["unknown"] + rendered_subject["unknown"])),
            "plan": plan,
        }

    async def test_send(self, *, channel: str, to: str, subject: str = "", body: str,
                        actor: str) -> dict[str, Any]:
        """Kendime test gönder — TEK alıcı, örnek veriyle doldurulmuş metin.

        Sessiz saat kapısı UYGULANMAZ: alıcı kullanıcının kendisidir ve amaç
        şablonu denemektir. Günlük limit de tüketilmez; tek mesajın limiti
        yemesi, asıl işi engelleyen bir yan etki olurdu.
        """
        problem = messaging.channel_error(channel)
        if problem:
            return {"ok": False, "error": problem}
        target = messaging.text(to)
        if not target:
            return {"ok": False, "error": "Test gönderimi için kendi adresinizi/numaranızı yazın."}

        preview = self.preview_template(channel=channel, subject=subject, body=body)
        payload = {"channel": channel, "to": [target], "subject": preview["subject"],
                   "body": preview["body"], "test": True}
        await self._record(action="test_send", reason=TEST_REASON, actor=actor,
                           result="denendi", detail={"channel": channel, "to": target})
        try:
            result = await self._api.bbd_send_notification(
                payload=payload, reason=TEST_REASON, actor=actor, dry_run=False)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="test_send", reason=TEST_REASON, actor=actor,
                               result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(action="test_send", reason=TEST_REASON, actor=actor, result="ok",
                           detail={"channel": channel, "to": target})
        return {"ok": True, "error": "", "to": target,
                "sent": bool(result.get("sent", True)), "missing": preview["missing"]}

    # =============================================================== kurallar

    @staticmethod
    def _pending(failure: Exception) -> bool:
        """Uç henüz yayında değil mi — yoksa gerçek bir arıza mı?

        İkisi ekranda ayrı davranış gerektirir: yayında olmayan uçta kural
        yazma düğmesi TIKLANMADAN kapanmalı, gerçek arızada "tekrar dene"
        anlamlıdır. Kod alanı `StoreApiError` üstünde durur; sınıf import
        EDİLMEZ (K3), yalnız kod dizesi taşınır.
        """
        return getattr(failure, "code", "") == "bbd_endpoint_missing"

    async def rules(self) -> dict[str, Any]:
        try:
            payload = await self._api.bbd_notification_rules()
        except Exception as failure:  # noqa: BLE001 — uç henüz yayında olmayabilir
            self._log.warning("kurallar okunamadı", error=str(failure))
            # Kural YAZMA ucu okuma ucuyla aynı pakette geliyor: liste 404
            # dönüyorsa yazma da dönecektir. Düğmeyi açık bırakmak, kuralı
            # kurdurup gerekçe yazdırdıktan sonra reddetmek olurdu.
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "endpointPending": self._pending(failure), "items": []}
        rows = [messaging.rule_row(item) for item in (payload.get("items") or [])
                if isinstance(item, dict)]
        return {"ok": True, "connected": True, "error": "", "endpointPending": False,
                "items": rows}

    async def save_rule(self, *, rule_id: int | None, event: str, channel: str,
                        template: str, condition: str = "", delay_minutes: int = 0,
                        active: bool = True, reason: str, actor: str,
                        dry_run: bool = True) -> dict[str, Any]:
        """Olay → koşul → şablon + kanal + gecikme.

        Kural MAĞAZADA çalışır: tetikleyen olay orada olur. Yerelde kural
        tutmak, Kontrol Merkezi kapalıyken bildirim gitmemesi demekti.
        """
        problem = self._guard(reason) or messaging.rule_problem(
            event=event, channel=channel, template=template, delay_minutes=delay_minutes)
        if problem:
            return {"ok": False, "error": problem}

        payload = {"event": event, "channel": channel, "template_id": template,
                   "condition": messaging.text(condition),
                   "delay_minutes": max(0, messaging.as_int(delay_minutes)),
                   "active": 1 if active else 0}
        await self._record(action="save_rule", reason=reason, actor=actor, result="denendi",
                           detail={"id": rule_id, **payload})
        try:
            result = await self._api.bbd_save_notification_rule(
                payload=payload, rule_id=rule_id, reason=reason, actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="save_rule", reason=reason, actor=actor, result="hata",
                               detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(action="save_rule", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"id": rule_id, **payload})
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run))}

    async def toggle_rule(self, rule_id: int, *, active: bool, reason: str, actor: str,
                          dry_run: bool = True) -> dict[str, Any]:
        """Kuralı açar/kapatır. Kural SİLİNMEZ: kapalı kural neyin neden
        durdurulduğunu anlatır, silinen kural iz bırakmaz."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        try:
            result = await self._api.bbd_save_notification_rule(
                payload={"active": 1 if active else 0}, rule_id=int(rule_id), reason=reason,
                actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        await self._record(action="toggle_rule", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"id": int(rule_id), "active": active})
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run))}

    # =============================================================== kanallar

    async def channels(self) -> dict[str, Any]:
        """SMTP + SMS + Push + mobil ayarları. SIRLAR MASKELİ (K8).

        Üç ayrı yerden gelir ve ekranda AYRI gösterilir:
          · Mağaza `core_config` — vitrini ve müşteriye giden postayı etkiler,
          · Kontrol Merkezi `notify` yeteneği — SMS katmanının kendi durumu,
          · Bu ekranın tercihi — sessiz saatler ve günlük limit.
        """
        discipline = await self._discipline()
        slug = str(self._config.get("email_config_slug") or "emails")
        out: dict[str, Any] = {
            "ok": True, "connected": True, "error": "",
            "local": {
                "quietStart": discipline["start"], "quietEnd": discipline["end"],
                "quietEnabled": discipline["enabled"], "dailyLimit": discipline["limit"],
                "sentToday": await self._sent_today(),
            },
            "quiet": messaging.quiet_state(messaging.now_hm(), start=discipline["start"],
                                           end=discipline["end"],
                                           enabled=discipline["enabled"]),
            "storeSlug": slug, "store": {}, "storeAvailable": True,
            "sms": {"available": self._notify is not None},
            "mobile": {}, "mobileAvailable": True,
        }

        try:
            payload = await self._api.configuration(slug, channel=self._channel,
                                                    locale=self._locale)
            values = payload.get("values") if isinstance(payload.get("values"), dict) else payload
            out["store"] = {
                key: messaging.config_entry(values, self._email_key(key))
                for key in messaging.EMAIL_CONFIG_KEYS
            }
        except Exception as failure:  # noqa: BLE001 — K7
            out["storeAvailable"] = False
            out["error"] = self._fail(failure)

        if self._notify is not None:
            try:
                out["sms"] = {"available": True, **await self._notify.ready()}
            except Exception as failure:  # noqa: BLE001 — K7
                out["sms"] = {"available": True, "error": self._fail(failure)}

        try:
            out["mobile"] = messaging.mobile_row(await self._api.bbd_mobile_settings())
        except Exception as failure:  # noqa: BLE001 — uç henüz yayında olmayabilir
            out["mobileAvailable"] = False
            out["mobileError"] = self._fail(failure)

        return out

    def _email_key(self, name: str) -> str:
        """SMTP anahtarının `core_config` yolu.

        Yol CANLIDA DOĞRULANDI (bkz. `messaging.EMAIL_CONFIG_KEYS`): bağlantı
        bilgisi `configure.smtp.*`, gönderen kimliği `configure.email_settings.*`
        altındadır. Tek grup varsayan eski yol gönderen adresini hiç bulamıyordu.

        Yol sürüme göre yine kayabilir; `config_entry` bulamazsa anahtarın SON
        parçasına göre arar ve yine bulamazsa "yok" der. Bulunmayan anahtara
        yazmak, hiçbir şeyi etkilemeyen bir satır açıp kullanıcıya ayarı
        değiştirdiğini düşündürürdü.
        """
        prefix = str(self._config.get("email_config_slug") or "emails")
        return f"{prefix}.{messaging.EMAIL_CONFIG_KEYS.get(name, name)}"

    async def save_channels(self, *, quiet_start: str | None = None,
                            quiet_end: str | None = None, quiet_enabled: bool | None = None,
                            daily_limit: int | None = None,
                            mobile: dict[str, Any] | None = None, reason: str, actor: str,
                            dry_run: bool = True) -> dict[str, Any]:
        """Yerel disiplin + mobil ayarlar. SMTP/SMS SIRLARI BU UÇTAN YAZILMAZ.

        Sır kasada durur (K8): ekrandan parola yazmak, onu istek gövdesine,
        log'a ve denetim izine sokardı. Ekran yalnız maskeli değeri gösterir ve
        nereden değiştirileceğini söyler.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        # BİÇİM DENETİMİ ÖNCE, YAZMA SONRA: bozuk bir bitiş saati yüzünden
        # yarısı yazılmış bir pencere bırakmak, kullanıcının kaydettiğini
        # sandığı ama uygulanmayan bir disiplin üretirdi.
        changed: list[str] = []
        writes: list[tuple[str, str, str]] = []
        for value, key, label in ((quiet_start, "quiet_start", "sessiz saat başlangıcı"),
                                  (quiet_end, "quiet_end", "sessiz saat bitişi")):
            if value is None:
                continue
            if not messaging.valid_hm(value):
                return {"ok": False, "error": f"{label.capitalize()} SS:DD biçiminde olmalı."}
            writes.append((key, messaging.text(value), label))

        if quiet_enabled is not None:
            writes.append(("quiet_enabled", "1" if quiet_enabled else "0", "sessiz saatler"))
        if daily_limit is not None:
            writes.append(("daily_limit", str(max(0, int(daily_limit))), "günlük limit"))

        try:
            for key, value, label in writes:
                await self._set_pref(key, value, actor)
                changed.append(label)
        except Exception as failure:  # noqa: BLE001 — yerel yazma; ekran ayakta kalmalı (K7)
            self._log.warning("tercih yazılamadı", error=str(failure))
            return {"ok": False, "error": f"Ayarlar kaydedilemedi: {failure}", "changed": changed}

        mobile_skipped = ""
        if mobile:
            try:
                await self._api.bbd_update_mobile_settings(
                    payload=mobile, reason=reason, actor=actor, dry_run=dry_run)
                changed.append("mobil uygulama ayarları")
            except Exception as failure:  # noqa: BLE001 — K7
                mobile_skipped = self._fail(failure)

        await self._record(action="save_channels", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"changed": changed, "mobileError": mobile_skipped})
        return {"ok": True, "error": "", "changed": changed, "mobileError": mobile_skipped,
                "dryRun": dry_run}

    async def test_connection(self, kind: str) -> dict[str, Any]:
        """[Bağlantıyı sına]. Gerçek bir mesaj GÖNDERMEZ.

        E-posta için SMTP'yi doğrudan sınayan bir mağaza ucu yok; burada
        yapılabilecek en dürüst şey mağazaya erişimi doğrulamak ve "SMTP'nin
        gerçekten çalıştığını yalnız test gönderimi gösterir" demektir.
        """
        if kind == "sms":
            if self._notify is None:
                return {"ok": True, "ready": False,
                        "message": "SMS katmanı bu kurulumda yok (notify yeteneği kapalı)."}
            try:
                state = await self._notify.ready()
            except Exception as failure:  # noqa: BLE001 — K7
                return {"ok": True, "ready": False, "message": self._fail(failure)}
            ready = bool(state.get("configured")) and bool(state.get("enabled"))
            note = state.get("error") or (
                f"Sağlayıcı {state.get('provider')} · başlık {state.get('header') or '—'}"
                + (" · KURU PROVA AÇIK: gerçek SMS gitmez." if state.get("dryRun") else ""))
            return {"ok": True, "ready": ready, "message": note, "state": state}

        try:
            health = await self._api.health()
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": True, "ready": False, "message": self._fail(failure)}
        if not health.get("ok"):
            return {"ok": True, "ready": False, "message": messaging.text(health.get("error"))}
        return {"ok": True, "ready": True,
                "message": f"Mağazaya erişildi ({health.get('elapsedMs')} ms). SMTP'nin "
                           "gerçekten çalıştığını yalnız [Kendime test gönder] gösterir.",
                "state": health}

    # ============================================================ abonelikler

    async def subscribers(self, *, q: str = "", subscribed: str = "", page: int = 1,
                          size: int = 0) -> dict[str, Any]:
        per_page = size or self._page_size
        filters: dict[str, Any] = {}
        if messaging.text(q):
            filters["email"] = messaging.text(q)
        if subscribed in ("0", "1"):
            filters["is_subscribed"] = int(subscribed)
        try:
            payload = await self._api.subscribers(filters, page=page, per_page=per_page)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("aboneler okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "items": [], "total": 0, "page": page, "size": per_page, "pages": 0}
        rows = [messaging.subscriber_row(item) for item in (payload.get("items") or [])
                if isinstance(item, dict)]
        meta = payload.get("meta") or {}
        return {
            "ok": True, "connected": True, "error": "", "items": rows,
            "total": messaging.as_int(meta.get("total"), len(rows)),
            "page": messaging.as_int(meta.get("currentPage"), page),
            "size": messaging.as_int(meta.get("perPage"), per_page),
            "pages": messaging.as_int(meta.get("lastPage"), 1),
        }

    # ========================================================= toplu gönderim

    async def broadcast_preview(self, *, channel: str, template: str = "", subject: str = "",
                                body: str = "", audience: str = "subscribers",
                                filters: dict[str, Any] | None = None,
                                actor: str = "") -> dict[str, Any]:
        """KURU PROVA. Alıcı sayısını ve tahmini maliyeti mağazadan sorar.

        Jeton üretilir; gerçek gönderim o jetonla gelir. Böylece uygulanan işin
        önizlenen iş olduğu kanıtlanır — arada süzgeç değişirse jeton eskisini
        taşır ve kullanıcı 1.800 yerine 12.000 kişiye göndermez.
        """
        # KİTLE DE KANALDAN ÖNCE DOĞRULANIR. Serbest metin kabul etmek, mağazanın
        # sessizce yok saydığı bir süzgeçle "herkese gönder" işini açık bırakırdı
        # (bkz. `messaging.audience_error`). Kapı burada, arayüzde değil (K9).
        problem = messaging.channel_error(channel) or messaging.audience_error(audience)
        if problem:
            return {"ok": False, "error": problem}
        if not messaging.text(body) and not messaging.text(template):
            return {"ok": False, "error": "Gönderilecek şablon ya da metin verilmedi."}

        payload = {"channel": channel, "audience": audience, "filters": filters or {},
                   "template_id": messaging.text(template), "subject": messaging.text(subject),
                   "body": body}
        try:
            result = await self._api.bbd_send_notification(
                payload=payload, reason="Toplu bildirim kuru provası — alıcı ve maliyet ölçümü.",
                actor=actor, dry_run=True)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("toplu bildirim provası yapılamadı", error=str(failure))
            return {"ok": False, "error": self._fail(failure)}

        recipients = messaging.as_int(result.get("recipients") or result.get("count"))
        if recipients <= 0:
            return {"ok": False,
                    "error": "Bu seçimde alıcı bulunamadı; süzgeci gevşetin. "
                             "Alıcısı olmayan gönderim başlatılmaz."}
        if recipients > self._recipient_cap:
            return {"ok": False,
                    "error": f"Seçim {recipients} alıcı; tek seferde en çok "
                             f"{self._recipient_cap} kabul edilir. Süzgeci daraltın."}

        price = messaging.as_int(self._config.get("sms_price_kurus"), 0)
        plan = messaging.sms_plan(body, recipients=recipients, price_kurus=price) \
            if channel == "sms" else None
        discipline = await self._discipline()
        quiet = messaging.quiet_state(messaging.now_hm(), start=discipline["start"],
                                      end=discipline["end"], enabled=discipline["enabled"])
        limit = messaging.limit_state(await self._sent_today(), discipline["limit"],
                                      wanted=recipients)

        token = uuid.uuid4().hex
        try:
            await self._store.execute(
                f"INSERT INTO {self._sends} "
                "(token, channel, template, audience, params, recipients, parts, cost, "
                "status, actor, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'preview', ?, '', ?)",
                (token, channel, messaging.text(template), audience,
                 json.dumps(payload, ensure_ascii=False), recipients,
                 (plan or {}).get("credits", 0), (plan or {}).get("cost") or 0,
                 actor, _now()))
        except Exception as failure:  # noqa: BLE001 — jeton yazılamazsa gönderim açılmaz
            self._log.warning("toplu gönderim önizlemesi kaydedilemedi", error=str(failure))
            return {"ok": False, "error": "Önizleme kaydedilemedi; gönderim açılmadı."}

        preview = self.preview_template(channel=channel, subject=subject, body=body,
                                        recipients=recipients)
        return {
            "ok": True, "error": "", "token": token, "recipients": recipients,
            "channel": channel, "plan": plan, "quiet": quiet, "limit": limit,
            "subject": preview["subject"], "body": preview["body"],
            "missing": preview["missing"],
            "blocked": bool(quiet["active"] or limit["error"]),
            "note": quiet["message"] or limit["error"] or "",
        }

    async def broadcast_send(self, *, token: str, reason: str, actor: str,
                             dry_run: bool = True) -> dict[str, Any]:
        """Önizlenen toplu gönderimi uygular. GERİ ALINAMAZ."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        try:
            row = await self._store.fetch_one(
                f"SELECT token, channel, params, recipients, status FROM {self._sends} "
                "WHERE token = ?", (messaging.text(token),))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        if not row:
            return {"ok": False,
                    "error": "Önizleme bulunamadı. Alıcı sayısı görülmeden toplu bildirim "
                             "gönderilmez; kuru provayı yeniden alın."}
        if row["status"] != "preview":
            return {"ok": False, "error": "Bu önizleme zaten kullanıldı."}

        recipients = messaging.as_int(row["recipients"])
        discipline = await self._discipline()
        quiet = messaging.quiet_state(messaging.now_hm(), start=discipline["start"],
                                      end=discipline["end"], enabled=discipline["enabled"])
        if quiet["active"] and not dry_run:
            return {"ok": False,
                    "error": f"Sessiz saatler açık ({quiet['window']}). {recipients} kişiye "
                             "şu an bildirim gitmez; pencereyi kapatın ya da bekleyin."}
        limit = messaging.limit_state(await self._sent_today(), discipline["limit"],
                                      wanted=recipients)
        if limit["error"] and not dry_run:
            return {"ok": False, "error": limit["error"]}

        try:
            payload = json.loads(row["params"])
        except (TypeError, ValueError) as failure:
            # Önizlenen iş okunamıyorsa GÖNDERİM AÇILMAZ. Jetonun tek işi
            # "uygulanan iş, önizlenen iştir" demektir; gövdesi bozuksa o
            # kanıt yoktur ve 1.800 kişiye ne gideceği bilinmiyordur.
            self._log.warning("önizlenen gönderim gövdesi çözülemedi",
                              token=row["token"], error=str(failure))
            return {"ok": False,
                    "error": "Önizlenen gönderim okunamadı; kuru provayı yeniden alın."}

        await self._record(action="broadcast", reason=reason, actor=actor, result="denendi",
                           detail={"token": row["token"], "recipients": recipients})
        try:
            result = await self._api.bbd_send_notification(
                payload=payload, reason=reason, actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="broadcast", reason=reason, actor=actor, result="hata",
                               detail={"error": str(failure), "token": row["token"]})
            return {"ok": False, "error": self._fail(failure)}

        status = "dry_run" if dry_run else "sent"
        try:
            await self._store.execute(
                f"UPDATE {self._sends} SET status = ?, actor = ?, reason = ?, applied_at = ? "
                "WHERE token = ?", (status, actor, reason, _now(), row["token"]))
        except Exception as failure:  # noqa: BLE001 — GÖNDERİM ZATEN YAPILDI
            # Jeton "kullanıldı" diye işaretlenemedi. İstisnayı dışarı bırakmak
            # 500 üretir ve kullanıcı gönderimin gitmediğini sanıp jetonu
            # yeniden kullanmayı dener — 1.800 kişiye İKİNCİ kez gider. Bu
            # yüzden sonuç başarıyla döner, uyarı ayrıca söylenir.
            self._log.warning("gönderim jetonu kapatılamadı", token=row["token"],
                              error=str(failure))
            await self._record(action="broadcast", reason=reason, actor=actor,
                               result="jeton_kapatilamadi",
                               detail={"token": row["token"], "error": str(failure)})
        await self._record(action="broadcast", reason=reason, actor=actor, result=status,
                           detail={"token": row["token"], "recipients": recipients,
                                   "channel": row["channel"]})
        if not dry_run:
            await self._emit("store.notification.sent",
                             {"channel": row["channel"], "recipients": recipients,
                              "actor": actor, "reason": reason})
        return {"ok": True, "error": "", "recipients": recipients, "dryRun": dry_run,
                "jobId": messaging.text(result.get("jobId") or result.get("requestId"))}

    async def send_direct(self, *, template: str, recipients: list[str], channel: str = "email",
                          values: dict[str, Any] | None = None, reason: str, actor: str = "",
                          dry_run: bool = True) -> dict[str, Any]:
        """`store.notify.send` yeteneğinin gövdesi — başka modüllerin girişi.

        Buradan geçen çağrı da sessiz saat ve günlük limit kapılarından geçer:
        "Siparişler ekranından gönderilen" bir bildirim gece 03:00'te müşteriyi
        uyandırmamalı.
        """
        problem = self._guard(reason) or messaging.channel_error(channel)
        if problem:
            return {"ok": False, "error": problem}
        people = [messaging.text(item) for item in (recipients or []) if messaging.text(item)]
        if not people:
            return {"ok": False, "error": "Alıcı verilmedi."}
        if len(people) > self._recipient_cap:
            return {"ok": False,
                    "error": f"Tek çağrıda en çok {self._recipient_cap} alıcı kabul edilir."}

        source, number = messaging.split_template_id(template)
        if not source:
            return {"ok": False, "error": "Şablon kimliği çözülemedi (örn. 'store:12')."}

        discipline = await self._discipline()
        quiet = messaging.quiet_state(messaging.now_hm(), start=discipline["start"],
                                      end=discipline["end"], enabled=discipline["enabled"])
        if quiet["active"] and not dry_run:
            return {"ok": False, "error": quiet["message"], "quiet": quiet}
        limit = messaging.limit_state(await self._sent_today(), discipline["limit"],
                                      wanted=len(people))
        if limit["error"] and not dry_run:
            return {"ok": False, "error": limit["error"]}

        payload = {"channel": channel, "to": people, "template_id": template,
                   "values": values or {}}
        try:
            result = await self._api.bbd_send_notification(
                payload=payload, reason=reason, actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="send_direct", reason=reason, actor=actor, result="hata",
                               detail={"error": str(failure), "template": template})
            return {"ok": False, "error": self._fail(failure)}

        if not dry_run:
            try:
                await self._store.execute(
                    f"INSERT INTO {self._sends} "
                    "(token, channel, template, audience, params, recipients, parts, cost, "
                    "status, actor, reason, created_at, applied_at) "
                    "VALUES (?, ?, ?, 'direct', ?, ?, 0, 0, 'sent', ?, ?, ?, ?)",
                    (uuid.uuid4().hex, channel, template,
                     json.dumps({"count": len(people)}, ensure_ascii=False), len(people),
                     actor, reason, _now(), _now()))
            except Exception as failure:  # noqa: BLE001 — sayaç yazılamadı, iş durmasın
                self._log.warning("gönderim sayacı yazılamadı", error=str(failure))

        await self._record(action="send_direct", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"template": template, "channel": channel,
                                   "recipients": len(people), "source": source, "id": number})
        return {"ok": True, "error": "", "recipients": len(people), "dryRun": dry_run,
                "sent": bool(result.get("sent", not dry_run))}

    # ================================================================ denetim

    async def audit(self, *, limit: int = 50) -> dict[str, Any]:
        """Bu ekrandan yapılan yazmaların YEREL izi (gerekçeleriyle)."""
        try:
            rows = await self._store.fetch_all(
                f"SELECT action, reason, actor, result, created_at FROM {self._audit} "
                "ORDER BY id DESC LIMIT ?", (max(1, min(500, int(limit))),))
        except Exception as failure:  # noqa: BLE001 — iz okunamadı, ekran dursun
            return {"ok": True, "items": [], "error": self._fail(failure)}
        return {"ok": True, "error": "", "items": [
            {"action": row["action"], "reason": row["reason"], "actor": row["actor"],
             "result": row["result"], "createdAt": row["created_at"]} for row in rows]}

    # ================================================================== rapor

    async def _scan(self, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
        """Rapor için geçmiş taraması — SIRAYLA ve tavanlı.

        Paralel sayfa çekmek geçidin dakikada 55 isteklik kovasını bir anda
        tüketiyor; sıralı tarama yavaş ama başka araçların payını yemiyor.
        """
        rows: list[dict[str, Any]] = []
        truncated = False
        page = 1
        while page <= REPORT_PAGE_CAP:
            payload = await self._api.bbd_notifications(filters, page=page,
                                                        per_page=REPORT_PAGE_SIZE)
            batch = payload.get("items") or []
            rows.extend(messaging.history_row(item) for item in batch if isinstance(item, dict))
            meta = payload.get("meta") or {}
            last = messaging.as_int(meta.get("lastPage"), page)
            if len(rows) >= REPORT_ROW_CAP:
                truncated = True
                rows = rows[:REPORT_ROW_CAP]
                break
            if not batch or page >= last:
                break
            page += 1
        else:
            truncated = True
        return rows, truncated

    async def export_csv(self, *, channel: str = "", status: str = "", date_from: str = "",
                         date_to: str = "") -> dict[str, Any]:
        """Gönderim raporu CSV — rapor klasörüne yazılır.

        SÜZGEÇ HAM GEÇİRİLMEZ. Bu uç eskiden route'un kurduğu sözlüğü olduğu
        gibi mağazaya veriyordu; liste ucu (`_history_filters`) ise aynı
        değerleri tanınan kümeye karşı süzüyordu. Fark sessiz ve tehlikeliydi:
        Laravel tanımadığı parametreyi YOK SAYAR, dolayısıyla `channel=uydurma`
        ile alınan CSV **bütün kanalları** içerir ama kullanıcı onu süzülmüş
        sanır. Artık dışa aktarım listeyle AYNI kapıdan geçer.
        """
        filters = self._history_filters(
            q="", channel=channel, template="", status=status, event="",
            date_from=date_from, date_to=date_to, cost_min=None, cost_max=None,
            failed_only=False)
        try:
            rows, truncated = await self._scan(filters)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}

        headers = ["Zaman", "Kanal", "Şablon", "Alıcı", "Özet", "Durum", "Deneme", "Parça",
                   "Maliyet", "Hata"]
        table = [[row["createdAt"], row["channelLabel"], row["template"], row["recipient"],
                  row["summary"], row["statusLabel"], row["attempts"], row["parts"],
                  money(row["cost"]) if row["cost"] is not None else "",
                  row["error"]] for row in rows]

        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        name = f"magaza-bildirim-gonderim-{stamp}.csv"
        try:
            path = write_private(self._export_dir / name, csv_bytes(headers, table))
        except OSError as failure:
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
        if kind not in ("delivery", "sms_cost"):
            return {"ok": False, "error": f"Bilinmeyen rapor: {kind}"}

        # Kanıtlanamayan kanal süzgeci gönderilmez (bkz. `_history_filters`).
        filters: dict[str, Any] = {}
        if params.get("dateFrom"):
            filters["date_from"] = messaging.text(params["dateFrom"])[:10]
        if params.get("dateTo"):
            filters["date_to"] = messaging.text(params["dateTo"])[:10]
        if kind == "sms_cost":
            filters["channel"] = "sms"
        elif params.get("channel") in messaging.CHANNELS:
            filters["channel"] = params["channel"]

        try:
            rows, truncated = await self._scan(filters)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        if not rows:
            return {"ok": False, "error": "Bu süzgeçte rapora girecek gönderim yok."}

        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        if kind == "delivery":
            content = self._delivery_pdf(rows, truncated=truncated)
            name = f"magaza-bildirim-raporu-{stamp}.pdf"
        else:
            content = self._cost_pdf(rows, truncated=truncated)
            name = f"magaza-sms-maliyet-{stamp}.pdf"

        try:
            path = write_private(self._export_dir / name, content)
        except (OSError, ExportError) as failure:
            return {"ok": False, "error": str(failure)}
        self._log.info("bildirim raporu üretildi", kind=kind, rows=len(rows), path=str(path))
        return {"ok": True, "error": "", "path": str(path), "name": name,
                "bytes": len(content), "rows": len(rows), "truncated": truncated}

    def _delivery_pdf(self, rows: list[dict[str, Any]], *, truncated: bool) -> bytes:
        summary = messaging.history_summary(rows)
        sections: list[dict[str, Any]] = [{
            "kind": "tiles", "title": "Özet",
            "tiles": [("Gönderim", number(summary["total"])),
                      ("İletildi", number(summary["delivered"])),
                      ("Başarısız", number(summary["failed"])),
                      ("Kuyrukta", number(summary["queued"])),
                      ("Maliyet", money(summary["cost"]) if summary["cost"] is not None else "—")],
        }, {
            "kind": "table", "title": "Kanal kırılımı",
            "headers": ["Kanal", "Adet", "Parça", "Maliyet", "Bilinmeyen"],
            "align": "LRRRR", "widths": [2, 1, 1, 1.2, 1],
            "rows": [[item["key"], number(item["count"]), number(item["parts"]),
                      money(item["cost"]) if item["cost"] else "—", number(item["unknown"])]
                     for item in messaging.cost_by_key(rows, "channelLabel")],
        }]
        failed = [row for row in rows if row["failed"]]
        if failed:
            sections.append({
                "kind": "table", "title": "Başarısız gönderimler",
                "headers": ["Zaman", "Kanal", "Alıcı", "Şablon", "Hata"],
                "align": "LLLLL", "widths": [1.4, 0.8, 1.6, 1.6, 2.4],
                "rows": [[row["createdAt"], row["channelLabel"], row["recipient"],
                          row["template"], row["error"] or row["statusLabel"]]
                         for row in failed[:300]],
            })
        if truncated:
            sections.append({"kind": "note",
                             "text": "Geçmiş tavana dayandı; rapor en yeni kayıtlarla sınırlı."})
        if summary["costUnknown"]:
            sections.append({"kind": "note",
                             "text": f"{summary['costUnknown']} satırın maliyeti mağazadan "
                                     "gelmedi ve toplama girmedi."})
        return build_pdf(title="Bildirim gönderim raporu",
                         subtitle=f"{summary['total']} gönderim",
                         sections=sections, footer="Kontrol Merkezi · Mağaza")

    def _cost_pdf(self, rows: list[dict[str, Any]], *, truncated: bool) -> bytes:
        summary = messaging.history_summary(rows)
        by_template = messaging.cost_by_key(rows, "template")
        by_day = sorted(messaging.cost_by_key(
            [{**row, "day": row["createdAt"][:10]} for row in rows], "day"),
            key=lambda item: item["key"])
        sections: list[dict[str, Any]] = [{
            "kind": "tiles", "title": "SMS icmali",
            "tiles": [("Mesaj", number(summary["total"])),
                      ("Parça (kredi)", number(summary["parts"])),
                      ("Tutar", money(summary["cost"]) if summary["cost"] is not None else "—"),
                      ("Başarısız", number(summary["failed"]))],
        }, {
            "kind": "table", "title": "Şablona göre",
            "headers": ["Şablon", "Mesaj", "Parça", "Tutar"],
            "align": "LRRR", "widths": [3, 1, 1, 1.4],
            "rows": [[item["key"], number(item["count"]), number(item["parts"]),
                      money(item["cost"]) if item["cost"] else "—"]
                     for item in by_template[:120]],
        }, {
            "kind": "table", "title": "Güne göre",
            "headers": ["Gün", "Mesaj", "Parça", "Tutar"],
            "align": "LRRR", "widths": [2, 1, 1, 1.4],
            "rows": [[item["key"], number(item["count"]), number(item["parts"]),
                      money(item["cost"]) if item["cost"] else "—"] for item in by_day[:120]],
        }]
        if summary["costUnknown"]:
            sections.append({
                "kind": "note",
                "text": f"{summary['costUnknown']} mesajın birim maliyeti bilinmiyor. Birim "
                        "fiyat modül ayarındaki `sms_price_kurus` ile girilir; girilmeden "
                        "tutar UYDURULMAZ.",
            })
        if truncated:
            sections.append({"kind": "note", "text": "Geçmiş tavana dayandı; icmal eksik olabilir."})
        return build_pdf(title="SMS maliyet icmali",
                         subtitle=f"{summary['total']} mesaj · {number(summary['parts'])} parça",
                         sections=sections, footer="Kontrol Merkezi · Mağaza")

    async def _render_pages(self, path: Path, *, max_pages: int = 12,
                            dpi: int = 110) -> list[str]:
        binary = shutil.which("pdftoppm")
        if not binary:
            raise PreviewError(
                "Önizleme üretilemedi: `pdftoppm` yok (poppler-utils kurulmalı). "
                "Rapor yine de kaydedildi ve yazdırılabilir.")
        with tempfile.TemporaryDirectory(prefix="km-bildirim-onizleme-") as folder:
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
