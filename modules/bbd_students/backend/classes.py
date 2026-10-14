"""Sınıf üyeliği → öğrencinin türetilmiş alanları (SINIF = GRUP, göç 009).

Tek kaynak üyelik tablosudur (`mod_bbd_students_group_member`). Öğrenci
satırında iki TÜRETİLMİŞ kopya durur:

    class_ids   öğrencinin BUGÜN geçerli sınıf kimlikleri (arşivsiz sınıflar;
                virgülle, sıralı) — dizinin `classes` alanı ve "değişti mi"
                denetimi bundan
    class_name  ESKİ tekil sınıf adı — yalnız geriye uyum (eski Akademi dizinden
                `className` okuyup `km_class` grubu kurar). Değeri: bugün geçerli,
                `legacy_class_name` taşıyan (eski sınıftan dönüşmüş) üyeliklerin
                ad sırasıyla İLKİNİN `legacy_class_name`'i; yoksa '' (sözleşme
                km-ogrenci-gruplari-ve-zil §2.6).

Değişen her öğrenci için ÖNCE-KM yazma yolundan (`StudentSync.write`) tek
işlemde profil + öğrenci revizyonu + `student.updated` (`["classes"]`,
eski ad da değiştiyse `"className"`) yazılır; dizin `since` artımlı okumada
öğrenciyi görür. `refresh` grup yazmasının AÇIK İŞLEMİNE katılır.

BİRLEŞTİRME ÖNCESİ (ya da geri alındıysa) `class_name` ana sınıf olarak tek
kaynaktır ve DOKUNULMAZ; yalnız `class_ids` güncellenir.

Tarihli üyelik gece yarısı kendiliğinden yürürlüğe girer/çıkar: `reconcile_all`
uzlaştırma turunda günde bir kez bütün öğrencileri karşılaştırır, yalnız
değişeni yazar.

SEVİYE. Öğrencinin seviye/alan/hedef sınavı ÖĞRENCİDE durur; sınıfın seviyesi
şablondur: `apply_level` onu öğrencinin kendi sütunlarına yazar.
`level_override` açık öğrencide yalnız seviye yazılır, kendi alanı ve hedef
sınavı korunur (göç 008'deki istisnanın anlamı).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date
from typing import Any

from . import curriculum
from .groups import fold, today_local
from .sync import Event, StudentSync

#: Tek sorguda en çok bu kadar yer tutucu (SQLite sınırının altında).
CHUNK = 500


def _ids_text(ids: Iterable[str]) -> str:
    return ",".join(sorted(set(ids)))


class ClassLink:
    """Üyelik değişikliğini öğrenci kaydına yansıtır; sınıf seviyesini öğrenciye yazar."""

    def __init__(self, *, store: Any, sync: StudentSync, log: Any,
                 today: Callable[[], date] = today_local) -> None:
        self._store = store
        self._sync = sync
        self._log = log
        self._today = today
        self._groups = store.table("group")
        self._members = store.table("group_member")
        self._profiles = store.table("profile")
        self._tombstones = store.table("tombstone")
        self._merge = store.table("class_merge")
        #: `reconcile_all`'ın en son koştuğu yerel gün (günde bir kez).
        self._reconciled: date | None = None

    # ================================================================ okuma

    async def _live_groups(self) -> dict[str, dict[str, Any]]:
        rows = await self._store.fetch_all(
            f"SELECT id, name, origin, legacy_class_name FROM {self._groups} "
            f"WHERE archived_at = ''")
        return {str(row["id"]): dict(row) for row in rows}

    async def _current_rows(self, students: list[str] | None) -> list[dict[str, Any]]:
        day = self._today().isoformat()
        base = (f"SELECT group_id, kantin_id, valid_from, origin FROM {self._members} "
                f"WHERE valid_from <= ? AND (valid_to = '' OR valid_to >= ?)")
        if students is None:
            return await self._store.fetch_all(base, (day, day))
        rows: list[dict[str, Any]] = []
        for start in range(0, len(students), CHUNK):
            chunk = students[start:start + CHUNK]
            marks = ", ".join("?" for _ in chunk)
            rows += await self._store.fetch_all(
                f"{base} AND kantin_id IN ({marks})", (day, day, *chunk))
        return rows

    async def derive(self, students: list[str] | None = None) -> dict[str, tuple[str, str]]:
        """Öğrenci → (`class_ids`, eski `class_name`) — BUGÜNÜN üyeliklerinden.

        `students=None` bütün üyeleri döner (üyeliği olmayan öğrenci listede yoktur).
        """
        groups = await self._live_groups()
        rows = await self._current_rows(students)
        ids: dict[str, set[str]] = {}
        legacy: dict[str, tuple[str, str, str]] = {}
        for row in rows:
            group = groups.get(str(row["group_id"]))
            if group is None:
                continue
            student = str(row["kantin_id"])
            ids.setdefault(student, set()).add(str(group["id"]))
            name = str(group.get("legacy_class_name") or "")
            if name:
                # Ad sırasıyla ilki (Türkçe katlamalı; eşitlikte kimlik — kararlı).
                key = (fold(name), name, str(group["id"]))
                if student not in legacy or key < legacy[student]:
                    legacy[student] = key
        out = {student: (_ids_text(found), legacy[student][1] if student in legacy else "")
               for student, found in ids.items()}
        for student in students or []:
            out.setdefault(student, ("", ""))
        return out

    async def merged(self) -> bool:
        """Sınıf birleştirmesi bitti mi? Bitmediyse eski `class_name` tek kaynaktır."""
        row = await self._store.fetch_one(f"SELECT state FROM {self._merge} WHERE id = 1")
        return row is not None and str(row["state"] or "") == "done"

    async def class_names(self) -> dict[str, str]:
        """Canlı sınıf kimliği → ad (dizin `classes[]` için; tek sorgu)."""
        return {key: str(value["name"]) for key, value in (await self._live_groups()).items()}

    # ================================================================ yazma

    async def refresh(self, students: Iterable[str], *, force: bool = False) -> int:
        """Üyeliği değişen öğrencilerin türetilmiş alanları + `student.updated`.

        Grup yazmasının AÇIK İŞLEMİNE katılır (aynı commit). `force`: alan
        değişmese de olay yaz (sınıfın adı değişti → dizindeki `classes[].name`).
        Silinmiş (mezar taşlı) öğrenci atlanır. Yazılan öğrenci sayısını döner.
        """
        wanted = sorted({str(student) for student in students if student})
        if not wanted:
            return 0
        derived = await self.derive(wanted)
        profiles = await self._profiles_of(wanted)
        gone = await self._gone(wanted)
        legacy = await self.merged()
        written = 0
        for student in wanted:
            if student in gone:
                continue
            written += await self._write(student, profiles.get(student), derived[student],
                                         force=force, legacy=legacy)
        return written

    async def reconcile_all(self, *, force_day: bool = False) -> int:
        """Gece yarısı yürürlüğe giren/çıkan üyelikler — günde bir kez, yalnız değişen yazılır."""
        today = self._today()
        if self._reconciled == today and not force_day:
            return 0
        derived = await self.derive(None)
        rows = await self._store.fetch_all(
            f"SELECT kantin_id, class_ids, class_name FROM {self._profiles}")
        profiles = {str(row["kantin_id"]): dict(row) for row in rows}
        gone = {str(row["kantin_id"]) for row in await self._store.fetch_all(
            f"SELECT kantin_id FROM {self._tombstones}")}
        legacy = await self.merged()
        written = 0
        for student in sorted(set(derived) | {key for key, row in profiles.items()
                                              if row.get("class_ids")}):
            if student in gone:
                continue
            value = derived.get(student, ("", ""))
            written += await self._write(student, profiles.get(student), value, legacy=legacy)
        self._reconciled = today
        if written:
            self._log.info("sınıf üyelikleri günlük uzlaştırma", ogrenci=written)
        return written

    async def _profiles_of(self, students: list[str]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for start in range(0, len(students), CHUNK):
            chunk = students[start:start + CHUNK]
            marks = ", ".join("?" for _ in chunk)
            for row in await self._store.fetch_all(
                    f"SELECT * FROM {self._profiles} WHERE kantin_id IN ({marks})",
                    tuple(chunk)):
                out[str(row["kantin_id"])] = dict(row)
        return out

    async def _gone(self, students: list[str]) -> set[str]:
        out: set[str] = set()
        for start in range(0, len(students), CHUNK):
            chunk = students[start:start + CHUNK]
            marks = ", ".join("?" for _ in chunk)
            out |= {str(row["kantin_id"]) for row in await self._store.fetch_all(
                f"SELECT kantin_id FROM {self._tombstones} WHERE kantin_id IN ({marks})",
                tuple(chunk))}
        return out

    async def _write(self, student: str, profile: dict[str, Any] | None,
                     value: tuple[str, str], *, force: bool = False,
                     legacy: bool = True) -> int:
        class_ids, class_name = value
        before_ids = str((profile or {}).get("class_ids") or "")
        before_name = str((profile or {}).get("class_name") or "")
        if profile is None and not class_ids and not force:
            return 0
        columns: dict[str, Any] = {}
        fields: list[str] = []
        if class_ids != before_ids:
            columns["class_ids"] = class_ids
            fields.append("classes")
        if legacy and class_name != before_name:
            columns["class_name"] = class_name
            fields.append("className")
        if force and "classes" not in fields:
            fields.append("classes")
        if not fields:
            return 0
        await self._sync.write(student, columns=columns, events=[Event("student.updated", fields)])
        return 1

    async def apply_level(self, students: list[str], plan: dict[str, Any]) -> int:
        """Sınıfın seviyesini öğrencilerin KENDİ seviyesine yazar; değişene `student.updated`.

        `level_override` açık öğrencide yalnız seviye yazılır (kendi alanı ve hedef
        sınavı korunur — boşsa sınıfınki). Alan 10. sınıf altında tutulmaz.
        """
        wanted = sorted({str(student) for student in students if student})
        if not wanted or plan.get("gradeLevel") is None:
            return 0
        profiles = await self._profiles_of(wanted)
        gone = await self._gone(wanted)
        updated = 0
        for student in wanted:
            if student in gone:
                continue
            profile = profiles.get(student)
            own = curriculum.from_row(profile)
            override = bool(int((profile or {}).get("level_override") or 0))
            grade = plan["gradeLevel"]
            track = plan.get("track")
            exam = plan.get("examTarget")
            if override:
                track = own["track"] if own["track"] is not None else track
                exam = own["examTarget"] if own["examTarget"] is not None else exam
            if not curriculum.track_allowed(grade):
                track = None
            after = {"gradeLevel": grade, "track": track, "examTarget": exam}
            changed = [key for key in ("gradeLevel", "track", "examTarget")
                       if own[key] != after[key]]
            if not changed:
                continue
            await self._sync.write(student, columns={
                "grade_level": str(grade), "track": track, "exam_target": exam,
            }, events=[Event("student.updated", changed)])
            updated += 1
        return updated
