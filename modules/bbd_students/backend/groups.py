"""Öğrenci grupları ve TARİHLİ üyelik — `bbd_students.groups` yeteneği.

Sözleşme: bbd-akademi/docs/integrations/km-ogrenci-gruplari-ve-zil.md §2.
GRUBUN TEK SAHİBİ BURASIDIR. BBD Akademi grubu buradan çeker (mobil geçit
`GET /groups`), zil sistemi elle çağrı listesini buradan okur — ikisi de
yetenek üzerinden (K3), tabloya bakmadan (K5).

    list(since_revision=None) → {"revision", "full", "groups": [GroupRecord], "complete"}
    get(group_id)             → GroupRecord | None
    for_student(student_id)   → [{"group": GroupSummary, "validFrom", "validTo"}]
    import_named(name, *, kind, call_name="", actor) → {"id", "created"}

`GroupRecord` = {id, name, callName, kind, archived, archivedAt, updatedAt,
revision, members: [{studentId, validFrom, validTo}]} — `members` grubun
BÜTÜN üyelik satırlarıdır (geçmiş dahil). Tarihler Europe/Istanbul yerel
GÜNÜDÜR; iki uç da dahildir; `validTo: null` açık uç.

KURALLAR (§2.1) TEK YERDE, BURADA:
  · ad tekilliği canlı gruplar arasında, Türkçe duyarlı katlamayla (`fold`);
  · aynı öğrenci aynı grupta kesişen iki üyelik taşıyamaz;
  · `individual` grupta herhangi bir günde en çok bir geçerli üye olur;
  · geçmiş tarihli değişiklik (bugünden önceki bir gün) gerekçe ister ve
    denetim izine yazılır;
  · silme yoktur, arşiv vardır; arşivde açık üyelikler arşiv gününde kapanır.

REVİZYON. Her yazma kalıcı sayacı (`mod_bbd_students_group_rev`) +1 artırır ve
dokunduğu grup ile üyelik satırlarına damgalar; grubun `revision`'ı üyeliği
değişince de yükselir — artımlı okuma (`since`) bu yüzden tek indeksli sorgudur.
Yazmalar süreç içi tek kilitle sıralanır: sayaç, doğrulama ve yazma arasına
başka bir yazma giremez (modül deposunda işlem açılamıyor; KM sunucusu tek
süreçtir).

OLAY. `bbd_students.groups_changed` (yük `{revision}`) yazmanın HEMEN ardından
yayınlanır; yayın patlarsa yazma yine tamamdır (K7, `bell.week_changed`
kalıbı).
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

ISTANBUL = ZoneInfo("Europe/Istanbul")

KINDS = ("group", "individual")
NAME_MAX = 60
CALL_NAME_MAX = 80
NOTE_MAX = 500
REASON_MAX = 200
#: Tek istekte en çok bu kadar öğrenci (toplu ekle/çıkar/taşı).
BULK_MAX = 2000
#: Canlı grup üst sınırı — elle bozulmuş veri ekranı boğmasın.
GROUP_MAX = 1000

#: Kayıt listesi ve kimlik listesi. Sınıfların `list` metodu tür adını
#: gölgelemesin diye modül düzeyinde adlandırılır (directory.py ile aynı).
Records = list[dict[str, Any]]
Ids = list[Any]

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_STUDENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

#: Türkçede noktalı/noktasız i `casefold()` ile eşleşmez ("İlayda" ↔ "ilayda").
#: Kural zil modülündekinin AYNISIDIR (bell/backend/service.py → `fold`); modül
#: modülü import edemediği için (K3) burada yeniden yazılır.
_FOLD = str.maketrans({"I": "ı", "İ": "i"})


def fold(text: str) -> str:
    """Türkçe duyarlı katlama — yalnız ad karşılaştırmak için."""
    return " ".join(str(text or "").split()).translate(_FOLD).casefold()


def today_local() -> date:
    return datetime.now(ISTANBUL).date()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _local_iso(value: str) -> str | None:
    """Saklanan UTC damgası → Europe/Istanbul ISO (sözleşme biçimi)."""
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(ISTANBUL).isoformat(timespec="seconds")


class GroupError(ValueError):
    """Kural ihlali. `status` HTTP karşılığı, `code` makine için, mesaj Türkçe."""

    def __init__(self, message: str, *, status: int = 400, code: str = "validation_error",
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.details = details or {}


def parse_day(value: Any, *, field: str) -> date:
    text = str(value or "").strip()
    if not _DATE.match(text):
        raise GroupError(f"“{field}” YYYY-AA-GG biçiminde olmalı.", details={"field": field})
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise GroupError(f"“{field}” geçerli bir tarih değil.",
                         details={"field": field}) from None


def _day(text: str) -> date | None:
    """Saklanan gün; '' → None (açık uç)."""
    return date.fromisoformat(text) if text else None


def _overlaps(a_from: date, a_to: date | None, b_from: date, b_to: date | None) -> bool:
    """İki kapalı aralık kesişiyor mu? `None` son = açık uç."""
    return (b_to is None or a_from <= b_to) and (a_to is None or b_from <= a_to)


def _clean_name(value: Any) -> str:
    name = " ".join(str(value or "").split())
    if not name:
        raise GroupError("Grup adı boş olamaz.", details={"field": "name"})
    if len(name) > NAME_MAX:
        raise GroupError(f"Grup adı en çok {NAME_MAX} karakter olabilir.",
                         details={"field": "name"})
    return name


def _clean_kind(value: Any) -> str:
    kind = str(value or "group").strip()
    if kind not in KINDS:
        raise GroupError("Tür “group” (toplu ders) ya da “individual” (özel ders) olmalı.",
                         details={"field": "kind"})
    return kind


def _clean_students(values: Iterable[Any]) -> list[str]:
    seen: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not _STUDENT_ID.match(text):
            raise GroupError("Geçersiz öğrenci kimliği.", details={"studentId": text[:64]})
        if text not in seen:
            seen.append(text)
    if not seen:
        raise GroupError("En az bir öğrenci seçin.", details={"field": "studentIds"})
    if len(seen) > BULK_MAX:
        raise GroupError(f"Tek seferde en çok {BULK_MAX} öğrenci.",
                         details={"field": "studentIds"})
    return seen


def _clean_reason(value: Any) -> str:
    return " ".join(str(value or "").split())[:REASON_MAX]


class GroupService:
    """Grup ve üyelik iş kuralları + `bbd_students.groups` yeteneğinin gövdesi."""

    def __init__(self, *, store: Any, log: Any,
                 publish: Callable[[int], Awaitable[None]] | None = None,
                 today: Callable[[], date] = today_local) -> None:
        self._store = store
        self._log = log
        self._publish = publish
        self._today = today
        self._groups = store.table("group")
        self._members = store.table("group_member")
        self._rev = store.table("group_rev")
        self._audit = store.table("group_log")
        self._profiles = store.table("profile")
        self._lock = asyncio.Lock()

    # ============================================================== yardım

    async def revision(self) -> int:
        row = await self._store.fetch_one(f"SELECT revision FROM {self._rev} WHERE id = 1")
        return int(row["revision"]) if row else 0

    async def _bump(self) -> int:
        """Kalıcı sayacı +1 artırır ve yeni değeri döner. KİLİT ALTINDA çağrılır."""
        current = await self.revision()
        if current == 0 and await self._store.fetch_one(
                f"SELECT id FROM {self._rev} WHERE id = 1") is None:
            await self._store.execute(
                f"INSERT INTO {self._rev} (id, revision) VALUES (1, 1)")
            return 1
        await self._store.execute(
            f"UPDATE {self._rev} SET revision = ? WHERE id = 1", (current + 1,))
        return current + 1

    async def _changed(self, revision: int) -> None:
        if self._publish is None:
            return
        try:
            await self._publish(revision)
        except Exception as failure:  # noqa: BLE001 — yayın yazmayı düşürmez (K7)
            self._log.warning("grup değişikliği yayınlanamadı", error=str(failure))

    async def _write_audit(self, *, actor: str, action: str, group_id: str, revision: int,
                           detail: dict[str, Any]) -> None:
        await self._store.execute(
            f"INSERT INTO {self._audit} (at, actor, action, group_id, revision, detail) "
            f"VALUES (?, ?, ?, ?, ?, ?)",
            (_now(), actor[:160], action, group_id, revision,
             json.dumps(detail, ensure_ascii=False)[:4000]),
        )

    async def _group_row(self, group_id: str) -> dict[str, Any]:
        row = await self._store.fetch_one(
            f"SELECT * FROM {self._groups} WHERE id = ?", (str(group_id),))
        if row is None:
            raise GroupError("Grup bulunamadı.", status=404, code="group_not_found")
        return dict(row)

    async def _member_rows(self, group_ids: list[str] | None = None) -> list[dict[str, Any]]:
        """Üyelik satırları — tek sorgu (N+1 yok). `None` = hepsi."""
        rows: list[dict[str, Any]] = []
        if group_ids is None:
            rows += await self._store.fetch_all(
                f"SELECT * FROM {self._members} ORDER BY group_id, valid_from, kantin_id")
            return rows
        # Yer tutucu sınırına takılmamak için parçalı.
        for start in range(0, len(group_ids), 500):
            chunk = group_ids[start:start + 500]
            if not chunk:
                continue
            marks = ", ".join("?" for _ in chunk)
            rows += await self._store.fetch_all(
                f"SELECT * FROM {self._members} WHERE group_id IN ({marks}) "
                f"ORDER BY group_id, valid_from, kantin_id", tuple(chunk))
        return rows

    def _need_reason(self, days: Iterable[date | None], reason: str) -> None:
        """Bugünden önceki bir güne dokunan değişiklik gerekçe ister (§2.1)."""
        today = self._today()
        if any(day is not None and day < today for day in days) and not reason:
            raise GroupError("Geçmiş tarihli değişiklik için gerekçe yazın.",
                             code="reason_required", details={"field": "reason"})

    @staticmethod
    def _check_members(kind: str, rows: list[dict[str, Any]]) -> None:
        """Bir grubun BÜTÜN satırları için kesişme ve bireysel-tek-üye kuralı."""
        spans = [(str(row["kantin_id"]), _day(str(row["valid_from"])) or date.min,
                  _day(str(row["valid_to"]))) for row in rows]
        for index, (student, start, end) in enumerate(spans):
            if end is not None and end < start:
                raise GroupError("Son gün başlangıçtan önce olamaz.",
                                 details={"studentId": student})
            for other, other_start, other_end in spans[index + 1:]:
                if not _overlaps(start, end, other_start, other_end):
                    continue
                if other == student:
                    raise GroupError("Öğrencinin bu grupta aynı tarihlere düşen başka bir "
                                     "üyeliği var.", status=409, code="membership_overlap",
                                     details={"studentId": student})
                if kind == "individual":
                    raise GroupError("Özel ders grubunda aynı gün yalnız bir öğrenci "
                                     "olabilir.", status=409, code="individual_single_member",
                                     details={"studentId": other})

    async def _check_name_free(self, name: str, *, except_id: str = "") -> None:
        rows = await self._store.fetch_all(
            f"SELECT id FROM {self._groups} WHERE name_fold = ? AND archived_at = ''",
            (fold(name),))
        if any(str(row["id"]) != except_id for row in rows):
            raise GroupError(f"“{name}” adında bir grup zaten var.", status=409,
                             code="group_name_taken", details={"field": "name"})

    # ============================================================== kayıt

    @staticmethod
    def _member_view(row: dict[str, Any], *, full: bool) -> dict[str, Any]:
        record: dict[str, Any] = {
            "studentId": str(row["kantin_id"]),
            "validFrom": str(row["valid_from"]),
            "validTo": str(row["valid_to"]) or None,
        }
        if full:
            record.update({"id": str(row["id"]), "reason": str(row["reason"] or ""),
                           "updatedAt": _local_iso(str(row["updated_at"])),
                           "updatedBy": str(row["updated_by"] or "")})
        return record

    def _record(self, row: dict[str, Any], members: list[dict[str, Any]], *,
                full: bool = False) -> dict[str, Any]:
        record: dict[str, Any] = {
            "id": str(row["id"]),
            "name": str(row["name"]),
            "callName": str(row["call_name"] or ""),
            "kind": str(row["kind"] or "group"),
            "archived": bool(row["archived_at"]),
            "archivedAt": _local_iso(str(row["archived_at"] or "")),
            "updatedAt": _local_iso(str(row["updated_at"])),
            "revision": int(row["revision"] or 0),
            "members": [self._member_view(member, full=full) for member in members],
        }
        if full:
            record["note"] = str(row["note"] or "")
            record["createdAt"] = _local_iso(str(row["created_at"]))
        return record

    async def _records(self, rows: list[dict[str, Any]], *, full: bool = False,
                       all_members: bool = False) -> list[dict[str, Any]]:
        ids = [str(row["id"]) for row in rows]
        members = await self._member_rows(None if all_members else ids)
        by_group: dict[str, list[dict[str, Any]]] = {}
        for member in members:
            by_group.setdefault(str(member["group_id"]), []).append(member)
        return [self._record(row, by_group.get(str(row["id"]), []), full=full) for row in rows]

    # ======================================================== yetenek (okuma)

    async def list(self, since_revision: int | None = None) -> dict[str, Any]:
        """`bbd_students.groups.list` — tam ya da `revision > since` olan gruplar.

        Her grup TAM üye listesiyle döner (istemci grubu bütün olarak değiştirir).
        Kaynak yerel tablodur; okuma yarıda kalmaz, `complete` her zaman doğrudur.
        """
        revision = await self.revision()
        if since_revision is None:
            rows = await self._store.fetch_all(
                f"SELECT * FROM {self._groups} ORDER BY name_fold, id")
            groups = await self._records(rows, all_members=True)
            return {"revision": revision, "full": True, "groups": groups, "complete": True}
        rows = await self._store.fetch_all(
            f"SELECT * FROM {self._groups} WHERE revision > ? ORDER BY revision, id",
            (int(since_revision),))
        groups = await self._records(rows)
        return {"revision": revision, "full": False, "groups": groups, "complete": True}

    async def get(self, group_id: str) -> dict[str, Any] | None:
        row = await self._store.fetch_one(
            f"SELECT * FROM {self._groups} WHERE id = ?", (str(group_id),))
        if row is None:
            return None
        return (await self._records([dict(row)]))[0]

    async def for_student(self, student_id: str) -> Records:
        """Öğrencinin bütün üyelikleri (geçmiş dahil), en yeni başlangıç önce."""
        members = await self._store.fetch_all(
            f"SELECT * FROM {self._members} WHERE kantin_id = ? "
            f"ORDER BY valid_from DESC, id", (str(student_id),))
        if not members:
            return []
        ids = sorted({str(row["group_id"]) for row in members})
        marks = ", ".join("?" for _ in ids)
        groups = {str(row["id"]): row for row in await self._store.fetch_all(
            f"SELECT * FROM {self._groups} WHERE id IN ({marks})", tuple(ids))}
        out: list[dict[str, Any]] = []
        for member in members:
            group = groups.get(str(member["group_id"]))
            if group is None:
                continue
            out.append({
                "group": {"id": str(group["id"]), "name": str(group["name"]),
                          "callName": str(group["call_name"] or ""),
                          "kind": str(group["kind"] or "group"),
                          "archived": bool(group["archived_at"])},
                "validFrom": str(member["valid_from"]),
                "validTo": str(member["valid_to"]) or None,
                "memberId": str(member["id"]),
                "reason": str(member["reason"] or ""),
            })
        return out

    async def import_named(self, name: str, *, kind: str, call_name: str = "",
                           actor: str) -> dict[str, Any]:
        """Zil gruplarının taşınması için (§6): katlanmış ada göre İDEMPOTENT.

        Aynı adlı canlı grup varsa o döner (`created: false`); yoksa üyesiz
        yeni grup açılır.
        """
        clean = _clean_name(name)
        row = await self._store.fetch_one(
            f"SELECT id FROM {self._groups} WHERE name_fold = ? AND archived_at = '' "
            f"ORDER BY created_at LIMIT 1", (fold(clean),))
        if row is not None:
            return {"id": str(row["id"]), "created": False}
        record = await self.create({"name": clean, "kind": kind, "callName": call_name},
                                   actor=actor, action="import")
        return {"id": record["id"], "created": True}

    # ======================================================== ekran (okuma)

    async def overview(self, *, archived: str = "live", kind: str = "", query: str = "",
                       on: date | None = None) -> dict[str, Any]:
        """Gruplar sekmesinin listesi: bugünkü üye sayısıyla. İki sorgu."""
        day = on or self._today()
        rows = await self._store.fetch_all(
            f"SELECT * FROM {self._groups} ORDER BY name_fold, id")
        needle = fold(query)
        chosen = [dict(row) for row in rows
                  if (archived == "all" or (archived == "archived") == bool(row["archived_at"]))
                  and (not kind or str(row["kind"]) == kind)
                  and (not needle or needle in str(row["name_fold"])
                       or needle in fold(str(row["call_name"] or "")))]
        members = await self._member_rows([str(row["id"]) for row in chosen])
        current: dict[str, int] = {}
        for member in members:
            start = _day(str(member["valid_from"])) or date.min
            end = _day(str(member["valid_to"]))
            if start <= day and (end is None or day <= end):
                current[str(member["group_id"])] = current.get(str(member["group_id"]), 0) + 1
        groups = []
        for row in chosen:
            groups.append({
                "id": str(row["id"]), "name": str(row["name"]),
                "callName": str(row["call_name"] or ""), "kind": str(row["kind"]),
                "note": str(row["note"] or ""), "archived": bool(row["archived_at"]),
                "archivedAt": _local_iso(str(row["archived_at"] or "")),
                "updatedAt": _local_iso(str(row["updated_at"])),
                "revision": int(row["revision"] or 0),
                "memberCount": current.get(str(row["id"]), 0),
            })
        return {"date": day.isoformat(), "revision": await self.revision(), "groups": groups}

    async def memberships(self, *, on: date | None = None) -> dict[str, Any]:
        """O gün (vars. bugün) geçerli bütün üyelikler, canlı gruplarda. İki sorgu.

        Öğrenci seçen paneller (`bbd_students.list` arayüz yeteneği) öğrencinin
        gruplarını buradan eşler; grup başına istek atmaz.
        """
        day = (on or self._today()).isoformat()
        groups = {str(row["id"]): row for row in await self._store.fetch_all(
            f"SELECT id, name, kind FROM {self._groups} WHERE archived_at = ''")}
        rows = await self._store.fetch_all(
            f"SELECT group_id, kantin_id FROM {self._members} "
            f"WHERE valid_from <= ? AND (valid_to = '' OR valid_to >= ?) "
            f"ORDER BY kantin_id", (day, day))
        items = [{"studentId": str(row["kantin_id"]), "groupId": str(row["group_id"]),
                  "groupName": str(groups[str(row["group_id"])]["name"]),
                  "kind": str(groups[str(row["group_id"])]["kind"])}
                 for row in rows if str(row["group_id"]) in groups]
        return {"date": day, "items": items}

    async def detail(self, group_id: str, *, on: date | None = None) -> dict[str, Any]:
        """Grup çekmecesi: kayıt + bütün satırlar (kimlikleriyle) + o günün üyeleri."""
        row = await self._group_row(group_id)
        record = (await self._records([row], full=True))[0]
        day = on or self._today()
        record["date"] = day.isoformat()
        record["current"] = [
            member for member in record["members"]
            if member["validFrom"] <= day.isoformat()
            and (member["validTo"] is None or day.isoformat() <= member["validTo"])
        ]
        return record

    async def history(self, group_id: str, *, limit: int = 100) -> Records:
        await self._group_row(group_id)
        rows = await self._store.fetch_all(
            f"SELECT * FROM {self._audit} WHERE group_id = ? ORDER BY id DESC LIMIT ?",
            (str(group_id), max(1, min(500, int(limit)))))
        out = []
        for row in rows:
            try:
                detail = json.loads(str(row["detail"] or "{}"))
            except json.JSONDecodeError:
                detail = {}
            out.append({"at": _local_iso(str(row["at"])), "actor": str(row["actor"]),
                        "action": str(row["action"]), "revision": int(row["revision"]),
                        "detail": detail})
        return out

    # ============================================================== yazma

    async def create(self, payload: dict[str, Any], *, actor: str,
                     action: str = "create") -> dict[str, Any]:
        """Grup = ad + öğrenci seçimi. `studentIds` verilirse `validFrom`'dan üye olur."""
        name = _clean_name(payload.get("name"))
        kind = _clean_kind(payload.get("kind"))
        call_name = " ".join(str(payload.get("callName") or "").split())[:CALL_NAME_MAX]
        note = str(payload.get("note") or "").strip()[:NOTE_MAX]
        students = list(payload.get("studentIds") or [])
        start = (parse_day(payload["validFrom"], field="validFrom")
                 if payload.get("validFrom") else self._today())
        reason = _clean_reason(payload.get("reason"))
        if students:
            students = _clean_students(students)
            self._need_reason([start], reason)
            if kind == "individual" and len(students) > 1:
                raise GroupError("Özel ders grubunda yalnız bir öğrenci olabilir.", status=409,
                                 code="individual_single_member")

        async with self._lock:
            await self._check_name_free(name)
            live = await self._store.fetch_one(
                f"SELECT COUNT(*) AS n FROM {self._groups} WHERE archived_at = ''")
            if live is not None and int(live["n"]) >= GROUP_MAX:
                raise GroupError(f"En çok {GROUP_MAX} canlı grup olabilir.", status=409,
                                 code="group_limit")
            revision = await self._bump()
            group_id = str(uuid.uuid4())
            stamp = _now()
            await self._store.execute(
                f"INSERT INTO {self._groups} (id, name, name_fold, call_name, kind, note, "
                f"archived_at, created_at, created_by, updated_at, updated_by, revision) "
                f"VALUES (?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?)",
                (group_id, name, fold(name), call_name, kind, note, stamp, actor, stamp,
                 actor, revision),
            )
            for student in students:
                await self._insert_member(group_id, student, start, None, reason=reason,
                                          actor=actor, revision=revision, stamp=stamp)
            await self._write_audit(actor=actor, action=action, group_id=group_id,
                                    revision=revision,
                                    detail={"name": name, "kind": kind,
                                            "students": len(students),
                                            "validFrom": start.isoformat() if students else None})
        await self._changed(revision)
        self._log.info("öğrenci grubu açıldı", group=group_id, kind=kind,
                       students=len(students), actor=actor)
        return await self.detail(group_id)

    async def from_class(self, class_name: str, payload: dict[str, Any], *,
                         actor: str) -> dict[str, Any]:
        """"Sınıftan grup oluştur": sınıfın o anki öğrencileri yeni gruba KOPYALANIR."""
        clean = " ".join(str(class_name or "").split())
        if not clean:
            raise GroupError("Sınıf seçin.", details={"field": "className"})
        rows = await self._store.fetch_all(
            f"SELECT kantin_id FROM {self._profiles} WHERE class_name = ? ORDER BY kantin_id",
            (clean,))
        students = [str(row["kantin_id"]) for row in rows]
        if not students:
            raise GroupError(f"“{clean}” sınıfında öğrenci yok.", status=404,
                             code="class_empty")
        body = {**payload, "studentIds": students}
        body.setdefault("name", clean)
        body["kind"] = "group"
        return await self.create(body, actor=actor, action="from_class")

    async def update(self, group_id: str, payload: dict[str, Any], *,
                     actor: str) -> dict[str, Any]:
        """Ad, tür, anons adı, not. Gönderilmeyen alan değişmez."""
        async with self._lock:
            row = await self._group_row(group_id)
            changes: dict[str, Any] = {}
            if "name" in payload:
                name = _clean_name(payload["name"])
                if name != row["name"]:
                    if not row["archived_at"]:
                        await self._check_name_free(name, except_id=str(group_id))
                    changes.update({"name": name, "name_fold": fold(name)})
            if "kind" in payload:
                kind = _clean_kind(payload["kind"])
                if kind != row["kind"]:
                    self._check_members(kind, await self._member_rows([str(group_id)]))
                    changes["kind"] = kind
            if "callName" in payload:
                call_name = " ".join(str(payload["callName"] or "").split())[:CALL_NAME_MAX]
                if call_name != row["call_name"]:
                    changes["call_name"] = call_name
            if "note" in payload:
                note = str(payload["note"] or "").strip()[:NOTE_MAX]
                if note != row["note"]:
                    changes["note"] = note
            if not changes:
                return await self.detail(group_id)
            revision = await self._bump()
            await self._touch_group(group_id, actor=actor, revision=revision, extra=changes)
            await self._write_audit(actor=actor, action="update", group_id=str(group_id),
                                    revision=revision,
                                    detail={key: value for key, value in changes.items()
                                            if key != "name_fold"})
        await self._changed(revision)
        return await self.detail(group_id)

    async def archive(self, group_id: str, *, actor: str) -> dict[str, Any]:
        """Arşivler. Açık üyelikler arşiv gününde kapanır; henüz başlamamış
        üyelikler (başlangıcı bugünden sonra) hiç yürürlüğe girmediği için
        kaldırılır ve denetim izine yazılır. Geçmiş satırlara dokunulmaz."""
        today = self._today()
        async with self._lock:
            row = await self._group_row(group_id)
            if row["archived_at"]:
                return await self.detail(group_id)
            revision = await self._bump()
            stamp = _now()
            members = await self._member_rows([str(group_id)])
            closed, dropped = 0, 0
            for member in members:
                start = _day(str(member["valid_from"])) or date.min
                end = _day(str(member["valid_to"]))
                if start > today:
                    await self._store.execute(
                        f"DELETE FROM {self._members} WHERE id = ?", (str(member["id"]),))
                    dropped += 1
                elif end is None or end > today:
                    await self._store.execute(
                        f"UPDATE {self._members} SET valid_to = ?, updated_at = ?, "
                        f"updated_by = ?, revision = ? WHERE id = ?",
                        (today.isoformat(), stamp, actor, revision, str(member["id"])))
                    closed += 1
            await self._touch_group(group_id, actor=actor, revision=revision,
                                    extra={"archived_at": stamp})
            await self._write_audit(actor=actor, action="archive", group_id=str(group_id),
                                    revision=revision,
                                    detail={"closed": closed, "droppedFuture": dropped,
                                            "on": today.isoformat()})
        await self._changed(revision)
        self._log.info("öğrenci grubu arşivlendi", group=group_id, closed=closed, actor=actor)
        return await self.detail(group_id)

    async def restore(self, group_id: str, *, actor: str) -> dict[str, Any]:
        """Arşivden geri alır. Üyelikler kendiliğinden açılmaz."""
        async with self._lock:
            row = await self._group_row(group_id)
            if not row["archived_at"]:
                return await self.detail(group_id)
            await self._check_name_free(str(row["name"]), except_id=str(group_id))
            revision = await self._bump()
            await self._touch_group(group_id, actor=actor, revision=revision,
                                    extra={"archived_at": ""})
            await self._write_audit(actor=actor, action="restore", group_id=str(group_id),
                                    revision=revision, detail={})
        await self._changed(revision)
        return await self.detail(group_id)

    async def add_members(self, group_id: str, student_ids: Ids, *,
                          valid_from: Any = None, reason: Any = "",
                          actor: str) -> dict[str, Any]:
        """Çoklu ekleme. Zaten o tarihte üye olan öğrenci `skipped` içinde döner."""
        students = _clean_students(student_ids)
        start = parse_day(valid_from, field="validFrom") if valid_from else self._today()
        why = _clean_reason(reason)
        self._need_reason([start], why)
        async with self._lock:
            row = await self._group_row(group_id)
            if row["archived_at"]:
                raise GroupError("Arşivlenmiş gruba üye eklenemez.", status=409,
                                 code="group_archived")
            existing = await self._member_rows([str(group_id)])
            skipped = [student for student in students if any(
                str(member["kantin_id"]) == student and _overlaps(
                    start, None, _day(str(member["valid_from"])) or date.min,
                    _day(str(member["valid_to"])))
                for member in existing)]
            fresh = [student for student in students if student not in skipped]
            if not fresh:
                return {"added": 0, "skipped": skipped, "group": await self.detail(group_id)}
            planned = existing + [{"kantin_id": student, "valid_from": start.isoformat(),
                                   "valid_to": ""} for student in fresh]
            self._check_members(str(row["kind"]), planned)
            revision = await self._bump()
            stamp = _now()
            for student in fresh:
                await self._insert_member(str(group_id), student, start, None, reason=why,
                                          actor=actor, revision=revision, stamp=stamp)
            await self._touch_group(group_id, actor=actor, revision=revision)
            await self._write_audit(actor=actor, action="members_add", group_id=str(group_id),
                                    revision=revision,
                                    detail={"students": fresh, "validFrom": start.isoformat(),
                                            "reason": why})
        await self._changed(revision)
        return {"added": len(fresh), "skipped": skipped, "group": await self.detail(group_id)}

    async def end_members(self, group_id: str, student_ids: Ids, *,
                          valid_to: Any = None, reason: Any = "",
                          actor: str) -> dict[str, Any]:
        """Çıkarma: `validTo` (dahil, son gün) o gün geçerli üyeliğe yazılır.

        O gün geçerli üyeliği olmayan öğrenci `skipped` içinde döner.
        """
        students = _clean_students(student_ids)
        end = parse_day(valid_to, field="validTo") if valid_to else self._today()
        why = _clean_reason(reason)
        self._need_reason([end], why)
        async with self._lock:
            await self._group_row(group_id)
            existing = await self._member_rows([str(group_id)])
            targets: list[dict[str, Any]] = []
            skipped: list[str] = []
            for student in students:
                match = next((member for member in existing
                              if str(member["kantin_id"]) == student
                              and (_day(str(member["valid_from"])) or date.min) <= end
                              and (_day(str(member["valid_to"])) is None
                                   or end < (_day(str(member["valid_to"])) or date.max))),
                             None)
                if match is None:
                    skipped.append(student)
                else:
                    targets.append(match)
            if not targets:
                return {"ended": 0, "skipped": skipped, "group": await self.detail(group_id)}
            revision = await self._bump()
            stamp = _now()
            for member in targets:
                await self._store.execute(
                    f"UPDATE {self._members} SET valid_to = ?, reason = ?, updated_at = ?, "
                    f"updated_by = ?, revision = ? WHERE id = ?",
                    (end.isoformat(), why or str(member["reason"] or ""), stamp, actor,
                     revision, str(member["id"])))
            await self._touch_group(group_id, actor=actor, revision=revision)
            await self._write_audit(actor=actor, action="members_end", group_id=str(group_id),
                                    revision=revision,
                                    detail={"students": [str(m["kantin_id"]) for m in targets],
                                            "validTo": end.isoformat(), "reason": why})
        await self._changed(revision)
        return {"ended": len(targets), "skipped": skipped, "group": await self.detail(group_id)}

    async def move(self, student_ids: Ids, *, from_group: str, to_group: str,
                   effective_from: Any = None, reason: Any = "",
                   actor: str) -> dict[str, Any]:
        """"Taşı" — A'da `validTo = d-1`, B'de `validFrom = d`. Tek işlem: önce
        iki tarafın kuralı birlikte doğrulanır, sonra ikisi birden yazılır."""
        students = _clean_students(student_ids)
        if str(from_group) == str(to_group):
            raise GroupError("Kaynak ve hedef grup aynı olamaz.")
        start = (parse_day(effective_from, field="effectiveFrom") if effective_from
                 else self._today())
        last = start - timedelta(days=1)
        why = _clean_reason(reason)
        self._need_reason([last], why)
        async with self._lock:
            source = await self._group_row(from_group)
            target = await self._group_row(to_group)
            if target["archived_at"]:
                raise GroupError("Arşivlenmiş gruba taşınamaz.", status=409,
                                 code="group_archived")
            source_rows = await self._member_rows([str(from_group)])
            ending: list[dict[str, Any]] = []
            for student in students:
                match = next((member for member in source_rows
                              if str(member["kantin_id"]) == student
                              and (_day(str(member["valid_from"])) or date.min) <= last
                              and (_day(str(member["valid_to"])) is None
                                   or last < (_day(str(member["valid_to"])) or date.max))),
                             None)
                if match is None:
                    raise GroupError("Öğrencinin kaynak grupta taşıma gününden önce başlayan "
                                     "açık üyeliği yok.", status=409, code="not_a_member",
                                     details={"studentId": student})
                ending.append(match)
            target_rows = await self._member_rows([str(to_group)])
            planned = target_rows + [{"kantin_id": student, "valid_from": start.isoformat(),
                                      "valid_to": ""} for student in students]
            self._check_members(str(target["kind"]), planned)
            revision = await self._bump()
            stamp = _now()
            for member in ending:
                await self._store.execute(
                    f"UPDATE {self._members} SET valid_to = ?, reason = ?, updated_at = ?, "
                    f"updated_by = ?, revision = ? WHERE id = ?",
                    (last.isoformat(), why or str(member["reason"] or ""), stamp, actor,
                     revision, str(member["id"])))
            for student in students:
                await self._insert_member(str(to_group), student, start, None, reason=why,
                                          actor=actor, revision=revision, stamp=stamp)
            await self._touch_group(from_group, actor=actor, revision=revision)
            await self._touch_group(to_group, actor=actor, revision=revision)
            detail = {"students": students, "from": str(from_group), "to": str(to_group),
                      "effectiveFrom": start.isoformat(), "reason": why}
            await self._write_audit(actor=actor, action="move_out", group_id=str(from_group),
                                    revision=revision, detail=detail)
            await self._write_audit(actor=actor, action="move_in", group_id=str(to_group),
                                    revision=revision, detail=detail)
        await self._changed(revision)
        self._log.info("öğrenciler taşındı", students=len(students), source=source["id"],
                       target=target["id"], actor=actor)
        return {"moved": len(students), "from": await self.detail(from_group),
                "to": await self.detail(to_group)}

    async def patch_member(self, group_id: str, member_id: str, payload: dict[str, Any], *,
                           actor: str) -> dict[str, Any]:
        """Yanlış girilmiş başlangıç/son gün düzeltmesi. Geçmişe dokunan düzeltme
        gerekçe ister. Akademi'nin dondurulmuş yoklama listeleri DEĞİŞMEZ (§2.5)."""
        async with self._lock:
            group = await self._group_row(group_id)
            rows = await self._member_rows([str(group_id)])
            member = next((row for row in rows if str(row["id"]) == str(member_id)), None)
            if member is None:
                raise GroupError("Üyelik bulunamadı.", status=404, code="member_not_found")
            old_from = _day(str(member["valid_from"]))
            old_to = _day(str(member["valid_to"]))
            new_from = (parse_day(payload["validFrom"], field="validFrom")
                        if payload.get("validFrom") else old_from)
            if "validTo" in payload:
                new_to = (parse_day(payload["validTo"], field="validTo")
                          if payload["validTo"] else None)
            else:
                new_to = old_to
            if new_from == old_from and new_to == old_to:
                return await self.detail(group_id)
            why = _clean_reason(payload.get("reason"))
            touched: list[date | None] = []
            if new_from != old_from:
                touched += [old_from, new_from]
            if new_to != old_to:
                touched += [old_to, new_to]
            self._need_reason(touched, why)
            planned = [row if str(row["id"]) != str(member_id) else {
                **row, "valid_from": new_from.isoformat() if new_from else "",
                "valid_to": new_to.isoformat() if new_to else ""} for row in rows]
            self._check_members(str(group["kind"]), planned)
            revision = await self._bump()
            await self._store.execute(
                f"UPDATE {self._members} SET valid_from = ?, valid_to = ?, reason = ?, "
                f"updated_at = ?, updated_by = ?, revision = ? WHERE id = ?",
                (new_from.isoformat() if new_from else "", new_to.isoformat() if new_to else "",
                 why or str(member["reason"] or ""), _now(), actor, revision, str(member_id)))
            await self._touch_group(group_id, actor=actor, revision=revision)
            await self._write_audit(
                actor=actor, action="member_fix", group_id=str(group_id), revision=revision,
                detail={"student": str(member["kantin_id"]),
                        "before": [str(member["valid_from"]), str(member["valid_to"]) or None],
                        "after": [new_from.isoformat() if new_from else None,
                                  new_to.isoformat() if new_to else None],
                        "reason": why})
        await self._changed(revision)
        return await self.detail(group_id)

    # ------------------------------------------------------------ iç yazma

    async def _insert_member(self, group_id: str, student: str, start: date, end: date | None,
                             *, reason: str, actor: str, revision: int, stamp: str) -> None:
        await self._store.execute(
            f"INSERT INTO {self._members} (id, group_id, kantin_id, valid_from, valid_to, "
            f"reason, created_at, created_by, updated_at, updated_by, revision) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), group_id, student, start.isoformat(),
             end.isoformat() if end else "", reason, stamp, actor, stamp, actor, revision),
        )

    async def _touch_group(self, group_id: str, *, actor: str, revision: int,
                           extra: dict[str, Any] | None = None) -> None:
        """Grubun `revision`/`updated_*` damgası — üyelik değişince de yükselir."""
        columns = dict(extra or {})
        columns.update({"updated_at": _now(), "updated_by": actor, "revision": revision})
        assignments = ", ".join(f"{name} = ?" for name in columns)
        await self._store.execute(
            f"UPDATE {self._groups} SET {assignments} WHERE id = ?",
            (*columns.values(), str(group_id)))


class StudentGroups:
    """`bbd_students.groups` BACKEND yeteneği — servisin yalnız sözleşmedeki yüzü.

    Tüketiciler (mobil geçit, zil) yazma ucu görmez; tek istisna taşıma için
    `import_named` (§6).
    """

    def __init__(self, service: GroupService) -> None:
        self._service = service

    async def list(self, since_revision: int | None = None) -> dict[str, Any]:
        return await self._service.list(since_revision)

    async def get(self, group_id: str) -> dict[str, Any] | None:
        return await self._service.get(group_id)

    async def for_student(self, student_id: str) -> Records:
        rows = await self._service.for_student(student_id)
        return [{"group": row["group"], "validFrom": row["validFrom"],
                 "validTo": row["validTo"]} for row in rows]

    async def import_named(self, name: str, *, kind: str, call_name: str = "",
                           actor: str) -> dict[str, Any]:
        return await self._service.import_named(name, kind=kind, call_name=call_name,
                                                actor=actor)

    async def revision(self) -> int:
        return await self._service.revision()
