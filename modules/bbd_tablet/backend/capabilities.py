"""`bbd_tablet.read` ve `bbd_tablet.control` yetenekleri (mobil geçit sözleşmesi §4).

Başka modüller (ör. mobil geçit) tabletleri bu yeteneklerden görür; tabloları
okumaz (K5), servisi import etmez (K3). Her şey VAR OLAN servis
fonksiyonlarından geçer: doğrulama, oturum kapatma ve kayıt kuralları
yönetim panelininkiyle aynıdır.

    read.board()                                  → §4 GET /tablets biçimi
    read.student_usage(student_id, date_from, date_to) → §4 tablet-usage biçimi
    control.grant(student_id, package_name, extra_seconds, reason, actor) → grant_id
    control.block(student_id, until, reason, actor)  → blocked_until | None
    control.unblock(student_id, reason, actor)       → None
    control.end_session(session_id, reason, actor)   → bool

Hepsi `async`tır. Hata `TabletCapabilityError` ile gelir ve makine kodu taşır
(`reason`): `validation_error` · `student_not_found` · `session_not_found` ·
`canteen_unavailable`. Çağıran modül sınıfı import edemez (K3); kodu
`getattr(hata, "reason", None)` ile okur.

**Kimlikler KM'nin kendi kimlikleridir** (UUID metni). Sözleşme örneğindeki
tam sayılar örnektir; geçit olduğu gibi taşır.

**Zamanlar** `+03:00` ofsetli ISO'dur (Europe/Istanbul); KM UTC saklar.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .rules import inside_usage_window
from .service import (
    CanteenUnavailableError,
    NotFoundError,
    TabletService,
    parse_time,
    zone,
)

_ISTANBUL = ZoneInfo("Europe/Istanbul")

#: Kullanım raporunun en uzun aralığı (gün, iki uç dahil).
MAX_USAGE_DAYS = 62

#: Gerekçe sınırları (sözleşme §4: zorunlu, 3–200 karakter).
REASON_MIN, REASON_MAX = 3, 200

#: `until` verilmeyen kısıtlama "aksi söylenene kadar"dır; servis en çok bir
#: yıl kabul eder, o yüzden bir yıl yazılır.
OPEN_BLOCK = timedelta(days=365)


class TabletCapabilityError(Exception):
    """Yetenek çağrısı reddedildi. `reason` makine içindir, mesaj Türkçe."""

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


def _iso(value: Any) -> str | None:
    moment = parse_time(value) if not isinstance(value, datetime) else value
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(_ISTANBUL).isoformat(timespec="seconds")


def _reason(text: Any) -> str:
    clean = " ".join(str(text or "").split())
    if not REASON_MIN <= len(clean) <= REASON_MAX:
        raise TabletCapabilityError(
            f"Gerekçe {REASON_MIN}–{REASON_MAX} karakter olmalı.", reason="validation_error"
        )
    return clean


def _actor(actor: Any) -> str:
    return " ".join(str(actor or "").split())[:160] or "bilinmeyen"


def _day(value: Any, name: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise TabletCapabilityError(f"{name} YYYY-AA-GG olmalı.",
                                    reason="validation_error") from exc


class TabletRead:
    """`bbd_tablet.read` — tablet panosu ve öğrenci kullanımı."""

    def __init__(self, service: TabletService) -> None:
        self._service = service

    async def board(self) -> dict[str, Any]:
        """§4 `GET /tablets`.

        `state` önceliği: revoked > pending > offline > in_use > locked > free.
        `locked` tabletin kendisi kilitliyse, giriş kilidi sürüyorsa, profili
        yoksa ya da profilin kullanım saatleri dışındaysa yazılır; hangisi
        olduğu `reason` alanındadır (`device_locked` · `login_locked` ·
        `no_profile` · `outside_hours`). `free` sözleşmenin tanımıdır: eşli,
        çevrimiçi, kilitsiz, profili var, aktif oturumu yok, giriş kilidi yok
        ve kullanım saatleri içinde.
        """
        overview = await self._service.overview()
        current = self._service._now()
        profiles = {profile["id"]: profile for profile in overview["profiles"]}
        sessions = {row["device_id"]: row for row in overview["sessions"]}

        devices: list[dict[str, Any]] = []
        summary = {"free": 0, "inUse": 0, "offline": 0, "locked": 0}
        for device in overview["devices"]:
            status = device["status"]
            session = sessions.get(device["id"])
            profile = profiles.get(device.get("profile_id") or "")
            reason: str | None = None
            if status in ("revoked", "pending", "offline"):
                state = status
            elif session is not None:
                state = "in_use"
            elif device.get("locked"):
                state, reason = "locked", "device_locked"
            elif device.get("login_locked_until"):
                state, reason = "locked", "login_locked"
            elif profile is None:
                state, reason = "locked", "no_profile"
            elif not inside_usage_window(
                profile.get("usageWindows") or [],
                current.astimezone(zone(profile.get("timezone") or "Europe/Istanbul")),
            ):
                state, reason = "locked", "outside_hours"
            else:
                state = "free"

            if state == "in_use":
                summary["inUse"] += 1
            elif state in ("free", "offline", "locked"):
                summary[state] += 1

            devices.append({
                "id": device["id"],
                "name": device["name"],
                "state": state,
                "reason": reason,
                "battery": device.get("battery_percent"),
                "charging": device.get("charging"),
                "lastSeenAt": _iso(device.get("last_seen_at")),
                "activeSession": None if session is None else {
                    "id": session["id"],
                    "studentId": session["student_id"],
                    "studentName": session.get("student_name") or "",
                    "loginAt": _iso(session.get("login_at")),
                },
            })
        return {"summary": summary, "devices": devices}

    async def student_usage(self, student_id: str, date_from: Any,
                            date_to: Any) -> dict[str, Any]:
        """§4 `tablet-usage`: gün gün uygulama kırılımı, oturumlar, aktif oturum, kısıt.

        Aralık iki uç dahil en çok 62 gündür. Her gün listede bulunur (kullanım
        yoksa `totalSeconds: 0`). `label` uygulama adı bilinmiyorsa `None`.
        """
        start, end = _day(date_from, "from"), _day(date_to, "to")
        if end < start:
            raise TabletCapabilityError("Bitiş başlangıçtan önce olamaz.",
                                        reason="validation_error")
        span = (end - start).days + 1
        if span > MAX_USAGE_DAYS:
            raise TabletCapabilityError(f"Aralık en çok {MAX_USAGE_DAYS} gün olabilir.",
                                        reason="validation_error")
        service = self._service
        days = [(start + timedelta(days=offset)).isoformat() for offset in range(span)]
        rows = await service._usage_rows(student_id, days)
        labels = await service._app_names({row["packageName"] for row in rows})

        by_day: dict[str, list[dict[str, Any]]] = {day: [] for day in days}
        for row in rows:
            seconds = int(row["usedSeconds"])
            if seconds <= 0:
                continue
            by_day.setdefault(row["localDate"], []).append({
                "package": row["packageName"],
                "label": labels.get(row["packageName"]) or None,
                "seconds": seconds,
            })

        timezone = zone(service.default_timezone)
        sessions: list[dict[str, Any]] = []
        active: dict[str, Any] | None = None
        for row in await service.list_sessions(limit=500, student_id=student_id):
            login = parse_time(row["loginAt"])
            if row["state"] == "active" and active is None:
                active = {"id": row["id"], "deviceName": row["deviceName"],
                          "loginAt": _iso(row["loginAt"])}
            if login is None:
                continue
            if not start <= login.astimezone(timezone).date() <= end:
                continue
            ended = row["state"] != "active"
            sessions.append({
                "id": row["id"],
                "deviceName": row["deviceName"],
                "loginAt": _iso(row["loginAt"]),
                "endedAt": _iso(row["endedAt"]) if ended else None,
                "endReason": row["endReason"] if ended else None,
            })

        return {
            "days": [
                {"date": day,
                 "totalSeconds": sum(item["seconds"] for item in apps),
                 "apps": sorted(apps, key=lambda item: (-item["seconds"], item["package"]))}
                for day, apps in by_day.items()
            ],
            "sessions": sessions,
            "activeSession": active,
            "blockedUntil": _iso(await service._active_block_until(student_id, service._now())),
        }


class TabletControl:
    """`bbd_tablet.control` — ek süre, kısıtlama, oturum kapatma."""

    def __init__(self, service: TabletService, log: Any) -> None:
        self._service = service
        self._log = log

    async def _student(self, student_id: str) -> None:
        try:
            await self._service._require_student(str(student_id))
        except CanteenUnavailableError as exc:
            raise TabletCapabilityError(str(exc), reason="canteen_unavailable") from exc
        except ValueError as exc:
            raise TabletCapabilityError(str(exc), reason="student_not_found") from exc

    async def grant(self, student_id: str, package_name: str | None, extra_seconds: int,
                    reason: str, actor: str) -> str:
        """Bugün (Europe/Istanbul) için uygulamaya ek süre; ek sürenin kimliği döner.

        KM'de ek süre UYGULAMA BAZINDADIR (profilde süre sınırı uygulama
        başınadır, tablet geneli bir sınır yoktur). `package_name=None`
        bu yüzden `validation_error` ile reddedilir.
        """
        why = _reason(reason)
        package = str(package_name or "").strip()
        if not package:
            raise TabletCapabilityError(
                "Ek süre uygulama bazındadır; uygulama (packageName) seçilmeli.",
                reason="validation_error",
            )
        if isinstance(extra_seconds, bool) or not isinstance(extra_seconds, int) \
                or not 0 < extra_seconds <= 86400:
            raise TabletCapabilityError("Ek süre 1 saniye ile 24 saat arasında olmalı.",
                                        reason="validation_error")
        await self._student(student_id)
        local_date = self._service._now().astimezone(
            zone(self._service.default_timezone)).date().isoformat()
        try:
            granted = await self._service.grant_time(
                student_id=str(student_id), package_name=package, local_date=local_date,
                extra_seconds=extra_seconds, reason=why, actor=_actor(actor),
            )
        except CanteenUnavailableError as exc:
            raise TabletCapabilityError(str(exc), reason="canteen_unavailable") from exc
        except ValueError as exc:
            raise TabletCapabilityError(str(exc), reason="validation_error") from exc
        self._log.info("tablet ek süre (yetenek)", student=student_id, package=package,
                       seconds=extra_seconds, actor=_actor(actor))
        return str(granted["id"])

    async def block(self, student_id: str, until: datetime | str | None, reason: str,
                    actor: str) -> str | None:
        """Öğrenciyi tabletten kısıtlar; açık oturumu `blocked` ile kapanır.

        `until=None` "aksi söylenene kadar"dır — servis sınırı olan bir yıl
        yazılır. Dönüş, öğrencinin süren en geç kısıt bitişidir.
        """
        why = _reason(reason)
        current = self._service._now()
        if until is None:
            ends = current + OPEN_BLOCK
        elif isinstance(until, datetime):
            ends = until
        else:
            parsed = parse_time(until)
            if parsed is None:
                raise TabletCapabilityError("Kısıt bitişi ISO zaman olmalı.",
                                            reason="validation_error")
            ends = parsed
        await self._student(student_id)
        try:
            await self._service.block_student(student_id=str(student_id), until=ends,
                                              reason=why, actor=_actor(actor))
        except CanteenUnavailableError as exc:
            raise TabletCapabilityError(str(exc), reason="canteen_unavailable") from exc
        except ValueError as exc:
            raise TabletCapabilityError(str(exc), reason="validation_error") from exc
        self._log.info("tablet kısıtı (yetenek)", student=student_id, actor=_actor(actor),
                       reason=why)
        return _iso(await self._service._active_block_until(str(student_id),
                                                             self._service._now()))

    async def unblock(self, student_id: str, reason: str, actor: str) -> None:
        """Süren kısıtları kaldırır. Kısıt yoksa da sessizce başarılıdır."""
        why = _reason(reason)
        result = await self._service.unblock_student(student_id=str(student_id))
        self._log.info("tablet kısıtı kaldırıldı (yetenek)", student=student_id,
                       lifted=result.get("lifted"), actor=_actor(actor), reason=why)

    async def end_session(self, session_id: str, reason: str, actor: str) -> bool:
        """Oturumu `admin` nedeniyle kapatır. Zaten kapalıysa `False`."""
        why = _reason(reason)
        try:
            result = await self._service.end_session(session_id=str(session_id), message=why)
        except NotFoundError as exc:
            raise TabletCapabilityError(str(exc), reason="session_not_found") from exc
        self._log.info("tablet oturumu kapatıldı (yetenek)", session=session_id,
                       ended=result.get("ended"), actor=_actor(actor))
        return bool(result.get("ended"))
