"""Pure contract rules shared by the service and its tests (sözleşme v2).

Nothing here touches the store: enrollment codes, usage windows, app modes,
command payloads and the session view the tablet reads in `sync`.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, time, timedelta
from typing import Any

ENROLLMENT_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ENROLLMENT_CODE_LENGTH = 8

OFFLINE_MODES = ("local", "allowed", "denied")
COMMAND_TYPES = ("message", "refresh_inventory", "flush_usage", "reboot",
                 "self_update", "diagnostics")
END_REASONS = ("admin", "expired", "logout", "revoked", "blocked", "outside_hours", "replaced")

MAX_USAGE_WINDOWS = 21
MIN_DAILY_LIMIT_SECONDS = 60
MAX_DAILY_LIMIT_SECONDS = 86400
MAX_MESSAGE_LENGTH = 500

# Sözleşme v2.2: the tablet's `selfUpdate` report and `policyDetail`.
SELF_UPDATE_STATES = ("idle", "downloading", "ready", "installing", "failed", "installed")
MAX_POLICY_DETAIL = 300
_SELF_UPDATE_TEXT = {"releaseId": 100, "error": 200, "detail": 300, "nextRetryAt": 64,
                     "at": 64}
_SELF_UPDATE_NUMBERS = ("versionCode", "receivedBytes", "attempt")

_HHMM = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_CODE_SEPARATORS = re.compile(r"[\s-]+")


def new_enrollment_code() -> str:
    return "".join(secrets.choice(ENROLLMENT_ALPHABET) for _ in range(ENROLLMENT_CODE_LENGTH))


def normalize_enrollment_code(value: str) -> str:
    """Server and tablet accept `abcd-efgh`, `ABCD EFGH` and `ABCDEFGH` alike."""
    return _CODE_SEPARATORS.sub("", value).upper()


def display_enrollment_code(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def normalize_usage_windows(raw: Any) -> list[dict[str, Any]]:
    """Validate `usageWindows`: ISO weekdays 1–7, `HH:MM`, start < end.

    An empty list means "always open". Days are sorted and de-duplicated so the
    stored form is stable and revisions compare cleanly.
    """
    if raw is None:
        return []
    items = raw if isinstance(raw, list) else None
    if items is None:
        raise ValueError("Kullanım saatleri liste olmalı.")
    if len(items) > MAX_USAGE_WINDOWS:
        raise ValueError(f"En fazla {MAX_USAGE_WINDOWS} kullanım aralığı tanımlanabilir.")
    windows: list[dict[str, Any]] = []
    for entry in items:
        item = entry if isinstance(entry, dict) else {}
        days = item.get("days")
        if not isinstance(days, list) or not days or any(
            type(day) is not int or not 1 <= day <= 7 for day in days
        ):
            raise ValueError("Kullanım aralığında günler 1 (Pazartesi) ile 7 (Pazar) arasında olmalı.")
        start, end = item.get("start"), item.get("end")
        if not isinstance(start, str) or not isinstance(end, str) \
                or not _HHMM.match(start) or not _HHMM.match(end):
            raise ValueError("Kullanım saatleri SS:DD biçiminde olmalı.")
        if start >= end:
            raise ValueError("Kullanım aralığının başlangıcı bitişinden önce olmalı.")
        windows.append({"days": sorted(set(days)), "start": start, "end": end})
    return windows


def inside_usage_window(windows: list[dict[str, Any]], moment: datetime) -> bool:
    """`moment` must already be in the profile's time zone."""
    if not windows:
        return True
    clock = moment.strftime("%H:%M")
    weekday = moment.isoweekday()
    return any(weekday in window["days"] and window["start"] <= clock < window["end"]
               for window in windows)


def next_window_start(windows: list[dict[str, Any]], moment: datetime) -> datetime | None:
    """First window start strictly after `moment`, in `moment`'s time zone."""
    for offset in range(8):
        day = moment.date() + timedelta(days=offset)
        weekday = day.isoweekday()
        starts = []
        for window in windows:
            if weekday not in window["days"]:
                continue
            hour, minute = (int(part) for part in window["start"].split(":"))
            start = datetime.combine(day, time(hour, minute), tzinfo=moment.tzinfo)
            if start > moment:
                starts.append(start)
        if starts:
            return min(starts)
    return None


def normalize_daily_total(raw: Any) -> int:
    """Profile-wide daily total in seconds: 0 (none) or 60–86400, like an app limit."""
    if raw is None or raw == "":
        return 0
    try:
        seconds = int(raw if not isinstance(raw, bool) else "")
    except (TypeError, ValueError) as exc:
        raise ValueError("Günlük toplam süre sayı olmalı.") from exc
    if seconds == 0:
        return 0
    if not MIN_DAILY_LIMIT_SECONDS <= seconds <= MAX_DAILY_LIMIT_SECONDS:
        raise ValueError("Günlük toplam süre 1 dakika ile 24 saat arasında olmalı.")
    return seconds


