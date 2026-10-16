"""Sınıflar (eski adıyla öğrenci grupları) ve TARİHLİ üyelik — `bbd_students.groups` yeteneği.

SINIF = GRUP (kullanıcı kararı 2026-10-14, iş 23; göç 009). Tek kavram "Sınıf":
bu tablo hem eski grupları hem öğrencinin `class_name`'inden dönüşen sınıfları
tutar (`origin`: 'group' | 'class'); bir öğrenci birden çok sınıfa EŞİT üye
olabilir. Kod ve şema adları ("group") kaldı; arayüz "Sınıf" der. Sınıfın
isteğe bağlı seviyesi (`level`) ŞABLONDUR: öğrenci eklenirken / "üyelere
uygula" ile öğrencinin kendi seviyesine yazılır (`apply_level` kancası).

İKİ GÖRÜNÜM (`list(view=…)`): `classes` her şeyi verir (yeni Akademi,
`legacyClassName` + `classLevel` ile); `legacy` (varsayılan, eski Akademi) dönüşen
sınıfları ve dönüşümün eklediği üyelikleri GİZLER — eski Akademi onları
dizindeki `className`'den `km_class` olarak zaten görüyor, çift kayıt olmasın.

Sözleşme: bbd-akademi/docs/integrations/km-ogrenci-gruplari-ve-zil.md §2.
GRUBUN TEK SAHİBİ BURASIDIR. BBD Akademi grubu buradan çeker (mobil geçit
`GET /groups`), zil sistemi elle çağrı listesini buradan okur — ikisi de
yetenek üzerinden (K3), tabloya bakmadan (K5).

    list(since_revision=None, *, view="legacy"|"classes")
                              → {"revision", "full", "view", "groups": [GroupRecord], "complete"}
    get(group_id)             → GroupRecord | None
    for_student(student_id)   → [{"group": GroupSummary, "validFrom", "validTo"}]
    import_named(name, *, kind, call_name="", actor) → {"id", "created"}

`GroupRecord` = {id, name, callName, kind, archived, archivedAt, updatedAt,
revision, members: [{studentId, validFrom, validTo}]} (+ `classes` görünümünde
`origin`, `legacyClassName`, `classLevel`) — `members` grubun
BÜTÜN üyelik satırlarıdır (geçmiş dahil). Tarihler Europe/Istanbul yerel
GÜNÜDÜR; iki uç da dahildir; `validTo: null` açık uç.

KURALLAR (§2.1) TEK YERDE, BURADA:
  · ad tekilliği canlı gruplar arasında, Türkçe duyarlı katlamayla (`fold`);
  · aynı öğrenci aynı grupta kesişen iki üyelik taşıyamaz;
  · `individual` grupta herhangi bir günde en çok bir geçerli üye olur;
  · geçmiş tarihli değişiklik (bugünden önceki bir gün) gerekçe ister ve
    denetim izine yazılır;
  · arşivde açık üyelikler arşiv gününde kapanır; KALICI SİLME yalnız arşivdeki
    sınıfta, ad yazılarak + gerekçe + PIN (uçta) — satır ve üyelikleri gider,
    kimliği mezar taşında kalır (artımlı akış `deleted: true`), denetim izi kalır;
  · her sınıfın DÖNEMİ ("2026-2027") vardır (göç 011); yeni sınıfta varsayılan
    güncel dönem, boş bırakılamaz.

REVİZYON. Her yazma kalıcı sayacı (`mod_bbd_students_group_rev`) +1 artırır ve
dokunduğu grup ile üyelik satırlarına damgalar; grubun `revision`'ı üyeliği
değişince de yükselir — artımlı okuma (`since`) bu yüzden tek indeksli sorgudur.

İŞLEM (ADR 0029). Her yazma TEK İŞLEMDİR ve işlemin İLK adımı sayacı
`revision = revision + 1` ile artırmaktır: sayaç satırının kilidi aynı anda
gelen grup yazmalarını commit'e kadar sıralar (SQLite'ta depo kilidi), sonraki
doğrulama okumaları öncekinin commit'ini görür. Böylece sayaç, doğrulama ve
yazma arasına başka bir yazma giremez — eskiden bunu süreç içi bir kilit
sağlıyordu ("modül deposunda işlem açılamıyor" notu artık geçersiz). Değişiklik
yoksa ya da kural reddederse işlem GERİ ALINIR: revizyon harcanmaz, davranış
eskisiyle aynıdır. Öğrenci pasifleştirme de üyelikleri aynı yoldan, öğrenci
işleminin İÇİNDE kapatır (`close_student_memberships`).

OLAY. Aynı işlemde outbox'a `group.changed` (`revision` = grup revizyonu,
km-merkezi-senkron §2.2) yazılır; commit'ten sonra süreç içi
`bbd_students.groups_changed` (yük `{revision}`) yayınlanır — yayın patlarsa
yazma yine tamamdır (K7, `bell.week_changed` kalıbı).
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from . import curriculum
from .academic_year import current_year, parse_year, year_choices

ISTANBUL = ZoneInfo("Europe/Istanbul")

KINDS = ("group", "individual")
#: `list(view=…)`: eski Akademi (`legacy`) ya da tek "Sınıf" modeli (`classes`).
VIEWS = ("legacy", "classes")
#: Satırın kökeni: eski grup / ekrandan açılan sınıf ya da `class_name`'den dönüşen sınıf.
ORIGIN_GROUP, ORIGIN_CLASS = "group", "class"
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
#: Kantin `opaque_id` (öğrenci kimliği) — harf/rakam (Türkçe dahil), `_`, `-`.
#: CANLIDA kimliklerin bir kısmı Türkçe harf taşıyor (kullanıcı adından türemiş,
#: ör. "…yazılım"); eski ASCII kuralı bu öğrencileri gruba eklemeyi 400 "Geçersiz
#: öğrenci kimliği" ile reddediyor ve TOPLU "Gruba ekle" isteğinin TAMAMI
#: düşüyordu (13.10.2026). Boşluk, nokta, eğik çizgi hâlâ reddedilir.
_STUDENT_ID = re.compile(r"^[\w-]{1,64}$")

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
        raise GroupError("Sınıf adı boş olamaz.", details={"field": "name"})
    if len(name) > NAME_MAX:
        raise GroupError(f"Sınıf adı en çok {NAME_MAX} karakter olabilir.",
                         details={"field": "name"})
    return name


def level_of(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """Sınıf satırının seviyesi `{gradeLevel, track, examTarget}` ya da None (girilmemiş)."""
    return curriculum.class_from_row(row) if row else None


def parse_level(value: Any) -> dict[str, Any]:
    """Gövdedeki `level` → yazılacak sütunlar. `None` seviyeyi kaldırır.

    Kural göç 008'in sınıf tanımıyla AYNIDIR (`curriculum.resolve_class`): seviye
    zorunlu, alan yalnız 10. sınıf ve üstünde / mezunda.
    """
    if value is None:
        return {"grade_level": None, "track": None, "exam_target": None}
    if not isinstance(value, dict):
        raise GroupError("Seviye {gradeLevel, track, examTarget} olmalı.",
                         details={"field": "level"})
    patch = {key: value.get(key) for key in ("gradeLevel", "track", "examTarget")
             if key in value}
    try:
        # Akademi README §14 madde 102: grup için seviye isteğe bağlı (yalnız hedef sınav / alan olabilir).
        return curriculum.resolve_group(patch)
    except curriculum.CurriculumError as failure:
        raise GroupError(str(failure), details={"field": "level"}) from None


def clean_year(value: Any) -> str:
    """Sınıfın dönemi ("2026-2027"). Kural `academic_year.parse_year`'dadır."""
    try:
        return parse_year(value)
    except ValueError as failure:
        raise GroupError(str(failure), details={"field": "academicYear"}) from None


