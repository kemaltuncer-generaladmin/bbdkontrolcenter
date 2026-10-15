"""Sınıfın DÖNEMİ (akademik yıl) — kural + mevcut sınıflara bir kerelik atama (göç 011).

Kullanıcı (2026-10-14, iş 30): "tüm mevcut sınıflara da 2026-2027 dönemini ata."
Dönem "YYYY-YYYY" biçimindedir (ikinci yıl = birinci + 1) ve 1 Eylül'de döner
(Europe/Istanbul): 2026-10 → "2026-2027", 2027-08 → "2026-2027", 2027-09 → "2027-2028".
Bu kural Akademi'nin müfredat setindekiyle (`academicYearOf`) aynıdır.

    current_year(today)   → "2026-2027"
    parse_year(value)     → "YYYY-YYYY" ya da ValueError (servis GroupError'a çevirir)
    year_choices(today)   → seçicide gösterilecek dönemler (güncel ±)
    YearBackfill.run()    → dönemi BOŞ sınıflara BACKFILL_YEAR yazar (tek işlem)
    YearRollover          → yıl sonu DÖNEM GEÇİŞİ (önizleme, uygulama, geri alma; göç 013)

`YearBackfill` modül açılışında koşar (`module.class_upkeep`); idempotenttir:
yalnız `academic_year` boş (NULL ya da '') satırlara yazar. Yeni kod hiçbir
sınıfı dönemsiz bırakmaz (oluştururken varsayılan güncel dönem, düzenlemede boş
kabul edilmez); boş satır ancak eski sürümün (0.5.13) yazdığı satırdır.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from . import curriculum

#: Kullanıcı kararı: mevcut bütün sınıflar bu döneme atanır.
BACKFILL_YEAR = "2026-2027"
ACTOR = "sistem: sınıf dönemi"

_YEAR = re.compile(r"^(\d{4})-(\d{4})$")


def current_year(today: date) -> str:
    """Günün dönemi — 1 Eylül ve sonrası yeni dönem."""
    start = today.year if today.month >= 9 else today.year - 1
    return f"{start}-{start + 1}"


def parse_year(value: Any) -> str:
    """Gövdedeki dönem → "YYYY-YYYY". Boş, bozuk ya da ardışık olmayan yıl ValueError."""
    text = str(value or "").strip()
    match = _YEAR.match(text)
    if match is None:
        raise ValueError("Dönem “2026-2027” biçiminde olmalı.")
    first, second = int(match.group(1)), int(match.group(2))
    if second != first + 1 or not 2000 <= first <= 2100:
        raise ValueError("Dönemin ikinci yılı birinciden bir fazla olmalı (ör. 2026-2027).")
    return text


def year_choices(today: date, *, extra: list[str] | None = None) -> list[str]:
    """Seçicideki dönemler: önceki iki dönem, güncel, sonraki dönem + verideki diğerleri.

    Yeniden eskiye sıralı ("2027-2028", "2026-2027", …).
    """
    start = int(current_year(today)[:4])
    found = {f"{year}-{year + 1}" for year in range(start - 2, start + 2)}
    for value in extra or []:
        if value and _YEAR.match(str(value)):
            found.add(str(value))
    return sorted(found, reverse=True)


class YearBackfill:
    """Dönemi boş sınıflara `BACKFILL_YEAR` — tek işlem, revizyon +1, `group.changed`."""

    def __init__(self, *, store: Any, groups: Any, log: Any) -> None:
        self._store = store
        #: `GroupService` — sayaç (`_bump`), outbox (`_emit`), yayın (`after_commit`).
        self._groups_service = groups
        self._log = log
        self._groups = store.table("group")
        self._audit = store.table("group_log")

    async def pending(self) -> int:
        row = await self._store.fetch_one(
            f"SELECT COUNT(*) AS n FROM {self._groups} "
            f"WHERE academic_year IS NULL OR academic_year = ''")
        return int((row or {}).get("n") or 0)

    async def run(self, *, year: str = BACKFILL_YEAR, actor: str = ACTOR) -> int:
        """Dönemi boş satırları doldurur; yazılan satır sayısını döner (0 = yapılacak yok)."""
        if await self.pending() == 0:
            return 0
        try:
            return await self._run_in(year=year, actor=actor)
        except _Nothing:
            return 0

    async def _run_in(self, *, year: str, actor: str) -> int:
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        async with self._store.transaction():
            # Sayaç kilidi eşzamanlı koşuları sıralar; sayım İŞLEMİN İÇİNDE yeniden yapılır.
            revision = await self._groups_service._bump()
            count = await self.pending()
            if count == 0:
                # Başka bir koşu bitirdi: boş işlem — revizyon harcanmasın.
                raise _Nothing
            await self._store.execute(
                f"UPDATE {self._groups} SET academic_year = ?, revision = ? "
                f"WHERE academic_year IS NULL OR academic_year = ''", (year, revision))
            await self._store.execute(
                f"INSERT INTO {self._audit} (at, actor, action, group_id, revision, detail) "
                f"VALUES (?, ?, 'year_backfill', '', ?, ?)",
                (stamp, actor[:160], revision,
                 json.dumps({"academicYear": year, "classes": count}, ensure_ascii=False)))
            await self._groups_service._emit(revision)
        await self._groups_service.after_commit(revision)
        self._log.info("mevcut sınıflara dönem atandı", donem=year, sinif=count)
        return count


class _Nothing(Exception):
    """İşlemin içinde görüldü: yapılacak yok — geri al."""


# ====================================================================== DÖNEM GEÇİŞİ
#
# Yıl sonu sihirbazı (iş 37 / İ4, lider kararı 2026-10-15): "sınıfları yeni döneme
# taşı/kopyala, seviye +1, mezun et, müfredat varsayılanları, önizleme + geri
# alınabilir." Eylül'de parça parça (dönem ata, toplu seviye, toplu pasifleştir,
# program kopyala) yapılan iş TEK akıştır:
#
#   preview(payload)  → yazmadan plan: her sınıfın işlemi, yeni adı/seviyesi,
#                       taşınan öğrenciler, mezunlar, seviyesi değişenler, sorunlar
#   apply(payload)    → planı uygular: sınıf tarafı TEK işlem (tek revizyon,
#                       `group.changed`), sonra seviyeler ve mezunların pasifleşmesi;
#                       geri alma verisi `mod_bbd_students_rollover` satırına yazılır
#   undo(id)          → son uygulamayı geri alır (açılan sınıflar silinir, eski
#                       sınıflar ve üyelikler eski haline döner, seviyeler geri yazılır,
#                       mezunlar yeniden etkinleşir)
#
# SINIF İŞLEMLERİ (sınıf başına; varsayılan `default_action`):
#   promote  "Üst seviyeye taşı"  yeni dönemde yeni sınıf (ad önerisi 7-A → 8-A,
#                                 seviye +1), bugünkü üyeler geçiş gününden üye,
#                                 eski sınıf arşivlenir
#   copy     "Aynen kopyala"      yeni dönemde aynı ad + aynı seviye; eski arşivlenir
#   graduate "Mezun et"           eski sınıf arşivlenir, üyeleri "mezun" gerekçesiyle
#                                 pasifleşir (istisna listesindekiler hariç)
#   archive  "Yalnız arşivle"     eski sınıf arşivlenir, öğrenciler etkin kalır
#   keep     "Dokunma"            sınıf olduğu gibi kalır
#
# Yeni ad yeni dönemde ZATEN AÇILMIŞ (aynı tür) bir canlı sınıfa denk gelirse yeni
# sınıf açılmaz, öğrenciler o sınıfa eklenir ("katıl"). Başka bir canlı sınıfla ad
# çakışması sorundur — uygulama reddedilir.
#
# TARİHLER. Geçiş günü E (yeni üyeliklerin ilk günü). Eski üyelikler
# `min(bugün, E - 1)` günü kapanır (arşiv kuralı: o günden sonra başlayan eski
# üyelik kaldırılır). E bugünden en çok 14 gün geride, 60 gün ileride olabilir ve
# hedef dönemin başlangıcından (1 Eylül) en çok 92 gün önce (1 Haziran) olabilir —
# ekim ayında yanlışlıkla yıl atlatılmasın. Önizleme her zaman serbesttir.
#
# SEVİYE. Sınıfın seviyesi şablondur (göç 009): taşınan öğrenci yeni sınıfın
# seviyesini alır (`level_override` açık öğrencide alan ve hedef sınav korunur —
# `classes.ClassLink.apply_level` ile aynı kural). Seviyesi olmayan sınıflardaki
# öğrencilerin kendi seviyesi `bumpOthers` açıkken +1 olur (`next_level`). Yalnız
# kaynak dönemin sınıflarındaki öğrenciler sayılır: yazın doğrudan yeni dönem
# sınıfına kaydedilen öğrencinin seviyesi iki kez artmaz.
#
# MÜFREDAT. Akademi varsayılan müfredatı öğrencinin seviye/alan/hedef sınavından
# türetir (dizin olayı `student.updated`); sınıf müfredatı ve ders programı Akademi
# tarafında "Dönem geçişi kopyası" ile yeni sınıflara aktarılır (`summary.pairs`).

ACTIONS = ("promote", "copy", "graduate", "archive", "keep")
#: Eski sınıfı arşivleyen işlemler.
ARCHIVING = ("promote", "copy", "graduate", "archive")
#: Yeni dönemde sınıf açan (ya da var olana katılan) işlemler.
CREATING = ("promote", "copy")
#: Geçiş günü penceresi (bugüne göre) ve hedef dönemin başından en erken uygulama.
PAST_DAYS = 14
FUTURE_DAYS = 60
EARLIEST_BEFORE_START = 92
#: Mezun sınıflar: LGS (8), YKS (12) ve mezun grubu.
GRADUATING = (8, 12, curriculum.GRADUATE)
ROLLOVER_ACTOR_REASON = "Dönem geçişi"
_NAME_GRADE = re.compile(r"^(\s*)(\d{1,2})(?!\d)(.*)$")


def next_year(year: str) -> str:
    """"2026-2027" → "2027-2028"."""
    first = int(parse_year(year)[:4]) + 1
    return f"{first}-{first + 1}"


def year_start(year: str) -> date:
    """Dönemin ilk günü (1 Eylül)."""
    return date(int(parse_year(year)[:4]), 9, 1)


def _exam_for(grade: int | str | None) -> str | None:
    if grade == 8:
        return "LGS"
    if grade == curriculum.GRADUATE or grade in (11, 12):
        return "YKS"
    return None


def next_level(level: dict[str, Any] | None) -> dict[str, Any] | None:
    """Seviye +1: 5..11 → bir üst, 12 → mezun, mezun → mezun.

    Alan yalnız 10+ / mezunda kalır; LGS 8'den sonra düşer; hedef sınav boşsa
    seviyeden önerilir (8 → LGS, 11/12/mezun → YKS). Seviyesiz → None.
    """
    if not level or level.get("gradeLevel") is None:
        return None
    grade = level["gradeLevel"]
    if grade == curriculum.GRADUATE or (isinstance(grade, int) and grade >= 12):
        new: int | str = curriculum.GRADUATE
    else:
        new = int(grade) + 1
    track = level.get("track") if curriculum.track_allowed(new) else None
    exam = level.get("examTarget")
    if exam == "LGS" and new != 8:
        exam = None
    if exam is None:
        exam = _exam_for(new)
    return {"gradeLevel": new, "track": track, "examTarget": exam}


def next_name(name: str, level: dict[str, Any] | None) -> str:
    """Ad önerisi: baştaki sayı sınıfın seviyesiyse +1 ("7-A" → "8-A", "12-SAY" →
    "Mezun-SAY"); değilse ad aynen kalır ("TYT Matematik")."""
    match = _NAME_GRADE.match(str(name or ""))
    grade = (level or {}).get("gradeLevel")
    if match is None or not isinstance(grade, int) or int(match.group(2)) != grade:
        return str(name or "")
    after = next_level(level)
    token = "Mezun" if after is None or after["gradeLevel"] == curriculum.GRADUATE \
        else str(after["gradeLevel"])
    return f"{match.group(1)}{token}{match.group(3)}".strip()


def default_action(row: dict[str, Any], level: dict[str, Any] | None) -> str:
    """Özel ders ve seviyesiz sınıf → arşivle; 8/12/mezun → mezun et; diğerleri → taşı."""
    if str(row.get("kind") or "group") == "individual" or level is None:
        return "archive"
    if level["gradeLevel"] in GRADUATING:
        return "graduate"
    return "promote"


def _level_columns(level: dict[str, Any] | None) -> dict[str, Any] | None:
    if level is None:
        return None
    return {"grade_level": str(level["gradeLevel"]), "track": level.get("track"),
            "exam_target": level.get("examTarget")}


def _level_after(profile: dict[str, Any] | None, plan: dict[str, Any]) -> dict[str, Any]:
    """Sınıf şablonu öğrenciye yazılınca ne olur — `ClassLink.apply_level` ile AYNI kural."""
    own = curriculum.from_row(profile)
    grade = plan["gradeLevel"]
    track, exam = plan.get("track"), plan.get("examTarget")
    if bool(int((profile or {}).get("level_override") or 0)):
        track = own["track"] if own["track"] is not None else track
        exam = own["examTarget"] if own["examTarget"] is not None else exam
    if not curriculum.track_allowed(grade):
        track = None
    return {"gradeLevel": grade, "track": track, "examTarget": exam}


def _error(message: str, *, status: int = 400, code: str = "validation_error",
           details: dict[str, Any] | None = None) -> Exception:
    from .groups import GroupError  # döngüsel içe aktarma olmasın (groups bu modülü okur)

    return GroupError(message, status=status, code=code, details=details)


def _tr_day(day: date) -> str:
    return day.strftime("%d.%m.%Y")


class YearRollover:
    """Dönem geçişi — önizleme, uygulama, geri alma (modül başlığındaki kurallar)."""

    def __init__(self, *, store: Any, groups: Any, log: Any, sync: Any = None,
                 lifecycle: Any = None) -> None:
        self._store = store
        #: `GroupService` — sayaç, outbox, iç yazma yardımcıları (aynı modül).
        self._g = groups
        self._log = log
        #: `StudentSync` — seviye yazması (önce KM, `student.updated`). Yoksa seviye atlanır.
        self._sync = sync
        #: `Lifecycle` — mezunların pasifleşmesi / geri almada yeniden etkinleşme.
        self._lifecycle = lifecycle
        self._groups = store.table("group")
        self._members = store.table("group_member")
        self._profiles = store.table("profile")
        self._tombstones = store.table("tombstone")
        self._runs = store.table("rollover")

    def _today(self) -> date:
        return self._g._today()

    # ============================================================== okuma

    async def _live_years(self) -> dict[str, int]:
        rows = await self._store.fetch_all(
            f"SELECT academic_year, COUNT(*) AS n FROM {self._groups} WHERE archived_at = '' "
            f"GROUP BY academic_year")
        return {str(row["academic_year"]): int(row["n"]) for row in rows
                if row.get("academic_year") and _YEAR.match(str(row["academic_year"]))}

    async def default_from(self) -> str:
        """Kaynak dönem önerisi: Eylül–Aralık'ta önceki dönemde canlı sınıf varsa o;
        yoksa canlı sınıfı olan en yeni (güncelden ileri olmayan) dönem; o da yoksa güncel."""
        today = self._today()
        current = current_year(today)
        years = await self._live_years()
        previous = f"{int(current[:4]) - 1}-{current[:4]}"
        if today.month >= 9 and previous in years:
            return previous
        candidates = sorted(year for year in years if year <= current)
        return candidates[-1] if candidates else current

    def default_effective(self, to_year: str) -> date:
        """Geçiş günü önerisi: hedef dönemin 1 Eylül'ü pencere içindeyse o, değilse bugün."""
        today = self._today()
        start = year_start(to_year)
        if today - timedelta(days=PAST_DAYS) <= start <= today + timedelta(days=FUTURE_DAYS):
            return start
        return today

    async def overview(self) -> dict[str, Any]:
        """Sihirbazın açılış verisi: öneriler, pencere, dönemler, son geçiş ve geçmiş."""
        today = self._today()
        source = await self.default_from()
        target = next_year(source)
        runs = await self._store.fetch_all(
            f"SELECT * FROM {self._runs} ORDER BY revision DESC, created_at DESC LIMIT 10")
        history = [self._summary(dict(row)) for row in runs]
        latest_applied = next((item for item in history if item["state"] == "applied"), None)
        if latest_applied is not None and history and history[0]["id"] == latest_applied["id"]:
            latest_applied["canUndo"] = True
        return {
            "today": today.isoformat(),
            "currentYear": current_year(today),
            "fromYear": source,
            "toYear": target,
            "effectiveFrom": self.default_effective(target).isoformat(),
            "window": {"earliest": (today - timedelta(days=PAST_DAYS)).isoformat(),
                       "latest": (today + timedelta(days=FUTURE_DAYS)).isoformat()},
            "years": [{"academicYear": year, "classes": count}
                      for year, count in sorted((await self._live_years()).items(), reverse=True)],
            "latest": latest_applied,
            "history": history,
            "revision": await self._g.revision(),
        }

    async def get(self, rollover_id: str) -> dict[str, Any]:
        row = await self._run_row(rollover_id)
        return self._summary(row)

    async def _run_row(self, rollover_id: str) -> dict[str, Any]:
        row = await self._store.fetch_one(
            f"SELECT * FROM {self._runs} WHERE id = ?", (str(rollover_id),))
        if row is None:
            raise _error("Dönem geçişi kaydı bulunamadı.", status=404, code="rollover_not_found")
        return dict(row)

    def _summary(self, row: dict[str, Any]) -> dict[str, Any]:
        try:
            summary = json.loads(str(row.get("summary") or "{}"))
        except json.JSONDecodeError:
            summary = {}
        from .groups import _local_iso

        return {
            "id": str(row["id"]),
            "createdAt": _local_iso(str(row["created_at"])),
            "createdBy": str(row.get("created_by") or ""),
            "fromYear": str(row["from_year"]),
            "toYear": str(row["to_year"]),
            "effectiveFrom": str(row["effective_from"]),
            "state": str(row.get("state") or "applied"),
            "undoneAt": _local_iso(str(row.get("undone_at") or "")),
            "undoneBy": str(row.get("undone_by") or "") or None,
            "counts": summary.get("counts") or {},
            "closeDay": summary.get("closeDay"),
            "pairs": summary.get("pairs") or [],
            "graduation": summary.get("graduation") or None,
            "canUndo": False,
        }

    # ============================================================== plan

    async def preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        plan = await self._plan(payload)
        return plan["public"]

    async def _plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Yazmadan plan. `public` ekrana gider; geri kalanı `apply` içindir."""
        today = self._today()
        try:
            from_year = parse_year(payload.get("fromYear") or await self.default_from())
            to_year = parse_year(payload.get("toYear") or next_year(from_year))
        except ValueError as failure:
            raise _error(str(failure), details={"field": "fromYear"}) from None
        if to_year != next_year(from_year):
            raise _error(f"Hedef dönem {next_year(from_year)} olmalı (bir sonraki dönem).",
                         details={"field": "toYear"})
        effective = self._effective(payload.get("effectiveFrom"), to_year)
        close_day = min(today, effective - timedelta(days=1))
        keep = {str(item).strip() for item in payload.get("keepStudentIds") or []
                if str(item).strip()}
        bump_others = payload.get("bumpOthers", True) is not False
        overrides: dict[str, dict[str, Any]] = {}
        for item in payload.get("items") or []:
            group_id = str((item or {}).get("groupId") or "").strip()
            if group_id:
                overrides[group_id] = dict(item)

        all_rows = [dict(row) for row in await self._store.fetch_all(
            f"SELECT * FROM {self._groups} ORDER BY name_fold, id")]
        rows = [row for row in all_rows
                if not row["archived_at"] and str(row.get("academic_year") or "") == from_year]
        known = {str(row["id"]) for row in rows}
        stray = [group_id for group_id in overrides if group_id not in known]
        if stray:
            raise _error(f"Seçilen sınıf {from_year} döneminin canlı sınıfları arasında değil.",
                         status=404, code="group_not_found", details={"groupIds": stray[:20]})

        members = await self._members_on([str(row["id"]) for row in rows], close_day)
        students = sorted({student for found in members.values() for student in found})
        profiles = await self._profiles_of(students)
        active = {student for student in students
                  if student in profiles and str(profiles[student].get("status") or "active")
                  != "inactive"}

        from .groups import _clean_name, fold, level_of

        problems: list[dict[str, Any]] = []
        classes: list[dict[str, Any]] = []
        for row in rows:
            group_id = str(row["id"])
            level = level_of(row)
            fallback = default_action(row, level)
            wanted = overrides.get(group_id, {})
            action = str(wanted.get("action") or fallback)
            if action not in ACTIONS:
                raise _error("İşlem promote, copy, graduate, archive ya da keep olmalı.",
                             details={"groupId": group_id, "field": "action"})
            entry: dict[str, Any] = {
                "groupId": group_id, "name": str(row["name"]), "kind": str(row["kind"]),
                "classLevel": level, "members": sorted(members.get(group_id, set()) & active),
                "memberCount": len(members.get(group_id, set()) & active),
                "action": action, "defaultAction": fallback, "newName": None,
                "nameSuggested": None, "newLevel": None, "target": None, "targetId": None,
            }
            if action in CREATING:
                if action == "promote" and level is None:
                    problems.append({"groupId": group_id, "code": "level_missing",
                                     "message": f"“{row['name']}” sınıfının seviyesi yok; üst seviyeye "
                                                "taşınamaz. Seviye girin ya da “Aynen kopyala” seçin."})
                suggested = next_name(str(row["name"]), level) if action == "promote" \
                    else str(row["name"])
                entry["nameSuggested"] = suggested
                raw_name = wanted.get("name")
                try:
                    entry["newName"] = _clean_name(raw_name if raw_name else suggested)
                except Exception as failure:  # noqa: BLE001 — GroupError: ad kuralı
                    problems.append({"groupId": group_id, "code": "name_invalid",
                                     "message": f"“{row['name']}”: {failure}"})
                    entry["newName"] = suggested
                entry["newLevel"] = next_level(level) if action == "promote" else level
            classes.append(entry)

        # --------------------------------------------- ad çakışması / var olana katılma
        archived_now = {entry["groupId"] for entry in classes if entry["action"] in ARCHIVING}
        live_by_name: dict[str, dict[str, Any]] = {}
        for row in all_rows:
            if not row["archived_at"] and str(row["id"]) not in archived_now:
                live_by_name.setdefault(fold(str(row["name"])), row)
        creating: dict[str, list[dict[str, Any]]] = {}
        for entry in classes:
            if entry["action"] not in CREATING or not entry["newName"]:
                continue
            key = fold(entry["newName"])
            existing = live_by_name.get(key)
            if existing is not None:
                same = (str(existing.get("academic_year") or "") == to_year
                        and str(existing["kind"]) == entry["kind"])
                if same:
                    entry["target"] = "join"
                    entry["targetId"] = str(existing["id"])
                    target_level = level_of(existing)
                    if target_level is not None:
                        entry["newLevel"] = target_level
                    continue
                problems.append({"groupId": entry["groupId"], "code": "name_taken",
                                 "message": f"“{entry['newName']}” adında canlı bir sınıf var "
                                            f"({existing.get('academic_year') or 'dönemsiz'}). "
                                            "Yeni adı değiştirin ya da o sınıfı arşivleyin."})
                continue
            entry["target"] = "create"
            creating.setdefault(key, []).append(entry)
        for key, same_name in creating.items():
            if len(same_name) > 1:
                names = ", ".join(f"“{entry['name']}”" for entry in same_name)
                for entry in same_name:
                    problems.append({"groupId": entry["groupId"], "code": "name_duplicate",
                                     "message": f"{names} aynı ada (“{entry['newName']}”) taşınıyor; "
                                                "adlardan birini değiştirin."})

        # ------------------------------------------------------------ mezunlar
        graduating: dict[str, list[str]] = {}
        for entry in classes:
            if entry["action"] == "graduate":
                for student in entry["members"]:
                    graduating.setdefault(student, []).append(entry["name"])
        graduates = sorted(student for student in graduating if student not in keep)
        kept = sorted(student for student in graduating if student in keep)
        gone = set(graduates)
        if graduates and self._lifecycle is None:
            problems.append({"groupId": None, "code": "lifecycle_missing",
                             "message": "Öğrenci pasifleştirme bu kurulumda yok; “Mezun et” yerine "
                                        "“Yalnız arşivle” seçin."})

        # ------------------------------------------------- taşınanlar ve seviyeler
        notes: list[str] = []
        level_plan: dict[str, tuple[dict[str, Any], str]] = {}
        moved = 0
        for entry in classes:
            if entry["action"] not in CREATING:
                continue
            entry["moving"] = [student for student in entry["members"] if student not in gone]
            entry["leaving"] = [student for student in entry["members"] if student in gone]
            moved += len(entry["moving"])
            if entry["kind"] == "individual" and len(entry["moving"]) > 1:
                problems.append({"groupId": entry["groupId"], "code": "individual_single_member",
                                 "message": f"“{entry['name']}” özel ders sınıfında birden çok öğrenci var."})
            if entry["newLevel"] is None:
                continue
            for student in entry["moving"]:
                if student in level_plan and level_plan[student][0] != entry["newLevel"]:
                    notes.append(f"{student}: iki farklı seviyeli sınıfa taşınıyor; "
                                 f"“{level_plan[student][1]}” sınıfının seviyesi geçerli.")
                    continue
                level_plan.setdefault(student, (entry["newLevel"], entry["newName"] or entry["name"]))
        changes: list[dict[str, Any]] = []
        for student in sorted(active - gone):
            profile = profiles.get(student)
            before = curriculum.from_row(profile)
            if student in level_plan:
                after = _level_after(profile, level_plan[student][0])
                source = "class"
            elif bump_others:
                bumped = next_level(before)
                if bumped is None:
                    continue
                after = bumped
                source = "bump"
            else:
                continue
            if after != before:
                changes.append({"studentId": student, "before": before, "after": after,
                                "source": source})
        for entry in classes:
            if entry["action"] == "graduate":
                entry["leaving"] = [student for student in entry["members"] if student in gone]
            entry.pop("members", None)

        blocked = self._blocked(effective, to_year, classes, problems)
        public = {
            "fromYear": from_year, "toYear": to_year,
            "effectiveFrom": effective.isoformat(), "closeDay": close_day.isoformat(),
            "revision": await self._g.revision(),
            "classes": [{key: value for key, value in entry.items()
                         if key not in ("moving", "leaving")}
                        | {"moving": len(entry.get("moving", [])),
                           "leaving": len(entry.get("leaving", []))}
                        for entry in classes],
            "graduates": [{"studentId": student, "classes": graduating[student]}
                          for student in graduates],
            "keptGraduates": [{"studentId": student, "classes": graduating[student]}
                              for student in kept],
            "levelChanges": changes,
            "problems": problems,
            "notes": notes,
            "blocked": blocked,
            "counts": {
                "classes": len(classes),
                "created": sum(1 for entry in classes if entry["target"] == "create"),
                "joined": sum(1 for entry in classes if entry["target"] == "join"),
                "archived": sum(1 for entry in classes if entry["action"] in ARCHIVING),
                "kept": sum(1 for entry in classes if entry["action"] == "keep"),
                "moved": moved,
                "graduates": len(graduates),
                "levelChanges": len(changes),
            },
        }
        return {"public": public, "classes": classes, "graduates": graduates,
                "changes": changes, "effective": effective, "closeDay": close_day,
                "fromYear": from_year, "toYear": to_year, "blocked": blocked}

    def _effective(self, value: Any, to_year: str) -> date:
        if value in (None, ""):
            return self.default_effective(to_year)
        from .groups import parse_day

        return parse_day(value, field="effectiveFrom")

    def _blocked(self, effective: date, to_year: str, classes: list[dict[str, Any]],
                 problems: list[dict[str, Any]]) -> str | None:
        """Uygulamayı engelleyen ilk sebep (önizleme yine gösterilir) ya da None."""
        today = self._today()
        earliest = year_start(to_year) - timedelta(days=EARLIEST_BEFORE_START)
        if effective < earliest:
            return (f"{to_year} dönemine geçiş en erken {_tr_day(earliest)} gününden itibaren "
                    "uygulanabilir. Önizleme şimdiden yapılabilir.")
        if effective >= year_start(next_year(to_year)):
            return f"Geçiş günü {to_year} dönemi içinde olmalı."
        if effective < today - timedelta(days=PAST_DAYS):
            return f"Geçiş günü en çok {PAST_DAYS} gün geride olabilir."
        if effective > today + timedelta(days=FUTURE_DAYS):
            return f"Geçiş günü en çok {FUTURE_DAYS} gün ileride olabilir."
        if not any(entry["action"] != "keep" for entry in classes):
            return "Uygulanacak değişiklik yok: bütün sınıflar “Dokunma”da."
        if problems:
            return "Önce sorunları giderin."
        return None

    async def _members_on(self, group_ids: list[str], day: date) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        iso_day = day.isoformat()
        for start in range(0, len(group_ids), 500):
            chunk = group_ids[start:start + 500]
            if not chunk:
                continue
            marks = ", ".join("?" for _ in chunk)
            for row in await self._store.fetch_all(
                    f"SELECT group_id, kantin_id FROM {self._members} WHERE group_id IN ({marks}) "
                    f"AND valid_from <= ? AND (valid_to = '' OR valid_to >= ?)",
                    (*chunk, iso_day, iso_day)):
                out.setdefault(str(row["group_id"]), set()).add(str(row["kantin_id"]))
        return out

    async def _profiles_of(self, students: list[str]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for start in range(0, len(students), 500):
            chunk = students[start:start + 500]
            if not chunk:
                continue
            marks = ", ".join("?" for _ in chunk)
            for row in await self._store.fetch_all(
                    f"SELECT * FROM {self._profiles} WHERE kantin_id IN ({marks})", tuple(chunk)):
                out[str(row["kantin_id"])] = dict(row)
        return out

    # ============================================================ uygulama

    async def apply(self, payload: dict[str, Any], *, actor: str) -> dict[str, Any]:
        """Planı uygular. Sınıf tarafı tek işlem; seviye ve pasifleştirme commit'ten sonra
        (her öğrenci kendi olayıyla). Önizlemeden sonra sınıflar değiştiyse 409."""
        plan = await self._plan(payload)
        if plan["blocked"]:
            raise _error(plan["blocked"], status=409, code="rollover_blocked",
                         details={"problems": plan["public"]["problems"][:20]})
        # Önizlemenin revizyonu (ekran gönderir); yoksa planın kendi revizyonu: plan ile yazma
        # arasına başka bir sınıf yazması girerse plan bayattır.
        expected = payload.get("expectedRevision")
        if expected is None:
            expected = plan["public"]["revision"]
        to_year, effective, close_day = plan["toYear"], plan["effective"], plan["closeDay"]
        reason = f"{ROLLOVER_ACTOR_REASON} {to_year}"
        rollover_id = str(uuid.uuid4())
        from .groups import _now

        stamp = _now()
        snapshot: dict[str, Any] = {"archived": [], "created": [], "joined": [],
                                    "levels": [], "graduation": None}
        pairs: list[dict[str, Any]] = []
        touched: set[str] = set()
        g = self._g
        async with self._store.transaction():
            revision = await g._bump()
            if int(expected) + 1 != revision:
                raise _error("Önizlemeden sonra sınıflar değişti; önizlemeyi yenileyip tekrar "
                             "deneyin.", status=409, code="rollover_stale")
            for entry in plan["classes"]:
                group_id = entry["groupId"]
                if entry["action"] not in ARCHIVING:
                    continue
                before = {str(row["id"]): dict(row) for row in await g._member_rows([group_id])}
                await g._archive_in(group_id, actor=actor, revision=revision, today=close_day)
                after = {str(row["id"]): dict(row) for row in await g._member_rows([group_id])}
                touched.update(str(row["kantin_id"]) for row in before.values())
                row = await g._group_row(group_id)
                snapshot["archived"].append({
                    "id": group_id, "name": entry["name"], "archivedAt": str(row["archived_at"]),
                    "closeDay": close_day.isoformat(),
                    "closed": [{"id": member_id, "validTo": str(old["valid_to"] or "")}
                               for member_id, old in before.items()
                               if member_id in after
                               and str(after[member_id]["valid_to"]) != str(old["valid_to"])],
                    "dropped": [old for member_id, old in before.items() if member_id not in after],
                })
                await g._write_audit(actor=actor, action="rollover", group_id=group_id,
                                     revision=revision,
                                     detail={"toYear": to_year, "action": entry["action"],
                                             "newName": entry["newName"],
                                             "effectiveFrom": effective.isoformat(),
                                             "reason": reason})
            for entry in plan["classes"]:
                if entry["action"] not in CREATING:
                    continue
                moving = entry["moving"]
                if entry["target"] == "join":
                    target_id = entry["targetId"]
                    target = await g._group_row(target_id)
                    existing = await g._member_rows([target_id])
                    fresh = [student for student in moving if not any(
                        str(member["kantin_id"]) == student
                        and (str(member["valid_to"] or "") == ""
                             or str(member["valid_to"]) >= effective.isoformat())
                        for member in existing)]
                    g._check_members(str(target["kind"]), existing + [
                        {"kantin_id": student, "valid_from": effective.isoformat(),
                         "valid_to": ""} for student in fresh])
                    inserted = [await g._insert_member(target_id, student, effective, None,
                                                       reason=reason, actor=actor,
                                                       revision=revision, stamp=stamp)
                                for student in fresh]
                    await g._touch_group(target_id, actor=actor, revision=revision)
                    await g._write_audit(actor=actor, action="rollover_join", group_id=target_id,
                                         revision=revision,
                                         detail={"from": entry["groupId"], "students": fresh,
                                                 "validFrom": effective.isoformat(),
                                                 "reason": reason})
                    snapshot["joined"].append({"id": target_id, "memberIds": inserted})
                    pairs.append(self._pair(entry, target_id, str(target["name"]), to_year,
                                            joined=True, students=len(fresh)))
                    touched.update(fresh)
                    continue
                level = entry["newLevel"]
                group_row = next(row for row in await self._store.fetch_all(
                    f"SELECT * FROM {self._groups} WHERE id = ?", (entry["groupId"],)))
                new_id = await g._insert_group(
                    name=entry["newName"], kind=entry["kind"],
                    call_name=next_name(str(group_row["call_name"] or ""), entry["classLevel"])
                    if entry["action"] == "promote" else str(group_row["call_name"] or ""),
                    note=str(group_row["note"] or ""), level=_level_columns(level),
                    year=to_year, actor=actor, revision=revision, stamp=stamp)
                for student in moving:
                    await g._insert_member(new_id, student, effective, None, reason=reason,
                                           actor=actor, revision=revision, stamp=stamp)
                await g._write_audit(actor=actor, action="rollover_create", group_id=new_id,
                                     revision=revision,
                                     detail={"from": entry["groupId"], "fromName": entry["name"],
                                             "name": entry["newName"], "academicYear": to_year,
                                             "students": len(moving),
                                             "validFrom": effective.isoformat()})
                snapshot["created"].append({"id": new_id, "name": entry["newName"]})
                pairs.append(self._pair(entry, new_id, entry["newName"], to_year, joined=False,
                                        students=len(moving)))
                touched.update(moving)
            await g._touched_students(touched)
            counts = dict(plan["public"]["counts"])
            await self._store.execute(
                f"INSERT INTO {self._runs} (id, created_at, created_by, from_year, to_year, "
                f"effective_from, state, revision, summary, snapshot) "
                f"VALUES (?, ?, ?, ?, ?, ?, 'applied', ?, ?, ?)",
                (rollover_id, stamp, actor[:160], plan["fromYear"], to_year,
                 effective.isoformat(), revision,
                 json.dumps({"counts": counts, "closeDay": close_day.isoformat(),
                             "pairs": pairs}, ensure_ascii=False),
                 json.dumps(snapshot, ensure_ascii=False)))
            await g._emit(revision)
        await g._changed(revision)

        levels = await self._write_levels(plan["changes"], actor=actor)
        snapshot["levels"] = levels
        graduation = await self._graduate(plan["graduates"], effective=effective,
                                          to_year=to_year, actor=actor)
        snapshot["graduation"] = graduation
        counts["levelChanges"] = len(levels)
        await self._store.execute(
            f"UPDATE {self._runs} SET snapshot = ?, summary = ? WHERE id = ?",
            (json.dumps(snapshot, ensure_ascii=False),
             json.dumps({"counts": counts, "closeDay": close_day.isoformat(), "pairs": pairs,
                         "graduation": self._graduation_counts(graduation)},
                        ensure_ascii=False), rollover_id))
        self._log.info("dönem geçişi uygulandı", kaynak=plan["fromYear"], hedef=to_year,
                       sinif=counts["classes"], mezun=counts["graduates"], actor=actor)
        result = await self.get(rollover_id)
        result["canUndo"] = True
        return result

    @staticmethod
    def _pair(entry: dict[str, Any], to_id: str, to_name: str, to_year: str, *,
              joined: bool, students: int) -> dict[str, Any]:
        return {"fromId": entry["groupId"], "fromName": entry["name"], "action": entry["action"],
                "toId": to_id, "toName": to_name, "toYear": to_year, "joined": joined,
                "students": students}

    @staticmethod
    def _graduation_counts(graduation: dict[str, Any] | None) -> dict[str, int] | None:
        if not graduation:
            return None
        return {key: len(graduation.get(key) or []) for key in ("deactivated", "scheduled", "failed")}

    async def _write_levels(self, changes: list[dict[str, Any]], *, actor: str) -> list[dict[str, Any]]:
        """Seviye/alan/hedef sınav — öğrenci başına önce KM + `student.updated` (sınıf
        şablonunun öğrenciye yazılması gibi). Yazılanların önceki/sonraki değeri döner."""
        if self._sync is None or not changes:
            return []
        from .sync import Event

        written: list[dict[str, Any]] = []
        for change in changes:
            student = change["studentId"]
            try:
                profile = await self._sync.profile(student)
                before = curriculum.from_row(profile)
                after = change["after"]
                fields = [key for key in ("gradeLevel", "track", "examTarget")
                          if before[key] != after[key]]
                if not fields:
                    continue
                await self._sync.write(student, columns={
                    "grade_level": None if after["gradeLevel"] is None else str(after["gradeLevel"]),
                    "track": after["track"], "exam_target": after["examTarget"],
                }, events=[Event("student.updated", fields)],
                    log={"action": "rollover_level", "actor": actor})
                written.append({"studentId": student, "before": before, "after": after})
            except Exception as failure:  # noqa: BLE001 — biri patlarsa diğerleri sürsün
                self._log.warning("dönem geçişi seviyesi yazılamadı", student=student,
                                  error=str(failure))
        return written

    async def _graduate(self, students: list[str], *, effective: date, to_year: str,
                        actor: str) -> dict[str, Any] | None:
        if not students or self._lifecycle is None:
            return None
        today = self._today()
        out: dict[str, Any] = {"deactivated": [], "scheduled": [], "failed": []}
        # Toplu pasifleştirmenin istek sınırı (lifecycle.BULK_MAX = 2000) — parça parça.
        for start in range(0, len(students), 2000):
            chunk = students[start:start + 2000]
            try:
                done = await self._lifecycle.bulk_deactivate(
                    chunk, reason="graduated",
                    effective_at=effective.isoformat() if effective > today else None,
                    note=f"{ROLLOVER_ACTOR_REASON} {to_year}", actor=actor)
            except Exception as failure:  # noqa: BLE001 — sınıf tarafı tamam; sonuç kayda yazılır
                self._log.warning("dönem geçişi mezunları pasifleştirilemedi", error=str(failure))
                done = {"failed": [{"studentId": student, "error": str(failure)}
                                   for student in chunk]}
            for key in out:
                out[key] += list(done.get(key) or [])
        return out

    # ========================================================== geri alma

    async def undo(self, rollover_id: str, *, actor: str) -> dict[str, Any]:
        """SON uygulamayı geri alır. Açılan sınıflar KALICI silinir (Akademi'ye mezar
        taşı), var olana eklenen üyelikler kaldırılır, arşivlenen sınıflar ve geçişin
        kapattığı/kaldırdığı üyelikler eski haline döner; sonra seviyeler (geçişten sonra
        elle değişmemişse) geri yazılır ve geçişin pasifleştirdiği mezunlar yeniden etkinleşir."""
        row = await self._run_row(rollover_id)
        if str(row.get("state") or "") != "applied":
            raise _error("Bu dönem geçişi zaten geri alınmış.", status=409,
                         code="rollover_not_applied")
        newest = await self._store.fetch_one(
            f"SELECT id FROM {self._runs} ORDER BY revision DESC, created_at DESC LIMIT 1")
        if newest is None or str(newest["id"]) != str(row["id"]):
            raise _error("Yalnız en son dönem geçişi geri alınabilir.", status=409,
                         code="rollover_not_latest")
        try:
            snapshot = json.loads(str(row.get("snapshot") or "{}"))
        except json.JSONDecodeError:
            snapshot = {}
        g = self._g
        from .groups import ORIGIN_GROUP, _now, fold

        stamp = _now()
        reason = f"{ROLLOVER_ACTOR_REASON} geri alındı ({row['to_year']})"
        notes: list[str] = []
        purged: list[str] = []
        restored = 0
        touched: set[str] = set()
        async with self._store.transaction():
            revision = await g._bump()
            for created in snapshot.get("created") or []:
                current = await self._store.fetch_one(
                    f"SELECT * FROM {self._groups} WHERE id = ?", (str(created["id"]),))
                if current is None:
                    notes.append(f"“{created['name']}” zaten silinmiş.")
                    continue
                students, _rows = await g._purge_in(dict(current), reason=reason, actor=actor,
                                                    revision=revision, action="rollover_undo")
                touched.update(students)
                purged.append(str(created["id"]))
            for joined in snapshot.get("joined") or []:
                for member_id in joined.get("memberIds") or []:
                    member = await self._store.fetch_one(
                        f"SELECT kantin_id FROM {self._members} WHERE id = ?", (str(member_id),))
                    if member is None:
                        continue
                    touched.add(str(member["kantin_id"]))
                    await self._store.execute(
                        f"DELETE FROM {self._members} WHERE id = ?", (str(member_id),))
                if await self._store.fetch_one(
                        f"SELECT id FROM {self._groups} WHERE id = ?", (str(joined["id"]),)):
                    await g._touch_group(str(joined["id"]), actor=actor, revision=revision)
                    await g._write_audit(actor=actor, action="rollover_undo",
                                         group_id=str(joined["id"]), revision=revision,
                                         detail={"removedMembers": len(joined.get("memberIds") or []),
                                                 "reason": reason})
            for archived in snapshot.get("archived") or []:
                group_id = str(archived["id"])
                current = await self._store.fetch_one(
                    f"SELECT * FROM {self._groups} WHERE id = ?", (group_id,))
                if current is None:
                    notes.append(f"“{archived['name']}” sonradan kalıcı silinmiş; geri açılamadı.")
                    continue
                if str(current["archived_at"]) != str(archived["archivedAt"]):
                    notes.append(f"“{archived['name']}” geçişten sonra değişmiş; olduğu gibi bırakıldı.")
                    continue
                taken = await self._store.fetch_one(
                    f"SELECT id FROM {self._groups} WHERE name_fold = ? AND archived_at = '' "
                    f"AND id <> ?", (fold(str(current["name"])), group_id))
                if taken is not None:
                    notes.append(f"“{archived['name']}” adında canlı bir sınıf açılmış; sınıf "
                                 "arşivde bırakıldı.")
                    continue
                for closed in archived.get("closed") or []:
                    await self._store.execute(
                        f"UPDATE {self._members} SET valid_to = ?, updated_at = ?, updated_by = ?, "
                        f"revision = ? WHERE id = ? AND valid_to = ?",
                        (str(closed["validTo"] or ""), stamp, actor, revision, str(closed["id"]),
                         str(archived["closeDay"])))
                for dropped in archived.get("dropped") or []:
                    exists = await self._store.fetch_one(
                        f"SELECT id FROM {self._members} WHERE id = ?", (str(dropped["id"]),))
                    if exists is not None:
                        continue
                    await self._store.execute(
                        f"INSERT INTO {self._members} (id, group_id, kantin_id, valid_from, "
                        f"valid_to, reason, created_at, created_by, updated_at, updated_by, "
                        f"revision, origin) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (str(dropped["id"]), group_id, str(dropped["kantin_id"]),
                         str(dropped["valid_from"]), str(dropped.get("valid_to") or ""),
                         str(dropped.get("reason") or ""), str(dropped.get("created_at") or stamp),
                         str(dropped.get("created_by") or actor), stamp, actor, revision,
                         str(dropped.get("origin") or ORIGIN_GROUP)))
                g._check_members(str(current["kind"]), await g._member_rows([group_id]))
                await g._touch_group(group_id, actor=actor, revision=revision,
                                     extra={"archived_at": ""})
                await g._write_audit(actor=actor, action="rollover_undo", group_id=group_id,
                                     revision=revision, detail={"restored": True, "reason": reason})
                touched.update(str(member["kantin_id"])
                               for member in await g._member_rows([group_id]))
                restored += 1
            await g._touched_students(touched)
            await self._store.execute(
                f"UPDATE {self._runs} SET state = 'undone', undone_at = ?, undone_by = ?, "
                f"undo_revision = ? WHERE id = ?", (stamp, actor[:160], revision, str(row["id"])))
            await g._emit(revision)
        await g._changed(revision)
        if g._deleted is not None:
            for group_id in purged:
                try:
                    await g._deleted(group_id, revision)
                except Exception as failure:  # noqa: BLE001 — geri alma tamam (K7)
                    self._log.warning("sınıf silme olayı yayınlanamadı", group=group_id,
                                      error=str(failure))
        levels = await self._revert_levels(snapshot.get("levels") or [], actor=actor, notes=notes)
        reactivated = await self._reactivate(snapshot.get("graduation") or {}, actor=actor,
                                             notes=notes)
        self._log.info("dönem geçişi geri alındı", hedef=row["to_year"], silinen=len(purged),
                       geri_acilan=restored, actor=actor)
        result = await self.get(str(row["id"]))
        result["undo"] = {"deletedClasses": len(purged), "restoredClasses": restored,
                          "levelsReverted": levels, "reactivated": reactivated, "notes": notes}
        return result

    async def _revert_levels(self, levels: list[dict[str, Any]], *, actor: str,
                             notes: list[str]) -> int:
        if self._sync is None or not levels:
            return 0
        from .sync import Event

        reverted = skipped = 0
        for change in levels:
            student = str(change["studentId"])
            try:
                profile = await self._sync.profile(student)
                if profile is None:
                    continue
                now = curriculum.from_row(profile)
                if now != change["after"]:
                    skipped += 1
                    continue
                before = change["before"]
                fields = [key for key in ("gradeLevel", "track", "examTarget")
                          if now[key] != before[key]]
                await self._sync.write(student, columns={
                    "grade_level": None if before["gradeLevel"] is None else str(before["gradeLevel"]),
                    "track": before["track"], "exam_target": before["examTarget"],
                }, events=[Event("student.updated", fields)],
                    log={"action": "rollover_level_undo", "actor": actor})
                reverted += 1
            except Exception as failure:  # noqa: BLE001
                self._log.warning("dönem geçişi seviyesi geri yazılamadı", student=student,
                                  error=str(failure))
        if skipped:
            notes.append(f"{skipped} öğrencinin seviyesi geçişten sonra elle değiştiği için "
                         "geri yazılmadı.")
        return reverted

    async def _reactivate(self, graduation: dict[str, Any], *, actor: str,
                          notes: list[str]) -> int:
        ids = [str(item) for item in (graduation.get("deactivated") or [])
               + (graduation.get("scheduled") or [])]
        if not ids or self._lifecycle is None:
            return 0
        done = 0
        for student in ids:
            profile = await self._store.fetch_one(
                f"SELECT status, status_reason, status_effective_at FROM {self._profiles} "
                f"WHERE kantin_id = ?", (student,))
            if profile is None:
                continue
            inactive = str(profile.get("status") or "active") == "inactive"
            scheduled = bool(profile.get("status_effective_at"))
            if str(profile.get("status_reason") or "") != "graduated" or not (inactive or scheduled):
                continue
            try:
                await self._lifecycle.reactivate(student, actor=actor)
                done += 1
            except Exception as failure:  # noqa: BLE001
                self._log.warning("mezun yeniden etkinleştirilemedi", student=student,
                                  error=str(failure))
        if done:
            notes.append(f"{done} mezun yeniden etkinleştirildi; başka sınıflardaki üyelikleri "
                         "kendiliğinden açılmaz.")
        return done