# BBD Akademi her profilde izinli ve sınırsızdır: süreden yemez, günlük toplama
# sayılmaz. TabletKontrol 0.7.7+ aynı kuralı profil okurken kendisi de uygular.
ALWAYS_UNLIMITED_APPS: dict[str, str] = {"com.veykemtu.benimdunyam": "BBD Akademi"}


def with_always_unlimited(apps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Profile app views with every always-unlimited app present, allowed and unlimited."""
    present = {app["packageName"] for app in apps}
    fixed = [
        {**app, "allowed": True, "unlimited": True, "dailyLimitSeconds": 0}
        if app["packageName"] in ALWAYS_UNLIMITED_APPS else app
        for app in apps
    ]
    first = min((int(app.get("sortOrder", 0)) for app in apps), default=0) - 1
    added = [{"packageName": package, "appName": name, "allowed": True, "unlimited": True,
              "dailyLimitSeconds": 0, "sortOrder": first}
             for package, name in ALWAYS_UNLIMITED_APPS.items() if package not in present]
    return fixed + added


def normalize_app_rules(apps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply the three app modes: blocked, unlimited, timed (60–86400 s)."""
    rules: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in apps:
        package = str(raw.get("packageName") or "").strip()
        if not 2 <= len(package) <= 255:
            raise ValueError("Uygulama paket adı geçersiz.")
        if package in seen:
            raise ValueError("Aynı uygulama profilde bir kez bulunabilir.")
        seen.add(package)
        # Tablets require a display name; an unnamed app shows its package name.
        name = (str(raw.get("appName") or "").strip() or package)[:120]
        allowed = bool(raw.get("allowed", False))
        unlimited = allowed and bool(raw.get("unlimited", False))
        if package in ALWAYS_UNLIMITED_APPS:
            allowed = unlimited = True
        try:
            limit = int(raw.get("dailyLimitSeconds") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("Günlük süre sayı olmalı.") from exc
        if allowed and not unlimited:
            if not MIN_DAILY_LIMIT_SECONDS <= limit <= MAX_DAILY_LIMIT_SECONDS:
                raise ValueError(
                    f"{name or package}: günlük süre 1 dakika ile 24 saat arasında olmalı."
                )
        else:
            limit = 0
        rules.append({"packageName": package, "appName": name, "allowed": allowed,
                      "unlimited": unlimited, "dailyLimitSeconds": limit})
    return rules


def normalize_command(command_type: str, payload: Any) -> dict[str, Any]:
    """Return the stored payload for a command type, or raise ValueError."""
    if command_type not in COMMAND_TYPES:
        raise ValueError("Desteklenmeyen komut türü.")
    if command_type != "message":
        return {}
    text = str((payload or {}).get("text") or "").strip() if isinstance(payload, dict) else ""
    if not text:
        raise ValueError("Mesaj metni boş olamaz.")
    if len(text) > MAX_MESSAGE_LENGTH:
        raise ValueError(f"Mesaj en fazla {MAX_MESSAGE_LENGTH} karakter olabilir.")
    return {"text": text}


def normalize_self_update(raw: Any) -> dict[str, Any] | None:
    """The stored form of a sync `selfUpdate` report, or None when unusable.

    Lenient on purpose: a malformed report must never turn the tablet's only
    periodic call into a 422, so overlong text is cut, unknown keys and
    non-numeric numbers are dropped, and an unknown `state` drops the report.
    """
    if not isinstance(raw, dict) or raw.get("state") not in SELF_UPDATE_STATES:
        return None
    report: dict[str, Any] = {"state": raw["state"]}
    for key, limit in _SELF_UPDATE_TEXT.items():
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            report[key] = value.strip()[:limit]
    for key in _SELF_UPDATE_NUMBERS:
        value = raw.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            report[key] = value
    return report


def normalize_policy_detail(raw: Any) -> str | None:
    text = raw.strip()[:MAX_POLICY_DETAIL] if isinstance(raw, str) else ""
    return text or None


def session_view(row: dict[str, Any], *, expires_at: str | None) -> dict[str, Any]:
    """The `session` object of `sync`, also used by the session history list."""
    if row["state"] == "active":
        return {"id": row["id"], "state": "active", "endReason": None, "message": "",
                "expiresAt": expires_at}
    reason = row.get("end_reason") or ("logout" if row["state"] == "logged_out" else "expired")
    return {"id": row["id"], "state": "ended", "endReason": reason,
            "message": row.get("end_message") or "", "expiresAt": row.get("ended_at")}
