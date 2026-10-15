"""`bbd_tablet.read` ve `bbd_tablet.control` yetenekleri (mobil geçit sözleşmesi §4).

Başka modüller (ör. mobil geçit) tabletleri bu yeteneklerden görür; tabloları
okumaz (K5), servisi import etmez (K3). Her şey VAR OLAN servis
fonksiyonlarından geçer: doğrulama, oturum kapatma ve kayıt kuralları
yönetim panelininkiyle aynıdır.

    read.board()                                  → §4 GET /tablets biçimi
    read.student_usage(student_id, date_from, date_to) → §4 tablet-usage biçimi
    read.daily_usage(local_date)                  → §4 GET /tablets/usage biçimi (kurum geneli)
    control.grant(student_id, package_name|None, extra_seconds, reason, actor) → grant_id
    control.block(student_id, until, reason, actor)  → blocked_until | None
    control.unblock(student_id, reason, actor)       → None
    control.end_session(session_id, reason, actor)   → bool
    read.student_limits(student_id)               → öğrencinin bugünkü sınırları (tablet-sinir)
    control.update_profile_limits(profile_id, expected_revision, apps, usage_windows, reason, actor)
                                                  → yeni profil revizyonu (tablet-sinir)
    control.revoke_grant(student_id, grant_id, reason, actor) → {id, revoked, alreadyRevoked}

Hepsi `async`tır. Hata `TabletCapabilityError` ile gelir ve makine kodu taşır
(`reason`): `validation_error` · `student_not_found` · `session_not_found` ·
`canteen_unavailable` · `profile_not_found` · `grant_not_found` · `profile_conflict`. Çağıran modül sınıfı import edemez (K3); kodu
`getattr(hata, "reason", None)` ile okur.

**Kimlikler KM'nin kendi kimlikleridir** (UUID metni). Sözleşme örneğindeki
tam sayılar örnektir; geçit olduğu gibi taşır.

**Zamanlar** `+03:00` ofsetli ISO'dur (Europe/Istanbul); KM UTC saklar.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .rules import (
    MAX_DAILY_LIMIT_SECONDS,
    MIN_DAILY_LIMIT_SECONDS,
    inside_usage_window,
    normalize_usage_windows,
)
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

#: Uygulama kipleri (Akademi "Tablet sınırları" ekranı): yasak · sınırsız · günlük süreli.
APP_MODES = ("blocked", "unlimited", "limited")

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

    async def daily_usage(self, local_date: Any) -> dict[str, Any]:
        """§4 `GET /tablets/usage?date=`: kurum geneli, o günün öğrenci başına süresi.

        `{date, items: [{studentId, totalSeconds, blockedUntil}]}` — yalnız o gün
        kullanımı olan ya da kısıtı ŞU AN süren öğrenciler (kısıt o günün değil,
        çağrı anının durumudur; `student_usage` ile aynı anlam). Gün bugünden
        sonra ya da `MAX_USAGE_DAYS` günden eski olamaz (`validation_error`).
        """
        day = _day(local_date, "date")
        service = self._service
        today = service._now().astimezone(zone(service.default_timezone)).date()
        if day > today:
            raise TabletCapabilityError("Gelecek bir gün sorulamaz.", reason="validation_error")
        if (today - day).days >= MAX_USAGE_DAYS:
            raise TabletCapabilityError(f"En çok {MAX_USAGE_DAYS} gün geriye bakılabilir.",
                                        reason="validation_error")
        items = await service.daily_usage_totals(day.isoformat())
        for item in items:
            item["blockedUntil"] = _iso(item["blockedUntil"])
        return {"date": day.isoformat(), "items": items}

    async def student_limits(self, student_id: str) -> dict[str, Any]:
        """Öğrencinin BUGÜNKÜ tablet sınırları (Akademi "Tablet sınırları" ekranı).

        KM'de süre sınırı öğrenciye değil PROFİLE bağlıdır: profil tablete atanır, o
        tablette giriş yapan her öğrenci aynı kuralları görür. Öğrencinin profili son
        oturumunun (sürüyorsa açık oturumun) profilidir; hiç giriş yapmadıysa `None`.
        Öğrenciye özel olanlar o günün ek süreleri ve kısıtlamadır.

        `apps` profilin kuralları (sırasıyla) + profilde olmayıp bugün kullanılan
        uygulamalar (`inProfile: false`). `remainingSeconds` yalnız süreli uygulamada
        doludur (sınır + ek süre − kullanım, en az 0). `sync` değişikliğin tablete en geç
        ne kadar sürede indiğini söyler (oturum açıkken / boştayken sync aralığı).
        """
        service = self._service
        timezone = zone(service.default_timezone)
        local_date = service._now().astimezone(timezone).date().isoformat()
        last = await service.store.fetch_one(
            f"SELECT profile_id FROM {service.sessions} WHERE student_id=? "
            "ORDER BY login_at DESC LIMIT 1", (str(student_id),),
        )
        profile = await service.profile(last["profile_id"]) if last else None
        used = {row["packageName"]: row["usedSeconds"]
                for row in await service._usage_rows(str(student_id), [local_date])}
        grants = await service._grant_items(str(student_id), local_date)
        extra: dict[str, int] = {}
        for grant in grants:
            extra[grant["packageName"]] = extra.get(grant["packageName"], 0) + grant["extraSeconds"]
        rules = list((profile or {}).get("apps") or [])
        known = {app["packageName"] for app in rules}
        names = await service._app_names(set(used) | set(extra) | known)

        def item(package: str, rule: dict[str, Any] | None) -> dict[str, Any]:
            allowed = bool(rule and rule["allowed"])
            unlimited = bool(rule and rule["unlimited"])
            mode = "blocked" if not allowed else ("unlimited" if unlimited else "limited")
            limit = int(rule["dailyLimitSeconds"]) if rule and mode == "limited" else None
            spent, bonus = int(used.get(package, 0)), int(extra.get(package, 0))
            label = (rule or {}).get("appName") or names.get(package) or package
            return {
                "packageName": package, "appName": label, "mode": mode,
                "dailyLimitSeconds": limit, "usedSeconds": spent, "extraSeconds": bonus,
                "remainingSeconds": None if limit is None else max(0, limit + bonus - spent),
                "inProfile": rule is not None,
            }

        apps = [item(rule["packageName"], rule) for rule in rules]
        apps += [item(package, None)
                 for package in sorted(set(used) - known, key=lambda name: -used[name])
                 if used[package] > 0]
        devices = 0
        if profile is not None:
            row = await service.store.fetch_one(
                f"SELECT COUNT(*) AS n FROM {service.devices} "
                "WHERE profile_id=? AND revoked_at IS NULL", (profile["id"],),
            )
            devices = int((row or {}).get("n") or 0)
        return {
            "studentId": str(student_id),
            "localDate": local_date,
            "profile": None if profile is None else {
                "id": profile["id"], "name": profile["name"],
                "revision": int(profile["revision"]), "timezone": profile["timezone"],
                "updatedAt": _iso(profile.get("updatedAt")),
                "usageWindows": [dict(window) for window in profile.get("usageWindows") or []],
                "deviceCount": devices,
            },
            "apps": apps,
            "grants": [{
                "id": grant["id"], "packageName": grant["packageName"],
                "appName": names.get(grant["packageName"]) or grant["packageName"],
                "extraSeconds": int(grant["extraSeconds"]), "reason": grant["reason"],
                "createdAt": _iso(grant["createdAt"]), "createdBy": grant["createdBy"],
            } for grant in grants],
            "blockedUntil": _iso(await service._active_block_until(str(student_id),
                                                                     service._now())),
            "sync": {"activeSeconds": service.sync_active_seconds,
                     "idleSeconds": service.sync_idle_seconds},
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
        """Bugün (Europe/Istanbul) için ek süre; ek sürenin kimliği döner.

        `package_name` verilirse yalnız o uygulamaya. `package_name=None`
        TÜM TABLETE ek süredir (sözleşme §4). KM'de süre sınırı uygulama
        başınadır, tablet geneli bir bütçe yoktur; bu yüzden tüm tablet ek
        süresi, öğrencinin profilindeki (`device_grant_packages`) izinli ve süre
        sınırlı HER uygulamaya aynı süreyle ayrı bir satır olarak yazılır —
        tablet uygulaması değişmeden uygular, KM panelinde satırlar ayrı ayrı
        görünür ve geri alınır. Dönen kimlik ilk satırındır (paket adına göre
        sıralı). Süre sınırlı uygulama yoksa `validation_error`.
        """
        why = _reason(reason)
        package = str(package_name or "").strip()
        if isinstance(extra_seconds, bool) or not isinstance(extra_seconds, int) \
                or not 0 < extra_seconds <= 86400:
            raise TabletCapabilityError("Ek süre 1 saniye ile 24 saat arasında olmalı.",
                                        reason="validation_error")
        await self._student(student_id)
        packages = [package] if package \
            else await self._service.device_grant_packages(str(student_id))
        if not packages:
            raise TabletCapabilityError(
                "Öğrencinin profilinde süre sınırlı uygulama yok; ek süre gerekmiyor.",
                reason="validation_error",
            )
        local_date = self._service._now().astimezone(
            zone(self._service.default_timezone)).date().isoformat()
        granted_ids: list[str] = []
        for name in packages:
            try:
                granted = await self._service.grant_time(
                    student_id=str(student_id), package_name=name, local_date=local_date,
                    extra_seconds=extra_seconds, reason=why, actor=_actor(actor),
                )
            except CanteenUnavailableError as exc:
                raise TabletCapabilityError(str(exc), reason="canteen_unavailable") from exc
            except ValueError as exc:
                raise TabletCapabilityError(str(exc), reason="validation_error") from exc
            granted_ids.append(str(granted["id"]))
        self._log.info("tablet ek süre (yetenek)", student=student_id,
                       package=package or "*", apps=len(granted_ids),
                       seconds=extra_seconds, actor=_actor(actor))
        return granted_ids[0]

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

    async def unblock(self, student_id: str, reason: str, actor: str, *,
                      only_reason: str | None = None) -> None:
        """Süren kısıtları kaldırır. Kısıt yoksa da sessizce başarılıdır.

        `only_reason`: yalnız bu gerekçeyle konmuş kısıtlar kalkar (gerekçe
        `block`taki gibi boşlukları sadeleştirilerek karşılaştırılır).
        """
        why = _reason(reason)
        only = " ".join(str(only_reason).split()) if only_reason is not None else None
        result = await self._service.unblock_student(student_id=str(student_id),
                                                     only_reason=only)
        self._log.info("tablet kısıtı kaldırıldı (yetenek)", student=student_id,
                       lifted=result.get("lifted"), actor=_actor(actor), reason=why)

    async def session_student(self, session_id: str) -> str | None:
        """Oturumun öğrencisi (`STU-…`); oturum yoksa None. Geçit, oturum bitirme
        olayını (`tablet.policy_changed`) doğru öğrenciye bağlamak için sorar."""
        row = await self._service.store.fetch_one(
            f"SELECT student_id FROM {self._service.sessions} WHERE id=?", (str(session_id),))
        return str(row["student_id"]) if row and row.get("student_id") else None

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

    async def update_profile_limits(self, profile_id: str, expected_revision: int,
                                    apps: list[dict[str, Any]] | None,
                                    usage_windows: list[dict[str, Any]] | None,
                                    reason: str, actor: str) -> dict[str, Any]:
        """Profilin uygulama sınırlarını ve kullanım saatlerini düzenler (yeni revizyon).

        Yalnız profilde ZATEN olan uygulamaların kipi/süresi değişir (`apps`:
        `[{packageName, mode: blocked|unlimited|limited, dailyLimitSeconds}]`); ad,
        sıra, sistem kontrolleri ve anılmayan uygulamalar korunur. Yeni uygulama
        eklemek / çıkarmak KM ekranındadır. `usage_windows=None` saatleri değiştirmez,
        liste verilirse tamamen yerine geçer (boş liste = her zaman açık).

        `expected_revision` profilin şu anki revizyonu değilse `profile_conflict`
        (bu arada KM'de ya da başka yöneticice değiştirilmiş). Okuma ve yazma
        profil kilidi altında yapılır. Tablet değişikliği sonraki sync'inde alır
        (profil revizyonu farklı → tam profil).
        """
        why = _reason(reason)
        service = self._service
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
            raise TabletCapabilityError("Beklenen revizyon tamsayı olmalı.",
                                        reason="validation_error")
        changes: dict[str, dict[str, Any]] = {}
        for raw in apps or []:
            entry = raw if isinstance(raw, dict) else {}
            package = str(entry.get("packageName") or "").strip()
            mode = entry.get("mode")
            if not package or mode not in APP_MODES:
                raise TabletCapabilityError("Uygulama kuralı geçersiz (paket adı ve kip gerekli).",
                                            reason="validation_error")
            if package in changes:
                raise TabletCapabilityError("Aynı uygulama bir kez gönderilebilir.",
                                            reason="validation_error")
            limit = entry.get("dailyLimitSeconds")
            if mode == "limited" and (isinstance(limit, bool) or not isinstance(limit, int)
                                      or not MIN_DAILY_LIMIT_SECONDS <= limit
                                      <= MAX_DAILY_LIMIT_SECONDS):
                raise TabletCapabilityError(
                    "Günlük süre 1 dakika ile 24 saat arasında olmalı.", reason="validation_error")
            changes[package] = {"mode": mode, "limit": limit if mode == "limited" else 0}
        try:
            windows = None if usage_windows is None else normalize_usage_windows(usage_windows)
        except ValueError as exc:
            raise TabletCapabilityError(str(exc), reason="validation_error") from exc
        if not changes and windows is None:
            raise TabletCapabilityError("Değişiklik yok.", reason="validation_error")

        async with service._profile_lock:
            current = await service.profile(str(profile_id))
            if current is None:
                raise TabletCapabilityError("Profil bulunamadı.", reason="profile_not_found")
            if int(current["revision"]) != expected_revision:
                raise TabletCapabilityError(
                    "Profil bu arada değişti; sınırları yeniden yükleyin.",
                    reason="profile_conflict")
            unknown = sorted(set(changes) - {app["packageName"] for app in current["apps"]})
            if unknown:
                raise TabletCapabilityError(
                    "Profilde olmayan uygulama: " + ", ".join(unknown[:3]),
                    reason="validation_error")
            rules = []
            for app in current["apps"]:
                rule = {"packageName": app["packageName"], "appName": app["appName"],
                        "allowed": app["allowed"], "unlimited": app["unlimited"],
                        "dailyLimitSeconds": app["dailyLimitSeconds"]}
                change = changes.get(app["packageName"])
                if change is not None:
                    rule["allowed"] = change["mode"] != "blocked"
                    rule["unlimited"] = change["mode"] == "unlimited"
                    rule["dailyLimitSeconds"] = change["limit"]
                rules.append(rule)
            try:
                saved = await service._save_profile(
                    profile_id=current["id"], name=current["name"],
                    timezone=current["timezone"], apps=rules, system_controls=None,
                    usage_windows=windows,
                )
            except ValueError as exc:
                raise TabletCapabilityError(str(exc), reason="validation_error") from exc
        self._log.info("tablet profil sınırları (yetenek)", profile=profile_id,
                       revision=saved.get("revision"), apps=len(changes),
                       windows=windows is not None, actor=_actor(actor), reason=why)
        return {"id": saved["id"], "name": saved["name"], "revision": int(saved["revision"]),
                "updatedAt": _iso(saved.get("updatedAt"))}

    async def revoke_grant(self, student_id: str, grant_id: str, reason: str,
                           actor: str) -> dict[str, Any]:
        """Ek süreyi geri alır (yalnız o öğrencinin). Zaten geri alınmışsa
        `alreadyRevoked: true`; yoksa `grant_not_found`. Açık oturumdaki tablet
        `grantsVersion` değiştiği için sonraki sync'te listeyi yeniler."""
        why = _reason(reason)
        try:
            result = await self._service.revoke_grant(student_id=str(student_id),
                                                      grant_id=str(grant_id),
                                                      actor=_actor(actor))
        except NotFoundError as exc:
            raise TabletCapabilityError(str(exc), reason="grant_not_found") from exc
        self._log.info("tablet ek süresi geri alındı (yetenek)", student=student_id,
                       grant=grant_id, actor=_actor(actor), reason=why)
        return {"id": str(result["id"]), "revoked": True,
                "alreadyRevoked": bool(result.get("alreadyRevoked"))}
