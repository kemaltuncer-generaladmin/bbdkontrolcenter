"""Zil planı ve saat setleri — Akademi'den gelen günlük plan, KM'nin kendi saat setleri.

Sözleşme: bbd-akademi/docs/integrations/km-ogrenci-gruplari-ve-zil.md §3.

SAHİPLİK. Haftalık saatler ve SAAT SETLERİ KM'nindir (`mod_bell_time`,
`mod_bell_profile*`). ZİL PLANI Akademi'den türetilmiş çalışma kopyasıdır:
her gün için kip (`normal` · `off` · `profile`) ve oturum başı grup
çağrıları. Akademi `PUT /bell/plan` ile iter; KM planı yerelde tutar ve
ÇALMA ANINDA Akademi'ye gitmez. Plan hiç yoksa bugünkü gibi haftalık
saatlerle çalınır, çağrı yapılmaz.

GÜN UYGULAMA KURALI (§3.3), her gün BAĞIMSIZ:
  · `planHash` aynı                     → `unchanged`
  · `revision` mevcuttan küçük          → `stale` (yok sayılır)
  · aynı `revision`, farklı `planHash`  → `rejected` + `revision_conflict`
  · `profile` kipi, KM'de olmayan set   → `rejected` + `unknown_profile`
  · aksi hâlde                          → `applied` (o güne KM acil kapatması
                                          varsa plan saklanır, sonuç `overridden`)

YAZMA SIRASI. Modül deposunda işlem açılamıyor; gün içeriği şu sırayla yazılır:
önce o günün çağrıları silinip yeniden yazılır, EN SON gün satırı (revizyon ve
özetiyle). Yarıda kalan bir yazma gün satırını eski özetle bırakır; Akademi'nin
uzlaştırma işi durum ucunda farkı görür ve günü yeniden iter — kendini onarır.

Bu dosya saf kural + kendi tablolarına erişimdir; çalma, ses ve zamanlama
`service.py`'dedir.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

ISTANBUL = ZoneInfo("Europe/Istanbul")

MODES = ("normal", "off", "profile")
GROUP_KINDS = ("group", "individual")
CALL_KINDS = ("lesson", "etut", "makeup")
RESULTS = ("applied", "unchanged", "stale", "overridden", "rejected")

#: Akademi'nin ürettiği pencere (bugün dahil) ve tek itmenin en geniş aralığı.
WINDOW_DAYS = 14
PUSH_MAX_DAYS = 15
#: Durum ucunun en geniş aralığı.
STATUS_MAX_DAYS = 31
#: Tetik kurulan pencere: platform zamanlayıcısı haftalıktır (gün + saat);
#: yedi gün her hafta gününe TEK tarih düşürür.
SCHEDULE_DAYS = 7
#: Bir günde en çok bu kadar çağrı (sözleşme ≈ 60; pay bırakılır).
CALLS_MAX_PER_DAY = 200
#: Saklanan geçmiş: durum ucu en çok 31 gün okur; fazlası gece silinir.
KEEP_DAYS = 62

REASON_MAX = 80
NAME_MAX = 80
TEACHER_MAX = 60
ROOM_MAX = 40
SUBJECT_MAX = 60
REF_MAX = 64
GROUP_REF_MAX = 120
PROFILE_NAME_MAX = 40
PROFILE_TIMES_MAX = 40
PROFILE_MAX = 30

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CLOCK = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def today_local() -> date:
    return datetime.now(ISTANBUL).date()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def local_iso(value: str) -> str | None:
    """Saklanan UTC damgası → Europe/Istanbul ISO."""
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(ISTANBUL).isoformat(timespec="seconds")


def play_at(day: date, clock_text: str, *, offset_seconds: int = 0) -> str:
    """O günün o saati, Europe/Istanbul'da MUTLAK an (ISO). Ajan bu ana dek bekler.

    Makinenin saat dilimine bakılmaz: sunucu konteyneri UTC'de koşar
    (bkz. km_platform/scheduler/weekly.py → `DEFAULT_TIMEZONE`).
    """
    hour, minute = (int(part) for part in clock_text.split(":"))
    moment = datetime(day.year, day.month, day.day, hour, minute, tzinfo=ISTANBUL)
    return (moment + timedelta(seconds=offset_seconds)).isoformat(timespec="seconds")


class PlanError(ValueError):
    """Plan reddedildi. `status` HTTP karşılığı, `code` makine için, mesaj Türkçe."""

    def __init__(self, message: str, *, status: int = 422, code: str = "validation_error",
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.details = details or {}


def parse_day(value: Any, *, path: str) -> date:
    text = str(value or "").strip()
    if not _DATE.match(text):
        raise PlanError(f"{path}: YYYY-AA-GG biçiminde tarih bekleniyor.",
                        details={"path": path})
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise PlanError(f"{path}: geçerli bir tarih değil.", details={"path": path}) from None


def _text(value: Any, limit: int, *, path: str, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise PlanError(f"{path} zorunlu.", details={"path": path})
        return None
    if not isinstance(value, str):
        raise PlanError(f"{path} metin olmalı.", details={"path": path})
    clean = " ".join(value.split())
    if not clean:
        if required:
            raise PlanError(f"{path} boş olamaz.", details={"path": path})
        return None
    if len(clean) > limit:
        raise PlanError(f"{path} en çok {limit} karakter olabilir.", details={"path": path})
    return clean


def _choice(value: Any, allowed: tuple[str, ...], *, path: str) -> str:
    if value not in allowed:
        raise PlanError(f"{path}: {' | '.join(allowed)} bekleniyor.", details={"path": path})
    return str(value)


def validate_push(body: Any) -> tuple[date, date, list[dict[str, Any]]]:
    """`PUT /bell/plan` gövdesi → (from, to, temiz günler). Yapı bozuksa `PlanError`.

    `days` aralıktaki HER günü taşımalıdır (eksik gün `validation_error`).
    Bilinmeyen saat seti burada DEĞİL, gün uygulanırken reddedilir (gün
    `rejected`, diğerleri uygulanır).
    """
    if not isinstance(body, dict):
        raise PlanError("Gövde nesne olmalı.", details={"path": ""})
    start = parse_day(body.get("from"), path="from")
    end = parse_day(body.get("to"), path="to")
    if end < start:
        raise PlanError("to, from'dan önce olamaz.", details={"path": "to"})
    span = (end - start).days + 1
    if span > PUSH_MAX_DAYS:
        raise PlanError(f"Aralık en çok {PUSH_MAX_DAYS} gün olabilir.", details={"path": "to"})
    raw_days = body.get("days")
    if not isinstance(raw_days, list):
        raise PlanError("days liste olmalı.", details={"path": "days"})

    days: list[dict[str, Any]] = []
    seen: set[date] = set()
    for index, raw in enumerate(raw_days):
        path = f"days[{index}]"
        if not isinstance(raw, dict):
            raise PlanError(f"{path} nesne olmalı.", details={"path": path})
        day = parse_day(raw.get("date"), path=f"{path}.date")
        if day < start or day > end:
            raise PlanError(f"{path}.date aralığın dışında.", details={"path": f"{path}.date"})
        if day in seen:
            raise PlanError(f"{path}.date iki kez geçiyor.", details={"path": f"{path}.date"})
        seen.add(day)
        mode = _choice(raw.get("mode"), MODES, path=f"{path}.mode")
        profile_id = _text(raw.get("profileId"), 64, path=f"{path}.profileId")
        if mode == "profile" and not profile_id:
            raise PlanError(f"{path}.profileId profile kipinde zorunlu.",
                            details={"path": f"{path}.profileId"})
        revision = raw.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise PlanError(f"{path}.revision pozitif tamsayı olmalı.",
                            details={"path": f"{path}.revision"})
        plan_hash = _text(raw.get("planHash"), 128, path=f"{path}.planHash", required=True)
        calls_raw = raw.get("calls")
        if not isinstance(calls_raw, list):
            raise PlanError(f"{path}.calls liste olmalı.", details={"path": f"{path}.calls"})
        if len(calls_raw) > CALLS_MAX_PER_DAY:
            raise PlanError(f"{path}.calls en çok {CALLS_MAX_PER_DAY} çağrı taşır.",
                            details={"path": f"{path}.calls"})
        calls = [_call(item, path=f"{path}.calls[{position}]")
                 for position, item in enumerate(calls_raw)]
        refs = [call["ref"] for call in calls]
        if len(set(refs)) != len(refs):
            raise PlanError(f"{path}.calls aynı ref'i iki kez taşıyor.",
                            details={"path": f"{path}.calls"})
        if mode == "off" and calls:
            raise PlanError(f"{path}.calls off kipinde boş olmalı.",
                            details={"path": f"{path}.calls"})
        days.append({
            "date": day, "mode": mode, "profileId": profile_id if mode == "profile" else None,
            "reason": _text(raw.get("reason"), REASON_MAX, path=f"{path}.reason"),
            "revision": revision, "planHash": plan_hash, "calls": calls,
        })

    missing = [start + timedelta(days=offset) for offset in range(span)
               if start + timedelta(days=offset) not in seen]
    if missing:
        raise PlanError("days aralıktaki her günü taşımalı.",
                        details={"path": "days", "missing": [day.isoformat() for day in missing]})
    return start, end, days


def _call(raw: Any, *, path: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise PlanError(f"{path} nesne olmalı.", details={"path": path})
    ref = _text(raw.get("ref"), REF_MAX, path=f"{path}.ref", required=True)
    at = str(raw.get("at") or "")
    if not _CLOCK.match(at):
        raise PlanError(f"{path}.at SS:DD olmalı.", details={"path": f"{path}.at"})
    return {
        "ref": ref,
        "at": at,
        "groupKind": _choice(raw.get("groupKind"), GROUP_KINDS, path=f"{path}.groupKind"),
        "groupName": _text(raw.get("groupName"), NAME_MAX, path=f"{path}.groupName",
                           required=True),
        "groupRef": _text(raw.get("groupRef"), GROUP_REF_MAX, path=f"{path}.groupRef") or "",
        "teacherName": _text(raw.get("teacherName"), TEACHER_MAX, path=f"{path}.teacherName"),
        "room": _text(raw.get("room"), ROOM_MAX, path=f"{path}.room"),
        "subject": _text(raw.get("subject"), SUBJECT_MAX, path=f"{path}.subject"),
        "kind": _choice(raw.get("kind"), CALL_KINDS, path=f"{path}.kind"),
    }


# ------------------------------------------------------------------- metin

#: Şablon yer tutucuları. `{grup}` dışındakiler değer yoksa boş kalır ve
#: boşluk sadeleşir; genel şablon (`callGeneric`/`soloGeneric`) öğretmen adı
#: olmayan çağrı içindir.
PLACEHOLDERS = ("{grup}", "{hoca}", "{derslik}", "{ders}")


def render_call(template: str, *, group: str, teacher: str | None = None,
                room: str | None = None, subject: str | None = None) -> str:
    text = str(template or "")
    for key, value in (("{grup}", group), ("{hoca}", teacher), ("{derslik}", room),
                       ("{ders}", subject)):
        text = text.replace(key, " ".join(str(value or "").split())[:NAME_MAX])
    # Boş kalan yer tutucu ", ile" gibi kırıntı bırakabilir; boşluk sadeleşir.
    text = " ".join(text.split())
    return re.sub(r"\s+([,.])", r"\1", text)


def solo(kind: str) -> bool:
    """Tekil hitap mı? Zil grubu türü (`ozel`) ve plan türü (`individual`) aynı anlamda."""
    return kind in ("individual", "ozel")


# ----------------------------------------------------------------- tablolar


class PlanBook:
    """`mod_bell_day`, `mod_bell_call`, `mod_bell_profile*` — modülün kendi tabloları (K5)."""

    def __init__(self, store: Any) -> None:
        self._store = store
        self.day_table = store.table("day")
        self.call_table = store.table("call")
        self.profile_table = store.table("profile")
        self.profile_time_table = store.table("profile_time")

    # ------------------------------------------------------------ saat setleri

    async def profiles(self) -> list[dict[str, Any]]:
        rows = await self._store.fetch_all(
            f"SELECT * FROM {self.profile_table} ORDER BY sort, name")
        times = await self._store.fetch_all(
            f"SELECT * FROM {self.profile_time_table} ORDER BY profile_id, time")
        by_profile: dict[str, list[dict[str, Any]]] = {}
        for row in times:
            by_profile.setdefault(str(row["profile_id"]), []).append(
                {"time": str(row["time"]), "label": str(row["label"]),
                 "kind": str(row["kind"] or "ders")})
        return [{"id": str(row["id"]), "name": str(row["name"]), "sort": int(row["sort"]),
                 "times": by_profile.get(str(row["id"]), []),
                 "updatedAt": local_iso(str(row["updated_at"]))} for row in rows]

    async def profile_ids(self) -> set[str]:
        rows = await self._store.fetch_all(f"SELECT id FROM {self.profile_table}")
        return {str(row["id"]) for row in rows}

    async def save_profile(self, profile_id: str, name: str, name_fold: str,
                           times: list[tuple[str, str, str]], *, sort: int, actor: str,
                           create: bool) -> None:
        stamp = _now()
        if create:
            await self._store.execute(
                f"INSERT INTO {self.profile_table} (id, name, name_fold, sort, created_at, "
                f"updated_at, updated_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (profile_id, name, name_fold, sort, stamp, stamp, actor))
        else:
            await self._store.execute(
                f"UPDATE {self.profile_table} SET name = ?, name_fold = ?, updated_at = ?, "
                f"updated_by = ? WHERE id = ?", (name, name_fold, stamp, actor, profile_id))
        await self._store.execute(
            f"DELETE FROM {self.profile_time_table} WHERE profile_id = ?", (profile_id,))
        for clock_text, label, kind in times:
            await self._store.execute(
                f"INSERT INTO {self.profile_time_table} (id, profile_id, time, label, kind) "
                f"VALUES (?, ?, ?, ?, ?)",
                (f"{profile_id}-{clock_text.replace(':', '')}", profile_id, clock_text, label,
                 kind))

    async def delete_profile(self, profile_id: str) -> None:
        await self._store.execute(
            f"DELETE FROM {self.profile_time_table} WHERE profile_id = ?", (profile_id,))
        await self._store.execute(
            f"DELETE FROM {self.profile_table} WHERE id = ?", (profile_id,))

    async def days_using_profile(self, profile_id: str, since: date) -> list[str]:
        rows = await self._store.fetch_all(
            f"SELECT date FROM {self.day_table} WHERE profile_id = ? AND date >= ? "
            f"AND mode = 'profile' ORDER BY date", (profile_id, since.isoformat()))
        return [str(row["date"]) for row in rows]

    # ------------------------------------------------------------------ günler

    async def day(self, day: date) -> dict[str, Any] | None:
        row = await self._store.fetch_one(
            f"SELECT * FROM {self.day_table} WHERE date = ?", (day.isoformat(),))
        return dict(row) if row else None

    async def days(self, start: date, end: date) -> dict[str, dict[str, Any]]:
        rows = await self._store.fetch_all(
            f"SELECT * FROM {self.day_table} WHERE date >= ? AND date <= ?",
            (start.isoformat(), end.isoformat()))
        return {str(row["date"]): dict(row) for row in rows}

    async def calls(self, start: date, end: date) -> list[dict[str, Any]]:
        """Aralığın çağrıları — `(date, at)` indeksiyle tek sorgu."""
        rows = await self._store.fetch_all(
            f"SELECT * FROM {self.call_table} WHERE date >= ? AND date <= ? "
            f"ORDER BY date, at, group_name", (start.isoformat(), end.isoformat()))
        return [dict(row) for row in rows]

    async def calls_at(self, day: date, at: str) -> list[dict[str, Any]]:
        """O dakikanın çalınacak çağrıları — iptal edilenler (`cancelled`) hariç."""
        rows = await self._store.fetch_all(
            f"SELECT * FROM {self.call_table} WHERE date = ? AND at = ? "
            f"AND state != 'cancelled' ORDER BY group_name", (day.isoformat(), at))
        return [dict(row) for row in rows]

    async def cancel_group_calls(self, group_ref: str, since: date, detail: str) -> int:
        """`group_ref` sınıfının `since` ve sonrasındaki PLANLI çağrıları `cancelled`.

        Satır silinmez (Akademi planının kopyası; plan özeti değişmez) — durum
        ekranında "iptal" görünür, çalınmaz. Akademi yeni planı gönderince gün
        satırları zaten yeniden yazılır. Dokunulan satır sayısını döner.
        """
        rows = await self._store.fetch_all(
            f"SELECT id FROM {self.call_table} WHERE group_ref = ? AND date >= ? "
            f"AND state = 'planned'", (group_ref, since.isoformat()))
        stamp = _now()
        for row in rows:
            await self._store.execute(
                f"UPDATE {self.call_table} SET state = 'cancelled', detail = ?, updated_at = ? "
                f"WHERE id = ?", (detail[:300], stamp, str(row["id"])))
        return len(rows)

    async def write_day(self, entry: dict[str, Any], *, calls: list[dict[str, Any]],
                        existing: dict[str, Any] | None, result: str = "applied") -> None:
        """Günün çağrılarını değiştirir, EN SON gün satırını yazar (başlıktaki sıra)."""
        day = entry["date"].isoformat()
        stamp = _now()
        await self._store.execute(f"DELETE FROM {self.call_table} WHERE date = ?", (day,))
        for call in calls:
            await self._store.execute(
                f"INSERT INTO {self.call_table} (id, date, at, ref, group_kind, group_name, "
                f"group_ref, teacher_name, room, subject, kind, text_hash, voice_key, "
                f"generic_voice_key, state, detail, updated_at) "
                f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned', '', ?)",
                (str(uuid.uuid4()), day, call["at"], call["ref"], call["groupKind"],
                 call["groupName"], call["groupRef"], call["teacherName"] or "",
                 call["room"] or "", call["subject"] or "", call["kind"], call["textHash"],
                 call["voiceKey"], call["genericVoiceKey"], stamp))
        values = (entry["mode"], entry["profileId"] or "", entry["reason"] or "",
                  entry["revision"], entry["planHash"], stamp, result, "")
        if existing is None:
            await self._store.execute(
                f"INSERT INTO {self.day_table} (date, mode, profile_id, reason, revision, "
                f"plan_hash, source, received_at, last_result, last_error) "
                f"VALUES (?, ?, ?, ?, ?, ?, 'akademi', ?, ?, ?)", (day, *values))
        else:
            await self._store.execute(
                f"UPDATE {self.day_table} SET mode = ?, profile_id = ?, reason = ?, "
                f"revision = ?, plan_hash = ?, source = 'akademi', received_at = ?, "
                f"last_result = ?, last_error = ? WHERE date = ?", (*values, day))

    async def note_result(self, day: date, existing: dict[str, Any] | None, *,
                          result: str, error: str = "") -> None:
        """Uygulanmayan günün sonucunu (rejected/stale) gün satırına not eder.

        Plan alanlarına DOKUNMAZ: reddedilen gün mevcut planıyla (ya da plan
        yoksa haftalık saatlerle) çalmaya devam eder.
        """
        if existing is None:
            await self._store.execute(
                f"INSERT INTO {self.day_table} (date, mode, source, received_at, last_result, "
                f"last_error) VALUES (?, 'normal', '', ?, ?, ?)",
                (day.isoformat(), _now(), result, error))
            return
        await self._store.execute(
            f"UPDATE {self.day_table} SET last_result = ?, last_error = ?, received_at = ? "
            f"WHERE date = ?", (result, error, _now(), day.isoformat()))

    async def bump_revision(self, day: date, revision: int) -> None:
        """Özet aynı, revizyon ileri: içerik değişmez, imleç ilerler."""
        await self._store.execute(
            f"UPDATE {self.day_table} SET revision = ?, received_at = ?, last_result = "
            f"'unchanged', last_error = '' WHERE date = ?",
            (revision, _now(), day.isoformat()))

    async def set_override(self, day: date, mode: str, *, actor: str) -> None:
        existing = await self.day(day)
        stamp = _now()
        if existing is None:
            await self._store.execute(
                f"INSERT INTO {self.day_table} (date, mode, source, override_mode, override_by, "
                f"override_at) VALUES (?, 'normal', '', ?, ?, ?)",
                (day.isoformat(), mode, actor, stamp))
            return
        await self._store.execute(
            f"UPDATE {self.day_table} SET override_mode = ?, override_by = ?, override_at = ? "
            f"WHERE date = ?", (mode, actor, stamp, day.isoformat()))

    async def clear_override(self, day: date) -> None:
        await self._store.execute(
            f"UPDATE {self.day_table} SET override_mode = '', override_by = '', "
            f"override_at = '' WHERE date = ?", (day.isoformat(),))

    async def mark_call(self, call_id: str, state: str, detail: str) -> None:
        await self._store.execute(
            f"UPDATE {self.call_table} SET state = ?, detail = ?, updated_at = ? WHERE id = ?",
            (state, detail[:300], _now(), call_id))

    async def mark_dispatch(self, day: date) -> None:
        await self._store.execute(
            f"UPDATE {self.day_table} SET last_dispatch_at = ? WHERE date = ?",
            (_now(), day.isoformat()))

    async def voice_keys(self, start: date, end: date) -> set[str]:
        """Pencerenin çağrı sesleri (kesin + genel) — köprü eşitlemesi silmesin."""
        rows = await self._store.fetch_all(
            f"SELECT voice_key, generic_voice_key FROM {self.call_table} "
            f"WHERE date >= ? AND date <= ?", (start.isoformat(), end.isoformat()))
        keys: set[str] = set()
        for row in rows:
            keys.update(key for key in (str(row["voice_key"]), str(row["generic_voice_key"]))
                        if key)
        return keys

    async def purge(self, before: date) -> None:
        """Eski günleri siler — durum ucu en çok 31 gün geriye bakar."""
        await self._store.execute(
            f"DELETE FROM {self.call_table} WHERE date < ?", (before.isoformat(),))
        await self._store.execute(
            f"DELETE FROM {self.day_table} WHERE date < ?", (before.isoformat(),))
