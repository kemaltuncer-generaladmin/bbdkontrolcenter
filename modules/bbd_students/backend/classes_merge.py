"""SINIF = GRUP birleştirmesi — göç 009'un VERİ dönüşümü (iş 23, 2026-10-14).

Kullanıcı kararı: "KM'deki her sınıf (öğrenci üzerindeki `className`) otomatik
bir sınıf kaydına dönüşür, üyeleri korunur; mevcut gruplar aynı listeye geçer;
hiçbir şey silinmez."

    run(actor)      → özet (idempotent: yapılmışı yeniden yapmaz)
    status()        → {"state", "version", "startedAt", "finishedAt", "summary"}
    rollback(actor) → özet (dönüşümün açtıklarını kaldırır, seviyeleri yedekten döndürür)

NE YAPAR (tek işlem):
  1. Öğrenci seviyesinin YEDEĞİ (`mod_bbd_students_level_backup`, satır başına
     bir kez).
  2. Her farklı `class_name` (öğrencide kullanılan) ∪ göç 008 sınıf tanımı için
     bir sınıf kaydı: adı aynı, `legacy_class_name` = eski ad, seviye sınıf
     tanımından. Kimlik eski addan türetilen kararlı uuid'dir (uuid5) — tekrar
     koşunca aynı. Aynı adlı (Türkçe katlamayla) CANLI grup varsa ona
     BAĞLANMAZ (Akademi'de o grubun kendi `km_group` satırı var; iki satır tek
     KM kimliğine bağlanamaz): sınıf "<ad> (sınıf)" adıyla açılır, kullanıcı
     isterse birini arşivler. `legacy_class_name` taşıyan her kayıt `origin =
     'class'`tır.
  3. ETKİN her öğrenciye kendi `class_name`'i olan sınıfta üyelik:
     `validFrom` = akademik yıl başı (1 Eylül), açık uç, `origin = 'class'`.
     Öğrencinin o sınıfta (herhangi tarihli) satırı varsa dokunulmaz. Pasif
     öğrenciye üyelik açılmaz (yeniden etkinleştirmede de açılmaz — mevcut kural).
  4. SEVİYE ÖĞRENCİDE KALIR: sınıfı seviyeli öğrencinin GEÇERLİ değeri (göç 008
     kuralı: sınıftan; `level_override` ile kendi alanı/hedefi) öğrencinin kendi
     sütunlarına yazılır. Geçerli değer DEĞİŞMEDİĞİ için olay yazılmaz.
  5. `class_ids` doldurulur (olaysız: dizin değeri değişmez; yeni alan).
  6. Grup revizyonu +1, `group.changed` (outbox), commit'ten sonra
     `bbd_students.groups_changed`.

Eski Akademi etkilenmez: dönüşen sınıflar ve eklenen üyelikler `GET /groups`'un
eski görünümünde GİZLİDİR; `className` aynı kalır.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Any

from . import curriculum
from .academic_year import current_year
from .groups import ORIGIN_CLASS, fold, today_local

VERSION = 1
ACTOR = "sistem: sınıf birleştirme"
#: Kararlı kimlik ad alanı (eski sınıf adı → uuid5).
NAMESPACE = uuid.UUID("6f1c0b0e-23aa-4c1d-9a51-5b1d0a0c2323")


def academic_year_start(today: date) -> date:
    """1 Eylül — eylülden önceki günlerde önceki yılın."""
    return date(today.year if today.month >= 9 else today.year - 1, 9, 1)


def class_id(name: str) -> str:
    return str(uuid.uuid5(NAMESPACE, fold(name)))


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class _Done(Exception):
    """İşlemin içinde görüldü: başka bir koşu dönüşümü bitirmiş — geri al, atla."""


class ClassMerge:
    """Dönüşümü koşturur, durumunu söyler, geri alır."""

    def __init__(self, *, store: Any, groups: Any, log: Any,
                 publish: Callable[[int], Awaitable[None]] | None = None,
                 today: Callable[[], date] = today_local) -> None:
        self._store = store
        #: `GroupService` — sayaç (`_bump`), outbox (`_emit`), yayın (`after_commit`).
        self._groups_service = groups
        self._log = log
        self._publish = publish
        self._today = today
        self._state = store.table("class_merge")
        self._groups = store.table("group")
        self._members = store.table("group_member")
        self._profiles = store.table("profile")
        self._classes = store.table("class")
        self._backup = store.table("level_backup")
        self._audit = store.table("group_log")
        self._tombstones = store.table("group_tombstone")

    async def status(self) -> dict[str, Any]:
        row = await self._store.fetch_one(f"SELECT * FROM {self._state} WHERE id = 1")
        if row is None:
            return {"state": "", "version": 0, "startedAt": None, "finishedAt": None,
                    "summary": {}}
        try:
            summary = json.loads(str(row["summary"] or "{}"))
        except json.JSONDecodeError:
            summary = {}
        return {"state": str(row["state"] or ""), "version": int(row["version"] or 0),
                "startedAt": str(row["started_at"] or "") or None,
                "finishedAt": str(row["finished_at"] or "") or None, "summary": summary}

    async def run(self, *, actor: str = ACTOR, force: bool = False) -> dict[str, Any]:
        """Dönüşüm. Bir kez yapılmışsa (`done`, aynı sürüm) hiçbir şey yazmaz.

        `force`: tamamlanmış dönüşümü yeniden tarar (yalnız EKSİK sınıf ve
        üyelikleri ekler; bitmiş ya da elle değiştirilmiş üyeliğe dokunmaz).
        Geri alınmış (`rolled_back`) dönüşüm yalnız `force` ile yeniden koşar.
        """
        current = await self.status()
        if current["state"] == "done" and current["version"] >= VERSION and not force:
            return {"skipped": True, **current["summary"]}
        if current["state"] == "rolled_back" and not force:
            return {"skipped": True, "rolledBack": True}
        today = self._today()
        start = academic_year_start(today)
        stamp = _now()
        summary = {"classes": 0, "renamed": 0, "members": 0, "levels": 0, "students": 0}
        try:
            revision, changed = await self._run_in(actor=actor, force=force, today=today,
                                                   start=start, stamp=stamp, summary=summary)
        except _Done:
            return {"skipped": True, **(await self.status())["summary"]}
        if changed:
            await self._groups_service.after_commit(revision)
        self._log.info("sınıf birleştirme tamam", **summary)
        return {"skipped": False, **summary}

    async def _run_in(self, *, actor: str, force: bool, today: date, start: date, stamp: str,
                      summary: dict[str, int]) -> tuple[int, bool]:
        async with self._store.transaction():
            # Sayaç kilidi eşzamanlı koşuları sıralar; durum İŞLEMİN İÇİNDE yeniden okunur.
            revision = await self._groups_service._bump()
            again = await self.status()
            if again["state"] in ("done", "rolled_back") and not force:
                raise _Done
            await self._store.execute(
                f"INSERT OR IGNORE INTO {self._state} (id, version, state, started_at) "
                f"VALUES (1, 0, '', ?)", (stamp,))
            profiles = [dict(row) for row in await self._store.fetch_all(
                f"SELECT * FROM {self._profiles}")]
            plans = {str(row["name"]): curriculum.class_from_row(row)
                     for row in await self._store.fetch_all(f"SELECT * FROM {self._classes}")}

            # 1. Seviye yedeği (ilk değer korunur).
            for profile in profiles:
                await self._store.execute(
                    f"INSERT OR IGNORE INTO {self._backup} (kantin_id, class_name, grade_level, "
                    f"track, exam_target, level_override, saved_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (str(profile["kantin_id"]), str(profile.get("class_name") or ""),
                     profile.get("grade_level"), profile.get("track"), profile.get("exam_target"),
                     int(profile.get("level_override") or 0), stamp))

            # 2. Sınıf kayıtları.
            names = sorted({str(p.get("class_name") or "").strip() for p in profiles} - {""}
                           | {name for name in plans if name.strip()})
            rows = [dict(row) for row in await self._store.fetch_all(
                f"SELECT * FROM {self._groups}")]
            by_legacy = {str(row["legacy_class_name"]): row for row in rows
                         if row.get("legacy_class_name")}
            live_folds = {str(row["name_fold"]) for row in rows if not row["archived_at"]}
            # Kalıcı silinmiş sınıf (göç 011) `force` koşusunda yeniden açılmaz.
            buried = {str(row["id"]) for row in await self._store.fetch_all(
                f"SELECT id FROM {self._tombstones}")}
            target: dict[str, str] = {}
            for name in names:
                if name not in by_legacy and class_id(name) in buried:
                    continue
                plan = plans.get(name)
                level = {"grade_level": None if plan is None else str(plan["gradeLevel"]),
                         "track": None if plan is None else plan["track"],
                         "exam_target": None if plan is None else plan["examTarget"]}
                if name in by_legacy:
                    target[name] = str(by_legacy[name]["id"])
                    continue
                display = name[:60]
                if fold(display) in live_folds:
                    # Aynı adlı canlı grup var: ayrı kayıt, ayırt edici adla.
                    display = f"{name[:50]} (sınıf)"
                    suffix = 2
                    while fold(display) in live_folds:
                        display = f"{name[:46]} (sınıf {suffix})"
                        suffix += 1
                    summary["renamed"] += 1
                group_id = class_id(name)
                taken = await self._store.fetch_one(
                    f"SELECT id FROM {self._groups} WHERE id = ?", (group_id,))
                if taken is not None:
                    # Arşivlenmiş eski dönüşüm satırı: yeniden canlandırılmaz, bağlanır.
                    await self._store.execute(
                        f"UPDATE {self._groups} SET legacy_class_name = ?, revision = ? "
                        f"WHERE id = ?", (name, revision, group_id))
                    target[name] = group_id
                    continue
                await self._store.execute(
                    f"INSERT INTO {self._groups} (id, name, name_fold, call_name, kind, note, "
                    f"archived_at, created_at, created_by, updated_at, updated_by, revision, "
                    f"origin, legacy_class_name, grade_level, track, exam_target, "
                    f"academic_year) "
                    f"VALUES (?, ?, ?, '', 'group', '', '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (group_id, display, fold(display), stamp, actor, stamp, actor, revision,
                     ORIGIN_CLASS, name, level["grade_level"], level["track"],
                     level["exam_target"], current_year(today)))
                live_folds.add(fold(display))
                target[name] = group_id
                summary["classes"] += 1

            # 3. Üyelikler (etkin öğrenciler; o sınıfta satırı olmayanlar).
            existing_pairs = {(str(row["group_id"]), str(row["kantin_id"]))
                              for row in await self._store.fetch_all(
                                  f"SELECT group_id, kantin_id FROM {self._members}")}
            for profile in profiles:
                name = str(profile.get("class_name") or "").strip()
                student = str(profile["kantin_id"])
                if not name or profile.get("status") == "inactive" or name not in target:
                    continue
                pair = (target[name], student)
                if pair in existing_pairs:
                    continue
                await self._store.execute(
                    f"INSERT INTO {self._members} (id, group_id, kantin_id, valid_from, "
                    f"valid_to, reason, created_at, created_by, updated_at, updated_by, "
                    f"revision, origin) VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), pair[0], student, start.isoformat(),
                     "Sınıf birleştirme (eski sınıf adı)", stamp, actor, stamp, actor,
                     revision, ORIGIN_CLASS))
                existing_pairs.add(pair)
                summary["members"] += 1
            for group_id in sorted(set(target.values())):
                await self._store.execute(
                    f"UPDATE {self._groups} SET revision = ?, updated_at = ? WHERE id = ?",
                    (revision, stamp, group_id))

            # 4. Seviye öğrencide: geçerli değer (sınıftan) öğrencinin kendi sütunlarına.
            for profile in profiles:
                name = str(profile.get("class_name") or "")
                plan = plans.get(name) if name else None
                if plan is None:
                    continue
                effective = curriculum.effective(profile, plan)
                own = curriculum.from_row(profile)
                if all(own[key] == effective[key] for key in ("gradeLevel", "track",
                                                               "examTarget")):
                    continue
                await self._store.execute(
                    f"UPDATE {self._profiles} SET grade_level = ?, track = ?, exam_target = ? "
                    f"WHERE kantin_id = ?",
                    (None if effective["gradeLevel"] is None else str(effective["gradeLevel"]),
                     effective["track"], effective["examTarget"], str(profile["kantin_id"])))
                summary["levels"] += 1

            # 5. `class_ids` (olaysız; dizinin yeni alanı).
            summary["students"] = await self._fill_class_ids(today)

            await self._store.execute(
                f"INSERT INTO {self._audit} (at, actor, action, group_id, revision, detail) "
                f"VALUES (?, ?, 'class_merge', '', ?, ?)",
                (stamp, actor[:160], revision, json.dumps(summary, ensure_ascii=False)))
            await self._store.execute(
                f"UPDATE {self._state} SET version = ?, state = 'done', finished_at = ?, "
                f"actor = ?, summary = ? WHERE id = 1",
                (VERSION, _now(), actor[:160], json.dumps(summary, ensure_ascii=False)))
            # BOŞ DÖNÜŞÜM OLAY YAZMAZ (63-duzeltici). Yeni kurulumda (öğrenci/sınıf yok)
            # dönüşüm yine "tamam" işaretlenir ama hiçbir grup satırı değişmedi:
            # `group.changed` yazılsaydı her yeni kurulumun outbox'ı boş bir olayla
            # açılır, Akademi/zil boşuna grupları yeniden çekerdi.
            changed = bool(target) or any(summary[key] for key in ("classes", "members", "levels"))
            if changed:
                await self._groups_service._emit(revision)
        return int(revision), changed

    async def _fill_class_ids(self, today: date) -> int:
        day = today.isoformat()
        live = {str(row["id"]) for row in await self._store.fetch_all(
            f"SELECT id FROM {self._groups} WHERE archived_at = ''")}
        current: dict[str, set[str]] = {}
        for row in await self._store.fetch_all(
                f"SELECT group_id, kantin_id FROM {self._members} WHERE valid_from <= ? "
                f"AND (valid_to = '' OR valid_to >= ?)", (day, day)):
            if str(row["group_id"]) in live:
                current.setdefault(str(row["kantin_id"]), set()).add(str(row["group_id"]))
        filled = 0
        for student, ids in current.items():
            await self._store.execute(
                f"UPDATE {self._profiles} SET class_ids = ? WHERE kantin_id = ?",
                (",".join(sorted(ids)), student))
            filled += 1
        return filled

    async def rollback(self, *, actor: str) -> dict[str, Any]:
        """Dönüşümü geri alır. KALDIRILAN: dönüşümün açtığı sınıflar (`origin='class'`)
        ve onların BÜTÜN üyelikleri (dönüşümden sonra elle eklenenler dahil — geri
        alma yalnız yayından hemen sonra içindir). Öğrenci seviyesi yedekten döner;
        `class_ids` boşalır. `class_name` dokunulmaz (eski sürüm onu okur).
        """
        stamp = _now()
        summary = {"classes": 0, "members": 0, "levels": 0}
        async with self._store.transaction():
            revision = await self._groups_service._bump()
            created = [str(row["id"]) for row in await self._store.fetch_all(
                f"SELECT id FROM {self._groups} WHERE origin = ?", (ORIGIN_CLASS,))]
            for group_id in created:
                gone = await self._store.fetch_one(
                    f"SELECT COUNT(*) AS n FROM {self._members} WHERE group_id = ?", (group_id,))
                summary["members"] += int((gone or {}).get("n") or 0)
                await self._store.execute(
                    f"DELETE FROM {self._members} WHERE group_id = ?", (group_id,))
                await self._store.execute(f"DELETE FROM {self._groups} WHERE id = ?", (group_id,))
                summary["classes"] += 1
            extra = await self._store.fetch_one(
                f"SELECT COUNT(*) AS n FROM {self._members} WHERE origin = ?", (ORIGIN_CLASS,))
            summary["members"] += int((extra or {}).get("n") or 0)
            await self._store.execute(
                f"DELETE FROM {self._members} WHERE origin = ?", (ORIGIN_CLASS,))
            for row in await self._store.fetch_all(f"SELECT * FROM {self._backup}"):
                await self._store.execute(
                    f"UPDATE {self._profiles} SET grade_level = ?, track = ?, exam_target = ?, "
                    f"level_override = ?, class_ids = '' WHERE kantin_id = ?",
                    (row["grade_level"], row["track"], row["exam_target"],
                     int(row["level_override"] or 0), str(row["kantin_id"])))
                summary["levels"] += 1
            await self._store.execute(f"UPDATE {self._profiles} SET class_ids = ''")
            await self._store.execute(
                f"INSERT INTO {self._audit} (at, actor, action, group_id, revision, detail) "
                f"VALUES (?, ?, 'class_merge_rollback', '', ?, ?)",
                (stamp, actor[:160], revision, json.dumps(summary, ensure_ascii=False)))
            await self._store.execute(
                f"INSERT OR IGNORE INTO {self._state} (id, version, state, started_at) "
                f"VALUES (1, 0, '', ?)", (stamp,))
            await self._store.execute(
                f"UPDATE {self._state} SET state = 'rolled_back', finished_at = ?, actor = ?, "
                f"summary = ? WHERE id = 1",
                (stamp, actor[:160], json.dumps(summary, ensure_ascii=False)))
            await self._groups_service._emit(revision)
        await self._groups_service.after_commit(revision)
        self._log.warning("sınıf birleştirme geri alındı", actor=actor, **summary)
        return summary