def _clean_kind(value: Any) -> str:
    kind = str(value or "group").strip()
    if kind not in KINDS:
        raise GroupError("Tür “group” (toplu ders) ya da “individual” (özel ders) olmalı.",
                         details={"field": "kind"})
    return kind


def _clean_view(value: Any) -> str:
    view = str(value or "legacy").strip()
    if view not in VIEWS:
        raise GroupError("Görünüm “legacy” ya da “classes” olmalı.", details={"field": "view"})
    return view


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


class _Unchanged(Exception):
    """Yazma bir şey değiştirmedi: işlem geri alınır (revizyon harcanmaz)."""

    def __init__(self, value: Any = None) -> None:
        super().__init__("değişiklik yok")
        self.value = value


class GroupService:
    """Grup ve üyelik iş kuralları + `bbd_students.groups` yeteneğinin gövdesi."""

    def __init__(self, *, store: Any, log: Any,
                 publish: Callable[[int], Awaitable[None]] | None = None,
                 today: Callable[[], date] = today_local,
                 outbox: Callable[[], Any] | None = None,
                 students_changed: Callable[..., Awaitable[Any]] | None = None,
                 apply_level: Callable[[list[str], dict[str, Any]], Awaitable[int]]
                 | None = None,
                 deleted: Callable[[str, int], Awaitable[None]] | None = None) -> None:
        self._store = store
        self._log = log
        self._publish = publish
        self._today = today
        #: Üyelik değişince öğrencinin türetilmiş alanları (`class_ids`, eski
        #: `class_name`) + `student.updated` — AÇIK İŞLEMİN İÇİNDE çağrılır
        #: (`classes.ClassLink.refresh`). Yoksa (testler) atlanır.
        self._students_changed = students_changed
        #: Sınıfın seviyesini öğrencilere yazar (commit'ten SONRA;
        #: `classes.ClassLink.apply_level`). Yoksa seviye uygulanmaz.
        self._apply_level = apply_level
        #: Kalıcı silmeden sonra (commit'ten SONRA) süreç içi `bbd_students.group_deleted`
        #: — zil o sınıfın planlı çağrılarını iptal eder. Yoksa (testler) atlanır.
        self._deleted = deleted
        #: `events.outbox` yeteneğini ÇAĞRI ANINDA çözer (yoksa None) — olay
        #: yazmanın İÇİNDE, aynı işlemde (`group.changed`).
        self._outbox = outbox or (lambda: None)
        self._groups = store.table("group")
        self._members = store.table("group_member")
        self._rev = store.table("group_rev")
        self._audit = store.table("group_log")
        self._profiles = store.table("profile")
        self._tombstones = store.table("group_tombstone")

    # ============================================================== yardım

    async def revision(self) -> int:
        row = await self._store.fetch_one(f"SELECT revision FROM {self._rev} WHERE id = 1")
        return int(row["revision"]) if row else 0

    async def _bump(self) -> int:
        """Kalıcı sayacı +1 artırır ve yeni değeri döner. İŞLEMİN İLK ADIMI.

        `revision = revision + 1`: satır kilidi eşzamanlı yazmaları commit'e kadar
        sıralar (okuyup yazmak iki işlemde aynı değeri üretebilirdi).
        """
        await self._store.execute(
            f"INSERT OR IGNORE INTO {self._rev} (id, revision) VALUES (1, 0)")
        await self._store.execute(
            f"UPDATE {self._rev} SET revision = revision + 1 WHERE id = 1")
        return await self.revision()

    async def _emit(self, revision: int) -> None:
        """Outbox'a `group.changed` — AÇIK İŞLEMİN İÇİNDE (aynı commit)."""
        outbox = self._outbox()
        if outbox is not None:
            await outbox.emit("group.changed", ("groups", None), revision=revision)

    async def _changed(self, revision: int) -> None:
        if self._publish is None:
            return
        try:
            await self._publish(revision)
        except Exception as failure:  # noqa: BLE001 — yayın yazmayı düşürmez (K7)
            self._log.warning("grup değişikliği yayınlanamadı", error=str(failure))

    async def _touched_students(self, students: Iterable[str], *, force: bool = False) -> None:
        """Üyeliği değişen öğrenciler — açık işlemin içinde kancaya bildirilir."""
        wanted = sorted({str(student) for student in students if student})
        if wanted and self._students_changed is not None:
            await self._students_changed(wanted, force=force)

    async def _group_students(self, group_id: str) -> list[str]:
        rows = await self._store.fetch_all(
            f"SELECT DISTINCT kantin_id FROM {self._members} WHERE group_id = ?",
            (str(group_id),))
        return [str(row["kantin_id"]) for row in rows]

    async def _level_to(self, group_id: str, students: list[str]) -> int:
        """Sınıfın seviyesini (varsa) öğrencilere yazar — commit'ten SONRA."""
        if not students or self._apply_level is None:
            return 0
        plan = level_of(await self._group_row(group_id))
        if plan is None:
            return 0
        try:
            return await self._apply_level(students, plan)
        except Exception as failure:  # noqa: BLE001 — üyelik yazıldı; seviye ayrı adım
            self._log.warning("sınıf seviyesi öğrencilere yazılamadı", group=group_id,
                              error=str(failure))
            return 0

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
            raise GroupError("Sınıf bulunamadı.", status=404, code="group_not_found")
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
                    raise GroupError("Öğrencinin bu sınıfta aynı tarihlere düşen başka bir "
                                     "üyeliği var.", status=409, code="membership_overlap",
                                     details={"studentId": student})
                if kind == "individual":
                    raise GroupError("Özel ders sınıfında aynı gün yalnız bir öğrenci "
                                     "olabilir.", status=409, code="individual_single_member",
                                     details={"studentId": other})

    async def _check_name_free(self, name: str, *, except_id: str = "") -> None:
        rows = await self._store.fetch_all(
            f"SELECT id FROM {self._groups} WHERE name_fold = ? AND archived_at = ''",
            (fold(name),))
        if any(str(row["id"]) != except_id for row in rows):
            raise GroupError(f"“{name}” adında bir sınıf zaten var.", status=409,
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
            # Sınıf = grup (göç 009). Eski görünüm bu üç alanı DÜŞÜRÜR (`list`).
            "origin": str(row.get("origin") or ORIGIN_GROUP),
            "legacyClassName": str(row.get("legacy_class_name") or "") or None,
            "classLevel": level_of(row),
            # Müfredat seviyesi (Akademi README §14 madde 102): seviyesiz de olabilir (yalnız hedef / alan).
            "curriculumLevel": curriculum.group_from_row(row),
            # Dönem (göç 011). Eski görünüm bu alanı da DÜŞÜRÜR (`_shape`).
            "academicYear": str(row.get("academic_year") or "") or None,
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
        out = []
        for row in rows:
            members_of = by_group.get(str(row["id"]), [])
            record = self._record(row, members_of, full=full)
            # Üyeliğin kökeni yalnız eski görünüm süzgeci içindir (`_shape` düşürür).
            record["_origins"] = [str(member.get("origin") or ORIGIN_GROUP)
                                  for member in members_of]
            out.append(record)
        return out

    # ======================================================== yetenek (okuma)

    async def list(self, since_revision: int | None = None, *,
                   view: str = "legacy") -> dict[str, Any]:
        """`bbd_students.groups.list` — tam ya da `revision > since` olan sınıflar.

        Her kayıt TAM üye listesiyle döner (istemci kaydı bütün olarak değiştirir).
        Kaynak yerel tablodur; okuma yarıda kalmaz, `complete` her zaman doğrudur.

        `view="legacy"` (varsayılan; eski Akademi): `class_name`'den dönüşen
        sınıflar ve dönüşümün eklediği üyelikler YOK, kayıtta yeni alanlar
        (`origin`, `legacyClassName`, `classLevel`) yok — biçim birebir eskisidir.
        `view="classes"`: hepsi, yeni alanlarla (`academicYear` dahil). `since` ile
        KALICI SİLİNEN sınıflar mezar taşı olarak gelir: `{id, deleted: true,
        deletedAt, revision}` (yalnız bu görünümde — eski Akademi bilmez; tam
        listede silinen sınıf yoktur, istemci aynasını tam listeyle kurar).
        """
        view = _clean_view(view)
        revision = await self.revision()
        if since_revision is None:
            rows = await self._store.fetch_all(
                f"SELECT * FROM {self._groups} ORDER BY name_fold, id")
            groups = await self._records(self._visible(rows, view), all_members=True)
            return {"revision": revision, "full": True, "view": view,
                    "groups": self._shape(groups, view), "complete": True}
        rows = await self._store.fetch_all(
            f"SELECT * FROM {self._groups} WHERE revision > ? ORDER BY revision, id",
            (int(since_revision),))
        groups = self._shape(await self._records(self._visible(rows, view)), view)
        if view == "classes":
            groups += await self._tombstones_since(int(since_revision))
        return {"revision": revision, "full": False, "view": view,
                "groups": groups, "complete": True}

    async def _tombstones_since(self, since_revision: int) -> list[dict[str, Any]]:
        rows = await self._store.fetch_all(
            f"SELECT id, deleted_at, revision FROM {self._tombstones} WHERE revision > ? "
            f"ORDER BY revision, id", (since_revision,))
        return [{"id": str(row["id"]), "deleted": True,
                 "deletedAt": _local_iso(str(row["deleted_at"])),
                 "revision": int(row["revision"] or 0)} for row in rows]

    @staticmethod
    def _visible(rows: list[dict[str, Any]], view: str) -> list[dict[str, Any]]:
        if view == "classes":
            return [dict(row) for row in rows]
        return [dict(row) for row in rows if str(row.get("origin") or ORIGIN_GROUP) != ORIGIN_CLASS]

    @staticmethod
    def _shape(groups: list[dict[str, Any]], view: str) -> list[dict[str, Any]]:
        if view == "classes":
            for group in groups:
                group.pop("_origins", None)
            return groups
        out = []
        for group in groups:
            origins = group.pop("_origins", [])
            members = [member for member, origin in zip(group["members"], origins, strict=True)
                       if origin != ORIGIN_CLASS]
            out.append({key: value for key, value in group.items()
                        if key not in ("origin", "legacyClassName", "classLevel",
                                       "curriculumLevel", "academicYear")}
                       | {"members": members})
        return out

    async def get(self, group_id: str) -> dict[str, Any] | None:
        row = await self._store.fetch_one(
            f"SELECT * FROM {self._groups} WHERE id = ?", (str(group_id),))
        if row is None:
            return None
        return self._shape(await self._records([dict(row)]), "classes")[0]

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
                          "archived": bool(group["archived_at"]),
                          "classLevel": level_of(dict(group))},
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
                       on: date | None = None, year: str = "") -> dict[str, Any]:
        """Sınıflar ekranının listesi: bugünkü üye sayısıyla. İki sorgu.

        `year`: '' = bütün dönemler, "none" = dönemi girilmemiş, "YYYY-YYYY" = o dönem.
        Yanıtta `currentYear` (bugünün dönemi) ve `years` (verideki dönemler ∪ seçici
        dönemleri — süzgeç ve form seçicisi) da gelir.
        """
        day = on or self._today()
        rows = await self._store.fetch_all(
            f"SELECT * FROM {self._groups} ORDER BY name_fold, id")
        needle = fold(query)
        wanted_year = str(year or "").strip()
        chosen = [dict(row) for row in rows
                  if (archived == "all" or (archived == "archived") == bool(row["archived_at"]))
                  and (not kind or str(row["kind"]) == kind)
                  and (not wanted_year
                       or (wanted_year == "none" and not row.get("academic_year"))
                       or str(row.get("academic_year") or "") == wanted_year)
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
                "origin": str(row.get("origin") or ORIGIN_GROUP),
                "legacyClassName": str(row.get("legacy_class_name") or "") or None,
                "classLevel": level_of(row),
                "academicYear": str(row.get("academic_year") or "") or None,
                "label": curriculum.class_label(str(row["name"]), level_of(row))
                if level_of(row) else str(row["name"]),
            })
        return {"date": day.isoformat(), "revision": await self.revision(), "groups": groups,
                "currentYear": current_year(self._today()),
                "years": year_choices(self._today(), extra=[
                    str(row.get("academic_year") or "") for row in rows])}

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
        record = self._shape(await self._records([row], full=True), "classes")[0]
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
        """Sınıf = ad + öğrenci seçimi. `studentIds` verilirse `validFrom`'dan üye olur.

        `level` (isteğe bağlı) sınıfın seviyesidir; `applyLevel` (varsayılan açık)
        üyelerin kendi seviyesine yazılır.
        """
        name = _clean_name(payload.get("name"))
        kind = _clean_kind(payload.get("kind"))
        level = parse_level(payload.get("level")) if payload.get("level") is not None else None
        call_name = " ".join(str(payload.get("callName") or "").split())[:CALL_NAME_MAX]
        note = str(payload.get("note") or "").strip()[:NOTE_MAX]
        # Dönem: verilmezse bugünün dönemi (1 Eylül'de döner).
        year = (clean_year(payload["academicYear"]) if payload.get("academicYear")
                else current_year(self._today()))
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

        async with self._store.transaction():
            revision = await self._bump()
            await self._check_name_free(name)
            live = await self._store.fetch_one(
                f"SELECT COUNT(*) AS n FROM {self._groups} WHERE archived_at = ''")
            if live is not None and int(live["n"]) >= GROUP_MAX:
                raise GroupError(f"En çok {GROUP_MAX} canlı grup olabilir.", status=409,
                                 code="group_limit")
            stamp = _now()
            group_id = await self._insert_group(name=name, kind=kind, call_name=call_name,
                                                note=note, level=level, year=year, actor=actor,
                                                revision=revision, stamp=stamp)
            for student in students:
                await self._insert_member(group_id, student, start, None, reason=reason,
                                          actor=actor, revision=revision, stamp=stamp)
            await self._touched_students(students)
            await self._write_audit(actor=actor, action=action, group_id=group_id,
                                    revision=revision,
                                    detail={"name": name, "kind": kind, "academicYear": year,
                                            "students": len(students),
                                            "validFrom": start.isoformat() if students else None})
            await self._emit(revision)
        await self._changed(revision)
        if level is not None and payload.get("applyLevel", True) is not False:
            await self._level_to(group_id, students)
        self._log.info("sınıf açıldı", group=group_id, kind=kind,
                       students=len(students), actor=actor)
        return await self.detail(group_id)

    async def from_class(self, class_name: str, payload: dict[str, Any], *,
                         actor: str) -> dict[str, Any]:
        """"Sınıftan yeni sınıf": kaynak sınıfın BUGÜNKÜ üyeleri yeni sınıfa KOPYALANIR.

        Kaynak, adı (Türkçe katlamayla) eşleşen canlı sınıftır; yoksa (dönüşüm
        öncesi) öğrencinin eski `class_name`'i.
        """
        clean = " ".join(str(class_name or "").split())
        if not clean:
            raise GroupError("Sınıf seçin.", details={"field": "className"})
        source = await self._store.fetch_one(
            f"SELECT id FROM {self._groups} WHERE name_fold = ? AND archived_at = '' "
            f"ORDER BY created_at LIMIT 1", (fold(clean),))
        if source is not None:
            day = self._today().isoformat()
            rows = await self._store.fetch_all(
                f"SELECT DISTINCT kantin_id FROM {self._members} WHERE group_id = ? "
                f"AND valid_from <= ? AND (valid_to = '' OR valid_to >= ?) ORDER BY kantin_id",
                (str(source["id"]), day, day))
        else:
            rows = await self._store.fetch_all(
                f"SELECT kantin_id FROM {self._profiles} WHERE class_name = ? "
                f"ORDER BY kantin_id", (clean,))
        students = [str(row["kantin_id"]) for row in rows]
        if not students:
            raise GroupError(f"“{clean}” sınıfında öğrenci yok.", status=404,
                             code="class_empty")
        body = {**payload, "studentIds": students}
        if not body.get("name"):
            body["name"] = clean
        body["kind"] = "group"
        return await self.create(body, actor=actor, action="from_class")

    async def update(self, group_id: str, payload: dict[str, Any], *,
                     actor: str) -> dict[str, Any]:
        """Ad, tür, anons adı, not, seviye, dönem. Gönderilmeyen alan değişmez.

        `level` değişip `applyLevel` (varsayılan açık) ise sınıfın BUGÜNKÜ üyelerinin
        seviyesi yazılır (eski "sınıfın seviyesi öğrencilere geçer" davranışı).
        """
        try:
            async with self._store.transaction():
                revision = await self._bump()
                changes = await self._update_in(group_id, payload, actor=actor,
                                                revision=revision)
                if "name" in changes:
                    # Ad değişti: dizindeki `classes[].name` değişir → üyeler `since`'te görünsün.
                    await self._touched_students(await self._group_students(group_id),
                                                 force=True)
                await self._emit(revision)
        except _Unchanged:
            changes = {}
        else:
            await self._changed(revision)
        if "grade_level" in changes and payload.get("applyLevel", True) is not False:
            await self._level_to(group_id, await self.current_students(group_id))
        return await self.detail(group_id)

    async def current_students(self, group_id: str, *, on: date | None = None) -> list[str]:
        """Sınıfın o gün (vars. bugün) geçerli üyeleri — tek indeksli sorgu."""
        day = (on or self._today()).isoformat()
        rows = await self._store.fetch_all(
            f"SELECT DISTINCT kantin_id FROM {self._members} WHERE group_id = ? "
            f"AND valid_from <= ? AND (valid_to = '' OR valid_to >= ?) ORDER BY kantin_id",
            (str(group_id), day, day))
        return [str(row["kantin_id"]) for row in rows]

    async def apply_level_now(self, group_id: str, *, actor: str) -> dict[str, Any]:
        """"Seviyeyi üyelere uygula": sınıfın seviyesini bugünkü üyelere yazar."""
        row = await self._group_row(group_id)
        if level_of(row) is None:
            raise GroupError("Bu sınıfa seviye girilmemiş.", code="level_missing",
                             details={"field": "level"})
        updated = await self._level_to(group_id, await self.current_students(group_id))
        self._log.info("sınıf seviyesi üyelere uygulandı", group=group_id, updated=updated,
                       actor=actor)
        return {"updatedStudents": updated, "group": await self.detail(group_id)}

    async def _update_in(self, group_id: str, payload: dict[str, Any], *, actor: str,
                         revision: int) -> dict[str, Any]:
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
        if "level" in payload:
            level = parse_level(payload["level"])
            if any((row.get(column) or None) != level[column] for column in level):
                changes.update(level)
        if "academicYear" in payload:
            year = clean_year(payload["academicYear"])
            if year != (row.get("academic_year") or ""):
                changes["academic_year"] = year
        if not changes:
            raise _Unchanged
        await self._touch_group(group_id, actor=actor, revision=revision, extra=changes)
        await self._write_audit(actor=actor, action="update", group_id=str(group_id),
                                revision=revision,
                                detail={key: value for key, value in changes.items()
                                        if key != "name_fold"})
        return changes

    async def archive(self, group_id: str, *, actor: str) -> dict[str, Any]:
        """Arşivler. Açık üyelikler arşiv gününde kapanır; henüz başlamamış
        üyelikler (başlangıcı bugünden sonra) hiç yürürlüğe girmediği için
        kaldırılır ve denetim izine yazılır. Geçmiş satırlara dokunulmaz."""
        today = self._today()
        try:
            async with self._store.transaction():
                revision = await self._bump()
                closed = await self._archive_in(group_id, actor=actor, revision=revision,
                                                today=today)
                await self._touched_students(await self._group_students(group_id))
                await self._emit(revision)
        except _Unchanged:
            return await self.detail(group_id)
        await self._changed(revision)
        self._log.info("sınıf arşivlendi", group=group_id, closed=closed, actor=actor)
        return await self.detail(group_id)

    async def _archive_in(self, group_id: str, *, actor: str, revision: int,
                          today: date) -> int:
        row = await self._group_row(group_id)
        if row["archived_at"]:
            raise _Unchanged
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
        return closed

    async def restore(self, group_id: str, *, actor: str) -> dict[str, Any]:
        """Arşivden geri alır. Üyelikler kendiliğinden açılmaz."""
        try:
            async with self._store.transaction():
                revision = await self._bump()
                row = await self._group_row(group_id)
                if not row["archived_at"]:
                    raise _Unchanged
                await self._check_name_free(str(row["name"]), except_id=str(group_id))
                await self._touch_group(group_id, actor=actor, revision=revision,
                                        extra={"archived_at": ""})
                await self._write_audit(actor=actor, action="restore", group_id=str(group_id),
                                        revision=revision, detail={})
                await self._touched_students(await self._group_students(group_id))
                await self._emit(revision)
        except _Unchanged:
            return await self.detail(group_id)
        await self._changed(revision)
        return await self.detail(group_id)

    async def set_year(self, group_ids: Ids, year: Any, *, actor: str) -> dict[str, Any]:
        """Toplu "dönem ata": seçili sınıflara (arşivliler dahil) aynı dönem — TEK işlem.

        Zaten o dönemde olan sınıf `skipped` içinde döner; hiçbiri değişmezse
        revizyon harcanmaz.
        """
        clean = clean_year(year)
        ids: list[str] = []
        for value in group_ids:
            text = str(value or "").strip()
            if text and text not in ids:
                ids.append(text)
        if not ids:
            raise GroupError("En az bir sınıf seçin.", details={"field": "groupIds"})
        if len(ids) > GROUP_MAX:
            raise GroupError(f"Tek seferde en çok {GROUP_MAX} sınıf.",
                             details={"field": "groupIds"})
        try:
            async with self._store.transaction():
                revision = await self._bump()
                rows = {str(row["id"]): dict(row) for row in await self._store.fetch_all(
                    f"SELECT id, academic_year FROM {self._groups} "
                    f"WHERE id IN ({', '.join('?' for _ in ids)})", tuple(ids))}
                missing = [group_id for group_id in ids if group_id not in rows]
                if missing:
                    raise GroupError("Sınıf bulunamadı.", status=404, code="group_not_found",
                                     details={"groupIds": missing[:20]})
                changed = [group_id for group_id in ids
                           if str(rows[group_id].get("academic_year") or "") != clean]
                if not changed:
                    raise _Unchanged
                for group_id in changed:
                    await self._touch_group(group_id, actor=actor, revision=revision,
                                            extra={"academic_year": clean})
                    await self._write_audit(
                        actor=actor, action="year_set", group_id=group_id, revision=revision,
                        detail={"academicYear": clean,
                                "before": rows[group_id].get("academic_year") or None})
                await self._emit(revision)
        except _Unchanged:
            return {"updated": 0, "skipped": ids, "academicYear": clean}
        await self._changed(revision)
        self._log.info("sınıflara dönem atandı", donem=clean, sinif=len(changed), actor=actor)
        return {"updated": len(changed),
                "skipped": [group_id for group_id in ids if group_id not in changed],
                "academicYear": clean}

    async def delete(self, group_id: str, *, confirm_name: Any, reason: Any,
                     actor: str) -> dict[str, Any]:
        """ARŞİVDEKİ sınıfı KALICI siler (geri alınamaz).

        Ön koşul: sınıf arşivde (409 `group_not_archived`); onay için sınıfın adı
        Türkçe katlamayla aynen yazılır (422 `confirm_mismatch`); gerekçe zorunlu
        (422 `reason_required`). PIN teyidi uçtadır.

        TEK işlem: üyelik satırları ve sınıf satırı silinir, kimlik mezar taşına
        (`group_tombstone`) yazılır — artımlı akış `{id, deleted: true}` verir;
        denetim izine `delete` (ad, gerekçe, silinen satır sayısı) yazılır; geçmiş
        denetim satırları KALIR. Üyeleri olan öğrencilerin türetilmiş alanları
        (`class_ids`) aynı işlemde yenilenir; `group.changed` (outbox). Commit'ten
        sonra `groups_changed` ve `group_deleted` (zil o sınıfın planlı çağrılarını
        iptal eder). Akademi'de geçmiş (yoklama, oturum, program, müfredat) silinmez.
        """
        why = " ".join(str(reason or "").split())[:REASON_MAX]
        if len(why) < 3:
            raise GroupError("Silme gerekçesini yazın.", status=422, code="reason_required",
                             details={"field": "reason"})
        async with self._store.transaction():
            revision = await self._bump()
            row = await self._group_row(group_id)
            if not row["archived_at"]:
                raise GroupError("Yalnız arşivdeki sınıf kalıcı silinir; önce arşivleyin.",
                                 status=409, code="group_not_archived")
            if fold(str(confirm_name or "")) != fold(str(row["name"])):
                raise GroupError("Onay için sınıfın adını aynen yazın.", status=422,
                                 code="confirm_mismatch", details={"field": "confirmName"})
            students, member_rows = await self._purge_in(row, reason=why, actor=actor,
                                                         revision=revision)
            await self._touched_students(students)
            await self._emit(revision)
        await self._changed(revision)
        if self._deleted is not None:
            try:
                await self._deleted(str(group_id), revision)
            except Exception as failure:  # noqa: BLE001 — silme tamam; bağlı kayıt ayrı adım (K7)
                self._log.warning("sınıf silme olayı yayınlanamadı", group=group_id,
                                  error=str(failure))
        self._log.warning("sınıf kalıcı silindi", group=group_id, name=str(row["name"]),
                          rows=member_rows, actor=actor)
        return {"deleted": True, "id": str(group_id), "name": str(row["name"]),
                "removedMemberships": member_rows, "students": len(students),
                "revision": revision}

    async def _purge_in(self, row: dict[str, Any], *, reason: str, actor: str, revision: int,
                        action: str = "delete") -> tuple[list[str], int]:
        """Sınıf satırını ve üyeliklerini siler, kimliği mezar taşına yazar — AÇIK İŞLEMDE.

        Ön koşullar çağırandadır (`delete`: arşivde + ad + gerekçe; dönem geçişini geri
        alma: geçişin açtığı sınıf). Üyeliği olan öğrencileri ve silinen satır sayısını döner.
        """
        group_id = str(row["id"])
        students = await self._group_students(group_id)
        counted = await self._store.fetch_one(
            f"SELECT COUNT(*) AS n FROM {self._members} WHERE group_id = ?", (group_id,))
        member_rows = int((counted or {}).get("n") or 0)
        stamp = _now()
        await self._store.execute(f"DELETE FROM {self._members} WHERE group_id = ?", (group_id,))
        await self._store.execute(f"DELETE FROM {self._groups} WHERE id = ?", (group_id,))
        await self._store.execute(
            f"INSERT INTO {self._tombstones} (id, name, deleted_at, deleted_by, reason, "
            f"member_rows, revision) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (group_id, str(row["name"]), stamp, actor[:160], reason, member_rows, revision))
        await self._write_audit(
            actor=actor, action=action, group_id=group_id, revision=revision,
            detail={"name": str(row["name"]), "reason": reason, "memberRows": member_rows,
                    "students": len(students),
                    "legacyClassName": str(row.get("legacy_class_name") or "") or None,
                    "academicYear": str(row.get("academic_year") or "") or None})
        return students, member_rows

    async def add_members(self, group_id: str, student_ids: Ids, *,
                          valid_from: Any = None, reason: Any = "",
                          actor: str, apply_level: bool = True) -> dict[str, Any]:
        """Çoklu ekleme. Zaten o tarihte üye olan öğrenci `skipped` içinde döner.

        Sınıfın seviyesi varsa ve `apply_level` açıksa eklenenlerin seviyesi yazılır.
        """
        students = _clean_students(student_ids)
        start = parse_day(valid_from, field="validFrom") if valid_from else self._today()
        why = _clean_reason(reason)
        self._need_reason([start], why)
        try:
            async with self._store.transaction():
                revision = await self._bump()
                skipped, fresh = await self._add_in(group_id, students, start=start, why=why,
                                                    actor=actor, revision=revision)
                await self._touched_students(fresh)
                await self._emit(revision)
        except _Unchanged as unchanged:
            return {"added": 0, "skipped": unchanged.value,
                    "group": await self.detail(group_id)}
        await self._changed(revision)
        leveled = await self._level_to(group_id, fresh) if apply_level else 0
        return {"added": len(fresh), "skipped": skipped, "levelUpdated": leveled,
                "group": await self.detail(group_id)}

    async def _add_in(self, group_id: str, students: list[str], *, start: date, why: str,
                      actor: str, revision: int) -> tuple[list[str], list[str]]:
        row = await self._group_row(group_id)
        if row["archived_at"]:
            raise GroupError("Arşivlenmiş sınıfa öğrenci eklenemez.", status=409,
                             code="group_archived")
        existing = await self._member_rows([str(group_id)])
        skipped = [student for student in students if any(
            str(member["kantin_id"]) == student and _overlaps(
                start, None, _day(str(member["valid_from"])) or date.min,
                _day(str(member["valid_to"])))
            for member in existing)]
        fresh = [student for student in students if student not in skipped]
        if not fresh:
            raise _Unchanged(skipped)
        planned = existing + [{"kantin_id": student, "valid_from": start.isoformat(),
                               "valid_to": ""} for student in fresh]
        self._check_members(str(row["kind"]), planned)
        stamp = _now()
        for student in fresh:
            await self._insert_member(str(group_id), student, start, None, reason=why,
                                      actor=actor, revision=revision, stamp=stamp)
        await self._touch_group(group_id, actor=actor, revision=revision)
        await self._write_audit(actor=actor, action="members_add", group_id=str(group_id),
                                revision=revision,
                                detail={"students": fresh, "validFrom": start.isoformat(),
                                        "reason": why})
        return skipped, fresh

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
        try:
            async with self._store.transaction():
                revision = await self._bump()
                skipped, targets = await self._end_in(group_id, students, end=end, why=why,
                                                      actor=actor, revision=revision)
                await self._touched_students(str(member["kantin_id"]) for member in targets)
                await self._emit(revision)
        except _Unchanged as unchanged:
            return {"ended": 0, "skipped": unchanged.value,
                    "group": await self.detail(group_id)}
        await self._changed(revision)
        return {"ended": len(targets), "skipped": skipped, "group": await self.detail(group_id)}

    async def _end_in(self, group_id: str, students: list[str], *, end: date, why: str,
                      actor: str, revision: int) -> tuple[list[str], list[dict[str, Any]]]:
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
            raise _Unchanged(skipped)
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
        return skipped, targets

    async def move(self, student_ids: Ids, *, from_group: str, to_group: str,
                   effective_from: Any = None, reason: Any = "",
                   actor: str, apply_level: bool = True) -> dict[str, Any]:
        """"Taşı" — A'da `validTo = d-1`, B'de `validFrom = d`. Tek işlem: önce
        iki tarafın kuralı birlikte doğrulanır, sonra ikisi birden yazılır."""
        students = _clean_students(student_ids)
        if str(from_group) == str(to_group):
            raise GroupError("Kaynak ve hedef sınıf aynı olamaz.")
        start = (parse_day(effective_from, field="effectiveFrom") if effective_from
                 else self._today())
        last = start - timedelta(days=1)
        why = _clean_reason(reason)
        self._need_reason([last], why)
        async with self._store.transaction():
            revision = await self._bump()
            source = await self._group_row(from_group)
            target = await self._group_row(to_group)
            if target["archived_at"]:
                raise GroupError("Arşivlenmiş sınıfa taşınamaz.", status=409,
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
                    raise GroupError("Öğrencinin kaynak sınıfta taşıma gününden önce başlayan "
                                     "açık üyeliği yok.", status=409, code="not_a_member",
                                     details={"studentId": student})
                ending.append(match)
            target_rows = await self._member_rows([str(to_group)])
            planned = target_rows + [{"kantin_id": student, "valid_from": start.isoformat(),
                                      "valid_to": ""} for student in students]
            self._check_members(str(target["kind"]), planned)
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
            await self._touched_students(students)
            await self._emit(revision)
        await self._changed(revision)
        if apply_level and start <= self._today():
            await self._level_to(str(to_group), students)
        self._log.info("öğrenciler taşındı", students=len(students), source=source["id"],
                       target=target["id"], actor=actor)
        return {"moved": len(students), "from": await self.detail(from_group),
                "to": await self.detail(to_group)}

    async def patch_member(self, group_id: str, member_id: str, payload: dict[str, Any], *,
                           actor: str) -> dict[str, Any]:
        """Yanlış girilmiş başlangıç/son gün düzeltmesi. Geçmişe dokunan düzeltme
        gerekçe ister. Akademi'nin dondurulmuş yoklama listeleri DEĞİŞMEZ (§2.5)."""
        try:
            async with self._store.transaction():
                revision = await self._bump()
                student = await self._patch_in(group_id, member_id, payload, actor=actor,
                                               revision=revision)
                await self._touched_students([student])
                await self._emit(revision)
        except _Unchanged:
            return await self.detail(group_id)
        await self._changed(revision)
        return await self.detail(group_id)

    async def _patch_in(self, group_id: str, member_id: str, payload: dict[str, Any], *,
                        actor: str, revision: int) -> str:
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
            raise _Unchanged
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
        return str(member["kantin_id"])

    async def close_student_memberships(self, student_id: str, *, last_day: date,
                                        actor: str, reason: str) -> int | None:
        """Öğrencinin açık üyeliklerini `last_day` günü kapatır (pasifleştirme).

        ÇAĞIRANIN İŞLEMİNE KATILIR (km-merkezi-senkron §5.2): öğrenci durumu,
        öğrenci olayı, üyelik kapanışı ve `group.changed` tek commit'tir.
        `last_day`den SONRA başlayan üyelikler hiç yürürlüğe girmediği için
        kaldırılır (arşiv kuralı). Dokunulan bir şey yoksa `None` döner ve
        revizyon harcanmaz; varsa yeni grup revizyonu döner — çağıran commit'ten
        sonra `after_commit(revision)` ile süreç içi olayı yayınlar.
        """
        student = str(student_id)
        rows = await self._store.fetch_all(
            f"SELECT * FROM {self._members} WHERE kantin_id = ?", (student,))
        targets = []
        for member in rows:
            start = _day(str(member["valid_from"])) or date.min
            end = _day(str(member["valid_to"]))
            if start > last_day or end is None or end > last_day:
                targets.append((member, start > last_day))
        if not targets:
            return None
        async with self._store.transaction():
            revision = await self._bump()
            stamp = _now()
            touched: set[str] = set()
            for member, future in targets:
                if future:
                    await self._store.execute(
                        f"DELETE FROM {self._members} WHERE id = ?", (str(member["id"]),))
                else:
                    await self._store.execute(
                        f"UPDATE {self._members} SET valid_to = ?, reason = ?, updated_at = ?, "
                        f"updated_by = ?, revision = ? WHERE id = ?",
                        (last_day.isoformat(), reason[:REASON_MAX], stamp, actor, revision,
                         str(member["id"])))
                touched.add(str(member["group_id"]))
            for group_id in sorted(touched):
                await self._touch_group(group_id, actor=actor, revision=revision)
                await self._write_audit(
                    actor=actor, action="student_deactivated", group_id=group_id,
                    revision=revision,
                    detail={"student": student, "validTo": last_day.isoformat(),
                            "reason": reason[:REASON_MAX]})
            await self._emit(revision)
        return revision

    async def reopen_student_memberships(self, student_id: str, *, start: date, actor: str,
                                         closed_reason: str, reason: str) -> tuple[int | None, list[str]]:
        """Pasifleştirmenin kapattığı sınıf üyeliklerini geri açar (yeniden etkinleştirme).

        ÇAĞIRANIN İŞLEMİNE KATILIR (öğrenci durumu, `student.reactivated` ve
        `group.changed` tek commit; olay sırası: önce öğrenci, sonra grup). SON
        pasifleştirmenin kapattığı satırlar bulunur (`reason` = `closed_reason`, en geç
        `valid_to`); arşivlenmiş sınıf ve öğrencinin `start`tan itibaren zaten üye olduğu
        sınıf atlanır. Kapanış günü `start`ın bir önceki günü ya da sonrasıysa (aynı gün
        geri alındı) aynı satırın sonu silinir — üyelik kesintisiz sürer; değilse
        `start`tan açık uçlu YENİ üyelik eklenir (pasif kalınan günler sınıf listesine,
        yoklamaya girmez). Özel ders sınıfında o günlerde başka öğrenci varsa atlanır.
        Döner: (yeni grup revizyonu ya da hiçbir şey açılmadıysa `None`, açılan sınıf adları).
        """
        student = str(student_id)
        rows = await self._store.fetch_all(
            f"SELECT * FROM {self._members} WHERE kantin_id = ?", (student,))
        closed = [dict(row) for row in rows
                  if str(row["reason"] or "") == closed_reason[:REASON_MAX] and _day(str(row["valid_to"] or ""))]
        if not closed:
            return None, []
        last = max(_day(str(row["valid_to"])) or date.min for row in closed)
        targets = [row for row in closed if _day(str(row["valid_to"])) == last]
        groups = {str(row["id"]): dict(row) for row in await self._store.fetch_all(
            f"SELECT * FROM {self._groups} WHERE id IN ({', '.join('?' for _ in targets)})",
            tuple(str(row["group_id"]) for row in targets))}
        plan: list[tuple[dict[str, Any], dict[str, Any], bool]] = []
        for member in targets:
            group = groups.get(str(member["group_id"]))
            if group is None or group["archived_at"]:
                continue
            existing = await self._member_rows([str(member["group_id"])])
            mine = [row for row in existing if str(row["kantin_id"]) == student
                    and str(row["id"]) != str(member["id"])]
            if any(_overlaps(start, None, _day(str(row["valid_from"])) or date.min,
                             _day(str(row["valid_to"]))) for row in mine):
                continue
            continuous = last >= start - timedelta(days=1)
            others = [row for row in existing if str(row["id"]) != str(member["id"])]
            planned = others + [{"kantin_id": student,
                                 "valid_from": str(member["valid_from"]) if continuous else start.isoformat(),
                                 "valid_to": ""}]
            if not continuous:
                planned.append(member)
            try:
                self._check_members(str(group["kind"]), planned)
            except GroupError:
                continue
            plan.append((member, group, continuous))
        if not plan:
            return None, []
        async with self._store.transaction():
            revision = await self._bump()
            stamp = _now()
            for member, group, continuous in plan:
                if continuous:
                    await self._store.execute(
                        f"UPDATE {self._members} SET valid_to = '', reason = ?, updated_at = ?, "
                        f"updated_by = ?, revision = ? WHERE id = ?",
                        (reason[:REASON_MAX], stamp, actor, revision, str(member["id"])))
                else:
                    await self._insert_member(str(group["id"]), student, start, None,
                                              reason=reason[:REASON_MAX], actor=actor,
                                              revision=revision, stamp=stamp)
                await self._touch_group(str(group["id"]), actor=actor, revision=revision)
                await self._write_audit(
                    actor=actor, action="student_reactivated", group_id=str(group["id"]),
                    revision=revision,
                    detail={"student": student, "validFrom": (str(member["valid_from"]) if continuous
                                                              else start.isoformat()),
                            "continued": continuous, "reason": reason[:REASON_MAX]})
            await self._touched_students([student])
            await self._emit(revision)
        return revision, [str(group["name"]) for _member, group, _continuous in plan]

    async def after_commit(self, revision: int) -> None:
        """Dış işlem commit edildikten sonra süreç içi `groups_changed` yayını."""
        await self._changed(revision)

    # ------------------------------------------------------------ iç yazma

    async def _insert_member(self, group_id: str, student: str, start: date, end: date | None,
                             *, reason: str, actor: str, revision: int, stamp: str,
                             origin: str = ORIGIN_GROUP) -> str:
        """Üyelik satırı ekler; satırın kimliğini döner (dönem geçişi geri alma için)."""
        member_id = str(uuid.uuid4())
        await self._store.execute(
            f"INSERT INTO {self._members} (id, group_id, kantin_id, valid_from, valid_to, "
            f"reason, created_at, created_by, updated_at, updated_by, revision, origin) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (member_id, group_id, student, start.isoformat(),
             end.isoformat() if end else "", reason, stamp, actor, stamp, actor, revision,
             origin),
        )
        return member_id

    async def _insert_group(self, *, name: str, kind: str, call_name: str, note: str,
                            level: dict[str, Any] | None, year: str, actor: str, revision: int,
                            stamp: str) -> str:
        """Canlı sınıf satırı ekler (kurallar çağıranda denetlenir); kimliği döner.

        `create` ve dönem geçişi (`academic_year.YearRollover`) aynı yoldan yazar.
        """
        group_id = str(uuid.uuid4())
        columns = level or {"grade_level": None, "track": None, "exam_target": None}
        await self._store.execute(
            f"INSERT INTO {self._groups} (id, name, name_fold, call_name, kind, note, "
            f"archived_at, created_at, created_by, updated_at, updated_by, revision, "
            f"grade_level, track, exam_target, academic_year) "
            f"VALUES (?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (group_id, name, fold(name), call_name, kind, note, stamp, actor, stamp,
             actor, revision, columns["grade_level"], columns["track"],
             columns["exam_target"], year),
        )
        return group_id

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

    async def list(self, since_revision: int | None = None, *,
                   view: str = "legacy") -> dict[str, Any]:
        """`view="classes"`: tek "Sınıf" modeli (yeni Akademi); varsayılan eski görünüm."""
        return await self._service.list(since_revision, view=view)

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
